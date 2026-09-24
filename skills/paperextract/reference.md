# paperextract reference for agents

## Library root

| Path | Content |
| --- | --- |
| `corpus.json` | Library identifier and `layout` (`flat`, `by-year`, `by-initial`). In a sharded layout papers live one level down, such as `2019/Travers_2019_HighEnergyPulse`; catalog `directory` values are those relative paths. |
| `catalog.jsonl` | One JSON row per paper: `directory`, `doi`, `arxiv`, `title`, `authors`, `year`, `venue`, `abstract`, `document_version`, `bibliographic_status`, `processing_status`, `source_sha256`. |
| `catalog.md`, `library.bib`, `catalog.csl.json` | Human, BibTeX and CSL-JSON views of the catalog. |
| `.paperextract/` | Private: index, runs, journals, acquired downloads. Do not edit. |

## `paper.md` front matter

`title`, `authors`, `journal`, `year`, `doi`, `arxiv`, `document_version`,
`bibliographic_status`, `processing_status` (`COMPLETE` or `PARTIAL`),
`observed` (what the PDF itself shows) and `sources` (the PDF, supplements and
saved pages with their roles).

## Finding objects in `paper.md`

Figures and tables start with a bold label line (`**Table 1**`, `**Figure 2**`)
followed by `**Published caption:**`; search for those. Display equations are
between `$$` lines and inline math between single `$`, both as LaTeX. A
supplement's objects carry anchors such as `figure-s3` and `table-s1` that the
main text links to.

## `document.json`

The canonical structure (schema `paperextract.document` 0.3): `blocks` in
reading order with `kind` (`heading`, `paragraph`, `list`, `equation`,
`figure`, `table`, `page_furniture`, `unclassified`), each located by page
and a box in points. Tables have `cells` (row, column, spans, text) and
`alternatives` (other readings: `ocr`, `docling`). `findings` list problems
by `code`, `severity`, `block_ids` and `page`.

## Findings worth reporting

| Code | Meaning for a reader |
| --- | --- |
| `PAGE_NOT_PROCESSED`, `PAGE_EMPTY` | Content of that page is missing. |
| `UNMAPPED_GLYPHS`, `GLYPH_CODES` | Some characters (often math brackets) were lost; check the page. |
| `TABLE_OCR_SELECTED` | The table body was re-read from the page image; its numbers matched the text layer. A changed grid ("5x2 became 4x2") usually means a spurious row was dropped; the text-layer reading stays in the table JSON's `alternatives`, and `tables/<id>.jpg` shows the printed table. |
| `TABLE_OCR_REJECTED`, `TABLE_CHECK_DISAGREED` | Two readings of the table disagree; check the page before quoting numbers. |
| `TABLE_CHECK_AGREED` | An independent Docling reading has the same numbers. |
| `CAPTION_UNMATCHED`, `FIGURE_GROUPING_HEURISTIC` | Figure panels or captions may be grouped wrongly. |
| `EQUATION_UNBALANCED`, `EQUATION_NOT_TRANSCRIBED` | The LaTeX is structurally broken or absent. |
| `HTML_*` | Results of comparing a saved publisher page with the PDF (`html_check.json`). |
| `OTHER_VERSION_IN_LIBRARY`, `DUPLICATE_CANDIDATE` | Related or possibly duplicate papers (`validation.json` → `relations`). |

## JSON outputs

- `search --json`: `hits[]` with `library`, `directory`, `title`, `year`,
  `doi`, `score` (lower is better), `snippet` (matches in brackets) and
  `in_description`.
- `lookup --json`: `matches[]` with `query`, `library`, `status`,
  `directory`, `doi`, `title`, `reason`.
- `extract`, `batch`, `reprocess`, `describe --json`: `items[]` with `path`,
  `status`, `directory`, `identity`, `processing`, `findings`, `message`,
  `supplements`, `captures`, `warnings`.

## Manifest line

```json
{"id": "hisol", "paper": "hisol/paper.pdf", "supplements": ["hisol/si.pdf"], "html": ["hisol/page.mhtml"], "document_version": "version_of_record"}
```

after the header `{"schema": "paperextract.batch-manifest", "schema_version": 1}`;
`paper` may also be `arXiv:ID`. Paths are relative to the manifest.

The manual (`docs/` in the checkout, especially `cli.md`, `export.md`,
`html.md`, `describe.md`) has the full details.
