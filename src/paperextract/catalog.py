"""Derive the library catalog from the published paper directories.

The catalog, ``catalog.jsonl`` at the library root, has one row per paper. Each
row is computed from the paper directory's own files, so the catalog can always
be rebuilt from the directories, which remain the source of truth.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import cast

from paperextract.fields import integer, items, mapping, record
from paperextract.formats import require_readable
from paperextract.identity import title_key
from paperextract.names import ascii_fold

__all__ = [
    "CATALOG_FILENAME",
    "CATALOG_ROW_SCHEMA",
    "CATALOG_ROW_VERSION",
    "LAYOUTS",
    "author_names",
    "catalog_row",
    "paper_directories",
    "read_catalog",
    "rebuild_catalog",
    "shard_for",
    "write_catalog",
]

CATALOG_FILENAME = "catalog.jsonl"
CATALOG_ROW_SCHEMA = "paperextract.catalog-row"
# Version 2 adds the abstract, citation key and first-author family name.
CATALOG_ROW_VERSION = 2
# Library layouts: every paper directly in the library, or one directory
# level by validated publication year or by the first author's initial.
LAYOUTS = ("flat", "by-year", "by-initial")
UNVERIFIED_SHARD = "Unverified"


def shard_for(layout: str, *, validated: bool, year: object, family: object) -> str:
    """Name the shard directory a paper belongs in.

    Parameters
    ----------
    layout : str
        One of :data:`LAYOUTS`.
    validated : bool
        Whether the paper's identity is validated.
    year : object
        Validated publication year.
    family : object
        First author's validated family name.

    Returns
    -------
    str
        ``""`` for the flat layout; otherwise the year or the upper-case
        ASCII initial, and ``Unverified`` when the needed field is not
        validated.

    Raises
    ------
    ValueError
        The layout is unknown.

    Examples
    --------
    >>> shard_for("by-year", validated=True, year=2019, family="Travers")
    '2019'
    >>> shard_for("by-initial", validated=True, year=2019, family="Ångström")
    'A'
    >>> shard_for("by-year", validated=False, year=None, family=None)
    'Unverified'
    """
    if layout not in LAYOUTS:
        raise ValueError(f"Unknown library layout {layout!r}")
    if layout == "flat":
        return ""
    if layout == "by-year":
        return str(year) if validated and isinstance(year, int) else UNVERIFIED_SHARD
    if validated and isinstance(family, str):
        initial = next((c for c in ascii_fold(family) if c.isalpha()), None)
        if initial is not None:
            return initial.upper()
    return UNVERIFIED_SHARD


def _load(path: Path) -> dict[str, object]:
    """Read one JSON object.

    Parameters
    ----------
    path : Path
        JSON file.

    Returns
    -------
    dict of str to object
        Parsed object.
    """
    return mapping(json.loads(path.read_text(encoding="utf-8")))


def _field(fields: Mapping[str, object], name: str) -> object:
    """Return the value of a metadata field.

    Parameters
    ----------
    fields : Mapping of str to object
        ``fields`` of ``metadata.json``.
    name : str
        Field name.

    Returns
    -------
    object
        Field value, or None when absent.
    """
    entry = fields.get(name)
    return None if entry is None else mapping(entry).get("value")


def author_names(authors: object) -> list[str]:
    """Format structured authors as ``Given Family`` strings.

    Parameters
    ----------
    authors : object
        List of author objects with ``given``, ``family`` and ``literal``.

    Returns
    -------
    list of str
        Names in order; empty for a missing value.
    """
    names: list[str] = []
    for item in cast("list[object]", authors) if isinstance(authors, list) else []:
        author = mapping(item)
        parts = [str(author.get("given") or ""), str(author.get("family") or "")]
        name = " ".join(part for part in parts if part) or str(
            author.get("literal") or ""
        )
        if name:
            names.append(name)
    return names


def _first_family(authors: object) -> str | None:
    """Return the first author's family or literal name.

    Parameters
    ----------
    authors : object
        List of author objects.

    Returns
    -------
    str or None
        Name, or None without authors.
    """
    if not isinstance(authors, list) or not authors:
        return None
    first = mapping(cast("list[object]", authors)[0])
    name = first.get("family") or first.get("literal")
    return None if name is None else str(name)


def _citation_key(paper: Path) -> str | None:
    """Read the key of the paper's BibTeX entry.

    Parameters
    ----------
    paper : Path
        Paper directory.

    Returns
    -------
    str or None
        Key, or None without ``citation.bib``.
    """
    path = paper / "citation.bib"
    if not path.is_file():
        return None
    head = path.read_text(encoding="utf-8").split("\n", 1)[0]
    return head.split("{", 1)[1].rstrip(",").strip() if "{" in head else None


def catalog_row(
    paper: Path, library_id: object, library: Path | None = None
) -> dict[str, object]:
    """Compute the catalog row of a published paper directory.

    Parameters
    ----------
    paper : Path
        Paper directory with ``manifest.json``, ``metadata.json``,
        ``validation.json``, ``extraction.json`` and ``document.json``.
    library_id : object
        Identifier from the library's ``corpus.json``.
    library : Path or None
        Library root; the row's ``directory`` is relative to it, such as
        ``2019/Travers_2019_HighEnergyPulse`` in a sharded layout. Without
        it the directory is the paper's own name.

    Returns
    -------
    dict of str to object
        Row with identity fields null unless validated and observed values
        labelled as such.
    """
    manifest = _load(paper / "manifest.json")
    metadata = _load(paper / "metadata.json")
    validation = _load(paper / "validation.json")
    extraction = _load(paper / "extraction.json")
    document = _load(paper / "document.json")
    for name, data in (
        ("manifest.json", manifest),
        ("metadata.json", metadata),
        ("validation.json", validation),
        ("extraction.json", extraction),
        ("document.json", document),
    ):
        require_readable(data, f"{paper.name}/{name}")
    fields = mapping(metadata.get("fields", {}))
    identity = mapping(metadata.get("identity", {}))
    observations = mapping(metadata.get("observations", {}))
    entries = [mapping(item) for item in items(observations.get("entries", []))]
    registry = identity.get("registry_record")
    abstract = None if registry is None else mapping(registry).get("abstract")
    sources = [mapping(item) for item in items(extraction["sources"])]
    fingerprint = mapping(sources[0]["fingerprint"])
    title = _field(fields, "title")
    observed = observations.get("title")
    authors = _field(fields, "authors")
    return {
        "schema": CATALOG_ROW_SCHEMA,
        "schema_version": CATALOG_ROW_VERSION,
        "library_id": library_id,
        "directory": paper.name
        if library is None
        else paper.relative_to(library).as_posix(),
        "generation": manifest["generation"],
        "work_id": metadata.get("work_id"),
        "document_version": metadata.get("document_version", "unknown"),
        "doi": identity.get("doi"),
        "arxiv": metadata.get("arxiv"),
        "title": title,
        "title_key": None if title is None else title_key(str(title)),
        "authors": author_names(authors),
        "first_author_family": _first_family(authors),
        "year": _field(fields, "year"),
        "venue": _field(fields, "journal"),
        "abstract": abstract,
        "citation_key": _citation_key(paper),
        "title_observed": observed,
        "title_key_observed": None if observed is None else title_key(str(observed)),
        "authors_observed": [e["value"] for e in entries if e.get("field") == "author"],
        "identifiers_observed": [
            e["value"] for e in entries if e.get("field") == "identifier"
        ],
        "doi_candidates": list(items(fingerprint.get("doi_candidates", []))),
        "source_sha256": [source["sha256"] for source in sources],
        "supplements": [
            {
                "sha256": source["sha256"],
                "text_sha256": mapping(source["fingerprint"]).get("text_sha256"),
                "markdown": supplement["markdown"],
            }
            for source, supplement in zip(
                sources[1:],
                [mapping(item) for item in items(extraction.get("supplements", []))],
                strict=False,
            )
        ],
        "text_sha256": fingerprint.get("text_sha256"),
        "page_count": len(items(document["pages"])),
        "counts": validation.get("counts"),
        "processing_status": validation.get("processing_status"),
        "bibliographic_status": metadata.get("bibliographic_status"),
        "backend": document.get("backend"),
        "backend_version": document.get("backend_version"),
        "run_id": extraction.get("run_id"),
        "published_utc": extraction.get("published_utc"),
    }


def read_catalog(library: Path) -> tuple[dict[str, object], ...]:
    """Read every catalog row of a library.

    Parameters
    ----------
    library : Path
        Library root.

    Returns
    -------
    tuple of dict
        Rows in file order; an absent catalog yields an empty tuple.

    Raises
    ------
    ValueError
        A row has another schema.
    """
    path = library / CATALOG_FILENAME
    if not path.is_file():
        return ()
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = mapping(json.loads(line))
            record(
                {
                    "schema": row.get("schema"),
                    "schema_version": row.get("schema_version"),
                },
                "schema schema_version",
            )
            if row.get("schema") != CATALOG_ROW_SCHEMA:
                raise ValueError("Catalog row has another schema")
            integer(row.get("schema_version"), minimum=1)
            require_readable(row, str(path))
            rows.append(row)
    return tuple(rows)


def write_catalog(library: Path, rows: Iterable[Mapping[str, object]]) -> None:
    """Replace the catalog atomically.

    Parameters
    ----------
    library : Path
        Library root.
    rows : Iterable of Mapping
        Rows in order.
    """
    path = library / CATALOG_FILENAME
    temporary = path.with_name(f".{CATALOG_FILENAME}.tmp")
    temporary.write_text(
        "".join(
            json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def paper_directories(library: Path) -> list[Path]:
    """List the published paper directories of a library.

    Parameters
    ----------
    library : Path
        Library root.

    Returns
    -------
    list of Path
        Directories with a ``manifest.json``, directly in the library or in
        a shard directory one level down, sorted by relative path; the
        private ``.paperextract`` area is skipped.
    """
    if not library.is_dir():
        return []
    found: list[Path] = []
    for path in library.iterdir():
        if not path.is_dir() or path.name.startswith("."):
            continue
        if (path / "manifest.json").is_file():
            found.append(path)
            continue
        found.extend(
            child
            for child in path.iterdir()
            if child.is_dir()
            and not child.name.startswith(".")
            and (child / "manifest.json").is_file()
        )
    return sorted(found, key=lambda path: path.relative_to(library).as_posix())


def rebuild_catalog(
    library: Path, library_id: object, directories: Sequence[Path] | None = None
) -> list[dict[str, object]]:
    """Recompute and write the catalog from the paper directories.

    Parameters
    ----------
    library : Path
        Library root.
    library_id : object
        Identifier from ``corpus.json``.
    directories : Sequence of Path or None
        Paper directories to include, or None for every one.

    Returns
    -------
    list of dict
        Rows written, sorted by directory name.
    """
    papers = paper_directories(library) if directories is None else list(directories)
    rows = [catalog_row(paper, library_id, library) for paper in papers]
    write_catalog(library, rows)
    return rows
