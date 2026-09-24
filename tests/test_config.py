"""Resolve layered command-line configuration."""

import subprocess
import sys
from pathlib import Path

import pytest

import paperextract.config
from paperextract.config import (
    Configuration,
    ConfigurationError,
    checkout_root,
    cuda_worker_available,
    load_config_file,
    resolve_configuration,
    user_config_path,
)
from paperextract.describers import (
    AnthropicDescriber,
    MlxDescriber,
    OpenAICompatibleDescriber,
)
from paperextract.protocol import DoclingProfile, MarkerProfile, MineruProfile
from paperextract.worker import WorkerEnvironment

REPOSITORY = Path(__file__).resolve().parents[1]


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def resolve(
    tmp_path: Path,
    options: dict[str, object] | None = None,
    explicit: Path | None = None,
) -> Configuration:
    return resolve_configuration(
        options or {},
        explicit=explicit,
        environ={"HOME": str(tmp_path / "home")},
        cwd=tmp_path,
        package_file=tmp_path / "site" / "paperextract" / "config.py",
    )


def test_user_file_follows_xdg_with_a_home_fallback() -> None:
    assert user_config_path({"HOME": "/h"}) == Path(
        "/h/.config/paperextract/config.toml"
    )
    assert user_config_path({"HOME": "/h", "XDG_CONFIG_HOME": ""}) == Path(
        "/h/.config/paperextract/config.toml"
    )
    assert user_config_path({"HOME": "/h", "XDG_CONFIG_HOME": "/x"}) == Path(
        "/x/paperextract/config.toml"
    )


def test_checkout_is_found_only_beside_the_worker_script(tmp_path: Path) -> None:
    package = REPOSITORY / "src" / "paperextract" / "config.py"
    assert checkout_root(package) == REPOSITORY
    assert checkout_root(tmp_path / "site" / "paperextract" / "config.py") is None


def test_defaults_name_a_local_library_and_the_registry(tmp_path: Path) -> None:
    config = resolve(tmp_path)
    assert config.library == (tmp_path / "literature").resolve()
    assert config.worker_root is None
    assert config.model_dir is None
    assert config.registry is True
    assert config.offline is False
    assert config.contact is None
    assert config.timeout_seconds == 3600.0
    assert set(config.origins.values()) == {"default"}


def test_layers_resolve_option_then_explicit_then_user(tmp_path: Path) -> None:
    home = tmp_path / "home"
    write(
        home / ".config" / "paperextract" / "config.toml",
        'library = "user-lib"\n[worker]\nroot = "~/code"\ncpu_threads = 2\n'
        '[registry]\ncontact = " someone@example.org "\n',
    )
    explicit = write(
        tmp_path / "project" / "explicit.toml",
        'library = "explicit-lib"\n[worker]\ntimeout_seconds = 90\n'
        "[registry]\noffline = true\nenabled = true\n",
    )
    config = resolve_configuration(
        {"library": Path("cli-lib"), "registry.enabled": False, "worker.models": None},
        explicit=explicit,
        environ={"HOME": str(home)},
        cwd=tmp_path,
        package_file=tmp_path / "site" / "paperextract" / "config.py",
    )
    assert config.library == (tmp_path / "cli-lib").resolve()
    assert config.registry is False
    assert config.offline is True
    assert config.timeout_seconds == 90.0
    assert config.cpu_threads == 2
    assert config.contact == "someone@example.org"
    code = Path("~/code").expanduser().resolve()
    assert config.worker_root == code
    assert config.model_dir == code / "model-cache" / "mineru" / "models"
    assert config.origins == {
        "library": "option",
        "registry": "option",
        "offline": "config",
        "timeout_seconds": "config",
        "worker_root": "user",
        "model_dir": "user",
        "cpu_threads": "user",
        "contact": "user",
        "table_ocr": "default",
        "backend": "default",
        "table_check": "default",
        "docling_models": "user",
        "docling_device": "default",
        "docling_formulas": "default",
        "marker_models": "user",
        "marker_server": "user",
        "marker_mode": "default",
        "mineru_engine": "default",
        "mineru_small_models": "default",
        "mineru_concurrency": "default",
        "mineru_batch_invariant": "default",
    }
    record = config.to_dict()
    assert record["contact_configured"] is True
    assert "someone" not in str(record)


