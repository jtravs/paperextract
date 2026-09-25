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
    Check,
    Identity,
    Observed,
    arxiv_base,
    arxiv_identifier,
    clean_title,
    collect_candidates,
    compare_record,
    filename_candidates,
    identity_from_bibtex,
    math_as_text,
    observe,
    resolve_identity,
    title_agreement,
    title_candidates,
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
        # The plain title first, then the title with the page's hints.
        assert query.startswith("Laser ionization of noble gases")
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


# --- Evidence beyond a printed DOI --------------------------------------------


@pytest.mark.parametrize(
    ("observed", "registry", "grade"),
    [
        ("Dispersion of Nitrogen*", "Dispersion of Nitrogen", "same"),
        ("Index of N<sub>2</sub> and H_{2}", "Index of N2 and H2", "same"),
        (
            "Rayleigh scattering of Lyman light at 1216",
            "Rayleigh scattering of Lyman α light at 1216",  # noqa: RUF001
            "near",
        ),
        (
            "Refractivities of H2, He and Kr for 168[?]l[?]288 nm",
            "Refractivities of H2 , He and Kr for 168≤λ≤288 nm",
            "near",
        ),
        (
            "Raman gain coefficients of hydrogen - Quantum Electronics, IEEE",
            "Raman gain coefficients of hydrogen",
            "near",
        ),
        (
            "Rotational wavepacket revivals in H2, D2 and N20",
            "Rotational wavepacket revivals in H_2, D_2 and N_2O",
            "near",
        ),
        (
            "Transient optical nonlinearity in N2, O2, N2O1, and Ar",
            "Transient optical nonlinearity in N 2 , O 2 , N 2 O, and Ar",
            "near",
        ),
        (
            "Rayleigh scattering of Lyman α light",  # noqa: RUF001
            "Rayleigh scattering of Lyman β light",
            "different",
        ),
        (
            "Polarizability of molecular H2 revisited",
            "Polarizability of molecular D2 revisited",
            "different",
        ),
        ("Laser ionization of noble", "Laser ionization of noble gases", "different"),
        ("Short N20", "Short N2O", "different"),
        (
            "Rotational wavepacket revivals in H2 and N20 gas",
            "Rotational wavepacket revivals in H2 and N2O gases",
            "different",
        ),
    ],
)
def test_titles_are_graded_allowing_only_markup_and_reading_errors(
    observed: str, registry: str, grade: str
) -> None:
    assert title_agreement(observed, registry) == grade


def test_clean_title_removes_markup_but_not_words() -> None:
    assert clean_title("A &amp; B of CO<sub>2</sub>†") == "A & B of CO2"
    assert clean_title("Plain title") == "Plain title"
    assert clean_title(r"\mathbf{N}_2 in � gas") == "N2 in gas"


@pytest.mark.parametrize(
    ("name", "dois"),
    [
        ("PhysRevA.13.1422-2.pdf", ("10.1103/physreva.13.1422",)),
        ("PhysRevLett.114.173004.pdf", ("10.1103/physrevlett.114.173004",)),
        ("josa-61-1-89.pdf", ("10.1364/josa.61.000089",)),
        ("optica-6-4-495.pdf", ("10.1364/optica.6.000495",)),
        ("paper-1-2-3.pdf", ()),
        ("s41598-018-34641-y.pdf", ("10.1038/s41598-018-34641-y",)),
        ("rspa.1920.0020.pdf", ("10.1098/rspa.1920.0020",)),
        ("jp980221f.pdf", ("10.1021/jp980221f",)),
        ("jp2094438-2.pdf", ("10.1021/jp2094438",)),
        (
            "1-s2.0-S0092640X83710132-main.pdf",
            ("10.1016/0092-640x(83)71013-2",),
        ),
        ("1-s2.0-S0009261408009834-main.pdf", ()),
        ("2206.01062v2.pdf", ("10.48550/arxiv.2206.01062",)),
        ("D_V_Willetts_1982_J._Phys._D%3A_Appl._Phys._15_51.pdf", ()),
    ],
)
def test_publisher_file_names_give_weak_doi_candidates(
    name: str, dois: tuple[str, ...]
) -> None:
    assert filename_candidates(name) == dois


