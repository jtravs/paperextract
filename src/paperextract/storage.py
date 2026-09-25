"""Publish staged extraction output as a portable paper directory.

A paper directory is assembled completely inside the library's private staging
area, its manifest is written last, and only then is the directory renamed into
place. Every reference inside the directory is relative, so the directory can be
moved between libraries or machines unchanged.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import secrets
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path, PurePosixPath
from typing import cast

from paperextract.bibtex import bibtex_entry, validate_bibtex
from paperextract.capture import read_capture_html, read_capture_record
from paperextract.catalog import (
    CATALOG_FILENAME,
    author_names,
    catalog_row,
    paper_directories,
    read_catalog,
    rebuild_catalog,
    shard_for,
    write_catalog,
)
from paperextract.describe import (
    DESCRIPTIONS_DIRECTORY,
    description_path,
    read_descriptions,
    rendered_description,
)
from paperextract.document import (
    SCHEMA_VERSION,
    Document,
    Equation,
    Figure,
    Finding,
    PageFurniture,
    Table,
    plain_text,
)
from paperextract.export import (
    EXPORT_SCHEMA_VERSION,
    FigureAssets,
    PaperAssets,
    TableAssets,
    processing_status,
    render_markdown,
    review_markdown,
    table_csv,
    title_observation,
    validation_document,
)
from paperextract.fields import dump, items, mapping, string
from paperextract.formats import require_readable
from paperextract.html_article import parse_article
from paperextract.html_check import check_html
from paperextract.identity import (
    Identity,
    arxiv_identifier,
    identity_from_bibtex,
    observe,
    resolve_identity,
)
from paperextract.names import ascii_fold
from paperextract.pdf import UnreadablePdfError, render_region_png
from paperextract.pipeline import (
    DOCUMENT_FILENAME,
    OCR_WORKER_DIRECTORY,
    SOURCE_FILENAME,
    SOURCE_RECORD_FILENAME,
    SOURCE_RECORD_SCHEMA,
    SOURCE_RECORD_VERSION,
    SUPPLEMENTS_DIRECTORY,
    TABLE_CHECK_DIRECTORY,
    WORKER_DIRECTORY,
    BundleOutcome,
    ExtractionSettings,
    PaperSources,
    capture_directories,
    equivalent_directories,
    extract_bundle,
    read_hints,
)
from paperextract.protocol import (
    REQUEST_FILENAME,
    RESULT_FILENAME,
    ExtractionRequest,
    ExtractionResult,
)
from paperextract.registry import Lookup, Search
from paperextract.supplements import SupplementIndex, anchor_ids, find_references
from paperextract.versions import (
    Relation,
    VersionDecision,
    decide_version,
    first_author_family,
    library_relations,
    relation_findings,
)

__all__ = [
    "CATALOG_FILENAME",
    "CORPUS_FILENAME",
    "IDENTITY_FILENAME",
    "INTERNAL_DIRECTORY",
    "ORGANIZE_JOURNAL",
    "SUPPLEMENT_MARKDOWN",
    "Move",
    "PublicationConflictError",
    "PublishedPaper",
    "ensure_library",
    "extract_and_publish",
    "organize_library",
    "paper_directory_name",
    "plan_organize",
    "publish",
    "read_catalog",
    "recover_library",
    "resolve_and_write_identity",
]

CORPUS_FILENAME = "corpus.json"
SUPPLEMENT_MARKDOWN = "supplement.md"
IDENTITY_FILENAME = "identity.json"
INTERNAL_DIRECTORY = ".paperextract"
CORPUS_SCHEMA = "paperextract.corpus"
MANIFEST_SCHEMA = "paperextract.paper-manifest"
_NAME_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "on",
        "in",
        "and",
        "for",
        "to",
        "with",
        "by",
        "at",
        "from",
        "via",
    }
)
_NAME_WORDS = 3
_NAME_BYTES = 96
_MIN_NAME_WORD = 2
HTML_CHECK_FILENAME = "html_check.json"
_RAW_FILES = (
    "middle_json.json",
    "structured_content.json",
    "model_output.json",
    "markdown.md",
    "docling.json",
)


class PublicationConflictError(FileExistsError):
    """Report that the library already holds a directory with this name."""


@dataclass(frozen=True)
class PublishedPaper:
    """Describe a paper directory that was renamed into the library.

    Attributes
    ----------
    directory : Path
        Final paper directory.
    name : str
        Directory within the library as a relative POSIX path: the paper's
        name, below its shard directory in a sharded layout.
    generation : str
        Identifier of this publication generation.
    files : tuple of str
        Relative POSIX paths recorded in ``manifest.json``.
    """

    directory: Path
    name: str
    generation: str
    files: tuple[str, ...]


def _name_part(text: str) -> str:
    """Fold a name fragment to ASCII letters and digits with a capital.

    Parameters
    ----------
    text : str
        Unicode fragment.

    Returns
    -------
    str
        Filesystem-safe fragment, possibly empty.
    """
    folded = "".join(ch for ch in ascii_fold(text) if ch.isalnum())
    return folded[:1].upper() + folded[1:] if folded else ""


def paper_directory_name(
    identity: Identity, source_sha256: str, taken: Mapping[str, set[str]] | None = None
) -> str:
    """Name a paper directory from validated identity, or from the digest.

    Parameters
    ----------
    identity : Identity
        Bibliographic identity.
    source_sha256 : str
        Digest of the preserved source.
    taken : Mapping of str to set of str or None
        Names already used in the library, each with the source digests it
        holds; a collision with another source receives a digest suffix.

    Returns
    -------
    str
        ``Family_Year_FirstWords`` for a validated identity, suffixed with six
        digest characters when another source already uses that name;
        otherwise ``Unverified_`` plus the first twelve digest characters.
    """
    if not identity.named():
        return f"Unverified_{source_sha256[:12]}"
    authors = identity.field("authors")
    first: Mapping[str, object] = {}
    if isinstance(authors, list) and authors:
        first = mapping(cast("list[object]", authors)[0])
    family = (
        _name_part(str(first.get("family") or first.get("literal") or ""))
        or "Anonymous"
    )
    year = identity.field("year")
    year_text = str(year) if isinstance(year, int) else "nodate"
    title = str(identity.field("title") or "")
    words = [
        _name_part(word)
        for word in "".join(ch if ch.isalnum() else " " for ch in title).split()
        if word.lower() not in _NAME_STOP_WORDS and len(word) >= _MIN_NAME_WORD
    ]
    short = "".join([word for word in words if word][:_NAME_WORDS]) or "Untitled"
    name = f"{family}_{year_text}_{short}"
    while len(name.encode("utf-8")) > _NAME_BYTES:
        name = name[:-1]
    if taken and name in taken and source_sha256 not in taken[name]:
        name = f"{name}_{source_sha256[:6]}"
    return name


def _utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string.

    Returns
    -------
    str
        Timestamp with second precision.
    """
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def _candidates(fingerprint: Mapping[str, object], key: str) -> list[str]:
    """Read a list of identifier candidates from a serialized fingerprint.

    Parameters
    ----------
    fingerprint : Mapping of str to object
        Serialized content fingerprint.
    key : str
        ``doi_candidates`` or ``arxiv_candidates``.

    Returns
    -------
    list of str
        Candidates, or an empty list when absent.
    """
    value = fingerprint.get(key)
    return [] if value is None else [string(item) for item in items(value)]


