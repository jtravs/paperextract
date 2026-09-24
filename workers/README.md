# Isolated worker environments

These are the backend environments, separate from the lightweight package.
They pin the investigated backend releases and every resolved dependency in their
own `uv.lock`. The locks resolve for **macOS arm64 and Linux x86_64 with Python
3.12**; the Mac pins did not change when Linux was added. Linux runs were
qualified on NVIDIA A40 nodes of a Slurm cluster; they are not
distributable runtime bundles.

From the repository root, create one environment at a time:

```sh
uv sync --directory workers/docling --python 3.12.13 --locked
uv sync --directory workers/marker --python 3.12.13 --locked
uv sync --directory workers/mineru --python 3.12.13 --locked
uv sync --directory workers/describe --python 3.12.13 --locked
```

On a Linux CUDA host, MinerU's GPU engines come from the `cuda` extra
(`mineru[full]`: vLLM 0.28.0 and torch 2.13 with CUDA 13.0 wheels):

```sh
uv sync --directory workers/mineru --python 3.12.13 --locked --extra cuda
```

It needs only an NVIDIA driver recent enough for CUDA 13 (595.71 was used);
no system CUDA toolkit. On Linux, MinerU's packaged llama.cpp has CPU and
Vulkan builds only and ONNX Runtime is CPU-only, so the default engine runs
on the CPU there. vLLM needs the original weights
`opendatalab/MinerU2.5-Pro-2605-1.2B` and torch small models need
`opendatalab/MinerU-4_models_torch` (both Apache-2.0) in the model directory,
each with an empty `.mineru_complete` marker at its root, because MinerU only
accepts a model directory that carries it.

Use `uv run --directory workers/<name> --offline --no-sync ...` for a prepared
worker. `workers/mineru/paperextract_mineru_worker.py` is the protocol-version-1
worker script that the core adapter starts with `workers/mineru/.venv/bin/python`;
see the manual's worker boundary page. `workers/docling/paperextract_docling_worker.py` and
`workers/marker/paperextract_marker_worker.py` implement the same protocol for
Docling and Marker. `workers/describe/paperextract_describe_worker.py`
runs a local MLX vision-language model (mlx-vlm 0.7.2) for figure descriptions;
its weights come from the `describe` model set. These are independent uv
projects, not members of the core workspace.

Models are a separate, explicit setup step. `models.json` pins every model
snapshot by repository, revision and file (size and SHA-256), and the
llama.cpp server archive per platform; `paperextract models fetch SET`
downloads a set into the configured model directories under the ignored
`model-cache/`, verifying each file before it is used, and `paperextract
models status --verify` checks what is present. The sets are `mineru`
(ONNX and llama.cpp), `mineru-cuda` (torch and vLLM), `docling`, `marker`
(with the server binary) and `describe`. Extraction never downloads.

Current pins: Docling 2.129.0, Marker 2.0.0 and MinerU 4.0.5. All three resolved
and installed on an Apple silicon Mac on 22 September 2026. MinerU is selected as the first adapter to implement, with the recorded scientific
limitations; this is not a general quality/default certification. Model snapshots
and file hashes are recorded in `workers/models.json`.

Docling code is MIT; Marker code is Apache-2.0 with separately restricted model
weights; MinerU has its own Apache-based license with extra conditions. See
[model licences](../docs/models.md) for the review of the model terms. Installing these separate workers
does not change this project's license or permit redistributing their weights.

Run backends sequentially for measurements. Current Mac trials are affected by
other work on the machine. Distinguish first conversion with cached weights,
reused converter, fresh process and model download; do not call all of them
“cold” or “warm” without saying what was reused. Record load and memory context,
retain raw outputs and failures, and defer throughput rankings until quiet repeats.