def hinted_document(
    *,
    info_title: str | None = None,
    headings: tuple[tuple[int, int, str], ...] = (),
    header: str = "",
    authors: str = "S. Augst and D. Meyerhofer",
    body: str = "Rochester, 1991",
) -> Document:
    blocks: list[Block] = []
    if header:
        blocks.append(PageFurniture("hdr", "header", runs(header), span(1)))
    for number, (page, level, text) in enumerate(headings):
        blocks.append(Heading(f"hd{number}", level, runs(text), span(page)))
        if number == 0:
            blocks.append(Paragraph("auth", "body", runs(authors), span(page)))
    blocks.append(Paragraph("body", "body", runs(body), span(1)))
    return Document(
        source_sha256="f" * 64,
        backend="mineru",
        backend_version="4.0.5",
        native_schema="s",
        native_schema_version="1",
        pages=tuple(PageRecord(n, None, None, "processed") for n in (1, 2, 3)),
        blocks=tuple(blocks),
        metadata=()
        if info_title is None
        else (MetadataObservation("title", info_title, "pdf"),),
        findings=(),
    )


def test_title_candidates_skip_leftovers_and_later_pages() -> None:
    document = hinted_document(
        info_title="acs_JX_jp-2011-094438 1..7",
        headings=(
            (1, 1, "The Journal of Physical Chemistry A"),
            (1, 2, "Section heading"),
            (2, 1, "Molecular and atomic polarizabilities"),
            (2, 1, "Molecular and atomic polarizabilities"),
            (3, 1, "A heading on page three"),
            (1, 1, "Using JCP format"),
        ),
    )
    assert title_candidates(document) == (
        "The Journal of Physical Chemistry A",
        "Molecular and atomic polarizabilities",
    )
    many = hinted_document(
        headings=tuple((1, 1, f"Heading number {n}") for n in "abcde")
    )
    assert len(title_candidates(many)) == 4
    assert title_candidates(hinted_document(info_title="Real title")) == ("Real title",)
    second = hinted_document(
        headings=((1, 2, "Polarization of laser light"), (1, 3, "Deeper heading"))
    )
    assert title_candidates(second) == ("Polarization of laser light",)
    third = hinted_document(headings=((1, 3, "Only a third-level heading"),))
    assert title_candidates(third) == ()
    sections = hinted_document(
        headings=((1, 2, "1. Introduction"), (2, 2, "Results and Discussion"))
    )
    assert title_candidates(sections) == ()


def test_a_neighbouring_letter_on_the_page_is_not_taken_for_this_one() -> None:
    document = hinted_document(
        headings=(
            (1, 2, "The Spin of Hydrogen Isotope"),
            (1, 2, "The Production of X-Rays by Fast Mercury Ions"),
        ),
    )
    document = replace(
        document, metadata=(MetadataObservation("author", "G. N. Lewis", "pdf"),)
    )
    other = make_record(
        doi="10.1103/physrev.43.837",
        title="The Production of X-Rays by Fast Mercury Ions",
        authors=(("J. M.", "Cork", None),),
        year=1933,
    )
    this = make_record(
        doi="10.1103/physrev.43.837.2",
        title="The Spin of Hydrogen Isotope",
        authors=(("G. N.", "Lewis", None),),
        year=1933,
    )
    lookup = lookup_with(**{key(other.doi): other, key(this.doi): this})
    fingerprint = {"doi_candidates": [other.doi, this.doi]}
    identity = resolve_identity(document, fingerprint, lookup)
    assert identity.doi == this.doi
    assert "matches only a secondary heading" in str(identity.alternatives[0])


def test_a_later_heading_confirms_a_doi_despite_a_running_header_title() -> None:
    document = hinted_document(
        info_title="PHYSICAL REVIEW A, 66, 033402 (2002)",
        headings=((1, 1, "Theory of molecular tunneling ionization"),),
        header=f"doi:{DOI}",
    )
    record = make_record(title="Theory of molecular tunneling ionization")
    identity = resolve_identity(document, {}, lookup_with(**{key(DOI): record}))
    assert identity.status == "VALIDATED"
    assert identity.checks[0] == Check(
        "title", "pass", "Titles agree: 'Theory of molecular tunneling ionization'"
    )
    near = compare_record(
        make_record(title="Theory of molecular tunneling ionization in N₂ gas"),
        Observed("Theory of molecular tunneling ionization in N gas", (), ()),
    )
    assert near[0].outcome == "warn"
    assert "apart from markup" in near[0].detail


def test_a_doi_in_the_file_name_is_weak_and_warned() -> None:
    document = hinted_document(
        headings=((1, 1, "Dispersion of Carbon Dioxide"),), body="Old, 1971"
    )
    found = collect_candidates(document, {}, ("josa-61-1-89.pdf",))
    assert found == (Candidate("10.1364/josa.61.000089", "doi", ("file_name",)),)
    record = make_record(
        doi="10.1364/josa.61.000089",
        title="Dispersion of Carbon Dioxide*",
        authors=(("J. G.", "Old", None),),
        year=1971,
    )
    identity = resolve_identity(
        document,
        {},
        lookup_with(**{key(record.doi): record}),
        file_names=("josa-61-1-89.pdf",),
    )
    assert identity.status == "VALIDATED_WITH_WARNINGS"
    assert identity.checks[-1].name == "identifier"
    assert "naming of the file" in identity.checks[-1].detail
    wrong = resolve_identity(
        document,
        {},
        lookup_with(**{key(record.doi): make_record(doi=record.doi)}),
        file_names=("josa-61-1-89.pdf",),
    )
    assert wrong.status == "UNVERIFIED"
    assert wrong.alternatives[0]["outcome"] == "rejected"


