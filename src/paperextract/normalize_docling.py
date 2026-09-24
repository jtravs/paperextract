"""Normalize Docling native output into canonical structures without repair.

The input is the worker's wrapper ``native/docling.json``: one serialized
``DoclingDocument`` per converted page range, with the files of the picture
and table crops the worker saved. The body tree gives reading order; texts,
formulas, tables and pictures become canonical blocks with their source spans,
and the Docling strings are kept verbatim. Docling's coordinates have a
bottom-left origin and are converted to the top-left points used everywhere
else. Nothing here reads the PDF.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Collection, Mapping
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
from paperextract.normalize import figure_label, table_label
from paperextract.protocol import NATIVE_DIRECTORY, PageCoverage
from paperextract.tables import parse_html_table

__all__ = [
    "NATIVE_SCHEMA",
    "NATIVE_SCHEMA_VERSION",
    "glyph_code_count",
    "normalize_docling",
]

NATIVE_SCHEMA = "paperextract.docling-native"
NATIVE_SCHEMA_VERSION = 1

Json = dict[str, object]
Box = tuple[float, float, float, float]
Size = tuple[float, float]

_METADATA_SOURCE = "pdf_information_via_docling_worker"
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
_PARAGRAPH_ROLES: Mapping[str, Literal["body", "reference", "footnote", "aside"]] = {
    "text": "body",
    "paragraph": "body",
    "reference": "reference",
    "footnote": "footnote",
    "caption": "aside",
}
_FURNITURE: Mapping[str, Literal["header", "footer"]] = {
    "page_header": "header",
    "page_footer": "footer",
}
# Docling writes a glyph without a Unicode mapping as a code such as ".0137".
_GLYPH_CODE = re.compile(r"(?<![0-9])\.0[0-9]{3}(?![0-9])")
_PRINTED_NUMBER = re.compile(r"\((S?\d+[a-z]?)\)")
_REFERENCES_HEADING = re.compile(
    r"^\s*(?:\d+\.?\s*)?(references|bibliography)\s*$", re.I
)
_MIN_GLYPH_CODES = 2
# Page sizes that differ by more than this many points mean another page box.
_SIZE_TOLERANCE = 1.0


def glyph_code_count(text: str) -> int:
    r"""Count Docling's codes for glyphs without a Unicode mapping.

    Parameters
    ----------
    text : str
        Docling text.

    Returns
    -------
    int
        Occurrences of a code such as ``.0137`` not attached to other digits.

    Examples
    --------
    >>> glyph_code_count("S\\u03c4 .0137 e E.0138 0.0137")
    2
    """
    return len(_GLYPH_CODE.findall(text))


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
    return cast("Json", value) if isinstance(value, dict) else None


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
    return cast("list[object]", value) if isinstance(value, list) else []


def _string(value: object) -> str:
    """Return a string value, or an empty string for any other value.

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


def _ref(value: object) -> str:
    """Read the target of a Docling ``{"$ref": ...}`` pointer.

    Parameters
    ----------
    value : object
        Decoded pointer.

    Returns
    -------
    str
        Pointer such as ``#/texts/3``, or ``""``.
    """
    pointer = _mapping(value)
    return "" if pointer is None else _string(pointer.get("$ref"))


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


