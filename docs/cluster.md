# Running on a Slurm cluster

A large batch can run on a cluster's GPU nodes without containers or
administrator rights. The login node sets up environments and models and
publishes results; the GPU jobs only extract. Replace the placeholders in
angle brackets with your site's partition, resources and paths.

## What runs where

| Step | Where | Network |
| --- | --- | --- |
| Environments, models (`paperextract models fetch`) | login node | yes |
| Duplicate check and manifest (`dedup --manifest-out`) | a short CPU job | no |
| Extraction (`batch --manifest ... --shard K/N --runs-to DIR`) | one GPU per array task | no |
| Publication (`publish DIR`) | a short CPU job or the login node | registry lookups |
| Figure descriptions (`describe`) | one GPU job with a local vLLM server | no |

GPU jobs never write to the library: each writes finished runs into its own
directory, and one publication step afterwards adds them to the library, so
concurrent jobs cannot race on its catalog.

## Requirements

- Linux x86_64 with an NVIDIA driver for CUDA 13 (driver 580 or later);
  no system CUDA toolkit is needed.
- `uv` on the login node, and shared project storage for the checkout, the
  uv cache and the models. A home directory with a small file quota is not
  enough: the environments hold hundreds of thousands of files.
- Node-local disk on GPU nodes for vLLM's compile cache is helpful.

## Setting up

On the login node, in `tmux` or `screen` so a dropped connection does not
stop it:

```sh
export UV_CACHE_DIR=<storage>/uv-cache UV_PYTHON_INSTALL_DIR=<storage>/uv-python
git clone https://github.com/jtravs/paperextract.git <storage>/paperextract
cd <storage>/paperextract
uv sync --locked --python 3.12
uv sync --directory workers/mineru --locked --extra cuda --python 3.12
.venv/bin/paperextract models fetch mineru-cuda     # 3.2 GB, verified
.venv/bin/paperextract models fetch describe        # 56 GB, optional
```

`paperextract models status --verify` hashes every file afterwards.

A configuration file for the GPU jobs, `<storage>/gpu.toml`:

```toml
[worker]
root = "<storage>/paperextract"
cpu_threads = 8
timeout_seconds = 3600
# persistent = true is the default with vllm: one MinerU process per task

[mineru]
engine = "auto"              # vllm, torch and 16 in flight on a GPU node

[registry]
offline = true               # identity is resolved when publishing
```

## Extraction jobs

Write the manifest once, from the sources and the library to extend:

```sh
paperextract dedup <papers>/ --library <library> --manifest-out <run>/papers.jsonl
```

Then one array task per GPU, each taking a stable share of the manifest:

```sh
#!/bin/bash
#SBATCH --job-name=paperextract
#SBATCH --partition=<gpu partition>
#SBATCH --array=1-4
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --signal=TERM@120
#SBATCH --output=<run>/logs/extract-%A-%a.out
set -euo pipefail
export VLLM_CACHE_ROOT=${TMPDIR:-/tmp}/paperextract-vllm-$SLURM_JOB_ID
cd <run>
<storage>/paperextract/.venv/bin/paperextract batch \
  --manifest papers.jsonl --shard "$SLURM_ARRAY_TASK_ID/$SLURM_ARRAY_TASK_COUNT" \
  --library <library> --runs-to "staged/$SLURM_ARRAY_TASK_ID" \
  --config <storage>/gpu.toml --json > "logs/extract-$SLURM_ARRAY_TASK_ID.json"
```

Each task reads the library, when it exists, only to skip papers it
already holds. Slurm's `--signal=TERM@120` stops a task two minutes before
its time limit: the paper in progress is abandoned and its worker killed,
and the papers already finished stay in `staged/`. Submitting the same array
again resumes: runs completed earlier are reported as `staged by an earlier
run` and not extracted again.

## Publishing

```sh
<storage>/paperextract/.venv/bin/paperextract publish <run>/staged/* --library <library>
```

`publish` takes run directories or directories of runs, publishes every
completed run in turn with duplicate checks and identity resolution, and
reports a run a stopped job left incomplete as `failed`. Run directories are
kept; delete them after checking the result.

## Figure descriptions

A model that needs more memory than one GPU can be served across the GPUs
of one node. For example, Qwen3.8-27B in bf16 on two 46 GB cards:

```sh
#!/bin/bash
#SBATCH --partition=<gpu partition>
#SBATCH --gres=gpu:2
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=04:00:00
set -euo pipefail
export VLLM_NO_USAGE_STATS=1 DO_NOT_TRACK=1 HF_HUB_OFFLINE=1
export VLLM_USE_FLASHINFER_SAMPLER=0 NCCL_P2P_DISABLE=1
export VLLM_CACHE_ROOT=${TMPDIR:-/tmp}/paperextract-vllm-$SLURM_JOB_ID
PORT=$(( 20000 + SLURM_JOB_ID % 20000 ))
<vllm environment>/bin/vllm serve <storage>/paperextract/model-cache/describe/Qwen--Qwen3.8-27B \
  --served-model-name qwen3.8-27b --host 127.0.0.1 --port $PORT \
  --tensor-parallel-size 2 --disable-custom-all-reduce \
  --max-model-len 32768 --gpu-memory-utilization 0.90 --max-num-seqs 16 \
  --max-num-batched-tokens 8192 --limit-mm-per-prompt '{"image": 1}' --seed 0 &
SERVER=$!
trap 'kill $SERVER' EXIT
until curl -sf http://127.0.0.1:$PORT/health >/dev/null; do sleep 10; done
<storage>/paperextract/.venv/bin/paperextract describe --all --library <library> \
  --endpoint http://127.0.0.1:$PORT/v1 --model qwen3.8-27b --config <describe.toml>
```

with `[describe] concurrency = 16` and `papers = 4` in `<describe.toml>`.
The server listens on the node's loopback address only. The vLLM
environment is separate from the workers (for example `uv venv` and
`uv pip install vllm==0.30.0`).

## Measured on A40 nodes

On 23 and 24 September 2026, on nodes with two NVIDIA A40 (46 GB) cards:

| Work | Result |
| --- | --- |
| MinerU, 15 papers, 124 pages, one A40, persistent worker | 273–283 s; 410 s batch-invariant (repeatable) |
| The same 5 papers on an M5 Max laptop, llama.cpp | 407 s against 170 s on one A40 |
| Qwen3.8-27B descriptions, two A40s, 79 figures | 1916 s one paper at a time; 672 s with four at once |
| Two array tasks, one A40 each, the 15 papers | 209 s and 329 s (5 and 10 papers), then `publish` 38 s |
| CPU-only node, llama.cpp and ONNX, 5 papers | 6992 s |

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `Could not find nvcc` when vLLM starts | FlashInfer's sampler compiles CUDA code; the worker sets `VLLM_USE_FLASHINFER_SAMPLER=0`, do the same for `vllm serve`. |
| Two-GPU `vllm serve` hangs at NCCL start-up | GPU peer-to-peer transfer does not work between the cards: set `NCCL_P2P_DISABLE=1`. |
| It then hangs while capturing CUDA graphs | Add `--disable-custom-all-reduce`. |
| `cutlass_scaled_mm_sm80` error with `--quantization fp8` | vLLM's FP8 path fails on Ampere cards; use more GPUs in bf16. |
| `Model repo ... is not ready` from MinerU | A model directory lacks its `.mineru_complete` marker; `paperextract models fetch` writes it. |
| Each paper spends half a minute starting vLLM | `[worker] persistent` was set to false; remove it. |
