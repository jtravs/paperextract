"""Generate and check BibTeX from validated identities."""

from dataclasses import replace

from paperextract.bibtex import (
    bibtex_entry,
    citation_key,
    parse_bibtex,
    validate_bibtex,
)
from paperextract.document import Document
from paperextract.identity import Field, Identity, Observed, identity_from_bibtex


def make_identity(status: str = "VALIDATED", **overrides: object) -> Identity:
    values: dict[str, object] = {
        "title": "High-energy pulse self-compression in HCF & VUV generation",
        "authors": [
            {
                "given": "John C.",
                "family": "Travers",
                "literal": None,
                "orcid": None,
                "sequence": "first",
            },
            {
                "given": None,
                "family": None,
                "literal": "The Collaboration",
                "orcid": None,
                "sequence": None,
            },
            {
                "given": "Émilie",
                "family": "Dupont-Müller",
                "literal": None,
                "orcid": None,
                "sequence": None,
            },
        ],
        "journal": "Nature Photonics",
        "publisher": "Springer",
        "doi": "10.1000/example.1",
        "volume": "13",
        "issue": "8",
        "pages": "547-554",
        "article_number": None,
        "year": 2019,
        "published_online": [2019, 4, 15],
        "published_print": None,
        "url": "https://doi.org/10.1000/example.1",
        "issn": ["1749-4885"],
        "license": None,
        "article_type": "journal-article",
    }
    values.update(overrides)
    return Identity(
        status=status,  # type: ignore[arg-type]
        work_id="work_abc",
        doi="10.1000/example.1",
        provider="crossref",
        record=None,
        fields=tuple(
            Field(name, value, "VALIDATED", "crossref")
            for name, value in values.items()
        ),
        checks=(),
        candidates=(),
        alternatives=(),
        observed=Observed(None, (), ()),
        reasons=("test",),
    )


def test_article_entry_round_trips_and_protects_capitals() -> None:
    identity = make_identity()
    entry = bibtex_entry(identity)
    assert entry is not None
    assert entry.startswith("@article{travers2019highenergy,\n")
    entry_type, key, fields = parse_bibtex(entry)
    assert (entry_type, key) == ("article", "travers2019highenergy")
    assert (
        fields["author"]
        == "Travers, John C. and {The Collaboration} and Dupont-Müller, Émilie"
    )
    assert (
        fields["title"]
        == "High-energy pulse self-compression in {HCF} \\& {VUV} generation"
    )
    assert fields["journal"] == "Nature Photonics"
    assert fields["pages"] == "547--554"
    assert fields["number"] == "8"
    assert fields["doi"] == "10.1000/example.1"
    assert "publisher" not in fields
    assert validate_bibtex(entry, identity) == ()


def test_other_types_and_article_numbers() -> None:
    identity = make_identity(
        article_type="proceedings-article", pages=None, article_number="063828"
    )
    entry = bibtex_entry(identity)
    assert entry is not None
    entry_type, _key, fields = parse_bibtex(entry)
    assert entry_type == "inproceedings"
    assert fields["booktitle"] == "Nature Photonics"
    assert fields["eid"] == "063828"
    assert fields["publisher"] == "Springer"
    assert "pages" not in fields
    preprint = make_identity(
        article_type="posted-content", journal=None, authors=None, year=None, title=None
    )
    entry = bibtex_entry(preprint)
    assert entry is not None
    entry_type, key, fields = parse_bibtex(entry)
    assert (entry_type, key, fields["note"]) == (
        "misc",
        "anonymousnodateuntitled",
        "Preprint",
    )
    assert citation_key(preprint) == "anonymousnodateuntitled"
    problems = validate_bibtex(entry, preprint)
    assert problems == ("missing required field(s): author, title, year",)


def test_unvalidated_identities_produce_no_entry() -> None:
    assert bibtex_entry(Identity.unverified("no lookup")) is None
    assert bibtex_entry(make_identity(status="CONFLICT")) is None


def test_validation_detects_tampering_and_malformed_entries() -> None:
    identity = make_identity()
    entry = bibtex_entry(identity)
    assert entry is not None
    tampered = (
        entry.replace("doi = {10.1000/example.1}", "doi = {10.1000/other}")
        .replace("year = {2019}", "year = {2020}")
        .replace("travers2019highenergy", "other")
    )
    assert validate_bibtex(tampered, identity) == (
        "citation key differs from the deterministic key",
        "DOI field differs from the validated DOI",
        "year field differs from the validated year",
    )
    assert validate_bibtex("not bibtex", identity) == ("Not a single BibTeX entry",)
    assert validate_bibtex("@article{k,\n  title = {ok},\n  broken\n}", identity) == (
        "Malformed BibTeX field near: 'broken'",
    )


def test_entry_skips_absent_fields_and_odd_authors() -> None:
    identity = make_identity(
        authors=[
            {
                "given": "X",
                "family": None,
                "literal": None,
                "orcid": None,
                "sequence": None,
            },
            {
                "given": None,
                "family": "Solo",
                "literal": None,
                "orcid": None,
                "sequence": None,
            },
        ],
        title="Double  space  ABC test",
        volume=None,
        issue=None,
        pages=None,
        publisher=None,
    )
    identity = replace(identity, doi=None)
    entry = bibtex_entry(identity)
    assert entry is not None
    _type, _key, fields = parse_bibtex(entry)
    assert fields["author"] == "Solo"
    assert fields["title"] == "Double  space  {ABC} test"
    assert "volume" not in fields and "doi" not in fields and "url" not in fields
    assert validate_bibtex(entry, identity) == ()


def asserted(entry: str) -> Identity:
    document = Document("f" * 64, "mineru", "4.0.5", "s", "1", (), (), (), ())
    return identity_from_bibtex(entry, document)


def test_asserted_reports_and_theses_keep_their_entry_type() -> None:
    report = asserted(
        "@techreport{x, author = {Phelps, A. V.}, title = {Cross sections},"
        " institution = {JILA}, number = {26}, year = {1985},"
        " url = {https://example.org/r}}"
    )
    entry = bibtex_entry(report)
    assert entry is not None
    entry_type, key, fields = parse_bibtex(entry)
    assert (entry_type, key) == ("techreport", "phelps1985cross")
    assert fields["institution"] == "JILA"
    assert "publisher" not in fields
    assert fields["url"] == "https://example.org/r"
    assert "doi" not in fields
    assert validate_bibtex(entry, report) == ()
    thesis = asserted(
        "@phdthesis{x, author = {Anna Wiesner}, title = {Ozone},"
        " school = {Uppsala University}, publisher = {Acta}, year = {2003}}"
    )
    fields = parse_bibtex(bibtex_entry(thesis) or "")[2]
    assert fields["school"] == "Uppsala University"
    assert fields["publisher"] == "Acta"
    booklet = asserted("@booklet{x, author = {A}, title = {T}, year = {2000}}")
    assert booklet.field("article_type") == "bibtex:booklet"
    assert parse_bibtex(bibtex_entry(booklet) or "")[0] == "booklet"
