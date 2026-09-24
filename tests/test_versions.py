"""Decide document versions and relate papers already in the library."""

import pytest

from paperextract.document import (
    Document,
    Heading,
    InlineRun,
    PageFurniture,
    PageRecord,
    Paragraph,
    SourceSpan,
)
from paperextract.identity import Candidate, Field, Identity, Observed
from paperextract.versions import (
    decide_version,
    library_relations,
    relation_findings,
)

DOI = "10.1038/s41566-019-0416-4"


def document(*texts: str, page: int = 1) -> Document:
    span = SourceSpan(1, None, None, "text", 0)
    body = SourceSpan(page, None, None, "text", 1)
    blocks = (
        Heading("hd", 1, (InlineRun("text", "Title"),), span),
        PageFurniture("aux", "header", (InlineRun("text", "Nature Photonics"),), span),
        *(
            Paragraph(f"p{i}", "body", (InlineRun("text", t),), body)
            for i, t in enumerate(texts)
        ),
    )
    return Document(
        source_sha256="a" * 64,
        backend="mineru",
        backend_version="4",
        native_schema="x",
        native_schema_version="1",
        pages=(PageRecord(1, 1.0, 1.0, "processed"),),
        blocks=blocks,
        metadata=(),
        findings=(),
    )


def identity(
    *,
    sources: tuple[str, ...] = ("page_1_furniture",),
    journal: object = "Nature Photonics",
    authors: object = None,
    doi: str | None = DOI,
    status: str = "VALIDATED",
) -> Identity:
    fields = (
        Field("title", "High-Energy Pulse Self-Compression", "VALIDATED", "crossref"),
        Field("journal", journal, "VALIDATED", "crossref"),
        Field("year", 2019, "VALIDATED", "crossref"),
        Field(
            "authors",
            [{"family": "Travers", "given": "J."}] if authors is None else authors,
            "VALIDATED",
            "crossref",
        ),
    )
    return Identity(
        status=status,  # type: ignore[arg-type]
        work_id="work_x",
        doi=doi,
        provider="crossref",
        record=None,
        fields=fields,
        checks=(),
        candidates=(Candidate(DOI, "doi", sources),),
        alternatives=(),
        observed=Observed("Title", (), ()),
        reasons=(),
    )


def test_a_printed_validated_doi_with_its_journal_is_the_version_of_record() -> None:
    decision = decide_version(document("Body."), identity())
    assert decision.version == "version_of_record"
    assert decision.to_dict()["asserted"] is False
    assert decide_version(document(), identity(sources=("page_1_text",))).version == (
        "unknown"
    )
    assert decide_version(document(), identity(journal=None)).version == "unknown"
    assert decide_version(document(), identity(status="UNVERIFIED")).version == (
        "unknown"
    )


@pytest.mark.parametrize(
    ("text", "version"),
    [
        ("This is the author's accepted manuscript.", "accepted_manuscript"),
        ("Submitted to Physical Review Letters.", "submitted_manuscript"),
        ("A preprint of our work.", "preprint"),
    ],
)
def test_manuscript_markers_on_the_first_pages_decide(text: str, version: str) -> None:
    assert decide_version(document(text), identity()).version == version
    # Beyond the first two pages a marker is ignored.
    assert decide_version(document(text, page=3), identity()).version == (
        "version_of_record"
    )


def test_assertions_and_arxiv_come_before_the_file_evidence() -> None:
    asserted = decide_version(document(), identity(), hint="accepted_manuscript")
    assert (asserted.version, asserted.asserted) == ("accepted_manuscript", True)
    with pytest.raises(ValueError, match="Unknown document version"):
        decide_version(document(), identity(), hint="draft")
    named = decide_version(document(), identity(), file_name="Travers_2018_AAM.pdf")
    assert named.version == "accepted_manuscript"
    assert decide_version(
        document(), identity(), file_name="draft_submitted.pdf"
    ).version == ("submitted_manuscript")
    assert decide_version(
        document(), identity(), file_name="a_preprint.pdf"
    ).version == ("preprint")
    # A marker inside a word is not a marker.
    assert decide_version(document(), identity(), file_name="gaamma.pdf").version == (
        "version_of_record"
    )
    eprint = decide_version(document(), identity(), arxiv="2206.01062v1")
    assert eprint.version == "preprint"
    assert "arXiv:2206.01062v1" in eprint.evidence[0]


def rows() -> list[dict[str, object]]:
    return [
        {
            "directory": "Old",
            "doi": DOI.upper(),
            "document_version": "version_of_record",
        },
        {
            "directory": "2019/Twin",
            "doi": None,
            "title_key": "high energy pulse self compression",
            "first_author_family": "Travers",
            "year": 2019,
        },
        {
            "directory": "Other",
            "doi": "10.1/other",
            "title_key": "high energy pulse self compression",
            "first_author_family": "Someone",
            "year": 2019,
        },
        {"directory": "Replaced", "doi": DOI},
    ]


def test_same_doi_and_same_title_author_year_are_related() -> None:
    relations = library_relations(identity(), rows(), exclude="Replaced")
    assert [(r.directory, r.relation) for r in relations] == [
        ("Old", "same_work"),
        ("Twin", "possible_duplicate"),
    ]
    assert relations[1].to_dict()["other_version"] == "unknown"
    findings = relation_findings(relations, "accepted_manuscript")
    assert [f.code for f in findings] == [
        "OTHER_VERSION_IN_LIBRARY",
        "DUPLICATE_CANDIDATE",
    ]
    same = relation_findings(relations[:1], "version_of_record")
    assert [f.code for f in same] == ["DUPLICATE_CANDIDATE"]


@pytest.mark.parametrize("authors", [[], ["Travers"], [{"family": ""}], "Travers"])
def test_unknown_first_authors_never_make_a_possible_duplicate(authors: object) -> None:
    relations = library_relations(identity(authors=authors, doi=None), rows())
    assert relations == ()
