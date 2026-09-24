"""Resolve command-line configuration from layered TOML files and options.

Values resolve in the order of Plan §17: command-line options, then an explicit
``--config`` file, then the user file, then versioned defaults. Every resolved
value remembers which layer supplied it, so a run can show where its library or
model directory came from.

Example user file, ``~/.config/paperextract/config.toml``::

    library = "~/papers/optics"

    [worker]
    root = "~/code/paperextract"
    backend = "mineru"                  # mineru or docling
    timeout_seconds = 3600
    table_ocr = true
    table_check = false                 # cross-check tables with Docling

    [docling]
    models = "~/code/paperextract/model-cache/docling"
    device = "auto"                     # auto, cpu, mps or cuda
    formulas = true

    [marker]
    models = "~/code/paperextract/model-cache/marker"
    server = "~/bin/llama-server"       # default: the pinned llama.cpp release
    mode = "balanced"                   # balanced or fast

    [registry]
    contact = "name@example.org"

    [describe]
    backend = "openai"                  # openai, anthropic or mlx
    endpoint = "http://127.0.0.1:8000/v1"
    max_usd = 5.0                       # required for anthropic
    key_file = "~/.config/paperextract/anthropic-key"  # never logged
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from paperextract.describe import PROMPT_VERSION, PROMPT_VERSIONS
from paperextract.describers import (
    DEFAULT_ANTHROPIC_MODEL,
    AnthropicDescriber,
    Describer,
    DescriberError,
    MlxDescriber,
    OpenAICompatibleDescriber,
    SdkClient,
    anthropic_key,
)
from paperextract.fields import mapping
from paperextract.pipeline import ExtractionSettings, TableCheckSettings
from paperextract.protocol import (
    Backend,
    Device,
    DoclingProfile,
    MarkerMode,
    MarkerProfile,
    MineruProfile,
    Profile,
    SmallBackend,
)
from paperextract.registry import Lookup
from paperextract.worker import WorkerEnvironment

__all__ = [
    "CONFIG_FILENAME",
    "DEFAULT_LIBRARY",
    "DESCRIBE_BACKENDS",
    "EXTRACTION_BACKENDS",
    "SPEND_LEDGER",
    "Configuration",
    "ConfigurationError",
    "DescribeSettings",
    "checkout_root",
    "cuda_worker_available",
    "load_config_file",
    "resolve_configuration",
    "user_config_path",
]

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "config.toml"
DEFAULT_LIBRARY = "literature"
EngineChoice = Literal["auto", "llama-cpp", "vllm"]
# VLM requests in flight when an automatic choice picks vLLM.
_GPU_CONCURRENCY = 16
_DEFAULT_TIMEOUT_SECONDS = 3600.0
_DEFAULT_CPU_THREADS = 4
_MODEL_SUBDIRECTORY = Path("model-cache") / "mineru" / "models"
_DOCLING_MODELS = Path("model-cache") / "docling"
_MARKER_MODELS = Path("model-cache") / "marker"
# The llama.cpp release recorded in workers/models.json.
_MARKER_SERVER = (
    Path("model-cache") / "llama.cpp" / "b10964" / "llama-b10964" / "llama-server"
)
EXTRACTION_BACKENDS = ("mineru", "docling", "marker")
_MARKER_MODES = ("balanced", "fast")
_DEVICES = ("auto", "cpu", "mps", "cuda")
_WORKER_SCRIPT = Path("workers") / "mineru" / "paperextract_mineru_worker.py"
DESCRIBE_BACKENDS = ("openai", "anthropic", "mlx")
# The P3 trial's choice: Qwen3.8-27B served by vLLM, reached through a tunnel.
_DEFAULT_ENDPOINT = "http://127.0.0.1:8000/v1"
_DEFAULT_REPOSITORY = "Qwen/Qwen3.8-27B"
_DEFAULT_REVISION = "1d4bf0f2ff60"
_DEFAULT_CONCURRENCY = 16
_DEFAULT_SERVER_TOKENS = 8192
# Opus 5.5 always reasons; its reasoning counts against the output limit.
_DEFAULT_ANTHROPIC_TOKENS = 12288
_DESCRIBE_WORKER = Path("workers") / "describe"
_DESCRIBE_MODELS = Path("model-cache") / "describe"
SPEND_LEDGER = Path(".paperextract") / "describe-spend.jsonl"

# Accepted keys per table and the value kind each one must have.
_SCHEMA: dict[str, dict[str, str]] = {
    "": {"library": "path"},
    "worker": {
        "root": "path",
        "models": "path",
        "timeout_seconds": "seconds",
        "cpu_threads": "count",
        "table_ocr": "flag",
        "backend": "extractor",
        "table_check": "flag",
        "persistent": "flag",
    },
    "docling": {"models": "path", "device": "device", "formulas": "flag"},
    "marker": {"models": "path", "server": "path", "mode": "marker_mode"},
    "mineru": {
        "engine": "vlm_engine",
        "small_models": "small_backend",
        "concurrency": "count",
        "batch_invariant": "flag",
    },
    "registry": {"enabled": "flag", "offline": "flag", "contact": "text"},
    "describe": {
        "backend": "backend",
        "endpoint": "text",
        "model": "text",
        "repository": "text",
        "revision": "text",
        "concurrency": "count",
        "max_tokens": "count",
        "json_output": "flag",
        "prompt": "prompt",
        "max_usd": "amount",
        "effort": "text",
        "key_file": "path",
        "model_dir": "path",
        "timeout_seconds": "seconds",
        "papers": "count",
    },
}


def cuda_worker_available(environment: WorkerEnvironment) -> bool:
    """Tell whether a MinerU worker could run vLLM on a CUDA GPU here.

    Parameters
    ----------
    environment : WorkerEnvironment
        The MinerU worker environment.

    Returns
    -------
    bool
        True on Linux when the environment has vLLM (the ``cuda`` extra),
        ``CUDA_VISIBLE_DEVICES`` does not hide every GPU, and ``nvidia-smi``
        lists a GPU.
    """
    if platform.system() != "Linux" or os.environ.get("CUDA_VISIBLE_DEVICES") == "":
        return False
    venv = environment.python.parent.parent
    if not any(venv.glob("lib/python3*/site-packages/vllm/__init__.py")):
        return False
    smi = shutil.which("nvidia-smi")
    if smi is None:
        return False
    try:
        listed = subprocess.run(  # fixed arguments
            [smi, "-L"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return listed.returncode == 0 and "GPU " in listed.stdout


class ConfigurationError(ValueError):
    """Report an invalid configuration file, option or missing setup step."""


@dataclass(frozen=True)
class DescribeSettings:
    """Hold the figure-description settings.

    Attributes
    ----------
    backend : str
        ``openai`` for an OpenAI-compatible server such as vLLM, ``anthropic``
        for the paid API, or ``mlx`` for the local worker.
    endpoint : str
        Base URL of the OpenAI-compatible server.
    model : str or None
        Served model name, Anthropic model identifier, or None for the
        backend's default.
    repository : str
        Model repository recorded as provenance for the server or worker.
    revision : str or None
        Model revision recorded as provenance.
    concurrency : int
        Requests in flight at once on the server.
    max_tokens : int or None
        Output limit per figure, or None for the backend's default.
    json_output : bool
        Ask the server to constrain answers to valid JSON.
    prompt : str
        Prompt template version.
    max_usd : float or None
        Spend cap for one paid run; the Anthropic backend requires it.
    effort : str
        Anthropic reasoning effort.
    key_file : Path or None
        File holding the Anthropic API key.
    model_dir : Path or None
        Local model snapshot for the MLX worker.
    timeout_seconds : float
        Limit for one request or worker run.
    papers : int
        Papers described at once, each with up to ``concurrency`` requests,
        so a server stays busy while one paper's last figures finish (four
        at once were 2.9 times faster than one on two A40s); the MLX worker
        always takes one paper at a time.
    """

    backend: str = "openai"
    endpoint: str = _DEFAULT_ENDPOINT
    model: str | None = None
    repository: str = _DEFAULT_REPOSITORY
    revision: str | None = _DEFAULT_REVISION
    concurrency: int = _DEFAULT_CONCURRENCY
    max_tokens: int | None = None
    json_output: bool = True
    prompt: str = PROMPT_VERSION
    max_usd: float | None = None
    effort: str = "low"
    key_file: Path | None = None
    model_dir: Path | None = None
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    papers: int = 4

    def to_dict(self) -> dict[str, object]:
        """Serialize the settings for run records.

        Returns
        -------
        dict of str to object
            JSON-compatible fields; the key file is reduced to whether one is
            configured.
        """
        return {
            "backend": self.backend,
            "endpoint": self.endpoint,
            "model": self.model,
            "repository": self.repository,
            "revision": self.revision,
            "concurrency": self.concurrency,
            "max_tokens": self.max_tokens,
            "json_output": self.json_output,
            "prompt": self.prompt,
            "max_usd": self.max_usd,
            "effort": self.effort,
            "key_file_configured": self.key_file is not None,
            "model_dir": None if self.model_dir is None else str(self.model_dir),
            "timeout_seconds": self.timeout_seconds,
            "papers": self.papers,
        }


@dataclass(frozen=True)
class Configuration:
    """Hold every resolved setting with the layer that supplied it.

    Attributes
    ----------
    library : Path
        Absolute library root.
    worker_root : Path or None
        Repository checkout that holds ``workers/mineru``, when known.
    model_dir : Path or None
        Absolute MinerU model directory, when known.
    timeout_seconds : float
        Worker wall-clock budget per paper.
    cpu_threads : int
        CPU thread budget handed to the worker.
    table_ocr : bool
        Re-extract tables whose text layer lost glyphs with OCR, keeping the
        OCR body only when its numbers agree with the text layer.
    registry : bool
        Whether identity resolution consults metadata registries at all.
    offline : bool
        Use registry snapshots only, without network requests.
    contact : str or None
        Polite-pool contact address for Crossref, set only by configuration.
    describe : DescribeSettings
        Figure-description settings.
    backend : str
        Extraction backend, ``mineru`` or ``docling``.
    table_check : bool
        Cross-check MinerU's tables with an independent Docling reading.
    docling_models : Path or None
        Absolute Docling model directory, when known.
    docling_device : str
        Accelerator for Docling's layout and table models.
    docling_formulas : bool
        Whether Docling transcribes formulas when it is the backend.
    marker_models : Path or None
        Absolute Marker model directory, when known.
    marker_server : Path or None
        Absolute path of the llama.cpp server Marker's worker starts.
    marker_mode : str
        Marker converter mode.
    mineru_engine : str
        MinerU's local VLM engine: ``llama-cpp``, ``vllm``, or ``auto``,
        which chooses vLLM on a machine with a CUDA GPU and the worker's
        ``cuda`` extra.
    mineru_small_models : str
        Runtime of MinerU's small models, ``onnx`` or ``torch``.
    mineru_concurrency : int
        Simultaneous requests to MinerU's local VLM engine.
    mineru_batch_invariant : bool
        Ask vLLM for batch-invariant, repeatable results.
    persistent : bool or None
        Serve all of a batch's MinerU requests through one worker process
        per configuration instead of one process per request; None, the
        default, does so when MinerU runs vLLM, whose engine takes half a
        minute to start.
    origins : Mapping of str to str
        Layer name for each field: ``option``, ``config``, ``user`` or
        ``default``.
    """

    library: Path
    worker_root: Path | None
    model_dir: Path | None
    timeout_seconds: float
    cpu_threads: int
    table_ocr: bool
    registry: bool
    offline: bool
    contact: str | None
    origins: Mapping[str, str]
    describe: DescribeSettings = DescribeSettings()
    backend: Backend = "mineru"
    table_check: bool = False
    docling_models: Path | None = None
    docling_device: Device = "auto"
    docling_formulas: bool = True
    marker_models: Path | None = None
    marker_server: Path | None = None
    marker_mode: MarkerMode = "balanced"
    mineru_engine: EngineChoice = "auto"
    mineru_small_models: SmallBackend = "onnx"
    mineru_concurrency: int = 1
    mineru_batch_invariant: bool = True
    persistent: bool | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize the configuration for run records.

        Returns
        -------
        dict of str to object
            JSON-compatible fields. The contact address is replaced by whether
            one is configured, so personal details stay out of shared records.
        """
        return {
            "library": str(self.library),
            "worker_root": None if self.worker_root is None else str(self.worker_root),
            "model_dir": None if self.model_dir is None else str(self.model_dir),
            "timeout_seconds": self.timeout_seconds,
            "cpu_threads": self.cpu_threads,
            "table_ocr": self.table_ocr,
            "backend": self.backend,
            "table_check": self.table_check,
            "docling_models": None
            if self.docling_models is None
            else str(self.docling_models),
            "docling_device": self.docling_device,
            "docling_formulas": self.docling_formulas,
            "marker_models": None
            if self.marker_models is None
            else str(self.marker_models),
            "marker_server": None
            if self.marker_server is None
            else str(self.marker_server),
            "marker_mode": self.marker_mode,
            "mineru_engine": self.mineru_engine,
            "mineru_small_models": self.mineru_small_models,
            "mineru_concurrency": self.mineru_concurrency,
            "mineru_batch_invariant": self.mineru_batch_invariant,
            "persistent": self.persistent,
            "registry": self.registry,
            "offline": self.offline,
            "contact_configured": self.contact is not None,
            "describe": self.describe.to_dict(),
            "origins": dict(sorted(self.origins.items())),
        }

    def _mineru_profile(self, environment: WorkerEnvironment) -> MineruProfile:
        """Build the MinerU profile, resolving ``engine = "auto"``.

        Parameters
        ----------
        environment : WorkerEnvironment
            The MinerU worker environment, inspected for vLLM.

        Returns
        -------
        MineruProfile
            An explicit profile: with ``auto``, vLLM with torch small models
            and 16 requests in flight when the environment has vLLM and a
            CUDA GPU is visible, else llama.cpp with ONNX and one request.
            Settings given explicitly keep their values.
        """
        engine = self.mineru_engine
        small = self.mineru_small_models
        concurrency = self.mineru_concurrency
        if engine == "auto":
            gpu = cuda_worker_available(environment)
            engine = "vllm" if gpu else "llama-cpp"
            if self.origins.get("mineru_small_models", "default") == "default":
                small = "torch" if gpu else "onnx"
            if self.origins.get("mineru_concurrency", "default") == "default":
                concurrency = _GPU_CONCURRENCY if gpu else 1
            logger.info("MinerU engine auto: %s with %s small models", engine, small)
        return MineruProfile(
            cpu_threads=self.cpu_threads,
            vlm_engine=engine,
            small_backend=small,
            vlm_max_concurrency=concurrency,
            batch_invariant=self.mineru_batch_invariant,
        )

    def model_roots(self) -> dict[str, Path | None]:
        """Return each backend's model directory.

        Returns
        -------
        dict of str to Path or None
            ``mineru``, ``docling``, ``marker`` and ``describe`` directories,
            None where no checkout or setting gives one. The describe
            directory holds one snapshot per model.
        """
        return {
            "mineru": self.model_dir,
            "docling": self.docling_models,
            "marker": self.marker_models,
            "describe": None
            if self.worker_root is None
            else self.worker_root / _DESCRIBE_MODELS,
        }

    def describe_model(self) -> dict[str, object]:
        """Return the provenance that decides whether a figure is described.

        Returns
        -------
        dict of str to object
            Repository and revision of the configured model, available
            without contacting it.
        """
        settings = self.describe
        if settings.backend == "anthropic":
            return {
                "repository": settings.model or DEFAULT_ANTHROPIC_MODEL,
                "revision": None,
            }
        return {"repository": settings.repository, "revision": settings.revision}

    def describer(self, environ: Mapping[str, str]) -> Describer:
        """Build the configured figure describer.

        Parameters
        ----------
        environ : Mapping of str to str
            Process environment, consulted for the Anthropic API key.

        Returns
        -------
        Describer
            OpenAI-compatible, Anthropic or MLX describer.

        Raises
        ------
        ConfigurationError
            A paid run has no spend cap or key, the model has no known price,
            or the MLX worker or its model is missing.
        """
        settings = self.describe
        if settings.backend == "openai":
            return OpenAICompatibleDescriber(
                endpoint=settings.endpoint,
                repository=settings.repository,
                revision=settings.revision,
                served_model=settings.model,
                concurrency=settings.concurrency,
                max_tokens=settings.max_tokens or _DEFAULT_SERVER_TOKENS,
                json_output=settings.json_output,
                timeout_seconds=settings.timeout_seconds,
            )
        if settings.backend == "anthropic":
            if settings.max_usd is None:
                raise ConfigurationError(
                    "The Anthropic backend needs a spend cap: set [describe] "
                    "max_usd or pass --max-usd"
                )
            try:
                return AnthropicDescriber(
                    client=SdkClient(anthropic_key(settings.key_file, environ)),
                    max_usd=settings.max_usd,
                    ledger=self.library / SPEND_LEDGER,
                    model=settings.model or DEFAULT_ANTHROPIC_MODEL,
                    max_tokens=settings.max_tokens or _DEFAULT_ANTHROPIC_TOKENS,
                    effort=settings.effort,
                )
            except DescriberError as exc:
                raise ConfigurationError(str(exc)) from exc
        return self._mlx_describer()

    def _mlx_describer(self) -> MlxDescriber:
        """Build the local MLX describer after checking its setup.

        Returns
        -------
        MlxDescriber
            Describer using ``workers/describe``.

        Raises
        ------
        ConfigurationError
            The checkout, worker environment or model snapshot is missing.
        """
        settings = self.describe
        if self.worker_root is None:
            raise ConfigurationError(
                "No worker checkout is known; set [worker] root to the "
                "paperextract repository that contains workers/describe"
            )
        worker = self.worker_root / _DESCRIBE_WORKER
        python = worker / ".venv" / "bin" / "python"
        if not python.is_file():
            raise ConfigurationError(
                f"Describe worker environment not found: {python}; create it "
                "with `uv sync --directory workers/describe --python 3.12.13 --locked`"
            )
        model_dir = settings.model_dir or (
            self.worker_root / _DESCRIBE_MODELS / settings.repository.replace("/", "--")
        )
        if not model_dir.is_dir():
            raise ConfigurationError(
                f"Model directory not found: {model_dir}; download the model "
                "or set [describe] model_dir"
            )
        return MlxDescriber(
            python=python,
            script=worker / "paperextract_describe_worker.py",
            model_dir=model_dir,
            repository=settings.repository,
            revision=settings.revision,
            work_dir=self.library / ".paperextract" / "runs",
            max_tokens=settings.max_tokens or _DEFAULT_SERVER_TOKENS,
            timeout_seconds=settings.timeout_seconds,
        )

    def extraction_settings(self, lookup: Lookup | None) -> ExtractionSettings:
        """Check the worker setup and build the pipeline settings.

        Parameters
        ----------
        lookup : Callable or None
            Registry lookup for the identity stage.

        Returns
        -------
        ExtractionSettings
            Worker environment, model directory, profile and timeout.

        Raises
        ------
        ConfigurationError
            The worker checkout, its environment or the model directory is
            missing; the message names the setup step.
        """
        check = None
        if self.backend == "marker":
            environment, models = self._worker("marker")
            if self.marker_server is None or not self.marker_server.is_file():
                raise ConfigurationError(
                    f"llama.cpp server not found: {self.marker_server}; download "
                    "the release recorded in workers/models.json, or set "
                    "[marker] server"
                )
            profile: Profile = MarkerProfile(
                server_binary=self.marker_server,
                mode=self.marker_mode,
                cpu_threads=self.cpu_threads,
            )
        elif self.backend == "docling":
            environment, models = self._worker("docling")
            profile = DoclingProfile(
                formulas=self.docling_formulas,
                device=self.docling_device,
                cpu_threads=self.cpu_threads,
            )
        else:
            environment, models = self._worker("mineru")
            profile = self._mineru_profile(environment)
            if self.table_check:
                check_environment, check_models = self._worker("docling")
                check = TableCheckSettings(
                    check_environment,
                    check_models,
                    DoclingProfile(
                        formulas=False,
                        images=False,
                        device=self.docling_device,
                        cpu_threads=self.cpu_threads,
                    ),
                )
        return ExtractionSettings(
            environment=environment,
            model_dir=models,
            profile=profile,
            timeout_seconds=self.timeout_seconds,
            lookup=lookup,
            table_ocr=self.table_ocr,
            table_check=check,
        )

    def _worker(self, backend: Backend) -> tuple[WorkerEnvironment, Path]:
        """Locate and check one backend's worker environment and models.

        Parameters
        ----------
        backend : str
            ``mineru`` or ``docling``.

        Returns
        -------
        tuple
            Worker environment and model directory.

        Raises
        ------
        ConfigurationError
            The checkout, environment or models are missing.
        """
        if self.worker_root is None:
            raise ConfigurationError(
                "No worker checkout is known; set [worker] root to the "
                f"paperextract repository that contains workers/{backend}"
            )
        environment = WorkerEnvironment.for_repository(self.worker_root, backend)
        if not environment.script.is_file():
            raise ConfigurationError(f"Worker script not found: {environment.script}")
        if not environment.python.is_file():
            raise ConfigurationError(
                f"Worker environment not found: {environment.python}; create it "
                f"with `uv sync --directory workers/{backend} --python 3.12.13 "
                "--locked`"
            )
        models = {
            "mineru": self.model_dir,
            "docling": self.docling_models,
            "marker": self.marker_models,
        }[backend]
        if models is None or not models.is_dir():
            setting = (
                "[worker] models" if backend == "mineru" else f"[{backend}] models"
            )
            raise ConfigurationError(
                f"Model directory not found: {models}; download the {backend} "
                f"models recorded in workers/models.json, or set {setting}"
            )
        return environment, models


