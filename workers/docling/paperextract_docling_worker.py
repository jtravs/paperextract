"""Run one Docling extraction request inside the pinned worker environment.

This script implements worker protocol version 1 for the ``docling`` backend,
documented in the manual (``docs/worker.md``) and defined for the coordinator
in ``paperextract.protocol``. The coordinator starts it with the request file
name as the only argument and the staging directory as the working directory.
It imports nothing from the core package.

Docling converts one contiguous page range per call, so a selection such as
pages 2, 3 and 8 becomes two conversions with one reused converter. Each
conversion's ``DoclingDocument`` is kept verbatim inside the wrapper file
``native/docling.json`` together with the page range it covers and the files
of its picture and table crops. OCR is never enabled: Docling's OCR engines
download their own models on first use, which would be a hidden network
dependency.
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
from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
from docling.datamodel.vlm_engine_options import TransformersVlmEngineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

PROTOCOL_VERSION = 1
REQUEST_SCHEMA = "paperextract.worker-request"
RESULT_SCHEMA = "paperextract.worker-result"
RESULT_FILENAME = "result.json"
NATIVE_DIRECTORY = "native"
NATIVE_FILENAME = "docling.json"
NATIVE_SCHEMA = "paperextract.docling-native"
NATIVE_SCHEMA_VERSION = 1
BACKEND = "docling"
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
    "table_mode",
    "cell_matching",
    "formulas",
    "images",
    "device",
    "cpu_threads",
)
# Crops at twice the PDF resolution (144 dpi), Docling's usual export scale.
IMAGES_SCALE = 2.0
_RECORDED_VARIABLE_PREFIXES = ("HF_", "OMP_", "TRANSFORMERS_", "DOCLING_")

logger = logging.getLogger("paperextract.docling_worker")


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
    table_mode: str
    cell_matching: bool
    formulas: bool
    images: bool
    device: str
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
        The document has another schema or version, names another backend
        or lacks fields.
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
    pages = document["pages"]
    return Request(
        request_id=str(document["request_id"]),
        source_path=Path(document["source_path"]),
        source_sha256=str(document["source_sha256"]),
        expected_page_count=int(document["expected_page_count"]),
        pages=None if pages is None else tuple(int(page) for page in pages),
        table_mode=str(profile["table_mode"]),
        cell_matching=bool(profile["cell_matching"]),
        formulas=bool(profile["formulas"]),
        images=bool(profile["images"]),
        device=str(profile["device"]),
        cpu_threads=int(profile["cpu_threads"]),
        model_dir=Path(document["model_dir"]),
        output_dir=Path(document["output_dir"]),
    )


def verify_source(path: Path, expected_sha256: str) -> None:
    """Hash the preserved source and refuse a file that does not match.

    Parameters
    ----------
    path : Path
        Preserved PDF copy.
    expected_sha256 : str
        Digest recorded in the request.
    """
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != expected_sha256:
        raise RequestError(f"Source digest {digest} differs from the request")


def count_pages(path: Path) -> tuple[int, dict[str, str]]:
    """Count pages and read the information dictionary with PDFium.

    Parameters
    ----------
    path : Path
        Readable PDF.

    Returns
    -------
    tuple
        Number of pages, independently of Docling, and the non-empty
        document information entries, which Docling does not report.
    """
    with pdfium.PdfDocument(path) as document:
        return len(document), dict(document.get_metadata_dict(skip_empty=True))


def page_runs(pages: Sequence[int]) -> list[tuple[int, int]]:
    """Group increasing one-based pages into inclusive contiguous ranges.

    Parameters
    ----------
    pages : Sequence of int
        Increasing pages.

    Returns
    -------
    list of tuple of int
        ``(first, last)`` pairs, for example ``[(2, 3), (8, 8)]``.
    """
    runs: list[tuple[int, int]] = []
    for page in pages:
        if runs and page == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], page)
        else:
            runs.append((page, page))
    return runs


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
    """Report the installed Docling distribution version.

    Returns
    -------
    str
        Version string, or ``unknown`` if the distribution metadata is absent.
    """
    return _version("docling") or "unknown"


def model_files(model_dir: Path) -> list[dict[str, object]]:
    """List model files by relative path and size, skipping hidden entries.

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
            name: _version(name)
            for name in (
                "docling",
                "docling-core",
                "docling-ibm-models",
                "docling-parse",
                "pypdfium2",
                "torch",
                "transformers",
                "psutil",
            )
        },
        "mps_available": bool(torch.backends.mps.is_available()),
        "cuda_available": bool(torch.cuda.is_available()),
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
    return {
        "max_rss_self_bytes": own.ru_maxrss * scale,
        "load_average": list(os.getloadavg()),
        "note": (
            "Maximum resident set size of this process; not unified-memory "
            "pressure or peak accelerator memory."
        ),
    }


