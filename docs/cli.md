# Command line

The `paperextract` command extracts PDFs into a library with an isolated
backend worker (MinerU by default, Docling or Marker), checks for duplicates before any model runs, resolves
bibliographic identity and publishes portable paper directories. It is
installed with the package; `python -m paperextract` is equivalent.

```sh
paperextract paper.pdf                       # shorthand for `extract`
paperextract extract paper.pdf --library literature
paperextract batch incoming/ more.pdf --library literature
paperextract dedup incoming/ --library literature --report dedup.json
paperextract dedup incoming/ --manifest-out papers.jsonl   # propose a manifest
paperextract batch --manifest papers.jsonl --shard 1/2 --runs-to staged/1
paperextract publish staged/1 staged/2       # publish staged runs in one step
paperextract publish literature/.paperextract/runs/RUN --refresh-identity
paperextract models status --verify          # pinned models present and intact?
paperextract models fetch mineru docling
paperextract extract paper.pdf --supplement paper_SI.pdf   # also for a published paper
paperextract extract paper.pdf --table-check    # cross-check tables with Docling
paperextract extract paper.pdf --backend docling
paperextract compare paper.pdf --backends mineru,docling,marker
paperextract extract arXiv:2206.01062        # download the current version
paperextract extract aam.pdf --document-version accepted_manuscript
paperextract reprocess --all                 # rebuild from kept output, no backend
paperextract lookup --file new.pdf --doi 10.1364/optica.6.000495 literature other-library
paperextract search "soliton self-compression" --limit 5
paperextract index rebuild
paperextract organize --layout by-year --dry-run
paperextract migrate --dry-run               # which papers are in older formats
paperextract reprocess Unverified_1c17a06cda95 --refresh-identity
paperextract reprocess Unverified_1c17a06cda95 --doi 10.1103/PhysRevA.13.1422
paperextract extract -- ./status             # a file whose name is a command
```

| Command | Purpose |
| --- | --- |
| `extract PDF [--supplement PDF]... [--html PAGE]...` | Extract one paper with its supplements, published in one paper directory, with saved article pages preserved and compared. When the paper is already in the library, its supplements are extracted and added to it, which republishes the paper from its kept output. Several positional paths are refused; use `--supplement` or `batch`. |
| `batch PATH...` | Extract every top-level PDF of the given directories and every given file, as separate papers. Subdirectories are not searched. |
| `dedup PATH...` | Report duplicates against each other and the library. Nothing is copied, extracted or changed. |
| `publish RUN...` | Publish kept or staged run directories, or directories of them, one after another: after a failure was fixed, into a second library, or after `batch --runs-to`. |
| `models status [SET...]` / `models fetch SET...` | Check or download the pinned model snapshots and binaries the workers need; see the worker README. |
| `lookup [LIBRARY]... --file PDF \| --doi DOI \| --bibtex FILE \| --title T [--author A] [--year Y]` | Report whether papers are already in one or more libraries: `present`, `related`, `candidate` or `absent`, with the evidence. |
| `search QUERY [LIBRARY]... [--limit N]` | Full-text search over titles, authors, abstracts and paper text, ranked, with a snippet. |
| `migrate [--dry-run]` | Check every paper's record versions and files against its manifest, rebuild outdated papers in the current formats from their kept output, and rebuild the catalog and index. |
| `index rebuild` | Recompute the catalog from the paper directories and rebuild the search index and derived catalogs. |
| `reprocess PAPER... \| --all` | Rebuild published papers from the output they keep, with the current normalization, corrections and export, and replace them; `--refresh-identity` resolves identity again and may rename a paper. For one named paper, `--doi DOI` or `--bibtex FILE` asserts its identity; see [bibliographic identity](identity.md). The extraction backend does not run. |

The extraction commands need a source checkout with the worker environment
of the chosen backend and its models; see the worker README for setup. The
command checks both before starting and names the missing step.

## Duplicates before extraction

`extract` and `batch` run the same duplicate pass as `dedup` and apply its
automatic actions, following the design's duplicate tiers:

