"""Correct document-level reading order and classification after normalization.

Normalization translates one backend block at a time. Some backend mistakes are
only visible across blocks or pages: a running header classified as a heading
on one page, a column of reference entries read before the neighbouring column
of prose, a drop capital split from its word. The passes here correct such
cases from evidence in the document itself or in the PDF text layer, never by
guessing, and record every change as a finding so it can be reviewed.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import replace
from typing import cast

from paperextract.document import (
    Block,
    Box,
    Document,
    Figure,
    Finding,
    Heading,
    InlineRun,
    PageFurniture,
    Paragraph,
    plain_text,
)

__all__ = [
    "DropCapLookup",
    "flag_unmapped_glyphs",
    "gather_interleaved_references",
    "reclassify_repeated_furniture",
    "refine_document",
    "restore_drop_caps",
    "unmapped_count",
]

DropCapLookup = Callable[[int, Box, str], str | None]
"""Return the drop capital printed before a fragment, from the PDF text layer.

Called with the one-based page, the paragraph box in points with a top-left
origin, and the paragraph's first fragment; returns one capital letter or None.
"""

# Running headers repeat at the same place: within 1% of the page height and 2%
# of its width, on at least two other pages.
_FURNITURE_Y_TOLERANCE = 0.01
_FURNITURE_X_TOLERANCE = 0.02
_FURNITURE_MIN_PAGES = 2
_HALF_PAGE = 0.5
_MIN_REFERENCE_RUNS = 2
_DROP_CAP_FRAGMENT = re.compile(r"^[a-z]{2,}$")
_WHITESPACE = re.compile(r"\s+")


def _page(block: Block) -> int:
    """Return the source page of a block.

    Parameters
    ----------
    block : Block
        Canonical block.

    Returns
    -------
    int
        One-based page.
    """
    return block.page if isinstance(block, Figure) else block.span.page


def _key(text: str) -> str:
    """Normalize text for comparing repeated page furniture.

    Parameters
    ----------
    text : str
        Plain text.

    Returns
    -------
    str
        Case-folded text with collapsed whitespace.
    """
    return _WHITESPACE.sub(" ", text).strip().casefold()


def _near(first: Box, second: Box) -> bool:
    """Decide whether two fractional boxes start at the same place.

    Parameters
    ----------
    first : tuple of float
        Fractional box.
    second : tuple of float
        Fractional box.

    Returns
    -------
    bool
        True when the top-left corners agree within the furniture tolerances.
    """
    return (
        abs(first[0] - second[0]) <= _FURNITURE_X_TOLERANCE
        and abs(first[1] - second[1]) <= _FURNITURE_Y_TOLERANCE
    )


def reclassify_repeated_furniture(document: Document) -> Document:
    """Turn headings and paragraphs that repeat page furniture into furniture.

    Parameters
    ----------
    document : Document
        Canonical document.

    Returns
    -------
    Document
        Document in which a heading or paragraph whose text and position match
        page furniture on at least two other pages is page furniture, with one
        ``FURNITURE_RECLASSIFIED`` finding per change.
    """
    furniture = [
        (block.span.page, _key(plain_text(block.runs)), block.span.bbox_fraction)
        for block in document.blocks
        if isinstance(block, PageFurniture) and block.span.bbox_fraction is not None
    ]
    blocks: list[Block] = []
    findings = list(document.findings)
    for block in document.blocks:
        box = None if isinstance(block, Figure) else block.span.bbox_fraction
        if isinstance(block, Heading | Paragraph) and box is not None:
            text = _key(plain_text(block.runs))
            pages = {
                page
                for page, other, other_box in furniture
                if page != block.span.page and other == text and _near(box, other_box)
            }
            if text and len(pages) >= _FURNITURE_MIN_PAGES:
                role = "header" if box[1] < _HALF_PAGE else "footer"
                blocks.append(PageFurniture(block.id, role, block.runs, block.span))
                findings.append(
                    Finding(
                        "FURNITURE_RECLASSIFIED",
                        "info",
                        f"'{plain_text(block.runs)}' repeats the page {role} of "
                        f"{len(pages)} other pages at the same position and is "
                        "omitted from the text.",
                        (block.id,),
                        block.span.page,
                    )
                )
                continue
        blocks.append(block)
    return replace(document, blocks=tuple(blocks), findings=tuple(findings))


def _is_reference(block: Block) -> bool:
    """Tell whether a block is a reference-list entry.

    Parameters
    ----------
    block : Block
        Canonical block.

    Returns
    -------
    bool
        True for a paragraph with the reference role.
    """
    return isinstance(block, Paragraph) and block.role == "reference"


def _reorder_page(page_blocks: list[Block]) -> tuple[list[Block], list[str]]:
    """Move earlier reference runs of one page next to its last reference run.

    Parameters
    ----------
    page_blocks : list of Block
        Blocks of one page in reading order.

    Returns
    -------
    tuple
        Reordered blocks and the identifiers of the moved references.
    """
    runs: list[list[int]] = []
    previous = False
    for index, block in enumerate(page_blocks):
        if isinstance(block, PageFurniture):
            continue
        current = _is_reference(block)
        if current and not previous:
            runs.append([])
        if current:
            runs[-1].append(index)
        previous = current
    if len(runs) < _MIN_REFERENCE_RUNS:
        return page_blocks, []
    moved = [index for run in runs[:-1] for index in run]
    moved_set = set(moved)
    reordered: list[Block] = []
    for index, block in enumerate(page_blocks):
        if index == runs[-1][0]:
            reordered.extend(page_blocks[i] for i in moved)
        if index not in moved_set:
            reordered.append(block)
    return reordered, [page_blocks[i].id for i in moved]


def gather_interleaved_references(document: Document) -> Document:
    """Keep reference entries together when a column break split them.

    Parameters
    ----------
    document : Document
        Canonical document.

    Returns
    -------
    Document
        Document in which, on each page, every run of reference entries that
        prose separates from a later run on the same page is moved to join that
        run, keeping entry order; one ``REFERENCES_REORDERED`` finding per page.

    Notes
    -----
    A two-column page whose left column ends with reference entries and whose
    right column continues the prose before more entries is read column by
    column, which puts entries in the middle of the text. A reference list that
    simply ends before a following section is a single run and stays in place.
    """
    blocks: list[Block] = []
    findings = list(document.findings)
    start = 0
    ordered = list(document.blocks)
    while start < len(ordered):
        stop = start
        while stop < len(ordered) and _page(ordered[stop]) == _page(ordered[start]):
            stop += 1
        page_blocks, moved = _reorder_page(ordered[start:stop])
        blocks.extend(page_blocks)
        if moved:
            findings.append(
                Finding(
                    "REFERENCES_REORDERED",
                    "info",
                    f"{len(moved)} reference entries read before a column of "
                    "prose were moved to join the later entries on the page.",
                    tuple(moved),
                    _page(ordered[start]),
                )
            )
        start = stop
    return replace(document, blocks=tuple(blocks), findings=tuple(findings))


def _strings(value: object) -> Iterator[str]:
    """Yield every string inside serialized block data.

    Parameters
    ----------
    value : object
        JSON-compatible value.

    Yields
    ------
    str
        Each string value.
    """
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in cast("dict[str, object]", value).values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in cast("list[object]", value):
            yield from _strings(item)


def unmapped_count(text: str) -> int:
    """Count control characters that stand for unmapped font glyphs.

    Parameters
    ----------
    text : str
        Extracted text.

    Returns
    -------
    int
        Number of Unicode control characters other than tab and newline.
    """
    return sum(
        1 for char in text if unicodedata.category(char) == "Cc" and char not in "\t\n"
    )


def flag_unmapped_glyphs(document: Document) -> Document:
    """Report blocks whose text contains glyphs without a Unicode mapping.

    Parameters
    ----------
    document : Document
        Canonical document.

    Returns
    -------
    Document
        Document with one ``UNMAPPED_GLYPHS`` warning per affected block. The
        characters themselves are kept; Markdown shows them as a visible mark.
    """
    findings = list(document.findings)
    for block in document.blocks:
        data = block.to_dict()
        # Alternatives are kept for review, and cells repeat the table HTML;
        # count each selected character once.
        data.pop("alternatives", None)
        data.pop("cells", None)
        count = sum(unmapped_count(text) for text in _strings(data))
        if count:
            findings.append(
                Finding(
                    "UNMAPPED_GLYPHS",
                    "warning",
                    f"{count} characters came from font glyphs without a Unicode "
                    "mapping, typically math brackets or accents; the text is "
                    "incomplete, so compare it with the page image.",
                    (block.id,),
                    _page(block),
                )
            )
    return replace(document, findings=tuple(findings))


def restore_drop_caps(document: Document, lookup: DropCapLookup) -> Document:
    """Restore drop capitals that the backend split from their first word.

    Parameters
    ----------
    document : Document
        Canonical document.
    lookup : Callable
        Reads the capital printed before a fragment from the PDF text layer.

    Returns
    -------
    Document
        Document in which a paragraph starting with a superscript lower-case
        fragment, and whose text layer confirms a larger capital before it,
        starts with the capital and the fragment as plain text. Each change is
        a ``DROP_CAP_RESTORED`` finding; an unconfirmed candidate is left as it
        was with a ``DROP_CAP_SUSPECTED`` finding.
    """
    blocks: list[Block] = []
    findings = list(document.findings)
    for original in document.blocks:
        block = original
        first = block.runs[0] if isinstance(block, Paragraph) and block.runs else None
        if (
            isinstance(block, Paragraph)
            and first is not None
            and first.kind == "text"
            and "superscript" in first.styles
            and _DROP_CAP_FRAGMENT.match(first.text)
            and block.span.bbox_pt is not None
        ):
            letter = lookup(block.span.page, block.span.bbox_pt, first.text)
            if letter is None:
                findings.append(
                    Finding(
                        "DROP_CAP_SUSPECTED",
                        "warning",
                        f"Paragraph starts with superscript '{first.text}'; a lost "
                        "drop capital could not be confirmed from the text layer.",
                        (block.id,),
                        block.span.page,
                    )
                )
            else:
                styles = tuple(s for s in first.styles if s != "superscript")
                restored = InlineRun("text", letter + first.text, styles, first.url)
                block = replace(block, runs=(restored, *block.runs[1:]))
                findings.append(
                    Finding(
                        "DROP_CAP_RESTORED",
                        "info",
                        f"Drop capital '{letter}' restored from the PDF text layer "
                        f"before '{first.text}'.",
                        (block.id,),
                        block.span.page,
                    )
                )
        blocks.append(block)
    return replace(document, blocks=tuple(blocks), findings=tuple(findings))


def refine_document(
    document: Document, drop_caps: DropCapLookup | None = None
) -> Document:
    """Apply every document-level correction in a fixed order.

    Parameters
    ----------
    document : Document
        Normalized document.
    drop_caps : Callable or None
        Text-layer lookup for drop capitals, or None to skip that pass.

    Returns
    -------
    Document
        Corrected document with findings for each change.
    """
    document = reclassify_repeated_furniture(document)
    document = gather_interleaved_references(document)
    if drop_caps is not None:
        document = restore_drop_caps(document, drop_caps)
    return flag_unmapped_glyphs(document)
