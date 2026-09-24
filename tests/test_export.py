"""Render Markdown, tables and reports from the canonical document."""

import csv
import io
from dataclasses import replace

import pytest

from paperextract.document import (
    Document,
    Equation,
    Figure,
    Heading,
    InlineRun,
    MetadataObservation,
    Paragraph,
    SourceSpan,
    Table,
    TableCell,
)
from paperextract.export import (
    FigureAssets,
    PaperAssets,
    TableAssets,
    cell_markdown,
    inline_markdown,
    processing_status,
    render_markdown,
    review_markdown,
    table_csv,
    table_has_spans,
    table_is_simple,
    table_markdown,
    title_observation,
    validation_document,
    yaml_front_matter,
)
from paperextract.supplements import SupplementIndex

SPAN = SourceSpan(
    page=1, bbox_pt=None, bbox_fraction=None, backend_type="table", backend_index=0
)


def test_front_matter_quotes_strings_and_nests_structures() -> None:
    text = yaml_front_matter(
        {
            "title": 'Colons: and "quotes" — µm',
            "count": 3,
            "ratio": 0.5,
            "flag": True,
            "nothing": None,
            "empty_list": [],
            "empty_map": {},
            "authors": ["A. Author", "B. Author"],
            "sources": [
                {"id": "source_01", "pages": 10},
                {"id": "source_02", "pages": 2},
            ],
            "nested": {"inner": {"deep": "value"}},
        }
    )
    assert text.startswith("---\n") and text.endswith("\n---\n")
    lines = text.splitlines()
    assert 'title: "Colons: and \\"quotes\\" — µm"' in lines
    assert "count: 3" in lines
    assert "ratio: 0.5" in lines
    assert "flag: true" in lines
    assert "nothing: null" in lines
    assert "empty_list: []" in lines
    assert "empty_map: {}" in lines
    assert lines[lines.index("authors:") + 1] == '- "A. Author"'
    start = lines.index("sources:")
    assert lines[start + 1 : start + 5] == [
        '- id: "source_01"',
        "  pages: 10",
        '- id: "source_02"',
        "  pages: 2",
    ]
    assert lines[lines.index("nested:") + 1 : lines.index("nested:") + 3] == [
        "  inner:",
        '    deep: "value"',
    ]
    with pytest.raises(TypeError):
        yaml_front_matter({"bad": object()})


def test_inline_markdown_renders_runs_and_styles() -> None:
    runs = (
        InlineRun("text", "Energy ", ("bold",)),
        InlineRun("math", "E = h\\nu"),
        InlineRun("text", " and "),
        InlineRun("link", "doi", (), "https://doi.org/x"),
        InlineRun("code", "x"),
        InlineRun("text", "10", ()),
        InlineRun("text", "15", ("superscript",)),
        InlineRun("text", " n", ("subscript", "italic")),
        InlineRun("text", "gone", ("strikethrough", "underline")),
        InlineRun("text", "   ", ("bold",)),
        InlineRun("link", "no url", (), None),
    )
    assert inline_markdown(runs) == (
        "**Energy** $E = h\\nu$ and [doi](https://doi.org/x)`x`10<sup>15</sup>"
        " *<sub>n</sub>*~~gone~~   no url"
    )


def make_table(
    cells: list[tuple[int, int, int, int, str]], rows: int, columns: int
) -> Table:
    return Table(
        id="tbl_1",
        label="1",
        caption=(InlineRun("text", "Table 1. Values"),),
        footnotes=((InlineRun("text", "a note"),),),
        html="<table><tr><td>raw</td></tr></table>",
        cells=tuple(
            TableCell(r, c, rs, cs, text, text) for r, c, rs, cs, text in cells
        ),
        rows=rows,
        columns=columns,
        asset=None,
        span=SPAN,
    )


def test_simple_tables_become_pipe_tables_and_csv() -> None:
    table = make_table(
        [
            (0, 0, 1, 1, "Pressure | mb"),
            (0, 1, 1, 1, "Energy"),
            (1, 0, 1, 1, "230"),
            (1, 1, 1, 1, ""),
        ],
        2,
        2,
    )
    assert table_is_simple(table)
    assert table_markdown(table) == (
        "| Pressure \\| mb | Energy |\n| --- | --- |\n| 230 |  |"
    )
    text = table_csv(table)
    assert text is not None
    assert list(csv.reader(io.StringIO(text))) == [
        ["Pressure | mb", "Energy"],
        ["230", ""],
    ]