| Relationship | Action | Reported as |
| --- | --- | --- |
| Identical bytes within the batch | Extract one copy; the other names are recorded as aliases | `alias` lines, `aliases` in JSON |
| Identical bytes already in the library | Not extracted | `skipped` |
| Different bytes, identical text on every page | Not extracted; preserved in the equivalent paper as `original/source_NN/` with role `equivalent_copy` (a library paper is republished without extraction) | `attached` (`held` if that paper failed) |
| Same first DOI candidate, different text | Both are extracted; listed by `dedup` for review | `shared DOI` in `dedup` |
| A file named as a supplement (`SI`, `supp`, `suppl`, `sm`, `supplementary`, `ESM`, `supporting`) | Published with the paper that shares its DOI, or with the one paper whose file name clearly shares the longest start; otherwise held | `supplement` lines; `held` when unpaired or when its paper is already published |

Text equivalence requires at least 200 characters of text, so short covers and
scans without a text layer never match. The copy's bytes then count as part
of the library, so a later batch skips it. A shared DOI never merges papers: it may be
a published version beside an accepted manuscript, or a DOI printed in a
reference list.

After identity, each paper is related to the library's papers with the same
DOI or the same title, first author and year; see
[document versions](identity.md). These relations are reported, never acted on.

## Manifests

A manifest names each paper explicitly, one JSON object per line, after a
header line; paths are relative to the manifest:

```text
{"schema": "paperextract.batch-manifest", "schema_version": 1}
{"id": "hisol", "paper": "hisol/published.pdf", "supplements": ["hisol/si.pdf"], "html": ["hisol/page.mhtml"]}
{"id": "copra", "paper": "copra/optica.pdf", "document_version": "version_of_record"}
{"id": "doclaynet", "paper": "arXiv:2206.01062"}
```

`batch --manifest FILE` checks the whole file before any work: the header,
known keys, unique identifiers, existing files, known versions, pages that
are saved web pages, and no file or identical bytes named by two entries;
every problem is listed with its line number. Supplements and pages are
taken from the manifest instead of pairing by name or DOI; duplicates against
the library are still skipped or held. Each paper's `extraction.json` records
its `manifest_id`. A rerun resumes, because published sources are skipped by
their digest and failed ones are tried again. `--shard K/N` processes the
entries whose identifier falls in shard K of N, a stable split for running
parts of a manifest on separate machines.

`dedup --manifest-out FILE` writes the manifest `batch` would follow for the
given sources: one entry per paper to extract with its paired supplements and
pages, leaving out copies, held files and papers already in the library.
`dedup` also lists `same title?` groups, batch files whose PDF information
title matches another file's or a library paper's; this is a candidate for
review only.

## Library layout

A library records its layout in `corpus.json`: `flat` (the default, every
paper directly in the library), `by-year` (`2019/Travers_2019_…`) or
`by-initial` (`T/Travers_2019_…`). Shards come from validated identity only;
unverified papers go to `Unverified/`. New papers are published into their
shard, and names stay unique across shards so a paper can move between
layouts. `organize --layout L` moves every paper to its place under a
journal (an interruption is completed by the next command), records the
layout, and rebuilds the catalog and index; `--dry-run` lists the moves.
Paper directories hold only relative references, so a move changes nothing
inside them. Commands that name papers accept the bare name, such as
`Travers_2019_HighEnergyPulse`, in any layout.

## arXiv

`extract arXiv:ID` downloads the paper from arXiv and extracts it; an
unversioned identifier is resolved to the current version through the arXiv
API first, with the three-second pause arXiv asks of automated clients. The
PDF is kept in `.paperextract/acquired/arxiv/` and reused on a later request
for the same version; the request, resolved identifier, address and time are
recorded as `assertions` in `extraction.json`. Nothing is downloaded with
`--offline` or `--no-registry`. Only arXiv is supported.

## Tables with lost glyphs

Some publisher math fonts have glyphs without a Unicode mapping, so a table
that MinerU reads from the PDF text layer loses brackets, bars and accents.
Unless `--no-table-ocr` is given or `[worker] table_ocr = false` is set, the
pages holding such tables are extracted a second time in OCR mode. The OCR
table replaces the text-layer table only when both contain exactly the same
numbers, because OCR may misread digits the text layer holds exactly; either
way the other version is kept in `document.json` as an alternative and a
`TABLE_OCR_SELECTED` or `TABLE_OCR_REJECTED` finding records the decision.
The OCR run is recorded in `extraction.json` and its raw output is kept under
`diagnostics/raw/<run>/table-ocr/`.

## Cross-checking tables with Docling

