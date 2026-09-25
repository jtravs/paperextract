"""Publish staged extractions into a portable library directory."""

import hashlib
import json
import re
import shutil
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from paperextract.bibtex import citation_key
from paperextract.catalog import catalog_row
from paperextract.formats import UnsupportedFormatError, check_paper
from paperextract.identity import Field, Identity, Observed, title_key
from paperextract.pipeline import (
    DOCUMENT_FILENAME,
    SOURCE_FILENAME,
    ExtractionOutcome,
    ExtractionSettings,
    PaperSources,
    extract_bundle,
    extract_pdf,
)
from paperextract.protocol import MineruProfile
from paperextract.registry import Author, RegistryFailure, RegistryRecord
from paperextract.storage import (
    CATALOG_FILENAME,
    CORPUS_FILENAME,
    IDENTITY_FILENAME,
    INTERNAL_DIRECTORY,
    PublicationConflictError,
    _author_names,  # pyright: ignore[reportPrivateUsage]
    ensure_library,
    extract_and_publish,
    paper_directory_name,
    publish,
    read_catalog,
    resolve_and_write_identity,
)
from paperextract.worker import WorkerEnvironment


def make_settings(
    tmp_path: Path,
    native_worker: Callable[[Path, str], Path],
    behaviour: str = "ok",
) -> ExtractionSettings:
    script = native_worker(tmp_path / f"worker_{behaviour}.py", behaviour)
    return ExtractionSettings(
        environment=WorkerEnvironment(python=Path(sys.executable), script=script),
        model_dir=tmp_path / "models",
        profile=MineruProfile(cpu_threads=1),
        timeout_seconds=60,
    )


def make_pdf(tmp_path: Path, pdf_builder: Callable[..., bytes]) -> Path:
    pdf = tmp_path / "My Paper.pdf"
    pdf.write_bytes(
        pdf_builder(["Body doi:10.1000/z", "Second", None, None], title="Paper")
    )
    return pdf


@pytest.fixture
def staged(
    tmp_path: Path,
    pdf_builder: Callable[..., bytes],
    native_worker: Callable[[Path, str], Path],
) -> ExtractionOutcome:
    staging = tmp_path / "staging"
    staging.mkdir()
    return extract_pdf(
        make_pdf(tmp_path, pdf_builder),
        staging,
        make_settings(tmp_path, native_worker),
        request_id="run-1",
    )


@pytest.fixture
def published(
    staged: ExtractionOutcome, tmp_path: Path
) -> tuple[Path, Path, ExtractionOutcome]:
    library = tmp_path / "library"
    paper = publish(staged.staging, library, dpi=72)
    return library, paper.directory, staged


def test_publish_builds_a_verified_directory(
    published: tuple[Path, Path, ExtractionOutcome],
) -> None:
    library, paper, staged = published
    assert paper == library / paper_directory_name(
        Identity.unverified("test"), staged.source.sha256
    )
    for name in (
        "paper.md",
        "document.json",
        "metadata.json",
        "extraction.json",
        "validation.json",
        "manifest.json",
    ):
        assert (paper / name).is_file(), name
    original = paper / "original" / "source_01" / "My Paper.pdf"
    assert original.read_bytes() == (staged.staging / SOURCE_FILENAME).read_bytes()
    assert (paper / DOCUMENT_FILENAME).read_bytes() == (
        staged.staging / DOCUMENT_FILENAME
    ).read_bytes()
    manifest = json.loads((paper / "manifest.json").read_text())
    recorded = {item["path"]: item for item in manifest["files"]}
    assert "manifest.json" not in recorded
    for relative, item in recorded.items():
        data = (paper / relative).read_bytes()
        assert hashlib.sha256(data).hexdigest() == item["sha256"], relative
        assert len(data) == item["size_bytes"]
    assert not any((library / INTERNAL_DIRECTORY / "staging").iterdir())