def user_config_path(environ: Mapping[str, str]) -> Path:
    """Locate the user configuration file.

    Parameters
    ----------
    environ : Mapping of str to str
        Process environment.

    Returns
    -------
    Path
        ``$XDG_CONFIG_HOME/paperextract/config.toml``, falling back to
        ``~/.config`` when the variable is unset or empty.
    """
    base = environ.get("XDG_CONFIG_HOME") or str(Path(environ["HOME"]) / ".config")
    return Path(base) / "paperextract" / CONFIG_FILENAME


def checkout_root(package_file: Path) -> Path | None:
    """Find the repository checkout that this package runs from, if any.

    Parameters
    ----------
    package_file : Path
        A file inside the ``paperextract`` package.

    Returns
    -------
    Path or None
        Root of a source checkout with the MinerU worker script, or None for
        an installed wheel, which does not carry the worker environments.
    """
    root = package_file.resolve().parents[2]
    return root if (root / _WORKER_SCRIPT).is_file() else None


def _value(kind: str, raw: object, where: str, base: Path) -> object:
    """Validate one configuration value.

    Parameters
    ----------
    kind : str
        Expected kind from the schema.
    raw : object
        Decoded TOML value.
    where : str
        Dotted key for messages.
    base : Path
        Directory against which relative paths resolve.

    Returns
    -------
    object
        Validated value; paths are absolute and user-expanded.

    Raises
    ------
    ConfigurationError
        The value has the wrong type or range.
    """
    if kind == "flag":
        if not isinstance(raw, bool):
            raise ConfigurationError(f"{where} must be true or false")
        return raw
    choices = {
        "backend": DESCRIBE_BACKENDS,
        "prompt": PROMPT_VERSIONS,
        "extractor": EXTRACTION_BACKENDS,
        "device": _DEVICES,
        "marker_mode": _MARKER_MODES,
        "vlm_engine": ("auto", "llama-cpp", "vllm"),
        "small_backend": ("onnx", "torch"),
    }.get(kind)
    if choices is not None:
        if raw not in choices:
            raise ConfigurationError(f"{where} must be one of {', '.join(choices)}")
        return raw
    if kind in {"seconds", "count", "amount"}:
        if isinstance(raw, bool) or not isinstance(raw, int | float) or raw <= 0:
            raise ConfigurationError(f"{where} must be a positive number")
        if kind == "count":
            if not isinstance(raw, int):
                raise ConfigurationError(f"{where} must be a positive integer")
            return raw
        return float(raw)
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigurationError(f"{where} must be a non-empty string")
    if kind == "text":
        return raw.strip()
    return (base / Path(raw).expanduser()).resolve()


