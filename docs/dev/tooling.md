# Development tooling

`pyproject.toml` is the source of truth for check configuration. `uv.lock` pins
the resolved tools. CI runs the same `poe check` task as developers.

## Commands

| Purpose | Command |
| --- | --- |
| Reproduce the environment | `uv sync --locked` |
| Check lock consistency | `uv lock --check` |
| Apply fixes, then format | `uv run poe fix` |
| Complete quality gate | `uv run poe check` |
| Lint / format check | `uv run poe lint` / `uv run poe format-check` |
| Strict types | `uv run poe types` |
| NumPy-style docstrings | `uv run poe docstrings` |
| Spelling | `uv run poe spell` |
| Offline tests and branch coverage | `uv run poe test` |
| Manual with warnings as errors | `uv run poe docs` |
| Build distribution artifacts | `uv build` |

The gate does not rewrite source. This prevents CI autofixes from concealing an
uncommitted correction. `poe fix` is separate and runs lint fixes before formatting.
Inspect its diff before rerunning the gate.

Poe uses its simple executor inside the environment selected by `uv run`. This
avoids a second dependency resolution or editable rebuild for each subtask. After
the environment is installed, `uv run --offline --no-sync poe check` runs the gate
without dependency refreshes. Initial setup and dependency changes use `uv sync`.

## What is enforced

Ruff checks naming, imports, annotations, NumPy-style docstrings, path handling,
function argument count, and blanket or stale suppressions. Basedpyright runs in
strict mode and is pinned exactly. Numpydoc checks docstring sections recursively
under the source package. Codespell excludes the immutable brief, official
license text, private inputs, and generated artifacts; other prose is checked.

Pytest rejects unknown markers/configuration, treats warnings as errors,
randomizes ordering, and disables sockets through pytest-socket. Network denial
applies to the test process; subprocess isolation is an additional responsibility
when backend runners are implemented. Temporary writes must use `tmp_path`;
that policy is reviewed, not globally enforced by pytest. The current two tests
check install metadata and distribution of the typing marker. Coverage at this
stage says nothing about extraction quality.

## Test lanes

The default lane includes unit tests, small synthetic integration fixtures,
doctests, and core branch coverage with a 100% threshold. Exercise meaningful
contracts and failure paths; do not add tautological tests to chase a number.
Any narrowly justified coverage exclusion needs a reason and review.

Markers `backend`, `gpu`, `network`, and `benchmark` reserve explicit opt-in
qualification lanes. The first `backend` test runs the real MinerU worker on a
locally supplied PDF; see the [worker boundary](../worker.md) for its command
and variables. The first `network` test fetches one real Crossref record when
`PAPEREXTRACT_LIVE_REGISTRY=1` is set and re-enables sockets for itself; see the
[bibliographic identity](../identity.md) page. The `gpu` and `benchmark` lanes
have no tests yet, and no empty lane is part of the definition of done. Add runnable commands, environment recipes, and CI jobs
when those tests arrive. Live-service tests will explicitly re-enable
sockets; they must never be folded into the offline gate.

Backend upgrades will require adapter-contract tests and a scientific regression
report on the supplied private corpus. Store reviewed gold values separately
from raw backend output; do not regenerate expected results blindly. GPU jobs
and full private-corpus runs are not prerequisites for a documentation edit.

## Dependency and distribution policy

Use `uv add` / `uv add --dev` when a dependency is needed and authorized by the
task; let uv update the lockfile. Document its purpose and license. Discuss new
heavyweight runtimes, services, or restrictive licenses before adoption. The
bootstrap intentionally has no runtime dependencies and no backend extras.

Keep backend environments separate from the core to isolate ML dependency and
Python-version constraints. No numerical kernel tooling, array stubs, distributed
scheduler, or GPU framework is needed in the development baseline.

Versions come from Git via hatch-vcs, with `0.0.0` as the fallback when version
information is unavailable. Source distributions retain build metadata for wheel
rebuilds outside Git. Agents must not create tags or releases. CI builds artifacts
and imports the installed wheel from a temporary directory outside the checkout.
