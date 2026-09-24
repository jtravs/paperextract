"""Run one PDF through preservation, inspection, the worker and normalization."""

from __future__ import annotations

import functools
import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import cast

from paperextract.capture import preserve_capture
from paperextract.document import Document, Finding
from paperextract.ingest import SourceArtifact, preserve_pdf
from paperextract.layout import refine_document
from paperextract.normalize import normalize_mineru
from paperextract.normalize_docling import normalize_docling
from paperextract.normalize_marker import normalize_marker
from paperextract.pdf import (
    ContentFingerprint,
    PdfInspection,
    drop_cap_letter,
    fingerprint_pdf,
    inspect_pdf,
)
from paperextract.protocol import (
    RESULT_FILENAME,
    DoclingProfile,
    ExtractionRequest,
    ExtractionResult,
    MineruProfile,
    Profile,
    native_document_path,
)
from paperextract.registry import Lookup, Search
from paperextract.table_check import apply_table_check, table_check_pages
from paperextract.table_ocr import apply_ocr_tables, ocr_candidate_pages
from paperextract.worker import (
    WorkerEnvironment,
    WorkerError,
    WorkerSessions,
    require_empty_directory,
    run_worker,
)

__all__ = [
    "CAPTURES_DIRECTORY",
    "DOCUMENT_FILENAME",
    "EQUIVALENTS_DIRECTORY",
    "HINTS_FILENAME",
    "OCR_WORKER_DIRECTORY",
    "SOURCE_FILENAME",
    "SOURCE_RECORD_FILENAME",
    "SOURCE_RECORD_SCHEMA",
    "SOURCE_RECORD_VERSION",
    "SUPPLEMENTS_DIRECTORY",
    "TABLE_CHECK_DIRECTORY",
    "WORKER_DIRECTORY",
    "BundleOutcome",
    "ExtractionOutcome",
    "ExtractionSettings",
    "PaperSources",
    "TableCheckSettings",
    "attach_capture",
    "attach_equivalent",
    "capture_directories",
    "equivalent_directories",
    "extract_bundle",
    "extract_pdf",
    "read_hints",
    "rebuild_document",
    "supplement_directory",
    "write_hints",
]

SOURCE_FILENAME = "source.pdf"
SOURCE_RECORD_FILENAME = "source.json"
SOURCE_RECORD_SCHEMA = "paperextract.source-record"
SOURCE_RECORD_VERSION = 1
WORKER_DIRECTORY = "worker"
OCR_WORKER_DIRECTORY = "worker-ocr"
TABLE_CHECK_DIRECTORY = "worker-check"
SUPPLEMENTS_DIRECTORY = "supplements"
CAPTURES_DIRECTORY = "html"
EQUIVALENTS_DIRECTORY = "equivalents"
HINTS_FILENAME = "hints.json"
DOCUMENT_FILENAME = "document.json"


@dataclass(frozen=True)
class TableCheckSettings:
    """Fix the Docling worker used to cross-check extracted tables.

    Attributes
    ----------
    environment : WorkerEnvironment
        Pinned Docling interpreter and script.
    model_dir : Path
        Absolute Docling model directory.
    profile : DoclingProfile
        Docling configuration; formulas and crops are off by default because
        the check reads only table cells.
    """

    environment: WorkerEnvironment
    model_dir: Path
    profile: DoclingProfile = field(
        default_factory=lambda: DoclingProfile(formulas=False, images=False)
    )


@dataclass(frozen=True)
class ExtractionSettings:
    """Fix the worker, models and profile used for a run.

    Attributes
    ----------
    environment : WorkerEnvironment
        Pinned worker interpreter and script.
    model_dir : Path
        Absolute local model directory.
    profile : MineruProfile or DoclingProfile
        Explicit backend configuration; its type selects the backend.
    timeout_seconds : float or None
        Wall-clock budget for the worker process.
    lookup : Callable or None
        Registry lookup for the identity stage, or None to skip it.
    search : Callable or None
        Bibliographic search for papers without an accepted DOI.
    table_ocr : bool
        Re-extract, with OCR, the pages of tables whose text layer lost
        glyphs; MinerU only.
    table_check : TableCheckSettings or None
        Cross-check MinerU's tables with Docling, or None to skip the check.
    sessions : WorkerSessions or None
        Serving MinerU processes shared by the papers of a batch, or None to
        start a process per request.
    """

    environment: WorkerEnvironment
    model_dir: Path
    profile: Profile = field(default_factory=MineruProfile)
    timeout_seconds: float | None = None
    lookup: Lookup | None = None
    search: Search | None = None
    table_ocr: bool = True
    table_check: TableCheckSettings | None = None
    sessions: WorkerSessions | None = None

    def mineru_sessions(self) -> WorkerSessions | None:
        """Return the sessions when the profile runs MinerU, which can serve.

        Returns
        -------
        WorkerSessions or None
            The sessions, or None for another backend.
        """
        return self.sessions if isinstance(self.profile, MineruProfile) else None


