"""Compare a saved article page with the PDF extraction."""

from collections import Counter

from paperextract.capture import CaptureFile, CaptureRecord
from paperextract.document import (
    Document,
    Figure,
    Heading,
    InlineRun,
    ListBlock,
    PageRecord,
    Paragraph,
    SourceSpan,
    Table,
    TableCell,
)
from paperextract.html_article import (
    HtmlArticle,
    HtmlEquation,
    HtmlFigure,
    HtmlReference,
    HtmlTable,
)
from paperextract.html_check import HtmlCheck, check_html
from paperextract.tables import parse_html_table

TEXT = (
    "Soliton self-compression in hollow capillary fibres reaches few-cycle "
    "durations at high energy while the ultraviolet dispersive wave is tunable"
)
MISSING = (
    "This paragraph was dropped by the PDF backend and is not present anywhere "
    "in the extracted document text at all"
)
SPAN = SourceSpan(1, None, None, "text", 0)
CAPTURE = CaptureRecord(
    "html", "page.html", "page.html", (CaptureFile("page.html", "a" * 64, 1),), None
)


def runs(text: str, *math: str) -> tuple[InlineRun, ...]:
    return (InlineRun("text", text), *(InlineRun("math", m) for m in math))


def cells(*texts: str) -> tuple[TableCell, ...]:
    return tuple(TableCell(0, i, 1, 1, text, text) for i, text in enumerate(texts))


def table(label: str | None, *texts: str, continues: bool = False) -> Table:
    return Table(
        id=f"tbl_{label}_{len(texts)}_{continues}",
        label=label,
        caption=None if label is None else runs(f"Table {label}. Values"),
        footnotes=(runs("a Note."),),
        html="<table></table>",
        cells=cells(*texts),
        rows=1,
        columns=len(texts),
        asset=None,
        span=SPAN,
        continues_previous=continues,
    )


def document() -> Document:
    blocks = (
        Heading("hd_1", 2, runs("2. Methods"), SPAN),
        Paragraph("par_1", "body", runs(TEXT, "x^2"), SPAN),
        Paragraph("par_2", "body", runs("Scaling rules for solitons. The text"), SPAN),
        ListBlock("list_1", (runs("an item in a list with several words"),), SPAN),
        Paragraph("ref_1", "reference", runs("1. A reference."), SPAN),
        Figure(
            id="fig_1",
            label="1",
            caption=runs("Fig. 1. Setup of the compression experiment."),
            caption_span=SPAN,
            footnotes=(),
            panels=(),
            context_bbox_pt=None,
            grouping="single",
            page=1,
        ),
        Figure("fig_2", "2", runs("Fig. 2. Short."), SPAN, (), (), None, "single", 1),
        table("1", "1.8", "0.5"),
        table("1", "9.9", continues=True),
        table("2", "3.0"),
        table("3", "x"),
        table("4", "9"),
        table("5", "1"),
        table(None, "7"),
    )
    return Document(
        source_sha256="b" * 64,
        backend="mineru",
        backend_version="4.0.5",
        native_schema="docvortex.middle",
        native_schema_version="2.0",
        pages=(PageRecord(1, 600.0, 800.0, "processed"),),
        blocks=blocks,
        metadata=(),
        findings=(),
    )


def html_table(label: str | None, markup: str, math_cells: int = 0) -> HtmlTable:
    return HtmlTable(
        label, f"Table {label}.", markup, parse_html_table(markup), math_cells
    )


def article(**changes: object) -> HtmlArticle:
    fields: dict[str, object] = {
        "meta": {},
        "doi": "10.1000/abc",
        "title": "A Title",
        "headings": ("Abstract", "Methods", "Scaling rules for solitons", "Funding"),
        "paragraphs": (TEXT + " " + TEXT, MISSING, "short text", "x" * 3000),
        "figures": (
            HtmlFigure("1", "Fig. 1: Setup of the compression experiment.", ()),
            HtmlFigure("2", "Fig. 2. A much longer caption than the PDF kept.", ()),
            HtmlFigure("3", "Fig. 3. Only on the page.", ()),
        ),
        "tables": (
            html_table("1", "<table><tr><td>1.8</td><td>0.5</td></tr></table>"),
            html_table("2", "<table><tr><td>3.1</td></tr></table>", math_cells=1),
            html_table(
                "3", "<table><tr><td>y</td><td><math></math></td></tr></table>", 1
            ),
            html_table("4", "<table><tr><td>8</td></tr></table>"),
            html_table("5", "<table><tr><td><math></math></td></tr></table>", 1),
            HtmlTable("6", "Table 6.", "<table></table>", None, 0),
            html_table(None, "<table><tr><td>layout</td></tr></table>"),
        ),
        "equations": (
            HtmlEquation(True, "E=mc^2", "<math/>"),
            HtmlEquation(False, None, None),
        ),
        "references": (
            HtmlReference("A reference.", ("10.1/x",)),
            HtmlReference("Another.", ()),
        ),
    }
    fields.update(changes)
    return HtmlArticle(**fields)  # type: ignore[arg-type]


