# Implementation status and agent handoff

**Last updated:** 23 September 2026.\
**Current milestone:** P6 (Linux/HPC) in progress since 23 September 2026:
Linux worker locks, GPU engines for MinerU, serving workers, first A40 runs;
P1 qualification and the first-slice review continue.\
**Next action:** optional review of the 17 materials papers without
described figures and the 34 unverified identities; watch the CI runner
move to Ubuntu 26 (from 19 October 2026).\
**Application state:** preservation, inspection, fingerprints, intake planning with
identical-byte, identical-text and supplement pairing, worker protocol and MinerU
adapter, canonical schema 0.2, normalization with document-level corrections,
targeted table OCR re-extraction with a numerical check, transactional publication
with supplements and cross-links, bibliographic identity through Crossref/DataCite,
the `paperextract` command line, reprocessing from kept output, identity by
corroborated Crossref search, and the library index with lookup and full-text
search are implemented and verified on real papers; machine-generated figure
descriptions (`describe`), Docling as a second backend and table cross-check,
saved-page (HTML/MHTML) cross-checks, document versions and library relations,
arXiv resolution and download, batch manifests with shards, library
layouts with `organize`, Marker as a third backend, the backend `compare`
command and library migration (`migrate`, record version guards) are
implemented.

This is the canonical progress record. Read it before work and update it at
meaningful checkpoints and before handoff/final responses. `dev/Plan.md` is
the working design; `dev/Corpus.md` records the initial corpus findings.
Do not equate a plan, inspection script or candidate annotation with an implemented
and qualified feature.

## Scope and settled requirements

- John explicitly approved the plan on 22 September 2026 and supplied local papers.
  Initial P1 trials are complete; P2 implementation is now in scope. Plan approval
  covers implementation; do not ask for it again at each milestone.
- MacBook Pro M5 Max with 128 GB unified memory is a first-class target. The usual
  one-to-few-paper workflow must work there. Hardware and MPS availability are
  observed; measured local trials are recorded below.
- Cluster target is **two A40 GPUs, 48 GB each**, two 32-core EPYC 7543 CPUs and
  512 GB RAM per node; subset allocations permitted. Require user-space dependency
  environments, without assuming containers or administrator-installed tools.
- Rented GPUs are an ordinary larger-batch option, also when HPC queues are slow.
  No paid infrastructure or remote AI call has been made or separately authorized
  for this P1 inspection. No credentials are needed for the next local work.
- Figure descriptions remain an early priority (P3); prefer a local model and
  compare optional paid APIs later. Metadata queries are allowed by default;
  remote article-content processing requires explicit enabling.
- Author TeX is reference evidence for the accepted version. Journal copy edits
  and separate supplements require version-aware comparisons, not automatic merges.
- John added two library-scale requirements on 22 September 2026, now in Plan §5
  (“Library scale, layout and lookup index”) and §15 (“Duplicate handling before
  extraction”): the extracted library will be used by AI agents, probably as several
  subject-specific libraries that may grow large, so paper directories must stay
  location-independent, the library needs a rebuildable catalog/index with title,
  abstract and identifier lookup, and a tiered duplicate check must run before any
  file is extracted. John's first real batch is a private
  materials dump (surveyed below). Identical-byte skip, fingerprints and the catalog row
  are P2 scope; near-duplicate tiers, SQLite/FTS lookup and layout policy are P5.
- `dev/Brief.md` remains unchanged. Apache-2.0, John C. Travers
  `<jtravs@gmail.com>`. John requested clean logical commits during this session.
  Commit relevant project content deliberately; private data stays out of Git.
  No tag or push is authorized. Use `git log --oneline -5` to locate this checkout's
  latest checkpoint; do not assume earlier uncommitted work is disposable.

## Milestone ledger

| Package | Status | Completed / remaining evidence |
| --- | --- | --- |
| P0: plan and bootstrap | Complete | Detailed plan, licenses, packaging, lockfile, manual, CI configuration and project-specific agent instructions. Earlier full gates on Python 3.12/3.14, package builds and isolated wheel import passed. CI itself has not run. |
| P1: corpus and runtime qualification | In progress | Corpus inspected and fingerprinted; four captures compared; 44 reference candidates seeded. Benchmark validator and three Mac smoke trials complete; full published HISOL tested with MinerU; model licences reviewed (24 September). A reviewed reference set, scan review and quiet timing repeats remain; John accepts the pilot verification for now. |
| P2: first extraction slice | In progress | Preservation, inspection, fingerprints, intake planning, worker protocol v1, MinerU adapter, canonical schema 0.1 with reader, normalization, one-PDF pipeline, portable export, bibliographic identity with Crossref/DataCite, `citation.bib` and validated names, and the command line with duplicate tiers 1–2 implemented; three real papers published and validated through `batch`. The first full paper review with John remains. |
| P3: figure descriptions | Implemented | `describe` command with vLLM, Anthropic (capped) and MLX backends; labelled records, Markdown blocks and index column. Model chosen from John's ratings of 7 models on 19 figures and a prompt v2 retrial. |
| P4: semantic HTML/reconciliation | Implemented as a cross-check | HTML and MHTML captures preserved as sources and compared (text, captions, tables, references) by DOI; no merging into the paper, by design; math not compared. |
| P5: backend breadth/batch | Implemented | Docling backend and table check, manifests with resume and shards, versions and relations, tier-4 title candidates, arXiv, layouts and `organize`, identical-text copies preserved in their paper, Marker backend, `compare`, `migrate` with version guards and integrity checks. |
| P6: Linux batch/rentals | In progress | Linux locks, `[mineru]` GPU engines, serving workers and first A40 pilot measurements done; Slurm batch lane, describe on A40s, larger-batch measurements and rental comparison remain. |
| P7: hardening/release | Not started | Scientific holdout acceptance, portability/recovery, reviewed defaults and license audit. |

## Current checkpoint: benchmark implementation

- Implemented dependency-free `load_benchmark` and `compare` APIs; see the manual
  `docs/benchmark.md`. Explicit schema version and strict fields, duplicate-key/ID
  checks, source hashes/path containment, source/version/page/object scope,
  exact cells/headers and deliberately conservative equation normalization.
- Private converted manifest: `data/corpus-inspection/benchmark-v1.json`, SHA-256
  `294e5cccae34e2d4d8a5d6ac8abd0818033e2a1a813b0530cbfa6466b153d652`.
  All 7 PDF source hashes verified; 44 candidates include 4 explicit blanks.
  Earlier inspection files are retained unchanged. This does not promote gold.
- Full offline gate passed: 74 tests, 100% core line/branch coverage, strict types,
  lint/format, docstrings, spelling and warning-free Sphinx manual.
- John authorized the validator, all three isolated Mac trials, evidence-based
  first adapter selection and the extraction workflow, with figure descriptions
  following early. Latest quota check reports 99% consumed; no reset requested.
- John reports background contention on this Mac. Label current measurements
  contention-affected and record load/memory context. Repeat on a quieter system
  before treating timings as a backend ranking. Do not interrupt unrelated work.
- Isolated Mac/arm64 Python 3.12.13 environments resolved and installed in
  `workers/docling`, `workers/marker`, `workers/mineru`, with separate `uv.lock`
  files. Pins: Docling 2.129.0, Marker 2.0.0, MinerU 4.0.5. Core lock unchanged.
- Hardware probe confirms Apple M5 Max, 137438953472 bytes RAM (128 GiB), 18 CPUs;
  PyTorch 2.14.0 reports MPS built and available. Initial load 6.73/7.25/7.33.
- Docling model setup completed with frozen Hugging Face revisions and hashes in
  private `data/backend-trials/docling-models.json`. Models under
  `model-cache/docling/`; download script/log under `data/backend-trials/`.
- Docling selected-page trial completed: see `dev/Trials.md`. MPS formula failed
  in v1; v2 used MPS layout/CPU formulas. Accepted HISOL page 1 took 52.06/45.13 s,
  page 15 took 1.88/1.87 s; contention-affected selected pages, not throughput.
  Private evidence/scripts/logs: `data/backend-trials/docling-smoke-v1/`,
  `docling-smoke-v2/`, `docling-smoke-v2.py`, `compare-docling.py`.
  Comparison retains candidate status: 38 raw nonblank strings correct; four
  blanks only in derived grid; header/inline-math/caption limitations recorded.
- Remaining frozen MinerU/Surya models and official llama.cpp b10964 arm64
  binary downloaded and hash-recorded in `remaining-model-downloads.json` and
  `llama-binary-release.json`, under `data/backend-trials/`.
- MinerU standard/ONNX + local llama.cpp selected-page trial completed in
  `mineru-smoke-v2/`: page 1 took 21.74/4.94 s, page 15 took 0.69/0.61 s,
  sampled process-tree RSS up to approximately 5.2 GB. Scientific findings recorded.
  v1 failed because the scratch harness lacked the multiprocessing main guard;
  this was fixed in `mineru-smoke-v2.py`, not an extraction defect.
- Marker balanced smoke trial completed in `marker-smoke-v1/`: page 1 took
  51.43/22.84 s, page 15 took 1.92/1.61 s, plus 16.62 s local server startup.
  Its owned server is stopped. Inline math promising, but critical table failure:
  seven columns become ten; decimal points disappear into split integer/fraction
  cells. No numeric repair or positional scoring applied to the broken topology.
- Full published HISOL (10 pages) completed twice with MinerU in
  `mineru-published-v1/`: 47.72/39.59 s, 6.80/7.17 GB sampled process-tree RSS.
  Both raw JSON outputs identical; page indices 0–9 present. All five captions
  survive, but figures are split among 27 chart + 5 image blocks; captions attach
  to selected panels. Composite figure context is a required adapter/validation task.
- Forced-OCR Shelton page 6 trial completed in `mineru-scan-v1/`: 15.26/12.64 s,
  3.21/3.44 GB sampled process-tree RSS. Agent visual checks of uncertainty values,
  header spans and blanks agree with the 300-DPI reference render; full scan gold
  remains open. All trial subprocesses and the owned Marker server have stopped.
- **Decision:** MinerU is the first P2 adapter, for the reasons and limitations in
  `dev/Trials.md`. This is implementation sequencing, not a blanket quality default.
  Public model/file hashes and native binary pin are in `workers/models.json`.
  Underlying MinerU model-license audit remains pending before redistribution.
- `preserve_pdf` now copies, hashes and verifies source evidence without overwriting
  any existing destination; interrupted partial copies are removed. See
  `docs/ingest.md`. It is a staging primitive, not bundle publication.
