"""Compare what several backends extracted from the same PDF.

Each backend's canonical document is compared with the first one, the
reference: page coverage, the kinds of blocks found, text coverage in both
directions, headings, figure captions and table numbers matched by printed
label (or by position for unlabelled tables), and display equations by
printed number. The comparison says where the readings differ; it does not
say which one is right, which needs the page or a reviewed reference.
"""

from __future__ import annotations

import difflib
import re
from collections import Counter
from collections.abc import Mapping, Sequence

from paperextract.document import (
    Document,
    Equation,
    Figure,
    Heading,
    ListBlock,
    Paragraph,
    Table,
    plain_text,
)
from paperextract.html_check import words
from paperextract.table_check import check_numbers
from paperextract.table_ocr import match_table

__all__ = [
    "COMPARE_SCHEMA",
    "COMPARE_VERSION",
    "compare_documents",
    "latex_key",
]

COMPARE_SCHEMA = "paperextract.backend-comparison"
COMPARE_VERSION = 1
_SHINGLE = 6
_MIN_WORDS = 12
_COVERED = 0.5
_CAPTION_AGREEMENT = 0.9
_SPACE_COMMANDS = re.compile(r"\\(?:[,;:!]|(?:quad|qquad|left|right|mathrm|rm|text)\b)")
_NOISE = re.compile(r"[\s{}]")


def latex_key(latex: str) -> str:
    r"""Reduce LaTeX to a key that ignores spacing and presentation commands.

    Parameters
    ----------
    latex : str
        LaTeX source.

    Returns
    -------
    str
        The source without whitespace, braces, spacing commands, ``\left``,
        ``\right``, ``\mathrm``, ``\text`` and ``\tag{...}``; a comparison
        key, never a replacement for the source.

    Examples
    --------
    >>> first = latex_key(r"L _ {\mathrm{loss}} \approx a\tag{2}")
    >>> first == latex_key(r"L_{loss}\approx a")
    True
    """
    text = re.sub(r"\\tag\*?\{[^{}]*\}", "", latex)
    text = _SPACE_COMMANDS.sub("", text)
    return _NOISE.sub("", text)


def _paragraph_texts(document: Document) -> list[str]:
    """List the text of paragraphs, list items and headings.

    Parameters
    ----------
    document : Document
        Extraction.

    Returns
    -------
    list of str
        Texts without math.
    """
    texts: list[str] = []
    for block in document.blocks:
        if isinstance(block, Heading | Paragraph):
            texts.append(" ".join(r.text for r in block.runs if r.kind != "math"))
        elif isinstance(block, ListBlock):
            texts.extend(
                " ".join(r.text for r in item if r.kind != "math")
                for item in block.items
            )
    return texts


def _shingles(texts: Sequence[str]) -> set[tuple[str, ...]]:
    """Form overlapping six-word sequences of every text.

    Parameters
    ----------
    texts : Sequence of str
        Texts.

    Returns
    -------
    set of tuple of str
        Word sequences.
    """
    found: set[tuple[str, ...]] = set()
    for text in texts:
        tokens = words(text)
        found.update(
            tuple(tokens[i : i + _SHINGLE]) for i in range(len(tokens) - _SHINGLE + 1)
        )
    return found


def _coverage(document: Document, other: Document) -> dict[str, object]:
    """Measure how many of a document's paragraphs the other contains.

    Parameters
    ----------
    document : Document
        Document whose paragraphs are checked.
    other : Document
        Document searched.

    Returns
    -------
    dict of str to object
        Paragraphs compared, those mostly absent, and up to five examples.
    """
    target = _shingles(_paragraph_texts(other))
    compared = 0
    absent: list[str] = []
    for text in _paragraph_texts(document):
        shingles = _shingles([text])
        if len(words(text)) < _MIN_WORDS or not shingles:
            continue
        compared += 1
        if len(shingles & target) / len(shingles) < _COVERED:
            absent.append(text[:100])
    return {"compared": compared, "absent": len(absent), "examples": absent[:5]}