def test_spanned_tables_become_pipe_tables_with_merged_cells_once() -> None:
    spanned = make_table(
        [(0, 0, 2, 1, "A"), (0, 1, 1, 1, "B"), (1, 1, 1, 1, "C"), (5, 5, 1, 1, "X")],
        2,
        2,
    )
    assert not table_is_simple(spanned)
    assert table_has_spans(spanned)
    assert table_markdown(spanned) == "| A | B |\n| --- | --- |\n|  | C |"
    assert table_csv(spanned) is None
    incomplete = make_table([(0, 0, 1, 1, "A")], 2, 2)
    assert not table_is_simple(incomplete)
    assert not table_has_spans(incomplete)
    assert table_markdown(incomplete) == "| A |  |\n| --- | --- |\n|  |  |"
    no_grid = Table("tbl_2", None, None, (), "", None, None, None, None, SPAN)
    assert table_markdown(no_grid) == "*(table body unavailable)*"
    continued = replace(spanned, continues_previous=True)
    rendered = render_markdown(
        Document("c" * 64, "mineru", "1", "s", "1", (), (continued,), (), ()),
        {},
        PaperAssets(),
    )
    assert "**Table 1** *(continued)*" in rendered
    assert "*Merged cells are shown once" in rendered
    raw = replace(no_grid, html="<table><tr><td>raw</td></tr></table>")
    assert table_markdown(raw) == "<table><tr><td>raw</td></tr></table>"


@pytest.mark.parametrize(
    ("markup", "rendered"),
    [
        ("<eq>1.853 \\pm 0.004</eq>", "$1.853 \\pm 0.004$"),
        ("<eq> a &lt; b </eq>(nm)", "$a < b$(nm)"),
        ("x<sup>-1</sup><SUB>2</SUB>", "x<sup>-1</sup><SUB>2</SUB>"),
        ("<span class='c'>a</span><br/>b<br>c", "a b c"),
        ("F\x05E\x06 | y\nz", "F\ufffdE\ufffd \\| y z"),
    ],
)
def test_cells_render_math_and_keep_scripts(markup: str, rendered: str) -> None:
    assert cell_markdown(TableCell(0, 0, 1, 1, markup, "")) == rendered


def test_unmapped_glyphs_are_visible_in_prose() -> None:
    runs = (InlineRun("text", "B\x01\u03c9\x03\ttab"), InlineRun("math", "x\x05"))
    assert inline_markdown(runs) == "B\ufffd\u03c9\ufffd\ttab$x\ufffd$"


def test_footnotes_follow_the_content_of_their_page() -> None:
    footnote = replace(_paragraph("Affiliation", 1, False), role="footnote")
    late = replace(_paragraph("Last note", 2, False), id="late", role="footnote")
    text = render_markdown(
        Document(
            source_sha256="c" * 64,
            backend="mineru",
            backend_version="1",
            native_schema="s",
            native_schema_version="1",
            pages=(),
            blocks=(
                _paragraph("First part", 1, False),
                footnote,
                _paragraph("More text", 1, False),
                _paragraph("second part", 2, True),
                late,
            ),
            metadata=(),
            findings=(),
        ),
        {},
        PaperAssets(),
    )
    assert (
        "First part\n\nMore text\n<!-- source: pdf page 2 -->\nsecond part\n\n"
        "*Footnote (page 1):* Affiliation\n\n*Footnote (page 2):* Last note\n"
    ) in text


