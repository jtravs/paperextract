"""Drive the command line end to end with a stand-in worker."""

import importlib
import json
import runpy
import shutil
import signal
import sys
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

import paperextract.cli
import paperextract.config
from conftest import fake_download, manifest_document, served_files
from paperextract.acquire import Acquisition, AcquisitionError
from paperextract.cli import (
    EXIT_CANCELLED,
    EXIT_CONFLICT,
    EXIT_FAILURE,
    EXIT_OK,
    EXIT_PARTIAL,
    EXIT_USAGE,
    RESULT_SCHEMA,
    ItemResult,
    build_parser,
    exit_code,
    main,
    parse_pages,
)
from paperextract.describers import DescribeRequest, DescriberError, ModelReply
from paperextract.storage import (
    CATALOG_FILENAME,
    CORPUS_FILENAME,
    PublicationConflictError,
    read_catalog,
)

# About 300 characters per page, enough for the content-equivalence check.
PAGE_TEXT = "Pulse compression in hollow capillary fibres " * 7


class Cli:
    """Run the command line inside one temporary workspace."""

    def __init__(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        native_worker: Callable[[Path, str], Path],
        behaviour: str = "ok",
    ) -> None:
        self.tmp_path = tmp_path
        self.capsys = capsys
        self.root = tmp_path / "checkout"
        worker = self.root / "workers" / "mineru"
        native_worker(worker / "paperextract_mineru_worker.py", behaviour)
        python = worker / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True, exist_ok=True)
        if not python.exists():
            python.symlink_to(sys.executable)
        (self.root / "model-cache" / "mineru" / "models").mkdir(
            parents=True, exist_ok=True
        )
        self.config = tmp_path / "paperextract.toml"
        self.config.write_text(
            f'[worker]\nroot = "{self.root}"\ntimeout_seconds = 60\ncpu_threads = 1\n'
            "[registry]\noffline = true\n"
        )
        self.library = tmp_path / "literature"

    def __call__(self, *argv: str) -> tuple[int, str, str]:
        self.capsys.readouterr()
        code = main(
            [*argv, "--config", str(self.config)],
            environ={"HOME": str(self.tmp_path / "home")},
        )
        out, err = self.capsys.readouterr()
        return code, out, err

    def json(self, *argv: str) -> tuple[int, dict[str, object]]:
        code, out, _err = self(*argv, "--json")
        return code, json.loads(out)