@dataclass(frozen=True)
class ExtractionOutcome:
    """Collect everything one run produced.

    Attributes
    ----------
    staging : Path
        Resolved staging directory that holds every file below.
    source : SourceArtifact
        Preserved source copy.
    inspection : PdfInspection
        Independent page inspection of the preserved copy.
    fingerprint : ContentFingerprint
        Text fingerprint of the preserved copy.
    request : ExtractionRequest
        Request sent to the worker.
    result : ExtractionResult
        Verified worker result, completed or failed.
    document : Document or None
        Canonical document, present only for a completed result.
    ocr_result : ExtractionResult or None
        Verified result of the table OCR re-extraction, when one ran.
    check_result : ExtractionResult or None
        Verified result of the Docling table cross-check, when one ran.
    """

    staging: Path
    source: SourceArtifact
    inspection: PdfInspection
    fingerprint: ContentFingerprint
    request: ExtractionRequest
    result: ExtractionResult
    document: Document | None
    ocr_result: ExtractionResult | None = None
    check_result: ExtractionResult | None = None


def _source_record(
    source: SourceArtifact, inspection: PdfInspection, fingerprint: ContentFingerprint
) -> str:
    """Serialize the preserved-source record.

    Parameters
    ----------
    source : SourceArtifact
        Preserved copy.
    inspection : PdfInspection
        Page inspection.
    fingerprint : ContentFingerprint
        Text fingerprint.

    Returns
    -------
    str
        Stable JSON document.
    """
    record: dict[str, object] = {
        "schema": SOURCE_RECORD_SCHEMA,
        "schema_version": SOURCE_RECORD_VERSION,
        "sha256": source.sha256,
        "size_bytes": source.size_bytes,
        "original_name": source.original_name,
        "stored_path": SOURCE_FILENAME,
        "inspection": {
            "pdf_version": inspection.pdf_version,
            "page_count": inspection.page_count,
            "pages": [
                {
                    "number": page.number,
                    "width_pt": page.width_pt,
                    "height_pt": page.height_pt,
                    "rotation": page.rotation,
                    "mediabox": None if page.mediabox is None else list(page.mediabox),
                }
                for page in inspection.pages
            ],
        },
        "fingerprint": fingerprint.to_dict(),
    }
    return json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _normalize(
    worker_dir: Path,
    result: ExtractionResult,
    source_sha256: str,
    page_sizes: Mapping[int, tuple[float, float]],
) -> Document:
    """Normalize the native output of one completed worker run.

    Parameters
    ----------
    worker_dir : Path
        Worker staging directory with the backend's native document.
    result : ExtractionResult
        Verified completed result.
    source_sha256 : str
        Digest of the preserved source.
    page_sizes : Mapping of int to tuple of float
        Independent page sizes in points by one-based page number.

    Returns
    -------
    Document
        Canonical document before document-level corrections.

    Raises
    ------
    ValueError
        The native document is not a JSON object or the result has no
        coverage.
    """
    native = json.loads((worker_dir / native_document_path(result.backend)).read_text())
    if not isinstance(native, dict) or result.coverage is None:
        raise ValueError("Native document is not a JSON object")
    data = cast("dict[str, object]", native)
    assets = {item.path for item in result.files}
    if result.backend == "marker":
        return normalize_marker(
            data,
            source_sha256=source_sha256,
            coverage=result.coverage,
            backend_version=result.backend_version,
            assets=assets,
            page_sizes=page_sizes,
        )
    if result.backend == "docling":
        return normalize_docling(
            data,
            source_sha256=source_sha256,
            coverage=result.coverage,
            backend_version=result.backend_version,
            assets=assets,
            page_sizes=page_sizes,
        )
    return normalize_mineru(
        data,
        source_sha256=source_sha256,
        coverage=result.coverage,
        backend_version=result.backend_version,
        assets=assets,
        page_sizes=page_sizes,
    )


