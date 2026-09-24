"""Correct reading order and classification across blocks and pages."""

from dataclasses import replace

from paperextract.document import (
    Block,
    Box,
    Document,
    Figure,
    Heading,
    InlineRun,
    PageFurniture,
    Paragraph,
    SourceSpan,
    Table,
    plain_text,
)
from paperextract.layout import (
    flag_unmapped_glyphs,
    gather_interleaved_references,
    reclassify_repeated_furniture,
    refine_document,
    restore_drop_caps,
)


def span(page: int, fraction: Box | None = (0.1, 0.03, 0.3, 0.05)) -> SourceSpan:
    points = None if fraction is None else tuple(v * 600 for v in fraction)
    return SourceSpan(page, points, fraction, "text", 0)  # type: ignore[arg-type]


def para(
    name: str, page: int, role: str = "body", *runs: InlineRun, box: Box | None = None
) -> Paragraph:
    return Paragraph(
        id=name,
        role=role,  # type: ignore[arg-type]
        runs=runs or (InlineRun("text", name),),
        span=span(page, box or (0.1, 0.3, 0.5, 0.4)),
    )


def header(page: int, text: str = "Research Article", box: Box | None = None) -> Block:
    return PageFurniture(
        f"hdr{page}",
        "header",
        (InlineRun("text", text),),
        span(page, box or (0.1, 0.03, 0.3, 0.05)),
    )


def doc(*blocks: Block) -> Document:
    return Document(
        source_sha256="a" * 64,
        backend="mineru",
        backend_version="4.0.5",
        native_schema="s",
        native_schema_version="1",
        pages=(),
        blocks=blocks,
        metadata=(),
        findings=(),
    )


def codes(document: Document) -> list[str]:
    return [finding.code for finding in document.findings]


def test_a_heading_repeating_the_running_header_becomes_furniture() -> None:
    heading = Heading(
        "hd3",
        2,
        (InlineRun("text", "Research  article"),),
        span(3, (0.105, 0.035, 0.3, 0.05)),
    )
    footer_like = replace(heading, id="hd4", span=span(4, (0.1, 0.95, 0.3, 0.97)))
    result = reclassify_repeated_furniture(
        doc(
            header(1),
            header(2),
            heading,
            header(4, "Page footer", (0.1, 0.95, 0.3, 0.97)),
            header(5, "Page footer", (0.1, 0.95, 0.3, 0.97)),
            footer_like,
        )
    )
    moved = [
        b
        for b in result.blocks
        if isinstance(b, PageFurniture) and b.id in {"hd3", "hd4"}
    ]
    assert [b.id for b in moved] == ["hd3"]
    assert moved[0].role == "header"
    assert codes(result) == ["FURNITURE_RECLASSIFIED"]
    assert result.findings[0].page == 3


def test_footers_and_unmatched_blocks_are_judged_by_position_and_count() -> None:
    footer_text = para("Optica 6, 495", 3, box=(0.1, 0.95, 0.3, 0.97))
    elsewhere = replace(footer_text, id="elsewhere", span=span(4, (0.5, 0.5, 0.7, 0.6)))
    once = para("Once", 3, box=(0.1, 0.03, 0.3, 0.05))
    empty = Paragraph("empty", "body", (), span(3, (0.1, 0.03, 0.3, 0.05)))
    unplaced = Paragraph(
        "unplaced", "body", (InlineRun("text", "Optica 6, 495"),), span(3, None)
    )
    figure = Figure("fig", "1", None, None, (), (), None, "single", 3)
    blocks = (
        header(1, "Optica 6, 495", (0.1, 0.95, 0.3, 0.97)),
        header(2, "Optica 6, 495", (0.11, 0.955, 0.3, 0.97)),
        header(5, "Once", (0.1, 0.03, 0.3, 0.05)),
        PageFurniture("nobox", "header", (InlineRun("text", "Once"),), span(6, None)),
        footer_text,
        elsewhere,
        once,
        empty,
        unplaced,
        figure,
    )
    result = reclassify_repeated_furniture(doc(*blocks))
    kinds = {b.id: type(b).__name__ for b in result.blocks}
    assert kinds["Optica 6, 495"] == "PageFurniture"
    assert next(b for b in result.blocks if b.id == "Optica 6, 495").role == "footer"  # type: ignore[union-attr]
    assert kinds["elsewhere"] == "Paragraph"
    assert kinds["Once"] == "Paragraph"
    assert kinds["empty"] == "Paragraph"
    assert kinds["unplaced"] == "Paragraph"
    assert codes(result) == ["FURNITURE_RECLASSIFIED"]