@dataclass
class _Run:
    """Carry the state of one converted page range while its tree is walked."""

    index: int
    document: Json
    images: Mapping[str, str]
    source_sha256: str
    sizes: Mapping[int, Size]
    assets: Collection[str]
    findings: list[Finding]
    ordinal: int = 0
    blocks: list[tuple[int, int, Block]] = field(
        default_factory=list[tuple[int, int, Block]]
    )
    heading: str = ""

    def item(self, pointer: str) -> Json | None:
        """Resolve a Docling pointer within this run's document.

        Parameters
        ----------
        pointer : str
            Pointer such as ``#/tables/0``.

        Returns
        -------
        dict of str to object or None
            The item, or None for an unknown pointer.
        """
        parts = pointer.split("/")
        if len(parts) != 3 or parts[0] != "#" or not parts[2].isdigit():  # noqa: PLR2004
            return None
        collection = _sequence(self.document.get(parts[1]))
        position = int(parts[2])
        return _mapping(collection[position]) if position < len(collection) else None

    def finding(
        self,
        code: str,
        severity: Literal["info", "warning", "error"],
        message: str,
        block_ids: tuple[str, ...] = (),
        page: int | None = None,
    ) -> None:
        """Record a finding.

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
        page : int or None
            Page of the finding.
        """
        self.findings.append(Finding(code, severity, message, block_ids, page))

    def block_id(self, prefix: str, item: Json) -> str:
        """Derive a stable identifier from the source and the Docling pointer.

        Parameters
        ----------
        prefix : str
            Short kind prefix such as ``par``.
        item : dict of str to object
            Docling item.

        Returns
        -------
        str
            Prefix plus twelve hexadecimal digits.
        """
        self.ordinal += 1
        anchor = (
            f"{self.source_sha256}|docling|{self.index}|"
            f"{_string(item.get('self_ref'))}|{self.ordinal}"
        )
        return f"{prefix}_{hashlib.sha256(anchor.encode()).hexdigest()[:12]}"

    def span(self, item: Json, provenance: Json | None = None) -> SourceSpan:
        """Build the source span of an item from its first provenance record.

        Parameters
        ----------
        item : dict of str to object
            Docling item.
        provenance : dict of str to object or None
            Provenance record to use instead of the item's first.

        Returns
        -------
        SourceSpan
            Span in top-left points; boxes are None without a page size.
        """
        prov = provenance or next(
            iter(map(_mapping, _sequence(item.get("prov")))), None
        )
        page = 1
        points = fraction = None
        if prov is not None:
            page = int(_number(prov.get("page_no")) or 1)
            points = self.box(_mapping(prov.get("bbox")), page)
        size = self.sizes.get(page)
        if points is not None and size is not None:
            width, height = size
            fraction = (
                round(points[0] / width, 6),
                round(points[1] / height, 6),
                round(points[2] / width, 6),
                round(points[3] / height, 6),
            )
        pointer = _string(item.get("self_ref")).rsplit("/", 1)[-1]
        return SourceSpan(
            page=page,
            bbox_pt=points,
            bbox_fraction=fraction,
            backend_type=_string(item.get("label")) or "unknown",
            backend_index=int(pointer) if pointer.isdigit() else None,
        )

    def box(self, bbox: Json | None, page: int) -> Box | None:
        """Convert a Docling box to top-left points.

        Parameters
        ----------
        bbox : dict of str to object or None
            Docling box with ``l``, ``t``, ``r``, ``b`` and ``coord_origin``.
        page : int
            One-based page of the box.

        Returns
        -------
        tuple of float or None
            ``(x0, y0, x1, y1)``, or None when malformed or the page size is
            unknown for a bottom-left box.
        """
        if bbox is None:
            return None
        values = [_number(bbox.get(key)) for key in ("l", "t", "r", "b")]
        if any(value is None for value in values):
            return None
        left, top, right, bottom = cast("list[float]", values)
        if _string(bbox.get("coord_origin")) == "TOPLEFT":
            y0, y1 = top, bottom
        else:
            size = self.sizes.get(page)
            if size is None:
                return None
            y0, y1 = size[1] - top, size[1] - bottom
        return (
            round(left, 2),
            round(min(y0, y1), 2),
            round(right, 2),
            round(max(y0, y1), 2),
        )

    def text_runs(self, item: Json) -> RichText:
        """Return an item's text as one run, flagging glyph codes.

        Parameters
        ----------
        item : dict of str to object
            Docling text item.

        Returns
        -------
        tuple of InlineRun
            One text or code run, or none for empty text.
        """
        text = _string(item.get("text"))
        if not text:
            return ()
        kind: Literal["text", "code"] = (
            "code" if item.get("label") == "code" else "text"
        )
        styles = self.styles(item)
        hyperlink = _string(item.get("hyperlink"))
        if hyperlink and kind == "text":
            return (InlineRun("link", text, styles, hyperlink),)
        return (InlineRun(kind, text, styles),)

    @staticmethod
    def styles(item: Json) -> tuple[str, ...]:
        """Read Docling's character formatting as canonical style names.

        Parameters
        ----------
        item : dict of str to object
            Docling text item.

        Returns
        -------
        tuple of str
            Style names such as ``bold`` in a fixed order.
        """
        formatting = _mapping(item.get("formatting")) or {}
        names = ("bold", "italic", "underline", "strikethrough")
        styles = [name for name in names if formatting.get(name) is True]
        script = _string(formatting.get("script"))
        if script in ("sub", "super"):
            styles.append("subscript" if script == "sub" else "superscript")
        return tuple(styles)

    def emit(self, block: Block, span: SourceSpan, rank: int = 0) -> None:
        """Append a block with its sort key.

        Parameters
        ----------
        block : Block
            Canonical block.
        span : SourceSpan
            Span whose page orders the block.
        rank : int
            ``-1`` for page headers, ``1`` for page footers, ``0`` otherwise.
        """
        self.blocks.append((span.page, rank, block))