def _page_sizes(inspection: PdfInspection) -> dict[int, tuple[float, float]]:
    """Map page numbers to sizes in points.

    Parameters
    ----------
    inspection : PdfInspection
        Page inspection.

    Returns
    -------
    dict of int to tuple of float
        Width and height by one-based page.
    """
    return {page.number: (page.width_pt, page.height_pt) for page in inspection.pages}


def _reextract_tables(
    document: Document,
    request: ExtractionRequest,
    inspection: PdfInspection,
    source: SourceArtifact,
    settings: ExtractionSettings,
) -> tuple[Document, ExtractionResult | None]:
    """Re-extract the pages of tables with unmapped glyphs in OCR mode.

    Parameters
    ----------
    document : Document
        Refined native document.
    request : ExtractionRequest
        First request, whose staging directory holds the new run beside it.
    inspection : PdfInspection
        Page inspection.
    source : SourceArtifact
        Preserved source.
    settings : ExtractionSettings
        Worker settings; the profile is reused with ``parse_mode="ocr"``.

    Returns
    -------
    tuple
        The document after choosing table bodies, and the OCR result, or the
        unchanged document and None when no table needed it. A failed OCR run
        keeps the native tables and adds ``TABLE_OCR_FAILED``.
    """
    pages = ocr_candidate_pages(document)
    if not pages or not isinstance(settings.profile, MineruProfile):
        return document, None
    ocr_dir = request.output_dir.parent / OCR_WORKER_DIRECTORY
    ocr_dir.mkdir()
    ocr_request = replace(
        request,
        request_id=f"{request.request_id}-ocr",
        pages=pages,
        profile=replace(settings.profile, parse_mode="ocr"),
        output_dir=ocr_dir,
    )
    try:
        ocr_result = run_worker(
            ocr_request,
            settings.environment,
            timeout_seconds=settings.timeout_seconds,
            sessions=settings.mineru_sessions(),
        )
    except WorkerError as exc:
        failure = f"{type(exc).__name__}: {exc}"
        ocr_result = None
    else:
        failure = None if ocr_result.status == "completed" else "worker failed"
    if ocr_result is None or failure is not None:
        finding = Finding(
            "TABLE_OCR_FAILED",
            "warning",
            f"The table OCR re-extraction of pages {list(pages)} failed ({failure}); "
            "the text-layer tables are kept.",
        )
        return replace(document, findings=(*document.findings, finding)), ocr_result
    ocr_document = _normalize(
        ocr_dir, ocr_result, source.sha256, _page_sizes(inspection)
    )
    return apply_ocr_tables(document, ocr_document), ocr_result


def _check_tables(
    document: Document,
    request: ExtractionRequest,
    inspection: PdfInspection,
    source: SourceArtifact,
    settings: ExtractionSettings,
) -> tuple[Document, ExtractionResult | None]:
    """Read the pages of extracted tables again with Docling and compare.

    Parameters
    ----------
    document : Document
        Document after any table OCR choice.
    request : ExtractionRequest
        First request, whose staging directory holds the new run beside it.
    inspection : PdfInspection
        Page inspection.
    source : SourceArtifact
        Preserved source.
    settings : ExtractionSettings
        Settings with the table check configured.

    Returns
    -------
    tuple
        The document with cross-check findings and Docling alternatives, and
        the Docling result; the unchanged document and None when there is
        no table or no check. A failed check adds ``TABLE_CHECK_FAILED``.
    """
    check = settings.table_check
    pages = table_check_pages(document)
    if check is None or not pages or request.backend != "mineru":
        return document, None
    check_dir = request.output_dir.parent / TABLE_CHECK_DIRECTORY
    check_dir.mkdir()
    check_request = ExtractionRequest(
        request_id=f"{request.request_id}-check",
        source_path=request.source_path,
        source_sha256=request.source_sha256,
        expected_page_count=request.expected_page_count,
        pages=pages,
        profile=check.profile,
        model_dir=check.model_dir,
        output_dir=check_dir,
    )
    try:
        result = run_worker(
            check_request, check.environment, timeout_seconds=settings.timeout_seconds
        )
    except WorkerError as exc:
        failure: str | None = f"{type(exc).__name__}: {exc}"
        result = None
    else:
        failure = None if result.status == "completed" else "worker failed"
    if result is None or failure is not None:
        finding = Finding(
            "TABLE_CHECK_FAILED",
            "warning",
            f"The Docling table cross-check of pages {list(pages)} failed "
            f"({failure}); the tables are unchecked.",
        )
        return replace(document, findings=(*document.findings, finding)), result
    reference = _normalize(check_dir, result, source.sha256, _page_sizes(inspection))
    return apply_table_check(document, reference), result


