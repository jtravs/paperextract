"""Validate the versioned worker request and result contract."""

import json
from pathlib import Path

import pytest

from paperextract.protocol import (
    NATIVE_MIDDLE_JSON,
    PROTOCOL_VERSION,
    REQUEST_SCHEMA,
    RESULT_SCHEMA,
    Diagnostic,
    DoclingProfile,
    ExtractionRequest,
    ExtractionResult,
    FailureInfo,
    MineruProfile,
    PageCoverage,
    ResultFile,
    native_document_path,
)

SHA = "a" * 64
ROOT = Path("/private/staging")


def make_request(**overrides: object) -> ExtractionRequest:
    fields: dict[str, object] = {
        "request_id": "req-1",
        "source_path": ROOT / "source.pdf",
        "source_sha256": SHA,
        "expected_page_count": 10,
        "pages": (1, 3, 10),
        "profile": MineruProfile(),
        "model_dir": ROOT / "models",
        "output_dir": ROOT / "out",
    }
    fields.update(overrides)
    return ExtractionRequest(**fields)  # type: ignore[arg-type]


def make_result(**overrides: object) -> ExtractionResult:
    fields: dict[str, object] = {
        "request_id": "req-1",
        "backend_version": "4.0.5",
        "status": "completed",
        "source_sha256": SHA,
        "page_count": 10,
        "coverage": PageCoverage(requested=(1, 3, 10), returned=(1, 3), empty=(3,)),
        "files": (ResultFile(NATIVE_MIDDLE_JSON, SHA, 2),),
        "configuration": {"tier": "standard"},
        "environment": {"python": "3.12.13"},
        "timing": {"parse_seconds": 1.5},
        "resources": {"max_rss_self_bytes": 1},
        "diagnostics": (Diagnostic("PAGE_EMPTY", "warning", "Page 3 is empty."),),
        "failure": None,
    }
    fields.update(overrides)
    return ExtractionResult(**fields)  # type: ignore[arg-type]


def test_request_round_trips_through_json() -> None:
    request = make_request()
    text = request.to_json()
    assert text.endswith("\n")
    decoded = json.loads(text)
    assert decoded["schema"] == REQUEST_SCHEMA
    assert decoded["protocol_version"] == PROTOCOL_VERSION
    assert decoded["pages"] == [1, 3, 10]
    assert ExtractionRequest.from_json(text) == request


def test_request_without_pages_resolves_every_page() -> None:
    request = make_request(pages=None)
    assert request.resolved_pages() == tuple(range(1, 11))
    assert make_request().resolved_pages() == (1, 3, 10)
    assert ExtractionRequest.from_json(request.to_json()).pages is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"request_id": "bad id"},
        {"request_id": ""},
        {"request_id": "x" * 65},
        {"source_sha256": "A" * 64},
        {"expected_page_count": 0},
        {"source_path": Path("relative.pdf")},
        {"pages": ()},
        {"pages": (3, 1)},
        {"pages": (1, 1)},
        {"pages": (0, 1)},
        {"pages": (1, 11)},
    ],
)
def test_request_rejects_invalid_fields(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        make_request(**overrides)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", "paperextract.other"),
        ("protocol_version", 2),
        ("backend", "docling"),
        ("source_path", "relative.pdf"),
        ("expected_page_count", True),
        ("pages", [1, "2"]),
        ("pages", "1-2"),
        ("request_id", 7),
        ("profile", []),
    ],
)
def test_request_parsing_rejects_foreign_documents(field: str, value: object) -> None:
    document = make_request().to_dict()
    document[field] = value
    with pytest.raises(ValueError):
        ExtractionRequest.from_dict(document)


def test_request_parsing_requires_exact_fields() -> None:
    document = make_request().to_dict()
    del document["model_dir"]
    with pytest.raises(ValueError, match="exactly these fields"):
        ExtractionRequest.from_dict(document)
    with pytest.raises(ValueError, match="JSON object"):
        ExtractionRequest.from_dict([])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tier", "turbo"),
        ("parse_mode", "fast"),
        ("image_analysis", "no"),
        ("vlm_engine", "remote"),
        ("small_backend", "tensorrt"),
        ("vlm_max_concurrency", 0),
        ("cpu_threads", 1.5),
    ],
)
def test_profile_parsing_rejects_unsupported_values(field: str, value: object) -> None:
    document = MineruProfile().to_dict()
    document[field] = value
    with pytest.raises(ValueError):
        MineruProfile.from_dict(document)


def test_mineru_profiles_choose_a_local_engine_and_small_models() -> None:
    gpu = MineruProfile(vlm_engine="vllm", small_backend="torch")
    assert MineruProfile.from_dict(gpu.to_dict()) == gpu
    # Requests written before these fields existed used ONNX and ordinary
    # batching, whatever today's defaults are.
    older = MineruProfile().to_dict()
    del older["small_backend"], older["batch_invariant"]
    assert MineruProfile.from_dict(older) == MineruProfile(
        small_backend="onnx", batch_invariant=False
    )


@pytest.mark.parametrize("field", ["vlm_max_concurrency", "cpu_threads"])
def test_profile_requires_positive_limits(field: str) -> None:
    with pytest.raises(ValueError):
        MineruProfile(**{field: 0})  # type: ignore[arg-type]


def test_profile_round_trips() -> None:
    profile = MineruProfile(tier="flash", parse_mode="ocr", cpu_threads=8)
    assert MineruProfile.from_dict(profile.to_dict()) == profile


@pytest.mark.parametrize(
    "path",
    ["", "/absolute", "a//b", "a\\b", "a\0b", "../escape", "./native", "native/", "."],
)
def test_result_file_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValueError, match="Unsafe result path"):
        ResultFile(path, SHA, 1)


