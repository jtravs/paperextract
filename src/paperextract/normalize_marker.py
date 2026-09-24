"""Normalize Marker 2 native output into canonical structures without repair.

The input is the worker's wrapper ``native/marker.json``: Marker's JSON
renderer output, a tree of pages, groups (figure, picture, table and list
groups) and leaf blocks with HTML content, boxes in points with a top-left
origin, and embedded images that the worker saved as files. Leaf HTML is
turned into rich text: ``<math>`` becomes a math run with its LaTeX kept
verbatim, and ``<sup>``, ``<sub>``, ``<b>`` and ``<i>`` become styles.
Nothing here reads the PDF.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from html.parser import HTMLParser
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
from paperextract.normalize import figure_label, latex_balance_problems, table_label
from paperextract.protocol import NATIVE_DIRECTORY, PageCoverage
from paperextract.tables import parse_html_table

__all__ = [
    "NATIVE_SCHEMA",
    "NATIVE_SCHEMA_VERSION",
    "html_runs",
    "normalize_marker",
]

NATIVE_SCHEMA = "paperextract.marker-native"
NATIVE_SCHEMA_VERSION = 1

Json = dict[str, object]
Box = tuple[float, float, float, float]
Size = tuple[float, float]

_METADATA_SOURCE = "pdf_information_via_marker_worker"
_METADATA_FIELDS: Mapping[str, str] = {
    "Title": "title",
    "Author": "author",
    "Subject": "subject",
    "Keywords": "keyword",
    "Creator": "creator_application",
    "Producer": "producer_application",
    "CreationDate": "created_at",
    "ModDate": "modified_at",
}
_PARAGRAPHS: Mapping[str, Literal["body", "reference", "footnote", "aside"]] = {
    "Text": "body",
    "TextInlineMath": "body",
    "Code": "body",
    "Handwriting": "body",
    "Reference": "reference",
    "Bibliography": "reference",
    "Footnote": "footnote",
    "Caption": "aside",
}
_FURNITURE: Mapping[str, Literal["header", "footer"]] = {
    "PageHeader": "header",
    "PageFooter": "footer",
}
_VISUALS = frozenset({"Figure", "Picture", "Diagram", "ChemicalBlock"})
_VISUAL_GROUPS = frozenset({"FigureGroup", "PictureGroup"})
_STYLES: Mapping[str, str] = {
    "sup": "superscript",
    "sub": "subscript",
    "b": "bold",
    "strong": "bold",
    "i": "italic",
    "em": "italic",
}
_HEADING = re.compile(r"<h([1-6])\b", re.I)
_TRAILING_NUMBER = re.compile(
    r"(?:\\quad|\\qquad|\s|~)*\(\s*(S?\d+[a-z]?)\s*\)\s*[.,]?\s*$"
)
_BOX_LENGTH = 4


class _RunParser(HTMLParser):
    """Collect rich-text runs from block HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.runs: list[InlineRun] = []
        self.styles: list[str] = []
        self.link: list[str] = []
        self.math: list[str] | None = None
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Track styles, links, math and skipped elements.

        Parameters
        ----------
        tag : str
            Tag name.
        attrs : list of tuple
            Attributes.
        """
        if tag == "math":
            self.math = []
        elif tag in _STYLES:
            self.styles.append(_STYLES[tag])
        elif tag == "a":
            self.link.append(dict(attrs).get("href") or "")
        elif tag in ("script", "style", "content-ref"):
            self.skip += 1
        elif tag in ("br", "p", "li", "div") and self.runs and self.math is None:
            self._text(" ")

    def handle_endtag(self, tag: str) -> None:
        """Close styles, links and math.

        Parameters
        ----------
        tag : str
            Tag name.
        """
        if tag == "math" and self.math is not None:
            self.runs.append(InlineRun("math", "".join(self.math).strip()))
            self.math = None
        elif tag in _STYLES and _STYLES[tag] in self.styles:
            self.styles.remove(_STYLES[tag])
        elif tag == "a" and self.link:
            self.link.pop()
        elif tag in ("script", "style", "content-ref") and self.skip:
            self.skip -= 1

    def handle_data(self, data: str) -> None:
        """Add text to the current math or text run.

        Parameters
        ----------
        data : str
            Text.
        """
        if self.skip:
            return
        if self.math is not None:
            self.math.append(data)
        else:
            self._text(data)

    def _text(self, data: str) -> None:
        """Append text, merging with a previous run of the same kind.

        Parameters
        ----------
        data : str
            Text.
        """
        styles = tuple(sorted(set(self.styles)))
        url = self.link[-1] if self.link else None
        kind: Literal["text", "link"] = "link" if url else "text"
        last = self.runs[-1] if self.runs else None
        if last is not None and (last.kind, last.styles, last.url) == (
            kind,
            styles,
            url,
        ):
            self.runs[-1] = InlineRun(kind, last.text + data, styles, url)
        else:
            self.runs.append(InlineRun(kind, data, styles, url))


def html_runs(html: str) -> RichText:
    """Convert Marker block HTML to rich-text runs.

    Parameters
    ----------
    html : str
        Block HTML.

    Returns
    -------
    tuple of InlineRun
        Runs with whitespace collapsed and empty runs dropped.

    Examples
    --------
    >>> runs = html_runs("<p>Energy <math>E</math> x<sup>2</sup></p>")
    >>> [(r.kind, r.text) for r in runs]
    [('text', 'Energy '), ('math', 'E'), ('text', ' x'), ('text', '2')]
    """
    parser = _RunParser()
    parser.feed(html)
    parser.close()
    runs: list[InlineRun] = []
    for run in parser.runs:
        text = run.text if run.kind == "math" else re.sub(r"\s+", " ", run.text)
        if text.strip() or (text and runs):
            runs.append(InlineRun(run.kind, text, run.styles, run.url))
    while runs and runs[-1].kind != "math" and not runs[-1].text.strip():
        runs.pop()
    if runs and runs[0].kind != "math":
        first = runs[0]
        runs[0] = InlineRun(first.kind, first.text.lstrip(), first.styles, first.url)
    if runs and runs[-1].kind != "math":
        last = runs[-1]
        runs[-1] = InlineRun(last.kind, last.text.rstrip(), last.styles, last.url)
    return tuple(runs)


def _mapping(value: object) -> Json | None:
    """Return a JSON object as a dict, or None.

    Parameters
    ----------
    value : object
        Decoded JSON value.

    Returns
    -------
    dict of str to object or None
        The object, or None.
    """
    return cast("Json", value) if isinstance(value, dict) else None


def _children(node: Json) -> list[Json]:
    """List a node's object children.

    Parameters
    ----------
    node : dict of str to object
        Marker block.

    Returns
    -------
    list of dict
        Children in order.
    """
    value = node.get("children")
    items = cast("list[object]", value) if isinstance(value, list) else []
    return [child for child in map(_mapping, items) if child]


def _string(value: object) -> str:
    """Return a string, or ``""``.

    Parameters
    ----------
    value : object
        Decoded JSON value.

    Returns
    -------
    str
        The string, or ``""``.
    """
    return value if isinstance(value, str) else ""


def _box(value: object) -> Box | None:
    """Read a four-number box in points.

    Parameters
    ----------
    value : object
        Decoded JSON value.

    Returns
    -------
    tuple of float or None
        ``(x0, y0, x1, y1)``, or None when malformed.
    """
    if not isinstance(value, list):
        return None
    numbers = [n for n in cast("list[object]", value) if type(n) in (int, float)]
    if len(numbers) != _BOX_LENGTH:
        return None
    x0, y0, x1, y1 = (round(float(cast("float", n)), 2) for n in numbers)
    return (x0, y0, x1, y1)


def _union(boxes: list[Box | None]) -> Box | None:
    """Compute the bounding union of the known boxes.

    Parameters
    ----------
    boxes : list of tuple of float or None
        Boxes.

    Returns
    -------
    tuple of float or None
        Union, or None.
    """
    known = [box for box in boxes if box is not None]
    if not known:
        return None
    return (
        min(b[0] for b in known),
        min(b[1] for b in known),
        max(b[2] for b in known),
        max(b[3] for b in known),
    )


@dataclass
class _Page:
    """Carry per-page state while blocks are normalized."""

    number: int
    size: Size | None
    source_sha256: str
    images: Mapping[str, str]
    assets: Collection[str]
    findings: list[Finding]
    blocks: list[Block] = field(default_factory=list[Block])
    ordinal: int = 0

    def block_id(self, prefix: str, node: Json) -> str:
        """Derive a stable identifier from the source and Marker's block id.

        Parameters
        ----------
        prefix : str
            Kind prefix such as ``par``.
        node : dict of str to object
            Marker block.

        Returns
        -------
        str
            Prefix plus twelve hexadecimal digits.
        """
        self.ordinal += 1
        anchor = f"{self.source_sha256}|marker|{_string(node.get('id'))}|{self.ordinal}"
        return f"{prefix}_{hashlib.sha256(anchor.encode()).hexdigest()[:12]}"

    def span(self, node: Json) -> SourceSpan:
        """Build a block's source span.

        Parameters
        ----------
        node : dict of str to object
            Marker block.

        Returns
        -------
        SourceSpan
            Span with the box in points and as page fractions.
        """
        box = _box(node.get("bbox"))
        fraction = None
        if box is not None and self.size is not None:
            width, height = self.size
            fraction = (
                round(box[0] / width, 6),
                round(box[1] / height, 6),
                round(box[2] / width, 6),
                round(box[3] / height, 6),
            )
        tail = _string(node.get("id")).rsplit("/", 1)[-1]
        return SourceSpan(
            page=self.number,
            bbox_pt=box,
            bbox_fraction=fraction,
            backend_type=_string(node.get("block_type")) or "unknown",
            backend_index=int(tail) if tail.isdigit() else None,
        )

    def finding(
        self,
        code: str,
        severity: Literal["info", "warning", "error"],
        message: str,
        block_ids: tuple[str, ...] = (),
    ) -> None:
        """Record a finding on this page.

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
        self.findings.append(Finding(code, severity, message, block_ids, self.number))

    def image(self, node: Json, block_id: str) -> str | None:
        """Resolve the image file the worker saved for a block.

        Parameters
        ----------
        node : dict of str to object
            Marker block.
        block_id : str
            Owning block, for the finding.

        Returns
        -------
        str or None
            Path relative to the worker staging directory.
        """
        keys = list(_mapping(node.get("images")) or {})
        name = next((self.images[k] for k in keys if k in self.images), None)
        if name is None:
            return None
        path = f"{NATIVE_DIRECTORY}/{name}"
        if path not in self.assets:
            self.finding(
                "ASSET_MISSING",
                "warning",
                f"Marker output references {path}, which the worker did not record.",
                (block_id,),
            )
        return path