def run(
    page: HtmlArticle, doi: str | None = "10.1000/ABC", title: str | None = None
) -> HtmlCheck:
    return check_html(
        document(), page, CAPTURE, source_id="source_02", doi=doi, title=title
    )


def test_the_same_article_is_compared_part_by_part() -> None:
    result = run(article())
    report = result.report
    assert report["identity"] == {
        "status": "same_doi",
        "page_doi": "10.1000/abc",
        "paper_doi": "10.1000/ABC",
    }
    assert report["compared"] is True
    codes = Counter(f.code for f in result.findings)
    assert result.findings[0].code == "HTML_CHECK_SUMMARY"
    assert codes["HTML_TEXT_NOT_IN_PDF"] == 1
    assert codes["HTML_CAPTION_DIFFERS"] == 1
    assert codes["HTML_FIGURE_NOT_IN_PDF"] == 1
    assert codes["HTML_TABLE_AGREED"] == 1
    assert codes["HTML_TABLE_DISAGREED"] == 2
    assert codes["HTML_TABLE_NOT_COMPARABLE"] == 2
    assert codes["HTML_TABLE_NOT_IN_PDF"] == 1
    assert codes["HTML_REFERENCES_DIFFER"] == 1
    assert "HTML_INCOMPLETE" not in codes
    # Unprinted headings and run-in headings are not reported as missing.
    headings = report["headings"]
    assert isinstance(headings, dict) and headings["missing"] == ["Funding"]
    text = report["text"]
    assert isinstance(text, dict) and text["compared"] == 2
    tables = {entry["label"]: entry for entry in report["tables"]}  # type: ignore[union-attr]
    assert tables["1"]["status"] == "agrees"
    assert tables["2"]["only_page"] == ["3.1"]
    assert tables["3"]["page_words_found"] == 0.0
    assert tables["5"]["page_words_found"] is None
    assert tables["5"]["status"] == "not_comparable"
    assert tables["6"] == {"label": "6", "status": "only_in_page"}
    messages = [f.message for f in result.findings if f.code == "HTML_TABLE_DISAGREED"]
    assert any("math that the page renders as graphics" in m for m in messages)
    assert any("renders" not in m for m in messages)
    comparable = [
        f.message for f in result.findings if f.code == "HTML_TABLE_NOT_COMPARABLE"
    ]
    assert any("0% of its words" in m for m in comparable)
    assert any("no words to compare" in m for m in comparable)
    assert report["equations"] == {
        "page": 2,
        "page_display": 1,
        "page_with_tex": 1,
        "page_mathml": 1,
    }
    references = report["references"]
    assert isinstance(references, dict) and references["entries"][0]["dois"] == [
        "10.1/x"
    ]


def test_different_works_are_not_compared() -> None:
    result = run(article(doi="10.1000/other"))
    assert [f.code for f in result.findings] == ["HTML_VERSION_MISMATCH"]
    assert result.report["compared"] is False
    unknown = run(article(doi=None, title="Something else"), doi=None, title="A Title")
    assert [f.code for f in unknown.findings] == ["HTML_IDENTITY_UNCONFIRMED"]
    assert unknown.report["compared"] is False


def test_a_shared_title_allows_a_comparison_with_a_warning() -> None:
    result = run(article(doi=None, title="A  title!"), doi=None, title="A Title")
    assert result.report["identity"]["status"] == "same_title"  # type: ignore[index]
    assert result.findings[1].code == "HTML_IDENTITY_UNCONFIRMED"


def test_short_pages_are_reported_as_incomplete() -> None:
    page = article(
        paragraphs=(TEXT,),
        headings=("Methods",),
        figures=(),
        tables=(),
        references=(),
    )
    codes = [f.code for f in run(page).findings]
    assert codes == ["HTML_CHECK_SUMMARY", "HTML_INCOMPLETE"]
