"""Run an isolated extraction worker and admit only verified results.

Each backend executes in its own pinned environment as a subprocess, one
process per request by default. A MinerU worker can instead serve several
requests in one process (:class:`WorkerSessions`), so models and inference
engines load once per batch. This module writes the request, launches the
process with a minimal environment that forces local models and offline
operation, and then checks the result file for integrity before anything
downstream reads native output.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import queue
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import IO

from paperextract.protocol import (
    REQUEST_FILENAME,
    RESULT_FILENAME,
    Backend,
    ExtractionRequest,
    ExtractionResult,
    MarkerProfile,
    MineruProfile,
    native_document_path,
)

__all__ = [
    "ResultIntegrityError",
    "WorkerEnvironment",
    "WorkerError",
    "WorkerProcessError",
    "WorkerSessions",
    "require_empty_directory",
    "run_worker",
    "verify_result",
    "worker_environment_variables",
]

logger = logging.getLogger(__name__)

STDOUT_LOG = "worker.stdout.log"
STDERR_LOG = "worker.stderr.log"
SERVE_ARGUMENT = "--serve"
# CUDA_VISIBLE_DEVICES keeps the GPU a scheduler such as Slurm assigned.
_INHERITED_VARIABLES = (
    "PATH",
    "HOME",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "CUDA_VISIBLE_DEVICES",
)
_LOG_TAIL_CHARACTERS = 2000
# Grace period for a serving process to exit after its input closes.
_STOP_SECONDS = 30


class WorkerError(RuntimeError):
    """Base class for failures of the worker boundary."""


class WorkerProcessError(WorkerError):
    """Report a worker process that produced no readable result."""


class ResultIntegrityError(WorkerError):
    """Report a result that violates the request or the staging contract."""


@dataclass(frozen=True)
class WorkerEnvironment:
    """Locate the pinned interpreter and entry script of one worker.

    Attributes
    ----------
    python : Path
        Interpreter inside the separately locked worker environment.
    script : Path
        Standalone worker script executed with that interpreter.
    """

    python: Path
    script: Path

    @classmethod
    def for_repository(
        cls, root: Path, backend: Backend = "mineru"
    ) -> WorkerEnvironment:
        """Point at a worker checked out beneath a repository root.

        Parameters
        ----------
        root : Path
            Repository root containing ``workers/<backend>``.
        backend : str
            Worker family.

        Returns
        -------
        WorkerEnvironment
            Conventional interpreter and script locations; not checked to exist.
        """
        worker = root / "workers" / backend
        return cls(
            python=worker / ".venv" / "bin" / "python",
            script=worker / f"paperextract_{backend}_worker.py",
        )


def worker_environment_variables(
    request: ExtractionRequest, home: Path | None = None
) -> dict[str, str]:
    """Build the minimal process environment for one worker run.

    Parameters
    ----------
    request : ExtractionRequest
        Request whose model directory, staging directory and profile decide
        the backend settings.
    home : Path or None
        Directory for the backend's home and cache directories instead of
        the request's staging directory, for a process that serves several
        requests.

    Returns
    -------
    dict of str to str
        Variables for the subprocess. Only a few inherited variables survive;
        credentials and unrelated configuration are never passed through.

    Notes
    -----
    MinerU reads ``MINERU_*`` variables into its configuration model. The
    values force the local model directory and the profile's small-model
    backend and VLM engine, and move MinerU's home directory into the
    staging directory so nothing is written beneath the user's home; with
    vLLM they also switch off its usage statistics. Docling
    and Marker receive their models through the request; their Hugging Face
    and cache directories move into the staging directory, and Marker's
    Surya model cache points at the local model directory. Hugging Face offline flags
    stop any download attempt from becoming a hidden network dependency
    during extraction, and the telemetry switches of Hugging Face and ONNX
    Runtime stop usage reports leaving the machine.
    """
    base = request.output_dir if home is None else home
    variables = {
        name: os.environ[name] for name in _INHERITED_VARIABLES if name in os.environ
    }
    variables.update(
        {
            "PYTHONUNBUFFERED": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            # ONNX Runtime 1.29+ uploads usage telemetry from macOS and Linux
            # unless this is set before the library initializes.
            "ORT_DISABLE_TELEMETRY": "1",
            "OMP_NUM_THREADS": str(request.profile.cpu_threads),
        }
    )
    profile = request.profile
    if isinstance(profile, MineruProfile):
        variables.update(
            {
                "MINERU_HOME": str(base / "mineru-home"),
                "MINERU_MODEL_BASE_DIR": str(request.model_dir),
                "MINERU_MODEL_SOURCE": "local",
                "MINERU_MODEL_SMALL_BACKEND": profile.small_backend,
                "MINERU_MODEL_VLM_ENGINE": profile.vlm_engine,
                "MINERU_LOG_LEVEL": "info",
            }
        )
        if profile.vlm_engine == "vllm":
            variables.update(
                {
                    # vLLM reports usage statistics unless told not to.
                    "VLLM_NO_USAGE_STATS": "1",
                    "DO_NOT_TRACK": "1",
                    # A job may share one compile cache between its papers,
                    # such as on node-local disk; otherwise each run has its own.
                    "VLLM_CACHE_ROOT": os.environ.get(
                        "VLLM_CACHE_ROOT",
                        str(base / "mineru-home" / "vllm"),
                    ),
                    # FlashInfer's sampler compiles CUDA code on first use,
                    # which needs a CUDA toolkit that GPU nodes need not have.
                    "VLLM_USE_FLASHINFER_SAMPLER": "0",
                    "VLLM_BATCH_INVARIANT": "1" if profile.batch_invariant else "0",
                }
            )
    elif isinstance(profile, MarkerProfile):
        cache_home = base / "marker-home"
        variables.update(
            {
                "HF_HOME": str(cache_home / "huggingface"),
                "XDG_CACHE_HOME": str(cache_home / "cache"),
                "TORCH_HOME": str(cache_home / "torch"),
                "MODEL_CACHE_DIR": str(request.model_dir / "cache"),
            }
        )
    else:
        cache_home = base / "docling-home"
        variables.update(
            {
                "HF_HOME": str(cache_home / "huggingface"),
                "XDG_CACHE_HOME": str(cache_home / "cache"),
                "TORCH_HOME": str(cache_home / "torch"),
            }
        )
    return variables


def require_empty_directory(path: Path) -> None:
    """Require an existing, empty staging directory owned by the caller.

    Parameters
    ----------
    path : Path
        Candidate staging directory.

    Raises
    ------
    FileNotFoundError
        The directory does not exist.
    FileExistsError
        The directory already contains entries.
    """
    if not path.is_dir():
        raise FileNotFoundError(f"Staging directory does not exist: {path}")
    if any(path.iterdir()):
        raise FileExistsError(f"Staging directory is not empty: {path}")


def _tail(path: Path) -> str:
    """Read the end of a log file for an error message.

    Parameters
    ----------
    path : Path
        Log file created before the worker process started.

    Returns
    -------
    str
        Final characters of the log.
    """
    return path.read_text(errors="replace")[-_LOG_TAIL_CHARACTERS:]


def _launch(
    request: ExtractionRequest,
    environment: WorkerEnvironment,
    timeout_seconds: float | None,
) -> int:
    """Run the worker process to completion or kill its process group.

    Parameters
    ----------
    request : ExtractionRequest
        Request already written to the staging directory.
    environment : WorkerEnvironment
        Interpreter and script to execute.
    timeout_seconds : float or None
        Wall-clock budget, or None to wait indefinitely.

    Returns
    -------
    int
        Process exit status.
    """
    command = [str(environment.python), str(environment.script), REQUEST_FILENAME]
    stdout_path = request.output_dir / STDOUT_LOG
    stderr_path = request.output_dir / STDERR_LOG
    with (
        stdout_path.open("wb") as stdout,
        stderr_path.open("wb") as stderr,
        subprocess.Popen(
            command,
            cwd=request.output_dir,
            env=worker_environment_variables(request),
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        ) as process,
    ):
        try:
            return process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            # The worker may have spawned inference subprocesses; the new
            # session lets the whole group be stopped, not only the parent.
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise WorkerProcessError(
                f"Worker {request.request_id} exceeded {timeout_seconds} s and "
                "was killed"
            ) from None
        except BaseException:
            # Cancellation (Ctrl-C, SIGTERM mapped to KeyboardInterrupt) does
            # not reach a worker in its own session, and Popen's exit handler
            # does not kill on KeyboardInterrupt; stop the group explicitly.
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise


def run_worker(
    request: ExtractionRequest,
    environment: WorkerEnvironment,
    *,
    timeout_seconds: float | None = None,
    sessions: WorkerSessions | None = None,
) -> ExtractionResult:
    """Execute one request in the worker process and verify its result.

    Parameters
    ----------
    request : ExtractionRequest
        Validated request; its staging directory must exist and be empty.
    environment : WorkerEnvironment
        Pinned interpreter and worker script.
    timeout_seconds : float or None
        Wall-clock budget for the whole process, or for this request when a
        serving process handles it.
    sessions : WorkerSessions or None
        Serving processes to reuse; None starts a process for this request.

    Returns
    -------
    ExtractionResult
        Verified result, which may still report ``failed`` status when the
        backend could not process the source; the caller decides what to do.

    Raises
    ------
    FileNotFoundError
        The staging directory does not exist.
    FileExistsError
        The staging directory already contains files.
    WorkerProcessError
        The process timed out, crashed without a result, or wrote a result
        that does not parse as protocol version 1.
    ResultIntegrityError
        The result disagrees with the request or references unsafe, missing
        or altered files.

    Notes
    -----
    Standard output and error are captured to ``worker.stdout.log`` and
    ``worker.stderr.log`` in the staging directory. Missing pages are not an
    integrity error: they are reported through the result's coverage so that
    downstream validation can mark the document partial.
    """
    if sessions is not None:
        return sessions.run(request, environment, timeout_seconds=timeout_seconds)
    require_empty_directory(request.output_dir)
    (request.output_dir / REQUEST_FILENAME).write_text(request.to_json())
    logger.info(
        "Starting worker %s for request %s", environment.script.name, request.request_id
    )
    returncode = _launch(request, environment, timeout_seconds)
    return _admit(request, returncode)


def _admit(request: ExtractionRequest, returncode: int) -> ExtractionResult:
    """Read and verify the result a worker wrote for one request.

    Parameters
    ----------
    request : ExtractionRequest
        Request the worker answered.
    returncode : int
        Exit status of a one-request process, or the status a serving
        process reported for this request.

    Returns
    -------
    ExtractionResult
        Verified result.

    Raises
    ------
    WorkerProcessError
        No result was written, or it does not parse.
    ResultIntegrityError
        The result disagrees with the request or its files.
    """
    result_path = request.output_dir / RESULT_FILENAME
    if not result_path.is_file():
        raise WorkerProcessError(
            f"Worker exited with status {returncode} without writing "
            f"{RESULT_FILENAME}; stderr tail:\n{_tail(request.output_dir / STDERR_LOG)}"
        )
    try:
        result = ExtractionResult.from_json(result_path.read_text())
    except (ValueError, UnicodeDecodeError) as exc:
        raise WorkerProcessError(f"Unreadable worker result: {exc}") from exc
    verify_result(request, result)
    if returncode != 0 and result.status == "completed":
        raise ResultIntegrityError(
            f"Worker exited with status {returncode} but reported completion"
        )
    logger.info(
        "Worker request %s %s with %d files",
        request.request_id,
        result.status,
        len(result.files),
    )
    return result


class _ProcessGoneError(WorkerProcessError):
    """Report a serving process that exited before answering a request."""


@dataclass
class _Session:
    """Hold one serving worker process and its log files."""

    process: subprocess.Popen[bytes]
    stdin: IO[bytes]
    replies: queue.Queue[str | None]
    reader: threading.Thread
    stderr: Path
    handled: int = 0


class WorkerSessions:
    """Serve requests through one worker process per worker configuration.

    A request is sent to the process started for the same interpreter,
    script and environment, and a new process is started for a different
    one. Each request still gets its own staging directory, request and
    result files and ``worker.stderr.log``, and its result is verified as
    for a one-request process. A process that crashes or exceeds a request's
    time budget is killed; the next request starts a new one.

    Parameters
    ----------
    directory : Path
        Directory for the processes' home, cache and log directories; it is
        created when missing. Use the object as a context manager, or call
        :meth:`close`, so the processes stop.
    """

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._sessions: dict[tuple[object, ...], _Session] = {}

    def __enter__(self) -> WorkerSessions:
        """Return the sessions for use in a ``with`` block.

        Returns
        -------
        WorkerSessions
            This object.
        """
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop every serving process when the block ends.

        Parameters
        ----------
        kind : type or None
            Exception type, if any.
        value : BaseException or None
            Exception, if any.
        traceback : TracebackType or None
            Traceback, if any.
        """
        self.close()

    def close(self) -> None:
        """Stop every serving process."""
        for session in self._sessions.values():
            _stop(session)
        self._sessions.clear()

    def run(
        self,
        request: ExtractionRequest,
        environment: WorkerEnvironment,
        *,
        timeout_seconds: float | None = None,
    ) -> ExtractionResult:
        """Execute one request in a serving process and verify its result.

        Parameters
        ----------
        request : ExtractionRequest
            Validated request; its staging directory must exist and be empty.
        environment : WorkerEnvironment
            Pinned interpreter and worker script, which must support
            ``--serve``.
        timeout_seconds : float or None
            Wall-clock budget for this request.

        Returns
        -------
        ExtractionResult
            Verified result.

        Raises
        ------
        WorkerProcessError
            The process timed out, crashed, or wrote no readable result.
        ResultIntegrityError
            The result disagrees with the request or its files.
        """
        require_empty_directory(request.output_dir)
        request_path = request.output_dir / REQUEST_FILENAME
        request_path.write_text(request.to_json())
        home = self._directory / "home"
        variables = worker_environment_variables(request, home)
        key = (environment.python, environment.script, *sorted(variables.items()))
        # A process can exit between requests yet still look alive, or crash
        # before touching the request; then the request is sent once more to
        # a new process, and its log keeps both attempts.
        logs: list[bytes] = []
        attempt = 0
        while True:
            attempt += 1
            session = self._session(key, environment, variables)
            logger.info(
                "Sending request %s to serving worker %s (request %d of this process)",
                request.request_id,
                environment.script.name,
                session.handled + 1,
            )
            offset = session.stderr.stat().st_size
            try:
                status = self._await(session, request_path, timeout_seconds)
            except BaseException as exc:
                _stop(session)
                del self._sessions[key]
                logs.append(_log_since(session.stderr, offset))
                untouched = [p.name for p in request.output_dir.iterdir()] == [
                    REQUEST_FILENAME
                ]
                if attempt == 1 and isinstance(exc, _ProcessGoneError) and untouched:
                    logger.warning("%s; starting a new serving worker", exc)
                    continue
                _write_logs(request, logs)
                raise
            logs.append(_log_since(session.stderr, offset))
            _write_logs(request, logs)
            session.handled += 1
            return _admit(request, status)

    def _session(
        self,
        key: tuple[object, ...],
        environment: WorkerEnvironment,
        variables: dict[str, str],
    ) -> _Session:
        """Return the live process for a configuration, starting one if needed.

        Parameters
        ----------
        key : tuple
            Interpreter, script and environment.
        environment : WorkerEnvironment
            Interpreter and script.
        variables : dict of str to str
            Process environment.

        Returns
        -------
        _Session
            A running process.
        """
        session = self._sessions.get(key)
        if session is not None and session.process.poll() is not None:
            _stop(session)
            session = None
        if session is None:
            session = self._start(environment, variables, len(self._sessions))
            self._sessions[key] = session
        return session

    def _start(
        self,
        environment: WorkerEnvironment,
        variables: dict[str, str],
        index: int,
    ) -> _Session:
        """Launch a serving process.

        Parameters
        ----------
        environment : WorkerEnvironment
            Interpreter and script.
        variables : dict of str to str
            Process environment.
        index : int
            Number of processes started before, for the log directory name.

        Returns
        -------
        _Session
            The running process.
        """
        logs = self._directory / f"process-{index + 1:02d}-{time.time_ns()}"
        logs.mkdir(parents=True)
        stderr = logs / STDERR_LOG
        with stderr.open("wb") as handle:
            process = subprocess.Popen(
                [str(environment.python), str(environment.script), SERVE_ARGUMENT],
                cwd=logs,
                env=variables,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=handle,
                start_new_session=True,
            )
        replies: queue.Queue[str | None] = queue.Queue()
        stdin, stdout = process.stdin, process.stdout
        assert stdin is not None and stdout is not None  # set by PIPE
        reader = threading.Thread(
            target=_read_replies, args=(stdout, replies), daemon=True
        )
        reader.start()
        logger.info(
            "Started serving worker %s (pid %d)", environment.script.name, process.pid
        )
        return _Session(process, stdin, replies, reader, stderr)

    @staticmethod
    def _await(
        session: _Session, request_path: Path, timeout_seconds: float | None
    ) -> int:
        """Send a request and wait for the process to report it handled.

        Parameters
        ----------
        session : _Session
            Serving process.
        request_path : Path
            Request file to send.
        timeout_seconds : float or None
            Budget for this request.

        Returns
        -------
        int
            Status the worker reported: 0 completed, 1 failed, 2 unusable.

        Raises
        ------
        WorkerProcessError
            The process exited or the budget ran out first.
        """
        try:
            session.stdin.write(f"{request_path}\n".encode())
            session.stdin.flush()
        except BrokenPipeError:
            raise _ProcessGoneError(
                "Serving worker exited before the request"
            ) from None
        try:
            reply = session.replies.get(timeout=timeout_seconds)
        except queue.Empty:
            raise WorkerProcessError(
                f"Worker request {request_path.parent.name} exceeded "
                f"{timeout_seconds} s; the serving process was killed"
            ) from None
        if reply is None:
            raise _ProcessGoneError(
                f"Serving worker exited with status {session.process.wait()} "
                f"during the request; stderr tail:\n{_tail(session.stderr)}"
            )
        answer = json.loads(reply)
        if answer.get("request") != str(request_path):
            raise WorkerProcessError(
                f"Serving worker answered another request: {reply}"
            )
        return int(answer["status"])


