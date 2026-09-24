"""Describe figure images with a local MLX vision-language model.

Runs inside the isolated ``workers/describe`` environment. Reads a JSON request
naming a local model directory, sampling settings and a list of items (image
path and prompt), loads the model once, and writes a JSON result after every
item so a long run keeps its progress. No network access is needed: the model
directory must already hold the weights, and Hugging Face offline mode is set.
"""

import importlib.metadata
import json
import os
import platform
import sys
import time
import traceback
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

PROTOCOL_VERSION = 1


def write(path: Path, document: dict) -> None:
    """Replace the result file atomically."""
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def main() -> int:
    """Run one request and return the process status."""
    request_path = Path(sys.argv[1])
    request = json.loads(request_path.read_text())
    if request.get("protocol_version") != PROTOCOL_VERSION:
        raise SystemExit("unsupported protocol version")
    result_path = Path(request["result_path"])
    sampling = request["sampling"]
    result: dict = {
        "schema": "paperextract.describe-result",
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request["request_id"],
        "status": "running",
        "versions": {
            name: importlib.metadata.version(name)
            for name in ("mlx", "mlx-vlm", "transformers")
        },
        "platform": platform.platform(),
        "items": [],
        "failure": None,
    }
    write(result_path, result)
    try:
        # Imported here so a broken runtime is recorded in the result file.
        import mlx.core as mx  # noqa: PLC0415
        from mlx_vlm import generate, load  # noqa: PLC0415
        from mlx_vlm.prompt_utils import apply_chat_template  # noqa: PLC0415

        begin = time.perf_counter()
        model, processor = load(request["model_dir"])
        result["load_seconds"] = round(time.perf_counter() - begin, 2)
        write(result_path, result)
        for item in request["items"]:
            prompt = apply_chat_template(
                processor,
                model.config,
                item["prompt"],
                num_images=1,
                enable_thinking=sampling["enable_thinking"],
            )
            mx.random.seed(sampling["seed"])
            start = time.perf_counter()
            output = generate(
                model,
                processor,
                prompt,
                image=[item["image"]],
                max_tokens=sampling["max_tokens"],
                temperature=sampling["temperature"],
                enable_thinking=sampling["enable_thinking"],
                verbose=False,
            )
            result["items"].append(
                {
                    "id": item["id"],
                    "text": output.text,
                    "seconds": round(time.perf_counter() - start, 2),
                    "prompt_tokens": output.prompt_tokens,
                    "generation_tokens": output.generation_tokens,
                    "generation_tps": output.generation_tps,
                    "peak_memory_gb": output.peak_memory,
                    "finish_reason": output.finish_reason,
                }
            )
            write(result_path, result)
        result["status"] = "completed"
    except Exception as exc:  # recorded in the result for the caller
        result["status"] = "failed"
        result["failure"] = {
            "kind": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
    write(result_path, result)
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
