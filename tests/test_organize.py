"""Place papers by a library layout and move them between layouts."""

import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from paperextract.catalog import paper_directories, read_catalog, shard_for
from paperextract.pipeline import ExtractionSettings, PaperSources
from paperextract.protocol import MineruProfile
from paperextract.registry import RegistryFailure, RegistryRecord
from paperextract.reprocess import stage_from_paper
from paperextract.storage import (
    INTERNAL_DIRECTORY,
    ORGANIZE_JOURNAL,
    PublicationConflictError,
    ensure_library,
    extract_and_publish,
    organize_library,
    plan_organize,
    publish,
    recover_library,
)
from paperextract.worker import WorkerEnvironment

Lookup = Callable[[str], RegistryRecord | RegistryFailure]


def publish_two(
    tmp_path: Path,
    pdf_builder: Callable[..., bytes],
    native_worker: Callable[[Path, str], Path],
    lookup: Lookup,
) -> Path:
    script = native_worker(tmp_path / "worker.py", "ok")
    library = tmp_path / "library"
    for index, paper_lookup in enumerate((lookup, None)):
        pdf = tmp_path / f"p{index}.pdf"
        pdf.write_bytes(pdf_builder([f"Body doi:10.1000/z {index}", "Two", None, None]))
        staging = tmp_path / f"run{index}"
        staging.mkdir()
        settings = ExtractionSettings(
            WorkerEnvironment(Path(sys.executable), script),
            tmp_path / "models",
            MineruProfile(cpu_threads=1),
            60,
            paper_lookup,
        )
        extract_and_publish(
            PaperSources(pdf), staging, settings, library, request_id=f"r{index}"
        )
    return library


def test_shards_come_from_validated_fields() -> None:
    assert shard_for("flat", validated=True, year=2020, family="A") == ""
    assert shard_for("by-initial", validated=True, year=None, family="Łukasz") == "L"
    assert shard_for("by-initial", validated=True, year=None, family="123") == (
        "Unverified"
    )
    assert shard_for("by-initial", validated=False, year=None, family="B") == (
        "Unverified"
    )
    with pytest.raises(ValueError, match="Unknown library layout"):
        shard_for("by-colour", validated=True, year=None, family=None)


def test_layouts_move_papers_and_new_papers_follow_them(
    tmp_path: Path,
    pdf_builder: Callable[..., bytes],
    native_worker: Callable[[Path, str], Path],
    synthetic_lookup: Lookup,
) -> None:
    library = publish_two(tmp_path, pdf_builder, native_worker, synthetic_lookup)
    names = sorted(str(row["directory"]) for row in read_catalog(library))
    assert names[0] == "Author_2020_SyntheticPaper"
    moves = plan_organize(library, "by-year")
    assert {(m.old, m.new) for m in moves} == {
        ("Author_2020_SyntheticPaper", "2020/Author_2020_SyntheticPaper"),
        (names[1], f"Unverified/{names[1]}"),
    }
    assert organize_library(library, "by-year") == moves
    corpus = json.loads((library / "corpus.json").read_text())
    assert corpus["layout"] == "by-year"
    assert sorted(str(r["directory"]) for r in read_catalog(library)) == sorted(
        m.new for m in moves
    )
    assert [p.relative_to(library).as_posix() for p in paper_directories(library)] == [
        "2020/Author_2020_SyntheticPaper",
        f"Unverified/{names[1]}",
    ]
    # A rebuilt paper is replaced where it lies; a new identity moves it.
    unverified = library / "Unverified" / names[1]
    run = tmp_path / "rebuild"
    run.mkdir()
    stage_from_paper(unverified, run)
    again = publish(run, library, replace=f"Unverified/{names[1]}")
    assert again.name == f"Unverified/{names[1]}"
    identity = json.loads(
        (library / "2020" / "Author_2020_SyntheticPaper" / "diagnostics" / "raw")
        .joinpath(
            next(
                (
                    library
                    / "2020"
                    / "Author_2020_SyntheticPaper"
                    / "diagnostics"
                    / "raw"
                ).iterdir()
            ).name,
            "identity.json",
        )
        .read_text()
    )
    run = tmp_path / "rename"
    run.mkdir()
    stage_from_paper(unverified, run)
    (run / "identity.json").write_text(json.dumps(identity))
    renamed = publish(run, library, replace=f"Unverified/{names[1]}")
    assert renamed.name.startswith("2020/Author_2020_SyntheticPaper_")
    assert not (library / "Unverified").exists()
    assert organize_library(library, "by-year") == []


def test_conflicts_and_interruptions(
    tmp_path: Path,
    pdf_builder: Callable[..., bytes],
    native_worker: Callable[[Path, str], Path],
    synthetic_lookup: Lookup,
) -> None:
    library = publish_two(tmp_path, pdf_builder, native_worker, synthetic_lookup)
    (library / "2020" / "Author_2020_SyntheticPaper").mkdir(parents=True)
    with pytest.raises(PublicationConflictError, match="already exists"):
        organize_library(library, "by-year")
    (library / "2020" / "Author_2020_SyntheticPaper").rmdir()
    # An organize interrupted after its journal is completed on recovery.
    moves = plan_organize(library, "by-initial")
    journal = library / INTERNAL_DIRECTORY / "journal" / ORGANIZE_JOURNAL
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(
        json.dumps(
            {
                "schema": "paperextract.organize",
                "schema_version": 1,
                "layout": "by-initial",
                "moves": [{"old": m.old, "new": m.new} for m in moves],
            }
        )
    )
    (library / moves[0].old).rename(library / "moved-by-hand")
    assert recover_library(library) == ["completed an interrupted organize"]
    assert not journal.exists()
    assert ensure_library(library)["layout"] == "by-initial"
    # A directory that vanished from its old place is skipped, not invented.
    assert (library / moves[1].new).is_dir()
    assert not (library / moves[0].new).exists()
    assert (library / "moved-by-hand").is_dir()