def _glyph_check(run: _Run, block_id: str, texts: list[str], page: int) -> None:
    """Record a finding when Docling wrote glyph codes into a block.

    Parameters
    ----------
    run : _Run
        Run state.
    block_id : str
        Block identifier.
    texts : list of str
        Strings of the block.
    page : int
        Page of the block.
    """
    count = sum(glyph_code_count(text) for text in texts)
    if count >= _MIN_GLYPH_CODES:
        run.finding(
            "GLYPH_CODES",
            "warning",
            f"{count} glyph codes such as '.0137' stand for font glyphs without a "
            "Unicode mapping, typically math brackets or accents; the text is "
            "incomplete, so compare it with the page image.",
            (block_id,),
            page,
        )


def _text_block(run: _Run, item: Json) -> None:
    """Normalize a heading, paragraph, footnote, code or orphan caption.

    Parameters
    ----------
    run : _Run
        Run state.
    item : dict of str to object
        Docling text item.
    """
    label = _string(item.get("label"))
    span = run.span(item)
    runs = run.text_runs(item)
    if label in ("title", "section_header"):
        level = _number(item.get("level"))
        block_id = run.block_id("hd", item)
        run.heading = plain_text(runs)
        heading_level = 1 if label == "title" else 1 + int(level or 1)
        run.emit(Heading(block_id, heading_level, runs, span), span)
        return
    if label in _PARAGRAPH_ROLES or label == "code":
        role = _PARAGRAPH_ROLES.get(label, "body")
        block_id = run.block_id("par", item)
        if label == "caption":
            run.finding(
                "CAPTION_UNATTACHED",
                "info",
                "A caption that Docling attached to no table or picture is kept "
                "as a separate paragraph.",
                (block_id,),
                span.page,
            )
        run.emit(Paragraph(block_id, role, runs, span), span)
        _glyph_check(run, block_id, [plain_text(runs)], span.page)
        return
    _unclassified(run, item)


def _unclassified(run: _Run, item: Json) -> None:
    """Retain an item that has no canonical mapping.

    Parameters
    ----------
    run : _Run
        Run state.
    item : dict of str to object
        Docling item.
    """
    block_id = run.block_id("raw", item)
    span = run.span(item)
    label = _string(item.get("label")) or "unknown"
    run.finding(
        "BLOCK_UNCLASSIFIED",
        "info",
        f"Docling item label {label!r} has no canonical mapping and was retained raw.",
        (block_id,),
        span.page,
    )
    raw = json.dumps(item, ensure_ascii=False, sort_keys=True)
    run.emit(Unclassified(block_id, label, raw, span), span)


def _formula(run: _Run, item: Json, *, enriched: bool) -> None:
    """Normalize a display formula.

    Parameters
    ----------
    run : _Run
        Run state.
    item : dict of str to object
        Docling formula item.
    enriched : bool
        Whether Docling's formula model ran; otherwise the text is the PDF
        text layer, not LaTeX.
    """
    block_id = run.block_id("eq", item)
    span = run.span(item)
    original = _string(item.get("orig"))
    numbers = _PRINTED_NUMBER.findall(original)
    label = numbers[0] if len(numbers) == 1 else None
    latex = _string(item.get("text")) if enriched else ""
    if not enriched:
        run.finding(
            "EQUATION_NOT_TRANSCRIBED",
            "warning",
            "Docling's formula model was off, so no LaTeX exists; the text "
            f"layer reads {original[:120]!r}.",
            (block_id,),
            span.page,
        )
    elif not latex.strip():
        run.finding(
            "EQUATION_EMPTY", "warning", "Equation block has no LaTeX.", (block_id,)
        )
    run.emit(Equation(block_id, latex, label, None, span), span)


