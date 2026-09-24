"""Normalize the Docling worker's native output into canonical blocks."""

from collections import Counter
from typing import cast

import pytest

from conftest import DOCLING_ASSETS, docling_native
from paperextract.document import (
    Block,
    Document,
    Equation,
    Figure,
    Heading,
    ListBlock,
    PageFurniture,
    Paragraph,
    Table,
    Unclassified,
    plain_text,
)
from paperextract.normalize_docling import glyph_code_count, normalize_docling
from paperextract.protocol import PageCoverage

SHA = "c" * 64
COVERAGE = PageCoverage(requested=(1, 2, 3), returned=(1, 2), empty=(2,))


def normalized(*, formulas: bool = True) -> Document:
    native = docling_native(formulas=formulas)
    return normalize_docling(
        native,
        source_sha256=SHA,
        coverage=COVERAGE,
        backend_version="2.129.0",
        assets=DOCLING_ASSETS,
        page_sizes={1: (300.0, 200.0), 2: (310.0, 200.0)},
    )


def codes(document: Document) -> Counter[str]:
    return Counter(finding.code for finding in document.findings)


def test_blocks_follow_docling_reading_order_with_furniture_at_page_edges() -> None:
    document = normalized()
    assert document.backend == "docling"
    assert (document.native_schema, document.native_schema_version) == (
        "DoclingDocument",
        "1.10.0",
    )
    first, *_rest, last_on_page_one = [b for b in document.blocks if _page(b) == 1]
    assert isinstance(first, PageFurniture) and first.role == "header"
    assert isinstance(last_on_page_one, PageFurniture)
    assert last_on_page_one.role == "footer"
    headings = [b for b in document.blocks if isinstance(b, Heading)]
    assert [(h.level, plain_text(h.runs)) for h in headings] == [
        (1, "A Synthetic Title"),
        (2, "Methods"),
        (2, "References"),
    ]
    # Coordinates turn from Docling's bottom-left origin into top-left points.
    assert headings[0].span.bbox_pt == (10.0, 10.0, 290.0, 20.0)
    assert headings[0].span.bbox_fraction == (
        round(10 / 300, 6),
        0.05,
        round(290 / 300, 6),
        0.1,
    )
    assert headings[0].span.backend_type == "title"
    assert headings[0].span.backend_index == 0
    assert [p.number for p in document.pages] == [1, 2, 3]
    # Page sizes follow Docling's own measurement, which its boxes refer to.
    assert [(p.width_pt, p.status) for p in document.pages] == [
        (300.0, "processed"),
        (300.0, "empty"),
        (None, "missing"),
    ]
    assert [m.field for m in document.metadata] == ["title", "creator_application"]


def _page(block: Block) -> int:
    return block.page if isinstance(block, Figure) else block.span.page


def synthetic_document(native: dict[str, object]) -> dict[str, object]:
    runs = cast("list[dict[str, object]]", native["runs"])
    return cast("dict[str, object]", runs[0]["document"])


def synthetic_texts(native: dict[str, object]) -> list[dict[str, object]]:
    return cast("list[dict[str, object]]", synthetic_document(native)["texts"])


def test_text_labels_map_to_roles_styles_links_and_code() -> None:
    document = normalized()
    paragraphs = {
        plain_text(b.runs): b for b in document.blocks if isinstance(b, Paragraph)
    }
    assert paragraphs["Bold prose"].runs[0].styles == ("bold", "superscript")
    link = paragraphs["A link"].runs[0]
    assert (link.kind, link.url) == ("link", "https://example.org")
    assert paragraphs["a Footnote."].role == "footnote"
    assert paragraphs["x = 1"].runs[0].kind == "code"
    assert paragraphs["Orphan caption"].role == "aside"
    assert paragraphs["1. Ref one."].role == "reference"
    assert paragraphs["Ref two."].role == "reference"
    assert paragraphs["inside a group"].role == "body"
    # A non-item child of a list group is still normalized.
    assert "in a list group but not an item" in paragraphs
    # Text inside a picture belongs to the picture, not to the body.
    assert "axis label inside the picture" not in paragraphs
    lists = [b for b in document.blocks if isinstance(b, ListBlock)]
    assert [len(item.items) for item in lists] == [2, 1]
    found = codes(document)
    assert found["CAPTION_UNATTACHED"] == 1
    assert found["BLOCK_UNCLASSIFIED"] == 3
    assert found["GLYPH_CODES"] == 2
    assert found["BACKEND_ERROR"] == 1
    assert found["PAGE_SIZE_MISMATCH"] == 1
    assert found["PAGE_NOT_PROCESSED"] == found["PAGE_EMPTY"] == 1
    kinds = {b.backend_type for b in document.blocks if isinstance(b, Unclassified)}
    assert kinds == {"checkbox_selected", "form", "handwritten_text"}