def test_interleaved_reference_columns_are_joined_in_order() -> None:
    blocks = (
        para("left prose", 14),
        para("ref 1", 14, "reference"),
        para("ref 2", 14, "reference"),
        header(14),
        para("right prose", 14),
        Heading("ack", 2, (InlineRun("text", "Acknowledgments"),), span(14)),
        para("ref 3", 14, "reference"),
        header(14, "running"),
        para("ref 4", 14, "reference"),
        para("ref 5", 15, "reference"),
        para("more prose", 15),
    )
    result = gather_interleaved_references(doc(*blocks))
    assert [b.id for b in result.blocks] == [
        "left prose",
        "hdr14",
        "right prose",
        "ack",
        "ref 1",
        "ref 2",
        "ref 3",
        "hdr14",
        "ref 4",
        "ref 5",
        "more prose",
    ]
    (finding,) = result.findings
    assert finding.code == "REFERENCES_REORDERED"
    assert finding.block_ids == ("ref 1", "ref 2")
    assert finding.page == 14


def test_a_reference_list_ending_before_a_section_stays_in_place() -> None:
    blocks = (
        para("ref 50", 7, "reference"),
        Heading("methods", 1, (InlineRun("text", "Methods"),), span(7)),
        para("methods text", 7),
        Figure("fig", "4", None, None, (), (), None, "single", 8),
    )
    result = gather_interleaved_references(doc(*blocks))
    assert result.blocks == blocks
    assert result.findings == ()


def test_unmapped_glyphs_are_reported_per_block() -> None:
    table = Table(
        "tbl", "1", None, (), "<td>F\x05E\x06</td>", None, None, None, None, span(2)
    )
    clean = para("clean", 2)
    tabbed = para("tab\tand\nnewline", 2)
    result = flag_unmapped_glyphs(doc(table, clean, tabbed))
    (finding,) = result.findings
    assert finding.code == "UNMAPPED_GLYPHS"
    assert finding.block_ids == ("tbl",)
    assert finding.message.startswith("2 characters")
    assert result.blocks == (table, clean, tabbed)


def drop_cap(page: int, box: Box, fragment: str) -> str | None:
    assert page == 1
    assert box[0] == 60.0
    return "H" if fragment == "ollow" else None


def test_drop_caps_are_restored_only_with_text_layer_evidence() -> None:
    restored = para(
        "restored",
        1,
        "body",
        InlineRun("text", "ollow", ("superscript", "bold")),
        InlineRun("text", " capillary fibres"),
        InlineRun("text", "1,2", ("superscript",)),
    )
    suspected = para(
        "suspected", 1, "body", InlineRun("text", "ptical", ("superscript",))
    )
    ordinary = [
        para("citation", 1, "body", InlineRun("text", "12", ("superscript",))),
        para("single", 1, "body", InlineRun("text", "a", ("superscript",))),
        para("plain", 1, "body", InlineRun("text", "ollow")),
        para("math", 1, "body", InlineRun("math", "ab", ("superscript",))),
        Paragraph("empty", "body", (), span(1)),
        Paragraph(
            "nobox",
            "body",
            (InlineRun("text", "ollow", ("superscript",)),),
            span(1, None),
        ),
        Figure("fig", "1", None, None, (), (), None, "single", 1),
    ]
    result = restore_drop_caps(doc(restored, suspected, *ordinary), drop_cap)
    first = result.blocks[0]
    assert isinstance(first, Paragraph)
    assert plain_text(first.runs) == "Hollow capillary fibres1,2"
    assert first.runs[0] == InlineRun("text", "Hollow", ("bold",))
    assert first.runs[2].styles == ("superscript",)
    assert result.blocks[1] == suspected
    assert result.blocks[2:] == tuple(ordinary)
    assert codes(result) == ["DROP_CAP_RESTORED", "DROP_CAP_SUSPECTED"]


def test_refinement_applies_every_pass_in_order() -> None:
    blocks = (
        header(1),
        header(2),
        Heading("hd3", 2, (InlineRun("text", "Research Article"),), span(3)),
        para(
            "drop",
            3,
            "body",
            InlineRun("text", "ollow", ("superscript",)),
            box=(0.1, 0.3, 0.5, 0.4),
        ),
        para("glyph\x01", 3),
    )
    without = refine_document(doc(*blocks))
    assert codes(without) == ["FURNITURE_RECLASSIFIED", "UNMAPPED_GLYPHS"]

    def lookup(page: int, box: Box, fragment: str) -> str | None:
        del page, box, fragment
        return "H"

    with_lookup = refine_document(doc(*blocks), lookup)
    assert codes(with_lookup) == [
        "FURNITURE_RECLASSIFIED",
        "DROP_CAP_RESTORED",
        "UNMAPPED_GLYPHS",
    ]