def _crop(run: _Run, item: Json, block_id: str) -> str | None:
    """Resolve the crop the worker saved for a picture or table.

    Parameters
    ----------
    run : _Run
        Run state.
    item : dict of str to object
        Docling picture or table.
    block_id : str
        Owning block, for the finding.

    Returns
    -------
    str or None
        Path relative to the worker staging directory, or None.
    """
    name = run.images.get(_string(item.get("self_ref")))
    if name is None:
        return None
    path = f"{NATIVE_DIRECTORY}/{name}"
    if path not in run.assets:
        run.finding(
            "ASSET_MISSING",
            "warning",
            f"Docling output references {path}, which the worker did not record.",
            (block_id,),
        )
    return path


def _captions(run: _Run, item: Json, key: str) -> list[tuple[RichText, SourceSpan]]:
    """Resolve an item's caption or footnote pointers.

    Parameters
    ----------
    run : _Run
        Run state.
    item : dict of str to object
        Docling table or picture.
    key : str
        ``captions`` or ``footnotes``.

    Returns
    -------
    list of tuple
        Runs and span of each resolved text.
    """
    resolved: list[tuple[RichText, SourceSpan]] = []
    for pointer in _sequence(item.get(key)):
        text = run.item(_ref(pointer))
        if text is not None:
            resolved.append((run.text_runs(text), run.span(text)))
    return resolved


def _table_html(cells: list[Json], rows: int, columns: int) -> tuple[str, list[str]]:
    """Write Docling's cell list as HTML, filling uncovered positions.

    Parameters
    ----------
    cells : list of dict
        Docling ``table_cells``.
    rows : int
        Grid rows.
    columns : int
        Grid columns.

    Returns
    -------
    tuple
        HTML with one element per cell and an empty ``td`` for every grid
        position no cell covers, and problems found while placing cells.
    """
    problems: list[str] = []
    covered: dict[tuple[int, int], Json] = {}
    starts: dict[tuple[int, int], Json] = {}
    for cell in cells:
        row = int(_number(cell.get("start_row_offset_idx")) or 0)
        column = int(_number(cell.get("start_col_offset_idx")) or 0)
        row_span = max(1, int(_number(cell.get("row_span")) or 1))
        column_span = max(1, int(_number(cell.get("col_span")) or 1))
        if (row, column) in starts:
            continue  # Docling repeats spanning cells once per covered position.
        area = [
            (r, c)
            for r in range(row, row + row_span)
            for c in range(column, column + column_span)
        ]
        if any(position in covered for position in area):
            # Placing it would shift every later cell of the row in the HTML.
            problems.append(
                f"cell {_string(cell.get('text'))!r} at row {row}, column {column} "
                "overlaps another cell and is left out of the grid"
            )
            continue
        starts[(row, column)] = cell
        covered.update(dict.fromkeys(area, cell))
    lines = ["<table>"]
    for row in range(rows):
        parts: list[str] = []
        for column in range(columns):
            cell = starts.get((row, column))
            if cell is None:
                if (row, column) not in covered:
                    parts.append("<td></td>")
                continue
            tag = "th" if cell.get("column_header") or cell.get("row_header") else "td"
            attributes = ""
            row_span = int(_number(cell.get("row_span")) or 1)
            column_span = int(_number(cell.get("col_span")) or 1)
            if row_span > 1:
                attributes += f' rowspan="{row_span}"'
            if column_span > 1:
                attributes += f' colspan="{column_span}"'
            text = html.escape(_string(cell.get("text")), quote=False)
            parts.append(f"<{tag}{attributes}>{text}</{tag}>")
        lines.append("<tr>" + "".join(parts) + "</tr>")
    lines.append("</table>")
    return "".join(lines), problems