def test_publish_exports_assets_and_diagnostics(
    published: tuple[Path, Path, ExtractionOutcome],
) -> None:
    _library, paper, _staged = published
    figure_pngs = sorted(p.name for p in (paper / "figures").glob("*.png"))
    assert len(figure_pngs) == 3
    png = (paper / "figures" / figure_pngs[0]).read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(list((paper / "figures").glob("*_p*.jpg"))) == 2
    tables = sorted(p.name for p in (paper / "tables").iterdir())
    assert sum(name.endswith(".html") for name in tables) == 5
    assert sum(name.endswith(".json") for name in tables) == 5
    assert sum(name.endswith(".csv") for name in tables) == 1
    assert sum(name.endswith(".jpg") for name in tables) == 1
    assert len(list((paper / "equations").iterdir())) == 1
    raw = paper / "diagnostics" / "raw" / "run-1"
    assert {p.name for p in raw.iterdir()} >= {
        "middle_json.json",
        "markdown.md",
        "request.json",
        "result.json",
        "worker.stdout.log",
        "worker.stderr.log",
    }
    assert (paper / "diagnostics" / "review.md").read_text().startswith("# Review")
    extraction = json.loads((paper / "extraction.json").read_text())
    assert extraction["run_id"] == "run-1"
    assert extraction["sources"][0]["original_name"] == "My Paper.pdf"
    assert len(extraction["omitted_blocks"]) == 3
    assert any(
        "context crop not rendered" in note or "no geometry" in note
        for note in extraction["asset_notes"]
    )
    metadata = json.loads((paper / "metadata.json").read_text())
    assert metadata["schema_version"] == 2
    assert metadata["bibliographic_status"] == "UNVERIFIED"
    assert metadata["fields"]["title"] == {
        "name": "title",
        "value": None,
        "status": "UNVERIFIED",
        "source": None,
    }
    assert metadata["observations"]["title"] == "Synthetic Paper"
    assert metadata["observations"]["doi_candidates"] == ["10.1000/z"]
    assert metadata["bibliography_validation"]["citation"] is None
    assert not (paper / "citation.bib").exists()
    validation = json.loads((paper / "validation.json").read_text())
    assert validation["processing_status"] == "PARTIAL"


def test_publish_writes_markdown_and_catalog(
    published: tuple[Path, Path, ExtractionOutcome],
) -> None:
    library, paper, staged = published
    markdown = (paper / "paper.md").read_text()
    assert markdown.startswith("---\n")
    assert 'bibliographic_status: "UNVERIFIED"' in markdown
    assert 'processing_status: "PARTIAL"' in markdown
    assert '  title: "Synthetic Paper"' in markdown
    assert '- "10.1000/z"' in markdown
    assert 'path: "original/source_01/My Paper.pdf"' in markdown
    for link in _markdown_links(markdown):
        assert (paper / link).is_file(), link
    corpus = json.loads((library / CORPUS_FILENAME).read_text())
    assert corpus["schema"] == "paperextract.corpus"
    rows = read_catalog(library)
    assert len(rows) == 1
    row = rows[0]
    assert row["directory"] == paper.name
    assert row["library_id"] == corpus["library_id"]
    assert row["title_observed"] == "Synthetic Paper"
    assert row["title_key_observed"] == "synthetic paper"
    assert row["source_sha256"] == [staged.source.sha256]
    assert row["processing_status"] == "PARTIAL"
    counts = row["counts"]
    assert isinstance(counts, dict)
    assert counts["figures"] == 4


def test_a_published_paper_is_current_and_newer_records_are_refused(
    published: tuple[Path, Path, ExtractionOutcome],
) -> None:
    library, paper, _staged = published
    check = check_paper(paper, library)
    assert (check.state, check.problems) == ("current", ())
    assert {r.path for r in check.records} >= {
        "manifest.json",
        "document.json",
        "extraction.json",
        "metadata.json",
        "validation.json",
    }
    metadata = json.loads((paper / "metadata.json").read_text())
    metadata["schema_version"] = 9
    (paper / "metadata.json").write_text(json.dumps(metadata))
    with pytest.raises(UnsupportedFormatError, match=r"metadata\.json: .* 9"):
        catalog_row(paper, "id", library)


