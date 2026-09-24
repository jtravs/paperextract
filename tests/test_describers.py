"""Run figure describers against stand-in servers, SDK clients and workers."""

import json
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import anthropic
import pytest

import paperextract.describers
from paperextract.describers import (
    AnthropicCall,
    AnthropicDescriber,
    DescribeRequest,
    DescriberError,
    MlxDescriber,
    OpenAICompatibleDescriber,
    SdkClient,
    anthropic_key,
    urllib_transport,
)

REQUESTS = [
    DescribeRequest("fig_a", b"png-a", "Describe a"),
    DescribeRequest("fig_b", b"png-b", "Describe b"),
]


class Server:
    """Answer the OpenAI-compatible endpoints like a vLLM server."""

    def __init__(self, models: list[object] | None = None) -> None:
        self.models = [{"id": "qwen3.8-27b"}] if models is None else models
        self.bodies: list[dict[str, object]] = []
        self.fail: set[str] = set()

    def __call__(self, url: str, body: bytes | None, timeout: float) -> bytes:
        assert timeout > 0
        if url.endswith("/models"):
            assert body is None
            return json.dumps({"object": "list", "data": self.models}).encode()
        assert url == "http://127.0.0.1:8000/v1/chat/completions"
        assert body is not None
        request = json.loads(body)
        self.bodies.append(request)
        prompt = request["messages"][0]["content"][1]["text"]
        if prompt in self.fail:
            raise OSError("connection reset")
        if prompt == "garbled":
            return b"{not json"
        return json.dumps(
            {
                "choices": [
                    {
                        "message": {"content": f"answer to {prompt}"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1200, "completion_tokens": 300},
            }
        ).encode()


def served(
    server: Callable[[str, bytes | None, float], bytes], **options: object
) -> OpenAICompatibleDescriber:
    return OpenAICompatibleDescriber(
        endpoint="http://127.0.0.1:8000/v1/",
        repository="Qwen/Qwen3.8-27B",
        revision="1d4bf0f2ff60",
        transport=server,
        **options,  # type: ignore[arg-type]
    )


def test_a_server_describes_figures_in_order() -> None:
    server = Server()
    describer = served(server, concurrency=2)
    first, second = describer.describe(REQUESTS)
    assert (
        first.text == "answer to Describe a" and second.text == "answer to Describe b"
    )
    assert (first.prompt_tokens, first.generation_tokens, first.finish_reason) == (
        1200,
        300,
        "stop",
    )
    assert describer.model == {
        "repository": "Qwen/Qwen3.8-27B",
        "revision": "1d4bf0f2ff60",
        "runtime": "openai-compatible",
        "served": "qwen3.8-27b",
        "endpoint": "http://127.0.0.1:8000",
        "label": "Qwen3.8-27B",
    }
    assert describer.sampling["json_output"] is True
    body = server.bodies[0]
    assert body["model"] == "qwen3.8-27b"
    assert body["response_format"] == {"type": "json_object"}
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert json.dumps(body["messages"]).count('"url": "data:image/png;base64,') == 1


def test_server_failures_are_per_figure_or_up_front() -> None:
    server = Server()
    server.fail.add("Describe a")
    describer = served(server, served_model="given", json_output=False, label="Q")
    failed, garbled = describer.describe(
        [REQUESTS[0], DescribeRequest("fig_g", b"g", "garbled")]
    )
    assert failed.error == "OSError: connection reset" and failed.text == ""
    assert garbled.error is not None and garbled.error.startswith("JSONDecodeError")
    assert "response_format" not in server.bodies[0]
    assert describer.model["served"] == "given"
    describer.connect()
    assert describer.model["served"] == "given"
    with pytest.raises(DescriberError, match="serves no model"):
        served(Server(models=[])).describe(REQUESTS)

    def down(_url: str, _body: bytes | None, _timeout: float) -> bytes:
        raise OSError("refused")

    with pytest.raises(
        DescriberError, match=r"No model server at http://127\.0\.0\.1:8000"
    ):
        served(down).connect()  # type: ignore[arg-type]


def test_endpoint_provenance_drops_credentials() -> None:
    describer = OpenAICompatibleDescriber(
        endpoint="https://user:secret@gpu.example.org/v1?token=x",
        repository="org/model",
        revision=None,
    )
    assert describer.model["endpoint"] == "https://gpu.example.org"


def test_the_standard_transport_posts_json(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[urllib.request.Request, float]] = []

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def read(self) -> bytes:
            return b"reply"

    def urlopen(request: urllib.request.Request, timeout: float) -> Response:
        seen.append((request, timeout))
        return Response()

    monkeypatch.setattr(paperextract.describers.urllib.request, "urlopen", urlopen)
    assert urllib_transport("http://h/x", b"{}", 5.0) == b"reply"
    ((request, timeout),) = seen
    assert timeout == 5.0
    assert request.get_method() == "POST"
    assert request.get_header("Content-type") == "application/json"


def test_api_keys_come_from_a_file_or_the_environment(tmp_path: Path) -> None:
    key = tmp_path / "key"
    key.write_text("sk-file\n")
    assert anthropic_key(key, {}) == "sk-file"
    empty = tmp_path / "empty"
    empty.write_text("\n")
    assert anthropic_key(empty, {"ANTHROPIC_API_KEY": " sk-env "}) == "sk-env"
    assert anthropic_key(None, {"ANTHROPIC_API_KEY": "sk-env"}) == "sk-env"
    with pytest.raises(DescriberError, match="Cannot read the API key file"):
        anthropic_key(tmp_path / "missing", {})
    with pytest.raises(DescriberError, match="No Anthropic API key"):
        anthropic_key(None, {})


class FakeClient:
    """Stand in for the SDK wrapper with fixed token counts."""

    def __init__(self, counted: int = 2000, output: int = 1500) -> None:
        self.counted = counted
        self.output = output
        self.created: list[tuple[str, int, str]] = []
        self.raise_on: str | None = None

    def count(self, model: str, png: bytes, prompt: str) -> int:
        assert model.startswith("claude-") and prompt
        assert png.startswith(b"png")
        return self.counted

    def create(
        self, model: str, png: bytes, prompt: str, *, max_tokens: int, effort: str
    ) -> AnthropicCall:
        assert png.startswith(b"png")
        if prompt == self.raise_on:
            raise RuntimeError("overloaded")
        self.created.append((model, max_tokens, effort))
        return AnthropicCall(
            f"claude on {prompt}", 2100, self.output, "end_turn", "req_1"
        )


def test_paid_calls_are_capped_metered_and_ledgered(tmp_path: Path) -> None:
    ledger = tmp_path / "spend.jsonl"
    client = FakeClient()
    # Worst case per figure: 2000 * $4 + 12288 * $20 per million = $0.2538.
    describer = AnthropicDescriber(client=client, max_usd=0.28, ledger=ledger)
    first, second = describer.describe(REQUESTS)
    assert first.text == "claude on Describe a"
    assert first.usd == pytest.approx(0.0384)  # 2100 * 4 + 1500 * 20 per million
    assert (first.prompt_tokens, first.generation_tokens, first.finish_reason) == (
        2100,
        1500,
        "end_turn",
    )
    assert second.error is not None and second.error.startswith(
        "spend cap: $0.0384 spent"
    )
    assert describer.spent == pytest.approx(0.0384)
    assert client.created == [("claude-opus-5-5", 12288, "low")]
    (entry,) = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert entry["figure_id"] == "fig_a" and entry["request_id"] == "req_1"
    assert "sk-" not in ledger.read_text()
    assert describer.model == {
        "repository": "claude-opus-5-5",
        "revision": None,
        "runtime": "anthropic-api",
        "label": "claude-opus-5-5",
    }
    assert describer.sampling == {"max_tokens": 12288, "effort": "low"}
    client.raise_on = "Describe b"
    roomy = AnthropicDescriber(
        client=client, max_usd=5.0, ledger=ledger, max_tokens=4096
    )
    ok, failed = roomy.describe(REQUESTS)
    assert ok.error is None
    assert failed.error == "RuntimeError: overloaded"


def test_unpriced_models_and_empty_caps_are_refused(tmp_path: Path) -> None:
    with pytest.raises(DescriberError, match="No price is known for claude-x"):
        AnthropicDescriber(
            client=FakeClient(), max_usd=1.0, ledger=tmp_path / "l", model="claude-x"
        )
    with pytest.raises(DescriberError, match="must be positive"):
        AnthropicDescriber(client=FakeClient(), max_usd=0.0, ledger=tmp_path / "l")


def test_the_sdk_client_builds_image_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    class Messages:
        def count_tokens(self, **kwargs: object) -> object:
            calls.append(kwargs)
            return SimpleNamespace(input_tokens=1781)

        def create(self, **kwargs: object) -> object:
            calls.append(kwargs)
            content = [
                SimpleNamespace(type="thinking", thinking=""),
                SimpleNamespace(type="text", text="{}"),
            ]
            return SimpleNamespace(
                content=content,
                usage=SimpleNamespace(input_tokens=1781, output_tokens=1059),
                stop_reason="end_turn",
                _request_id="req_2",
            )

    class Client:
        def __init__(self, *, api_key: str, max_retries: int) -> None:
            assert (api_key, max_retries) == ("sk-test", 2)
            self.messages = Messages()

    monkeypatch.setattr(anthropic, "Anthropic", Client)
    client = SdkClient("sk-test")
    assert client.count("claude-opus-5-5", b"png", "Describe") == 1781
    call = client.create(
        "claude-opus-5-5", b"png", "Describe", max_tokens=100, effort="low"
    )
    assert call == AnthropicCall("{}", 1781, 1059, "end_turn", "req_2")
    created = calls[1]
    assert created["output_config"] == {"effort": "low"}
    assert "thinking" not in created and "temperature" not in created
    blocks = created["messages"][0]["content"]  # type: ignore[index]
    assert blocks[0]["source"]["media_type"] == "image/png"  # type: ignore[index]
    assert blocks[1] == {"type": "text", "text": "Describe"}  # type: ignore[index]


def test_a_missing_sdk_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(DescriberError, match=r"paperextract\[anthropic\]"):
        SdkClient("sk-test")


WORKER = """
import json, sys, time
request = json.load(open(sys.argv[1]))
behaviour = __BEHAVIOUR__
if behaviour == "sleep":
    time.sleep(5)
result = {"status": "completed", "failure": None, "items": []}
if behaviour == "fail":
    result = {"status": "failed", "failure": {"message": "no GPU"}, "items": []}
for item in request["items"][:1]:
    assert open(item["image"], "rb").read().startswith(b"png")
    result["items"].append({"id": item["id"], "text": "mlx " + item["prompt"],
        "seconds": 2.5, "prompt_tokens": 900, "generation_tokens": 80,
        "finish_reason": "stop"})
if behaviour != "silent":
    json.dump(result, open(request["result_path"], "w"))
"""


def mlx(tmp_path: Path, behaviour: str, timeout: float = 60.0) -> MlxDescriber:
    script = tmp_path / f"worker_{behaviour}.py"
    script.write_text(WORKER.replace("__BEHAVIOUR__", repr(behaviour)))
    return MlxDescriber(
        python=Path(sys.executable),
        script=script,
        model_dir=tmp_path / "model",
        repository="Qwen/Qwen3.8-27B",
        revision="rev",
        work_dir=tmp_path / "runs",
        timeout_seconds=timeout,
    )


def test_the_local_worker_describes_figures(tmp_path: Path) -> None:
    describer = mlx(tmp_path, "ok")
    assert describer.describe([]) == []
    first, second = describer.describe(REQUESTS)
    assert (first.text, first.seconds, first.generation_tokens) == (
        "mlx Describe a",
        2.5,
        80,
    )
    assert second.error == "worker gave no answer"
    assert describer.model["runtime"] == "mlx-vlm"
    assert describer.sampling["enable_thinking"] is False
    (run,) = (tmp_path / "runs").iterdir()
    request = json.loads((run / "request.json").read_text())
    assert request["protocol_version"] == 1
    assert request["model_dir"] == str(tmp_path / "model")


@pytest.mark.parametrize(
    ("behaviour", "timeout", "message"),
    [
        ("fail", 60.0, "worker failed: no GPU"),
        ("silent", 60.0, "FileNotFoundError"),
        ("sleep", 0.5, "TimeoutExpired"),
    ],
)
def test_local_worker_failures_reach_every_figure(
    tmp_path: Path, behaviour: str, timeout: float, message: str
) -> None:
    replies = mlx(tmp_path, behaviour, timeout).describe(REQUESTS)
    assert all(
        reply.error is not None and message in reply.error for reply in replies[1:]
    )
    assert replies[1].text == ""
