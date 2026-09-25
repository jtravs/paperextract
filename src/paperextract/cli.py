"""Command-line interface: extract, batch, dedup and publish.

Machine output goes to standard output, as a short table or, with ``--json``, a
versioned JSON document; progress goes to standard error. Exit codes follow
Plan §17 and are listed in :data:`EXIT_CODES`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import logging
import os
import re
import secrets
import shutil
import signal
import sys
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from types import FrameType
from typing import cast

from paperextract.acquire import (
    Acquisition,
    AcquisitionError,
    acquire_arxiv,
    arxiv_request,
    urllib_fetch,
)
from paperextract.bibread import read_entries
from paperextract.capture import capture_kind, read_page_html
from paperextract.catalog import (
    CATALOG_FILENAME,
    CATALOG_ROW_SCHEMA,
    LAYOUTS,
    paper_directories,
    rebuild_catalog,
)
from paperextract.compare import compare_documents
from paperextract.config import (
    DESCRIBE_BACKENDS,
    EXTRACTION_BACKENDS,
    Configuration,
    ConfigurationError,
    resolve_configuration,
)
from paperextract.describe_stage import describe_staging, pending_figures
from paperextract.describers import Describer, DescriberError
from paperextract.document import Document
from paperextract.export import processing_status
from paperextract.fields import dump, mapping, string
from paperextract.fields import items as json_items
from paperextract.formats import (
    READABLE_VERSIONS,
    PaperCheck,
    UnsupportedFormatError,
    check_paper,
    record_state,
    require_readable,
)
from paperextract.html_article import page_doi
from paperextract.identity import Identity
from paperextract.index import (
    Query,
    lookup,
    queries_from_bibtex,
    query_from_file,
    refresh_index,
    search,
)
from paperextract.ingest import (
    CapturePairing,
    Equivalence,
    IntakePlan,
    SourceChangedError,
    SourceGroup,
    SupplementPairing,
    content_equivalents,
    dedup_report,
    discover_pdfs,
    pair_captures,
    pair_supplements,
    plan_intake,
    shared_doi_candidates,
)
from paperextract.manifest import (
    ManifestError,
    manifest_text,
    parse_shard,
    read_manifest,
    shard_entries,
)
from paperextract.models import (
    ModelManifest,
    ModelSnapshot,
    binary_installed,
    current_platform,
    fetch_binary,
    fetch_snapshot,
    load_manifest,
    snapshot_status,
    urllib_download,
)
from paperextract.pipeline import (
    DOCUMENT_FILENAME,
    SOURCE_RECORD_FILENAME,
    SUPPLEMENTS_DIRECTORY,
    ExtractionSettings,
    PaperSources,
    attach_capture,
    attach_equivalent,
    extract_bundle,
    extract_pdf,
    read_hints,
    rebuild_document,
    supplement_directory,
    write_hints,
)
from paperextract.protocol import Backend, MineruProfile
from paperextract.registry import (
    Lookup,
    Search,
    default_lookup,
    default_search,
    normalize_doi,
)
from paperextract.reprocess import stage_from_paper
from paperextract.storage import (
    CORPUS_FILENAME,
    IDENTITY_FILENAME,
    INTERNAL_DIRECTORY,
    PublicationConflictError,
    ensure_library,
    organize_library,
    plan_organize,
    publish,
    read_catalog,
    recover_library,
    resolve_and_write_identity,
)
from paperextract.versions import VERSIONS, intake_title_matches
from paperextract.worker import WorkerError, WorkerSessions

__all__ = [
    "BATCHES_DIRECTORY",
    "COMMANDS",
    "EXIT_CANCELLED",
    "EXIT_CODES",
    "EXIT_CONFLICT",
    "EXIT_FAILURE",
    "EXIT_OK",
    "EXIT_PARTIAL",
    "EXIT_USAGE",
    "RESULT_SCHEMA",
    "RESULT_VERSION",
    "RUNS_DIRECTORY",
    "SNAPSHOT_DIRECTORY",
    "ItemResult",
    "build_parser",
    "exit_code",
    "main",
    "main_entry",
    "parse_pages",
]

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_CONFLICT = 3
EXIT_PARTIAL = 4
EXIT_FAILURE = 5
EXIT_CANCELLED = 130
EXIT_CODES: Mapping[int, str] = {
    EXIT_OK: "completed; warnings are summarized",
    EXIT_USAGE: "usage or configuration error",
    EXIT_CONFLICT: "publication conflict with an existing paper directory",
    EXIT_PARTIAL: "mixed batch result, or a warning under --strict",
    EXIT_FAILURE: "execution failure",
    EXIT_CANCELLED: "cancelled",
}
COMMANDS = (
    "extract",
    "batch",
    "dedup",
    "publish",
    "organize",
    "migrate",
    "models",
    "compare",
    "reprocess",
    "describe",
    "index",
    "lookup",
    "search",
)
RESULT_SCHEMA = "paperextract.cli-result"
# Version 2 adds the captures of each item.
RESULT_VERSION = 2
RUNS_DIRECTORY = "runs"
BATCHES_DIRECTORY = "batches"
SESSIONS_DIRECTORY = "sessions"
MODEL_MANIFEST = Path("workers") / "models.json"
# The download function, replaced in tests.
_DOWNLOAD = urllib_download
SNAPSHOT_DIRECTORY = "registry-snapshots"
_SUCCESS = frozenset(
    {"published", "republished", "skipped", "held", "attached", "staged"}
)


@dataclass(frozen=True)
class ItemResult:
    """Report what happened to one supplied source.

    Attributes
    ----------
    path : str
        Source path as supplied, or the run directory for ``publish``.
    status : str
        ``published``, ``skipped`` (bytes already in the library),
        ``attached`` (text identical to a paper, preserved in it as an
        equivalent copy), ``held`` (such a copy whose paper failed, a
        supplement without its paper, or a page without a match),
        ``rejected``, ``failed`` or ``conflict``.
    sha256 : str or None
        Source digest, when the file could be read.
    directory : str or None
        Paper directory name in the library, or the equivalent source for a
        held item.
    identity : str or None
        Bibliographic status of a published paper.
    processing : str or None
        ``COMPLETE`` or ``PARTIAL`` page coverage of a published paper.
    findings : int or None
        Number of structured findings in the published document.
    aliases : tuple of str
        Further paths with identical bytes, recorded but not extracted.
    supplements : tuple of str
        Supplement paths extracted and published with the paper.
    captures : tuple of str
        Saved web pages preserved with the paper and compared with it.
    stage : str or None
        Stage that failed: ``intake``, ``extraction`` or ``publication``,
        which includes identity resolution.
    message : str or None
        Reason for a skip, hold, rejection or failure.
    run : str or None
        Run directory kept for diagnosis after a failure.
    """

    path: str
    status: str
    sha256: str | None = None
    directory: str | None = None
    identity: str | None = None
    processing: str | None = None
    findings: int | None = None
    aliases: tuple[str, ...] = ()
    supplements: tuple[str, ...] = ()
    captures: tuple[str, ...] = ()
    stage: str | None = None
    message: str | None = None
    run: str | None = None

    def warnings(self) -> tuple[str, ...]:
        """List the conditions that ``--strict`` treats as a partial result.

        Returns
        -------
        tuple of str
            Unvalidated identity, incomplete page coverage or a held source.
        """
        found: list[str] = []
        if self.status == "held":
            found.append("held for review")
        if self.identity is not None and self.identity != "VALIDATED":
            found.append(f"identity {self.identity}")
        if self.processing == "PARTIAL":
            found.append("partial page coverage")
        return tuple(found)

    def to_dict(self) -> dict[str, object]:
        """Serialize the item.

        Returns
        -------
        dict of str to object
            JSON-compatible fields including the warnings.
        """
        return {
            "path": self.path,
            "status": self.status,
            "sha256": self.sha256,
            "directory": self.directory,
            "identity": self.identity,
            "processing": self.processing,
            "findings": self.findings,
            "aliases": list(self.aliases),
            "supplements": list(self.supplements),
            "captures": list(self.captures),
            "stage": self.stage,
            "message": self.message,
            "run": self.run,
            "warnings": list(self.warnings()),
        }


@dataclass(frozen=True)
class _Context:
    """Carry the settings shared by every item of one invocation.

    Attributes
    ----------
    config : Configuration
        Resolved configuration.
    settings : ExtractionSettings
        Checked pipeline settings.
    pages : tuple of int or None
        Page selection.
    keep_runs : bool
        Keep run directories after successful publication.
    captures : Mapping of str to tuple of Path
        Saved web pages to preserve and compare, by paper group digest.
    hints : Mapping of str to Mapping of str to str
        User assertions and acquisition records, by paper group digest.
    runs_to : Path or None
        Directory that keeps finished runs unpublished, for publication
        later in one step; None publishes each paper.
    staged : Mapping of str to Path
        Completed runs already in ``runs_to``, by source digest, so a rerun
        resumes instead of extracting again.
    """

    config: Configuration
    settings: ExtractionSettings
    pages: tuple[int, ...] | None
    keep_runs: bool
    captures: Mapping[str, tuple[Path, ...]] = field(
        default_factory=dict[str, tuple[Path, ...]]
    )
    runs_to: Path | None = None
    staged: Mapping[str, Path] = field(default_factory=dict[str, Path])
    hints: Mapping[str, Mapping[str, str]] = field(
        default_factory=dict[str, Mapping[str, str]]
    )


def parse_pages(text: str) -> tuple[int, ...]:
    """Parse a one-based page selection such as ``1-3,15``.

    Parameters
    ----------
    text : str
        Comma-separated pages and inclusive ranges.

    Returns
    -------
    tuple of int
        Strictly increasing page numbers.

    Raises
    ------
    argparse.ArgumentTypeError
        The selection is malformed, empty, not positive or not increasing.

    Examples
    --------
    >>> parse_pages("1-3,15")
    (1, 2, 3, 15)
    """
    pages: list[int] = []
    try:
        for part in text.split(","):
            first, dash, last = part.strip().partition("-")
            start = int(first)
            stop = int(last) if dash else start
            if start < 1 or stop < start:
                raise ValueError(part)
            pages.extend(range(start, stop + 1))
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid page selection: {text!r}") from None
    if any(later <= earlier for earlier, later in itertools.pairwise(pages)):
        raise argparse.ArgumentTypeError(f"pages must increase: {text!r}")
    return tuple(pages)


def _common(parser: argparse.ArgumentParser) -> None:
    """Add the options every command accepts.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        Subcommand parser.
    """
    parser.add_argument("--config", type=Path, help="explicit TOML configuration")
    parser.add_argument("--library", type=Path, help="library root")
    parser.add_argument("--json", action="store_true", help="print JSON on stdout")
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="only warnings on stderr"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="debug messages on stderr"
    )


def _registry_options(parser: argparse.ArgumentParser) -> None:
    """Add the registry and strictness options of publishing commands.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        Subcommand parser.
    """
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--offline",
        action="store_const",
        const=True,
        help="use registry snapshots only",
    )
    group.add_argument(
        "--no-registry",
        dest="registry",
        action="store_const",
        const=False,
        help="skip registry lookups; papers are published unverified",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 4 when any paper is unverified, partial or held",
    )


def _extract_options(parser: argparse.ArgumentParser) -> None:
    """Add the worker options of extracting commands.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        Subcommand parser.
    """
    parser.add_argument("--models", type=Path, help="MinerU model directory")
    parser.add_argument(
        "--timeout", type=float, help="worker time limit per paper in seconds"
    )
    parser.add_argument(
        "--keep-run",
        action="store_true",
        help="keep run directories after successful publication",
    )
    parser.add_argument(
        "--no-table-ocr",
        dest="table_ocr",
        action="store_const",
        const=False,
        help="do not re-extract tables whose text layer lost glyphs",
    )
    parser.add_argument(
        "--backend",
        dest="extract_backend",
        choices=EXTRACTION_BACKENDS,
        help="extraction backend (default mineru)",
    )
    parser.add_argument(
        "--table-check",
        dest="table_check",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="cross-check tables with an independent Docling reading",
    )


def _extract_parser(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],  # pyright: ignore[reportPrivateUsage] - argparse exposes no public name for it
) -> argparse.ArgumentParser:
    """Add the ``extract`` command.

    Parameters
    ----------
    commands : argparse._SubParsersAction
        Subcommand registry.

    Returns
    -------
    argparse.ArgumentParser
        The command's parser.
    """
    extract = commands.add_parser(
        "extract", help="extract one PDF, or an arXiv:ID, into the library"
    )
    extract.add_argument("sources", nargs="+", type=Path, metavar="PDF")
    extract.add_argument(
        "--pages", type=parse_pages, help="one-based pages, such as 1-3,15"
    )
    extract.add_argument(
        "--supplement",
        type=Path,
        action="append",
        default=[],
        metavar="PDF",
        help="supplementary PDF published with the paper; repeatable",
    )
    extract.add_argument(
        "--html",
        type=Path,
        action="append",
        default=[],
        metavar="CAPTURE",
        help="saved web page of the article, preserved and compared; repeatable",
    )
    extract.add_argument(
        "--document-version",
        choices=VERSIONS,
        help="assert which version of the work the PDF is",
    )
    return extract


def _query_parsers(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],  # pyright: ignore[reportPrivateUsage] - argparse exposes no public name for it
) -> tuple[argparse.ArgumentParser, argparse.ArgumentParser]:
    """Add the ``lookup`` and ``search`` commands.

    Parameters
    ----------
    commands : argparse._SubParsersAction
        Subcommand registry.

    Returns
    -------
    tuple of argparse.ArgumentParser
        The two parsers.
    """
    lookup = commands.add_parser(
        "lookup", help="report whether papers are already in one or more libraries"
    )
    lookup.add_argument("libraries", nargs="*", type=Path, metavar="LIBRARY")
    lookup.add_argument("--doi")
    lookup.add_argument("--arxiv", help="arXiv identifier, such as 2206.01062")
    lookup.add_argument("--file", type=Path, action="append", default=[])
    lookup.add_argument("--bibtex", type=Path)
    lookup.add_argument("--title")
    lookup.add_argument("--author", help="first author's family name")
    lookup.add_argument("--year", type=int)
    search_parser = commands.add_parser(
        "search", help="full-text search over titles, authors, abstracts and text"
    )
    search_parser.add_argument("query")
    search_parser.add_argument("libraries", nargs="*", type=Path, metavar="LIBRARY")
    search_parser.add_argument("--limit", type=int, default=10)
    return lookup, search_parser


def _compare_parser(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],  # pyright: ignore[reportPrivateUsage] - argparse exposes no public name for it
) -> argparse.ArgumentParser:
    """Add the ``compare`` command.

    Parameters
    ----------
    commands : argparse._SubParsersAction
        Subcommand registry.

    Returns
    -------
    argparse.ArgumentParser
        The command's parser.
    """
    compare = commands.add_parser(
        "compare", help="extract one PDF with several backends and compare them"
    )
    compare.add_argument("source", type=Path, metavar="PDF")
    compare.add_argument(
        "--backends",
        default="mineru,docling",
        help="comma-separated backends, the first is the reference "
        "(default mineru,docling)",
    )
    compare.add_argument(
        "--pages", type=parse_pages, help="one-based pages, such as 1-3,15"
    )
    compare.add_argument(
        "--out", type=Path, help="directory for the runs and comparison.json"
    )
    return compare


def _organize_parser(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],  # pyright: ignore[reportPrivateUsage] - argparse exposes no public name for it
) -> argparse.ArgumentParser:
    """Add the ``organize`` command.

    Parameters
    ----------
    commands : argparse._SubParsersAction
        Subcommand registry.

    Returns
    -------
    argparse.ArgumentParser
        The command's parser.
    """
    organize = commands.add_parser(
        "organize", help="move papers into a library layout and record it"
    )
    organize.add_argument("--layout", choices=LAYOUTS, required=True)
    organize.add_argument(
        "--dry-run", action="store_true", help="list the moves without moving"
    )
    return organize


def _migrate_parser(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],  # pyright: ignore[reportPrivateUsage] - argparse exposes no public name for it
) -> argparse.ArgumentParser:
    """Add the ``migrate`` command.

    Parameters
    ----------
    commands : argparse._SubParsersAction
        Subcommand registry.

    Returns
    -------
    argparse.ArgumentParser
        The command's parser.
    """
    migrate = commands.add_parser(
        "migrate",
        help="check a library's record versions and integrity, and rebuild "
        "outdated papers in the current formats",
    )
    migrate.add_argument(
        "--dry-run", action="store_true", help="report without changing anything"
    )
    return migrate


def _models_parser(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],  # pyright: ignore[reportPrivateUsage] - argparse exposes no public name for it
) -> argparse.ArgumentParser:
    """Add the ``models`` command.

    Parameters
    ----------
    commands : argparse._SubParsersAction
        Subcommand registry.

    Returns
    -------
    argparse.ArgumentParser
        The command's parser.
    """
    models = commands.add_parser(
        "models", help="check or fetch the pinned model snapshots the workers use"
    )
    models.add_argument("action", choices=["status", "fetch"])
    models.add_argument(
        "sets", nargs="*", metavar="SET", help="such as mineru, mineru-cuda, docling"
    )
    models.add_argument(
        "--verify", action="store_true", help="hash every file instead of sizes only"
    )
    models.add_argument(
        "--offline", action="store_true", default=None, help="refuse to download"
    )
    models.add_argument("--models", type=Path, help="MinerU model directory")
    return models


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Returns
    -------
    argparse.ArgumentParser
        Parser with the ``extract``, ``batch``, ``dedup`` and ``publish``
        commands.
    """
    parser = argparse.ArgumentParser(
        prog="paperextract",
        description="Extract scientific papers into an auditable library.",
        epilog="A first argument that is not a command means `extract`. Exit "
        "codes: "
        + "; ".join(f"{code} {meaning}" for code, meaning in EXIT_CODES.items()),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    extract = _extract_parser(commands)
    batch = commands.add_parser(
        "batch", help="extract every PDF of directories or file lists"
    )
    batch.add_argument("sources", nargs="*", type=Path, metavar="PATH")
    batch.add_argument(
        "--manifest", type=Path, help="JSON Lines manifest with one paper per line"
    )
    batch.add_argument(
        "--runs-to",
        type=Path,
        metavar="DIR",
        help="keep finished runs in DIR unpublished, for `publish` later",
    )
    batch.add_argument(
        "--shard",
        type=parse_shard,
        help="process only shard K of N manifest entries, such as 1/2",
    )
    dedup = commands.add_parser(
        "dedup", help="report duplicates before extraction; changes nothing"
    )
    dedup.add_argument("sources", nargs="+", type=Path, metavar="PATH")
    dedup.add_argument("--report", type=Path, help="also write the JSON report here")
    dedup.add_argument(
        "--manifest-out", type=Path, help="write a proposed batch manifest here"
    )
    publish_parser = commands.add_parser(
        "publish", help="publish kept or staged run directories, one after another"
    )
    publish_parser.add_argument("runs", nargs="+", type=Path, metavar="RUN")
    publish_parser.add_argument(
        "--refresh-identity",
        action="store_true",
        help="resolve identity again even when the run already has one",
    )
    organize = _organize_parser(commands)
    migrate = _migrate_parser(commands)
    models = _models_parser(commands)
    compare = _compare_parser(commands)
    reprocess = commands.add_parser(
        "reprocess",
        help="rebuild published papers from their kept output, without the backend",
    )
    reprocess.add_argument("papers", nargs="*", type=Path, metavar="PAPER")
    reprocess.add_argument(
        "--all", action="store_true", help="rebuild every paper of the library"
    )
    reprocess.add_argument(
        "--refresh-identity",
        action="store_true",
        help="resolve bibliographic identity again, which may rename the paper",
    )
    reprocess.add_argument(
        "--html",
        action="append",
        default=[],
        type=Path,
        metavar="CAPTURE",
        help="add a saved web page of the article (one named paper only)",
    )
    reprocess.add_argument(
        "--document-version",
        choices=VERSIONS,
        help="assert the paper's version (one named paper only)",
    )
    reprocess.add_argument(
        "--doi",
        help="assert the paper's DOI and resolve identity with it (one named "
        "paper only)",
    )
    reprocess.add_argument(
        "--bibtex",
        type=Path,
        metavar="FILE",
        help="assert the identity of a work no registry holds, such as a report "
        "or thesis, with one BibTeX entry (one named paper only)",
    )
    describe = commands.add_parser(
        "describe",
        help="add machine-generated figure descriptions to published papers",
    )
    describe.add_argument("papers", nargs="*", type=Path, metavar="PAPER")
    describe.add_argument(
        "--all", action="store_true", help="describe every paper of the library"
    )
    describe.add_argument("--backend", choices=DESCRIBE_BACKENDS)
    describe.add_argument(
        "--endpoint", help="OpenAI-compatible server, such as http://127.0.0.1:8000/v1"
    )
    describe.add_argument(
        "--model",
        dest="describe_model",
        help="served model name or Anthropic model identifier",
    )
    describe.add_argument(
        "--max-usd", type=float, help="spend cap for a paid run, in US dollars"
    )
    describe.add_argument(
        "--force",
        action="store_true",
        help="describe again figures that already have this model's description",
    )
    describe.add_argument(
        "--dry-run",
        action="store_true",
        help="count the figures to describe without contacting the model",
    )
    describe.add_argument(
        "--strict",
        action="store_true",
        help="exit 4 when any paper is unverified or partial",
    )
    index = commands.add_parser(
        "index", help="rebuild the catalog and search index from the paper directories"
    )
    index.add_argument("action", choices=["rebuild"])
    lookup, search_parser = _query_parsers(commands)
    for sub in (
        extract,
        batch,
        dedup,
        publish_parser,
        organize,
        migrate,
        models,
        compare,
        reprocess,
        describe,
        index,
        lookup,
        search_parser,
    ):
        _common(sub)
    for sub in (extract, batch):
        _extract_options(sub)
    for sub in (extract, batch, publish_parser, reprocess):
        _registry_options(sub)
    return parser


def _normalize_argv(argv: Sequence[str]) -> list[str]:
    """Treat a leading non-command argument as the ``extract`` shorthand.

    Parameters
    ----------
    argv : Sequence of str
        Arguments without the program name.

    Returns
    -------
    list of str
        Arguments with ``extract`` inserted when needed.
    """
    if argv and argv[0] not in COMMANDS and not argv[0].startswith("-"):
        return ["extract", *argv]
    return list(argv)


def _configure(args: argparse.Namespace, environ: Mapping[str, str]) -> Configuration:
    """Resolve the configuration for parsed arguments.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    environ : Mapping of str to str
        Process environment.

    Returns
    -------
    Configuration
        Resolved configuration.
    """
    options: dict[str, object] = {
        "library": args.library,
        "worker.models": getattr(args, "models", None),
        "worker.timeout_seconds": getattr(args, "timeout", None),
        "worker.table_ocr": getattr(args, "table_ocr", None),
        "worker.backend": getattr(args, "extract_backend", None),
        "worker.table_check": getattr(args, "table_check", None),
        "registry.enabled": getattr(args, "registry", None),
        "registry.offline": getattr(args, "offline", None),
        "describe.backend": getattr(args, "backend", None),
        "describe.endpoint": getattr(args, "endpoint", None),
        "describe.model": getattr(args, "describe_model", None),
        "describe.max_usd": getattr(args, "max_usd", None),
    }
    timeout = options["worker.timeout_seconds"]
    if isinstance(timeout, float) and timeout <= 0:
        raise ConfigurationError("--timeout must be positive")
    cap = options["describe.max_usd"]
    if isinstance(cap, float) and cap <= 0:
        raise ConfigurationError("--max-usd must be positive")
    return resolve_configuration(
        options,
        explicit=args.config,
        environ=environ,
        cwd=Path.cwd(),
        package_file=Path(__file__),
    )


def _search(config: Configuration) -> Search | None:
    """Build the bibliographic search the configuration allows.

    Parameters
    ----------
    config : Configuration
        Resolved configuration.

    Returns
    -------
    Callable or None
        Snapshot-backed Crossref search, or None when registries are disabled.
    """
    if not config.registry:
        return None
    return default_search(
        config.library / INTERNAL_DIRECTORY / SNAPSHOT_DIRECTORY,
        contact=config.contact,
        offline=config.offline,
    )


def _lookup(config: Configuration) -> Lookup | None:
    """Build the registry lookup the configuration allows.

    Parameters
    ----------
    config : Configuration
        Resolved configuration.

    Returns
    -------
    Callable or None
        Snapshot-backed lookup, or None when registries are disabled.
    """
    if not config.registry:
        return None
    return default_lookup(
        config.library / INTERNAL_DIRECTORY / SNAPSHOT_DIRECTORY,
        contact=config.contact,
        offline=config.offline,
    )


def _library_digests(library: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Map source and text digests to the library directories that hold them.

    Parameters
    ----------
    library : Path
        Library root, which may not exist yet.

    Returns
    -------
    tuple of dict
        Source digest to directory, and whole-text digest to directory, for
        catalog rows whose directory still exists.
    """
    sources: dict[str, str] = {}
    texts: dict[str, str] = {}
    for row in read_catalog(library):
        name = string(row["directory"])
        if not (library / name).is_dir():
            continue
        for digest in json_items(row.get("source_sha256", [])):
            sources.setdefault(string(digest), name)
        texts.setdefault(string(row["text_sha256"]), name)
    return sources, texts


def _collect(sources: Sequence[Path]) -> list[Path]:
    """Expand batch arguments into candidate files.

    Parameters
    ----------
    sources : Sequence of Path
        Directories, whose top-level PDFs are taken, and files.

    Returns
    -------
    list of Path
        Candidate files in argument order.
    """
    paths: list[Path] = []
    for source in sources:
        paths.extend(discover_pdfs(source) if source.is_dir() else (source,))
    return paths


def _plan(sources: Sequence[Path], library: Path) -> tuple[IntakePlan, dict[str, str]]:
    """Plan intake against the library.

    Parameters
    ----------
    sources : Sequence of Path
        Batch arguments.
    library : Path
        Library root.

    Returns
    -------
    tuple
        The intake plan and the library's text digests.
    """
    known, texts = _library_digests(library)
    logger.info("Planning intake of %d argument(s)", len(sources))
    return plan_intake(_collect(sources), known=known), texts


def _failure(
    group: SourceGroup, stage: str, message: str, run: Path | None
) -> ItemResult:
    """Build a failed item.

    Parameters
    ----------
    group : SourceGroup
        Source group.
    stage : str
        Failed stage.
    message : str
        Reason.
    run : Path or None
        Run directory kept for diagnosis.

    Returns
    -------
    ItemResult
        Failed item.
    """
    return ItemResult(
        path=str(group.primary.path),
        status="failed",
        sha256=group.sha256,
        aliases=tuple(str(item.path) for item in group.aliases),
        stage=stage,
        message=message,
        run=None if run is None else str(run),
    )


def _publish_run(
    run: Path,
    library: Path,
    lookup: Lookup | None,
    *,
    refresh: bool,
    search: Search | None = None,
) -> ItemResult:
    """Resolve identity when needed and publish one run directory.

    Parameters
    ----------
    run : Path
        Run directory with a completed extraction.
    library : Path
        Library root.
    lookup : Callable or None
        Registry lookup, or None to keep the run's identity or publish it
        unverified.
    refresh : bool
        Resolve identity even when ``identity.json`` exists.
    search : Callable or None
        Bibliographic search for papers without an accepted DOI.

    Returns
    -------
    ItemResult
        Published item without the source path.

    Raises
    ------
    PublicationConflictError
        The target directory exists.
    """
    if lookup is not None and (refresh or not (run / IDENTITY_FILENAME).is_file()):
        logger.info("Resolving bibliographic identity")
        resolve_and_write_identity(run, lookup, search)
    paper = publish(run, library)
    document = Document.from_json((paper.directory / DOCUMENT_FILENAME).read_text())
    identity_path = run / IDENTITY_FILENAME
    status = (
        Identity.from_json(identity_path.read_text()).status
        if identity_path.is_file()
        else "UNVERIFIED"
    )
    return ItemResult(
        path=str(run),
        status="published",
        sha256=document.source_sha256,
        directory=paper.name,  # relative path within the library
        identity=status,
        processing=processing_status(document),
        findings=len(document.findings),
    )


def _process(
    group: SourceGroup,
    context: _Context,
    supplements: Sequence[SourceGroup] = (),
    equivalents: Sequence[SourceGroup] = (),
) -> ItemResult:
    """Extract, identify and publish one paper with its supplements.

    Parameters
    ----------
    group : SourceGroup
        Paper to extract; its aliases are recorded, not extracted.
    context : _Context
        Shared settings.
    supplements : Sequence of SourceGroup
        Supplement groups published in the same paper directory.
    equivalents : Sequence of SourceGroup
        Groups with the same text, preserved in the paper without extraction.

    Returns
    -------
    ItemResult
        Published, failed or conflicting item. A failure keeps the run
        directory; a success removes it unless asked to keep it, because the
        paper directory already holds its raw output and logs.
    """
    earlier = context.staged.get(group.sha256)
    if earlier is not None:
        return ItemResult(
            path=str(group.primary.path),
            status="staged",
            sha256=group.sha256,
            aliases=tuple(str(alias.path) for alias in group.aliases),
            run=str(earlier),
            message="staged by an earlier run",
        )
    # The random part keeps two runs of one source in the same second apart.
    stamp = (
        f"{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%SZ}-{group.sha256[:12]}"
        f"-{secrets.token_hex(3)}"
    )
    runs = (
        context.runs_to or context.config.library / INTERNAL_DIRECTORY / RUNS_DIRECTORY
    )
    run = runs / stamp
    stage = "extraction"
    try:
        run.mkdir(parents=True)
        sources = PaperSources(
            group.primary.path,
            tuple(item.primary.path for item in supplements),
            context.captures.get(group.sha256, ()),
        )
        bundle = extract_bundle(
            sources, run, context.settings, request_id=stamp, pages=context.pages
        )
        write_hints(run, context.hints.get(group.sha256, {}))
        if bundle.failure is None:
            for copy in equivalents:
                attach_equivalent(run, copy.primary.path)
        failed = bundle.failure
        if failed is not None:
            failure = failed.result.failure
            assert failure is not None  # the protocol requires it for failures
            where = (
                ""
                if failed is bundle.main
                else f"supplement {failed.source.original_name}: "
            )
            return _failure(
                group, stage, f"{where}{failure.kind}: {failure.message}", run
            )
        if context.runs_to is not None:
            return ItemResult(
                path=str(group.primary.path),
                status="staged",
                sha256=group.sha256,
                aliases=tuple(str(alias.path) for alias in group.aliases),
                supplements=tuple(str(item.primary.path) for item in supplements),
                captures=tuple(str(path) for path in sources.captures),
                run=str(run),
            )
        stage = "publication"
        item = _publish_run(
            run,
            context.config.library,
            context.settings.lookup,
            refresh=False,
            search=context.settings.search,
        )
    except PublicationConflictError as exc:
        return replace(_failure(group, stage, str(exc), run), status="conflict")
    except (WorkerError, SourceChangedError, ValueError, OSError) as exc:
        # Batch boundary: record this paper's failure and let the others run.
        return _failure(group, stage, f"{type(exc).__name__}: {exc}", run)
    if not context.keep_runs:
        shutil.rmtree(run)
    return replace(
        item,
        path=str(group.primary.path),
        aliases=tuple(str(alias.path) for alias in group.aliases),
        supplements=tuple(str(item.primary.path) for item in supplements),
        captures=tuple(str(path) for path in sources.captures),
    )


def _supplement_item(
    group: SourceGroup, message: str, directory: str | None = None
) -> ItemResult:
    """Build a held item for a supplement that is not extracted.

    Parameters
    ----------
    group : SourceGroup
        Supplement group.
    message : str
        Reason.
    directory : str or None
        Related paper, if known.

    Returns
    -------
    ItemResult
        Held item.
    """
    return ItemResult(
        path=str(group.primary.path),
        status="held",
        sha256=group.sha256,
        directory=directory,
        aliases=tuple(str(item.path) for item in group.aliases),
        message=message,
    )


def _run_plan(
    plan: IntakePlan,
    texts: Mapping[str, str],
    context: _Context,
    pairing: SupplementPairing | None = None,
) -> list[ItemResult]:
    """Run a plan, through serving worker processes when configured.

    Parameters
    ----------
    plan : IntakePlan
        Intake plan.
    texts : Mapping of str to str
        Library text digests.
    context : _Context
        Shared settings.
    pairing : SupplementPairing or None
        Supplements of the batch, or None to pair them by name and DOI.

    Returns
    -------
    list of ItemResult
        As for :func:`_extract_plan`.
    """
    persistent = context.config.persistent
    if persistent is None:
        profile = context.settings.profile
        persistent = isinstance(profile, MineruProfile) and profile.vlm_engine == "vllm"
    if not persistent:
        return _extract_plan(plan, texts, context, pairing)
    now = dt.datetime.now(dt.UTC)
    base = context.runs_to or context.config.library / INTERNAL_DIRECTORY
    directory = (
        base / SESSIONS_DIRECTORY / f"{now:%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"
    )
    try:
        with WorkerSessions(directory) as sessions:
            shared = replace(
                context, settings=replace(context.settings, sessions=sessions)
            )
            return _extract_plan(plan, texts, shared, pairing)
    finally:
        # Each request keeps its own log; the processes' directory holds
        # only their homes and caches.
        if not context.keep_runs:
            shutil.rmtree(directory, ignore_errors=True)


def _extract_plan(
    plan: IntakePlan,
    texts: Mapping[str, str],
    context: _Context,
    pairing: SupplementPairing | None = None,
) -> list[ItemResult]:
    """Apply the automatic duplicate actions and extract the rest.

    Parameters
    ----------
    plan : IntakePlan
        Intake plan.
    texts : Mapping of str to str
        Library text digests.
    context : _Context
        Shared settings.
    pairing : SupplementPairing or None
        Supplements of the batch, or None to pair them by name and DOI.

    Returns
    -------
    list of ItemResult
        One item per group and per rejected file, in plan order; a paired
        supplement is reported with its paper, and a supplement without a
        paper to extract with is held.
    """
    held = {item.sha256: item for item in content_equivalents(plan, texts)}
    if pairing is None:
        pairing = pair_supplements(plan, exclude=held)
    paper_of = {
        member.sha256: paper
        for paper, members in pairing.pairs.items()
        for member in members
    }
    by_sha = {group.sha256: group for group in plan.groups}
    unpaired = {group.sha256 for group in pairing.unpaired}
    items: list[ItemResult] = [
        ItemResult(
            path=str(item.path), status="rejected", stage="intake", message=item.reason
        )
        for item in plan.rejected
    ]
    todo = [
        g
        for g in plan.to_extract()
        if g.sha256 not in held
        and g.sha256 not in paper_of
        and g.sha256 not in unpaired
    ]
    done = 0
    outcomes: dict[str, ItemResult] = {}
    for group in plan.groups:
        aliases = tuple(str(item.path) for item in group.aliases)
        if group.known_as is not None:
            items.append(
                ItemResult(
                    path=str(group.primary.path),
                    status="skipped",
                    sha256=group.sha256,
                    directory=group.known_as,
                    aliases=aliases,
                    message="identical bytes already in the library",
                )
            )
        elif group.sha256 in paper_of:
            paper = by_sha[paper_of[group.sha256]]
            if paper.known_as is not None:
                items.append(
                    _supplement_item(
                        group,
                        "supplement of a paper already in the library; add it "
                        "with `paperextract extract PAPER --supplement FILE`",
                        paper.known_as,
                    )
                )
        elif group.sha256 in unpaired:
            items.append(
                _supplement_item(
                    group,
                    "looks like a supplement, but no paper in this batch matches "
                    f"({pairing.reasons[group.sha256]}); publish it with "
                    "`paperextract extract PAPER --supplement FILE`",
                )
            )
        elif group.sha256 in held:
            # Settled after the loop, once the equivalent paper is published.
            items.append(
                ItemResult(
                    path=str(group.primary.path),
                    status="held",
                    sha256=group.sha256,
                    directory=held[group.sha256].equivalent_to,
                    aliases=aliases,
                    message="identical text to another source; not extracted",
                )
            )
        else:
            done += 1
            logger.info("[%d/%d] %s", done, len(todo), group.primary.path.name)
            members = [
                member
                for member in pairing.pairs.get(group.sha256, ())
                if member.known_as is None and member.sha256 not in held
            ]
            copies = [
                by_sha[entry.sha256]
                for entry in held.values()
                if not entry.in_library
                and entry.equivalent_to == str(group.primary.path)
            ]
            item = _process(group, context, members, copies)
            outcomes[str(group.primary.path)] = item
            logger.info("[%d/%d] %s", done, len(todo), item.status)
            items.append(item)
    return [_equivalent_outcome(item, held, outcomes, context) for item in items]


def _equivalent_outcome(
    item: ItemResult,
    held: Mapping[str, Equivalence],
    outcomes: Mapping[str, ItemResult],
    context: _Context,
) -> ItemResult:
    """Settle a held content-equivalent copy once its paper is known.

    Parameters
    ----------
    item : ItemResult
        Any item of the run.
    held : Mapping of str to Equivalence
        Held groups by digest.
    outcomes : Mapping of str to ItemResult
        Results of the papers extracted in this run, by source path.
    context : _Context
        Shared settings.

    Returns
    -------
    ItemResult
        The item unchanged, or for a held copy: ``attached`` with the paper's
        directory when the copy was preserved in it, else still ``held``.
        A copy of a paper already in the library is added to it by
        republishing that paper without extraction.
    """
    entry = held.get(item.sha256 or "") if item.status == "held" else None
    if entry is None:
        return item
    if entry.in_library:
        paper = context.config.library / entry.equivalent_to
        result = _reprocess_one(
            paper.resolve(),
            context.config,
            None,
            refresh=False,
            additions=Additions(equivalents=(Path(item.path),)),
        )
        if result.status != "republished":
            return replace(item, message=f"not added to the paper: {result.message}")
        directory = result.directory
    else:
        outcome = outcomes.get(entry.equivalent_to)
        if outcome is None or outcome.status != "published":
            return item
        directory = outcome.directory
    return replace(
        item,
        status="attached",
        directory=directory,
        message="identical text; preserved with the paper as an equivalent copy",
    )


def exit_code(items: Sequence[ItemResult], *, strict: bool) -> int:
    """Compute the exit status for a set of item results.

    Parameters
    ----------
    items : Sequence of ItemResult
        Every item of the invocation.
    strict : bool
        Treat warnings as a partial result.

    Returns
    -------
    int
        ``0`` when every item succeeded, ``4`` for a mix of successes and
        problems or for warnings under ``strict``, else ``3`` when every
        problem is a publication conflict and ``5`` otherwise.
    """
    problems = [item for item in items if item.status not in _SUCCESS]
    if not problems:
        warned = strict and any(item.warnings() for item in items)
        return EXIT_PARTIAL if warned else EXIT_OK
    if len(problems) < len(items):
        return EXIT_PARTIAL
    if all(item.status == "conflict" for item in problems):
        return EXIT_CONFLICT
    return EXIT_FAILURE


def _result(
    command: str, config: Configuration, items: Sequence[ItemResult]
) -> dict[str, object]:
    """Build the versioned result document.

    Parameters
    ----------
    command : str
        Command name.
    config : Configuration
        Resolved configuration.
    items : Sequence of ItemResult
        Every item.

    Returns
    -------
    dict of str to object
        Result with a per-status summary.
    """
    summary: dict[str, int] = {}
    for item in items:
        summary[item.status] = summary.get(item.status, 0) + 1
    return {
        "schema": RESULT_SCHEMA,
        "schema_version": RESULT_VERSION,
        "command": command,
        "library": str(config.library),
        "configuration": config.to_dict(),
        "summary": dict(sorted(summary.items())),
        "items": [item.to_dict() for item in items],
    }


def _item_line(item: ItemResult) -> str:
    """Format one item for the text summary.

    Parameters
    ----------
    item : ItemResult
        Item.

    Returns
    -------
    str
        One tab-separated line, plus alias lines.
    """
    if item.status in {"published", "republished"}:
        detail = f"{item.directory}\t{item.identity}\t{item.processing}"
        detail += f"\t{item.findings} findings"
        if item.message is not None:
            detail += f"\t{item.message}"
    elif item.status in {"skipped", "held"}:
        detail = f"{item.directory}\t{item.message}"
    elif item.status == "staged":
        detail = f"{item.run}" + (f"\t{item.message}" if item.message else "")
    else:
        detail = f"{item.stage}\t{item.message}"
        if item.run is not None:
            detail += f"\trun kept: {item.run}"
    lines = [f"{item.status}\t{Path(item.path).name}\t{detail}"]
    lines.extend(f"alias\t{Path(alias).name}" for alias in item.aliases)
    lines.extend(f"supplement\t{Path(path).name}" for path in item.supplements)
    lines.extend(f"capture\t{Path(path).name}" for path in item.captures)
    return "\n".join(lines)


def _text(
    document: Mapping[str, object], lines: Sequence[str], config: Configuration
) -> str:
    """Format a result document for people.

    Parameters
    ----------
    document : Mapping of str to object
        Result document.
    lines : Sequence of str
        Formatted item lines.
    config : Configuration
        Resolved configuration, for the library origin.

    Returns
    -------
    str
        Item lines, the library and a summary line.
    """
    summary = cast("dict[str, int]", document["summary"])
    counts = ", ".join(f"{count} {status}" for status, count in summary.items())
    footer = [f"library\t{config.library} ({config.origins['library']})"]
    footer.append(counts or "nothing to do")
    return "\n".join([*lines, *footer]) + "\n"


def _emit(
    command: str,
    config: Configuration,
    items: Sequence[ItemResult],
    *,
    as_json: bool,
) -> dict[str, object]:
    """Write the result to standard output.

    Parameters
    ----------
    command : str
        Command name.
    config : Configuration
        Resolved configuration.
    items : Sequence of ItemResult
        Every item.
    as_json : bool
        Print JSON instead of text.

    Returns
    -------
    dict of str to object
        The result document.
    """
    document = _result(command, config, items)
    if as_json:
        sys.stdout.write(dump(document))
    else:
        lines = [_item_line(item) for item in items]
        sys.stdout.write(_text(document, lines, config))
    return document


def _record_batch(
    config: Configuration, document: Mapping[str, object], runs_to: Path | None
) -> Path:
    """Persist a batch result inside the library's private directory.

    Parameters
    ----------
    config : Configuration
        Resolved configuration.
    document : Mapping of str to object
        Result document.
    runs_to : Path or None
        Directory of staged runs, which then holds the record instead.

    Returns
    -------
    Path
        Written report.
    """
    base = runs_to or config.library / INTERNAL_DIRECTORY
    directory = base / BATCHES_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    stamp = f"{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%S%fZ}"
    path = directory / f"{stamp}.json"
    path.write_text(dump(document))
    logger.info("Batch record written to %s", path)
    return path


def _command_extract(args: argparse.Namespace, config: Configuration) -> int:
    """Run ``extract`` for one paper and its supplements.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    config : Configuration
        Resolved configuration.

    Returns
    -------
    int
        Exit status.

    Raises
    ------
    ConfigurationError
        Several positional paths were given, the source is a directory, or
        the paper or a supplement is not a readable PDF.
    """
    if len(args.sources) > 1:
        raise ConfigurationError(
            "Several paths would form one paper; pass supplements with "
            "--supplement, or use `paperextract batch` for separate papers"
        )
    source: Path = args.sources[0]
    hints: dict[str, str] = {}
    if args.document_version:
        hints["version"] = args.document_version
    if not source.exists() and arxiv_request(str(source)) is not None:
        acquisition = _acquire(str(source), config)
        source = acquisition.path
        hints.update(acquisition.hints())
    if source.is_dir():
        raise ConfigurationError(f"{source} is a directory; use `paperextract batch`")
    _check_captures(args.html)
    context = _context(args, config)
    plan, texts = _plan([source], config.library)
    context = replace(
        context,
        captures={group.sha256: tuple(args.html) for group in plan.groups},
        hints={group.sha256: hints for group in plan.groups},
    )
    extras = plan_intake(args.supplement)
    rejected = [*plan.rejected, *extras.rejected]
    if rejected:
        raise ConfigurationError(f"{rejected[0].path}: {rejected[0].reason}")
    if any(group.sha256 in {g.sha256 for g in plan.groups} for group in extras.groups):
        raise ConfigurationError("A supplement has the same bytes as the paper")
    known = plan.groups[0].known_as if len(plan.groups) == 1 else None
    if known is not None and extras.groups:
        # The paper is already published: add the supplements to it.
        items = [
            _attach_supplements(
                config.library / known,
                [group.primary.path for group in extras.groups],
                context.settings,
                config,
            )
        ]
    else:
        pairing = SupplementPairing(
            pairs={group.sha256: extras.groups for group in plan.groups},
            unpaired=(),
            reasons={},
        )
        items = _run_plan(plan, texts, context, pairing)
    _refresh(config, items)
    _emit("extract", config, items, as_json=args.json)
    return exit_code(items, strict=args.strict)


def _attach_supplements(
    paper: Path,
    supplements: Sequence[Path],
    settings: ExtractionSettings,
    config: Configuration,
) -> ItemResult:
    """Extract supplements and republish a published paper with them.

    Parameters
    ----------
    paper : Path
        Published paper directory.
    supplements : Sequence of Path
        Supplementary PDFs to add, after any the paper already has.
    settings : ExtractionSettings
        Worker settings for the supplements.
    config : Configuration
        Resolved configuration.

    Returns
    -------
    ItemResult
        Republished item, or a failed one that leaves the published paper
        unchanged and keeps the run directory. The paper is rebuilt from its
        kept output as by ``reprocess``, keeping its identity and figure
        descriptions; only the supplements are extracted.
    """
    now = dt.datetime.now(dt.UTC)
    stamp = f"{now:%Y%m%dT%H%M%SZ}-attach-{secrets.token_hex(3)}"
    run = config.library / INTERNAL_DIRECTORY / RUNS_DIRECTORY / stamp
    try:
        run.mkdir(parents=True)
        stage_from_paper(paper, run)
        rebuild_document(run)
        existing = sorted((run / SUPPLEMENTS_DIRECTORY).glob("*"))
        for directory in existing:
            rebuild_document(directory)
        for number, pdf in enumerate(supplements, len(existing) + 1):
            logger.info("Extracting supplement %s", pdf.name)
            directory = supplement_directory(run, number)
            directory.mkdir(parents=True)
            outcome = extract_pdf(
                pdf, directory, settings, request_id=f"{stamp}-s{number:02d}"
            )
            if outcome.document is None:
                failure = outcome.result.failure
                raise ValueError(
                    f"{pdf.name}: "
                    + ("worker failed" if failure is None else failure.message)
                )
        published = publish(run, config.library, replace=_relative(paper, config))
    except (WorkerError, ValueError, OSError) as exc:
        return ItemResult(
            path=str(paper),
            status="failed",
            stage="supplement",
            message=f"{type(exc).__name__}: {exc}",
            run=str(run),
        )
    shutil.rmtree(run)
    document = Document.from_json((published.directory / DOCUMENT_FILENAME).read_text())
    metadata = json.loads((published.directory / "metadata.json").read_text())
    return ItemResult(
        path=str(paper),
        status="republished",
        sha256=document.source_sha256,
        directory=published.name,
        identity=str(metadata["bibliographic_status"]),
        processing=processing_status(document),
        findings=len(document.findings),
        supplements=tuple(str(pdf) for pdf in supplements),
        message=f"added {len(supplements)} supplement(s)",
    )


def _context(args: argparse.Namespace, config: Configuration) -> _Context:
    """Check the worker setup and build the shared context.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    config : Configuration
        Resolved configuration.

    Returns
    -------
    _Context
        Settings for every item.

    Raises
    ------
    ConfigurationError
        The worker setup is incomplete or the library is not a library.
    """
    # Check the worker setup before the lookup creates its snapshot directory.
    settings = replace(
        config.extraction_settings(None),
        lookup=_lookup(config),
        search=_search(config),
    )
    runs_to: Path | None = getattr(args, "runs_to", None)
    if runs_to is not None:
        # Staged runs never write to the library; it is read, when it
        # exists, only to skip papers it already holds.
        runs_to = runs_to.resolve()
        runs_to.mkdir(parents=True, exist_ok=True)
        corpus = config.library / CORPUS_FILENAME
        if corpus.is_file():
            require_readable(json.loads(corpus.read_text()), str(corpus))
    else:
        try:
            ensure_library(config.library)
        except ValueError as exc:
            raise ConfigurationError(str(exc)) from exc
    return _Context(
        config=config,
        settings=settings,
        pages=getattr(args, "pages", None),
        keep_runs=args.keep_run,
        runs_to=runs_to,
        staged={} if runs_to is None else _completed_runs(runs_to)[0],
    )


def _acquire(request: str, config: Configuration) -> Acquisition:
    """Download an arXiv paper named on the command line.

    Parameters
    ----------
    request : str
        An arXiv request such as ``arXiv:2206.01062v2``.
    config : Configuration
        Resolved configuration.

    Returns
    -------
    Acquisition
        Downloaded PDF below ``.paperextract/acquired/arxiv`` in the library.

    Raises
    ------
    ConfigurationError
        Offline mode forbids the download, or the download failed.
    """
    if config.offline or not config.registry:
        raise ConfigurationError(
            f"{request} would be downloaded from arXiv, but network use is off"
        )
    directory = config.library / INTERNAL_DIRECTORY / "acquired" / "arxiv"
    logger.info("Downloading %s from arXiv", request)
    try:
        return acquire_arxiv(request, directory, urllib_fetch(config.contact))
    except AcquisitionError as exc:
        raise ConfigurationError(str(exc)) from exc


def _batch_manifest(args: argparse.Namespace, config: Configuration) -> int:
    """Run ``batch --manifest``.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    config : Configuration
        Resolved configuration.

    Returns
    -------
    int
        Exit status.

    Raises
    ------
    ConfigurationError
        The manifest is invalid, names a page that is not one, or names
        identical bytes in two entries.
    """
    try:
        entries = read_manifest(args.manifest)
    except (ManifestError, OSError, UnicodeDecodeError) as exc:
        raise ConfigurationError(str(exc)) from exc
    if args.shard is not None:
        entries = shard_entries(entries, *args.shard)
    for entry in entries:
        _check_captures(entry.html)
    context = _context(args, config)
    papers: list[Path] = []
    hints: dict[Path, dict[str, str]] = {}
    for entry in entries:
        hint = {"manifest_id": entry.id}
        if entry.document_version:
            hint["version"] = entry.document_version
        if isinstance(entry.paper, str):
            acquisition = _acquire(entry.paper, config)
            path = acquisition.path
            hint.update(acquisition.hints())
        else:
            path = entry.paper
        papers.append(path)
        hints[path] = hint
    supplements = [path for entry in entries for path in entry.supplements]
    plan, texts = _plan([*papers, *supplements], config.library)
    digest = {
        member.path: group
        for group in plan.groups
        for member in (group.primary, *group.aliases)
    }
    owners: dict[str, str] = {}
    for entry, path in zip(entries, papers, strict=True):
        for file in (path, *entry.supplements):
            group = digest.get(file)
            if group is None:
                continue  # rejected at intake and reported as such
            if group.sha256 in owners and owners[group.sha256] != entry.id:
                raise ConfigurationError(
                    f"{file.name} in {entry.id!r} has the same bytes as a file of "
                    f"{owners[group.sha256]!r}"
                )
            owners[group.sha256] = entry.id
    pairing = SupplementPairing(
        pairs={
            digest[path].sha256: tuple(
                digest[item] for item in entry.supplements if item in digest
            )
            for entry, path in zip(entries, papers, strict=True)
            if path in digest
        },
        unpaired=(),
        reasons={},
    )
    context = replace(
        context,
        captures={
            digest[path].sha256: entry.html
            for entry, path in zip(entries, papers, strict=True)
            if path in digest
        },
        hints={digest[path].sha256: hints[path] for path in papers if path in digest},
    )
    items = _run_plan(plan, texts, context, pairing)
    _refresh(config, items)
    document = _emit("batch", config, items, as_json=args.json)
    _record_batch(config, document, context.runs_to)
    return exit_code(items, strict=args.strict)


def _check_captures(paths: Sequence[Path]) -> None:
    """Require every named capture to be a saved web page.

    Parameters
    ----------
    paths : Sequence of Path
        Capture arguments.

    Raises
    ------
    ConfigurationError
        A path is missing or is not a saved HTML page or MHTML archive.
    """
    for path in paths:
        if not path.is_file() or capture_kind(path) is None:
            raise ConfigurationError(
                f"{path} is not a saved HTML page or MHTML archive"
            )


def _discover_captures(sources: Sequence[Path]) -> list[Path]:
    """Find the saved web pages among batch arguments.

    Parameters
    ----------
    sources : Sequence of Path
        Directories, whose top-level files are examined, and files.

    Returns
    -------
    list of Path
        Captures recognized by content, in argument and name order.
    """
    found: list[Path] = []
    for source in sources:
        candidates = (
            sorted(p for p in source.iterdir() if p.is_file())
            if source.is_dir()
            else [source]
        )
        found.extend(path for path in candidates if capture_kind(path) is not None)
    return found


def _capture_items(plan: IntakePlan, pairing: CapturePairing) -> list[ItemResult]:
    """Report the captures that were not published with a new paper.

    Parameters
    ----------
    plan : IntakePlan
        Intake plan.
    pairing : CapturePairing
        Capture pairs.

    Returns
    -------
    list of ItemResult
        A held item for each unpaired capture and for a capture of a paper
        the library already holds.
    """
    items = [
        ItemResult(path=str(path), status="held", stage="intake", message=reason)
        for path, reason in pairing.unpaired.items()
    ]
    for group in plan.groups:
        if group.known_as is None:
            continue
        items.extend(
            ItemResult(
                path=str(path),
                status="held",
                stage="intake",
                directory=group.known_as,
                message="saved page of a paper already in the library; add it "
                f"with `paperextract reprocess {group.known_as} --html FILE`",
            )
            for path in pairing.pairs.get(group.sha256, ())
        )
    return items


def _command_batch(args: argparse.Namespace, config: Configuration) -> int:
    """Run ``batch`` over directories and files.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    config : Configuration
        Resolved configuration.

    Returns
    -------
    int
        Exit status.

    Raises
    ------
    ConfigurationError
        An argument does not exist.
    """
    if args.manifest is not None:
        if args.sources:
            raise ConfigurationError("Give paths or --manifest, not both")
        return _batch_manifest(args, config)
    if args.shard is not None:
        raise ConfigurationError("--shard selects manifest entries; add --manifest")
    if not args.sources:
        raise ConfigurationError("Give directories or files, or --manifest")
    missing = [str(path) for path in args.sources if not path.exists()]
    if missing:
        raise ConfigurationError(f"Not found: {', '.join(missing)}")
    context = _context(args, config)
    captures = _discover_captures(args.sources)
    pdfs = [path for path in args.sources if path.is_dir() or path not in captures]
    plan, texts = _plan(pdfs, config.library)
    pairing = pair_captures(
        plan, [(path, page_doi(read_page_html(path))) for path in captures]
    )
    context = replace(context, captures=pairing.pairs)
    items = _run_plan(plan, texts, context)
    items.extend(_capture_items(plan, pairing))
    _refresh(config, items)
    document = _emit("batch", config, items, as_json=args.json)
    _record_batch(config, document, context.runs_to)
    return exit_code(items, strict=args.strict)


def _command_dedup(args: argparse.Namespace, config: Configuration) -> int:
    """Run ``dedup``: plan intake and report duplicates without extracting.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    config : Configuration
        Resolved configuration.

    Returns
    -------
    int
        ``0``; the report is informational.

    Raises
    ------
    ConfigurationError
        An argument does not exist or the report file already exists.
    """
    missing = [str(path) for path in args.sources if not path.exists()]
    if missing:
        raise ConfigurationError(f"Not found: {', '.join(missing)}")
    report_path: Path | None = args.report
    if report_path is not None and report_path.exists():
        raise ConfigurationError(f"Report already exists: {report_path}")
    manifest_path: Path | None = args.manifest_out
    if manifest_path is not None and manifest_path.exists():
        raise ConfigurationError(f"Manifest already exists: {manifest_path}")
    plan, texts = _plan(args.sources, config.library)
    report = dedup_report(plan, texts)
    report["library"] = str(config.library)
    similar = intake_title_matches(plan, read_catalog(config.library))
    report["same_title"] = [match.to_dict() for match in similar]
    if report_path is not None:
        report_path.write_text(dump(report))
    if manifest_path is not None:
        manifest_path.write_text(
            _proposed_manifest(plan, texts, args.sources, manifest_path)
        )
    if args.json:
        sys.stdout.write(dump(report))
        return EXIT_OK
    text = _dedup_text(plan, content_equivalents(plan, texts), config)
    lines = [
        f"same title?\t{', '.join(Path(path).name for path in match.paths)}"
        + (f"\tlibrary: {', '.join(match.library)}" if match.library else "")
        for match in similar
    ]
    sys.stdout.write("".join(f"{line}\n" for line in lines) + text)
    return EXIT_OK


def _proposed_manifest(
    plan: IntakePlan,
    texts: Mapping[str, str],
    sources: Sequence[Path],
    target: Path,
) -> str:
    """Write the manifest that ``batch`` would follow for these sources.

    Parameters
    ----------
    plan : IntakePlan
        Intake plan.
    texts : Mapping of str to str
        Library text digests.
    sources : Sequence of Path
        ``dedup`` arguments, searched for saved article pages.
    target : Path
        Manifest file, against whose directory paths are made relative.

    Returns
    -------
    str
        One entry per paper to extract, with its supplements and pages;
        copies, held files and library papers are left out.
    """
    held = {item.sha256 for item in content_equivalents(plan, texts)}
    pairing = pair_supplements(plan, exclude=held)
    members = pairing.supplement_digests()
    pages = pair_captures(
        plan,
        [
            (path, page_doi(read_page_html(path)))
            for path in _discover_captures(sources)
        ],
    )
    entries: list[dict[str, object]] = []
    used: set[str] = set()
    for group in plan.to_extract():
        if group.sha256 in held or group.sha256 in members:
            continue
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", group.primary.path.stem)[:56] or "paper"
        identifier, number = stem, 1
        while identifier in used:
            number += 1
            identifier = f"{stem}-{number}"
        used.add(identifier)
        entry: dict[str, object] = {"id": identifier, "paper": group.primary.path}
        supplements = [m.primary.path for m in pairing.pairs.get(group.sha256, ())]
        if supplements:
            entry["supplements"] = supplements
        captures = pages.pairs.get(group.sha256, ())
        if captures:
            entry["html"] = list(captures)
        entries.append(entry)
    return manifest_text(entries, target.resolve().parent)


def _dedup_text(
    plan: IntakePlan, equivalents: Sequence[Equivalence], config: Configuration
) -> str:
    """Format a duplicate report for people.

    Parameters
    ----------
    plan : IntakePlan
        Intake plan.
    equivalents : Sequence of Equivalence
        Groups held back for identical text.
    config : Configuration
        Resolved configuration.

    Returns
    -------
    str
        One line per duplicate or supplement relationship and a summary.
    """
    lines: list[str] = []
    for group in plan.groups:
        name = group.primary.path.name
        if group.known_as is not None:
            lines.append(f"in library\t{name}\t{group.known_as}")
        lines.extend(
            f"identical\t{alias.path.name}\tsame bytes as {name}"
            for alias in group.aliases
        )
    by_sha = {group.sha256: group for group in plan.groups}
    for entry in equivalents:
        held = by_sha[entry.sha256].primary.path.name
        other = Path(entry.equivalent_to).name
        lines.append(f"same text\t{held}\tidentical text to {other}")
    held_digests = {entry.sha256 for entry in equivalents}
    pairing = pair_supplements(plan, exclude=held_digests)
    for paper, members in pairing.pairs.items():
        for member in members:
            lines.append(
                f"supplement\t{member.primary.path.name}\tof "
                f"{by_sha[paper].primary.path.name} "
                f"({pairing.reasons[member.sha256]})"
            )
    lines.extend(
        f"supplement?\t{group.primary.path.name}\tno matching paper; held"
        for group in pairing.unpaired
    )
    supplements = pairing.supplement_digests()
    for doi, paths in sorted(
        shared_doi_candidates(plan, held_digests | supplements).items()
    ):
        names = ", ".join(Path(path).name for path in paths)
        lines.append(f"shared DOI\t{doi}\t{names}; review, each is extracted")
    lines.extend(f"rejected\t{item.path.name}\t{item.reason}" for item in plan.rejected)
    aliases = sum(len(group.aliases) for group in plan.groups)
    reuse = len(plan.groups) - len(plan.to_extract())
    extractable = {group.sha256 for group in plan.to_extract()}
    remaining = len(extractable - held_digests - supplements)
    lines.append(f"library\t{config.library} ({config.origins['library']})")
    lines.append(
        f"{aliases + len(plan.groups) + len(plan.rejected)} files, "
        f"{len(plan.groups)} distinct, {aliases} identical copies, {reuse} in "
        f"library, {len(equivalents)} same text, {len(supplements)} supplements, "
        f"{len(plan.rejected)} rejected; to extract: {remaining}"
    )
    return "\n".join(lines) + "\n"


def _command_publish(args: argparse.Namespace, config: Configuration) -> int:
    """Run ``publish`` for a kept run directory.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    config : Configuration
        Resolved configuration.

    Returns
    -------
    int
        Exit status.

    Raises
    ------
    ConfigurationError
        The run directory has no canonical document.
    """
    runs: list[Path] = []
    items: list[ItemResult] = []
    for given in (path.resolve() for path in args.runs):
        if (given / DOCUMENT_FILENAME).is_file():
            runs.append(given)
            continue
        completed, incomplete = _completed_runs(given)
        if not completed and not incomplete:
            raise ConfigurationError(f"No completed extraction in {given}")
        runs.extend(completed.values())
        items.extend(
            ItemResult(
                path=str(run),
                status="failed",
                stage="extraction",
                message="incomplete run: the extraction did not finish",
            )
            for run in incomplete
        )
    lookup = _lookup(config)
    for number, run in enumerate(runs, 1):
        logger.info("[%d/%d] %s", number, len(runs), run.name)
        items.append(_publish_one(run, config, lookup, refresh=args.refresh_identity))
    _refresh(config, items)
    _emit("publish", config, items, as_json=args.json)
    return exit_code(items, strict=args.strict)


def _completed_runs(directory: Path) -> tuple[dict[str, Path], list[Path]]:
    """Find the run directories inside a directory of staged runs.

    Parameters
    ----------
    directory : Path
        Directory given to ``batch --runs-to``.

    Returns
    -------
    tuple
        Completed runs by source digest, in name order, and run directories
        whose extraction did not finish, such as one a stopped job left.
    """
    completed: dict[str, Path] = {}
    incomplete: list[Path] = []
    if not directory.is_dir():
        return completed, incomplete
    for run in sorted(path for path in directory.iterdir() if path.is_dir()):
        record = run / SOURCE_RECORD_FILENAME
        if not record.is_file():
            continue
        if (run / DOCUMENT_FILENAME).is_file():
            digest = str(json.loads(record.read_text())["sha256"])
            completed.setdefault(digest, run)
        else:
            incomplete.append(run)
    return completed, incomplete


def _publish_one(
    run: Path, config: Configuration, lookup: Lookup | None, *, refresh: bool
) -> ItemResult:
    """Publish one run directory into the library.

    Parameters
    ----------
    run : Path
        Run directory with a completed extraction.
    config : Configuration
        Resolved configuration.
    lookup : Callable or None
        Registry lookup.
    refresh : bool
        Resolve identity again even when the run already has one.

    Returns
    -------
    ItemResult
        Published, conflicting or failed item; the run directory is kept.
    """
    try:
        return _publish_run(
            run, config.library, lookup, refresh=refresh, search=_search(config)
        )
    except PublicationConflictError as exc:
        return ItemResult(
            path=str(run), status="conflict", stage="publication", message=str(exc)
        )
    except (ValueError, OSError) as exc:
        return ItemResult(
            path=str(run),
            status="failed",
            stage="publication",
            message=f"{type(exc).__name__}: {exc}",
        )


@dataclass(frozen=True)
class Additions:
    """Collect what a rebuild adds to a published paper.

    Attributes
    ----------
    captures : tuple of Path
        Saved web pages.
    hints : Mapping of str to str
        User assertions.
    equivalents : tuple of Path
        PDFs with the paper's text.
    """

    captures: tuple[Path, ...] = ()
    hints: Mapping[str, str] = field(default_factory=dict[str, str])
    equivalents: tuple[Path, ...] = ()


def _reprocess_one(
    paper: Path,
    config: Configuration,
    lookup: Lookup | None,
    *,
    refresh: bool,
    additions: Additions | None = None,
) -> ItemResult:
    """Rebuild one published paper from its kept output and replace it.

    Parameters
    ----------
    paper : Path
        Published paper directory.
    config : Configuration
        Resolved configuration.
    lookup : Callable or None
        Registry lookup used when ``refresh`` is set.
    refresh : bool
        Resolve identity again instead of reusing the kept identity.
    additions : Additions or None
        Saved pages, assertions and equivalent copies to add to the paper.

    Returns
    -------
    ItemResult
        Republished or failed item; a failure keeps the run directory and
        leaves the published paper unchanged.
    """
    now = dt.datetime.now(dt.UTC)
    stamp = f"{now:%Y%m%dT%H%M%SZ}-reprocess-{secrets.token_hex(3)}"
    run = config.library / INTERNAL_DIRECTORY / RUNS_DIRECTORY / stamp
    try:
        run.mkdir(parents=True)
        stage_from_paper(paper, run)
        extra = additions or Additions()
        for capture in extra.captures:
            attach_capture(run, capture)
        if extra.hints:
            write_hints(run, {**read_hints(run), **extra.hints})
        for copy in extra.equivalents:
            attach_equivalent(run, copy)
        rebuild_document(run)
        for directory in sorted((run / SUPPLEMENTS_DIRECTORY).glob("*")):
            rebuild_document(directory)
        if refresh and lookup is not None:
            resolve_and_write_identity(run, lookup, _search(config))
        published = publish(run, config.library, replace=_relative(paper, config))
    except (ValueError, OSError) as exc:
        return ItemResult(
            path=str(paper),
            status="failed",
            stage="reprocess",
            message=f"{type(exc).__name__}: {exc}",
            run=str(run),
        )
    shutil.rmtree(run)
    document = Document.from_json((published.directory / DOCUMENT_FILENAME).read_text())
    metadata = json.loads((published.directory / "metadata.json").read_text())
    renamed = published.name != _relative(paper, config)
    return ItemResult(
        path=str(paper),
        status="republished",
        sha256=document.source_sha256,
        directory=published.name,
        identity=str(metadata["bibliographic_status"]),
        processing=processing_status(document),
        findings=len(document.findings),
        message=f"renamed from {_relative(paper, config)}" if renamed else None,
    )


def _command_compare(args: argparse.Namespace, config: Configuration) -> int:
    """Run ``compare``: extract one PDF with several backends and compare.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    config : Configuration
        Resolved configuration.

    Returns
    -------
    int
        ``0`` when every backend completed, ``4`` when some failed and ``5``
        when fewer than two completed.

    Raises
    ------
    ConfigurationError
        The source is not a file, a backend is unknown or repeated, or a
        backend's setup is missing.
    """
    names = [name.strip() for name in str(args.backends).split(",") if name.strip()]
    unknown = [name for name in names if name not in EXTRACTION_BACKENDS]
    if unknown or len(set(names)) != len(names) or len(names) < 2:  # noqa: PLR2004
        raise ConfigurationError(
            "--backends needs two or more distinct backends from "
            f"{', '.join(EXTRACTION_BACKENDS)}"
        )
    source: Path = args.source
    if not source.is_file():
        raise ConfigurationError(f"Not a file: {source}")
    settings = {
        name: replace(
            config, backend=cast("Backend", name), table_check=False
        ).extraction_settings(None)
        for name in names
    }
    stamp = f"{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"
    out: Path = (
        args.out
        if args.out is not None
        else config.library / INTERNAL_DIRECTORY / "compare" / stamp
    )
    documents: dict[str, Document] = {}
    failures: dict[str, str] = {}
    for name in names:
        staging = out / name
        staging.mkdir(parents=True)
        logger.info("Extracting %s with %s", source.name, name)
        try:
            outcome = extract_pdf(
                source,
                staging,
                settings[name],
                request_id=f"{stamp}-{name}",
                pages=args.pages,
            )
        except (WorkerError, ValueError, OSError) as exc:
            failures[name] = f"{type(exc).__name__}: {exc}"
            continue
        if outcome.document is None:
            failure = outcome.result.failure
            failures[name] = "worker failed" if failure is None else failure.message
            continue
        documents[name] = outcome.document
    report: dict[str, object] = {"failures": failures, "out": str(out)}
    if len(documents) >= 2:  # noqa: PLR2004
        report = {**compare_documents(documents), **report}
    (out / "comparison.json").write_text(dump(report))
    if args.json:
        sys.stdout.write(dump(report))
    else:
        sys.stdout.write(_comparison_text(report))
    if len(documents) < 2:  # noqa: PLR2004
        return EXIT_FAILURE
    return EXIT_PARTIAL if failures else EXIT_OK


def _comparison_text(report: Mapping[str, object]) -> str:
    """Format a backend comparison for people.

    Parameters
    ----------
    report : Mapping of str to object
        Comparison report.

    Returns
    -------
    str
        One line per backend, one per comparison and one per failure.
    """
    lines: list[str] = []
    backends = cast("dict[str, dict[str, object]]", report.get("backends", {}))
    for name, summary in backends.items():
        blocks = cast("dict[str, int]", summary["blocks"])
        lines.append(
            f"backend\t{name} {summary['backend_version']}\tpages {summary['pages']}"
            f"\tblocks {sum(blocks.values())}\tinline math {summary['inline_math']}"
            f"\tfigures {len(cast('list[str]', summary['labelled_figures']))}"
            f"\ttables {len(cast('list[str]', summary['labelled_tables']))}"
            f"\tfindings {summary['findings']}"
        )
    comparisons = cast("dict[str, dict[str, object]]", report.get("comparisons", {}))
    for name, comparison in comparisons.items():
        forward = cast("dict[str, int]", comparison["reference_text_in_other"])
        backward = cast("dict[str, int]", comparison["other_text_in_reference"])
        figures = cast("list[dict[str, object]]", comparison["figures"])
        tables = cast("list[dict[str, object]]", comparison["tables"])
        equations = cast("dict[str, list[str]]", comparison["equations"])
        lines.append(
            f"versus\t{report['reference']} and {name}"
            f"\ttext missing {forward['absent']}/{forward['compared']} and "
            f"{backward['absent']}/{backward['compared']}"
            f"\tcaptions agree {sum(f['status'] == 'agrees' for f in figures)}/"
            f"{len(figures)}"
            f"\ttables agree {sum(t['status'] == 'agrees' for t in tables)}/"
            f"{len(tables)}"
            f"\tequations same {len(equations['same_latex'])}, different "
            f"{len(equations['different_latex'])}"
        )
    for name, message in cast("dict[str, str]", report["failures"]).items():
        lines.append(f"failed\t{name}\t{message}")
    lines.append(f"report\t{report['out']}/comparison.json")
    return "".join(f"{line}\n" for line in lines)


def _command_organize(args: argparse.Namespace, config: Configuration) -> int:
    """Run ``organize``.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    config : Configuration
        Resolved configuration.

    Returns
    -------
    int
        Exit status.

    Raises
    ------
    ConfigurationError
        The library does not exist or is not a library.
    """
    if not (config.library / CORPUS_FILENAME).is_file():
        raise ConfigurationError(f"{config.library} is not a library")
    if args.dry_run:
        moves = plan_organize(config.library, args.layout)
    else:
        try:
            moves = organize_library(config.library, args.layout)
        except PublicationConflictError as exc:
            sys.stderr.write(f"ERROR {exc}\n")
            return EXIT_CONFLICT
        refresh_index(config.library)
    verb = "would move" if args.dry_run else "moved"
    document = {
        "schema": "paperextract.organize-result",
        "schema_version": 1,
        "layout": args.layout,
        "dry_run": args.dry_run,
        "moves": [{"old": move.old, "new": move.new} for move in moves],
    }
    if args.json:
        sys.stdout.write(dump(document))
    else:
        sys.stdout.write(
            "".join(f"{verb}\t{move.old}\t{move.new}\n" for move in moves)
            + f"{len(moves)} papers {verb} to layout {args.layout}\n"
        )
    return EXIT_OK


def _record_changes(check: PaperCheck) -> str:
    """Summarize the older records of a paper.

    Parameters
    ----------
    check : PaperCheck
        Paper check.

    Returns
    -------
    str
        Such as ``paper-manifest 1→3, document 0.1→0.3, table 1→3 (2 files)``.
    """
    counts: dict[tuple[str, str, str], int] = {}
    for record in check.records:
        if record.state == "older":
            key = (
                record.schema.removeprefix("paperextract."),
                record.version,
                READABLE_VERSIONS[record.schema][-1],
            )
            counts[key] = counts.get(key, 0) + 1
    return ", ".join(
        f"{name} {old}→{new}" + (f" ({count} files)" if count > 1 else "")
        for (name, old, new), count in counts.items()
    )


def _catalog_state(library: Path) -> str:
    """Classify the row versions of a library's catalog.

    Parameters
    ----------
    library : Path
        Library root.

    Returns
    -------
    str
        ``absent``, ``unreadable``, ``unsupported``, ``older`` or
        ``current``.
    """
    path = library / CATALOG_FILENAME
    if not path.is_file():
        return "absent"
    states: set[str | None] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = mapping(json.loads(line))
        except ValueError:  # includes JSONDecodeError
            return "unreadable"
        states.add(record_state(CATALOG_ROW_SCHEMA, row.get("schema_version")))
    for state in ("unsupported", "older"):
        if state in states:
            return state
    return "current"


_MIGRATE_OUTCOMES: Mapping[str, str] = {
    "current": "current",
    "outdated": "would migrate",
    "unsupported": "refused",
    "damaged": "refused",
}
_MIGRATE_SHOWN = 5


def _migrate_detail(entry: Mapping[str, object], check: PaperCheck) -> str:
    """Describe one paper's migration outcome.

    Parameters
    ----------
    entry : Mapping of str to object
        Result entry with ``outcome`` and an optional ``message``.
    check : PaperCheck
        The paper's check.

    Returns
    -------
    str
        Record changes, problems, unsupported records or the failure.
    """
    if "message" in entry:
        return str(entry["message"])
    if check.problems:
        shown = list(check.problems[:_MIGRATE_SHOWN])
        if len(check.problems) > _MIGRATE_SHOWN:
            shown.append(f"and {len(check.problems) - _MIGRATE_SHOWN} more")
        return "damaged: " + "; ".join(shown)
    unsupported = [
        f"{record.path} {record.schema} {record.version}"
        for record in check.records
        if record.state == "unsupported"
    ]
    if unsupported:
        return "unsupported: " + "; ".join(unsupported)
    return _record_changes(check)


def _command_migrate(args: argparse.Namespace, config: Configuration) -> int:
    """Run ``migrate``: check a library and rebuild its outdated papers.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    config : Configuration
        Resolved configuration.

    Returns
    -------
    int
        ``0`` when every paper is current afterwards (or would be), ``4``
        when some papers were refused or failed, ``5`` when all were.

    Raises
    ------
    ConfigurationError
        The library does not exist.
    """
    library = config.library
    corpus_path = library / CORPUS_FILENAME
    if not corpus_path.is_file():
        raise ConfigurationError(f"Not a library: {library}")
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    require_readable(corpus, str(corpus_path))
    if not args.dry_run:
        for message in recover_library(library):
            logger.warning("%s", message)
    catalog = _catalog_state(library)
    papers = paper_directories(library)
    entries: list[dict[str, object]] = []
    lines: list[str] = []
    for number, paper in enumerate(papers, 1):
        logger.info("[%d/%d] checking %s", number, len(papers), paper.name)
        check = check_paper(paper, library)
        entry = {**check.to_dict(), "outcome": _MIGRATE_OUTCOMES[check.state]}
        if check.state == "outdated" and not args.dry_run:
            item = _reprocess_one(paper, config, None, refresh=False)
            if item.status == "republished":
                entry["outcome"] = "migrated"
            else:
                entry.update(outcome="failed", message=item.message)
        entries.append(entry)
        detail = _migrate_detail(entry, check)
        lines.append(f"{entry['outcome']}\t{check.directory}\t{detail}".rstrip("\t"))
    rebuilt = False
    if not args.dry_run and not any(e["state"] == "unsupported" for e in entries):
        rebuild_catalog(library, corpus.get("library_id"))
        refresh_index(library)
        rebuilt = True
    document = {
        "schema": "paperextract.migrate-result",
        "schema_version": 1,
        "library": str(library),
        "dry_run": args.dry_run,
        "catalog": {"state": catalog, "rebuilt": rebuilt},
        "papers": entries,
    }
    outcomes = [str(e["outcome"]) for e in entries]
    if args.json:
        sys.stdout.write(dump(document))
    else:
        summary = ", ".join(
            f"{outcomes.count(name)} {name}"
            for name in ("current", "would migrate", "migrated", "refused", "failed")
            if name in outcomes
        )
        state = f"{catalog}, rebuilt" if rebuilt else catalog
        sys.stdout.write(
            "".join(f"{line}\n" for line in lines)
            + f"catalog\t{state}\n{summary or 'no papers'}\n"
        )
    problems = sum(outcome in {"refused", "failed"} for outcome in outcomes)
    if not problems:
        return EXIT_OK
    return EXIT_PARTIAL if problems < len(outcomes) else EXIT_FAILURE


def _size(count: int) -> str:
    """Format a byte count for people.

    Parameters
    ----------
    count : int
        Bytes.

    Returns
    -------
    str
        Such as ``858 MB`` or ``1.2 GB``.
    """
    if count >= 10**9:
        return f"{count / 10**9:.1f} GB"
    return f"{count // 10**6} MB"


def _model_targets(
    config: Configuration, manifest: ModelManifest, names: Sequence[str]
) -> list[tuple[ModelSnapshot, Path]]:
    """Resolve where each snapshot of some sets belongs.

    Parameters
    ----------
    config : Configuration
        Resolved configuration.
    manifest : ModelManifest
        Model manifest.
    names : Sequence of str
        Set names.

    Returns
    -------
    list of tuple
        Each snapshot once, with its directory.

    Raises
    ------
    ConfigurationError
        A backend's model directory is not known.
    """
    roots = config.model_roots()
    seen: dict[str, tuple[ModelSnapshot, Path]] = {}
    for name in names:
        for member in manifest.sets[name].models:
            snapshot = manifest.models[member]
            if snapshot.backend == "describe" and config.describe.model_dir:
                seen[member] = (snapshot, config.describe.model_dir)
                continue
            root = roots.get(snapshot.backend)
            if root is None:
                raise ConfigurationError(
                    f"No model directory for {snapshot.backend}; set [worker] root"
                )
            seen[member] = (snapshot, root / snapshot.directory)
    return list(seen.values())


def _model_line(
    snapshot: ModelSnapshot, target: Path, args: argparse.Namespace
) -> tuple[str, bool]:
    """Check or fetch one snapshot.

    Parameters
    ----------
    snapshot : ModelSnapshot
        Snapshot.
    target : Path
        Its directory.
    args : argparse.Namespace
        Parsed ``models`` arguments.

    Returns
    -------
    tuple of str and bool
        Result line, and whether the snapshot is complete afterwards.
    """
    if args.action == "fetch":
        try:
            count = fetch_snapshot(snapshot, target, _DOWNLOAD)
        except (OSError, ValueError) as exc:
            return f"failed\t{snapshot.name}\t{type(exc).__name__}: {exc}", False
        return f"fetched\t{snapshot.name}\t{count} files\t{target}", True
    status = snapshot_status(snapshot, target, verify=args.verify)
    if status.state == "complete":
        return (
            f"complete\t{snapshot.name}\t{_size(snapshot.total_bytes)}\t{target}",
            True,
        )
    todo = f"{len(status.missing)} files, {_size(status.missing_bytes)} to fetch"
    return f"{status.state}\t{snapshot.name}\t{todo}\t{target}", False


def _binary_line(
    manifest: ModelManifest, root: Path, *, fetch: bool
) -> tuple[str, bool]:
    """Check or fetch this platform's pinned llama.cpp server.

    Parameters
    ----------
    manifest : ModelManifest
        Model manifest.
    root : Path
        Source checkout; the binary goes under its ``model-cache``.
    fetch : bool
        Download it when missing.

    Returns
    -------
    tuple of str and bool
        Result line, and whether the binary is installed afterwards.
    """
    binary = manifest.binary_for(current_platform())
    if binary is None:
        return (
            f"unavailable\tllama.cpp\tno pinned build for {current_platform()}",
            False,
        )
    target = root / "model-cache" / binary.directory
    where = f"{binary.name}\t{binary.platform}\t{target}"
    if fetch:
        try:
            fetch_binary(binary, target, _DOWNLOAD)
        except (OSError, ValueError) as exc:
            return f"failed\t{binary.name}\t{type(exc).__name__}: {exc}", False
        return f"fetched\t{where}", True
    if binary_installed(binary, target):
        return f"complete\t{where}", True
    return f"absent\t{where}", False


def _command_models(args: argparse.Namespace, config: Configuration) -> int:
    """Run ``models status`` or ``models fetch``.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    config : Configuration
        Resolved configuration.

    Returns
    -------
    int
        ``0`` when every snapshot is complete (status) or fetched (fetch),
        ``4`` when some are not, ``5`` when a fetch failed for all.

    Raises
    ------
    ConfigurationError
        No source checkout, an unknown set, a fetch without sets, or a fetch
        while offline.
    """
    if config.worker_root is None:
        raise ConfigurationError("Model sets need a source checkout; set [worker] root")
    try:
        manifest = load_manifest(config.worker_root / MODEL_MANIFEST)
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"Cannot read the model manifest: {exc}") from exc
    names: list[str] = args.sets
    unknown = [name for name in names if name not in manifest.sets]
    if unknown:
        raise ConfigurationError(
            f"Unknown model sets {unknown}; known: {', '.join(manifest.sets)}"
        )
    if args.action == "fetch":
        if not names:
            raise ConfigurationError(
                "Name the sets to fetch, such as: models fetch mineru"
            )
        if config.offline:
            raise ConfigurationError(
                "Fetching models needs the network; --offline is set"
            )
    names = names or list(manifest.sets)
    lines: list[str] = []
    problems = 0
    for snapshot, target in _model_targets(config, manifest, names):
        line, ok = _model_line(snapshot, target, args)
        lines.append(line)
        problems += not ok
    if any(manifest.sets[name].binaries for name in names):
        line, ok = _binary_line(
            manifest, config.worker_root, fetch=args.action == "fetch"
        )
        lines.append(line)
        problems += not ok
    sys.stdout.write("".join(f"{line}\n" for line in lines))
    if not problems:
        return EXIT_OK
    return EXIT_PARTIAL if problems < len(lines) else EXIT_FAILURE


def _command_reprocess(args: argparse.Namespace, config: Configuration) -> int:
    """Run ``reprocess`` for named papers or the whole library.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    config : Configuration
        Resolved configuration.

    Returns
    -------
    int
        Exit status.

    Raises
    ------
    ConfigurationError
        No paper was named, or a named paper is not in the library.
    """
    papers = _papers(args, config, "reprocess")
    captures: list[Path] = args.html
    if (captures or args.document_version or args.doi or args.bibtex) and (
        args.all or len(papers) != 1
    ):
        raise ConfigurationError(
            "--html, --document-version, --doi and --bibtex apply to exactly one "
            "named paper"
        )
    if args.doi and args.bibtex:
        raise ConfigurationError("Give --doi or --bibtex, not both")
    _check_captures(captures)
    hints: dict[str, str] = {}
    if args.document_version:
        hints["version"] = args.document_version
    if args.doi:
        if normalize_doi(args.doi) is None:
            raise ConfigurationError(f"--doi {args.doi!r} is not a DOI")
        hints["doi"] = args.doi
    if args.bibtex:
        hints["bibtex"] = _asserted_bibtex(args.bibtex)
    # An asserted identity only takes effect when identity is resolved again.
    refresh = args.refresh_identity or bool(args.doi or args.bibtex)
    lookup = _lookup(config) if refresh else None
    items: list[ItemResult] = []
    for number, paper in enumerate(papers, 1):
        logger.info("[%d/%d] %s", number, len(papers), paper.name)
        items.append(
            _reprocess_one(
                paper,
                config,
                lookup,
                refresh=refresh,
                additions=Additions(captures=tuple(captures), hints=hints),
            )
        )
    _refresh(config, items)
    _emit("reprocess", config, items, as_json=args.json)
    return exit_code(items, strict=args.strict)


def _asserted_bibtex(path: Path) -> str:
    """Read and check the BibTeX entry a user asserts for a paper.

    Parameters
    ----------
    path : Path
        File with exactly one entry.

    Returns
    -------
    str
        The file's text.

    Raises
    ------
    ConfigurationError
        The file is missing or its entry is unusable.
    """
    if not path.is_file():
        raise ConfigurationError(f"Not found: {path}")
    text = path.read_text(encoding="utf-8")
    entries = read_entries(text)
    if len(entries) != 1:
        raise ConfigurationError(f"{path} must hold exactly one BibTeX entry")
    fields = entries[0][1]
    missing = [name for name in ("author", "title", "year") if not fields.get(name)]
    if missing:
        raise ConfigurationError(f"{path} lacks {', '.join(missing)}")
    if fields.get("doi"):
        raise ConfigurationError(f"{path} has a DOI; assert it with --doi instead")
    if not fields["year"].isdigit():
        raise ConfigurationError(f"{path}: the year is not a number")
    return text


def _relative(paper: Path, config: Configuration) -> str:
    """Give a paper directory's path within the library.

    Parameters
    ----------
    paper : Path
        Absolute paper directory.
    config : Configuration
        Resolved configuration.

    Returns
    -------
    str
        Relative POSIX path, such as ``2019/Travers_2019_HighEnergyPulse``.
    """
    return paper.relative_to(config.library.resolve()).as_posix()


def _papers(
    args: argparse.Namespace, config: Configuration, command: str
) -> list[Path]:
    """Resolve the published papers a command names.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments with ``papers`` and ``all``.
    config : Configuration
        Resolved configuration.
    command : str
        Command name for messages.

    Returns
    -------
    list of Path
        Absolute paper directories.

    Raises
    ------
    ConfigurationError
        No paper was named, or a named paper is not in the library.
    """
    if args.all:
        papers = [
            config.library / str(row["directory"])
            for row in read_catalog(config.library)
        ]
    else:
        # A bare paper name also finds the paper inside a shard directory.
        by_name: dict[str, str] = {
            PurePosixPath(str(row["directory"])).name: str(row["directory"])
            for row in read_catalog(config.library)
        }
        named: list[Path] = args.papers
        papers = [
            path
            if path.is_dir()
            else config.library / by_name.get(path.as_posix(), path.as_posix())
            for path in named
        ]
    if not papers:
        raise ConfigurationError(f"Name papers to {command}, or pass --all")
    missing = [str(p) for p in papers if not (p / "manifest.json").is_file()]
    if missing:
        raise ConfigurationError(f"Not a published paper: {', '.join(missing)}")
    return [paper.resolve() for paper in papers]


@dataclass(frozen=True)
class _Described:
    """Hold a paper whose new descriptions await publication.

    Attributes
    ----------
    run : Path
        Run directory with the rebuilt paper and its descriptions.
    summary : str
        Counts for the result line.
    """

    run: Path
    summary: str


def _describe_run(
    paper: Path,
    config: Configuration,
    describer: Describer | None,
    *,
    force: bool,
) -> _Described | ItemResult:
    """Describe one published paper's figures in a run directory.

    Parameters
    ----------
    paper : Path
        Published paper directory.
    config : Configuration
        Resolved configuration.
    describer : Describer or None
        Model runtime, or None for a dry run that only counts figures.
    force : bool
        Describe again figures that already have this model's description.

    Returns
    -------
    _Described or ItemResult
        The run to publish, or the final ``skipped`` or ``failed`` item; a
        failure keeps the run directory.

    Notes
    -----
    Nothing here writes to the library outside the run directory, so
    several papers can be described at once.
    """
    now = dt.datetime.now(dt.UTC)
    stamp = f"{now:%Y%m%dT%H%M%SZ}-describe-{secrets.token_hex(3)}"
    run = config.library / INTERNAL_DIRECTORY / RUNS_DIRECTORY / stamp
    prompt = config.describe.prompt
    try:
        run.mkdir(parents=True)
        stage_from_paper(paper, run)
        if describer is None:
            pending, skipped, unrenderable = pending_figures(
                run, config.describe_model(), prompt_version=prompt, force=force
            )
            shutil.rmtree(run)
            return ItemResult(
                path=str(paper),
                status="skipped",
                directory=_relative(paper, config),
                message=f"dry run: {len(pending)} to describe, {skipped} already "
                f"described, {unrenderable} without an image",
            )
        report = describe_staging(run, describer, prompt_version=prompt, force=force)
    except (ValueError, OSError, DescriberError) as exc:
        return ItemResult(
            path=str(paper),
            status="failed",
            stage="describe",
            message=f"{type(exc).__name__}: {exc}",
            run=str(run),
        )
    for error in report.errors:
        logger.warning("%s: %s", paper.name, error)
    summary = (
        f"{report.described} described, {report.skipped} already described, "
        f"{report.failed} failed, {report.unrenderable} without an image"
    )
    if report.usd:
        summary += f", ${report.usd:.4f}"
    if report.recorded == 0:
        shutil.rmtree(run)
        return ItemResult(
            path=str(paper),
            status="failed" if report.failed else "skipped",
            directory=_relative(paper, config),
            stage="describe" if report.failed else None,
            message=summary,
        )
    return _Described(run, summary)


def _publish_described(
    paper: Path, config: Configuration, described: _Described | ItemResult
) -> ItemResult:
    """Publish a paper's new descriptions in place of the paper.

    Parameters
    ----------
    paper : Path
        Published paper directory.
    config : Configuration
        Resolved configuration.
    described : _Described or ItemResult
        Result of :func:`_describe_run`; an item is returned unchanged.

    Returns
    -------
    ItemResult
        ``republished``, or ``failed`` with the run directory kept and the
        published paper unchanged.
    """
    if isinstance(described, ItemResult):
        return described
    try:
        published = publish(
            described.run, config.library, replace=_relative(paper, config)
        )
    except (ValueError, OSError) as exc:
        return ItemResult(
            path=str(paper),
            status="failed",
            stage="describe",
            message=f"{type(exc).__name__}: {exc}",
            run=str(described.run),
        )
    shutil.rmtree(described.run)
    document = Document.from_json((published.directory / DOCUMENT_FILENAME).read_text())
    metadata = json.loads((published.directory / "metadata.json").read_text())
    return ItemResult(
        path=str(paper),
        status="republished",
        sha256=document.source_sha256,
        directory=published.name,
        identity=str(metadata["bibliographic_status"]),
        processing=processing_status(document),
        findings=len(document.findings),
        message=described.summary,
    )


def _command_describe(args: argparse.Namespace, config: Configuration) -> int:
    """Run ``describe`` for named papers or the whole library.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    config : Configuration
        Resolved configuration.

    Returns
    -------
    int
        Exit status.
    """
    papers = _papers(args, config, "describe")
    describer = None if args.dry_run else config.describer(os.environ)
    at_once = 1 if config.describe.backend == "mlx" else config.describe.papers
    items: list[ItemResult] = []
    # Papers are described in parallel but published one at a time, in order.
    with ThreadPoolExecutor(max_workers=at_once) as pool:
        runs = [
            pool.submit(_describe_run, paper, config, describer, force=args.force)
            for paper in papers
        ]
        try:
            for number, (paper, run) in enumerate(zip(papers, runs, strict=True), 1):
                logger.info("[%d/%d] %s", number, len(papers), paper.name)
                items.append(_publish_described(paper, config, run.result()))
        except KeyboardInterrupt:
            # Papers not yet started are dropped; running requests finish.
            pool.shutdown(cancel_futures=True)
            raise
    _refresh(config, items)
    _emit("describe", config, items, as_json=args.json)
    return exit_code(items, strict=args.strict)


def _refresh(config: Configuration, items: Sequence[ItemResult]) -> None:
    """Rebuild the search index and derived catalogs after a publication.

    Parameters
    ----------
    config : Configuration
        Resolved configuration.
    items : Sequence of ItemResult
        Items of the command; nothing is rebuilt when none was published.
    """
    if any(item.status in {"published", "republished"} for item in items):
        refresh_index(config.library)
        logger.info("Index and catalogs refreshed")


def _command_index(args: argparse.Namespace, config: Configuration) -> int:
    """Run ``index rebuild``.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    config : Configuration
        Resolved configuration.

    Returns
    -------
    int
        Exit status.

    Raises
    ------
    ConfigurationError
        The library does not exist.
    """
    del args
    if not (config.library / "corpus.json").is_file():
        raise ConfigurationError(f"Not a library: {config.library}")
    for message in recover_library(config.library):
        logger.warning("%s", message)
    corpus = ensure_library(config.library)
    rows = rebuild_catalog(config.library, corpus.get("library_id"))
    refresh_index(config.library)
    sys.stdout.write(
        f"indexed\t{len(rows)} papers\nlibrary\t{config.library} "
        f"({config.origins['library']})\n"
    )
    return EXIT_OK


def _queries(args: argparse.Namespace) -> list[Query]:
    """Build lookup queries from the command-line options.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.

    Returns
    -------
    list of Query
        One query per file and BibTeX entry, and one for the DOI and title
        options together.

    Raises
    ------
    ConfigurationError
        No query was given, or a file does not exist.
    """
    queries: list[Query] = []
    for path in args.file:
        if not path.is_file():
            raise ConfigurationError(f"Not found: {path}")
        queries.append(query_from_file(path))
    if args.bibtex is not None:
        if not args.bibtex.is_file():
            raise ConfigurationError(f"Not found: {args.bibtex}")
        queries.extend(queries_from_bibtex(args.bibtex.read_text(encoding="utf-8")))
    if args.doi or args.title or args.arxiv:
        doi = normalize_doi(args.doi) if args.doi else None
        if args.doi and doi is None:
            raise ConfigurationError(f"Not a DOI: {args.doi}")
        label = " ".join(str(v) for v in (args.doi, args.arxiv, args.title) if v)
        queries.append(
            Query(
                label=label,
                doi=doi,
                arxiv=args.arxiv,
                title=args.title,
                author=args.author,
                year=args.year,
            )
        )
    if not queries:
        raise ConfigurationError("Give --doi, --arxiv, --title, --file or --bibtex")
    return queries


def _libraries(args: argparse.Namespace, config: Configuration) -> list[Path]:
    """Resolve the libraries a query command reads.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    config : Configuration
        Resolved configuration.

    Returns
    -------
    list of Path
        Named libraries, or the configured library.

    Raises
    ------
    ConfigurationError
        A library has no ``corpus.json``.
    """
    libraries = [path.resolve() for path in args.libraries] or [config.library]
    missing = [str(p) for p in libraries if not (p / "corpus.json").is_file()]
    if missing:
        raise ConfigurationError(f"Not a library: {', '.join(missing)}")
    return libraries


def _command_lookup(args: argparse.Namespace, config: Configuration) -> int:
    """Run ``lookup``.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    config : Configuration
        Resolved configuration.

    Returns
    -------
    int
        ``0``; the answer is in the output.
    """
    libraries = _libraries(args, config)
    matches = [m for query in _queries(args) for m in lookup(libraries, query)]
    if args.json:
        sys.stdout.write(
            dump(
                {
                    "schema": "paperextract.lookup-result",
                    "schema_version": 1,
                    "matches": [match.to_dict() for match in matches],
                }
            )
        )
    else:
        sys.stdout.writelines(
            f"{m.status}\t{Path(m.library).name}/{m.directory or '-'}"
            f"\t{m.reason}\t{m.query}\n"
            for m in matches
        )
    return EXIT_OK


def _command_search(args: argparse.Namespace, config: Configuration) -> int:
    """Run ``search``.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    config : Configuration
        Resolved configuration.

    Returns
    -------
    int
        ``0``; the hits are in the output.

    Raises
    ------
    ConfigurationError
        The limit is not positive.
    """
    if args.limit < 1:
        raise ConfigurationError("--limit must be positive")
    hits = search(_libraries(args, config), args.query, limit=args.limit)
    if args.json:
        sys.stdout.write(
            dump(
                {
                    "schema": "paperextract.search-result",
                    "schema_version": 1,
                    "hits": [hit.to_dict() for hit in hits],
                }
            )
        )
    else:
        for hit in hits:
            sys.stdout.write(f"{hit.directory}\t{hit.year or ''}\t{hit.title or ''}\n")
            where = "machine-generated description: " if hit.in_description else ""
            sys.stdout.write(f"\t{where}{' '.join(hit.snippet.split())}\n")
    return EXIT_OK


_HANDLERS: Mapping[str, Callable[[argparse.Namespace, Configuration], int]] = {
    "extract": _command_extract,
    "batch": _command_batch,
    "dedup": _command_dedup,
    "publish": _command_publish,
    "reprocess": _command_reprocess,
    "organize": _command_organize,
    "migrate": _command_migrate,
    "models": _command_models,
    "compare": _command_compare,
    "describe": _command_describe,
    "index": _command_index,
    "lookup": _command_lookup,
    "search": _command_search,
}


def _terminate(signum: int, frame: FrameType | None) -> None:
    """Turn a termination signal into cancellation.

    Parameters
    ----------
    signum : int
        Signal number.
    frame : FrameType or None
        Interrupted frame.

    Raises
    ------
    KeyboardInterrupt
        Always, so the worker group is stopped on the way out.
    """
    del frame
    raise KeyboardInterrupt(f"signal {signum}")


def main(
    argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None
) -> int:
    """Run the command line.

    Parameters
    ----------
    argv : Sequence of str or None
        Arguments without the program name; defaults to ``sys.argv[1:]``.
    environ : Mapping of str to str or None
        Environment for locating the user configuration; defaults to
        ``os.environ``.

    Returns
    -------
    int
        Exit status from :data:`EXIT_CODES`.
    """
    parser = build_parser()
    try:
        given = sys.argv[1:] if argv is None else argv
        args = parser.parse_args(_normalize_argv(given))
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    package = logging.getLogger("paperextract")
    level = logging.DEBUG if args.verbose else logging.INFO
    package.setLevel(logging.WARNING if args.quiet else level)
    package.addHandler(handler)
    previous = signal.signal(signal.SIGTERM, _terminate)
    try:
        config = _configure(args, os.environ if environ is None else environ)
        return _HANDLERS[args.command](args, config)
    except ConfigurationError as exc:
        logger.error("%s", exc)
        return EXIT_USAGE
    except UnsupportedFormatError as exc:
        logger.error("%s", exc)
        return EXIT_FAILURE
    except KeyboardInterrupt:
        logger.error("Cancelled")
        return EXIT_CANCELLED
    finally:
        signal.signal(signal.SIGTERM, previous)
        package.removeHandler(handler)
        package.setLevel(logging.NOTSET)


def main_entry() -> None:
    """Run :func:`main` as the console script and exit with its status.

    Raises
    ------
    SystemExit
        Always, carrying the exit status.
    """
    sys.exit(main())
