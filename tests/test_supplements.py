"""Anchor supplement objects and link references to them."""

import pytest

from paperextract.document import (
    Document,
    Equation,
    Figure,
    Heading,
    InlineRun,
    ListBlock,
    Paragraph,
    SourceSpan,
    Table,
)
from paperextract.supplements import (
    DOCUMENT_KEY,
    Reference,
    SupplementIndex,
    anchor_ids,
    find_references,
    link_references,
    reference_target,
)

SPAN = SourceSpan(1, None, None, "text", 0)


def text(value: str) -> tuple[InlineRun, ...]:
    return (InlineRun("text", value),)


def figure(name: str, label: str | None) -> Figure:
    return Figure(name, label, text(f"FIG. {label}."), None, (), (), None, "single", 1)


def table(name: str, label: str | None, continued: bool = False) -> Table:
    return Table(name, label, None, (), "", None, None, None, None, SPAN, continued)


def doc(*blocks: object) -> Document:
    return Document("a" * 64, "mineru", "4.0.5", "s", "1", (), blocks, (), ())  # type: ignore[arg-type]


SUPPLEMENT = doc(
    figure("f1", "S1"),
    figure("f1b", "S1"),
    figure("f2", "2a"),
    figure("fx", None),
    figure("fy", "A"),
    table("t1", "S1"),
    table("t1c", "S1", continued=True),
    table("tx", None),
    Equation("e5", "x\\tag{S5}", "S5", None, SPAN),
    Equation("ex", "y", None, None, SPAN),
    Heading("h2", 1, text("S2. MAXIMUM SOLITON ORDER"), SPAN),
    Heading("h3", 1, text("Supplementary Note 3: Brightness"), SPAN),
    Heading("hx", 1, text("Methods"), SPAN),
    Paragraph("p", "body", text("Body"), SPAN),
)


def test_referenceable_objects_get_stable_anchors() -> None:
    assert anchor_ids(SUPPLEMENT) == {
        "f1": "figure-s1",
        "f2": "figure-s2a",
        "t1": "table-s1",
        "e5": "equation-s5",
        "h2": "section-s2",
        "h3": "section-s3",
    }


def test_indexes_link_from_the_paper_and_within_a_supplement() -> None:
    other = doc(figure("g1", "S1"), figure("g9", "S9"))
    paper = SupplementIndex.build(
        [
            ("supplement_01/supplement.md", SUPPLEMENT),
            ("supplement_02/supplement.md", other),
        ]
    )
    assert paper.targets[DOCUMENT_KEY] == "supplement_01/supplement.md"
    assert paper.targets[("figure", "1")] == "supplement_01/supplement.md#figure-s1"
    assert paper.targets[("figure", "9")] == "supplement_02/supplement.md#figure-s9"
    inside = SupplementIndex.build(
        [("supplement.md", SUPPLEMENT), ("../supplement_02/supplement.md", other)],
        own="supplement.md",
    )
    assert DOCUMENT_KEY not in inside.targets
    assert inside.targets[("section", "3")] == "#section-s3"
    assert inside.targets[("figure", "9")] == "../supplement_02/supplement.md#figure-s9"


INDEX = SupplementIndex.build([("s.md", SUPPLEMENT)])


@pytest.mark.parametrize(
    ("prose", "linked"),
    [
        ("(Supplementary Fig. 1)", "([Supplementary Fig. 1](s.md#figure-s1))"),
        (
            "Supplementary Figs. 2a and 2b",
            "[Supplementary Figs. 2a](s.md#figure-s2a) and 2b",
        ),
        (
            "see Eq. (S5), Table S1 and Fig. S7",
            "see [Eq. (S5)](s.md#equation-s5), [Table S1](s.md#table-s1) and Fig. S7",
        ),
        (
            "Supplementary Note 3 and Supplementary Section 2",
            "[Supplementary Note 3](s.md#section-s3) and "
            "[Supplementary Section 2](s.md#section-s2)",
        ),
        ("the Supplementary Information.", "the [Supplementary Information](s.md)."),
        (
            "Supplement 1, Supporting Information",
            "[Supplement 1](s.md), [Supporting Information](s.md)",
        ),
        ("Fig. 2 and Table 1 of this paper", "Fig. 2 and Table 1 of this paper"),
        ("Supplementary Fig. S1", "[Supplementary Fig. S1](s.md#figure-s1)"),
    ],
)
def test_references_in_prose_become_links(prose: str, linked: str) -> None:
    assert link_references(prose, INDEX) == linked


def test_a_link_run_resolves_only_when_it_is_one_reference() -> None:
    assert reference_target(" Supplement 1 ", INDEX) == "s.md"
    assert reference_target("see Supplement 1", INDEX) is None
    assert reference_target("Fig. S1 and Fig. S5", INDEX) is None
    assert reference_target("Fig. S7", INDEX) is None


def test_references_are_found_in_prose_captions_and_lists_but_not_math() -> None:
    paper = doc(
        Paragraph(
            "p1",
            "body",
            (
                InlineRun("text", "see Supplementary Fig. 1"),
                InlineRun("math", "Fig. S1"),
                InlineRun("code", "Table S1"),
                InlineRun("link", "Supplement 1", url="https://doi.org/x"),
            ),
            SPAN,
        ),
        Heading("h", 1, text("Methods (Supplementary Note 9)"), SPAN),
        ListBlock("l", (text("Table S1"),), SPAN),
        Figure(
            "f",
            "1",
            text("Fig. 1 | see Fig. S1a"),
            None,
            (text("Fig. S2"),),
            (),
            None,
            "single",
            1,
        ),
        Table(
            "t",
            "1",
            None,
            (text("Supplementary Table 1"),),
            "",
            None,
            None,
            None,
            None,
            SPAN,
        ),
        Equation("e", "Fig. S1", None, None, SPAN),
    )
    assert find_references(paper, INDEX) == [
        Reference("p1", "Supplementary Fig. 1", "s.md#figure-s1"),
        Reference("p1", "Supplement 1", "s.md"),
        Reference("h", "Supplementary Note 9", None),
        Reference("l", "Table S1", "s.md#table-s1"),
        Reference("f", "Fig. S1a", None),
        Reference("f", "Fig. S2", None),
        Reference("t", "Supplementary Table 1", "s.md#table-s1"),
    ]
