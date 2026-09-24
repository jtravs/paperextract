# AGENTS.md

Working instructions for agents and contributors to **paperextract**.

## Scope and authority

Read `README.md`, `CHANGELOG.md` and the manual under `docs/` for the implemented
surface before changing behavior. The maintainer keeps the design notes, the
implementation tracker and private evidence outside this repository; when one is
provided for the session, read it first and keep it current. Do not turn proposed
commands into claims of existing features.

Follow the user's task scope. Resolve ordinary reversible implementation details
using the accepted design and existing patterns. Ask about material ambiguity,
scientific policy, incompatible requirements, or new external commitments;
do not stop for every routine judgment. Record assumptions and unresolved choices.

`pyproject.toml` owns automated configuration. If these instructions disagree with
it, report and fix the discrepancy. Do not claim a policy is enforced unless it
actually is. See `docs/dev/tooling.md` for the distinction.

## Project invariants

- Scientific fidelity, especially table values and equations, comes before speed.
- Supplied sources are immutable evidence. Preserve their bytes and hashes.
- A paper may have multiple representations; work identity and document version
  are different concepts. Never merge versions or papers on a fuzzy title alone.
- Keep extraction backends behind adapters. Canonical schemas belong to this
  project and must not depend on a backend's Python classes.
- Preserve raw values, source spans, alternatives, and selection reasons. Unknown
  is a valid state. Model confidence is not proof of transcription accuracy.
- Never invent missing prose or metadata, silently repair equations, or rewrite
  numerical cells. Label generated descriptions and keep published captions.
- Default extraction stays local. Network access, model downloads, remote AI,
  and paid compute must obey the explicit execution policy and user authorization.
- Private papers, credentials, model weights, and generated corpora do not belong
  in Git. Use ignored local data directories and redistributable synthetic fixtures.
- The repository will be public. Keep it free of site-specific details: host
  names, user names, accounts, quotas, local paths, job scripts for a particular
  cluster, and credential locations belong in the maintainer's private site
  guide outside the checkout. Describe hardware and results generically.

## Commands and definition of done

| Purpose | Command |
| --- | --- |
| Sync locked environment | `uv sync --locked` |
| Check lock consistency | `uv lock --check` |
| Fix lint and format | `uv run poe fix` |
| Full offline gate | `uv run poe check` |
| Types / docstrings / spelling | `uv run poe types` / `uv run poe docstrings` / `uv run poe spell` |
| Tests with coverage | `uv run poe test` |
| Build manual | `uv run poe docs` |
| Build wheel and source archive | `uv build` |

Run the full gate before declaring a change complete. It checks lint, formatting,
strict types, docstrings, spelling, offline tests with coverage, and the Sphinx
manual, in that order. It does not edit source. CI invokes this same task.
Run `poe fix` separately when needed and inspect the diff. Check the lock after
dependency changes and build artifacts after packaging changes.

Do not suppress a failing check to obtain a green result. If a check cannot run,
state exactly what was unavailable and what remains unverified. Do not present a
documentation review as a measured backend benchmark. Real-backend qualification
is additionally required when changing an adapter or its pinned environment,
once the corresponding lane exists. Do not require nonexistent slow/GPU lanes.

## Environment and dependencies

- Use uv and `uv run`; never manually edit `uv.lock` or install into `.venv` by hand.
- The core baseline is Python 3.12. Backend workers may use separately pinned
  versions. Do not add every backend to the core environment.
- Every dependency must solve a named failure mode. Use `uv add` for authorized
  additions, discuss new heavy/service/restrictively licensed dependencies, and
  keep licensing and environment documentation current.
- Basedpyright is pinned exactly; change that pin deliberately and assess the
  resulting diagnostics. Other resolved tool versions are fixed in `uv.lock`.
- Package versions come from Git. Do not edit a version string, tag, or release.

## Code and interfaces

- Prefer small, single-purpose functions with explicit inputs and outputs. Keep
  parsing/reconciliation pure where practical and I/O at the boundaries.
- Use explicit public types, `Sequence`/`Mapping` for read-only inputs, `X | None`,
  built-in generics, f-strings, and `pathlib.Path`. Avoid `Any`; confine unavoidable
  untyped third-party data to an adapter with a specific explanation.
- Use frozen dataclasses for internal value objects. Validate serialized input at
  boundaries; do not duplicate the same schema in independent class hierarchies.
- Declare `__all__` in public modules. Keep imports absolute and side-effect free;
  importing the package must not load models, access the network, or configure logging.
- Follow Ruff. Suppressions must be narrow and explain why; fix causes first.
- Keep persisted schemas versioned. Define migration and compatibility behavior
  alongside changes; never reinterpret old data silently.
- Comments explain non-obvious reasons and tradeoffs. Mark deliberate follow-ups
  `TODO(context): ...`; avoid narrating obvious code.

## Errors, logs, and recovery

Use standard exceptions for ordinary Python contract violations and a small
project exception hierarchy for domain failures when implemented. Never catch
broad exceptions merely to hide a failure. Batch boundaries may catch and record
one paper's failure so other papers continue. Preserve useful stage output.

Use module loggers. Only CLI entry points configure handlers and levels; no
`print` in library code. Redact credentials and signed URL tokens. Write machine
output to stdout and progress to stderr. Model warnings as structured findings
when they belong in the artifact, rather than losing them in logs.

## Tests

- Use pytest under `tests/`, mirroring the package. Name tests by behavior and
  use parametrization for related cases. Bug fixes need regression coverage.
- Test normal, boundary, and failure behavior. Prefer scientific invariants over
  brittle whole-Markdown snapshots: exact cell strings, equation tokens, source
  coordinates, reading order, identity conflicts, and caption associations.
- Tests are isolated: temporary writes use `tmp_path`, network responses are
  fixtures, random data is seeded, and model weights are never downloaded implicitly.
- The default suite disables sockets, randomizes order, and errors on warnings.
  Assert expected warnings explicitly. Subprocesses require separate controls.
- Core branch coverage targets 100%; exclusions need a specific reason. Coverage
  does not replace quality measurements or justify tests that mirror implementation.
- Keep real-backend, GPU, live-network, and benchmark tests explicitly opt-in.
  Document and add each lane when it becomes runnable. Add private corpus cases
  only with provenance and review permission; never commit publisher PDFs by default.

## Documentation

Public modules, classes, and functions need NumPy-style docstrings. Use a concise
summary and applicable Parameters, Returns, Raises, and Examples sections.
Frozen dataclass fields belong under Attributes. Avoid documentation that merely
repeats implementation details.

Describe user-visible functionality in `docs/`, as well as in docstrings. Keep
`README.md`, `docs/usage.md`, and `CHANGELOG.md` honest and current. The manual
builds offline with warnings and missing references treated as errors. Research
claims need dated primary sources; benchmark claims need measured evidence.

## Git and reporting

Record user-visible changes under `[Unreleased]` in `CHANGELOG.md`. When the
maintainer supplies a private tracker, update it at meaningful checkpoints, before
an agent handoff, and before the final response of every work session: the current
milestone, completed and unfinished work, exact verification results, decisions,
blockers, artifact paths, and the next concrete action. Mark checks as pending until
actually run. Keep private tracker contents, corpus notes and paper contents out of
the repository.

Preserve unrelated user changes. Stage deliberately by path; never `git add .` or
`git add -A`. Do not commit unless requested. Never push, tag, release, rewrite
history, discard uncommitted work, or run destructive cleanup. Fetch, pull,
branch, and merge only when explicitly instructed.

Report what changed, why, which checks actually passed, and material limitations.
Keep plans and implemented behavior clearly distinguished.
