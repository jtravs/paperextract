"""Validate private benchmark inputs and compare explicitly aligned references."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, cast

__all__ = [
    "Benchmark",
    "Observation",
    "Reference",
    "Source",
    "compare",
    "load_benchmark",
]


@dataclass(frozen=True)
class Source:
    """Identify one immutable benchmark document.

    Attributes
    ----------
    id : str
        Unique source identifier.
    work_id : str
        Scientific work identity.
    version_id : str
        Document version; accepted and published versions remain separate.
    path : str
        Relative POSIX path beneath the separately configured corpus root.
    sha256 : str
        Lowercase SHA-256 digest of the original bytes.
    pages : int
        Declared PDF page count, checked against reference locators.
    split : str
        Either development or holdout; not an automatic permission to tune.
    """

    id: str
    work_id: str
    version_id: str
    path: str
    sha256: str
    pages: int
    split: Literal["development", "holdout"]


@dataclass(frozen=True)
class Reference:
    """Retain an expected string and its scientific scope.

    Attributes
    ----------
    id : str
        Unique reference identifier.
    source : Source
        Exact source to which the expectation applies.
    page : int
        One-based PDF page number, not a printed page label.
    locator : str
        Explicit object locator, including table row/column or equation label.
    kind : str
        Either table_cell or equation.
    expected : str
        Exact cell string or reference TeX; an empty cell is explicitly blank.
    header_path : tuple of str
        Ordered table header context, including units; empty for equations.
    review : str
        Candidate or reviewed; comparisons never promote a candidate.
    provenance : str
        Reviewer, date, evidence locator and reference limitations.
    """

    id: str
    source: Source
    page: int
    locator: str
    kind: Literal["table_cell", "equation"]
    expected: str
    header_path: tuple[str, ...]
    review: Literal["candidate", "reviewed"]
    provenance: str


@dataclass(frozen=True)
class Observation:
    """Describe one explicitly aligned extraction observation.

    Attributes
    ----------
    source : Source
        Input identity from the extraction run, not copied from its reference.
    page : int
        One-based input page number.
    locator : str
        Object alignment established by the caller.
    text : str or None
        Raw extracted content; None means missing, an empty string means blank.
    header_path : tuple of str
        Extracted header context; empty for equations or unresolved context.
    """

    source: Source
    page: int
    locator: str
    text: str | None
    header_path: tuple[str, ...] = ()


@dataclass(frozen=True)
class Benchmark:
    """Hold a validated, fingerprinted benchmark specification.

    Attributes
    ----------
    sha256 : str
        Digest of the exact manifest bytes, including reference annotations.
    sources : tuple of Source
        Hashed PDF sources; page counts are declarations, not PDF inspection.
    references : tuple of Reference
        Scoped reference records with independent review status.
    """

    sha256: str
    sources: tuple[Source, ...]
    references: tuple[Reference, ...]


def _record(value: object, keys: str) -> dict[str, object]:
    """Require the exact JSON object fields for this schema version.

    Parameters
    ----------
    value : object
        Decoded JSON value.
    keys : str
        Space-separated required field names.

    Returns
    -------
    dict of str to object
        Validated object with exactly the required fields.
    """
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    record = cast("dict[str, object]", value)
    if set(record) != set(keys.split()):
        raise ValueError(f"Expected exactly these fields: {keys}")
    return record


def _items(value: object) -> list[object]:
    """Require a JSON array at the parsing boundary.

    Parameters
    ----------
    value : object
        Decoded JSON value.

    Returns
    -------
    list of object
        Validated array.
    """
    if not isinstance(value, list):
        raise ValueError("Expected a JSON array")
    return cast("list[object]", value)


def _string(value: object, *, blank: bool = False) -> str:
    """Require a string, permitting explicit blank cells only when requested.

    Parameters
    ----------
    value : object
        Decoded JSON value.
    blank : bool
        Whether an empty or whitespace-only string is permitted.

    Returns
    -------
    str
        Validated string, unchanged.
    """
    if not isinstance(value, str) or (not blank and not value.strip()):
        raise ValueError("Expected a string with content")
    return value


def _positive(value: object) -> int:
    """Reject booleans and nonpositive page counts or locators.

    Parameters
    ----------
    value : object
        Decoded JSON page number or count.

    Returns
    -------
    int
        Strictly positive integer.
    """
    if type(value) is not int or value < 1:
        raise ValueError("Expected a positive integer, not a boolean")
    return value


def _choice(value: object, choices: str) -> str:
    """Restrict persisted vocabulary to the supported schema values.

    Parameters
    ----------
    value : object
        Decoded JSON value.
    choices : str
        Space-separated allowed values.

    Returns
    -------
    str
        Validated vocabulary member.
    """
    result = _string(value)
    if result not in choices.split():
        raise ValueError(f"Expected one of: {choices}")
    return result


def _unique(values: tuple[str, ...]) -> None:
    """Reject duplicates before constructing lookup dictionaries.

    Parameters
    ----------
    values : tuple of str
        Identifiers or paths to check.
    """
    if len(values) != len(set(values)):
        raise ValueError("Duplicate identifier or source path")


def _source(value: object, root: Path) -> Source:
    """Validate identity and verify bytes within the configured corpus root.

    Parameters
    ----------
    value : object
        Decoded source record.
    root : Path
        Resolved local corpus root.

    Returns
    -------
    Source
        Validated source with verified bytes.
    """
    item = _record(value, "id work_id version_id path sha256 pages split")
    source = Source(
        id=_string(item["id"]),
        work_id=_string(item["work_id"]),
        version_id=_string(item["version_id"]),
        path=_string(item["path"]),
        sha256=_string(item["sha256"]),
        pages=_positive(item["pages"]),
        split=cast(
            'Literal["development", "holdout"]',
            _choice(item["split"], "development holdout"),
        ),
    )
    relative = PurePosixPath(source.path)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or "\\" in source.path
        or ":" in source.path
        or relative.as_posix() != source.path
        or source.path == "."
    ):
        raise ValueError(f"Unsafe or noncanonical source path: {source.path}")
    path = (root / source.path).resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError(f"Source escapes corpus root: {source.path}")
    if re.fullmatch(r"[0-9a-f]{64}", source.sha256) is None:
        raise ValueError(f"Invalid SHA-256 for {source.id}")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != source.sha256:
        raise ValueError(f"Source hash mismatch: {source.id}")
    return source


def _reference(value: object, sources: dict[str, Source]) -> Reference:
    """Bind an annotation to its exact source, version and PDF page.

    Parameters
    ----------
    value : object
        Decoded reference record.
    sources : dict of str to Source
        Validated sources indexed by identifier.

    Returns
    -------
    Reference
        Annotation bound to a validated source.
    """
    item = _record(
        value,
        "id source_id version_id source_sha256 page locator kind expected "
        "header_path review provenance",
    )
    source_id = _string(item["source_id"])
    if source_id not in sources:
        raise ValueError(f"Unknown source: {source_id}")
    source = sources[source_id]
    if (item["version_id"], item["source_sha256"]) != (
        source.version_id,
        source.sha256,
    ):
        raise ValueError("Reference version or hash disagrees with its source")
    reference = Reference(
        id=_string(item["id"]),
        source=source,
        page=_positive(item["page"]),
        locator=_string(item["locator"]),
        kind=cast(
            'Literal["table_cell", "equation"]',
            _choice(item["kind"], "table_cell equation"),
        ),
        expected=_string(item["expected"], blank=True),
        header_path=tuple(_string(part) for part in _items(item["header_path"])),
        review=cast(
            'Literal["candidate", "reviewed"]',
            _choice(item["review"], "candidate reviewed"),
        ),
        provenance=_string(item["provenance"]),
    )
    if reference.page > source.pages:
        raise ValueError("Reference page exceeds declared source page count")
    if reference.kind == "table_cell":
        if not reference.header_path:
            raise ValueError("Table references require header context")
    elif reference.header_path or not reference.expected.strip():
        raise ValueError("Equations require content and no table header path")
    return reference


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject repeated JSON keys instead of silently taking the last value.

    Parameters
    ----------
    pairs : list of tuple
        Ordered JSON key/value pairs.

    Returns
    -------
    dict of str to object
        Object without duplicate keys.
    """
    _unique(tuple(key for key, _ in pairs))
    return dict(pairs)