def test_markdown_renders_blocks_in_order_with_anchors_and_assets(
    normalized_document: Document,
) -> None:
    figures = [b for b in normalized_document.blocks if isinstance(b, Figure)]
    tables = [b for b in normalized_document.blocks if isinstance(b, Table)]
    equations = [b for b in normalized_document.blocks if isinstance(b, Equation)]
    assets = PaperAssets(
        figures={
            figures[0].id: FigureAssets("figures/one.png", ("figures/one_p01.jpg",)),
            figures[1].id: FigureAssets(
                None, ("figures/two_p01.jpg", None, "figures/two_p03.jpg")
            ),
            figures[2].id: FigureAssets(None, (None,)),
        },
        tables={
            tables[0].id: TableAssets(
                "tables/t.html", "tables/t.json", None, "tables/t.jpg"
            ),
            tables[1].id: TableAssets(
                "tables/u.html", "tables/u.json", "tables/u.csv", None
            ),
        },
        equations={equations[0].id: "equations/e.jpg"},
    )
    text = render_markdown(normalized_document, {"title": None, "n": 1}, assets)
    assert text.startswith("---\ntitle: null\nn: 1\n---\n")
    assert "<!-- source: pdf page 1 -->\n# Synthetic Paper" in text
    assert "**Energy** $E = h\\nu$[doi link](https://doi.org/10.1000/x)" in text
    assert "$$\na^{2} + b^{2} = c^{2}\\tag{1}\n$$" in text
    assert "## Results" in text
    assert "- first item\n- nested item\n- third item" in text
    assert (
        "**Figure 1**\n\n![Figure 1](figures/one.png)\n\n"
        "**Published caption:** Fig. 1 | Single figure."
    ) in text
    assert "*Figure note:* Footnote." in text
    assert "**Figure 2**\n\n![Figure 2](figures/two_p01.jpg)" in text
    assert (
        "**Published caption:** Fig. 2. Two panels and more. *(Panel crops: "
        "[a](figures/two_p01.jpg), [3](figures/two_p03.jpg))*"
    ) in text
    assert "**Published caption:** *(none associated)*" in text
    assert (
        "**Figure (unlabelled)** *(panel grouping unresolved; see validation.json)*"
        in text
    )
    assert "*(no image asset available)*" in text
    assert "**Figure 3**\n\n*(no image asset available)*" in text
    assert "**Table S1**\n\n**Published caption:** TABLE S1. Parameters." in text
    assert (
        "Table files: [HTML](tables/t.html), [cells](tables/t.json), "
        "[image](tables/t.jpg)"
    ) in text
    assert "[CSV](tables/u.csv)" in text
    assert "*Table note:* a Estimated." in text
    assert "> Plain aside" in text
    assert "<!-- retained backend block 'code'; see document.json -->" in text
    assert "Journal Name" not in text
    assert "Footer" not in text
    assert "[1] A reference." in text
    assert "<!-- source: pdf page 2 -->" in text
    assert text.endswith("\n") and not text.endswith("\n\n")


def test_continuation_paragraphs_join_without_a_blank_line(
    normalized_document: Document,
) -> None:
    text = render_markdown(normalized_document, {}, PaperAssets())
    # The reference paragraph on page 1 continues the previous paragraph, so it
    # follows the list without a blank line but keeps its anchor comment.
    assert "\n- third item\n[1] A reference." not in text
    assert "\n\n[1] A reference." in text
    joined = render_markdown(
        Document(
            source_sha256="c" * 64,
            backend="mineru",
            backend_version="1",
            native_schema="s",
            native_schema_version="1",
            pages=(),
            blocks=(
                _paragraph("First part", 1, False),
                _paragraph("second part", 2, True),
                _paragraph("New paragraph", 2, False),
            ),
            metadata=(),
            findings=(),
        ),
        {},
        PaperAssets(),
    )
    assert (
        "<!-- source: pdf page 1 -->\nFirst part\n"
        "<!-- source: pdf page 2 -->\nsecond part\n\n"
        "New paragraph\n"
    ) in joined


def _paragraph(text: str, page: int, continues: bool) -> Paragraph:
    return Paragraph(
        id=f"par_{text[:4]}",
        role="body",
        runs=(InlineRun("text", text),),
        span=SourceSpan(page, None, None, "text", 0),
        continues_previous=continues,
    )


def test_validation_and_review_reports(normalized_document: Document) -> None:
    report = validation_document(normalized_document)
    assert report["schema"] == "paperextract.validation"
    assert report["processing_status"] == "PARTIAL"
    assert processing_status(normalized_document) == "PARTIAL"
    assert report["pages"] == {
        "requested": 4,
        "processed": [1, 2],
        "empty": [3],
        "missing": [4],
    }
    counts = report["counts"]
    assert isinstance(counts, dict)
    assert counts["figures"] == 4
    assert counts["panels"] == 6
    assert counts["tables"] == 5
    assert counts["tables_with_grid"] == 3
    assert counts["references"] == 1
    assert counts["page_furniture_omitted"] == 3
    checks = {check["name"]: check["outcome"] for check in report["checks"]}  # type: ignore[index]
    assert checks == {
        "page_coverage": "fail",
        "figure_captions": "review",
        "table_grids": "review",
        "equation_balance": "review",
        "assets_present": "review",
        "bibliographic_identity": "not_checked",
        "cross_source_comparison": "not_checked",
    }
    review = review_markdown(normalized_document)
    assert review.startswith("# Review\n")
    assert "## Error (1)" in review
    assert "`PAGE_NOT_PROCESSED` page 4" in review
    assert "- Figures: 4; unresolved groups: 1" in review
    assert title_observation(normalized_document) == "Synthetic Paper"