def ensure_library(library: Path) -> dict[str, object]:
    """Create or read the library's corpus record.

    Parameters
    ----------
    library : Path
        Library root; created if missing.

    Returns
    -------
    dict of str to object
        Parsed ``corpus.json``.

    Raises
    ------
    ValueError
        An existing ``corpus.json`` has another schema.
    """
    library.mkdir(parents=True, exist_ok=True)
    (library / INTERNAL_DIRECTORY / "staging").mkdir(parents=True, exist_ok=True)
    corpus_path = library / CORPUS_FILENAME
    if corpus_path.exists():
        corpus = mapping(json.loads(corpus_path.read_text()))
        if corpus.get("schema") != CORPUS_SCHEMA:
            raise ValueError(f"{corpus_path} is not a {CORPUS_SCHEMA} document")
        require_readable(corpus, str(corpus_path))
        return corpus
    corpus: dict[str, object] = {
        "schema": CORPUS_SCHEMA,
        "schema_version": 1,
        "library_id": secrets.token_hex(8),
        "layout": "flat",
        "created_utc": _utc_now(),
    }
    corpus_path.write_text(dump(corpus))
    return corpus


@dataclass(frozen=True)
class _Staged:
    """Hold everything read from an extraction staging directory."""

    staging: Path
    document: Document
    request: ExtractionRequest
    result: ExtractionResult
    source_record: dict[str, object]
    identity: Identity
    supplements: tuple[_Staged, ...] = ()

    @property
    def ocr_directory(self) -> Path:
        """Return the table OCR worker directory, which may not exist.

        Returns
        -------
        Path
            ``worker-ocr`` inside the staging directory.
        """
        return self.staging / OCR_WORKER_DIRECTORY

    @property
    def check_directory(self) -> Path:
        """Return the Docling table-check worker directory, which may not exist.

        Returns
        -------
        Path
            ``worker-check`` inside the staging directory.
        """
        return self.staging / TABLE_CHECK_DIRECTORY


def _read_staging(staging: Path, *, with_supplements: bool = True) -> _Staged:
    """Load and validate the artifacts of one extraction staging directory.

    Parameters
    ----------
    staging : Path
        Directory written by the pipeline.
    with_supplements : bool
        Also read the supplement staging directories below ``supplements/``.

    Returns
    -------
    _Staged
        Parsed artifacts.

    Raises
    ------
    ValueError
        The source record has another schema or the worker result failed.
    """
    source_record = mapping(json.loads((staging / SOURCE_RECORD_FILENAME).read_text()))
    if (
        source_record.get("schema") != SOURCE_RECORD_SCHEMA
        or source_record.get("schema_version") != SOURCE_RECORD_VERSION
    ):
        raise ValueError("Unsupported source record schema")
    document = Document.from_json((staging / DOCUMENT_FILENAME).read_text())
    worker = staging / WORKER_DIRECTORY
    request = ExtractionRequest.from_json((worker / REQUEST_FILENAME).read_text())
    result = ExtractionResult.from_json((worker / RESULT_FILENAME).read_text())
    if result.status != "completed":
        raise ValueError("Only a completed extraction can be published")
    if string(source_record["sha256"]) != document.source_sha256:
        raise ValueError("Source record and document disagree on the source digest")
    identity_path = staging / IDENTITY_FILENAME
    identity = (
        Identity.from_json(identity_path.read_text())
        if identity_path.is_file()
        else Identity.unverified("identity stage did not run", observe(document))
    )
    supplements: tuple[_Staged, ...] = ()
    directory = staging / SUPPLEMENTS_DIRECTORY
    if with_supplements and directory.is_dir():
        supplements = tuple(
            _read_staging(path, with_supplements=False)
            for path in sorted(p for p in directory.iterdir() if p.is_dir())
        )
    return _Staged(
        staging, document, request, result, source_record, identity, supplements
    )


def _copy(source: Path, target: Path) -> str:
    """Copy a file into the build directory and return its digest.

    Parameters
    ----------
    source : Path
        Existing file.
    target : Path
        Destination inside the build directory.

    Returns
    -------
    str
        SHA-256 of the copied bytes.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with source.open("rb") as reader, target.open("xb") as writer:
        while chunk := reader.read(1024 * 1024):
            writer.write(chunk)
            digest.update(chunk)
    return digest.hexdigest()


def _write(target: Path, content: str | bytes) -> None:
    """Write a new file inside the build directory.

    Parameters
    ----------
    target : Path
        Destination; parents are created.
    content : str or bytes
        Text is written as UTF-8.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        target.write_text(content, encoding="utf-8")
    else:
        target.write_bytes(content)


