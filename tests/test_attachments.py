"""Keep data files with a paper byte for byte, with their provenance."""

import hashlib
import json
from pathlib import Path

import pytest

from paperextract.attachments import (
    ATTACHMENTS_FILENAME,
    Attachment,
    Provenance,
    attach_file,
    attachment_path,
    parse_attachments,
    read_attachments,
    write_attachments,
)

WHEN = "2026-09-29T12:00:00+00:00"


def entry(**changes: object) -> dict[str, object]:
    data: dict[str, object] = {
        "id": "data_01",
        "path": "data/01/values.xlsx",
        "original_name": "values.xlsx",
        "sha256": "0" * 64,
        "size_bytes": 3,
        "url": None,
        "retrieved_utc": None,
        "attached_utc": WHEN,
        "note": None,
    }
    data.update(changes)
    return data


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "a\\b"])
def test_only_plain_file_names_are_attached(name: str) -> None:
    with pytest.raises(ValueError, match="Not a plain file name"):
        attachment_path(1, name)


def test_numbers_beyond_99_keep_their_digits() -> None:
    assert attachment_path(100, "x.txt") == "data/100/x.txt"


def test_records_round_trip_with_their_provenance() -> None:
    data = entry(url="https://example.org/v.xlsx", retrieved_utc="2026-09-01")
    attachment = Attachment.from_dict(data)
    assert attachment.provenance == Provenance(
        url="https://example.org/v.xlsx", retrieved_utc="2026-09-01"
    )
    assert attachment.to_dict() == data


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"id": "source_01"}, "Not an attachment identifier"),
        ({"path": "data/02/values.xlsx"}, "has the path"),
        ({"path": "../values.xlsx"}, "has the path"),
        ({"sha256": "abc"}, "no SHA-256 digest"),
        ({"size_bytes": -1}, "integer"),
        ({"note": ""}, "string with content"),
        ({"extra": 1}, "exactly these fields"),
    ],
)
def test_malformed_records_are_refused(
    changes: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        Attachment.from_dict(entry(**changes))


def test_attachments_are_numbered_in_order() -> None:
    first = entry()
    second = entry(id="data_03", path="data/03/values.xlsx")
    with pytest.raises(ValueError, match="data_03 is out of order"):
        parse_attachments([first, second])
    assert parse_attachments([]) == ()


def test_files_are_preserved_and_appended(tmp_path: Path) -> None:
    staging = tmp_path / "run"
    staging.mkdir()
    assert read_attachments(staging) == ()
    write_attachments(staging, ())
    assert not (staging / ATTACHMENTS_FILENAME).exists()
    first = tmp_path / "values.xlsx"
    first.write_bytes(b"PK\x03\x04")
    second = tmp_path / "table.Enk"
    second.write_bytes(b"energy 1.0\n")
    note = Provenance(note="from the author's website")
    one = attach_file(staging, first, note, attached_utc=WHEN)
    two = attach_file(staging, second, Provenance(), attached_utc=WHEN)
    assert (one.id, one.path, two.id, two.path) == (
        "data_01",
        "data/01/values.xlsx",
        "data_02",
        "data/02/table.Enk",
    )
    assert one.sha256 == hashlib.sha256(b"PK\x03\x04").hexdigest()
    assert (staging / two.path).read_bytes() == b"energy 1.0\n"
    assert read_attachments(staging) == (one, two)
    recorded = json.loads((staging / ATTACHMENTS_FILENAME).read_text())
    assert recorded["attachments"][0]["note"] == "from the author's website"


def test_the_same_bytes_are_attached_once(tmp_path: Path) -> None:
    staging = tmp_path / "run"
    staging.mkdir()
    first = tmp_path / "values.txt"
    first.write_bytes(b"1 2 3\n")
    again = tmp_path / "copy.txt"
    again.write_bytes(b"1 2 3\n")
    attach_file(staging, first, Provenance(), attached_utc=WHEN)
    with pytest.raises(ValueError, match=r"copy\.txt has the same bytes as data/01"):
        attach_file(staging, again, Provenance(), attached_utc=WHEN)
    assert sorted(p.name for p in (staging / "data").iterdir()) == ["01"]


def test_a_failed_copy_leaves_no_directory(tmp_path: Path) -> None:
    staging = tmp_path / "run"
    staging.mkdir()
    with pytest.raises(OSError, match="No such file"):
        attach_file(staging, tmp_path / "absent.txt", Provenance(), attached_utc=WHEN)
    assert not any((staging / "data").iterdir())
    assert read_attachments(staging) == ()
