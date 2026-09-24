# Portable paper directory

`paperextract.storage.publish` turns a completed extraction staging directory
into a portable paper directory inside a library, and
`paperextract.storage.extract_and_publish` chains it after
`extract_pdf`:

```python
from pathlib import Path

from paperextract.worker import WorkerEnvironment
from paperextract.pipeline import ExtractionSettings
from paperextract.storage import extract_and_publish

settings = ExtractionSettings(
    environment=WorkerEnvironment.for_repository(Path.cwd()),
    model_dir=Path("model-cache/mineru/models").resolve(),
    timeout_seconds=1800,
)
outcome, paper = extract_and_publish(
    Path("paper.pdf"),
    Path("staging/run-1"),
    settings,
    Path("literature"),
    request_id="run-1",
)
if paper is not None:
    print(paper.directory)
```

## Layout

```text
literature/
  corpus.json                      library id, layout policy
  catalog.jsonl                    one row per published paper
  .paperextract/staging/           private build area, empty between runs
  .paperextract/replaced/          directories retired by reprocess, kept until deleted
  .paperextract/index.sqlite       derived lookup tables and full-text index
  catalog.md                       human-readable catalog, derived
  library.bib                      every validated citation.bib, derived
  catalog.csl.json                 CSL-JSON for reference managers, derived
  .paperextract/journal/           replacements in progress, recovered automatically
  Travers_2019_HighEnergyPulse/   or Unverified_<sha12>/ when identity is not validated
    paper.md                       primary Markdown with YAML front matter
    citation.bib                   only for a validated identity
    document.json                  canonical structure (schema 0.2)
    metadata.json                  field-level status, evidence and observations
    extraction.json                sources, request, result, assets, omissions
    validation.json                processing status, counts, checks, findings
    manifest.json                  every file with SHA-256 and size
    original/source_01/<name>.pdf  byte-identical preserved source
    figures/<figure id>.png        complete-figure crop rendered at 300 DPI
    figures/<figure id>_p01.jpg    backend panel crops
    tables/<table id>.{html,json,csv,jpg}
    equations/<equation id>.jpg    backend equation crops
    diagnostics/review.md          findings by severity
    diagnostics/raw/<run_id>/      native worker output, request, result, logs
    diagnostics/raw/<run_id>/table-ocr/        OCR re-extraction of glyph-damaged tables
    original/source_02/<name>.pdf  preserved supplement, when one was supplied
    supplement_01/supplement.md    supplement Markdown with anchors, front matter
    supplement_01/document.json    supplement canonical structure
    supplement_01/{figures,tables,equations}/  supplement assets
    diagnostics/raw/<run_id>/supplement_01/    supplement worker output
```

A supplement supplied with the paper is extracted by the same pipeline and
published inside the paper directory. Its figures, tables, equations and
numbered sections carry anchors such as `<a id="figure-s3"></a>`, and main-text
references such as "Supplementary Fig. 3", "Fig. S3", "Eq. (S5)" or
"Supplementary Information" link to them. A publisher link named like the
supplement, such as Optica's "Supplement 1", points at the converted copy
followed by the original link. `validation.json` lists each supplement's own
validation and, under `supplement_references`, the number of resolved
references and every unresolved one. The front matter, `extraction.json` and
the catalog row list the supplement's source with the role `supplement`.

Every reference inside the directory is relative, so it can be moved between
libraries or machines unchanged. When the [identity stage](identity.md) validates
a DOI, the directory is named `Family_Year_FirstWords`, `citation.bib` is written
and `metadata.json` carries validated fields; otherwise the name is `Unverified_`
plus the first twelve characters of the source digest, every field is null, and
the front matter, `metadata.json` and the catalog row carry the PDF information
title and authors only as *observed* values and DOI strings only as candidates.

## Transaction

The directory is assembled under `.paperextract/staging/`, `manifest.json` is
written last, and the build is renamed into the library in one step. A second
publication of the same source raises `PublicationConflictError` and leaves the
existing directory untouched; a failure during the build removes the partial
build and leaves the extraction staging directory intact. The catalog row is
appended after the rename, so a row never points at a half-written paper.

## paper.md

The Markdown is derived from `document.json`, never from a separate extraction.
It contains, in reading order: headings at the backend's level; prose with
inline LaTeX (`$...$`), code spans, links, bold, italic, superscript and
subscript; display equations as `$$` blocks with their `\tag`; lists; figures
as a bold label, the complete-figure crop, and the published caption with the
panel-crop links in parentheses after it, then figure notes; tables as a pipe
table whenever a cell grid was parsed, with the backend's cell math as `$...$`,
sub- and superscripts kept, and a merged cell shown once at its first row and
column with a note, followed by table notes and links to the exported files,
whose HTML and cell JSON keep the exact structure; the raw backend HTML only
when no grid could be parsed; asides as block quotes; references as
paragraphs; page footnotes, such as affiliations, labelled after the content
of their page, or after a paragraph that continues onto the next page. Glyphs
without a Unicode mapping appear as `�` so the gap is visible. Page changes are marked with `<!-- source: pdf page N -->`
comments. A paragraph the backend marks as a continuation joins the preceding
paragraph without a blank line. Headers, footers and page numbers are omitted
and listed in `extraction.json` under `omitted_blocks`; unclassified backend
blocks leave an HTML comment and stay in `document.json`.

Prose is emitted verbatim: no Markdown escaping is applied to scientific text,
so an isolated asterisk or underscore in the source can affect rendering in a
strict viewer. Retrieval consumers see the exact transcription.

## Reports

`validation.json` records the processing status (`COMPLETE` only when every
requested page returned content, else `PARTIAL`), object counts, severity
counts, every finding, and named checks with outcomes `pass`, `fail`, `review`
or `not_checked`. Bibliographic identity and cross-source comparison report
`not_checked` because those stages do not exist yet. `diagnostics/review.md`
lists the same findings for a reader.

## Catalog

`catalog.jsonl` gains one row per publication with the directory, generation,
observed title and its normalized lookup key, observed authors and identifiers,
DOI candidates, source and text digests, page and object counts, processing and
bibliographic status, backend and run identifiers. It is derived data: a later
`index rebuild` reconstructs it from the paper directories. `read_catalog`
returns the rows.
