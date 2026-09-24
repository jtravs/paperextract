# paperextract: detailed design and implementation plan

**Status:** approved by John on 22 September 2026; P1 corpus qualification started. Extraction is not implemented.\
**Research date:** 22 September 2026.\
**Owner:** John C. Travers <jtravs@gmail.com>.\
**Application license:** Apache-2.0.

This plan follows all 73 sections of `dev/Brief.md`. The brief remains unchanged.
Current progress and handoff instructions are in `dev/Status.md`; initial
corpus findings are in `dev/Corpus.md`. Commands, schemas, modules,
thresholds, and milestones below remain proposed until the status tracker records
their implementation and verification.

## 1. Confirmed requirements and decisions

John clarified the following during planning; these supersede conflicting details
in the brief without changing that document:

- **The Mac is a first-class execution target:** MacBook Pro, M5 Max, 128 GB unified
  memory. It should handle the usual one-to-few-paper workflow locally. HPC is
  principally for larger batches. Mac support is a release criterion, not an
  optional compatibility check.
- **Actual HPC hardware is two NVIDIA A40 cards, each 48 GB**, on a node with two
  32-core AMD EPYC 7543 processors and 512 GB RAM. Smaller allocations are possible.
  The A100 references in the brief are superseded for this cluster.
- Compute nodes are bare Linux; code/environments installed on the login node can
  be used there. Public web access is expected but may be filtered. Assume no
  Docker, Apptainer, compiler, Tesseract, or other administrative installation.
- Paid A100/H100/H200-class resources are acceptable for batch work, including when
  HPC queues are inconvenient. Choose using quality, delivered throughput, total
  completion time, availability, and cost. Rental is a normal option, not only a
  last resort. No rental is authorized or provisioned by this planning exercise.
- Figure descriptions have high priority. They may follow the core extraction
  slice briefly, but should be available early. Prefer a strong local model;
  compare it against explicitly enabled Claude/OpenAI API descriptions.
- Ordinary runs may send identifiers/title/author information to metadata services.
  Article content goes to remote AI only under an explicit remote-description
  policy. Fully offline operation remains required after setup.

### Recommended starting design

Use a lightweight Python coordinator, a native semantic HTML pathway, and isolated
PDF backend workers. Own a versioned scientific document schema. Store alternative
extractions and evidence rather than choosing a universal PDF-versus-HTML winner.
Generate Markdown, tables, citations, and reports from that schema.

Implement **MinerU as the first P2 adapter**, following the 22 September Mac trials
recorded in `dev/Trials.md`. This supersedes the initial provisional
Docling choice. MinerU preserved the candidate numerical strings and performed
better on the inspected inline math; Marker corrupted the numerical table's
columns and decimal values. This selects implementation order, not a production
default or a general accuracy ranking. Broader qualification and the model-license
audit remain open. All three explicit backends belong in the first supported release.

The Mac-local path now has measured evidence: MinerU completed the ten-page
published HISOL paper twice using local models. The ledger records timings,
memory estimates, fidelity limitations, and background contention. Quiet-machine
throughput and larger batches remain unmeasured. Large unified memory helps
capacity; it does not imply CUDA-equivalent throughput or universal MPS support.
[M-release] [U-tiers] [D-models]

## 2. Research findings and limitations

### 2.1 Version snapshot and licensing

Package metadata was retrieved from PyPI and compared with tagged release source.
The checked public versions are:

| Component | Observed version / tag | Python declared upstream | Integration consequence |
| --- | --- | --- | --- |
| Marker (`marker-pdf`) | 2.0.0 / `v2.0.0`, release commit `947d768` | >=3.10,<4 | Major rewrite; do not copy 1.x GPU/process recipes. |
| MinerU (`mineru`) | 4.0.5 / `mineru-4.0.5-released`, `5115829` | >=3.10,<3.15 | New stateless parser interface and tiers; avoid obsolete 2.x examples. |
| Docling (`docling`) | 2.129.0 / `v2.129.0`, `890dd42` | >=3.10,<4 | Pin `docling-core`, models, and transitive runtime through the worker lock. |

Sources: [Marker release][M-release], [MinerU releases][U-release],
[Docling releases][D-release], [Marker package][M-pypi],
[MinerU package][U-pypi], [Docling package][D-pypi]. These are investigation pins,
not installed or qualified backend environments. Recheck before implementation.

Marker's current code is Apache-2.0; its model weights have separate modified
OpenRAIL terms. The documented free-use categories include research/personal use
and businesses below a funding/revenue threshold. Record each actual downloaded
model's license and revision; the code license does not license the weights.
[M-readme]

MinerU now declares `LicenseRef-MinerU-Open-Source-License`, based on Apache-2.0
with extra conditions: commercial thresholds of more than 100 million monthly
active users or USD 20 million total monthly revenue, and attribution when
providing online services. Do not label it plain Apache-2.0, or repeat old AGPL
claims about a different release. Its own license must accompany any distribution
that includes it. [U-license] [U-pypi]

Docling code is MIT; model licenses are separate. Keep the wrapper Apache-2.0 and
backends/model weights separately installable. Review resolved dependencies and
model notices before distributing worker bundles. Process isolation is useful
technically; it does not remove license obligations. Supplied papers retain their
own copyright and access conditions. [D-readme]

### 2.2 Capability assessment

**D** means documented capability or inspected source, not demonstrated correctness
on this corpus. **Q** means qualification required. No extraction quality or
A40/M5/A100/H100/H200 throughput has been measured in this planning stage.

| Requirement from brief §69 | Marker 2.0 | MinerU 4.0.5 | Docling 2.129 |
| --- | --- | --- | --- |
| 1. Inputs | D: PDF/images; full extra adds office/HTML/EPUB | D: PDF/images, native office/HTML and others | D: PDF/images/HTML, office, JATS and others |
| 2. Scientific PDF fidelity | Q: test optics corpus | Q: test optics corpus | Q: test optics corpus |
| 3. Reading order | D: layout/block hierarchy; Q columns | D: ordered blocks; Q columns | D: body tree order; Q columns |
| 4. Inline math | Advertised; Q symbol/span fidelity | Formula path; Q inline versus prose | Q: do not infer inline coverage from formula enrichment |
| 5. Display math | D: equation recognition | D: formula recognition | D: optional formula enrichment |
| 6. LaTeX quality | Q, especially numbering/multiline | Q, especially inline/numbering | Q; enrichment configuration matters |
| 7. Tables | D: table converter, text-layer/VLM paths | D: native/model table paths | D: TableFormer and cell structures |
| 8. Merged cells | HTML can carry spans; Q recognition | Rich output can carry spans; Q recognition | D: cell spans; Q recognition |
| 9. Table exports | HTML in JSON; CSV derived by us | JSON/HTML path; CSV derived by us | D: HTML and DataFrame/CSV examples |
| 10. Figures | D: extracted images | D: materialized assets | D: picture image export |
| 11. Native vectors | No archival vector guarantee verified | No archival vector guarantee verified | Raster export is not vector preservation |
| 12. Captions | Block association; Q correctness | Structured caption association; Q | Picture/table relations; Q |
| 13. Descriptions | Treat as independent optional stage | Treat as independent optional stage | D: optional picture description models |
| 14. Coordinates | D: page tree, polygons/bboxes | D: page/block geometry; verify schema units | D: item provenance/bboxes |
| 15. Internal model | Document/block tree and JSON renderer | ParseResult + structured JSON | DoclingDocument with refs/tree/cells |
| 16. HTML | D: supported with full extra | D: native Flash path | D: structured HTML input |
| 17. MHTML | No direct support verified; unwrap ourselves | No direct support verified; unwrap ourselves | No direct support verified; unwrap ourselves |
| 18. Apple Silicon | D; native installation | D; native installation | D; native installation |
| 19. Metal/MPS | Device-dependent fast mode; llama.cpp VLM | Torch/MPS small models; llama.cpp default VLM | Layout MPS; TableFormer MPS disabled |
| 20. CUDA | D: local inference server | D: Torch + suitable VLM engine | D: stage/model dependent |
| 21. Concurrency | Thin CPU workers share inference server | Reusable parser/VLM runtime; bound callers | D: threaded/batched pipeline |
| 22. Memory needs | Q for selected mode/server/context | Upstream ~8 GB VRAM starting point, not bound | Q per OCR/formula/VLM configuration |
| 23. A100 performance | Unmeasured here | Unmeasured here | Unmeasured here |
| 24. H100/H200 scaling | Unmeasured; cannot extrapolate B200 claims | Unmeasured; engine/CPU may dominate | Unmeasured; stage mix matters |
| 25. CPU bottlenecks | Text parsing/layout/image handling | ONNX stages/rendering/asset export | PDF parsing/rendering/TableFormer on Mac |
| 26. API stability | Pin converter/renderer seam; major rewrite | Pin new public parser, not legacy internals | Public converter; pin schema contract |
| 27. License | Apache code, separate restricted models | Custom Apache-based terms + model audit | MIT code + model audit |

Evidence is distributed across [Marker tagged README][M-readme],
[Marker JSON source][M-json], [MinerU tiers][U-tiers],
[MinerU parser API][U-sdk], [MinerU output contract][U-output],
[Docling document model][D-document], [Docling table export][D-tables],
[Docling model catalog][D-models], and [Docling enrichment example][D-formula].
The table's unknowns are deliberate; marketing claims do not establish fidelity.

### 2.3 Concrete adapter seams

**Marker:** inspected `PdfConverter`, `create_model_dict`, and `JSONRenderer` in the
2.0.0 source. JSON contains block IDs/types, HTML, geometry, child relationships,
and image information. Preserve this before normalization. Request JSON directly;
never reconstruct the scientific model by reparsing its Markdown. [M-pdf] [M-json]

Marker 2 uses a local Surya inference server. Its documentation describes automatic
Docker-based vLLM startup on NVIDIA and llama.cpp elsewhere, plus an external
`SURYA_INFERENCE_URL`. Bare-Linux HPC must use a deliberately launched, qualified
server or other supported local engine; an automatic Docker requirement is not
acceptable. Device-dependent defaults must be resolved and recorded explicitly.
[M-readme]

**MinerU:** use `mineru.parser.MinerUParser` / `parse` and `ParseResult`, or stateless
`mineru-kit parse`, rather than integrating its document library. The parser covers
all PDF pages by default; the document-reader command has a first-ten-pages default
that would be dangerous here. `ParseResult.to_json()` supplies a version-specific
boundary. Reuse a worker/parser across documents; async syntax alone does not
promise parallel model inference. [U-sdk] [U-readme]

**Docling:** use `DocumentConverter` with explicit `PdfFormatOption` and pipeline
options, then normalize `ConversionResult.document`. Preserve `DoclingDocument`
JSON, table cells and spans, picture/caption references, and provenance. Its
DataFrame table export is useful for simple output but must not become the
canonical rich table model. [D-document] [D-tables]

Disable remote enrichment in every worker unless a separately authorized policy
allows it. Resolve automatic OCR/device/model choices into the run manifest.
Docling distinguishes remote-service permission from model downloading, so
`enable_remote_services=False` alone is not a complete offline guarantee. [D-options]

### 2.4 Supporting components: adopt only to solve a failure