With `--table-check` or `[worker] table_check = true`, every page on which
MinerU found a table is read a second time by the Docling worker (Heron
layout, accurate TableFormer, cells matched to the text layer, no formulas,
no OCR). Each extracted table is paired with the Docling table at the same
position and their numbers are compared; the selected body never changes.

| Finding | Meaning |
| --- | --- |
| `TABLE_CHECK_AGREED` | Both readings contain exactly the same numbers. |
| `TABLE_CHECK_DISAGREED` | The numbers differ; the message lists how many agree and which appear only on one side. Check the table against the page. |
| `TABLE_CHECK_UNMATCHED` | Docling found no table at the same position. |
| `TABLE_CHECK_EXTRA` | Docling found a table on a checked page that matches no extracted table, which may be a missed table. |
| `TABLE_CHECK_FAILED` | The Docling run failed; the tables are unchecked. |

Docling's body is kept in `document.json` as an alternative with source
`docling`. The comparison ignores signs, removes Docling's codes for
unmapped glyphs (`.0137`) and joins a decimal point followed by spaces
(`2. 534`, from old OCR text layers); nothing else is normalized. On the
pilot papers Docling agreed completely with MinerU on born-digital tables and
read old scans worse than MinerU, because it takes cell text from the scan's
own OCR layer; a disagreement on a scan therefore often points at Docling.
The run is recorded in `extraction.json` as `table_check`, its raw output is
kept under `diagnostics/raw/<run>/table-check/`, and `reprocess` applies it
again. Pages without a MinerU table are not checked.

## Docling as the backend

`--backend docling` or `[worker] backend = "docling"` extracts with Docling
instead of MinerU, into the same canonical document, export and library.
Docling reports a composite figure as one picture with its caption, where
MinerU often splits it into panels. On the published HISOL paper (23
September 2026, this Mac under load) it took 5 minutes against MinerU's
48 s, mostly in its formula model on the CPU; its LaTeX for the 9 display
equations had errors that MinerU's did not (∝ read as ∞, a subscript r as
T), and it recognized no inline math, where MinerU found 64 inline spans.
MinerU therefore stays the default. The table
OCR re-extraction and the table check are MinerU stages and do not run with
Docling. `[docling] formulas = false` skips formulas, which are then
reported as `EQUATION_NOT_TRANSCRIBED`.

## Marker as the backend

`--backend marker` extracts with Marker 2.0 in balanced mode. Marker sends
layout and text recognition to the Surya model, which the worker serves with
the pinned llama.cpp release on 127.0.0.1 for the length of the request;
Marker's own server start, its LLM services and model downloads stay off.
Its models are in `model-cache/marker` and the server binary is set by
`[marker] server`. On the published HISOL paper (23 September 2026, this Mac
under load) it took 4 min 53 s, found all 5 figures with their captions, 9
numbered display equations and 109 inline math spans. In P1 it split
decimal numbers across table cells on the HISOL accepted manuscript, so check
its tables before relying on them.

## Comparing backends

`compare PDF --backends mineru,docling,marker` extracts one PDF with each
backend into a run directory (`--out`, default
`.paperextract/compare/<time>` in the library) and compares every backend
with the first: pages and block kinds, paragraph text found in each direction,
headings, figure captions by label, table numbers matched by position or
label, and display equations by printed number, comparing LaTeX with spacing
and `\left`, `\right`, `\mathrm`, `\text` and a trailing printed number
ignored. Text in inline math is left out of the text comparison, so a
paragraph whose symbols one backend reads as math and another as text may be
reported as absent. Backends run one after another, so a comparison takes
their sum: on a ten-page paper about 13 minutes on an M5 Max for all three,
mostly Docling and Marker. The report is
`comparison.json` (schema `paperextract.backend-comparison` 1) and a summary
on stdout; nothing is published. A comparison shows where readings differ,
not which one is right. The exit code is 0 when every backend completed, 4
when some failed and 5 when fewer than two did.

## Rebuilding a library

Every paper directory keeps its preserved PDFs, the backend's native output,
the worker records and the exported crops. `reprocess` reconstructs the
original run's staging directory from them, normalizes and corrects the
native output again, reapplies a kept table OCR run and publishes the result
in place of the old directory. Three papers take seconds instead of minutes.
A table that newly needs OCR is reported with `TABLE_OCR_NOT_RUN`, because
rebuilding never starts the backend; extract such a paper again to re-extract
it.

