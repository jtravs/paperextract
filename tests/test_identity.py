"""Resolve bibliographic identity from candidates, records and observations."""

import json
from dataclasses import replace

import pytest

from paperextract.document import (
    Block,
    Document,
    Equation,
    Figure,
    Heading,
    InlineRun,
    ListBlock,
    MetadataObservation,
    PageFurniture,
    PageRecord,
    Paragraph,
    SourceSpan,
    Table,
)
from paperextract.identity import (
    Candidate,
    Identity,
    Observed,
    arxiv_base,
    arxiv_identifier,
    collect_candidates,
    compare_record,
    math_as_text,
    observe,
    resolve_identity,
    title_key,
)
from paperextract.registry import Author, RegistryFailure, RegistryRecord

DOI = "10.1000/example.1"
OTHER = "10.1000/cited.2"


def span(page: int, kind: str = "text") -> SourceSpan:
    return SourceSpan(page, None, None, kind, 0)


def runs(text: str) -> tuple[InlineRun, ...]:
    return (InlineRun("text", text),)


def make_document(
    *,
    title: str | None = "High-energy pulse self-compression and ultraviolet generation",
    authors: tuple[str, ...] = ("John C. Travers",),
    identifier: str | None = f"doi:{DOI}",
    header_doi: str | None = None,
    body_doi: str | None = None,
    heading: str | None = None,
) -> Document:
    metadata: list[MetadataObservation] = []
    if title:
        metadata.append(MetadataObservation("title", title, "pdf"))
    metadata.extend(MetadataObservation("author", a, "pdf") for a in authors)
    if identifier:
        metadata.append(MetadataObservation("identifier", identifier, "pdf"))
    metadata.append(
        MetadataObservation("created_at", "2019-04-03T17:08:46+05:30", "pdf")
    )
    blocks: list[Block] = []
    if header_doi:
        blocks.append(
            PageFurniture(
                "aux_1",
                "header",
                runs(f"https://doi.org/{header_doi}"),
                span(1, "header"),
            )
        )
    if heading:
        blocks.append(Heading("hd_1", 1, runs(heading), span(1, "doc_title")))
    if body_doi:
        blocks.append(
            Paragraph("par_1", "body", runs(f"see {body_doi} for details"), span(1))
        )
    blocks.append(
        Paragraph("par_ref", "reference", runs(f"[1] Someone. doi:{OTHER}"), span(9))
    )
    blocks.append(
        Paragraph("par_2", "body", runs(f"later page mentions {OTHER}"), span(2))
    )
    return Document(
        source_sha256="f" * 64,
        backend="mineru",
        backend_version="4.0.5",
        native_schema="s",
        native_schema_version="1",
        pages=(),
        blocks=tuple(blocks),
        metadata=tuple(metadata),
        findings=(),
    )


def make_record(
    doi: str = DOI,
    title: str | None = "High-energy pulse self-compression and ultraviolet generation",
    authors: tuple[tuple[str | None, str | None, str | None], ...] = (
        ("John C.", "Travers", None),
        ("Christian", "Brahms", None),
    ),
    year: int | None = 2019,
    **overrides: object,
) -> RegistryRecord:
    values: dict[str, object] = {
        "provider": "crossref",
        "doi": doi,
        "type": "journal-article",
        "title": title,
        "title_raw": title,
        "subtitle": None,
        "authors": tuple(Author(g, f, lit, None, None) for g, f, lit in authors),
        "container_title": "Nature Photonics",
        "publisher": "Springer",
        "volume": "13",
        "issue": "8",
        "pages": "547-554",
        "article_number": None,
        "issued": (year,) if year is not None else (),
        "published_print": (2019, 8),
        "published_online": (),
        "url": f"https://doi.org/{doi}",
        "issn": ("1749-4885",),
        "license_urls": (),
        "abstract": None,
        "source_url": f"https://api.crossref.org/works/{doi}",
        "retrieved_utc": "2026-09-22T00:00:00+00:00",
        "body_sha256": "0" * 64,
        "cached": False,
    }
    values.update(overrides)
    return RegistryRecord(**values)  # type: ignore[arg-type]


