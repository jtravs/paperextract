"""Preserve content-equivalent copies of a paper without extracting them."""

import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from paperextract.pipeline import (
    EQUIVALENTS_DIRECTORY,
    SOURCE_FILENAME,
    ExtractionSettings,
    PaperSources,
    attach_equivalent,
    extract_bundle,
)
from paperextract.protocol import MineruProfile
from paperextract.reprocess import MissingOutputError, stage_from_paper
from paperextract.storage import publish
from paperextract.worker import WorkerEnvironment

PAGES = ["Same text on page one " * 20, "Second", None, None]


@pytest.fixture
def run(
    tmp_path: Path,
    pdf_builder: Callable[..., bytes],
    native_worker: Callable[[Path, str], Path],
) -> Path:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(pdf_builder(PAGES))
    staging = tmp_path / "run"
    staging.mkdir()
    settings = ExtractionSettings(
        environment=WorkerEnvironment(
            Path(sys.executable), native_worker(tmp_path / "worker.py", "ok")
        ),
        model_dir=tmp_path / "models",
        profile=MineruProfile(cpu_threads=1),
        timeout_seconds=60,
    )
    extract_bundle(PaperSources(pdf), staging, settings, request_id="r")
    return staging


def test_only_copies_with_the_same_text_are_attached(
    run: Path, tmp_path: Path, pdf_builder: Callable[..., bytes]
) -> None:
    other = tmp_path / "other.pdf"
    other.write_bytes(pdf_builder(["Different text " * 20, "Second", None, None]))
    with pytest.raises(ValueError, match="not have the same text"):
        attach_equivalent(run, other)
    assert not (run / EQUIVALENTS_DIRECTORY / "01").exists()
    copy = tmp_path / "copy.pdf"
    copy.write_bytes(pdf_builder(PAGES, title="Re-saved"))
    directory = attach_equivalent(run, copy)
    (directory / SOURCE_FILENAME).write_bytes(b"%PDF-1.4 tampered")
    with pytest.raises(ValueError, match="changed before publication"):
        publish(run, tmp_path / "library")


def test_a_lost_copy_stops_a_rebuild(
    run: Path, tmp_path: Path, pdf_builder: Callable[..., bytes]
) -> None:
    copy = tmp_path / "copy.pdf"
    copy.write_bytes(pdf_builder(PAGES, title="Re-saved"))
    attach_equivalent(run, copy)
    paper = publish(run, tmp_path / "library").directory
    (paper / "original" / "source_02" / "copy.pdf").unlink()
    rebuild = tmp_path / "rebuild"
    rebuild.mkdir()
    with pytest.raises(MissingOutputError, match=r"copy\.pdf"):
        stage_from_paper(paper, rebuild)