A replacement is journaled: the old directory moves to
`.paperextract/replaced/<name>.<generation>/` before the new one is renamed
into place, and an interrupted replacement is completed or rolled back the
next time the library is published to. Retired directories are kept until you
delete them. The catalog is rewritten from the published directories.

## Migrating a library

Every record a library keeps names its schema and version. A release reads
the versions listed in `paperextract.formats` and writes the last of each;
it refuses a record whose version it does not know, such as one written by
a newer release, instead of guessing, and exits with status 5. Commands
read older papers as they are. `migrate` brings a library to the current
formats:

```sh
paperextract migrate --dry-run
paperextract migrate
```

For each paper it checks the versions of its records and compares every file
with the paper's manifest. A paper whose records are all current is left
alone. An outdated paper is rebuilt from its kept output exactly as by
`reprocess`: no backend runs, identity is kept, and the old directory is
retired to `.paperextract/replaced/`. Because the current normalization,
corrections and export run again, the rebuilt `paper.md` can differ from the
old one, for example in corrected text or new findings. A paper is refused
when a record has an unknown version (`unsupported`) or when a file is
missing, changed since publication or not listed in the manifest
(`damaged`), because a rebuild would replace such files. Kept evidence in
`original/` and `diagnostics/` is checked for integrity only; it is never
rewritten. Afterwards the catalog and index are rebuilt, unless a paper has
an unknown version.

The output has one line per paper (`current`, `would migrate`, `migrated`,
`refused` or `failed`) with the records it changes, such as `paper-manifest
1→3, document 0.1→0.3`, or the problems found, then the state of the catalog
rows. `--json` writes `paperextract.migrate-result` 1. The exit code is 0
when no paper was refused or failed, 4 when some were and 5 when all were.

## Describing figures

`describe` adds machine-generated visual descriptions to the figures of
published papers, with a model served by vLLM on a GPU by default, the paid
Anthropic API under a spend cap, or a local MLX worker. It skips figures that
already have a description from the same model, prompt and image, so one GPU
session can describe everything extracted since the last one. Descriptions are
labelled as machine generated wherever they are saved and are indexed apart
from the paper's text. See [figure descriptions](describe.md).

## Finding papers

Every command that publishes refreshes `.paperextract/index.sqlite`,
`catalog.md`, `library.bib` and `catalog.csl.json`; `index rebuild` recomputes
them and `catalog.jsonl` from the paper directories, which stay the source of
truth. The index is rebuilt automatically when it is missing or older than the
catalog.

`lookup` answers "is this paper already in my libraries?" for PDFs, DOIs,
arXiv identifiers (`--arxiv`, any version), BibTeX files or titles, across
several libraries at once:

| Status | Evidence |
| --- | --- |
| `present` | Identical bytes, identical text on every page, or the same validated DOI. |
| `related` | The file prints the paper's DOI or arXiv identifier, or both print the same DOI string: another version, a supplement or a citing work. |
| `candidate` | A title with at least 80% of its words in common, with agreeing first author and year when given, or a library paper's title of four or more words printed on the file's first page. A title alone never proves identity. |
| `absent` | No evidence in that library. |

`search` requires every word of the query, ignores case and diacritics, and ranks
title matches above author, abstract and body matches, and those above
machine-generated figure descriptions; a hit found only in a description is
labelled as such. Both commands print JSON with `--json`.

## Output and exit codes

Standard output carries one tab-separated line per supplied source, alias
lines, the library with the origin of its setting, and a summary. With
`--json` it carries a versioned `paperextract.cli-result` document instead,
listing every item, including skipped, held, rejected and failed ones. Progress
goes to standard error; `-q` limits it to warnings and `-v` adds debug output.
Each `batch` also writes its result document to
`.paperextract/batches/` inside the library.

| Code | Meaning |
| --- | --- |
| 0 | Every item was published, republished, skipped or held; warnings are summarized. |
| 2 | Usage or configuration error, including a missing worker setup. |
| 3 | Every problem was a publication conflict with an existing directory. |
| 4 | Mixed batch result, or any warning under `--strict`. |
| 5 | Execution failure. |
| 130 | Cancelled with Ctrl-C or `SIGTERM`; the worker process group is stopped. |

Under `--strict`, a paper that is not `VALIDATED`, a partial page coverage and
a held copy count as warnings.