@dataclass
class _Build:
    """Accumulate asset paths and notes while assembling a paper directory."""

    root: Path
    staged: _Staged
    dpi: int
    figures: dict[str, FigureAssets]
    tables: dict[str, TableAssets]
    equations: dict[str, str]
    notes: list[str]
    descriptions: dict[str, str] = field(default_factory=dict[str, str])
    extra_sources: list[dict[str, object]] = field(
        default_factory=list[dict[str, object]]
    )
    html_checks: list[dict[str, object]] = field(
        default_factory=list[dict[str, object]]
    )
    html_findings: list[Finding] = field(default_factory=list[Finding])
    version: VersionDecision = field(
        default_factory=lambda: VersionDecision("unknown", ())
    )
    arxiv: str | None = None
    relations: tuple[Relation, ...] = ()

    def native(self, relative: str | None) -> Path | None:
        """Resolve a worker asset path, returning None when it is unavailable.

        Parameters
        ----------
        relative : str or None
            Path recorded in the canonical document, relative to the worker
            staging directory.

        Returns
        -------
        Path or None
            Existing file, or None with a note recorded.
        """
        if relative is None:
            return None
        path = self.staged.staging / WORKER_DIRECTORY / relative
        if not path.is_file():
            self.notes.append(f"asset {relative} was not available for copying")
            return None
        return path

    def export_figure(self, figure: Figure) -> None:
        """Render the complete-figure crop and copy the panel crops.

        Parameters
        ----------
        figure : Figure
            Canonical figure.
        """
        context: str | None = None
        if figure.context_bbox_pt is not None:
            try:
                rendered = render_region_png(
                    self.staged.staging / SOURCE_FILENAME,
                    figure.page,
                    figure.context_bbox_pt,
                    dpi=self.dpi,
                )
            except (ValueError, UnreadablePdfError) as exc:
                self.notes.append(
                    f"figure {figure.id}: context crop not rendered: {exc}"
                )
            else:
                context = f"figures/{figure.id}.png"
                _write(self.root / context, rendered.png)
        else:
            self.notes.append(f"figure {figure.id}: no geometry for a context crop")
        panels: list[str | None] = []
        for index, panel in enumerate(figure.panels, 1):
            source = self.native(panel.asset)
            if source is None:
                panels.append(None)
                continue
            relative = f"figures/{figure.id}_p{index:02d}{source.suffix.lower()}"
            _copy(source, self.root / relative)
            panels.append(relative)
        self.figures[figure.id] = FigureAssets(context=context, panels=tuple(panels))

    def export_description(self, figure: Figure) -> None:
        """Copy a figure's description records and render the latest one.

        Parameters
        ----------
        figure : Figure
            Canonical figure.
        """
        source = description_path(self.staged.staging, figure.id)
        if not source.is_file():
            return
        records = read_descriptions(source)
        _copy(source, description_path(self.root, figure.id))
        rendered = rendered_description(records)
        if rendered is not None:
            self.descriptions[figure.id] = rendered

    def export_table(self, table: Table) -> None:
        """Write the table's HTML, exact cells, optional CSV and crop.

        Parameters
        ----------
        table : Table
            Canonical table.
        """
        base = f"tables/{table.id}"
        caption = "" if table.caption is None else plain_text(table.caption)
        notes = "".join(f"<p>{plain_text(note)}</p>\n" for note in table.footnotes)
        html = (
            '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            f"<title>Table {table.label or ''}</title>\n</head>\n<body>\n"
            f"<p>{caption}</p>\n{table.html}\n{notes}</body>\n</html>\n"
        )
        _write(self.root / f"{base}.html", html)
        cells: dict[str, object] = {
            "schema": "paperextract.table",
            "schema_version": EXPORT_SCHEMA_VERSION,
        }
        cells.update(table.to_dict())
        _write(self.root / f"{base}.json", dump(cells))
        csv_text = table_csv(table)
        csv_path = None
        if csv_text is not None:
            csv_path = f"{base}.csv"
            _write(self.root / csv_path, csv_text)
        image = None
        source = self.native(table.asset)
        if source is not None:
            image = f"{base}{source.suffix.lower()}"
            _copy(source, self.root / image)
        self.tables[table.id] = TableAssets(
            html=f"{base}.html", cells=f"{base}.json", csv=csv_path, image=image
        )

    def export_equation(self, equation: Equation) -> None:
        """Copy an equation crop when the worker produced one.

        Parameters
        ----------
        equation : Equation
            Canonical equation.
        """
        source = self.native(equation.asset)
        if source is not None:
            relative = f"equations/{equation.id}{source.suffix.lower()}"
            _copy(source, self.root / relative)
            self.equations[equation.id] = relative


def _author_names(identity: Identity) -> list[str]:
    """List validated author names as ``Given Family`` strings.

    Parameters
    ----------
    identity : Identity
        Bibliographic identity.

    Returns
    -------
    list of str
        Names in order; empty when identity is not validated.
    """
    return author_names(identity.field("authors"))


def _front_matter(
    build: _Build, source_path: str, run_id: str, application_version: str
) -> dict[str, object]:
    """Assemble the compact metadata projection for ``paper.md``.

    Parameters
    ----------
    build : _Build
        Current build.
    source_path : str
        Relative path of the preserved source inside the paper directory.
    run_id : str
        Worker request identifier.
    application_version : str
        Installed paperextract version.

    Returns
    -------
    dict of str to object
        Front matter values; identity fields are null unless validated.
    """
    document = build.staged.document
    identity = build.staged.identity
    fingerprint = mapping(build.staged.source_record["fingerprint"])
    observed_authors = [m.value for m in document.metadata if m.field == "author"]
    identifiers = [m.value for m in document.metadata if m.field == "identifier"]
    return {
        "schema_version": SCHEMA_VERSION,
        "export_schema_version": EXPORT_SCHEMA_VERSION,
        "document_id": f"doc_{document.source_sha256[:12]}",
        "work_id": identity.work_id,
        "title": identity.field("title"),
        "authors": _author_names(identity),
        "journal": identity.field("journal"),
        "year": identity.field("year"),
        "volume": identity.field("volume"),
        "issue": identity.field("issue"),
        "pages": identity.field("pages"),
        "article_number": identity.field("article_number"),
        "doi": identity.doi,
        "arxiv": build.arxiv,
        "document_version": build.version.version,
        "bibliographic_status": identity.status,
        "processing_status": processing_status(document),
        "observed": {
            "title": title_observation(document),
            "authors": observed_authors,
            "identifiers": identifiers,
            "doi_candidates": _candidates(fingerprint, "doi_candidates"),
        },
        "sources": [
            {
                "id": "source_01",
                "role": "main",
                "type": "pdf",
                "sha256": document.source_sha256,
                "path": source_path,
                "pages": len(document.pages),
            },
            *(
                {
                    "id": _source_id(index),
                    "role": "supplement",
                    "type": "pdf",
                    "sha256": item.document.source_sha256,
                    "path": _original_path(item, index),
                    "pages": len(item.document.pages),
                    "markdown": _supplement_markdown(index),
                }
                for index, item in enumerate(build.staged.supplements, 1)
            ),
            *(
                {key: entry[key] for key in ("id", "role", "type", "sha256", "path")}
                for entry in build.extra_sources
            ),
        ],
        "supplements": [
            _supplement_markdown(index)
            for index in range(1, len(build.staged.supplements) + 1)
        ],
        "extraction": {
            "run_id": run_id,
            "application_version": application_version,
            "adapters": [
                {"name": document.backend, "version": document.backend_version}
            ],
            "timestamp": _utc_now(),
        },
        "validation_report": "validation.json",
    }


