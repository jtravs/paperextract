"""Rebuild published papers from their kept output and replace them safely."""

import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from paperextract.catalog import paper_directories, read_catalog, rebuild_catalog
from paperextract.document import Document
from paperextract.pipeline import (
    ExtractionSettings,
    PaperSources,
    rebuild_document,
)
from paperextract.protocol import MineruProfile
from paperextract.registry import RegistryFailure, RegistryRecord
from paperextract.reprocess import MissingOutputError, kept_run, stage_from_paper
from paperextract.storage import (
    INTERNAL_DIRECTORY,
    PublicationConflictError,
    extract_and_publish,
    publish,
    recover_library,
    resolve_and_write_identity,
)
from paperextract.worker import WorkerEnvironment

Lookup = Callable[[str], RegistryRecord | RegistryFailure]


def settings(
    tmp_path: Path, native_worker: Callable[[Path, str], Path]
) -> ExtractionSettings:
    script = native_worker(tmp_path / "worker.py", "ok")
    return ExtractionSettings(
        environment=WorkerEnvironment(Path(sys.executable), script),
        model_dir=tmp_path / "models",
        profile=MineruProfile(cpu_threads=1),
        timeout_seconds=60,
    )


@pytest.fixture
def library(
    tmp_path: Path,
    pdf_builder: Callable[..., bytes],
    native_worker: Callable[[Path, str], Path],
) -> Path:
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(
        pdf_builder(["Body doi:10.1000/z", "Second", None, None], title="Paper")
    )
    supplement = tmp_path / "paper_si.pdf"
    supplement.write_bytes(pdf_builder(["Supplement", "More", None, None], title="SI"))
    staging = tmp_path / "run"
    staging.mkdir()
    root = tmp_path / "library"
    _bundle, published = extract_and_publish(
        PaperSources(paper, (supplement,)),
        staging,
        settings(tmp_path, native_worker),
        root,
        request_id="first",
    )
    assert published is not None
    return root


def only_paper(library: Path) -> Path:
    (row,) = read_catalog(library)
    return library / str(row["directory"])


def test_a_paper_is_rebuilt_exactly_from_its_kept_output(
    library: Path, tmp_path: Path
) -> None:
    paper = only_paper(library)
    before = {
        p.relative_to(paper): p.read_bytes() for p in paper.rglob("*") if p.is_file()
    }
    run = tmp_path / "rebuild"
    run.mkdir()
    stage_from_paper(paper, run)
    assert (run / "worker" / "native" / "images" / "p1.jpg").read_bytes() == b"p1"
    assert (run / "supplements" / "01" / "worker" / "result.json").is_file()
    document = rebuild_document(run)
    assert document == Document.from_json((paper / "document.json").read_text())
    rebuild_document(run / "supplements" / "01")
    republished = publish(run, library, dpi=300, replace=paper.name)
    assert republished.directory == paper
    after = {
        p.relative_to(paper): p.read_bytes() for p in paper.rglob("*") if p.is_file()
    }
    changing = {
        "manifest.json",
        "extraction.json",
        "paper.md",
        "supplement_01/supplement.md",
    }
    assert set(before) == set(after)
    for relative, data in before.items():
        if relative.as_posix() not in changing:
            assert after[relative] == data, relative
    (retired,) = (library / INTERNAL_DIRECTORY / "replaced").iterdir()
    assert retired.name.startswith(f"{paper.name}.")
    (row,) = read_catalog(library)
    assert row["generation"] == republished.generation


def test_a_refreshed_identity_renames_the_paper(
    library: Path, tmp_path: Path, synthetic_lookup: Lookup
) -> None:
    paper = only_paper(library)
    assert paper.name.startswith("Unverified_")
    run = tmp_path / "rebuild"
    run.mkdir()
    stage_from_paper(paper, run)
    rebuild_document(run)
    resolve_and_write_identity(run, synthetic_lookup)
    republished = publish(run, library, dpi=72, replace=paper.name)
    assert republished.name == "Author_2020_SyntheticPaper"
    assert not paper.exists()
    (row,) = read_catalog(library)
    assert row["directory"] == "Author_2020_SyntheticPaper"
    assert row["abstract"] == "An abstract about synthetic pulses in Börzsönyi cells."
    assert row["citation_key"] == "author2020synthetic"
    assert row["first_author_family"] == "Author"