def test_candidates_are_collected_by_strength_and_exclude_references() -> None:
    document = make_document(header_doi=DOI, body_doi="10.1000/weak.3")
    candidates = collect_candidates(
        document, {"doi_candidates": [DOI.upper(), "10.1000/weak.3", "10.1000/from.fp"]}
    )
    assert [(c.value, c.sources) for c in candidates] == [
        (DOI, ("pdf_information", "page_1_furniture", "fingerprint")),
        ("10.1000/weak.3", ("fingerprint", "page_1_text")),
        ("10.1000/from.fp", ("fingerprint",)),
    ]
    assert candidates[0].strong() and not candidates[1].strong()
    assert all(c.value != OTHER for c in candidates)
    assert Candidate.from_dict(candidates[0].to_dict()) == candidates[0]
    assert collect_candidates(make_document(identifier=None), {}) == ()


def test_observations_come_from_metadata_and_headings() -> None:
    observed = observe(make_document(title=None, heading="Heading Title"))
    assert observed == Observed("Heading Title", ("John C. Travers",), (2019,))
    assert Observed.from_dict(observed.to_dict()) == observed


@pytest.mark.parametrize(
    ("title", "key"),
    [
        ("The Refractive Index of Helium", "refractive index of helium"),
        ("Dispersion  of\tArgon*", "dispersion of argon"),
        ("Étude des gaz: H₂ & D₂", "etude des gaz h2 d2"),
        ("A", "a"),
    ],
)
def test_title_keys_are_conservative(title: str, key: str) -> None:
    assert title_key(title) == key


def test_comparison_reports_title_authors_and_year() -> None:
    observed = observe(make_document())
    checks = {c.name: c for c in compare_record(make_record(), observed)}
    assert [checks[n].outcome for n in ("title", "authors", "year")] == [
        "pass",
        "pass",
        "pass",
    ]
    warn_title = make_record(
        title="High-energy pulse self-compression and ultraviolet emission"
    )
    checks = {c.name: c for c in compare_record(warn_title, observed)}
    assert checks["title"].outcome == "warn"
    fail_title = make_record(title="Something else entirely")
    assert {c.name: c.outcome for c in compare_record(fail_title, observed)}[
        "title"
    ] == "fail"
    checks = {
        c.name: c
        for c in compare_record(
            make_record(authors=(("A", "Nobody", None),), year=2015), observed
        )
    }
    assert (checks["authors"].outcome, checks["year"].outcome) == ("fail", "warn")
    checks = {
        c.name: c
        for c in compare_record(make_record(authors=()), Observed(None, (), ()))
    }
    assert [checks[n].outcome for n in ("title", "authors", "year")] == [
        "not_checked",
        "not_checked",
        "not_checked",
    ]
    partial = Observed("x", ("John C. Travers", "Nobody Here"), ())
    assert {c.name: c.outcome for c in compare_record(make_record(), partial)}[
        "authors"
    ] == "warn"


def lookup_with(**records: RegistryRecord | RegistryFailure):  # noqa: ANN201
    def lookup(doi: str) -> RegistryRecord | RegistryFailure:
        return records.get(
            doi.replace("/", "_").replace(".", "_"),
            RegistryFailure(doi, "not_found", "no record"),
        )

    return lookup


def key(doi: str) -> str:
    return doi.replace("/", "_").replace(".", "_")


def test_matching_record_validates_identity_and_fields() -> None:
    document = make_document()
    identity = resolve_identity(
        document, {"doi_candidates": [DOI]}, lookup_with(**{key(DOI): make_record()})
    )
    assert identity.status == "VALIDATED"
    assert identity.doi == DOI and identity.provider == "crossref"
    assert identity.work_id is not None and identity.work_id.startswith("work_")
    assert (
        identity.field("title")
        == "High-energy pulse self-compression and ultraviolet generation"
    )
    assert identity.field("year") == 2019
    assert identity.field("journal") == "Nature Photonics"
    assert identity.field("article_number") is None
    statuses = {f.name: f.status for f in identity.fields}
    assert (
        statuses["title"] == "VALIDATED" and statuses["article_number"] == "UNVERIFIED"
    )
    assert identity.validated()
    restored = Identity.from_json(identity.to_json())
    assert restored == identity
    assert json.loads(identity.to_json())["schema"] == "paperextract.identity"