| Candidate | Failure it addresses | Decision |
| --- | --- | --- |
| Pydantic v2 | Invalid/ambiguous persisted schemas and worker messages | Proposed boundary models, frozen where suitable; JSON Schema export. |
| lxml | Losing DOM, MathML, and table semantics through generic Markdown conversion | Proposed native HTML/XML parser with network/entities disabled. |
| httpx | Hanging requests, inconsistent timeouts/retries/service caches | Proposed one network boundary for acquisition/metadata. |
| PyYAML | Unsafe or malformed front matter | Safe dump/load only; canonical rich data remains JSON. |
| Pybtex | Broken names, braces, escaping, and unparsable BibTeX | Proposed parse/serialize/round-trip tool, not an identity authority. [Bib] |
| pypdfium2 | Independent PDF inspection/rendering/crops without requiring system tools | First rendering candidate; verify wheels and image-object export on both platforms. [PDFium] |
| Pillow | Image dimensions/previews and decompression limits | Add with figure handling, not as an OCR substitute. |
| GROBID | Weak PDF header/reference parsing | Optional isolated local service after baseline; adds Java/native runtime. [Grobid] |
| Crossref / DataCite | DOI identity independent of OCR | Core metadata providers, queried by identifier first. [Crossref] [DataCite] |
| DOI content negotiation | Secondary citation representation | Optional comparison; often same underlying registry, not independent evidence. [DOI] |
| OpenAlex | Ambiguous candidates and related works | Optional corroboration; handle evolving API budgets/key policy. [OpenAlex] |
| verify_citations | Flags questionable citation strings across services | Evaluation-only optional checker; do not embed its fuzzy/search workflow. [Verify] |
| pylatexenc / a MathML converter | Math token inspection / converting semantic markup | Evaluate on gold equations; no universal lossless converter assumed. |
| TeX parser | Recovering equations/captions when source exists | Later bounded, non-executing parser; no TeX compilation requirement. |
| MLX-VLM / Transformers | Local scientific figure description | Separate optional worker environments; benchmark same model/precision. [MLX] |

Use standard-library hashing, MIME/email parsing, pathlib, subprocess, CSV,
argparse, and SQLite where sufficient. No pandas, vector database, agent framework,
message broker, web UI, or distributed scheduler in the core. These are proposed
runtime additions, not installed bootstrap dependencies.

## 3. Architecture and module boundaries

```text
local files / explicit URL acquisition
                  |
             immutable ingest
                  |
          PaperSourceBundle + evidence
           /                      \
  PDF worker adapters        native HTML/archive adapter
           \                      /
          source-specific candidate Documents
                     |
         identity/version gate + object alignment
                     |
         evidence-based canonical selection
                     |
              scientific validation
                     |
      versioned document.json + assets + provenance
                     |
       deterministic Markdown/BibTeX/table exporters
                     |
          portable paper directory + review queue
                     |
          optional figure descriptions / chunks
```

Proposed module ownership:

| Package | Responsibility and boundary |
| --- | --- |
| `models` | Versioned value/schema definitions; no filesystem or backend imports. |
| `ingest` | Hash/copy sources, sniff formats, unpack captures, validate bundles. |
| `acquire` | Explicit arXiv/public URL requests; separate from extraction. |
| `backends` | Capability protocol, worker lifecycle, adapter normalization. |
| `html` | DOM extraction, semantic math/tables, asset resolution, publisher rules. |
| `identity` | Metadata candidates, registry providers, version relationships, BibTeX. |
| `reconcile` | Matching and transparent per-object selection; no I/O or model calls. |
| `validate` | Typed findings and completeness/fidelity checks. |
| `figures` | Asset evaluation, optional panels and generated description contracts. |
| `export` | Markdown/front matter, HTML/CSV, citations, optional RAG derivatives. |
| `storage` | Transactions, stage cache, run history, corpus index, migrations. |
| `pipeline` | Stage dependency graph, policies, checkpoints, failure boundaries. |
| `cli` | Argument parsing, configuration, concise status and exit codes. |

Implement modules only when a milestone uses them; do not create empty abstractions
for every eventual feature. Ordinary domain functions consume/return immutable
values. Serializing large images through JSON is prohibited: workers return staged
file paths and hashes within their assigned output root.

### Worker protocol

A versioned JSON request file contains source IDs/paths, requested capabilities,
page selection, explicit profile, device, cached model revisions, network policy,
time/resource budgets, and an output staging directory. The worker writes raw
backend output, candidate JSON, assets, a result manifest, and structured diagnostics.
The coordinator validates protocol/schema version, hashes, paths, and completion
before admitting results. Backend Python objects never cross this boundary.

The minimal adapter operations are `probe`, `extract`, and `normalize`, with a
separate setup/prefetch operation. Probe reports installed version, formats,
per-stage capabilities, device support, models present, and unsupported requests.
An explicit unavailable backend fails with remediation, never silently changes.
A future `auto` policy chooses only among qualified installed configurations and
records the policy version and decision. It never silently invokes remote AI.

Persistent workers amortize model load for batches. Begin with one request at a
time per worker; add bounded page/document concurrency only after measurement.
Workers can initially be single-shot subprocesses for simple crash isolation.

## 4. Source, work, version, and document models

### 4.1 Identity layers

Do not equate a file hash, DOI, scholarly work, and edition:

- `SourceArtifact`: exact bytes, SHA-256, size, MIME type, original name, acquisition
  information, role, and optional archive-member relationship.
- `PaperSourceBundle`: one ingestion request, ordered source IDs, user assertions,
  expected relationships, and a stable manifest digest. Multiple PDFs/HTMLs are
  legal; `source_sha256` cannot be a dictionary keyed only by file type.
- `ScholarlyWork`: stable local ID plus identifier assertions and related-work edges.
  Adding a DOI later adds an alias; it does not silently change the local ID.
- `DocumentVersion`: version of record, accepted manuscript, submitted manuscript,
  arXiv identifier/version, supplement, correction, or unknown. Preserve the source's
  own date/version, separately from the work's publication date.
- `Document`: extraction of a coherent document version, with a selected source
  spine and supplementary documents as explicit children or related documents.

PDF + HTML may be fused only when the same edition is established. A preprint and
publication can have a `published_as` relation without sharing textual content.
A correction has its own identity and `corrects` edge. Separate supplements remain
separate documents linked by `supplement_to`; embedded appendices remain in order.

If a purported bundle contains conflicting DOIs/versions, preserve all originals
in intake and issue a conflict. Do not publish a combined `paper.md`. Offer an
explicit split or primary-version selection. An uncertain edition is recorded as
unknown; unverified metadata never justifies destructive deduplication.

### 4.2 Canonical schema, proposed version 1.0

Every persisted top-level JSON object has `schema_name`, `schema_version`,
`document_id`, and `run_id` where applicable. Use UTF-8 and explicit null for a
known-but-unavailable field; omission means not collected where the schema allows
it. Reject nonfinite numbers and unknown major versions. Dates retain precision
(year/month/day), not fabricated January 1 dates.

| Type | Required content / invariants |
| --- | --- |
| `Document` | Work/version IDs, ordered sections/blocks, object registry, references, source IDs, language, processing status. |
| `Section` | Stable ID, heading rich text, explicit level, ordered child IDs; avoid inferring semantic depth from font size alone. |
| `Paragraph` / list / footnote | Ordered rich inline runs: text, inline math, emphasis, superscript/subscript, citation/cross-reference. |
| `Equation` | Inline/display kind, raw TeX/MathML candidates, selected LaTeX if available, printed label, source spans, findings. |
| `Figure` | Published label, captions, composite/panel relations, asset candidates, selected original and preview, descriptions. |
| `Table` | Dimensions, cell records, row/column/header relationships, caption/notes, visual assets, validation. |
| `Reference` | Original rich text and label, optional parsed fields/identifiers, linked citation occurrences. |
| `Candidate` | Candidate ID, content or asset reference, source spans, producer run, transformations, native confidence. |
| `Selection` | Selected candidate IDs, rule/version, reason, alternatives, conflict/comparison state; no unexplained overwriting. |
| `MetadataField` | Selected value, raw observations, evidence IDs, status, alternatives, adjudication if present. |
| `SourceSpan` | Source ID, native locator, normalized locator, transformations and granularity. |
| `Finding` | Code, severity, scope IDs, evidence IDs, message, suggested action and check version. |

Use a discriminated union for block types. Caption, footnote, and cross-reference
relationships are explicit IDs; never rely solely on adjacency. Retain unclassified
blocks instead of dropping them. For repeated headers/footers, store removal
reason and source text in diagnostics so deletion can be audited.

Stable IDs derive from immutable source anchors and object kind within an initial
extraction. Reprocessing reuses IDs only when alignment establishes continuity;
otherwise create IDs and a `supersedes` mapping. Backend-local IDs stay in provenance.
Printed “Figure 2” and “(3a)” are labels, not globally unique identifiers.

### 4.3 Source coordinates

Normalize PDF locations to **one-based page numbers**, unrotated page coordinates
in points, top-left origin, with `[x0, y0, x1, y1]`. Also preserve the backend's raw
coordinates, units, page crop/media boxes, rotation, and conversion matrix. Crop
pixel transforms include DPI. Test rotated and cropped pages explicitly.

For HTML retain original artifact/member ID, DOM ID if present, structural path,
and text offsets into a preserved decoded DOM snapshot. Offsets identify that
snapshot/version, not a live website. Archive member URL and MIME Content-ID are
additional locators. Inferred PDF/HTML alignment is labeled inferred, not claimed
as measured geometry. Missing coordinates are null with a reason.

## 5. Canonical directory and portability

```text
literature/
  corpus.json                         # format/policy versions, no private credentials
  .paperextract/                      # rebuildable index, intake, transactions
  Travers_2019_ShortTitle/
    paper.md                          # one primary scientific text artifact
    citation.bib
    metadata.json                     # canonical fields + metadata evidence
    document.json                     # canonical scientific structure
    extraction.json                   # source/run/selection provenance
    validation.json                   # findings + completion summary
    manifest.json                     # generation ID, paths, sizes, hashes
    original/
      source_01/paper.pdf
      source_02/publisher.html
      source_02/publisher_files/...
    figures/
      fig_<id>.svg                     # retained only when actually vector
      fig_<id>.png                     # preview/fallback
    tables/
      table_<id>.json                  # exact cell model
      table_<id>.html                  # rich accessible structure
      table_<id>.csv                   # only when lossless flattening is defined
      table_<id>.png                   # source crop when available
    equations/                        # MathML/raw markup/crops when needed
    descriptions/                     # generated claims and model provenance
    diagnostics/
      review.md
      raw/<run_id>/...
      runs/<run_id>.json
    overrides.json                    # explicit human adjudications, if any
```

All references are relative to this paper directory; no symlinks to shared caches
are required for portability. Preserve original directory structure inside each
source namespace so duplicate filenames cannot collide. Copy bytes and verify
hashes; hardlinks to user files are not the preservation default. Detect a source
changing during copying and abort that ingest. Model weights stay outside exports.

Naming: Unicode NFC, filesystem-safe first author, validated year, first few
meaningful title words, bounded to a proposed 96 UTF-8 bytes before suffix; retain
full Unicode names in metadata. Strip separators/control characters/reserved names,
normalize whitespace, and compare names case-insensitively on every platform.
Unknown identity goes to `Unverified_<sourcehash12>`, never invented metadata.

A short deterministic hash of the work/version key disambiguates colliding names.
For a new batch, calculate collision groups before publication and suffix all
members. Existing published names stay stable; a new collision receives a suffix
and the index retains the reservation. Thus names are deterministic **within the
corpus's persisted naming registry**, not globally independent of ingestion history.
This tradeoff avoids surprising renames; an explicit future `organize` operation
can calculate globally canonical names. Full hashes always detect rare suffix
collisions; lengthen rather than overwrite. Citation keys follow the same policy.

### Library scale, layout and lookup index

John intends the extracted library to serve AI agents as long-term knowledge,
most likely as several smaller subject-specific libraries, some of which may grow
to hundreds or thousands of papers (added 22 September 2026). Design for growth
from the start without complicating the first paper:

- **The paper directory is the portable unit; its location within a library is not
  part of its identity.** Nothing inside a paper directory refers to sibling
  directories or the library root. Moving a paper to another library or layout is a
  directory move plus an index rebuild.