def queries_to(  # noqa: ANN201
    answers: dict[str, tuple[RegistryRecord, ...] | RegistryFailure],
    asked: list[str],
):
    def search(query: str) -> tuple[RegistryRecord, ...] | RegistryFailure:
        asked.append(query)
        for start, answer in answers.items():
            if query.startswith(start) and (start != "" or len(asked) > 1):
                return answer
        return ()

    return search


def test_an_enriched_query_finds_a_generic_title() -> None:
    document = hinted_document(
        headings=((1, 1, "The Refractive Index of Air"),),
        authors="Bengt Edlen",
        header="Metrologia 2 71 (1966)",
        body="Received 1966",
    )
    edlen = make_record(
        doi="10.1088/0026-1394/2/2/002",
        title="The Refractive Index of Air",
        authors=(("Bengt", "Edlen", None),),
        year=1966,
    )
    asked: list[str] = []
    search = queries_to({"The Refractive Index of Air Bengt": (edlen,)}, asked)
    identity = resolve_identity(
        document, {}, no_lookup, search, file_names=("Edlen_1966_Metrologia.pdf",)
    )
    assert identity.doi == edlen.doi
    assert asked == [
        "The Refractive Index of Air",
        "The Refractive Index of Air Bengt Edlen Metrologia 2 71 (1966) "
        "Edlen 1966 Metrologia",
    ]


def test_a_long_first_paragraph_is_not_used_as_an_author_line() -> None:
    document = hinted_document(
        headings=((1, 1, "The Refractive Index of Air"),), authors="word " * 60
    )
    asked: list[str] = []
    resolve_identity(document, {}, no_lookup, queries_to({}, asked))
    assert asked == ["The Refractive Index of Air"]
    alone = replace(
        document,
        blocks=tuple(b for b in document.blocks if not isinstance(b, Paragraph)),
    )
    asked.clear()
    resolve_identity(alone, {}, no_lookup, queries_to({}, asked))
    assert asked == ["The Refractive Index of Air"]


def test_components_originals_and_uncited_hits_are_set_aside() -> None:
    document = hinted_document(
        headings=((1, 1, "Three-body electron attachment to a molecule"),),
        authors="N. L. Aleksandrov",
        body="Sov. Phys. Usp. 31 101 (1988)",
    )

    def hit(doi: str, **changes: object) -> RegistryRecord:
        return make_record(
            doi=doi,
            title="Three-body electron attachment to a molecule",
            authors=(("N. L.", "Aleksandrov", None),),
            year=1988,
            **changes,
        )

    translation = hit(
        "10.1070/pu1988", relations=(("is-translation-of", "10.3367/ufn1988"),)
    )
    original = hit(
        "10.3367/ufn1988", relations=(("has-translation", "10.1070/pu1988"),)
    )
    component = hit("10.1/x.s001", type="component")
    identity = resolve_identity(
        document, {}, no_lookup, search_all(component, original, translation)
    )
    assert identity.doi == "10.1070/pu1988"
    outcomes = {a["doi"]: a["outcome"] for a in identity.alternatives}
    assert outcomes == {
        "10.1/x.s001": "search_component",
        "10.3367/ufn1988": "translation_original",
    }
    cited = hit("10.1/cited", volume="31", pages="101-118")
    chapter = hit("10.1/chapter", volume="5", pages="7-9")
    chosen = resolve_identity(document, {}, no_lookup, search_all(chapter, cited))
    assert chosen.doi == "10.1/cited"
    assert {a["outcome"] for a in chosen.alternatives} == {"search_not_cited"}
    uncited = hit("10.1/other", volume="9", pages=None, article_number=None)
    still = resolve_identity(document, {}, no_lookup, search_all(chapter, uncited))
    assert still.status == "UNVERIFIED"
    assert [a["outcome"] for a in still.alternatives] == ["search_ambiguous"] * 2


def search_all(*records: RegistryRecord):  # noqa: ANN201
    def search(_query: str) -> tuple[RegistryRecord, ...]:
        return records

    return search


