# Known limitations

paperextract records what it could not verify instead of guessing, but a
model reading a PDF can still be wrong in ways no check detects. Treat
extracted equations and table values as transcriptions to check against the
page when a result matters. This page lists what is known; `validation.json`
in each paper directory lists what was flagged for that paper.

## Accuracy has not been measured on a reviewed reference set

The quality evidence so far is a pilot of about 15 papers: exact matches of
42 hand-checked table cells on one paper, comparisons between backends, between
the Mac and GPU engines, and with publishers' web pages, and reading of the
output. There is no reviewed gold set of equations, tables and figures yet,
so there are no accuracy rates to quote.

## Content

| Area | Limitation |
| --- | --- |
| Equations | LaTeX is the backend model's transcription and can differ from the printed equation, for example a `v` read as `\nu` or a dropped subscript. Engines differ from each other on a few equations per paper. Nothing is repaired; an equation that does not parse is flagged. |
| Inline math | Symbols inside running text may be read as math by one backend and as plain text by another. |
| Tables | Cell strings are kept exactly as read. Header cells with markup keep it in the HTML but are flattened in the plain `text` (such as `EnergyaµJ`). A table's printed axis row or header can be dropped by the model. When the text layer has lost glyphs, the page is read again with OCR and both readings are kept. |
| Figures | Captions are attached by label. A backend can split one figure into many tiles or merge panels, so panel crops may not match the printed panels; the whole-figure crop is always kept. Unmatched captions and heuristic groupings are flagged. |
| Scanned pages | Old scans are read with OCR; accuracy depends on the scan, and agreement between backends on scanned tables ranged from about half to almost all cells in the pilot. |
| Page furniture | Headers, footers and footnotes are separated by the backend's layout model and can be misclassified. |

## Identity and versions

- Identity comes only from registries (Crossref, DataCite, arXiv's DOIs) and
  must be corroborated by the paper; a paper without a validated DOI or a
  matching registry record stays `Unverified`. Papers not in Crossref, such
  as many older conference papers, stay unverified.
- The document version (accepted manuscript, version of record) is often
  unknown, because PDFs rarely state it. Papers are related to other
  versions by DOI or by title, first author and year, but never merged.

## Saved web pages and supplements

- A saved article page is preserved and compared with the PDF extraction;
  its content is never merged into the paper, and math is not compared.
- A supplement cannot be added to a paper that is already published;
  extract the paper again with `--supplement`.
- A reference range such as "Supplementary Figs. 3 and 4" links its first
  number only.

## Figure descriptions

Descriptions are machine generated and labelled as such. The model can
misread a plot; claims are checked against printed text on the figure where
possible, and about one figure in 80 fails to produce a parseable answer
(recorded as failed, not dropped).

## Backends and platforms

| Area | Limitation |
| --- | --- |
| Platforms | macOS on Apple silicon and Linux on x86_64 only; no Windows. paperextract runs from a source checkout. |
| MinerU on Linux without a GPU | The llama.cpp engine runs on the CPU and is slow: about 20 minutes per paper on 8 cores. |
| MinerU with vLLM | Output differs slightly from the Mac's llama.cpp engine, which uses quantized weights. With ordinary batching two runs of the same paper can differ; `batch_invariant = true`, the default, makes runs repeatable at about 1.5 times the time. |
| Docling | Its formula model is slow on a CPU; its tables read old scans worse than MinerU. |
| Marker | Its vision-language server has no CUDA build for Linux, so it runs on the CPU there. |
| Figure-description model | Qwen3.8-27B in bf16 needs a GPU with about 56 GB, or two GPUs of one node. |

## Operations

- A cancelled `batch` writes no result document; rerunning it resumes,
  because published sources are skipped by their digest and staged runs are
  reused.
- `--pages` selects pages for trials; the result is published with
  `PARTIAL` coverage.