def _read_replies(stream: IO[bytes], replies: queue.Queue[str | None]) -> None:
    """Forward a serving process's reply lines until it closes its output.

    Parameters
    ----------
    stream : IO of bytes
        The process's standard output.
    replies : queue.Queue
        Receives each line, then None at end of output.
    """
    with stream:
        for line in stream:
            replies.put(line.decode("utf-8", errors="replace").strip())
    replies.put(None)


def _stop(session: _Session) -> None:
    """End a serving process, killing its group if it does not exit.

    Parameters
    ----------
    session : _Session
        Serving process.
    """
    process = session.process
    # Closing the input asks a serving worker to exit after its request.
    with contextlib.suppress(BrokenPipeError):
        session.stdin.close()
    try:
        process.wait(timeout=_STOP_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    session.reader.join(timeout=_STOP_SECONDS)


def _log_since(source: Path, offset: int) -> bytes:
    """Read what a serving process logged after an offset.

    Parameters
    ----------
    source : Path
        The process's log.
    offset : int
        Size of the log when the request was sent.

    Returns
    -------
    bytes
        The log's new content.
    """
    with source.open("rb") as handle:
        handle.seek(offset)
        return handle.read()


def _write_logs(request: ExtractionRequest, logs: list[bytes]) -> None:
    """Write a request's log files from its attempts.

    Parameters
    ----------
    request : ExtractionRequest
        Request whose staging directory receives the logs.
    logs : list of bytes
        Standard error of each attempt, in order.
    """
    (request.output_dir / STDERR_LOG).write_bytes(b"".join(logs))
    (request.output_dir / STDOUT_LOG).touch()


def _verify_file(output_root: Path, path: str, sha256: str, size_bytes: int) -> None:
    """Check that one recorded file exists unchanged inside the staging root.

    Parameters
    ----------
    output_root : Path
        Resolved staging directory.
    path : str
        Relative path recorded by the worker.
    sha256 : str
        Recorded digest.
    size_bytes : int
        Recorded byte length.
    """
    candidate = output_root / path
    if candidate.is_symlink() or not candidate.is_file():
        raise ResultIntegrityError(f"Result file is missing or a symlink: {path}")
    if not candidate.resolve().is_relative_to(output_root):
        raise ResultIntegrityError(f"Result file escapes the staging directory: {path}")
    with candidate.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != sha256 or candidate.stat().st_size != size_bytes:
        raise ResultIntegrityError(f"Result file changed after it was recorded: {path}")


def verify_result(request: ExtractionRequest, result: ExtractionResult) -> None:
    """Check a parsed result against its request and the staging directory.

    Parameters
    ----------
    request : ExtractionRequest
        Request the result claims to answer.
    result : ExtractionResult
        Parsed result document.

    Raises
    ------
    ResultIntegrityError
        Identity, page count, coverage or file evidence disagrees with the
        request, or a recorded file is unsafe, missing or altered.
    """
    if result.request_id != request.request_id:
        raise ResultIntegrityError(
            f"Result answers request {result.request_id!r}, not {request.request_id!r}"
        )
    if result.source_sha256 != request.source_sha256:
        raise ResultIntegrityError("Result source digest differs from the request")
    if (
        result.page_count is not None
        and result.page_count != request.expected_page_count
    ):
        raise ResultIntegrityError(
            f"Worker counted {result.page_count} pages; the coordinator counted "
            f"{request.expected_page_count}"
        )
    output_root = request.output_dir.resolve()
    for item in result.files:
        _verify_file(output_root, item.path, item.sha256, item.size_bytes)
    if result.coverage is not None:
        if result.coverage.requested != request.resolved_pages():
            raise ResultIntegrityError(
                "Result coverage lists different requested pages"
            )
        if result.coverage.unexpected:
            raise ResultIntegrityError(
                f"Result returned pages that were not requested: "
                f"{list(result.coverage.unexpected)}"
            )
    if result.backend != request.backend:
        raise ResultIntegrityError(
            f"Result comes from {result.backend}, not {request.backend}"
        )
    native = native_document_path(request.backend)
    if result.status == "completed" and native not in {
        item.path for item in result.files
    }:
        raise ResultIntegrityError(f"Completed result lacks {native}")
