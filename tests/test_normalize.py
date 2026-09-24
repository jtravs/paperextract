"""Normalize synthetic MinerU output into canonical blocks and findings."""

import json
from collections.abc import Callable

import pytest

from paperextract.document import (
    SCHEMA_NAME,
    SCHEMA_VERSION,
    Document,
    Equation,
    Figure,
    Finding,
    Heading,
    ListBlock,
    PageFurniture,
    Paragraph,
    Table,
    Unclassified,
    plain_text,
)
from paperextract.normalize import (
    equation_label,
    latex_balance_problems,
    normalize_mineru,
)
from paperextract.protocol import PageCoverage

SHA = "b" * 64


@pytest.fixture
def document(normalized_document: Document) -> Document:
    return normalized_document


def test_pages_and_coverage_findings(document: Document) -> None:
    assert [(page.number, page.status) for page in document.pages] == [
        (1, "processed"),
        (2, "processed"),
        (3, "empty"),
        (4, "missing"),
    ]
    assert (document.pages[0].width_pt, document.pages[0].height_pt) == (600.0, 800.0)
    assert document.pages[1].width_pt is None
    assert (document.pages[2].width_pt, document.pages[2].height_pt) == (500.0, 700.0)
    codes = {finding.code for finding in document.findings}
    assert {
        "PAGE_NOT_PROCESSED",
        "PAGE_EMPTY",
        "PAGE_SIZE_UNKNOWN",
        "PAGE_MALFORMED",
    } <= codes
    missing = next(f for f in document.findings if f.code == "PAGE_NOT_PROCESSED")
    assert (missing.severity, missing.page) == ("error", 4)
    assert document.native_schema == "docvortex.middle"
    assert document.native_schema_version == "2.0"


def test_prose_blocks_keep_roles_runs_and_geometry(document: Document) -> None:
    headings = [b for b in document.blocks if isinstance(b, Heading)]
    assert [(h.level, plain_text(h.runs)) for h in headings] == [
        (1, "Synthetic Paper"),
        (2, "Results"),
        (2, "Untitled level"),
        (1, "Second title"),
    ]
    paragraphs = [b for b in document.blocks if isinstance(b, Paragraph)]
    assert [p.role for p in paragraphs] == [
        "body",
        "reference",
        "footnote",
        "aside",
        "body",
        "body",
    ]
    body = paragraphs[0]
    assert [(run.kind, run.text, run.url) for run in body.runs] == [
        ("text", "Energy ", None),
        ("math", "E = h\\nu", None),
        ("link", "doi link", "https://doi.org/10.1000/x"),
        ("math", "y", "https://doi.org/10.1000/x"),
        ("code", "x", None),
        ("text", "odd", None),
    ]
    assert body.runs[0].styles == ("bold",)
    assert body.span.bbox_pt == (60.0, 200.0, 540.0, 320.0)
    assert body.span.bbox_fraction == (0.1, 0.25, 0.9, 0.4)
    assert (body.span.backend_type, body.span.backend_index) == ("text", 2)
    assert paragraphs[1].continues_previous is True
    assert plain_text(paragraphs[3].runs) == "Plain aside"
    assert paragraphs[5].span.bbox_pt is None
    assert paragraphs[5].span.bbox_fraction is None
    furniture = [b for b in document.blocks if isinstance(b, PageFurniture)]
    assert [f.role for f in furniture] == ["header", "footer", "page_number"]
    unclassified = [b for b in document.blocks if isinstance(b, Unclassified)]
    assert [u.backend_type for u in unclassified] == ["code"]
    assert json.loads(unclassified[0].raw)["sub_type"] == "code"
    inline_notes = [f for f in document.findings if f.code == "INLINE_UNCLASSIFIED"]
    assert len(inline_notes) == 2


def test_equations_keep_raw_latex_and_report_balance(document: Document) -> None:
    equations = [b for b in document.blocks if isinstance(b, Equation)]
    assert [(e.latex, e.label, e.asset) for e in equations] == [
        ("a^{2} + b^{2} = c^{2}\\tag{1}", "1", "native/images/page_0_equation_3.jpg"),
        ("\\frac{a}{b", None, "native/images/nothere.jpg"),
        ("", None, None),
    ]
    empty = [f for f in document.findings if f.code == "EQUATION_EMPTY"]
    assert [f.block_ids for f in empty] == [(equations[2].id,)]
    unbalanced = [f for f in document.findings if f.code == "EQUATION_UNBALANCED"]
    assert [f.block_ids for f in unbalanced] == [(equations[1].id,)]
    missing = [f for f in document.findings if f.code == "ASSET_MISSING"]
    assert equations[1].id in {block_id for f in missing for block_id in f.block_ids}