def _metadata_document(
    document: Document,
    identity: Identity,
    fingerprint: Mapping[str, object],
    bibliography: Mapping[str, object],
) -> dict[str, object]:
    """Build ``metadata.json`` with field-level status and evidence.

    Parameters
    ----------
    document : Document
        Canonical document.
    identity : Identity
        Bibliographic identity.
    fingerprint : Mapping of str to object
        Serialized content fingerprint.
    bibliography : Mapping of str to object
        BibTeX validation outcome.

    Returns
    -------
    dict of str to object
        Metadata document, schema version 2.
    """
    return {
        "schema": "paperextract.metadata",
        "schema_version": 2,
        "bibliographic_status": identity.status,
        "work_id": identity.work_id,
        "document_version": "unknown",
        "fields": {item.name: item.to_dict() for item in identity.fields},
        "identity": {
            "doi": identity.doi,
            "provider": identity.provider,
            "reasons": list(identity.reasons),
            "checks": [check.to_dict() for check in identity.checks],
            "candidates": [candidate.to_dict() for candidate in identity.candidates],
            "alternatives": [dict(item) for item in identity.alternatives],
            "registry_record": None
            if identity.record is None
            else dict(identity.record),
        },
        "observations": {
            "title": title_observation(document),
            "entries": [m.to_dict() for m in document.metadata],
            "doi_candidates": _candidates(fingerprint, "doi_candidates"),
            "arxiv_candidates": _candidates(fingerprint, "arxiv_candidates"),
        },
        "bibliography_validation": dict(bibliography),
        "note": (
            "Field values come from the accepted registry record; observations are "
            "article-local evidence. Document version is not established."
        ),
    }


def _extraction_document(
    build: _Build, source_path: str, application_version: str
) -> dict[str, object]:
    """Build ``extraction.json`` with source, run and asset provenance.

    Parameters
    ----------
    build : _Build
        Current build.
    source_path : str
        Relative path of the preserved source inside the paper directory.
    application_version : str
        Installed paperextract version.

    Returns
    -------
    dict of str to object
        Provenance document.
    """
    staged = build.staged
    omitted = [
        {
            "id": block.id,
            "role": block.role,
            "page": block.span.page,
            "text": plain_text(block.runs),
            "reason": "page furniture omitted from paper.md",
        }
        for block in staged.document.blocks
        if isinstance(block, PageFurniture)
    ]
    return {
        "schema": "paperextract.extraction",
        "schema_version": EXPORT_SCHEMA_VERSION,
        "run_id": staged.request.request_id,
        "application_version": application_version,
        "assertions": read_hints(staged.staging),
        "published_utc": _utc_now(),
        "sources": [
            {
                "id": "source_01",
                "type": "pdf",
                "sha256": staged.document.source_sha256,
                "size_bytes": staged.source_record["size_bytes"],
                "original_name": staged.source_record["original_name"],
                "path": source_path,
                "inspection": staged.source_record["inspection"],
                "fingerprint": staged.source_record["fingerprint"],
            },
            *(
                {
                    "id": _source_id(index),
                    "role": "supplement",
                    "type": "pdf",
                    "sha256": item.document.source_sha256,
                    "size_bytes": item.source_record["size_bytes"],
                    "original_name": item.source_record["original_name"],
                    "path": _original_path(item, index),
                    "inspection": item.source_record["inspection"],
                    "fingerprint": item.source_record["fingerprint"],
                }
                for index, item in enumerate(staged.supplements, 1)
            ),
            *build.extra_sources,
        ],
        "request": staged.request.to_dict(),
        "result": staged.result.to_dict(),
        "table_ocr": _ocr_record(staged.ocr_directory),
        "table_check": _ocr_record(staged.check_directory),
        "supplements": [
            {
                "id": _supplement_id(index),
                "source": _source_id(index),
                "markdown": _supplement_markdown(index),
                "request": item.request.to_dict(),
                "result": item.result.to_dict(),
                "table_ocr": _ocr_record(item.ocr_directory),
                "table_check": _ocr_record(item.check_directory),
            }
            for index, item in enumerate(staged.supplements, 1)
        ],
        "assets": {
            "figures": {
                key: {"context": value.context, "panels": list(value.panels)}
                for key, value in build.figures.items()
            },
            "tables": {
                key: {"html": v.html, "cells": v.cells, "csv": v.csv, "image": v.image}
                for key, v in build.tables.items()
            },
            "equations": dict(build.equations),
            "render_dpi": build.dpi,
        },
        "asset_notes": list(build.notes),
        "omitted_blocks": omitted,
    }


def _manifest(
    root: Path, name: str, generation: str
) -> tuple[dict[str, object], tuple[str, ...]]:
    """Hash every file in the build directory.

    Parameters
    ----------
    root : Path
        Build directory.
    name : str
        Final directory name.
    generation : str
        Publication generation identifier.

    Returns
    -------
    tuple
        Manifest document and the recorded relative paths.
    """
    files: list[dict[str, object]] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        files.append(
            {"path": relative, "sha256": digest, "size_bytes": path.stat().st_size}
        )
    manifest: dict[str, object] = {
        "schema": MANIFEST_SCHEMA,
        "schema_version": EXPORT_SCHEMA_VERSION,
        "directory": name,
        "generation": generation,
        "created_utc": _utc_now(),
        "files": files,
    }
    return manifest, tuple(str(item["path"]) for item in files)


def _source_id(index: int) -> str:
    """Name the preserved source of a supplement.

    Parameters
    ----------
    index : int
        One-based supplement number.

    Returns
    -------
    str
        ``source_02`` for the first supplement; ``source_01`` is the paper.
    """
    return f"source_{index + 1:02d}"


def _supplement_id(index: int) -> str:
    """Name the directory of a supplement inside the paper directory.

    Parameters
    ----------
    index : int
        One-based supplement number.

    Returns
    -------
    str
        ``supplement_01`` and so on.
    """
    return f"supplement_{index:02d}"


def _supplement_markdown(index: int) -> str:
    """Return the relative path of a supplement's Markdown.

    Parameters
    ----------
    index : int
        One-based supplement number.

    Returns
    -------
    str
        Path relative to the paper directory.
    """
    return f"{_supplement_id(index)}/{SUPPLEMENT_MARKDOWN}"


def _original_path(staged: _Staged, index: int) -> str:
    """Return where a supplement's preserved PDF is published.

    Parameters
    ----------
    staged : _Staged
        Supplement staging artifacts.
    index : int
        One-based supplement number.

    Returns
    -------
    str
        Relative path below ``original/``.
    """
    name = PurePosixPath(string(staged.source_record["original_name"])).name
    return f"original/{_source_id(index)}/{name or SOURCE_FILENAME}"


def _ocr_record(directory: Path) -> dict[str, object] | None:
    """Describe a second worker run of one component, if it ran.

    Parameters
    ----------
    directory : Path
        Worker directory of the table OCR re-extraction or table check.

    Returns
    -------
    dict of str to object or None
        Request and result documents, or None when no such run happened.
    """
    result_path = directory / RESULT_FILENAME
    if not result_path.is_file():
        return None
    return {
        "request": ExtractionRequest.from_json(
            (directory / REQUEST_FILENAME).read_text()
        ).to_dict(),
        "result": ExtractionResult.from_json(result_path.read_text()).to_dict(),
    }