def test_search_failures_are_reported_only_when_nothing_answered() -> None:
    document = hinted_document(headings=((1, 1, "Laser ionization of noble gases"),))
    asked: list[str] = []
    failing = RegistryFailure("q", "unavailable", "down")
    later = resolve_identity(document, {}, no_lookup, queries_to({"": (AUGST,)}, asked))
    assert later.doi == AUGST.doi
    assert len(asked) == 2
    down = resolve_identity(document, {}, no_lookup, queries_to({"Laser": failing}, []))
    assert "bibliographic search unavailable: down" in down.reasons[0]
    mixed = resolve_identity(
        document,
        {},
        no_lookup,
        queries_to({"Laser ionization of noble gases S.": failing}, []),
    )
    assert "no bibliographic search result agrees" in mixed.reasons[0]


@pytest.mark.parametrize(
    ("title", "names"),
    [
        ("Supplementary Materials for", ()),
        ("Laser ionization of noble gases", ("abb5375_sm.pdf",)),
    ],
)
def test_a_supplement_is_not_identified_as_its_article(
    title: str, names: tuple[str, ...]
) -> None:
    document = hinted_document(
        headings=((1, 1, title), (1, 1, "Laser ionization of noble gases"))
    )
    identity = resolve_identity(
        document, {}, no_lookup, search_all(AUGST), file_names=names
    )
    assert identity.status == "UNVERIFIED"
    assert identity.alternatives[-1] == {
        "doi": AUGST.doi,
        "outcome": "supplement_of",
        "detail": "the article 'Laser ionization of noble gases'",
    }
    assert "supplementary material of 10.1364/josab.8.000858" in identity.reasons[0]


def test_an_asserted_doi_is_accepted_with_warnings_or_reported() -> None:
    document = hinted_document(headings=((1, 1, "Another title"),))
    identity = resolve_identity(
        document,
        {},
        lookup_with(**{key(DOI): make_record()}),
        asserted_doi=f"https://doi.org/{DOI}",
    )
    assert identity.status == "VALIDATED_WITH_WARNINGS"
    assert identity.candidates[0] == Candidate(DOI, "doi", ("user_assertion",))
    assert identity.checks[0].outcome == "fail"
    assert identity.checks[-1] == Check(
        "identifier", "warn", "DOI asserted by the user"
    )
    missing = resolve_identity(document, {}, no_lookup, asserted_doi=DOI)
    assert missing.status == "UNVERIFIED"
    assert missing.reasons == (f"asserted DOI {DOI}: none",)


REPORT = """@techreport{phelps1977,
  author = {Phelps, A. V. and Arthur Pitchford and Solo},
  title = {Anisotropic scattering of electrons by {N2}},
  institution = {JILA},
  number = {26},
  year = 1985,
  url = "https://example.org/report26"
}"""


def test_a_bibtex_entry_asserts_an_identity_no_registry_holds() -> None:
    document = hinted_document(
        headings=((1, 1, "Anisotropic scattering of electrons by N2"),),
        body="JILA Information Center Report 26, 1985",
    )
    identity = identity_from_bibtex(REPORT, document)
    assert identity.status == "ASSERTED"
    assert identity.named() and not identity.validated()
    assert identity.provider == "user"
    assert identity.field("journal") == "JILA"
    assert identity.field("publisher") == "JILA"
    assert identity.field("issue") == "26"
    assert identity.field("year") == 1985
    assert identity.field("article_type") == "bibtex:techreport"
    assert identity.field("authors") == [
        {
            "given": "A. V.",
            "family": "Phelps",
            "literal": None,
            "orcid": None,
            "sequence": "first",
        },
        {
            "given": "Arthur",
            "family": "Pitchford",
            "literal": None,
            "orcid": None,
            "sequence": "additional",
        },
        {
            "given": None,
            "family": "Solo",
            "literal": None,
            "orcid": None,
            "sequence": "additional",
        },
    ]
    assert [(c.name, c.outcome) for c in identity.checks] == [
        ("title", "pass"),
        ("year", "pass"),
        ("identifier", "warn"),
    ]
    statuses = {f.name: (f.status, f.source) for f in identity.fields}
    assert statuses["title"] == ("ASSERTED", "user")
    assert statuses["doi"] == ("UNVERIFIED", None)
    assert Identity.from_json(identity.to_json()) == identity
    bare = identity_from_bibtex(REPORT, hinted_document(body="undated"))
    assert [(c.name, c.outcome) for c in bare.checks][:2] == [
        ("title", "not_checked"),
        ("year", "warn"),
    ]


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (REPORT + REPORT, "exactly one BibTeX entry"),
        ("@misc{k, title = {T}}", "lacks author, year"),
        ("@misc{k, author = {A}, title = {T}, year = 1, doi = {10.1/x}}", "--doi"),
        ("@misc{k, author = {A}, title = {T}, year = {n.d.}}", "not a number"),
    ],
)
def test_unusable_bibtex_entries_are_refused(entry: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        identity_from_bibtex(entry, hinted_document())