def test_author_disagreement_only_warns() -> None:
    record = make_record(authors=(("A", "Nobody", None),))
    identity = resolve_identity(make_document(), {}, lookup_with(**{key(DOI): record}))
    assert identity.status == "VALIDATED_WITH_WARNINGS"
    assert "authors fail" in identity.reasons[0]


def test_strong_candidate_with_different_title_is_a_conflict() -> None:
    record = make_record(title="A completely different paper")
    identity = resolve_identity(make_document(), {}, lookup_with(**{key(DOI): record}))
    assert identity.status == "CONFLICT"
    assert identity.doi is None and identity.work_id is None
    assert identity.alternatives[0]["outcome"] == "rejected"
    assert not identity.validated()


def test_weak_candidate_with_different_title_stays_unverified() -> None:
    document = make_document(identifier=None, body_doi="10.1000/weak.3")
    record = make_record(doi="10.1000/weak.3", title="A cited paper")
    identity = resolve_identity(
        document, {}, lookup_with(**{key("10.1000/weak.3"): record})
    )
    assert identity.status == "UNVERIFIED"
    assert "no candidate resolved" in identity.reasons[0]
    assert identity.alternatives[0]["doi"] == "10.1000/weak.3"


def test_missing_observed_title_needs_a_strong_source() -> None:
    strong = make_document(title=None)
    identity = resolve_identity(strong, {}, lookup_with(**{key(DOI): make_record()}))
    assert identity.status == "VALIDATED_WITH_WARNINGS"
    assert "title not_checked" in identity.reasons[0]
    weak = make_document(title=None, identifier=None, body_doi=DOI)
    identity = resolve_identity(weak, {}, lookup_with(**{key(DOI): make_record()}))
    assert identity.status == "UNVERIFIED"
    assert "no observed title" in str(identity.alternatives[0]["detail"])


def test_registry_failures_are_recorded() -> None:
    document = make_document()
    identity = resolve_identity(
        document,
        {},
        lookup_with(**{key(DOI): RegistryFailure(DOI, "unavailable", "down")}),
    )
    assert identity.status == "UNVERIFIED"
    assert identity.reasons == ("registry unavailable: down",)
    identity = resolve_identity(document, {}, lookup_with())
    assert identity.alternatives[0]["outcome"] == "not_found"
    identity = resolve_identity(make_document(identifier=None), {}, lookup_with())
    assert identity.reasons == ("no DOI candidate in the PDF",)
    assert identity.candidates == ()


def test_identity_documents_are_validated_on_read() -> None:
    identity = Identity.unverified("nothing")
    payload = identity.to_dict()
    payload["schema_version"] = 99
    with pytest.raises(ValueError, match="Unsupported identity schema"):
        Identity.from_dict(payload)
    payload = identity.to_dict()
    payload["status"] = "MAYBE"
    with pytest.raises(ValueError):
        Identity.from_dict(payload)
    assert Identity.from_json(identity.to_json()) == identity
    assert identity.field("title") is None


def test_year_hints_ignore_unparseable_dates_and_repeated_candidates() -> None:
    document = Document(
        source_sha256="f" * 64,
        backend="mineru",
        backend_version="4.0.5",
        native_schema="s",
        native_schema_version="1",
        pages=(),
        blocks=(
            Paragraph(
                "par_1",
                "body",
                runs(f"see {DOI} and again {DOI} and 10.1234/."),
                span(1),
            ),
        ),
        metadata=(
            MetadataObservation("created_at", "D:20190403", "pdf"),
            MetadataObservation("modified_at", "2019-04-03", "pdf"),
            MetadataObservation("created_at", "2019-05-01", "pdf"),
        ),
        findings=(),
    )
    assert observe(document).years == (2019,)
    candidates = collect_candidates(document, {})
    assert [(c.value, c.sources) for c in candidates] == [(DOI, ("page_1_text",))]