def _summary(document: Document) -> dict[str, object]:
    """Summarize one backend's document.

    Parameters
    ----------
    document : Document
        Extraction.

    Returns
    -------
    dict of str to object
        Version, page statuses, block kinds, findings by severity and the
        numbers of inline math runs, labelled figures and tables.
    """
    kinds = Counter(type(block).__name__.lower() for block in document.blocks)
    severities = Counter(finding.severity for finding in document.findings)
    inline = sum(
        1
        for block in document.blocks
        if isinstance(block, Paragraph)
        for run in block.runs
        if run.kind == "math"
    )
    return {
        "backend": document.backend,
        "backend_version": document.backend_version,
        "pages": dict(Counter(page.status for page in document.pages)),
        "blocks": dict(sorted(kinds.items())),
        "inline_math": inline,
        "labelled_figures": sorted(
            {b.label for b in document.blocks if isinstance(b, Figure) and b.label}
        ),
        "labelled_tables": sorted(
            {b.label for b in document.blocks if isinstance(b, Table) and b.label}
        ),
        "findings": dict(sorted(severities.items())),
    }


def _headings(reference: Document, other: Document) -> dict[str, object]:
    """Compare heading texts.

    Parameters
    ----------
    reference : Document
        Reference document.
    other : Document
        Compared document.

    Returns
    -------
    dict of str to object
        Headings found only on each side.
    """

    def keys(document: Document) -> dict[str, str]:
        """Key headings by their words.

        Parameters
        ----------
        document : Document
            Extraction.

        Returns
        -------
        dict of str to str
            Heading text by its joined words.
        """
        return {
            "".join(words(plain_text(block.runs))): plain_text(block.runs)
            for block in document.blocks
            if isinstance(block, Heading) and plain_text(block.runs).strip()
        }

    left, right = keys(reference), keys(other)
    return {
        "only_reference": [left[k] for k in left if k not in right],
        "only_other": [right[k] for k in right if k not in left],
    }


def _figures(reference: Document, other: Document) -> list[dict[str, object]]:
    """Compare figure captions by printed label.

    Parameters
    ----------
    reference : Document
        Reference document.
    other : Document
        Compared document.

    Returns
    -------
    list of dict
        One entry per label on either side.
    """

    def captions(document: Document) -> dict[str, str]:
        """Collect the first caption of each figure label.

        Parameters
        ----------
        document : Document
            Extraction.

        Returns
        -------
        dict of str to str
            Caption text by label.
        """
        found: dict[str, str] = {}
        for block in document.blocks:
            if isinstance(block, Figure) and block.label and block.caption:
                found.setdefault(block.label, plain_text(block.caption))
        return found

    left, right = captions(reference), captions(other)
    entries: list[dict[str, object]] = []
    for label in sorted(set(left) | set(right), key=lambda item: (len(item), item)):
        if label not in right or label not in left:
            side = "reference" if label in left else "other"
            entries.append({"label": label, "status": f"only_{side}"})
            continue
        ratio = difflib.SequenceMatcher(
            None, words(left[label]), words(right[label]), autojunk=False
        ).ratio()
        entries.append(
            {
                "label": label,
                "status": "agrees" if ratio >= _CAPTION_AGREEMENT else "differs",
                "ratio": round(ratio, 3),
            }
        )
    return entries