def test_replacement_refuses_missing_papers_and_foreign_names(
    library: Path, tmp_path: Path, synthetic_lookup: Lookup
) -> None:
    paper = only_paper(library)
    run = tmp_path / "rebuild"
    run.mkdir()
    stage_from_paper(paper, run)
    with pytest.raises(FileNotFoundError, match="No published paper"):
        publish(run, library, replace="Absent_Paper")
    resolve_and_write_identity(run, synthetic_lookup)
    # A catalogued directory of the same source under the target name conflicts.
    shutil.copytree(paper, library / "Author_2020_SyntheticPaper")
    rebuild_catalog(library, "lib")
    with pytest.raises(PublicationConflictError):
        publish(run, library, replace=paper.name)
    assert paper.is_dir()


def test_missing_kept_output_is_reported(library: Path, tmp_path: Path) -> None:
    paper = only_paper(library)
    run = tmp_path / "rebuild"
    run.mkdir()
    (run / "stray").write_text("x")
    with pytest.raises(FileExistsError):
        stage_from_paper(paper, run)
    (run / "stray").unlink()
    raw = next((paper / "diagnostics" / "raw").iterdir())
    (raw / "supplement_01" / "result.json").unlink()
    with pytest.raises(MissingOutputError, match=r"supplement_01/result\.json"):
        kept_run(paper)
    (paper / "extraction.json").unlink()
    with pytest.raises(MissingOutputError, match=r"no extraction\.json"):
        stage_from_paper(paper, run)


def test_rebuilding_requires_a_completed_run(library: Path, tmp_path: Path) -> None:
    paper = only_paper(library)
    run = tmp_path / "rebuild"
    run.mkdir()
    stage_from_paper(paper, run)
    result = json.loads((run / "worker" / "result.json").read_text())
    result.update(
        status="failed",
        page_count=None,
        coverage=None,
        failure={"kind": "RuntimeError", "message": "x", "traceback": ""},
    )
    (run / "worker" / "result.json").write_text(json.dumps(result))
    with pytest.raises(ValueError, match="completed worker run"):
        rebuild_document(run)
    (run / "source.json").write_text("[]")
    with pytest.raises(ValueError, match="Source record"):
        rebuild_document(run)


def journal(library: Path, old: str, new: str, build: str, retired: str) -> None:
    directory = library / INTERNAL_DIRECTORY / "journal"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{build.rsplit('/', 1)[-1]}.json").write_text(
        json.dumps({"old": old, "new": new, "build": build, "retired": retired})
    )


def test_interrupted_replacements_are_completed_or_rolled_back(library: Path) -> None:
    paper = only_paper(library)
    name = paper.name
    internal = f"{INTERNAL_DIRECTORY}"
    # Interrupted after retiring the old directory: the build moves into place.
    build = library / internal / "staging" / "b1"
    shutil.copytree(paper, build)
    retired = library / internal / "replaced" / f"{name}.g1"
    retired.parent.mkdir(parents=True)
    paper.rename(retired)
    journal(
        library, name, name, f"{internal}/staging/b1", f"{internal}/replaced/{name}.g1"
    )
    assert recover_library(library) == [f"completed replacement of {name} by {name}"]
    assert paper.is_dir() and not build.exists()
    # Interrupted before retiring: the build is discarded.
    shutil.copytree(paper, build)
    journal(
        library, name, name, f"{internal}/staging/b1", f"{internal}/replaced/{name}.g2"
    )
    assert recover_library(library) == [f"rolled back replacement of {name}"]
    assert paper.is_dir() and not build.exists()
    # Interrupted after the final rename: only the journal remains.
    journal(
        library, name, name, f"{internal}/staging/b1", f"{internal}/replaced/{name}.g3"
    )
    assert recover_library(library) == [f"closed finished replacement of {name}"]
    assert not any((library / internal / "journal").iterdir())
    assert [row["directory"] for row in read_catalog(library)] == [name]
    assert recover_library(library) == []


def test_a_missing_library_has_no_papers(tmp_path: Path) -> None:
    assert paper_directories(tmp_path / "absent") == []