def _table(run: _Run, item: Json) -> None:
    """Normalize a table from Docling's cells.

    Parameters
    ----------
    run : _Run
        Run state.
    item : dict of str to object
        Docling table item.
    """
    block_id = run.block_id("tbl", item)
    span = run.span(item)
    data = _mapping(item.get("data")) or {}
    rows = int(_number(data.get("num_rows")) or 0)
    columns = int(_number(data.get("num_cols")) or 0)
    raw_cells = [
        cell for cell in map(_mapping, _sequence(data.get("table_cells"))) if cell
    ]
    captions = _captions(run, item, "captions")
    footnotes = [runs for runs, _span in _captions(run, item, "footnotes")]
    caption = captions[0][0] if captions else None
    footnotes = [runs for runs, _span in captions[1:]] + footnotes
    label = None if caption is None else table_label(plain_text(caption))
    if caption is None:
        run.finding(
            "CAPTION_UNMATCHED",
            "warning",
            "Table has no caption.",
            (block_id,),
            span.page,
        )
    markup = ""
    cells = grid_rows = grid_columns = None
    if not raw_cells or not rows or not columns:
        run.finding(
            "TABLE_BODY_MISSING",
            "warning",
            "Table has no cells.",
            (block_id,),
            span.page,
        )
    else:
        markup, problems = _table_html(raw_cells, rows, columns)
        grid = parse_html_table(markup)
        cells, grid_rows, grid_columns = grid.cells, grid.rows, grid.columns
        problems.extend(grid.problems)
        if problems:
            run.finding(
                "TABLE_STRUCTURE_UNRESOLVED",
                "warning",
                "Grid problems kept for review: " + "; ".join(problems),
                (block_id,),
                span.page,
            )
        _glyph_check(run, block_id, [cell.text for cell in grid.cells], span.page)
    run.emit(
        Table(
            id=block_id,
            label=label,
            caption=caption,
            footnotes=tuple(footnotes),
            html=markup,
            cells=cells,
            rows=grid_rows,
            columns=grid_columns,
            asset=_crop(run, item, block_id),
            span=span,
            continues_previous=caption is not None
            and re.search(r"\(\s*continued\s*\)", plain_text(caption), re.I)
            is not None,
        ),
        span,
    )


def _picture(run: _Run, item: Json) -> None:
    """Normalize a picture as a figure with one panel.

    Parameters
    ----------
    run : _Run
        Run state.
    item : dict of str to object
        Docling picture item.

    Notes
    -----
    Docling reports a composite figure as one picture, so no panel grouping
    heuristic is needed; text inside the picture is not part of the body.
    """
    panel_id = run.block_id("panel", item)
    span = run.span(item)
    panel = Panel(panel_id, _crop(run, item, panel_id), None, span)
    figure_id = f"fig_{hashlib.sha256(panel_id.encode()).hexdigest()[:12]}"
    captions = _captions(run, item, "captions")
    footnotes = [runs for runs, _span in captions[1:]]
    footnotes += [runs for runs, _span in _captions(run, item, "footnotes")]
    caption = captions[0] if captions else None
    boxes: list[Box | None] = [span.bbox_pt]
    if caption is None:
        run.finding(
            "CAPTION_UNMATCHED",
            "warning",
            "A picture has no figure caption; grouping is unresolved.",
            (figure_id,),
            span.page,
        )
    elif caption[1].page == span.page:
        boxes.append(caption[1].bbox_pt)
    run.emit(
        Figure(
            id=figure_id,
            label=None if caption is None else figure_label(plain_text(caption[0])),
            caption=None if caption is None else caption[0],
            caption_span=None if caption is None else caption[1],
            footnotes=tuple(footnotes),
            panels=(panel,),
            context_bbox_pt=_union(boxes),
            grouping="unresolved" if caption is None else "single",
            page=span.page,
        ),
        span,
    )


