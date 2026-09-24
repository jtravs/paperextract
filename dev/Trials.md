# Mac qualification ledger

Initial evidence, 22 September 2026. P1 qualification continues; MinerU is selected for the first
P2 adapter, subject to the explicit limitations below. Private scripts, outputs, model cards and measurements are in
`data/backend-trials/`. These are not carried by Git clones. See `dev/Status.md`
for the active command and next action.

## Hardware and measurement scope

Observed Apple M5 Max, 128 GiB unified memory, 18 CPU cores; Python 3.12.13.
PyTorch 2.14.0 reports MPS built and available. John explicitly reports background
work on this Mac. All measurements below are **contention-affected feasibility
observations**, requiring quieter repeats before latency comparisons. No unrelated
process was stopped. No remote AI or paid compute was used.

Dependencies are pinned independently under `workers/`; core dependencies remain
unchanged. Package installation, model download, imports/configuration, first
conversion with cached model files, and reused-converter calls are distinct phases.
Times below measure conversion plus an explicit MPS synchronization; export occurs
after the timer. They exclude imports, package installation and model download.
They include lazy pipeline/model initialization on the first conversion.

RSS is sampled every 0.2 seconds across the worker and its descendants. Summed
RSS may double-count shared pages and miss short peaks. It is not total unified
memory consumption. MPS allocated/driver values are post-call snapshots, not peak
GPU memory, and must not be added to RSS. Load averages, system memory and swap
counters are retained before/after each call. OS filesystem caches were not flushed.

## Docling: selected accepted-manuscript pages

Docling 2.129.0, docling-core 2.98.0, docling-ibm-models 4.0.3 and Transformers
5.17.0. Full transitive versions and hashes are in `workers/docling/uv.lock`.

A first run forcing MPS throughout failed during formula-engine initialization:
`AcceleratorDeviceNotAvailableError`. Both the legacy formula stage and the current
Transformers VLM engine exclude MPS; the current standard pipeline uses the latter.
The failed configuration and traceback remain in `docling-smoke-v1/` and its log.
Do not infer formula-device support from general package MPS support.

The successful retry uses explicit MPS layout, CPU formula enrichment, four CPU
threads, table structure, page/picture images, and OCR disabled. This is a digital
PDF baseline; it does not qualify scans. Hub offline mode and remote-service
permission disabled; models were downloaded separately and frozen first.

| Input/call | Conversion time | Sampled process-tree peak RSS |
| --- | --- | --- |
| Accepted HISOL page 1, first conversion/cached files | 52.06 s | 1.98 GB |
| Page 1, converter reused | 45.13 s | 2.01 GB |
| Page 15, converter reused | 1.88 s | 2.07 GB |
| Page 15, repeated | 1.87 s | 2.07 GB |

These are four calls on two pages, not document throughput or a statistical ranking.
The one-minute load average ranged from 4.41 before the first call to 7.17 after
the second. Post-call MPS driver allocation was approximately 1.29–1.30 GB.

### Scientific observations

Raw JSON, Markdown and resource reports are in `docling-smoke-v2/`. The private
`comparison.json` records explicit manual alignments to the versioned candidates.
The 44 references remain candidates; no reviewed gold score is claimed.

- All **38 nonblank candidate cell strings** match the raw table-cell objects,
  preserving decimal precision. The four known blank positions have no explicit
  objects in `table_cells`; they appear as empty placeholders in the derived
  `grid`. Grid strings match all 42 cell candidates, but placeholder origin must
  stay distinguishable from observed blank evidence.
- Table headers are flat strings. Units, footnote letters, subscripts and Unicode
  distinctions need separate handling. Comparing these unchanged headers to the
  structured candidate header paths yields 32 header mismatches, six exact
  comparisons (the simple `N` header), and four missing raw cells. This is an
  annotation/representation comparison, not 32 corrupted numeric values.
- Two display equations were detected with page/bounding-box provenance. Both
  fail the deliberately conservative string comparison. Spacing/bracing differs;
  the second also changes upright `loss` to separate mathematical letters. These
  need review; do not replace the reference or broaden normalization to make
  the benchmark pass.