def searched_document(
    *, first_page: str, title: str = "Laser ionization of noble gases"
) -> Document:
    return Document(
        source_sha256="f" * 64,
        backend="mineru",
        backend_version="4.0.5",
        native_schema="s",
        native_schema_version="1",
        pages=(
            PageRecord(1, None, None, "missing"),
            PageRecord(2, None, None, "processed"),
        ),
        blocks=(
            PageFurniture(
                "hdr", "header", runs("J. Opt. Soc. Am. B 8 (1991)"), span(2)
            ),
            Heading("hd", 1, runs(title), span(2, "doc_title")),
            Paragraph("par", "body", runs(first_page), span(2)),
            ListBlock("lst", (runs("Item"),), span(2)),
            Figure("fig", "1", runs("Fig. 1. Setup"), None, (), (), None, "single", 2),
            Table("tbl", None, None, (), "", None, None, None, None, span(2)),
            Equation("eq", "x = 2003", None, None, span(2)),
            Paragraph("later", "body", runs("Page three mentions 2003"), span(3)),
        ),
        metadata=(),
        findings=(),
    )


def search_with(*records: RegistryRecord | RegistryFailure):  # noqa: ANN201
    def search(query: str) -> tuple[RegistryRecord, ...] | RegistryFailure:
        assert query == "Laser ionization of noble gases"
        if records and isinstance(records[0], RegistryFailure):
            return records[0]
        return tuple(r for r in records if isinstance(r, RegistryRecord))

    return search


def no_lookup(doi: str) -> RegistryRecord | RegistryFailure:
    return RegistryFailure(doi, "not_found", "none")


AUGST = make_record(
    "10.1364/josab.8.000858",
    "Laser ionization of noble gases",
    (("S.", "Augst", None),),
    1991,
    published_print=(),
)


def test_a_corroborated_search_hit_identifies_a_paper_without_a_doi() -> None:
    document = searched_document(first_page="S. Augst, D. D. Meyerhofer and others")
    identity = resolve_identity(document, {}, no_lookup, search_with(AUGST))
    assert identity.status == "VALIDATED_WITH_WARNINGS"
    assert identity.doi == "10.1364/josab.8.000858"
    assert identity.candidates[-1] == Candidate(
        identity.doi, "doi", ("bibliographic_search",)
    )
    assert [(c.name, c.outcome) for c in identity.checks] == [
        ("title", "pass"),
        ("authors", "pass"),
        ("year", "pass"),
        ("identifier", "warn"),
    ]
    assert "bibliographic search found one work" in identity.reasons[0]


@pytest.mark.parametrize(
    ("first_page", "record", "reason"),
    [
        (
            "S. Augst and others",
            make_record(
                "10.1/a",
                "Laser ionization of rare gases",
                (("S.", "Augst", None),),
                1991,
            ),
            "registry 'Laser ionization of rare gases'",
        ),
        ("D. Meyerhofer and others", AUGST, "'augst' does not appear"),
        (
            "S. Augst and others",
            make_record(
                "10.1/b",
                "Laser ionization of noble gases",
                (("S.", "Augst", None),),
                1992,
                published_print=(),
            ),
            "Registry years [1992]",
        ),
        (
            "S. Augst and others",
            make_record(
                "10.1/c",
                "Laser ionization of noble gases",
                (),
                1991,
                published_print=(),
            ),
            "'none' does not appear",
        ),
    ],
)
def test_search_hits_need_title_author_and_year_on_the_first_page(
    first_page: str, record: RegistryRecord, reason: str
) -> None:
    identity = resolve_identity(
        searched_document(first_page=first_page), {}, no_lookup, search_with(record)
    )
    assert identity.status == "UNVERIFIED"
    assert "no bibliographic search result agrees" in identity.reasons[0]
    assert identity.alternatives[-1]["outcome"] == "search_rejected"
    assert reason in str(identity.alternatives[-1]["detail"])


def test_subtitles_print_years_and_ambiguity_are_handled() -> None:
    subtitled = make_record(
        "10.1/s",
        "Laser ionization",
        (("S.", "Augst", None),),
        None,
        subtitle="of noble gases",
        published_print=(1991, 5),
    )
    document = searched_document(first_page="Augst")
    accepted = resolve_identity(document, {}, no_lookup, search_with(subtitled))
    assert accepted.doi == "10.1/s"
    twin = make_record(
        "10.1/twin", "Laser ionization of noble gases", (("S.", "Augst", None),), 1991
    )
    ambiguous = resolve_identity(document, {}, no_lookup, search_with(AUGST, twin))
    assert ambiguous.status == "UNVERIFIED"
    assert "several works" in ambiguous.reasons[0]
    assert [a["outcome"] for a in ambiguous.alternatives] == ["search_ambiguous"] * 2
    same = resolve_identity(document, {}, no_lookup, search_with(AUGST, AUGST))
    assert same.doi == AUGST.doi


