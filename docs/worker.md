# Worker boundary

Extraction backends run in their own pinned environments, never inside the
core interpreter. Three workers implement the boundary: MinerU
(`workers/mineru`), Docling (`workers/docling`) and Marker (`workers/marker`). The profile type in the
request selects the backend. The MinerU flow:

```text
coordinator (core .venv)                    worker (workers/mineru/.venv)
------------------------                    -----------------------------
inspect_pdf(preserved.pdf)  -> page count
ExtractionRequest.to_json() -> request.json
run_worker(...)             -> spawn python paperextract_mineru_worker.py
                                             load_request, verify SHA-256,
                                             count pages with PDFium,
                                             MinerUParser.parse(...).save(native/)
                                             result.json + native/ files
verify_result(...)          <- result.json
```

The request and result documents are defined in `paperextract.protocol`
(protocol version 1). The worker script `workers/mineru/paperextract_mineru_worker.py`
implements the same contract with the standard library, MinerU, PDFium and
Torch only; it never imports the core package.

The Docling worker `workers/docling/paperextract_docling_worker.py` follows
the same contract with Docling 2.129.0, PDFium and Torch. Docling converts
one contiguous page range per call, so a selection such as pages 2, 3 and 8
becomes two conversions with one converter, and the worker writes one wrapper
file, `native/docling.json` (schema `paperextract.docling-native` 1). The
wrapper holds each range's `DoclingDocument` verbatim, the files of the
picture and table crops it saved, the PDF information dictionary (Docling
does not report it) and whether the formula model ran. Its `DoclingProfile`
fixes TableFormer mode (`accurate`), cell matching against the text layer,
the formula model (on the CPU, because it failed on MPS in the P1 trial),
crops, the device for layout and table models, and CPU threads. OCR is not
configurable and stays off, because Docling's OCR engines download their own
models on first use. Its Hugging Face and cache directories are moved into
the staging directory.

The Marker worker `workers/marker/paperextract_marker_worker.py` uses
Marker 2.0.0 with Surya 0.22.1. Its `MarkerProfile` names the local
`llama-server` binary, the mode and CPU threads. The worker starts the server
on a free 127.0.0.1 port with the Surya GGUF files from the model directory,
points Marker at it with automatic starts, keep-alive and LLM services off,
points Surya's model cache at `<models>/cache` and its layout checkpoints at
`<models>/surya_layout2`, and stops the server when the request ends. The
wrapper `native/marker.json` (schema `paperextract.marker-native` 1) holds the
JSON renderer's output verbatim, the PDF information dictionary and the files
of the block images, which are also saved below `native/images/`.

## Running one request

```python
from pathlib import Path

from paperextract.ingest import preserve_pdf
from paperextract.worker import WorkerEnvironment, run_worker
from paperextract.pdf import inspect_pdf
from paperextract.protocol import ExtractionRequest, MineruProfile

staging = Path("/private/staging/req-1")  # existing, empty, private
source = preserve_pdf(Path("paper.pdf"), staging.parent / "paper.pdf")
inspection = inspect_pdf(source.stored_path)
request = ExtractionRequest(
    request_id="req-1",
    source_path=source.stored_path,
    source_sha256=source.sha256,
    expected_page_count=inspection.page_count,
    pages=None,  # or (1, 15)
    profile=MineruProfile(),  # standard tier, local llama.cpp
    model_dir=Path("model-cache/mineru/models").resolve(),
    output_dir=staging,
)
result = run_worker(
    request, WorkerEnvironment.for_repository(Path.cwd()), timeout_seconds=1800
)
```

`run_worker` writes `request.json`, captures `worker.stdout.log` and
`worker.stderr.log`, and returns an `ExtractionResult` only after
`verify_result` has checked it. The result's `status` is `completed` or
`failed`; a failed result carries the worker's exception and any partial
native files, and the caller decides how to proceed.

## What the request binds

- The preserved source path and its SHA-256. The worker re-hashes the file
  and refuses a mismatch.
- The coordinator's independent PDFium page count. The worker counts again and
  refuses a disagreement, so a substituted file cannot be processed silently.
- An explicit one-based page selection, or every page. MinerU receives an
  inclusive range string such as `1-3,15`; a subset is rewritten through PDFium
  by MinerU itself, and returned `page_idx` values keep the original numbering.
- An explicit `MineruProfile`: tier, text/OCR mode, MinerU image analysis off,
  the local vision-language engine (the packaged llama.cpp engine, or vLLM on
  a CUDA GPU), its concurrency, CPU threads and the small-model runtime (ONNX
  or torch). A remote vision-language server is not representable in the
  profile. A profile written before `small_backend` existed reads as ONNX.
- The local model directory and the staging directory.

## Process environment

