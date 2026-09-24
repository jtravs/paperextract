"""Exercise the worker boundary with a deterministic stand-in worker."""

import hashlib
import json
import signal
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

import paperextract.worker
from paperextract.protocol import (
    REQUEST_FILENAME,
    DoclingProfile,
    ExtractionRequest,
    ExtractionResult,
    MineruProfile,
)
from paperextract.worker import (
    STDERR_LOG,
    STDOUT_LOG,
    ResultIntegrityError,
    WorkerEnvironment,
    WorkerProcessError,
    WorkerSessions,
    run_worker,
    worker_environment_variables,
)

# The stand-in worker speaks protocol version 1 with the standard library only.
# BEHAVIOUR selects one deviation from a correct run.
FAKE_WORKER = """
import hashlib
import json
import os
import sys
import time
from pathlib import Path

BEHAVIOUR = __BEHAVIOUR__
request = json.loads(Path(sys.argv[1]).read_text())
out = Path.cwd()


def record(relative, data):
    target = out / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    return {"path": relative, "sha256": digest, "size_bytes": len(data)}


if BEHAVIOUR == "timeout":
    time.sleep(30)
if BEHAVIOUR == "crash":
    sys.stderr.write("worker exploded\\n")
    sys.exit(3)
if BEHAVIOUR == "invalid_json":
    (out / "result.json").write_text("{not json")
    sys.exit(1)
if BEHAVIOUR == "symlink_dir":
    escape = out.parent / "escape"
    escape.mkdir(exist_ok=True)
    (out / "native").symlink_to(escape, target_is_directory=True)
files = [
    record("native/middle_json.json", b"{}"),
    record("native/images/p.jpg", b"jpg"),
]
requested = request["pages"] or list(range(1, request["expected_page_count"] + 1))
result = {
    "schema": "paperextract.worker-result",
    "protocol_version": 1,
    "request_id": request["request_id"],
    "backend": "mineru",
    "backend_version": "fake",
    "status": "completed",
    "source_sha256": request["source_sha256"],
    "page_count": request["expected_page_count"],
    "coverage": {"requested": requested, "returned": list(requested), "empty": []},
    "files": files,
    "configuration": {"variables": dict(os.environ)},
    "environment": {},
    "timing": {"parse_seconds": 0.5},
    "resources": {},
    "diagnostics": [],
    "failure": None,
}
if BEHAVIOUR == "wrong_request_id":
    result["request_id"] = "other"
elif BEHAVIOUR == "hash_mismatch":
    result["source_sha256"] = "0" * 64
elif BEHAVIOUR == "escape_path":
    files.append({"path": "../escape.txt", "sha256": "0" * 64, "size_bytes": 1})
elif BEHAVIOUR == "symlink_file":
    link = out / "native" / "link.pdf"
    link.symlink_to(request["source_path"])
    data = link.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    files.append({"path": "native/link.pdf", "sha256": digest, "size_bytes": len(data)})
elif BEHAVIOUR == "missing_file":
    files.append({"path": "native/missing.bin", "sha256": "0" * 64, "size_bytes": 1})
elif BEHAVIOUR == "bad_hash":
    files[0]["sha256"] = "0" * 64
elif BEHAVIOUR == "bad_size":
    files[0]["size_bytes"] += 1
elif BEHAVIOUR == "wrong_page_count":
    result["page_count"] = request["expected_page_count"] + 1
elif BEHAVIOUR == "unexpected_page":
    result["coverage"]["returned"] = requested + [max(requested) + 1]
elif BEHAVIOUR == "wrong_requested":
    result["coverage"]["requested"] = requested[:1]
elif BEHAVIOUR == "missing_page":
    result["coverage"]["returned"] = requested[:-1]
elif BEHAVIOUR == "no_middle_json":
    result["files"] = files[1:]
elif BEHAVIOUR == "failed":
    result.update(
        status="failed",
        page_count=None,
        coverage=None,
        failure={
            "kind": "RuntimeError",
            "message": "model exploded",
            "traceback": "tb",
        },
    )
(out / "result.json").write_text(json.dumps(result))
sys.exit(1 if BEHAVIOUR in ("failed", "completed_nonzero") else 0)
"""

