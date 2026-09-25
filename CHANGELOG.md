# Changelog

Notable changes are recorded here following Keep a Changelog. The package
version is derived from Git tags.

## [Unreleased]

### Changed

- Title candidates fall back to level-2 headings when the first pages have
  no level-1 heading, as in some *J. Chem. Phys.* layouts, and never include
  standard section names such as "Introduction". A record that matches only
  a secondary heading is refused when no author agrees, so the other letter
  on a shared page is not taken for this one.

## [0.2.0] - 2026-09-25

### Added

- A worked example: the Geib et al. (2019) COPRA paper from *Optica* with its
  frozen output under `examples/geib-2019-copra/`, followed from PDF to
  library in the new *Worked example* page of the manual. The paper keeps its
  publisher's non-commercial licence and is not part of the package.
- Identity from more evidence. DOIs encoded in publishers' file names (APS,
  Optica, Springer Nature, Royal Society, ACS, older Elsevier, arXiv) are weak
  candidates, accepted with an `identifier` warning. Up to four title
  candidates (the PDF title and the first pages' level-1 headings) are compared
  with each record, and typesetting leftovers such as `acs_JX_… 1..7` or
  `Using JCP format` are no longer taken as titles.
- Titles are compared without HTML, TeX, lost-glyph placeholders and footnote
  markers, and agree with a warning apart from a lost Greek letter, typical
  reading errors (0 for O, 1 for l, a stray footnote digit) or an appended
  journal name.
- Bibliographic search asks with each title candidate, then with the author
  line, running headers and file name added, ten results per query; checks
  author and year on the first two pages; sets aside supplementary components
  and uncited conference versions; and identifies a translated article as its
  English translation, recording the original.
- Supplementary material is no longer identified as its article: it stays
  unverified with the article recorded (`supplement_of`). Science's `_sm` file
  names are recognized as supplements.
- `extract PAPER --supplement FILE` adds supplements to a paper already in the
  library, republishing it from its kept output.
- `reprocess PAPER --doi DOI` asserts a paper's DOI, and `--bibtex FILE`
  asserts the identity of a work no registry holds, such as a report or
  thesis, with the new status `ASSERTED`: named and cited like a validated
  paper, never reported as validated. Assertions are kept with the paper.
- Registry records keep their DOI relations, such as `is-translation-of`.

### Changed

- Directory names, citation keys and author shards transliterate letters that
  Unicode does not decompose, such as ł, ø, æ, œ, ß, đ, þ and the dotless ı, so
  Pawłowski gives `Pawlowski_…` rather than `Pawowski_…`. Stored metadata keeps
  the original spelling; `paperextract reprocess PAPER` renames an affected
  paper.
- The integrity check of `migrate` ignores metadata files that file managers
  write into folders, such as Finder's `.DS_Store`, so opening a paper in
  Finder no longer marks it damaged.
- A registry author delivered as one string of initials and a surname, such as
  "D V Willetts", is split into given and family names, so the paper is named
  `Willetts_…` and cited correctly; the delivered string is kept.
- Compatibility: a paper whose identity was asserted with `--bibtex` records
  the status `ASSERTED`, which 0.1.0 does not know; read such libraries with
  0.2.0 or later.

### Removed

- The `dev/` design and tracking notes and the manual pages built from them
  (design, status, corpus assessment and trials); the manual keeps the
  tooling and release pages.

## [0.1.0] - 2026-09-24

First release. Everything below is new.

### Added

#### Extraction

- One-PDF pipeline from preservation to a canonical, versioned
  `document.json` (schema 0.3): headings, paragraphs with inline math,
  display equations with printed numbers, tables with exact cell grids and
  alternative bodies, figures with panels and captions, page furniture, and
  structured findings; every block keeps its page, box and backend type.
- Isolated backend workers behind a versioned protocol, each in its own
  locked environment, with a minimal offline process environment and result
  integrity checks: MinerU 4.0.5 (default), Docling 2.129.0 and Marker 2.0.0
  (`--backend`). Worker locks resolve for macOS arm64 and Linux x86_64.
- MinerU on Linux GPUs: the `cuda` extra with vLLM 0.28, `[mineru] engine`
  (`auto` by default: vLLM with torch small models and 16 requests in flight
  when a CUDA GPU is visible, else llama.cpp and ONNX), `small_models`,
  `concurrency` and `batch_invariant` (on by default, for repeatable output).
- Serving workers: `[worker] persistent` (on with vLLM) sends a batch's
  MinerU requests to one worker process, so models and the vLLM engine load
  once; each request keeps its own log and verified result.
- Tables whose text layer lost glyphs are read again with OCR and both
  bodies kept; `--table-check` compares MinerU's table numbers with Docling's.
- `paperextract compare PDF --backends A,B,...` reports where backends'
  readings of one PDF differ (`comparison.json`).
- `paperextract models status [--verify]` and `models fetch SET` check and
  download the pinned model snapshots and llama.cpp servers listed in
  `workers/models.json`, verifying every file's size and SHA-256.

#### Library

- Portable paper directories: `paper.md` with front matter, figure, panel and
  equation crops, tables as HTML, JSON and CSV, `citation.bib`, metadata,
  extraction and validation reports, the preserved originals, the backend's
  raw output and a manifest of hashes, published transactionally.
- Duplicate handling before extraction: identical bytes are skipped or
  extracted once, copies with identical text are preserved in their paper,
  shared DOIs and same-title files are reported; `paperextract dedup`.
- Supplements published inside their paper with anchors and resolved
  references; saved article pages preserved and compared with the PDF
  extraction.
- `batch` over directories or a checked manifest (`--manifest`, `--shard`),
  resuming on rerun; `batch --runs-to DIR` extracts without publishing and
  `publish RUN...` publishes staged runs in one step, for cluster jobs.
- Library index with lookup tables and full-text search (`lookup`,
  `search`, `index rebuild`), `catalog.md`, `library.bib` and
  `catalog.csl.json`.
- Library layouts (`flat`, `by-year`, `by-initial`) and `organize`;
  `reprocess` rebuilds papers from their kept output; `migrate` checks record
  versions and files and rebuilds outdated papers; records with an unknown
  version are refused.

#### Identity and versions

- Bibliographic identity from Crossref and DataCite, corroborated against
  the paper's own title, authors and year, with field-level status,
  `Family_Year_Title` names and parse-checked BibTeX; a corroborated Crossref
  search for papers without a DOI; arXiv identifiers resolved and
  `extract arXiv:ID` downloads.
- Document versions (version of record, accepted or submitted manuscript,
  preprint, unknown) with evidence or an assertion, and relations to other
  versions and likely duplicates in the library, never merged.

#### Figure descriptions

- `paperextract describe` adds labelled, machine-generated descriptions of
  figures with Qwen3.8-27B on an OpenAI-compatible server (default), Claude
  under a hard spend cap, or a local MLX worker; several papers at once
  (`[describe] papers`, 4 by default); descriptions are indexed separately
  from the paper's text.

#### Documentation and tooling

- User guide (getting started, command line, running on a Slurm cluster,
  figure descriptions, saved pages, known limitations, model licences) and
  reference pages in a manual with the PyData Sphinx theme; an agent skill
  in `skills/paperextract/`.
- Benchmark manifest validation and conservative table and equation
  comparisons.
- Typed package with an offline quality gate (lint, format, strict types,
  docstrings, spelling, tests with full branch coverage, warning-free manual)
  and a CI workflow for Linux and macOS on Python 3.12 and 3.14.

### Fixed

- PDFium calls are serialized; two threads rendering at once crashed the
  process.
- A serving worker that exits before answering a request it has not
  touched is replaced and the request sent once more; on Linux such a
  process could look alive for a moment and fail the next paper.
- Registry HTTP errors are closed after reading, which Python 3.14 warns
  about otherwise.
- Workers run with ONNX Runtime and vLLM telemetry switched off; ONNX Runtime
  1.30 had queued device telemetry and attempted uploads during extraction.
- Cancelling an extraction stops the worker's whole process group.
- Findings from the first review of published papers: merged-cell tables,
  Roman table numbers and continued captions, page footnotes, panel-crop
  links, running headers read as headings, split reference entries, lost drop
  capitals and glyphs without a Unicode mapping.

[Unreleased]: https://github.com/jtravs/paperextract/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/jtravs/paperextract/releases/tag/v0.1.0
