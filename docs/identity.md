# Bibliographic identity

`paperextract.identity.resolve_identity` decides what a document *is* from two
kinds of evidence: identifier candidates found in the PDF, and the registration
metadata a DOI registry holds for them. A candidate is never trusted on its own.
The registry record is compared with the article's own title and authors, and the
outcome is recorded field by field with the evidence used.

```python
from pathlib import Path

from paperextract.registry import default_lookup
from paperextract.storage import resolve_and_write_identity

lookup = default_lookup(
    Path("literature/.paperextract/registry-snapshots"), contact=None
)
identity = resolve_and_write_identity(Path("staging/run-1"), lookup)
print(identity.status, identity.doi, identity.field("title"))
```

`extract_and_publish` runs this step automatically when `ExtractionSettings`
carries a `lookup`. Without one, the paper is published as unverified.

## Candidates

DOI strings are collected from the PDF information dictionary and the first-page
running headers or footers (strong sources), from the content fingerprint's scan of
the first two pages, and from first-page body text (weak sources). DOIs inside
reference entries are excluded because they identify cited works. An Elsevier PII
in the information dictionary, such as `PII: 0022-4073(81)90057-1`, yields the weak
candidate `10.1016/0022-4073(81)90057-1`, since that PII is the suffix of the
article's DOI. Candidates are
normalized conservatively (prefixes and resolver URLs removed, lower-cased,
trailing punctuation dropped); the original spelling stays in the evidence.

The name a file was supplied under can encode its DOI by a publisher's
convention, and yields a weak candidate, tried before the page-text scans:

| Publisher | File name | DOI |
| --- | --- | --- |
| APS | `PhysRevA.13.1422.pdf` | `10.1103/PhysRevA.13.1422` |
| Optica | `josa-61-1-89.pdf`, `ao-47-17-3143.pdf` | `10.1364/JOSA.61.000089`, `10.1364/AO.47.003143` |
| Springer Nature | `s41598-018-34641-y.pdf` | `10.1038/s41598-018-34641-y` |
| Royal Society | `rspa.1920.0020.pdf` | `10.1098/rspa.1920.0020` |
| ACS | `jp980221f.pdf` | `10.1021/jp980221f` |
| Elsevier, before 2000 | `1-s2.0-S0092640X83710132-main.pdf` | `10.1016/0092-640X(83)71013-2` |
| arXiv | `2206.01062v2.pdf` | `10.48550/arxiv.2206.01062` |

A copy suffix such as `-2` is ignored. A person or a download service chose
the name, so it is evidence of the same weight as a DOI in body text: the
registry record must match the article's title, and a DOI accepted this way
is `VALIDATED_WITH_WARNINGS` with an `identifier` warning, as the file itself
prints no DOI.

An arXiv identifier on the first two pages, such as `arXiv:2206.01062v2` or
`arXiv:hep-th/9901001`, becomes the weak candidate `10.48550/arxiv.2206.01062`,
the DataCite DOI arXiv registers for every paper, and is checked against the
page like any DOI. A paper identified this way records its arXiv identifier,
with the version the file prints, in `metadata.json`, the front matter and the
catalog.

## Document version and related papers

Each paper records a `document_version` with its evidence
(`document_version_evidence` in `metadata.json`):

| Version | Evidence |
| --- | --- |
| asserted by the user | `--document-version` on `extract` or `reprocess`, recorded as an assertion and kept by `reprocess` |
| `accepted_manuscript`, `submitted_manuscript`, `preprint` | "accepted manuscript", "author's final version", "submitted to", "under review" or "preprint" on the first two pages |
| `preprint` | downloaded from arXiv, or identified through arXiv |
| from the file name | a name the user gave that says `AAM`, `accepted`, `postprint`, `submitted` or `preprint`, such as `Travers_HISOL_2018_NatPhot_AAM.pdf` |
| `version_of_record` | the file prints the validated DOI in its metadata or running headers and names the journal on its first pages |
| `unknown` | anything else |

At publication each paper is related to the papers already in the library:
the same validated DOI makes another document of the same work
(`OTHER_VERSION_IN_LIBRARY`, or `DUPLICATE_CANDIDATE` when both claim the same
known version), and the same normalized title, first-author family name and
year without a shared DOI make a possible duplicate (`DUPLICATE_CANDIDATE`).
Relations are listed in `metadata.json` (`related`), `validation.json`
(`relations`) and `diagnostics/review.md`; nothing is merged, held or deleted.
On 23 September 2026 the HISOL accepted manuscript, identified by title
search, was published beside the version of record and related to it by the
shared DOI; its version stays `unknown` because the file does not say.