def test_lists_are_flattened_with_a_finding(document: Document) -> None:
    lists = [b for b in document.blocks if isinstance(b, ListBlock)]
    assert len(lists) == 1
    assert [plain_text(item) for item in lists[0].items] == [
        "first item",
        "nested item",
        "third item",
    ]
    assert any(f.code == "LIST_FLATTENED" for f in document.findings)


def test_figures_group_panels_up_to_captions(document: Document) -> None:
    figures = [b for b in document.blocks if isinstance(b, Figure)]
    assert [(f.label, f.grouping, len(f.panels), f.page) for f in figures] == [
        ("1", "single", 1, 1),
        ("2", "caption_run", 3, 2),
        (None, "unresolved", 1, 2),
        ("3", "single", 1, 2),
    ]
    single, run, unresolved, trailing = figures
    assert trailing.panels[0].asset == "native/images/c23.jpg"
    assert single.caption is not None
    assert plain_text(single.caption) == "Fig. 1 | Single figure."
    assert single.panels[0].asset == "native/images/p1.jpg"
    assert single.context_bbox_pt == (60.0, 80.0, 240.0, 440.0)
    assert [plain_text(note) for note in single.footnotes] == ["Footnote."]
    assert [panel.label for panel in run.panels] == ["a", None, None]
    assert [plain_text(note) for note in run.footnotes] == [
        "Some stray caption",
        "Chart note",
    ]
    assert run.context_bbox_pt is None
    assert unresolved.panels[0].label == "b"
    assert unresolved.panels[0].asset is None
    assert unresolved.caption is None
    codes = [f.code for f in document.findings if unresolved.id in f.block_ids]
    assert codes == ["CAPTION_UNMATCHED"]
    assert any(
        f.code == "FIGURE_GROUPING_HEURISTIC" and run.id in f.block_ids
        for f in document.findings
    )


def test_tables_keep_raw_html_and_exact_cells(document: Document) -> None:
    tables = [b for b in document.blocks if isinstance(b, Table)]
    assert [t.label for t in tables] == ["S1", "2", None, None, "3"]
    labeled_by_note = tables[4]
    assert labeled_by_note.caption is not None
    assert plain_text(labeled_by_note.caption) == "Parameters only"
    assert [plain_text(note) for note in labeled_by_note.footnotes] == [
        "Table 3. Labeled note"
    ]
    first = tables[0]
    assert first.caption is not None
    assert plain_text(first.caption) == "TABLE S1. Parameters."
    assert [plain_text(note) for note in first.footnotes] == ["a Estimated."]
    assert first.asset == "native/images/t10.jpg"
    assert (first.rows, first.columns) == (2, 3)
    assert first.cells is not None
    assert [(c.row, c.column, c.text) for c in first.cells] == [
        (0, 0, "A"),
        (0, 1, "Energya µJ"),
        (1, 1, "1.8"),
        (1, 2, "0.5"),
    ]
    assert first.cells[1].html == "Energy<sup>a</sup> &micro;J"
    assert first.html.startswith("<table>")
    second = tables[1]
    assert second.continues_previous is True
    assert [plain_text(note) for note in second.footnotes] == ["Second caption"]
    assert second.cells is not None and (second.rows, second.columns) == (2, 2)
    assert tables[2].cells is None and tables[2].html == ""
    assert tables[3].cells is None
    by_code: dict[str, list[Finding]] = {}
    for finding in document.findings:
        by_code.setdefault(finding.code, []).append(finding)
    assert [f.block_ids for f in by_code["TABLE_CAPTION_FROM_FOOTNOTE"]] == [
        (first.id,)
    ]
    assert {b for f in by_code["TABLE_STRUCTURE_UNRESOLVED"] for b in f.block_ids} == {
        second.id,
        tables[3].id,
    }
    assert [f.block_ids for f in by_code["TABLE_BODY_MISSING"]] == [(tables[2].id,)]
    assert (tables[3].id,) in [f.block_ids for f in by_code["CAPTION_UNMATCHED"]]
    assert any(f.code == "BLOCK_MALFORMED" and f.page == 2 for f in document.findings)


