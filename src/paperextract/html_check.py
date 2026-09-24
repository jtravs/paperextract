"""Compare a saved article page with the PDF extraction, without merging them.

The PDF extraction stays the paper. A publisher page of the same article is a
second, independent rendering: its tables, captions, section headings, body
text and reference list can confirm what the PDF backend read or show what it
missed. The comparison runs only when the page and the paper are the same
work, established by their DOI, or, without a DOI on both sides, by an
identical title. Its results are a report and findings for review; nothing
from the page enters ``paper.md`` or ``document.json``.

Text comparison uses words only: case, accents, punctuation and math are
ignored on both sides, because the page renders math as MathML or graphics
and the PDF as LaTeX. Table numbers use the same comparison view as the
Docling table check.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from paperextract.capture import CaptureRecord
from paperextract.document import (
    Document,
    Figure,
    Finding,
    Heading,
    ListBlock,
    Paragraph,
    RichText,
    Table,
    TableBody,
)
from paperextract.html_article import HtmlArticle, HtmlTable
from paperextract.table_check import check_numbers

__all__ = [
    "CAPTION_AGREEMENT",
    "CHECK_SCHEMA",
    "CHECK_VERSION",
    "MIN_COVERAGE",
    "HtmlCheck",
    "check_html",
    "words",
]

CHECK_SCHEMA = "paperextract.html-check"
CHECK_VERSION = 1
# A paragraph whose word sequences are mostly absent from the PDF text points
# at text the PDF backend dropped.
MIN_COVERAGE = 0.5
# Captions differ in punctuation and math; below this word-sequence ratio the
# difference is worth a look.
CAPTION_AGREEMENT = 0.9
_SHINGLE = 6
_MIN_WORDS = 12
_MIN_ARTICLE_CHARACTERS = 3000
_SHOWN = 5
_WORD = re.compile(r"[^\W_]+")
# Headings that pages add but papers do not print.
_UNPRINTED = frozenset({"abstract", "main"})
_NUMBERING = re.compile(r"^\s*(?:[0-9]+(?:\.[0-9]+)*|[A-Z]|[IVXLC]+)[.)]?\s+")


def words(text: str) -> list[str]:
    """Split text into comparison words.

    Parameters
    ----------
    text : str
        Text.

    Returns
    -------
    list of str
        Case-folded words without accents or punctuation.

    Examples
    --------
    >>> words("Self-compression in Hollow Fibres, Ångström.")
    ['self', 'compression', 'in', 'hollow', 'fibres', 'angstrom']
    """
    folded = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(c for c in folded if not unicodedata.combining(c))
    return _WORD.findall(stripped)


def _runs_text(runs: RichText) -> str:
    """Join the non-math runs of rich text.

    Parameters
    ----------
    runs : tuple of InlineRun
        Runs.

    Returns
    -------
    str
        Text without math.
    """
    return " ".join(run.text for run in runs if run.kind != "math")


def _shingles(tokens: Sequence[str]) -> set[tuple[str, ...]]:
    """Form overlapping word sequences.

    Parameters
    ----------
    tokens : Sequence of str
        Words.

    Returns
    -------
    set of tuple of str
        Sequences of :data:`_SHINGLE` words.
    """
    return {tuple(tokens[i : i + _SHINGLE]) for i in range(len(tokens) - _SHINGLE + 1)}


@dataclass(frozen=True)
class HtmlCheck:
    """Hold the outcome of comparing one capture with the extraction.

    Attributes
    ----------
    report : dict of str to object
        JSON-compatible report with schema ``paperextract.html-check``.
    findings : tuple of Finding
        Findings for review; codes start with ``HTML_``.
    """

    report: dict[str, object]
    findings: tuple[Finding, ...]


def _title_key(text: str | None) -> str:
    """Build a title comparison key.

    Parameters
    ----------
    text : str or None
        Title.

    Returns
    -------
    str
        Joined comparison words, or ``""``.
    """
    return "".join(words(text or ""))


def _identity(
    article: HtmlArticle, doi: str | None, title: str | None
) -> tuple[str, Finding | None]:
    """Decide whether the page shows the same work as the paper.

    Parameters
    ----------
    article : HtmlArticle
        Page content.
    doi : str or None
        The paper's validated DOI.
    title : str or None
        The paper's title.

    Returns
    -------
    tuple
        ``same_doi``, ``same_title``, ``mismatch`` or ``unconfirmed``, and
        the finding to record, if any.
    """
    page = (article.doi or "").lower() or None
    paper = (doi or "").lower() or None
    if page and paper:
        if page == paper:
            return "same_doi", None
        return "mismatch", Finding(
            "HTML_VERSION_MISMATCH",
            "error",
            f"The saved page declares DOI {page}, the paper {paper}; they are "
            "different works or versions and were not compared.",
        )
    if _title_key(article.title) and _title_key(article.title) == _title_key(title):
        return "same_title", Finding(
            "HTML_IDENTITY_UNCONFIRMED",
            "warning",
            "The page and the paper have the same title but no DOI on both "
            "sides; they were compared on that basis.",
        )
    return "unconfirmed", Finding(
        "HTML_IDENTITY_UNCONFIRMED",
        "warning",
        "The page could not be tied to the paper by DOI or title, so it was not "
        "compared.",
    )


def _pdf_text(document: Document) -> list[str]:
    """Collect the PDF extraction's text blocks.

    Parameters
    ----------
    document : Document
        Extraction.

    Returns
    -------
    list of str
        Text of headings, paragraphs, list items and captions, without math.
    """
    texts: list[str] = []
    for block in document.blocks:
        if isinstance(block, Heading | Paragraph):
            texts.append(_runs_text(block.runs))
        elif isinstance(block, ListBlock):
            texts.extend(_runs_text(item) for item in block.items)
        elif isinstance(block, Figure | Table) and block.caption is not None:
            texts.append(_runs_text(block.caption))
            texts.extend(_runs_text(note) for note in block.footnotes)
    return texts


def _headings(
    document: Document, article: HtmlArticle
) -> tuple[dict[str, object], list[Finding]]:
    """Compare section headings.

    Parameters
    ----------
    document : Document
        Extraction.
    article : HtmlArticle
        Page content.

    Returns
    -------
    tuple
        Report section and findings.
    """
    pdf = {
        _title_key(_NUMBERING.sub("", _runs_text(block.runs)))
        for block in document.blocks
        if isinstance(block, Heading)
    }
    # Journals such as Nature print subsection headings run into the first
    # sentence of a paragraph.
    starts = [
        "".join(words(_runs_text(block.runs)))
        for block in document.blocks
        if isinstance(block, Paragraph)
    ]
    missing: list[str] = []
    for heading in article.headings:
        key = _title_key(_NUMBERING.sub("", heading))
        if key in _UNPRINTED or key in pdf or any(s.startswith(key) for s in starts):
            continue
        missing.append(heading)
    findings: list[Finding] = []
    if missing:
        findings.append(
            Finding(
                "HTML_HEADINGS_NOT_IN_PDF",
                "info",
                f"{len(missing)} of {len(article.headings)} page headings are not "
                f"headings in the extraction, for example {missing[:_SHOWN]}; "
                "the text may still be present under another block type.",
            )
        )
    return {"page": len(article.headings), "missing": missing}, findings


def _text(
    document: Document, article: HtmlArticle
) -> tuple[dict[str, object], list[Finding]]:
    """Measure how much of the page's body text the extraction contains.

    Parameters
    ----------
    document : Document
        Extraction.
    article : HtmlArticle
        Page content.

    Returns
    -------
    tuple
        Report section and findings.
    """
    pdf = _shingles([w for text in _pdf_text(document) for w in [*words(text), "|"]])
    compared = 0
    uncovered: list[dict[str, object]] = []
    for paragraph in article.paragraphs:
        tokens = words(paragraph)
        if len(tokens) < _MIN_WORDS:
            continue
        compared += 1
        shingles = _shingles(tokens)
        coverage = len(shingles & pdf) / len(shingles)
        if coverage < MIN_COVERAGE:
            uncovered.append(
                {
                    "coverage": round(coverage, 3),
                    "words": len(tokens),
                    "text": paragraph,
                }
            )
    findings: list[Finding] = []
    if uncovered:
        missing_words = sum(int(str(item["words"])) for item in uncovered)
        examples = [str(item["text"])[:80] for item in uncovered[:_SHOWN]]
        findings.append(
            Finding(
                "HTML_TEXT_NOT_IN_PDF",
                "warning",
                f"{len(uncovered)} of {compared} page paragraphs ({missing_words} "
                "words) are mostly absent from the extraction; check whether the "
                f"PDF backend dropped them. First: {examples}",
            )
        )
    return {"compared": compared, "uncovered": uncovered}, findings


def _captions(
    document: Document, article: HtmlArticle
) -> tuple[list[dict[str, object]], list[Finding]]:
    """Compare figure captions by printed label.

    Parameters
    ----------
    document : Document
        Extraction.
    article : HtmlArticle
        Page content.

    Returns
    -------
    tuple
        Report entries and findings.
    """
    pdf = {
        block.label: block
        for block in document.blocks
        if isinstance(block, Figure) and block.label and block.caption is not None
    }
    entries: list[dict[str, object]] = []
    findings: list[Finding] = []
    for figure in article.figures:
        label = figure.label
        match = pdf.get(label) if label else None
        if match is None or match.caption is None:
            entries.append({"label": label, "status": "only_in_page"})
            findings.append(
                Finding(
                    "HTML_FIGURE_NOT_IN_PDF",
                    "warning",
                    f"The page has Fig. {label}, which the extraction has no captioned "
                    "figure for.",
                )
            )
            continue
        ratio = difflib.SequenceMatcher(
            None,
            words(figure.caption),
            words(_runs_text(match.caption)),
            autojunk=False,
        ).ratio()
        status = "agrees" if ratio >= CAPTION_AGREEMENT else "differs"
        entries.append(
            {
                "label": label,
                "figure": match.id,
                "status": status,
                "ratio": round(ratio, 3),
            }
        )
        if status == "differs":
            findings.append(
                Finding(
                    "HTML_CAPTION_DIFFERS",
                    "warning",
                    f"The caption of Fig. {label} differs from the page's "
                    f"(word agreement {ratio:.2f}); the extraction may have cut or "
                    "merged it.",
                    (match.id,),
                    match.page,
                )
            )
    return entries, findings


def _table_body(table: HtmlTable) -> TableBody:
    """Wrap a page table as a body for the number comparison.

    Parameters
    ----------
    table : HtmlTable
        Page table.

    Returns
    -------
    TableBody
        Body with the page's cells.
    """
    grid = table.grid
    return TableBody(
        source="native",
        html=table.html,
        cells=None if grid is None else grid.cells,
        rows=None if grid is None else grid.rows,
        columns=None if grid is None else grid.columns,
    )


def _tables(
    document: Document, article: HtmlArticle
) -> tuple[list[dict[str, object]], list[Finding]]:
    """Compare the numbers of labelled tables.

    Parameters
    ----------
    document : Document
        Extraction.
    article : HtmlArticle
        Page content.

    Returns
    -------
    tuple
        Report entries and findings.
    """
    pdf: dict[str, Table] = {}
    for block in document.blocks:
        if isinstance(block, Table) and block.label and not block.continues_previous:
            pdf.setdefault(block.label, block)
    entries: list[dict[str, object]] = []
    findings: list[Finding] = []
    for table in article.tables:
        if table.label is None:
            continue
        match = pdf.get(table.label)
        if match is None:
            entries.append({"label": table.label, "status": "only_in_page"})
            findings.append(
                Finding(
                    "HTML_TABLE_NOT_IN_PDF",
                    "warning",
                    f"The page has Table {table.label}, which the extraction lacks.",
                )
            )
            continue
        page_numbers = check_numbers(_table_body(table))
        pdf_numbers = check_numbers(match)
        agreeing = sum((page_numbers & pdf_numbers).values())
        page_words = Counter(
            w
            for cell in (table.grid.cells if table.grid else ())
            for w in words(cell.text)
        )
        pdf_words = Counter(w for cell in match.cells or () for w in words(cell.text))
        word_share = (
            sum((page_words & pdf_words).values()) / sum(page_words.values())
            if page_words
            else None
        )
        entry: dict[str, object] = {
            "label": table.label,
            "table": match.id,
            "page_numbers": sum(page_numbers.values()),
            "pdf_numbers": sum(pdf_numbers.values()),
            "agreeing": agreeing,
            "page_math_cells": table.math_cells,
            "page_grid": None
            if table.grid is None
            else [table.grid.rows, table.grid.columns],
            "pdf_grid": [match.rows, match.columns],
            "page_words_found": None if word_share is None else round(word_share, 3),
        }
        if not page_numbers and table.math_cells:
            entry["status"] = "not_comparable"
            findings.append(
                Finding(
                    "HTML_TABLE_NOT_COMPARABLE",
                    "info",
                    f"The page's Table {table.label} shows {table.math_cells} cells as "
                    "math graphics without TeX, so its numbers cannot be compared; "
                    + (
                        f"{word_share:.0%} of its words are in the extracted table."
                        if word_share is not None
                        else "it has no words to compare."
                    ),
                    (match.id,),
                    match.span.page,
                )
            )
        elif page_numbers == pdf_numbers:
            entry["status"] = "agrees"
            findings.append(
                Finding(
                    "HTML_TABLE_AGREED",
                    "info",
                    f"The page's Table {table.label} has the same "
                    f"{sum(page_numbers.values())} numbers.",
                    (match.id,),
                    match.span.page,
                )
            )
        else:
            entry["status"] = "differs"
            entry["only_page"] = sorted((page_numbers - pdf_numbers).elements())[:20]
            entry["only_pdf"] = sorted((pdf_numbers - page_numbers).elements())[:20]
            note = (
                f" {table.math_cells} of the page's cells hold math that the page "
                "renders as graphics without TeX, so numbers inside it are missing "
                "from the page side."
                if table.math_cells
                else ""
            )
            findings.append(
                Finding(
                    "HTML_TABLE_DISAGREED",
                    "warning",
                    f"The page's Table {table.label} differs: {agreeing} of "
                    f"{sum(pdf_numbers.values())} extracted numbers agree; only in the "
                    f"page: {entry['only_page'][:8]}; only in the extraction: "
                    f"{entry['only_pdf'][:8]}.{note}",
                    (match.id,),
                    match.span.page,
                )
            )
        entries.append(entry)
    return entries, findings


def _references(
    document: Document, article: HtmlArticle
) -> tuple[dict[str, object], list[Finding]]:
    """Compare the length of the reference lists and keep the page's DOIs.

    Parameters
    ----------
    document : Document
        Extraction.
    article : HtmlArticle
        Page content.

    Returns
    -------
    tuple
        Report section and findings.
    """
    pdf = sum(
        1
        for block in document.blocks
        if isinstance(block, Paragraph) and block.role == "reference"
    )
    findings: list[Finding] = []
    if article.references and pdf != len(article.references):
        findings.append(
            Finding(
                "HTML_REFERENCES_DIFFER",
                "warning",
                f"The page lists {len(article.references)} references, the extraction "
                f"{pdf} reference paragraphs; entries may be merged, split or missing.",
            )
        )
    entries = [
        {"index": index, "text": reference.text, "dois": list(reference.dois)}
        for index, reference in enumerate(article.references, 1)
    ]
    return {"page": len(article.references), "pdf": pdf, "entries": entries}, findings


def check_html(
    document: Document,
    article: HtmlArticle,
    capture: CaptureRecord,
    *,
    source_id: str,
    doi: str | None,
    title: str | None,
) -> HtmlCheck:
    """Compare a saved page with the extraction of the same paper.

    Parameters
    ----------
    document : Document
        PDF extraction of the paper.
    article : HtmlArticle
        Content read from the page.
    capture : CaptureRecord
        The preserved capture.
    source_id : str
        Source identifier of the capture in the paper directory.
    doi : str or None
        The paper's validated DOI.
    title : str or None
        The paper's title, for pages without a DOI.

    Returns
    -------
    HtmlCheck
        Report and findings; with a DOI mismatch or unconfirmed identity
        only the identity finding and no comparison.
    """
    status, identity = _identity(article, doi, title)
    report: dict[str, object] = {
        "schema": CHECK_SCHEMA,
        "schema_version": CHECK_VERSION,
        "source": source_id,
        "kind": capture.kind,
        "saved_from": capture.saved_from,
        "sha256": capture.sha256,
        "identity": {"status": status, "page_doi": article.doi, "paper_doi": doi},
        "compared": status in ("same_doi", "same_title"),
    }
    findings: list[Finding] = [] if identity is None else [identity]
    if not report["compared"]:
        return HtmlCheck(report, tuple(findings))
    characters = sum(len(paragraph) for paragraph in article.paragraphs)
    if characters < _MIN_ARTICLE_CHARACTERS:
        findings.append(
            Finding(
                "HTML_INCOMPLETE",
                "warning",
                f"The page holds only {characters} characters of body text; it may "
                "be an abstract page or a capture made before the article loaded.",
            )
        )
    headings, found = _headings(document, article)
    findings.extend(found)
    text, found = _text(document, article)
    findings.extend(found)
    figures, found = _captions(document, article)
    findings.extend(found)
    tables, found = _tables(document, article)
    findings.extend(found)
    references, found = _references(document, article)
    findings.extend(found)
    equations = {
        "page": len(article.equations),
        "page_display": sum(1 for e in article.equations if e.display),
        "page_with_tex": sum(1 for e in article.equations if e.tex),
        "page_mathml": sum(1 for e in article.equations if e.mathml),
    }
    report.update(
        headings=headings,
        text=text,
        figures=figures,
        tables=tables,
        equations=equations,
        references=references,
    )
    agreeing = sum(1 for entry in figures if entry.get("status") == "agrees")
    tables_agreeing = sum(1 for entry in tables if entry.get("status") == "agrees")
    uncovered = cast("list[object]", text["uncovered"])
    findings.insert(
        0,
        Finding(
            "HTML_CHECK_SUMMARY",
            "info",
            f"Compared with the saved page {source_id} ({capture.kind}): "
            f"{len(uncovered)} of {text['compared']} paragraphs mostly absent, "
            f"{agreeing} of {len(figures)} figure captions and {tables_agreeing} of "
            f"{len(tables)} tables agree, {references['page']} page references "
            f"against {references['pdf']}; {equations['page']} math elements on the "
            "page, not compared.",
        ),
    )
    return HtmlCheck(report, tuple(findings))