def load_config_file(path: Path) -> dict[str, object]:
    """Read and validate one TOML configuration file.

    Parameters
    ----------
    path : Path
        Configuration file.

    Returns
    -------
    dict of str to object
        Values keyed by dotted name, such as ``worker.root``; relative paths
        resolve against the file's directory.

    Raises
    ------
    ConfigurationError
        The file is unreadable, is not TOML, or holds an unknown key or an
        invalid value.
    """
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"Cannot read configuration {path}: {exc}") from exc
    base = path.resolve().parent
    values: dict[str, object] = {}
    for key, raw in document.items():
        if key in _SCHEMA and key:
            if not isinstance(raw, dict):
                raise ConfigurationError(f"{path}: [{key}] must be a table")
            table, prefix = mapping(cast("object", raw)), f"{key}."
        else:
            table, prefix = {key: raw}, ""
        allowed = _SCHEMA[prefix.rstrip(".")]
        for name, item in table.items():
            dotted = f"{prefix}{name}"
            if name not in allowed:
                raise ConfigurationError(f"{path}: unknown setting {dotted}")
            values[dotted] = _value(allowed[name], item, f"{path}: {dotted}", base)
    return values


def resolve_configuration(
    options: Mapping[str, object],
    *,
    explicit: Path | None,
    environ: Mapping[str, str],
    cwd: Path,
    package_file: Path,
) -> Configuration:
    """Merge options, configuration files and defaults.

    Parameters
    ----------
    options : Mapping of str to object
        Command-line values keyed by dotted name; None means not given.
        Paths are resolved against ``cwd``.
    explicit : Path or None
        File named by ``--config``; it must exist.
    environ : Mapping of str to str
        Process environment, used to locate the user file.
    cwd : Path
        Working directory for relative command-line paths and the default
        library.
    package_file : Path
        A file inside the package, used to find a source checkout.

    Returns
    -------
    Configuration
        Resolved settings with their origins.

    Raises
    ------
    ConfigurationError
        A file is invalid, or the explicit file does not exist.
    """
    layers: list[tuple[str, Mapping[str, object]]] = []
    for name, value in options.items():
        if value is not None:
            given = (
                (cwd / value.expanduser()).resolve()
                if isinstance(value, Path)
                else value
            )
            layers.append(("option", {name: given}))
    if explicit is not None:
        if not explicit.is_file():
            raise ConfigurationError(f"Configuration file not found: {explicit}")
        layers.append(("config", load_config_file(explicit)))
    user = user_config_path(environ)
    if user.is_file():
        layers.append(("user", load_config_file(user)))
    root = checkout_root(package_file)
    defaults: dict[str, object] = {
        "library": (cwd / DEFAULT_LIBRARY).resolve(),
        "worker.root": root,
        "worker.timeout_seconds": _DEFAULT_TIMEOUT_SECONDS,
        "worker.cpu_threads": _DEFAULT_CPU_THREADS,
        "worker.table_ocr": True,
        "worker.backend": "mineru",
        "worker.table_check": False,
        "docling.device": "auto",
        "docling.formulas": True,
        "marker.mode": "balanced",
        "mineru.engine": "auto",
        "mineru.small_models": "onnx",
        "mineru.concurrency": 1,
        "mineru.batch_invariant": True,
        "registry.enabled": True,
        "registry.offline": False,
        "registry.contact": None,
    }
    layers.append(("default", defaults))
    resolved: dict[str, object] = {}
    origins: dict[str, str] = {}
    for origin, layer in layers:
        for key, value in layer.items():
            if key not in resolved:
                resolved[key] = value
                origins[key] = origin
    worker_root = resolved["worker.root"]
    if "worker.models" not in resolved and isinstance(worker_root, Path):
        resolved["worker.models"] = worker_root / _MODEL_SUBDIRECTORY
        origins["worker.models"] = origins["worker.root"]
    for key, relative in (
        ("docling.models", _DOCLING_MODELS),
        ("marker.models", _MARKER_MODELS),
        ("marker.server", _MARKER_SERVER),
    ):
        if key not in resolved and isinstance(worker_root, Path):
            resolved[key] = worker_root / relative
            origins[key] = origins["worker.root"]
    return _build(resolved, origins)


