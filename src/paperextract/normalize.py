"""Normalize MinerU native output into canonical structures without repair.

The input is the parsed ``middle_json.json`` written by the worker. Every block
becomes a canonical block with its source span; raw LaTeX, table HTML and cell
strings are kept verbatim. Ambiguities become findings, never silent choices.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Literal, cast

from paperextract.document import (
    Block,
    Document,
    Equation,
    Figure,
    Finding,
    Heading,
    InlineRun,
    ListBlock,
    MetadataObservation,
    PageFurniture,
    PageRecord,
    Panel,
    Paragraph,
    RichText,
    SourceSpan,
    Table,
    Unclassified,
    plain_text,
)
from paperextract.protocol import NATIVE_DIRECTORY, PageCoverage
from paperextract.tables import parse_html_table

__all__ = [
    "equation_label",
    "figure_label",
    "latex_balance_problems",
    "normalize_mineru",
    "table_label",
]

Json = dict[str, object]
Box = tuple[float, float, float, float]
Size = tuple[float, float]

# Supplements may number objects "Supplementary Figure 1" instead of "Fig. S1".
_FIGURE_LABEL = re.compile(
    r"^\s*(?:Supplementary\s+)?(?:Fig\.?|Figure|FIG\.?)\s*(S?\d+[A-Za-z]?)\b"
)
# Physical Review and other journals number tables with Roman numerals.
_TABLE_LABEL = re.compile(
    r"^\s*(?:Supplementary\s+)?(?:Table|TABLE|Tab\.)\s*(S?(?:\d+|[IVXLC]+)[A-Za-z]?)\b"
)
_CONTINUED = re.compile(r"\(\s*continued\s*\)", re.IGNORECASE)
_PANEL_LABEL = re.compile(r"^\s*\(?([A-Za-z]|[ivx]{1,4})\)?\.?\s*$")
_TAG = re.compile(r"\\tag\*?\{([^{}]*)\}")
_LEFT = re.compile(r"\\left(?![A-Za-z])")
_RIGHT = re.compile(r"\\right(?![A-Za-z])")
_ENVIRONMENT = re.compile(r"\\(begin|end)\{([^{}]*)\}")

_FURNITURE: Mapping[str, Literal["header", "footer", "page_number"]] = {
    "header": "header",
    "footer": "footer",
    "page_number": "page_number",
}
_PARAGRAPH_ROLES: Mapping[str, Literal["body", "reference", "footnote", "aside"]] = {
    "text": "body",
    "ref_text": "reference",
    "page_footnote": "footnote",
    "aside_text": "aside",
}
_INLINE_KINDS: Mapping[str, Literal["text", "math", "code"]] = {
    "text": "text",
    "equation_inline": "math",
    "code_inline": "code",
}
_VISUAL_TYPES = frozenset({"image", "chart"})
_BOX_LENGTH = 4
_BODY_TYPES = frozenset({"image_body", "chart_body"})
_METADATA_SOURCE = "pdf_information_via_mineru"


def _mapping(value: object) -> Json | None:
    """Return a JSON object as a dict, or None for any other value.

    Parameters
    ----------
    value : object
        Decoded JSON value.

    Returns
    -------
    dict of str to object or None
        The object, or None.
    """
    if isinstance(value, dict):
        return cast("Json", value)
    return None


def _sequence(value: object) -> list[object]:
    """Return a JSON array as a list, or an empty list for any other value.

    Parameters
    ----------
    value : object
        Decoded JSON value.

    Returns
    -------
    list of object
        The array, or an empty list.
    """
    if isinstance(value, list):
        return cast("list[object]", value)
    return []


def _string(value: object) -> str | None:
    """Return a string value, or None for any other value.

    Parameters
    ----------
    value : object
        Decoded JSON value.

    Returns
    -------
    str or None
        The string, or None.
    """
    return value if isinstance(value, str) else None


def _number(value: object) -> float | None:
    """Return a numeric value as float, rejecting booleans.

    Parameters
    ----------
    value : object
        Decoded JSON value.

    Returns
    -------
    float or None
        The number, or None.
    """
    if type(value) in (int, float):
        return float(cast("int | float", value))
    return None


def _box(value: object) -> Box | None:
    """Return a four-number fractional box, or None.

    Parameters
    ----------
    value : object
        Decoded JSON value.

    Returns
    -------
    tuple of float or None
        ``(x0, y0, x1, y1)`` fractions, or None when malformed.
    """
    items = [_number(item) for item in _sequence(value)]
    if len(items) != _BOX_LENGTH or any(item is None for item in items):
        return None
    x0, y0, x1, y1 = cast("list[float]", items)
    return (x0, y0, x1, y1)


def figure_label(caption: str) -> str | None:
    """Read the printed figure number at the start of a caption.

    Parameters
    ----------
    caption : str
        Caption as plain text.

    Returns
    -------
    str or None
        Label such as ``2`` or ``S1``, or None.

    Examples
    --------
    >>> figure_label("Supplementary Figure 3 | Spectra")
    '3'
    >>> figure_label("Fig. S2. Setup")
    'S2'
    """
    match = _FIGURE_LABEL.match(caption)
    return match.group(1) if match else None


def table_label(caption: str) -> str | None:
    """Read the printed table number at the start of a caption.

    Parameters
    ----------
    caption : str
        Caption as plain text.

    Returns
    -------
    str or None
        Label such as ``1`` or ``III``, or None.

    Examples
    --------
    >>> table_label("TABLE III. Results")
    'III'
    """
    match = _TABLE_LABEL.match(caption)
    return match.group(1) if match else None


def equation_label(latex: str) -> str | None:
    r"""Extract the printed equation number from a ``\tag`` command.

    Parameters
    ----------
    latex : str
        Raw display-equation LaTeX.

    Returns
    -------
    str or None
        Stripped tag argument of the last ``\tag``, or None when absent.
    """
    matches = _TAG.findall(latex)
    if not matches:
        return None
    label = cast("str", matches[-1]).strip()
    return label or None


def latex_balance_problems(latex: str) -> tuple[str, ...]:
    r"""Check delimiter balance without interpreting the mathematics.

    Parameters
    ----------
    latex : str
        Raw LaTeX.

    Returns
    -------
    tuple of str
        Descriptions of unbalanced braces, ``\left``/``\right`` pairs and
        environments. An empty tuple means no structural problem was found; it
        is not a statement that the transcription is correct.
    """
    problems: list[str] = []
    depth = 0
    index = 0
    while index < len(latex):
        character = latex[index]
        if character == "\\":
            index += 2
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth < 0:
                problems.append("closing brace without an opening brace")
                depth = 0
        index += 1
    if depth:
        problems.append(f"{depth} unclosed brace(s)")
    left = len(_LEFT.findall(latex))
    right = len(_RIGHT.findall(latex))
    if left != right:
        problems.append(f"{left} \\left versus {right} \\right")
    stack: list[str] = []
    for kind, name in _ENVIRONMENT.findall(latex):
        if kind == "begin":
            stack.append(name)
        elif not stack or stack.pop() != name:
            problems.append(f"\\end{{{name}}} without matching \\begin")
    problems.extend(f"\\begin{{{name}}} without matching \\end" for name in stack)
    return tuple(problems)


@dataclass
class _Context:
    """Carry per-page normalization state and collect findings."""

    source_sha256: str
    page: int
    size: Size | None
    assets: Collection[str]
    findings: list[Finding]
    ordinal: int = 0
    blocks: list[Block] = field(default_factory=list)

    def finding(
        self,
        code: str,
        severity: Literal["info", "warning", "error"],
        message: str,
        block_ids: tuple[str, ...] = (),
    ) -> None:
        """Record a finding for the current page.

        Parameters
        ----------
        code : str
            Finding code.
        severity : str
            Finding severity.
        message : str
            Explanation.
        block_ids : tuple of str
            Affected blocks.
        """
        self.findings.append(Finding(code, severity, message, block_ids, self.page))

    def block_id(self, prefix: str, block: Json) -> str:
        """Derive a stable identifier from source anchors and block position.

        Parameters
        ----------
        prefix : str
            Short kind prefix such as ``par``.
        block : dict of str to object
            Backend block.

        Returns
        -------
        str
            Prefix plus twelve hexadecimal digits.
        """
        self.ordinal += 1
        anchor = (
            f"{self.source_sha256}|{self.page}|{block.get('type')}|"
            f"{block.get('index')}|{self.ordinal}"
        )
        return f"{prefix}_{hashlib.sha256(anchor.encode()).hexdigest()[:12]}"

    def span(self, block: Json) -> SourceSpan:
        """Build the source span of a backend block.

        Parameters
        ----------
        block : dict of str to object
            Backend block with optional ``bbox`` and ``index``.

        Returns
        -------
        SourceSpan
            Span with point coordinates when the page size is known.
        """
        fraction = _box(block.get("bbox"))
        points: Box | None = None
        if fraction is not None and self.size is not None:
            width, height = self.size
            points = (
                round(fraction[0] * width, 2),
                round(fraction[1] * height, 2),
                round(fraction[2] * width, 2),
                round(fraction[3] * height, 2),
            )
        index = block.get("index")
        return SourceSpan(
            page=self.page,
            bbox_pt=points,
            bbox_fraction=fraction,
            backend_type=_string(block.get("type")) or "unknown",
            backend_index=index if type(index) is int else None,
        )

    def asset(self, block: Json, block_id: str) -> str | None:
        """Resolve a block's image crop against the recorded result files.

        Parameters
        ----------
        block : dict of str to object
            Backend block with an optional ``image_path``.
        block_id : str
            Owning canonical block, for the finding.

        Returns
        -------
        str or None
            Path relative to the staging directory, or None when absent.
        """
        image = _string(block.get("image_path"))
        if not image:
            return None
        path = f"{NATIVE_DIRECTORY}/{image}"
        if path not in self.assets:
            self.finding(
                "ASSET_MISSING",
                "warning",
                f"Backend references {path}, which the worker did not record.",
                (block_id,),
            )
        return path

    def runs(self, content: object) -> RichText:
        """Convert backend inline spans to canonical runs.

        Parameters
        ----------
        content : object
            Backend ``content`` value: a span list or a plain string.

        Returns
        -------
        tuple of InlineRun
            Runs in order; unknown span types are kept as text when they carry
            a string and reported otherwise.
        """
        if isinstance(content, str):
            return (InlineRun("text", content),) if content else ()
        runs: list[InlineRun] = []
        for item in _sequence(content):
            span = _mapping(item)
            if span is None:
                continue
            kind = _string(span.get("type")) or ""
            if kind == "hyperlink":
                url = _string(span.get("url"))
                for child in _sequence(span.get("content")):
                    runs.extend(self._inline(child, url))
            else:
                runs.extend(self._inline(item, None))
        return tuple(runs)

    def _inline(self, item: object, url: str | None) -> RichText:
        """Convert one non-link backend span.

        Parameters
        ----------
        item : object
            Backend span.
        url : str or None
            Enclosing hyperlink target, if any.

        Returns
        -------
        tuple of InlineRun
            Zero or one run.
        """
        span = _mapping(item)
        if span is None:
            return ()
        kind = _string(span.get("type")) or ""
        text = _string(span.get("content"))
        if text is None:
            self.finding(
                "INLINE_UNCLASSIFIED",
                "info",
                f"Inline span of type {kind!r} without string content was dropped.",
            )
            return ()
        styles = tuple(
            style
            for style in (_string(s) for s in _sequence(span.get("styles")))
            if style
        )
        canonical = _INLINE_KINDS.get(kind)
        if canonical is None:
            self.finding(
                "INLINE_UNCLASSIFIED",
                "info",
                f"Inline span of type {kind!r} kept as text.",
            )
            canonical = "text"
        if url is not None and canonical == "text":
            return (InlineRun("link", text, styles, url),)
        return (InlineRun(canonical, text, styles, url),)


def _layout_sizes(native: Mapping[str, object]) -> dict[int, Size]:
    """Read per-page sizes from the backend layout extension.

    Parameters
    ----------
    native : Mapping of str to object
        Parsed native document.

    Returns
    -------
    dict of int to tuple of float
        One-based page number to ``(width_pt, height_pt)``.
    """
    sizes: dict[int, Size] = {}
    extensions = _mapping(native.get("extensions")) or {}
    layout = _mapping(extensions.get("docvortex_layout")) or {}
    for item in _sequence(layout.get("pages")):
        page = _mapping(item)
        if page is None:
            continue
        index = page.get("page_idx")
        width = _number(page.get("width_pt"))
        height = _number(page.get("height_pt"))
        if type(index) is int and width is not None and height is not None:
            sizes[index + 1] = (width, height)
    return sizes


def _metadata_observations(
    native: Mapping[str, object],
) -> tuple[MetadataObservation, ...]:
    """Collect document-information values reported by the backend.

    Parameters
    ----------
    native : Mapping of str to object
        Parsed native document.

    Returns
    -------
    tuple of MetadataObservation
        Observations in a fixed field order.
    """
    metadata = _mapping(native.get("metadata")) or {}
    document = _mapping(metadata.get("document")) or {}
    observations: list[MetadataObservation] = []
    for name in (
        "title",
        "subject",
        "description",
        "publisher",
        "created_at",
        "modified_at",
        "creator_application",
        "producer_application",
    ):
        value = _string(document.get(name))
        if value:
            observations.append(MetadataObservation(name, value, _METADATA_SOURCE))
    for name, singular in (
        ("authors", "author"),
        ("keywords", "keyword"),
        ("identifiers", "identifier"),
        ("languages", "language"),
    ):
        for item in _sequence(document.get(name)):
            value = _string(item)
            if value:
                observations.append(
                    MetadataObservation(singular, value, _METADATA_SOURCE)
                )
    return tuple(observations)


def _children(block: Json) -> list[Json]:
    """List the object children of a compound backend block.

    Parameters
    ----------
    block : dict of str to object
        Backend block.

    Returns
    -------
    list of dict
        Children that are objects, in order.
    """
    return [child for child in map(_mapping, _sequence(block.get("content"))) if child]


def _union(boxes: list[Box | None]) -> Box | None:
    """Compute the bounding union of the known boxes.

    Parameters
    ----------
    boxes : list of tuple of float or None
        Boxes in points; None entries are skipped.

    Returns
    -------
    tuple of float or None
        Union, or None when no box is known.
    """
    known = [box for box in boxes if box is not None]
    if not known:
        return None
    return (
        min(box[0] for box in known),
        min(box[1] for box in known),
        max(box[2] for box in known),
        max(box[3] for box in known),
    )


def _equation(ctx: _Context, block: Json) -> Equation:
    """Normalize a display equation.

    Parameters
    ----------
    ctx : _Context
        Page context.
    block : dict of str to object
        Backend equation block.

    Returns
    -------
    Equation
        Canonical equation with raw LaTeX.
    """
    latex = _string(block.get("content")) or ""
    block_id = ctx.block_id("eq", block)
    if not latex.strip():
        ctx.finding(
            "EQUATION_EMPTY", "warning", "Equation block has no LaTeX.", (block_id,)
        )
    problems = latex_balance_problems(latex)
    if problems:
        ctx.finding(
            "EQUATION_UNBALANCED",
            "warning",
            "Unbalanced LaTeX kept verbatim: " + "; ".join(problems),
            (block_id,),
        )
    return Equation(
        id=block_id,
        latex=latex,
        label=equation_label(latex),
        asset=ctx.asset(block, block_id),
        span=ctx.span(block),
    )


def _list(ctx: _Context, block: Json) -> ListBlock:
    """Normalize a list block, flattening nested lists.

    Parameters
    ----------
    ctx : _Context
        Page context.
    block : dict of str to object
        Backend list block.

    Returns
    -------
    ListBlock
        Canonical list.
    """
    block_id = ctx.block_id("list", block)
    items: list[RichText] = []
    pending = _children(block)
    while pending:
        child = pending.pop(0)
        if child.get("type") == "list":
            ctx.finding(
                "LIST_FLATTENED",
                "info",
                "Nested list items were flattened into the parent list.",
                (block_id,),
            )
            pending = _children(child) + pending
        else:
            items.append(ctx.runs(child.get("content")))
    return ListBlock(
        id=block_id,
        items=tuple(items),
        span=ctx.span(block),
        continues_previous=bool(block.get("continues_prev")),
    )


def _table(ctx: _Context, block: Json) -> Table:
    """Normalize a table, keeping raw HTML and parsing an exact grid.

    Parameters
    ----------
    ctx : _Context
        Page context.
    block : dict of str to object
        Backend table block.

    Returns
    -------
    Table
        Canonical table; grid problems become findings.
    """
    block_id = ctx.block_id("tbl", block)
    children = _children(block)
    body = next((c for c in children if c.get("type") == "table_body"), None)
    captions = [
        ctx.runs(c.get("content")) for c in children if c.get("type") == "table_caption"
    ]
    footnotes = [
        ctx.runs(c.get("content"))
        for c in children
        if c.get("type") == "table_footnote"
    ]
    caption = captions[0] if captions else None
    footnotes = captions[1:] + footnotes
    label = None
    if caption is not None:
        match = _TABLE_LABEL.match(plain_text(caption))
        label = match.group(1) if match else None
    if label is None:
        for position, note in enumerate(footnotes):
            match = _TABLE_LABEL.match(plain_text(note))
            if match:
                label = match.group(1)
                if caption is None:
                    caption = footnotes.pop(position)
                    ctx.finding(
                        "TABLE_CAPTION_FROM_FOOTNOTE",
                        "info",
                        f"Caption 'Table {label}' was classified as a footnote by the "
                        "backend and is used as the caption.",
                        (block_id,),
                    )
                break
    if caption is None:
        ctx.finding(
            "CAPTION_UNMATCHED", "warning", "Table has no caption.", (block_id,)
        )
    markup = _string(body.get("content")) if body is not None else None
    cells = rows = columns = None
    if markup is None or not markup.strip():
        ctx.finding(
            "TABLE_BODY_MISSING", "warning", "Table has no HTML body.", (block_id,)
        )
        markup = markup or ""
    else:
        try:
            grid = parse_html_table(markup)
        except ValueError as exc:
            ctx.finding("TABLE_STRUCTURE_UNRESOLVED", "warning", str(exc), (block_id,))
        else:
            cells, rows, columns = grid.cells, grid.rows, grid.columns
            if grid.problems:
                ctx.finding(
                    "TABLE_STRUCTURE_UNRESOLVED",
                    "warning",
                    "Grid problems kept for review: " + "; ".join(grid.problems),
                    (block_id,),
                )
    anchor = body if body is not None else block
    return Table(
        id=block_id,
        label=label,
        caption=caption,
        footnotes=tuple(footnotes),
        html=markup,
        cells=cells,
        rows=rows,
        columns=columns,
        asset=ctx.asset(anchor, block_id),
        span=ctx.span(anchor),
        continues_previous=bool(block.get("continues_prev"))
        or (caption is not None and _CONTINUED.search(plain_text(caption)) is not None),
    )


def _figure(
    ctx: _Context,
    panels: list[Panel],
    caption: tuple[RichText, SourceSpan] | None,
    footnotes: list[RichText],
) -> Figure:
    """Assemble a figure from grouped panels.

    Parameters
    ----------
    ctx : _Context
        Page context.
    panels : list of Panel
        Panels in reading order.
    caption : tuple or None
        Caption runs and span when a figure caption anchors the group.
    footnotes : list of tuple of InlineRun
        Figure footnotes and unclassified caption text.

    Returns
    -------
    Figure
        Canonical figure with a grouping label and findings.
    """
    anchor = "|".join(panel.id for panel in panels)
    figure_id = f"fig_{hashlib.sha256(anchor.encode()).hexdigest()[:12]}"
    label = None
    boxes: list[Box | None] = [panel.span.bbox_pt for panel in panels]
    if caption is None:
        grouping: Literal["single", "caption_run", "unresolved"] = "unresolved"
        ctx.finding(
            "CAPTION_UNMATCHED",
            "warning",
            f"{len(panels)} visual block(s) have no figure caption; "
            "grouping is unresolved.",
            (figure_id,),
        )
    else:
        match = _FIGURE_LABEL.match(plain_text(caption[0]))
        label = match.group(1) if match else None
        boxes.append(caption[1].bbox_pt)
        grouping = "single" if len(panels) == 1 else "caption_run"
        if grouping == "caption_run":
            ctx.finding(
                "FIGURE_GROUPING_HEURISTIC",
                "info",
                f"{len(panels)} consecutive visual blocks were grouped up to the "
                f"caption of figure {label!r}; review the panel set.",
                (figure_id,),
            )
    return Figure(
        id=figure_id,
        label=label,
        caption=None if caption is None else caption[0],
        caption_span=None if caption is None else caption[1],
        footnotes=tuple(footnotes),
        panels=tuple(panels),
        context_bbox_pt=_union(boxes),
        grouping=grouping,
        page=ctx.page,
    )


def _figures(ctx: _Context, run: list[Json]) -> list[Figure]:
    """Group a run of consecutive visual blocks into figures.

    Parameters
    ----------
    ctx : _Context
        Page context.
    run : list of dict
        Consecutive image and chart blocks without intervening prose.

    Returns
    -------
    list of Figure
        One figure per caption, plus one unresolved figure for trailing panels.

    Notes
    -----
    A caption that starts with a figure label closes the group that precedes
    it, because captions are printed below their figures. Single-letter
    captions are panel labels. This is a heuristic and is reported as such.
    """
    figures: list[Figure] = []
    panels: list[Panel] = []
    footnotes: list[RichText] = []
    for block in run:
        children = _children(block)
        body = next((c for c in children if c.get("type") in _BODY_TYPES), block)
        panel_id = ctx.block_id("panel", block)
        panel_label: str | None = None
        caption: tuple[RichText, SourceSpan] | None = None
        for child in children:
            kind = _string(child.get("type")) or ""
            if kind.endswith("_footnote"):
                footnotes.append(ctx.runs(child.get("content")))
                continue
            if not kind.endswith("_caption"):
                continue
            runs = ctx.runs(child.get("content"))
            text = plain_text(runs)
            panel_match = _PANEL_LABEL.match(text)
            if caption is None and _FIGURE_LABEL.match(text):
                caption = (runs, ctx.span(child))
            elif panel_label is None and panel_match:
                panel_label = panel_match.group(1)
            else:
                footnotes.append(runs)
        panels.append(
            Panel(
                id=panel_id,
                asset=ctx.asset(body, panel_id),
                label=panel_label,
                span=ctx.span(body),
            )
        )
        if caption is not None:
            figures.append(_figure(ctx, panels, caption, footnotes))
            panels, footnotes = [], []
    if panels:
        figures.append(_figure(ctx, panels, None, footnotes))
    return figures


_COMPOUND: Mapping[str, Callable[[_Context, Json], Block]] = {
    "equation": _equation,
    "list": _list,
    "table": _table,
}


def _simple_block(ctx: _Context, kind: str, block: Json) -> Block:
    """Normalize a non-visual, non-compound backend block.

    Parameters
    ----------
    ctx : _Context
        Page context.
    kind : str
        Backend block type.
    block : dict of str to object
        Backend block.

    Returns
    -------
    Block
        Canonical block; unknown types are retained as unclassified.
    """
    if kind in _FURNITURE:
        return PageFurniture(
            id=ctx.block_id("aux", block),
            role=_FURNITURE[kind],
            runs=ctx.runs(block.get("content")),
            span=ctx.span(block),
        )
    if kind in _PARAGRAPH_ROLES:
        return Paragraph(
            id=ctx.block_id("par", block),
            role=_PARAGRAPH_ROLES[kind],
            runs=ctx.runs(block.get("content")),
            span=ctx.span(block),
            continues_previous=bool(block.get("continues_prev")),
        )
    if kind in ("doc_title", "paragraph_title"):
        level = block.get("level")
        return Heading(
            id=ctx.block_id("hd", block),
            level=level if type(level) is int else (1 if kind == "doc_title" else 2),
            runs=ctx.runs(block.get("content")),
            span=ctx.span(block),
        )
    if kind in _COMPOUND:
        return _COMPOUND[kind](ctx, block)
    block_id = ctx.block_id("raw", block)
    ctx.finding(
        "BLOCK_UNCLASSIFIED",
        "info",
        f"Backend block type {kind!r} has no canonical mapping and was retained raw.",
        (block_id,),
    )
    return Unclassified(
        id=block_id,
        backend_type=kind,
        raw=json.dumps(block, ensure_ascii=False, sort_keys=True),
        span=ctx.span(block),
    )


def _page_blocks(ctx: _Context, raw_blocks: list[object]) -> list[Block]:
    """Normalize the blocks of one page in backend reading order.

    Parameters
    ----------
    ctx : _Context
        Page context.
    raw_blocks : list of object
        Backend blocks.

    Returns
    -------
    list of Block
        Canonical blocks with visual runs grouped into figures.
    """
    blocks: list[Block] = []
    run: list[Json] = []
    for raw in raw_blocks:
        block = _mapping(raw)
        if block is None:
            ctx.finding("BLOCK_MALFORMED", "warning", "A page block was not an object.")
            continue
        kind = _string(block.get("type")) or "unknown"
        if kind in _VISUAL_TYPES:
            run.append(block)
            continue
        if run:
            blocks.extend(_figures(ctx, run))
            run = []
        blocks.append(_simple_block(ctx, kind, block))
    if run:
        blocks.extend(_figures(ctx, run))
    return blocks


def normalize_mineru(
    native: Mapping[str, object],
    *,
    source_sha256: str,
    coverage: PageCoverage,
    backend_version: str,
    assets: Collection[str],
    page_sizes: Mapping[int, Size] | None = None,
) -> Document:
    """Convert a parsed MinerU middle document into the canonical schema.

    Parameters
    ----------
    native : Mapping of str to object
        Parsed ``native/middle_json.json``.
    source_sha256 : str
        Digest of the preserved source.
    coverage : PageCoverage
        Verified page coverage from the worker result.
    backend_version : str
        Backend version from the worker result.
    assets : Collection of str
        Result file paths relative to the staging directory, used to confirm
        that referenced crops exist.
    page_sizes : Mapping of int to tuple of float or None
        Independent page sizes in points by one-based page number, used when
        the backend layout extension lacks a page.

    Returns
    -------
    Document
        Canonical document with findings for missing pages, empty pages,
        unresolved figure groups, table grid problems and unbalanced LaTeX.

    Notes
    -----
    Values are never repaired: LaTeX, table HTML and cell strings are copied
    verbatim, paragraphs are not merged across pages, and a figure group that
    no caption anchors is reported rather than guessed.
    """
    findings: list[Finding] = []
    sizes: dict[int, Size] = dict(page_sizes or {})
    sizes.update(_layout_sizes(native))
    returned: dict[int, list[object]] = {}
    for item in _sequence(native.get("pages")):
        page = _mapping(item)
        index = None if page is None else page.get("page_idx")
        if page is None or type(index) is not int:
            findings.append(
                Finding(
                    "PAGE_MALFORMED", "warning", "A native page entry was malformed."
                )
            )
            continue
        returned[index + 1] = _sequence(page.get("blocks"))
    for number in coverage.missing:
        findings.append(
            Finding(
                "PAGE_NOT_PROCESSED",
                "error",
                f"Page {number} was requested but not returned by the worker.",
                (),
                number,
            )
        )
    for number in coverage.empty:
        findings.append(
            Finding(
                "PAGE_EMPTY",
                "warning",
                f"Page {number} returned no blocks; content is unrecovered.",
                (),
                number,
            )
        )
    pages: list[PageRecord] = []
    for number in coverage.requested:
        if number in coverage.missing:
            status: Literal["processed", "empty", "missing"] = "missing"
        elif number in coverage.empty:
            status = "empty"
        else:
            status = "processed"
        size = sizes.get(number)
        if size is None and status == "processed":
            findings.append(
                Finding(
                    "PAGE_SIZE_UNKNOWN",
                    "warning",
                    f"No page size for page {number}; boxes stay fractional.",
                    (),
                    number,
                )
            )
        pages.append(
            PageRecord(
                number=number,
                width_pt=None if size is None else size[0],
                height_pt=None if size is None else size[1],
                status=status,
            )
        )
    blocks: list[Block] = []
    for number in sorted(returned):
        ctx = _Context(source_sha256, number, sizes.get(number), assets, findings)
        blocks.extend(_page_blocks(ctx, returned[number]))
    return Document(
        source_sha256=source_sha256,
        backend="mineru",
        backend_version=backend_version,
        native_schema=_string(native.get("schema")) or "unknown",
        native_schema_version=_string(native.get("schema_version")) or "unknown",
        pages=tuple(pages),
        blocks=tuple(blocks),
        metadata=_metadata_observations(native),
        findings=tuple(findings),
    )