def load_benchmark(path: Path, source_root: Path) -> Benchmark:
    """Load schema version 1 and verify every declared source hash.

    Parameters
    ----------
    path : Path
        UTF-8 JSON benchmark manifest, including reference records.
    source_root : Path
        Existing local corpus root; never taken from manifest data.

    Returns
    -------
    Benchmark
        Immutable validated records and a digest of the manifest bytes.

    Raises
    ------
    ValueError
        Invalid schema, duplicate IDs/keys, unsafe path, hash or scope mismatch.
    OSError
        A manifest or source cannot be read.

    Notes
    -----
    Unknown schema versions and fields are rejected. No migration, PDF parsing,
    network access, source execution or annotation promotion occurs. Sources must
    remain immutable between validation and extraction; recheck before each run.
    """
    raw = path.read_bytes()
    value = cast("object", json.loads(raw, object_pairs_hook=_json_object))
    record = _record(value, "schema schema_version sources references")
    if (record["schema"], record["schema_version"]) != (
        "paperextract.benchmark",
        "1",
    ):
        raise ValueError("Unsupported benchmark schema or version")
    root = source_root.resolve(strict=True)
    sources = tuple(_source(item, root) for item in _items(record["sources"]))
    if not sources:
        raise ValueError("A benchmark requires at least one source")
    _unique(tuple(source.id for source in sources))
    _unique(tuple(source.path for source in sources))
    by_id = {source.id: source for source in sources}
    references = tuple(_reference(item, by_id) for item in _items(record["references"]))
    _unique(tuple(reference.id for reference in references))
    return Benchmark(hashlib.sha256(raw).hexdigest(), sources, references)


def compare(reference: Reference, observation: Observation) -> str:
    """Compare an aligned observation without rewriting scientific content.

    Parameters
    ----------
    reference : Reference
        Expected content, scope and review state from a validated benchmark.
    observation : Observation
        Actual extraction content and independently established alignment.

    Returns
    -------
    str
        One of scope_mismatch, missing, header_mismatch, exact, normalized or mismatch.
        A match to a candidate remains a candidate comparison, not a gold score.

    Notes
    -----
    Cells and their header paths compare exactly, including blank strings and
    numeric precision. Only equations permit CRLF-to-LF conversion and trimming
    ASCII whitespace at the outer boundary. Internal spaces, commands, braces,
    signs, indices, delimiters and Unicode characters remain significant. This
    deliberately conservative rule can flag equivalent TeX for manual review;
    it never establishes mathematical equivalence or expands author macros.
    """
    if (
        observation.source != reference.source
        or observation.page != reference.page
        or observation.locator != reference.locator
    ):
        return "scope_mismatch"
    if observation.text is None:
        return "missing"
    if observation.header_path != reference.header_path:
        return "header_mismatch"
    if observation.text == reference.expected:
        return "exact"
    if reference.kind == "equation":
        expected = reference.expected.replace("\r\n", "\n").strip(" \t\r\n")
        actual = observation.text.replace("\r\n", "\n").strip(" \t\r\n")
        if actual == expected:
            return "normalized"
    return "mismatch"