def _markdown_links(markdown: str) -> list[str]:
    return [
        target
        for target in re.findall(r"\]\(([^)]+)\)", markdown)
        if not target.startswith("http")
    ]


def test_second_publication_of_the_same_source_conflicts(
    staged: ExtractionOutcome, tmp_path: Path
) -> None:
    library = tmp_path / "library"
    publish(staged.staging, library, dpi=72)
    with pytest.raises(PublicationConflictError):
        publish(staged.staging, library, dpi=72)
    assert len(read_catalog(library)) == 1
    assert not any((library / INTERNAL_DIRECTORY / "staging").iterdir())


def test_failed_build_removes_its_partial_directory(
    staged: ExtractionOutcome, tmp_path: Path
) -> None:
    library = tmp_path / "library"
    (staged.staging / SOURCE_FILENAME).unlink()
    with pytest.raises(FileNotFoundError):
        publish(staged.staging, library, dpi=72)
    assert not any((library / INTERNAL_DIRECTORY / "staging").iterdir())
    assert not (
        library
        / paper_directory_name(Identity.unverified("test"), staged.source.sha256)
    ).exists()
    assert read_catalog(library) == ()


def test_publish_rejects_inconsistent_staging(
    staged: ExtractionOutcome, tmp_path: Path
) -> None:
    library = tmp_path / "library"
    result_path = staged.staging / "worker" / "result.json"
    result = json.loads(result_path.read_text())
    result.update(
        status="failed",
        coverage=None,
        page_count=None,
        failure={"kind": "E", "message": "m", "traceback": ""},
    )
    result_path.write_text(json.dumps(result))
    with pytest.raises(ValueError, match="completed extraction"):
        publish(staged.staging, library, dpi=72)
    record_path = staged.staging / "source.json"
    record = json.loads(record_path.read_text())
    record["schema_version"] = 99
    record_path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="source record"):
        publish(staged.staging, library, dpi=72)


def test_publish_rejects_a_digest_mismatch(
    staged: ExtractionOutcome, tmp_path: Path
) -> None:
    record_path = staged.staging / "source.json"
    record = json.loads(record_path.read_text())
    record["sha256"] = "0" * 64
    record_path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="disagree"):
        publish(staged.staging, tmp_path / "library", dpi=72)


def test_library_records_are_validated(tmp_path: Path) -> None:
    library = tmp_path / "library"
    first = ensure_library(library)
    assert ensure_library(library) == first
    (library / CORPUS_FILENAME).write_text(json.dumps({"schema": "other"}))
    with pytest.raises(ValueError, match="corpus"):
        ensure_library(library)
    assert read_catalog(tmp_path / "absent") == ()
    (library / CATALOG_FILENAME).write_text(
        '\n{"schema": "paperextract.other", "schema_version": 1}\n'
    )
    with pytest.raises(ValueError, match="another schema"):
        read_catalog(library)
    (library / CATALOG_FILENAME).write_text(
        '{"schema": "paperextract.catalog-row", "schema_version": 3}\n'
    )
    with pytest.raises(UnsupportedFormatError, match="catalog-row version 3"):
        read_catalog(library)
    corpus = {**first, "schema_version": 2}
    (library / CORPUS_FILENAME).write_text(json.dumps(corpus))
    with pytest.raises(UnsupportedFormatError, match="corpus version 2"):
        ensure_library(library)


@pytest.mark.parametrize(
    ("title", "key"),
    [
        ("The Refractive Index of Helium", "refractive index of helium"),
        ("Dispersion  of\tArgon*", "dispersion of argon"),
        ("Étude des gaz: H₂ & D₂", "etude des gaz h2 d2"),
        ("A", "a"),
        ("An accurate measurement", "accurate measurement"),
    ],
)
def test_title_keys_are_conservative_lookup_keys(title: str, key: str) -> None:
    assert title_key(title) == key


