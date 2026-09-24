# Initial corpus assessment

**Inspected:** 22 September 2026.\
**Conclusion:** sufficient to start P1 and the first extraction implementation.\
**Source directory:** a private directory outside Git.\
**Progress and next action:** `dev/Status.md`.

## Inventory and useful coverage

There are five distinct papers, six article/manuscript PDFs totaling 75 pages,
and a separate 15-page published HISOL supplement: 90 PDF pages to evaluate in
total. Two papers have both complete-webpage and MHTML captures. Fifteen more
PDFs are HISOL author figure assets, not fifteen additional papers. The HISOL ZIP
duplicates the supplied TeX and fifteen figures; its sixteen members were checked
against the extracted files and match byte for byte.

| Case | Supplied representations | What it exercises |
| --- | --- | --- |
| Shelton (1990) | 15-page PDF | Scanned two-column article with OCR; dense equations, uncertainties, multi-level table headers, and a table continued across pages. |
| Wahlstrand et al. (2012) | 5-page PDF | Modern two-column paper, inline maths and plots; proposed holdout for the first comparison. |
| Lehmeier et al. (1985), filename `ubr03516_ocr.pdf` | 6-page PDF | Scan with imperfect OCR, old typography, superscripts, powers of ten, table footnotes and missing entries. |
| COPRA | 11-page published PDF, complete HTML with asset directory, MHTML | Dense inline/display maths, three scientific tables, eight figure identifiers, duplicate page widgets, incomplete image capture. |
| HISOL, published | 10-page article PDF, 15-page supplement PDF, complete HTML with asset directory, MHTML | Multi-panel figures, scientific math markup, publisher copy editing, separate methods and supplementary material. |
| HISOL, accepted | 28-page manuscript PDF including supplement, `main.tex`, fifteen figure PDFs, source ZIP | Author reference for equations, table values, captions and figure assets; rotated Table S2, grouped headers, footnotes, explicit blank cells; version separation. |

The manifest records 187 non-Finder files, including capture resources and source
build artifacts. `.DS_Store` files are excluded. These counts describe the frozen
inspection snapshot; recheck when the user adds files. DOIs found locally remain
metadata candidates until registry verification. No metadata service was queried
during this inspection.

All seven article/manuscript/supplement PDFs were inspected with `pdfinfo` and
`pdftotext -layout` (Poppler 26.08.0). All pages have extractable text; that does
not establish correctness. `pdfimages -list` confirms full-page scan images in
Shelton and Lehmeier. Their OCR already shows damaged symbols and words, so a
text-layer-present heuristic must not disable OCR or visual checks unconditionally.

Visual inspection covered the first page of each of these seven PDFs, Shelton
page 6, Lehmeier page 5, and accepted HISOL pages 15 and 23. This establishes
representative layout and failure modes, not an exhaustive page-by-page audit.
Wahlstrand has only been inventoried and first-page inspected; no backend output
or tuning has used it. Keep that distinction when claiming a holdout result.

## HTML and MHTML capture comparison

The supplied MHTML files were decoded as MIME containers, without executing scripts
or fetching remote resources. Their main HTML was compared with the saved complete
webpages. These observations apply to these snapshots, not to every capture made
with the same browser or format.

| Snapshot | Equation evidence in main HTML | Article figure payloads available locally |
| --- | --- | --- |
| COPRA complete HTML | 299 MathJax SVG containers; no `<math>` elements or original-TeX annotations found | 0 of 8; the figure elements use a loading GIF and remote `data-src` URLs. |
| COPRA MHTML | 299 MathJax SVG containers; no `<math>` elements | 0 of 8; MIME images are page furniture/loading assets, not the article figures. |
| HISOL complete HTML | 10 `<math>` elements, including 9 display blocks | All 5 main figures as PNG files, each 685 pixels wide. |
| HISOL MHTML | 10 `<math>` elements | Only main Fig. 1 is embedded, as WebP; Fig. 2–5 payloads are absent. |

DOM counts are not unique scientific-object counts. COPRA repeats its scientific
tables in page widgets, and HISOL has ten `<figure>` elements for five main figures.
Related-article thumbnails must not be mistaken for this article's figures. SVG
glyphs and speech metadata may help recover maths but are not equivalent to the
original TeX or a verified MathML expression. Ten MathML elements likewise do not
prove that every inline formula survived.

MHTML does not by itself solve deferred image loading. The HISOL archive also
demonstrates MIME-based image typing: Fig. 1 has a PNG-looking URL but a WebP
payload. Resolve both `Content-Location` and HTML `<picture>/<source>` references;
do not guess the image format from a URL suffix or remove query strings blindly.