def test_tables_keep_docling_cells_spans_and_empty_positions() -> None:
    document = normalized()
    tables = [b for b in document.blocks if isinstance(b, Table)]
    table, empty, coded = tables
    assert table.label == "1"
    assert (
        table.caption is not None and plain_text(table.caption) == "Table 1. Energies"
    )
    assert [plain_text(note) for note in table.footnotes] == ["a Estimated."]
    assert table.asset == "native/images/r01-table-000.png"
    assert table.span.bbox_pt == (30.0, 140.0, 270.0, 160.0)
    assert table.cells is not None
    placed = {
        (c.row, c.column): (c.text, c.row_span, c.column_span) for c in table.cells
    }
    assert placed[(0, 0)] == ("A", 2, 1)
    assert placed[(0, 1)] == ("Energy µJ", 1, 2)
    assert placed[(1, 1)] == ("1.8", 1, 1)
    assert '<th colspan="2">Energy µJ</th>' in table.html
    # An uncovered grid position becomes an empty cell rather than shifting.
    assert placed[(2, 0)] == ("x", 1, 1)
    assert placed[(2, 1)] == ("", 1, 1)
    problems = [
        f.message
        for f in document.findings
        if f.code == "TABLE_STRUCTURE_UNRESOLVED" and f.block_ids == (table.id,)
    ]
    assert problems and "'overlap' at row 1, column 0 overlaps" in problems[0]
    assert empty.cells is None and empty.html == ""
    assert coded.label == "1"
    assert codes(document)["TABLE_BODY_MISSING"] == 1


def test_pictures_become_single_panel_figures_with_captions() -> None:
    document = normalized()
    figures = [b for b in document.blocks if isinstance(b, Figure)]
    unresolved, other_page_caption, captioned = figures
    assert unresolved.grouping == "unresolved"
    assert unresolved.panels[0].asset == "native/images/r01-picture-001.png"
    assert other_page_caption.label == "2"
    # A caption on another page does not stretch the figure's context box.
    assert (
        other_page_caption.context_bbox_pt == other_page_caption.panels[0].span.bbox_pt
    )
    assert captioned.label == "2" and captioned.grouping == "single"
    assert captioned.panels[0].asset == "native/images/r01-picture-000.png"
    assert captioned.context_bbox_pt == (10.0, 10.0, 290.0, 100.0)
    assert [plain_text(note) for note in captioned.footnotes] == ["a Estimated."]
    found = codes(document)
    assert found["ASSET_MISSING"] == 1
    assert found["CAPTION_UNMATCHED"] == 2


def test_formulas_need_the_formula_model_for_latex() -> None:
    (equation,) = [b for b in normalized().blocks if isinstance(b, Equation)]
    assert (equation.latex, equation.label) == ("E = m c ^ { 2 }", "1")
    without = normalized(formulas=False)
    (equation,) = [b for b in without.blocks if isinstance(b, Equation)]
    assert equation.latex == ""
    (finding,) = [f for f in without.findings if f.code == "EQUATION_NOT_TRANSCRIBED"]
    assert "E=mc2 (1)" in finding.message


def test_empty_formula_text_and_unknown_wrappers_are_reported() -> None:
    native = docling_native()
    synthetic_texts(native)[9]["text"] = " "
    result = normalize_docling(
        native,
        source_sha256=SHA,
        coverage=COVERAGE,
        backend_version="x",
        assets=DOCLING_ASSETS,
    )
    assert codes(result)["EQUATION_EMPTY"] == 1
    with pytest.raises(ValueError, match=r"paperextract\.docling-native"):
        normalize_docling(
            {"schema": "other"},
            source_sha256=SHA,
            coverage=COVERAGE,
            backend_version="x",
            assets=(),
        )


def test_boxes_without_page_size_or_malformed_stay_unknown() -> None:
    native = docling_native()
    synthetic_document(native)["pages"] = {}
    texts = synthetic_texts(native)
    texts[1]["prov"] = []
    texts[4]["prov"] = [{"page_no": 1}]
    texts[2]["prov"] = [{"page_no": 1, "bbox": {"l": "x"}}]
    box = {"l": 1, "t": 2, "r": 3, "b": 4, "coord_origin": "TOPLEFT"}
    texts[3]["prov"] = [{"page_no": 1, "bbox": box}]
    result = normalize_docling(
        native,
        source_sha256=SHA,
        coverage=COVERAGE,
        backend_version="x",
        assets=DOCLING_ASSETS,
    )
    spans = {
        plain_text(b.runs): b.span
        for b in result.blocks
        if isinstance(b, Heading | Paragraph)
    }
    assert spans["A Synthetic Title"].bbox_pt is None
    assert spans["Methods"].page == 1 and spans["Methods"].bbox_pt is None
    assert spans["Bold prose"].bbox_pt is None
    assert spans["a Footnote."].bbox_pt is None
    assert spans["A link"].bbox_pt == (1.0, 2.0, 3.0, 4.0)
    assert spans["A link"].bbox_fraction is None


def test_glyph_codes_are_counted_only_when_detached_from_digits() -> None:
    assert glyph_code_count("value 0.0137 and 10.0137") == 0
    assert glyph_code_count("E.0133t .0134") == 2
