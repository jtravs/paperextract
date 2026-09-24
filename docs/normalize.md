# Canonical document and normalization

`paperextract.normalize.normalize_mineru` converts the worker's verified
`native/middle_json.json` into the canonical document schema defined in
`paperextract.document` (schema `paperextract.document`, version `0.1`, a
pre-release contract). `paperextract.pipeline.extract_pdf` runs the whole
first slice for one PDF: preserve, inspect, fingerprint, extract, normalize.

```python
from pathlib import Path

from paperextract.worker import WorkerEnvironment
from paperextract.pipeline import ExtractionSettings, extract_pdf

settings = ExtractionSettings(
    environment=WorkerEnvironment.for_repository(Path.cwd()),
    model_dir=Path("model-cache/mineru/models").resolve(),
    timeout_seconds=1800,
)
outcome = extract_pdf(
    Path("paper.pdf"), Path("staging/run-1"), settings, request_id="run-1"
)
if outcome.document is not None:
    print(len(outcome.document.blocks), [f.code for f in outcome.document.findings])
```

The staging directory must exist and be empty. Afterwards it holds
`source.pdf` and `source.json` (preserved copy, inspection and fingerprint),
`worker/` (request, result, logs and native output) and `document.json`. A
failed worker result returns without a document and leaves the earlier
artifacts for diagnosis.

## What normalization does

- **Geometry.** Backend boxes are fractions of the page. They become points
  with a top-left origin using the backend's own page size, falling back to the
  independent PDFium inspection; both the fractional and point boxes are kept
  in every `SourceSpan`, with the backend block type and index.
- **Prose.** `text`, `ref_text`, `page_footnote` and `aside_text` become
  paragraphs with roles `body`, `reference`, `footnote` and `aside`. Inline
  spans become runs of kind `text`, `math` (raw LaTeX), `code` or `link` with
  their styles. A block the backend marks as continuing across a page or column
  keeps `continues_previous`; blocks are not merged.
- **Headings.** `doc_title` and `paragraph_title` keep the backend's level.
- **Equations.** Raw LaTeX is copied verbatim, including `\tag`; the printed
  number is read from the last `\tag`. Unbalanced braces, `\left`/`\right`
  pairs or environments produce `EQUATION_UNBALANCED` and nothing is repaired.
- **Tables.** The backend HTML is kept verbatim and parsed into an exact cell
  grid with row and column spans; `html` keeps each cell's inner markup and
  `text` only strips tags, decodes entities and collapses whitespace. Ragged
  rows and overflowing spans become `TABLE_STRUCTURE_UNRESOLVED`. A caption
  that the backend filed among the footnotes but that starts with a table label
  is used as the caption with `TABLE_CAPTION_FROM_FOOTNOTE`. Labels may be
  Arabic or Roman (`TABLE III`), and a caption containing `(Continued)` marks
  the table as continuing the previous one.
- **Figures.** Consecutive `image` and `chart` blocks without intervening
  prose form a run. A caption beginning with a figure label closes the group
  before it, because captions are printed below their figures; a single-letter
  caption is a panel label. One captioned block is `single`, several are
  `caption_run` with `FIGURE_GROUPING_HEURISTIC`, and trailing blocks without a
  caption are `unresolved` with `CAPTION_UNMATCHED`. Every panel keeps its crop
  and span, and the figure records the union box for a complete-context crop.
- **Page furniture.** Headers, footers and page numbers are retained as
  `page_furniture` so their removal from exports can be audited.
- **Everything else** (`code`, `index`, unknown types) is retained as
  `unclassified` with the raw backend JSON and a `BLOCK_UNCLASSIFIED` finding.
- **Metadata.** Values from the PDF information dictionary that the backend
  reports are recorded as observations for the identity stage, not as identity.

## Document-level corrections

After normalization, `paperextract.layout.refine_document` corrects mistakes
that are only visible across blocks or pages. Each correction is recorded as a
finding; the native output under `diagnostics/raw/` keeps the backend's version.