def _copy_raw(staged: _Staged, target: Path) -> None:
    """Copy one component's native output, worker logs and identity.

    Parameters
    ----------
    staged : _Staged
        Component staging artifacts.
    target : Path
        Diagnostics directory for this component.
    """
    for worker, destination in (
        (staged.staging / WORKER_DIRECTORY, target),
        (staged.ocr_directory, target / "table-ocr"),
        (staged.check_directory, target / "table-check"),
    ):
        for filename in _RAW_FILES:
            candidate = worker / "native" / filename
            if candidate.is_file():
                _copy(candidate, destination / filename)
        for filename in (
            REQUEST_FILENAME,
            RESULT_FILENAME,
            "worker.stdout.log",
            "worker.stderr.log",
        ):
            candidate = worker / filename
            if candidate.is_file():
                _copy(candidate, destination / filename)
    identity_path = staged.staging / IDENTITY_FILENAME
    if identity_path.is_file():
        _copy(identity_path, target / IDENTITY_FILENAME)


def _export_assets(build: _Build) -> None:
    """Export the figures, tables and equations of one component.

    Parameters
    ----------
    build : _Build
        Build of the paper or of one supplement.
    """
    figures: set[str] = set()
    for block in build.staged.document.blocks:
        if isinstance(block, Figure):
            build.export_figure(block)
            build.export_description(block)
            figures.add(block.id)
        elif isinstance(block, Table):
            build.export_table(block)
        elif isinstance(block, Equation):
            build.export_equation(block)
    kept = build.staged.staging / DESCRIPTIONS_DIRECTORY
    if kept.is_dir():
        # A rebuild can change figure geometry and so its identifier; the old
        # description then describes an image that no longer exists.
        build.notes.extend(
            f"description {path.name} not published: figure {path.stem} is no "
            "longer in the document"
            for path in sorted(kept.glob("*.json"))
            if path.stem not in figures
        )


def _paper_assets(
    build: _Build,
    links: SupplementIndex | None,
    anchors: Mapping[str, str] | None = None,
) -> PaperAssets:
    """Collect the rendering inputs of one component.

    Parameters
    ----------
    build : _Build
        Completed component build.
    links : SupplementIndex or None
        Supplement targets for references in the text.
    anchors : Mapping of str to str or None
        Anchors written before referenceable blocks.

    Returns
    -------
    PaperAssets
        Asset paths, anchors and link targets.
    """
    return PaperAssets(
        figures=build.figures,
        tables=build.tables,
        equations=build.equations,
        anchors=dict(anchors or {}),
        descriptions=build.descriptions,
        links=links,
    )


def _assemble_supplement(
    build: _Build, index: int, staged: _Staged, run_id: str
) -> tuple[_Build, str]:
    """Write one supplement's original, document and assets.

    Parameters
    ----------
    build : _Build
        Build of the paper.
    index : int
        One-based supplement number.
    staged : _Staged
        Supplement staging artifacts.
    run_id : str
        Worker request identifier of the paper.

    Returns
    -------
    tuple
        The supplement's build and its original's relative path.

    Raises
    ------
    ValueError
        The preserved supplement changed before publication.
    """
    original = _original_path(staged, index)
    if (
        _copy(staged.staging / SOURCE_FILENAME, build.root / original)
        != staged.document.source_sha256
    ):
        raise ValueError("Preserved supplement digest changed before publication")
    sub = _Build(
        build.root / _supplement_id(index), staged, build.dpi, {}, {}, {}, build.notes
    )
    sub.root.mkdir()
    shutil.copyfile(staged.staging / DOCUMENT_FILENAME, sub.root / DOCUMENT_FILENAME)
    _export_assets(sub)
    _copy_raw(
        staged, build.root / "diagnostics" / "raw" / run_id / _supplement_id(index)
    )
    return sub, original


def _supplement_front_matter(
    staged: _Staged, index: int, original: str, paper: Document
) -> dict[str, object]:
    """Assemble the front matter of a supplement's Markdown.

    Parameters
    ----------
    staged : _Staged
        Supplement staging artifacts.
    index : int
        One-based supplement number.
    original : str
        Relative path of the preserved supplement inside the paper directory.
    paper : Document
        Main paper document.

    Returns
    -------
    dict of str to object
        Front matter values.
    """
    document = staged.document
    return {
        "schema_version": SCHEMA_VERSION,
        "export_schema_version": EXPORT_SCHEMA_VERSION,
        "document_id": f"doc_{document.source_sha256[:12]}",
        "role": "supplement",
        "supplement_to": f"doc_{paper.source_sha256[:12]}",
        "paper": "../paper.md",
        "processing_status": processing_status(document),
        "source": {
            "id": _source_id(index),
            "sha256": document.source_sha256,
            "path": f"../{original}",
            "pages": len(document.pages),
        },
        "run_id": staged.request.request_id,
    }


def _references_report(
    document: Document, links: SupplementIndex | None
) -> dict[str, object]:
    """Summarize references from the paper to its supplementary material.

    Parameters
    ----------
    document : Document
        Main paper document.
    links : SupplementIndex or None
        Supplement targets, or None without supplements.

    Returns
    -------
    dict of str to object
        Counts and the unresolved references; without supplements every
        reference is unresolved because no supplement was supplied.
    """
    references = find_references(document, links or SupplementIndex({}))
    unresolved = [
        {"block_id": item.block_id, "text": item.text}
        for item in references
        if item.target is None
    ]
    return {
        "resolved": len(references) - len(unresolved),
        "unresolved": unresolved,
    }


def _publish_captures(build: _Build) -> None:
    """Copy the saved web pages into ``original/`` and compare each one.

    Parameters
    ----------
    build : _Build
        Build state; its capture entries, check reports and findings are
        filled in.
    """
    staged = build.staged
    identity = staged.identity
    title = identity.field("title")
    for index, directory in enumerate(
        capture_directories(staged.staging), len(staged.supplements) + 1
    ):
        capture = read_capture_record(directory)
        source_id = _source_id(index)
        for item in capture.files:
            digest = _copy(
                directory / item.path, build.root / "original" / source_id / item.path
            )
            if digest != item.sha256:
                raise ValueError(f"Capture file {item.path} changed before publication")
        build.extra_sources.append(
            {
                "id": source_id,
                "role": "html_capture",
                "type": capture.kind,
                "sha256": capture.sha256,
                "original_name": capture.original_name,
                "path": f"original/{source_id}/{capture.main}",
                "capture": capture.to_dict(),
            }
        )
        article = parse_article(read_capture_html(directory, capture))
        check = check_html(
            staged.document,
            article,
            capture,
            source_id=source_id,
            doi=identity.doi,
            title=title if isinstance(title, str) else None,
        )
        build.html_checks.append(check.report)
        build.html_findings.extend(check.findings)


