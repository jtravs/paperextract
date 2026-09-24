"""Re-extract tables whose text layer lost glyphs, keeping them only when numbers agree.

MinerU reads tables in text-layer PDFs from the PDF text layer. Some publisher
math fonts have glyphs without a Unicode mapping, so brackets and accents turn
into control characters and a formula becomes unreadable. A page-level OCR run of
the same backend reads such tables from the image instead. OCR can misread
digits that the text layer holds exactly, so an OCR table replaces the native one
only when both contain exactly the same numbers; either way the other version is
kept as an alternative and a finding records the decision.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace
from typing import Literal

from paperextract.document import (
    Block,
    Box,
    Document,
    Finding,
    Table,
    TableBody,
    plain_text,
)
from paperextract.layout import unmapped_count

__all__ = [
    "MIN_TABLE_OVERLAP",
    "apply_ocr_tables",
    "match_table",
    "ocr_candidate_pages",
    "table_numbers",
]

# Two extractions of one table overlap far more than neighbouring tables do.
MIN_TABLE_OVERLAP = 0.5
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_TAG = re.compile(r"<[^<>]*>")
_SHOWN_DIFFERENCES = 8


def _needs_ocr(table: Table) -> bool:
    """Tell whether a table's body contains unmapped glyphs.

    Parameters
    ----------
    table : Table
        Canonical table.

    Returns
    -------
    bool
        True when the HTML or any cell holds a control character.
    """
    cells = table.cells or ()
    return unmapped_count(table.html) > 0 or any(
        unmapped_count(cell.html) > 0 for cell in cells
    )


def ocr_candidate_pages(document: Document) -> tuple[int, ...]:
    """List the pages whose tables should be re-extracted with OCR.

    Parameters
    ----------
    document : Document
        Refined canonical document.

    Returns
    -------
    tuple of int
        Sorted one-based pages holding a native table with unmapped glyphs.
    """
    return tuple(
        sorted(
            {
                block.span.page
                for block in document.blocks
                if isinstance(block, Table)
                and block.body_source == "native"
                and _needs_ocr(block)
            }
        )
    )


def table_numbers(body: Table | TableBody) -> Counter[str]:
    """Count the numbers written in a table body.

    Parameters
    ----------
    body : Table or TableBody
        Table or alternative body.

    Returns
    -------
    collections.Counter of str
        Digit groups, with decimal points or commas, as printed; signs are
        ignored because a text-layer hyphen and a LaTeX minus look different.

    Examples
    --------
    >>> from paperextract.document import TableBody
    >>> body = TableBody("native", "<td>10<sup>-63</sup> 1.5</td>", None, None, None)
    >>> sorted(table_numbers(body).items())
    [('1.5', 1), ('10', 1), ('63', 1)]
    """
    if body.cells:
        texts = [cell.text for cell in body.cells]
    else:
        texts = [_TAG.sub(" ", body.html)]
    return Counter(match for text in texts for match in _NUMBER.findall(text))


def _overlap(first: Box, second: Box) -> float:
    """Compute the intersection over union of two boxes.

    Parameters
    ----------
    first : tuple of float
        Box.
    second : tuple of float
        Box.

    Returns
    -------
    float
        Overlap ratio in ``[0, 1]``.
    """
    width = min(first[2], second[2]) - max(first[0], second[0])
    height = min(first[3], second[3]) - max(first[1], second[1])
    if width <= 0 or height <= 0:
        return 0.0
    inter = width * height
    area = (first[2] - first[0]) * (first[3] - first[1])
    area += (second[2] - second[0]) * (second[3] - second[1])
    return inter / (area - inter)


def match_table(table: Table, candidates: list[Table]) -> Table | None:
    """Find the table of another extraction that covers the same region.

    Parameters
    ----------
    table : Table
        Table of the selected extraction.
    candidates : list of Table
        Tables of the other extraction on the same page.

    Returns
    -------
    Table or None
        The best candidate with at least :data:`MIN_TABLE_OVERLAP` overlap;
        without usable boxes, the only candidate with the same label.
    """
    box = table.span.bbox_fraction
    best: Table | None = None
    best_overlap = MIN_TABLE_OVERLAP
    for candidate in candidates:
        other = candidate.span.bbox_fraction
        if box is None or other is None:
            continue
        overlap = _overlap(box, other)
        if overlap >= best_overlap:
            best, best_overlap = candidate, overlap
    if best is None and table.label is not None:
        labelled = [c for c in candidates if c.label == table.label]
        if len(labelled) == 1:
            best = labelled[0]
    return best


def _body(table: Table, source: Literal["native", "ocr"]) -> TableBody:
    """Capture a table's current body as an alternative.

    Parameters
    ----------
    table : Table
        Table whose body is captured.
    source : str
        ``native`` or ``ocr``.

    Returns
    -------
    TableBody
        Body record.
    """
    return TableBody(
        source=source,
        html=table.html,
        cells=table.cells,
        rows=table.rows,
        columns=table.columns,
    )


def _differences(native: Counter[str], ocr: Counter[str]) -> str:
    """Describe how two number multisets differ.

    Parameters
    ----------
    native : collections.Counter of str
        Numbers in the native body.
    ocr : collections.Counter of str
        Numbers in the OCR body.

    Returns
    -------
    str
        Short list of numbers found in only one version.
    """
    only_native = sorted((native - ocr).elements())[:_SHOWN_DIFFERENCES]
    only_ocr = sorted((ocr - native).elements())[:_SHOWN_DIFFERENCES]
    return f"only in the text layer: {only_native}; only in OCR: {only_ocr}"


def apply_ocr_tables(document: Document, ocr: Document) -> Document:
    """Choose between native and OCR bodies for tables with unmapped glyphs.

    Parameters
    ----------
    document : Document
        Refined native document.
    ocr : Document
        Normalized OCR re-extraction of the candidate pages.

    Returns
    -------
    Document
        Document in which each candidate table matched by position either
        takes the OCR body, when both bodies contain exactly the same numbers
        (``TABLE_OCR_SELECTED``), or keeps its native body with the OCR body as
        an alternative (``TABLE_OCR_REJECTED``). An unmatched candidate gets
        ``TABLE_OCR_UNMATCHED``. The unmapped-glyph finding of a replaced
        table is dropped when the OCR body has no such glyphs.
    """
    by_page: dict[int, list[Table]] = {}
    for block in ocr.blocks:
        if isinstance(block, Table):
            by_page.setdefault(block.span.page, []).append(block)
    findings = list(document.findings)
    resolved: set[str] = set()
    blocks: list[Block] = []
    for block in document.blocks:
        if not (isinstance(block, Table) and _needs_ocr(block)):
            blocks.append(block)
            continue
        match = match_table(block, by_page.get(block.span.page, []))
        if match is None:
            findings.append(
                Finding(
                    "TABLE_OCR_UNMATCHED",
                    "warning",
                    "The OCR re-extraction found no table at the same position; "
                    "the text-layer body is kept.",
                    (block.id,),
                    block.span.page,
                )
            )
            blocks.append(block)
            continue
        native_numbers = table_numbers(block)
        ocr_numbers = table_numbers(match)
        if native_numbers == ocr_numbers:
            count = sum(native_numbers.values())
            blocks.append(
                replace(
                    block,
                    html=match.html,
                    cells=match.cells,
                    rows=match.rows,
                    columns=match.columns,
                    body_source="ocr",
                    alternatives=(*block.alternatives, _body(block, "native")),
                )
            )
            findings.append(
                Finding(
                    "TABLE_OCR_SELECTED",
                    "info",
                    "The body comes from an OCR re-extraction because the text "
                    f"layer lost glyphs; all {count} numbers agree with the text "
                    f"layer. Grid {block.rows}x{block.columns} became "
                    f"{match.rows}x{match.columns}; the text-layer body is kept "
                    "as an alternative.",
                    (block.id,),
                    block.span.page,
                )
            )
            # The OCR run replaces the body only; lost glyphs in the caption
            # or notes still need the finding.
            notes = [block.caption or (), *block.footnotes]
            if not _needs_ocr(match) and not any(
                unmapped_count(plain_text(note)) for note in notes
            ):
                resolved.add(block.id)
        else:
            blocks.append(
                replace(block, alternatives=(*block.alternatives, _body(match, "ocr")))
            )
            findings.append(
                Finding(
                    "TABLE_OCR_REJECTED",
                    "warning",
                    "An OCR re-extraction was not used because its numbers differ "
                    f"from the text layer ({_differences(native_numbers, ocr_numbers)}"
                    "); it is kept as an alternative for review.",
                    (block.id,),
                    block.span.page,
                )
            )
    kept = [
        finding
        for finding in findings
        if not (
            finding.code == "UNMAPPED_GLYPHS"
            and finding.block_ids
            and set(finding.block_ids) <= resolved
        )
    ]
    return replace(document, blocks=tuple(blocks), findings=tuple(kept))