def test_complete_document_passes_checks() -> None:
    document = Document(
        source_sha256="d" * 64,
        backend="mineru",
        backend_version="1",
        native_schema="s",
        native_schema_version="1",
        pages=(),
        blocks=(),
        metadata=(),
        findings=(),
    )
    report = validation_document(document)
    assert report["processing_status"] == "COMPLETE"
    checks = {check["name"]: check["outcome"] for check in report["checks"]}  # type: ignore[index]
    assert checks["page_coverage"] == "pass"
    assert checks["figure_captions"] == "pass"
    assert checks["table_grids"] == "pass"
    assert title_observation(document) is None
    assert "- none" in review_markdown(document)


def test_multi_panel_figure_without_panel_files_has_no_crop_links(
    normalized_document: Document,
) -> None:
    figures = [b for b in normalized_document.blocks if isinstance(b, Figure)]
    assets = PaperAssets(
        figures={figures[1].id: FigureAssets(None, (None, None, None))}
    )
    text = render_markdown(normalized_document, {}, assets)
    assert "Panel crops:" not in text
    assert "**Figure 2**\n\n*(no image asset available)*" in text


def test_title_observation_falls_back_to_the_first_document_title() -> None:
    document = Document(
        source_sha256="e" * 64,
        backend="mineru",
        backend_version="1",
        native_schema="s",
        native_schema_version="1",
        pages=(),
        blocks=(
            Heading("hd_2", 2, (InlineRun("text", "Results"),), SPAN),
            Heading(
                "hd_1",
                1,
                (InlineRun("text", "Observed "), InlineRun("math", "x")),
                SPAN,
            ),
        ),
        metadata=(MetadataObservation("author", "A. Author", "pdf"),),
        findings=(),
    )
    assert title_observation(document) == "Observed x"


def test_supplement_anchors_and_links_render() -> None:
    index = SupplementIndex(
        {("figure", "1"): "s.md#figure-s1", ("document", ""): "s.md"}
    )
    paragraph = Paragraph(
        "par",
        "body",
        (
            InlineRun("text", "See Supplementary Fig. 1 and "),
            InlineRun("link", "Supplement 1", url="https://doi.org/10.6084/x"),
            InlineRun("link", "elsewhere", url="https://example.org"),
            InlineRun("math", "Fig. S1"),
        ),
        SPAN,
    )
    heading = Heading("hd", 1, (InlineRun("text", "S1. Derivation"),), SPAN)
    document = Document(
        "c" * 64, "mineru", "1", "s", "1", (), (heading, paragraph), (), ()
    )
    text = render_markdown(
        document, {}, PaperAssets(anchors={"hd": "section-s1"}, links=index)
    )
    assert '<a id="section-s1"></a>\n\n# S1. Derivation' in text
    assert (
        "See [Supplementary Fig. 1](s.md#figure-s1) and "
        "[Supplement 1](s.md) ([original link](https://doi.org/10.6084/x))"
        "[elsewhere](https://example.org)$Fig. S1$"
    ) in text
    plain = render_markdown(document, {}, PaperAssets())
    assert (
        "See Supplementary Fig. 1 and [Supplement 1](https://doi.org/10.6084/x)"
        in plain
    )


def test_captions_do_not_link_their_own_label() -> None:
    figure = Figure(
        "fig",
        "S2",
        (InlineRun("text", "FIG. S2. As in Fig. S1."),),
        None,
        (),
        (),
        None,
        "single",
        1,
    )
    index = SupplementIndex(
        {("figure", "2"): "#figure-s2", ("figure", "1"): "#figure-s1"}
    )
    document = Document("c" * 64, "mineru", "1", "s", "1", (), (figure,), (), ())
    text = render_markdown(
        document, {}, PaperAssets(anchors={"fig": "figure-s2"}, links=index)
    )
    assert "**Published caption:** FIG. S2. As in [Fig. S1](#figure-s1)." in text
    other = render_markdown(document, {}, PaperAssets(links=index))
    assert "[FIG. S2](#figure-s2)" in other
