"""Check and fetch pinned model snapshots and binaries against the manifest."""

import io
import json
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

import paperextract.models
from conftest import (
    BASE,
    FILES,
    fake_download,
    manifest_document,
    served_files,
)
from paperextract.config import _MARKER_SERVER  # pyright: ignore[reportPrivateUsage]
from paperextract.models import (
    BINARY_RECORD,
    PARTIAL_DIRECTORY,
    binary_installed,
    current_platform,
    fetch_binary,
    fetch_snapshot,
    load_manifest,
    snapshot_status,
    urllib_download,
)

REPOSITORY = Path(__file__).resolve().parents[1]


def write_manifest(tmp_path: Path, **changes: object) -> Path:
    path = tmp_path / "models.json"
    path.write_text(json.dumps(manifest_document(**changes)))
    return path


def test_the_repository_manifest_is_consistent() -> None:
    manifest = load_manifest(REPOSITORY / "workers" / "models.json")
    in_sets = {name for entry in manifest.sets.values() for name in entry.models}
    assert in_sets == set(manifest.models)
    assert {"mineru", "mineru-cuda", "docling", "marker", "describe"} <= set(
        manifest.sets
    )
    backends = {snapshot.backend for snapshot in manifest.models.values()}
    assert backends == {"mineru", "docling", "marker", "describe"}
    for target in ("darwin-arm64", "linux-x86_64"):
        binary = manifest.binary_for(target)
        assert binary is not None
        # The default [marker] server path points inside the unpacked archive.
        assert _MARKER_SERVER.as_posix().startswith(f"model-cache/{binary.directory}/")
    assert manifest.binary_for("plan9-mips") is None


def test_fetching_downloads_verifies_and_resumes(tmp_path: Path) -> None:
    manifest = load_manifest(write_manifest(tmp_path))
    snapshot = manifest.models["demo"]
    assert snapshot.total_bytes == sum(len(d) for d in FILES.values())
    target = tmp_path / "models" / "Demo"
    status = snapshot_status(snapshot, target)
    assert (status.state, status.missing_bytes) == ("absent", snapshot.total_bytes)
    calls: list[str] = []
    assert fetch_snapshot(snapshot, target, fake_download(served_files(), calls)) == 2
    assert (target / "weights" / "model bin").read_bytes() == FILES["weights/model bin"]
    assert (target / ".done").is_file()
    assert not (target / PARTIAL_DIRECTORY).exists()
    assert snapshot_status(snapshot, target, verify=True).state == "complete"
    # A damaged file of the right size is caught only when hashing.
    (target / "config.json").write_bytes(b'{"a": 2}')
    assert snapshot_status(snapshot, target).state == "complete"
    damaged = snapshot_status(snapshot, target, verify=True)
    assert (damaged.state, damaged.missing) == ("partial", ("config.json",))
    (target / "config.json").unlink()
    assert fetch_snapshot(snapshot, target, fake_download(served_files(), calls)) == 1
    assert calls[-1] == f"{BASE}/config.json"
    (target / ".done").unlink()
    assert snapshot_status(snapshot, target).state == "partial"


def test_a_download_that_does_not_match_is_refused(tmp_path: Path) -> None:
    snapshot = load_manifest(write_manifest(tmp_path)).models["demo"]
    served = served_files()
    served[f"{BASE}/config.json"] = b"tampered"
    target = tmp_path / "Demo"
    with pytest.raises(ValueError, match="do not match"):
        fetch_snapshot(snapshot, target, fake_download(served))
    assert not (target / "config.json").exists()
    assert not (target / ".done").exists()


def test_binaries_are_verified_and_unpacked_once(tmp_path: Path) -> None:
    manifest = load_manifest(write_manifest(tmp_path))
    binary = manifest.binary_for(current_platform())
    assert binary is not None
    target = tmp_path / "llama.cpp" / "b1"
    assert not binary_installed(binary, target)
    calls: list[str] = []
    assert fetch_binary(binary, target, fake_download(served_files(), calls))
    assert (target / "llama-b1" / "llama-server").read_bytes() == b"#!/bin/sh\n"
    assert (target / BINARY_RECORD).read_text().strip() == binary.sha256
    assert not fetch_binary(binary, target, fake_download(served_files(), calls))
    assert len(calls) == 1
    served = served_files()
    served[binary.url] = b"not the archive"
    with pytest.raises(ValueError, match="SHA-256"):
        fetch_binary(binary, tmp_path / "other", fake_download(served))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"schema_version": 1}, "version 2 manifest"),
        (
            {
                "sets": {
                    "x": {"description": "x", "models": ["nope"], "binaries": False}
                }
            },
            "unknown models",
        ),
    ],
)
def test_malformed_manifests_are_refused(
    tmp_path: Path, changes: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        load_manifest(write_manifest(tmp_path, **changes))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("path", "../escape", "unsafe path"),
        ("path", "/abs", "unsafe path"),
        ("sha256", "XYZ", "SHA-256"),
    ],
)
def test_unsafe_file_entries_are_refused(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    document = manifest_document()
    models = document["models"]
    assert isinstance(models, list)
    models[0]["files"][0][field] = value  # type: ignore[index]
    path = tmp_path / "models.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match=message):
        load_manifest(path)


def test_the_standard_download_needs_https(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError, match="non-HTTPS"):
        urllib_download("http://example.org/a", tmp_path / "a")
    requested: list[str] = []

    @contextmanager
    def urlopen(request: object, timeout: float) -> Generator[io.BytesIO]:
        del timeout
        requested.append(str(getattr(request, "full_url", "")))
        yield io.BytesIO(b"payload")

    monkeypatch.setattr(paperextract.models.urllib.request, "urlopen", urlopen)
    urllib_download("https://example.org/a", tmp_path / "a")
    assert (tmp_path / "a").read_bytes() == b"payload"
    assert requested == ["https://example.org/a"]


def test_stale_partial_files_and_snapshots_without_markers(tmp_path: Path) -> None:
    snapshot = replace(
        load_manifest(write_manifest(tmp_path)).models["demo"], complete_marker=None
    )
    target = tmp_path / "Demo"
    stale = target / PARTIAL_DIRECTORY / "leftover"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"from an interrupted fetch")
    assert fetch_snapshot(snapshot, target, fake_download(served_files())) == 2
    assert stale.is_file()
    assert not any(target.glob(".done"))
    assert snapshot_status(snapshot, target).state == "complete"
