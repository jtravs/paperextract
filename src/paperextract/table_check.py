"""Cross-check extracted tables against an independent Docling reading.

MinerU and Docling read tables with different models, so agreement between
them is evidence that the numbers were transcribed faithfully, and a
disagreement marks a table for review. The check never changes the selected
body: Docling's body is kept as an alternative, and a finding records whether
the numbers agree, differ, or could not be compared. Docling tables on a
checked page that match no extracted table suggest a table the extraction
missed.

The comparison applies only harmless display normalization to both sides:
Docling's glyph codes are removed and a decimal point followed by spaces
(``2. 534``, from old OCR text layers) is joined to the digits after it. A
glyph code such as ``.0134`` can follow a digit (``Ω0.0134z``) and then looks
like a number; it is removed only when the same code also appears in the
table where no digit precedes it. Signs are
ignored, as in the OCR check, because hyphens and minus signs differ between
text layers and LaTeX.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace

from paperextract.document import Block, Document, Finding, Table, TableBody
from paperextract.normalize_docling import glyph_code_count
from paperextract.table_ocr import match_table

__all__ = [
    "apply_table_check",
    "check_numbers",
    "table_check_pages",
]

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_TAG = re.compile(r"<[^<>]*>")
_GLYPH_CODE = re.compile(r"(?<![0-9])\.(0[0-9]{3})(?![0-9])")
_ANY_CODE = re.compile(r"\.(0[0-9]{3})(?![0-9])")
_SPLIT_DECIMAL = re.compile(r"(\d)\.\s+(\d)")
_SHOWN_DIFFERENCES = 8


def table_check_pages(document: Document) -> tuple[int, ...]:
    """List the pages that hold an extracted table.

    Parameters
    ----------
    document : Document
        Refined canonical document.

    Returns
    -------
    tuple of int
        Sorted one-based pages.
    """
    return tuple(
        sorted(
            {block.span.page for block in document.blocks if isinstance(block, Table)}
        )
    )


def _comparable(text: str, codes: frozenset[str]) -> str:
    """Apply the comparison view's normalization to one string.

    Parameters
    ----------
    text : str
        Cell text.
    codes : frozenset of str
        Glyph codes, such as ``0134``, seen in the table without a digit
        before them.

    Returns
    -------
    str
        Text without glyph codes and with split decimals joined.
    """
    if codes:
        text = _ANY_CODE.sub(
            lambda match: " " if match.group(1) in codes else match.group(0), text
        )
    return _SPLIT_DECIMAL.sub(r"\1.\2", text)


def check_numbers(body: Table | TableBody) -> Counter[str]:
    """Count the numbers of a table body in the comparison view.

    Parameters
    ----------
    body : Table or TableBody
        Table or alternative body.

    Returns
    -------
    collections.Counter of str
        Digit groups with decimal points or commas.

    Examples
    --------
    >>> from paperextract.document import TableBody
    >>> body = TableBody("docling", "<td>2. 534 + 0.019</td>", None, None, None)
    >>> sorted(check_numbers(body))
    ['0.019', '2.534']
    """
    if body.cells:
        texts = [cell.text for cell in body.cells]
    else:
        texts = [_TAG.sub(" ", body.html)]
    codes = frozenset(
        code
        for text in texts
        if glyph_code_count(text)
        for code in _GLYPH_CODE.findall(text)
    )
    return Counter(
        match for text in texts for match in _NUMBER.findall(_comparable(text, codes))
    )


def _alternative(table: Table) -> TableBody:
    """Capture a Docling table's body as an alternative.

    Parameters
    ----------
    table : Table
        Docling table.

    Returns
    -------
    TableBody
        Body record with source ``docling``.
    """
    return TableBody(
        source="docling",
        html=table.html,
        cells=table.cells,
        rows=table.rows,
        columns=table.columns,
    )


def _compare(block: Table, match: Table) -> Finding:
    """Compare the numbers of an extracted table with Docling's reading.

    Parameters
    ----------
    block : Table
        Extracted table with its selected body.
    match : Table
        Docling table at the same position.

    Returns
    -------
    Finding
        ``TABLE_CHECK_AGREED`` or ``TABLE_CHECK_DISAGREED``.
    """
    selected = check_numbers(block)
    docling = check_numbers(match)
    grid = (
        f"grid {block.rows}x{block.columns} versus Docling's "
        f"{match.rows}x{match.columns}"
    )
    if selected == docling:
        return Finding(
            "TABLE_CHECK_AGREED",
            "info",
            f"Docling's independent reading has the same {sum(selected.values())} "
            f"numbers ({grid}); its body is kept as an alternative.",
            (block.id,),
            block.span.page,
        )
    agreeing = sum((selected & docling).values())
    only_selected = sorted((selected - docling).elements())[:_SHOWN_DIFFERENCES]
    only_docling = sorted((docling - selected).elements())[:_SHOWN_DIFFERENCES]
    return Finding(
        "TABLE_CHECK_DISAGREED",
        "warning",
        f"Docling's independent reading differs: {agreeing} of "
        f"{sum(selected.values())} numbers agree ({grid}); only here: "
        f"{only_selected}; only in Docling: {only_docling}. Check the table "
        "against the page image; Docling's body is kept as an alternative.",
        (block.id,),
        block.span.page,
    )


def apply_table_check(document: Document, reference: Document) -> Document:
    """Compare every extracted table with Docling's reading of its page.

    Parameters
    ----------
    document : Document
        Refined document after any table OCR choice.
    reference : Document
        Normalized Docling extraction of the checked pages.

    Returns
    -------
    Document
        Document whose tables carry Docling's body as an alternative, with one
        ``TABLE_CHECK_AGREED``, ``TABLE_CHECK_DISAGREED`` or
        ``TABLE_CHECK_UNMATCHED`` finding per table and a
        ``TABLE_CHECK_EXTRA`` finding for each Docling table on a checked
        page that matches no extracted table. Selected bodies are unchanged.
    """
    pages = set(table_check_pages(document))
    by_page: dict[int, list[Table]] = {}
    for block in reference.blocks:
        if isinstance(block, Table) and block.span.page in pages:
            by_page.setdefault(block.span.page, []).append(block)
    findings = list(document.findings)
    matched: set[str] = set()
    blocks: list[Block] = []
    for block in document.blocks:
        if not isinstance(block, Table):
            blocks.append(block)
            continue
        candidates = [
            c for c in by_page.get(block.span.page, []) if c.id not in matched
        ]
        match = match_table(block, candidates)
        if match is None:
            findings.append(
                Finding(
                    "TABLE_CHECK_UNMATCHED",
                    "warning",
                    "Docling found no table at the same position, so the numbers "
                    "could not be cross-checked.",
                    (block.id,),
                    block.span.page,
                )
            )
            blocks.append(block)
            continue
        matched.add(match.id)
        findings.append(_compare(block, match))
        blocks.append(
            replace(block, alternatives=(*block.alternatives, _alternative(match)))
        )
    for page in sorted(by_page):
        for table in by_page[page]:
            if table.id in matched:
                continue
            label = f"Table {table.label}" if table.label else "an unlabelled table"
            findings.append(
                Finding(
                    "TABLE_CHECK_EXTRA",
                    "warning",
                    f"Docling found {label} ({table.rows}x{table.columns}) that "
                    "matches no extracted table; the extraction may have missed "
                    "it or read it as another block.",
                    (),
                    page,
                )
            )
    return replace(document, blocks=tuple(blocks), findings=tuple(findings))
