"""Record versions a library may hold, and checks of paper directories.

Every record paperextract writes names its schema and version. This module
lists the versions this release reads for each record of a library and its
paper directories; the last one listed is the version it writes. A record
with a version not listed was written by another release, most likely a
newer one, and is refused rather than read by guesswork.

Kept evidence (``original/`` and ``diagnostics/``) is checked for integrity
but not for versions: it is never rewritten, and the worker protocol and
native readers check their own versions when the evidence is read again.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from paperextract.fields import integer, items, mapping, string

__all__ = [
    "READABLE_VERSIONS",
    "PaperCheck",
    "RecordCheck",
    "UnsupportedFormatError",
    "check_paper",
    "paper_records",
    "record_state",
    "require_readable",
    "require_readable_paper",
    "system_file",
]

# Each earlier version listed here is read by the current readers and
# rebuilt by `reprocess`; versions 1 and 2 of the paper directory records
# were verified on real libraries on 23 September 2026. Add a version here
# only together with the reader change that supports it.
READABLE_VERSIONS: Mapping[str, tuple[str, ...]] = {
    "paperextract.corpus": ("1",),
    "paperextract.catalog-row": ("1", "2"),
    "paperextract.paper-manifest": ("1", "2", "3"),
    "paperextract.document": ("0.1", "0.2", "0.3"),
    "paperextract.extraction": ("1", "2", "3"),
    "paperextract.metadata": ("1", "2"),
    "paperextract.validation": ("1", "2", "3"),
    "paperextract.table": ("1", "2", "3"),
    "paperextract.html-checks": ("1",),
    "paperextract.figure-descriptions": ("1",),
}
_MANIFEST = "manifest.json"
_EVIDENCE = ("original/", "diagnostics/")
_CHUNK = 1 << 20

RecordState = Literal["current", "older", "unsupported"]
PaperState = Literal["current", "outdated", "unsupported", "damaged"]


class UnsupportedFormatError(ValueError):
    """A record has a version this release of paperextract does not read."""


def record_state(schema: str, version: object) -> RecordState | None:
    """Classify the version of a record.

    Parameters
    ----------
    schema : str
        Schema name, such as ``paperextract.document``.
    version : object
        The record's ``schema_version``.

    Returns
    -------
    str or None
        ``current`` for the version this release writes, ``older`` for an
        earlier version it reads, ``unsupported`` otherwise; None for a
        schema this module does not track.

    Examples
    --------
    >>> record_state("paperextract.document", "0.1")
    'older'
    >>> record_state("paperextract.paper-manifest", 9)
    'unsupported'
    """
    known = READABLE_VERSIONS.get(schema)
    if known is None:
        return None
    text = str(version)
    if text == known[-1]:
        return "current"
    return "older" if text in known else "unsupported"


def require_readable(data: Mapping[str, object], where: str) -> None:
    """Refuse a record whose version this release does not read.

    Parameters
    ----------
    data : Mapping of str to object
        Parsed record with ``schema`` and ``schema_version``.
    where : str
        Record location for the message.

    Raises
    ------
    UnsupportedFormatError
        The schema is tracked and its version is not one this release reads.
    """
    schema = data.get("schema")
    if not isinstance(schema, str):
        return
    if record_state(schema, data.get("schema_version")) == "unsupported":
        known = READABLE_VERSIONS[schema]
        raise UnsupportedFormatError(
            f"{where}: {schema} version {data.get('schema_version')} is not one "
            f"this paperextract reads ({', '.join(known)}); a newer "
            "paperextract may have written it"
        )


@dataclass(frozen=True)
class RecordCheck:
    """Report the version of one record.

    Attributes
    ----------
    path : str
        Path within the paper directory or library.
    schema : str
        Schema name.
    version : str
        Version found.
    state : str
        ``current``, ``older`` or ``unsupported``.
    """

    path: str
    schema: str
    version: str
    state: RecordState

    def to_dict(self) -> dict[str, object]:
        """Serialize the check.

        Returns
        -------
        dict of str to object
            Path, schema, version, state and the current version.
        """
        return {
            "path": self.path,
            "schema": self.schema,
            "version": self.version,
            "current": READABLE_VERSIONS[self.schema][-1],
            "state": self.state,
        }


@dataclass(frozen=True)
class PaperCheck:
    """Report the record versions and integrity of one paper directory.

    Attributes
    ----------
    directory : str
        Paper directory relative to the library.
    records : tuple of RecordCheck
        Versioned records the paper's manifest lists, and the manifest.
    problems : tuple of str
        Integrity problems: files missing, changed since publication or
        not listed in the manifest, and records that cannot be parsed.
    """

    directory: str
    records: tuple[RecordCheck, ...]
    problems: tuple[str, ...]

    @property
    def state(self) -> PaperState:
        """Summarize the paper.

        Returns
        -------
        str
            ``damaged`` when there is an integrity problem, else
            ``unsupported`` when a record's version is not read, else
            ``outdated`` when a record is older, else ``current``.
        """
        states = {record.state for record in self.records}
        if self.problems:
            return "damaged"
        if "unsupported" in states:
            return "unsupported"
        return "outdated" if "older" in states else "current"

    def to_dict(self) -> dict[str, object]:
        """Serialize the check.

        Returns
        -------
        dict of str to object
            Directory, state, the records that are not current, and the
            problems.
        """
        return {
            "directory": self.directory,
            "state": self.state,
            "records": [r.to_dict() for r in self.records if r.state != "current"],
            "problems": list(self.problems),
        }


def _parse(path: Path) -> tuple[bool, object]:
    """Parse a JSON file.

    Parameters
    ----------
    path : Path
        File to read.

    Returns
    -------
    tuple of bool and object
        Whether the file parsed, and its value.
    """
    try:
        return True, json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False, None


def _json_object(path: Path) -> dict[str, object] | None:
    """Read a JSON object, or None when the file does not hold one.

    Parameters
    ----------
    path : Path
        File to read.

    Returns
    -------
    dict of str to object or None
        Parsed object.
    """
    _, value = _parse(path)
    try:
        return mapping(value)
    except ValueError:
        return None


def _listed(paper: Path) -> tuple[dict[str, object] | None, list[dict[str, object]]]:
    """Read a paper's manifest and its file entries.

    Parameters
    ----------
    paper : Path
        Paper directory.

    Returns
    -------
    tuple
        The manifest, or None when it cannot be read, and its file entries.
    """
    manifest = _json_object(paper / _MANIFEST)
    if manifest is None:
        return None, []
    return manifest, [mapping(entry) for entry in items(manifest.get("files", []))]


def _record(path: str, data: Mapping[str, object]) -> RecordCheck | None:
    """Check the version of one parsed record.

    Parameters
    ----------
    path : str
        Record path.
    data : Mapping of str to object
        Parsed record.

    Returns
    -------
    RecordCheck or None
        The check, or None for an untracked schema.
    """
    schema = data.get("schema")
    if not isinstance(schema, str):
        return None
    state = record_state(schema, data.get("schema_version"))
    if state is None:
        return None
    return RecordCheck(path, schema, str(data.get("schema_version")), state)


def paper_records(paper: Path) -> tuple[RecordCheck, ...]:
    """Check the versions of a paper's records without hashing its files.

    Parameters
    ----------
    paper : Path
        Paper directory.

    Returns
    -------
    tuple of RecordCheck
        The manifest and every tracked JSON record it lists outside the
        kept evidence; unreadable records are left out.
    """
    manifest, entries = _listed(paper)
    if manifest is None:
        return ()
    checks = [_record(_MANIFEST, manifest)]
    for entry in entries:
        path = string(entry["path"])
        if path.endswith(".json") and not path.startswith(_EVIDENCE):
            data = _json_object(paper / path)
            checks.append(None if data is None else _record(path, data))
    return tuple(check for check in checks if check is not None)


def require_readable_paper(paper: Path) -> None:
    """Refuse a paper directory holding a record this release does not read.

    Parameters
    ----------
    paper : Path
        Paper directory.

    Raises
    ------
    UnsupportedFormatError
        A record's version is not one this release reads.
    """
    for check in paper_records(paper):
        if check.state == "unsupported":
            raise UnsupportedFormatError(
                f"{paper.name}/{check.path}: {check.schema} version "
                f"{check.version} is not one this paperextract reads "
                f"({', '.join(READABLE_VERSIONS[check.schema])}); a newer "
                "paperextract may have written it"
            )


def _sha256(path: Path) -> str:
    """Hash a file.

    Parameters
    ----------
    path : Path
        File.

    Returns
    -------
    str
        Hexadecimal SHA-256.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _integrity(paper: Path, entries: list[dict[str, object]]) -> list[str]:
    """Compare a paper directory with its manifest.

    Parameters
    ----------
    paper : Path
        Paper directory.
    entries : list of dict
        File entries of the manifest.

    Returns
    -------
    list of str
        ``missing``, ``changed`` and ``unlisted`` problems, by path.
    """
    problems: list[str] = []
    listed: set[str] = {_MANIFEST}
    for entry in entries:
        path = string(entry["path"])
        listed.add(path)
        target = paper / path
        if not target.is_file():
            problems.append(f"missing {path}")
        elif target.stat().st_size != integer(
            entry["size_bytes"], minimum=0
        ) or _sha256(target) != string(entry["sha256"]):
            problems.append(f"changed {path}")
    problems.extend(
        f"unlisted {relative}"
        for relative in sorted(
            item.relative_to(paper).as_posix()
            for item in paper.rglob("*")
            if item.is_file() and not system_file(item.name)
        )
        if relative not in listed
    )
    return problems