def test_directory_names_transliterate_letters_nfkd_cannot_fold() -> None:
    identity = replace(
        Identity.unverified("no lookup"),
        status="VALIDATED",
        fields=(
            Field(
                "authors",
                [{"given": "M.", "family": "Pawłowski", "literal": None}],
                "VALIDATED",
                "crossref",
            ),
            Field("year", 2005, "VALIDATED", "crossref"),
            Field("title", "Cauchy moments of Ne and Groß", "VALIDATED", "crossref"),
        ),
    )
    assert paper_directory_name(identity, "0" * 64) == (
        "Pawlowski_2005_CauchyMomentsNe"
    )
    assert citation_key(identity) == "pawlowski2005cauchy"


def test_unverified_directory_names_come_from_the_digest() -> None:
    identity = Identity.unverified("no lookup")
    assert paper_directory_name(identity, "abcdef0123456789" + "0" * 48) == (
        "Unverified_abcdef012345"
    )


def test_failed_context_crop_and_missing_logs_are_noted(
    staged: ExtractionOutcome, tmp_path: Path
) -> None:
    document_path = staged.staging / DOCUMENT_FILENAME
    document = json.loads(document_path.read_text())
    figure = next(b for b in document["blocks"] if b["kind"] == "figure")
    figure["context_bbox_pt"] = [0.0, 900.0, 100.0, 950.0]
    document_path.write_text(json.dumps(document))
    (staged.staging / "worker" / "worker.stdout.log").unlink()
    paper = publish(staged.staging, tmp_path / "library", dpi=72)
    extraction = json.loads((paper.directory / "extraction.json").read_text())
    assert any(
        "context crop not rendered" in note for note in extraction["asset_notes"]
    )
    raw = paper.directory / "diagnostics" / "raw" / "run-1"
    assert not (raw / "worker.stdout.log").exists()
    assert (raw / "worker.stderr.log").exists()


def test_changed_source_bytes_block_publication(
    staged: ExtractionOutcome, tmp_path: Path
) -> None:
    with (staged.staging / SOURCE_FILENAME).open("ab") as handle:
        handle.write(b"\n%tampered")
    with pytest.raises(ValueError, match="changed before publication"):
        publish(staged.staging, tmp_path / "library", dpi=72)


