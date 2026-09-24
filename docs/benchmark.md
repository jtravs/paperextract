# Benchmark validation and comparison

The first benchmark API validates local inputs and compares **explicitly aligned**
scientific objects. It does not extract PDFs, infer alignments, rank backends, or
promote candidate annotations to reviewed gold. It has no runtime dependencies
and makes no network requests.

## Manifest version 1

Store a UTF-8 JSON document with exactly four fields:

- `schema`: `"paperextract.benchmark"`.
- `schema_version`: `"1"`, a string. Other versions require explicit migration.
- `sources`: a nonempty array of PDF source records.
- `references`: an array of reference records; it may be empty for an inventory.

Each source has exactly `id`, `work_id`, `version_id`, `path`, `sha256`, `pages`,
and `split`. IDs and paths are nonempty strings. Paths are canonical relative POSIX
paths under the separately supplied corpus root; traversal, absolute paths and
symlinks escaping that root are rejected. SHA-256 is a lowercase hexadecimal digest.
`pages` is a positive integer; booleans are rejected. `split` is `development` or
`holdout`. Page counts are declarations: validation hashes bytes but does not parse
PDFs to verify page counts. A backend run must independently check page coverage.

Each reference has exactly these fields:

| Field | Meaning |
| --- | --- |
| `id` | Unique nonempty annotation identifier. |
| `source_id`, `version_id`, `source_sha256` | Exact source scope; must agree with the manifest source. |
| `page` | One-based PDF page, within the declared source page count. |
| `locator` | Nonempty object alignment key, such as `table:S1/row:230/column:2`. |
| `kind` | `table_cell` or `equation`. |
| `expected` | Raw cell string or reference TeX; an empty string is an explicit blank cell. |
| `header_path` | Nonempty array of nonempty header/unit strings for a cell; empty for equations. |
| `review` | `candidate` or `reviewed`; the loader does not grant scientific approval. |
| `provenance` | Nonempty reviewer/date/evidence description, including source locators and limitations. |

Unknown fields, duplicate JSON keys, duplicate source IDs/paths, duplicate reference
IDs and unknown source references fail validation. Errors raise `ValueError`;
unreadable inputs raise `OSError` subclasses. The returned immutable `Benchmark`
contains the digest of the exact manifest bytes, binding inputs and annotations to
a run. This schema is separate from the earlier private inspection draft: convert
explicitly, retain the draft and its provenance, and do not silently reinterpret it.

## Use the API

```python
from pathlib import Path
from paperextract.benchmark import Observation, compare, load_benchmark

benchmark = load_benchmark(Path("benchmark.json"), Path("/local/corpus"))
reference = benchmark.references[0]
```

Construct `Observation(source, page, locator, text, header_path)` from the actual
run's source identity and independently established object alignment. `source` is
a `Source` record. Do not copy identity, locators or header values from the reference
to make a match: wrong alignment would invalidate the comparison. Then call
`compare(reference, observation)`.

The result is one of the following, checked in this order:

1. `scope_mismatch`: source record, PDF page or object locator differs.
2. `missing`: observed text is `None`. This differs from an explicit empty cell.
3. `header_mismatch`: table context differs, including units or unresolved headers.
4. `exact`: raw strings match exactly.
5. `normalized`: equations alone match after CRLF-to-LF conversion and trimming
   outer ASCII whitespace. Raw evidence remains unchanged.
6. `mismatch`: the remaining strings differ.

Cells are never parsed as floats or stripped. `2.0` differs from `2`; a blank
differs from zero, a dash and a missing observation. Equation normalization keeps
internal whitespace, braces, commands, Unicode, signs, indices, powers, punctuation
and delimiters. It does not expand macros, remove math delimiters, rewrite symbols
or establish mathematical equivalence. Equivalent expressions can therefore require
manual review. Broader token normalization remains a separately reviewed follow-up.

Always retain the reference's review state beside the result. A candidate match
is useful diagnostic evidence, not a reviewed accuracy score. Report individual
outcomes and unresolved cases; this initial API intentionally offers no aggregate
leaderboard or automatic gold promotion. Header topology, footnotes, figure claims,
reading order and inline-math coverage still require further annotations/evaluators.

## Reproducible trials

Keep manifests, source papers, raw backend outputs and model caches private. Recheck
hashes immediately before extraction; validation does not lock external files.
Use isolated worker environments with exact dependency and model revisions. Record
setup/download separately from fresh-process, cached-model and reused-worker runs.

Other work on the measuring machine distorts timings. Label such runs as
contention-affected, record available system load and memory context, and use them
for feasibility and fidelity inspection. Repeat measurements on a quieter machine
before choosing defaults from latency rankings. Process RSS is not total unified
memory; unavailable accelerator or process-tree metrics must remain unknown.