def build_options(request: Request) -> PdfPipelineOptions:
    """Translate the request profile into explicit Docling pipeline options.

    Parameters
    ----------
    request : Request
        Validated request.

    Returns
    -------
    PdfPipelineOptions
        Local models only, remote services and OCR off.
    """
    options = PdfPipelineOptions(
        artifacts_path=request.model_dir,
        do_ocr=False,
        do_table_structure=True,
        do_formula_enrichment=request.formulas,
        enable_remote_services=False,
        generate_page_images=False,
        generate_picture_images=request.images,
        generate_table_images=request.images,
        images_scale=IMAGES_SCALE,
        accelerator_options=AcceleratorOptions(
            device=AcceleratorDevice(request.device),
            num_threads=request.cpu_threads,
        ),
    )
    options.table_structure_options.mode = TableFormerMode(request.table_mode)
    options.table_structure_options.do_cell_matching = request.cell_matching
    if request.formulas:
        # The formula model failed on MPS in the P1 trial; it runs on the CPU.
        options.code_formula_options.engine_options = TransformersVlmEngineOptions(
            device=AcceleratorDevice.CPU
        )
    return options


def save_images(document: object, output_dir: Path, run_index: int) -> dict[str, str]:
    """Write the picture and table crops Docling generated for one run.

    Parameters
    ----------
    document : DoclingDocument
        Converted document.
    output_dir : Path
        Staging directory.
    run_index : int
        One-based index of the conversion, part of each file name.

    Returns
    -------
    dict of str to str
        Docling ``self_ref`` to the crop's path relative to ``native/``.
    """
    saved: dict[str, str] = {}
    directory = output_dir / NATIVE_DIRECTORY / "images"
    for kind in ("pictures", "tables"):
        for index, item in enumerate(getattr(document, kind)):
            image = getattr(item, "image", None)
            pil = None if image is None else image.pil_image
            if pil is None:
                continue
            directory.mkdir(parents=True, exist_ok=True)
            name = f"images/r{run_index:02d}-{kind[:-1]}-{index:03d}.png"
            pil.save(output_dir / NATIVE_DIRECTORY / name, format="PNG")
            saved[str(item.self_ref)] = name
    return saved


def coverage_for(
    returned: set[int], occupied: set[int], requested: tuple[int, ...]
) -> tuple[dict[str, list[int]], list[dict[str, str]]]:
    """Compare the pages Docling returned with the requested pages.

    Parameters
    ----------
    returned : set of int
        One-based pages present in the converted documents.
    occupied : set of int
        Returned pages with at least one item.
    requested : tuple of int
        One-based pages the request covers.

    Returns
    -------
    tuple
        Coverage record and diagnostics for empty or missing pages.
    """
    empty = sorted(returned - occupied)
    diagnostics: list[dict[str, str]] = []
    if empty:
        diagnostics.append(
            {
                "code": "PAGE_EMPTY",
                "severity": "warning",
                "message": (
                    f"Pages {empty} returned no items; an unreadable page and a "
                    "blank page are indistinguishable here."
                ),
            }
        )
    missing = sorted(set(requested) - returned)
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
        "returned": sorted(returned),
        "empty": empty,
    }, diagnostics