@pytest.fixture
def cli(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    native_worker: Callable[[Path, str], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> Cli:
    monkeypatch.chdir(tmp_path)
    return Cli(tmp_path, capsys, native_worker)


def make_pdf(
    directory: Path, name: str, pdf_builder: Callable[..., bytes], **info: str
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    marker = info.pop("marker", "")
    path.write_bytes(
        pdf_builder([PAGE_TEXT + marker, "Second doi:10.1000/z", None, None], **info)
    )
    return path


def items(document: dict[str, object]) -> list[dict[str, object]]:
    value = document["items"]
    assert isinstance(value, list)
    return value  # type: ignore[return-value]


def test_shorthand_extract_publishes_and_removes_the_run(
    cli: Cli, pdf_builder: Callable[..., bytes]
) -> None:
    pdf = make_pdf(cli.tmp_path / "in", "paper.pdf", pdf_builder)
    code, out, err = cli(str(pdf))
    assert code == EXIT_OK
    assert out.startswith("published\tpaper.pdf\tUnverified_")
    assert "\tUNVERIFIED\tPARTIAL\t" in out
    assert f"library\t{cli.library.resolve()} (default)" in out
    assert out.endswith("1 published\n")
    assert "[1/1] paper.pdf" in err
    rows = read_catalog(cli.library)
    assert len(rows) == 1
    assert (cli.library / str(rows[0]["directory"]) / "paper.md").is_file()
    assert not any((cli.library / ".paperextract" / "runs").iterdir())
    snapshots = cli.library / ".paperextract" / "registry-snapshots"
    assert not snapshots.exists() or not any(snapshots.rglob("*.json"))


def test_json_result_is_versioned_and_warns_under_strict(
    cli: Cli, pdf_builder: Callable[..., bytes]
) -> None:
    pdf = make_pdf(cli.tmp_path / "in", "paper.pdf", pdf_builder)
    code, document = cli.json("extract", str(pdf), "--strict", "--keep-run")
    assert code == EXIT_PARTIAL
    assert document["schema"] == RESULT_SCHEMA
    assert document["summary"] == {"published": 1}
    configuration = document["configuration"]
    assert isinstance(configuration, dict)
    assert configuration["offline"] is True
    assert configuration["origins"]["worker_root"] == "config"  # type: ignore[index]
    (item,) = items(document)
    assert item["warnings"] == ["identity UNVERIFIED", "partial page coverage"]
    assert item["findings"]
    assert len(list((cli.library / ".paperextract" / "runs").iterdir())) == 1


def test_a_second_extraction_of_the_same_bytes_is_skipped(
    cli: Cli, pdf_builder: Callable[..., bytes]
) -> None:
    pdf = make_pdf(cli.tmp_path / "in", "paper.pdf", pdf_builder)
    assert cli("extract", str(pdf), "--no-registry")[0] == EXIT_OK
    copy = cli.tmp_path / "elsewhere" / "renamed.pdf"
    copy.parent.mkdir()
    shutil.copyfile(pdf, copy)
    code, out, _err = cli("extract", str(copy), "-q")
    assert code == EXIT_OK
    assert out.startswith("skipped\trenamed.pdf\tUnverified_")
    assert "identical bytes already in the library" in out
    assert len(read_catalog(cli.library)) == 1


def test_worker_failures_keep_the_run_and_exit_five(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    native_worker: Callable[[Path, str], Path],
    pdf_builder: Callable[..., bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    pdf = make_pdf(tmp_path / "in", "paper.pdf", pdf_builder)
    for behaviour, message in (
        ("failed", "RuntimeError: boom"),
        ("crash", "WorkerProcessError: Worker exited with status 3"),
    ):
        cli = Cli(tmp_path, capsys, native_worker, behaviour)
        code, out, _err = cli("extract", str(pdf), "-v")
        assert code == EXIT_FAILURE
        assert out.startswith(f"failed\tpaper.pdf\textraction\t{message}")
        assert "\trun kept: " in out
    runs = list((tmp_path / "literature" / ".paperextract" / "runs").iterdir())
    assert len(runs) == 2
    assert not (tmp_path / "literature" / CATALOG_FILENAME).exists()


def test_batch_applies_every_duplicate_tier(
    cli: Cli, pdf_builder: Callable[..., bytes]
) -> None:
    incoming = cli.tmp_path / "incoming"
    first = make_pdf(incoming, "a.pdf", pdf_builder)
    shutil.copyfile(first, incoming / "a copy.pdf")
    make_pdf(incoming, "b.pdf", pdf_builder, title="Re-saved download")
    make_pdf(incoming, "c.pdf", pdf_builder, marker=" distinct")
    (incoming / "notes.txt").write_text("not a paper")
    stray = cli.tmp_path / "stray.pdf"
    stray.write_text("not a pdf")
    code, document = cli.json("batch", str(incoming), str(stray))
    assert code == EXIT_PARTIAL
    assert document["summary"] == {"attached": 1, "published": 2, "rejected": 1}
    by_name = {Path(str(item["path"])).name: item for item in items(document)}
    assert by_name["stray.pdf"]["status"] == "rejected"
    assert by_name["a copy.pdf"]["status"] == "published"
    assert [Path(str(p)).name for p in by_name["a copy.pdf"]["aliases"]] == [  # type: ignore[union-attr]
        "a.pdf"
    ]
    # The re-saved copy is preserved in the paper it duplicates.
    assert by_name["b.pdf"]["status"] == "attached"
    paper = cli.library / str(by_name["a copy.pdf"]["directory"])
    assert by_name["b.pdf"]["directory"] == by_name["a copy.pdf"]["directory"]
    extraction = json.loads((paper / "extraction.json").read_text())
    (copy,) = [s for s in extraction["sources"] if s.get("role") == "equivalent_copy"]
    assert (paper / copy["path"]).read_bytes() == (incoming / "b.pdf").read_bytes()
    assert by_name["c.pdf"]["status"] == "published"
    records = list((cli.library / ".paperextract" / "batches").iterdir())
    assert len(records) == 1
    assert json.loads(records[0].read_text())["summary"] == document["summary"]
    code, out, _err = cli("batch", str(incoming))
    assert code == EXIT_OK
    assert out.count("skipped\t") == 3
    assert "alias\ta.pdf" in out
    # A later copy of a library paper is added to it without extraction.
    later = make_pdf(cli.tmp_path / "later", "d.pdf", pdf_builder, title="Again")
    code, document = cli.json("batch", str(later.parent))
    assert code == EXIT_OK
    (item,) = items(document)
    assert (item["status"], item["directory"]) == (
        "attached",
        by_name["a copy.pdf"]["directory"],
    )
    extraction = json.loads((paper / "extraction.json").read_text())
    roles = [s.get("role") for s in extraction["sources"]]
    assert roles.count("equivalent_copy") == 2
    # A rebuild keeps both copies.
    assert cli("reprocess", str(by_name["a copy.pdf"]["directory"]))[0] == EXIT_OK
    kept = json.loads((paper / "extraction.json").read_text())
    assert [s.get("role") for s in kept["sources"]] == roles


def test_publish_reuses_a_kept_run(cli: Cli, pdf_builder: Callable[..., bytes]) -> None:
    pdf = make_pdf(cli.tmp_path / "in", "paper.pdf", pdf_builder)
    assert cli("extract", str(pdf), "--keep-run")[0] == EXIT_OK
    (run,) = (cli.library / ".paperextract" / "runs").iterdir()
    code, out, _err = cli("publish", str(run))
    assert code == EXIT_CONFLICT
    assert out.startswith("conflict\t")
    other = cli.tmp_path / "other"
    code, document = cli.json(
        "publish", str(run), "--library", str(other), "--refresh-identity"
    )
    assert code == EXIT_OK
    (item,) = items(document)
    assert item["identity"] == "UNVERIFIED"
    assert (other / CORPUS_FILENAME).is_file()
    code, document = cli.json(
        "publish", str(run), "--library", str(cli.tmp_path / "third"), "--no-registry"
    )
    assert code == EXIT_OK
    (run / "source.json").unlink()
    code, out, _err = cli("publish", str(run), "--library", str(cli.tmp_path / "x"))
    assert code == EXIT_FAILURE
    assert out.startswith("failed\t")
    assert "FileNotFoundError" in out


def test_publish_without_identity_publishes_unverified(
    cli: Cli, pdf_builder: Callable[..., bytes]
) -> None:
    pdf = make_pdf(cli.tmp_path / "in", "paper.pdf", pdf_builder)
    assert cli("extract", str(pdf), "--keep-run", "--no-registry")[0] == EXIT_OK
    (run,) = (cli.library / ".paperextract" / "runs").iterdir()
    assert not (run / "identity.json").exists()
    code, document = cli.json(
        "publish", str(run), "--library", str(cli.tmp_path / "b"), "--no-registry"
    )
    assert code == EXIT_OK
    assert items(document)[0]["identity"] == "UNVERIFIED"


def test_dedup_reports_without_changing_anything(
    cli: Cli, pdf_builder: Callable[..., bytes]
) -> None:
    incoming = cli.tmp_path / "incoming"
    first = make_pdf(incoming, "a.pdf", pdf_builder)
    assert cli("extract", str(first), "--no-registry")[0] == EXIT_OK
    shutil.copyfile(first, incoming / "a copy.pdf")
    make_pdf(incoming, "b.pdf", pdf_builder, title="Re-saved download")
    make_pdf(incoming, "c.pdf", pdf_builder, marker=" distinct")
    (incoming / "d.pdf").write_bytes(b"%PDF-1.4\nbroken")
    report = cli.tmp_path / "dedup.json"
    before = sorted(p.name for p in cli.library.rglob("*"))
    code, out, _err = cli("dedup", str(incoming), "--report", str(report))
    assert code == EXIT_OK
    lines = out.splitlines()
    assert lines[0].startswith("in library\ta copy.pdf\tUnverified_")
    assert "identical\ta.pdf\tsame bytes as a copy.pdf" in lines
    assert any(
        line.startswith("same text\tb.pdf\tidentical text to Unverified_")
        for line in lines
    )
    assert (
        "shared DOI\t10.1000/z\ta copy.pdf, c.pdf; review, each is extracted" in lines
    )
    assert any(line.startswith("rejected\td.pdf\t") for line in lines)
    assert lines[-1] == (
        "5 files, 3 distinct, 1 identical copies, 1 in library, 1 same text, "
        "0 supplements, 1 rejected; to extract: 1"
    )
    written = json.loads(report.read_text())
    assert written["schema"] == "paperextract.dedup-report"
    assert written["summary"]["to_extract"] == 1
    assert sorted(p.name for p in cli.library.rglob("*")) == before
    code, out, _err = cli("dedup", str(incoming), "--report", str(report))
    assert code == EXIT_USAGE
    code, document = cli.json("dedup", str(incoming))
    assert code == EXIT_OK
    assert document["library"] == str(cli.library.resolve())


def test_deleted_paper_directories_are_not_treated_as_held(
    cli: Cli, pdf_builder: Callable[..., bytes]
) -> None:
    pdf = make_pdf(cli.tmp_path / "in", "paper.pdf", pdf_builder)
    assert cli("extract", str(pdf), "--no-registry")[0] == EXIT_OK
    (row,) = read_catalog(cli.library)
    shutil.rmtree(cli.library / str(row["directory"]))
    code, out, _err = cli("dedup", str(pdf))
    assert code == EXIT_OK
    assert out.splitlines()[-1].endswith(
        "0 in library, 0 same text, 0 supplements, 0 rejected; to extract: 1"
    )


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["extract", "a.pdf", "b.pdf"], "--supplement"),
        (["extract", "."], "is a directory"),
        (["extract", "missing.pdf"], "not a regular file"),
        (["batch", "missing"], "Not found: missing"),
        (["dedup", "missing"], "Not found: missing"),
        (["publish", "."], "No completed extraction"),
        (["extract", "a.pdf", "--timeout", "0"], "--timeout must be positive"),
    ],
)
def test_usage_errors_exit_two(cli: Cli, argv: list[str], message: str) -> None:
    code, out, err = cli(*argv)
    assert code == EXIT_USAGE
    assert out == ""
    assert message in err


def test_setup_problems_are_usage_errors(
    cli: Cli, pdf_builder: Callable[..., bytes]
) -> None:
    pdf = make_pdf(cli.tmp_path / "in", "paper.pdf", pdf_builder)
    code, _out, err = cli("extract", str(pdf), "--models", "absent")
    assert code == EXIT_USAGE
    assert "Model directory not found" in err
    assert not cli.library.exists()
    cli.library.mkdir()
    (cli.library / CORPUS_FILENAME).write_text('{"schema": "other"}')
    code, _out, err = cli("extract", str(pdf))
    assert code == EXIT_USAGE
    assert "is not a paperextract.corpus document" in err


def test_parser_errors_and_help_return_codes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--help"]) == EXIT_OK
    assert "Exit codes: 0 completed" in capsys.readouterr().out.replace("\n", " ")
    assert main(["extract", "a.pdf", "--pages", "3-1"]) == EXIT_USAGE
    assert main([]) == EXIT_USAGE
    assert "invalid page selection" in capsys.readouterr().err


def test_parser_accepts_an_explicit_separator() -> None:
    args = build_parser().parse_args(["extract", "--", "status"])
    assert args.sources == [Path("status")]


@pytest.mark.parametrize(
    ("text", "pages"),
    [("1", (1,)), ("1-3,15", (1, 2, 3, 15)), (" 2 , 4-5", (2, 4, 5))],
)
def test_page_selections_parse(text: str, pages: tuple[int, ...]) -> None:
    assert parse_pages(text) == pages


@pytest.mark.parametrize("text", ["", "0", "a", "3-1", "1,1", "2,1", "1-"])
def test_bad_page_selections_are_rejected(text: str) -> None:
    with pytest.raises(Exception, match="page"):
        parse_pages(text)


@pytest.mark.parametrize(
    ("statuses", "strict", "code"),
    [
        ([], False, EXIT_OK),
        (["published", "skipped", "held"], False, EXIT_OK),
        (["held"], True, EXIT_PARTIAL),
        (["published", "failed"], False, EXIT_PARTIAL),
        (["conflict", "conflict"], False, EXIT_CONFLICT),
        (["conflict", "failed"], False, EXIT_FAILURE),
        (["rejected"], False, EXIT_FAILURE),
    ],
)
def test_exit_codes_follow_the_plan(
    statuses: list[str], strict: bool, code: int
) -> None:
    results = [ItemResult(path="p", status=status) for status in statuses]
    assert exit_code(results, strict=strict) == code


def test_validated_complete_items_have_no_warnings() -> None:
    item = ItemResult(
        path="p", status="published", identity="VALIDATED", processing="COMPLETE"
    )
    assert item.warnings() == ()


def test_cancellation_exits_130(
    cli: Cli, pdf_builder: Callable[..., bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = make_pdf(cli.tmp_path / "in", "paper.pdf", pdf_builder)

    def cancel(*_args: object) -> list[ItemResult]:
        raise KeyboardInterrupt

    monkeypatch.setattr(paperextract.cli, "_run_plan", cancel)
    code, _out, err = cli("extract", str(pdf))
    assert code == EXIT_CANCELLED
    assert "Cancelled" in err
    assert signal.getsignal(signal.SIGTERM) is not paperextract.cli._terminate  # pyright: ignore[reportPrivateUsage]


def test_termination_signals_become_cancellation() -> None:
    with pytest.raises(KeyboardInterrupt, match="signal 15"):
        paperextract.cli._terminate(signal.SIGTERM, None)  # pyright: ignore[reportPrivateUsage]


def test_missing_checkout_is_explained(
    cli: Cli, pdf_builder: Callable[..., bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_checkout(_path: Path) -> None:
        return None

    monkeypatch.setattr(paperextract.config, "checkout_root", no_checkout)
    cli.config.write_text("")
    pdf = make_pdf(cli.tmp_path / "in", "paper.pdf", pdf_builder)
    code, _out, err = cli("extract", str(pdf))
    assert code == EXIT_USAGE
    assert "No worker checkout is known" in err


def test_module_and_console_entry_points_exit_with_the_status(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["paperextract", "--help"])
    monkeypatch.delitem(sys.modules, "paperextract.__main__", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("paperextract", run_name="__main__")
    assert excinfo.value.code == EXIT_OK
    assert "usage: paperextract" in capsys.readouterr().out


def test_importing_the_module_entry_point_runs_nothing() -> None:
    module = importlib.import_module("paperextract.__main__")
    assert module.main_entry is paperextract.cli.main_entry


def test_a_conflict_during_extraction_exits_three(
    cli: Cli, pdf_builder: Callable[..., bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = make_pdf(cli.tmp_path / "in", "paper.pdf", pdf_builder)

    def conflict(*_args: object) -> None:
        raise PublicationConflictError("Unverified_x appeared during publication")

    monkeypatch.setattr(paperextract.cli, "publish", conflict)
    code, out, _err = cli("extract", str(pdf))
    assert code == EXIT_CONFLICT
    assert out.startswith("conflict\tpaper.pdf\tpublication\tUnverified_x appeared")
    assert "run kept" in out


def test_extract_publishes_explicit_supplements(
    cli: Cli, pdf_builder: Callable[..., bytes]
) -> None:
    pdf = make_pdf(cli.tmp_path / "in", "paper.pdf", pdf_builder)
    supplement = make_pdf(cli.tmp_path / "in", "extra.pdf", pdf_builder, marker=" SI")
    code, out, _err = cli(
        "extract", str(pdf), "--supplement", str(supplement), "--no-table-ocr"
    )
    assert code == EXIT_OK
    assert "\nsupplement\textra.pdf\n" in out
    (row,) = read_catalog(cli.library)
    assert len(row["source_sha256"]) == 2  # type: ignore[arg-type]
    assert (
        cli.library / str(row["directory"]) / "supplement_01" / "supplement.md"
    ).is_file()
    code, document = cli.json("extract", str(supplement))
    assert code == EXIT_OK
    assert items(document)[0]["status"] == "skipped"
    configuration = document["configuration"]
    assert isinstance(configuration, dict)
    assert configuration["table_ocr"] is True


@pytest.mark.parametrize(
    ("setup", "message"),
    [("text", "no PDF signature"), ("same", "same bytes as the paper")],
)
def test_bad_supplements_are_usage_errors(
    cli: Cli, pdf_builder: Callable[..., bytes], setup: str, message: str
) -> None:
    pdf = make_pdf(cli.tmp_path / "in", "paper.pdf", pdf_builder)
    supplement = cli.tmp_path / "in" / "supp.pdf"
    if setup == "text":
        supplement.write_text("not a pdf")
    else:
        shutil.copyfile(pdf, supplement)
    code, _out, err = cli("extract", str(pdf), "--supplement", str(supplement))
    assert code == EXIT_USAGE
    assert message in err


def test_a_failed_supplement_fails_the_paper(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    native_worker: Callable[[Path, str], Path],
    pdf_builder: Callable[..., bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    cli = Cli(tmp_path, capsys, native_worker, "fail_supplement")
    pdf = make_pdf(tmp_path / "in", "paper.pdf", pdf_builder)
    supplement = make_pdf(tmp_path / "in", "extra.pdf", pdf_builder, marker=" SI")
    code, out, _err = cli("extract", str(pdf), "--supplement", str(supplement))
    assert code == EXIT_FAILURE
    assert "extraction\tsupplement extra.pdf: RuntimeError: boom" in out
    assert not (tmp_path / "literature" / CATALOG_FILENAME).exists()


def test_batch_pairs_supplements_and_holds_orphans(
    cli: Cli, pdf_builder: Callable[..., bytes]
) -> None:
    incoming = cli.tmp_path / "incoming"
    make_pdf(incoming, "soliton_paper.pdf", pdf_builder)
    make_pdf(incoming, "soliton_paper_si.pdf", pdf_builder, marker=" SI")
    orphan = incoming / "orphan_supp.pdf"
    orphan.write_bytes(pdf_builder(["Orphan text", "no identifier", None, None]))
    code, document = cli.json("batch", str(incoming))
    assert code == EXIT_OK
    assert document["summary"] == {"held": 1, "published": 1}
    by_name = {Path(str(item["path"])).name: item for item in items(document)}
    assert [Path(str(p)).name for p in by_name["soliton_paper.pdf"]["supplements"]] == [  # type: ignore[union-attr]
        "soliton_paper_si.pdf"
    ]
    assert "soliton_paper_si.pdf" not in by_name
    assert "no paper in this batch matches" in str(
        by_name["orphan_supp.pdf"]["message"]
    )
    make_pdf(incoming, "soliton_paper_supplement2.pdf", pdf_builder, marker=" v2")
    code, out, _err = cli("batch", str(incoming))
    assert code == EXIT_OK
    assert out.count("skipped\t") == 2
    assert "held\tsoliton_paper_supplement2.pdf\tUnverified_" in out
    assert "paperextract extract PAPER --supplement FILE" in out
    code, out, _err = cli("dedup", str(incoming))
    assert code == EXIT_OK
    assert "supplement?\torphan_supp.pdf\tno matching paper; held" in out
    assert "supplement\tsoliton_paper_supplement2.pdf\tof soliton_paper.pdf" in out


def test_reprocess_rebuilds_papers_without_the_backend(
    cli: Cli,
    pdf_builder: Callable[..., bytes],
    monkeypatch: pytest.MonkeyPatch,
    synthetic_lookup: Callable[[str], object],
) -> None:
    pdf = make_pdf(cli.tmp_path / "in", "paper.pdf", pdf_builder)
    supplement = make_pdf(cli.tmp_path / "in", "extra.pdf", pdf_builder, marker=" SI")
    argv = ("extract", str(pdf), "--supplement", str(supplement), "--no-registry")
    assert cli(*argv)[0] == EXIT_OK
    (row,) = read_catalog(cli.library)
    name = str(row["directory"])
    # Break the worker so any attempt to run it would fail the test.
    (cli.root / "workers" / "mineru" / "paperextract_mineru_worker.py").write_text(
        "raise SystemExit(9)"
    )
    code, out, _err = cli("reprocess", name)
    assert code == EXIT_OK
    assert out.startswith(f"republished\t{name}\t{name}\tUNVERIFIED\t")

    def lookup(_config: object) -> Callable[[str], object]:
        return synthetic_lookup

    monkeypatch.setattr(paperextract.cli, "_lookup", lookup)
    code, out, _err = cli("reprocess", "--all", "--refresh-identity")
    assert code == EXIT_OK
    assert "\tAuthor_2020_SyntheticPaper\tVALIDATED\t" in out
    assert f"\trenamed from {name}\n" in out
    renamed = cli.library / "Author_2020_SyntheticPaper"
    assert (renamed / "supplement_01" / "supplement.md").is_file()
    assert not (cli.library / name).exists()
    shutil.rmtree(
        next(
            (
                cli.library / "Author_2020_SyntheticPaper" / "diagnostics" / "raw"
            ).iterdir()
        )
    )
    code, out, _err = cli("reprocess", str(cli.library / "Author_2020_SyntheticPaper"))
    assert code == EXIT_FAILURE
    assert "reprocess\tMissingOutputError" in out
    assert "run kept" in out


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["reprocess"], "Name papers to reprocess"),
        (["reprocess", "Nope"], "Not a published paper"),
    ],
)
def test_reprocess_usage_errors(cli: Cli, argv: list[str], message: str) -> None:
    code, _out, err = cli(*argv)
    assert code == EXIT_USAGE
    assert message in err


def test_publishing_refreshes_the_index_and_queries_answer(
    cli: Cli, pdf_builder: Callable[..., bytes]
) -> None:
    pdf = make_pdf(cli.tmp_path / "in", "paper.pdf", pdf_builder)
    assert cli("extract", str(pdf), "--no-registry")[0] == EXIT_OK
    assert (cli.library / "catalog.md").is_file()
    assert (cli.library / ".paperextract" / "index.sqlite").is_file()
    code, out, _err = cli("lookup", "--file", str(pdf))
    assert code == EXIT_OK
    assert out.startswith("present\tliterature/Unverified_")
    code, document = cli.json("lookup", str(cli.library), "--doi", "10.1000/none")
    assert code == EXIT_OK
    assert document["matches"][0]["status"] == "absent"  # type: ignore[index]
    bib = cli.tmp_path / "refs.bib"
    bib.write_text("@article{k, title = {Synthetic Paper}, year = 1999}")
    code, out, _err = cli("lookup", "--bibtex", str(bib), "--title", "Synthetic Paper")
    assert out.count("candidate\t") == 2
    code, out, _err = cli("search", "synthetic energy")
    assert code == EXIT_OK
    assert out.startswith("Unverified_")
    assert "\n\t" in out
    code, document = cli.json("search", "synthetic", "--limit", "1")
    assert len(document["hits"]) == 1  # type: ignore[arg-type]
    (cli.library / "catalog.jsonl").write_text("")
    journal = cli.library / ".paperextract" / "journal"
    journal.mkdir()
    (journal / "g.json").write_text(
        json.dumps({"old": "a", "new": "b", "build": "c", "retired": "d"})
    )
    code, out, err = cli("index", "rebuild")
    assert code == EXIT_OK
    assert out.startswith("indexed\t1 papers\n")
    assert "closed finished replacement of a" in err
    assert len(read_catalog(cli.library)) == 1


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["lookup"], "Give --doi, --arxiv, --title, --file or --bibtex"),
        (["lookup", "--doi", "nonsense"], "Not a DOI"),
        (["lookup", "--file", "absent.pdf"], "Not found"),
        (["lookup", "--bibtex", "absent.bib"], "Not found"),
        (["lookup", "elsewhere", "--doi", "10.1000/x"], "Not a library"),
        (["search", "x", "--limit", "0"], "--limit must be positive"),
        (["index", "rebuild"], "Not a library"),
    ],
)
def test_query_usage_errors(cli: Cli, argv: list[str], message: str) -> None:
    if argv[0] != "index":
        cli.library.mkdir()
        (cli.library / "corpus.json").write_text('{"schema": "paperextract.corpus"}')
    code, _out, err = cli(*argv)
    assert code == EXIT_USAGE
    assert message in err


DESCRIBED = json.dumps(
    {"summary": "Quokka traces.", "panels": [{"label": "a", "kind": "plot"}]}
)


class StandInDescriber:
    """Describe every figure the same way, or fail as instructed."""

    def __init__(self, text: str = DESCRIBED, error: str | None = None) -> None:
        self.text = text
        self.error = error

    @property
    def model(self) -> dict[str, object]:
        return {"repository": "Qwen/Qwen3.8-27B", "revision": "1d4bf0f2ff60"}

    @property
    def sampling(self) -> dict[str, object]:
        return {}

    def describe(self, requests: Sequence[DescribeRequest]) -> list[ModelReply]:
        if self.error == "server":
            raise DescriberError("No model server at http://127.0.0.1:8000")
        return [
            ModelReply(self.text, 1.0, usd=0.01, error=self.error) for _ in requests
        ]


def test_describe_adds_descriptions_once_and_search_labels_them(
    cli: Cli, pdf_builder: Callable[..., bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = make_pdf(cli.tmp_path / "in", "paper.pdf", pdf_builder)
    assert cli("extract", str(pdf), "--no-registry")[0] == EXIT_OK
    (row,) = read_catalog(cli.library)
    name = str(row["directory"])
    code, out, _err = cli("describe", name, "--dry-run")
    assert code == EXIT_OK
    assert "dry run: 3 to describe, 0 already described, 1 without an image" in out
    describer = StandInDescriber()

    def build(_config: object, _environ: object) -> StandInDescriber:
        return describer

    monkeypatch.setattr(paperextract.config.Configuration, "describer", build)
    code, document = cli.json("describe", "--all")
    assert code == EXIT_OK
    (item,) = items(document)
    assert item["status"] == "republished"
    assert item["message"] == (
        "3 described, 0 already described, 0 failed, 1 without an image, $0.0300"
    )
    assert (cli.library / name / "descriptions").is_dir()
    code, out, _err = cli("search", "quokka")
    assert code == EXIT_OK
    assert "\tmachine-generated description: " in out
    assert "not from the paper:* [Quokka] traces. - (a) plot" in out
    code, out, _err = cli("describe", name)
    assert code == EXIT_OK
    assert "0 described, 3 already described" in out
    describer.error = "busy"
    code, out, _err = cli("describe", name, "--force")
    assert code == EXIT_FAILURE
    assert "3 failed" in out
    describer.error = "server"
    code, out, _err = cli("describe", name, "--force")
    assert code == EXIT_FAILURE
    assert "DescriberError: No model server" in out
    assert "run kept" in out


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["describe"], "Name papers to describe"),
        (["describe", "Nope"], "Not a published paper"),
        (["describe", "--all", "--max-usd", "0"], "--max-usd must be positive"),
    ],
)
def test_describe_usage_errors(cli: Cli, argv: list[str], message: str) -> None:
    code, _out, err = cli(*argv)
    assert code == EXIT_USAGE
    assert message in err


def test_extract_can_select_docling_and_the_table_check(
    cli: Cli,
    pdf_builder: Callable[..., bytes],
    docling_worker: Callable[[Path, str], Path],
) -> None:
    worker = cli.root / "workers" / "docling"
    docling_worker(worker / "paperextract_docling_worker.py", "ok")
    python = worker / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    (cli.root / "model-cache" / "docling").mkdir(parents=True)
    pdf = make_pdf(cli.tmp_path / "in", "paper.pdf", pdf_builder)
    code, document = cli.json("extract", str(pdf), "--table-check")
    assert code in (EXIT_OK, EXIT_PARTIAL)
    configuration = document["configuration"]
    assert isinstance(configuration, dict)
    assert (configuration["backend"], configuration["table_check"]) == ("mineru", True)
    (row,) = read_catalog(cli.library)
    paper = cli.library / str(row["directory"])
    extraction = json.loads((paper / "extraction.json").read_text())
    assert extraction["table_check"]["request"]["backend"] == "docling"
    other = make_pdf(cli.tmp_path / "in", "other.pdf", pdf_builder, marker=" other")
    code, document = cli.json("extract", str(other), "--backend", "docling")
    assert code in (EXIT_OK, EXIT_PARTIAL)
    configuration = document["configuration"]
    assert isinstance(configuration, dict)
    assert configuration["backend"] == "docling"


def page(path: Path, doi: str | None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = f'<meta name="citation_doi" content="{doi}">' if doi else ""
    path.write_text(f"<!DOCTYPE html><html><head>{meta}</head><body></body></html>")
    return path


def test_extract_preserves_a_saved_page_with_the_paper(
    cli: Cli, pdf_builder: Callable[..., bytes]
) -> None:
    pdf = make_pdf(cli.tmp_path / "in", "paper.pdf", pdf_builder)
    capture = page(cli.tmp_path / "pages" / "paper.html", "10.1000/z")
    code, out, _err = cli("extract", str(pdf), "--html", str(capture))
    assert code in (EXIT_OK, EXIT_PARTIAL)
    assert "\ncapture\tpaper.html\n" in out
    (row,) = read_catalog(cli.library)
    paper = cli.library / str(row["directory"])
    assert (paper / "original" / "source_02" / "paper.html").is_file()
    assert (paper / "html_check.json").is_file()
    code, _out, err = cli("extract", str(pdf), "--html", str(pdf))
    assert code == EXIT_USAGE
    assert "not a saved HTML page" in err


def test_batch_pairs_saved_pages_with_papers_by_doi(
    cli: Cli, pdf_builder: Callable[..., bytes]
) -> None:
    incoming = cli.tmp_path / "in"
    make_pdf(incoming, "paper.pdf", pdf_builder)
    page(incoming / "paper.html", "10.1000/Z")
    page(incoming / "nodoi.html", None)
    page(incoming / "other.html", "10.9999/none")
    (incoming / "notes.txt").write_text("not a page")
    code, document = cli.json("batch", str(incoming))
    assert code in (EXIT_OK, EXIT_PARTIAL)
    by_path = {Path(str(item["path"])).name: item for item in items(document)}
    assert by_path["paper.pdf"]["captures"] == [str(incoming / "paper.html")]
    assert by_path["nodoi.html"]["status"] == "held"
    assert by_path["nodoi.html"]["message"] == "the page declares no DOI"
    assert "no PDF in this batch prints DOI" in str(by_path["other.html"]["message"])
    # Once the paper is published, its page is held with a way to add it.
    code, document = cli.json("batch", str(incoming))
    held = {Path(str(item["path"])).name: item for item in items(document)}
    assert "reprocess" in str(held["paper.html"]["message"])
    assert held["paper.pdf"]["status"] == "skipped"


def test_reprocess_adds_a_saved_page_to_one_paper(
    cli: Cli, pdf_builder: Callable[..., bytes]
) -> None:
    pdf = make_pdf(cli.tmp_path / "in", "paper.pdf", pdf_builder)
    assert cli("extract", str(pdf))[0] in (EXIT_OK, EXIT_PARTIAL)
    (row,) = read_catalog(cli.library)
    name = str(row["directory"])
    capture = page(cli.tmp_path / "pages" / "late.mhtml.html", "10.1000/z")
    code, out, _err = cli("reprocess", name, "--html", str(capture))
    assert code in (EXIT_OK, EXIT_PARTIAL)
    assert out.startswith("republished")
    (row,) = read_catalog(cli.library)
    paper = cli.library / str(row["directory"])
    assert (paper / "original" / "source_02" / "late.mhtml.html").is_file()
    code, _out, err = cli("reprocess", "--all", "--html", str(capture))
    assert code == EXIT_USAGE
    assert "exactly one named paper" in err


def test_versions_are_asserted_kept_and_related(
    cli: Cli,
    pdf_builder: Callable[..., bytes],
    monkeypatch: pytest.MonkeyPatch,
    synthetic_lookup: Callable[[str], object],
) -> None:
    def lookup(_config: object) -> Callable[[str], object]:
        return synthetic_lookup

    monkeypatch.setattr(paperextract.cli, "_lookup", lookup)
    first = make_pdf(cli.tmp_path / "in", "paper.pdf", pdf_builder)
    assert cli("extract", str(first))[0] == EXIT_OK
    second = make_pdf(cli.tmp_path / "in", "aam.pdf", pdf_builder, marker=" AAM")
    code, _out, _err = cli(
        "extract", str(second), "--document-version", "accepted_manuscript"
    )
    assert code == EXIT_OK
    rows = {str(r["directory"]): r for r in read_catalog(cli.library)}
    assert len(rows) == 2
    new = next(name for name in rows if name != "Author_2020_SyntheticPaper")
    paper = cli.library / new
    metadata = json.loads((paper / "metadata.json").read_text())
    assert metadata["document_version"] == "accepted_manuscript"
    assert metadata["related"][0]["directory"] == "Author_2020_SyntheticPaper"
    validation = json.loads((paper / "validation.json").read_text())
    codes = [f["code"] for f in validation["relations"]["findings"]]
    assert codes == ["OTHER_VERSION_IN_LIBRARY"]
    assert (
        "# Related papers in the library"
        in (paper / "diagnostics" / "review.md").read_text()
    )
    # The assertion survives a rebuild and can be changed for one paper.
    assert cli("reprocess", new)[0] in (EXIT_OK, EXIT_PARTIAL)
    kept = json.loads((cli.library / new / "metadata.json").read_text())
    assert kept["document_version_evidence"]["asserted"] is True
    code, _out, _err = cli(
        "reprocess", new, "--document-version", "submitted_manuscript"
    )
    changed = json.loads((cli.library / new / "metadata.json").read_text())
    assert changed["document_version"] == "submitted_manuscript"
    code, _out, err = cli("reprocess", "--all", "--document-version", "preprint")
    assert code == EXIT_USAGE
    assert "exactly one named paper" in err


def test_arxiv_requests_are_downloaded_recorded_and_found(
    cli: Cli, pdf_builder: Callable[..., bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = make_pdf(cli.tmp_path / "dl", "2206.01062v2.pdf", pdf_builder)
    requests: list[str] = []

    def acquire(text: str, directory: Path, _fetch: object) -> Acquisition:
        requests.append(text)
        assert directory.name == "arxiv"
        return Acquisition(
            pdf, text, "2206.01062v2", "https://arxiv.org/pdf/2206.01062v2", "a", "t"
        )

    monkeypatch.setattr(paperextract.cli, "acquire_arxiv", acquire)
    code, _out, err = cli("extract", "arXiv:2206.01062", "--offline")
    assert code == EXIT_USAGE
    assert "network use is off" in err
    assert requests == []
    # Allow the network, with the registries replaced by nothing.
    cli.config.write_text(cli.config.read_text().replace("offline = true", ""))

    def nothing(_config: object) -> None:
        return None

    monkeypatch.setattr(paperextract.cli, "_lookup", nothing)
    monkeypatch.setattr(paperextract.cli, "_search", nothing)
    code, _out, _err = cli("extract", "arXiv:2206.01062")
    assert code in (EXIT_OK, EXIT_PARTIAL)
    assert requests == ["arXiv:2206.01062"]
    (row,) = read_catalog(cli.library)
    assert row["arxiv"] == "2206.01062v2"
    paper = cli.library / str(row["directory"])
    extraction = json.loads((paper / "extraction.json").read_text())
    assert extraction["assertions"]["acquired_from"].endswith("2206.01062v2")
    assert json.loads((paper / "metadata.json").read_text())["document_version"] == (
        "preprint"
    )
    code, out, _err = cli("lookup", "--arxiv", "arXiv:2206.01062v5")
    assert out.startswith("present\t")
    stamped = cli.tmp_path / "stamped.pdf"
    stamped.write_bytes(pdf_builder(["Preprint arXiv:2206.01062v1 [physics]", None]))
    code, out, _err = cli("lookup", "--file", str(stamped))
    assert out.startswith("related\t")

    def failing(_text: str, _directory: Path, _fetch: object) -> Acquisition:
        raise AcquisitionError("arXiv does not know 9999.99999")

    monkeypatch.setattr(paperextract.cli, "acquire_arxiv", failing)
    code, _out, err = cli("extract", "arXiv:9999.99999")
    assert code == EXIT_USAGE
    assert "does not know" in err


def test_a_manifest_names_papers_supplements_pages_and_versions(
    cli: Cli, pdf_builder: Callable[..., bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    incoming = cli.tmp_path / "in"
    make_pdf(incoming, "one.pdf", pdf_builder)
    make_pdf(incoming, "extra.pdf", pdf_builder, marker=" SI")
    page(incoming / "one.html", "10.1000/z")
    make_pdf(incoming, "two.pdf", pdf_builder, marker=" second")
    arxiv_pdf = make_pdf(cli.tmp_path / "dl", "e.pdf", pdf_builder, marker=" eprint")

    def acquire(text: str, _directory: Path, _fetch: object) -> Acquisition:
        return Acquisition(arxiv_pdf, text, "2206.01062v1", "https://x", "a", "t")

    monkeypatch.setattr(paperextract.cli, "acquire_arxiv", acquire)
    cli.config.write_text(cli.config.read_text().replace("offline = true", ""))

    def nothing(_config: object) -> None:
        return None

    monkeypatch.setattr(paperextract.cli, "_lookup", nothing)
    monkeypatch.setattr(paperextract.cli, "_search", nothing)
    manifest = incoming / "papers.jsonl"
    manifest.write_text(
        '{"schema": "paperextract.batch-manifest", "schema_version": 1}\n'
        '{"id": "one", "paper": "one.pdf", "supplements": ["extra.pdf"], '
        '"html": ["one.html"], "document_version": "accepted_manuscript"}\n'
        '{"id": "two", "paper": "two.pdf"}\n'
        '{"id": "eprint", "paper": "arXiv:2206.01062"}\n'
    )
    code, document = cli.json("batch", "--manifest", str(manifest))
    assert code in (EXIT_OK, EXIT_PARTIAL)
    by_name = {Path(str(item["path"])).name: item for item in items(document)}
    assert by_name["one.pdf"]["supplements"] == [str(incoming / "extra.pdf")]
    assert by_name["one.pdf"]["captures"] == [str(incoming / "one.html")]
    assert {by_name[name]["status"] for name in ("one.pdf", "two.pdf", "e.pdf")} == {
        "published"
    }
    paper = cli.library / str(by_name["one.pdf"]["directory"])
    extraction = json.loads((paper / "extraction.json").read_text())
    assert extraction["assertions"] == {
        "manifest_id": "one",
        "version": "accepted_manuscript",
    }
    eprint = cli.library / str(by_name["e.pdf"]["directory"])
    assert json.loads((eprint / "metadata.json").read_text())["arxiv"] == (
        "2206.01062v1"
    )
    # A rerun resumes: every published paper is skipped by its digest.
    code, document = cli.json("batch", "--manifest", str(manifest), "--shard", "1/1")
    assert {item["status"] for item in items(document)} == {"skipped"}


def test_manifest_problems_stop_the_batch_before_work(
    cli: Cli, pdf_builder: Callable[..., bytes]
) -> None:
    incoming = cli.tmp_path / "in"
    one = make_pdf(incoming, "one.pdf", pdf_builder)
    shutil.copyfile(one, incoming / "copy.pdf")
    header = '{"schema": "paperextract.batch-manifest", "schema_version": 1}\n'
    manifest = incoming / "m.jsonl"
    (incoming / "notes.pdf").write_text("not a PDF")
    manifest.write_text(header + '{"id": "n", "paper": "notes.pdf"}\n')
    code, document = cli.json("batch", "--manifest", str(manifest))
    assert items(document)[0]["status"] == "rejected"
    manifest.write_text(header + '{"id": "a", "paper": "gone.pdf"}\n')
    code, _out, err = cli("batch", "--manifest", str(manifest))
    assert (code, "gone.pdf does not exist" in err) == (EXIT_USAGE, True)
    manifest.write_text(
        header + '{"id": "a", "paper": "one.pdf"}\n{"id": "b", "paper": "copy.pdf"}\n'
    )
    code, _out, err = cli("batch", "--manifest", str(manifest))
    assert code == EXIT_USAGE
    assert "has the same bytes as a file of 'a'" in err
    manifest.write_text(
        header + '{"id": "a", "paper": "one.pdf", "html": ["copy.pdf"]}\n'
    )
    code, _out, err = cli("batch", "--manifest", str(manifest))
    assert "not a saved HTML page" in err
    for argv, message in (
        (("batch", str(incoming), "--manifest", str(manifest)), "not both"),
        (("batch", str(incoming), "--shard", "1/2"), "add --manifest"),
        (("batch",), "or --manifest"),
    ):
        code, _out, err = cli(*argv)
        assert (code, message in err) == (EXIT_USAGE, True)


def test_dedup_proposes_a_manifest_and_same_titles(
    cli: Cli, pdf_builder: Callable[..., bytes]
) -> None:
    incoming = cli.tmp_path / "in"
    make_pdf(
        incoming, "soliton_paper.pdf", pdf_builder, title="Soliton self-compression"
    )
    make_pdf(
        incoming,
        "renamed.pdf",
        pdf_builder,
        title="Soliton Self-Compression",
        marker=" x",
    )
    make_pdf(incoming, "soliton_paper_SI.pdf", pdf_builder, marker=" SI")
    page(incoming / "soliton_paper.html", "10.1000/z")
    (incoming / "unique doi.pdf").write_bytes(
        pdf_builder([PAGE_TEXT + " unique", "doi:10.5555/unique"])
    )
    (incoming / "unique-doi.pdf").write_bytes(pdf_builder([PAGE_TEXT + " again"]))
    page(incoming / "unique.html", "10.5555/unique")
    proposed = cli.tmp_path / "proposed.jsonl"
    code, out, _err = cli("dedup", str(incoming), "--manifest-out", str(proposed))
    assert code == EXIT_OK
    assert "same title?\trenamed.pdf, soliton_paper.pdf" in out
    lines = [json.loads(line) for line in proposed.read_text().splitlines()]
    entries = {entry["id"]: entry for entry in lines[1:]}
    assert entries["soliton_paper"]["supplements"] == ["in/soliton_paper_SI.pdf"]
    assert "soliton_paper_SI" not in entries
    assert entries["unique-doi"]["html"] == ["in/unique.html"]
    assert entries["unique-doi-2"]["paper"] == "in/unique-doi.pdf"
    code, _out, err = cli("dedup", str(incoming), "--manifest-out", str(proposed))
    assert (code, "already exists" in err) == (EXIT_USAGE, True)
    code, report = cli.json("dedup", str(incoming))
    assert report["same_title"][0]["library"] == []  # type: ignore[index]


def test_organize_moves_papers_that_stay_addressable_by_name(
    cli: Cli, pdf_builder: Callable[..., bytes]
) -> None:
    code, _out, err = cli("organize", "--layout", "by-year")
    assert (code, "is not a library" in err) == (EXIT_USAGE, True)
    pdf = make_pdf(cli.tmp_path / "in", "paper.pdf", pdf_builder)
    assert cli("extract", str(pdf))[0] in (EXIT_OK, EXIT_PARTIAL)
    (row,) = read_catalog(cli.library)
    name = str(row["directory"])
    code, out, _err = cli("organize", "--layout", "by-year", "--dry-run")
    assert out.splitlines() == [
        f"would move\t{name}\tUnverified/{name}",
        "1 papers would move to layout by-year",
    ]
    assert (cli.library / name).is_dir()
    code, document = cli.json("organize", "--layout", "by-year")
    assert (code, document["moves"]) == (
        EXIT_OK,
        [{"old": name, "new": f"Unverified/{name}"}],
    )
    code, out, _err = cli("reprocess", name)
    assert out.startswith(f"republished\t{name}\tUnverified/{name}\t")
    (cli.library / name).mkdir()
    code, _out, err = cli("organize", "--layout", "flat")
    assert code == EXIT_CONFLICT
    assert "already exists" in err


def test_copies_stay_held_when_their_paper_is_not_published(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    native_worker: Callable[[Path, str], Path],
    pdf_builder: Callable[..., bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "f").mkdir()
    (tmp_path / "w").mkdir()
    monkeypatch.chdir(tmp_path / "f")
    failing = Cli(tmp_path / "f", capsys, native_worker, "failed")
    incoming = failing.tmp_path / "in"
    make_pdf(incoming, "a.pdf", pdf_builder)
    make_pdf(incoming, "b.pdf", pdf_builder, title="Re-saved")
    _code, document = failing.json("batch", str(incoming))
    statuses = {Path(str(i["path"])).name: i["status"] for i in items(document)}
    assert statuses == {"a.pdf": "failed", "b.pdf": "held"}
    monkeypatch.chdir(tmp_path / "w")
    working = Cli(tmp_path / "w", capsys, native_worker)
    first = make_pdf(working.tmp_path / "in", "a.pdf", pdf_builder)
    assert working("extract", str(first))[0] == EXIT_OK
    (row,) = read_catalog(working.library)
    paper = working.library / str(row["directory"])
    shutil.rmtree(paper / "diagnostics" / "raw")
    later = make_pdf(working.tmp_path / "later", "c.pdf", pdf_builder, title="Again")
    _code, document = working.json("batch", str(later.parent))
    (item,) = items(document)
    assert item["status"] == "held"
    assert str(item["message"]).startswith("not added to the paper: MissingOutputError")


def add_worker(
    cli: Cli, backend: str, writer: Callable[[Path, str], Path], behaviour: str = "ok"
) -> None:
    worker = cli.root / "workers" / backend
    writer(worker / f"paperextract_{backend}_worker.py", behaviour)
    python = worker / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    if not python.exists():
        python.symlink_to(sys.executable)
    (cli.root / "model-cache" / backend).mkdir(parents=True, exist_ok=True)


def test_compare_extracts_one_pdf_with_several_backends(
    cli: Cli,
    pdf_builder: Callable[..., bytes],
    docling_worker: Callable[[Path, str], Path],
    marker_worker: Callable[[Path, str], Path],
) -> None:
    pdf = make_pdf(cli.tmp_path / "in", "paper.pdf", pdf_builder)
    add_worker(cli, "docling", docling_worker)
    out = cli.tmp_path / "cmp"
    code, text, _err = cli("compare", str(pdf), "--out", str(out))
    assert code == EXIT_OK
    lines = text.splitlines()
    assert lines[0].startswith("backend\tmineru ")
    assert lines[1].startswith("backend\tdocling ")
    assert lines[2].startswith("versus\tmineru and docling\ttext missing ")
    report = json.loads((out / "comparison.json").read_text())
    assert report["reference"] == "mineru"
    assert not list(cli.library.glob("*/manifest.json"))  # nothing is published
    # Marker needs its llama.cpp server as well as its worker.
    add_worker(cli, "marker", marker_worker)
    code, _text, err = cli("compare", str(pdf), "--backends", "mineru,marker")
    assert (code, "llama.cpp server not found" in err) == (EXIT_USAGE, True)
    server = cli.root / "model-cache" / "llama.cpp" / "b10964" / "llama-b10964"
    server.mkdir(parents=True)
    (server / "llama-server").write_text("")
    code, document = cli.json("compare", str(pdf), "--backends", "marker,mineru")
    assert (code, document["reference"]) == (EXIT_OK, "marker")
    for backends, message in (
        ("mineru", "two or more distinct"),
        ("mineru,mineru", "two or more distinct"),
        ("mineru,nougat", "two or more distinct"),
    ):
        code, _text, err = cli("compare", str(pdf), "--backends", backends)
        assert (code, message in err) == (EXIT_USAGE, True)
    code, _text, err = cli("compare", str(cli.tmp_path / "missing.pdf"))
    assert (code, "Not a file" in err) == (EXIT_USAGE, True)


def test_compare_reports_backends_that_fail(
    cli: Cli,
    pdf_builder: Callable[..., bytes],
    docling_worker: Callable[[Path, str], Path],
    marker_worker: Callable[[Path, str], Path],
) -> None:
    pdf = make_pdf(cli.tmp_path / "in", "paper.pdf", pdf_builder)
    add_worker(cli, "docling", docling_worker, "failed")
    add_worker(cli, "marker", marker_worker, "crash")
    server = cli.root / "model-cache" / "llama.cpp" / "b10964" / "llama-b10964"
    server.mkdir(parents=True)
    (server / "llama-server").write_text("")
    code, text, _err = cli("compare", str(pdf), "--backends", "mineru,docling,marker")
    assert code == EXIT_FAILURE
    assert "failed\tdocling\tboom" in text
    assert "failed\tmarker\tWorkerProcessError" in text
    add_worker(cli, "docling", docling_worker, "ok")
    code, text, _err = cli("compare", str(pdf), "--backends", "mineru,docling,marker")
    assert code == EXIT_PARTIAL
    assert "versus\tmineru and docling" in text


def published_papers(cli: Cli, pdf_builder: Callable[..., bytes]) -> list[Path]:
    first = make_pdf(cli.tmp_path / "in", "first.pdf", pdf_builder)
    second = make_pdf(cli.tmp_path / "in", "second.pdf", pdf_builder, marker=" two")
    assert cli("batch", str(first), str(second), "--no-registry")[0] == EXIT_OK
    # Break the worker so any attempt to run it would fail the test.
    (cli.root / "workers" / "mineru" / "paperextract_mineru_worker.py").write_text(
        "raise SystemExit(9)"
    )
    return [cli.library / str(row["directory"]) for row in read_catalog(cli.library)]


def set_version(path: Path, version: object) -> None:
    data = json.loads(path.read_text())
    data["schema_version"] = version
    path.write_text(json.dumps(data))


def test_migrate_rebuilds_outdated_papers_from_kept_output(
    cli: Cli, pdf_builder: Callable[..., bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    old, current = published_papers(cli, pdf_builder)

    def recovered(library: Path) -> list[str]:
        del library
        return ["completed an interrupted replacement"]

    monkeypatch.setattr(paperextract.cli, "recover_library", recovered)
    code, out, _err = cli("migrate", "--dry-run")
    assert (code, out.splitlines()[-2:]) == (EXIT_OK, ["catalog\tcurrent", "2 current"])
    set_version(old / "manifest.json", 2)
    catalog = cli.library / CATALOG_FILENAME
    rows = [json.loads(line) for line in catalog.read_text().splitlines()]
    catalog.write_text(
        "\n".join(json.dumps({**row, "schema_version": 1}) for row in rows) + "\n\n"
    )
    code, out, _err = cli("migrate", "--dry-run")
    assert code == EXIT_OK
    assert f"would migrate\t{old.name}\tpaper-manifest 2→3\n" in out
    assert f"current\t{current.name}\n" in out
    assert out.endswith("catalog\tolder\n1 current, 1 would migrate\n")
    assert json.loads((old / "manifest.json").read_text())["schema_version"] == 2
    code, out, err = cli("migrate", "--json")
    document = json.loads(out)
    assert code == EXIT_OK
    assert "WARNING completed an interrupted replacement" in err
    assert document["schema"] == "paperextract.migrate-result"
    assert document["catalog"] == {"state": "older", "rebuilt": True}
    papers = document["papers"]
    assert isinstance(papers, list)
    assert [(p["directory"], p["outcome"]) for p in papers] == [  # type: ignore[index]
        (old.name, "migrated"),
        (current.name, "current"),
    ]
    assert json.loads((old / "manifest.json").read_text())["schema_version"] == 3
    assert {row["schema_version"] for row in read_catalog(cli.library)} == {2}
    replaced = cli.library / ".paperextract" / "replaced"
    assert [p.name.split(".")[0] for p in replaced.iterdir()] == [old.name]


def test_migrate_refuses_unsupported_and_damaged_papers(
    cli: Cli, pdf_builder: Callable[..., bytes]
) -> None:
    newer, damaged = published_papers(cli, pdf_builder)
    set_version(newer / "manifest.json", 9)
    with (damaged / "paper.md").open("a") as handle:
        handle.write("edited\n")
    for number in range(5):
        (damaged / f"note{number}.txt").write_text("mine")
    code, out, _err = cli("migrate", "--dry-run")
    assert code == EXIT_FAILURE
    assert (
        f"refused\t{newer.name}\tunsupported: manifest.json "
        "paperextract.paper-manifest 9\n"
    ) in out
    assert (
        f"refused\t{damaged.name}\tdamaged: changed paper.md; unlisted note0.txt; "
        "unlisted note1.txt; unlisted note2.txt; unlisted note3.txt; and 1 more\n"
    ) in out
    code, out, _err = cli("migrate")
    assert code == EXIT_FAILURE
    assert out.endswith("catalog\tcurrent\n2 refused\n")
    for number in range(5):
        (damaged / f"note{number}.txt").unlink()
    code, out, _err = cli("migrate", "--dry-run")
    assert f"refused\t{damaged.name}\tdamaged: changed paper.md\n" in out
    code, _out, err = cli("index", "rebuild")
    assert code == EXIT_FAILURE
    assert "not one this paperextract reads" in err
    code, out, _err = cli("reprocess", newer.name)
    assert code == EXIT_FAILURE
    assert "UnsupportedFormatError" in out
    set_version(newer / "manifest.json", 3)
    set_version(damaged / "manifest.json", 2)
    shutil.rmtree(damaged / "diagnostics")
    manifest = json.loads((damaged / "manifest.json").read_text())
    manifest["files"] = [
        entry
        for entry in manifest["files"]
        if (damaged / entry["path"]).is_file() and entry["path"] != "paper.md"
    ]
    (damaged / "manifest.json").write_text(json.dumps(manifest))
    (damaged / "paper.md").unlink()
    code, out, _err = cli("migrate")
    assert code == EXIT_PARTIAL
    assert f"failed\t{damaged.name}\tMissingOutputError" in out
    assert out.endswith("catalog\tcurrent, rebuilt\n1 current, 1 failed\n")


def test_migrate_checks_the_library_itself(cli: Cli) -> None:
    code, _out, err = cli("migrate")
    assert code == EXIT_USAGE
    assert "Not a library" in err
    cli.library.mkdir()
    corpus = {"schema": "paperextract.corpus", "schema_version": 1}
    (cli.library / CORPUS_FILENAME).write_text(json.dumps(corpus))
    code, out, _err = cli("migrate", "--dry-run")
    assert (code, out) == (EXIT_OK, "catalog\tabsent\nno papers\n")
    (cli.library / CATALOG_FILENAME).write_text("not json\n")
    code, out, _err = cli("migrate", "--dry-run")
    assert out == "catalog\tunreadable\nno papers\n"
    (cli.library / CORPUS_FILENAME).write_text(
        json.dumps({**corpus, "schema_version": 2})
    )
    code, _out, err = cli("migrate")
    assert code == EXIT_FAILURE
    assert "corpus version 2" in err


def test_a_persistent_batch_serves_papers_through_one_worker(
    cli: Cli, pdf_builder: Callable[..., bytes]
) -> None:
    first = make_pdf(cli.tmp_path / "in", "first.pdf", pdf_builder)
    second = make_pdf(cli.tmp_path / "in", "second.pdf", pdf_builder, marker=" two")
    text = cli.config.read_text().replace("[worker]\n", "[worker]\npersistent = true\n")
    cli.config.write_text(text)
    code, out, err = cli("batch", str(first), str(second), "--no-registry")
    assert code == EXIT_OK
    assert out.count("published\t") == 2
    assert "(request 2 of this process)" in err
    sessions = cli.library / ".paperextract" / "sessions"
    assert not any(sessions.iterdir())
    paper = cli.library / str(read_catalog(cli.library)[0]["directory"])
    logs = list(paper.glob("diagnostics/raw/*/worker.stderr.log"))
    assert logs
    code, _out, _err = cli(
        "batch",
        str(make_pdf(cli.tmp_path / "in", "third.pdf", pdf_builder, marker=" 3")),
        "--no-registry",
        "--keep-run",
    )
    assert code == EXIT_OK
    assert any(sessions.iterdir())


def test_several_papers_are_described_at_once_and_published_in_order(
    cli: Cli, pdf_builder: Callable[..., bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    first = make_pdf(cli.tmp_path / "in", "first.pdf", pdf_builder)
    second = make_pdf(cli.tmp_path / "in", "second.pdf", pdf_builder, marker=" two")
    assert cli("batch", str(first), str(second), "--no-registry")[0] == EXIT_OK
    names = [str(row["directory"]) for row in read_catalog(cli.library)]
    cli.config.write_text(cli.config.read_text() + "[describe]\npapers = 2\n")
    describer = StandInDescriber()

    def build(_config: object, _environ: object) -> StandInDescriber:
        return describer

    monkeypatch.setattr(paperextract.config.Configuration, "describer", build)
    code, document = cli.json("describe", *names)
    assert code == EXIT_OK
    assert [Path(str(i["path"])).name for i in items(document)] == names
    assert {i["status"] for i in items(document)} == {"republished"}
    published = paperextract.cli.publish
    calls: list[Path] = []

    def refuse(run: Path, library: Path, **options: object) -> object:
        calls.append(run)
        if len(calls) == 1:
            raise OSError("disk full")
        return published(run, library, **options)  # type: ignore[arg-type]

    monkeypatch.setattr(paperextract.cli, "publish", refuse)
    code, out, _err = cli("describe", *names, "--force")
    assert code == EXIT_PARTIAL
    assert "OSError: disk full" in out and "run kept" in out

    def interrupt(*_args: object) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(paperextract.cli, "_publish_described", interrupt)
    code, _out, err = cli("describe", *names, "--force")
    assert code == EXIT_CANCELLED
    assert "Cancelled" in err


def test_models_reports_and_fetches_sets(
    cli: Cli, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = cli.root / "workers" / "models.json"
    manifest.write_text(json.dumps(manifest_document()))
    code, out, _err = cli("models", "status")
    assert code == EXIT_FAILURE
    assert out.startswith("absent\tdemo\t2 files, 0 MB to fetch\t")
    assert "\tmodel-cache/mineru/models/Demo\n" in out.replace(str(cli.root) + "/", "")
    assert "absent\tllama.cpp b1\t" in out
    monkeypatch.setattr(paperextract.cli, "_DOWNLOAD", fake_download(served_files()))
    # The shared test configuration is offline, which refuses downloads.
    cli.config.write_text(cli.config.read_text().replace("offline = true", ""))
    code, out, _err = cli("models", "fetch", "demo")
    assert code == EXIT_OK
    assert out.startswith("fetched\tdemo\t2 files\t")
    assert "fetched\tllama.cpp b1\t" in out
    code, out, _err = cli("models", "status", "demo", "--verify")
    assert code == EXIT_OK
    assert out.startswith("complete\tdemo\t0 MB\t")
    assert "complete\tllama.cpp b1\t" in out

    def unreachable(url: str, path: Path) -> None:
        raise OSError(f"no route to {url} for {path.name}")

    monkeypatch.setattr(paperextract.cli, "_DOWNLOAD", unreachable)
    shutil.rmtree(cli.root / "model-cache" / "llama.cpp")
    (cli.root / "model-cache" / "mineru" / "models" / "Demo" / "config.json").unlink()
    code, out, _err = cli("models", "fetch", "demo")
    assert code == EXIT_FAILURE
    assert out.count("failed\t") == 2


def test_models_needs_a_known_set_and_the_network(cli: Cli) -> None:
    code, _out, err = cli("models", "status")
    assert (code, "Cannot read the model manifest" in err) == (EXIT_USAGE, True)
    document = manifest_document()
    document["binaries"] = []
    models = cast("list[dict[str, object]]", document["models"])
    describe = {
        **models[0],
        "name": "qwen",
        "backend": "describe",
        "directory": "Q",
        "complete_marker": None,
    }
    models.append(describe)
    sets = cast("dict[str, object]", document["sets"])
    sets["qwen"] = {"description": "q", "models": ["qwen"], "binaries": True}
    (cli.root / "workers" / "models.json").write_text(json.dumps(document))
    for argv, message in [
        (["models", "status", "nope"], "Unknown model sets"),
        (["models", "fetch"], "Name the sets to fetch"),
        (["models", "fetch", "demo", "--offline"], "needs the network"),
    ]:
        code, _out, err = cli(*argv)
        assert (code, message in err) == (EXIT_USAGE, True), argv
    code, out, _err = cli("models", "status", "qwen")
    assert "model-cache/describe/Q" in out
    assert "unavailable\tllama.cpp\tno pinned build for " in out
    target = cli.tmp_path / "own-qwen"
    cli.config.write_text(
        cli.config.read_text() + f'[describe]\nmodel_dir = "{target}"\n'
    )
    code, out, _err = cli("models", "status", "qwen")
    assert f"\t{target}\n" in out


def test_models_needs_a_checkout_and_model_directories(cli: Cli) -> None:
    document = manifest_document()
    sets = cast("dict[str, object]", document["sets"])
    sets["plain"] = {"description": "p", "models": ["demo"], "binaries": False}
    (cli.root / "workers" / "models.json").write_text(json.dumps(document))
    code, out, _err = cli("models", "status", "plain")
    assert code == EXIT_FAILURE
    assert "llama.cpp" not in out
    own = cli.tmp_path / "own-models"
    code, out, _err = cli("models", "status", "plain", "--models", str(own))
    assert f"\t{own / 'Demo'}\n" in out
    config = paperextract.config.resolve_configuration(
        {},
        explicit=cli.config,
        environ={"HOME": str(cli.tmp_path / "home")},
        cwd=cli.tmp_path,
        package_file=Path(paperextract.config.__file__),
    )
    args = build_parser().parse_args(["models", "status"])
    with pytest.raises(paperextract.config.ConfigurationError, match="checkout"):
        paperextract.cli._command_models(  # pyright: ignore[reportPrivateUsage]
            args, replace(config, worker_root=None)
        )
    with pytest.raises(paperextract.config.ConfigurationError, match="No model dir"):
        paperextract.cli._command_models(  # pyright: ignore[reportPrivateUsage]
            args, replace(config, model_dir=None)
        )
    assert paperextract.cli._size(56 * 10**9) == "56.0 GB"  # pyright: ignore[reportPrivateUsage]


def test_a_staged_batch_leaves_the_library_alone_until_publish(
    cli: Cli, pdf_builder: Callable[..., bytes]
) -> None:
    first = make_pdf(cli.tmp_path / "in", "first.pdf", pdf_builder)
    second = make_pdf(cli.tmp_path / "in", "second.pdf", pdf_builder, marker=" two")
    staged = cli.tmp_path / "shard-1"
    code, out, _err = cli(
        "batch", str(first), str(second), "--no-registry", "--runs-to", str(staged)
    )
    assert code == EXIT_OK
    assert out.count("staged\t") == 2
    assert not cli.library.exists()
    runs = sorted(
        path for path in staged.iterdir() if (path / "document.json").is_file()
    )
    assert len(runs) == 2
    assert any((staged / "batches").iterdir())
    # A rerun resumes: completed runs are not extracted again.
    code, out, _err = cli(
        "batch", str(first), str(second), "--no-registry", "--runs-to", str(staged)
    )
    assert code == EXIT_OK
    assert out.count("\tstaged by an earlier run") == 2
    # A job stopped mid-paper leaves a run without its document.
    stopped = staged / "20260924T000000Z-stopped"
    shutil.copytree(runs[0], stopped)
    (stopped / "document.json").unlink()
    (staged / "notes").mkdir()
    code, out, _err = cli("publish", str(staged), "--no-registry")
    assert code == EXIT_PARTIAL
    assert out.count("published\t") == 2
    assert "incomplete run" in out
    assert len(read_catalog(cli.library)) == 2
    # A library that exists is read to skip what it holds, never written.
    catalog = (cli.library / CATALOG_FILENAME).read_bytes()
    code, out, _err = cli(
        "batch", str(first), "--no-registry", "--runs-to", str(cli.tmp_path / "again")
    )
    assert code == EXIT_OK
    assert "skipped\t" in out
    assert (cli.library / CATALOG_FILENAME).read_bytes() == catalog
    code, out, _err = cli("publish", str(runs[0]), "--no-registry")
    assert code == EXIT_CONFLICT
    code, _out, err = cli("publish", str(cli.tmp_path / "in"), "--no-registry")
    assert code == EXIT_USAGE
    assert "No completed extraction" in err
    assert paperextract.cli._completed_runs(cli.tmp_path / "absent") == ({}, [])  # pyright: ignore[reportPrivateUsage]


def test_extract_adds_supplements_to_a_published_paper(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    native_worker: Callable[[Path, str], Path],
    pdf_builder: Callable[..., bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    cli = Cli(tmp_path, capsys, native_worker)
    pdf = make_pdf(tmp_path / "in", "paper.pdf", pdf_builder)
    first = make_pdf(tmp_path / "in", "extra.pdf", pdf_builder, marker=" SI")
    second = make_pdf(tmp_path / "in", "more.pdf", pdf_builder, marker=" SI 2")
    assert cli("extract", str(pdf), "--no-registry")[0] == EXIT_OK
    (row,) = read_catalog(cli.library)
    name = str(row["directory"])
    code, document = cli.json("extract", str(pdf), "--supplement", str(first))
    assert code == EXIT_OK
    (item,) = items(document)
    assert item["status"] == "republished"
    assert item["message"] == "added 1 supplement(s)"
    assert (cli.library / name / "supplement_01" / "supplement.md").is_file()
    code, _out, _err = cli("extract", str(pdf), "--supplement", str(second))
    assert code == EXIT_OK
    assert (cli.library / name / "supplement_02" / "supplement.md").is_file()
    (row,) = read_catalog(cli.library)
    assert len(row["source_sha256"]) == 3  # type: ignore[arg-type]
    failing = Cli(tmp_path, capsys, native_worker, "failed")
    third = make_pdf(tmp_path / "in", "third.pdf", pdf_builder, marker=" SI 3")
    code, out, _err = failing("extract", str(pdf), "--supplement", str(third))
    assert code == EXIT_FAILURE
    assert "supplement\tValueError: third.pdf: boom" in out
    assert not (cli.library / name / "supplement_03").exists()


def test_reprocess_asserts_a_doi_or_a_bibtex_identity(
    cli: Cli,
    pdf_builder: Callable[..., bytes],
    monkeypatch: pytest.MonkeyPatch,
    synthetic_lookup: Callable[[str], object],
) -> None:
    pdf = make_pdf(cli.tmp_path / "in", "paper.pdf", pdf_builder)
    assert cli("extract", str(pdf), "--no-registry")[0] == EXIT_OK
    (row,) = read_catalog(cli.library)
    name = str(row["directory"])

    def lookup(_config: object) -> Callable[[str], object]:
        return synthetic_lookup

    monkeypatch.setattr(paperextract.cli, "_lookup", lookup)
    code, out, _err = cli("reprocess", name, "--doi", "doi:10.1000/X")
    assert code == EXIT_OK
    assert "\tAuthor_2020_SyntheticPaper\tVALIDATED_WITH_WARNINGS\t" in out
    paper = cli.library / "Author_2020_SyntheticPaper"
    extraction = json.loads((paper / "extraction.json").read_text())
    assert extraction["assertions"] == {"doi": "doi:10.1000/X"}
    entry = cli.tmp_path / "report.bib"
    entry.write_text(
        "@techreport{r, author = {Pitchford, L. C.}, title = {A synthetic report},"
        " institution = {JILA}, year = {1981}}"
    )
    code, out, _err = cli("reprocess", str(paper), "--bibtex", str(entry))
    assert code == EXIT_OK
    assert "\tPitchford_1981_SyntheticReport\tASSERTED\t" in out
    asserted = cli.library / "Pitchford_1981_SyntheticReport"
    citation = (asserted / "citation.bib").read_text()
    assert citation.startswith("@techreport{pitchford1981synthetic,")
    assert "institution = {JILA}" in citation
    metadata = json.loads((asserted / "metadata.json").read_text())
    assert metadata["bibliography_validation"]["status"] == "ASSERTED"
    (row,) = read_catalog(cli.library)
    assert row["bibliographic_status"] == "ASSERTED"
    assert "pitchford1981synthetic" in (cli.library / "library.bib").read_text()


@pytest.mark.parametrize(
    ("argv", "bib", "message"),
    [
        (["--all", "--doi", "10.1000/x"], None, "apply to exactly one named paper"),
        (["PAPER", "--doi", "10.1/x", "--bibtex", "BIB"], "", "not both"),
        (["PAPER", "--doi", "nonsense"], None, "is not a DOI"),
        (["PAPER", "--bibtex", "missing.bib"], None, "Not found"),
        (
            ["PAPER", "--bibtex", "BIB"],
            "@misc{a, title={T}} @misc{b, title={U}}",
            "exactly one",
        ),
        (["PAPER", "--bibtex", "BIB"], "@misc{a, title = {T}}", "lacks author, year"),
        (
            ["PAPER", "--bibtex", "BIB"],
            "@misc{a, author={A}, title={T}, year={1}, doi={10.1/x}}",
            "assert it with --doi",
        ),
        (
            ["PAPER", "--bibtex", "BIB"],
            "@misc{a, author={A}, title={T}, year={n.d.}}",
            "the year is not a number",
        ),
    ],
)
def test_reprocess_assertion_usage_errors(
    cli: Cli,
    pdf_builder: Callable[..., bytes],
    argv: list[str],
    bib: str | None,
    message: str,
) -> None:
    pdf = make_pdf(cli.tmp_path / "in", "paper.pdf", pdf_builder)
    assert cli("extract", str(pdf), "--no-registry")[0] == EXIT_OK
    (row,) = read_catalog(cli.library)
    entry = cli.tmp_path / "entry.bib"
    if bib is not None:
        entry.write_text(bib)
    values = [
        str(row["directory"]) if a == "PAPER" else str(entry) if a == "BIB" else a
        for a in argv
    ]
    code, _out, err = cli("reprocess", *values)
    assert code == EXIT_USAGE
    assert message in err