- **Layout is a recorded library-level policy.** `corpus.json` records `layout:
  flat` (default; the brief's adjacent directories) or an optional deterministic
  sharding policy such as `by-year` (`2019/Travers_2019_ShortTitle/`) or
  `by-initial` (`T/Travers_2019_ShortTitle/`). The policy is chosen when the
  library is created and changed only by an explicit `organize` operation that moves
  directories under the transaction journal and rebuilds the index. Sharding serves
  human browsing and tooling ergonomics; modern filesystems tolerate tens of
  thousands of entries per directory, so `flat` remains technically viable and is
  the right default for small subject libraries. Shard keys come from validated
  metadata only; unverified identity shards to `Unverified/`.
- **The index, not the directory tree, is the lookup mechanism.** Every publication
  writes one row to a library-root `catalog.jsonl`, derived mechanically from the
  paper's `metadata.json` and `manifest.json`: relative directory, work/version IDs,
  normalized DOI, arXiv ID, other identifiers, title, normalized title key, ordered
  authors, first-author family name, year, venue, abstract, keywords, citation key,
  document version, source SHA-256 list and content fingerprints, page/object
  counts, processing and bibliographic status, generation and extraction run ID.
  `index rebuild` reconstructs it from the paper directories, so a stale or
  corrupted catalog is never fatal and the filesystem stays the truth (§15).
- **A derived SQLite index adds fast lookup and full-text search.**
  `.paperextract/index.sqlite` holds unique lookup tables keyed by DOI, arXiv ID,
  source hash, content fingerprint and normalized title key, plus an FTS5 table over
  title, abstract, keywords and authors for agent queries. It is rebuildable,
  single-writer, never authoritative, and involves no embeddings or vector store;
  retrieval embeddings remain the consumer's concern (brief §54, §68). [FTS5]
- **Reference lookup answers “is this paper already in my library?”** Proposed
  `paperextract lookup` accepts a DOI, arXiv ID, file (hash and fingerprint),
  BibTeX or CSL-JSON entry, or title/author/year, across one or more library roots,
  and reports `present` (version and directory), `related` (another version of the
  same work), `candidate` (fuzzy match requiring review) or `absent`, as JSON plus a
  one-line summary. Derived `library.bib`, `catalog.csl.json` and a human-readable
  `catalog.md` (author, year, title, DOI, directory, status) let agents that browse
  rather than query find papers, and let reference managers import the library.
- **Federation is by identifier, not shared storage.** Each library carries a
  `library_id`; lookups over several roots report which library holds a work, so
  subject libraries stay independent while cross-library duplicate checks stay cheap.

Standard solutions exist and are reused as formats rather than reinvented: DOI,
arXiv and OpenAlex identifiers for identity; BibTeX and CSL-JSON, the interchange
format introduced by citeproc-js and adopted widely, for metadata exchange; SQLite
with FTS5 for local search. Zotero keeps its library in `zotero.sqlite` with
attachments in per-item key directories, and Calibre keeps a top-level SQLite
`metadata.db` over an `Author/Title (id)/` folder tree, a long-standing precedent
for a sharded per-item layout with a rebuildable database; Papis stores each
entry's bibliography in a human-readable YAML file. None of these produce the
scientific content this project extracts, so the index is a thin derived layer over
portable directories, not an adoption of a reference manager. [CSL] [Zotero]
[Calibre] [Papis]

The normalized title key (Unicode NFKC, case folding, diacritics and punctuation
removed, whitespace collapsed, leading articles dropped) is a candidate key for
lookup and duplicate detection only; it never authorizes a merge (§4.1). Sequencing:
P2 writes the catalog row, fingerprints and title key so the first library is
indexable from day one; the SQLite/FTS index, `lookup`, `catalog.md`, layout
policies and `organize` belong to P5 corpus behavior. This scope is separable from
single-paper extraction and can proceed independently once the row format exists.

## 6. Metadata and bibliographic identity

### Evidence hierarchy and decision procedure

1. Collect candidates from explicit user identifiers, publisher article metadata
   (citation meta tags, JSON-LD, Dublin Core, visible article header), arXiv metadata,
   PDF embedded metadata/links/text, and optional GROBID headers.
2. Distinguish article-local candidates from DOI strings in references, footers,
   corrections, and supplements. Frequency is not authority.
3. Normalize DOI syntax conservatively, preserving the original string. Resolve
   registration metadata through Crossref/DataCite as appropriate; a DOI resolving
   to *some* record is not proof that it describes this file.
4. Compare registry title, ordered authors, journal/year, article type, identifiers,
   and relationships against article-local evidence. Explicit user input is a
   strong assertion, but disagreement still produces a conflict.
5. When no identifier is reliable, retrieve title/author candidates, compute an
   explainable match score and margin, and require corroboration. Initially use
   fuzzy matches to suggest candidates, not automatically merge or mark validated.
   Implemented 23 September 2026 as exact corroboration, not a score: one Crossref
   hit whose title equals the observed title and whose first author and year are
   printed on the first page becomes `VALIDATED_WITH_WARNINGS`; anything weaker
   stays a recorded candidate.
6. Select metadata field by field: publication dates and identifiers from suitable
   registries, source-specific version/license from the actual source. Preserve
   online/print dates separately and document the citation-year selection rule.

Crossref supports unauthenticated retrieval and a polite contact option; observe
response rate/concurrency headers and back off on 429. Contact details are an
explicit user configuration, not silently borrowed from package authorship.
DataCite public retrieval supplies records outside Crossref's coverage.
[Crossref] [DataCite]

OpenAlex is corroboration, not an independent vote when it reproduces Crossref
metadata. Its current docs allow limited keyless access and larger budgets with
a key; do not hard-code old “always free/unlimited” or “always requires key” claims.
Record source lineage and response timestamp. [OpenAlex]

### Metadata schema

`metadata.json` has `work`, `document_version`, `fields`, `relationships`,
`evidence`, and `bibliography_validation`. Field names include title, ordered
structured authors (given/family/literal name, ORCID, affiliations), journal,
publisher, DOI, arXiv/base/version, volume, issue, pages, article number,
publication dates/year, abstract, keywords, article type, canonical/publisher URLs,
license and access status. License applies to a specific representation when needed.
Missing author parts/ORCIDs/affiliations stay missing; collective authors are legal.

Example of an intentionally incomplete field, with illustrative IDs:

```json
{
  "doi": {
    "value": null,
    "status": "UNVERIFIED",
    "evidence_ids": ["source-header-1"],
    "alternatives": [],
    "reason": "No reliable article DOI found"
  }
}
```

Bibliographic states: `VALIDATED`, `VALIDATED_WITH_WARNINGS`, `UNVERIFIED`,
`CONFLICT`. Validation means the checks specified in the report passed, not that
all metadata is infallible. Optional backend confidence is stored with its producer
and scale; do not average incomparable scores into a fictitious probability.

### BibTeX

Build one entry for the selected document/work version from canonical metadata.
Choose `article`, `inproceedings`, or `misc` according to evidence, retaining arXiv
version/eprint/archive fields for preprints. A publication DOI related to a preprint
does not silently turn its citation into a version-of-record citation.

Preserve full author order and name particles/diacritics; protect meaningful title
capitalization and TeX. Keep page ranges separate from article numbers in metadata
and use documented BibTeX field mapping for each. Validate by parsing the generated
entry, checking required fields, and comparing the parse result to canonical data.
A DOI-negotiated BibTeX response can expose disagreements, but may come from the
same registry. Failed network validation yields `UNVERIFIED`, not fabricated data.
[Bib] [DOI]

`verify_citations` is MIT and presents itself as a first-pass plausibility checker
using several online sources. It can be compared on ambiguous examples later;
its title/search matches must not replace identifier-first identity checking or
become a mandatory network dependency. Bibliography entries *inside* papers are
preserved, but verifying every cited work is out of the initial scope. [Verify]

## 7. Native HTML ingestion and browser capture

### Recommended acquisition workflow

Start with **PDF + SingleFile HTML in Chrome or Firefox**. This is a pragmatic
recommendation to qualify on John's publishers, not a claim that every saved page
retains semantic equations or original figures.

1. Open the full article in the normal authenticated browser. Expand sections,
   tables, and figure panels; scroll through to load deferred content.
2. Save the publisher PDF separately. Save a SingleFile capture with deferred-image
   loading enabled and hidden-content removal disabled for the initial evaluation,
   because hidden markup may contain TeX/MathML. Record extension version/options.
3. Reopen the saved file offline. Check an inline equation, a display equation,
   a complex table, and the largest figure against the online page and PDF.
4. Save original/high-resolution figure assets separately when the capture only
   contains thumbnails. A link to a high-resolution asset is not that asset.
5. Keep acquisition URL/date and any known article version beside the files.
   The application will later put these into the bundle manifest automatically
   when they are recoverable from the capture.

SingleFile archives a page and resources into one HTML document; its FAQ documents
size-reduction and deferred-image options. Do not assume defaults are ideal for
scientific preservation. Treat the extension as a user's acquisition tool, not a
library bundled into paperextract. [SingleFile] [SingleFile-FAQ]

| Format | Scientific content / offline behavior | Support decision |
| --- | --- | --- |
| Web Page, Complete | HTML plus assets is easy to parse; moving only HTML breaks resources; dynamic content and link rewriting need checking | First release. Preserve folder structure and original URLs. [Firefox-save] |
| SingleFile HTML | Convenient portable DOM/resource capture; compression/removal settings and MathJax-rendered markup may affect semantics | Preferred starting workflow, qualified per publisher. [SingleFile-FAQ] |
| MHTML | MIME bundle retains HTML/resources with Content-Location/Content-ID; browser capture can still omit unloaded assets | First release after HTML; stdlib MIME parsing + resource map. [MHTML] |
| Safari WebArchive | Native Safari page archive, but a platform-specific ingestion surface and still only captured resources | Preserve as supplied; defer parsing unless examples justify priority. [Safari-save] |
| WARC | Valuable request/response archival, substantial replay and multi-record selection complexity | Defer; no crawler/archive platform in v1. |
| HTML-only/page source | May keep semantic markup but not images or dynamically populated article content | Accept with missing-resource findings; never claim self-contained. |

No capture format can recover semantics that the publisher supplies only as pixels.
The deciding test is saved MathML/TeX/cells/assets, not whether the page looks similar.

### Parsing and asset resolution

Sniff content rather than trusting extensions. For MHTML, parse bounded MIME parts,
handle base64/quoted-printable, charsets, nested multipart content, `cid:` links,
Content-Location, and base URLs. Reject ambiguous duplicate resource mappings;
retain the original archive and map derived members back to it.

For HTML plus assets, enumerate a manifest of relative paths and hashes. Resolve
`src`, `srcset`, `picture`, inline SVG, data URIs, equation images, and supplied
asset links against the archived base. Distinguish “link known” from “bytes present.”
Do not fetch uncaptured resources during local parsing; an explicit acquisition
stage may retrieve public assets under policy.

Extract article metadata before removing page furniture. Use generic semantic
rules first (`article`, section hierarchy, figure/figcaption, table, MathML,
structured references), then small publisher-specific adapters when supplied
examples demonstrate need. Record rule versions and extraction coverage. A main
article selector that returns only an abstract must trigger an incomplete-content
finding; do not silently call it a complete paper.

Preserve source DOM/raw math before cleaning. Remove navigation/cookies/scripts
from canonical output, not originals. Deduplicate visible/accessible MathJax copies
of the same equation while retaining semantic annotations. Parse with external
entities/network disabled; never execute publisher scripts or arbitrary TeX.
Bound expanded archive/data URI/image sizes and enforce output-root confinement.
Reviewable HTML/SVG previews strip active content; authoritative originals remain
byte-identical and are not executed automatically.

## 8. PDF workflow

1. Copy/hash originals, inspect page count/encryption/rotation, and detect malformed
   or obviously incomplete files. Encrypted input requires a supplied password or
   an actionable error; do not try to bypass protection.
2. Inspect text-layer health by page: glyph coverage, replacement characters,
   plausible text density, and image coverage. These are diagnostic signals,
   not a simplistic “text exists, therefore no OCR needed” decision.
3. Run the selected pinned backend with explicit profile, OCR/model/device policy,
   and all requested pages. Keep raw JSON and assets. Check expected page count
   independently against reported processed pages.
4. Normalize blocks/inline runs, geometry, tables, math, figure/caption links,
   footnotes, references, and uncertain objects. Preserve backend raw strings.
5. Reconstruct reading order from backend hierarchy, layout, and matched HTML
   evidence if available. Retain columns, spanning headings, captions, and footnotes;
   coordinates sorted only by y are insufficient.
6. Join typeset line wraps and repair known ligatures conservatively. Remove
   typesetting hyphens only with defensible evidence; retain real compounds/minus
   signs and record ambiguous changes. Use NFC, not blanket compatibility folding
   that could alter scientific symbols.
7. Extract or crop figures/tables and create source overlays for flagged objects.
8. Validate completeness/content, reconcile any same-edition HTML, then export.

Supplementary PDFs are processed as their own document components; reference and
caption labels are scoped so both main and supplementary “Figure 1” can coexist.
John asked on 22 September 2026, after reviewing the first published papers, that
a supplement supplied with a paper be converted too and that cross-references be
updated to match: the supplement is published inside the same paper directory as
its own Markdown with scoped labels, and main-text references such as
“Supplementary Fig. 3”, “see Supplementary Information” or a “Supplement 1” link
point at the converted supplement's anchors. A reference that cannot be resolved
stays as printed text with a finding. Implemented on 23 September 2026 for
PDF supplements (see `docs/cli.md` and `docs/export.md`).

A table whose text layer yields glyphs without a Unicode mapping (math brackets
and accents in some publisher fonts) loses its formulas in MinerU's default
text-layer mode. On 22 September 2026 a page-level OCR run of the same backend
recovered the COPRA Table 1 formulas as clean LaTeX in 26 s. A targeted
re-extraction of such tables, keeping the text-layer version as an alternative
with a selection reason, is a candidate for the `high-quality` path; OCR may
misread digits that the text layer holds exactly, so John decided on 23 September
2026 to enable it with a numerical check: the OCR body is used only when its
numbers match the text layer exactly.
A failed page preserves successful pages but marks the document `PARTIAL` and
places an explicit missing-page/object marker in the Markdown.

## 9. Reconciliation: complementary evidence without a mixed-version paper

Run identity/version checks first. Choose one source as the **structural spine**
for section/block order, then align alternatives; do not concatenate two documents.
A high-quality DOM can supply reading order while PDF provides locators/assets.
The spine choice is recorded with evidence and can be overridden.

Alignment proceeds from strong anchors to weak ones:

1. Match section headings and printed equation/table/figure/reference labels.
2. Match captions and neighboring prose within those sections.
3. Use monotonic sequence alignment for paragraphs and inline math spans;
   allow insertions/deletions and retain unmatched blocks.
4. Require agreement across multiple signals before linking unlabeled objects.
   Ambiguous ties remain unmatched. “Not comparable” differs from “disagrees.”

Per-object rules are versioned, explainable, and conservative:

| Object | Selection evidence | Conflict behavior |
| --- | --- | --- |
| Prose | Completeness, reading order, glyph quality, heading/context alignment | Preserve spine plus alternative; flag missing/different substantive passages. |
| Equation | Genuine embedded TeX/MathML, parseability, complete label/content, visual support | Keep best transcription and both alternatives; never physics-based repair. |
| Table | Valid cell/span topology, exact lexemes, clear headers/units, cross-source match | Cell/structure diffs, explicit review; never average values. |
| Figure | Same composite content, vector/raster fidelity, resolution, clipping and label readability | Keep alternatives; source format alone never decides. |
| Reference | Printed label/order, structured bibliographic content and citation anchors | Preserve printed entry and structured alternatives. |
| Metadata | Identifier authority plus agreement with article-local identity | Field-level alternatives; incompatible identity blocks fusion. |

Comparison uses harmless display normalization only in a **comparison view**.
Raw/source and chosen strings remain available. Whitespace or equivalent formatting
can be labeled equivalent; different signs, subscripts, units, precision, or author
order cannot be ignored. Correlated models or registry mirrors are not independent
votes. Human overrides refer to evidence hashes so an upgrade cannot silently apply
an old correction to a different object.

## 10. Equations

Order of evidence: embedded TeX tied to a real equation; supported semantic MathML;
PDF recognition/embedded glyphs; visual crop when no reliable text exists. An HTML
`img` with an equation-looking alt string is weaker evidence than verified semantic
markup. Presentation MathML supplies notation structure, not a guarantee of unique
LaTeX or mathematical semantics. [MathML]

Preserve raw MathML and embedded annotations. Prototype a bounded MathML-to-LaTeX
converter on actual examples; unsupported operators/macros remain flagged and
preserved with crops rather than dropped. Preserve inline/display classification,
multi-line alignment, matrices, fractions, Greek symbols, superscripts/subscripts,
printed equation numbers, and in-text references. Scope appendix labels separately.

Markdown uses inline `\(...\)` and display `$$...$$`, with visible printed numbers
and stable anchors outside the equation when renderer portability requires it.
Do not insert a `\tag` that duplicates a number already represented in the TeX.
An unavailable transcription gets a source-linked placeholder/crop and a finding;
never invent LaTeX to make the Markdown look complete.

Checks include delimiter/environment balance, tokenization, suspicious truncation,
prose-to-math boundary anomalies, matrix row shape, numbering gaps/duplicates,
unresolved references, and aligned PDF/HTML token diffs. Numbering gaps are warnings:
papers legitimately skip labels or have unnumbered displays. Whitelisted TeX
rendering may be a visual diagnostic, but no arbitrary TeX execution and no claim
that compiling proves transcription fidelity. Symbolic equivalence is optional
later; it must not excuse differences in the printed notation.

## 11. Figures and early figure descriptions

### Asset preservation

Select among whole scientific figures, not every PDF image object. A vector plot
may consist of many drawing/text objects; an embedded photograph might omit axes
or labels. Preserve the composite, including panel labels and legends. Individual
panel crops are also useful and explicitly requested by John: retain both levels
when segmentation is reliable, with parent/composite, caption, panel-label and
source-page relationships. Keep uncertain backend crops as candidates with an
unresolved-grouping finding; never present them as independent complete figures.
Descriptions may address individual panels while retaining the shared caption
and surrounding figure context. Do not discard useful crops to fix grouping.

Prefer a matched original SVG or high-resolution raster when it actually contains
the full figure. Otherwise try native PDF images and a complete PDF crop. A
low-resolution HTML JPEG must not replace a sharper PDF figure. Record dimensions,
format, effective DPI when meaningful, alpha/color profile, crop box, source hash,
and completeness/quality observations. Byte size alone is not an image-quality
score. Do not upscale a thumbnail and claim recovered detail.

For vector PDF figures, retain the source PDF and crop coordinates; investigate a
clipped PDF/vector export before rasterization. A PNG preview alone is not a native
vector export. If the selected rendering tool cannot safely preserve a vector crop,
record that limitation, keep the original vector-bearing PDF, and provide a high-DPI
preview (initial candidate 300 DPI, increase only when label legibility needs it).
Avoid an AGPL/commercial PDF library in the core merely for convenience; qualify
PDFium first and review alternatives separately. [PDFium]

Markdown places figure, published caption, and generated description together near
the corresponding discussion. A figure with a missing asset remains a visible,
numbered object with its original caption and a missing-asset finding.

### Description stage and model comparison

Deliver this immediately after the first extraction slice, without waiting for
full cross-source reconciliation or cluster scheduling. Modes: `none`, `local`,
`remote`, and `backend-integrated`. Default initially `none` until a local model
passes the description evaluation; the normal scientific profile can then enable
the qualified local configuration explicitly in its versioned profile.

Initial candidates: **Qwen3-VL-8B-Instruct** as a stronger general vision candidate,
and **Granite Vision 3.3 2B** as a smaller document/chart-oriented baseline, both
with Apache-2.0 model cards. These are benchmark candidates, not presumed winners
or claims of newest/best models. Evaluate Mac MLX where the precise conversion is
supported and Linux Transformers/vLLM as appropriate; record conversion revision,
quantization, image token budget, and accuracy loss. [Qwen] [Granite] [MLX]

Approximate weight-only planning arithmetic: 8 billion parameters at 2 bytes is
about 16 GB, excluding vision components, activations, KV cache, runtime and batch
buffers. This makes an 8B-class trial credible on the 128 GB Mac and a 48 GB A40,
not a measured memory guarantee. Start with full precision supported by the model,
then qualify quantization separately. Larger models must demonstrate better
scientific descriptions, not merely consume available memory.

A `FigureDescription` stores model/provider/revision, prompt template/hash, image
and caption hashes, sampling settings, timestamp, wall time, token/cost usage where
available, and structured claims:

- Panel IDs, plot/diagram type, axes, quantities/units, printed tick/legend text.
- Explicitly printed numbers with visible evidence location when supported.
- Approximate axis-read ranges/estimates, labeled approximate.
- Qualitative trends/comparisons with no invented mechanism or precise measurement.
- Unreadable/uncertain portions and explicit abstentions.

Generate readable text mechanically from these claims, label it
“Machine-generated visual description,” and retain raw model output. The original
caption remains separately labeled. Image content and captions are untrusted input;
the model has no tools and must not execute instructions found in a paper.
Schema validation catches malformed responses, not factual hallucinations. Review
fidelity with manually annotated claims, including deliberate abstention tests.

Remote OpenAI/Claude trials use explicit provider/model configuration, per-run
budget and request limits, and only the selected figure/caption/context needed.
No remote fallback on local OOM or poor output. Benchmark remote/local on the same
assets and scoring rubric before choosing a default; select concrete remote model
IDs and verify pricing/API contracts at the implementation milestone, when needed.
No paid API calls are part of this planning stage.

Description cache keys are independent of PDF extraction. New description models
invalidate descriptions and Markdown rendering only; figure selection changes
invalidate the affected figure's description. A failed description leaves the
caption and image intact and reports the incomplete optional stage.

## 12. Numerical tables

### Canonical representation

JSON cells plus deterministic HTML are canonical rich representations. Each cell
has origin row/column, row/column span, rich inline content, **raw string value**,
header role, associated header IDs, footnote IDs, source spans, and candidates.
A span occupies a rectangle once; covered slots point to the origin cell rather
than copying its numerical value. Reject overlapping spans and impossible indices.

Retain lexical precision: `3.20 × 10^-4`, `5.2 ± 0.3`, `−`, inequalities, ranges,
superscripts/subscripts, units, empty cells, and ditto marks. Never parse numeric
cells through float as their canonical storage. Optional parsed quantities use
Decimal/string components, preserve the raw lexeme, and include interpretation
provenance; this is a later derivative, not required to export a correct table.
An empty cell is distinct from absent recognition, not-applicable, and ditto.

Retain captions and scoped footnotes verbatim. Distinguish units in a column header
from units in a cell, and retain hierarchical header paths. Cross-page tables need
explicit continuation evidence; repeated headers are not extra data rows.

### Exports for retrieval

Always emit rich HTML and JSON for a recognized table. Emit CSV only when each data
cell has an unambiguous header path and flattening preserves meaning. Record the
flattening map; otherwise omit CSV with a reason. Do not use a DataFrame round-trip
to define spans or coerce strings. If spreadsheet-safe CSV escaping is offered,
make it a separately labeled derivative so exact canonical lexemes remain intact.

Simple tables appear in Markdown as Markdown tables. Complex ones use HTML plus
mechanically generated row/cell text with full header paths, units, caption context,
and footnote markers. This text supplies retrieval coverage when an importer drops
HTML. A row is described from exact strings, never by an LLM. Repeat a spanned
header context, not a spanned numerical observation as multiple measurements.

Always retain the visual source: a PDF crop when available, otherwise the supplied
HTML and assets plus a render if a qualified local renderer exists. A generated
HTML rendering is labeled generated; it is not independent evidence for the
extracted values. If no original screenshot exists, say so. Do not make a browser
runtime mandatory merely to claim every HTML table has an original PNG.

### Checks

Validate cell occupancy, row/column dimensions, header graph acyclicity, explicit
span coverage, footnote attachment, and caption/object count. Flag isolated
exponents, unexpected blanks, probable row shifts, malformed uncertainty/range
lexemes, and inconsistent unit attachment. Missing units are suspicious only when
the table's context warrants them; dimensionless quantities are valid.

Compare aligned PDF/HTML tables on structure first, then exact cell strings and
header paths. Classify formatting equivalence separately from scientific changes
(e.g. `3.2` versus `3.20` can express different precision). Any disagreement in a
gold numerical cell is a regression until reviewed. Unreadable cells retain their
crop and an unknown marker; no inferred value fills the gap.

## 13. Markdown, front matter, and RAG derivatives

`paper.md` is the primary reading/retrieval document; `document.json` is the richer
canonical structure. Deterministic exporters produce Markdown from the selected
model, never from an independent extraction path. Include title/authors/abstract,
sections, paragraphs/lists, inline and display math, figures/captions/descriptions,
tables/notes, acknowledgements, appendices, embedded supplements, references, and
citation/cross-reference markers. Put source anchors beside objects without
repeating page furniture. Equation explanations stay beside the equations.

Front matter is a compact projection of metadata and provenance, not a competing
metadata store. Proposed shape (illustrative placeholders, not article metadata):

```yaml
---
schema_version: '1.0'
document_id: doc_example
work_id: work_example
title: null
authors: []
doi: null
arxiv: null
document_version: unknown
bibliographic_status: UNVERIFIED
processing_status: PARTIAL
sources:
  - id: source_01
    type: pdf
    sha256: '<full SHA-256>'
    path: original/source_01/paper.pdf
extraction:
  run_id: run_example
  application_version: '<installed version>'
  adapters:
    - name: docling
      version: '2.129.0'
  timestamp: '<UTC timestamp>'
validation_report: validation.json
---
```

Add supported journal/date/volume/pages/article number/URL/license/keywords fields
when known. The full provenance includes every contributing stage, not a misleading
single `extraction_backend` for a hybrid result. Safe YAML quoting preserves strings
that look like dates/numbers; titles containing punctuation must round-trip.

Keep assets external with relative paths; no base64 images in Markdown. Offer a
later text-only derivative with captions, descriptions, and exact table row text
for systems that do not follow local assets or preserve HTML/math. Do not promise
that uploading Markdown to a specific hosted retrieval product imports adjacent
images. Verify each intended consumer with a small retrieval smoke test when used.

Optional `chunks.jsonl` comes after canonical output: retain document/object IDs,
section path, source spans, and text. Keep equations with explanatory prose and
figure/table captions with their objects; repeat necessary header context rather
than separating table values from units. No embedding/vector-store dependency.

## 14. Validation, review, and failures

Separate three dimensions:

- **Processing:** `COMPLETE`, `PARTIAL`, `FAILED`, with per-stage statuses including
  skipped/not-requested/unavailable. Complete means all requested stages/pages
  finished; it does not mean scientifically certified.
- **Bibliography:** the four identity/BibTeX states defined above.
- **Scientific quality:** object-level findings, test coverage, comparisons,
  and human-review state. No uncalibrated single “accuracy score.”

Each check records its version, applicability, inputs, outcome (`pass`, `fail`,
`not_checked`, `not_applicable`), evidence, and affected IDs. A missing second source
is `not_checked` for cross-validation, not a pass. Report denominators: figures
observed versus matched, pages expected versus processed, equations labeled versus
transcribed, tables with known structure versus image-only candidates.

Initial findings include `IDENTITY_CONFLICT`, `VERSION_MISMATCH`,
`PAGE_NOT_PROCESSED`, `READING_ORDER_SUSPECT`, `EQUATION_UNBALANCED`,
`EQUATION_SOURCE_DISAGREEMENT`, `TABLE_CELL_DISAGREEMENT`, `TABLE_SPAN_INVALID`,
`CAPTION_UNMATCHED`, `ASSET_MISSING`, `METADATA_UNAVAILABLE`, and
`DESCRIPTION_FAILED`. Severity is independent of retryability. Unknown table
values or equation conflicts must remain visible in both report and Markdown.

A readable `diagnostics/review.md` lists priority findings with selected/alternative
snippets, source locators, crops, and the next action. Machine findings remain in
`validation.json`. Human adjudication is an explicit override record: object ID,
input hashes, chosen value/candidate, reviewer, timestamp, reason. Overrides are
revalidated after extraction changes. Detect manual edits to generated artifacts
via hashes before re-export; do not silently overwrite them.

| Failure | Required behavior |
| --- | --- |
| Corrupt/encrypted/unsupported input | Preserve intake evidence, fail that source with actionable reason. |
| Identity/version conflict | Stop fusion/publication as a normal paper; preserve candidates for review. |
| Missing HTML assets | Continue text/math/tables; list unavailable figures/resources. |
| Metadata outage/404/429 | Bounded retries/cache, distinguish unavailable from mismatch, emit unverified citation. |
| OCR/equation/table failure | Keep raw block/crop, mark unknown/partial, continue independent objects. |
| Local GPU OOM | Checkpoint, reduce documented batch once/bounded attempts, record changed settings; no remote escalation. |
| VLM/API failure | Keep image/caption, report optional-stage failure and actual cost, resume later. |
| Worker crash/time limit/pre-emption | Retain completed stage manifests; incomplete stage ignored and retried. |
| Disk full/interrupted publication | Keep previous completed export, recover journal, never mark half-write complete. |

Local code raises typed domain errors at boundaries. Batch execution catches them
per bundle and continues. Do not convert every exception into an empty successful
result. Logs have run/document/stage IDs; default output is a concise identity,
counts, warnings, backend, timing, and output-path summary. Debug logs redact keys,
credentials, and signed URL tokens.

## 15. Caching, reproducibility, and corpus transactions

Stages form a small explicit dependency graph:

```text
copy/hash -> inspect -> extract per source -> normalize -> align/select
                              |                              |
metadata candidates -> registry snapshot -> identity --------+
                                                             |
                                                   validate -> export
                                                             ^
selected figure assets -> describe ---------------------------+
```

A stage key hashes canonical serialized inputs: relevant source hashes, schema and
adapter versions, model repository + immutable revision/weight digest, configuration,
execution policy, and any upstream artifact hashes. Include precision/device when
it can affect results. Record full environment/lock digest and platform even where
not part of equivalence policy. Stage results contain status and checksums; validate
all referenced files before reuse. A timestamp is provenance, not an input forcing
every cache miss. `--force` creates a new run without deleting valid old evidence.

Changing metadata reruns identity/naming/citation/exports, not OCR. Changing a
figure-description prompt reruns only affected descriptions/export. Changing table
selection invalidates table validation/export. A backend unable to rerun tables
alone must report that it will re-extract the document, rather than pretending a
narrow capability exists. Cache invalidation must follow actual dependencies.

Network responses retain request identity, retrieval time, status, ETag if present,
provider, body hash and relevant raw metadata. Normal runs can reuse fresh cached
responses; explicit refresh creates a new snapshot. Offline uses existing snapshots
marked with age or reports unverified. A changed external response must not silently
rewrite previously reviewed metadata.

For runs, record application version/Git commit/dirty-state digest; worker package
versions/locks; model revisions and licenses; OCR mode; complete resolved config;
OS/CPU/GPU/driver/runtime; timing and available resource measurements; source
acquisition details; network services contacted; raw outputs; warnings/errors.
Stochastic inference may differ; promise configuration reproducibility, not
bit-identical ML output. Use fixed sampling/seeds where supported and record limits.

### Publication and concurrency

Workers write unique staging directories and never mutate shared canonical papers.
A single corpus publisher verifies manifests and assigns names. Initial publication
uses same-filesystem directory rename after complete staging. Reprocessing uses a
journaled replacement with the previous completed directory retained until the new
generation is verified. Renaming a nonempty directory over another is not assumed
to be portable or atomic. Recovery completes or rolls back the journal; consumers
requiring consistency check the generation/transaction state or take the corpus
read lock. Document the brief unavailable-path window during replacement.

Use filesystem manifests as durable truth. A local SQLite index can speed status
and lookup, but must be rebuildable from paper manifests. No many-node SQLite WAL
writers on a shared HPC filesystem. HPC workers emit per-job result/status files;
a single merge step performs corpus publication. Duplicate jobs race only at this
publisher, which compares source/stage hashes and adopts one complete equivalent
result. Lock recovery checks run ownership; do not delete a lock solely because
another job took longer than expected.

Garbage collection and history pruning are explicit maintenance actions with a
preview of retained/referenced artifacts, not part of normal extraction.

### Duplicate handling before extraction

A dump collected over years contains duplicates of several kinds, and John's first
batch (surveyed 22 September 2026; counts in `dev/Status.md`) shows all of them:
identical bytes under two names; a publisher article re-downloaded as a re-optimized
PDF with identical text but different bytes; one article as two publisher issues
differing in cover page and OCR layer; two independent scans of one 1985 paper with
different OCR text; and files identical to papers in another corpus. Detect
duplicates **before any model runs**, in tiers of decreasing certainty, and let the
certainty determine the automatic action:

| Tier | Signal | Automatic action |
| --- | --- | --- |
| 1. Identical bytes | SHA-256 equal to another intake file or a library source | Never extract twice. Record the extra filename, path and time as an alias of the existing source; both names appear in provenance. |
| 2. Content-equivalent PDF | Same page count and identical per-page text digests after conservative normalization (whitespace collapsed; known download-stamp lines removed and recorded), with agreeing embedded identifiers where present | Extract once. Preserve both originals in the bundle; the second is an alternative representation of the same document version, marked `content_equivalent`. |
| 3. Same work by identifier | Same normalized DOI or arXiv ID from embedded metadata, first-page text or user input, but content differs | Never merge. Assign each a `DocumentVersion` (§4.1), extract both and link them as versions or representations of one `ScholarlyWork`. If both claim the same version, publish one and hold the other for review with a `DUPLICATE_CANDIDATE` finding. |
| 4. Fuzzy bibliographic | Normalized title key plus first-author family name and year from PDF metadata, first-page text or registry lookup; for scans without a usable text layer, a low-resolution perceptual hash of page 1 as a weak hint | Candidate only. Extraction proceeds; the publisher flags the pair for review. Explicit `--treat-as-distinct` or `--alias-of` resolves it. |

Rules: a matching hash proves identical bytes, nothing more, and a shared DOI never
proves identical content; tier 3 covers a version of record beside an accepted
manuscript, or a supplement beside its article. Deduplication never deletes an
input or skips its preservation; it only avoids redundant extraction and records
relationships. Fuzzy signals cannot authorize a merge (§4.1). The identity
distractors in §18 (a bibliography DOI mistaken for the article DOI, a preprint
sharing a DOI relationship) apply to tier 3, so a DOI taken from page text remains a
candidate until registry checks confirm it describes this file.

Workflow: `paperextract dedup incoming/ [--library literature/]` runs offline,
writes a report (groups by tier with evidence, proposed primary and aliases) and a
proposed batch manifest; `batch` runs the same pass implicitly and applies the
automatic actions, so a duplicate is recognized even when the user does not ask.
Content fingerprints (page count, per-page text digests, embedded identifiers and
first-page identifier candidates) are stored with each preserved source and in the
catalog (§5), so later checks against a large library compare fingerprints, not
files. Tier 1 and fingerprint computation belong in P2 intake; tiers 2–4, the
report and the manifest belong in P5 unless the first real batch needs them earlier.

Fingerprinting needs a PDF text reader in the core environment. pypdfium2, already
the proposed inspection/rendering candidate (§2.4) and a transitive dependency of the
MinerU worker, is the first choice; Poppler command-line tools are acceptable only as
a development-time cross-check, not an application dependency. [PDFium]

## 16. Execution and deployment

### 16.1 First-class Mac workflow

Use native macOS arm64 environments and cached models; avoid running the everyday
Mac path in Linux containers. The user-facing command stays the same on Mac/HPC.
Default to one extraction worker for interactive use. Preflight reports the actual
MPS/Metal/CPU path for every stage, models present, and expected downloads before
setup. Display phase progress so cold model loading is distinguishable from a hang.

Qualify Docling layout on MPS with CPU TableFormer; Marker fast and explicit
balanced/local-server configurations where supported; and MinerU Standard with
Torch/MPS and its documented Mac VLM runtime. Never compare device-dependent
defaults as if they were identical profiles. [D-models] [M-release] [U-tiers]

Measure one representative paper cold, warm, and a five-paper sequence. Record
latency by stage, peak process and system memory, memory pressure/swap, device
fallbacks, and time until a usable artifact. Warm model reuse is important for
one-to-few-paper sessions; keep-alive should be bounded/configurable rather than
leaving large models resident indefinitely.

Provisional UX target to review after baseline: a typical 8–15-page born-digital
paper completes extraction within five warm minutes on this Mac, without sustained
swap and without sacrificing scientific acceptance criteria. This is a proposed
engineering target, **not a performance estimate**. Time descriptions separately.
If it misses, identify slow stages and offer clear measured alternatives; do not
quietly reduce table or math quality. Test cancellation and restart on the Mac too.

### 16.2 Clean Linux/HPC environments

Ship separate environment recipes, for example `environments/docling/`,
`environments/marker/`, `environments/mineru/`, and `environments/descriptions/`, each
with its own exact lock and platform qualification record. Use uv-managed Python
3.12 initially, a user-owned uv binary, wheels including compatible CUDA user-space
libraries, and pinned standalone binaries where required. Install/download on the
login node without running GPU workloads there. Compute-node jobs run locked and
offline by default after prefetching.

“Self-contained” cannot include the kernel, compatible glibc, GPU device access,
or the NVIDIA host driver. Probe architecture/glibc/driver and CUDA compatibility
before choosing wheels; report these unavoidable host prerequisites clearly.
Do not require a system CUDA toolkit when the qualified wheels provide runtime
libraries. If a dependency would need a compiler or a missing native library,
resolve a portable artifact/user-space package environment during qualification;
never assume `apt install` permission. Driver installation remains host-managed.
[NVIDIA-driver]

Do not assume venvs can be copied between machines or relocated arbitrarily.
Install from a pinned wheelhouse at the intended shared path; verify on a compute
node. If binary wheels cannot cover a selected stack, a separately locked micromamba
runtime is a contingency, with one dependency owner per environment. Container
recipes are optional packaging for rental machines; Apptainer is supported only if
availability is confirmed, never the baseline HPC requirement.

Marker's automatic Docker inference startup must be disabled/replaced with its
qualified external local server path. Prove model server startup, readiness, shutdown,
port binding to loopback, and per-job isolation before calling Marker HPC-supported.
Use separate ports and cache/output roots when two tasks share a node.

### 16.3 Model cache and network policy

User-configurable persistent model/cache roots; never repeated downloads per task.
Prefetch pins exact revisions, records digests/licenses, and verifies a complete
model manifest. Populate shared cache once with a lock and temporary download paths;
workers read it, using job-local scratch for libraries that insist on writing.
Stage onto node-local storage if measurements justify it. Libraries must not need
a shared writable model cache to perform inference.

`--offline` denies acquisition, metadata requests, model downloads, telemetry, and
remote inference. Workers receive model-specific offline flags and local paths;
qualification runs also use an OS/network boundary where available. Do not assume
one Hugging Face environment variable controls all engines. Check backend telemetry
and update/model calls; MinerU documents telemetry controls that require explicit
configuration. [U-readme] [D-options]

Normal mode allows metadata queries and explicitly requested public acquisition;
remote AI remains disabled. Prefetch is an explicit setup command, not a hidden
side effect in extraction. Restricted HPC networking is therefore not on the
critical path. Cache registry responses before a large batch where practical.

### 16.4 Two A40s and Slurm

**Baseline: one persistent worker per allocated GPU, processing different papers.**
Two 48 GB cards are not a single 96 GB memory pool. Do not assume NVLink, tensor
parallelism, or A100-specific performance. Starting allocation per worker: one GPU,
eight CPU cores, 64 GB host RAM, measured wall-time request. These are tunable
qualification settings, not a requirement to reserve the full 64-core/512 GB node.

Use a job array with concurrency capped at two, or two `srun` tasks on one node.
For short papers, shard several papers per task to amortize model loading. Respect
Slurm's GPU visibility; do not set a global physical GPU index that defeats binding.
A worker normally addresses its one visible accelerator as device zero. Use bounded
CPU threads per worker to avoid nested oversubscription.

Illustrative template after local partition/account options are supplied:

```sh
#!/bin/bash
#SBATCH --array=0-1%2
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=02:00:00
set -euo pipefail
# Prepared absolute executable path and run directory are site configuration.
srun /shared/paperextract/core/.venv/bin/paperextract batch \
  --manifest /shared/run/batch.jsonl \
  --shard-index "$SLURM_ARRAY_TASK_ID" --shard-count 2 \
  --offline --staging /shared/run/results
```

This is a proposed interface, not an executable bootstrap script. Array tasks may
run on separate nodes; a two-task node allocation is an alternative to benchmark.
The result merger publishes once after jobs finish. Handle TERM/pre-emption by
closing completed stage manifests and stopping new work; partial model calls can
restart. Never depend on requeue being automatically enabled by the cluster.

Benchmark one versus two GPUs, 1/2/4 CPU conversion callers per inference server,
and safe page/model batch sizes. More workers may exhaust VRAM, saturate CPU decode,
or harm interactive latency. Tensor parallelism is a later alternative only when
a needed model cannot fit one card or measurements beat independent-paper work.

### 16.5 Rental execution and completion-time economics

Reuse the Linux worker locks, model manifests, and staged result protocol on a
rented A100/H100/H200 or other qualified GPU. Use a pinned OCI image when appropriate;
record image digest, host driver, GPU type/count, attached storage, and exact models.
No vendor SDK or RunPod dependency is required for core extraction.

Compare **time until verified corpus is available**:

```text
completion time = queue/provision wait + environment/model setup + upload
                + extraction + descriptions + validation/export + download
cost = billed GPU/host hours × actual quoted rate + storage + transfer
papers/GPU-hour = accepted papers / sum of allocated GPU wall hours
papers/dollar = accepted papers / total billed cost
```

Use accepted papers at equivalent scientific quality, reporting failures/retries
separately. The brief's approximate USD 4/hour is a scenario input, not a verified
current price. Collect actual quotes at run time. A rental can be worthwhile even
when free HPC is equally fast once started, if its queue is long or John prefers
an earlier completion time. Offer measured small/medium/large-batch estimates with
an explicit spend cap; no arbitrary speedup threshold is a prerequisite.

Model download and cold startup can dominate small rentals. Persist model caches
only when worthwhile, verify result transfer before cleanup, and stop billable
resources when the authorized batch ends. Larger GPUs need a measured bottleneck
or availability benefit, not a blanket assumption that OCR scales with GPU size.

## 17. CLI, profiles, and batch input

Use standard-library argparse initially. The explicit command is `extract`; a
non-command positional path is shorthand for `extract`. Reserved-command ambiguity
is resolved with `paperextract extract -- ./status`. Options resolve in order:
CLI > explicit config > user config > versioned defaults. Persist the resolved
configuration, excluding secrets.

Proposed commands:

```sh
paperextract paper.pdf --corpus literature
paperextract paper.pdf publisher.html --corpus literature --backend marker
paperextract extract source_bundle/ --backend mineru --profile balanced
paperextract extract paper.pdf --offline
paperextract extract arXiv:2206.01062v1 --corpus literature
paperextract batch incoming/ --corpus literature
paperextract batch --manifest papers.jsonl --corpus literature --resume
paperextract validate literature/SomePaper --refresh-metadata
paperextract describe literature/SomePaper --provider local --model MODEL
paperextract describe literature/SomePaper --provider openai --model MODEL --max-cost USD
paperextract reprocess literature/SomePaper --stage tables --backend docling
paperextract compare --manifest benchmark.jsonl --backends marker,mineru,docling
paperextract status literature --json
paperextract dedup incoming/ --library literature --report dedup.json
paperextract lookup --doi 10.1038/s41566-019-0416-4 literature other-library
paperextract index rebuild literature
paperextract organize literature --layout by-year --dry-run
paperextract doctor --backend docling
paperextract models prefetch --backend docling --profile balanced
```

`--corpus` is optional after user configuration; proposed unconfigured default is
`./literature`, shown explicitly in the result. Network model setup must be
completed first or return a clear setup instruction. No automatic backend install
or unannounced remote use. `doctor` diagnoses devices/dependencies/model manifests
without sending documents anywhere.

One invocation with several source paths means **one bundle**. `batch` explicitly
means multiple papers. For a directory without a manifest: one PDF plus one article
HTML and its asset tree is a candidate bundle; several main PDFs/HTMLs require a
manifest or explicit batch interpretation. Do not recursively merge every file in
a folder. A batch directory treats each top-level PDF as a bundle and each child
bundle directory as a bundle; asset directories are not standalone papers.

A JSONL manifest provides one explicit bundle per line with schema version,
bundle ID, relative source paths/URLs, roles/version hints, optional DOI/arXiv,
and per-item overrides. Resolve paths relative to the manifest. Verify unique IDs,
source existence, deterministic ordering, and no ambiguous duplicate publication
targets before expensive work. Shards partition by stable bundle digest modulo
shard count; checkpoint per bundle/stage, not just a last line number.

For arXiv, preserve base ID and explicit version, response metadata, original PDF,
URL/time, and related DOI if supplied by arXiv. Resolve an unversioned request to a
specific version and record that resolution. Support old-style identifiers too.
Respect arXiv's request rules, bounded acquisition, and retry delays; source TeX
is optional and safely unpacked without execution. [arXiv]

Public PDF/article URLs use bounded redirects, timeouts, content/MIME checks, size
limits and deterministic saved bytes. No publisher authentication, cookie replay,
paywall bypass, or browser session automation. Explicit acquisitions are independently
cacheable and can be performed on a network-enabled machine before offline HPC.

### Profiles

| Profile | Policy | Initial mapping to qualify |
| --- | --- | --- |
| `fast` | Preview/indexing, explicit fidelity limitations; no descriptions | Marker fast, MinerU Flash/Basic selected explicitly, Docling native/reduced models where supported. |
| `balanced` | Preserve scientific content; equations and rich tables enabled | Marker explicit mode by qualified platform, MinerU Standard, Docling standard + formula enrichment. |
| `high-quality` | Same outputs plus expensive checks/targeted second extraction | Stronger qualified settings, same-edition cross-checks, optional local description configuration. |

A profile is a versioned policy, not three names assumed equivalent upstream.
Backend/policy combinations expose unsupported features clearly. `high-quality`
never grants remote AI permission, silently paraphrases cells, or promises higher
accuracy merely because it uses more compute. Text/equation/table-specific backend
selectors remain internal config until a measured hybrid justifies public flags.

Exit codes: `0` completed (warnings summarized), `2` usage/configuration error,
`3` identity/version conflict preventing publication, `4` partial result or mixed
batch failure, `5` execution failure, `130` cancellation. A strict-validation mode
returns `4` for specified warning/error classes. JSON output has a stable schema;
progress goes to stderr. Batch status contains every item, including failed and
skipped ones, rather than only successes.

## 18. Scientific benchmark and regression program

### Start with John's papers

John supplied the initial corpus on 22 September 2026. Inspection found five
distinct papers, six article/manuscript PDFs (75 pages), the published HISOL
supplement (15 pages), two publisher HTML captures and their MHTML alternatives,
and HISOL's accepted-manuscript LaTeX and original figure assets. This
is sufficient to begin P1. `dev/Corpus.md` records the inventory, initial
inspection, version distinctions, capture defects, and proposed holdout.

Use this initial set to cover the following categories; categories can overlap:

1. Routine two-column experimental optics with a known DOI.
2. Equation-heavy theory with inline math, aligned equations and a matrix.
3. Numerical tables containing units, uncertainties/exponents and merged headers.
4. Multi-panel/vector figures with small axis labels and legends.
5. Clean paired publisher PDF/HTML, plus a poor/missing-asset HTML capture if possible.

The supplied set already contains scans with imperfect OCR, supplemental content,
an accepted manuscript, and a table rotated within the page. Later add arXiv
version changes, corrigenda, deliberate similar-title distractors, and a PDF with
no text layer. A large public corpus is not a prerequisite. Private sources stay
outside Git or in an ignored directory;
committed synthetic fixtures reproduce structural failure modes with redistribution
permission. Hash inputs and keep acquisition/license notes.

HISOL's LaTeX is reference evidence for the supplied accepted manuscript, not an
unconditional verbatim oracle for the journal PDF or HTML. Compare the source
against the matching rendered accepted PDF first, retaining TeX bytes, rendered
text, locators, and provenance separately. Copy editing, math typography, numbering,
and supplement coverage can differ in the version of record. Cross-version
differences must be classified and reviewed before becoming extraction errors;
do not insert accepted-only content into the published article. Using TeX to author
benchmark annotations does not bring a general TeX ingestion backend into scope.

The first gold annotations should be small enough to review manually: identity and
version for each paper; section/reading-order landmarks; 10–20 equations including
inline examples; 30–50 important table cells with header paths; all figure/table
counts and caption links; 10–20 figure claims. Scale according to actual papers.
These counts are proposed annotation workload, not a statistical guarantee.

A gold record identifies source hash, locator, exact expected content or relationship,
reviewer/date, and importance. Preserve visual crops for adjudication. Do not create
gold by accepting a backend's answer. Keep a holdout paper/pages separate from
profile tuning; initially report individual results, not statistically confident
rankings from five documents.

### Quality measurements and acceptance

| Area | Measurement | Initial acceptance gate |
| --- | --- | --- |
| Identity | Work/version/DOI accuracy; false merge rate | No false merge or silently wrong identifier on gold set. |
| Coverage | Requested/processed pages and expected objects | Every missing page/object is accounted for and visible. |
| Prose/order | Landmark ordering, paragraph boundaries, sampled transcription | No unflagged column splice or major missing section. |
| Math | Exact critical symbol/number/label checks, normalized token accuracy, visual review | No unflagged critical sign/subscript/exponent change; report unresolved rate. |
| Tables | Exact raw cell strings + header/footnote associations + topology | Every gold critical value correct, or explicitly unresolved with source evidence. |
| Figures | Composite completeness, legibility, caption match, vector preservation | All gold figure/caption matches correct; missing/low-quality assets flagged. |
| Descriptions | Printed-label precision, unsupported-claim rate, useful coverage, abstentions | Zero invented precise values in gold sample; review local/remote tradeoff. |
| Provenance | Source locator/asset hash coverage | Every selected scientific object traceable at stated granularity. |
| Export | Round-trip metadata, relative links, retrieval coverage | Move directory and still resolve all assets; preserve searchable numerical context. |
| Recovery | Interrupt/resume/corruption/duplicate races | Previous valid results survive and reruns do not publish duplicates. |

A system that marks everything unknown would pass some safety rules but be useless.
Therefore report **usable correct coverage**, unresolved count, and expert review
minutes per paper alongside error counts. Set minimum useful-coverage targets after
the pilot, explicitly with John; do not mask uncertainty behind a blanket success
rate. Content-changing regressions fail until reviewed even if aggregate scores rise.

### Hardware matrix

Run the same frozen corpus and model/profile settings where comparable:

- Mac CPU baseline and actual MPS/Metal path; cold versus warm; one/five papers.
- One A40 versus two A40s, independent workers first; CPU allocation/thread sweeps.
- Local descriptions separately from PDF extraction; precision and image-resolution
  sweeps scored for scientific accuracy.
- Rented A100/H100/H200 trial only when a batch/availability decision needs evidence;
  same accepted outputs, actual rates and end-to-end setup/transfer costs.

Record parsing, OCR, layout, equations, tables, figure assets, description, metadata,
validation, and export timings. If an upstream does not expose a stage timer,
report combined wall time with instrumentation granularity; do not invent a split.
Record documents/pages/hour, papers/GPU-hour, cold/warm times, p50/p95 when sample
size warrants them, peak RSS and process-tree memory, VRAM/utilization via NVML or
`nvidia-smi`, CPU load, and bytes transferred. On Mac distinguish reported framework
allocated memory from total unified-memory pressure; unavailable metrics are null.
Report warm-model batching and model-cache effects separately.

Keep model/backend comparisons and hardware comparisons distinct: an engine that
changes precision/model/settings is a new quality configuration. Repeat timed
trials on an otherwise quiet machine, retain failures, and record queue/provision
wait when comparing time-to-result. Never promise A100/H100 numbers from another
project's benchmark or scale linearly from B200 results.

### Regression lanes

Default offline gate: pure schema/parser/reconciliation/identity/export/storage
unit tests, synthetic end-to-end examples, property tests for table spans and path
handling when warranted, canned network responses, and worker protocol fakes.
Optional backend lane: real pinned models plus small gold corpus and raw-output
contract fixtures. Accelerator lane: Mac and Linux qualification. Live-network lane:
provider response-shape/availability checks without making CI flaky. Benchmark lane:
quality and resource reports, no automatic gold replacement.

Required adversarial cases include a bibliography DOI mistaken for the paper DOI;
preprint/publication sharing a related DOI; merged cells with blanks; exponent
splitting; omitted first/last pages; same figure number in a supplement; duplicate
archive paths; filename/case collisions; original mutation during copy; cache
corruption; metadata outage; interrupted publication; concurrent duplicate jobs;
and generated descriptions contradicting printed labels.

## 19. Implementation work packages and review gates

John supplied papers and approved this plan on 22 September 2026; P1 has started.
Track its evidence and subsequent milestones in `dev/Status.md`.
Each package adds manual/docs, docstrings, tests, and changelog entries with its
implementation. Do not build a general framework before demonstrating the first
useful paper directory. Starting P1 does not settle provisional backend choices.

| Package | Concrete deliverable | Depends on | Exit evidence |
| --- | --- | --- | --- |
| **P0 — plan/bootstrap** | This plan, project/license/tooling/manual, honest package skeleton | Current request | Offline gate, lock consistency, wheel/sdist builds and installed-wheel smoke check. |
| **P1 — corpus and runtime qualification** | Private corpus manifest/gold schema; three Mac backend smoke trials; hardware and license ledger; capture comparison | Plan approval, example papers | All versions/configurations recorded; raw output inspected for inline math/cells/coordinates; recommended first adapter chosen. |
| **P2 — first complete extraction slice** | Source/bundle schemas, ingest/hash with identical-byte duplicate skip and content fingerprints, one PDF adapter, metadata/BibTeX, math/figure/table assets, validation, portable export with catalog row, simple CLI | P1 | A few supplied PDFs produce all mandatory artifacts; offline rerun; gold checks; missing content explicitly flagged; Mac timing recorded. |
| **P3 — useful figure descriptions** | Independent description worker, structured claims/provenance, local model trial and optional remote comparator, selective re-description | P2 figure contract | Description rubric passed or limits exposed; no extraction rerun; no remote requests without policy; measured Mac latency. |
| **P4 — semantic HTML and reconciliation** | SingleFile/HTML+assets/MHTML ingest; semantic math/cells; same-version gate; object matching and diffs | P2 | Paired examples improve/verify content; disagreements preserved; missing assets survive; multi-source provenance complete. |
| **P5 — backend breadth and corpus behavior** | Remaining adapters; explicit all-three selection; comparison command; manifests/resume/versions/arXiv; tiered duplicate report and manifest; SQLite/FTS index, `lookup`, `catalog.md`, layout policy and `organize`; safe updates/migrations | P2, P4; P3 retained | Same canonical contract for all three; all-page checks; failure/collision/recovery tests; supported release candidate on Mac. |
| **P6 — Linux batch and rentals** | Self-contained locks/wheelhouse/model setup, one/two-A40 Slurm templates, single result merger, reusable OCI recipe | P1 runtime findings, P5 | Compute-node smoke test without admin installs; two-GPU qualification; pre-emption/resume; honest rental throughput/cost scenario. |
| **P7 — hardening and release review** | User guide, reviewed defaults/profiles, upgrade/migration tests, integrity checks, known-limitations matrix | P3–P6 | Scientific acceptance report on holdout; full offline gate; Mac and Linux qualification; licenses audited. |

P2 is an early useful preview, not a claim that the entire brief is complete. P3 is
intentionally before full backend/batch infrastructure to honor the high priority
of figure descriptions. P1 may inspect all backends without prematurely implementing
three polished adapters. P6 environment qualification can begin earlier if cluster
access is available, but it must not delay the Mac extraction loop.

Within P2 implement in this order: (a) schemas and synthetic fixtures; (b) immutable
source storage and minimal export; (c) one real adapter and geometry tests;
(d) DOI identity/BibTeX with offline fixtures; (e) rich tables/math/figures;
(f) validation, CLI, and first full paper review. Extend only the parts needed by
the selected papers. A thin public extraction API follows the proven pipeline;
there is no need for a plugin marketplace in v1.

Deferred beyond the first supported release: general TeX interpretation, WARC and
WebArchive readers without demonstrated need, semantic equation equivalence,
automatic plot digitization, aggressive panel segmentation, validating the full
citation graph, automatic parameter/unit conversion, vector-store integration,
GUI, authenticated acquisition, and distributed task queues. `auto` backend routing
should wait for enough benchmark evidence to define and test a policy.

### Implementation notes for P3–P5 (23 September 2026)

These record where the implementation settled a choice the plan left open,
or deliberately narrowed it; `dev/Status.md` has the evidence.

- **P4 is a cross-check, not reconciliation.** Saved pages are preserved as
  sources and compared with the PDF extraction (text coverage, captions,
  table numbers, references) only for the same DOI or, lacking one, the same
  title. Nothing from a page enters `paper.md`; object-level selection
  between sources (§9) is not implemented, and math is not compared.
- **Docling is the second backend.** It serves both as `--backend docling`
  and as an opt-in numerical table check of MinerU's tables (off by default,
  John's decision). Its formula model is slow on a CPU and weaker than
  MinerU's on the pilot, so MinerU stays the default. Marker was adapted
  later for completeness and for `compare`, which reports differences
  between backends without selecting between them.
- **Same-version duplicates are flagged, not held.** Tier 3 relations are
  recorded (`OTHER_VERSION_IN_LIBRARY`, `DUPLICATE_CANDIDATE`) and nothing is
  held, because document versions are rarely stated in files and a hold
  decided on weak evidence would lose papers from the library.
- **Document version is evidence-based and often unknown.** A user
  assertion wins; otherwise manuscript markers, arXiv provenance, or a
  printed validated DOI with the journal named decide.
- **arXiv identity uses arXiv's DataCite DOIs** (`10.48550/arXiv.*`), so the
  existing registry lookup and title check apply unchanged.
- **Tier 4 at intake uses PDF information titles only**; first-page title
  and page-image hashes for scans are not implemented.
- **Layout shards come from validated fields only**, with `Unverified/` for
  the rest; names stay unique across shards.
- **Migration is a rebuild from kept evidence.** `paperextract.formats` lists
  the versions each record may have; readers refuse unknown versions. The
  `migrate` command rebuilds outdated papers through the journaled
  `reprocess` path, so current normalization also applies; there are no
  per-version record converters. Papers whose files differ from their
  manifest are refused rather than rebuilt over.

### Implementation notes for P6 (23 September 2026)

Qualification hosts (site-specific details such as host names, accounts,
paths and quotas are kept outside the repository, in the maintainer's
private site guide):

- **A Slurm cluster** with GPU nodes of two NVIDIA A40s (46 GB, compute
  capability 8.6), 64 cores and 512 GB each; Rocky Linux 9, glibc 2.34,
  driver 595 (CUDA 13.2); node-local disk; no containers or administrator
  installs; network access from login and compute nodes. Builds, caches,
  models and runs live on shared project storage, not in a home directory
  with a file-count quota. Long login-node work runs under `tmux`; all
  compute goes through Slurm.
- **A CPU-only Linux workstation** (Ubuntu 24.04, glibc 2.39, 72 Xeon
  cores) as a check host.

Decisions and findings so far:

- The worker locks now resolve for macOS arm64 and Linux x86_64; the Mac
  pins are unchanged. MinerU's Linux lock has an opt-in `cuda` extra
  (`mineru[full]`: vLLM 0.28, torch 2.13 cu130); Docling and Marker use
  torch 2.14 cu130 from PyPI. No system CUDA toolkit or container is used.
- MinerU's Linux llama.cpp wheel has CPU and Vulkan builds only, and ONNX
  Runtime is CPU-only, so the Mac configuration runs on the CPU on Linux.
  GPU inference uses `[mineru] engine = "vllm"` with the original weights
  (`opendatalab/MinerU2.5-Pro-2605-1.2B`, Apache-2.0) and optionally
  `small_models = "torch"` (`opendatalab/MinerU-4_models_torch`,
  Apache-2.0). Both remain in-process local engines; remote servers stay
  refused. vLLM usage statistics and FlashInfer's JIT sampler are switched
  off in the worker environment.
- Three configurations are measured on the pilot papers: `portable`
  (ONNX + llama.cpp, CPU), `hybrid` (ONNX + vLLM) and `gpu` (torch + vLLM),
  and each is compared with the Mac output of the same PDF before any is
  called qualified. A persistent worker per GPU is added only if vLLM
  start-up measurably dominates per-paper time.

## 20. Risks, alternatives, and unresolved questions

| Risk / question | Planned response or decision gate |
| --- | --- |
| Initial corpus is small and not fully annotated | Five papers now supplied; freeze hashes, seed reviewed annotations, retain a holdout, and expand only when a missing failure mode warrants it. |
| M5 performance/engine support differs by stage | Native Mac smoke tests precede default selection; CPU fallback recorded and tested. |
| Scientific inline math may be lost by a nominal formula backend | Gold inline spans and PDF/HTML comparisons; no display-only acceptance shortcut. |
| Hidden MathML stripped from browser capture | Capture profile comparison before recommending a publisher-specific workflow. |
| Two same-title versions accidentally fused | Identity/version gate before reconciliation; explicit relationships and conflicts. |
| Marker 2 NVIDIA startup expects Docker | Qualify standalone local server path; otherwise report that backend unavailable on bare HPC. |
| MinerU 4 API/schema still changing | Exact lock, ParseResult contract fixture, upgrade reports; do not code to old examples. |
| Table header/span ambiguity | Exact raw model plus visual evidence; mark unresolved rather than plausible CSV. |
| VLM makes confident false claims | Structured claim types, no tools, printed-number/abstention evaluation; local/remote benchmark. |
| Native PDF vectors difficult to isolate | Preserve original + crop geometry; qualify vector export separately from raster preview. |
| Self-contained Linux stack lacks compatible binaries | Probe host ABI/driver; user-space runtime contingency, no assumed admin packages. |
| Network firewalls/model-download outages | Prefetch + manifest-verified caches; no downloads on critical extraction path. |
| Hidden telemetry in backend dependencies | Worker environments set every documented opt-out before import (`ORT_DISABLE_TELEMETRY`, `HF_HUB_DISABLE_TELEMETRY`); qualification checks worker sockets and home-directory side effects. ONNX Runtime 1.30 telemetry was found active on 22 September 2026 and switched off (`dev/Status.md`). |
| HPC shared filesystem locks/SQLite contention | Per-job staging and one publisher; filesystem manifests as truth. |
| New metadata changes a published filename | Persistent naming registry; deliberate rename operation only. |
| Import order versus globally deterministic collision names | Document registry-scoped determinism; batch collision groups deterministic; no silent existing rename. |
| Metadata/citation licenses or backend model restrictions | Audit actual pinned assets, keep model notices separate, no automatic relicensing. |
| Hosted RAG drops HTML/math/local assets | Exact row text, labeled descriptions, portable text derivative, consumer-specific smoke test. |
| Tuning overfits five papers | Holdout plus incremental difficult cases; report per-object outcomes and uncertainty. |
| Library outgrows a browsable flat directory | Paper directories independent of location; recorded layout policy; index as the lookup mechanism; explicit `organize`, never ad hoc moves. |
| Re-downloaded publisher PDFs differ in bytes or cover pages | Tiered duplicate detection with content fingerprints before extraction; aliases recorded; nothing deleted or merged on fuzzy evidence. |

Still needed before affected implementation work:

- Review and extend the initial gold candidates; papers and source paths are now
  available. Additional publishers are useful later, not required to start.
- Slurm account/partition/time limits, host driver/glibc versions, shared filesystem
  semantics, node-local scratch, and whether user-space standalone model servers
  are permitted. Do not ask for or assume privileged system installation.
- Preferred practical one-paper latency and expected batch sizes (10/100/1000).
  Five warm minutes is a proposed target to adjust after measurements.
- API provider/model and spend cap when running the description comparison; no key
  is needed to begin the local implementation.
- Final backend default, capture profile, native vector exporter, and useful-coverage
  thresholds after P1/P2 evidence. These are deliberately undecided, not omitted.

## 21. Coverage of the brief

| Brief sections | Addressed in this plan |
| --- | --- |
| 1–5 | §§3–4, 7–9, 17: local bundles, complementary sources, cross-checks, simple CLI. |
| 6–9 | §§5, 13: naming, portable artifacts, complete Markdown, front matter. |
| 10–12 | §6: independent identity, field evidence, BibTeX validation and verification tools. |
| 13–15 | §10: inline/display semantic math, fidelity, validation and uncertainty. |
| 16–22 | §11: assets, source quality, descriptions, models, panels and re-description. |
| 23–27 | §12: exact cells, spans, HTML/CSV, searchable text and validation. |
| 28–32 | §§3–4, 9, 18: locators, canonical model, adapters, hybrid selection and comparison. |
| 33–38 | §§4–8, 17: capture formats, manual acquisition, arXiv, versions and originals. |
| 39–43 | §§14–16: provenance, cache, quality, no silent fabrication and offline policy. |
| 44–49 | §§1, 16, 18: first-class M5, corrected A40 HPC, concurrency, rentals and measurement. |
| 50–53 | §§15, 17–19: resumable batch, supplied corpus, gold annotations and regressions. |
| 54–59 | §§3–4, 8, 13, 17–18: retrieval, references, Unicode, rich structure, profiles and evolution. |
| 60–65 | §§2, 7, 14–16: licenses, environments, caches, logs, recovery and acquisition boundaries. |
| 66–68 | §§1, 17, 19: simple workflows, priority ordering, explicit deferred scope. |
| 69–71 | §§2, 7: 27-point backend matrix, dependency purposes and capture recommendation. |
| 72–73 | §§18–20 and the complete design: small-corpus staged delivery, review before implementation. |

## 22. Bootstrap delivered alongside this proposal

The scaffold has an installable `src/paperextract` package and typing marker,
Apache-2.0 `LICENSE`/`NOTICE`, owner metadata, README/changelog, Sphinx manual,
uv configuration/lock, package smoke tests, and a GitHub Actions workflow.
No extraction CLI, source acquisition, backend/model install, schema implementation,
or paid execution is included. The plan is rendered into the manual without
copying a second editable version.

The inherited solprop workflow was adapted deliberately: Python 3.12 baseline;
no numerical libraries, JIT/kernel coverage, GPU build system, gallery, or nonexistent
slow lane; strict types; offline default tests; recursive docstring lint;
100% core branch coverage; non-mutating checks separated from autofix; shared
local/CI gate; installed-wheel check; explicit private-data policy. AGENTS.md now
permits routine decisions within scope and reserves questions for material choices.

Bootstrap checks validate packaging and tooling only. They are not evidence of
paper-extraction quality, ML compatibility, GPU performance, or HPC availability.
No backend weights were downloaded and no sample papers were processed during
planning. See the final task report for checks actually executed and their outcome.

## 23. Primary sources

Sources were checked on 22 September 2026. Tagged code and package metadata were
inspected where linked. Moving documentation must be rechecked against the eventual
worker pins; links below record evidence, not a promise of future compatibility.

[M-release]: https://github.com/datalab-to/marker/releases/tag/v2.0.0
[M-pypi]: https://pypi.org/project/marker-pdf/2.0.0/
[M-readme]: https://github.com/datalab-to/marker/blob/v2.0.0/README.md
[M-pdf]: https://github.com/datalab-to/marker/blob/v2.0.0/marker/converters/pdf.py
[M-json]: https://github.com/datalab-to/marker/blob/v2.0.0/marker/renderers/json.py
[U-release]: https://github.com/opendatalab/MinerU/releases/tag/mineru-4.0.5-released
[U-pypi]: https://pypi.org/project/mineru/4.0.5/
[U-readme]: https://github.com/opendatalab/MinerU/blob/mineru-4.0.5-released/README.md
[U-license]: https://github.com/opendatalab/MinerU/blob/mineru-4.0.5-released/LICENSE.md
[U-tiers]: https://opendatalab.github.io/MinerU/usage/tiers/
[U-sdk]: https://opendatalab.github.io/MinerU/usage/sdk_api/
[U-output]: https://opendatalab.github.io/MinerU/reference/output_files/
[D-release]: https://github.com/docling-project/docling/releases/tag/v2.129.0
[D-pypi]: https://pypi.org/project/docling/2.129.0/
[D-readme]: https://github.com/docling-project/docling/blob/v2.129.0/README.md
[D-document]: https://docling-project.github.io/docling/concepts/docling_document/
[D-tables]: https://github.com/docling-project/docling/blob/v2.129.0/docs/examples/export_tables.py
[D-models]: https://docling-project.github.io/docling/usage/model_catalog/
[D-formula]: https://docling-project.github.io/docling/_generated/examples/code_formula_granite_docling/
[D-options]: https://github.com/docling-project/docling/blob/v2.129.0/docs/usage/advanced_options.md
[Crossref]: https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/
[DataCite]: https://support.datacite.org/docs/api-get-doi
[DOI]: https://citation.doi.org/docs.html
[OpenAlex]: https://help.openalex.org/api/authentication/
[Grobid]: https://github.com/grobidOrg/grobid
[Verify]: https://github.com/vishakhpk/verify_citations
[Bib]: https://pybtex.org/
[PDFium]: https://pypdfium2.readthedocs.io/en/stable/
[MathML]: https://www.w3.org/TR/mathml-core/
[SingleFile]: https://github.com/gildas-lormeau/SingleFile
[SingleFile-FAQ]: https://github.com/gildas-lormeau/SingleFile/blob/master/faq.md
[Firefox-save]: https://support.mozilla.org/en-US/kb/how-save-web-page
[MHTML]: https://www.rfc-editor.org/info/rfc2557/
[Safari-save]: https://support.apple.com/en-kw/guide/safari/ibrw1089/mac
[arXiv]: https://info.arxiv.org/help/api/tou.html
[Qwen]: https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct
[Granite]: https://huggingface.co/ibm-granite/granite-vision-3.3-2b
[MLX]: https://github.com/Blaizzy/mlx-vlm
[NVIDIA-driver]: https://docs.nvidia.com/datacenter/tesla/driver-installation-guide/latest/
[FTS5]: https://sqlite.org/fts5.html
[CSL]: https://github.com/citation-style-language/schema
[Zotero]: https://www.zotero.org/support/dev/client_coding/direct_sqlite_database_access
[Calibre]: https://manual.calibre-ebook.com/faq.html
[Papis]: https://github.com/papis/papis