- **Repeated furniture.** A heading or paragraph whose text and position match
  page furniture on at least two other pages becomes page furniture, such as a
  journal's running header read as a section title on one page.
- **Split reference columns.** On a two-column page whose left column ends with
  reference entries and whose right column continues the prose before more
  entries, the earlier entries move to join the later ones in order. A
  reference list that ends before a following section stays in place.
- **Drop capitals.** When a paragraph starts with a superscript lower-case
  fragment, the pipeline asks the PDF text layer for a capital just before it
  that is at least twice as tall and sits at the paragraph's top-left corner.
  A confirmed letter is restored; otherwise the paragraph is left unchanged
  and flagged.
- **Unmapped glyphs.** Control characters that stand for font glyphs without
  a Unicode mapping, typically math brackets in text-layer tables, are kept
  and reported, because the text they belong to is incomplete.

Schema 0.2 adds `body_source` (`native` or `ocr`) and `alternatives` to
tables; each alternative keeps its source, HTML and cell grid. Version 0.1
documents still read, as native bodies without alternatives. The table OCR
stage is described on the [command-line page](cli.md).

## Findings

| Code | Severity | Meaning |
| --- | --- | --- |
| `PAGE_NOT_PROCESSED` | error | A requested page was not returned by the worker. |
| `PAGE_EMPTY` | warning | A returned page has no blocks; content is unrecovered. |
| `PAGE_SIZE_UNKNOWN` | warning | No page size; boxes stay fractional. |
| `PAGE_MALFORMED`, `BLOCK_MALFORMED` | warning | Native entries that were not objects. |
| `ASSET_MISSING` | warning | A referenced crop is not among the worker's recorded files. |
| `EQUATION_UNBALANCED`, `EQUATION_EMPTY` | warning | Delimiter imbalance or empty LaTeX, kept verbatim. |
| `TABLE_STRUCTURE_UNRESOLVED`, `TABLE_BODY_MISSING` | warning | Grid problems or no HTML body. |
| `TABLE_CAPTION_FROM_FOOTNOTE` | info | Caption recovered from a misclassified footnote. |
| `CAPTION_UNMATCHED` | warning | A figure group or table has no caption. |
| `FIGURE_GROUPING_HEURISTIC` | info | Several blocks grouped up to one caption; review the panel set. |
| `LIST_FLATTENED`, `INLINE_UNCLASSIFIED`, `BLOCK_UNCLASSIFIED` | info | Structure retained in a simpler form. |
| `FURNITURE_RECLASSIFIED` | info | A repeated running header or footer was removed from the text. |
| `REFERENCES_REORDERED` | info | Reference entries split by a column of prose were rejoined. |
| `DROP_CAP_RESTORED` | info | A drop capital was restored from the PDF text layer. |
| `DROP_CAP_SUSPECTED` | warning | A likely lost drop capital could not be confirmed; text unchanged. |
| `UNMAPPED_GLYPHS` | warning | Text contains glyphs without a Unicode mapping; compare with the page image. |
| `TABLE_OCR_SELECTED` | info | The table body comes from an OCR re-extraction whose numbers all agree with the text layer. |
| `TABLE_OCR_REJECTED` | warning | An OCR re-extraction was kept only as an alternative because its numbers differ. |
| `TABLE_OCR_UNMATCHED`, `TABLE_OCR_FAILED` | warning | No OCR table at the same position, or the OCR run failed; the text-layer body is kept. |

Findings carry the affected block identifiers and page. Block identifiers are
derived from the source digest, page, backend type, index and position, so
re-normalizing the same native output yields the same document.

## Limits

No export exists yet: `document.json` is the canonical structure, not
`paper.md`. Bibliographic identity is not established; metadata entries are
observations. Figure grouping is a documented heuristic and a run that spans
two stacked figures without captions between them stays one unresolved group.
An empty page cannot be distinguished from a page the backend could not read.