# Files that file managers write into folders they display: Finder's
# .DS_Store and AppleDouble "._" files, Windows' Thumbs.db and desktop.ini,
# KDE's .directory. They are not part of a paper and never listed.
_SYSTEM_FILES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini", ".directory"})


def system_file(name: str) -> bool:
    """Tell whether a file name is metadata a file manager wrote.

    Parameters
    ----------
    name : str
        File name without its directory.

    Returns
    -------
    bool
        True for ``.DS_Store``, AppleDouble ``._*`` files, ``Thumbs.db``,
        ``desktop.ini`` and ``.directory``, which the integrity check ignores.

    Examples
    --------
    >>> system_file(".DS_Store"), system_file("._paper.md"), system_file("paper.md")
    (True, True, False)
    """
    return name in _SYSTEM_FILES or name.startswith("._")


def check_paper(paper: Path, library: Path) -> PaperCheck:
    """Check a paper directory's record versions and integrity.

    Parameters
    ----------
    paper : Path
        Paper directory.
    library : Path
        Library root, for the relative directory.

    Returns
    -------
    PaperCheck
        Versions of its records and every integrity problem. Files are
        hashed, so this reads the whole directory.
    """
    directory = paper.relative_to(library).as_posix()
    manifest, entries = _listed(paper)
    if manifest is None:
        return PaperCheck(directory, (), (f"unreadable {_MANIFEST}",))
    problems = _integrity(paper, entries)
    unparsable = [
        f"unreadable {path}"
        for entry in entries
        if (path := string(entry["path"])).endswith(".json")
        and not path.startswith(_EVIDENCE)
        and (paper / path).is_file()
        and not _parse(paper / path)[0]
    ]
    return PaperCheck(directory, paper_records(paper), (*problems, *unparsable))
