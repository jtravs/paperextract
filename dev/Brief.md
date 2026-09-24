# Scientific Paper Extraction and RAG Preparation Tool — Requirements and Goals

I want you to act as the planning/design agent for a software project. Do **not** begin implementation yet.

First investigate the current state of the relevant libraries, models, metadata services and document formats, then produce a detailed architecture and implementation plan satisfying the requirements below.

The objective is to build a robust, local-first tool for converting scientific papers into a high-quality, structured, machine-readable representation suitable for use as long-term RAG/project knowledge in systems such as ChatGPT Projects, Claude Projects, or other literature-analysis systems.

The corpus will consist primarily of physics and optics papers. These are often two-column documents with substantial mathematics, plots, diagrams, multi-panel figures, numerical tables, footnotes, references, superscripts/subscripts, Greek symbols, scientific notation and specialist notation.

Preservation of scientific content is considerably more important than cosmetic reproduction of the paper.

The tool must explicitly treat:

- prose;
- equations;
- figures;
- figure captions;
- numerical tables;
- table structure;
- bibliographic identity;
- metadata;
- provenance

as first-class scientific content.

Accuracy, provenance and auditability are higher priorities than raw extraction speed.

---

# 1. Expected real-world workflow

The normal workflow should be extremely simple.

In practice I will usually obtain papers manually because journal access restrictions, institutional authentication and publisher access controls make automated acquisition unreliable.

Therefore the **primary supported workflow should assume that I already possess the source files**.

Typical inputs might be:

- a local PDF;
- a locally saved publisher HTML article;
- a PDF plus saved publisher HTML for the same paper;
- an MHTML or other self-contained browser capture;
- a directory containing an HTML file plus associated assets;
- an arXiv URL or identifier;
- occasionally a directly accessible PDF URL;
- occasionally a directly accessible journal article URL;
- eventually a directory or manifest containing many papers.

Automated publisher downloading is not a core requirement.

Do not spend substantial engineering effort attempting to bypass publisher authentication or reproduce my browser login/session.

The system should instead make ingesting manually downloaded material extremely convenient.

---

# 2. A paper may have multiple source representations

Do **not** assume that one paper corresponds to exactly one input file.

An important workflow will be supplying both:

```text
paper.pdf
publisher.html
```

or equivalent saved resources for the same article.

Treat these as multiple representations of the **same scholarly work**, not as duplicate papers.

Conceptually, an input may therefore be a:

```text
PaperSourceBundle
```

containing one or more of:

- publisher PDF;
- accepted manuscript PDF;
- arXiv PDF;
- publisher HTML;
- saved self-contained HTML;
- HTML plus asset directory;
- MHTML;
- source TeX where available;
- supplementary material;
- metadata supplied by the user.

The architecture should explicitly support this concept.

---

# 3. PDF and HTML should be complementary evidence

Publisher HTML quality is highly variable.

Some publishers expose excellent structured HTML containing:

- clean paragraphs;
- semantic headings;
- MathML or TeX equations;
- structured tables;
- high-resolution SVG/PNG figures;
- DOI metadata;
- author metadata;
- references.

Others expose poor HTML containing:

- low-resolution rasterized equations;
- badly rasterized figures;
- incomplete content;
- dynamically loaded elements;
- inaccessible image assets;
- broken table semantics;
- formatting that is worse than the PDF.

Therefore do **not** establish a universal rule that HTML is always better than PDF or vice versa.

When both are supplied, the system should be capable of taking the best representation of each scientific object.

For example:

- bibliographic metadata may come from DOI/Crossref/publisher metadata;
- paragraph text may come from HTML if clean;
- equations may come from HTML MathML/TeX if reliable;
- equations may instead come from PDF extraction if the HTML contains only images;
- tables may come from semantic HTML if available;
- figures may come from high-resolution publisher HTML assets;
- PDF figures may be preferred if publisher HTML assets are degraded;
- PDF page coordinates remain useful for visual provenance.

The planner should design a principled reconciliation strategy.

---

# 4. Cross-checking PDF and HTML

Where both PDF and HTML representations exist, investigate using them to validate each other.

Potential examples:

### Equations

Compare HTML MathML/TeX against equation recognition from the PDF.

Large disagreement should create a warning rather than silently choosing one.

### Tables

Compare HTML table cell structure against PDF-derived table extraction.

Differences in:

- dimensions;
- headers;
- numerical values;
- scientific notation;
- units

should be detectable where practical.

### Figures

Compare:

- figure count;
- captions;
- panel labels;
- image dimensions;
- figure numbering.

Prefer the highest-quality original image asset rather than blindly selecting the HTML or PDF version.

### Text

Use HTML reading order as evidence when PDF multi-column reconstruction is ambiguous.

### References

Compare HTML structured references against extracted PDF bibliography.

This cross-source checking could substantially improve reliability and should be considered an important feature when multiple source representations are available.

It need not all be implemented in version 1, but the architecture should support it.

---

# 5. Primary user interface

For a simple case, conceptual usage should be approximately:

```text
paperextract paper.pdf
```

with optional backend selection such as:

```text
paperextract paper.pdf --backend marker
paperextract paper.pdf --backend mineru
paperextract paper.pdf --backend docling
paperextract paper.pdf --backend auto
```