def _paragraph(page: _Page, node: Json, kind: str) -> None:
    """Normalize a text-like leaf block.

    Parameters
    ----------
    page : _Page
        Page state.
    node : dict of str to object
        Marker block.
    kind : str
        Marker block type.
    """
    runs = html_runs(_string(node.get("html")))
    span = page.span(node)
    if kind in _FURNITURE:
        page.blocks.append(
            PageFurniture(page.block_id("aux", node), _FURNITURE[kind], runs, span)
        )
        return
    if kind == "SectionHeader":
        match = _HEADING.search(_string(node.get("html")))
        level = int(match.group(1)) if match else 2
        page.blocks.append(Heading(page.block_id("hd", node), level, runs, span))
        return
    block_id = page.block_id("par", node)
    if kind == "Caption":
        page.finding(
            "CAPTION_UNATTACHED",
            "info",
            "A caption outside a figure or table group is kept as a paragraph.",
            (block_id,),
        )
    if kind == "Code":
        runs = (InlineRun("code", plain_text(runs)),) if runs else ()
    page.blocks.append(Paragraph(block_id, _PARAGRAPHS[kind], runs, span))


def _equation(page: _Page, node: Json) -> None:
    """Normalize a display equation.

    Parameters
    ----------
    page : _Page
        Page state.
    node : dict of str to object
        Marker equation block.
    """
    block_id = page.block_id("eq", node)
    math = [
        run.text for run in html_runs(_string(node.get("html"))) if run.kind == "math"
    ]
    latex = " ".join(math)
    if not latex.strip():
        page.finding(
            "EQUATION_EMPTY", "warning", "Equation block has no LaTeX.", (block_id,)
        )
    problems = latex_balance_problems(latex)
    if problems:
        page.finding(
            "EQUATION_UNBALANCED",
            "warning",
            "Unbalanced LaTeX kept verbatim: " + "; ".join(problems),
            (block_id,),
        )
    match = _TRAILING_NUMBER.search(latex)
    page.blocks.append(
        Equation(
            block_id,
            latex,
            match.group(1) if match else None,
            page.image(node, block_id),
            page.span(node),
        )
    )


