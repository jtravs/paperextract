"""Canonical scientific document structures produced by normalization.

Schema ``paperextract.document`` is a pre-release contract: the field set is
expected to grow before 1.0. The versions it reads are listed in
:mod:`paperextract.formats`; ``paperextract migrate`` rebuilds older
documents. Every block keeps its source span and raw backend strings; nothing
here repairs, reflows or re-interprets values.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, cast

from paperextract.fields import (
    boolean,
    choice,
    integer,
    items,
    mapping,
    number,
    record,
    string,
    text,
)
from paperextract.formats import READABLE_VERSIONS

__all__ = [
    "SCHEMA_NAME",
    "SCHEMA_VERSION",
    "Block",
    "Document",
    "Equation",
    "Figure",
    "Finding",
    "Heading",
    "InlineRun",
    "ListBlock",
    "MetadataObservation",
    "PageFurniture",
    "PageRecord",
    "Panel",
    "Paragraph",
    "RichText",
    "SourceSpan",
    "Table",
    "TableBody",
    "TableCell",
    "Unclassified",
    "block_from_dict",
    "plain_text",
]

SCHEMA_NAME = "paperextract.document"
SCHEMA_VERSION = "0.3"
# Version 0.1 lacks the table body source and alternatives; it reads as native
# bodies without alternatives. Version 0.3 adds Docling alternative bodies.
_READABLE_VERSIONS = frozenset(READABLE_VERSIONS[SCHEMA_NAME])
BodySource = Literal["native", "ocr", "docling"]

Box = tuple[float, float, float, float]
Severity = Literal["info", "warning", "error"]
_BOX_LENGTH = 4


@dataclass(frozen=True)
class SourceSpan:
    """Locate a block in the source PDF and in the backend's own output.

    Attributes
    ----------
    page : int
        One-based PDF page number.
    bbox_pt : tuple of float or None
        ``(x0, y0, x1, y1)`` in points with a top-left origin, derived from the
        backend's fractional box and the page size; None when either is unknown.
    bbox_fraction : tuple of float or None
        The backend's raw box as fractions of the page width and height.
    backend_type : str
        Backend block type, kept verbatim.
    backend_index : int or None
        Backend reading-order index within the page.
    """

    page: int
    bbox_pt: Box | None
    bbox_fraction: Box | None
    backend_type: str
    backend_index: int | None

    @classmethod
    def from_dict(cls, value: object) -> SourceSpan:
        """Parse a serialized span.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        SourceSpan
            Validated span.
        """
        data = record(value, "page bbox_pt bbox_fraction backend_type backend_index")
        index = data["backend_index"]
        return cls(
            page=integer(data["page"], minimum=1),
            bbox_pt=_box_from(data["bbox_pt"]),
            bbox_fraction=_box_from(data["bbox_fraction"]),
            backend_type=string(data["backend_type"]),
            backend_index=None if index is None else integer(index, minimum=0),
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize the span.

        Returns
        -------
        dict of str to object
            JSON-compatible fields.
        """
        return {
            "page": self.page,
            "bbox_pt": None if self.bbox_pt is None else list(self.bbox_pt),
            "bbox_fraction": None
            if self.bbox_fraction is None
            else list(self.bbox_fraction),
            "backend_type": self.backend_type,
            "backend_index": self.backend_index,
        }