def test_search_is_skipped_or_reported_when_it_cannot_help() -> None:
    document = searched_document(first_page="S. Augst")
    failed = resolve_identity(
        document,
        {},
        no_lookup,
        search_with(RegistryFailure("q", "unavailable", "down")),
    )
    assert "bibliographic search unavailable: down" in failed.reasons[0]
    untitled = make_document(title=None, identifier=None)

    def never(query: str) -> tuple[RegistryRecord, ...]:
        raise AssertionError(query)

    assert resolve_identity(untitled, {}, no_lookup, never).reasons == (
        "no DOI candidate in the PDF",
    )
    conflicted = make_document(header_doi=DOI)
    lookup = lookup_with(**{key(DOI): make_record(title="Something else entirely")})
    assert resolve_identity(conflicted, {}, lookup, never).status == "CONFLICT"


def test_real_title_quirks_do_not_block_identification() -> None:
    # Regression from the materials batch: an Elsevier PII in the PDF title
    # field, a heading with inline math and a registry title without spaces
    # around stripped MathML.
    document = Document(
        source_sha256="f" * 64,
        backend="mineru",
        backend_version="4.0.5",
        native_schema="s",
        native_schema_version="1",
        pages=(),
        blocks=(
            Heading(
                "hd",
                1,
                (
                    InlineRun("text", "Susceptibility of "),
                    InlineRun("math", "\\mathrm{H}_{2}"),
                    InlineRun("text", " in gases"),
                ),
                span(1, "doc_title"),
            ),
        ),
        metadata=(MetadataObservation("title", "PII: 0022-4073(81)90057-1", "pdf"),),
        findings=(),
    )
    assert observe(document).title == "Susceptibility of H2 in gases"
    (candidate,) = collect_candidates(document, {})
    assert candidate == Candidate(
        "10.1016/0022-4073(81)90057-1", "doi", ("pdf_information_pii",)
    )
    record = make_record(candidate.value, "Susceptibility ofH2in gases", year=None)
    identity = resolve_identity(
        document, {}, lookup_with(**{key(candidate.value): record})
    )
    assert identity.status == "VALIDATED"
    assert identity.checks[0].outcome == "pass"
    assert math_as_text("\\mathbf{D}_2 ^{+}") == "D2+"


def test_arxiv_identifiers_are_resolved_through_their_datacite_doi() -> None:
    document = make_document(identifier=None, title=None)
    fingerprint = {"arxiv_candidates": ["1111.2222", "2206.01062v2"]}
    candidates = collect_candidates(document, fingerprint)
    assert (
        Candidate("10.48550/arxiv.2206.01062", "doi", ("arxiv_identifier",))
        in candidates
    )
    record = make_record(doi="10.48550/arxiv.2206.01062")
    identity = resolve_identity(
        make_document(identifier=None, heading=record.title),
        fingerprint,
        lambda doi: (
            record if doi == record.doi else RegistryFailure(doi, "not_found", "")
        ),
    )
    assert identity.validated()
    assert arxiv_identifier(identity, fingerprint) == "2206.01062v2"
    assert arxiv_identifier(identity, {}) == "2206.01062"
    assert arxiv_identifier(Identity.unverified("none"), fingerprint) is None
    assert arxiv_base("arXiv:hep-th/9901001v3") == "hep-th/9901001"


def test_footnote_markers_do_not_join_the_observed_title() -> None:
    heading = Heading(
        "hd_1",
        1,
        (
            InlineRun("text", "Soliton dynamics in fibres"),
            InlineRun("text", "a", ("superscript",)),
            InlineRun("text", " and more", ("superscript",)),
        ),
        span(1, "doc_title"),
    )
    document = replace(make_document(title=None), blocks=(heading,))
    assert observe(document).title == "Soliton dynamics in fibres and more"