def _list(page: _Page, node: Json) -> None:
    """Normalize a list group.

    Parameters
    ----------
    page : _Page
        Page state.
    node : dict of str to object
        Marker list group.
    """
    items = tuple(
        html_runs(_string(child.get("html")))
        for child in _children(node)
        if child.get("block_type") == "ListItem"
    )
    if not items:
        items = (html_runs(_string(node.get("html"))),)
    page.blocks.append(ListBlock(page.block_id("list", node), items, page.span(node)))


def _captions(
    page: _Page, members: list[Json]
) -> tuple[list[tuple[RichText, SourceSpan]], list[RichText]]:
    """Split a group's caption and footnote blocks.

    Parameters
    ----------
    page : _Page
        Page state.
    members : list of dict
        Group children.

    Returns
    -------
    tuple
        Captions with spans, and footnotes.
    """
    captions = [
        (html_runs(_string(m.get("html"))), page.span(m))
        for m in members
        if m.get("block_type") == "Caption"
    ]
    notes = [
        html_runs(_string(m.get("html")))
        for m in members
        if m.get("block_type") == "Footnote"
    ]
    return captions, notes


def _table(page: _Page, node: Json, members: list[Json]) -> None:
    """Normalize a table with the captions of its group.

    Parameters
    ----------
    page : _Page
        Page state.
    node : dict of str to object
        Marker table block.
    members : list of dict
        The group's other blocks.
    """
    block_id = page.block_id("tbl", node)
    captions, notes = _captions(page, members)
    caption = captions[0][0] if captions else None
    footnotes = [runs for runs, _span in captions[1:]] + notes
    if caption is None:
        page.finding(
            "CAPTION_UNMATCHED", "warning", "Table has no caption.", (block_id,)
        )
    markup = _string(node.get("html"))
    cells = rows = columns = None
    try:
        grid = parse_html_table(markup)
    except ValueError as exc:
        page.finding("TABLE_STRUCTURE_UNRESOLVED", "warning", str(exc), (block_id,))
    else:
        cells, rows, columns = grid.cells, grid.rows, grid.columns
        if grid.problems:
            page.finding(
                "TABLE_STRUCTURE_UNRESOLVED",
                "warning",
                "Grid problems kept for review: " + "; ".join(grid.problems),
                (block_id,),
            )
    page.blocks.append(
        Table(
            id=block_id,
            label=None if caption is None else table_label(plain_text(caption)),
            caption=caption,
            footnotes=tuple(footnotes),
            html=markup,
            cells=cells,
            rows=rows,
            columns=columns,
            asset=page.image(node, block_id),
            span=page.span(node),
            continues_previous=caption is not None
            and re.search(r"\(\s*continued\s*\)", plain_text(caption), re.I)
            is not None,
        )
    )