Keep these captures unchanged as missing-asset regression cases. The supplied PDFs
allow work to proceed. Later, compare a fresh capture after scrolling through the
whole article and loading each figure, preferably also obtaining original-size
assets. A SingleFile comparison can then test the proposed acquisition advice.
No replacement capture is required before starting extraction.

## How HISOL becomes reference evidence

Use three related sources of truth with explicit scope:

1. **Author TeX and original figures:** exact source-level evidence, including
   values, signs, labels, precision and intended relationships.
2. **Rendered accepted manuscript:** the visual reference for extracting that
   specific PDF. Match TeX spans to its pages and labels before scoring them.
3. **Published article and published supplement:** the references for extracting
   the journal versions. Align with the author material, preserving legitimate
   copy edits, changed labels and coverage differences.

Never treat every TeX/PDF text difference as an extraction error. Typesetting
expands macros, inserts numbering and resolves references; journal editing changes
wording and presentation. Preserve the source TeX and rendered expectation
separately. Compare critical equation symbols and grouping under explicit,
reviewed normalization rules rather than requiring identical TeX serialization.
Keep uncertain alignments unresolved instead of repairing the published article.

The supplied accepted PDF includes its supplement beginning on PDF page 15
(printed page S1); Table S2 is rotated within PDF page 23. The published supplement
is a separate 15-page file whose first page is the journal cover. Its title page
says the supplementary information is supplied by the authors and unedited, but
that statement alone is not a byte-for-byte equivalence check. The published main
article does not contain Tables S1/S2. Missing them from extraction of the main
article alone is not a failed page extraction.

The TeX/source archive was inspected without compiling or executing it. Full
source-to-PDF equivalence has not been established. General-purpose TeX ingestion
remains deferred; source-assisted benchmark annotation does not require it.

## Private inspection artifacts

Artifacts live under the ignored project directory `data/corpus-inspection/`.
These files are available to another agent on this checkout, but are not included
in Git, packages, or an ordinary clone. Transfer them deliberately if moving hosts.
Keep publisher material and extracted content private by default.

| Artifact | Current contents and limits |
| --- | --- |
| `benchmark-v1.json` | Executable schema v1: seven PDF sources and 44 candidate references; validated by `paperextract.benchmark`. Derived explicitly from the retained drafts; candidates remain unpromoted. |
| `manifest.json` | Draft `0.1` inventory; relative paths, sizes, SHA-256 hashes, source IDs, work/version relationships, page counts, proposed split and provenance. External source root is local configuration. Not yet a supported schema/API. |
| `gold-candidates.json` | Draft reference records: 42 exact Table S1 cell strings, including 4 blanks, plus 2 display equations. Source hashes and PDF/TeX locators included. Agent visually checked; no owner review or automated scoring yet. |
| `capture-comparison.json` | Per-capture hashes, markup counts and available article-image payloads; no claim that capture quality is fully scored. |
| `*.pdfinfo.txt`, `*.txt`, `*.png` | Poppler inspection output and the pages rendered for review. Text output is diagnostic evidence, never accepted as gold by itself. |
| `*.audit.json`, `*.mime-audit.json`, `*-part*.html` | Detailed private HTML/MIME observations and decoded HTML; may contain browser capture metadata and article content. |

The candidate cells cover Table S1 rows with pressure keys 230, 276, 300, 350,
1100 and 1200 mb, across all seven columns. They preserve values such as `2.0`
and `1.0` as strings and leave the four unpublished RDW-energy/efficiency cells
blank. Blank is not zero. Source header units remain `mb`; annotation must not
silently standardize the author's unit notation.

These 44 candidates are a useful seed, not completed gold coverage. Still add
inline equations, scan-derived numeric cells, reading order, complete figure and
caption associations, figure-description claims, and Table S2 topology/footnotes.
Promote candidates only after comparison rules and reference scope are checked;
never replace them automatically with an extractor's output.

## Next experiment

Proceed with P1 Mac runtime qualification using isolated, pinned environments for
Docling, Marker and MinerU. Start with accepted HISOL pages 1 and 15, published
HISOL and its supplement as separate inputs, and Shelton page 6. Then inspect
complete-document coverage, COPRA equations and the Lehmeier scan. Keep Wahlstrand
out of profile tuning and use it for a later check.

Record successful and failed runs, exact versions, model revisions/licenses,
profile/precision, CPU versus Metal/MPS execution, cold/warm wall time, memory,
raw outputs and scientific defects. Do not extrapolate full-paper throughput from
selected pages. No backend/model trial or hardware throughput measurement has been
performed during this corpus assessment.

Additional papers are useful later for PDFs with no OCR layer, corrupt/encrypted
inputs, changed preprints, identity distractors and other publishers. Synthetic
fixtures can cover many structural and failure cases without redistributing papers.
There is no need to collect a larger corpus before this next experiment.