def occupied_pages(document: dict[str, object]) -> set[int]:
    """List the pages on which a serialized document has items.

    Parameters
    ----------
    document : dict of str to object
        ``DoclingDocument`` as a dictionary.

    Returns
    -------
    set of int
        One-based page numbers of every provenance record.
    """
    pages: set[int] = set()
    for kind in ("texts", "tables", "pictures"):
        for item in document.get(kind, []) or []:
            for prov in item.get("prov", []) or []:
                pages.add(int(prov["page_no"]))
    return pages


def extract(
    request: Request, output_dir: Path, startup_seconds: float
) -> dict[str, object]:
    """Verify the source, run Docling per page range and assemble the result.

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
    torch.set_num_threads(request.cpu_threads)
    verify_source(request.source_path, request.source_sha256)
    page_count, information = count_pages(request.source_path)
    if page_count != request.expected_page_count:
        raise RequestError(
            f"PDFium counts {page_count} pages; the request expects "
            f"{request.expected_page_count}"
        )
    requested = request.pages or tuple(range(1, page_count + 1))
    if any(page > page_count for page in requested):
        raise RequestError("Requested pages exceed the page count")
    options = build_options(request)
    timing: dict[str, float] = {"startup_seconds": startup_seconds}
    started = time.perf_counter()
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )
    converter.initialize_pipeline(InputFormat.PDF)
    timing["load_seconds"] = time.perf_counter() - started
    runs: list[dict[str, object]] = []
    diagnostics: list[dict[str, str]] = []
    returned: set[int] = set()
    occupied: set[int] = set()
    convert_seconds = 0.0
    for index, (first, last) in enumerate(page_runs(requested), 1):
        started = time.perf_counter()
        converted = converter.convert(
            request.source_path, page_range=(first, last), raises_on_error=False
        )
        convert_seconds += time.perf_counter() - started
        document = converted.document.export_to_dict()
        errors = [str(error) for error in converted.errors]
        if errors:
            diagnostics.append(
                {
                    "code": "BACKEND_ERRORS",
                    "severity": "warning",
                    "message": f"Pages {first}-{last}: {errors[:5]}",
                }
            )
        returned.update(int(page) for page in document.get("pages", {}))
        occupied.update(occupied_pages(document))
        runs.append(
            {
                "pages": [first, last],
                "status": str(converted.status.value),
                "errors": errors,
                "images": save_images(converted.document, output_dir, index)
                if request.images
                else {},
                "document": document,
            }
        )
    timing["convert_seconds"] = convert_seconds
    native = {
        "schema": NATIVE_SCHEMA,
        "schema_version": NATIVE_SCHEMA_VERSION,
        "backend_version": backend_version(),
        "pdf_information": information,
        "formulas": request.formulas,
        "runs": runs,
    }
    (output_dir / NATIVE_DIRECTORY).mkdir(exist_ok=True)
    (output_dir / NATIVE_DIRECTORY / NATIVE_FILENAME).write_text(
        json.dumps(native, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    coverage, page_diagnostics = coverage_for(returned, occupied, requested)
    diagnostics.extend(page_diagnostics)
    configuration = {
        "pipeline_options": options.model_dump(mode="json"),
        "page_runs": [list(run) for run in page_runs(requested)],
        "torch_threads": torch.get_num_threads(),
        "native_schema": {
            "schema": NATIVE_SCHEMA,
            "schema_version": NATIVE_SCHEMA_VERSION,
            "docling_schema": runs[0]["document"].get("schema_name") if runs else None,
            "docling_version": runs[0]["document"].get("version") if runs else None,
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
        "configuration": {},
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


def main(argv: Sequence[str]) -> int:
    """Run the worker for one request file.

    Parameters
    ----------
    argv : Sequence of str
        Command-line arguments without the program name.

    Returns
    -------
    int
        Zero after a completed result, one after a failed result, two when no
        result could be written because the request itself was unusable.
    """
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    startup_seconds = time.time() - psutil.Process().create_time()
    if len(argv) != 1:
        logger.error("Usage: paperextract_docling_worker.py REQUEST_JSON")
        return 2
    request_path = Path(argv[0]).resolve()
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


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
