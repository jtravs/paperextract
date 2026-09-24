"""Preserve saved web pages with a paper, compare them and keep them on rebuild."""

import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from paperextract.catalog import read_catalog
from paperextract.pipeline import (
    CAPTURES_DIRECTORY,
    ExtractionSettings,
    PaperSources,
    attach_capture,
    capture_directories,
    extract_bundle,
)
from paperextract.protocol import MineruProfile
from paperextract.reprocess import MissingOutputError, stage_from_paper
from paperextract.storage import HTML_CHECK_FILENAME, extract_and_publish, publish
from paperextract.worker import WorkerEnvironment

PAGE = (
    '<!DOCTYPE html><html><head><meta name="citation_title" content="Paper">'
    "</head><body><article><h2>Results</h2><p>" + "Body text words " * 10 + "</p>"
    "</article></body></html>"
)


@pytest.fixture
def settings(
    tmp_path: Path, native_worker: Callable[[Path, str], Path]
) -> ExtractionSettings:
    return ExtractionSettings(
        environment=WorkerEnvironment(
            Path(sys.executable), native_worker(tmp_path / "worker.py", "ok")
        ),
        model_dir=tmp_path / "models",
        profile=MineruProfile(cpu_threads=1),
        timeout_seconds=60,
    )


@pytest.fixture
def pdf(tmp_path: Path, pdf_builder: Callable[..., bytes]) -> Path:
    path = tmp_path / "paper.pdf"
    path.write_bytes(pdf_builder(["Body doi:10.1000/z", "Second", None, None]))
    return path


def capture(tmp_path: Path, name: str = "page.html") -> Path:
    path = tmp_path / "in" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(PAGE)
    assets = path.with_name(f"{path.stem}_files")
    assets.mkdir(exist_ok=True)
    (assets / "fig.png").write_bytes(b"png")
    return path


def test_captures_are_published_as_sources_with_a_check(
    tmp_path: Path, pdf: Path, settings: ExtractionSettings
) -> None:
    staging = tmp_path / "run"
    staging.mkdir()
    library = tmp_path / "library"
    page = capture(tmp_path)
    bundle, published = extract_and_publish(
        PaperSources(pdf, captures=(page,)),
        staging,
        settings,
        library,
        request_id="first",
    )
    assert published is not None and bundle.failure is None
    paper = published.directory
    assert (paper / "original" / "source_02" / "page.html").read_text() == PAGE
    assert (paper / "original" / "source_02" / "page_files" / "fig.png").is_file()
    extraction = json.loads((paper / "extraction.json").read_text())
    source = extraction["sources"][1]
    assert (source["id"], source["role"], source["type"]) == (
        "source_02",
        "html_capture",
        "html",
    )
    check = json.loads((paper / HTML_CHECK_FILENAME).read_text())
    (report,) = check["captures"]
    # The paper is unverified and the page has no DOI, so titles decide.
    assert report["identity"]["status"] in ("same_title", "unconfirmed")
    validation = json.loads((paper / "validation.json").read_text())
    assert validation["html_check"]["report"] == HTML_CHECK_FILENAME
    assert "# HTML cross-check" in (paper / "diagnostics" / "review.md").read_text()
    front = (paper / "paper.md").read_text()
    assert 'role: "html_capture"' in front
    manifest = json.loads((paper / "manifest.json").read_text())
    assert "original/source_02/page_files/fig.png" in {
        f["path"] for f in manifest["files"]
    }
    # A rebuild restores the capture from the paper directory.
    rebuild = tmp_path / "rebuild"
    rebuild.mkdir()
    stage_from_paper(paper, rebuild)
    (restored,) = capture_directories(rebuild)
    assert (restored / "page_files" / "fig.png").read_bytes() == b"png"
    attach_capture(rebuild, capture(tmp_path, "second.html"))
    again = publish(rebuild, library, replace=paper.name).directory
    assert (again / "original" / "source_03" / "second.html").is_file()
    (row,) = read_catalog(library)
    assert len(row["source_sha256"]) == 3  # type: ignore[arg-type]
    # A capture file lost from the paper directory stops a rebuild.
    (again / "original" / "source_03" / "second.html").unlink()
    broken = tmp_path / "broken"
    broken.mkdir()
    with pytest.raises(MissingOutputError, match=r"second\.html"):
        stage_from_paper(again, broken)


def test_captures_wait_for_a_complete_bundle_and_unchanged_files(
    tmp_path: Path,
    pdf: Path,
    settings: ExtractionSettings,
    native_worker: Callable[[Path, str], Path],
) -> None:
    failing = ExtractionSettings(
        environment=WorkerEnvironment(
            Path(sys.executable), native_worker(tmp_path / "bad.py", "failed")
        ),
        model_dir=tmp_path / "models",
    )
    staging = tmp_path / "failed"
    staging.mkdir()
    bundle = extract_bundle(
        PaperSources(pdf, captures=(capture(tmp_path),)),
        staging,
        failing,
        request_id="bad",
    )
    assert bundle.failure is not None
    assert not (staging / CAPTURES_DIRECTORY).exists()
    staging = tmp_path / "changed"
    staging.mkdir()
    extract_bundle(
        PaperSources(pdf, captures=(capture(tmp_path),)),
        staging,
        settings,
        request_id="ok",
    )
    (staging / CAPTURES_DIRECTORY / "01" / "page_files" / "fig.png").write_bytes(b"x")
    with pytest.raises(ValueError, match="changed before publication"):
        publish(staging, tmp_path / "library")