def extract_pdf(
    pdf: Path,
    staging: Path,
    settings: ExtractionSettings,
    *,
    request_id: str,
    pages: tuple[int, ...] | None = None,
) -> ExtractionOutcome:
    """Preserve, inspect, fingerprint, extract and normalize one PDF.

    Parameters
    ----------
    pdf : Path
        User-supplied PDF; never modified.
    staging : Path
        Existing, empty, private directory that receives every artifact.
    settings : ExtractionSettings
        Worker environment, model directory, profile and timeout.
    request_id : str
        Identifier for the worker request.
    pages : tuple of int or None
        Increasing one-based page selection, or None for every page.

    Returns
    -------
    ExtractionOutcome
        Preserved source, inspection, fingerprint, request, verified result and
        the canonical document when the worker completed.

    Raises
    ------
    FileNotFoundError
        The staging directory does not exist.
    FileExistsError
        The staging directory is not empty.
    ValueError
        The source is not a PDF, or the worker's native document is malformed.
    WorkerError
        The worker produced no usable or no consistent result.

    Notes
    -----
    Layout inside the staging directory: ``source.pdf`` and ``source.json``
    for the preserved copy, ``worker/`` for the request, result, logs and
    native output, and ``document.json`` for the canonical document. A failed
    worker result leaves the earlier artifacts in place and returns without a
    document, so the caller can record the failure and continue a batch.
    """
    staging = staging.resolve()
    require_empty_directory(staging)
    source = preserve_pdf(pdf, staging / SOURCE_FILENAME)
    inspection = inspect_pdf(source.stored_path)
    fingerprint = fingerprint_pdf(source.stored_path)
    (staging / SOURCE_RECORD_FILENAME).write_text(
        _source_record(source, inspection, fingerprint)
    )
    worker_dir = staging / WORKER_DIRECTORY
    worker_dir.mkdir()
    request = ExtractionRequest(
        request_id=request_id,
        source_path=source.stored_path,
        source_sha256=source.sha256,
        expected_page_count=inspection.page_count,
        pages=pages,
        profile=settings.profile,
        model_dir=settings.model_dir,
        output_dir=worker_dir,
    )
    result = run_worker(
        request,
        settings.environment,
        timeout_seconds=settings.timeout_seconds,
        sessions=settings.mineru_sessions(),
    )
    document: Document | None = None
    ocr_result: ExtractionResult | None = None
    check_result: ExtractionResult | None = None
    if result.status == "completed" and result.coverage is not None:
        document = refine_document(
            _normalize(worker_dir, result, source.sha256, _page_sizes(inspection)),
            functools.partial(drop_cap_letter, source.stored_path),
        )
        if settings.table_ocr:
            document, ocr_result = _reextract_tables(
                document, request, inspection, source, settings
            )
        document, check_result = _check_tables(
            document, request, inspection, source, settings
        )
        (staging / DOCUMENT_FILENAME).write_text(document.to_json())
    return ExtractionOutcome(
        staging=staging,
        source=source,
        inspection=inspection,
        fingerprint=fingerprint,
        request=request,
        result=result,
        document=document,
        ocr_result=ocr_result,
        check_result=check_result,
    )


@dataclass(frozen=True)
class PaperSources:
    """Name the PDFs that make up one paper.

    Attributes
    ----------
    paper : Path
        Main paper PDF.
    supplements : tuple of Path
        Supplement PDFs in order, published in the same paper directory.
    captures : tuple of Path
        Saved web pages of the same article, preserved with the paper and
        compared with its extraction.
    """

    paper: Path
    supplements: tuple[Path, ...] = ()
    captures: tuple[Path, ...] = ()


@dataclass(frozen=True)
class BundleOutcome:
    """Collect the extraction of a main PDF and its supplements.

    Attributes
    ----------
    main : ExtractionOutcome
        Main paper.
    supplements : tuple of ExtractionOutcome
        Supplements in the order given; extraction stops at the first failure.
    """

    main: ExtractionOutcome
    supplements: tuple[ExtractionOutcome, ...] = ()

    @property
    def failure(self) -> ExtractionOutcome | None:
        """Return the first component whose worker failed.

        Returns
        -------
        ExtractionOutcome or None
            Failed component, or None when every component completed.
        """
        return next(
            (item for item in (self.main, *self.supplements) if item.document is None),
            None,
        )