def test_relative_paths_resolve_against_the_file(tmp_path: Path) -> None:
    path = write(
        tmp_path / "conf" / "c.toml", '[worker]\nroot = "../repo"\nmodels = "m"\n'
    )
    values = load_config_file(path)
    assert values == {
        "worker.root": (tmp_path / "repo").resolve(),
        "worker.models": (tmp_path / "conf" / "m").resolve(),
    }


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("library = ", "Cannot read"),
        ('colour = "blue"\n', "unknown setting colour"),
        ("[extra]\nx = 1\n", "unknown setting extra"),
        ('worker = "x"\n', r"\[worker\] must be a table"),
        ("[worker]\ngpu = 1\n", "unknown setting worker.gpu"),
        ("[registry]\noffline = 1\n", "must be true or false"),
        ("[worker]\ntable_ocr = 1\n", "must be true or false"),
        ("[worker]\ntimeout_seconds = 0\n", "positive number"),
        ("[worker]\ntimeout_seconds = true\n", "positive number"),
        ('[worker]\ntimeout_seconds = "1"\n', "positive number"),
        ("[worker]\ncpu_threads = 1.5\n", "positive integer"),
        ('[registry]\ncontact = " "\n', "non-empty string"),
        ("library = 3\n", "non-empty string"),
    ],
)
def test_invalid_files_are_rejected(tmp_path: Path, content: str, message: str) -> None:
    path = write(tmp_path / "bad.toml", content)
    with pytest.raises(ConfigurationError, match=message):
        load_config_file(path)


def test_unreadable_files_are_configuration_errors(tmp_path: Path) -> None:
    path = tmp_path / "binary.toml"
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(ConfigurationError, match="Cannot read"):
        load_config_file(path)


def test_missing_explicit_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not found"):
        resolve(tmp_path, explicit=tmp_path / "missing.toml")


def worker_checkout(root: Path) -> Path:
    write(root / "workers" / "mineru" / "paperextract_mineru_worker.py", "")
    python = root / "workers" / "mineru" / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    (root / "model-cache" / "mineru" / "models").mkdir(parents=True)
    return root


def test_extraction_settings_check_the_setup(tmp_path: Path) -> None:
    root = worker_checkout(tmp_path / "repo")
    config = resolve(tmp_path, {"worker.root": root})
    settings = config.extraction_settings(None)
    assert settings.environment.script.name == "paperextract_mineru_worker.py"
    assert settings.model_dir == root / "model-cache" / "mineru" / "models"
    assert settings.profile.cpu_threads == 4
    assert settings.timeout_seconds == 3600.0
    assert settings.lookup is None


def test_extraction_settings_name_the_missing_step(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="No worker checkout"):
        resolve(tmp_path).extraction_settings(None)
    root = tmp_path / "repo"
    with pytest.raises(ConfigurationError, match="Worker script not found"):
        resolve(tmp_path, {"worker.root": root}).extraction_settings(None)
    write(root / "workers" / "mineru" / "paperextract_mineru_worker.py", "")
    with pytest.raises(ConfigurationError, match="uv sync --directory workers/mineru"):
        resolve(tmp_path, {"worker.root": root}).extraction_settings(None)
    python = root / "workers" / "mineru" / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    with pytest.raises(ConfigurationError, match="Model directory not found"):
        resolve(tmp_path, {"worker.root": root}).extraction_settings(None)


