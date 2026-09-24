"""Describe figures of staged papers, publish the descriptions and index them."""

import json
import sqlite3
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

import paperextract.describe_stage
from paperextract.catalog import read_catalog
from paperextract.describe import (
    DESCRIPTION_SET_SCHEMA,
    MACHINE_NOTICE,
    description_path,
    read_descriptions,
)
from paperextract.describe_stage import describe_staging, pending_figures
from paperextract.describers import DescribeRequest, ModelReply
from paperextract.export import DESCRIPTION_BEGIN, DESCRIPTION_END
from paperextract.index import INDEX_PATH, refresh_index, search
from paperextract.pipeline import ExtractionSettings, PaperSources, rebuild_document
from paperextract.protocol import MineruProfile
from paperextract.reprocess import stage_from_paper
from paperextract.storage import extract_and_publish, publish
from paperextract.worker import WorkerEnvironment

MAIN_FIGURE = "fig_f3255fc605cb"
ANSWER = json.dumps(
    {
        "figure_type": "line plot",
        "summary": "Zebrafish traces over time.",
        "panels": [
            {
                "label": "a",
                "kind": "line plot",
                "features": ["peak near 10"],
                "printed_text": ["Body"],
            }
        ],
        "relationships": ["panels share the time axis"],
    }
)
NOW = "2026-09-23T00:00:00+00:00"


class FakeDescriber:
    """Answer every request with a function of the request."""

    def __init__(self, answer: Callable[[DescribeRequest], ModelReply]) -> None:
        self.answer = answer
        self.batches: list[list[str]] = []

    @property
    def model(self) -> dict[str, object]:
        return {
            "repository": "org/fake",
            "revision": "r1",
            "runtime": "fake",
            "label": "Fake",
        }

    @property
    def sampling(self) -> dict[str, object]:
        return {"temperature": 0.0}

    def describe(self, requests: Sequence[DescribeRequest]) -> list[ModelReply]:
        self.batches.append([request.figure_id for request in requests])
        return [self.answer(request) for request in requests]


def good(_request: DescribeRequest) -> ModelReply:
    return ModelReply(ANSWER, 1.5, 100, 20, "stop")


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
    settings = ExtractionSettings(
        environment=WorkerEnvironment(
            Path(sys.executable), native_worker(tmp_path / "worker.py", "ok")
        ),
        model_dir=tmp_path / "models",
        profile=MineruProfile(cpu_threads=1),
        timeout_seconds=60,
    )
    root = tmp_path / "library"
    _bundle, published = extract_and_publish(
        PaperSources(paper, (supplement,)), staging, settings, root, request_id="first"
    )
    assert published is not None
    return root


def paper_of(library: Path) -> Path:
    (row,) = read_catalog(library)
    return library / str(row["directory"])


def staged(library: Path, tmp_path: Path, name: str = "stage") -> Path:
    run = tmp_path / name
    run.mkdir()
    stage_from_paper(paper_of(library), run)
    return run


def test_figures_are_described_once_per_model_prompt_and_image(
    library: Path, tmp_path: Path
) -> None:
    run = staged(library, tmp_path)
    describer = FakeDescriber(good)
    report = describe_staging(run, describer, clock=lambda: NOW)
    # Paper and supplement each have three figures with geometry and one without.
    assert (report.described, report.skipped, report.unrenderable) == (6, 0, 2)
    assert (report.failed, report.recorded, report.errors) == (0, 6, ())
    (record,) = read_descriptions(description_path(run, MAIN_FIGURE))
    assert record["created_utc"] == NOW
    assert record["machine_generated"] is True
    assert record["notice"] == MACHINE_NOTICE
    assert record["prompt_version"] == "figure-claims-v2"
    assert record["model"] == describer.model
    check = record["printed_check"]
    assert isinstance(check, dict) and check["claimed"] == 1
    assert len(list((run / "supplements" / "01" / "descriptions").iterdir())) == 3
    again = describe_staging(run, describer, clock=lambda: NOW)
    assert (again.described, again.skipped) == (0, 6)
    assert describer.batches[1:] == []
    forced = describe_staging(run, describer, force=True, clock=lambda: NOW)
    assert forced.described == 6
    assert len(read_descriptions(description_path(run, MAIN_FIGURE))) == 2
    other = describe_staging(
        run, describer, prompt_version="figure-claims-v1", clock=lambda: NOW
    )
    assert other.described == 6