## Registry lookup

`paperextract.registry.lookup_doi` asks Crossref first and DataCite second, by
identifier only. `UrllibClient` paces requests below the limit the service
advertises in its `x-rate-limit-*` headers, retries transient statuses with
`Retry-After` or exponential backoff, and identifies itself with a User-Agent that
carries the contact address only when you configure one. `CachedClient` stores
every successful or not-found response as a snapshot under the library's private
directory, so reruns, re-exports and offline runs never query again unless you
ask for a refresh. Observed on 22 September 2026: Crossref's anonymous pool
advertises five requests per second; DataCite documents a per-address limit of
3000 requests per five minutes. A batch of a few hundred papers is well inside
both, provided lookups run from one process rather than from every cluster job.

## Titles

The article's title is not always the PDF's title. Several candidates are
compared with every registry record, most likely first: the PDF information
title, unless it names an identifier, an authoring file or a typesetting
template (such as `PII: …`, `Microsoft Word - …`, `acs_JX_jp-2011-094438 1..7`,
`vyk90e3.tmp` or `Using JCP format`), then up to three level-1 headings of the
first two processed pages. A running header or a journal name set as a heading
is often among them, which is harmless: a candidate counts only when a registry
title matches it.

Titles are compared after removing markup that is not words: HTML tags and
entities (`N<sub>2</sub>`), TeX (`H_{2}`, `\mathbf{N}`), placeholders for
glyphs the PDF could not map (`[?]`) and trailing footnote markers (`*`, `†`).
Titles also match when their keys agree with spaces removed, because Crossref
drops the spaces around MathML in some titles. Beyond that, two titles agree
only *nearly*, which is a title warning, when

- they differ in at most two one-letter or non-ASCII words and one side lost
  them or shows an ASCII letter in their place: "Lyman" for "Lyman α", "168 l
  288" for "168≤λ≤288". Titles that differ in one Greek letter each, α against
  β, stay different;
- they differ in at most two characters that are typical reading errors: a zero
  for the letter O ("N20" for "N2O"), a one for l or I, or one extra digit such
  as a footnote number read into a formula. A letter for another letter, H2
  against D2, is never tolerated; or
- one of them is the other followed by a separator and a name, as in a PDF
  title that appends " - Quantum Electronics, IEEE Journal of".

## Search for papers without a DOI

When no candidate is accepted, no strong candidate conflicts and the registry is
reachable, `paperextract.registry.search_bibliographic` asks Crossref's
bibliographic search, ten results per query: first with each of the first three
title candidates, then once more with the first title followed by the author line
under it, the running headers of the first pages (where journals print a
citation such as "PHYSICAL REVIEW A VOLUME 13 … 1976") and a descriptive file
name (such as `J_A_R_Samson_1994_J._Phys._B_27_887.pdf`). The extra words rank a
generic title's work first: "The Refractive Index of Air" alone does not find
Edlén's 1966 paper in the first results; with "Edlén 1966 Metrologia" it is first.

A hit is accepted only when all three checks pass against the article itself:
its title equals, or nearly equals, a title candidate, with or without its
subtitle; the first registry author's family name appears on the first two
processed pages; and its year, or its print or online year, is printed there.
Two pages, because downloaded articles often begin with a publisher's cover
page. Among several hits that pass:

- components, such as the supplementary files Crossref registers with a
  `.s001` DOI, are set aside;
- an original is set aside for its registered translation, so an article
  published in Russian and in English translation, which Crossref links with
  `is-translation-of`, is identified as the English translation, the language
  of the file; the original is recorded as an alternative;
- when several remain, those whose volume and first page the first pages or
  the file name print are kept, which separates a journal article from a
  conference version with the same title and authors.

Exactly one DOI may remain. The identity is then `VALIDATED_WITH_WARNINGS` with an
`identifier` warning, because the file names no DOI. Rejected, set-aside and
ambiguous hits are kept as alternatives. Search responses are snapshots like
lookups, so a rerun queries nothing.

## Supplementary material

A supplement shares its article's title and authors, so it would otherwise be
identified as the article. When a title candidate reads like "Supplementary
Materials for" or "Supporting Information", or the file name is a supplement's
(`SI`, `supp`, `suppl`, `sm`, `esm`, `supporting`, or starting with
`supplement`), an accepted identity is withheld: the document stays
`UNVERIFIED`, and the article is recorded as an alternative with outcome
`supplement_of`. Publish it with its paper instead:
`paperextract extract PAPER.pdf --supplement FILE`, which also adds a supplement
to a paper already in the library.