For paired inputs, something conceptually like:

```text
paperextract paper.pdf publisher.html
```

or:

```text
paperextract source_bundle/
```

should be possible.

Do not regard these exact commands as fixed requirements.

Design the CLI after considering the internal model.

The common case should nevertheless remain extremely simple.

---

# 6. Corpus directory

The output should live inside a user-selected corpus directory containing adjacent paper directories.

For example:

```text
literature/
    Travers_2019_SolitonDynamics/
    Joly_2011_BrightSpatiallyCoherent/
    Mak_2013_...
```

Folder names should be:

- human readable;
- automatically generated;
- deterministic;
- based on validated metadata;
- filesystem safe;
- reasonably short;
- collision resistant.

A scheme resembling:

```text
FirstAuthor_Year_ShortTitle
```

is likely appropriate.

Use a deterministic disambiguating suffix only when necessary.

Do not accidentally merge distinct papers.

---

# 7. Canonical output for each paper

A successfully processed paper should produce something conceptually like:

```text
FirstAuthor_Year_ShortTitle/
│
├── paper.md
├── citation.bib
├── metadata.json
├── extraction.json
├── validation.json
│
├── original/
│   ├── paper.pdf
│   ├── publisher.html
│   └── publisher_files/
│
├── figures/
│   ├── figure_01.png
│   ├── figure_01.svg
│   ├── figure_02.png
│   └── ...
│
├── tables/
│   ├── table_01.csv
│   ├── table_01.html
│   ├── table_01.png
│   ├── table_02.csv
│   └── ...
│
└── diagnostics/
    └── ...
```

This exact hierarchy is not mandatory.

Propose the best structure.

However, the conceptual outputs are mandatory:

1. original supplied source material;
2. a single primary text Markdown representation;
3. extracted figure assets;
4. structured table data;
5. validated bibliographic metadata;
6. validated BibTeX;
7. extraction provenance;
8. validation/quality information.

The directory should be portable.

Relative references inside `paper.md` should continue to work if the complete directory is moved.

---

# 8. Primary Markdown document

The central artifact should be:

```text
paper.md
```

This should be a UTF-8 Markdown document designed both for human inspection and RAG ingestion.

It should contain the complete scientific textual content as far as practical.

Preserve:

- title;
- authors;
- abstract;
- sections;
- subsections;
- paragraphs;
- lists;
- equations;
- equation numbers;
- figure captions;
- textual figure descriptions;
- tables or searchable representations of tables;
- table captions;
- table footnotes;
- acknowledgements;
- appendices;
- references;
- supplementary sections contained in the supplied article.

Remove noise such as:

- repeated headers;
- repeated footers;
- page numbers in prose;
- publisher navigation;
- web menus;
- cookie notices;
- unrelated sidebar material.

PDF line wrapping must not remain as artificial paragraph breaks.

Hyphenation introduced solely by typesetting should normally be removed.

Multi-column reading order must be reconstructed correctly.

---

# 9. Metadata in `paper.md`

Use machine-readable front matter, preferably YAML unless there is a compelling alternative.

Conceptually:

```yaml
---
title: "..."
authors:
  - name: "..."
    orcid: "..."
journal: "..."
year: 2026
volume: "..."
issue: "..."
pages: "..."
article_number: "..."
doi: "..."
arxiv: "..."
publisher: "..."
publication_date: "..."
canonical_url: "..."
document_version: "published"
license: "..."
source_types:
  - pdf
  - publisher_html
source_sha256:
  pdf: "..."
  publisher_html: "..."
extraction_backend: "..."
extraction_backend_version: "..."
extraction_date: "..."
---
```

Design the exact schema carefully.

Potential metadata includes:

- complete title;
- ordered author list;
- ORCIDs where reliably available;
- affiliations where reliably available;
- journal/proceedings title;
- publisher;
- DOI;
- arXiv identifier;
- volume;
- issue;
- pages;
- article number;
- publication year;
- publication date;
- abstract;
- keywords;
- article type;
- license/open-access status;
- canonical URL;
- publisher URL;
- arXiv URL;
- preprint/publication relationships.

Do not invent absent metadata.

---

# 10. Bibliographic identity must be independent of OCR

Do not use first-page OCR as the sole source of bibliographic truth.

Attempt to establish identity using appropriate evidence such as:

- DOI embedded in the PDF;
- DOI supplied by the user;
- Crossref;
- DataCite;
- publisher metadata;
- structured HTML metadata;
- arXiv metadata;
- OpenAlex;
- GROBID;
- PDF metadata;
- fuzzy title/author matching where necessary.

Establish an evidence hierarchy.

Avoid confusing:

- arXiv preprint and journal publication;
- conference and journal versions;
- accepted manuscript and version of record;
- correction/erratum and original paper;
- supplementary material and main paper;
- similarly titled works.

If multiple representations correspond to the same scholarly work, record that fact.

Do not pretend an arXiv PDF is the publisher version merely because they share a DOI relationship.

---

# 11. Metadata provenance

For important canonical metadata fields, consider retaining:

- selected value;
- source;
- validation status;
- confidence;
- alternative conflicting values.

For example:

