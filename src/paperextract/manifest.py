"""Read and write batch manifests: one explicit paper per line.

A manifest is a JSON Lines file. Its first line is the header
``{"schema": "paperextract.batch-manifest", "schema_version": 1}``; every
further line describes one paper::

    {"id": "hisol", "paper": "hisol/published.pdf",
     "supplements": ["hisol/si.pdf"], "html": ["hisol/page.html"],
     "document_version": "version_of_record"}
    {"id": "docling", "paper": "arXiv:2206.01062"}

Paths are relative to the manifest. Blank lines and lines starting with
``#`` are ignored. The whole manifest is checked before any work starts:
unique identifiers, known keys, existing files, known versions, and no file
named twice. ``dedup --manifest-out`` writes a proposed manifest for review.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from paperextract.acquire import arxiv_request
from paperextract.versions import VERSIONS

__all__ = [
    "MANIFEST_SCHEMA",
    "MANIFEST_VERSION",
    "ManifestEntry",
    "ManifestError",
    "manifest_text",
    "parse_shard",
    "read_manifest",
    "shard_entries",
]

MANIFEST_SCHEMA = "paperextract.batch-manifest"
MANIFEST_VERSION = 1
_KEYS = frozenset({"id", "paper", "supplements", "html", "document_version"})
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_SHARD = re.compile(r"(\d+)/(\d+)")


class ManifestError(ValueError):
    """Report every problem found in a manifest."""


@dataclass(frozen=True)
class ManifestEntry:
    """Describe one paper of a manifest.

    Attributes
    ----------
    id : str
        Unique identifier within the manifest.
    paper : Path or str
        Main PDF, or an arXiv request such as ``arXiv:2206.01062``.
    supplements : tuple of Path
        Supplement PDFs.
    html : tuple of Path
        Saved article pages.
    document_version : str or None
        Asserted document version.
    line : int
        Line number in the manifest.
    """

    id: str
    paper: Path | str
    supplements: tuple[Path, ...]
    html: tuple[Path, ...]
    document_version: str | None
    line: int

    def files(self) -> tuple[Path, ...]:
        """List the local files the entry names.

        Returns
        -------
        tuple of Path
            Paper (unless it is an arXiv request), supplements and pages.
        """
        paper = () if isinstance(self.paper, str) else (self.paper,)
        return (*paper, *self.supplements, *self.html)


def _paths(
    value: object, base: Path, key: str, problems: list[str]
) -> tuple[Path, ...]:
    """Read a list of relative paths.

    Parameters
    ----------
    value : object
        Decoded JSON value.
    base : Path
        Manifest directory.
    key : str
        Key, for messages.
    problems : list of str
        Problems to extend.

    Returns
    -------
    tuple of Path
        Absolute paths.
    """
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in cast("list[object]", value)
    ):
        problems.append(f"{key} must be a list of paths")
        return ()
    return tuple((base / item).resolve() for item in cast("list[str]", value))


def _entry(
    data: Mapping[str, object], base: Path, line: int
) -> tuple[ManifestEntry | None, list[str]]:
    """Parse one entry line.

    Parameters
    ----------
    data : Mapping of str to object
        Decoded line.
    base : Path
        Manifest directory.
    line : int
        Line number.

    Returns
    -------
    tuple
        The entry, or None when it is unusable, and its problems.
    """
    problems: list[str] = []
    unknown = sorted(set(data) - _KEYS)
    if unknown:
        problems.append(f"unknown keys {unknown}")
    identifier = data.get("id")
    if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
        problems.append("id must be up to 64 letters, digits, '.', '_' or '-'")
    paper_value = data.get("paper")
    paper: Path | str | None = None
    if not isinstance(paper_value, str) or not paper_value:
        problems.append("paper must name a PDF or an arXiv identifier")
    elif arxiv_request(paper_value) is not None:
        paper = paper_value
    else:
        paper = (base / paper_value).resolve()
    version = data.get("document_version")
    if version is not None and version not in VERSIONS:
        problems.append(f"document_version must be one of {', '.join(VERSIONS)}")
    supplements = _paths(data.get("supplements"), base, "supplements", problems)
    html = _paths(data.get("html"), base, "html", problems)
    local = [paper] if isinstance(paper, Path) else []
    problems.extend(
        f"{path} does not exist"
        for path in (*local, *supplements, *html)
        if not path.is_file()
    )
    if problems or paper is None or not isinstance(identifier, str):
        return None, problems
    entry = ManifestEntry(
        id=identifier,
        paper=paper,
        supplements=supplements,
        html=html,
        document_version=cast("str | None", version),
        line=line,
    )
    return entry, problems


def _repeats(entries: Sequence[ManifestEntry]) -> list[str]:
    """Find repeated identifiers and files.

    Parameters
    ----------
    entries : Sequence of ManifestEntry
        Parsed entries.

    Returns
    -------
    list of str
        One problem per repetition.
    """
    problems: list[str] = []
    seen_ids: dict[str, int] = {}
    seen_files: dict[Path, str] = {}
    for entry in entries:
        if entry.id in seen_ids:
            problems.append(
                f"line {entry.line}: id {entry.id!r} repeats line {seen_ids[entry.id]}"
            )
        seen_ids.setdefault(entry.id, entry.line)
        for file in entry.files():
            if file in seen_files:
                problems.append(
                    f"line {entry.line}: {file.name} is also named by "
                    f"{seen_files[file]!r}"
                )
            seen_files.setdefault(file, entry.id)
    return problems


def read_manifest(path: Path) -> tuple[ManifestEntry, ...]:
    """Read and check a manifest.

    Parameters
    ----------
    path : Path
        Manifest file.

    Returns
    -------
    tuple of ManifestEntry
        Entries in file order.

    Raises
    ------
    ManifestError
        The header is missing or another version, or any line has a
        problem; the message lists every problem with its line number.
    """
    base = path.resolve().parent
    problems: list[str] = []
    entries: list[ManifestEntry] = []
    header_seen = False
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            problems.append(f"line {number}: not JSON ({exc.msg})")
            continue
        if not isinstance(value, dict):
            problems.append(f"line {number}: not a JSON object")
            continue
        data = cast("dict[str, object]", value)
        if not header_seen:
            header_seen = True
            if (
                data.get("schema") != MANIFEST_SCHEMA
                or data.get("schema_version") != MANIFEST_VERSION
            ):
                raise ManifestError(
                    f"{path}: the first line must be the header "
                    f'{{"schema": "{MANIFEST_SCHEMA}", "schema_version": '
                    f"{MANIFEST_VERSION}}}"
                )
            continue
        entry, found = _entry(data, base, number)
        problems.extend(f"line {number}: {problem}" for problem in found)
        if entry is not None:
            entries.append(entry)
    if not header_seen:
        raise ManifestError(f"{path} is empty")
    problems.extend(_repeats(entries))
    if problems:
        raise ManifestError(f"{path}:\n" + "\n".join(problems))
    return tuple(entries)


def parse_shard(text: str) -> tuple[int, int]:
    """Parse a shard selection such as ``1/2``.

    Parameters
    ----------
    text : str
        One-based shard number and shard count.

    Returns
    -------
    tuple of int
        Shard number and count.

    Raises
    ------
    ValueError
        The text is malformed or the number is out of range.

    Examples
    --------
    >>> parse_shard("2/3")
    (2, 3)
    """
    match = _SHARD.fullmatch(text.strip())
    if match is None:
        raise ValueError(f"Shard must look like 1/2, not {text!r}")
    number, count = int(match.group(1)), int(match.group(2))
    if not 1 <= number <= count:
        raise ValueError(f"Shard {number} is outside 1..{count}")
    return number, count


def shard_entries(
    entries: Sequence[ManifestEntry], number: int, count: int
) -> tuple[ManifestEntry, ...]:
    """Select the entries of one shard.

    Parameters
    ----------
    entries : Sequence of ManifestEntry
        Every entry.
    number : int
        One-based shard number.
    count : int
        Shard count.

    Returns
    -------
    tuple of ManifestEntry
        Entries whose identifier's digest falls in the shard, so the split
        is stable when entries are added or reordered.
    """
    return tuple(
        entry
        for entry in entries
        if int(hashlib.sha256(entry.id.encode()).hexdigest(), 16) % count == number - 1
    )


def manifest_text(entries: Iterable[Mapping[str, object]], base: Path) -> str:
    """Write entries as a manifest.

    Parameters
    ----------
    entries : Iterable of Mapping
        Entries with ``id``, ``paper`` and optional lists, as absolute paths
        or arXiv requests.
    base : Path
        Directory of the manifest; paths are written relative to it.

    Returns
    -------
    str
        JSON Lines text with the header.
    """

    def relative(value: object) -> object:
        """Make paths relative to the manifest directory.

        Parameters
        ----------
        value : object
            Entry value.

        Returns
        -------
        object
            The value with paths below the directory made relative.
        """
        if isinstance(value, Path):
            try:
                return value.relative_to(base).as_posix()
            except ValueError:
                return str(value)
        if isinstance(value, list | tuple):
            return [relative(item) for item in cast("list[object]", value)]
        return value

    lines = [
        json.dumps({"schema": MANIFEST_SCHEMA, "schema_version": MANIFEST_VERSION})
    ]
    for entry in entries:
        lines.append(
            json.dumps(
                {key: relative(value) for key, value in entry.items()},
                ensure_ascii=False,
            )
        )
    return "\n".join(lines) + "\n"