- No active trial process remains. Next: versioned worker
  protocol, native-output validation and canonical normalization. First complete
  paper directory, metadata/BibTeX and figure context are still unimplemented.


## Current checkpoint: worker boundary (step 1 of the P2 sequence)

- `pypdfium2>=5.13.0` is the first core runtime dependency (John approved on
  22 September 2026). `paperextract.pdf.inspect_pdf` reports page count, PDF
  version and per-page displayed size, rotation and raw MediaBox; it raises
  `UnreadablePdfError` for damaged or encrypted files. This is structure, not content.
- `paperextract.protocol` defines worker protocol **version 1**: `ExtractionRequest`
  (request ID, preserved source path and SHA-256, coordinator page count, explicit
  one-based page tuple or all pages, `MineruProfile`, model and staging directories)
  and `ExtractionResult` (status, worker page count, `PageCoverage` with
  requested/returned/empty pages, hashed file list, redacted configuration,
  environment, timing, resources, diagnostics, failure). Parsing is strict: exact
  field sets, schema/version envelope, absolute paths, normalized relative result
  paths without `..`, status invariants.
- `paperextract.mineru.run_worker` writes `request.json`, launches
  `workers/mineru/.venv/bin/python workers/mineru/paperextract_mineru_worker.py` in
  its own session with a minimal environment (local model directory, ONNX small
  models, llama.cpp engine, Hugging Face offline, `MINERU_HOME` inside staging),
  captures both logs, kills the process group on timeout, and admits a result only
  after `verify_result`: request ID and source digest match, worker page count equals
  the coordinator's, every recorded file exists unchanged inside staging and is not
  a symlink, coverage lists exactly the requested pages, no unrequested page, and a
  completed result includes `native/middle_json.json`. Missing pages are reported
  in coverage, not rejected. A `failed` result is returned for the caller to judge.
- The worker script imports only MinerU, PDFium, Torch, psutil and the standard
  library; it re-hashes the source, recounts pages, converts the page tuple to
  MinerU's inclusive range grammar, saves native output under `native/`, and writes
  `result.json` atomically for both completed and failed runs. Empty returned pages
  produce `PAGE_EMPTY`; absent pages produce `PAGE_NOT_RETURNED`.
- Offline gate after this step: 189 tests (23 boundary tests with a stand-in worker
  covering each integrity violation, timeout and crash), 100% core line/branch
  coverage, strict types, docstrings, spelling, lint/format and a warning-free manual
  including the new `docs/worker.md`. `uv lock --check` passes offline.
- **Real smoke through the boundary** (opt-in `backend` lane, published HISOL PDF,
  all pages, standard profile): the first attempt (`worker-smoke-v1/`) crashed in the
  worker because `mineru.__version__` does not exist in 4.0.5; the adapter reported
  a `WorkerProcessError` with the stderr tail, as designed. After switching to
  distribution metadata, `worker-smoke-v2/` completed: 10/10 pages returned, none
  empty, 45 native files verified, `parse_seconds` 52.60, `save_seconds` 0.16,
  worker `max_rss_self_bytes` 6.09 GB, load average rising from 3.96 to 7.72 during
  the run (contention-affected). The saved `native/middle_json.json` is byte-identical
  to the earlier direct trial's (`2de4b44045dc0e99…`), so the boundary changes
  nothing in MinerU's output. Private evidence: `data/backend-trials/worker-smoke-v1/`
  and `worker-smoke-v2/` (request, result, logs, native output).
- Known limits: no persistent worker yet, so each run pays model start-up; the
  adapter is POSIX-only (process groups); MinerU's placeholder page for an unreadable
  page is indistinguishable from a blank page at this boundary; model file hashes
  are not re-verified per run (names and sizes are recorded, pins live in
  `workers/models.json`).

## Current checkpoint: public repository (24 September 2026)

