"""Read, check, shard and write batch manifests."""

import json
from pathlib import Path

import pytest

from paperextract.manifest import (
    ManifestError,
    manifest_text,
    parse_shard,
    read_manifest,
    shard_entries,
)

HEADER = '{"schema": "paperextract.batch-manifest", "schema_version": 1}'


def write(path: Path, *lines: str) -> Path:
    path.write_text("\n".join(lines) + "\n")
    return path


def files(tmp_path: Path, *names: str) -> None:
    for name in names:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"%PDF-1.4")


def test_entries_resolve_relative_paths_and_arxiv_requests(tmp_path: Path) -> None:
    files(tmp_path, "a/paper.pdf", "a/si.pdf", "a/page.html")
    manifest = write(
        tmp_path / "m.jsonl",
        HEADER,
        "# a comment",
        "",
        json.dumps(
            {
                "id": "a",
                "paper": "a/paper.pdf",
                "supplements": ["a/si.pdf"],
                "html": ["a/page.html"],
                "document_version": "accepted_manuscript",
            }
        ),
        json.dumps({"id": "b", "paper": "arXiv:2206.01062"}),
    )
    first, second = read_manifest(manifest)
    assert first.paper == tmp_path / "a" / "paper.pdf"
    assert first.supplements == (tmp_path / "a" / "si.pdf",)
    assert first.document_version == "accepted_manuscript"
    assert first.line == 4
    assert second.paper == "arXiv:2206.01062"
    assert second.files() == ()


def test_every_problem_is_reported_before_work_starts(tmp_path: Path) -> None:
    files(tmp_path, "p.pdf")
    manifest = write(
        tmp_path / "m.jsonl",
        HEADER,
        "not json",
        "[1]",
        json.dumps({"id": "x y", "paper": "", "extra": 1}),
        json.dumps({"id": "a", "paper": "p.pdf"}),
        json.dumps({"id": "s", "paper": "arXiv:1234.5678", "supplements": "si.pdf"}),
        json.dumps({"id": "b", "paper": "missing.pdf", "document_version": "draft"}),
        json.dumps({"id": "c", "paper": "p.pdf"}),
        json.dumps({"id": "c", "paper": "arXiv:1234.5678", "html": ["p.pdf"]}),
    )
    with pytest.raises(ManifestError) as excinfo:
        read_manifest(manifest)
    message = str(excinfo.value)
    for expected in (
        "line 2: not JSON",
        "line 3: not a JSON object",
        "line 4: unknown keys ['extra']",
        "line 4: id must be",
        "line 4: paper must name",
        "line 6: supplements must be a list",
        "line 7: document_version must be one of",
        "missing.pdf does not exist",
        "line 8: p.pdf is also named by 'a'",
        "line 9: id 'c' repeats line 8",
        "line 9: p.pdf is also named by 'a'",
    ):
        assert expected in message


@pytest.mark.parametrize(
    ("lines", "message"),
    [((), "is empty"), (('{"schema": "other"}',), "must be the header")],
)
def test_the_header_is_required(
    tmp_path: Path, lines: tuple[str, ...], message: str
) -> None:
    manifest = tmp_path / "m.jsonl"
    manifest.write_text("\n".join(lines))
    with pytest.raises(ManifestError, match=message):
        read_manifest(manifest)


def test_shards_are_stable_and_complete(tmp_path: Path) -> None:
    lines = [json.dumps({"id": f"p{i}", "paper": "arXiv:1234.5678"}) for i in range(20)]
    # Identifiers are unique; arXiv papers name no files, so none repeat.
    entries = read_manifest(write(tmp_path / "m.jsonl", HEADER, *lines))
    shards = [shard_entries(entries, number, 3) for number in (1, 2, 3)]
    assert sorted(e.id for shard in shards for e in shard) == sorted(
        e.id for e in entries
    )
    assert all(shard for shard in shards)
    assert shard_entries(tuple(reversed(entries)), 1, 3) == tuple(reversed(shards[0]))
    assert parse_shard(" 1/1 ") == (1, 1)
    for bad in ("0/2", "3/2", "a/b"):
        with pytest.raises(ValueError, match="Shard"):
            parse_shard(bad)


def test_written_manifests_read_back(tmp_path: Path) -> None:
    files(tmp_path, "in/p.pdf", "in/s.pdf")
    outside = Path("/elsewhere/q.pdf")
    text = manifest_text(
        [
            {
                "id": "p",
                "paper": tmp_path / "in" / "p.pdf",
                "supplements": [tmp_path / "in" / "s.pdf"],
            },
            {"id": "q", "paper": outside, "document_version": None},
        ],
        tmp_path,
    )
    lines = text.splitlines()
    assert lines[0] == HEADER
    assert json.loads(lines[1]) == {
        "id": "p",
        "paper": "in/p.pdf",
        "supplements": ["in/s.pdf"],
    }
    assert json.loads(lines[2])["paper"] == "/elsewhere/q.pdf"
    manifest = write(tmp_path / "m.jsonl", *lines[:2])
    (entry,) = read_manifest(manifest)
    assert entry.paper == tmp_path / "in" / "p.pdf"