def test_failed_requests_and_answers_are_reported(
    library: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = staged(library, tmp_path)

    def answer(request: DescribeRequest) -> ModelReply:
        if request.figure_id == MAIN_FIGURE:
            return ModelReply("", 0.5, error="server busy", usd=0.25)
        return ModelReply("not json", 1.0, finish_reason="length")

    report = describe_staging(run, FakeDescriber(answer), clock=lambda: NOW)
    assert (report.described, report.failed, report.recorded) == (0, 6, 5)
    assert report.usd == 0.25
    assert f"{MAIN_FIGURE}: server busy" in report.errors
    assert sum("no complete JSON object" in error for error in report.errors) == 5
    assert not description_path(run, MAIN_FIGURE).exists()

    def broken(*_args: object, **_kwargs: object) -> object:
        raise ValueError("region outside the page")

    monkeypatch.setattr(paperextract.describe_stage, "render_region_png", broken)
    pending, skipped, unrenderable = pending_figures(run, {"repository": "x"})
    assert (pending, skipped, unrenderable) == ([], 0, 8)


def test_descriptions_are_published_labelled_kept_and_indexed(
    library: Path, tmp_path: Path
) -> None:
    run = staged(library, tmp_path)
    describe_staging(run, FakeDescriber(good), clock=lambda: NOW)
    # A description of a figure that a rebuild no longer produces.
    (run / "descriptions" / "fig_gone.json").write_text("{}")
    paper = publish(run, library, replace=paper_of(library).name).directory
    kept = paper / "descriptions" / f"{MAIN_FIGURE}.json"
    assert json.loads(kept.read_text())["schema"] == DESCRIPTION_SET_SCHEMA
    manifest = json.loads((paper / "manifest.json").read_text())
    assert f"descriptions/{MAIN_FIGURE}.json" in {f["path"] for f in manifest["files"]}
    assert not (paper / "descriptions" / "fig_gone.json").exists()
    notes = json.loads((paper / "extraction.json").read_text())["asset_notes"]
    assert any("fig_gone.json not published" in note for note in notes)
    markdown = (paper / "paper.md").read_text()
    start, end = markdown.index(DESCRIPTION_BEGIN), markdown.index(DESCRIPTION_END)
    block = markdown[start:end]
    assert (
        "> *Machine-generated visual description (Fake), not from the paper:* "
        "Zebrafish traces over time." in block
    )
    assert "> - across panels: panels share the time axis" in block
    assert markdown.index("**Published caption:**") < markdown.index(DESCRIPTION_BEGIN)
    assert DESCRIPTION_BEGIN in (paper / "supplement_01" / "supplement.md").read_text()

    refresh_index(library)
    (hit,) = search([library], "zebrafish")
    assert hit.in_description is True
    assert "[Zebrafish]" in hit.snippet
    (body,) = search([library], "second")
    assert body.in_description is False
    # An index from before descriptions were indexed is rebuilt on use.
    connection = sqlite3.connect(library / INDEX_PATH)
    connection.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
    connection.commit()
    connection.close()
    assert search([library], "zebrafish")[0].in_description is True

    rebuilt = staged(library, tmp_path, "rebuild")
    assert (rebuilt / "descriptions" / f"{MAIN_FIGURE}.json").is_file()
    rebuild_document(rebuilt)
    rebuild_document(rebuilt / "supplements" / "01")
    again = publish(rebuilt, library, replace=paper.name).directory
    assert (again / "descriptions" / f"{MAIN_FIGURE}.json").read_bytes() == (
        kept.read_bytes()
    )
    assert DESCRIPTION_BEGIN in (again / "paper.md").read_text()


def test_unparsed_descriptions_are_kept_but_not_rendered(
    library: Path, tmp_path: Path
) -> None:
    run = staged(library, tmp_path)
    describe_staging(
        run, FakeDescriber(lambda _r: ModelReply("no json", 1.0)), clock=lambda: NOW
    )
    paper = publish(run, library, replace=paper_of(library).name).directory
    assert (paper / "descriptions" / f"{MAIN_FIGURE}.json").is_file()
    assert DESCRIPTION_BEGIN not in (paper / "paper.md").read_text()