def _list(run: _Run, group: Json, members: list[Json]) -> None:
    """Normalize a list group, or reference entries below a references heading.

    Parameters
    ----------
    run : _Run
        Run state.
    group : dict of str to object
        Docling list group.
    members : list of dict
        Its list items in order.
    """
    if not members:
        return
    if _REFERENCES_HEADING.match(run.heading):
        for member in members:
            span = run.span(member)
            marker = _string(member.get("marker"))
            runs = run.text_runs(member)
            if marker and runs:
                runs = (InlineRun("text", f"{marker} "), *runs)
            run.emit(
                Paragraph(run.block_id("par", member), "reference", runs, span), span
            )
        return
    span = run.span(members[0])
    items = tuple(run.text_runs(member) for member in members)
    run.emit(ListBlock(run.block_id("list", group), items, span), span)


def _walk(run: _Run, pointer: str, *, enriched: bool) -> None:
    """Normalize one node of the body tree and its children in order.

    Parameters
    ----------
    run : _Run
        Run state.
    pointer : str
        Pointer to the node.
    enriched : bool
        Whether formulas carry LaTeX.
    """
    node = run.item(pointer)
    if node is None or node.get("content_layer") == "furniture":
        return
    kind = pointer.split("/")[1]
    label = _string(node.get("label"))
    if kind == "tables":
        _table(run, node)
    elif kind == "pictures":
        _picture(run, node)
    elif kind == "groups":
        children = [run.item(_ref(child)) for child in _sequence(node.get("children"))]
        if label in ("list", "ordered_list"):
            members = [
                child
                for child in children
                if child is not None and child.get("label") == "list_item"
            ]
            _list(run, node, members)
            others = [_ref(child) for child in _sequence(node.get("children"))]
            for child_pointer, child in zip(others, children, strict=True):
                if child is not None and child.get("label") != "list_item":
                    _walk(run, child_pointer, enriched=enriched)
            return
        for child in _sequence(node.get("children")):
            _walk(run, _ref(child), enriched=enriched)
    elif label == "formula":
        _formula(run, node, enriched=enriched)
    elif label == "list_item":
        _list(run, node, [node])
    elif kind == "texts":
        _text_block(run, node)
    else:
        _unclassified(run, node)


def _furniture(run: _Run) -> None:
    """Normalize the page headers and footers of a run.

    Parameters
    ----------
    run : _Run
        Run state.
    """
    for value in _sequence(run.document.get("texts")):
        item = _mapping(value)
        if item is None or item.get("content_layer") != "furniture":
            continue
        role = _FURNITURE.get(_string(item.get("label")))
        span = run.span(item)
        if role is None:
            _unclassified(run, item)
            continue
        block = PageFurniture(
            run.block_id("aux", item), role, run.text_runs(item), span
        )
        run.emit(block, span, -1 if role == "header" else 1)


def _metadata(native: Mapping[str, object]) -> tuple[MetadataObservation, ...]:
    """Collect the PDF information dictionary the worker read.

    Parameters
    ----------
    native : Mapping of str to object
        Wrapper document.

    Returns
    -------
    tuple of MetadataObservation
        Observations in a fixed field order.
    """
    information = _mapping(native.get("pdf_information")) or {}
    observations: list[MetadataObservation] = []
    for key, name in _METADATA_FIELDS.items():
        value = _string(information.get(key)).strip()
        if value:
            observations.append(MetadataObservation(name, value, _METADATA_SOURCE))
    return tuple(observations)


def _run_sizes(
    document: Json, page_sizes: Mapping[int, Size], findings: list[Finding]
) -> dict[int, Size]:
    """Read Docling's page sizes and compare them with the inspection.

    Parameters
    ----------
    document : dict of str to object
        Serialized ``DoclingDocument``.
    page_sizes : Mapping of int to tuple of float
        Independent page sizes in points.
    findings : list of Finding
        Findings to extend.

    Returns
    -------
    dict of int to tuple of float
        Docling's measured sizes, which its coordinates refer to.
    """
    sizes: dict[int, Size] = {}
    pages = _mapping(document.get("pages")) or {}
    for key, value in pages.items():
        page = _mapping(value) or {}
        size = _mapping(page.get("size")) or {}
        width, height = _number(size.get("width")), _number(size.get("height"))
        if not key.isdigit() or width is None or height is None:
            continue
        number = int(key)
        known = page_sizes.get(number)
        if known is not None and (
            abs(known[0] - width) > _SIZE_TOLERANCE
            or abs(known[1] - height) > _SIZE_TOLERANCE
        ):
            findings.append(
                Finding(
                    "PAGE_SIZE_MISMATCH",
                    "warning",
                    f"Docling measured page {number} as {width}x{height} pt, the "
                    f"inspection as {known[0]}x{known[1]} pt; boxes follow Docling.",
                    (),
                    number,
                )
            )
        sizes[number] = (width, height)
    return sizes