def _publish_equivalents(build: _Build) -> None:
    """Copy the content-equivalent PDFs of the paper into ``original/``.

    Parameters
    ----------
    build : _Build
        Build state; source entries are added for each copy.
    """
    staged = build.staged
    first = len(staged.supplements) + len(build.extra_sources) + 1
    for index, directory in enumerate(equivalent_directories(staged.staging), first):
        record = mapping(json.loads((directory / SOURCE_RECORD_FILENAME).read_text()))
        name = PurePosixPath(string(record["original_name"])).name or SOURCE_FILENAME
        source_id = _source_id(index)
        path = f"original/{source_id}/{name}"
        if _copy(directory / SOURCE_FILENAME, build.root / path) != record["sha256"]:
            raise ValueError(f"Equivalent copy {name} changed before publication")
        build.extra_sources.append(
            {
                "id": source_id,
                "role": "equivalent_copy",
                "type": "pdf",
                "sha256": record["sha256"],
                "size_bytes": record["size_bytes"],
                "original_name": record["original_name"],
                "path": path,
                "inspection": record["inspection"],
                "fingerprint": record["fingerprint"],
            }
        )


def _write_reports(
    build: _Build,
    supplements: Sequence[tuple[_Build, str]],
    links: SupplementIndex | None,
) -> None:
    """Write ``diagnostics/review.md``, ``validation.json`` and the HTML check.

    Parameters
    ----------
    build : _Build
        Build state after the captures were compared.
    supplements : Sequence of tuple
        Supplement builds with their original paths.
    links : SupplementIndex or None
        Supplement anchors for the reference report.
    """
    document = build.staged.document
    identity = build.staged.identity
    review = review_markdown(document)
    related = relation_findings(build.relations, build.version.version)
    if related:
        review += "\n# Related papers in the library\n\n" + "".join(
            f"- **{finding.code}** ({finding.severity}): {finding.message}\n"
            for finding in related
        )
    for index, (sub, _) in enumerate(supplements, 1):
        supplement_review = review_markdown(sub.staged.document)
        review += "\n" + supplement_review.replace(
            "# Review", f"# Review of {_supplement_id(index)}", 1
        )
    if build.html_findings:
        review += "\n# HTML cross-check\n\n" + "".join(
            f"- **{finding.code}** ({finding.severity}): {finding.message}\n"
            for finding in build.html_findings
        )
    _write(build.root / "diagnostics" / "review.md", review)
    validation = validation_document(
        document,
        bibliographic_status=identity.status,
        identity_detail="; ".join(identity.reasons) or "no reason recorded",
    )
    validation["supplements"] = [
        {"id": _supplement_id(index), **validation_document(sub.staged.document)}
        for index, (sub, _) in enumerate(supplements, 1)
    ]
    validation["supplement_references"] = _references_report(document, links)
    validation["relations"] = {
        "related": [relation.to_dict() for relation in build.relations],
        "findings": [finding.to_dict() for finding in related],
    }
    if build.html_checks:
        validation["html_check"] = {
            "report": HTML_CHECK_FILENAME,
            "findings": [finding.to_dict() for finding in build.html_findings],
        }
        _write(
            build.root / HTML_CHECK_FILENAME,
            dump(
                {
                    "schema": "paperextract.html-checks",
                    "schema_version": 1,
                    "captures": build.html_checks,
                }
            ),
        )
    _write(build.root / "validation.json", dump(validation))


def _assemble(build: _Build, name: str, generation: str) -> tuple[str, ...]:
    """Write every file of the paper directory into the build root.

    Parameters
    ----------
    build : _Build
        Build state with an empty root directory.
    name : str
        Final directory name.
    generation : str
        Publication generation identifier.

    Returns
    -------
    tuple of str
        Relative paths recorded in the manifest.
    """
    staged = build.staged
    document = staged.document
    identity = staged.identity
    original_name = PurePosixPath(string(staged.source_record["original_name"])).name
    source_path = f"original/source_01/{original_name or SOURCE_FILENAME}"
    digest = _copy(staged.staging / SOURCE_FILENAME, build.root / source_path)
    if digest != document.source_sha256:
        raise ValueError("Preserved source digest changed before publication")
    shutil.copyfile(staged.staging / DOCUMENT_FILENAME, build.root / DOCUMENT_FILENAME)
    _export_assets(build)
    run_id = staged.request.request_id
    _copy_raw(staged, build.root / "diagnostics" / "raw" / run_id)
    version = metadata.version("paperextract")
    supplements = [
        _assemble_supplement(build, index, item, run_id)
        for index, item in enumerate(staged.supplements, 1)
    ]
    entries = [
        (_supplement_markdown(index), sub.staged.document)
        for index, (sub, _) in enumerate(supplements, 1)
    ]
    links = SupplementIndex.build(entries) if entries else None
    _publish_captures(build)
    _publish_equivalents(build)
    for index, (sub, original) in enumerate(supplements, 1):
        own = [
            (
                SUPPLEMENT_MARKDOWN
                if number == index
                else f"../{_supplement_markdown(number)}",
                item,
            )
            for number, (_, item) in enumerate(entries, 1)
        ]
        _write(
            sub.root / SUPPLEMENT_MARKDOWN,
            render_markdown(
                sub.staged.document,
                _supplement_front_matter(sub.staged, index, original, document),
                _paper_assets(
                    sub,
                    SupplementIndex.build(own, own=SUPPLEMENT_MARKDOWN),
                    anchor_ids(sub.staged.document),
                ),
            ),
        )
    bibliography: dict[str, object] = {
        "status": "UNVERIFIED",
        "problems": [],
        "citation": None,
    }
    entry = bibtex_entry(identity)
    if entry is not None:
        problems = validate_bibtex(entry, identity)
        _write(build.root / "citation.bib", entry)
        bibliography = {
            "status": "ASSERTED"
            if identity.status == "ASSERTED"
            else "VALIDATED"
            if not problems
            else "VALIDATED_WITH_WARNINGS",
            "problems": list(problems),
            "citation": "citation.bib",
        }
    _write_reports(build, supplements, links)
    fingerprint = mapping(staged.source_record["fingerprint"])
    metadata_document = _metadata_document(
        document, identity, fingerprint, bibliography
    )
    metadata_document["arxiv"] = build.arxiv
    metadata_document["document_version"] = build.version.version
    metadata_document["document_version_evidence"] = build.version.to_dict()
    metadata_document["related"] = [relation.to_dict() for relation in build.relations]
    _write(build.root / "metadata.json", dump(metadata_document))
    _write(
        build.root / "extraction.json",
        dump(_extraction_document(build, source_path, version)),
    )
    _write(
        build.root / "paper.md",
        render_markdown(
            document,
            _front_matter(build, source_path, run_id, version),
            _paper_assets(build, links),
        ),
    )
    manifest, files = _manifest(build.root, name, generation)
    _write(build.root / "manifest.json", dump(manifest))
    return files


