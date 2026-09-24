"""Classify record versions and check paper directories against their manifest."""

import hashlib
import json
from pathlib import Path

import pytest

from paperextract.catalog import CATALOG_ROW_SCHEMA, CATALOG_ROW_VERSION
from paperextract.describe import DESCRIPTION_SET_SCHEMA, DESCRIPTION_SET_VERSION
from paperextract.document import SCHEMA_NAME, SCHEMA_VERSION
from paperextract.export import EXPORT_SCHEMA_VERSION
from paperextract.formats import (
    READABLE_VERSIONS,
    UnsupportedFormatError,
    check_paper,
    paper_records,
    record_state,
    require_readable,
    require_readable_paper,
)


def record(schema: str, version: object) -> bytes:
    return json.dumps({"schema": schema, "schema_version": version}).encode()


def paper(
    tmp_path: Path, files: dict[str, bytes], manifest_version: object = 3
) -> Path:
    directory = tmp_path / "library" / "Paper"
    for path, data in files.items():
        (directory / path).parent.mkdir(parents=True, exist_ok=True)
        (directory / path).write_bytes(data)
    entries = [
        {
            "path": path,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        }
        for path, data in files.items()
    ]
    manifest = {
        "schema": "paperextract.paper-manifest",
        "schema_version": manifest_version,
        "files": entries,
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return directory


def current_files() -> dict[str, bytes]:
    return {
        "document.json": record(SCHEMA_NAME, SCHEMA_VERSION),
        "extraction.json": record("paperextract.extraction", 3),
        "tables/table_01.json": record("paperextract.table", 3),
        "paper.md": b"# Paper\n",
        "listing.json": b"[1, 2]",
        "plain.json": b'{"a": 1}',
        "result.json": record("paperextract.cli-result", 2),
        "diagnostics/raw/run/result.json": record("paperextract.document", "9"),
        "original/source_01/capture.json": b"not json",
    }


def test_the_last_readable_version_is_the_one_written() -> None:
    written = {
        SCHEMA_NAME: SCHEMA_VERSION,
        "paperextract.paper-manifest": str(EXPORT_SCHEMA_VERSION),
        "paperextract.extraction": str(EXPORT_SCHEMA_VERSION),
        "paperextract.validation": str(EXPORT_SCHEMA_VERSION),
        "paperextract.table": str(EXPORT_SCHEMA_VERSION),
        CATALOG_ROW_SCHEMA: str(CATALOG_ROW_VERSION),
        DESCRIPTION_SET_SCHEMA: str(DESCRIPTION_SET_VERSION),
    }
    for schema, version in written.items():
        assert READABLE_VERSIONS[schema][-1] == version


@pytest.mark.parametrize(
    ("schema", "version", "state"),
    [
        ("paperextract.document", "0.3", "current"),
        ("paperextract.document", "0.2", "older"),
        ("paperextract.document", "0.4", "unsupported"),
        ("paperextract.paper-manifest", 3, "current"),
        ("paperextract.paper-manifest", 1, "older"),
        ("paperextract.paper-manifest", None, "unsupported"),
        ("paperextract.worker-result", 1, None),
    ],
)
def test_record_versions_are_classified(
    schema: str, version: object, state: str | None
) -> None:
    assert record_state(schema, version) == state


def test_unsupported_records_are_refused() -> None:
    require_readable({"schema": "paperextract.metadata", "schema_version": 2}, "m")
    require_readable({"schema_version": 99}, "no schema")
    require_readable({"schema": "paperextract.other", "schema_version": 99}, "x")
    with pytest.raises(UnsupportedFormatError, match=r"m\.json: .* version 3 .*1, 2"):
        require_readable(
            {"schema": "paperextract.metadata", "schema_version": 3}, "m.json"
        )


def test_a_current_paper_passes(tmp_path: Path) -> None:
    directory = paper(tmp_path, current_files())
    check = check_paper(directory, tmp_path / "library")
    assert (check.directory, check.state, check.problems) == ("Paper", "current", ())
    # Kept evidence and non-record JSON are not version-checked.
    assert sorted(r.path for r in check.records) == [
        "document.json",
        "extraction.json",
        "manifest.json",
        "tables/table_01.json",
    ]
    assert check.to_dict() == {
        "directory": "Paper",
        "state": "current",
        "records": [],
        "problems": [],
    }
    require_readable_paper(directory)


def test_older_and_unsupported_records_are_reported(tmp_path: Path) -> None:
    files = {**current_files(), "document.json": record(SCHEMA_NAME, "0.1")}
    directory = paper(tmp_path, files, manifest_version=1)
    check = check_paper(directory, tmp_path / "library")
    assert check.state == "outdated"
    assert check.to_dict()["records"] == [
        {
            "path": "manifest.json",
            "schema": "paperextract.paper-manifest",
            "version": "1",
            "current": "3",
            "state": "older",
        },
        {
            "path": "document.json",
            "schema": SCHEMA_NAME,
            "version": "0.1",
            "current": SCHEMA_VERSION,
            "state": "older",
        },
    ]
    require_readable_paper(directory)
    newer = paper(tmp_path / "newer", {"document.json": record(SCHEMA_NAME, "0.9")})
    assert check_paper(newer, tmp_path / "newer" / "library").state == "unsupported"
    with pytest.raises(UnsupportedFormatError, match=r"Paper/document\.json: .* 0\.9"):
        require_readable_paper(newer)


def test_integrity_problems_mark_a_paper_damaged(tmp_path: Path) -> None:
    files = {
        **current_files(),
        "same-size.md": b"aaaa",
        "gone.png": b"png",
        "broken.json": b"{}",
    }
    directory = paper(tmp_path, files)
    (directory / "paper.md").write_text("# Paper, edited\n")
    (directory / "same-size.md").write_bytes(b"bbbb")
    (directory / "gone.png").unlink()
    (directory / "broken.json").write_text("{")
    (directory / "notes" / "mine.txt").parent.mkdir()
    (directory / "notes" / "mine.txt").write_text("my notes")
    check = check_paper(directory, tmp_path / "library")
    assert check.state == "damaged"
    assert check.problems == (
        "changed paper.md",
        "changed same-size.md",
        "missing gone.png",
        "changed broken.json",
        "unlisted notes/mine.txt",
        "unreadable broken.json",
    )
    (directory / "manifest.json").write_text("[]")
    check = check_paper(directory, tmp_path / "library")
    assert (check.records, check.problems) == ((), ("unreadable manifest.json",))
    assert paper_records(directory) == ()