The worker inherits only `PATH`, `HOME`, `TMPDIR`, `LANG`, `LC_ALL` and
`CUDA_VISIBLE_DEVICES`, so a scheduler's GPU assignment holds. The adapter
sets `MINERU_MODEL_SOURCE=local`, `MINERU_MODEL_BASE_DIR`,
`MINERU_MODEL_SMALL_BACKEND` and `MINERU_MODEL_VLM_ENGINE` from the profile,
`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `HF_HUB_DISABLE_TELEMETRY=1`,
`ORT_DISABLE_TELEMETRY=1`, `OMP_NUM_THREADS`, and points
`MINERU_HOME` into the staging directory so MinerU writes nothing beneath the
user's home. Credentials and unrelated variables never reach the worker.
With vLLM it also sets `VLLM_NO_USAGE_STATS=1` and `DO_NOT_TRACK=1`, because
vLLM otherwise reports usage statistics, `VLLM_USE_FLASHINFER_SAMPLER=0`,
because FlashInfer's sampler compiles CUDA code on first use and GPU nodes
need not have a CUDA compiler, and `VLLM_CACHE_ROOT` (inherited when set).
The worker runs in its own session; on timeout or cancellation the whole
process group is killed, including inference helpers.

`WorkerSessions` keeps a MinerU worker started with `--serve` for several
requests with the same environment: the core writes each request's path on
the process's standard input, and the worker writes one JSON line naming the
request and its status on its original standard output after writing
`result.json`; library output there is redirected to standard error. The
part of the process's standard error logged during a request becomes that
request's `worker.stderr.log`. The result is verified exactly as for a
one-request process. A process that exits before answering, having written
nothing for the request, is replaced and the request sent once more; the
request's log then holds both attempts. A second failure is reported.

ONNX Runtime 1.29 and later upload usage telemetry from macOS and Linux unless
`ORT_DISABLE_TELEMETRY=1` is set before the library initializes, according to
the ONNX Runtime 1.29.0 release notes. Calling its disable function after
import is too late. Before 22 September 2026 the worker did not set the
variable: ONNX Runtime 1.30.0 kept a device identifier and an event queue
under `~/Library/Application Support/Microsoft/DeveloperTools/.onnxruntime/`
and made at least one upload attempt from a MinerU run. The queued events held
device and process details, such as the Mac model, the operating system and
processor, and no paper content was seen in them.

## What is verified before a result is admitted

| Check | Failure |
| --- | --- |
| Result parses as protocol version 1 with exactly the defined fields | `WorkerProcessError` |
| Process exited without `result.json`, or timed out | `WorkerProcessError` |
| `request_id` and `source_sha256` equal the request | `ResultIntegrityError` |
| Worker page count equals the coordinator's count | `ResultIntegrityError` |
| Every recorded file exists, is not a symlink, resolves inside the staging directory, and matches its digest and size | `ResultIntegrityError` |
| Recorded file paths are normalized relative POSIX paths without `..` | parse error, `WorkerProcessError` |
| Coverage lists the requested pages and returns no unrequested page | `ResultIntegrityError` |
| The result's backend equals the request's | `ResultIntegrityError` |
| A completed result includes its backend's native document (`native/middle_json.json`, `native/docling.json` or `native/marker.json`) | `ResultIntegrityError` |
| A process exit status other than zero with a `completed` result | `ResultIntegrityError` |

Missing pages are **not** an integrity error. They appear in
`result.coverage.missing`, and pages returned without blocks appear in
`result.coverage.empty`, with `PAGE_NOT_RETURNED` and `PAGE_EMPTY`
diagnostics. MinerU inserts an empty placeholder page for a page PDFium cannot
render; a placeholder and a genuinely blank page look identical at this
boundary, so downstream validation must treat an empty page as unrecovered
content, not as success.

## Native output and provenance

The worker saves MinerU's own output unchanged under `native/`:
`middle_json.json` (schema `docvortex.middle` 2.0 with page blocks and
fractional bounding boxes), `markdown.md`, `structured_content.json`,
`model_output.json` and `images/`. Every file is listed in the result with
its digest. The result also records the resolved MinerU configuration with
credentials redacted, package versions, MPS availability, the model directory
listing, timings for startup, parsing and saving, and maximum resident set
sizes with their measurement limits. Normalization into the canonical
scientific schema is a later step and does not modify these files.

## Test lanes

The default offline gate exercises the boundary with a stand-in worker that
speaks the same protocol and deliberately violates it in each checked way.
The real worker is an opt-in `backend` lane:

```sh
PAPEREXTRACT_SMOKE_PDF=/path/to/paper.pdf \
uv run --offline --no-sync pytest -m backend tests/test_mineru_smoke.py
```

`PAPEREXTRACT_SMOKE_PAGES="1,15"` limits the selection and
`PAPEREXTRACT_SMOKE_OUTPUT` keeps the staging directory. The lane requires
the installed `workers/mineru` environment and the frozen local models
recorded in `workers/models.json` (`paperextract models fetch mineru`); it
performs no downloads.