def _page_records(
    coverage: PageCoverage, sizes: Mapping[int, Size], findings: list[Finding]
) -> tuple[PageRecord, ...]:
    """Build page records and coverage findings.

    Parameters
    ----------
    coverage : PageCoverage
        Verified coverage.
    sizes : Mapping of int to tuple of float
        Page sizes.
    findings : list of Finding
        Findings to extend.

    Returns
    -------
    tuple of PageRecord
        One record per requested page.
    """
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
                f"Page {number} returned no items; content is unrecovered.",
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
                number=number,
                width_pt=None if size is None else size[0],
                height_pt=None if size is None else size[1],
                status=status,
            )
        )
    return tuple(pages)


def normalize_docling(
    native: Mapping[str, object],
    *,
    source_sha256: str,
    coverage: PageCoverage,
    backend_version: str,
    assets: Collection[str],
    page_sizes: Mapping[int, Size] | None = None,
) -> Document:
    """Convert the Docling worker's native output into the canonical schema.

    Parameters
    ----------
    native : Mapping of str to object
        Parsed ``native/docling.json`` wrapper.
    source_sha256 : str
        Digest of the preserved source.
    coverage : PageCoverage
        Verified page coverage from the worker result.
    backend_version : str
        Backend version from the worker result.
    assets : Collection of str
        Result file paths relative to the staging directory.
    page_sizes : Mapping of int to tuple of float or None
        Independent page sizes in points by one-based page number.

    Returns
    -------
    Document
        Canonical document in page order and Docling reading order.

    Raises
    ------
    ValueError
        The wrapper has another schema or version.

    Notes
    -----
    Strings are copied verbatim. The wrapper's ``formulas`` flag says
    whether Docling's formula model ran; without it formulas have no LaTeX
    and are reported. Picture-internal text, such as axis labels,
    is left in the native output only. Page headers open and footers close
    their page's blocks.
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
    formulas = native.get("formulas") is True
    sizes: dict[int, Size] = dict(page_sizes or {})
    keyed: list[tuple[int, int, Block]] = []
    docling_schema = docling_version = "unknown"
    for index, value in enumerate(_sequence(native.get("runs")), 1):
        entry = _mapping(value) or {}
        document = _mapping(entry.get("document")) or {}
        docling_schema = _string(document.get("schema_name")) or docling_schema
        docling_version = _string(document.get("version")) or docling_version
        images = {
            key: name
            for key, name in (_mapping(entry.get("images")) or {}).items()
            if isinstance(name, str)
        }
        for error in _sequence(entry.get("errors")):
            findings.append(
                Finding("BACKEND_ERROR", "warning", f"Docling reported: {error}")
            )
        measured = _run_sizes(document, page_sizes or {}, findings)
        sizes.update(measured)
        run_sizes = {**(page_sizes or {}), **measured}
        run = _Run(index, document, images, source_sha256, run_sizes, assets, findings)
        body = _mapping(document.get("body")) or {}
        for child in _sequence(body.get("children")):
            _walk(run, _ref(child), enriched=formulas)
        _furniture(run)
        keyed.extend(run.blocks)
    # Stable: reading order within a page, headers first and footers last.
    keyed.sort(key=lambda entry: (entry[0], entry[1]))
    pages = _page_records(coverage, sizes, findings)
    return Document(
        source_sha256=source_sha256,
        backend="docling",
        backend_version=backend_version,
        native_schema=docling_schema,
        native_schema_version=docling_version,
        pages=pages,
        blocks=tuple(block for _page, _rank, block in keyed),
        metadata=_metadata(native),
        findings=tuple(findings),
    )