INTEGRITY_BEHAVIOURS = [
    "wrong_request_id",
    "hash_mismatch",
    "symlink_dir",
    "symlink_file",
    "missing_file",
    "bad_hash",
    "bad_size",
    "wrong_page_count",
    "unexpected_page",
    "wrong_requested",
    "no_middle_json",
    "completed_nonzero",
]


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"%PDF-1.4\nsynthetic\n%%EOF\n")
    return path


def make_request(
    tmp_path: Path, source: Path, pages: tuple[int, ...] | None
) -> ExtractionRequest:
    staging = tmp_path / "staging"
    staging.mkdir()
    return ExtractionRequest(
        request_id="req-1",
        source_path=source,
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        expected_page_count=3,
        pages=pages,
        profile=MineruProfile(cpu_threads=2),
        model_dir=tmp_path / "models",
        output_dir=staging,
    )


def make_worker(tmp_path: Path, behaviour: str) -> WorkerEnvironment:
    script = tmp_path / f"fake_worker_{behaviour}.py"
    script.write_text(FAKE_WORKER.replace("__BEHAVIOUR__", repr(behaviour)))
    return WorkerEnvironment(python=Path(sys.executable), script=script)


def test_repository_layout_locates_the_mineru_worker() -> None:
    environment = WorkerEnvironment.for_repository(Path("/repo"))
    assert environment.python == Path("/repo/workers/mineru/.venv/bin/python")
    assert environment.script == Path(
        "/repo/workers/mineru/paperextract_mineru_worker.py"
    )


