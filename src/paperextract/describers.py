"""Run vision-language models that describe figure images.

Three runtimes share one interface. An OpenAI-compatible server, such as vLLM
on a rented GPU reached through an SSH tunnel, is the standard choice; the
Anthropic API is an opt-in paid choice with a hard spend cap; the local MLX
worker runs offline on Apple silicon. Each describer turns requests into
replies without interpreting them: parsing, checking and recording belong to
:mod:`paperextract.describe_stage`.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from paperextract.fields import dump, integer, items, mapping, string

__all__ = [
    "ANTHROPIC_PRICES",
    "DEFAULT_ANTHROPIC_MODEL",
    "AnthropicCall",
    "AnthropicClient",
    "AnthropicDescriber",
    "DescribeRequest",
    "Describer",
    "DescriberError",
    "MlxDescriber",
    "ModelReply",
    "OpenAICompatibleDescriber",
    "SdkClient",
    "Transport",
    "anthropic_key",
    "urllib_transport",
]

# USD per million tokens (input, output), Anthropic first-party rates.
ANTHROPIC_PRICES: Mapping[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
}
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"
_MLX_PROTOCOL_VERSION = 1

Transport = Callable[[str, bytes | None, float], bytes]
"""Send a request and return the response body.

Arguments are the URL, a JSON body (None for GET) and a timeout in seconds.
"""


class DescriberError(RuntimeError):
    """Report a describer that cannot run at all, before any figure."""


@dataclass(frozen=True)
class DescribeRequest:
    """Ask for one figure's description.

    Attributes
    ----------
    figure_id : str
        Canonical figure identifier.
    png : bytes
        Rendered figure image.
    prompt : str
        Complete instruction text.
    """

    figure_id: str
    png: bytes
    prompt: str


@dataclass(frozen=True)
class ModelReply:
    """Hold one model response, or why there is none.

    Attributes
    ----------
    text : str
        Unmodified answer text, without separated reasoning.
    seconds : float
        Request wall time.
    prompt_tokens : int
        Input tokens including the image.
    generation_tokens : int
        Output tokens.
    finish_reason : str or None
        Why generation stopped.
    usd : float or None
        Metered cost of a paid call.
    error : str or None
        Failure of this request; the text is then empty.
    """

    text: str
    seconds: float
    prompt_tokens: int = 0
    generation_tokens: int = 0
    finish_reason: str | None = None
    usd: float | None = None
    error: str | None = None


class Describer(Protocol):
    """Describe figure images with one model and fixed settings."""

    @property
    def model(self) -> Mapping[str, object]:
        """Provenance of the model: repository, revision, runtime and label."""
        ...

    @property
    def sampling(self) -> Mapping[str, object]:
        """Generation settings recorded with every description."""
        ...

    def describe(self, requests: Sequence[DescribeRequest]) -> list[ModelReply]:
        """Describe figures.

        Parameters
        ----------
        requests : Sequence of DescribeRequest
            Figures.

        Returns
        -------
        list of ModelReply
            One reply per request, in request order.
        """
        ...


def urllib_transport(url: str, body: bytes | None, timeout: float) -> bytes:
    """Send an HTTP request with the standard library.

    Parameters
    ----------
    url : str
        Target URL.
    body : bytes or None
        JSON body for POST; None sends GET.
    timeout : float
        Seconds before the request fails.

    Returns
    -------
    bytes
        Response body.

    Raises
    ------
    OSError
        The connection failed or the server returned an HTTP error.
    """
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return bytes(response.read())


def _origin(endpoint: str) -> str:
    """Reduce an endpoint to scheme, host and port for provenance.

    Parameters
    ----------
    endpoint : str
        Base URL, which may carry credentials or query parameters.

    Returns
    -------
    str
        ``scheme://host:port`` without credentials, path or query.
    """
    parts = urllib.parse.urlsplit(endpoint)
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{parts.hostname or ''}{port}"


class OpenAICompatibleDescriber:
    """Describe figures through an OpenAI-compatible chat server such as vLLM.

    Parameters
    ----------
    endpoint : str
        API base URL, such as ``http://127.0.0.1:8000/v1``.
    repository : str
        Model repository recorded as provenance, such as ``Qwen/Qwen3.8-27B``.
    revision : str or None
        Model revision the server loaded.
    served_model : str or None
        Model name the server expects; None asks the server.
    concurrency : int
        Requests in flight at once; the server batches them.
    max_tokens : int
        Output token limit per figure.
    json_output : bool
        Ask the server to constrain the answer to valid JSON.
    timeout_seconds : float
        Limit for one request.
    transport : Transport or None
        HTTP function; None uses :func:`urllib_transport`.
    label : str or None
        Short model name for rendered descriptions.
    """

    def __init__(  # noqa: PLR0913 - each setting is independent and keyword-only
        self,
        *,
        endpoint: str,
        repository: str,
        revision: str | None,
        served_model: str | None = None,
        concurrency: int = 16,
        max_tokens: int = 4096,
        json_output: bool = True,
        timeout_seconds: float = 3600.0,
        transport: Transport | None = None,
        label: str | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._repository = repository
        self._revision = revision
        self._served = served_model
        self._concurrency = concurrency
        self._max_tokens = max_tokens
        self._json_output = json_output
        self._timeout = timeout_seconds
        self._transport = transport or urllib_transport
        self._label = label or repository.rsplit("/", 1)[-1]

    @property
    def model(self) -> Mapping[str, object]:
        """Provenance of the served model.

        Returns
        -------
        Mapping of str to object
            Repository, revision, runtime, served name, endpoint origin and
            label.
        """
        return {
            "repository": self._repository,
            "revision": self._revision,
            "runtime": "openai-compatible",
            "served": self._served,
            "endpoint": _origin(self._endpoint),
            "label": self._label,
        }

    @property
    def sampling(self) -> Mapping[str, object]:
        """Generation settings.

        Returns
        -------
        Mapping of str to object
            Greedy decoding without reasoning.
        """
        return {
            "temperature": 0.0,
            "seed": 0,
            "max_tokens": self._max_tokens,
            "enable_thinking": False,
            "json_output": self._json_output,
        }

    def connect(self) -> None:
        """Check the server and learn its model name when none was given.

        Raises
        ------
        DescriberError
            The server cannot be reached or lists no model.
        """
        try:
            listing = mapping(
                json.loads(self._transport(f"{self._endpoint}/models", None, 30.0))
            )
            models = [mapping(item) for item in items(listing.get("data"))]
        except (OSError, ValueError) as exc:
            raise DescriberError(
                f"No model server at {_origin(self._endpoint)}: {exc}"
            ) from exc
        if not models:
            raise DescriberError(f"{_origin(self._endpoint)} serves no model")
        if self._served is None:
            self._served = string(models[0]["id"])

    def _one(self, request: DescribeRequest) -> ModelReply:
        """Describe one figure.

        Parameters
        ----------
        request : DescribeRequest
            Figure request.

        Returns
        -------
        ModelReply
            Answer, or the request's failure.
        """
        image = base64.standard_b64encode(request.png).decode("ascii")
        body: dict[str, object] = {
            "model": self._served,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{image}"},
                        },
                        {"type": "text", "text": request.prompt},
                    ],
                }
            ],
            "temperature": 0.0,
            "seed": 0,
            "max_tokens": self._max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if self._json_output:
            body["response_format"] = {"type": "json_object"}
        start = time.perf_counter()
        try:
            reply = mapping(
                json.loads(
                    self._transport(
                        f"{self._endpoint}/chat/completions",
                        json.dumps(body).encode(),
                        self._timeout,
                    )
                )
            )
            choice = mapping(items(reply["choices"])[0])
            message = mapping(choice["message"])
            usage = mapping(reply["usage"])
            return ModelReply(
                text=str(message.get("content") or ""),
                seconds=round(time.perf_counter() - start, 2),
                prompt_tokens=integer(usage["prompt_tokens"], minimum=0),
                generation_tokens=integer(usage["completion_tokens"], minimum=0),
                finish_reason=None
                if choice.get("finish_reason") is None
                else str(choice["finish_reason"]),
            )
        except (OSError, ValueError, KeyError, IndexError) as exc:
            return ModelReply(
                text="",
                seconds=round(time.perf_counter() - start, 2),
                error=f"{type(exc).__name__}: {exc}",
            )

    def describe(self, requests: Sequence[DescribeRequest]) -> list[ModelReply]:
        """Describe figures concurrently.

        Parameters
        ----------
        requests : Sequence of DescribeRequest
            Figures.

        Returns
        -------
        list of ModelReply
            One reply per request, in order.

        Raises
        ------
        DescriberError
            The server cannot be reached.
        """
        if self._served is None:
            self.connect()
        with ThreadPoolExecutor(max_workers=self._concurrency) as pool:
            return list(pool.map(self._one, requests))


@dataclass(frozen=True)
class AnthropicCall:
    """Hold the parts of one Anthropic response that a description needs.

    Attributes
    ----------
    text : str
        Concatenated text blocks.
    input_tokens : int
        Billed input tokens.
    output_tokens : int
        Billed output tokens, including reasoning.
    stop_reason : str or None
        Why generation stopped.
    request_id : str or None
        Request identifier for support.
    """

    text: str
    input_tokens: int
    output_tokens: int
    stop_reason: str | None
    request_id: str | None


class AnthropicClient(Protocol):
    """Count and send figure requests to the Anthropic API."""

    def count(self, model: str, png: bytes, prompt: str) -> int:
        """Count a request's input tokens without generating.

        Parameters
        ----------
        model : str
            Model identifier.
        png : bytes
            Figure image.
        prompt : str
            Instruction text.

        Returns
        -------
        int
            Input tokens.
        """
        ...

    def create(
        self, model: str, png: bytes, prompt: str, *, max_tokens: int, effort: str
    ) -> AnthropicCall:
        """Send one request.

        Parameters
        ----------
        model : str
            Model identifier.
        png : bytes
            Figure image.
        prompt : str
            Instruction text.
        max_tokens : int
            Output limit, including reasoning.
        effort : str
            Reasoning effort.

        Returns
        -------
        AnthropicCall
            Text, usage and identifiers.
        """
        ...


def anthropic_key(key_file: Path | None, environ: Mapping[str, str]) -> str:
    """Read the Anthropic API key without recording it anywhere.

    Parameters
    ----------
    key_file : Path or None
        File that holds only the key, readable by the user.
    environ : Mapping of str to str
        Process environment, consulted for ``ANTHROPIC_API_KEY``.

    Returns
    -------
    str
        The key.

    Raises
    ------
    DescriberError
        No key is available.
    """
    if key_file is not None:
        try:
            key = key_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise DescriberError(f"Cannot read the API key file: {exc}") from exc
        if key:
            return key
    key = environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise DescriberError(
            "No Anthropic API key: set [describe] key_file or ANTHROPIC_API_KEY"
        )
    return key


class SdkClient:
    """Reach the Anthropic API through the official SDK.

    Parameters
    ----------
    api_key : str
        API key; it stays inside the SDK client.

    Raises
    ------
    DescriberError
        The optional ``anthropic`` package is not installed.
    """

    def __init__(self, api_key: str) -> None:
        try:
            import anthropic  # noqa: PLC0415 - optional extra, imported on use
        except ImportError as exc:
            raise DescriberError(
                "The Anthropic backend needs the optional extra: "
                "install paperextract[anthropic]"
            ) from exc
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=2)

    @staticmethod
    def _content(png: bytes, prompt: str) -> list[dict[str, object]]:
        """Build the user content blocks.

        Parameters
        ----------
        png : bytes
            Figure image.
        prompt : str
            Instruction text.

        Returns
        -------
        list of dict
            Image block followed by the text block.
        """
        data = base64.standard_b64encode(png).decode("ascii")
        return [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": data},
            },
            {"type": "text", "text": prompt},
        ]

    def count(self, model: str, png: bytes, prompt: str) -> int:
        """Count the input tokens of a request.

        Parameters
        ----------
        model : str
            Model identifier.
        png : bytes
            Figure image.
        prompt : str
            Instruction text.

        Returns
        -------
        int
            Input tokens.
        """
        counted = self._client.messages.count_tokens(
            model=model,
            messages=[{"role": "user", "content": self._content(png, prompt)}],  # pyright: ignore[reportArgumentType] - blocks match the SDK's TypedDicts
        )
        return counted.input_tokens

    def create(
        self, model: str, png: bytes, prompt: str, *, max_tokens: int, effort: str
    ) -> AnthropicCall:
        """Send one request.

        Parameters
        ----------
        model : str
            Model identifier.
        png : bytes
            Figure image.
        prompt : str
            Instruction text.
        max_tokens : int
            Output limit, including reasoning.
        effort : str
            Reasoning effort, such as ``low``.

        Returns
        -------
        AnthropicCall
            Text, usage and identifiers.
        """
        response = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": self._content(png, prompt)}],  # pyright: ignore[reportArgumentType] - blocks match the SDK's TypedDicts
            output_config={"effort": effort},  # pyright: ignore[reportArgumentType] - effort values are validated by the API
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        return AnthropicCall(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=response.stop_reason,
            request_id=response._request_id,  # pyright: ignore[reportPrivateUsage] - documented public attribute despite the underscore
        )


class AnthropicDescriber:
    """Describe figures with a Claude model under a hard spend cap.

    Before each request the actual spend of the run plus the request's worst
    case (counted input, the full output limit) must fit under the cap;
    otherwise that figure and the rest are refused. Every paid call is
    appended to a ledger.

    Parameters
    ----------
    client : AnthropicClient
        API access, usually :class:`SdkClient`.
    max_usd : float
        Spend cap for this describer's lifetime.
    ledger : Path
        JSON Lines file that receives one entry per paid call.
    model : str
        Model identifier from :data:`ANTHROPIC_PRICES`.
    max_tokens : int
        Output limit per figure, including reasoning.
    effort : str
        Reasoning effort.

    Raises
    ------
    DescriberError
        The model has no known price or the cap is not positive.
    """

    def __init__(
        self,
        *,
        client: AnthropicClient,
        max_usd: float,
        ledger: Path,
        model: str = DEFAULT_ANTHROPIC_MODEL,
        max_tokens: int = 12288,
        effort: str = "low",
    ) -> None:
        if model not in ANTHROPIC_PRICES:
            known = ", ".join(sorted(ANTHROPIC_PRICES))
            raise DescriberError(f"No price is known for {model}; use one of {known}")
        if max_usd <= 0:
            raise DescriberError("The spend cap must be positive")
        self._client = client
        self._max_usd = max_usd
        self._ledger = ledger
        self._model = model
        self._max_tokens = max_tokens
        self._effort = effort
        self._spent = 0.0
        self._lock = threading.Lock()

    @property
    def spent(self) -> float:
        """Return the metered spend so far.

        Returns
        -------
        float
            US dollars.
        """
        return self._spent

    @property
    def model(self) -> Mapping[str, object]:
        """Provenance of the model.

        Returns
        -------
        Mapping of str to object
            Model identifier as repository, runtime and label.
        """
        return {
            "repository": self._model,
            "revision": None,
            "runtime": "anthropic-api",
            "label": self._model,
        }

    @property
    def sampling(self) -> Mapping[str, object]:
        """Generation settings.

        Returns
        -------
        Mapping of str to object
            Output limit and effort; these models take no temperature.
        """
        return {"max_tokens": self._max_tokens, "effort": self._effort}

    def _cost(self, input_tokens: int, output_tokens: int) -> float:
        """Price a request.

        Parameters
        ----------
        input_tokens : int
            Input tokens.
        output_tokens : int
            Output tokens.

        Returns
        -------
        float
            US dollars.
        """
        price_in, price_out = ANTHROPIC_PRICES[self._model]
        return (input_tokens * price_in + output_tokens * price_out) / 1e6

    def _record(
        self, request: DescribeRequest, call: AnthropicCall, usd: float
    ) -> None:
        """Append one paid call to the ledger.

        Parameters
        ----------
        request : DescribeRequest
            Figure request.
        call : AnthropicCall
            Response usage.
        usd : float
            Metered cost.
        """
        entry = {
            "utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "model": self._model,
            "figure_id": request.figure_id,
            "input_tokens": call.input_tokens,
            "output_tokens": call.output_tokens,
            "usd": round(usd, 6),
            "request_id": call.request_id,
        }
        self._ledger.parent.mkdir(parents=True, exist_ok=True)
        with self._ledger.open("a", encoding="utf-8") as ledger:
            ledger.write(json.dumps(entry, sort_keys=True) + "\n")

    def _one(self, request: DescribeRequest) -> ModelReply:
        """Describe one figure if the cap allows it.

        Parameters
        ----------
        request : DescribeRequest
            Figure request.

        Returns
        -------
        ModelReply
            Answer, a refusal at the cap, or the request's failure.
        """
        start = time.perf_counter()
        try:
            counted = self._client.count(self._model, request.png, request.prompt)
            worst = self._cost(counted, self._max_tokens)
            if self._spent + worst > self._max_usd:
                return ModelReply(
                    text="",
                    seconds=0.0,
                    error=f"spend cap: ${self._spent:.4f} spent, this figure could "
                    f"cost ${worst:.4f}, cap ${self._max_usd:.2f}",
                )
            call = self._client.create(
                self._model,
                request.png,
                request.prompt,
                max_tokens=self._max_tokens,
                effort=self._effort,
            )
        except Exception as exc:
            return ModelReply(
                text="",
                seconds=round(time.perf_counter() - start, 2),
                error=f"{type(exc).__name__}: {exc}",
            )
        usd = self._cost(call.input_tokens, call.output_tokens)
        with self._lock:
            self._spent += usd
            self._record(request, call, usd)
        return ModelReply(
            text=call.text,
            seconds=round(time.perf_counter() - start, 2),
            prompt_tokens=call.input_tokens,
            generation_tokens=call.output_tokens,
            finish_reason=call.stop_reason,
            usd=round(usd, 6),
        )

    def describe(self, requests: Sequence[DescribeRequest]) -> list[ModelReply]:
        """Describe figures one at a time, so the cap is exact.

        Parameters
        ----------
        requests : Sequence of DescribeRequest
            Figures.

        Returns
        -------
        list of ModelReply
            One reply per request, in order.
        """
        return [self._one(request) for request in requests]


class MlxDescriber:
    """Describe figures with the local MLX worker on Apple silicon.

    Parameters
    ----------
    python : Path
        Interpreter of the ``workers/describe`` environment.
    script : Path
        Worker script.
    model_dir : Path
        Downloaded model snapshot.
    repository : str
        Model repository recorded as provenance.
    revision : str or None
        Snapshot revision.
    work_dir : Path
        Directory for the images, request and result of each call.
    max_tokens : int
        Output token limit per figure.
    timeout_seconds : float
        Limit for one worker run.
    """

    def __init__(  # noqa: PLR0913 - each setting is independent and keyword-only
        self,
        *,
        python: Path,
        script: Path,
        model_dir: Path,
        repository: str,
        revision: str | None,
        work_dir: Path,
        max_tokens: int = 4096,
        timeout_seconds: float = 3600.0,
    ) -> None:
        self._python = python
        self._script = script
        self._model_dir = model_dir
        self._repository = repository
        self._revision = revision
        self._work_dir = work_dir
        self._max_tokens = max_tokens
        self._timeout = timeout_seconds

    @property
    def model(self) -> Mapping[str, object]:
        """Provenance of the local model.

        Returns
        -------
        Mapping of str to object
            Repository, revision, runtime and label.
        """
        return {
            "repository": self._repository,
            "revision": self._revision,
            "runtime": "mlx-vlm",
            "label": self._repository.rsplit("/", 1)[-1],
        }

    @property
    def sampling(self) -> Mapping[str, object]:
        """Generation settings.

        Returns
        -------
        Mapping of str to object
            Greedy decoding without reasoning.
        """
        return {
            "temperature": 0.0,
            "seed": 0,
            "max_tokens": self._max_tokens,
            "enable_thinking": False,
        }

    def describe(self, requests: Sequence[DescribeRequest]) -> list[ModelReply]:
        """Describe figures in one worker run, loading the model once.

        Parameters
        ----------
        requests : Sequence of DescribeRequest
            Figures.

        Returns
        -------
        list of ModelReply
            One reply per request, in order; every reply carries the failure
            when the worker could not run.
        """
        if not requests:
            return []
        stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ")
        run = self._work_dir / f"mlx-{stamp}"
        (run / "images").mkdir(parents=True)
        entries: list[dict[str, object]] = []
        for request in requests:
            image = run / "images" / f"{request.figure_id}.png"
            image.write_bytes(request.png)
            entries.append(
                {"id": request.figure_id, "image": str(image), "prompt": request.prompt}
            )
        result_path = run / "result.json"
        request_path = run / "request.json"
        request_path.write_text(
            dump(
                {
                    "protocol_version": _MLX_PROTOCOL_VERSION,
                    "request_id": run.name,
                    "model_dir": str(self._model_dir),
                    "result_path": str(result_path),
                    "sampling": dict(self.sampling),
                    "items": entries,
                }
            )
        )
        failure: str | None = None
        try:
            subprocess.run(
                [str(self._python), str(self._script), str(request_path)],
                capture_output=True,
                timeout=self._timeout,
                check=False,
            )
            result = mapping(json.loads(result_path.read_text()))
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            failure = f"{type(exc).__name__}: {exc}"
            result = {}
        if failure is None and result.get("failure") is not None:
            failure = f"worker failed: {mapping(result['failure']).get('message')}"
        answers = {
            string(item["id"]): item
            for item in (mapping(entry) for entry in items(result.get("items", [])))
        }
        replies: list[ModelReply] = []
        for request in requests:
            answer = answers.get(request.figure_id)
            if answer is None:
                replies.append(
                    ModelReply(
                        text="", seconds=0.0, error=failure or "worker gave no answer"
                    )
                )
                continue
            replies.append(
                ModelReply(
                    text=str(answer.get("text") or ""),
                    seconds=float(str(answer.get("seconds", 0.0))),
                    prompt_tokens=integer(answer.get("prompt_tokens", 0), minimum=0),
                    generation_tokens=integer(
                        answer.get("generation_tokens", 0), minimum=0
                    ),
                    finish_reason=None
                    if answer.get("finish_reason") is None
                    else str(answer["finish_reason"]),
                )
            )
        return replies