def test_metadata_observations_and_stable_json(
    document: Document, native_builder: Callable[[], dict[str, object]]
) -> None:
    assert [(m.field, m.value) for m in document.metadata] == [
        ("title", "Synthetic Paper"),
        ("author", "A. Author"),
        ("identifier", "doi:10.1000/x"),
    ]
    payload = document.to_json()
    decoded = json.loads(payload)
    assert decoded["schema"] == SCHEMA_NAME
    assert decoded["schema_version"] == SCHEMA_VERSION
    assert decoded["source_sha256"] == SHA
    kinds = [item["kind"] for item in decoded["blocks"]]
    assert kinds.count("figure") == 4
    assert kinds.count("table") == 5
    assert payload.endswith("\n")
    assert list(decoded) == sorted(decoded)
    ids = [item["id"] for item in decoded["blocks"]]
    assert len(ids) == len(set(ids))
    again = normalize_mineru(
        native_builder(),
        source_sha256=SHA,
        coverage=PageCoverage(requested=(1, 2, 3, 4), returned=(1, 2, 3), empty=(3,)),
        backend_version="4.0.5",
        assets={
            "native/middle_json.json",
            "native/images/page_0_equation_3.jpg",
            "native/images/p1.jpg",
        },
        page_sizes={3: (500.0, 700.0)},
    )
    assert again.to_json() == payload


def test_documents_without_metadata_or_layout_still_normalize() -> None:
    native: dict[str, object] = {"pages": [{"page_idx": 0, "blocks": []}]}
    document = normalize_mineru(
        native,
        source_sha256=SHA,
        coverage=PageCoverage(requested=(1,), returned=(1,), empty=(1,)),
        backend_version="4.0.5",
        assets=set(),
    )
    assert document.native_schema == "unknown"
    assert document.metadata == ()
    assert document.blocks == ()
    assert [f.code for f in document.findings] == ["PAGE_EMPTY"]


@pytest.mark.parametrize(
    ("latex", "label"),
    [
        ("x\\tag{1}", "1"),
        ("x\\tag*{ S2 }", "S2"),
        ("x\\tag{}", None),
        ("x\\tag{1}\\tag{2}", "2"),
        ("x", None),
    ],
)
def test_equation_labels_come_from_the_last_tag(latex: str, label: str | None) -> None:
    assert equation_label(latex) == label


@pytest.mark.parametrize(
    ("latex", "problems"),
    [
        ("\\frac{a}{b}", ()),
        ("\\{a\\}", ()),
        ("\\left(x\\right)", ()),
        ("\\begin{matrix}a\\end{matrix}", ()),
        ("a}", ("closing brace without an opening brace",)),
        ("{a", ("1 unclosed brace(s)",)),
        ("\\left(x", ("1 \\left versus 0 \\right",)),
        ("\\leftarrow", ()),
        ("\\end{matrix}", ("\\end{matrix} without matching \\begin",)),
        ("\\begin{matrix}", ("\\begin{matrix} without matching \\end",)),
        ("\\begin{a}\\end{b}", ("\\end{b} without matching \\begin",)),
    ],
)
def test_latex_balance_checks_report_without_repairing(
    latex: str, problems: tuple[str, ...]
) -> None:
    assert latex_balance_problems(latex) == problems


@pytest.mark.parametrize(
    ("caption_text", "label", "continued"),
    [
        ("TABLE I. Experimental results.", "I", False),
        ("TABLE III. (Continued).", "III", True),
        ("Table 2 (continued)", "2", True),
        ("Table IVa. Fits.", "IVa", False),
        ("Supplementary Table 2 | Schemes", "2", False),
        ("Table Comparison of methods", None, False),
    ],
)
def test_table_labels_accept_roman_numerals_and_continuations(
    caption_text: str, label: str | None, continued: bool
) -> None:
    table = {
        "type": "table",
        "index": 0,
        "bbox": [0.1, 0.1, 0.9, 0.5],
        "content": [
            {
                "type": "table_caption",
                "index": 0,
                "content": [{"type": "text", "content": caption_text}],
            },
            {
                "type": "table_body",
                "index": 0,
                "content": "<table><tr><td>1</td></tr></table>",
            },
        ],
    }
    native: dict[str, object] = {"pages": [{"page_idx": 0, "blocks": [table]}]}
    document = normalize_mineru(
        native,
        source_sha256=SHA,
        coverage=PageCoverage(requested=(1,), returned=(1,), empty=()),
        backend_version="4.0.5",
        assets=set(),
        page_sizes={1: (600.0, 800.0)},
    )
    (result,) = document.blocks
    assert isinstance(result, Table)
    assert result.label == label
    assert result.continues_previous is continued
