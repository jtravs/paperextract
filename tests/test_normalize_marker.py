"""Normalize the Marker worker's native output into canonical blocks."""

from collections import Counter

import pytest

from conftest import MARKER_ASSETS, marker_native
from paperextract.document import (
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
from paperextract.normalize_marker import html_runs, normalize_marker
from paperextract.protocol import PageCoverage

COVERAGE = PageCoverage(requested=(1, 2, 3), returned=(1, 2), empty=(2,))


def normalized() -> Document:
    return normalize_marker(
        marker_native(),
        source_sha256="c" * 64,
        coverage=COVERAGE,
        backend_version="2.0.0",
        assets=MARKER_ASSETS,
        page_sizes={2: (300.0, 200.0)},
    )


def test_html_becomes_runs_with_math_styles_and_links() -> None:
    runs = html_runs(
        "<p>Energy <math>E=mc^2</math> of <b>bold</b> x<sup>2</sup> "
        '<a href="https://example.org">link</a><br><content-ref src="x">hidden'
        "</content-ref> end</p>"
    )
    assert [(r.kind, r.text, r.styles) for r in runs] == [
        ("text", "Energy ", ()),
        ("math", "E=mc^2", ()),
        ("text", " of ", ()),
        ("text", "bold", ("bold",)),
        ("text", " x", ()),
        ("text", "2", ("superscript",)),
        ("text", " ", ()),
        ("link", "link", ()),
        ("text", " end", ()),
    ]
    assert runs[7].url == "https://example.org"
    assert html_runs("<p>  </p>") == ()


def test_blocks_map_to_canonical_kinds_in_reading_order() -> None:
    document = normalized()
    assert document.backend == "marker"
    kinds = [type(block).__name__ for block in document.blocks]
    assert kinds[0] == "PageFurniture" and kinds[-1] == "PageFurniture"
    headings = [b for b in document.blocks if isinstance(b, Heading)]
    assert [(h.level, plain_text(h.runs)) for h in headings] == [
        (1, "A Synthetic Title"),
        (2, "No heading tag"),
    ]
    assert headings[0].span.bbox_pt == (10.0, 10.0, 100.0, 20.0)
    assert headings[0].span.bbox_fraction == (
        round(10 / 300, 6),
        0.05,
        round(100 / 300, 6),
        0.1,
    )
    roles = {
        plain_text(b.runs): b.role for b in document.blocks if isinstance(b, Paragraph)
    }
    assert roles["a Note."] == "footnote"
    assert roles["[1] A reference."] == "reference"
    assert roles["Orphan caption"] == "aside"
    assert "tail b" in roles
    code = next(
        b
        for b in document.blocks
        if isinstance(b, Paragraph) and b.runs and b.runs[0].kind == "code"
    )
    assert code.runs[0].text == "x = 1"
    lists = [b for b in document.blocks if isinstance(b, ListBlock)]
    assert [len(item.items) for item in lists] == [2, 1]
    furniture = [b.role for b in document.blocks if isinstance(b, PageFurniture)]
    assert furniture == ["header", "footer"]
    raw = {b.backend_type for b in document.blocks if isinstance(b, Unclassified)}
    assert raw == {"TableGroup", "PictureGroup", "Form"}
    assert [m.field for m in document.metadata] == ["title"]
    assert [(p.width_pt, p.status) for p in document.pages] == [
        (300.0, "processed"),
        (300.0, "empty"),
        (None, "missing"),
    ]
    codes = Counter(f.code for f in document.findings)
    assert codes["PAGE_MALFORMED"] == codes["PAGE_NOT_PROCESSED"] == 1
    assert codes["CAPTION_UNATTACHED"] == 1


def test_equations_keep_latex_and_their_printed_number() -> None:
    document = normalized()
    equations = [b for b in document.blocks if isinstance(b, Equation)]
    assert [(e.latex, e.label) for e in equations] == [
        ("a = b \\quad (3)", "3"),
        ("", None),
        ("\\frac{a", None),
    ]
    codes = Counter(f.code for f in document.findings)
    assert codes["EQUATION_EMPTY"] == codes["EQUATION_UNBALANCED"] == 1


def test_tables_and_figures_take_their_group_captions() -> None:
    document = normalized()
    tables = [b for b in document.blocks if isinstance(b, Table)]
    grouped, ragged, broken = tables
    assert grouped.label == "1"
    assert (
        grouped.caption is not None
        and plain_text(grouped.caption) == "Table 1. Energies"
    )
    assert [plain_text(n) for n in grouped.footnotes] == [
        "Second caption",
        "b Estimated.",
    ]
    assert grouped.cells is not None and grouped.rows == 3
    assert grouped.asset == "native/images/page_0_Table_19.jpg"
    assert ragged.cells is not None and broken.cells is None
    figures = [b for b in document.blocks if isinstance(b, Figure)]
    single, lone, pair, unplaced = figures
    assert unplaced.context_bbox_pt is None
    assert (single.label, single.grouping) == ("2", "single")
    assert single.panels[0].asset == "native/images/page_0_Figure_27.jpg"
    assert (lone.label, lone.grouping) == (None, "unresolved")
    assert (pair.label, pair.grouping, len(pair.panels)) == ("3", "caption_run", 2)
    assert pair.panels[1].asset is None
    codes = Counter(f.code for f in document.findings)
    assert codes["ASSET_MISSING"] == 1
    assert codes["CAPTION_UNMATCHED"] == 4  # two tables, two figures
    assert codes["TABLE_STRUCTURE_UNRESOLVED"] == 2


def test_other_wrappers_are_refused() -> None:
    with pytest.raises(ValueError, match=r"paperextract\.marker-native"):
        normalize_marker(
            {"schema": "x"},
            source_sha256="c" * 64,
            coverage=COVERAGE,
            backend_version="2",
            assets=(),
        )