def supplement_directory(staging: Path, index: int) -> Path:
    """Locate the staging directory of one supplement inside a run.

    Parameters
    ----------
    staging : Path
        Main extraction staging directory.
    index : int
        One-based supplement number.

    Returns
    -------
    Path
        ``supplements/NN`` below the main staging directory.
    """
    return staging / SUPPLEMENTS_DIRECTORY / f"{index:02d}"


def extract_bundle(
    sources: PaperSources,
    staging: Path,
    settings: ExtractionSettings,
    *,
    request_id: str,
    pages: tuple[int, ...] | None = None,
) -> BundleOutcome:
    """Extract a main PDF and its supplements into one staging directory.

    Parameters
    ----------
    sources : PaperSources
        Paper and supplement PDFs.
    staging : Path
        Existing, empty staging directory for the main paper.
    settings : ExtractionSettings
        Worker settings shared by every component.
    request_id : str
        Request identifier of the main paper; supplements append ``-sNN``.
    pages : tuple of int or None
        Page selection for the main paper; supplements are extracted whole.

    Returns
    -------
    BundleOutcome
        Every component extracted before the first failure. Captures are
        preserved below ``html/`` only when every component completed.
    """
    main = extract_pdf(
        sources.paper, staging, settings, request_id=request_id, pages=pages
    )
    outcomes: list[ExtractionOutcome] = []
    if main.document is not None:
        for index, supplement in enumerate(sources.supplements, 1):
            directory = supplement_directory(main.staging, index)
            directory.mkdir(parents=True)
            outcome = extract_pdf(
                supplement,
                directory,
                settings,
                request_id=f"{request_id}-s{index:02d}",
            )
            outcomes.append(outcome)
            if outcome.document is None:
                break
    bundle = BundleOutcome(main, tuple(outcomes))
    if bundle.failure is None:
        for capture in sources.captures:
            attach_capture(main.staging, capture)
    return bundle


def equivalent_directories(staging: Path) -> list[Path]:
    """List the preserved content-equivalent copies of a staging directory.

    Parameters
    ----------
    staging : Path
        Main extraction staging directory.

    Returns
    -------
    list of Path
        ``equivalents/NN`` directories in order.
    """
    directory = staging / EQUIVALENTS_DIRECTORY
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.iterdir() if path.is_dir())


def attach_equivalent(staging: Path, pdf: Path) -> Path:
    """Preserve a PDF whose text equals the paper's as another copy of it.

    Parameters
    ----------
    staging : Path
        Main extraction staging directory.
    pdf : Path
        Content-equivalent PDF, such as a re-optimized download; never
        modified and not extracted.

    Returns
    -------
    Path
        The new ``equivalents/NN`` directory with ``source.pdf`` and
        ``source.json``.

    Raises
    ------
    ValueError
        The file is not a PDF or its text differs from the paper's.
    """
    index = len(equivalent_directories(staging)) + 1
    directory = staging / EQUIVALENTS_DIRECTORY / f"{index:02d}"
    directory.mkdir(parents=True)
    source = preserve_pdf(pdf, directory / SOURCE_FILENAME)
    fingerprint = fingerprint_pdf(source.stored_path)
    paper = json.loads((staging / SOURCE_RECORD_FILENAME).read_text())
    expected = cast("dict[str, dict[str, object]]", paper)["fingerprint"]["text_sha256"]
    if fingerprint.text_sha256 != expected:
        shutil.rmtree(directory)
        raise ValueError(f"{pdf} does not have the same text as the paper")
    (directory / SOURCE_RECORD_FILENAME).write_text(
        _source_record(source, inspect_pdf(source.stored_path), fingerprint)
    )
    return directory


def write_hints(staging: Path, hints: Mapping[str, str]) -> None:
    """Record what the user asserted about a paper for its publication.

    Parameters
    ----------
    staging : Path
        Main extraction staging directory.
    hints : Mapping of str to str
        Assertions such as ``version``; an empty mapping writes nothing.
    """
    if hints:
        (staging / HINTS_FILENAME).write_text(
            json.dumps(dict(hints), indent=2, sort_keys=True) + "\n"
        )


