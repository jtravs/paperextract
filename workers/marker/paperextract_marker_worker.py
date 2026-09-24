"""Run one Marker 2 extraction request inside the pinned worker environment.

This script implements worker protocol version 1 for the ``marker`` backend,
documented in the manual (``docs/worker.md``) and defined for the coordinator
in ``paperextract.protocol``. It imports nothing from the core package.

Marker 2 sends layout and recognition to a Surya vision-language model served
by llama.cpp. The worker starts that server itself from the local binary and
GGUF files named in the request, bound to 127.0.0.1 on a free port, and stops
it when the request ends; Marker's automatic server start, remote LLM services
and model downloads are all switched off. The JSON renderer's output is saved
verbatim as ``native/marker.json``; the block images it embeds are also
written as files below ``native/images/``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import platform
import resource
import socket
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

import psutil
import pypdfium2 as pdfium

PROTOCOL_VERSION = 1
REQUEST_SCHEMA = "paperextract.worker-request"
RESULT_SCHEMA = "paperextract.worker-result"
RESULT_FILENAME = "result.json"
NATIVE_DIRECTORY = "native"
NATIVE_FILENAME = "marker.json"
BACKEND = "marker"
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
PROFILE_FIELDS = ("mode", "server_binary", "cpu_threads")
GGUF_DIRECTORY = "surya-ocr-2-gguf"
SERVER_STARTUP_SECONDS = 300.0
_RECORDED_VARIABLE_PREFIXES = ("HF_", "OMP_", "SURYA_", "MODEL_", "FAST_", "TORCH_")

logger = logging.getLogger("paperextract.marker_worker")


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
    mode: str
    server_binary: Path
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
        lacks fields, or asks for an unknown mode.
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
    if profile["mode"] not in ("balanced", "fast"):
        raise RequestError(f"Unknown Marker mode {profile['mode']!r}")
    pages = document["pages"]
    return Request(
        request_id=str(document["request_id"]),
        source_path=Path(document["source_path"]),
        source_sha256=str(document["source_sha256"]),
        expected_page_count=int(document["expected_page_count"]),
        pages=None if pages is None else tuple(int(page) for page in pages),
        mode=str(profile["mode"]),
        server_binary=Path(profile["server_binary"]),
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
        Number of pages and the non-empty document information entries.
    """
    with pdfium.PdfDocument(path) as document:
        return len(document), dict(document.get_metadata_dict(skip_empty=True))


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
    """Report the installed Marker distribution version.

    Returns
    -------
    str
        Version string, or ``unknown``.
    """
    return _version("marker-pdf") or "unknown"


def model_files(model_dir: Path) -> list[dict[str, object]]:
    """List model files by relative path and size.

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
    """Describe the interpreter, packages and model evidence.

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
            for name in ("marker-pdf", "surya-ocr", "pdftext", "pypdfium2", "torch")
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
        Byte-normalized maximum resident sizes and load averages.
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
            "children, which include the llama.cpp server; not accelerator memory."
        ),
    }


