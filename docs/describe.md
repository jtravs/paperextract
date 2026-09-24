# Figure descriptions

`paperextract describe` adds a machine-generated visual description to every
figure of published papers. A vision-language model reads the figure image
from the preserved PDF and answers in a fixed JSON form: axes with their
printed labels and ticks, legend entries, printed text and numbers, located
features, trends, estimated readings, relationships between panels, and what
it could not read. The published caption and the first sentence that cites
the figure are given to the model as context only.

A description is not part of the paper and is never presented as if it were.
Every record says `"machine_generated": true` and carries a notice that
readings, labels and trends may be wrong or invented. In `paper.md` the
description follows the published caption as a quoted block that begins
*Machine-generated visual description (model), not from the paper:*, between
`<!-- machine-generated description: begin -->` and `... end -->`. The search
index keeps descriptions in their own column: a hit that matches only a
description is labelled `machine-generated description:` in `search` output
and `"in_description": true` in its JSON.

## Workflow

Extraction does not describe figures. Extract a batch locally, then describe
the whole library in one session with a model server:

```sh
paperextract batch ~/incoming
paperextract describe --all --dry-run     # counts figures; contacts nothing
paperextract describe --all               # default: vLLM at 127.0.0.1:8000
```

`describe` skips every figure that already has a parsed description from the
same model repository, revision and prompt version of the same rendered
image, so it can be rerun at any time: only new papers and figures are sent.
`--force` describes again. A description from another model is added beside
earlier ones; `paper.md` shows the most recent parsed description, and every
record stays in `descriptions/<figure_id>.json`.

Each paper is rebuilt from its kept output, described, and republished under
the same journaled replacement as [`reprocess`](cli.md); a failed paper keeps
its run directory and is left unchanged. `reprocess` carries descriptions
forward. A rebuild that changes a figure's geometry changes its identifier;
the old description is then not published and `extraction.json` notes it.

## Record

`descriptions/<figure_id>.json` (schema `paperextract.figure-descriptions`,
version 1) holds a list of records (schema `paperextract.figure-description`,
version 1). Each record keeps the model's repository, revision, runtime and
server origin, the prompt version and SHA-256, the SHA-256 of the image
described, the sampling settings, token counts, time, cost of a paid call,
the raw answer, the validated claims or the parse error, and a
**printed-string check**. The check looks up every string the model claims
is printed in the PDF text layer inside the figure region, after removing the
caption's words. It is not applicable to raster or outlined figures, where
too little text remains, or to scans whose text layer is invisible OCR,
since OCR is itself a machine reading. A confirmed string shows that the text
exists in the region, not that the model placed it correctly; unconfirmed
strings are not necessarily wrong.

Images are rendered at up to 1536 pixels on the long side and 200 dpi, the
resolution of the trials. The default prompt is `figure-claims-v2`;
`figure-claims-v1` is kept for comparison.

## Backends

| Backend | Use | Cost |
| --- | --- | --- |
| `openai` (default) | An OpenAI-compatible server such as vLLM, typically on a rented GPU through an SSH tunnel. | GPU time |
| `anthropic` | Claude through the Anthropic API, for careful jobs. Needs the optional extra `paperextract[anthropic]`, a key and a spend cap. | Metered |
| `mlx` | The local MLX worker in `workers/describe` on Apple silicon. Slow; for a few papers offline. | None |

```toml
[describe]
backend = "openai"
endpoint = "http://127.0.0.1:8000/v1"
repository = "Qwen/Qwen3.8-27B"     # provenance of the served model
revision = "1d4bf0f2ff60"
concurrency = 16                    # requests in flight per paper
papers = 4                          # papers described at once
max_tokens = 8192                   # per figure
json_output = true                  # server constrains answers to JSON
prompt = "figure-claims-v2"
```

For the Anthropic backend set `backend = "anthropic"`, `max_usd` (or pass
`--max-usd`) and `key_file`, a file readable only by you that holds the key;
`ANTHROPIC_API_KEY` is used when no file is set. The key is never written to
records or logs. `model` defaults to `claude-opus-5-5` at `effort = "low"`;
`claude-opus-5` and `claude-sonnet-5` are also priced. Before every figure the
spend so far plus that figure's worst case (counted input tokens and the full
output limit) must fit under the cap, or the figure and the rest of the run
are refused. Every paid call is appended to
`.paperextract/describe-spend.jsonl` with its tokens, cost and request
identifier.

For `mlx`, create the worker environment with
`uv sync --directory workers/describe --python 3.12.13 --locked` and place the
model snapshot in `model-cache/describe/<org>--<name>` (or set `model_dir`).

Options: `--backend`, `--endpoint`, `--model` (served name or Anthropic model),
`--max-usd`, `--force`, `--dry-run`, `--all`, `--json`, `--strict`.

## A GPU session

Measured on 23 September 2026 on one rented H100 80 GB with vLLM 0.30.0 (see
[trials](trials.md)): Qwen3.8-27B in bf16 described figures for about
$0.003–0.01 of GPU time each at $4.41 per hour when 16 were in flight;
startup, model download and idle time come on top. Claude Opus 5.5 at low
effort cost $0.04–0.06 per figure. On the Mac the same model took about two
and a half minutes per figure.

On a Linux machine with one 80 GB GPU and drivers installed:

```sh
uv venv --python 3.12 venv && uv pip install --python venv/bin/python vllm ninja
PATH=$PWD/venv/bin:$PATH HF_HUB_DISABLE_TELEMETRY=1 VLLM_NO_USAGE_STATS=1 \
  hf download Qwen/Qwen3.8-27B --revision 1d4bf0f2ff60 --local-dir qwen27b
PATH=$PWD/venv/bin:$PATH VLLM_NO_USAGE_STATS=1 venv/bin/vllm serve qwen27b \
  --served-model-name qwen3.8-27b --host 127.0.0.1 --port 8000 \
  --max-model-len 32768 --gpu-memory-utilization 0.85 --max-num-seqs 16 \
  --max-num-batched-tokens 8192 --limit-mm-per-prompt '{"image": 1}' \
  --seed 0 --attention-backend FLASHINFER
```

The server listens on the machine's loopback address only. From the machine
with the library:

```sh
ssh -f -N -L 8000:127.0.0.1:8000 user@gpu-host
paperextract describe --all
```

Figures are sent only to that server; results are written locally. Shut the
GPU machine down when the run ends. With vLLM 0.30.0 on that image, the
default FlashAttention-3 kernel failed at start-up and FlashInfer needed
`ninja` on `PATH`.

## Current limits

- `extract` and `batch` do not describe figures; run `describe` afterwards.
- Model quality was judged on 19 figures of three papers by one reviewer;
  the printed-string check could be applied to only one of them. Treat every
  description as a lead to check against the figure, not as data.
- Figures without geometry are not described. A figure whose image changes
  gets a new description; the old record is kept in the file but no longer
  shown.
- Rented GPUs are not provisioned by the package.
