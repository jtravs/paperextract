"""Versioned request and result contract for isolated extraction workers.

The coordinator writes a request file into a private staging directory, the
worker process writes native backend output plus a result file there, and the
coordinator admits the result only after the checks in
:mod:`paperextract.worker`. Backend Python objects never cross this boundary;
both sides exchange validated JSON documents whose fields are defined here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, cast

from paperextract.fields import (
    boolean,
    choice,
    dump,
    integer,
    items,
    mapping,
    record,
    string,
    text,
)

__all__ = [
    "NATIVE_DIRECTORY",
    "NATIVE_DOCLING_JSON",
    "NATIVE_MARKER_JSON",
    "NATIVE_MIDDLE_JSON",
    "PROTOCOL_VERSION",
    "REQUEST_FILENAME",
    "REQUEST_SCHEMA",
    "RESULT_FILENAME",
    "RESULT_SCHEMA",
    "Backend",
    "Diagnostic",
    "DoclingProfile",
    "ExtractionRequest",
    "ExtractionResult",
    "FailureInfo",
    "MarkerProfile",
    "MineruProfile",
    "PageCoverage",
    "Profile",
    "ResultFile",
    "SmallBackend",
    "VlmEngine",
    "native_document_path",
]

PROTOCOL_VERSION = 1
REQUEST_SCHEMA = "paperextract.worker-request"
RESULT_SCHEMA = "paperextract.worker-result"
REQUEST_FILENAME = "request.json"
RESULT_FILENAME = "result.json"
NATIVE_DIRECTORY = "native"
NATIVE_MIDDLE_JSON = "native/middle_json.json"
NATIVE_DOCLING_JSON = "native/docling.json"
NATIVE_MARKER_JSON = "native/marker.json"

Backend = Literal["mineru", "docling", "marker"]
MarkerMode = Literal["balanced", "fast"]
TableMode = Literal["accurate", "fast"]
Device = Literal["auto", "cpu", "mps", "cuda"]

Tier = Literal["flash", "basic", "standard", "advanced"]
ParseMode = Literal["auto", "txt", "ocr"]
VlmEngine = Literal["llama-cpp", "vllm"]
SmallBackend = Literal["onnx", "torch"]
Severity = Literal["info", "warning", "error"]
Status = Literal["completed", "failed"]

_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _timing(value: object) -> dict[str, float]:
    """Require an object of named durations in seconds.

    Parameters
    ----------
    value : object
        Decoded JSON value.

    Returns
    -------
    dict of str to float
        Durations converted to floats.
    """
    result: dict[str, float] = {}
    for key, item in mapping(value).items():
        if type(item) not in (int, float):
            raise ValueError(f"Expected a numeric duration for {key}")
        result[key] = float(cast("int | float", item))
    return result


def _absolute_path(value: object) -> Path:
    """Require an absolute filesystem path string.

    Parameters
    ----------
    value : object
        Decoded JSON value.

    Returns
    -------
    Path
        Absolute path; existence is not checked here.
    """
    path = Path(string(value))
    if not path.is_absolute():
        raise ValueError(f"Expected an absolute path, got {value!r}")
    return path


def _sha256(value: str) -> str:
    """Require a lowercase hexadecimal SHA-256 digest.

    Parameters
    ----------
    value : str
        Candidate digest.

    Returns
    -------
    str
        The digest, unchanged.
    """
    if not _SHA256.fullmatch(value):
        raise ValueError("Expected a lowercase hexadecimal SHA-256 digest")
    return value


def _request_id(value: str) -> str:
    """Require a filesystem-safe request identifier.

    Parameters
    ----------
    value : str
        Candidate identifier.

    Returns
    -------
    str
        The identifier, unchanged.
    """
    if not _REQUEST_ID.fullmatch(value):
        raise ValueError("Request identifiers use up to 64 ASCII word characters")
    return value


def _increasing_pages(values: tuple[int, ...], *, maximum: int | None) -> None:
    """Require strictly increasing one-based page numbers within a bound.

    Parameters
    ----------
    values : tuple of int
        Candidate page numbers.
    maximum : int or None
        Largest permitted page number, or None for no upper bound.
    """
    previous = 0
    for page in values:
        if type(page) is not int or page <= previous:
            raise ValueError("Pages must be strictly increasing positive integers")
        if maximum is not None and page > maximum:
            raise ValueError(f"Page {page} exceeds the page count {maximum}")
        previous = page


def _page_tuple(value: object) -> tuple[int, ...]:
    """Convert a JSON array to a tuple of page numbers.

    Parameters
    ----------
    value : object
        Decoded JSON value.

    Returns
    -------
    tuple of int
        Page numbers; ordering and bounds are validated by their owner.
    """
    return tuple(integer(item, minimum=1) for item in items(value))


def _check_result_path(path: str) -> None:
    """Reject result paths that could escape the staging directory.

    Parameters
    ----------
    path : str
        Relative POSIX path recorded by the worker.
    """
    pure = PurePosixPath(path)
    if (
        not path
        or path == "."
        or pure.is_absolute()
        or pure.as_posix() != path
        or "\\" in path
        or "\0" in path
        or ".." in pure.parts
    ):
        raise ValueError(f"Unsafe result path: {path!r}")


@dataclass(frozen=True)
class MineruProfile:
    """Select the explicit local MinerU configuration for one request.

    Attributes
    ----------
    tier : str
        MinerU parsing tier; ``standard`` is the qualified scientific setting.
    parse_mode : str
        ``auto`` lets MinerU choose text or OCR per document; ``txt`` and
        ``ocr`` force one path.
    image_analysis : bool
        Whether MinerU's own image description stage runs; kept off because
        figure descriptions are a separate, labeled stage.
    vlm_engine : str
        Local vision-language engine run inside the worker: the packaged
        llama.cpp engine with the quantized GGUF weights, or vLLM with the
        original weights on a CUDA GPU. A remote server is never permitted.
    vlm_max_concurrency : int
        Simultaneous local VLM requests.
    cpu_threads : int
        Intra-operation CPU threads for the worker's inference libraries.
    small_backend : str
        Runtime of the layout, OCR, formula and table models: ``onnx``, or
        ``torch``, which uses a CUDA GPU when one is visible.
    batch_invariant : bool
        Ask vLLM for results that do not depend on which requests share a
        batch, at some cost in speed (about 1.5x on an A40); on by default,
        vLLM only.
    """

    tier: Tier = "standard"
    parse_mode: ParseMode = "auto"
    image_analysis: bool = False
    vlm_engine: VlmEngine = "llama-cpp"
    vlm_max_concurrency: int = 1
    cpu_threads: int = 4
    small_backend: SmallBackend = "onnx"
    batch_invariant: bool = True

    def __post_init__(self) -> None:
        """Validate numeric limits for Python and JSON constructed profiles."""
        integer(self.vlm_max_concurrency, minimum=1)
        integer(self.cpu_threads, minimum=1)

    def to_dict(self) -> dict[str, object]:
        """Serialize the profile to JSON-compatible fields.

        Returns
        -------
        dict of str to object
            Field values in schema order.
        """
        return {
            "tier": self.tier,
            "parse_mode": self.parse_mode,
            "image_analysis": self.image_analysis,
            "vlm_engine": self.vlm_engine,
            "vlm_max_concurrency": self.vlm_max_concurrency,
            "cpu_threads": self.cpu_threads,
            "small_backend": self.small_backend,
            "batch_invariant": self.batch_invariant,
        }

    @classmethod
    def from_dict(cls, value: object) -> MineruProfile:
        """Parse and validate a serialized profile.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        MineruProfile
            Validated profile. A profile without ``small_backend`` or
            ``batch_invariant``, written before those fields existed, used
            the ONNX backend and ordinary batching.
        """
        fields = "tier parse_mode image_analysis vlm_engine vlm_max_concurrency"
        fields += " cpu_threads"
        raw = mapping(value)
        for optional in ("small_backend", "batch_invariant"):
            if optional in raw:
                fields += f" {optional}"
        data = record(raw, fields)
        return cls(
            tier=cast("Tier", choice(data["tier"], "flash basic standard advanced")),
            parse_mode=cast("ParseMode", choice(data["parse_mode"], "auto txt ocr")),
            image_analysis=boolean(data["image_analysis"]),
            vlm_engine=cast("VlmEngine", choice(data["vlm_engine"], "llama-cpp vllm")),
            vlm_max_concurrency=integer(data["vlm_max_concurrency"], minimum=1),
            cpu_threads=integer(data["cpu_threads"], minimum=1),
            small_backend=cast(
                "SmallBackend", choice(data.get("small_backend", "onnx"), "onnx torch")
            ),
            batch_invariant=boolean(data.get("batch_invariant", False)),
        )


@dataclass(frozen=True)
class DoclingProfile:
    """Select the explicit local Docling configuration for one request.

    Attributes
    ----------
    table_mode : str
        TableFormer mode; ``accurate`` is the qualified setting.
    cell_matching : bool
        Whether cell text comes from the PDF text layer matched to the
        predicted cells, rather than from the model's own cell boxes.
    formulas : bool
        Whether Docling's formula model transcribes display formulas as LaTeX;
        slow on a CPU, and without it formulas are not transcribed.
    images : bool
        Whether the worker saves picture and table crops.
    device : str
        Accelerator for layout and table models; ``auto`` lets Docling
        choose and the result records the choice. The formula model always
        runs on the CPU.
    cpu_threads : int
        Intra-operation CPU threads.

    Notes
    -----
    OCR is not configurable: it stays off because Docling's OCR engines
    download models on first use.
    """

    table_mode: TableMode = "accurate"
    cell_matching: bool = True
    formulas: bool = True
    images: bool = True
    device: Device = "auto"
    cpu_threads: int = 4

    def __post_init__(self) -> None:
        """Validate numeric limits for Python and JSON constructed profiles."""
        integer(self.cpu_threads, minimum=1)

    def to_dict(self) -> dict[str, object]:
        """Serialize the profile to JSON-compatible fields.

        Returns
        -------
        dict of str to object
            Field values in schema order.
        """
        return {
            "table_mode": self.table_mode,
            "cell_matching": self.cell_matching,
            "formulas": self.formulas,
            "images": self.images,
            "device": self.device,
            "cpu_threads": self.cpu_threads,
        }

    @classmethod
    def from_dict(cls, value: object) -> DoclingProfile:
        """Parse and validate a serialized profile.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        DoclingProfile
            Validated profile.
        """
        data = record(
            value, "table_mode cell_matching formulas images device cpu_threads"
        )
        return cls(
            table_mode=cast("TableMode", choice(data["table_mode"], "accurate fast")),
            cell_matching=boolean(data["cell_matching"]),
            formulas=boolean(data["formulas"]),
            images=boolean(data["images"]),
            device=cast("Device", choice(data["device"], "auto cpu mps cuda")),
            cpu_threads=integer(data["cpu_threads"], minimum=1),
        )


@dataclass(frozen=True)
class MarkerProfile:
    """Select the explicit local Marker 2 configuration for one request.

    Attributes
    ----------
    server_binary : Path
        Absolute path of the local llama.cpp ``llama-server`` that serves the
        Surya model; the worker starts it on 127.0.0.1 and stops it.
    mode : str
        Marker converter mode; ``balanced`` sends layout and recognition to
        the Surya model, ``fast`` uses the small layout detector.
    cpu_threads : int
        CPU threads for the server and the worker.

    Notes
    -----
    Marker's LLM services are not configurable and stay off.
    """

    server_binary: Path
    mode: MarkerMode = "balanced"
    cpu_threads: int = 4

    def __post_init__(self) -> None:
        """Validate the server path and thread count."""
        integer(self.cpu_threads, minimum=1)
        if not self.server_binary.is_absolute():
            raise ValueError(f"Expected an absolute path, got {self.server_binary}")

    def to_dict(self) -> dict[str, object]:
        """Serialize the profile to JSON-compatible fields.

        Returns
        -------
        dict of str to object
            Field values in schema order.
        """
        return {
            "mode": self.mode,
            "server_binary": str(self.server_binary),
            "cpu_threads": self.cpu_threads,
        }

    @classmethod
    def from_dict(cls, value: object) -> MarkerProfile:
        """Parse and validate a serialized profile.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        MarkerProfile
            Validated profile.
        """
        data = record(value, "mode server_binary cpu_threads")
        return cls(
            server_binary=_absolute_path(data["server_binary"]),
            mode=cast("MarkerMode", choice(data["mode"], "balanced fast")),
            cpu_threads=integer(data["cpu_threads"], minimum=1),
        )


Profile = MineruProfile | DoclingProfile | MarkerProfile
_PROFILE_BACKENDS: Mapping[type, Backend] = {
    MineruProfile: "mineru",
    DoclingProfile: "docling",
    MarkerProfile: "marker",
}
_NATIVE_DOCUMENTS: Mapping[str, str] = {
    "mineru": NATIVE_MIDDLE_JSON,
    "docling": NATIVE_DOCLING_JSON,
    "marker": NATIVE_MARKER_JSON,
}
_PROFILE_READERS: Mapping[str, Callable[[object], Profile]] = {
    "mineru": MineruProfile.from_dict,
    "docling": DoclingProfile.from_dict,
    "marker": MarkerProfile.from_dict,
}


def native_document_path(backend: str) -> str:
    """Name the native document a completed run of a backend must contain.

    Parameters
    ----------
    backend : str
        Worker family.

    Returns
    -------
    str
        Path relative to the worker staging directory.

    Examples
    --------
    >>> native_document_path("docling")
    'native/docling.json'
    """
    return _NATIVE_DOCUMENTS[backend]


@dataclass(frozen=True)
class ExtractionRequest:
    """Bind one worker run to a preserved source and explicit settings.

    Attributes
    ----------
    request_id : str
        Caller-chosen identifier, up to 64 ASCII word characters.
    source_path : Path
        Absolute path of the preserved PDF copy the worker may read.
    source_sha256 : str
        Digest the worker must verify before parsing.
    expected_page_count : int
        Page count from the coordinator's independent inspection.
    pages : tuple of int or None
        Increasing one-based page selection, or None for every page.
    profile : MineruProfile, DoclingProfile or MarkerProfile
        Explicit backend configuration; its type decides the backend.
    model_dir : Path
        Absolute local model directory; no download may occur.
    output_dir : Path
        Absolute staging directory owned by this request.
    """

    request_id: str
    source_path: Path
    source_sha256: str
    expected_page_count: int
    pages: tuple[int, ...] | None
    profile: Profile
    model_dir: Path
    output_dir: Path

    @property
    def backend(self) -> Backend:
        """Name the worker family that serves this request.

        Returns
        -------
        str
            ``mineru``, ``docling`` or ``marker``, from the profile type.
        """
        return _PROFILE_BACKENDS[type(self.profile)]

    def __post_init__(self) -> None:
        """Validate identifiers, paths and the page selection."""
        _request_id(self.request_id)
        _sha256(self.source_sha256)
        integer(self.expected_page_count, minimum=1)
        for path in (self.source_path, self.model_dir, self.output_dir):
            if not path.is_absolute():
                raise ValueError(f"Expected an absolute path, got {path}")
        if self.pages is not None:
            if not self.pages:
                raise ValueError("An explicit page selection cannot be empty")
            _increasing_pages(self.pages, maximum=self.expected_page_count)

    def resolved_pages(self) -> tuple[int, ...]:
        """Return the concrete one-based pages this request covers.

        Returns
        -------
        tuple of int
            The explicit selection, or every page when none was given.
        """
        if self.pages is None:
            return tuple(range(1, self.expected_page_count + 1))
        return self.pages

    def to_dict(self) -> dict[str, object]:
        """Serialize the request with its schema and protocol version.

        Returns
        -------
        dict of str to object
            JSON-compatible request document.
        """
        return {
            "schema": REQUEST_SCHEMA,
            "protocol_version": PROTOCOL_VERSION,
            "request_id": self.request_id,
            "backend": self.backend,
            "source_path": str(self.source_path),
            "source_sha256": self.source_sha256,
            "expected_page_count": self.expected_page_count,
            "pages": None if self.pages is None else list(self.pages),
            "profile": self.profile.to_dict(),
            "model_dir": str(self.model_dir),
            "output_dir": str(self.output_dir),
        }

    def to_json(self) -> str:
        """Serialize the request as stable, human-readable JSON.

        Returns
        -------
        str
            Indented UTF-8 JSON with a trailing newline.
        """
        return dump(self.to_dict())

    @classmethod
    def from_dict(cls, value: object) -> ExtractionRequest:
        """Parse and validate a serialized request.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        ExtractionRequest
            Validated request.
        """
        data = record(
            value,
            "schema protocol_version request_id backend source_path source_sha256 "
            "expected_page_count pages profile model_dir output_dir",
        )
        _check_envelope(data, REQUEST_SCHEMA)
        pages = data["pages"]
        backend = choice(data["backend"], "mineru docling marker")
        profile = _PROFILE_READERS[backend](data["profile"])
        return cls(
            request_id=string(data["request_id"]),
            source_path=_absolute_path(data["source_path"]),
            source_sha256=string(data["source_sha256"]),
            expected_page_count=integer(data["expected_page_count"], minimum=1),
            pages=None if pages is None else _page_tuple(pages),
            profile=profile,
            model_dir=_absolute_path(data["model_dir"]),
            output_dir=_absolute_path(data["output_dir"]),
        )

    @classmethod
    def from_json(cls, text: str) -> ExtractionRequest:
        """Parse a serialized request document.

        Parameters
        ----------
        text : str
            JSON text produced by :meth:`to_json` or an equivalent writer.

        Returns
        -------
        ExtractionRequest
            Validated request.
        """
        return cls.from_dict(json.loads(text))


@dataclass(frozen=True)
class ResultFile:
    """Record one file the worker wrote beneath its staging directory.

    Attributes
    ----------
    path : str
        Normalized relative POSIX path within the staging directory.
    sha256 : str
        Digest of the written bytes.
    size_bytes : int
        Byte length of the file.
    """

    path: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        """Reject unsafe paths and malformed digests."""
        _check_result_path(self.path)
        _sha256(self.sha256)
        integer(self.size_bytes, minimum=0)

    def to_dict(self) -> dict[str, object]:
        """Serialize the file record.

        Returns
        -------
        dict of str to object
            JSON-compatible fields.
        """
        return {"path": self.path, "sha256": self.sha256, "size_bytes": self.size_bytes}

    @classmethod
    def from_dict(cls, value: object) -> ResultFile:
        """Parse and validate a serialized file record.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        ResultFile
            Validated record.
        """
        data = record(value, "path sha256 size_bytes")
        return cls(
            path=text(data["path"]),
            sha256=string(data["sha256"]),
            size_bytes=integer(data["size_bytes"], minimum=0),
        )


@dataclass(frozen=True)
class PageCoverage:
    """Compare requested pages with the pages actually returned.

    Attributes
    ----------
    requested : tuple of int
        One-based pages the worker was asked to process.
    returned : tuple of int
        One-based pages present in the native output.
    empty : tuple of int
        Returned pages without any block; a placeholder for an unreadable page
        and a genuinely blank page are indistinguishable here.
    """

    requested: tuple[int, ...]
    returned: tuple[int, ...]
    empty: tuple[int, ...]

    def __post_init__(self) -> None:
        """Validate ordering and containment of the page lists."""
        if not self.requested:
            raise ValueError("Coverage requires at least one requested page")
        _increasing_pages(self.requested, maximum=None)
        _increasing_pages(self.returned, maximum=None)
        _increasing_pages(self.empty, maximum=None)
        if not set(self.empty) <= set(self.returned):
            raise ValueError("Empty pages must be among the returned pages")

    @property
    def missing(self) -> tuple[int, ...]:
        """List requested pages that the worker did not return.

        Returns
        -------
        tuple of int
            Increasing one-based page numbers.
        """
        return tuple(sorted(set(self.requested) - set(self.returned)))

    @property
    def unexpected(self) -> tuple[int, ...]:
        """List returned pages that were never requested.

        Returns
        -------
        tuple of int
            Increasing one-based page numbers.
        """
        return tuple(sorted(set(self.returned) - set(self.requested)))

    def to_dict(self) -> dict[str, object]:
        """Serialize the coverage record.

        Returns
        -------
        dict of str to object
            JSON-compatible fields.
        """
        return {
            "requested": list(self.requested),
            "returned": list(self.returned),
            "empty": list(self.empty),
        }

    @classmethod
    def from_dict(cls, value: object) -> PageCoverage:
        """Parse and validate a serialized coverage record.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        PageCoverage
            Validated record.
        """
        data = record(value, "requested returned empty")
        return cls(
            requested=_page_tuple(data["requested"]),
            returned=_page_tuple(data["returned"]),
            empty=_page_tuple(data["empty"]),
        )


@dataclass(frozen=True)
class Diagnostic:
    """Carry one structured worker observation into the result.

    Attributes
    ----------
    code : str
        Stable upper-case code such as ``PAGE_EMPTY``.
    severity : str
        ``info``, ``warning`` or ``error``.
    message : str
        Human-readable explanation with the affected pages or objects.
    """

    code: str
    severity: Severity
    message: str

    def to_dict(self) -> dict[str, object]:
        """Serialize the diagnostic.

        Returns
        -------
        dict of str to object
            JSON-compatible fields.
        """
        return {"code": self.code, "severity": self.severity, "message": self.message}

    @classmethod
    def from_dict(cls, value: object) -> Diagnostic:
        """Parse and validate a serialized diagnostic.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        Diagnostic
            Validated diagnostic.
        """
        data = record(value, "code severity message")
        return cls(
            code=string(data["code"]),
            severity=cast("Severity", choice(data["severity"], "info warning error")),
            message=string(data["message"]),
        )


@dataclass(frozen=True)
class FailureInfo:
    """Describe why a worker run failed.

    Attributes
    ----------
    kind : str
        Exception class name or worker-defined failure kind.
    message : str
        Exception message without credentials.
    traceback : str
        Formatted traceback, or an empty string when unavailable.
    """

    kind: str
    message: str
    traceback: str

    def to_dict(self) -> dict[str, object]:
        """Serialize the failure description.

        Returns
        -------
        dict of str to object
            JSON-compatible fields.
        """
        return {"kind": self.kind, "message": self.message, "traceback": self.traceback}

    @classmethod
    def from_dict(cls, value: object) -> FailureInfo:
        """Parse and validate a serialized failure description.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        FailureInfo
            Validated description.
        """
        data = record(value, "kind message traceback")
        return cls(
            kind=string(data["kind"]),
            message=text(data["message"]),
            traceback=text(data["traceback"]),
        )


@dataclass(frozen=True)
class ExtractionResult:
    """Report one worker run, whether it completed or failed.

    Attributes
    ----------
    request_id : str
        Identifier copied from the request.
    backend_version : str
        Installed backend package version.
    status : str
        ``completed`` when native output for the request exists; ``failed``
        otherwise. Completed is not a statement about scientific quality.
    source_sha256 : str
        Digest the worker verified before parsing.
    page_count : int or None
        Page count the worker measured itself; None when it never got that far.
    coverage : PageCoverage or None
        Requested versus returned pages; required for a completed run.
    files : tuple of ResultFile
        Every file written beneath the staging directory, with digests.
    configuration : Mapping of str to object
        Resolved backend configuration with credentials redacted.
    environment : Mapping of str to object
        Interpreter, platform, package versions, device and model evidence.
    timing : Mapping of str to float
        Named wall-clock durations in seconds.
    resources : Mapping of str to object
        Memory and load observations, with their measurement limits.
    diagnostics : tuple of Diagnostic
        Structured observations such as empty or missing pages.
    failure : FailureInfo or None
        Present exactly when the status is ``failed``.
    backend : str
        Worker family, ``mineru``, ``docling`` or ``marker``.
    """

    request_id: str
    backend_version: str
    status: Status
    source_sha256: str
    page_count: int | None
    coverage: PageCoverage | None
    files: tuple[ResultFile, ...]
    configuration: Mapping[str, object]
    environment: Mapping[str, object]
    timing: Mapping[str, float]
    resources: Mapping[str, object]
    diagnostics: tuple[Diagnostic, ...]
    failure: FailureInfo | None
    backend: Backend = "mineru"

    def __post_init__(self) -> None:
        """Validate identifiers, file uniqueness and status invariants."""
        _request_id(self.request_id)
        _sha256(self.source_sha256)
        paths = [item.path for item in self.files]
        if len(set(paths)) != len(paths):
            raise ValueError("Result files must have unique paths")
        if self.status == "completed":
            if self.coverage is None or self.page_count is None:
                raise ValueError("A completed result needs coverage and a page count")
            if self.failure is not None:
                raise ValueError("A completed result cannot carry a failure")
        elif self.failure is None:
            raise ValueError("A failed result must describe its failure")

    def to_dict(self) -> dict[str, object]:
        """Serialize the result with its schema and protocol version.

        Returns
        -------
        dict of str to object
            JSON-compatible result document.
        """
        return {
            "schema": RESULT_SCHEMA,
            "protocol_version": PROTOCOL_VERSION,
            "request_id": self.request_id,
            "backend": self.backend,
            "backend_version": self.backend_version,
            "status": self.status,
            "source_sha256": self.source_sha256,
            "page_count": self.page_count,
            "coverage": None if self.coverage is None else self.coverage.to_dict(),
            "files": [item.to_dict() for item in self.files],
            "configuration": dict(self.configuration),
            "environment": dict(self.environment),
            "timing": dict(self.timing),
            "resources": dict(self.resources),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "failure": None if self.failure is None else self.failure.to_dict(),
        }

    def to_json(self) -> str:
        """Serialize the result as stable, human-readable JSON.

        Returns
        -------
        str
            Indented UTF-8 JSON with a trailing newline.
        """
        return dump(self.to_dict())

    @classmethod
    def from_dict(cls, value: object) -> ExtractionResult:
        """Parse and validate a serialized result.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        ExtractionResult
            Validated result.
        """
        data = record(
            value,
            "schema protocol_version request_id backend backend_version status "
            "source_sha256 page_count coverage files configuration environment "
            "timing resources diagnostics failure",
        )
        _check_envelope(data, RESULT_SCHEMA)
        page_count = data["page_count"]
        coverage = data["coverage"]
        failure = data["failure"]
        return cls(
            request_id=string(data["request_id"]),
            backend_version=string(data["backend_version"]),
            status=cast("Status", choice(data["status"], "completed failed")),
            source_sha256=string(data["source_sha256"]),
            page_count=None if page_count is None else integer(page_count, minimum=1),
            coverage=None if coverage is None else PageCoverage.from_dict(coverage),
            files=tuple(ResultFile.from_dict(item) for item in items(data["files"])),
            configuration=mapping(data["configuration"]),
            environment=mapping(data["environment"]),
            timing=_timing(data["timing"]),
            resources=mapping(data["resources"]),
            diagnostics=tuple(
                Diagnostic.from_dict(item) for item in items(data["diagnostics"])
            ),
            failure=None if failure is None else FailureInfo.from_dict(failure),
            backend=cast("Backend", choice(data["backend"], "mineru docling marker")),
        )

    @classmethod
    def from_json(cls, text: str) -> ExtractionResult:
        """Parse a serialized result document.

        Parameters
        ----------
        text : str
            JSON text written by a worker.

        Returns
        -------
        ExtractionResult
            Validated result.
        """
        return cls.from_dict(json.loads(text))


def _check_envelope(data: Mapping[str, object], schema: str) -> None:
    """Reject documents from another schema or protocol version.

    Parameters
    ----------
    data : Mapping of str to object
        Decoded top-level document.
    schema : str
        Expected schema name.
    """
    if data["schema"] != schema:
        raise ValueError(f"Expected schema {schema}, got {data['schema']!r}")
    if data["protocol_version"] != PROTOCOL_VERSION:
        raise ValueError(
            f"Unsupported protocol version {data['protocol_version']!r}; "
            f"this coordinator speaks version {PROTOCOL_VERSION}"
        )