def test_result_file_round_trips_and_validates() -> None:
    item = ResultFile("native/images/page_1.jpg", SHA, 0)
    assert ResultFile.from_dict(item.to_dict()) == item
    with pytest.raises(ValueError):
        ResultFile("native/x", "zz", 1)
    with pytest.raises(ValueError):
        ResultFile("native/x", SHA, -1)
    with pytest.raises(ValueError):
        ResultFile.from_dict({"path": 1, "sha256": SHA, "size_bytes": 1})


def test_coverage_reports_missing_and_unexpected_pages() -> None:
    coverage = PageCoverage(requested=(1, 2, 5), returned=(2, 5, 7), empty=(7,))
    assert coverage.missing == (1,)
    assert coverage.unexpected == (7,)
    assert PageCoverage.from_dict(coverage.to_dict()) == coverage


@pytest.mark.parametrize(
    "fields",
    [
        {"requested": (), "returned": (), "empty": ()},
        {"requested": (2, 1), "returned": (), "empty": ()},
        {"requested": (1,), "returned": (1, 1), "empty": ()},
        {"requested": (1,), "returned": (1,), "empty": (2,)},
        {"requested": (1,), "returned": (1,), "empty": (0,)},
    ],
)
def test_coverage_rejects_inconsistent_pages(
    fields: dict[str, tuple[int, ...]],
) -> None:
    with pytest.raises(ValueError):
        PageCoverage(**fields)


def test_coverage_parsing_rejects_non_integer_pages() -> None:
    with pytest.raises(ValueError):
        PageCoverage.from_dict({"requested": [1], "returned": [1.0], "empty": []})
    with pytest.raises(ValueError, match="JSON array"):
        PageCoverage.from_dict({"requested": 1, "returned": [], "empty": []})


def test_diagnostic_and_failure_round_trip() -> None:
    diagnostic = Diagnostic("PAGE_EMPTY", "warning", "Page 3 is empty.")
    assert Diagnostic.from_dict(diagnostic.to_dict()) == diagnostic
    failure = FailureInfo("RuntimeError", "", "")
    assert FailureInfo.from_dict(failure.to_dict()) == failure
    with pytest.raises(ValueError, match="one of"):
        Diagnostic.from_dict({"code": "X", "severity": "fatal", "message": "m"})
    with pytest.raises(ValueError, match="Expected a string"):
        FailureInfo.from_dict({"kind": "E", "message": None, "traceback": ""})


def test_result_round_trips_through_json() -> None:
    result = make_result()
    text = result.to_json()
    decoded = json.loads(text)
    assert decoded["schema"] == RESULT_SCHEMA
    assert decoded["coverage"]["empty"] == [3]
    restored = ExtractionResult.from_json(text)
    assert restored == result
    assert restored.coverage is not None
    assert restored.coverage.missing == (10,)


def test_failed_result_round_trips_without_coverage() -> None:
    result = make_result(
        status="failed",
        page_count=None,
        coverage=None,
        files=(),
        diagnostics=(),
        failure=FailureInfo("RequestError", "digest differs", "Traceback..."),
    )
    restored = ExtractionResult.from_json(result.to_json())
    assert restored == result
    assert restored.failure is not None
    assert restored.failure.kind == "RequestError"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"coverage": None}, "coverage and a page count"),
        ({"page_count": None}, "coverage and a page count"),
        ({"failure": FailureInfo("E", "m", "")}, "cannot carry a failure"),
        ({"status": "failed"}, "must describe its failure"),
        ({"request_id": "bad id"}, "Request identifiers"),
        ({"source_sha256": "g" * 64}, "SHA-256"),
        (
            {"files": (ResultFile("a/b", SHA, 1), ResultFile("a/b", SHA, 2))},
            "unique paths",
        ),
    ],
)
def test_result_enforces_status_invariants(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        make_result(**overrides)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", "paperextract.worker-request"),
        ("protocol_version", "1"),
        ("status", "done"),
        ("backend", "nougat"),
        ("page_count", 0),
        ("files", {}),
        ("configuration", []),
        ("timing", {"parse_seconds": "fast"}),
        ("timing", {"parse_seconds": True}),
        ("diagnostics", [{"code": "X"}]),
        ("backend_version", ""),
    ],
)
def test_result_parsing_rejects_foreign_documents(field: str, value: object) -> None:
    document = make_result().to_dict()
    document[field] = value
    with pytest.raises(ValueError):
        ExtractionResult.from_dict(document)


def test_result_timing_accepts_integers_as_seconds() -> None:
    document = make_result().to_dict()
    document["timing"] = {"parse_seconds": 2}
    assert ExtractionResult.from_dict(document).timing == {"parse_seconds": 2.0}


def test_docling_requests_carry_their_own_profile() -> None:
    profile = DoclingProfile(formulas=False, device="mps", cpu_threads=2)
    request = make_request(profile=profile)
    assert request.backend == "docling"
    parsed = ExtractionRequest.from_json(request.to_json())
    assert parsed == request
    assert json.loads(request.to_json())["backend"] == "docling"
    assert DoclingProfile.from_dict(profile.to_dict()) == profile
    assert native_document_path("docling") == "native/docling.json"
    assert native_document_path("mineru") == NATIVE_MIDDLE_JSON
    with pytest.raises(ValueError, match="at least 1"):
        DoclingProfile(cpu_threads=0)
    with pytest.raises(ValueError, match="one of"):
        DoclingProfile.from_dict({**profile.to_dict(), "device": "tpu"})
    result = make_result(backend="docling")
    assert ExtractionResult.from_json(result.to_json()).backend == "docling"