```json
{
  "doi": {
    "value": "10....",
    "status": "validated",
    "sources": ["crossref", "pdf", "publisher_html"]
  }
}
```

The exact representation is up to the planner.

---

# 12. BibTeX generation and validation

Every paper directory should contain:

```text
citation.bib
```

The entry should describe the actual scholarly work.

For DOI-bearing papers, prefer authoritative identifier-based metadata such as Crossref/DataCite/publisher metadata.

The BibTeX should be independently checked where practical.

Investigate whether tools such as:

```text
vishakhpk/verify_citations
```

or alternatives provide useful secondary verification.

Do not make fuzzy citation-verification tools the sole source of truth.

Useful validation states might include:

```text
VALIDATED
VALIDATED_WITH_WARNINGS
UNVERIFIED
CONFLICT
```

BibTeX should preserve:

- complete author list;
- title;
- appropriate capitalization;
- journal;
- year;
- volume;
- issue;
- page range or article number;
- DOI;
- URL where useful.

Citation keys should be deterministic and human readable.

---

# 13. Equations are first-class content

Both inline and display mathematics must be preserved.

Canonical Markdown should use LaTeX math.

For example:

```markdown
The nonlinear length is \(L_\mathrm{NL}=1/(\gamma P_0)\).

$$
i\frac{\partial A}{\partial z}
-\frac{\beta_2}{2}\frac{\partial^2A}{\partial t^2}
+\gamma|A|^2A=0.
$$
```

Requirements:

- inline equations remain inline;
- display equations remain blocks;
- equation numbers are preserved;
- equation references remain meaningful;
- Greek symbols remain correct;
- superscripts/subscripts remain correct;
- matrices are preserved;
- multiline equations are preserved;
- fractions are preserved;
- scientific notation remains correct;
- adjacent prose must not become part of an equation.

---

# 14. Prefer semantic math where available

If HTML contains genuine:

- MathML;
- embedded TeX;
- structured equation markup

prefer this over reconstructing the same equation visually.

However, verify that the HTML representation is actually semantic.

Some publishers merely embed equations as images.

When HTML equation quality is inferior or absent, use the PDF extraction path.

If both exist, cross-comparison is desirable.

---

# 15. Equation validation

Investigate automatic checks such as:

- balanced delimiters;
- plausible LaTeX structure;
- detection of truncated equations;
- unusual prose embedded inside math;
- disagreement between PDF and HTML equations;
- equation-number continuity;
- source coordinate/page mapping.

Do not use a general LLM to silently rewrite an equation according to what it thinks the physics ought to be.

If uncertain, preserve the best extraction and flag it.

Scientific plausibility is not proof of transcription fidelity.

---

# 16. Figures are first-class output

For every scientific figure, obtain the highest-quality practical representation.

Preference may be:

1. original publisher SVG/vector asset;
2. original high-resolution publisher raster asset;
3. native image extracted from PDF;
4. high-resolution PDF crop.

Do not unnecessarily rasterize vector content.

Retain a PNG preview where useful for compatibility.

Each figure should have:

- stable internal identifier;
- published figure number;
- original caption;
- page/source location;
- relative asset path;
- source representation;
- optional machine-generated description.

---

# 17. Figure handling when both HTML and PDF exist

Do not blindly prefer HTML figures.

Publisher HTML may contain:

- excellent SVG;
- full-resolution PNG;
- thumbnail images;
- heavily compressed JPEGs;
- rasterized versions inferior to the PDF.

Inspect available resolution/quality.

If HTML supplies an original high-quality SVG or image, prefer it.

If the HTML representation is degraded, recover from the PDF instead.

Record where each selected figure asset came from.

---

# 18. Figure representation in Markdown

Conceptually:

```markdown
## Figure 3

![Figure 3](figures/figure_03.png)

**Published caption:** ...

**Machine-readable visual description:** ...
```

The exact format should be optimized for RAG retrieval and human readability.

The published caption and generated description must be clearly distinguished.

---

# 19. Figure descriptions for RAG

Because many RAG systems retrieve only textual information, optionally produce scientific textual descriptions of figures.

For plots, descriptions should attempt to identify:

- plot type;
- panel labels;
- axes;
- physical quantities;
- units;
- approximate ranges;
- legend entries;
- plotted variables;
- line/marker labels;
- visible annotations;
- major qualitative trends;
- extrema;
- transitions;
- comparisons between curves or panels.

For example:

```text
Panel (b) plots spectral intensity versus wavelength and propagation
distance. Wavelength spans approximately 200–1200 nm. A narrow UV
feature appears close to 250 nm after approximately 12 cm.
```

Descriptions are an indexing aid, not authoritative data.

---

# 20. Figure-description reliability

Descriptions must clearly distinguish:

- explicitly printed numbers;
- approximate values visually inferred from axes;
- qualitative observations.

Do not fabricate precise numerical measurements from a plot.

Do not replace:

- original image;
- published caption

with the generated description.

Keep them all.

---

# 21. Figure-description backend

Figure description should be modular.

Possible modes:

```text
none
local VLM
remote VLM
backend-integrated VLM
```

Local/offline operation should be possible.

It should be possible to extract the corpus now and run a better VLM later without re-running document extraction.

Likewise:

```text
extract papers first
describe figures later
```

should be a supported workflow.

---

# 22. Multi-panel figures

Preserve composite figures.

Where reliable, additionally segment panels such as:

```text
(a)
(b)
(c)
(d)
```

but do not make segmentation mandatory.

Do not falsely split a figure based on uncertain recognition.

Descriptions should preserve relationships between panels.

---

# 23. Tables are first-class scientific data

Numerical tables are extremely important.

A major intended use case is asking:

```text
Across these papers, list the core diameter, gas pressure,
input pulse energy and compressed pulse duration.
```

Therefore tables must retain actual cells rather than merely prose summaries.

For every table attempt to retain:

1. structured content;
2. visual source representation;
3. caption;
4. footnotes;
5. provenance.

Possible outputs:

```text
tables/table_03.html
tables/table_03.csv
tables/table_03.png
```

---

# 24. HTML as canonical rich table representation

CSV is useful but cannot represent:

- merged cells;
- row spans;
- column spans;
- hierarchical headers;
- complex footnotes.

For complex tables, HTML or structured JSON should preserve these relationships.

Do **not** flatten a complex table into CSV if doing so changes its meaning.

CSV may be an additional simplified representation.

The original image/crop must remain available for checking.

---

# 25. Numerical table fidelity

Preserve exactly:

- decimal places;
- signs;
- exponents;
- scientific notation;
- uncertainties;
- `±`;
- ranges;
- inequalities;
- units;
- superscripts;
- subscripts;
- blanks;
- footnotes;
- repeated/ditto semantics.

For example:

```text
3.20 × 10^-4
```

must not become:

```text
3.20
10
-4
```

and:

```text
5.2 ± 0.3
```

must not become two unrelated cells.

Numerical fidelity is more important than visually attractive formatting.

---

# 26. Table representation for RAG

For simple tables, include a searchable table representation inline in `paper.md`.

Optionally also produce row-oriented text such as:

```text
Row: Argon
Pressure = 5 bar
Energy = 120 µJ
Duration = 8.1 fs
```

This must be mechanically derived from structured data.

Do not let an LLM paraphrase numerical cells.

For complicated tables, inline HTML inside Markdown may be more appropriate than Markdown table syntax.

Investigate this explicitly.

---

# 27. Table validation

Investigate practical checks for:

- inconsistent column counts;
- malformed header hierarchies;
- unexpected isolated exponent tokens;
- missing units;
- row shifts;
- column shifts;
- malformed numerical strings;
- unusual empty-cell patterns;
- disagreement between HTML and PDF tables;
- mismatch between table captions and extracted objects.

Flag questionable tables for manual inspection.

Prefer:

```text
Table 2 requires inspection
```

over silently returning plausible but incorrect numbers.

---

# 28. Source provenance

Scientific content should remain traceable to its origin.

Where practical retain:

- PDF page;
- bounding box;
- HTML DOM identifier/path;
- source representation;
- figure/table number;
- equation number;
- backend block identifier.

Detailed mappings may live in `extraction.json`.

`paper.md` should remain readable.

Optional unobtrusive anchors such as:

```html
<!-- source: pdf page 7 -->
```

may be useful.

---

# 29. Normalized internal document model

Do not allow Marker/MinerU/Docling-specific structures to permeate the entire application.

Design a normalized internal representation containing concepts such as:

```text
Document
Section
Paragraph
Equation
Figure
Table
Reference
MetadataField
SourceSpan
ValidationResult
```

Objects should support multiple possible source representations.

For example, an equation might have:

```text
pdf extraction
html MathML extraction
chosen canonical representation
validation result
```

This enables multi-source reconciliation.

---

# 30. Extraction backend abstraction

Support at minimum:

- Marker;
- MinerU;
- Docling.

Do not assume APIs from memory.

Inspect current releases and documentation.

I want explicit selection:

```text
--backend marker
--backend mineru
--backend docling
```

and eventually:

```text
--backend auto
```

The canonical output format must remain independent of backend.

---

# 31. Hybrid extraction

Do not prevent workflows such as:

```text
Marker for main PDF extraction
Docling for tables
publisher HTML for equations
local VLM for figures
GROBID for references
Crossref for metadata
```

It is not necessary to expose all combinations immediately.

Avoid an architecture that assumes one backend must perform every task.

Potential future selectors might include:

```text
--text-backend
--equation-backend
--table-backend
--figure-backend
--metadata-backend
```

Only expose this complexity if justified.

---

# 32. Backend comparison facility

It should be easy to process the same papers using:

- Marker;
- MinerU;
- Docling

and compare:

- text fidelity;
- reading order;
- equation fidelity;
- inline mathematics;
- table fidelity;
- merged-cell preservation;
- figure extraction;
- caption association;
- runtime;
- CPU use;
- GPU use;
- RAM;
- VRAM.

The best backend may change as upstream projects evolve.

Do not assume one backend will remain universally best.

---

# 33. HTML ingestion

HTML should be treated as a genuine structured source, not merely rendered and OCRed.

Where good semantics exist, preserve:

- DOM hierarchy;
- paragraphs;
- headings;
- MathML;
- TeX;
- semantic tables;
- image links;
- captions;
- metadata;
- references.