def _figure(page: _Page, visuals: list[Json], members: list[Json]) -> None:
    """Normalize a figure from its visual blocks and captions.

    Parameters
    ----------
    page : _Page
        Page state.
    visuals : list of dict
        Picture, figure or diagram blocks, one panel each.
    members : list of dict
        The group's other blocks.
    """
    panels: list[Panel] = []
    for visual in visuals:
        panel_id = page.block_id("panel", visual)
        panels.append(
            Panel(panel_id, page.image(visual, panel_id), None, page.span(visual))
        )
    anchor = "|".join(panel.id for panel in panels)
    figure_id = f"fig_{hashlib.sha256(anchor.encode()).hexdigest()[:12]}"
    captions, notes = _captions(page, members)
    caption = captions[0] if captions else None
    footnotes = [runs for runs, _span in captions[1:]] + notes
    boxes: list[Box | None] = [panel.span.bbox_pt for panel in panels]
    if caption is None:
        page.finding(
            "CAPTION_UNMATCHED",
            "warning",
            f"{len(panels)} visual block(s) have no figure caption; grouping is "
            "unresolved.",
            (figure_id,),
        )
    else:
        boxes.append(caption[1].bbox_pt)
    page.blocks.append(
        Figure(
            id=figure_id,
            label=None if caption is None else figure_label(plain_text(caption[0])),
            caption=None if caption is None else caption[0],
            caption_span=None if caption is None else caption[1],
            footnotes=tuple(footnotes),
            panels=tuple(panels),
            context_bbox_pt=_union(boxes),
            grouping="unresolved"
            if caption is None
            else "single"
            if len(panels) == 1
            else "caption_run",
            page=page.number,
        )
    )


def _node(page: _Page, node: Json) -> None:
    """Normalize one block or group of a page.

    Parameters
    ----------
    page : _Page
        Page state.
    node : dict of str to object
        Marker block.
    """
    kind = _string(node.get("block_type"))
    members = _children(node)
    if kind in _PARAGRAPHS or kind in _FURNITURE or kind == "SectionHeader":
        _paragraph(page, node, kind)
    elif kind == "Equation":
        _equation(page, node)
    elif kind == "ListGroup":
        _list(page, node)
    elif kind == "Table":
        _table(page, node, [])
    elif kind == "TableGroup":
        tables = [m for m in members if m.get("block_type") == "Table"]
        others = [m for m in members if m.get("block_type") != "Table"]
        for table in tables:
            _table(page, table, others)
        if not tables:
            _unclassified(page, node)
    elif kind in _VISUALS:
        _figure(page, [node], [])
    elif kind in _VISUAL_GROUPS:
        visuals = [m for m in members if m.get("block_type") in _VISUALS]
        others = [m for m in members if m.get("block_type") not in _VISUALS]
        if visuals:
            _figure(page, visuals, others)
        else:
            _unclassified(page, node)
    else:
        _unclassified(page, node)


