# Getting started

paperextract turns scientific PDFs into a library of portable paper
directories: Markdown with equations in LaTeX, exact table cells, figure crops
with their captions, bibliographic identity and BibTeX, and a record of where
every block came from in the PDF. It runs locally; extraction models run in
isolated worker environments on your machine or cluster.

## What you need

- macOS on Apple silicon, or Linux on x86_64. Linux machines with an NVIDIA
  GPU (driver for CUDA 13) run MinerU's GPU engines; see
  [Running on a Slurm cluster](cluster.md) for batch work on GPU nodes.
- [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git.
- Disk for the models: about 2 GB for the default MinerU set, 3 GB more for
  the GPU set, 56 GB for the optional figure-description model.

paperextract runs from a source checkout, which holds the worker
environments and the pinned model manifest; it is not yet installable as a
self-contained package.

## Install

```sh
git clone https://github.com/jtravs/paperextract.git && cd paperextract
uv sync --locked                                     # the core command
uv sync --directory workers/mineru --locked          # the default backend
uv run paperextract models fetch mineru              # pinned models, verified
```

On a Linux machine with an NVIDIA GPU, install MinerU's GPU engines and
their models instead:

```sh
uv sync --directory workers/mineru --locked --extra cuda
uv run paperextract models fetch mineru-cuda
```

`[mineru] engine = "auto"`, the default, then uses vLLM on the GPU and
llama.cpp elsewhere. `uv run paperextract models status` shows which model
sets are present; `--verify` hashes every file. Optional backends and their
models: `workers/docling` with `models fetch docling`, `workers/marker` with
`models fetch marker` (which also fetches the pinned llama.cpp server).

Downloads happen only in `models fetch`; extraction never downloads. The
models have their own licences, listed on the [model licences](models.md)
page.

## Extract a paper

```sh
uv run paperextract paper.pdf --library ~/papers/optics
```

This checks the PDF against the library for duplicates, extracts it, looks
up its DOI in Crossref or DataCite, and publishes a directory such as
`~/papers/optics/Travers_2019_HighEnergyPulse/`:

| Path | Contents |
| --- | --- |
| `paper.md` | The paper as Markdown with front matter, LaTeX equations, tables and figure links |
| `document.json` | The canonical document: every block with its page, box and backend type |
| `figures/`, `tables/`, `equations/` | Figure and panel crops, tables as HTML, JSON and CSV, equation images |
| `citation.bib`, `metadata.json` | Validated identity, and each field's source and status |
| `validation.json` | Findings: what could not be verified, such as lost glyphs or unmatched captions |
| `original/` | The PDF you supplied, byte for byte |
| `diagnostics/` | The backend's raw output and logs |
| `manifest.json` | Every file with its size and SHA-256 |

A paper whose identity is not validated is published as
`Unverified_<digest>`; nothing is invented to fill a gap. Supplements
(`--supplement SI.pdf`) are published in the same directory, and saved web
pages of the article (`--html page.mhtml`) are preserved and compared with
the extraction.

## Many papers

```sh
uv run paperextract dedup incoming/ --library ~/papers/optics   # report only
uv run paperextract batch incoming/ --library ~/papers/optics
```

`dedup` reports byte-identical files, copies with identical text, shared
DOIs and supplements before anything is extracted, and `batch` applies the
same checks: identical copies are skipped or preserved with their paper.
For large or repeated jobs, write a manifest with
`dedup --manifest-out papers.jsonl` and run `batch --manifest papers.jsonl`;
a rerun resumes.

## Find and read papers

```sh
uv run paperextract search "soliton self-compression" --library ~/papers/optics
uv run paperextract lookup --doi 10.1038/s41566-019-0416-4 --library ~/papers/optics
```

The library also holds `catalog.jsonl`, `catalog.md` and `library.bib`.
Paper directories use relative links only, so a library can be moved,
synced or shared as a folder. `skills/paperextract/` teaches an AI agent to
search a library and read its papers.

## Describe figures

```sh
uv run paperextract describe --all --library ~/papers/optics
```

`describe` asks a vision-language model for a structured description of each
figure, labelled as machine generated wherever it is saved. It needs a model
server (Qwen3.8-27B with vLLM by default; see
[figure descriptions](describe.md)), the paid Anthropic API under a spend
cap, or the slow local MLX worker on a Mac.

## Next

- [Command line](cli.md): every command, option and setting.
- [Known limitations](limitations.md): what to check before relying on a result.
- [Running on a Slurm cluster](cluster.md): GPU batches.
