# Source preservation

The first P2 primitive preserves a PDF before extraction:

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from paperextract.ingest import preserve_pdf

with TemporaryDirectory() as staging:
    artifact = preserve_pdf(Path("paper.pdf"), Path(staging) / "source.pdf")
    # Use artifact.stored_path within this staging lifetime.
    # artifact.sha256 and artifact.size_bytes identify the preserved bytes.
```

Choose a private staging directory whose parent already exists. The destination
must be new: an existing file, directory, symlink or the original itself raises
`FileExistsError` without being overwritten. The copy has independent bytes, so
later edits to the user's file do not modify preserved evidence.

Preservation checks the PDF signature, streams a copy and SHA-256, flushes it,
then reads the source again. A changed digest, file identity or metadata raises
`SourceChangedError`. Failures and Python interruptions remove only the new
partial file owned by the call. This detects ordinary concurrent modifications;
it does not lock the original against another application. Abrupt termination
can leave a staging file for the caller to recover.

The returned frozen `SourceArtifact` includes the digest, byte size, original
basename and staging path. That local path is a runtime handle, not a portable
bundle reference. Source/work/version schemas and publication transactions follow
in P2. Signature checking alone does not establish that a PDF is readable,
unencrypted or complete; the extraction worker must check those properties.

This API neither runs a model nor publishes a paper directory. The full extraction
workflow and command line remain under implementation.

## Planning a batch before extraction

`plan_intake` hashes every supplied file, groups byte-identical copies, and
fingerprints each distinct source, all without copying or modifying anything:

```python
from pathlib import Path

from paperextract.ingest import discover_pdfs, plan_intake

paths = discover_pdfs(Path("incoming"))  # top-level *.pdf only
plan = plan_intake(paths, known={})  # known: digest -> library reference
for group in plan.to_extract():
    print(group.primary.path, [alias.path.name for alias in group.aliases])
Path("intake-plan.json").write_text(plan.to_json())
```

Each `SourceGroup` has one deterministic primary (the first path in
lexicographic order), the aliases that share its bytes, the reference under
which a library already holds the digest if the caller supplied one, and a
`ContentFingerprint`. Identical bytes are therefore never extracted twice, and
every extra filename stays in provenance. Paths that are not regular files,
lack the `%PDF-` signature or cannot be opened by PDFium are listed under
`rejected` with the reason; a group whose primary is unreadable is rejected
as a whole because its aliases are the same bytes.

The fingerprint, from `paperextract.pdf.fingerprint_pdf`, records the page
count, a digest of each page's text after NFC normalization and whitespace
collapsing, a whole-document text digest, the non-empty PDF information
entries, and DOI and arXiv strings found in the information dictionary and the
first two pages. Identical page digests across different bytes are the signal
for re-downloaded copies of the same PDF; download stamps are not removed yet,
so copies that differ only by a stamp still differ. Identifier strings are
candidates for the identity stage, never validated identity. A scan without a
text layer fingerprints as empty pages, which is recorded rather than hidden.

The `known` mapping is how a library reports digests it already holds; the
command line fills it from the library catalog.

## Content-equivalent copies and shared identifiers

`content_equivalents(plan, known_text)` finds the second duplicate tier: a
group whose whole-text digest equals that of an earlier group, or of a library
paper supplied as text digest to directory. The digest covers the page count
and every page's normalized text, and needs at least
`MIN_EQUIVALENT_CHARACTERS` characters, so short covers and textless scans do
not match. `shared_doi_candidates(plan, held)` lists groups whose first DOI
candidate is the same string, for review only; groups already held for
identical text are left out. `pair_supplements(plan, exclude)` treats a file
as a supplement only when `looks_like_supplement` accepts its name, then pairs
it with the unique paper sharing a DOI candidate, or else the unique paper
whose file name shares the longest start of at least `MIN_PAIRING_PREFIX`
characters; the rest are unpaired. `dedup_report` combines the plan with these
results into a versioned `paperextract.dedup-report` document, which is what
`paperextract dedup --report` writes. Fuzzy bibliographic matching remains
planned.