Each paper runs in `.paperextract/runs/<run>/` inside the library. A
successful publication removes the run, because the paper directory already
keeps the raw output and worker logs under `diagnostics/raw/`; `--keep-run`
keeps it. A failed or cancelled run is always kept for diagnosis and for
`publish`. Deleting old runs is safe.

## Configuration

Settings resolve in this order: command-line options, the file named by
`--config`, the user file `~/.config/paperextract/config.toml` (or under
`$XDG_CONFIG_HOME`), then defaults. Relative paths in a file resolve against
that file's directory.

```toml
library = "~/papers/optics"          # default: ./literature

[worker]
root = "~/code/paperextract"         # default: the checkout running the command
models = "~/models/mineru"           # default: <root>/model-cache/mineru/models
timeout_seconds = 3600               # per paper
cpu_threads = 4
table_ocr = true                     # --no-table-ocr to skip re-extraction
backend = "mineru"                   # --backend docling or marker
table_check = false                  # --table-check
# persistent: one MinerU process for all papers of a batch; on with vllm


[mineru]
engine = "auto"                      # vllm with a CUDA GPU and the cuda extra,
                                     # else llama-cpp; or name one
small_models = "onnx"                # torch with vllm when auto
concurrency = 1                      # VLM requests in flight; 16 with vllm
batch_invariant = true               # vllm: repeatable output, slower

[docling]
models = "~/models/docling"          # default: <root>/model-cache/docling
device = "auto"                      # auto, cpu, mps or cuda
formulas = true

[marker]
models = "~/models/marker"           # default: <root>/model-cache/marker
server = "~/bin/llama-server"        # default: the pinned llama.cpp release
mode = "balanced"                    # balanced or fast

[registry]
enabled = true                       # --no-registry publishes unverified
offline = false                      # --offline uses snapshots only
contact = "name@example.org"         # Crossref polite pool; unset by default

[describe]                           # see the figure descriptions page
backend = "openai"                   # openai, anthropic or mlx
endpoint = "http://127.0.0.1:8000/v1"
max_usd = 5.0                        # required for anthropic
key_file = "~/.config/paperextract/anthropic-key"
```

| Option | Setting |
| --- | --- |
| `--library PATH` | `library` |
| `--models PATH` | `worker.models` |
| `--timeout SECONDS` | `worker.timeout_seconds` |
| `--no-table-ocr` | `worker.table_ocr` |
| `--backend mineru\|docling\|marker` (extract, batch) | `worker.backend` |
| `--table-check` / `--no-table-check` | `worker.table_check` |
| `--offline` / `--no-registry` | `registry.offline` / `registry.enabled` |
| `--backend`, `--endpoint`, `--model`, `--max-usd` | `describe.backend`, `describe.endpoint`, `describe.model`, `describe.max_usd` |

Registry lookups are metadata queries to Crossref and DataCite, paced below
their published limits, and stored as snapshots in
`.paperextract/registry-snapshots/` so later runs and offline runs reuse them.
The contact address is sent to Crossref only when configured. Result documents
record the resolved configuration and each value's origin, and record only
whether a contact is configured, never the address.

## GPU hosts

On a Linux machine with an NVIDIA GPU, MinerU can run its vision-language
model with vLLM and its layout, OCR and formula models with torch; see the
worker README for the environment. Both run inside the worker process with
local weights, never as a remote service. `[mineru] engine = "vllm"` needs
`concurrency` above 1 to keep the GPU busy, and vLLM builds its engine for
each process, which takes half a minute to a minute; `[worker] persistent =
true` lets a `batch` send every MinerU request, including table OCR, to one
serving worker process, so the engine is built once. Each request keeps its
own staging directory, log and verified result; a crashed or timed-out
process is killed and the next request starts a new one. vLLM's compile
cache goes to `VLLM_CACHE_ROOT` when set, such as node-local disk in a
Slurm job, and otherwise into the batch's session directory.

With many requests in flight, vLLM's output can differ between runs of the
same paper, because rounding depends on which requests share a batch; on the
A40 pilot this changed LaTeX spellings and digit grouping, not values.
`batch_invariant = true`, the default, asks vLLM for batch-independent
results: two runs of 15 papers were identical, and about 1.5 times slower
than ordinary batching, which `batch_invariant = false` restores.

## Current limits

See [known limitations](limitations.md).