@dataclass(frozen=True)
class InlineRun:
    """Hold one run of rich inline content.

    Attributes
    ----------
    kind : str
        ``text``, ``math`` (raw LaTeX without delimiters), ``code`` or ``link``.
    text : str
        Raw content of the run, never rewritten.
    styles : tuple of str
        Backend style names such as ``bold`` or ``superscript``.
    url : str or None
        Link target for runs that sit inside a hyperlink.
    """

    kind: Literal["text", "math", "code", "link"]
    text: str
    styles: tuple[str, ...] = ()
    url: str | None = None

    @classmethod
    def from_dict(cls, value: object) -> InlineRun:
        """Parse a serialized run.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        InlineRun
            Validated run.
        """
        data = record(value, "kind text styles url")
        url = data["url"]
        return cls(
            kind=cast(
                "Literal['text', 'math', 'code', 'link']",
                choice(data["kind"], "text math code link"),
            ),
            text=text(data["text"]),
            styles=tuple(string(item) for item in items(data["styles"])),
            url=None if url is None else string(url),
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize the run.

        Returns
        -------
        dict of str to object
            JSON-compatible fields.
        """
        return {
            "kind": self.kind,
            "text": self.text,
            "styles": list(self.styles),
            "url": self.url,
        }


RichText = tuple[InlineRun, ...]


def plain_text(runs: RichText) -> str:
    """Concatenate runs for label detection and diagnostics.

    Parameters
    ----------
    runs : tuple of InlineRun
        Rich text runs.

    Returns
    -------
    str
        Run texts joined without delimiters; math appears as raw LaTeX. This is
        a diagnostic view, not the export representation.
    """
    return "".join(run.text for run in runs)


def _optional_string(value: object) -> str | None:
    """Accept None or a non-blank string.

    Parameters
    ----------
    value : object
        Decoded JSON value.

    Returns
    -------
    str or None
        Validated value.
    """
    return None if value is None else string(value)


def _box_from(value: object) -> Box | None:
    """Accept None or a four-number box.

    Parameters
    ----------
    value : object
        Decoded JSON value.

    Returns
    -------
    tuple of float or None
        Validated box.
    """
    if value is None:
        return None
    numbers = [number(item) for item in items(value)]
    if len(numbers) != _BOX_LENGTH:
        raise ValueError("Expected a box of four numbers")
    return (numbers[0], numbers[1], numbers[2], numbers[3])


def _runs_from(value: object) -> RichText:
    """Parse serialized rich text.

    Parameters
    ----------
    value : object
        Decoded JSON array of runs.

    Returns
    -------
    tuple of InlineRun
        Validated runs.
    """
    return tuple(InlineRun.from_dict(item) for item in items(value))


def _runs(runs: RichText) -> list[dict[str, object]]:
    """Serialize rich text.

    Parameters
    ----------
    runs : tuple of InlineRun
        Rich text runs.

    Returns
    -------
    list of dict
        JSON-compatible runs.
    """
    return [run.to_dict() for run in runs]


@dataclass(frozen=True)
class Finding:
    """Record one validation observation about the document.

    Attributes
    ----------
    code : str
        Stable upper-case code such as ``PAGE_NOT_PROCESSED``.
    severity : str
        ``info``, ``warning`` or ``error``.
    message : str
        Human-readable explanation.
    block_ids : tuple of str
        Affected block identifiers, if any.
    page : int or None
        Affected one-based page, if the finding concerns one page.
    """

    code: str
    severity: Severity
    message: str
    block_ids: tuple[str, ...] = ()
    page: int | None = None

    @classmethod
    def from_dict(cls, value: object) -> Finding:
        """Parse a serialized finding.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        Finding
            Validated finding.
        """
        data = record(value, "code severity message block_ids page")
        page = data["page"]
        return cls(
            code=string(data["code"]),
            severity=cast("Severity", choice(data["severity"], "info warning error")),
            message=string(data["message"]),
            block_ids=tuple(string(item) for item in items(data["block_ids"])),
            page=None if page is None else integer(page, minimum=1),
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize the finding.

        Returns
        -------
        dict of str to object
            JSON-compatible fields.
        """
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "block_ids": list(self.block_ids),
            "page": self.page,
        }