def _taken(library: Path) -> dict[str, set[str]]:
    """Map existing paper directory names to the source digests they hold.

    Parameters
    ----------
    library : Path
        Library root.

    Returns
    -------
    dict of str to set of str
        Paper names, without shard directories, from the catalog and from
        directories on disk; a directory without a catalog row maps to an
        empty set.
    """
    taken: dict[str, set[str]] = {}
    for row in read_catalog(library):
        digests = {string(item) for item in items(row.get("source_sha256", []))}
        name = PurePosixPath(string(row["directory"])).name
        taken.setdefault(name, set()).update(digests)
    # Names are unique across shards, so a paper can move between layouts.
    for path in paper_directories(library):
        taken.setdefault(path.name, set())
    return taken


def resolve_and_write_identity(
    staging: Path, lookup: Lookup, search: Search | None = None
) -> Identity:
    """Resolve bibliographic identity for a staged extraction and persist it.

    Parameters
    ----------
    staging : Path
        Extraction staging directory with ``document.json`` and ``source.json``.
    lookup : Callable
        Registry lookup, usually :func:`paperextract.registry.default_lookup`.
    search : Callable or None
        Bibliographic search for papers without an accepted DOI, usually
        :func:`paperextract.registry.default_search`.

    Returns
    -------
    Identity
        The identity written to ``identity.json`` in the staging directory.
        The source's original file name is a weak DOI candidate and search
        hint; a DOI the user asserted (the ``doi`` assertion) is tried first,
        and a BibTeX entry the user asserted (``bibtex``) replaces the
        registry for a work that has none.
    """
    document = Document.from_json((staging / DOCUMENT_FILENAME).read_text())
    source_record = mapping(json.loads((staging / SOURCE_RECORD_FILENAME).read_text()))
    name = PurePosixPath(string(source_record["original_name"])).name
    hints = read_hints(staging)
    if "bibtex" in hints:
        identity = identity_from_bibtex(hints["bibtex"], document)
    else:
        identity = resolve_identity(
            document,
            mapping(source_record["fingerprint"]),
            lookup,
            search,
            file_names=(name,) if name else (),
            asserted_doi=hints.get("doi"),
        )
    (staging / IDENTITY_FILENAME).write_text(identity.to_json())
    return identity


def _journal_directory(library: Path) -> Path:
    """Return the directory of pending replacement journals.

    Parameters
    ----------
    library : Path
        Library root.

    Returns
    -------
    Path
        ``.paperextract/journal``.
    """
    return library / INTERNAL_DIRECTORY / "journal"


def recover_library(library: Path) -> list[str]:
    """Finish or roll back replacements interrupted before completion.

    Parameters
    ----------
    library : Path
        Library root.

    Returns
    -------
    list of str
        One description per recovered replacement; the catalog is rebuilt
        when any was found.

    Notes
    -----
    A replacement retires the old directory, then renames the new build into
    place. If the old directory was already retired, the build is moved into
    place; if it was not, the build is discarded and the old paper stays.
    """
    directory = _journal_directory(library)
    if not directory.is_dir():
        return []
    recovered: list[str] = []
    organize = directory / ORGANIZE_JOURNAL
    if organize.is_file():
        _finish_organize(library, organize)
        recovered.append("completed an interrupted organize")
    for path in sorted(directory.glob("*.json")):
        journal = mapping(json.loads(path.read_text()))
        old = library / string(journal["old"])
        new = library / string(journal["new"])
        build = library / string(journal["build"])
        retired = library / string(journal["retired"])
        if (
            build.exists()
            and not new.exists()
            and retired.exists()
            and not old.exists()
        ):
            build.rename(new)
            recovered.append(f"completed replacement of {old.name} by {new.name}")
        elif build.exists():
            shutil.rmtree(build)
            recovered.append(f"rolled back replacement of {old.name}")
        else:
            recovered.append(f"closed finished replacement of {old.name}")
        path.unlink()
    if recovered:
        rebuild_catalog(library, ensure_library(library).get("library_id"))
    return recovered


ORGANIZE_JOURNAL = "organize.json"


@dataclass(frozen=True)
class Move:
    """Move one paper directory to another place in the library.

    Attributes
    ----------
    old : str
        Current relative path.
    new : str
        Relative path under the new layout.
    """

    old: str
    new: str


def plan_organize(library: Path, layout: str) -> list[Move]:
    """List the moves that put every paper where a layout wants it.

    Parameters
    ----------
    library : Path
        Library root.
    layout : str
        Target layout, one of :data:`paperextract.catalog.LAYOUTS`.

    Returns
    -------
    list of Move
        Moves for the papers not already in place, in catalog order; the
        shard comes from each paper's validated year or first author.

    Raises
    ------
    ValueError
        The layout is unknown.
    """
    moves: list[Move] = []
    for row in rebuild_catalog(library, ensure_library(library).get("library_id")):
        old = string(row["directory"])
        shard = shard_for(
            layout,
            validated=row.get("bibliographic_status")
            in ("VALIDATED", "VALIDATED_WITH_WARNINGS", "ASSERTED"),
            year=row.get("year"),
            family=row.get("first_author_family"),
        )
        name = PurePosixPath(old).name
        new = f"{shard}/{name}" if shard else name
        if new != old:
            moves.append(Move(old, new))
    return moves


def organize_library(library: Path, layout: str) -> list[Move]:
    """Move every paper to its place under a layout and record the layout.

    Parameters
    ----------
    library : Path
        Library root.
    layout : str
        Target layout.

    Returns
    -------
    list of Move
        Moves performed.

    Raises
    ------
    ValueError
        The layout is unknown.
    PublicationConflictError
        A target directory already exists.

    Notes
    -----
    The moves are written to a journal first and each is a rename within the
    library, so an interruption is completed by :func:`recover_library`.
    Paper directories hold only relative references, so moving them changes
    nothing inside them; the catalog and derived files are rebuilt.
    """
    recover_library(library)
    moves = plan_organize(library, layout)
    for move in moves:
        if (library / move.new).exists():
            raise PublicationConflictError(f"{library / move.new} already exists")
    journal = _journal_directory(library) / ORGANIZE_JOURNAL
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(
        dump(
            {
                "schema": "paperextract.organize",
                "schema_version": 1,
                "layout": layout,
                "moves": [{"old": m.old, "new": m.new} for m in moves],
            }
        )
    )
    _finish_organize(library, journal)
    return moves


def _finish_organize(library: Path, journal: Path) -> None:
    """Perform the remaining moves of an organize journal and close it.

    Parameters
    ----------
    library : Path
        Library root.
    journal : Path
        Journal written by :func:`organize_library`.
    """
    data = mapping(json.loads(journal.read_text()))
    for item in items(data["moves"]):
        move = mapping(item)
        old, new = library / string(move["old"]), library / string(move["new"])
        if old.exists() and not new.exists():
            new.parent.mkdir(parents=True, exist_ok=True)
            old.rename(new)
            _remove_empty_shard(library, string(move["old"]))
    corpus_path = library / CORPUS_FILENAME
    corpus = mapping(json.loads(corpus_path.read_text()))
    corpus["layout"] = string(data["layout"])
    corpus_path.write_text(dump(corpus))
    rebuild_catalog(library, corpus.get("library_id"))
    journal.unlink()