Investigate whether a dedicated DOM/HTML pathway is preferable to routing all HTML through Marker/MinerU/Docling.

It probably will be.

The resulting canonical representation must nevertheless match the PDF workflow.

---

# 34. Saved publisher pages

I expect to download journal pages manually.

I am willing to use whichever browser or browser-saving mechanism produces the best archival input.

Do not assume Safari `.webarchive` must be the preferred format.

Investigate practical support for:

- self-contained HTML;
- SingleFile-style saved HTML;
- HTML plus asset directory;
- MHTML;
- Safari WebArchive;
- WARC if justified.

Criteria should include:

- preservation of semantic HTML;
- preservation of MathML;
- preservation of tables;
- preservation of high-resolution image links/assets;
- offline reproducibility;
- ease of parsing;
- portability;
- browser availability;
- reliability across publisher sites.

Prefer a simple robust workflow over supporting every archival format.

If a self-contained HTML capture is superior to WebArchive, recommend that workflow.

---

# 35. Manual acquisition is expected

Assume that nearly all paywalled publisher PDF and HTML acquisition will be performed manually by me in a normal authenticated browser.

Therefore:

- automated publisher scraping is low priority;
- authenticated browser automation is low priority;
- session/cookie handling is low priority;
- bypassing access controls is out of scope.

The tool should make this workflow convenient:

```text
download PDF manually
save article HTML manually
place both in an input location
run extraction
```

This is a feature, not a limitation.

---

# 36. arXiv support

Accept:

```text
https://arxiv.org/abs/...
https://arxiv.org/pdf/...
arXiv:xxxx.xxxxx
```

At minimum:

- obtain PDF;
- obtain arXiv metadata;
- preserve arXiv identifier/version;
- store the original.

If source TeX is available, optionally exploit it.

TeX may provide substantially better:

- equations;
- tables;
- captions;
- references.

However, do not make TeX source central to the architecture.

In my field, even papers on arXiv frequently need to be treated effectively as PDF sources.

---

# 37. Duplicate and version detection

Compute cryptographic hashes such as SHA-256 for source artifacts.

Detect exact duplicates.

Also identify likely bibliographic duplicates using:

- DOI;
- arXiv ID;
- title;
- authors;
- publication metadata.

Handle:

- identical PDF downloaded twice;
- publisher and arXiv versions;
- accepted manuscript and version of record;
- supplementary documents.

Do not automatically merge genuinely different document versions.

Record relationships.

---

# 38. Preservation of originals

Never modify the user's source destructively.

Retain exact copies or use a clearly documented content-addressed store.

For a multi-source bundle, preserve all supplied inputs.

For example:

```text
original/
    publisher.pdf
    publisher.singlefile.html
```

Store hashes for each.

Normalized extraction is derived data.

The originals remain authoritative.

---

# 39. Reproducibility

Record enough information to understand and reproduce an extraction later.

Include:

- input names;
- source hashes;
- source types;
- acquisition URLs if known;
- extraction timestamp;
- application version/commit;
- backend;
- backend version;
- backend configuration;
- ML model versions;
- OCR mode;
- VLM model if used;
- hardware;
- operating system;
- network services used;
- warnings/errors.

Exact deterministic reproduction is not required where ML inference is nondeterministic.

Configuration reproducibility is.

---

# 40. Idempotency and reprocessing

Repeated execution should not create uncontrolled duplicates.

Support concepts such as:

```text
already processed
reuse result
force re-extract
try another backend
upgrade extraction
re-run only tables
re-run only figure descriptions
re-run metadata validation
```

Expensive stages should be separable and cacheable.

---

# 41. Validation report

Every paper should receive a structured quality report covering matters such as:

- bibliographic identity established?;
- DOI validated?;
- BibTeX validated?;
- pages processed?;
- number of figures;
- number of tables;
- number of equations;
- extraction warnings;
- low-confidence tables;
- malformed equations;
- OCR required?;
- suspicious reading order?;
- unmatched captions?;
- disagreement between PDF and HTML?;
- missing assets?;
- figure descriptions completed?

Distinguish fatal errors from warnings.

Partial useful output should survive non-fatal failures.

---

# 42. No silent hallucination

This is a core system principle.

Machine-generated assistance may be used for:

- figure descriptions;
- difficult layout interpretation;
- metadata reconciliation;
- diagnostics.

However:

- never invent missing article text;
- never silently repair an equation according to physical intuition;
- never change a table value because another value looks more plausible;
- never fabricate metadata;
- never turn an approximate visual estimate into precise data.

Generated content must be identifiable as generated content.

---

# 43. Local-first operation

The core extraction path should work locally.

It should not require sending papers to an external AI API.

Remote LLM/VLM services may be optional.

It should be possible to run:

```text
offline extraction
```

once relevant models are cached.

Network access will normally still be useful for:

- DOI validation;
- Crossref;
- DataCite;
- OpenAlex;
- arXiv metadata.

Also consider an explicitly offline mode.

---

# 44. Primary local development machine

My main local machine is:

```text
MacBook Pro
Apple M5 Max
128 GB unified memory
```

This is not merely a minimal compatibility target.

It is a powerful machine and should be treated as a useful platform for:

- development;
- testing;
- one-off extraction;
- small batches;
- benchmarking;
- potentially local VLM inference where Metal/MPS support is good.

Take advantage of:

- Apple Silicon;
- large unified memory;
- Metal/MPS where supported;
- high memory bandwidth.

Do not unnecessarily assume CUDA is required for routine use.

---

# 45. Free HPC resources

I also have access to HPC resources through Slurm.

Critically:

```text
up to two NVIDIA A100 GPUs can be used simultaneously
```

and this resource is effectively **free to me**.

This should fundamentally affect hardware optimization decisions.

The preferred large-batch execution hierarchy is therefore:

1. MacBook Pro for development and one-off work;
2. free A100 HPC resources for substantial GPU batch processing;
3. paid GPU rentals only when demonstrably worthwhile.

The application should have a clean Slurm execution path.

---

# 46. Slurm/HPC support

Do not build a full distributed cluster framework unless necessary.

However, make batch processing easy under Slurm.

Consider workflows such as:

```text
one paper per task
job arrays
one worker per GPU
multiple concurrent extraction processes
```

I can use two A100s concurrently.

Determine whether it is more effective to:

- assign different papers to each A100;
- run job arrays;
- use multiple workers per A100;
- batch pages;
- use both GPUs within one inference job.

For this workload, independent-paper parallelism may be preferable to complex multi-GPU model parallelism.

Benchmark rather than assume.

---

# 47. Paid H100/H200 rentals

I can rent GPUs such as:

- H100;
- H200;
- potentially other high-end accelerators

through services such as RunPod.

However, these cost approximately:

```text
~$4/hour
```

and should be regarded as an **exceptional resource**, not the default execution target.

Do not recommend H100/H200 merely because they are faster.

They should only be used when a benchmark demonstrates a compelling advantage over the free A100 resources.

Potential justifications might include:

- a model does not fit efficiently on A100;
- a substantially larger VLM materially improves scientific figure extraction;
- very large one-time corpus ingestion;
- H100/H200 throughput reduces overall processing time sufficiently to justify rental cost.

Cost/performance matters more than headline throughput.

---

# 48. Hardware benchmarking objective

Measure:

```text
papers processed / wall-clock hour
papers processed / GPU-hour
```

and, where relevant for paid resources:

```text
papers processed / dollar
```

Stage timings should include:

- PDF parsing;
- OCR;
- layout detection;
- formula recognition;
- table recognition;
- figure extraction;
- VLM figure description;
- metadata lookup;
- validation;
- serialization.

Also record where practical:

- CPU utilization;
- GPU utilization;
- peak RAM;
- peak VRAM.

This should show whether a stage is:

- CPU bound;
- GPU compute bound;
- VRAM bound;
- I/O bound;
- network bound.

---

# 49. Do not assume larger GPUs automatically help

During planning investigate for Marker, MinerU and Docling:

- which operations execute on GPU;
- batching strategy;
- page-level concurrency;
- document-level concurrency;
- VRAM requirements;
- scaling with compute;
- CPU preprocessing bottlenecks;
- image decoding bottlenecks;
- output serialization bottlenecks.

Likewise determine whether local figure-description VLMs scale differently from document extraction.

An H200 may be useful for a large VLM while offering little benefit to basic PDF parsing.

Treat these as separate workloads.

---

# 50. Batch processing

Support a corpus workflow such as:

```text
incoming/
    paper1.pdf
    paper2/
        paper.pdf
        publisher.html
    paper3.pdf
```

producing:

```text
literature/
    AuthorA_2021_...
    AuthorB_2023_...
    AuthorC_2025_...
```

Batch execution should:

- isolate failures;
- resume interrupted work;
- avoid unnecessary reprocessing;
- use suitable parallelism;
- produce corpus-level status;
- function well under Slurm.

Eventually support manifests/lists of inputs.

---

# 51. Benchmark corpus: user-supplied and incremental

I expect to supply the benchmark corpus myself, or at least a substantial portion of it.

Do not make obtaining a large public benchmark corpus a prerequisite for starting implementation.

The sensible initial workflow is probably:

```text
a few representative papers
```

chosen specifically to exercise important cases.

For example, an initial set might contain:

1. a normal two-column experimental optics paper;
2. an equation-heavy paper;
3. a paper with important numerical tables;
4. a paper with multi-panel figures;
5. a paper for which both publisher PDF and high-quality HTML are available.

Get the end-to-end pipeline functioning on these first.

Then grow the benchmark corpus progressively.

---

# 52. Benchmark corpus evolution

Over time I can provide deliberately difficult examples containing:

- equation-heavy theory;
- inline mathematics;
- aligned equations;
- matrices;
- simple tables;
- hierarchical tables;
- merged cells;
- uncertainties;
- scientific notation;
- multi-panel figures;
- raster plots;
- vector plots;
- poor PDFs;
- clean HTML;
- poor HTML;
- PDF+HTML pairs;
- arXiv versions;
- supplementary material.

Create a mechanism for marking important manually verified "gold" elements.

Examples:

```text
expected DOI
expected equation
expected Table 2 row 4 values
expected number of figures
expected caption
```

Do not judge correctness merely by whether Markdown looks plausible.

---

# 53. Regression testing