def read_hints(staging: Path) -> dict[str, str]:
    """Read the user's assertions about a paper.

    Parameters
    ----------
    staging : Path
        Main extraction staging directory.

    Returns
    -------
    dict of str to str
        Assertions, empty when none were recorded.

    Raises
    ------
    ValueError
        The hints file is not an object of strings.
    """
    path = staging / HINTS_FILENAME
    if not path.is_file():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or not all(
        isinstance(value, str) for value in cast("dict[str, object]", data).values()
    ):
        raise ValueError(f"{path} is not an object of strings")
    return cast("dict[str, str]", data)


def capture_directories(staging: Path) -> list[Path]:
    """List the preserved web-page captures of a staging directory.

    Parameters
    ----------
    staging : Path
        Main extraction staging directory.

    Returns
    -------
    list of Path
        ``html/NN`` directories in order.
    """
    directory = staging / CAPTURES_DIRECTORY
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.iterdir() if path.is_dir())


def attach_capture(staging: Path, capture: Path) -> Path:
    """Preserve a saved web page of the article inside a staging directory.

    Parameters
    ----------
    staging : Path
        Main extraction staging directory.
    capture : Path
        Saved HTML page or MHTML archive; never modified.

    Returns
    -------
    Path
        The new ``html/NN`` directory.

    Raises
    ------
    paperextract.capture.CaptureError
        The file is not a usable saved web page.
    """
    index = len(capture_directories(staging)) + 1
    directory = staging / CAPTURES_DIRECTORY / f"{index:02d}"
    preserve_capture(capture, directory)
    return directory


def rebuild_document(staging: Path) -> Document:
    """Normalize and refine the kept worker output of a staging directory again.

    Parameters
    ----------
    staging : Path
        Staging directory with ``source.pdf``, ``source.json`` and a completed
        ``worker/`` run, as written by :func:`extract_pdf` or reconstructed
        from a published paper.

    Returns
    -------
    Document
        Canonical document from the current normalization and corrections,
        also written to ``document.json``. A kept table OCR run and a kept
        table cross-check are applied again; no worker is started, so a
        table that newly needs OCR gets a ``TABLE_OCR_NOT_RUN`` finding.

    Raises
    ------
    ValueError
        The worker run did not complete or a record is malformed.
    """
    record = json.loads((staging / SOURCE_RECORD_FILENAME).read_text())
    if not isinstance(record, dict):
        raise ValueError("Source record is not a JSON object")
    source_record = cast("dict[str, object]", record)
    sha256 = str(source_record["sha256"])
    inspection = cast("dict[str, object]", source_record["inspection"])
    sizes = {
        int(page["number"]): (float(page["width_pt"]), float(page["height_pt"]))
        for page in cast("list[dict[str, float]]", inspection["pages"])
    }
    worker_dir = staging / WORKER_DIRECTORY
    result = ExtractionResult.from_json((worker_dir / RESULT_FILENAME).read_text())
    if result.status != "completed":
        raise ValueError("Only a completed worker run can be rebuilt")
    document = refine_document(
        _normalize(worker_dir, result, sha256, sizes),
        functools.partial(drop_cap_letter, staging / SOURCE_FILENAME),
    )
    ocr_path = staging / OCR_WORKER_DIRECTORY / RESULT_FILENAME
    ocr = (
        ExtractionResult.from_json(ocr_path.read_text()) if ocr_path.is_file() else None
    )
    if ocr is not None and ocr.status == "completed":
        document = apply_ocr_tables(
            document, _normalize(staging / OCR_WORKER_DIRECTORY, ocr, sha256, sizes)
        )
    elif ocr_candidate_pages(document):
        finding = Finding(
            "TABLE_OCR_NOT_RUN",
            "warning",
            "Tables on pages "
            f"{list(ocr_candidate_pages(document))} lost glyphs, but rebuilding "
            "from kept output does not start the OCR worker; extract the paper "
            "again to re-extract them.",
        )
        document = replace(document, findings=(*document.findings, finding))
    check_path = staging / TABLE_CHECK_DIRECTORY / RESULT_FILENAME
    if check_path.is_file():
        check = ExtractionResult.from_json(check_path.read_text())
        if check.status == "completed":
            reference = _normalize(
                staging / TABLE_CHECK_DIRECTORY, check, sha256, sizes
            )
            document = apply_table_check(document, reference)
    (staging / DOCUMENT_FILENAME).write_text(document.to_json())
    return document