- Inline mathematical notation in prose loses subscript/superscript structure;
  examples include mode indices and the Bessel-function order. Display formula
  enrichment alone does not satisfy inline-math requirements.
- The page-15 table has no attached caption reference in this output. Caption,
  footnote associations and table topology still require qualification.
- Upstream logs warn about the model's padding-token configuration and unequal
  supposedly tied weights. Those warnings were retained, not silenced or repaired.

### Frozen model evidence

Private `docling-models.json` contains the exact downloaded file hashes and cards.
No weights are distributed with this repository.

| Repository | Commit | Declared model-card license |
| --- | --- | --- |
| `docling-project/docling-layout-heron` | `8f39ad3c0b4c58e9c2d2c84a38465abf757272d8` | Apache-2.0 |
| `docling-project/docling-models`, tag `v2.3.0` | `fc0f2d45e2218ea24bce5045f58a389aed16dc23` | CDLA-Permissive-2.0 |
| `docling-project/CodeFormulaV2` | ecedbe111d15c2dc60bfd4a823cbe80127b58af4 | CDLA-Permissive-2.0 |

Primary model cards: [Heron](https://huggingface.co/docling-project/docling-layout-heron),
[TableFormer bundle](https://huggingface.co/docling-project/docling-models/tree/v2.3.0),
[CodeFormulaV2](https://huggingface.co/docling-project/CodeFormulaV2).

## MinerU: selected accepted-manuscript pages

MinerU 4.0.5, standard tier, automatic text/OCR choice (resolved to `txt` for these
pages), CPU ONNX small models and the packaged local llama.cpp VLM, one VLM request
at a time, four intra-operation CPU threads and one inter-operation thread. Image
analysis disabled; no remote VLM URL. Source resolution is `local`, Hub offline.

| Input/call | Conversion time | Sampled process-tree peak RSS |
| --- | --- | --- |
| Accepted HISOL page 1, first conversion/cached files | 21.74 s | 4.45 GB |
| Page 1, parser/models reused | 4.94 s | 5.15 GB |
| Page 15, parser/models reused | 0.69 s | 5.16 GB |
| Page 15, repeated | 0.61 s | 5.17 GB |

Private `mineru-smoke-v2/` includes all raw JSON, materialized output, resource
measurements and candidate comparisons. All 42 candidate cell strings match,
including four explicit empty HTML cells. Footnotes remain associated with the
table, although its caption is classified as another table footnote. Header
subscripts/footnote markup are partly retained but inconsistent. Flattened diagnostic
headers yield six exact comparisons and 36 header mismatches; this is not a count
of corrupt values. Both display equations require reviewed normalization and still
fail the strict string comparison. More inline mathematical structure and equation
numbers survive than in the Docling sample, but some math spans absorb neighboring
punctuation or ordinary text. No aggregate quality winner is declared.

The first attempt (`mineru-smoke-v1`) failed because the scratch harness omitted
its macOS multiprocessing entry-point guard. That harness bug was fixed before
these measurements. A native log also reports an optional Metal tensor API shader
compilation failure and disables that API. Do not interpret a Torch MPS counter as
memory used by the separate llama.cpp Metal runtime; stage/device instrumentation
still needs improvement.

## MinerU: complete published paper

The ten-page published HISOL PDF was processed twice sequentially with the same
standard profile: **47.72 s** first conversion/cached files and **39.59 s** reused
worker, with sampled process-tree RSS of **6.80/7.17 GB**. Imports, setup and export
are excluded. Both outputs contain all page indices 0–9 and are byte-identical in
the serialized raw JSON (SHA-256
`fac75951283d173f005d95f687c9c0afc83d0cb0d41681844e6737cdebbc6d24`).
This is one paper on a busy machine, not a batch-throughput estimate.

All five main figure captions survive. However, multi-panel figures are fragmented
across **27 chart blocks and five image blocks**, with full captions attached to
particular panels; some panel letters become captions too. There are 41 exported
image assets including equation crops. Neither asset count nor image-block count
is a valid figure count. Preserve full composite context from the original PDF
and validate panel/caption associations before figure descriptions. This is a
required wrapper task, not evidence that five complete figures were extracted.
John explicitly values the individual panel crops too: preserve them alongside
complete figure context and link each to its parent, shared caption, panel label
and source page. Uncertain associations remain candidates for review. Cropping
itself is useful; loss of context or an incorrect grouping is the defect.

Private evidence: `mineru-published-v1/`, with raw and materialized output, resource
reports and the full run log. A forced-OCR scan check is the next qualification.

## Marker: selected accepted-manuscript pages

Marker 2.0.0 with Surya 0.22.1, explicit balanced profile, remote LLM processing
disabled. The pinned native llama.cpp b10964 server was launched by the trial on
loopback with one request slot, 16384 context tokens and four threads. It was
terminated by its owning script after the trial. Server startup was **16.62 s**,
recorded separately from conversion; later calls reused the models/server but
constructed a new converter to set each page range.

| Input/call | Conversion time | Sampled process-tree peak RSS |
| --- | --- | --- |
| Accepted HISOL page 1, ready server/first conversion | 51.43 s | 3.97 GB |
| Page 1, models/server reused | 22.84 s | 4.05 GB |
| Page 15, models/server reused | 1.92 s | 4.10 GB |
| Page 15, repeated | 1.61 s | 4.14 GB |

Raw evidence is in `marker-smoke-v1/`. Page 1 retains useful inline math,
display formulas and equation numbers, with source page/geometry. This sample
looks promising for math, though exact semantic quality still needs review.

**Critical table failure:** native text-based table reconstruction produces ten
columns for a seven-column table. Decimal points disappear while integer and
fractional parts are split across cells: a source `1.8` becomes adjacent `1` and
`8`. Headers also split and shift. Do not concatenate these cells or infer missing
decimal points. The changed topology makes affected cell alignment unresolved;
a numeric score based blindly on column indices would be misleading. The profile
fails the scientific table gate despite successful conversion status. The caption
is recognized separately. Test a different table/OCR path later if retaining Marker
for this material, but keep this failure as a regression case.

## Model licensing and standalone runtime
Marker requires a native llama-server for its local VLM on this Mac. The system
PATH had no such binary; the trial used a pinned official user-space release.
The exact binary archive and digest are recorded privately.

Surya's downloaded license is **modified** OpenRAIL and includes terms concerning
outputs; the model-card shorthand is not a complete license description. Keep raw
results private and review those terms before any redistribution. MinerU's ONNX
bundle refers to source-model provenance/licenses in its manifest; its GGUF
conversion repository has no model card. Record underlying model evidence rather
than assigning the wrapper's license to either bundle.

## MinerU through the worker boundary

On 22 September 2026 the published ten-page HISOL PDF was processed again, this
time through the implemented protocol-version-1 boundary (`paperextract.mineru`
starting `workers/mineru/paperextract_mineru_worker.py` as a subprocess with a
minimal offline environment). The run completed with all ten pages returned and
none empty: 52.60 s parse, 0.16 s save, 0.70 s process start-up before request
handling, worker maximum resident size 6.09 GB, one-minute load average 3.96
before and 7.72 after. The saved `native/middle_json.json` is byte-identical to
the direct trial in `mineru-published-v1/run-0/`, so the boundary reproduces
MinerU's output exactly. A first attempt failed inside the worker on a missing
`mineru.__version__` attribute; the adapter surfaced it as a process error with
the log tail, and the worker now reads the distribution version. Evidence:
`data/backend-trials/worker-smoke-v1/` and `worker-smoke-v2/`. This is one
contention-affected run, not a throughput measurement.

## End-to-end pipeline check on HISOL

On 22 September 2026 the one-PDF pipeline (preserve, inspect, fingerprint,
worker, normalize) ran on the published ten-page HISOL PDF in 56.18 s wall time
(49.31 s parse, load average about 5–10) and on accepted HISOL pages 1 and 15 in
10.72 s. Normalization grouped the published paper's 27 chart and 5 image blocks
into exactly five figures matching the printed figures, with the six-panel Fig. 2
and the eighteen-tile Fig. 4 flagged `FIGURE_GROUPING_HEURISTIC`; all nine display
equations carry their printed numbers. On the accepted supplement page, Table S1's
caption was recovered from a misclassified footnote and its 175 cells were placed
on a 25 × 7 grid; all 42 candidate cell strings match the canonical `text` values
exactly, six of them also matching the flattened header, and both page-1 equations
still fail the strict string comparison on spacing and bracing. Evidence:
`data/backend-trials/pipeline-check-v1/` (staging directories, `document.json`
files and `report.json`). This is one contention-affected run per input, not a
throughput measurement, and the comparison keeps candidate status.

## Portable export of the HISOL extractions

On 22 September 2026 both pipeline results were published into a private library
(`data/library-check-v1/`). The published paper became a 61-file directory with a
458-line `paper.md`, five 300 DPI complete-figure crops rendered from the preserved
PDF, 32 backend panel crops and nine equation crops; its validation report is
`COMPLETE` with every implemented check passing. Visual inspection of the Fig. 2
and Fig. 4 crops confirmed complete composites with all panels and the caption,
including Fig. 4 where the backend had produced eighteen tiles. The accepted
supplement pages published Table S1 as a seven-column pipe table with matching CSV,
JSON, HTML and crop. Every relative link in both Markdown files resolves and every
manifest digest verifies. Identity fields are null and directories are named
`Unverified_<sha12>` because the bibliographic stage does not exist yet.

## Identity resolution of the HISOL extractions

On 22 September 2026 the identity stage ran on both pipeline results with the
standard Crossref/DataCite lookup and a fresh snapshot cache. The published paper
resolved in 0.48 s: its single DOI candidate came from the PDF information
dictionary, the first-page running header and the fingerprint together; the
Crossref record's title, first author and year all agreed with the article, so the
identity was `VALIDATED`, fifteen fields were filled from the record, and the paper
was published as `Travers_2019_HighEnergyPulse` with a parse-checked
`citation.bib`. The accepted-manuscript pages contain no DOI string and stayed
`UNVERIFIED`. The live registry lane fetched the same record independently. One
response snapshot was written and no contact email was sent. Evidence:
`data/library-check-v2/` (private).

## Command line runs and the ONNX Runtime telemetry finding

On 22 September 2026 the new command line ran read-only `dedup` over the 181 PDFs
now in the materials dump (6.8 s; 169 distinct, 12 identical copies, 5
identical-text pairs, 164 to extract), then real extractions into a private
library. The first extraction attempt of COPRA completed its parse (58.8 s model
inference, 11 pages) but the worker process aborted with SIGABRT afterwards, inside
ONNX Runtime 1.30.0's telemetry thread while it handled an HTTP response. ONNX
Runtime's POSIX telemetry (release notes of 1.29.0) is disabled only by
`ORT_DISABLE_TELEMETRY=1` before initialization; the worker had not set it, so
every earlier MinerU trial ran with telemetry enabled. With the variable set, COPRA
extracted and validated with exit 0, no worker sockets were observed by 2 s
polling, and the local telemetry queue was unchanged. A three-paper `batch` took
3 min 42 s on a machine also running other work: HISOL and Shelton 1990 published
with validated identity and COPRA was skipped by its digest. SIGTERM during an
extraction returned exit 130 and left no worker process. Evidence:
`data/cli-check-v1/` (private).

## OCR mode for a text-layer table with unmapped glyphs

COPRA Table 1 (page 2) is set in a math font whose brackets have no Unicode
mapping. In MinerU's default `auto` mode the table cells come from the text layer
and contain control characters instead of brackets, so the formulas are
unreadable. On 22 September 2026 the same worker ran page 2 alone with
`parse_mode="ocr"`: it completed in 26.3 s including model loading, and the table
came back as clean LaTeX in `<eq>` cells, for example
`\mathcal{F}^{-1}[e^{i\tau\omega}\tilde{E}]\mathcal{F}^{-1}[\tilde{E}]`, without the
spurious first row of the text-layer version. Other pages and numerical tables
were not compared, so this is evidence for a targeted re-extraction, not for OCR
mode in general. Evidence: `data/backend-trials/copra-table-ocr-v1/` and its
script (private). In the implemented pipeline stage (23 September
2026) all three COPRA tables took the OCR body because every number agreed with the
text layer (7, 5 and 3 numbers); the text-layer bodies are kept as alternatives.

## Docling tables against MinerU on the pilot papers

On 23 September 2026 the isolated Docling 2.129.0 worker ran offline with the
frozen P1 models (Heron layout on MPS, accurate TableFormer with cell matching,
formula enrichment and OCR off) on every page where MinerU found a table: Shelton
pages 6, 7, 8, 9 and 11, COPRA pages 2, 3 and 8, and accepted HISOL page 15. Each
Docling table was paired with the MinerU table on the page sharing most cell
strings, and their numbers were compared after joining digit groups split by
spaces and removing LaTeX markup and Docling's unmapped-glyph codes.

| Paper and table | Docling numbers | MinerU numbers | Agreeing |
| --- | --- | --- | --- |
| COPRA Tables 1–3 (born digital; MinerU body from OCR) | 7, 5, 3 | 7, 5, 3 | 100% each |
| Shelton Table I (2011 scan with OCR text layer) | 157 | 163 | 155 (95%) |
| Shelton Table II | 13 | 23 | 12 (52%) |
| Shelton Table III, both pages | 292, 250 | 279, 239 | 86%, 76% |
| Shelton Table IV | 70 | 75 | 66 (88%) |
| Shelton Table V | 42 | 69 | 42 (61%) |

Accepted HISOL page 15 again gave the clean 25x7 benchmark table. On Shelton,
Docling takes cell text from the scan's own OCR text layer: spaces inside numbers
("2. 534"), "+" for "±", wavenumbers split ("14 399") and garbled headers; two
tables lost a header column or row. MinerU's cells for the same tables are clean
LaTeX (`2.534 \pm 0.019`). With forced Tesseract full-page OCR the captions
changed but the table cells did not, because cell matching still used the text
layer. COPRA's text layer gives Docling the same unmapped-glyph codes (".0137")
that MinerU's text mode had. Conclusion: on born-digital tables Docling is an
independent and agreeing second reading, useful as a numerical cross-check; on
old scans with poor text layers it is weaker than MinerU unless a table OCR path
that bypasses the text layer is qualified. Timing: 0.4–11 s per page after a
cold first page. Evidence: `data/backend-trials/docling-tables-v1/`,
`docling-tables-ocr-v1/` and their scripts (private).

## Identity search on papers without a DOI

Page 1 of six DOI-less papers from the materials dump was extracted and identified
with the new Crossref bibliographic search (23 September 2026). Augst et al. 1991
and Cuthbertson 1932 were identified directly. Mizrahi and Shelton 1985, a scan,
needed two fixes found here: inline math in the heading (`\mathrm{H}_{2}`) is now
flattened for comparison, and Crossref's title, which drops the spaces around
MathML, now matches with spaces ignored and parses with word boundaries.
Bideau-Mehu et al. 1981 carried an Elsevier PII as its PDF title; implausible PDF
titles are now skipped and the PII yields the DOI `10.1016/0022-4073(81)90057-1`,
which then validated against the heading. Ammosov et al. 1986 correctly stays
unverified: the only close Crossref hit is a differently titled SPIE record. All
fixes were applied with `reprocess --refresh-identity` in seconds. Evidence:
`data/search-check-v1/` (private).

## Forced-OCR scan check and first-adapter decision

Shelton page 6 was processed with the MinerU standard profile and OCR explicitly
forced: 15.26 s first conversion and 12.64 s reused, sampled process-tree RSS
3.21/3.44 GB. Visual comparison used the immutable PDF and a 300-DPI table render
(`data/backend-trials/shelton-table-check.png`). The output retains two-level
headers with row/column spans, uncertainty expressions and deliberate empty cells.
Spot checks include the Ar/N2 and D2/H2 density ratios and the final row's blanks;
these are agent visual checks, not completed reviewed scan gold or whole-page
accuracy certification. PDF object-offset warnings remain in the log.

**Decision:** implement MinerU first. In these profiles it preserves scientific
structure better than Docling's observed inline-math path, avoids Marker's observed
decimal/table corruption, and completes the representative ten-page Mac workload
within the proposed latency target even under contention. This is enough evidence
to choose an implementation sequence, not to establish a general quality ranking.

Required P2 behavior: retain immutable originals and native output; preserve raw
math/table strings and uncertainty; flag unsupported/ambiguous object associations;
retain full figure context; verify requested page coverage and artifact hashes.
Do not claim model confidence establishes correctness. Figure descriptions follow
only from a suitable composite/context asset with the published caption retained.

`workers/models.json` records the publicly shareable snapshot/file digests and
native binary pin; it contains no paper contents or weights. Underlying MinerU
model-license review remains open before redistribution or a supported default.

## Figure descriptions: local models against Claude (P3 trial v1)

On 23 September 2026 the 19 figures of the three v5 library papers (HISOL 5,
COPRA 8, Shelton 6) were rendered at up to 1536 px and described with the same
`figure-claims-v1` prompt (published caption and citing sentence as context
only) by four local MLX models (mlx-vlm 0.7.2, temperature 0, seed 0, reasoning
off, 2048 output tokens) and three Claude models (Haiku 4.5 at temperature 0;
Sonnet 5 with thinking disabled; Opus 5.5 at low effort, since its thinking
cannot be disabled; 4096 output tokens, 8192 for Opus). John approved the API
spend (cap $5) and the three papers. The machine was heavily loaded, so the
timings below are indicative only.

The first printed-string check was misleading: the figure region contains the
printed caption, COPRA's plot text is outlined (only panel letters remain in the
text layer) and Shelton's text layer is invisible OCR (render mode 3) over a
scan. `verify_printed` now removes the caption's words and is not applicable to
an OCR layer or a region with too little text left; `pdf.ocr_text_layer`
detects invisible text. After the fix only the HISOL figures (3–5 per model
after parse failures) are checkable, too few to rank models.

| Model | Parsed | Informative strings confirmed (checkable figures) | Mean s | Peak GB / cost |
| --- | --- | --- | --- | --- |
| Granite Vision 4.1 4B | 17/19 | 180/219 (82%) | 17 | 9.8 |
| Qwen3.5-9B | 19/19 | 218/241 (90%) | 39 | 20.8 |
| Gemma 4 26B-A4B | 14/19 | 90/101 (89%) | 28 | 52.6 |
| Qwen3.8-27B | 19/19 | 212/238 (89%) | 151 | 58.1 |
| Claude Haiku 4.5 | 18/19 | 184/230 (80%) | API | $0.15 |
| Claude Sonnet 5 | 19/19 | 260/306 (85%) | API | $0.39 |
| Claude Opus 5.5 | 19/19 | 249/293 (85%) | API | $0.80 |

Parse failures: Gemma wrote LaTeX backslashes into JSON strings (3), one wrong
field type and one length cut-off; Granite one length cut-off (a runaway minus
sequence) and one bad escape; Haiku one wrong field type. A first Sonnet run at
2048 tokens was cut off on 3 figures and was repeated at 4096. Total API spend
$1.75 (ledger `data/describe-trials/claude-spend.json`, private). Descriptions
and the review page are in `data/describe-trials/trial-v1/` (private). The
choice needs John's ratings; the automatic check alone cannot separate them.

John's ratings (13 of 19 figures, partial, `trial-v1/figure-ratings.json`,
private): Opus 5.5 12 good of 12 (notes "excellent" throughout); Sonnet 5 12 good,
1 minor; Qwen3.8-27B 11 good, 2 minor (several "good but less detail"); Haiku,
Gemma, Qwen3.5-9B and Granite mostly minor or bad, with sparse detail and some
wrong figure types. Decision direction (23 September 2026): Opus 5.5 when a
paid run is authorized, Qwen3.8-27B as the local default; Sonnet not worth the
saving; drop the other three. Opus 5.5 at low effort cost $0.042 per figure
(range $0.023–0.064), about $0.27 per paper at the pilot's 6.3 figures per paper.

## Figure descriptions: prompt v2 and a rented H100 (P3 trial v2)

On 23 September 2026 prompt `figure-claims-v2` (located features, comparisons
within panels, relationships between panels, plain-Unicode math) was run on the
7 figures John had marked as lacking detail, plus two controls
(`data/describe-trials/trial-v2/`, private). John authorized one rented
GPU: no H200 capacity was available, so one H100 80 GB ($4.41/h, a vendor
image with NVIDIA drivers) ran vLLM 0.30.0
(torch 2.13.0, FlashInfer attention; its FlashAttention-3 kernel failed at
start-up) from 12:49 to 13:26 UTC, about $2.70, then was deleted. Logs and
versions: `data/gpu-trials/2026-09-23-h100/` (private). Qwen3.8-27B ran in bf16
with the same pinned revision as on the Mac.

Measured on the H100 (GPU cost is wall time at $4.41/h):

| Run | Figures | Parsed | Wall s | Output tok/s | GPU $/figure |
| --- | --- | --- | --- | --- | --- |
| Qwen3.8-27B, v1, 8 at once | 19 | 19 | 83 | 276 | 0.0054 |
| Qwen3.8-27B, v1, 16 at once | 19 | 18 | 51 | 458 | 0.0033 |
| Qwen3.8-27B, v1 + JSON, 16 at once | 76 | 76 | 161 | 531 | 0.0026 |
| Qwen3.8-27B, v2 | 7 | 7 | 55 | 219 | 0.0096 |
| Qwen3.8-27B, v2, reasoning, JSON | 7 | 7 | 167 | 158 | 0.029 |
| Molmo2-8B, v1 | 19 | 19 | 13 | 809 | 0.0008 |
| InternVL3.5-38B (FP8 on load), v1 | 19 | 18 | 32 | 391 | 0.0021 |

On the Mac the same Qwen run took about 151 s per figure. Opus 5.5 with v2 cost
$0.059 per figure (v1: $0.042). Reasoning mode without constrained output gave
malformed JSON on 3 of 7 figures; vLLM's `response_format` JSON mode with the
`qwen3` reasoning parser fixed all 7. The parser now also drops reasoning
before an unmatched closing `</think>` tag. InternVL3.5 Flash was not run:
its gain is speed and vLLM support for its router is unverified. Molmo2 needed
`trust_remote_code` (AllenAI's repository, pinned, run only on the rented machine).
John reviewed both pages on 23 September 2026: with prompt v2, Qwen3.8-27B
(reasoning off) gave the detail his v1 notes had missed; Molmo2-8B and
InternVL3.5-38B underperformed; Qwen with reasoning was judged not worth it
beside Opus 5.5. Decision: Qwen3.8-27B v2 on a rented GPU as the standard,
Opus 5.5 for careful paid jobs.

The implemented stage renders byte-identical images to both trials. Its
citing-sentence context differs from the trial scripts in one way: they
dropped the first letter of a sentence that began the paragraph ("igure 2
shows ..."), which affected 3 of the 7 v2 prompts; the stage keeps it.

## Next evidence

All three selected-page smoke trials are complete. Extend to scans with OCR,
figures, whole-paper first/last-page coverage, and published HISOL/supplement as
separate sources. Keep Wahlstrand out of tuning. Broaden reference coverage before
declaring a supported default; repeat timings on a quieter machine. The current
Docling result supports local feasibility but already establishes scientific gaps.