- Published at https://github.com/jtravs/paperextract with a fresh history
  (John's choice): one initial commit holding the current tree, so older
  commits with site details stay private. The first GitHub CI run passed on
  all four cells (Linux and macOS, Python 3.12 and 3.14), including the
  wheel check outside the checkout.
- Release setup (24 September 2026): PyPI trusted publishing from the
  `Release` workflow (pending publisher added by John; GitHub environment
  `pypi` limited to `v*` tags), Read the Docs building from
  `.readthedocs.yaml` (first build passed), Codecov with the repository
  token (100% coverage), README badges, `CITATION.cff`, and
  `docs/dev/releasing.md`. Zenodo skipped by John. The changelog is ready
  for 0.1.0.

## Current checkpoint: full materials run (24 September 2026, complete)

- Started at John's request with the tested cluster procedure: the private
  materials dump (181 PDFs; `dedup`: 169 distinct, 12 identical copies, 5
  same-text copies, 2 supplements, 1 shared DOI for review) gave a manifest
  of 162 papers. A four-task array, one A40 each (the account's QOS allows 4
  GPUs per user; the standard QOS allows 2), extracts with vLLM, torch small
  models, 16 in flight, batch-invariant, persistent worker, 12 h per task
  with a stop signal 5 minutes before; a CPU job then publishes all staged
  runs with registry identity (afterany); a two-A40 job describes every
  figure with Qwen3.8-27B, four papers at once (afterok).
- **Extraction and publication:** all 162 papers staged (39, 47, 35 and 41
  per task) and all 162 published, 2923 pages. Three tasks took 885-1170 s;
  the fourth took 3313 s because its share held a 1194-page database report
  (37 minutes of parse on its own). Worker parse time in total 6295 s, about
  28 pages per minute per A40. Publication with registry identity took
  226 s: 40 `VALIDATED`, 88 `VALIDATED_WITH_WARNINGS`, 34 `UNVERIFIED`; 161
  `COMPLETE`, 1 `PARTIAL` (the long report, pages without content flagged
  `PAGE_EMPTY`); 1960 findings in all.
- **Descriptions** (Qwen3.8-27B on two A40s, four papers at once): 6500 s
  wall including 260 s of server start-up; 925 figures described, about 7 s
  per figure, 1.14 million generated tokens; 4 answers failed to parse (2
  hit the 8192-token limit), recorded as failed. 145 papers republished; 17
  had no figure to describe, several of them older text-only papers, though
  a few (such as two ionization papers from 1990 and 2008) may have figures
  the backend did not detect, worth a look.
- Copied to John's local library location (28,271 files, 4.1 GB);
  `migrate --dry-run` there: all 162 papers current and every file matching
  its manifest hash; search works on the copied index.

## Current checkpoint: completing the package, steps 1-2 (24 September 2026)

- John's order (23 September): 1 cluster support in generic form, 2 reviewed
  defaults, 3 user guide and README, 4 model license audit, 5 release
  readiness; then the full materials run. Verification of the A40 outputs is
  accepted ("I have read many of the outputs and I am happy").
- **Step 1.** `paperextract.models` and `models status|fetch`:
  `workers/models.json` is schema `paperextract.model-manifest` 2 with 11
  snapshots (every file's size and SHA-256; new: MinerU torch and vLLM
  weights, Qwen3.8-27B, Surya's OCR-error model from Datalab's server) and
  the llama.cpp b10964 archives for macOS arm64 and Linux x86_64 (no CUDA
  build is published for Linux). A fresh cluster fetch of `mineru-cuda`
  (27 files, 3.2 GB, 4.5 min) verified complete; the Mac holds every set.
  `batch --runs-to DIR` stages runs without writing the library and resumes
  from completed runs; `publish` takes many runs or directories and reports
  runs a stopped job left incomplete. Real cluster check: manifest in a CPU
  job, a two-task array (one A40 each) staged 5 and 10 papers in 209 s and
  329 s, one `publish` of both published all 15 in 38 s with registry
  identity (14 validated). Manual page `docs/cluster.md` with generic
  templates; the site-specific versions live in the private site guide.
- **Step 2.** `[mineru] engine = "auto"` (default) resolves to vLLM + torch +
  16 in flight when the MinerU worker has vLLM and `nvidia-smi` lists a GPU,
  else llama.cpp + ONNX + 1; explicit settings win; the profile written to
  each request stays explicit. `[worker] persistent` defaults to on with
  vLLM only: on the Mac, llama.cpp through a serving worker gave output
  identical to per-paper processes on all six pilot documents but no speed
  gain (448 s against 407 s). `[describe] papers` defaults to 4.
- Gate passed (731 tests, 100% coverage).
- **Step 3.** User guide: `docs/usage.md` is now "Getting started" (install
  on a Mac or Linux GPU, fetch models, first extraction, what a paper
  directory holds, batches, search, descriptions); new
  `docs/limitations.md` (replaces the stale "Current limits" in the CLI
  page, which still said arXiv was not resolved and copies were not kept);
  the manual index is grouped into user guide, reference and development;
  `README.md` rewritten as a public front page.
- **Step 4.** Model licence review (`docs/models.md`, licences recorded in
  `workers/models.json` with how each was established): MinerU's torch and
  vLLM weights, Qwen3.8-27B, Docling's models and the PaddlePaddle sources of
  MinerU's ONNX bundle declare Apache-2.0 or CDLA-Permissive-2.0; the ONNX
  bundle and the third-party GGUF declare none (Apache-2.0 inferred from
  their sources); Surya's modified AI Pubs OpenRAIL-M restricts commercial
  use above USD 5M revenue or funding (research and personal use excepted)
  and applies attribution and share-alike to outputs, so Marker-extracted
  libraries carry those terms. Code: MinerU Open Source License (Apache-2.0
  plus thresholds), Docling MIT, Marker Apache-2.0, vLLM Apache-2.0,
  llama.cpp MIT.
- **Step 5.** Release readiness. The CI matrix (Linux and macOS, Python
  3.12 and 3.14) was reproduced locally because GitHub CI cannot run until
  John pushes the repository: macOS on this Mac, Linux on the CPU
  workstation, each with `uv lock --check`, the full gate, `uv build` and a
  wheel import outside the checkout. The first round found three real
  problems: a serving worker that exits just after answering can still look
  alive on Linux and failed the next paper (now retried once on a new
  process when it had not touched the request, with both attempts in the
  log); registry `HTTPError`s were not closed, which Python 3.14 reports as
  a `ResourceWarning` (an error in the suite); and a timing-dependent test.
  After the fixes all four cells pass. Source archive and wheel checked: no
  private data, `LICENSE` and `NOTICE` in the wheel, the installed command
  runs outside a checkout and says when it needs one. Changelog rewritten
  as one grouped Unreleased section; classifier "3 - Alpha". Open for John:
  choose the public repository (and whether to publish fresh history, since
  older commits contain site details), push, and see the first GitHub CI run.

## Current checkpoint: Linux and A40 qualification (P6, in progress)

- Requested by John on 23 September 2026 ("go ahead with P6 ... very
  interested in the A40 performance"; all compute through Slurm; use the
  cluster and a CPU workstation for testing and validation only). Generic
  host facts and design notes: Plan "Implementation notes for P6".
- **Site-specific details are not in the repository** (John, 23 September
  2026: the package will be public). Host names, accounts, quotas, paths,
  job scripts, configs and run locations are in the maintainer's private site
  guide outside the checkout. The cluster holds a synced checkout, built
  environments, the pinned MinerU models (the 19 files recorded in
  `workers/models.json` match by SHA-256), Qwen3.8-27B (`1d4bf0f2ff60`,
  52 GB), a trial vLLM 0.30.0 serving environment, private pilot copies and
  the runs below. Run names: `portable`, `hybrid`, `gpu`, `gpu16`
  (concurrency 16), `gpu16p` (plus `[worker] persistent`).
- Implemented: Linux x86_64 in all worker locks (Mac pins unchanged);
  MinerU `cuda` extra; `MineruProfile.vlm_engine` `vllm` and
  `small_backend` `onnx`/`torch` (older profiles read as ONNX); `[mineru]
  engine`, `small_models`, `concurrency`; vLLM telemetry and FlashInfer JIT
  sampler off, `VLLM_CACHE_ROOT` and `CUDA_VISIBLE_DEVICES` passed through;
  `WorkerSessions` with MinerU `--serve` and `[worker] persistent` for batch.
- **A40 pilot** (5 papers, 47 pages incl. the HISOL supplement, `batch`,
  table OCR on, one A40, 8 CPUs; two jobs shared a node at times):

  | Run | Wall | Worker parse | Notes |
  | --- | --- | --- | --- |
  | Mac M5 Max, llama.cpp + ONNX | 407 s | 326 s | inference 309 s |
  | A40 `hybrid` vLLM + ONNX, concurrency 1 | 958 s | 693 s | |
  | A40 `gpu` vLLM + torch, concurrency 1 | 826 s | 582 s | vLLM engine 62 s per paper; GPU 21% |
  | A40 `gpu16`, concurrency 16, job compile cache | 519 s | 350 s | engine 32 s after the first; inference 123 s; GPU 10% |
  | A40 `gpu16p`, as `gpu16` with `[worker] persistent` | **170 s** | 145 s | one process for 7 requests (5 papers + 2 table OCR); HISOL 10 pages in 11 s |

  Pure inference is 2.5x faster than the Mac, but a per-paper vLLM engine
  build and slower filesystem work (170 s outside the worker against 81 s)
  lose it; the serving worker recovers it: 2.4x the Mac's wall time for the
  pilot. `portable` (CPU llama.cpp and ONNX, 8 cores of a CPU node) took
  6992 s for the pilot, so CPU-only Linux nodes are not a practical lane.
- **Fidelity against the Mac** (`compare` logic, same PDFs): paragraph text
  identical both ways on all six documents, every caption agrees, 12 of 14
  tables agree, identically for every A40 variant. The variants are not
  byte-identical to each other: torch and ONNX small models split one
  Shelton block differently, and vLLM at concurrency 16 varies run to run
  (per-process against serving: 6 blocks, LaTeX spellings such as
  `\omega_\infty`/`\omega_{\infty}` and digit grouping `219444.5464` against
  `219 444.5464`, value unchanged). Try vLLM's batch-invariant mode before
  qualifying. Differences from the Mac come from vLLM with the original
  weights versus llama.cpp Q8:
  3-5 equations per paper differ (HISOL (5) `v` became `\nu`, likely wrong;
  HISOL (7) and COPRA (24) look better on the A40), a Wahlstrand cell `0,0`
  split, and the unlabelled COPRA page-8 table lost its printed axis row of
  noise levels (0%, 0.1%, ...). For John's review before any GPU setting is
  called qualified.
- **Larger set** (15 papers, 124 pages: the pilot plus 10 drawn with seed
  20260923 from `dedup --manifest-out` of the materials dump): persistent worker, concurrency 16, one A40 each,
  two runs per mode. Ordinary vLLM batching: 273 s and 283 s wall (about 30
  pages per minute of parse); the two runs differ in 11 of 2516 blocks
  across 8 documents, none in numbers. `[mineru] batch_invariant = true`:
  410 s and 412 s (about 20 pages per minute), and the two runs are
  identical (0 of 2516 blocks differ). Invariant against ordinary: 19 blocks
  differ, 2 with different numbers (to review with the other fidelity items).
  **Decision (John, 23 September 2026):** batch-invariant mode is the vLLM
  default; `batch_invariant = false` restores ordinary batching.
- **Describe on A40s** (Qwen3.8-27B bf16, prompt v2, JSON, 16 in flight,
  trial vLLM 0.30.0): one A40 cannot hold the bf16 weights, and vLLM's FP8
  path failed on Ampere (`cutlass_scaled_mm_sm80` error). Tensor parallelism
  over both A40s of a node hung in NCCL initialization, then in CUDA-graph
  capture; it works with `NCCL_P2P_DISABLE=1` and
  `--disable-custom-all-reduce`. Result: server ready in 300 s; 36 figures of
  the five pilot papers in 861 s (24 s per figure, about 75 generated
  tokens/s in aggregate, 14 per request), all parsed. The H100 did about
  8 s per figure and the Mac about 151 s. Same 7 figures as the H100 v2
  run: mean similarity 0.76, none identical (unrated).
- **Describing across papers** (`[describe] papers`, 15-paper library, 79
  figures, two A40s, same server settings): one paper at a time 1916 s
  (24 s per figure); four at once **672 s (8.5 s per figure, 2.9x faster)**,
  about the H100's one-paper rate. Server start-up 260-325 s on top. One
  figure failed to parse in each run, a different one each time: an answer
  that hit the 8192-token limit, and one with malformed readings. Found and
  fixed on the way: PDFium crashed when two threads rendered at once.
- Gate passed (712 tests, 100% coverage).

## Current checkpoint: library migration (P5)

- Requested by John on 23 September 2026 ("go ahead with the migration
  tooling"). Survey first: the private libraries hold three generations of
  paper directory (manifest 1 / document 0.1, 2 / 0.2, 3 / 0.3) and catalog
  rows 1 and 2; every command already read them and `reprocess --all`
  already rewrote them. Missing were visibility, refusal of unknown
  versions (paper records were read without any version check) and an
  explicit command.
- `formats.py`: `READABLE_VERSIONS` per record schema (last = written; a
  test ties each to the writer's constant; `document.py` reads its set from
  it), `require_readable` (catalog rows, corpus, catalog row inputs),
  `require_readable_paper` (reprocess), `check_paper` (record versions plus
  manifest integrity: missing, changed, unlisted, unreadable files; kept
  evidence integrity-checked only). `UnsupportedFormatError` exits 5.
- `paperextract migrate [--dry-run]`: per paper `current` / `would migrate` /
  `migrated` / `refused` (unsupported or damaged) / `failed`, with record
  changes such as `paper-manifest 1→3, document 0.1→0.3`; rebuilds outdated
  papers through the journaled reprocess path (offline, identity kept, old
  generation retired), then the catalog and index unless a paper is
  unsupported. Result `paperextract.migrate-result` 1.
- **Real run** (private copies in the session scratchpad, not kept): dry run
  over all 19 private libraries found no damaged or unsupported paper; 28
  papers outdated. Migrating all copies: exit 0 everywhere, 28 migrated, 26 s
  total; a second dry run reports every paper current and intact; the 15
  figure-description files are unchanged. The originals under `data/` were
  not modified.
- Gate passed (703 tests, 100% coverage).

## Current checkpoint: Marker backend and `compare` (P5)

- Requested by John on 23 September 2026 ("go ahead with the compare command,
  and also marker for completeness").
- **Marker** 2.0.0 (surya 0.22.1): `workers/marker/paperextract_marker_worker.py`
  starts the pinned llama.cpp `llama-server` (b10964) on 127.0.0.1 with the local
  Surya GGUF, runs `PdfConverter` with the JSON renderer (`use_llm=False`), writes
  `native/marker.json` (`paperextract.marker-native` 1) and image files, and stops
  the server. `MarkerProfile(server_binary, mode, cpu_threads)`; `[marker]
  models/server/mode` configuration; `normalize_marker.py` maps Marker blocks and
  groups (HTML runs with inline math, equations with printed numbers, list,
  table and figure groups with captions). Only local server requests were seen.
- **`compare`** (`compare.py`, `paperextract compare PDF --backends ...`): per
  backend summary and, against the first backend, text coverage both ways,
  headings, captions by label, table numbers, equations by number with a LaTeX
  key. Nothing is published. The equation key ignores a trailing printed
  number (Marker keeps `\quad (2)` in its LaTeX); added after the real run.
- **Real run** (private, `data/compare-check-v1/hisol/`, published HISOL, 10
  pages, 13 min 20 s wall, partly overlapping gate runs): MinerU 41 s parse,
  Docling 368 s, Marker 305 s. All processed 10 pages and 5 labelled figures;
  inline math runs 64 / 0 / 109. MinerU vs Docling: 2/154 and 4/146 paragraphs
  mostly absent (affiliation and date lines; Docling's figure axis text), 4/5
  captions agree (Fig. 3 differs), equations 1 same, 7 different, (7) missing in
  Docling. MinerU vs Marker: 4/154 and 4/78 absent, mostly inline-math symbols
  read as math on one side and text on the other; 3/5 captions (Marker splits
  Fig. 1's panel text into a paragraph, Fig. 3 differs); equations 5 of 9 same
  after the key fix (0 before). `comparison.json` there was regenerated from
  the kept documents with the fixed key. The paper has no tables.
- Gate passed (686 tests, 100% coverage).

## Current checkpoint: agent skill and equivalent copies

- `skills/paperextract/` (SKILL.md, reference.md), requested by John on
  23 September 2026. A fresh agent given only the skill answered a library
  question with exact values and warnings checked, and added COPRA to a new
  library and read its Table 1. Its critique led to fixes: phrase search for
  hyphenated or quoted words; relations store the other paper's name (they
  went stale after `organize`); lost glyphs in table notes keep their finding
  after an OCR body replaces the table body; file names saying AAM, accepted,
  submitted or preprint set the version; skill corrections (shell function,
  exit codes, unverified papers, `--offline`).
- Identical-text copies are preserved in the equivalent paper
  (`original/source_NN/`, role `equivalent_copy`, status `attached`), also
  for a paper already in the library (republished without extraction);
  `attached` counts as success. Real check (private,
  `data/equivalents-check-v1/`): the Huber and Tondello 1974 pair from the
  materials dump, one published and the re-download attached, 27 s.
- Gate passed (673 tests, 100% coverage).

## Current checkpoint: library layouts and `organize` (P5)

`corpus.json` `layout` (`flat`, `by-year`, `by-initial`) now places new papers
in a shard directory from validated fields (`Unverified/` otherwise); names
stay unique across shards; catalog rows record the relative path; discovery
looks one level down; replacement moves papers across shards and removes
emptied shards. `organize --layout L [--dry-run]` moves papers under a
journal completed by `recover_library`, records the layout and rebuilds the
catalog and index. Commands accept a paper's bare name in a sharded library.
Gate passed (667 tests, 100% coverage). Real check (private,
`data/organize-check-v1/`): five papers flat → by-year (lookup, search,
reprocess and catalog links follow) → a new paper published into `2022/` →
by-initial → flat; 484 manifest hashes verified, empty shards removed.

## Current checkpoint: batch manifests (P5)

`manifest.py` and `batch --manifest` (resume by digest, `--shard K/N`),
`dedup --manifest-out`, `same title?` groups in `dedup` (tier 4 at intake from
PDF information titles). Gate passed (662 tests, 100% coverage). Real checks
(private, `data/manifest-check-v1/`): `dedup` of the materials dump proposed
161 entries (matching "to extract: 161") and found two same-title pairs the
byte and text tiers missed (`j100285a012.pdf` / Koszykowski 1987 and
`6846_1_online.pdf` / Tegeler 1999); a two-entry manifest published HISOL
with supplement and MHTML page and COPRA with its HTML page and asserted
version in 3 min 8 s.

## Current checkpoint: document versions, relations and arXiv (P5)

Implemented and gate-passed (652 tests, 100% coverage): `versions.py`
(document version with evidence or user assertion, relations to library
papers by DOI and by title/first author/year, findings
`OTHER_VERSION_IN_LIBRARY` and `DUPLICATE_CANDIDATE`, reported and never acted
on); `hints.json`/`extraction.json` `assertions` kept by `reprocess`;
`--document-version` on `extract` and `reprocess`; arXiv identifiers resolve
through `10.48550/arxiv.*` DataCite DOIs; `acquire.py` and `extract arXiv:ID`;
`lookup --arxiv`, index schema 3. Observed titles drop superscript footnote
markers (fixed identification of the HISOL accepted manuscript).
Decision: a same-version duplicate is published and flagged, not held; the
plan's "hold" would need reliable versions, which files rarely state.
Real checks (private): `data/versions-check-v1/` (COPRA and HISOL version of
record, Shelton unknown; HISOL AAM validated by search and related to the
version of record); `data/arxiv-check-v1/` (`arXiv:2206.01062` resolved to v1, downloaded,
identified by its printed ACM DOI, version preprint, found by
`lookup --arxiv` and by file).

## Current checkpoint: Docling backend, table check and HTML cross-check (P4/P5)

John asked on 23 September 2026 to go ahead with the Docling adapter and the
remaining stages up to and including P5. Implemented, gate passed (628 tests,
100% coverage, strict types, docstrings, spelling, manual), not yet
documented in the manual beyond `docs/cli.md` and `docs/worker.md`:

- `paperextract.mineru` renamed to `paperextract.worker`; protocol 1 gains
  `DoclingProfile` (backend chosen by profile type), `native_document_path`.
- `workers/docling/paperextract_docling_worker.py`: one conversion per
  contiguous page range, wrapper `native/docling.json`
  (`paperextract.docling-native` 1) with PDF information and crops; OCR off.
- `normalize_docling.py` into the canonical document (schema 0.3 adds
  `docling` alternative bodies); `--backend docling`.
- `table_check.py`: `--table-check` reads MinerU table pages with Docling and
  records `TABLE_CHECK_AGREED/DISAGREED/UNMATCHED/EXTRA/FAILED`; kept under
  `diagnostics/raw/<run>/table-check/`, reapplied by `reprocess`.
- P4: `capture.py` (saved HTML with `_files`, MHTML), `html_article.py`
  (stdlib DOM reader), `html_check.py` (DOI/title gate, headings, text
  coverage, captions, table numbers, references). `extract --html`, batch
  pairing by DOI, `reprocess PAPER --html`; captures published as
  `original/source_NN/`, report `html_check.json`, findings in
  `validation.json` and `review.md`. CLI result schema 2 (`captures`).
- Real checks (private, `data/docling-check-v1/`): COPRA tables 1–3 agree
  with Docling after the glyph-code fix; COPRA page 8 (Fig. 7 read as a table
  by MinerU) unmatched; Shelton scan 52–95% agreement as in the trial. HISOL
  with `--backend docling`: validated, 10 pages, 4 min 56 s, display LaTeX
  worse than MinerU and no inline math. COPRA HTML and MHTML: 92/92
  paragraphs, 7/8 captions (Fig. 7 flagged), tables not comparable (math
  graphics); HISOL page: 5/5 captions, 80 vs 81 references; Wahlstrand page
  against HISOL refused as DOI mismatch. Reprocess with captures idempotent.
- **Decision (John, 23 September 2026):** the table check stays off by
  default; `--table-check` or `[worker] table_check = true` enables it.

## Current checkpoint: figure descriptions (P3)

**Decision (23 September 2026, John's ratings of two review pages):**
Qwen3.8-27B with prompt `figure-claims-v2`, reasoning off, served by vLLM on a
rented single GPU is the standard; Claude Opus 5.5 (low effort) is the opt-in
choice for careful jobs. Rejected: Granite Vision 4.1 4B, Qwen3.5-9B, Gemma 4
26B-A4B, Molmo2-8B, InternVL3.5-38B (close to Qwen with prompt v1 only),
Haiku 4.5, Sonnet 5 (not worth the saving) and Qwen reasoning mode (Opus is
better for the money). Rejected local weights (74 GB) were moved to the macOS
Trash; only `model-cache/describe/Qwen--Qwen3.8-27B` remains. Trial evidence is
in `dev/Trials.md`; private trial data in `data/describe-trials/` and
`data/gpu-trials/2026-09-23-h100/`.

**Implemented and committed:**

- `describe.py`: claims schema, prompts v1 and v2 (default v2), parser
  (reasoning blocks, template-opened `</think>`), printed-string check with
  caption exclusion and OCR-layer refusal, labelled rendering, records with
  `machine_generated`, notice, `created_utc`, finish reason and cost;
  description files `descriptions/<figure_id>.json`
  (`paperextract.figure-descriptions` v1); `citing_sentence` (fixes the trial
  script's clipped first letter).
- `describers.py`: OpenAI-compatible (vLLM) with concurrency and JSON output;
  Anthropic through the SDK (optional extra `paperextract[anthropic]`, also in
  the dev group) with a per-run hard cap from counted input plus full output,
  and a ledger `.paperextract/describe-spend.jsonl`; local MLX worker.
- `describe_stage.py`: renders exactly the trial images (19 of 19 identical
  bytes), skips figures with an equivalent description, records failures.
- Publication copies description files (manifest-hashed) and renders the
  latest parsed one under the caption between marker comments (export schema
  3); `reprocess` carries descriptions forward; orphaned descriptions are noted.
- Index schema 2: descriptions in their own FTS column, stripped from body;
  hits labelled `in_description`; old indexes rebuild on use.
- `[describe]` configuration and `paperextract describe` (`--all`, `--dry-run`,
  `--force`, `--backend`, `--endpoint`, `--model`, `--max-usd`, `--strict`).
- Docs: `docs/describe.md` (workflow, backends, GPU session recipe, limits),
  CLI, usage, README, changelog.
- Offline gate passed (576 tests, 100% coverage). Real check on the v5
  library copy `data/describe-check-v1/` (private): dry run found 29 figures;
  the MLX worker with Qwen3.8-27B described 14 of HISOL's 15 figures (5 main,
  10 supplement), one stopped at the 4096-token limit on a large supplement
  figure, so the default limit is now 8192; rerunning described only that
  figure and skipped 14. The republished manifest verified every hash, and
  `paper.md` and `supplement.md` carry the labelled blocks.

**Not done:** `extract --describe`; GPU provisioning stays outside the
package (the rental recipe is in the manual and the private site guide).

## Current checkpoint: reprocessing, identity search and library index

John asked on 23 September 2026 for items 1–3 in order, then nearly everything up
to and including P5, one second backend chosen for complementary strengths, an
assessment of P4, and researched P3 model options to discuss before downloads.

- **Reprocess** (`reprocess.py`, `catalog.py`, `publish(replace=…)`,
  `recover_library`): a paper directory's kept sources, native output, worker
  records and crops reconstruct the run's staging directory; `rebuild_document`
  re-normalizes and reapplies kept table OCR (`TABLE_OCR_NOT_RUN` otherwise);
  publication replaces the old directory under a journal and retires it to
  `.paperextract/replaced/`. Catalog rows are derived from published directories
  (row schema 2). Real check: three v5 papers rebuilt in 3.4 s with byte-identical
  `document.json` and assets (`data/reprocess-check-v1/`).
- **Identity search**: Crossref `query.bibliographic` by observed title, accepted
  only for one hit whose title equals the observed title and whose first author
  and year are printed on page 1 (`VALIDATED_WITH_WARNINGS`); Elsevier PII DOI
  candidates; implausible PDF titles skipped; heading math flattened; MathML word
  boundaries kept. 89 of 169 distinct dump sources print no DOI; 5 of 6 tested
  DOI-less papers were identified, the sixth correctly not (see Trials).
- **Index** (`index.py`): `.paperextract/index.sqlite` (lookup tables, FTS5 over
  title, authors, abstract, text), `catalog.md`, `library.bib`,
  `catalog.csl.json`, refreshed on each publication; `lookup` (present, related,
  candidate, absent across libraries; file, DOI, BibTeX, title), `search`,
  `index rebuild`. Real check: the published HISOL PDF is `present`, its accepted
  manuscript a `candidate` through its printed title, search 0.1 s.
- **Second backend evidence**: Docling tables agree 100% with MinerU on COPRA and
  52–95% on the Shelton scan (see Trials); recommendation in the session report.
- Offline gate: 524 tests, 100% coverage, strict types, docstrings, spelling,
  warning-free manual; live registry lane (2 tests) passed.

## Current checkpoint: table OCR re-extraction and supplements

John approved both on 23 September 2026 ("build the targeted OCR table
re-extraction with the check, and add the supplement handling now").

- **Table OCR** (`table_ocr.py`, pipeline stage after refinement): pages holding a
  native table with unmapped glyphs are re-extracted by the same worker with
  `parse_mode="ocr"` (request `<id>-ocr`, directory `worker-ocr/`). An OCR table is
  matched by position (IoU ≥ 0.5) or, without boxes, by a unique label; it replaces
  the native body only when both bodies contain exactly the same multiset of digit
  groups (signs ignored), otherwise the native body stays. Either way the other body
  is kept in `Table.alternatives` with `Table.body_source`, and
  `TABLE_OCR_SELECTED`/`REJECTED`/`UNMATCHED`/`FAILED` records the decision. A
  failed OCR run never fails the paper. Switch: `--no-table-ocr`,
  `[worker] table_ocr`. Document schema **0.2** (reads 0.1); export schema **2**.
- **Supplements** (`supplements.py`, `PaperSources`, `extract_bundle`):
  `extract PAPER --supplement SI` or automatic `batch` pairing (file name marks a
  supplement; paired by a shared DOI candidate, else a unique longest shared file
  name start of ≥ 10 characters; unpaired or already-published-paper supplements are
  held). Each supplement is extracted by the full pipeline into `supplements/NN/` of
  the run; an incomplete bundle is never published. Published as
  `original/source_02/…` and `supplement_01/{supplement.md,document.json,figures,
  tables,equations}`; anchors `figure-sN`, `table-sN`, `equation-sN`, `section-sN`;
  main-text "Supplementary Fig. 3", "Fig. S3", "Eq. (S5)", "Supplementary
  Information", "Supplement 1" (publisher link kept after the local one) become
  links; captions do not link their own label. `validation.json` gains
  `supplements` and `supplement_references` (resolved count, unresolved list);
  front matter, `extraction.json` and the catalog row list the supplement source.
- Offline gate: 484 tests, 100% line/branch coverage, strict types, docstrings,
  spelling, lint/format, warning-free manual.
- **Real runs** (private): COPRA tables 1–3 all took the OCR body with every
  number agreeing (7, 5 and 3 numbers); the OCR bodies restore brackets, bars and
  tildes as LaTeX and drop a spurious text-layer "e" row. HISOL with its supplement
  (15 pages): paired by DOI in `batch`, 10 figures, 2 tables, 19 equations and 3
  sections anchored, 24 of 25 main-text references resolved; the unresolved
  "Supplementary Table 2" is a MinerU caption truncation ("n of some schemes…"),
  which leaves that table unlabelled. Libraries `data/cli-check-v3/` to
  `data/cli-check-v5/`.
- Limits: a reference range links its first number only; supplements cannot be
  added to an already published paper; the numerical check compares digit
  multisets, not cell positions.

## Current checkpoint: first paper review fixes

John reviewed HISOL, COPRA and Shelton 1990 as published by the command line on
22 September 2026: all three "excellent" and usable, minor comments on HISOL and
COPRA, more critical ones on the Shelton tables. Resolution, each verified by
re-extracting all three papers into `data/cli-check-v2/literature/` (private):

| Comment | Cause | Resolution |
| --- | --- | --- |
| HISOL "ollow" shown as superscript | MinerU dropped the drop capital and styled the rest superscript; the PDF text layer holds a 5x taller "H" | `layout.restore_drop_caps` with `pdf.drop_cap_letter`: restored only when the text layer confirms it (`DROP_CAP_RESTORED`) |
| HISOL affiliation inside the text | Already a `footnote` paragraph; the renderer printed footnotes in place | Page footnotes are labelled and follow their page's content, or a paragraph continuing onto the next page |
| HISOL panel-crop lines read as text | Separate paragraph after the caption | Links in parentheses at the end of the caption |
| COPRA Table 1 math garbled | MinerU built the table from the text layer; the math font's brackets are unmapped control characters (preserved, not dropped, but invisible) | `UNMAPPED_GLYPHS` warning and a visible `�`; **not fixed**. An OCR-mode run of page 2 (26 s) gave correct LaTeX; policy decision pending (Plan §8) |
| COPRA "Research Article" between refs 40/41 | Running header classified as heading on page 11 only | `layout.reclassify_repeated_furniture` (same text and position on ≥2 other pages) |
| COPRA/HISOL supplements | Not implemented | Recorded in Plan §8 as a bundle feature with cross-link rewriting |
| Shelton "Table (unlabelled)" | Label pattern lacked Roman numerals | `TABLE III` labels; `(Continued)` marks a continuation, shown as *(continued)* |
| Shelton table math shown raw | Cell math is backend `<eq>` markup; pipe tables used plain cell text and merged-cell tables were raw HTML, which viewers do not render as math | `export.cell_markdown`; every parsed grid is a pipe table, merged cells shown once with a note; HTML/cells files keep exact structure |
| Shelton refs 1–7 mid-discussion | Page 14 read left column (prose, refs 1–7) before the right column (prose, refs 8–) | `layout.gather_interleaved_references` moves earlier reference runs to the page's last run (`REFERENCES_REORDERED`); a list ending before a section is unchanged |

Other observations: COPRA Fig. 7's bar chart comes back from MinerU as a table
carrying the figure caption (`Table (unlabelled)` with "Fig. 7" caption); not
changed. MinerU output was identical between the two extraction runs, and the
HISOL Markdown diff shows only the intended changes. Offline gate: 432 tests, 100%
line/branch coverage, strict types, docstrings, spelling, warning-free manual.

## Current checkpoint: command line (step 6)

- `paperextract.config`: layered TOML configuration (options > `--config` file >
  `~/.config/paperextract/config.toml` or `$XDG_CONFIG_HOME` > defaults) with strict
  keys (`library`, `[worker] root/models/timeout_seconds/cpu_threads`,
  `[registry] enabled/offline/contact`), paths resolved against the file, and the
  origin of every value. Default library `./literature`; the worker root defaults to
  the checkout that runs the command (none for an installed wheel); the model
  directory to `<root>/model-cache/mineru/models`; timeout 3600 s per paper.
  `Configuration.extraction_settings` checks script, worker interpreter and models
  and names the setup step. Result records store `contact_configured`, never the
  address.
- `paperextract.cli` (console script `paperextract`, also `python -m paperextract`):
  `extract PDF` (a first non-command argument is the shorthand; several paths are
  refused because they would form a bundle), `batch PATH...` (top-level PDFs of
  directories plus files), `dedup PATH... [--report FILE]` (read-only) and
  `publish RUN [--refresh-identity]`. Options `--library`, `--models`, `--timeout`,
  `--offline`/`--no-registry`, `--strict`, `--keep-run`, `--pages`, `--json`,
  `-q`/`-v`. Tab-separated text or versioned `paperextract.cli-result` JSON on
  stdout, progress on stderr, a batch record in `.paperextract/batches/`. Runs live
  in `.paperextract/runs/<UTC>-<sha12>-<random>/`, are removed after a successful
  publication (the paper already holds raw output and logs) and kept on failure or
  cancellation. Exit codes 0/2/3/4/5/130 as in Plan §17; `--strict` counts
  unverified identity, partial coverage and held copies as warnings.
- Duplicate actions (Plan §15): tier 1 against batch and library (catalog digests
  whose directory still exists) skips or aliases; **tier 2 was brought forward**
  because the first real batch needs it: identical whole-text digest with at least
  200 characters marks the later copy `held` (not extracted, file untouched) until
  bundles can preserve it as a second representation. Tier 3 is reported by `dedup`
  as shared first DOI candidates, excluding held copies; both are extracted.
  Functions `content_key`, `content_equivalents`, `shared_doi_candidates` and
  `dedup_report` (schema `paperextract.dedup-report` 1) are in `ingest.py`.
- **Bugs found and fixed.** (1) Ctrl-C left the MinerU worker running, because it
  runs in its own session and `Popen.__exit__` does not kill on KeyboardInterrupt;
  `_launch` now kills the process group on any exception and SIGTERM maps to
  cancellation. (2) Two runs of one source in the same second collided on the run
  directory; a random suffix was added. (3) **ONNX Runtime 1.30.0 telemetry was
  active in every MinerU run so far.** The ONNX Runtime 1.29.0 release notes state
  that POSIX telemetry exists on macOS/Linux and that `ORT_DISABLE_TELEMETRY=1`
  before initialization disables it. On this Mac it had created
  `~/Library/Application Support/Microsoft/DeveloperTools/.onnxruntime/` (device id
  and `onnxruntime.db` event queue) at 11:50 on 22 September 2026, the first P1
  MinerU trial; the binary embeds `https://mobile.events.data.microsoft.com/OneCollector/1.0`.
  A worker then aborted with SIGABRT inside the telemetry thread's HTTP response
  handler after writing a completed result, which shows at least one upload
  attempt. Three queued events contained device/process details (Mac model, macOS
  version, CPU, device and session identifiers, execution-provider registration)
  and no paper content or paths; earlier, possibly uploaded events were not
  inspectable. The worker environment now sets `ORT_DISABLE_TELEMETRY=1`
  (regression test added). At John's request the `.onnxruntime` directory was
  moved to his Trash as `~/.Trash/onnxruntime-telemetry-2026-09-22` (not emptied);
  the sibling `deviceid` of other Microsoft developer tools was left in place. No
  event was queued after the fix; later file-time changes came from read-only
  inspection.
- At John's request his address is the Crossref contact in
  `~/.config/paperextract/config.toml` (outside the repository). Crossref needs no
  registration: on 22 September 2026 an anonymous request was served from
  `public-single` (5 requests/s, concurrency 1) and one with the contact from
  `polite-single` (10 requests/s, concurrency 3).
- Offline gate: 405 tests, 100% line/branch coverage, strict types, docstrings,
  spelling, lint/format, warning-free manual; `uv lock --check`, wheel and sdist
  build passed and the wheel declares the console script.
- **Real runs** (private, `data/cli-check-v1/`): `dedup` over the materials dump
  read 181 PDFs in 6.8 s (John has added files since the 122-file survey; three
  non-PDF files, a table text file and two NIST HTML pages, are ignored): 169
  distinct, 12 identical copies, the same 5 identical-text pairs and the Gordon 1993
  shared DOI as the earlier survey; 164 to extract. After the telemetry fix, `extract`
  of COPRA published `Geib_2019_CommonPulseRetrieval` (`VALIDATED`, exit 0); polling
  the worker's sockets every 2 s saw none and the telemetry store did not change.
  SIGTERM during a HISOL extraction returned 130 with no worker process left. A
  three-paper `batch` (3 min 42 s) skipped COPRA by digest and published
  `Travers_2019_HighEnergyPulse` (`VALIDATED`) and
  `Shelton_1990_NonlinearOpticalSusceptibilities` (`VALIDATED_WITH_WARNINGS`: the
  PDF's own date is the 2011 digitization); a rerun skipped all three with exit 0.
- Limits: no bundles, so held copies are not yet preserved in the paper; a
  cancelled batch writes no batch record (a rerun resumes by digest); no title search, so papers without a printed DOI stay unverified.

## Current checkpoint: bibliographic identity (step 5)

- `paperextract.registry`: `UrllibClient` (standard library, no new dependency)
  paces requests with a minimum interval that tightens to whatever the service
  advertises in `x-rate-limit-limit`/`x-rate-limit-interval`, retries 429/5xx with
  `Retry-After` or exponential backoff up to three attempts, and carries a contact
  email in the User-Agent **only when configured** (`contact`, default None; not
  borrowed from package metadata). `ResponseCache` stores every 200/404 response as a
  `paperextract.registry-snapshot` with retrieval time and body digest;
  `CachedClient` serves snapshots first and, when `offline`, never fetches.
  `lookup_doi` tries Crossref then DataCite by identifier and returns a
  `RegistryRecord` (plain and raw titles, structured authors with ORCID, container,
  publisher, volume/issue/pages/article number, issued/print/online date parts, URL,
  ISSN, licenses, abstract, provenance) or a `RegistryFailure`
  (`not_found`/`unavailable`/`malformed`). Live observation 22 September 2026:
  Crossref anonymous pool advertises 5 requests per 1 s; DataCite documents 3000
  requests per 5 minutes per IP. Parsers were checked against real Crossref and
  DataCite responses and kept synthetic fixtures in tests.
- `paperextract.identity`: candidates from the PDF information dictionary and
  first-page running headers (strong), the fingerprint's first two pages, and
  first-page body text (weak); reference-entry DOIs are excluded. `resolve_identity`
  compares the registry record with the observed title (normalized key; token
  overlap ≥ 0.7 warns, less fails), observed authors (family-name containment) and
  PDF date years, and decides `VALIDATED`, `VALIDATED_WITH_WARNINGS` (author/year
  disagreement, or no observed title for a strong candidate), `CONFLICT` (a strong
  candidate resolves to a different title) or `UNVERIFIED` (no candidate, nothing
  registered, weak mismatch, or registry unavailable). Every check, candidate and
  rejected alternative is serialized in `identity.json` (schema
  `paperextract.identity` 1); `work_id` derives from the validated DOI.
- `paperextract.bibtex`: deterministic key `familyYearFirstword`
  (`travers2019highenergy`), entry type from the registry type, authors in
  `Family, Given` order with literal names braced, TeX escaping, acronym/inner-capital
  protection, `pages` with `--` and article numbers in `eid`, DOI and URL; the entry is
  parsed back and checked for key, required fields, DOI and year.
- Publication integration: `resolve_and_write_identity(staging, lookup)` writes
  `identity.json`; `publish` reads it, names the directory `Family_Year_FirstWords`
  (ASCII-folded, first three significant title words, 96-byte bound, `_<sha6>` suffix
  when another source already holds the name, `Unverified_<sha12>` otherwise),
  writes `citation.bib`, `metadata.json` **schema 2** (field-level value/status/source,
  identity checks, candidates, alternatives, registry record, observations,
  bibliography validation), fills front matter and catalog row (`doi`, `title`,
  `title_key`, `authors`, `year`, `venue`, `work_id`, `bibliographic_status`), and
  copies `identity.json` into `diagnostics/raw/<run_id>/`. `ExtractionSettings.lookup`
  turns identity on in `extract_and_publish`; `default_lookup(snapshot_dir, contact,
  offline)` builds the standard client. Documented in `docs/identity.md`.
- Offline gate: 334 tests, 100% line/branch coverage, strict types, docstrings,
  spelling, lint/format, warning-free manual. The `network` lane
  (`tests/test_registry_live.py`, `PAPEREXTRACT_LIVE_REGISTRY=1`) fetched the real
  HISOL Crossref record and passed.
- **Real run** (`data/library-check-v2/`, private): the published HISOL extraction
  resolved in 0.48 s to DOI `10.1038/s41566-019-0416-4` with title, authors and year
  all `pass`, status `VALIDATED`, 15 fields validated, published as
  `Travers_2019_HighEnergyPulse` with a parse-checked `citation.bib`
  (`@article{travers2019highenergy, …}`, Nature Photonics 13(8) 547–554). The
  accepted-manuscript pages carry no DOI string, so they stay `UNVERIFIED` and
  `Unverified_eecfbf9aaa9d`, as intended. One Crossref snapshot was stored; no contact
  email was sent.
- Limits: no arXiv resolution, no Crossref title search (most pre-2000 papers in the
  dump have no printed DOI and will stay unverified until then), document version
  not established, no renaming of previously published `Unverified_` directories.

## Current checkpoint: portable paper directory export (step 4)

- `paperextract.fields` now holds the shared JSON field validators; `protocol.py`
  uses them and `document.py` gained `from_dict`/`from_json` readers for every
  class plus `block_from_dict`, so export reads persisted `document.json` instead of
  re-normalizing. Round trips are byte-stable.
- `paperextract.pdf.render_region_png` rasterizes a page region from the preserved
  PDF at a chosen DPI (300 default, 6 pt margin, clamped to the page) and encodes
  PNG with the standard library, so no imaging dependency was added. A 300 DPI crop
  of a full-page figure takes well under a second.
- `paperextract.export` renders `paper.md` deterministically from the canonical
  document: hand-written YAML front matter with JSON-quoted scalars, headings at
  backend level, prose with inline `$...$`, code, links, bold/italic/sup/sub, `$$`
  equations keeping `\tag`, lists, figures (bold label, complete-figure crop,
  published caption, notes, panel-crop links), tables (pipe table only for
  span-free complete grids, else raw backend HTML, plus notes and file links),
  asides as block quotes, page anchors as HTML comments, continuation paragraphs
  joined without a blank line, page furniture omitted, unclassified blocks as
  comments. `validation_document` (status, counts, severity counts, named checks,
  findings) and `review_markdown` are pure functions. Prose is not Markdown-escaped;
  documented.
- `paperextract.storage.publish` builds the directory under
  `<library>/.paperextract/staging/`, writes `original/source_01/<name>.pdf`
  (digest re-verified), `document.json`, figure context PNGs and panel crops,
  `tables/<id>.{html,json,csv?,jpg?}`, equation crops, `diagnostics/raw/<run_id>/`
  (native JSON, request, result, logs), `diagnostics/review.md`, `metadata.json`
  (all fields `UNVERIFIED` with observations), `extraction.json` (sources, request,
  result, asset map, notes, omitted furniture), `validation.json`, then
  `manifest.json` with every file's SHA-256, and renames the build into
  `<library>/Unverified_<sha12>/`. A second publication of the same source raises
  `PublicationConflictError`; a build failure removes the partial build. `corpus.json`
  (library id, `layout: flat`) is created on first use and one `catalog.jsonl` row
  (observed title and normalized `title_key`, observed authors/identifiers, DOI
  candidates, digests, counts, statuses, backend, run id) is appended after the
  rename. `extract_and_publish` chains the pipeline; `read_catalog` reads rows.
  Directory names stay `Unverified_…` until the identity stage validates metadata;
  nothing is invented in front matter, metadata or catalog.
- Offline gate: 286 tests, 100% line/branch coverage, strict types, docstrings,
  spelling, lint/format, warning-free manual with the new `docs/export.md`.
- **Real publication** (`data/library-check-v1/`, private): published HISOL →
  `Unverified_1c17a06cda95`, 61 files, 458-line `paper.md`, 5 figure context PNGs
  (Fig. 4 crop 2200×2215 px shows all seven panels a–g and the caption) plus 32
  panel crops, 9 equation crops, validation `COMPLETE` with every check `pass` and
  identity/cross-source `not_checked`; accepted HISOL pages 1/15 →
  `Unverified_eecfbf9aaa9d`, Table S1 as a 7-column pipe table with CSV, JSON, HTML
  and crop. Every relative link in both `paper.md` files resolves; manifests verify.
- Review points for John: the abstract paragraph is bold because the backend styled
  it so; the drop-cap “H” of the first body word is lost by the backend (“ollow”
  in superscript), a transcription defect kept verbatim; inline LaTeX carries the
  backend's spacing (`\mathrm { H E } _ { n m }`); `Unverified_` names and null
  identity fields persist until step 5.

## Current checkpoint: normalization and one-PDF pipeline (step 3)

- `paperextract.document` defines schema `paperextract.document` **0.1** (pre-1.0,
  no migration promised yet): `SourceSpan` (page, point box with top-left origin,
  raw fractional box, backend type/index), rich `InlineRun`s (text/math/code/link
  with styles), `Heading`, `Paragraph` (body/reference/footnote/aside,
  `continues_previous`), `ListBlock`, `Equation` (raw LaTeX, `\tag` label, crop),
  `Figure` (label, caption, footnotes, `Panel`s with crops and letters, context box,
  grouping `single`/`caption_run`/`unresolved`), `Table` (raw HTML, exact `TableCell`
  grid with spans, caption, footnotes, crop), `PageFurniture`, `Unclassified` (raw
  JSON), `PageRecord`, `MetadataObservation`, `Finding`. Serialization only
  (`to_dict`/`to_json`); a reader follows when export needs to load persisted files.
- `paperextract.tables.parse_html_table` places cells on a grid honoring row and
  column spans; inner HTML and decoded text are both kept, ragged rows, overflowing
  spans, invalid span attributes and stray cells are reported as problems, never
  repaired. Only the first top-level table is parsed; nested tables stay raw.
- `paperextract.normalize.normalize_mineru` maps every MinerU block type, converts
  boxes with the backend page size (PDFium fallback), reads equation labels from
  `\tag`, checks LaTeX delimiter balance, recovers a table caption misfiled as a
  footnote (`TABLE_CAPTION_FROM_FOOTNOTE`), groups consecutive visual blocks up to a
  figure-labelled caption with single-letter captions as panel labels
  (`FIGURE_GROUPING_HEURISTIC`, `CAPTION_UNMATCHED` for trailing panels), verifies
  crops against the worker's recorded files (`ASSET_MISSING`), and emits
  `PAGE_NOT_PROCESSED`/`PAGE_EMPTY`/`PAGE_SIZE_UNKNOWN` from coverage. Block IDs derive
  from source digest, page, backend type/index and position, so output is
  deterministic. Documented in `docs/normalize.md` with the findings table.
- `paperextract.pipeline.extract_pdf` runs preserve → inspect → fingerprint →
  worker → normalize for one PDF into an empty staging directory: `source.pdf`,
  `source.json` (schema `paperextract.source-record` 1), `worker/`, `document.json`.
  A failed worker result returns without a document; a malformed native document
  raises. `require_empty_directory` is now public in `paperextract.mineru`.
- Offline gate: 236 tests, 100% line/branch coverage, strict types, docstrings,
  spelling, lint/format, warning-free manual. New synthetic native fixtures cover
  every block type, malformed entries, nested lists, span tables and figure runs.
- **Real runs** (`data/backend-trials/pipeline-check-v1/`, contention-affected,
  load ~5–10): published HISOL, all ten pages, 56.18 s wall (49.31 s parse): 160
  paragraphs (78 body, 81 references, 1 footnote), 13 headings, 9 equations all with
  labels 1–9, 31 furniture blocks, **5 figures matching the five printed figures**
  (Fig. 1 single; Fig. 2 six panels with letters c/b recovered; Fig. 3 two; Fig. 4
  eighteen backend tiles for a five-panel figure, letter e recovered; Fig. 5 five),
  findings only four `FIGURE_GROUPING_HEURISTIC`. Accepted HISOL pages 1 and 15,
  10.72 s wall: Table S1 recovered with its caption from a footnote, 25 rows × 7
  columns, 175 cells; **all 42 candidate cell strings match exactly** through the
  canonical grid (6 exact including header, 36 header-representation mismatches as
  before), both equations carry labels 1 and 2 and still fail the deliberately strict
  string comparison on spacing/bracing. No value was repaired.
- Review points for John: MinerU over-segments Fig. 4 into 18 tiles, so the grouped
  figure is right but panel-level crops are not five panels; header cells flatten
  `Energy<sup>a</sup>µJ` to `EnergyaµJ` in `text` while `html` keeps the markup;
  equation comparison normalization remains an open benchmark policy.

## Current checkpoint: intake planning and fingerprints (step 2)

- `paperextract.pdf.fingerprint_pdf` digests each page's text after NFC and
  whitespace normalization, digests the whole text layer, keeps the non-empty PDF
  information entries, and lists DOI/arXiv candidates from the information
  dictionary and the first two pages. Candidates are for the identity stage, not
  identity. `normalize_text` and `identifier_candidates` are public helpers.
- `paperextract.ingest.plan_intake` hashes a batch, groups identical bytes with a
  deterministic primary (first path in lexicographic order) and aliases, looks up
  digests in a caller-supplied `known` mapping (`reuse` versus `extract`), rejects
  non-files, non-PDF signatures and PDFium-unreadable files with reasons, and
  fingerprints each primary. `discover_pdfs` lists top-level PDFs only. The plan
  serializes as schema `paperextract.intake-plan` version 1. Nothing is copied,
  moved or deleted. See `docs/ingest.md`.
- Offline gate: 201 tests, 100% line/branch coverage, strict types, docstrings,
  spelling, lint/format, warning-free manual. Synthetic PDFs come from a shared
  `tests/conftest.py` builder that writes Helvetica text pages without an xref
  table, so PDFium's reconstruction path is exercised too.
- **Real run on the materials dump** (read-only, 4.37 s for 122 files): 112
  groups, 10 alias pairs, 0 rejections, 49 groups with a DOI candidate, one with an
  arXiv candidate, one file with no text layer at all. Beyond the byte-identical
  pairs, **five pairs of different files share an identical text fingerprint**
  (Marchetti 2006, Zhang 2008, Huber 1974, Peck 1977, Nibbering 1997: publisher and
  author-named downloads of the same PDF), so the dump holds at most 107 distinct
  documents. Two further pairs share a first DOI candidate with different text
  (Marchetti again, Gordon 1993 with a cover page). Plan saved privately as
  `data/backend-trials/intake-materialpapers-v1.json`. Tier 2 automation remains P5.

## Materials dump survey (22 September 2026)

Source root: a private directory outside Git.
Read-only survey with Poppler `pdfinfo`/`pdftotext` and `shasum`; nothing was
copied, renamed, extracted or benchmarked. No manifest or fingerprints recorded yet.

| Observation | Count |
| --- | --- |
| PDF files (one flat directory, plus `.DS_Store`) | 122 |
| PDF pages in total (largest files 72 and 90 pages) | 1068 |
| Distinct SHA-256 digests | 112 |
| Byte-identical pairs, several under unrelated filenames | 10 |
| Same-DOI pairs with different bytes | 2 |
| Files byte-identical to pilot-corpus papers (Wahlstrand 2012, Shelton 1990) | 2 |
| Files with a DOI string in the first two pages of text | 37 |

Of the two same-DOI pairs, one is a re-optimized Elsevier download with identical
page text (tier 2 in Plan §15) and one is an AIP re-issue with a cover page and a
different OCR layer (tier 3: same work, different representation). The Lehmeier 1985
paper appears as a different scan from the pilot corpus copy, with no shared
per-page text digests (tier 4 only). Most files are 1950s–1990s articles without
printed DOIs, so title/author/year lookup and registry queries matter more than
embedded DOIs for this batch. These counts justify the tiered design; they are not
a validated inventory. Do not process this directory until identical-byte
detection exists; then run `dedup` before `batch`.

## Corpus inspection checkpoint

Source root: a private directory outside Git.
Private evidence: `data/corpus-inspection/` in this checkout.

- Five distinct works; six main/accepted PDFs (75 pages), plus the published HISOL
  supplement (15 pages). Fifteen author figure PDFs are reference assets.
- Complete-webpage and MHTML captures for COPRA and HISOL are included in the
  frozen inventory. It contains 187 non-Finder files. Later additions require
  an inventory update, not a silent change to an existing benchmark run.
- The HISOL accepted PDF has 28 pages including supplementary material. The
  published main PDF has 10 pages and its supplement is a separate source.
- `manifest.json` maps source hashes, relative paths, work/version identities and
  evidence. It is draft data, not a validated runtime schema.
- `gold-candidates.json` contains 42 exact Table S1 cells (4 deliberately blank)
  and 2 display equations, checked against TeX and rendered accepted-PDF pages.
  These are agent-reviewed candidates, not owner-approved or executable gold tests.
- COPRA has no captured article figure payloads in either HTML or MHTML. Its maths
  survives as 299 MathJax SVG containers, with no `<math>` elements found.
- HISOL complete HTML has all five article figures at 685 pixels wide; its MHTML
  embeds only Fig. 1. Both contain ten MathML elements. Page widgets duplicate
  scientific objects; raw element counts must not become output object counts.
- No saved webpage scripts were executed, remote assets fetched or source PDFs
  rewritten. Poppler and bundled read-only PDF/HTML tools were used for inspection;
  they were not added as application dependencies.
- Proposed holdout: Wahlstrand. Inventory and first-page inspection only so far;
  no backend tuning has used it.

Inventory fingerprint, using the encoding documented in `manifest.json`:

```text
f8cdbc8dd528fbee5a97fc30c2c8d3d5e12d32e0f9deebc01a742e7a7f3766c1
```

## Resume in this order

1. Read this file, `dev/Corpus.md`, and Plan §§2, 16, 18–19. Inspect
   `git status --short` and `git log --oneline -5`; preserve any uncommitted work.
2. Confirm private sources and inspection records are present and still match
   the recorded hashes. If moving agents on the same checkout they remain local;
   a Git clone or source distribution does **not** contain these private artifacts.
3. Read `docs/benchmark.md` and `src/paperextract/benchmark.py`. Schema v1 now
   validates source hashes, paths, reference scope and exact/normalized comparisons.
   Use private `data/corpus-inspection/benchmark-v1.json` (7 PDFs, 44 candidates);
   retain candidate status and establish actual object alignment independently.
4. Read `dev/Trials.md` and the private model/run ledgers. Worker environments and
   initial trials exist; do not repeat setup blindly. Finish the underlying MinerU
   model-license audit and broader scientific checks. Keep core `.venv` lightweight
   and remote AI disabled. Saved scratch scripts are experiments, not a public CLI.
5. Extend the completed small Mac smoke comparison: accepted HISOL pages 1/15, published HISOL
   and supplement separately, Shelton page 6; extend to full papers, COPRA and
   Lehmeier. Cover inline math, exact numbers, figure/caption geometry and all-page
   behavior. Keep raw backend output and failures. MinerU is selected for the
   first adapter; do not repeat selection without new evidence.
6. Continue the P2 end-to-end slice with the isolated worker protocol/adapter.
   `preserve_pdf` in `src/paperextract/ingest.py` now handles source copying.
   Add enough structure for real supplied papers; defer abstractions that lack
   a demonstrated need.

Steps 1–6 of the P2 sequence are implemented and verified; see the checkpoints
above and `docs/cli.md`, `docs/worker.md`, `docs/ingest.md`, `docs/normalize.md`,
`docs/export.md`, `docs/identity.md`. The next step is the **first full paper
review with John** of a directory published by the command line, then the
materials batch through `paperextract batch` once he agrees. Open decisions for
him: Fig. 4 panel over-segmentation, flattened table header
text, equation comparison strictness, and naming of papers without a DOI.

Facts checked in the installed MinerU 4.0.5 source and the private raw outputs
(22 September 2026): `MinerUParser(tier, parse_mode, image_analysis, vlm_config)`
and `parse(path, page_range=...)` take a one-based inclusive range string such as
`"1-5,r3-r1"`; a subset is rewritten through pdfium and unreadable pages return as
empty `PageInfo` placeholders, so the worker must report requested, retained and
broken page indices explicitly. `ParseResult.to_json()` emits schema
`docvortex.middle` version `2.0` with `pages[].blocks[]`, block `bbox` values as
page fractions in `[0, 1]`, and an `extensions.docvortex_layout.pages[]` entry with
`width_pt`/`height_pt` per page; convert to §4.3 point coordinates using those.
`ParseResult.save(FileBasedDataWriter(dir))` materializes `markdown.md`,
`middle_json.json`, `structured_content.json`, `model_output.json` and `images/`.
Observed block types: `text`, `ref_text`, `paragraph_title`, `doc_title`, `header`,
`footer`, `page_footnote`, `equation`, `image`, `chart` (with nested `chart_caption`
and `chart_body`), plus tables on other pages. Embedded PDF metadata surfaces under
`metadata.document` (title, authors, identifiers such as `doi:...`, page count).
Intake should compute SHA-256 against the batch and library before scheduling
work (Plan §15 tier 1) and store page count and per-page text digests as the
content fingerprint; publication should append the Plan §5 catalog row. Verified
in the worker smoke: returned `page_idx` values keep the original zero-based
numbering for a page subset, `save()` writes 41 image crops for the ten-page
paper, and the worker's PDFium page count agreed with the coordinator's.

Next, normalize raw scientific structures without repairing values. For figures,
retain useful individual panel crops **and** complete figure context, with explicit
parent, caption, label and page relations. The full ten-page HISOL output and
published PDF supply the immediate grouping test case. Ambiguous grouping stays
visible for review. Metadata/BibTeX, transactional portable bundles and the public
CLI followed as steps 4–6.

No additional corpus or user answer blocked steps 1–6. Ask later only
for material choices that require John's judgment: ambiguous reference alignments,
useful accuracy/latency thresholds, provider spend and cluster details. Do not ask
again whether the five-paper pilot is sufficient.

## Verification evidence

Earlier bootstrap session:

- Full offline gate passed on Python 3.12.13 and 3.14.5: lint, formatting, strict
  types, docstrings, spelling, two scaffold tests, coverage and warning-free docs.
- `uv lock --check --offline`, wheel/sdist builds and a clean installed-wheel
  import/metadata/typed-marker check passed. These are packaging checks, not proof
  of extraction accuracy. The scaffold's 100% coverage covers almost no behavior.
- CI is configured for Linux/macOS and Python 3.12/3.14 but has not run remotely.

Prior corpus/tracker session:

- PDF metadata/text inspection and eleven representative page renders inspected.
- Both MHTML containers decoded and article-image payloads checked; both complete
  HTML captures inspected as data. Sixteen HISOL ZIP members match extracted files.
- Private reference records checked for 44 candidates, 42 cells, 4 blanks and
  locators tied to inventoried source hashes. Formal schema validation remains open.
- Full gate passed on Python 3.12.13: lint/format, zero strict-type diagnostics,
  docstrings, spelling, two scaffold tests and warning-free Sphinx docs. Included
  Markdown links were corrected after the documentation gate identified them.
- Lock consistency and wheel/sdist builds passed offline. Archive inspection
  confirms status/corpus docs are included and private data is excluded; the wheel
  retains its typing marker and Apache notices.
- All 187 corpus files match recorded hashes; no unrecorded files remain in the
  snapshot. `dev/Brief.md` matches Git byte for byte; `git diff --check` passed.
- The initial gate attempt triggered Poe's automatic nested uv dependency refresh
  and failed at sandbox DNS. Cached offline execution succeeded. Poe now uses its
  simple executor inside `uv run` to prevent that redundant resolution.
- No backend quality, model fit, cold/warm timing or GPU throughput measurements.

The sandbox cannot use the default uv cache, so successful offline commands use
`UV_CACHE_DIR=/tmp/paperextract-uv-cache uv run --offline --no-sync poe check`.
The main `.venv` remains Python 3.12. Dependencies and the lockfile have not changed
during corpus inspection. The source archive now includes the status/corpus docs;
the private corpus and generated inspection outputs must remain excluded.

## Session log

- **2026-09-22 — planning/bootstrap:** read all of the brief; researched current
  backends and capture/runtime options; wrote the plan and project scaffold;
  verified offline gates, builds and license metadata. No application features.
- **2026-09-22 — corpus/P1 start:** inspected supplied papers and author sources;
  incorporated added MHTML captures and the published supplement; recorded capture
  defects and version distinctions; seeded private manifest/reference candidates;
  added this handoff tracker and its maintenance requirement to `AGENTS.md`.
  John then explicitly approved the plan and requested logical commits.

- **2026-09-22 — P1 benchmark implementation:** added manifest/reference validator
  and exact/scoped comparisons, 72 new synthetic cases, manual/API documentation;
  validated the private converted corpus. All 74 tests and the full gate passed.
  Timing contention recorded. Next: isolated workers and actual model trials.

- **2026-09-22 — P1 runtime setup:** installed all three separately locked workers;
  confirmed MPS availability; downloaded/hash-pinned three Docling model repositories.
  MPS-only formula smoke failed; mixed MPS-layout/CPU-formula retry in progress.
  Full core gate and package build passed after excluding ignored model caches
  from spelling checks; no source check was suppressed.

- **2026-09-22 — all-three smoke comparison:** Docling, Marker and MinerU ran on
  accepted HISOL pages 1/15 with repeated calls. Marker breaks numerical-table
  topology and decimal values; MinerU retains all 42 candidate strings. Full
  published HISOL ran twice with MinerU; complete page coverage and deterministic
  raw output, but figure composites require reconstruction/review.
- **2026-09-22 — first adapter/P2 ingest:** forced-OCR scan spot checks completed;
  selected MinerU, recorded public model pins and revised the plan. John explicitly
  requests useful panel crops alongside complete figure context, shared captions,
  panel labels and source-page links. PDF preservation implemented with 15 new
  tests: all 89 tests, 100% core line/branch coverage, strict types, lint/format,
  docstrings, spelling and warning-free docs pass. Wheel/sdist builds passed;
  no worker adapter or extraction CLI exists yet.
- **2026-09-22 — review and library-scale planning:** reviewed status, brief, plan,
  trials, corpus notes, ingest code and the installed MinerU API; surveyed John's
  first real batch (122 PDFs, 112 distinct digests, byte-identical, same-DOI and
  same-work-different-scan duplicates). Added Plan §5 library layout/index and §15
  tiered duplicate-handling notes with verified sources, CLI proposals, work-package
  and risk updates. No code changed; spelling and documentation gates rerun.
- **2026-09-23 — P3 describe stage:** prompt v2 and a rented H100 (vLLM,
  about $2.70, machine deleted) compared Qwen3.8-27B, Molmo2-8B, InternVL3.5-38B
  and Opus 5.5; John chose Qwen3.8-27B v2 on GPU with Opus 5.5 for careful jobs.
  Implemented `describe` with three backends, labelled publication, index
  column and docs. Gate passed (576 tests, 100% coverage).
- **2026-09-23 — P3 model trial:** four local models downloaded at pinned
  revisions; describe module, MLX worker and trial scripts; Claude comparison
  ($1.75); printed-string check corrected for captions, outlined text and OCR
  layers. Gate passed (545 tests, 100% coverage); nothing committed.
- **2026-09-23 — items 1–3 and research:** reprocess with journaled
  replacement, corroborated Crossref search identity, SQLite/FTS library index
  with lookup and search; Docling table trial; P3 model and P4 research. Gate
  passed (524 tests, 100% coverage).
- **2026-09-23 — P4/P5 and agent use:** Docling backend and table check, HTML
  cross-check, versions and relations, arXiv, manifests, layouts and
  `organize`, the agent skill and equivalent copies, then Marker and
  `compare`, then `migrate` and record version guards; see the checkpoints
  above. Gate passed (703 tests, 100% coverage).
- **2026-09-23 — table OCR and supplements:** added `table_ocr.py`,
  `supplements.py`, table body alternatives (schema 0.2), bundle extraction,
  supplement publication with anchors and links, batch pairing, `--supplement`
  and `--no-table-ocr`. Gate passed (484 tests, 100% coverage). Real runs on
  COPRA, HISOL with supplement and Shelton.
- **2026-09-22 — first paper review:** John's comments on three published
  papers; added `layout.py` (repeated furniture, split reference columns, drop
  capitals from the text layer, unmapped-glyph findings), Roman and continued table
  labels, pipe tables with cell math and merged cells, page-end footnotes and
  caption-attached panel links. Moved the ONNX Runtime store to the Trash and set
  John's Crossref contact at his request. OCR-mode trial recovered COPRA Table 1.
  Gate passed (432 tests, 100% coverage); three papers re-published.
- **2026-09-22 — command line (P2 step 6):** added `config.py`, `cli.py`,
  `__main__.py` and the console script; tier-2 identical-text holding and tier-3
  shared-DOI review in `ingest.py`; `docs/cli.md`. Fixed worker cancellation, run
  directory collisions and **ONNX Runtime telemetry** (now disabled; it had been
  active since the first MinerU trial). Gate passed (405 tests, 100% coverage).
  Real `dedup` on 181 dump files, `extract` and a three-paper `batch` published
  three validated papers; SIGTERM cancellation verified.
- **2026-09-22 — bibliographic identity (P2 step 5):** added the paced,
  snapshot-cached registry client with Crossref/DataCite parsers, identity resolution
  with candidate strength and field-level status, BibTeX generation with parse check,
  validated directory names, metadata schema 2 and catalog identity fields; 60 new
  tests and `docs/identity.md`. Gate passed (334 tests, 100% coverage); live lane
  passed. Real run validated the published HISOL paper against its DOI and
  published it as `Travers_2019_HighEnergyPulse` with `citation.bib`.
- **2026-09-22 — portable export (P2 step 4):** added shared field validators,
  document readers, stdlib PNG region rendering, Markdown/report exporters, the
  transactional publisher with manifest, corpus record and catalog row, and
  `extract_and_publish`; 50 new tests, `docs/export.md`. Gate passed (286 tests,
  100% coverage). Published both real HISOL extractions into a private library;
  all links resolve and the Fig. 4 context crop shows the complete seven-panel figure.
- **2026-09-22 — normalization and pipeline (P2 step 3):** added the canonical
  document schema 0.1, exact HTML table grid parsing, MinerU normalization with
  heuristic figure grouping and findings, the one-PDF pipeline, 35 tests and the
  manual page. Gate passed (236 tests, 100% coverage). Real HISOL runs: five figures
  grouped correctly, all 42 candidate cells matched through canonical cells.
- **2026-09-22 — intake planning (P2 step 2):** added content fingerprints and
  identifier candidates to `pdf.py`, batch planning with identical-byte aliases,
  rejections and a serialized plan to `ingest.py`, a shared synthetic PDF builder
  and 12 tests. Gate passed (201 tests, 100% coverage). Read-only run on the 122-file
  dump found 112 groups, 10 aliases and five content-equivalent pairs with
  different bytes.
- **2026-09-22 — worker boundary (P2 step 1):** John approved pypdfium2 in core,
  committing the plan changes and starting the boundary. Added `pdf.py`,
  `protocol.py`, `mineru.py`, the standalone MinerU worker script, 100 new tests,
  `docs/worker.md` and the opt-in `backend` smoke lane. Full offline gate passed
  (189 tests, 100% coverage). Real smoke: first run exposed and fixed a version
  lookup crash; second run completed all ten HISOL pages with output identical to
  the direct trial. Contention-affected timing recorded; no remote calls.
