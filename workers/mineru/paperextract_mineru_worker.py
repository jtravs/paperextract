"""Run one MinerU 4 extraction request inside the pinned worker environment.

This script implements worker protocol version 1, documented in the manual
(``docs/worker.md``) and defined for the coordinator in
``paperextract.protocol``. The coordinator starts it with the request file name
as the only argument and the staging directory as the working directory. It
deliberately imports nothing from the core package, so the worker environment
stays independent of the coordinator's installation.

The multiprocessing entry-point guard at the bottom is required on macOS:
MinerU starts helper processes that re-import the main module.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import resource
import sys
import time
import traceback
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

import psutil
import pypdfium2 as pdfium
import torch
from mineru.config import VlmConfig, config
from mineru.parser import MinerUParser
from mineru.parser.writer import FileBasedDataWriter

PROTOCOL_VERSION = 1
REQUEST_SCHEMA = "paperextract.worker-request"
RESULT_SCHEMA = "paperextract.worker-result"
RESULT_FILENAME = "result.json"
NATIVE_DIRECTORY = "native"
BACKEND = "mineru"
REQUEST_FIELDS = (
    "schema",
    "protocol_version",
    "request_id",
    "backend",
    "source_path",
    "source_sha256",
    "expected_page_count",
    "pages",
    "profile",
    "model_dir",
    "output_dir",
)
PROFILE_FIELDS = (
    "tier",
    "parse_mode",
    "image_analysis",
    "vlm_engine",
    "vlm_max_concurrency",
    "cpu_threads",
)
# Engines that run inside this process; a remote server URL is never set.
LOCAL_ENGINES = ("llama-cpp", "vllm")
SMALL_BACKENDS = ("onnx", "torch")
_SENSITIVE_KEY_PARTS = ("api_key", "token", "secret", "password")
_RECORDED_VARIABLE_PREFIXES = (
    "MINERU_",
    "HF_",
    "OMP_",
    "TRANSFORMERS_",
    "VLLM_",
    "CUDA_",
    "DO_NOT_TRACK",
)

logger = logging.getLogger("paperextract.mineru_worker")


class RequestError(ValueError):
    """Reject a request this worker cannot honor safely."""


@dataclass(frozen=True)
class Request:
    """Hold the validated request fields this worker acts on."""

    request_id: str
    source_path: Path
    source_sha256: str
    expected_page_count: int
    pages: tuple[int, ...] | None
    tier: str
    parse_mode: str
    image_analysis: bool
    vlm_engine: str
    vlm_max_concurrency: int
    cpu_threads: int
    model_dir: Path
    output_dir: Path


def load_request(path: Path) -> Request:
    """Read and check the request document written by the coordinator.

    Parameters
    ----------
    path : Path
        Request file inside the staging directory.

    Returns
    -------
    Request
        Validated request.

    Raises
    ------
    RequestError
        The document has another schema or version, names another backend,
        lacks fields, or asks for a remote vision-language engine.
    """
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise RequestError("Request must be a JSON object")
    missing = [field for field in REQUEST_FIELDS if field not in document]
    if missing:
        raise RequestError(f"Request lacks fields: {missing}")
    if document["schema"] != REQUEST_SCHEMA:
        raise RequestError(f"Unexpected request schema {document['schema']!r}")
    if document["protocol_version"] != PROTOCOL_VERSION:
        raise RequestError(
            f"Unsupported protocol version {document['protocol_version']!r}"
        )
    if document["backend"] != BACKEND:
        raise RequestError(f"This worker serves {BACKEND}, not {document['backend']!r}")
    profile = document["profile"]
    if not isinstance(profile, dict) or any(f not in profile for f in PROFILE_FIELDS):
        raise RequestError("Request profile lacks fields")
    if profile["vlm_engine"] not in LOCAL_ENGINES:
        raise RequestError("Only the local llama.cpp and vLLM engines are permitted")
    if profile.get("small_backend", "onnx") not in SMALL_BACKENDS:
        raise RequestError(f"Unknown small-model backend {profile['small_backend']!r}")
    pages = document["pages"]
    return Request(
        request_id=str(document["request_id"]),
        source_path=Path(document["source_path"]),
        source_sha256=str(document["source_sha256"]),
        expected_page_count=int(document["expected_page_count"]),
        pages=None if pages is None else tuple(int(page) for page in pages),
        tier=str(profile["tier"]),
        parse_mode=str(profile["parse_mode"]),
        image_analysis=bool(profile["image_analysis"]),
        vlm_engine=str(profile["vlm_engine"]),
        vlm_max_concurrency=int(profile["vlm_max_concurrency"]),
        cpu_threads=int(profile["cpu_threads"]),
        model_dir=Path(document["model_dir"]),
        output_dir=Path(document["output_dir"]),
    )


def verify_source(path: Path, expected_sha256: str) -> int:
    """Hash the preserved source and refuse a file that does not match.

    Parameters
    ----------
    path : Path
        Preserved PDF copy.
    expected_sha256 : str
        Digest recorded in the request.

    Returns
    -------
    int
        Size of the verified file in bytes.
    """
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != expected_sha256:
        raise RequestError(f"Source digest {digest} differs from the request")
    return path.stat().st_size


def count_pages(path: Path) -> int:
    """Count pages with PDFium, independently of MinerU.

    Parameters
    ----------
    path : Path
        Readable PDF.

    Returns
    -------
    int
        Number of pages.
    """
    with pdfium.PdfDocument(path) as document:
        return len(document)


def page_range_string(pages: tuple[int, ...] | None) -> str:
    """Convert a one-based page tuple to MinerU's inclusive range grammar.

    Parameters
    ----------
    pages : tuple of int or None
        Increasing one-based pages, or None for the whole document.

    Returns
    -------
    str
        For example ``"1-3,5"``; an empty string selects every page.
    """
    if pages is None:
        return ""
    runs: list[tuple[int, int]] = []
    for page in pages:
        if runs and page == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], page)
        else:
            runs.append((page, page))
    return ",".join(f"{a}" if a == b else f"{a}-{b}" for a, b in runs)


def redact(value: object) -> object:
    """Replace credential-like values in a configuration dump.

    Parameters
    ----------
    value : object
        JSON-compatible configuration value.

    Returns
    -------
    object
        Copy with non-empty sensitive strings replaced by a marker.
    """
    if isinstance(value, dict):
        return {
            key: "<redacted>"
            if any(part in key.lower() for part in _SENSITIVE_KEY_PARTS) and item
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def hash_tree(output_dir: Path, subtree: Path) -> list[dict[str, object]]:
    """Record every regular file beneath a staging subtree with its digest.

    Parameters
    ----------
    output_dir : Path
        Staging directory; recorded paths are relative to it.
    subtree : Path
        Directory to walk; it may not exist yet.

    Returns
    -------
    list of dict
        Sorted ``path``/``sha256``/``size_bytes`` records.
    """
    records: list[dict[str, object]] = []
    if not subtree.is_dir():
        return records
    for path in sorted(subtree.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"Refusing to record a symlink in staging: {path}")
        if path.is_file():
            with path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            records.append(
                {
                    "path": path.relative_to(output_dir).as_posix(),
                    "sha256": digest,
                    "size_bytes": path.stat().st_size,
                }
            )
    return records


def _version(distribution: str) -> str | None:
    """Look up an installed distribution version without failing the run.

    Parameters
    ----------
    distribution : str
        Distribution name.

    Returns
    -------
    str or None
        Version string, or None when the distribution is absent.
    """
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


def backend_version() -> str:
    """Report the installed MinerU distribution version.

    Returns
    -------
    str
        Version string, or ``unknown`` if the distribution metadata is absent.
    """
    return _version("mineru") or "unknown"


def model_files(model_dir: Path) -> list[dict[str, object]]:
    """List model files by relative path and size, skipping cache directories.

    Parameters
    ----------
    model_dir : Path
        Local model directory named in the request.

    Returns
    -------
    list of dict
        Sorted ``path``/``size_bytes`` records; hashes are pinned separately.
    """
    if not model_dir.is_dir():
        return []
    return [
        {
            "path": path.relative_to(model_dir).as_posix(),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(model_dir.rglob("*"))
        if path.is_file() and not any(part.startswith(".") for part in path.parts)
    ]


def collect_environment(model_dir: Path) -> dict[str, object]:
    """Describe the interpreter, packages, devices and model evidence.

    Parameters
    ----------
    model_dir : Path
        Local model directory named in the request.

    Returns
    -------
    dict of str to object
        JSON-compatible environment record.
    """
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "packages": {
            "mineru": backend_version(),
            "docvortex": _version("docvortex"),
            "pypdfium2": _version("pypdfium2"),
            "torch": torch.__version__,
            "onnxruntime": _version("onnxruntime"),
            "vllm": _version("vllm"),
            "psutil": psutil.__version__,
        },
        "mps_available": bool(torch.backends.mps.is_available()),
        "cuda": {
            "available": bool(torch.cuda.is_available()),
            "torch_cuda": torch.version.cuda,
            "devices": [
                torch.cuda.get_device_name(index)
                for index in range(torch.cuda.device_count())
            ],
            "visible": os.environ.get("CUDA_VISIBLE_DEVICES"),
        },
        "variables": {
            name: value
            for name, value in sorted(os.environ.items())
            if name.startswith(_RECORDED_VARIABLE_PREFIXES)
        },
        "model_dir": str(model_dir),
        "model_files": model_files(model_dir),
    }


def resource_snapshot() -> dict[str, object]:
    """Read process memory maxima and system load.

    Returns
    -------
    dict of str to object
        Byte-normalized maximum resident sizes and load averages, with a note
        describing what these numbers do not measure.
    """
    scale = 1 if sys.platform == "darwin" else 1024
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {
        "max_rss_self_bytes": own.ru_maxrss * scale,
        "max_rss_children_bytes": children.ru_maxrss * scale,
        "load_average": list(os.getloadavg()),
        "note": (
            "Maximum resident set sizes of this process and its waited-for "
            "children; not unified-memory pressure or peak accelerator memory."
        ),
    }


def configure_threads(cpu_threads: int) -> list[dict[str, str]]:
    """Set explicit CPU thread counts for the inference libraries.

    Parameters
    ----------
    cpu_threads : int
        Intra-operation threads.

    Returns
    -------
    list of dict
        Diagnostics for settings that could not be applied.
    """
    diagnostics: list[dict[str, str]] = []
    torch.set_num_threads(cpu_threads)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError as exc:
        # Torch allows this only before its inter-op pool starts; record it.
        diagnostics.append(
            {
                "code": "THREADS_NOT_APPLIED",
                "severity": "info",
                "message": f"Inter-operation thread count left unchanged: {exc}",
            }
        )
    return diagnostics


def coverage_for(
    pages: Sequence[object], requested: tuple[int, ...]
) -> tuple[dict[str, list[int]], list[dict[str, str]]]:
    """Compare the pages MinerU returned with the requested pages.

    Parameters
    ----------
    pages : Sequence of object
        ``PageInfo`` objects from the parse result, zero-based ``page_idx``.
    requested : tuple of int
        One-based pages the request covers.

    Returns
    -------
    tuple
        Coverage record and diagnostics for empty or missing pages.
    """
    returned = sorted(int(getattr(page, "page_idx")) + 1 for page in pages)  # noqa: B009
    empty = sorted(
        int(getattr(page, "page_idx")) + 1  # noqa: B009
        for page in pages
        if not getattr(page, "blocks")  # noqa: B009
    )
    diagnostics: list[dict[str, str]] = []
    if empty:
        diagnostics.append(
            {
                "code": "PAGE_EMPTY",
                "severity": "warning",
                "message": (
                    f"Pages {empty} returned no blocks; an unreadable page and a "
                    "blank page are indistinguishable here."
                ),
            }
        )
    missing = sorted(set(requested) - set(returned))
    if missing:
        diagnostics.append(
            {
                "code": "PAGE_NOT_RETURNED",
                "severity": "warning",
                "message": f"Requested pages {missing} are absent from the output.",
            }
        )
    return {
        "requested": list(requested),
        "returned": returned,
        "empty": empty,
    }, diagnostics


def extract(
    request: Request, output_dir: Path, startup_seconds: float
) -> dict[str, object]:
    """Verify the source, run MinerU and assemble a completed result.

    Parameters
    ----------
    request : Request
        Validated request.
    output_dir : Path
        Staging directory; native output goes to its ``native`` subdirectory.
    startup_seconds : float
        Seconds from process creation to the start of request handling.

    Returns
    -------
    dict of str to object
        Result document with ``completed`` status.
    """
    environment = collect_environment(request.model_dir)
    diagnostics = configure_threads(request.cpu_threads)
    verify_source(request.source_path, request.source_sha256)
    page_count = count_pages(request.source_path)
    if page_count != request.expected_page_count:
        raise RequestError(
            f"PDFium counts {page_count} pages; the request expects "
            f"{request.expected_page_count}"
        )
    requested = request.pages or tuple(range(1, page_count + 1))
    if any(page > page_count for page in requested):
        raise RequestError("Requested pages exceed the page count")
    page_range = page_range_string(request.pages)

    parser = MinerUParser(
        tier=request.tier,  # type: ignore[arg-type]
        parse_mode=request.parse_mode,  # type: ignore[arg-type]
        image_analysis=request.image_analysis,
        vlm_config=VlmConfig(
            engine=request.vlm_engine,  # type: ignore[arg-type]
            server_url="",
            max_concurrency=request.vlm_max_concurrency,
        ),
    )
    timing: dict[str, float] = {"startup_seconds": startup_seconds}
    try:
        started = time.perf_counter()
        parsed = parser.parse(request.source_path, page_range=page_range)
        timing["parse_seconds"] = time.perf_counter() - started
        started = time.perf_counter()
        parsed.save(FileBasedDataWriter(str(output_dir / NATIVE_DIRECTORY)))
        timing["save_seconds"] = time.perf_counter() - started
    finally:
        parser.close()

    coverage, page_diagnostics = coverage_for(parsed.pages, requested)
    diagnostics.extend(page_diagnostics)
    if not parsed.middle_json.is_full_document:
        diagnostics.append(
            {
                "code": "PARTIAL_DOCUMENT",
                "severity": "info",
                "message": "MinerU marks this output as a partial document.",
            }
        )
    configuration = {
        "mineru": redact(config.model_dump(mode="json")),
        "parser": {
            "tier": parser.tier,
            "effort": parser.effort,
            "parse_mode": parser.parse_mode,
            "image_analysis": parser.image_analysis,
            "vlm": redact(parser.vlm_config.model_dump(mode="json")),
        },
        "page_range": page_range,
        "torch_threads": torch.get_num_threads(),
        "torch_interop_threads": torch.get_num_interop_threads(),
        "native_schema": {
            "schema": parsed.middle_json.schema_id,
            "schema_version": parsed.middle_json.schema_version,
        },
    }
    return {
        "schema": RESULT_SCHEMA,
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request.request_id,
        "backend": BACKEND,
        "backend_version": backend_version(),
        "status": "completed",
        "source_sha256": request.source_sha256,
        "page_count": page_count,
        "coverage": coverage,
        "files": hash_tree(output_dir, output_dir / NATIVE_DIRECTORY),
        "configuration": configuration,
        "environment": environment,
        "timing": timing,
        "resources": resource_snapshot(),
        "diagnostics": diagnostics,
        "failure": None,
    }


def failure_document(
    request: Request, output_dir: Path, exc: BaseException, startup_seconds: float
) -> dict[str, object]:
    """Assemble a failed result that still records partial evidence.

    Parameters
    ----------
    request : Request
        Validated request.
    output_dir : Path
        Staging directory whose partial native output is recorded.
    exc : BaseException
        Failure cause.
    startup_seconds : float
        Seconds from process creation to the start of request handling.

    Returns
    -------
    dict of str to object
        Result document with ``failed`` status.
    """
    return {
        "schema": RESULT_SCHEMA,
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request.request_id,
        "backend": BACKEND,
        "backend_version": backend_version(),
        "status": "failed",
        "source_sha256": request.source_sha256,
        "page_count": None,
        "coverage": None,
        "files": hash_tree(output_dir, output_dir / NATIVE_DIRECTORY),
        "configuration": {"mineru": redact(config.model_dump(mode="json"))},
        "environment": collect_environment(request.model_dir),
        "timing": {"startup_seconds": startup_seconds},
        "resources": resource_snapshot(),
        "diagnostics": [],
        "failure": {
            "kind": type(exc).__name__,
            "message": str(exc),
            "traceback": "".join(traceback.format_exception(exc)),
        },
    }


def write_result(output_dir: Path, document: dict[str, object]) -> None:
    """Write the result document atomically into the staging directory.

    Parameters
    ----------
    output_dir : Path
        Staging directory.
    document : dict of str to object
        JSON-compatible result.
    """
    text = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    temporary = output_dir / f".{RESULT_FILENAME}.tmp"
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(output_dir / RESULT_FILENAME)


def handle(request_path: Path, startup_seconds: float) -> int:
    """Run one request file and write its result next to it.

    Parameters
    ----------
    request_path : Path
        Request file inside its staging directory.
    startup_seconds : float
        Process start-up time to attribute to this request.

    Returns
    -------
    int
        Zero after a completed result, one after a failed result, two when no
        result could be written because the request itself was unusable.
    """
    output_dir = request_path.parent
    try:
        request = load_request(request_path)
    except (OSError, ValueError) as exc:
        logger.error("Unusable request %s: %s", request_path, exc)
        return 2
    try:
        document = extract(request, output_dir, startup_seconds)
        status = 0
    except Exception as exc:
        # The process boundary records every failure in the result document
        # instead of hiding it; the traceback is preserved for the coordinator.
        logger.exception("Extraction failed for request %s", request.request_id)
        document = failure_document(request, output_dir, exc, startup_seconds)
        status = 1
    write_result(output_dir, document)
    logger.info("Wrote %s with status %s", RESULT_FILENAME, document["status"])
    return status


def serve(startup_seconds: float) -> int:
    """Handle request paths read from standard input, one per line.

    Models and inference engines that MinerU caches in the process are kept
    between requests. After each request one JSON line naming it and its
    status is written to the original standard output; anything libraries
    print there goes to standard error instead, so it cannot corrupt the
    replies.

    Parameters
    ----------
    startup_seconds : float
        Process start-up time, attributed to the first request.

    Returns
    -------
    int
        Zero when standard input closes.
    """
    replies = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    for line in sys.stdin:
        path = line.strip()
        if not path:
            continue
        status = handle(Path(path).resolve(), startup_seconds)
        startup_seconds = 0.0
        replies.write(json.dumps({"request": path, "status": status}) + "\n")
        replies.flush()
    return 0


def main(argv: Sequence[str]) -> int:
    """Run the worker for one request file, or serve requests from stdin.

    Parameters
    ----------
    argv : Sequence of str
        Command-line arguments without the program name: a request file, or
        ``--serve``.

    Returns
    -------
    int
        For one request, zero after a completed result, one after a failed
        result, two when no result could be written because the request
        itself was unusable; zero when serving ends.
    """
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    startup_seconds = time.time() - psutil.Process().create_time()
    if list(argv) == ["--serve"]:
        return serve(startup_seconds)
    if len(argv) != 1:
        logger.error("Usage: paperextract_mineru_worker.py REQUEST_JSON | --serve")
        return 2
    return handle(Path(argv[0]).resolve(), startup_seconds)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