## Decision

| Situation | Status |
| --- | --- |
| Registry title matches the observed title; authors and year agree | `VALIDATED` |
| Title matches but authors or year disagree, or no observed title exists for a strong candidate | `VALIDATED_WITH_WARNINGS` |
| A strong candidate resolves to a different title | `CONFLICT` |
| Only a DOI encoded in the file name matches | `VALIDATED_WITH_WARNINGS` |
| No DOI accepted, but exactly one search hit agrees with the first pages on title, first author and year | `VALIDATED_WITH_WARNINGS` |
| A DOI the user asserted is registered | `VALIDATED_WITH_WARNINGS`, whatever the comparison says |
| The user asserted a BibTeX entry for a work no registry holds | `ASSERTED` |
| The document is supplementary material | `UNVERIFIED`, with its article recorded |
| No candidate or search hit accepted, or the registry is unreachable | `UNVERIFIED` |

Titles are compared through a normalized key (case folded, diacritics and
punctuation removed, leading article dropped); a high token overlap short of
equality is a warning, not a match. Author comparison looks for each observed
author's family name among the registry authors. Year comparison uses PDF
creation and modification years as weak hints. Every check, every rejected
alternative and the accepted registry record are kept in `metadata.json`.

## Asserting an identity

Some works are not in any registry, and some files defeat every check. You can
say what a paper is, once, and the assertion is kept with the paper and
reapplied by every later `reprocess`:

```sh
paperextract reprocess Unverified_1c17a06cda95 --doi 10.1103/PhysRevA.13.1422
paperextract reprocess Unverified_70221263c429 --bibtex report.bib
```

`--doi` looks the DOI up and accepts it when registered, as
`VALIDATED_WITH_WARNINGS` with the warning "DOI asserted by the user"; the
comparison with the article is still recorded, and a disagreement is a further
warning rather than a refusal. `--bibtex` takes a file with one entry that has at
least `author`, `title` and `year`, for a report, thesis or old article without a
DOI (an entry with a DOI is refused; assert the DOI instead):

```bibtex
@techreport{key,
  author = {Surname, A. B. and Other, C.},
  title = {Title of the report as printed},
  institution = {Issuing institute},
  number = {26},
  year = {1985}
}
```

The identity is then `ASSERTED`, a status of its own that is never reported as
validated: every field it supplies has status `ASSERTED` and source `user`, the
title and year are compared with the article's pages as evidence, and the
`identifier` check says that the user asserted it. An asserted paper is named,
catalogued and cited like a validated one; its `citation.bib` keeps the entry
type, with the institution or school, and the URL.

## What validation changes

A validated identity supplies the title, authors, journal, publisher, volume,
issue, pages or article number, year, dates, URL, ISSN, license and article type
fields, each marked with its status and source. Some older records deliver a
whole name in the family-name field, such as "D V Willetts"; a name that is only
initials followed by one capitalized surname is split into given names and family
name, and the delivered string is kept as `literal`. Anything less clear is kept
as delivered. The paper directory is named `Family_Year_FirstWords` from the
first author, the year and the first three
significant title words, folded to ASCII (letters such as ł, ø and ß are
transliterated), with a six-character digest suffix when
another source already uses that name in the library. `citation.bib` is written
and parsed back to confirm its key, required fields, DOI and year. The front
matter of `paper.md` and the catalog row carry the same values.

An unverified or conflicting identity leaves every field null, keeps the
`Unverified_<digest>` name and writes no citation. The observed title and authors
remain available as observations, clearly labelled.

## Limits

The document version is decided only from explicit evidence, so many
accepted manuscripts stay `unknown`; a DOI shared by a published article and
its accepted manuscript validates both to the same work, and the naming suffix
keeps their directories apart. arXiv's DataCite records do not name the
journal version, so an arXiv e-print is related to its published version only
through a shared DOI or the possible-duplicate rule. A paper
without a DOI stays unverified when it is not in Crossref, as for many reports,
theses and Soviet journals read in translation, when no title candidate was
extracted, or when its first pages do not show the first author and year; assert
its identity with `--bibtex`. A file-name convention of another publisher, or a
file renamed by hand, gives no candidate. Registry metadata is
taken as delivered; a registry error becomes a validated wrong value only when the
article itself agrees with it.

## Live lane

Opt-in `network` tests fetch one real Crossref record and run one real
bibliographic search when `PAPEREXTRACT_LIVE_REGISTRY=1` is set; they are never
part of the offline gate.
