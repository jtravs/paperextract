"""Extract with Docling as the backend and cross-check MinerU tables with it."""

import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from paperextract.catalog import read_catalog
from paperextract.document import Document, Figure, Table
from paperextract.pipeline import (
    TABLE_CHECK_DIRECTORY,
    ExtractionOutcome,
    ExtractionSettings,
    PaperSources,
    TableCheckSettings,
    extract_pdf,
    rebuild_document,
)
from paperextract.protocol import REQUEST_FILENAME, DoclingProfile, MineruProfile
from paperextract.reprocess import stage_from_paper
from paperextract.storage import extract_and_publish
from paperextract.worker import WorkerEnvironment

Writer = Callable[[Path, str], Path]


@pytest.fixture
def pdf(tmp_path: Path, pdf_builder: Callable[..., bytes]) -> Path:
    path = tmp_path / "paper.pdf"
    path.write_bytes(pdf_builder(["Body doi:10.1000/z", "Second", None, None]))
    return path


def environment(script: Path) -> WorkerEnvironment:
    return WorkerEnvironment(Path(sys.executable), script)


def check_settings(
    tmp_path: Path, docling_worker: Writer, behaviour: str = "ok"
) -> TableCheckSettings:
    return TableCheckSettings(
        environment(docling_worker(tmp_path / f"docling-{behaviour}.py", behaviour)),
        tmp_path / "docling-models",
    )


def mineru_settings(
    tmp_path: Path, native_worker: Writer, check: TableCheckSettings | None
) -> ExtractionSettings:
    return ExtractionSettings(
        environment=environment(native_worker(tmp_path / "mineru.py", "ok")),
        model_dir=tmp_path / "models",
        profile=MineruProfile(cpu_threads=1),
        timeout_seconds=60,
        table_check=check,
    )


def run(
    tmp_path: Path, pdf: Path, settings: ExtractionSettings, name: str = "run"
) -> ExtractionOutcome:
    staging = tmp_path / name
    staging.mkdir()
    return extract_pdf(pdf, staging, settings, request_id=name)


def codes(document: Document | None) -> list[str]:
    assert document is not None
    return [finding.code for finding in document.findings]


def test_docling_can_be_the_extraction_backend(
    tmp_path: Path, pdf: Path, docling_worker: Writer
) -> None:
    settings = ExtractionSettings(
        environment=environment(docling_worker(tmp_path / "docling.py", "ok")),
        model_dir=tmp_path / "docling-models",
        profile=DoclingProfile(cpu_threads=1),
        timeout_seconds=60,
        table_check=check_settings(tmp_path, docling_worker),
    )
    outcome = run(tmp_path, pdf, settings)
    assert outcome.request.backend == "docling"
    assert outcome.result.backend == "docling"
    document = outcome.document
    assert document is not None and document.backend == "docling"
    assert any(isinstance(block, Figure) and block.label for block in document.blocks)
    # Neither the MinerU OCR re-extraction nor a check against itself runs.
    assert (outcome.ocr_result, outcome.check_result) == (None, None)
    request = json.loads((outcome.staging / "worker" / REQUEST_FILENAME).read_text())
    assert request["backend"] == "docling"
    assert request["profile"]["formulas"] is True
    rebuilt = rebuild_document(outcome.staging)
    assert rebuilt.to_json() == document.to_json()


def test_mineru_tables_are_cross_checked_on_their_pages_only(
    tmp_path: Path, pdf: Path, native_worker: Writer, docling_worker: Writer
) -> None:
    settings = mineru_settings(
        tmp_path, native_worker, check_settings(tmp_path, docling_worker)
    )
    outcome = run(tmp_path, pdf, settings)
    assert outcome.check_result is not None
    request = json.loads(
        (outcome.staging / TABLE_CHECK_DIRECTORY / REQUEST_FILENAME).read_text()
    )
    assert request["pages"] == [2]
    assert request["profile"]["formulas"] is False
    assert request["profile"]["images"] is False
    found = codes(outcome.document)
    assert found.count("TABLE_CHECK_AGREED") == 1
    assert "TABLE_CHECK_EXTRA" in found
    assert outcome.document is not None
    first = next(b for b in outcome.document.blocks if isinstance(b, Table))
    assert [body.source for body in first.alternatives] == ["docling"]
    # Rebuilding from kept output applies the kept check again.
    rebuilt = rebuild_document(outcome.staging)
    assert codes(rebuilt).count("TABLE_CHECK_AGREED") == 1


@pytest.mark.parametrize("behaviour", ["failed", "crash"])
def test_a_failed_check_keeps_the_tables_unchecked(
    tmp_path: Path,
    pdf: Path,
    native_worker: Writer,
    docling_worker: Writer,
    behaviour: str,
) -> None:
    settings = mineru_settings(
        tmp_path, native_worker, check_settings(tmp_path, docling_worker, behaviour)
    )
    outcome = run(tmp_path, pdf, settings)
    found = codes(outcome.document)
    assert "TABLE_CHECK_FAILED" in found
    assert "TABLE_CHECK_AGREED" not in found
    # A failed check that was kept is not applied on rebuild.
    assert "TABLE_CHECK_AGREED" not in codes(rebuild_document(outcome.staging))


def test_the_docling_backend_is_not_checked_against_itself(
    tmp_path: Path, pdf: Path, docling_worker: Writer
) -> None:
    settings = ExtractionSettings(
        environment=environment(docling_worker(tmp_path / "docling.py", "ok")),
        model_dir=tmp_path / "models",
        profile=DoclingProfile(cpu_threads=1),
        table_check=check_settings(tmp_path, docling_worker),
    )
    outcome = run(tmp_path, pdf, settings)
    assert not (outcome.staging / TABLE_CHECK_DIRECTORY).exists()


def test_a_published_check_survives_reprocessing(
    tmp_path: Path, pdf: Path, native_worker: Writer, docling_worker: Writer
) -> None:
    settings = mineru_settings(
        tmp_path, native_worker, check_settings(tmp_path, docling_worker)
    )
    staging = tmp_path / "publish-run"
    staging.mkdir()
    library = tmp_path / "library"
    _bundle, published = extract_and_publish(
        PaperSources(pdf), staging, settings, library, request_id="first"
    )
    assert published is not None
    (row,) = read_catalog(library)
    paper = library / str(row["directory"])
    extraction = json.loads((paper / "extraction.json").read_text())
    assert extraction["table_check"]["request"]["pages"] == [2]
    raw = paper / "diagnostics" / "raw" / str(extraction["run_id"]) / "table-check"
    assert (raw / "docling.json").is_file()
    rebuilt = tmp_path / "rebuild"
    rebuilt.mkdir()
    stage_from_paper(paper, rebuilt)
    assert (rebuilt / TABLE_CHECK_DIRECTORY / "native" / "docling.json").is_file()
    assert codes(rebuild_document(rebuilt)).count("TABLE_CHECK_AGREED") == 1