def test_environment_variables_force_local_offline_models(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SECRET_TOKEN", "do-not-leak")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    request = make_request(tmp_path, source, None)
    variables = worker_environment_variables(request)
    assert "SECRET_TOKEN" not in variables
    assert variables["LC_ALL"] == "C.UTF-8"
    assert variables["MINERU_MODEL_BASE_DIR"] == str(tmp_path / "models")
    assert variables["MINERU_MODEL_SOURCE"] == "local"
    assert variables["MINERU_MODEL_VLM_ENGINE"] == "llama-cpp"
    assert variables["MINERU_HOME"] == str(request.output_dir / "mineru-home")
    assert variables["HF_HUB_OFFLINE"] == "1"
    assert variables["OMP_NUM_THREADS"] == "2"


def test_docling_workers_keep_their_caches_in_staging(
    tmp_path: Path, source: Path
) -> None:
    request = replace(
        make_request(tmp_path, source, None), profile=DoclingProfile(cpu_threads=3)
    )
    variables = worker_environment_variables(request)
    home = request.output_dir / "docling-home"
    assert variables["HF_HOME"] == str(home / "huggingface")
    assert variables["XDG_CACHE_HOME"] == str(home / "cache")
    assert variables["HF_HUB_OFFLINE"] == "1"
    assert variables["OMP_NUM_THREADS"] == "3"
    assert not any(name.startswith("MINERU_") for name in variables)
    environment = WorkerEnvironment.for_repository(Path("/repo"), "docling")
    assert environment.script == Path(
        "/repo/workers/docling/paperextract_docling_worker.py"
    )
    # A MinerU result does not answer a Docling request.
    with pytest.raises(ResultIntegrityError, match="comes from mineru"):
        run_worker(request, make_worker(tmp_path, "ok"))


def test_environment_variables_switch_off_library_telemetry(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VLLM_CACHE_ROOT", raising=False)
    # Regression: ONNX Runtime 1.30 queued and uploaded device telemetry
    # from inside the worker until this switch was set.
    variables = worker_environment_variables(make_request(tmp_path, source, None))
    assert variables["ORT_DISABLE_TELEMETRY"] == "1"
    assert variables["HF_HUB_DISABLE_TELEMETRY"] == "1"
    assert "VLLM_NO_USAGE_STATS" not in variables
    (tmp_path / "gpu").mkdir()
    gpu = replace(
        make_request(tmp_path / "gpu", source, None),
        profile=MineruProfile(vlm_engine="vllm", small_backend="torch"),
    )
    variables = worker_environment_variables(gpu)
    assert variables["VLLM_NO_USAGE_STATS"] == variables["DO_NOT_TRACK"] == "1"
    assert variables["VLLM_CACHE_ROOT"].startswith(str(gpu.output_dir))
    assert variables["MINERU_MODEL_SMALL_BACKEND"] == "torch"
    assert variables["VLLM_USE_FLASHINFER_SAMPLER"] == "0"
    assert variables["VLLM_BATCH_INVARIANT"] == "1"
    loose = replace(gpu, profile=replace(gpu.profile, batch_invariant=False))
    assert worker_environment_variables(loose)["VLLM_BATCH_INVARIANT"] == "0"
    monkeypatch.setenv("VLLM_CACHE_ROOT", "/node/cache")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1")
    variables = worker_environment_variables(gpu)
    assert variables["VLLM_CACHE_ROOT"] == "/node/cache"
    assert variables["CUDA_VISIBLE_DEVICES"] == "1"


def test_completed_run_is_verified_and_logged(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SECRET_TOKEN", "do-not-leak")
    request = make_request(tmp_path, source, (1, 3))
    result = run_worker(request, make_worker(tmp_path, "ok"), timeout_seconds=30)
    assert isinstance(result, ExtractionResult)
    assert result.status == "completed"
    assert result.coverage is not None
    assert result.coverage.requested == (1, 3)
    assert result.coverage.missing == ()
    assert {item.path for item in result.files} == {
        "native/middle_json.json",
        "native/images/p.jpg",
    }
    variables = result.configuration["variables"]
    assert isinstance(variables, dict)
    assert variables["MINERU_MODEL_SOURCE"] == "local"
    assert "SECRET_TOKEN" not in variables
    staged_request = ExtractionRequest.from_json(
        (request.output_dir / REQUEST_FILENAME).read_text()
    )
    assert staged_request == request
    assert (request.output_dir / STDOUT_LOG).exists()
    assert (request.output_dir / STDERR_LOG).exists()


def test_missing_pages_are_reported_not_rejected(tmp_path: Path, source: Path) -> None:
    request = make_request(tmp_path, source, None)
    result = run_worker(request, make_worker(tmp_path, "missing_page"))
    assert result.status == "completed"
    assert result.coverage is not None
    assert result.coverage.missing == (3,)


def test_failed_status_is_returned_for_the_caller_to_judge(
    tmp_path: Path, source: Path
) -> None:
    request = make_request(tmp_path, source, None)
    result = run_worker(request, make_worker(tmp_path, "failed"))
    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.message == "model exploded"
    assert result.page_count is None


@pytest.mark.parametrize("behaviour", INTEGRITY_BEHAVIOURS)
def test_integrity_violations_are_rejected(
    tmp_path: Path, source: Path, behaviour: str
) -> None:
    request = make_request(tmp_path, source, None)
    with pytest.raises(ResultIntegrityError):
        run_worker(request, make_worker(tmp_path, behaviour))


@pytest.mark.parametrize("behaviour", ["crash", "invalid_json", "escape_path"])
def test_unusable_results_are_process_errors(
    tmp_path: Path, source: Path, behaviour: str
) -> None:
    request = make_request(tmp_path, source, None)
    with pytest.raises(WorkerProcessError) as excinfo:
        run_worker(request, make_worker(tmp_path, behaviour))
    if behaviour == "crash":
        assert "status 3" in str(excinfo.value)
        assert "worker exploded" in str(excinfo.value)


def test_timeout_kills_the_worker_group(tmp_path: Path, source: Path) -> None:
    request = make_request(tmp_path, source, None)
    with pytest.raises(WorkerProcessError, match=r"exceeded 0\.5 s"):
        run_worker(request, make_worker(tmp_path, "timeout"), timeout_seconds=0.5)
    assert not (request.output_dir / "result.json").exists()


def test_cancellation_kills_the_worker_group(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = make_request(tmp_path, source, None)
    original = subprocess.Popen[bytes].wait
    waited: list[subprocess.Popen[bytes]] = []

    def interrupted(self: subprocess.Popen[bytes], timeout: float | None = None) -> int:
        if not waited:
            waited.append(self)
            raise KeyboardInterrupt
        return original(self, timeout)

    monkeypatch.setattr(subprocess.Popen, "wait", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run_worker(request, make_worker(tmp_path, "timeout"), timeout_seconds=30)
    assert waited[0].returncode == -signal.SIGKILL


def test_staging_directory_must_exist_and_be_empty(
    tmp_path: Path, source: Path
) -> None:
    request = make_request(tmp_path, source, None)
    worker = make_worker(tmp_path, "ok")
    (request.output_dir / "leftover").write_text("x")
    with pytest.raises(FileExistsError):
        run_worker(request, worker)
    absent = ExtractionRequest(
        request_id="req-2",
        source_path=request.source_path,
        source_sha256=request.source_sha256,
        expected_page_count=3,
        pages=None,
        profile=MineruProfile(),
        model_dir=request.model_dir,
        output_dir=tmp_path / "absent",
    )
    with pytest.raises(FileNotFoundError):
        run_worker(absent, worker)


def test_result_documents_are_stable_json(tmp_path: Path, source: Path) -> None:
    request = make_request(tmp_path, source, None)
    result = run_worker(request, make_worker(tmp_path, "ok"))
    decoded = json.loads(result.to_json())
    assert list(decoded) == sorted(decoded)


# A stand-in serving worker: it answers request paths from standard input in
# one process and records its pid and request count in each result.
SERVING_WORKER = """
import hashlib
import json
import os
import sys
import time
from pathlib import Path

BEHAVIOUR = __BEHAVIOUR__
replies = os.fdopen(os.dup(1), "w")
os.dup2(2, 1)
handled = 0
for line in sys.stdin:
    path = Path(line.strip())
    request = json.loads(path.read_text())
    out = path.parent
    handled += 1
    print(f"handling {request['request_id']}", flush=True)
    if BEHAVIOUR == "dies":
        sys.exit(4)
    if BEHAVIOUR == "crash" and handled == 2:
        sys.exit(3)
    if BEHAVIOUR == "hang":
        time.sleep(60)
    data = b"{}"
    (out / "native").mkdir()
    (out / "native" / "middle_json.json").write_bytes(data)
    requested = request["pages"] or list(
        range(1, request["expected_page_count"] + 1)
    )
    result = {
        "schema": "paperextract.worker-result",
        "protocol_version": 1,
        "request_id": request["request_id"],
        "backend": "mineru",
        "backend_version": "fake",
        "status": "completed",
        "source_sha256": request["source_sha256"],
        "page_count": request["expected_page_count"],
        "coverage": {"requested": requested, "returned": requested, "empty": []},
        "files": [
            {
                "path": "native/middle_json.json",
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
            }
        ],
        "configuration": {
            "pid": os.getpid(),
            "handled": handled,
            "home": os.environ["MINERU_HOME"],
        },
        "environment": {},
        "timing": {"parse_seconds": 0.1},
        "resources": {},
        "diagnostics": [],
        "failure": None,
    }
    (out / "result.json").write_text(json.dumps(result))
    answered = "other" if BEHAVIOUR == "wrong_reply" else str(path)
    replies.write(json.dumps({"request": answered, "status": 0}) + "\\n")
    replies.flush()
    if BEHAVIOUR == "once":
        sys.exit(0)
    if BEHAVIOUR == "closed_input":
        os.close(0)
        time.sleep(2)
        sys.exit(0)
"""


def serving_worker(tmp_path: Path, behaviour: str) -> WorkerEnvironment:
    script = tmp_path / f"serving_{behaviour}.py"
    script.write_text(SERVING_WORKER.replace("__BEHAVIOUR__", repr(behaviour)))
    return WorkerEnvironment(python=Path(sys.executable), script=script)


def session_request(
    tmp_path: Path, source: Path, name: str, threads: int = 2
) -> ExtractionRequest:
    staging = tmp_path / name
    staging.mkdir()
    return ExtractionRequest(
        request_id=name,
        source_path=source,
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        expected_page_count=3,
        pages=None,
        profile=MineruProfile(cpu_threads=threads),
        model_dir=tmp_path / "models",
        output_dir=staging,
    )


def test_sessions_reuse_one_process_per_worker_configuration(
    tmp_path: Path, source: Path
) -> None:
    environment = serving_worker(tmp_path, "ok")
    directory = tmp_path / "sessions"
    with WorkerSessions(directory) as sessions:
        first = run_worker(
            session_request(tmp_path, source, "a"), environment, sessions=sessions
        )
        second = sessions.run(session_request(tmp_path, source, "b"), environment)
        other = sessions.run(
            session_request(tmp_path, source, "c", threads=3), environment
        )
    assert first.configuration["pid"] == second.configuration["pid"]
    assert (first.configuration["handled"], second.configuration["handled"]) == (1, 2)
    assert other.configuration["pid"] != first.configuration["pid"]
    assert str(first.configuration["home"]).startswith(str(directory / "home"))
    log = (tmp_path / "b" / "worker.stderr.log").read_text()
    assert "handling b" in log and "handling a" not in log
    assert (tmp_path / "b" / "worker.stdout.log").read_bytes() == b""


def test_a_crashed_serving_process_is_replaced(tmp_path: Path, source: Path) -> None:
    environment = serving_worker(tmp_path, "crash")
    with WorkerSessions(tmp_path / "sessions") as sessions:
        first = sessions.run(session_request(tmp_path, source, "a"), environment)
        # The crash left the request untouched, so a new process answers it,
        # and the request's log keeps both attempts.
        again = sessions.run(session_request(tmp_path, source, "b"), environment)
    assert again.configuration["pid"] != first.configuration["pid"]
    assert (tmp_path / "b" / "worker.stderr.log").read_text().count("handling b") == 2
    dying = serving_worker(tmp_path, "dies")
    with (
        WorkerSessions(tmp_path / "dying") as sessions,
        pytest.raises(WorkerProcessError, match="exited with status 4"),
    ):
        sessions.run(session_request(tmp_path, source, "c"), dying)
    assert (tmp_path / "c" / "worker.stderr.log").read_text().count("handling c") == 2
    once = serving_worker(tmp_path, "once")
    with WorkerSessions(tmp_path / "once") as sessions:
        first = sessions.run(session_request(tmp_path, source, "d"), once)
        time.sleep(0.5)  # let the stand-in exit, as it does after one request
        # The process exited after one request; the next starts a new one.
        second = sessions.run(session_request(tmp_path, source, "e"), once)
    assert second.configuration["pid"] != first.configuration["pid"]


@pytest.mark.parametrize(
    ("behaviour", "message"),
    [
        ("hang", "exceeded 1 s"),
        ("wrong_reply", "answered another request"),
    ],
)
def test_serving_failures_stop_the_process(
    tmp_path: Path,
    source: Path,
    monkeypatch: pytest.MonkeyPatch,
    behaviour: str,
    message: str,
) -> None:
    monkeypatch.setattr(paperextract.worker, "_STOP_SECONDS", 0.5)
    environment = serving_worker(tmp_path, behaviour)
    sessions = WorkerSessions(tmp_path / "sessions")
    request = session_request(tmp_path, source, "a")
    with pytest.raises(WorkerProcessError, match=message):
        sessions.run(request, environment, timeout_seconds=1)
    sessions.close()


def test_a_process_that_stopped_reading_is_replaced(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paperextract.worker, "_STOP_SECONDS", 0.5)
    environment = serving_worker(tmp_path, "closed_input")
    with WorkerSessions(tmp_path / "sessions") as sessions:
        first = sessions.run(session_request(tmp_path, source, "a"), environment)
        time.sleep(0.3)  # the stand-in closes its input after replying
        # The write fails, and a new process answers the request instead.
        second = sessions.run(session_request(tmp_path, source, "b"), environment)
    assert second.configuration["pid"] != first.configuration["pid"]