def _free_port() -> int:
    """Pick a free loopback port.

    Returns
    -------
    int
        Port number.
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_healthy(url: str, server: subprocess.Popen[bytes]) -> None:
    """Wait until the llama.cpp server answers its health check.

    Parameters
    ----------
    url : str
        Server base address.
    server : subprocess.Popen
        Server process.

    Raises
    ------
    RuntimeError
        The server exited or did not start in time.
    """
    deadline = time.monotonic() + SERVER_STARTUP_SECONDS
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError(f"llama-server exited with {server.returncode}")
        try:
            with urllib.request.urlopen(url + "/health", timeout=1) as response:
                if response.status == 200:  # noqa: PLR2004
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.25)
    raise RuntimeError(f"llama-server did not start within {SERVER_STARTUP_SECONDS} s")


def start_server(
    request: Request, output_dir: Path
) -> tuple[subprocess.Popen[bytes], str]:
    """Start the local Surya llama.cpp server.

    Parameters
    ----------
    request : Request
        Validated request.
    output_dir : Path
        Staging directory, which receives ``llama-server.log``.

    Returns
    -------
    tuple
        Server process and base address.
    """
    gguf = request.model_dir / GGUF_DIRECTORY
    port = _free_port()
    command = [
        str(request.server_binary),
        "-m",
        str(gguf / "surya-2.gguf"),
        "--mmproj",
        str(gguf / "surya-2-mmproj.gguf"),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--parallel",
        "1",
        "--ctx-size",
        "16384",
        "--threads",
        str(request.cpu_threads),
        "--threads-batch",
        str(request.cpu_threads),
        "-ngl",
        "99",
        "--jinja",
        "--alias",
        "datalab-to/surya-ocr-2",
    ]
    log = (output_dir / "llama-server.log").open("wb")
    server = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    _wait_healthy(url, server)
    return server, url


def blocks(node: dict[str, object]) -> Iterator[dict[str, object]]:
    """Walk a JSON block tree depth first.

    Parameters
    ----------
    node : dict of str to object
        Block.

    Yields
    ------
    dict of str to object
        The block and its descendants.
    """
    yield node
    for child in node.get("children") or []:  # type: ignore[union-attr]
        yield from blocks(child)  # type: ignore[arg-type]


def save_images(document: dict[str, object], output_dir: Path) -> dict[str, str]:
    """Write the images the JSON renderer embedded in blocks.

    Parameters
    ----------
    document : dict of str to object
        Rendered JSON document.
    output_dir : Path
        Staging directory.

    Returns
    -------
    dict of str to str
        Image key to its path relative to ``native/``.
    """
    saved: dict[str, str] = {}
    directory = output_dir / NATIVE_DIRECTORY / "images"
    for block in blocks(document):
        for key, value in (block.get("images") or {}).items():  # type: ignore[union-attr]
            if not isinstance(value, str) or key in saved:
                continue
            directory.mkdir(parents=True, exist_ok=True)
            data = base64.b64decode(value)
            suffix = ".png" if data.startswith(b"\x89PNG") else ".jpg"
            name = "images/" + key.strip("/").replace("/", "_") + suffix
            (output_dir / NATIVE_DIRECTORY / name).write_bytes(data)
            saved[key] = name
    return saved


def page_number(block_id: str) -> int | None:
    """Read the one-based page of a block identifier such as ``/page/3/Text/1``.

    Parameters
    ----------
    block_id : str
        Marker block identifier.

    Returns
    -------
    int or None
        Page number, or None.
    """
    parts = block_id.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "page" and parts[1].isdigit():  # noqa: PLR2004
        return int(parts[1]) + 1
    return None


def coverage_for(
    document: dict[str, object], requested: tuple[int, ...]
) -> tuple[dict[str, list[int]], list[dict[str, str]]]:
    """Compare the pages Marker returned with the requested pages.

    Parameters
    ----------
    document : dict of str to object
        Rendered JSON document.
    requested : tuple of int
        One-based pages the request covers.

    Returns
    -------
    tuple
        Coverage record and diagnostics for empty or missing pages.
    """
    returned: set[int] = set()
    empty: set[int] = set()
    for page in document.get("children") or []:  # type: ignore[union-attr]
        number = page_number(str(page.get("id", "")))  # type: ignore[union-attr]
        if number is None:
            continue
        returned.add(number)
        if not page.get("children"):  # type: ignore[union-attr]
            empty.add(number)
    diagnostics: list[dict[str, str]] = []
    if empty:
        diagnostics.append(
            {
                "code": "PAGE_EMPTY",
                "severity": "warning",
                "message": f"Pages {sorted(empty)} returned no blocks.",
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
        "empty": sorted(empty),
    }, diagnostics


def configure_environment(request: Request) -> None:
    """Point Marker and Surya at local models and switch off downloads.

    Parameters
    ----------
    request : Request
        Validated request.
    """
    models = request.model_dir
    os.environ.update(
        {
            "MODEL_CACHE_DIR": str(models / "cache"),
            "FAST_LAYOUT_MODEL_CHECKPOINT": str(models / "surya_layout2"),
            "FAST_ORDER_MODEL_CHECKPOINT": str(models / "surya_layout2" / "order"),
            "SURYA_INFERENCE_AUTOSTART": "false",
            "SURYA_INFERENCE_PARALLEL": "1",
            "SURYA_INFERENCE_KEEP_ALIVE": "false",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
        }
    )


def extract(
    request: Request, output_dir: Path, startup_seconds: float
) -> dict[str, object]:
    """Verify the source, run Marker and assemble a completed result.

    Parameters
    ----------
    request : Request
        Validated request.
    output_dir : Path
        Staging directory.
    startup_seconds : float
        Seconds from process creation to the start of request handling.

    Returns
    -------
    dict of str to object
        Result document with ``completed`` status.
    """
    environment = collect_environment(request.model_dir)
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
    configure_environment(request)
    timing: dict[str, float] = {"startup_seconds": startup_seconds}
    started = time.perf_counter()
    server, url = start_server(request, output_dir)
    try:
        os.environ["SURYA_INFERENCE_URL"] = url
        timing["server_seconds"] = time.perf_counter() - started
        # Imported after the environment is set: Marker and Surya read it once.
        import torch  # noqa: PLC0415
        from marker.converters.pdf import PdfConverter  # noqa: PLC0415
        from marker.models import create_model_dict, shutdown_models  # noqa: PLC0415

        torch.set_num_threads(request.cpu_threads)
        started = time.perf_counter()
        models = create_model_dict(inference_backend="llamacpp")
        timing["load_seconds"] = time.perf_counter() - started
        config = {
            "mode": request.mode,
            "use_llm": False,
            "page_range": [page - 1 for page in requested],
        }
        try:
            started = time.perf_counter()
            converter = PdfConverter(
                artifact_dict=models,
                renderer="marker.renderers.json.JSONRenderer",
                config=config,
            )
            rendered = converter(str(request.source_path))
            timing["convert_seconds"] = time.perf_counter() - started
        finally:
            shutdown_models(models)
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
    document = json.loads(rendered.model_dump_json())
    images = save_images(document, output_dir)
    native = {
        "schema": "paperextract.marker-native",
        "schema_version": 1,
        "backend_version": backend_version(),
        "pdf_information": information,
        "images": images,
        "document": document,
    }
    (output_dir / NATIVE_DIRECTORY).mkdir(exist_ok=True)
    (output_dir / NATIVE_DIRECTORY / NATIVE_FILENAME).write_text(
        json.dumps(native, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    coverage, diagnostics = coverage_for(document, requested)
    configuration = {
        "converter": config,
        "renderer": "marker.renderers.json.JSONRenderer",
        "inference": {"backend": "llamacpp", "server": str(request.server_binary)},
        "native_schema": {"schema": "paperextract.marker-native", "schema_version": 1},
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
        Staging directory.
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
        logger.error("Usage: paperextract_marker_worker.py REQUEST_JSON")
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
        # The process boundary records every failure in the result document.
        logger.exception("Extraction failed for request %s", request.request_id)
        document = failure_document(request, output_dir, exc, startup_seconds)
        status = 1
    write_result(output_dir, document)
    logger.info("Wrote %s with status %s", RESULT_FILENAME, document["status"])
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