_FIELDS = {
    "library": "library",
    "worker.root": "worker_root",
    "worker.models": "model_dir",
    "worker.timeout_seconds": "timeout_seconds",
    "worker.cpu_threads": "cpu_threads",
    "worker.table_ocr": "table_ocr",
    "worker.backend": "backend",
    "worker.table_check": "table_check",
    "worker.persistent": "persistent",
    "docling.models": "docling_models",
    "docling.device": "docling_device",
    "docling.formulas": "docling_formulas",
    "marker.models": "marker_models",
    "marker.server": "marker_server",
    "marker.mode": "marker_mode",
    "mineru.engine": "mineru_engine",
    "mineru.small_models": "mineru_small_models",
    "mineru.concurrency": "mineru_concurrency",
    "mineru.batch_invariant": "mineru_batch_invariant",
    "registry.enabled": "registry",
    "registry.offline": "offline",
    "registry.contact": "contact",
}


def _build(resolved: Mapping[str, object], origins: Mapping[str, str]) -> Configuration:
    """Convert merged dotted values into a configuration.

    Parameters
    ----------
    resolved : Mapping of str to object
        Merged values; each layer validated its own, and the default layer
        supplies every key except the model directory.
    origins : Mapping of str to str
        Layer for each dotted key.

    Returns
    -------
    Configuration
        Typed configuration.
    """
    return Configuration(
        library=cast("Path", resolved["library"]),
        worker_root=cast("Path | None", resolved["worker.root"]),
        model_dir=cast("Path | None", resolved.get("worker.models")),
        timeout_seconds=cast("float", resolved["worker.timeout_seconds"]),
        cpu_threads=cast("int", resolved["worker.cpu_threads"]),
        table_ocr=cast("bool", resolved["worker.table_ocr"]),
        registry=cast("bool", resolved["registry.enabled"]),
        offline=cast("bool", resolved["registry.offline"]),
        contact=cast("str | None", resolved["registry.contact"]),
        backend=cast("Backend", resolved["worker.backend"]),
        table_check=cast("bool", resolved["worker.table_check"]),
        docling_models=cast("Path | None", resolved.get("docling.models")),
        docling_device=cast("Device", resolved["docling.device"]),
        docling_formulas=cast("bool", resolved["docling.formulas"]),
        marker_models=cast("Path | None", resolved.get("marker.models")),
        marker_server=cast("Path | None", resolved.get("marker.server")),
        marker_mode=cast("MarkerMode", resolved["marker.mode"]),
        mineru_engine=cast("EngineChoice", resolved["mineru.engine"]),
        mineru_small_models=cast("SmallBackend", resolved["mineru.small_models"]),
        mineru_concurrency=cast("int", resolved["mineru.concurrency"]),
        mineru_batch_invariant=cast("bool", resolved["mineru.batch_invariant"]),
        persistent=cast("bool | None", resolved.get("worker.persistent")),
        # Describe settings keep their dotted names, such as describe.backend.
        origins={_FIELDS.get(key, key): value for key, value in origins.items()},
        describe=DescribeSettings(
            **{
                key.removeprefix("describe."): value
                for key, value in resolved.items()
                if key.startswith("describe.")
            }  # pyright: ignore[reportArgumentType] - each value was validated against _SCHEMA
        ),
    )