def _unclassified(page: _Page, node: Json) -> None:
    """Retain a block that has no canonical mapping.

    Parameters
    ----------
    page : _Page
        Page state.
    node : dict of str to object
        Marker block.
    """
    block_id = page.block_id("raw", node)
    kind = _string(node.get("block_type")) or "unknown"
    page.finding(
        "BLOCK_UNCLASSIFIED",
        "info",
        f"Marker block type {kind!r} has no canonical mapping and was retained raw.",
        (block_id,),
    )
    raw = json.dumps(node, ensure_ascii=False, sort_keys=True)
    page.blocks.append(Unclassified(block_id, kind, raw, page.span(node)))


def _page_number(node: Json) -> int | None:
    """Read the one-based page number from a page block identifier.

    Parameters
    ----------
    node : dict of str to object
        Marker page block.

    Returns
    -------
    int or None
        Page number, or None.
    """
    parts = _string(node.get("id")).strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "page" and parts[1].isdigit():  # noqa: PLR2004
        return int(parts[1]) + 1
    return None


def normalize_marker(
    native: Mapping[str, object],
    *,
    source_sha256: str,
    coverage: PageCoverage,
    backend_version: str,
    assets: Collection[str],
    page_sizes: Mapping[int, Size] | None = None,
) -> Document:
    """Convert the Marker worker's native output into the canonical schema.

    Parameters
    ----------
    native : Mapping of str to object
        Parsed ``native/marker.json`` wrapper.
    source_sha256 : str
        Digest of the preserved source.
    coverage : PageCoverage
        Verified page coverage from the worker result.
    backend_version : str
        Backend version from the worker result.
    assets : Collection of str
        Result file paths relative to the staging directory.
    page_sizes : Mapping of int to tuple of float or None
        Independent page sizes in points, used when a page block has no box.

    Returns
    -------
    Document
        Canonical document in page order and Marker reading order.

    Raises
    ------
    ValueError
        The wrapper has another schema or version.
    """
    if (
        native.get("schema") != NATIVE_SCHEMA
        or native.get("schema_version") != NATIVE_SCHEMA_VERSION
    ):
        raise ValueError(
            f"Expected {NATIVE_SCHEMA} {NATIVE_SCHEMA_VERSION}, got "
            f"{native.get('schema')!r} {native.get('schema_version')!r}"
        )
    findings: list[Finding] = []
    images = {
        key: name
        for key, name in (_mapping(native.get("images")) or {}).items()
        if isinstance(name, str)
    }
    document = _mapping(native.get("document")) or {}
    sizes: dict[int, Size] = dict(page_sizes or {})
    blocks: list[Block] = []
    for page_node in _children(document):
        number = _page_number(page_node)
        if number is None:
            findings.append(
                Finding("PAGE_MALFORMED", "warning", "A Marker page had no page id.")
            )
            continue
        box = _box(page_node.get("bbox"))
        if box is not None and box[2] > box[0] and box[3] > box[1]:
            sizes[number] = (box[2] - box[0], box[3] - box[1])
        page = _Page(number, sizes.get(number), source_sha256, images, assets, findings)
        for node in _children(page_node):
            _node(page, node)
        blocks.extend(page.blocks)
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
        status: Literal["processed", "empty", "missing"] = "processed"
        if number in coverage.missing:
            status = "missing"
        elif number in coverage.empty:
            status = "empty"
        size = sizes.get(number)
        pages.append(
            PageRecord(
                number,
                None if size is None else size[0],
                None if size is None else size[1],
                status,
            )
        )
    information = _mapping(native.get("pdf_information")) or {}
    metadata = tuple(
        MetadataObservation(
            name, _string(information.get(key)).strip(), _METADATA_SOURCE
        )
        for key, name in _METADATA_FIELDS.items()
        if _string(information.get(key)).strip()
    )
    return Document(
        source_sha256=source_sha256,
        backend="marker",
        backend_version=backend_version,
        native_schema="marker.json-renderer",
        native_schema_version=_string(native.get("backend_version")) or "unknown",
        pages=tuple(pages),
        blocks=tuple(blocks),
        metadata=metadata,
        findings=tuple(findings),
    )