def _tables(reference: Document, other: Document) -> list[dict[str, object]]:
    """Compare tables matched by position or label.

    Parameters
    ----------
    reference : Document
        Reference document.
    other : Document
        Compared document.

    Returns
    -------
    list of dict
        One entry per reference table, plus unmatched tables of the other.
    """
    candidates: dict[int, list[Table]] = {}
    for block in other.blocks:
        if isinstance(block, Table):
            candidates.setdefault(block.span.page, []).append(block)
    used: set[str] = set()
    entries: list[dict[str, object]] = []
    for block in reference.blocks:
        if not isinstance(block, Table):
            continue
        pool = [t for t in candidates.get(block.span.page, []) if t.id not in used]
        match = match_table(block, pool)
        entry: dict[str, object] = {
            "label": block.label,
            "page": block.span.page,
            "reference_grid": [block.rows, block.columns],
        }
        if match is None:
            entry["status"] = "only_reference"
            entries.append(entry)
            continue
        used.add(match.id)
        left, right = check_numbers(block), check_numbers(match)
        entry.update(
            other_grid=[match.rows, match.columns],
            numbers=[sum(left.values()), sum(right.values())],
            agreeing=sum((left & right).values()),
            status="agrees" if left == right else "differs",
        )
        if left != right:
            entry["only_reference"] = sorted((left - right).elements())[:10]
            entry["only_other"] = sorted((right - left).elements())[:10]
        entries.append(entry)
    entries.extend(
        {
            "label": table.label,
            "page": table.span.page,
            "other_grid": [table.rows, table.columns],
            "status": "only_other",
        }
        for tables in candidates.values()
        for table in tables
        if table.id not in used
    )
    return entries


def _equations(reference: Document, other: Document) -> dict[str, object]:
    """Compare display equations by printed number.

    Parameters
    ----------
    reference : Document
        Reference document.
    other : Document
        Compared document.

    Returns
    -------
    dict of str to object
        Counts, labels on only one side, and labels whose LaTeX keys agree
        or differ.
    """

    def numbered(document: Document) -> dict[str, str]:
        """Collect the LaTeX key of each numbered equation.

        Parameters
        ----------
        document : Document
            Extraction.

        Returns
        -------
        dict of str to str
            LaTeX key by printed number.
        """
        found: dict[str, str] = {}
        for block in document.blocks:
            if isinstance(block, Equation) and block.label:
                key = latex_key(block.latex)
                # Some backends keep the printed number in the LaTeX, others
                # move it to \tag or drop it; the label already records it.
                key = key.removesuffix(f"({block.label})")
                found.setdefault(block.label, key)
        return found

    left, right = numbered(reference), numbered(other)
    shared = sorted(set(left) & set(right), key=lambda item: (len(item), item))
    return {
        "counts": [
            sum(isinstance(b, Equation) for b in reference.blocks),
            sum(isinstance(b, Equation) for b in other.blocks),
        ],
        "only_reference": sorted(set(left) - set(right)),
        "only_other": sorted(set(right) - set(left)),
        "same_latex": [label for label in shared if left[label] == right[label]],
        "different_latex": [label for label in shared if left[label] != right[label]],
    }


def compare_documents(documents: Mapping[str, Document]) -> dict[str, object]:
    """Compare the documents several backends extracted from one PDF.

    Parameters
    ----------
    documents : Mapping of str to Document
        Documents by backend name; the first is the reference.

    Returns
    -------
    dict of str to object
        Report with schema ``paperextract.backend-comparison``: a summary of
        each document and, for every other backend, its differences from the
        reference.

    Raises
    ------
    ValueError
        Fewer than two documents, or documents of different sources.
    """
    if len(documents) < 2:  # noqa: PLR2004
        raise ValueError("Comparing needs at least two backends")
    names = list(documents)
    reference = documents[names[0]]
    if any(d.source_sha256 != reference.source_sha256 for d in documents.values()):
        raise ValueError("The documents come from different sources")
    comparisons: dict[str, object] = {}
    for name in names[1:]:
        other = documents[name]
        comparisons[name] = {
            "reference_text_in_other": _coverage(reference, other),
            "other_text_in_reference": _coverage(other, reference),
            "headings": _headings(reference, other),
            "figures": _figures(reference, other),
            "tables": _tables(reference, other),
            "equations": _equations(reference, other),
        }
    return {
        "schema": COMPARE_SCHEMA,
        "schema_version": COMPARE_VERSION,
        "source_sha256": reference.source_sha256,
        "reference": names[0],
        "backends": {name: _summary(documents[name]) for name in names},
        "comparisons": comparisons,
    }