As the software or upstream extraction backend changes, the supplied scientific corpus should function as a regression suite.

Important regressions include:

- equation corruption;
- column-order errors;
- changed table values;
- lost units;
- figure/caption mismatch;
- metadata identity errors.

Pinning exact output text may be too brittle.

Design more meaningful scientific-content tests.

---

# 54. Corpus suitability for RAG

The primary consumer will frequently be a retrieval system.

Optimize `paper.md` accordingly.

Important properties:

- meaningful heading hierarchy;
- intact paragraph context;
- equations near explanatory prose;
- figure caption and description together;
- tables and captions together;
- metadata near beginning;
- references preserved;
- no repetitive page furniture;
- no navigation clutter;
- no base64 images embedded in Markdown;
- stable asset paths.

Optionally generate:

```text
chunks.jsonl
```

or similar RAG-specific derivative output.

Do not couple the canonical representation to one embedding model or vector database.

---

# 55. References inside papers

Preserve:

- bibliography section;
- in-text citation markers;
- DOI/reference metadata where naturally available.

Structured citation extraction is useful.

However, validating every reference cited by every paper is not necessary for the initial system.

The priority is verifying the identity and BibTeX of the paper being ingested.

Leave room for richer citation-graph extraction later.

---

# 56. Scientific notation and Unicode

Preserve notation such as:

```text
µm
µJ
fs
nm
cm⁻¹
10¹⁵ W cm⁻²
β₂
χ⁽³⁾
ω₀
Δk
±
≤
≥
→
```

Use LaTeX appropriately inside mathematics.

Avoid unnecessary ASCII degradation.

Correct PDF ligature artifacts such as:

```text
ﬁ
ﬂ
```

where appropriate.

Preserve accented author names.

Canonical text files should use UTF-8.

---

# 57. Intermediate structured representation

Do not discard information merely because Markdown cannot express it.

Retain a machine-readable representation containing:

- document hierarchy;
- block types;
- page coordinates;
- source representation;
- tables;
- equations;
- figures;
- caption relationships;
- provenance;
- validation state.

JSON is a likely representation.

The user's primary interface should remain stable even if internal schemas evolve.

---

# 58. Quality profiles

Consider simple user-facing profiles such as:

```text
fast
balanced
high-quality
```

rather than exposing dozens of backend parameters.

Possible meaning:

### Fast

- use embedded text aggressively;
- minimal expensive vision;
- suitable for preliminary indexing.

### Balanced

- default scientific extraction;
- good equations/tables/layout;
- reasonable runtime.

### High-quality

- expensive layout recovery;
- enhanced equations;
- enhanced tables;
- multi-source cross-checking;
- optional figure VLM;
- intended for durable corpus ingestion.

Map these sensibly onto each backend.

---

# 59. Backend/version evolution

Marker, MinerU and Docling evolve quickly.

Requirements:

- explicit version management;
- exact backend version recorded;
- isolated adapters;
- regression tests;
- ability to reprocess selected papers after upgrades.

Avoid relying heavily on unstable internal APIs if stable public APIs exist.

---

# 60. Licensing

Investigate:

- application licenses;
- model licenses;
- VLM licenses;
- constraints on local academic use;
- constraints on distributing the wrapper.

Flag material restrictions.

Do not accidentally make the overall application impossible to distribute because of an avoidable dependency choice.

---

# 61. Environment strategy

I need straightforward deployment in two main environments.

## macOS

Target:

```text
Apple M5 Max / 128 GB
```

Prefer a normal reproducible Python environment or similarly straightforward developer setup.

## HPC

Target:

```text
Linux
Slurm
NVIDIA A100
up to two GPUs concurrently
```

A reproducible container or environment is likely useful.

Possible technologies include:

- uv/pip/conda;
- lockfiles;
- Docker for non-HPC GPU environments;
- Apptainer/Singularity where appropriate for HPC.

Investigate what best fits the backend dependencies.

Do not assume Docker is available inside Slurm.

## Optional RunPod

A containerized environment may be useful for exceptional H100/H200 runs.

This is secondary.

---

# 62. Model caches and HPC operation

Model downloads can be expensive and inconvenient on compute nodes.

Design for:

- persistent model cache;
- configurable cache location;
- prefetching models before Slurm jobs;
- read-only shared caches where useful;
- avoiding repeated downloads per job.

The same consideration applies to RunPod persistent volumes.

---

# 63. Logging

Normal successful output should be concise.

For example:

```text
Extracted: Smith et al. (2024)
DOI: validated
Sources: PDF + publisher HTML
Pages: 12
Figures: 7
Tables: 3
Equations: 26
Backend: Marker ...
Cross-source warnings: 1
Output: literature/Smith_2024_...
```

Detailed diagnostics should be available in verbose/debug modes.

---

# 64. Failure recovery

Handle gracefully:

- corrupted PDFs;
- encrypted PDFs;
- malformed HTML;
- missing HTML assets;
- unavailable metadata services;
- inaccessible DOI;
- table recognition failure;
- equation recognition failure;
- missing figure assets;
- VLM failure;
- GPU out-of-memory;
- Slurm pre-emption/time limit;
- network outage.

Prefer partial valid output to loss of all work.

Persist stage results so jobs can resume.