def _replace(
    library: Path, old: str, root: Path, target: Path, generation: str
) -> None:
    """Swap a new build in for a published directory under a journal.

    Parameters
    ----------
    library : Path
        Library root.
    old : str
        Relative path of the directory being replaced.
    root : Path
        Completed build.
    target : Path
        Final directory of the new build.
    generation : str
        Publication generation, which names the retired copy.
    """
    retired = (
        library
        / INTERNAL_DIRECTORY
        / "replaced"
        / f"{PurePosixPath(old).name}.{generation}"
    )
    retired.parent.mkdir(parents=True, exist_ok=True)
    journal = _journal_directory(library) / f"{generation}.json"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(
        dump(
            {
                "schema": "paperextract.replacement",
                "schema_version": 1,
                "old": old,
                "new": target.relative_to(library).as_posix(),
                "build": root.relative_to(library).as_posix(),
                "retired": retired.relative_to(library).as_posix(),
            }
        )
    )
    (library / old).rename(retired)
    root.rename(target)
    journal.unlink()


def publish(
    staging: Path, library: Path, *, dpi: int = 300, replace: str | None = None
) -> PublishedPaper:
    """Turn one extraction staging directory into a published paper directory.

    Parameters
    ----------
    staging : Path
        Directory written by :func:`paperextract.pipeline.extract_pdf` for a
        completed extraction, optionally holding ``identity.json`` from
        :func:`resolve_and_write_identity`.
    library : Path
        Library root; created together with ``corpus.json`` when missing.
    dpi : int
        Resolution for complete-figure context crops.
    replace : str or None
        Name of a published directory that this publication replaces; the new
        directory may take a different name when the identity changed.

    Returns
    -------
    PublishedPaper
        Final directory, name, generation and recorded files.

    Raises
    ------
    PublicationConflictError
        The library already holds this source under the chosen name.
    FileNotFoundError
        The directory to replace does not exist.
    ValueError
        The staging artifacts are inconsistent or the extraction failed.

    Notes
    -----
    The directory is built under ``.paperextract/staging`` in the library and
    renamed into place only after ``manifest.json`` is written, so a reader
    never sees a half-written paper. A failure removes the partial build and
    leaves the extraction staging directory untouched. A replacement retires
    the previous directory to ``.paperextract/replaced/`` under a journal that
    :func:`recover_library` completes or rolls back after an interruption.
    The catalog row is derived from the published files afterwards. A
    validated identity names the directory ``Family_Year_FirstWords`` and adds
    ``citation.bib``; otherwise the name is ``Unverified_`` plus the source
    digest and identity fields stay null.
    """
    staged = _read_staging(staging.resolve())
    corpus = ensure_library(library)
    recover_library(library)
    taken = _taken(library)
    if replace is not None:
        if not (library / replace / "manifest.json").is_file():
            raise FileNotFoundError(f"No published paper {replace} in {library}")
        taken.pop(PurePosixPath(replace).name, None)
    name = paper_directory_name(staged.identity, staged.document.source_sha256, taken)
    shard = shard_for(
        str(corpus.get("layout", "flat")),
        validated=staged.identity.named(),
        year=staged.identity.field("year"),
        family=first_author_family(staged.identity),
    )
    relative = f"{shard}/{name}" if shard else name
    target = library / relative
    if target.exists() and relative != replace:
        raise PublicationConflictError(f"{target} already exists")
    generation = f"{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}"
    root = library / INTERNAL_DIRECTORY / "staging" / f"{name}.{generation}"
    root.mkdir(parents=True)
    hints = read_hints(staged.staging)
    arxiv = hints.get("arxiv") or arxiv_identifier(
        staged.identity, mapping(staged.source_record["fingerprint"])
    )
    build = _Build(
        root,
        staged,
        dpi,
        {},
        {},
        {},
        [],
        version=decide_version(
            staged.document,
            staged.identity,
            hint=hints.get("version"),
            arxiv=arxiv,
            file_name=string(staged.source_record["original_name"]),
        ),
        arxiv=arxiv,
        relations=library_relations(
            staged.identity, read_catalog(library), exclude=replace
        ),
    )
    try:
        files = _assemble(build, name, generation)
        target.parent.mkdir(parents=True, exist_ok=True)
        if replace is not None:
            _replace(library, replace, root, target, generation)
        else:
            try:
                root.rename(target)
            except OSError as exc:
                raise PublicationConflictError(
                    f"{target} appeared during publication"
                ) from exc
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise
    if replace is not None:
        _remove_empty_shard(library, replace)
    row = catalog_row(target, corpus.get("library_id"), library)
    kept = [
        r
        for r in read_catalog(library)
        if r.get("directory") not in {relative, replace}
    ]
    write_catalog(library, [*kept, row])
    return PublishedPaper(
        directory=target, name=relative, generation=generation, files=files
    )


def _remove_empty_shard(library: Path, relative: str) -> None:
    """Remove the shard directory a paper left, when it is now empty.

    Parameters
    ----------
    library : Path
        Library root.
    relative : str
        Former relative path of the paper.
    """
    parent = (library / relative).parent
    if parent != library and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()


def extract_and_publish(
    sources: PaperSources,
    staging: Path,
    settings: ExtractionSettings,
    library: Path,
    *,
    request_id: str,
    pages: tuple[int, ...] | None = None,
) -> tuple[BundleOutcome, PublishedPaper | None]:
    """Run the pipeline for a paper and its supplements, then publish it.

    Parameters
    ----------
    sources : PaperSources
        Paper PDF and supplement PDFs, published in one paper directory.
    staging : Path
        Existing, empty extraction staging directory.
    settings : ExtractionSettings
        Worker environment, model directory, profile, timeout and the registry
        lookup for the identity stage; without a lookup the paper is published
        as unverified.
    library : Path
        Library root.
    request_id : str
        Identifier for the worker request and the diagnostics run directory.
    pages : tuple of int or None
        Page selection for the paper, or None for every page.

    Returns
    -------
    tuple
        The bundle outcome and the published paper, or None when a worker
        failed and nothing was published; an incomplete bundle is never
        published.
    """
    bundle = extract_bundle(
        sources, staging, settings, request_id=request_id, pages=pages
    )
    if bundle.failure is not None:
        return bundle, None
    if settings.lookup is not None:
        resolve_and_write_identity(
            bundle.main.staging, settings.lookup, settings.search
        )
    return bundle, publish(bundle.main.staging, library)