def test_the_package_checkout_supplies_the_default_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def checkout(_path: Path) -> Path:
        return tmp_path

    monkeypatch.setattr(paperextract.config, "checkout_root", checkout)
    config = resolve(tmp_path)
    assert config.worker_root == tmp_path
    assert config.origins["worker_root"] == "default"
    assert config.to_dict()["worker_root"] == str(tmp_path)


def test_describe_settings_default_to_the_trial_choice(tmp_path: Path) -> None:
    config = resolve(tmp_path)
    settings = config.describe
    assert (settings.backend, settings.endpoint) == (
        "openai",
        "http://127.0.0.1:8000/v1",
    )
    assert (settings.repository, settings.prompt) == (
        "Qwen/Qwen3.8-27B",
        "figure-claims-v2",
    )
    assert config.describe_model() == {
        "repository": "Qwen/Qwen3.8-27B",
        "revision": "1d4bf0f2ff60",
    }
    described = config.describer({})
    assert isinstance(described, OpenAICompatibleDescriber)
    assert described.sampling["max_tokens"] == 8192
    record = config.to_dict()["describe"]
    assert isinstance(record, dict) and record["key_file_configured"] is False


def test_paid_descriptions_need_a_cap_and_a_key(tmp_path: Path) -> None:
    key = write(tmp_path / "key", "sk-test\n")
    explicit = write(
        tmp_path / "c.toml",
        f'[describe]\nbackend = "anthropic"\nkey_file = "{key}"\nmax_usd = 2.5\n',
    )
    config = resolve(tmp_path, explicit=explicit)
    assert config.origins["describe.backend"] == "config"
    assert config.describe_model() == {
        "repository": "claude-opus-5-5",
        "revision": None,
    }
    described = config.describer({})
    assert isinstance(described, AnthropicDescriber)
    assert described.sampling == {"max_tokens": 12288, "effort": "low"}
    assert config.to_dict()["describe"]["key_file_configured"] is True  # type: ignore[index]
    uncapped = resolve(tmp_path, {"describe.max_usd": None}, explicit=explicit)
    assert uncapped.describe.max_usd == 2.5
    with pytest.raises(ConfigurationError, match="needs a spend cap"):
        resolve(tmp_path, {"describe.backend": "anthropic"}).describer({})
    with pytest.raises(ConfigurationError, match="No Anthropic API key"):
        resolve(
            tmp_path, {"describe.backend": "anthropic", "describe.max_usd": 1.0}
        ).describer({})
    with pytest.raises(ConfigurationError, match="No price is known"):
        resolve(tmp_path, {"describe.model": "claude-x"}, explicit=explicit).describer(
            {}
        )


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ('[describe]\nbackend = "gpt"\n', "must be one of openai, anthropic, mlx"),
        ('[describe]\nprompt = "v9"\n', "must be one of figure-claims-v1"),
        ("[describe]\nmax_usd = -1\n", "must be a positive number"),
    ],
)
def test_invalid_describe_settings_are_rejected(
    tmp_path: Path, content: str, message: str
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        load_config_file(write(tmp_path / "c.toml", content))


def test_the_local_describer_checks_its_setup(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    explicit = write(
        tmp_path / "c.toml",
        f'[worker]\nroot = "{root}"\n[describe]\nbackend = "mlx"\n',
    )
    with pytest.raises(ConfigurationError, match="Describe worker environment"):
        resolve(tmp_path, explicit=explicit).describer({})
    python = root / "workers" / "describe" / ".venv" / "bin" / "python"
    write(python, "")
    with pytest.raises(ConfigurationError, match="Model directory not found"):
        resolve(tmp_path, explicit=explicit).describer({})
    (root / "model-cache" / "describe" / "Qwen--Qwen3.8-27B").mkdir(parents=True)
    described = resolve(tmp_path, explicit=explicit).describer({})
    assert isinstance(described, MlxDescriber)
    assert described.model["runtime"] == "mlx-vlm"
    bare = write(tmp_path / "bare.toml", '[describe]\nbackend = "mlx"\n')
    with pytest.raises(ConfigurationError, match="No worker checkout"):
        resolve(tmp_path, explicit=bare).describer({})


def docling_checkout(root: Path) -> Path:
    write(root / "workers" / "docling" / "paperextract_docling_worker.py", "")
    python = root / "workers" / "docling" / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    (root / "model-cache" / "docling").mkdir(parents=True)
    return root


def test_docling_can_be_the_backend_or_the_table_check(tmp_path: Path) -> None:
    root = docling_checkout(worker_checkout(tmp_path / "repo"))
    config = resolve(tmp_path, {"worker.root": root, "worker.backend": "docling"})
    settings = config.extraction_settings(None)
    assert settings.environment.script.name == "paperextract_docling_worker.py"
    assert settings.model_dir == root / "model-cache" / "docling"
    assert isinstance(settings.profile, DoclingProfile)
    assert settings.profile.formulas is True
    assert settings.table_check is None
    assert config.to_dict()["backend"] == "docling"
    checked = resolve(tmp_path, {"worker.root": root, "worker.table_check": True})
    settings = checked.extraction_settings(None)
    assert isinstance(settings.profile, MineruProfile)
    assert settings.table_check is not None
    assert settings.table_check.profile.formulas is False
    assert settings.table_check.model_dir == root / "model-cache" / "docling"
    file = write(
        tmp_path / "docling.toml",
        '[docling]\ndevice = "cpu"\nformulas = false\nmodels = "m"\n',
    )
    values = load_config_file(file)
    assert values["docling.device"] == "cpu"
    assert values["docling.models"] == tmp_path / "m"
    with pytest.raises(ConfigurationError, match="must be one of auto, cpu"):
        load_config_file(write(tmp_path / "bad.toml", '[docling]\ndevice = "tpu"\n'))
    with pytest.raises(ConfigurationError, match="must be one of mineru, docling"):
        load_config_file(write(tmp_path / "bad2.toml", '[worker]\nbackend = "x"\n'))


def test_a_missing_docling_setup_names_its_step(tmp_path: Path) -> None:
    root = worker_checkout(tmp_path / "repo")
    config = resolve(tmp_path, {"worker.root": root, "worker.table_check": True})
    with pytest.raises(ConfigurationError, match="workers/docling"):
        config.extraction_settings(None)
    docling_checkout(root)
    (root / "model-cache" / "docling").rmdir()
    with pytest.raises(ConfigurationError, match=r"\[docling\] models"):
        config.extraction_settings(None)
    assert config.to_dict()["docling_models"] == str(root / "model-cache" / "docling")
    bare = resolve(tmp_path)
    assert bare.to_dict()["docling_models"] is None


def test_marker_needs_its_server_and_models(tmp_path: Path) -> None:
    root = worker_checkout(tmp_path / "repo")
    write(root / "workers" / "marker" / "paperextract_marker_worker.py", "")
    python = root / "workers" / "marker" / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    config = resolve(tmp_path, {"worker.root": root, "worker.backend": "marker"})
    with pytest.raises(ConfigurationError, match=r"\[marker\] models"):
        config.extraction_settings(None)
    (root / "model-cache" / "marker").mkdir(parents=True)
    with pytest.raises(ConfigurationError, match=r"llama\.cpp server not found"):
        config.extraction_settings(None)
    server = write(root / "bin" / "llama-server", "")
    file = write(
        tmp_path / "m.toml",
        f'[worker]\nbackend = "marker"\n[marker]\nserver = "{server}"\nmode = "fast"\n',
    )
    settings = resolve(
        tmp_path, {"worker.root": root}, explicit=file
    ).extraction_settings(None)
    assert isinstance(settings.profile, MarkerProfile)
    assert (settings.profile.server_binary, settings.profile.mode) == (server, "fast")
    record = resolve(tmp_path, {"worker.root": root}, explicit=file).to_dict()
    assert record["marker_server"] == str(server)
    assert resolve(tmp_path).to_dict()["marker_models"] is None
    with pytest.raises(ConfigurationError, match="balanced, fast"):
        load_config_file(write(tmp_path / "bad.toml", '[marker]\nmode = "slow"\n'))


def test_mineru_engine_and_small_models_are_configurable(tmp_path: Path) -> None:
    root = worker_checkout(tmp_path / "repo")
    file = write(
        tmp_path / "gpu.toml",
        '[mineru]\nengine = "vllm"\nsmall_models = "torch"\nconcurrency = 16\n'
        "batch_invariant = true\n",
    )
    settings = resolve(
        tmp_path, {"worker.root": root}, explicit=file
    ).extraction_settings(None)
    assert isinstance(settings.profile, MineruProfile)
    assert (
        settings.profile.vlm_engine,
        settings.profile.small_backend,
        settings.profile.vlm_max_concurrency,
        settings.profile.batch_invariant,
    ) == ("vllm", "torch", 16, True)
    with pytest.raises(ConfigurationError, match="llama-cpp, vllm"):
        load_config_file(write(tmp_path / "bad.toml", '[mineru]\nengine = "sglang"\n'))


def test_mineru_engine_auto_chooses_vllm_only_with_a_gpu_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = worker_checkout(tmp_path / "repo")
    settings = resolve(tmp_path, {"worker.root": root}).extraction_settings(None)
    assert isinstance(settings.profile, MineruProfile)
    assert settings.profile.vlm_engine == "llama-cpp"

    def available(_environment: WorkerEnvironment) -> bool:
        return True

    monkeypatch.setattr(paperextract.config, "cuda_worker_available", available)
    profile = resolve(tmp_path, {"worker.root": root}).extraction_settings(None).profile
    assert isinstance(profile, MineruProfile)
    assert (profile.vlm_engine, profile.small_backend, profile.vlm_max_concurrency) == (
        "vllm",
        "torch",
        16,
    )
    file = write(
        tmp_path / "own.toml", '[mineru]\nsmall_models = "onnx"\nconcurrency = 4\n'
    )
    profile = (
        resolve(tmp_path, {"worker.root": root}, explicit=file)
        .extraction_settings(None)
        .profile
    )
    assert isinstance(profile, MineruProfile)
    assert (profile.vlm_engine, profile.small_backend, profile.vlm_max_concurrency) == (
        "vllm",
        "onnx",
        4,
    )


def test_cuda_workers_need_linux_vllm_and_a_listed_gpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv = tmp_path / "venv"
    environment = WorkerEnvironment(
        python=venv / "bin" / "python", script=tmp_path / "w.py"
    )
    monkeypatch.setattr(paperextract.config.platform, "system", lambda: "Darwin")
    assert not cuda_worker_available(environment)
    monkeypatch.setattr(paperextract.config.platform, "system", lambda: "Linux")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    assert not cuda_worker_available(environment)
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES")
    assert not cuda_worker_available(environment)  # no vLLM installed
    write(venv / "lib" / "python3.12" / "site-packages" / "vllm" / "__init__.py", "")

    def nothing(_name: str) -> str | None:
        return None

    monkeypatch.setattr(paperextract.config.shutil, "which", nothing)
    assert not cuda_worker_available(environment)
    smi = write(tmp_path / "nvidia-smi", "")

    def found(_name: str) -> str | None:
        return str(smi)

    monkeypatch.setattr(paperextract.config.shutil, "which", found)
    replies: list[object] = [
        subprocess.CompletedProcess([], 0, "GPU 0: NVIDIA A40 (UUID: x)\n", ""),
        subprocess.CompletedProcess([], 9, "", "No devices were found"),
        OSError("exec format error"),
    ]

    def run(*_args: object, **_kwargs: object) -> object:
        reply = replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    monkeypatch.setattr(paperextract.config.subprocess, "run", run)
    assert cuda_worker_available(environment)
    assert not cuda_worker_available(environment)
    assert not cuda_worker_available(environment)