def test_rename_race_is_reported_as_a_conflict(
    staged: ExtractionOutcome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(self: Path, target: Path) -> Path:
        raise OSError(f"cannot rename {self} to {target}")

    monkeypatch.setattr(Path, "rename", refuse)
    library = tmp_path / "library"
    with pytest.raises(PublicationConflictError, match="appeared"):
        publish(staged.staging, library, dpi=72)
    assert not any((library / INTERNAL_DIRECTORY / "staging").iterdir())


def test_extract_and_publish_chains_the_pipeline(
    tmp_path: Path,
    pdf_builder: Callable[..., bytes],
    native_worker: Callable[[Path, str], Path],
) -> None:
    pdf = make_pdf(tmp_path, pdf_builder)
    library = tmp_path / "library"
    staging = tmp_path / "staging-ok"
    staging.mkdir()
    bundle, paper = extract_and_publish(
        PaperSources(pdf),
        staging,
        make_settings(tmp_path, native_worker),
        library,
        request_id="ok",
    )
    assert paper is not None
    assert bundle.main.document is not None
    assert bundle.failure is None
    assert (paper.directory / "paper.md").exists()
    failing = tmp_path / "staging-failed"
    failing.mkdir()
    bundle, paper = extract_and_publish(
        PaperSources(pdf),
        failing,
        make_settings(tmp_path, native_worker, "failed"),
        library,
        request_id="failed",
    )
    assert paper is None
    assert bundle.failure is bundle.main
    assert bundle.supplements == ()


def make_record(doi: str, title: str, family: str, year: int) -> RegistryRecord:
    return RegistryRecord(
        provider="crossref",
        doi=doi,
        type="journal-article",
        title=title,
        title_raw=title,
        subtitle=None,
        authors=(Author("A.", family, None, None, "first"),),
        container_title="Synthetic Letters",
        publisher="Synthetic Press",
        volume="1",
        issue="2",
        pages="10-20",
        article_number=None,
        issued=(year, 1),
        published_print=(),
        published_online=(),
        url=f"https://doi.org/{doi}",
        issn=(),
        license_urls=(),
        abstract=None,
        source_url="u",
        retrieved_utc="2026-09-22T00:00:00+00:00",
        body_sha256="0" * 64,
        cached=False,
    )


def matching_lookup(doi: str) -> RegistryRecord | RegistryFailure:
    if doi == "10.1000/x":
        return make_record(doi, "Synthetic Paper", "Author", 2020)
    return RegistryFailure(doi, "not_found", "no record")


def test_validated_identity_names_cites_and_catalogs(
    staged: ExtractionOutcome, tmp_path: Path
) -> None:
    identity = resolve_and_write_identity(staged.staging, matching_lookup)
    assert identity.status == "VALIDATED"
    assert (staged.staging / IDENTITY_FILENAME).is_file()
    library = tmp_path / "library"
    paper = publish(staged.staging, library, dpi=72)
    assert paper.name == "Author_2020_SyntheticPaper"
    bib = (paper.directory / "citation.bib").read_text()
    assert bib.startswith("@article{author2020synthetic,")
    assert "doi = {10.1000/x}" in bib
    metadata = json.loads((paper.directory / "metadata.json").read_text())
    assert metadata["bibliographic_status"] == "VALIDATED"
    assert metadata["fields"]["title"]["value"] == "Synthetic Paper"
    assert metadata["fields"]["year"]["source"] == "crossref"
    assert metadata["bibliography_validation"]["status"] == "VALIDATED"
    assert metadata["identity"]["registry_record"]["doi"] == "10.1000/x"
    markdown = (paper.directory / "paper.md").read_text()
    assert 'title: "Synthetic Paper"' in markdown
    assert 'bibliographic_status: "VALIDATED"' in markdown
    assert '- "A. Author"' in markdown
    row = read_catalog(library)[0]
    assert (row["doi"], row["year"], row["venue"]) == (
        "10.1000/x",
        2020,
        "Synthetic Letters",
    )
    assert row["title_key"] == "synthetic paper"
    assert row["work_id"] == identity.work_id
    validation = json.loads((paper.directory / "validation.json").read_text())
    checks = {c["name"]: c["outcome"] for c in validation["checks"]}
    assert checks["bibliographic_identity"] == "pass"
    raw = paper.directory / "diagnostics" / "raw" / "run-1" / IDENTITY_FILENAME
    assert raw.is_file()


def test_same_name_for_another_source_gets_a_digest_suffix(
    staged: ExtractionOutcome, tmp_path: Path
) -> None:
    resolve_and_write_identity(staged.staging, matching_lookup)
    library = tmp_path / "library"
    publish(staged.staging, library, dpi=72)
    (library / "Author_2020_Other").mkdir()
    identity = Identity.from_json((staged.staging / IDENTITY_FILENAME).read_text())
    other = "1" * 64
    taken: dict[str, set[str]] = {
        "Author_2020_SyntheticPaper": {staged.source.sha256},
        "Author_2020_Other": set(),
    }
    assert paper_directory_name(identity, staged.source.sha256, taken) == (
        "Author_2020_SyntheticPaper"
    )
    assert (
        paper_directory_name(identity, other, taken)
        == "Author_2020_SyntheticPaper_111111"
    )
    with pytest.raises(PublicationConflictError):
        publish(staged.staging, library, dpi=72)


def test_directory_names_fold_and_bound_titles() -> None:
    record = make_record(
        "10.1000/y",
        "Étude des gaz nobles: hélium et argon",
        "Dupont-Müller" + "y" * 100,
        1999,
    )
    identity = Identity(
        status="VALIDATED",
        work_id="work_y",
        doi="10.1000/y",
        provider="crossref",
        record=None,
        fields=(
            Field("title", record.title, "VALIDATED", "crossref"),
            Field(
                "authors",
                [a.to_dict() for a in record.authors],
                "VALIDATED",
                "crossref",
            ),
            Field("year", 1999, "VALIDATED", "crossref"),
        ),
        checks=(),
        candidates=(),
        alternatives=(),
        observed=Observed(None, (), ()),
        reasons=(),
    )
    name = paper_directory_name(identity, "a" * 64)
    assert name.startswith("DupontMulleryyyy")
    assert len(name.encode()) <= 96
    assert "_1999_" not in name or name.endswith("EtudeDesGaz")
    short_family = replace(
        identity,
        fields=(
            Field("title", record.title, "VALIDATED", "crossref"),
            Field("authors", [{"family": "Dupont-Müller"}], "VALIDATED", "crossref"),
            Field("year", 1999, "VALIDATED", "crossref"),
        ),
    )
    assert (
        paper_directory_name(short_family, "a" * 64) == "DupontMuller_1999_EtudeDesGaz"
    )
    anonymous = replace(
        identity,
        fields=(
            Field(
                "authors",
                [{"literal": "???"}, {"given": None, "family": None, "literal": None}],
                "VALIDATED",
                "crossref",
            ),
        ),
    )
    assert paper_directory_name(anonymous, "b" * 64) == "Anonymous_nodate_Untitled"
    assert _author_names(anonymous) == ["???"]
    no_authors = replace(
        identity, fields=(Field("title", "Some Title", "VALIDATED", "x"),)
    )
    assert paper_directory_name(no_authors, "c" * 64) == "Anonymous_nodate_SomeTitle"
    assert _author_names(no_authors) == []


def test_extract_and_publish_resolves_identity_when_a_lookup_is_set(
    tmp_path: Path,
    pdf_builder: Callable[..., bytes],
    native_worker: Callable[[Path, str], Path],
) -> None:
    settings = make_settings(tmp_path, native_worker)
    settings = ExtractionSettings(
        environment=settings.environment,
        model_dir=settings.model_dir,
        profile=settings.profile,
        timeout_seconds=settings.timeout_seconds,
        lookup=matching_lookup,
    )
    staging = tmp_path / "staging-identity"
    staging.mkdir()
    _outcome, paper = extract_and_publish(
        PaperSources(make_pdf(tmp_path, pdf_builder)),
        staging,
        settings,
        tmp_path / "library",
        request_id="id",
    )
    assert paper is not None and paper.name == "Author_2020_SyntheticPaper"


def test_supplements_are_published_inside_the_paper_directory(
    tmp_path: Path,
    pdf_builder: Callable[..., bytes],
    native_worker: Callable[[Path, str], Path],
) -> None:
    paper_pdf = make_pdf(tmp_path, pdf_builder)
    supplement_pdf = tmp_path / "My Paper SI.pdf"
    supplement_pdf.write_bytes(
        pdf_builder(["Supplementary material", "More", None, None], title="SI")
    )
    staging = tmp_path / "bundle"
    staging.mkdir()
    library = tmp_path / "library"
    bundle, paper = extract_and_publish(
        PaperSources(paper_pdf, (supplement_pdf,)),
        staging,
        make_settings(tmp_path, native_worker),
        library,
        request_id="bundle",
    )
    assert paper is not None
    assert len(bundle.supplements) == 1
    root = paper.directory
    supplement = root / "supplement_01"
    assert (root / "original" / "source_02" / "My Paper SI.pdf").read_bytes() == (
        supplement_pdf.read_bytes()
    )
    assert (supplement / "document.json").is_file()
    markdown = (supplement / "supplement.md").read_text()
    assert markdown.startswith("---\n")
    assert 'role: "supplement"' in markdown
    assert 'paper: "../paper.md"' in markdown
    assert '<a id="figure-s1"></a>\n\n**Figure 1**' in markdown
    assert "](figures/" in markdown
    assert any((supplement / "figures").iterdir())
    main = (root / "paper.md").read_text()
    assert 'supplements:\n- "supplement_01/supplement.md"' in main
    assert 'role: "supplement"' in main
    validation = json.loads((root / "validation.json").read_text())
    assert [item["id"] for item in validation["supplements"]] == ["supplement_01"]
    # The synthetic caption "TABLE S1. Parameters." resolves to the supplement.
    assert validation["supplement_references"] == {"resolved": 1, "unresolved": []}
    extraction = json.loads((root / "extraction.json").read_text())
    assert [s["id"] for s in extraction["sources"]] == ["source_01", "source_02"]
    assert extraction["supplements"][0]["request"]["request_id"] == "bundle-s01"
    assert extraction["table_ocr"] is None
    assert (
        root / "diagnostics" / "raw" / "bundle" / "supplement_01" / "result.json"
    ).is_file()
    assert (
        "# Review of supplement_01" in (root / "diagnostics" / "review.md").read_text()
    )
    (row,) = read_catalog(library)
    assert row["source_sha256"] == [
        bundle.main.source.sha256,
        bundle.supplements[0].source.sha256,
    ]
    assert row["supplements"][0]["markdown"] == "supplement_01/supplement.md"  # type: ignore[index]
    manifest = json.loads((root / "manifest.json").read_text())
    assert "supplement_01/supplement.md" in {item["path"] for item in manifest["files"]}


def test_a_failed_supplement_stops_publication(
    tmp_path: Path,
    pdf_builder: Callable[..., bytes],
    native_worker: Callable[[Path, str], Path],
) -> None:
    paper_pdf = make_pdf(tmp_path, pdf_builder)
    first = tmp_path / "s1.pdf"
    first.write_bytes(pdf_builder(["One", "b", None, None]))
    second = tmp_path / "s2.pdf"
    second.write_bytes(pdf_builder(["Two", "b", None, None]))
    staging = tmp_path / "bundle"
    staging.mkdir()
    bundle, paper = extract_and_publish(
        PaperSources(paper_pdf, (first, second)),
        staging,
        make_settings(tmp_path, native_worker, "fail_supplement"),
        tmp_path / "library",
        request_id="bundle",
    )
    assert paper is None
    assert len(bundle.supplements) == 1
    assert bundle.failure is bundle.supplements[0]


def test_table_ocr_runs_are_recorded_and_copied(
    staged: ExtractionOutcome, tmp_path: Path
) -> None:
    ocr = staged.staging / "worker-ocr"
    worker = staged.staging / "worker"
    ocr.mkdir()
    for name in ("request.json", "result.json", "worker.stderr.log"):
        shutil.copyfile(worker / name, ocr / name)
    (ocr / "native").mkdir()
    shutil.copyfile(
        worker / "native" / "middle_json.json", ocr / "native" / "middle_json.json"
    )
    paper = publish(staged.staging, tmp_path / "library", dpi=72)
    extraction = json.loads((paper.directory / "extraction.json").read_text())
    assert extraction["table_ocr"]["request"]["request_id"] == "run-1"
    raw = paper.directory / "diagnostics" / "raw" / "run-1" / "table-ocr"
    assert sorted(p.name for p in raw.iterdir()) == [
        "middle_json.json",
        "request.json",
        "result.json",
        "worker.stderr.log",
    ]


def test_a_changed_supplement_is_not_published(
    tmp_path: Path,
    pdf_builder: Callable[..., bytes],
    native_worker: Callable[[Path, str], Path],
) -> None:
    supplement = tmp_path / "si.pdf"
    supplement.write_bytes(pdf_builder(["SI", "b", None, None]))
    staging = tmp_path / "bundle"
    staging.mkdir()
    extract_bundle(
        PaperSources(make_pdf(tmp_path, pdf_builder), (supplement,)),
        staging,
        make_settings(tmp_path, native_worker),
        request_id="bundle",
    )
    (staging / "supplements" / "01" / "source.pdf").write_bytes(b"%PDF-1.4 changed")
    with pytest.raises(ValueError, match="supplement digest changed"):
        publish(staging, tmp_path / "library", dpi=72)
    assert not any((tmp_path / "library" / INTERNAL_DIRECTORY / "staging").iterdir())
