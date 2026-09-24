"""Cross-check extracted tables against Docling's independent reading."""

from collections import Counter
from dataclasses import replace

from conftest import DOCLING_ASSETS, docling_native
from paperextract.document import Document, Table, TableCell
from paperextract.normalize_docling import normalize_docling
from paperextract.protocol import PageCoverage
from paperextract.table_check import apply_table_check, check_numbers, table_check_pages


def reference() -> Document:
    return normalize_docling(
        docling_native(),
        source_sha256="c" * 64,
        coverage=PageCoverage(requested=(1, 2), returned=(1, 2), empty=()),
        backend_version="2.129.0",
        assets=DOCLING_ASSETS,
    )


def tables(document: Document) -> list[Table]:
    return [block for block in document.blocks if isinstance(block, Table)]


def test_agreeing_numbers_keep_the_body_and_add_docling_as_alternative(
    normalized_document: Document,
) -> None:
    assert table_check_pages(normalized_document) == (2,)
    checked = apply_table_check(normalized_document, reference())
    good = tables(checked)[0]
    before = tables(normalized_document)[0]
    assert (good.html, good.body_source) == (before.html, before.body_source)
    assert [body.source for body in good.alternatives] == ["docling"]
    assert good.alternatives[0].cells is not None
    findings = {f.code: f for f in checked.findings if f.block_ids == (good.id,)}
    assert "same 2 numbers" in findings["TABLE_CHECK_AGREED"].message
    codes = Counter(f.code for f in checked.findings)
    # The other MinerU tables on the page have no Docling counterpart, and the
    # two unmatched Docling tables are reported as possibly missed.
    assert codes["TABLE_CHECK_UNMATCHED"] == len(tables(checked)) - 1
    assert codes["TABLE_CHECK_EXTRA"] == 2
    extra = [f.message for f in checked.findings if f.code == "TABLE_CHECK_EXTRA"]
    assert any("an unlabelled table" in message for message in extra)
    assert any("Table 1 (1x1)" in message for message in extra)


def test_different_numbers_are_reported_with_both_sides(
    normalized_document: Document,
) -> None:
    first = tables(normalized_document)[0]
    assert first.cells is not None
    changed_cells = tuple(
        replace(cell, text="1.9") if cell.text == "1.8" else cell
        for cell in first.cells
    )
    blocks = tuple(
        replace(block, cells=changed_cells) if block.id == first.id else block
        for block in normalized_document.blocks
    )
    checked = apply_table_check(
        replace(normalized_document, blocks=blocks), reference()
    )
    (finding,) = [f for f in checked.findings if f.code == "TABLE_CHECK_DISAGREED"]
    assert "1 of 2 numbers agree" in finding.message
    assert "only here: ['1.9']; only in Docling: ['1.8']" in finding.message


def test_comparison_view_joins_split_decimals_and_drops_glyph_codes() -> None:
    cells = (
        TableCell(0, 0, 1, 1, "", "2. 534 + 0.019"),
        TableCell(0, 1, 1, 1, "", "14 .5 and .0137 E.0138 Ω0.0137z"),
        TableCell(1, 0, 1, 1, "", "a real 0.0139"),
    )
    table = replace(tables_in_reference()[0], cells=cells, html="<table></table>")
    assert sorted(check_numbers(table).elements()) == [
        "0",
        "0.0139",
        "0.019",
        "14",
        "2.534",
        "5",
    ]


def tables_in_reference() -> list[Table]:
    return tables(reference())
