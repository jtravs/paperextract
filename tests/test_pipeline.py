"""Run the end-to-end slice with a stand-in worker that emits native output."""

import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from paperextract.pipeline import (
    DOCUMENT_FILENAME,
    HINTS_FILENAME,
    SOURCE_FILENAME,
    SOURCE_RECORD_FILENAME,
    WORKER_DIRECTORY,
    ExtractionSettings,
    extract_pdf,
    read_hints,
    write_hints,
)
from paperextract.protocol import MineruProfile
from paperextract.worker import WorkerEnvironment

# A stand-in worker that copies a prepared native document into place.
NATIVE_WORKER = """
import hashlib
import json
import sys
from pathlib import Path

BEHAVIOUR = __BEHAVIOUR__
NATIVE = Path(__NATIVE__)
request = json.loads(Path(sys.argv[1]).read_text())
out = Path.cwd()


def record(relative, data):
    target = out / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    return {"path": relative, "sha256": digest, "size_bytes": len(data)}


native_bytes = b"[]" if BEHAVIOUR == "not_object" else NATIVE.read_bytes()
files = [
    record("native/middle_json.json", native_bytes),
    record("native/images/page_0_equation_3.jpg", b"jpg"),
]
count = request["expected_page_count"]
result = {
    "schema": "paperextract.worker-result",
    "protocol_version": 1,
    "request_id": request["request_id"],
    "backend": "mineru",
    "backend_version": "4.0.5",
    "status": "completed",
    "source_sha256": request["source_sha256"],
    "page_count": count,
    "coverage": {
        "requested": list(range(1, count + 1)),
        "returned": [1, 2],
        "empty": [2],
    },
    "files": files,
    "configuration": {},
    "environment": {},
    "timing": {"parse_seconds": 0.1},
    "resources": {},
    "diagnostics": [],
    "failure": None,
}
if BEHAVIOUR == "failed":
    result.update(
        status="failed",
        page_count=None,
        coverage=None,
        failure={"kind": "RuntimeError", "message": "boom", "traceback": ""},
    )
(out / "result.json").write_text(json.dumps(result))
sys.exit(1 if BEHAVIOUR == "failed" else 0)
"""

NATIVE: dict[str, object] = {
    "schema": "docvortex.middle",
    "schema_version": "2.0",
    "is_full_document": True,
    "metadata": {
        "file_suffix": "pdf",
        "producer": {"name": "mineru", "version": "4.0.5"},
    },
    "extensions": {
        "docvortex_layout": {
            "pages": [{"page_idx": 0, "width_pt": 300.0, "height_pt": 200.0}]
        }
    },
    "pages": [
        {
            "page_idx": 0,
            "blocks": [
                {
                    "type": "doc_title",
                    "index": 0,
                    "bbox": [0.1, 0.1, 0.9, 0.2],
                    "level": 1,
                    "content": [{"type": "text", "content": "Title"}],
                },
                {
                    "type": "equation",
                    "index": 1,
                    "bbox": [0.1, 0.3, 0.9, 0.4],
                    "content": "x^{2}\\tag{1}",
                    "image_path": "images/page_0_equation_3.jpg",
                },
            ],
        },
        {"page_idx": 1, "blocks": []},
    ],
}


def make_settings(tmp_path: Path, behaviour: str) -> ExtractionSettings:
    native = tmp_path / "native.json"
    native.write_text(json.dumps(NATIVE))
    script = tmp_path / f"worker_{behaviour}.py"
    script.write_text(
        NATIVE_WORKER.replace("__BEHAVIOUR__", repr(behaviour)).replace(
            "__NATIVE__", repr(str(native))
        )
    )
    return ExtractionSettings(
        environment=WorkerEnvironment(python=Path(sys.executable), script=script),
        model_dir=tmp_path / "models",
        profile=MineruProfile(cpu_threads=1),
        timeout_seconds=60,
    )


def test_completed_run_writes_source_record_and_document(
    tmp_path: Path, pdf_builder: Callable[..., bytes]
) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(pdf_builder(["Body text doi:10.1000/z", None, None], title="Paper"))
    staging = tmp_path / "staging"
    staging.mkdir()
    outcome = extract_pdf(
        pdf, staging, make_settings(tmp_path, "ok"), request_id="run-1"
    )
    assert outcome.staging == staging.resolve()
    assert outcome.source.original_name == "paper.pdf"
    assert outcome.inspection.page_count == 3
    assert outcome.fingerprint.doi_candidates == ("10.1000/z",)
    assert outcome.request.expected_page_count == 3
    assert outcome.result.status == "completed"
    assert outcome.document is not None
    assert [page.status for page in outcome.document.pages] == [
        "processed",
        "empty",
        "missing",
    ]
    assert [(page.width_pt, page.height_pt) for page in outcome.document.pages] == [
        (300.0, 200.0),
        (300.0, 200.0),
        (300.0, 200.0),
    ]
    codes = {finding.code for finding in outcome.document.findings}
    assert {"PAGE_EMPTY", "PAGE_NOT_PROCESSED"} <= codes
    assert "ASSET_MISSING" not in codes
    record = json.loads((staging / SOURCE_RECORD_FILENAME).read_text())
    assert record["schema"] == "paperextract.source-record"
    assert record["sha256"] == outcome.source.sha256
    assert record["stored_path"] == SOURCE_FILENAME
    assert record["inspection"]["page_count"] == 3
    assert record["fingerprint"]["doi_candidates"] == ["10.1000/z"]
    written = json.loads((staging / DOCUMENT_FILENAME).read_text())
    assert written == outcome.document.to_dict()
    assert (staging / WORKER_DIRECTORY / "native" / "middle_json.json").exists()
    assert (staging / SOURCE_FILENAME).read_bytes() == pdf.read_bytes()


def test_failed_worker_leaves_source_artifacts_without_a_document(
    tmp_path: Path, pdf_builder: Callable[..., bytes]
) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(pdf_builder(["Body"]))
    staging = tmp_path / "staging"
    staging.mkdir()
    outcome = extract_pdf(
        pdf, staging, make_settings(tmp_path, "failed"), request_id="run-2"
    )
    assert outcome.result.status == "failed"
    assert outcome.document is None
    assert not (staging / DOCUMENT_FILENAME).exists()
    assert (staging / SOURCE_RECORD_FILENAME).exists()


def test_malformed_native_document_is_rejected(
    tmp_path: Path, pdf_builder: Callable[..., bytes]
) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(pdf_builder(["Body", None]))
    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(ValueError, match="not a JSON object"):
        extract_pdf(
            pdf, staging, make_settings(tmp_path, "not_object"), request_id="run-3"
        )


def test_staging_must_be_empty(
    tmp_path: Path, pdf_builder: Callable[..., bytes]
) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(pdf_builder(["Body"]))
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "leftover").write_text("x")
    with pytest.raises(FileExistsError):
        extract_pdf(pdf, staging, make_settings(tmp_path, "ok"), request_id="run-4")


def test_hints_are_objects_of_strings(tmp_path: Path) -> None:
    write_hints(tmp_path, {})
    assert read_hints(tmp_path) == {}
    write_hints(tmp_path, {"version": "preprint"})
    assert read_hints(tmp_path) == {"version": "preprint"}
    (tmp_path / HINTS_FILENAME).write_text('{"version": 3}')
    with pytest.raises(ValueError, match="object of strings"):
        read_hints(tmp_path)
