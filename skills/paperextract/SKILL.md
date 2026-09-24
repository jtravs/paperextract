---
name: paperextract
description: Find, read and add scientific papers in a paperextract library. Use when the user asks what their paper library says about a topic, whether a paper is already in a library, to cite or quote a table, equation or figure from a library paper, or to add PDFs, arXiv papers or saved article pages to a library.
---

# paperextract libraries

A library is a directory of paper directories plus a search index. Each paper
directory is self-contained and plain text first: read files directly; use the
command line to search, look up and add papers.

## Running the command

`paperextract` runs from its source checkout, which holds the extraction
workers and models. If `paperextract` is not on `PATH`, define a shell
function once per shell (a variable holding the command does not work in
zsh):

```sh
px() { uv run --no-sync --project /path/to/paperextract paperextract "$@"; }
px lookup --help
```

Add `--offline` after `uv run` when the machine has no network for package
downloads; set `UV_CACHE_DIR` to a writable directory if uv complains about
its cache.

Always pass `--library /absolute/path` (or name libraries positionally for
`lookup` and `search`) unless the user's configuration already sets one; the
default is `./literature` relative to the current directory. Add `--json` when
you will parse the output. Progress goes to stderr, results to stdout.

## Finding papers

1. **Is a specific paper in the library?** Use `lookup`, which checks bytes,
   text, DOIs, arXiv identifiers and titles:
   `paperextract lookup --doi 10.1038/s41566-019-0416-4 LIBRARY --json`, or
   `--file paper.pdf`, `--arxiv 2206.01062`, `--bibtex refs.bib`, or
   `--title "full title" [--author Family] [--year 2019]`. Status is
   `present`, `related` (another version, a supplement or a citing work),
   `candidate` (needs checking) or `absent`. `--title` needs nearly the whole
   title; for a topic use `search`. `lookup` and `dedup` also work when the
   library does not exist yet (everything is `absent`).
2. **What does the library say about a topic?** Use
   `paperextract search "words" LIBRARY... --limit 20 --json`. Every word must
   occur; hyphenated words (`self-compression`) and `"quoted words"` must occur
   as a phrase. Try synonyms and fewer words if nothing comes back. Hits give
   the paper `directory`, `title`, `year`, `doi` and a `snippet`, but no page;
   find the passage by searching `paper.md` for the snippet words. A hit with
   `in_description: true` matched only a machine-generated figure
   description. Two hits can be documents of one work (same `doi`); see
   `related` in `metadata.json`.
3. **Read before answering.** Open `LIBRARY/<directory>/paper.md`; search inside
   it for the snippet words to find the passage. `catalog.md` at the library
   root lists every paper with author, year, title and DOI.

## Reading a paper directory

| File | Use |
| --- | --- |
| `paper.md` | Full text with YAML front matter, headings, inline and display math as LaTeX, tables, figure captions and links to assets. Start here. |
| `citation.bib`, `metadata.json` | Validated bibliographic identity; `bibliographic_status` says whether it is `VALIDATED`, `VALIDATED_WITH_WARNINGS` or `UNVERIFIED`. Unverified papers have no `citation.bib`; their observed title, authors and DOI candidates are under `observed` in the `paper.md` front matter. |
| `tables/<id>.json`, `.csv`, `.html` | Exact table cells. Take numbers from here, not from the Markdown rendering. |
| `figures/<id>.png` | Complete figure crops, to look at when a caption is not enough. |
| `descriptions/<id>.json` | Machine-generated figure descriptions. |
| `validation.json`, `diagnostics/review.md` | Findings: missing pages, unmapped glyphs, table checks, HTML cross-check, related papers; a supplement's findings are under `supplements[]`. Check before relying on a value. |
| `tables/<id>.jpg` or `.png` | The table as printed, to compare with the extracted cells. |
| `supplement_01/supplement.md` | Converted supplementary material, when present. |
| `original/` | The preserved source files. |

See [reference.md](reference.md) for the fields and findings that matter.

## Rules for using the content

- Quote numbers exactly as extracted, with the table or equation label and the
  paper. Extraction can be wrong: when `validation.json` has a warning on that
  table or page (for example `UNMAPPED_GLYPHS`, `TABLE_CHECK_DISAGREED`,
  `TABLE_OCR_REJECTED`, `PAGE_NOT_PROCESSED`), say so, and offer to check the
  page image or the original PDF.
- Figure descriptions (the blocks labelled *Machine-generated visual
  description* in `paper.md`, and `descriptions/`) are model output, not the
  paper. Treat them as leads; never present them as the authors' statements.
- Unverified papers (`Unverified_*` directories) have no validated title or
  authors; cite them by their observed title and say they are unverified.
- Several documents of one work can exist (a version of record beside an
  accepted manuscript or arXiv preprint): `metadata.json` has
  `document_version` and `related` (other papers by name; find their
  location in `catalog.jsonl`). `document_version` is often `unknown`; the
  source file name in the front matter and the page count can help. Prefer
  the version of record for citation and say which one you used.
- Cite with `citation.bib` or `metadata.json`; do not reconstruct citations
  from memory.

## Adding papers

Extraction runs a local model for minutes per paper and writes to the library;
run it only when the user asks, and in the background for more than one paper.

1. Check first: `paperextract dedup FOLDER --library LIBRARY` lists copies,
   papers already in the library, supplements and same-title files, and
   changes nothing.
2. Add: `paperextract batch FOLDER --library LIBRARY --json` (every top-level
   PDF; supplements and saved pages are paired automatically), or
   `paperextract extract paper.pdf --supplement si.pdf --html page.html`, or
   `paperextract extract arXiv:2206.01062` (downloads from arXiv).
3. Report each item's `status` (`published`, `skipped`, `attached` for a
   copy with identical text preserved in its paper, `held`, `rejected`,
   `failed`) with its message and `warnings`. Exit code 0 means every item
   succeeded (warnings such as `identity UNVERIFIED` are listed but do not
   change it unless `--strict`), 4 some items failed (or, with `--strict`, any
   warning, including partial page coverage), 2 a
   usage or setup error, 3 a naming conflict, 5 everything failed.
4. Then search and read the new papers as above.

Useful options: `--document-version accepted_manuscript` when the user says
what a PDF is; `--table-check` for a second, Docling reading of every table
(slower, needs the Docling worker); `paperextract compare paper.pdf --backends
mineru,docling,marker` when the user wants to see how the backends differ on
one PDF (minutes per backend; publishes nothing). `--offline` avoids registry lookups, so
papers are published `UNVERIFIED` even when they print a DOI;
`paperextract reprocess PAPER --refresh-identity` (online) validates them
later without extracting again, and may rename them.
For large or repeated jobs write a manifest (`dedup --manifest-out`, then
`batch --manifest FILE`). Figure descriptions are a separate step,
`paperextract describe --all`, that needs a model server; never use
`--backend anthropic` without the user's explicit spend cap (`--max-usd`).

## Do not

- Edit, move or delete files inside paper directories or `original/`; use
  `reprocess`, `organize` or a new extraction instead. If a command says a
  record's version is not one it reads, a newer paperextract wrote it; tell
  the user rather than working around it. `paperextract migrate --dry-run`
  reports papers in older formats; run `migrate` only when the user asks.
- Merge papers because titles look alike; report `DUPLICATE_CANDIDATE` and
  `same title?` results to the user.
- Send library content to remote services unless the user asks.
