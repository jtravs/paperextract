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

## Search for papers without a DOI

When no candidate is accepted, no strong candidate conflicts and the registry is
reachable, `paperextract.registry.search_bibliographic` asks Crossref's
bibliographic search for the observed title. A hit is accepted only when all
three checks pass against the article itself: its normalized title equals the
observed title, with or without its subtitle; the first registry author's family
name appears on the first page; and its year, or its print or online year, is
printed on the first page. Exactly one DOI may pass. The identity is then
`VALIDATED_WITH_WARNINGS` with an `identifier` warning, because the file names no
DOI. Rejected and ambiguous hits are kept as alternatives. Search responses are
snapshots like lookups, so a rerun queries nothing.

The observed title skips PDF information titles that name an identifier or an
authoring file, such as `PII: …` or `Microsoft Word - …`, and flattens inline math
in a heading, so `H$_2$` is compared as `H2`. Titles also match when their keys
agree with spaces removed, because Crossref drops the spaces around MathML in some
titles.

## Decision

| Situation | Status |
| --- | --- |
| Registry title matches the observed title; authors and year agree | `VALIDATED` |
| Title matches but authors or year disagree, or no observed title exists for a strong candidate | `VALIDATED_WITH_WARNINGS` |
| A strong candidate resolves to a different title | `CONFLICT` |
| No DOI accepted, but exactly one search hit agrees with the first page on title, first author and year | `VALIDATED_WITH_WARNINGS` |
| No candidate or search hit accepted, or the registry is unreachable | `UNVERIFIED` |

Titles are compared through a normalized key (case folded, diacritics and
punctuation removed, leading article dropped); a high token overlap short of
equality is a warning, not a match. Author comparison looks for each observed
author's family name among the registry authors. Year comparison uses PDF
creation and modification years as weak hints. Every check, every rejected
alternative and the accepted registry record are kept in `metadata.json`.

## What validation changes

A validated identity supplies the title, authors, journal, publisher, volume,
issue, pages or article number, year, dates, URL, ISSN, license and article type
fields, each marked with its status and source. The paper directory is named
`Family_Year_FirstWords` from the first author, the year and the first three
significant title words, folded to ASCII, with a six-character digest suffix when
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
without a DOI stays unverified when it is not in Crossref, as for many Soviet
journals read in translation, when its title was not extracted, or when its first
page does not show the first author and year. Registry metadata is
taken as delivered; a registry error becomes a validated wrong value only when the
article itself agrees with it.

## Live lane

Opt-in `network` tests fetch one real Crossref record and run one real
bibliographic search when `PAPEREXTRACT_LIVE_REGISTRY=1` is set; they are never
part of the offline gate.