@dataclass(frozen=True)
class Heading:
    """Represent a document or section title.

    Attributes
    ----------
    id : str
        Stable block identifier.
    level : int
        Heading level as reported by the backend; 1 is the document title.
    runs : tuple of InlineRun
        Title text.
    span : SourceSpan
        Source location.
    """

    id: str
    level: int
    runs: RichText
    span: SourceSpan

    @classmethod
    def from_dict(cls, value: object) -> Heading:
        """Parse a serialized heading.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        Heading
            Validated block.
        """
        data = record(value, "kind id level runs span")
        choice(data["kind"], "heading")
        return cls(
            id=string(data["id"]),
            level=integer(data["level"], minimum=1),
            runs=_runs_from(data["runs"]),
            span=SourceSpan.from_dict(data["span"]),
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize the block.

        Returns
        -------
        dict of str to object
            JSON-compatible fields with ``kind``.
        """
        return {
            "kind": "heading",
            "id": self.id,
            "level": self.level,
            "runs": _runs(self.runs),
            "span": self.span.to_dict(),
        }


@dataclass(frozen=True)
class Paragraph:
    """Represent one prose, reference, footnote or aside block.

    Attributes
    ----------
    id : str
        Stable block identifier.
    role : str
        ``body``, ``reference``, ``footnote`` or ``aside``.
    runs : tuple of InlineRun
        Paragraph content.
    span : SourceSpan
        Source location.
    continues_previous : bool
        Whether the backend marks this block as continuing the previous
        paragraph across a page or column break. Blocks are not merged here.
    """

    id: str
    role: Literal["body", "reference", "footnote", "aside"]
    runs: RichText
    span: SourceSpan
    continues_previous: bool = False

    @classmethod
    def from_dict(cls, value: object) -> Paragraph:
        """Parse a serialized paragraph.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        Paragraph
            Validated block.
        """
        data = record(value, "kind id role runs span continues_previous")
        choice(data["kind"], "paragraph")
        return cls(
            id=string(data["id"]),
            role=cast(
                "Literal['body', 'reference', 'footnote', 'aside']",
                choice(data["role"], "body reference footnote aside"),
            ),
            runs=_runs_from(data["runs"]),
            span=SourceSpan.from_dict(data["span"]),
            continues_previous=boolean(data["continues_previous"]),
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize the block.

        Returns
        -------
        dict of str to object
            JSON-compatible fields with ``kind``.
        """
        return {
            "kind": "paragraph",
            "id": self.id,
            "role": self.role,
            "runs": _runs(self.runs),
            "span": self.span.to_dict(),
            "continues_previous": self.continues_previous,
        }


@dataclass(frozen=True)
class ListBlock:
    """Represent a list whose items are rich text.

    Attributes
    ----------
    id : str
        Stable block identifier.
    items : tuple of tuple of InlineRun
        Items in order; nested lists are flattened and reported as a finding.
    span : SourceSpan
        Source location.
    continues_previous : bool
        Whether the backend marks the list as continuing across a break.
    """

    id: str
    items: tuple[RichText, ...]
    span: SourceSpan
    continues_previous: bool = False

    @classmethod
    def from_dict(cls, value: object) -> ListBlock:
        """Parse a serialized list.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        ListBlock
            Validated block.
        """
        data = record(value, "kind id items span continues_previous")
        choice(data["kind"], "list")
        return cls(
            id=string(data["id"]),
            items=tuple(_runs_from(item) for item in items(data["items"])),
            span=SourceSpan.from_dict(data["span"]),
            continues_previous=boolean(data["continues_previous"]),
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize the block.

        Returns
        -------
        dict of str to object
            JSON-compatible fields with ``kind``.
        """
        return {
            "kind": "list",
            "id": self.id,
            "items": [_runs(item) for item in self.items],
            "span": self.span.to_dict(),
            "continues_previous": self.continues_previous,
        }


@dataclass(frozen=True)
class Equation:
    r"""Represent a display equation.

    Attributes
    ----------
    id : str
        Stable block identifier.
    latex : str
        Raw LaTeX from the backend, including any ``\tag``; never repaired.
    label : str or None
        Printed equation number extracted from the ``\tag`` argument, if any.
    asset : str or None
        Relative path of the backend's equation crop, when present.
    span : SourceSpan
        Source location.
    """

    id: str
    latex: str
    label: str | None
    asset: str | None
    span: SourceSpan

    @classmethod
    def from_dict(cls, value: object) -> Equation:
        """Parse a serialized equation.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        Equation
            Validated block.
        """
        data = record(value, "kind id latex label asset span")
        choice(data["kind"], "equation")
        return cls(
            id=string(data["id"]),
            latex=text(data["latex"]),
            label=_optional_string(data["label"]),
            asset=_optional_string(data["asset"]),
            span=SourceSpan.from_dict(data["span"]),
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize the block.

        Returns
        -------
        dict of str to object
            JSON-compatible fields with ``kind``.
        """
        return {
            "kind": "equation",
            "id": self.id,
            "latex": self.latex,
            "label": self.label,
            "asset": self.asset,
            "span": self.span.to_dict(),
        }


@dataclass(frozen=True)
class Panel:
    """Represent one backend visual block that belongs to a figure.

    Attributes
    ----------
    id : str
        Stable identifier.
    asset : str or None
        Relative path of the backend crop for this panel.
    label : str or None
        Panel letter when the backend attached one as a caption.
    span : SourceSpan
        Source location of the crop.
    """

    id: str
    asset: str | None
    label: str | None
    span: SourceSpan

    @classmethod
    def from_dict(cls, value: object) -> Panel:
        """Parse a serialized panel.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        Panel
            Validated panel.
        """
        data = record(value, "id asset label span")
        return cls(
            id=string(data["id"]),
            asset=_optional_string(data["asset"]),
            label=_optional_string(data["label"]),
            span=SourceSpan.from_dict(data["span"]),
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize the panel.

        Returns
        -------
        dict of str to object
            JSON-compatible fields.
        """
        return {
            "id": self.id,
            "asset": self.asset,
            "label": self.label,
            "span": self.span.to_dict(),
        }


@dataclass(frozen=True)
class Figure:
    """Represent a figure with its published caption and panel crops.

    Attributes
    ----------
    id : str
        Stable block identifier.
    label : str or None
        Printed figure number such as ``2`` or ``S1``, from the caption.
    caption : tuple of InlineRun or None
        Published caption; None when no caption was associated.
    caption_span : SourceSpan or None
        Source location of the caption.
    footnotes : tuple of tuple of InlineRun
        Backend figure footnotes.
    panels : tuple of Panel
        Individual crops in reading order; the composite is their union.
    context_bbox_pt : tuple of float or None
        Union of panel and caption boxes in points, for a complete-figure crop.
    grouping : str
        ``single`` for one captioned block, ``caption_run`` for consecutive
        blocks grouped up to a caption, ``unresolved`` when no caption anchors
        the group.
    page : int
        One-based page.
    """

    id: str
    label: str | None
    caption: RichText | None
    caption_span: SourceSpan | None
    footnotes: tuple[RichText, ...]
    panels: tuple[Panel, ...]
    context_bbox_pt: Box | None
    grouping: Literal["single", "caption_run", "unresolved"]
    page: int

    @classmethod
    def from_dict(cls, value: object) -> Figure:
        """Parse a serialized figure.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        Figure
            Validated block.
        """
        data = record(
            value,
            "kind id label caption caption_span footnotes panels context_bbox_pt "
            "grouping page",
        )
        choice(data["kind"], "figure")
        caption = data["caption"]
        caption_span = data["caption_span"]
        return cls(
            id=string(data["id"]),
            label=_optional_string(data["label"]),
            caption=None if caption is None else _runs_from(caption),
            caption_span=None
            if caption_span is None
            else SourceSpan.from_dict(caption_span),
            footnotes=tuple(_runs_from(item) for item in items(data["footnotes"])),
            panels=tuple(Panel.from_dict(item) for item in items(data["panels"])),
            context_bbox_pt=_box_from(data["context_bbox_pt"]),
            grouping=cast(
                "Literal['single', 'caption_run', 'unresolved']",
                choice(data["grouping"], "single caption_run unresolved"),
            ),
            page=integer(data["page"], minimum=1),
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize the block.

        Returns
        -------
        dict of str to object
            JSON-compatible fields with ``kind``.
        """
        return {
            "kind": "figure",
            "id": self.id,
            "label": self.label,
            "caption": None if self.caption is None else _runs(self.caption),
            "caption_span": None
            if self.caption_span is None
            else self.caption_span.to_dict(),
            "footnotes": [_runs(item) for item in self.footnotes],
            "panels": [panel.to_dict() for panel in self.panels],
            "context_bbox_pt": None
            if self.context_bbox_pt is None
            else list(self.context_bbox_pt),
            "grouping": self.grouping,
            "page": self.page,
        }


@dataclass(frozen=True)
class TableCell:
    """Hold one table cell exactly as the backend wrote it.

    Attributes
    ----------
    row : int
        Zero-based grid row of the cell's top-left corner.
    column : int
        Zero-based grid column of the cell's top-left corner.
    row_span : int
        Number of rows covered.
    column_span : int
        Number of columns covered.
    html : str
        Raw inner HTML, retaining superscripts, math and entities.
    text : str
        Tag-free text with entities decoded and whitespace collapsed.
    """

    row: int
    column: int
    row_span: int
    column_span: int
    html: str
    text: str

    @classmethod
    def from_dict(cls, value: object) -> TableCell:
        """Parse a serialized cell.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        TableCell
            Validated cell.
        """
        data = record(value, "row column row_span column_span html text")
        return cls(
            row=integer(data["row"], minimum=0),
            column=integer(data["column"], minimum=0),
            row_span=integer(data["row_span"], minimum=1),
            column_span=integer(data["column_span"], minimum=1),
            html=text(data["html"]),
            text=text(data["text"]),
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize the cell.

        Returns
        -------
        dict of str to object
            JSON-compatible fields.
        """
        return {
            "row": self.row,
            "column": self.column,
            "row_span": self.row_span,
            "column_span": self.column_span,
            "html": self.html,
            "text": self.text,
        }


@dataclass(frozen=True)
class TableBody:
    """Hold one extraction of a table body kept as an alternative.

    Attributes
    ----------
    source : str
        ``native`` for the backend's first extraction, ``ocr`` for a targeted
        page-level OCR re-extraction, ``docling`` for the independent reading
        of the table cross-check.
    html : str
        Raw backend HTML of this extraction.
    cells : tuple of TableCell or None
        Parsed grid, or None when the HTML could not be parsed.
    rows : int or None
        Grid row count when parsed.
    columns : int or None
        Grid column count when parsed.
    """

    source: BodySource
    html: str
    cells: tuple[TableCell, ...] | None
    rows: int | None
    columns: int | None

    @classmethod
    def from_dict(cls, value: object) -> TableBody:
        """Parse a serialized table body.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        TableBody
            Validated body.
        """
        data = record(value, "source html cells rows columns")
        cells = data["cells"]
        rows = data["rows"]
        columns = data["columns"]
        return cls(
            source=cast("BodySource", choice(data["source"], "native ocr docling")),
            html=text(data["html"]),
            cells=None
            if cells is None
            else tuple(TableCell.from_dict(item) for item in items(cells)),
            rows=None if rows is None else integer(rows, minimum=1),
            columns=None if columns is None else integer(columns, minimum=1),
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize the body.

        Returns
        -------
        dict of str to object
            JSON-compatible fields.
        """
        return {
            "source": self.source,
            "html": self.html,
            "cells": None
            if self.cells is None
            else [cell.to_dict() for cell in self.cells],
            "rows": self.rows,
            "columns": self.columns,
        }


@dataclass(frozen=True)
class Table:
    """Represent a table with raw HTML and, when parseable, an exact cell grid.

    Attributes
    ----------
    id : str
        Stable block identifier.
    label : str or None
        Printed table number such as ``1`` or ``S1``.
    caption : tuple of InlineRun or None
        Caption runs, possibly recovered from a misclassified footnote.
    footnotes : tuple of tuple of InlineRun
        Table footnotes in order.
    html : str
        Raw backend HTML, kept verbatim.
    cells : tuple of TableCell or None
        Parsed grid, or None when the HTML could not be parsed at all.
    rows : int or None
        Grid row count when parsed.
    columns : int or None
        Grid column count when parsed.
    asset : str or None
        Relative path of the backend's table crop.
    span : SourceSpan
        Source location of the table body.
    continues_previous : bool
        Whether the backend marks this as a continuation of a previous table.
    body_source : str
        Extraction that supplied ``html`` and ``cells``: ``native`` or
        ``ocr``; the choice is explained by a finding.
    alternatives : tuple of TableBody
        Other extractions of the same body, such as the native text-layer
        version replaced by OCR, kept for review.
    """

    id: str
    label: str | None
    caption: RichText | None
    footnotes: tuple[RichText, ...]
    html: str
    cells: tuple[TableCell, ...] | None
    rows: int | None
    columns: int | None
    asset: str | None
    span: SourceSpan
    continues_previous: bool = False
    body_source: Literal["native", "ocr"] = "native"
    alternatives: tuple[TableBody, ...] = ()

    @classmethod
    def from_dict(cls, value: object) -> Table:
        """Parse a serialized table.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        Table
            Validated block.
        """
        keys = (
            "kind id label caption footnotes html cells rows columns asset span "
            "continues_previous"
        )
        legacy = "alternatives" not in mapping(value)
        data = record(value, keys if legacy else f"{keys} body_source alternatives")
        choice(data["kind"], "table")
        body_source = "native" if legacy else choice(data["body_source"], "native ocr")
        alternatives = (
            ()
            if legacy
            else tuple(
                TableBody.from_dict(item) for item in items(data["alternatives"])
            )
        )
        caption = data["caption"]
        cells = data["cells"]
        rows = data["rows"]
        columns = data["columns"]
        return cls(
            id=string(data["id"]),
            label=_optional_string(data["label"]),
            caption=None if caption is None else _runs_from(caption),
            footnotes=tuple(_runs_from(item) for item in items(data["footnotes"])),
            html=text(data["html"]),
            cells=None
            if cells is None
            else tuple(TableCell.from_dict(item) for item in items(cells)),
            rows=None if rows is None else integer(rows, minimum=1),
            columns=None if columns is None else integer(columns, minimum=1),
            asset=_optional_string(data["asset"]),
            span=SourceSpan.from_dict(data["span"]),
            continues_previous=boolean(data["continues_previous"]),
            body_source=cast("Literal['native', 'ocr']", body_source),
            alternatives=alternatives,
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize the block.

        Returns
        -------
        dict of str to object
            JSON-compatible fields with ``kind``.
        """
        return {
            "kind": "table",
            "id": self.id,
            "label": self.label,
            "caption": None if self.caption is None else _runs(self.caption),
            "footnotes": [_runs(item) for item in self.footnotes],
            "html": self.html,
            "cells": None
            if self.cells is None
            else [cell.to_dict() for cell in self.cells],
            "rows": self.rows,
            "columns": self.columns,
            "asset": self.asset,
            "span": self.span.to_dict(),
            "continues_previous": self.continues_previous,
            "body_source": self.body_source,
            "alternatives": [item.to_dict() for item in self.alternatives],
        }


@dataclass(frozen=True)
class PageFurniture:
    """Retain running headers, footers and page numbers for audit.

    Attributes
    ----------
    id : str
        Stable block identifier.
    role : str
        ``header``, ``footer`` or ``page_number``.
    runs : tuple of InlineRun
        Furniture text.
    span : SourceSpan
        Source location.
    """

    id: str
    role: Literal["header", "footer", "page_number"]
    runs: RichText
    span: SourceSpan

    @classmethod
    def from_dict(cls, value: object) -> PageFurniture:
        """Parse serialized page furniture.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        PageFurniture
            Validated block.
        """
        data = record(value, "kind id role runs span")
        choice(data["kind"], "page_furniture")
        return cls(
            id=string(data["id"]),
            role=cast(
                "Literal['header', 'footer', 'page_number']",
                choice(data["role"], "header footer page_number"),
            ),
            runs=_runs_from(data["runs"]),
            span=SourceSpan.from_dict(data["span"]),
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize the block.

        Returns
        -------
        dict of str to object
            JSON-compatible fields with ``kind``.
        """
        return {
            "kind": "page_furniture",
            "id": self.id,
            "role": self.role,
            "runs": _runs(self.runs),
            "span": self.span.to_dict(),
        }


@dataclass(frozen=True)
class Unclassified:
    """Retain a backend block that has no canonical mapping yet.

    Attributes
    ----------
    id : str
        Stable block identifier.
    backend_type : str
        Backend block type.
    raw : str
        The backend block serialized as JSON, so nothing is lost.
    span : SourceSpan
        Source location.
    """

    id: str
    backend_type: str
    raw: str
    span: SourceSpan

    @classmethod
    def from_dict(cls, value: object) -> Unclassified:
        """Parse a serialized unclassified block.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        Unclassified
            Validated block.
        """
        data = record(value, "kind id backend_type raw span")
        choice(data["kind"], "unclassified")
        return cls(
            id=string(data["id"]),
            backend_type=string(data["backend_type"]),
            raw=text(data["raw"]),
            span=SourceSpan.from_dict(data["span"]),
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize the block.

        Returns
        -------
        dict of str to object
            JSON-compatible fields with ``kind``.
        """
        return {
            "kind": "unclassified",
            "id": self.id,
            "backend_type": self.backend_type,
            "raw": self.raw,
            "span": self.span.to_dict(),
        }


Block = (
    Heading
    | Paragraph
    | ListBlock
    | Equation
    | Figure
    | Table
    | PageFurniture
    | Unclassified
)


@dataclass(frozen=True)
class PageRecord:
    """Summarize one requested page.

    Attributes
    ----------
    number : int
        One-based page number.
    width_pt : float or None
        Page width in points, when known.
    height_pt : float or None
        Page height in points, when known.
    status : str
        ``processed``, ``empty`` (returned without blocks) or ``missing``
        (requested but not returned by the worker).
    """

    number: int
    width_pt: float | None
    height_pt: float | None
    status: Literal["processed", "empty", "missing"]

    @classmethod
    def from_dict(cls, value: object) -> PageRecord:
        """Parse a serialized page record.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        PageRecord
            Validated record.
        """
        data = record(value, "number width_pt height_pt status")
        width = data["width_pt"]
        height = data["height_pt"]
        return cls(
            number=integer(data["number"], minimum=1),
            width_pt=None if width is None else number(width),
            height_pt=None if height is None else number(height),
            status=cast(
                "Literal['processed', 'empty', 'missing']",
                choice(data["status"], "processed empty missing"),
            ),
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize the record.

        Returns
        -------
        dict of str to object
            JSON-compatible fields.
        """
        return {
            "number": self.number,
            "width_pt": self.width_pt,
            "height_pt": self.height_pt,
            "status": self.status,
        }


@dataclass(frozen=True)
class MetadataObservation:
    """Record one metadata value seen in a source, not yet validated.

    Attributes
    ----------
    field : str
        Field name such as ``title`` or ``identifier``.
    value : str
        Observed value, verbatim.
    source : str
        Where it was observed, such as the PDF information dictionary.
    """

    field: str
    value: str
    source: str

    @classmethod
    def from_dict(cls, value: object) -> MetadataObservation:
        """Parse a serialized observation.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        MetadataObservation
            Validated observation.
        """
        data = record(value, "field value source")
        return cls(
            field=string(data["field"]),
            value=string(data["value"]),
            source=string(data["source"]),
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize the observation.

        Returns
        -------
        dict of str to object
            JSON-compatible fields.
        """
        return {"field": self.field, "value": self.value, "source": self.source}


@dataclass(frozen=True)
class Document:
    """Hold the normalized structure of one extracted source.

    Attributes
    ----------
    source_sha256 : str
        Digest of the preserved source the blocks refer to.
    backend : str
        Producing backend name.
    backend_version : str
        Producing backend version.
    native_schema : str
        Backend output schema name.
    native_schema_version : str
        Backend output schema version.
    pages : tuple of PageRecord
        Every requested page with its status.
    blocks : tuple of Block
        Blocks in page order and backend reading order.
    metadata : tuple of MetadataObservation
        Metadata observations awaiting the identity stage.
    findings : tuple of Finding
        Validation observations gathered during normalization.
    """

    source_sha256: str
    backend: str
    backend_version: str
    native_schema: str
    native_schema_version: str
    pages: tuple[PageRecord, ...]
    blocks: tuple[Block, ...]
    metadata: tuple[MetadataObservation, ...]
    findings: tuple[Finding, ...]

    @classmethod
    def from_dict(cls, value: object) -> Document:
        """Parse a serialized document of this schema version.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        Document
            Validated document.

        Raises
        ------
        ValueError
            The document has another schema or version, or malformed content.
        """
        data = record(
            value,
            "schema schema_version source_sha256 backend backend_version native_schema "
            "native_schema_version pages blocks metadata findings",
        )
        if (
            data["schema"] != SCHEMA_NAME
            or data["schema_version"] not in _READABLE_VERSIONS
        ):
            raise ValueError(
                f"Expected {SCHEMA_NAME} {SCHEMA_VERSION}, got "
                f"{data['schema']!r} {data['schema_version']!r}"
            )
        return cls(
            source_sha256=string(data["source_sha256"]),
            backend=string(data["backend"]),
            backend_version=string(data["backend_version"]),
            native_schema=string(data["native_schema"]),
            native_schema_version=string(data["native_schema_version"]),
            pages=tuple(PageRecord.from_dict(item) for item in items(data["pages"])),
            blocks=tuple(block_from_dict(item) for item in items(data["blocks"])),
            metadata=tuple(
                MetadataObservation.from_dict(item) for item in items(data["metadata"])
            ),
            findings=tuple(Finding.from_dict(item) for item in items(data["findings"])),
        )

    @classmethod
    def from_json(cls, payload: str) -> Document:
        """Parse a serialized document from JSON text.

        Parameters
        ----------
        payload : str
            JSON text written by :meth:`to_json`.

        Returns
        -------
        Document
            Validated document.
        """
        return cls.from_dict(json.loads(payload))

    def to_dict(self) -> dict[str, object]:
        """Serialize the document with its schema envelope.

        Returns
        -------
        dict of str to object
            JSON-compatible document.
        """
        return {
            "schema": SCHEMA_NAME,
            "schema_version": SCHEMA_VERSION,
            "source_sha256": self.source_sha256,
            "backend": self.backend,
            "backend_version": self.backend_version,
            "native_schema": self.native_schema,
            "native_schema_version": self.native_schema_version,
            "pages": [page.to_dict() for page in self.pages],
            "blocks": [block.to_dict() for block in self.blocks],
            "metadata": [item.to_dict() for item in self.metadata],
            "findings": [item.to_dict() for item in self.findings],
        }

    def to_json(self) -> str:
        """Serialize the document as stable JSON.

        Returns
        -------
        str
            Indented JSON with sorted keys and a trailing newline.
        """
        return (
            json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)
            + "\n"
        )


_BLOCK_READERS = {
    "heading": Heading.from_dict,
    "paragraph": Paragraph.from_dict,
    "list": ListBlock.from_dict,
    "equation": Equation.from_dict,
    "figure": Figure.from_dict,
    "table": Table.from_dict,
    "page_furniture": PageFurniture.from_dict,
    "unclassified": Unclassified.from_dict,
}


def block_from_dict(value: object) -> Block:
    """Parse one serialized block by its ``kind`` discriminator.

    Parameters
    ----------
    value : object
        Decoded JSON value.

    Returns
    -------
    Block
        Validated block of the matching type.

    Raises
    ------
    ValueError
        The value is not an object or names an unknown kind.
    """
    kind = choice(mapping(value).get("kind"), " ".join(_BLOCK_READERS))
    return _BLOCK_READERS[kind](value)