---

# 65. Content acquisition boundaries

Do not build the system around bypassing publisher access controls.

Assume I legitimately obtain content through normal institutional/browser access.

Separate:

```text
acquisition
```

from:

```text
scientific extraction
```

The second is the core project.

---

# 66. Desired user experience

For a simple paper:

```text
paperextract ~/Downloads/paper.pdf
```

For a well-supplied important paper:

```text
paperextract paper.pdf publisher.html
```

Result:

```text
literature/
    Smith_2026_InterestingPaper/
        paper.md
        citation.bib
        metadata.json
        extraction.json
        validation.json
        original/
        figures/
        tables/
```

I should not routinely have to:

- rename assets;
- look up DOI manually;
- download BibTeX manually;
- clean page headers;
- reconstruct equations;
- convert tables to CSV;
- associate captions;
- create metadata files.

Warnings requiring expert review are acceptable.

Routine bookkeeping should be automatic.

---

# 67. Design priorities

In descending order:

1. scientific fidelity;
2. numerical-table fidelity;
3. equation fidelity;
4. provenance and auditability;
5. correct bibliographic identity;
6. effective PDF+HTML cross-validation;
7. high-quality figure preservation;
8. useful figure descriptions;
9. reliable two-column extraction;
10. canonical portable output;
11. backend interchangeability;
12. reproducibility;
13. ease of use;
14. efficient use of my free hardware;
15. throughput.

Do not sacrifice fidelity merely to improve headline pages/second.

---

# 68. Explicit non-goals for initial versions

This does not initially need to become:

- a GUI document manager;
- Zotero replacement;
- vector database;
- chatbot;
- embedding service;
- citation-network platform;
- publisher scraper;
- browser automation framework;
- general distributed-computing platform.

Its job is to produce excellent canonical scientific-paper representations.

---

# 69. Research required before implementation

Investigate current Marker, MinerU and Docling versions.

For each determine:

1. supported input formats;
2. PDF extraction quality;
3. reading-order reconstruction;
4. inline equation support;
5. display equation support;
6. LaTeX output quality;
7. table extraction;
8. merged-cell handling;
9. HTML/CSV/JSON table output;
10. figure extraction;
11. vector image preservation;
12. caption association;
13. figure-description capability;
14. bounding-box/page provenance;
15. internal structured representations;
16. HTML support;
17. MHTML support;
18. Apple Silicon support;
19. Metal/MPS support;
20. CUDA support;
21. batch/page concurrency;
22. VRAM requirements;
23. A100 performance;
24. H100/H200 scaling;
25. CPU bottlenecks;
26. programmatic API stability;
27. licenses.

Do not rely on old blog posts where current upstream documentation/code gives a better answer.

---

# 70. Other components to investigate

Investigate whether the system benefits from:

- GROBID;
- Crossref;
- DataCite;
- OpenAlex;
- DOI content negotiation;
- citation-verification tools;
- native HTML/MathML parsing;
- local scientific VLMs;
- browser archival tools such as SingleFile;
- MHTML parsing;
- source TeX parsing.

For every proposed dependency explain:

```text
what failure mode does this component solve?
```

Do not build a collection of dependencies without a clear purpose.

---

# 71. Browser capture recommendation

As part of planning, explicitly recommend how I should save journal HTML pages.

Compare at least:

- browser "Web Page, Complete";
- self-contained HTML via a tool such as SingleFile;
- MHTML;
- Safari WebArchive.

The answer should be based on what best preserves:

- text;
- DOM structure;
- MathML;
- tables;
- image assets;
- provenance.

I am happy to use Firefox, Chrome, Safari or another reasonable browser if one produces substantially better archival input.

This recommendation should be pragmatic.

---

# 72. Initial implementation strategy should use a small real corpus

Do not begin by trying to solve every conceivable paper format.

I will provide a few representative papers.

Use those to get:

```text
input
→ extraction
→ metadata validation
→ figures
→ tables
→ equations
→ canonical paper directory
```

working end to end.

Only then expand coverage.

The planner should propose a staged implementation that delivers useful end-to-end results early.

---

# 73. Deliverable from the planning agent

After researching the current ecosystem, produce a detailed design and implementation plan.

Include:

- high-level architecture;
- paper/source-bundle model;
- normalized internal document model;
- canonical directory schema;
- metadata schema;
- backend adapter design;
- PDF workflow;
- HTML workflow;
- PDF+HTML reconciliation workflow;
- equation workflow;
- figure workflow;
- table workflow;
- metadata/BibTeX validation;
- provenance model;
- validation framework;
- caching/idempotency;
- local Mac execution model;
- Slurm/A100 execution model;
- dual-A100 concurrency strategy;
- optional paid H100/H200 criteria;
- environment/dependency strategy;
- browser-save recommendation;
- CLI design;
- batch design;
- benchmark strategy using my supplied papers;
- regression testing;
- error handling;
- staged milestones;
- risks;
- unresolved technical questions.

Where several approaches are reasonable, compare them and explain the trade-offs.

Do not begin implementation until the plan has been reviewed.

The eventual goal is for a directory of processed papers to become a durable, high-quality, auditable **machine-readable scientific library**, rather than merely a collection of OCR-converted PDFs.