# paperextract

[![Check](https://github.com/jtravs/paperextract/actions/workflows/check.yml/badge.svg)](https://github.com/jtravs/paperextract/actions/workflows/check.yml)
[![Coverage](https://codecov.io/gh/jtravs/paperextract/graph/badge.svg)](https://codecov.io/gh/jtravs/paperextract)
[![Docs](https://readthedocs.org/projects/paperextract/badge/?version=latest)](https://paperextract.readthedocs.io)
[![PyPI](https://img.shields.io/pypi/v/paperextract)](https://pypi.org/project/paperextract/)
[![Python](https://img.shields.io/pypi/pyversions/paperextract)](https://pypi.org/project/paperextract/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![basedpyright](https://img.shields.io/badge/types-basedpyright-blue)](https://docs.basedpyright.com)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/jtravs/paperextract/blob/main/LICENSE)

**Turn a folder of scientific PDFs into a searchable, citable, machine-readable
library, locally, without losing the equations, tables or figures.**

paperextract is built for technical literature, where a misread exponent or a
shifted table cell matters. It runs state-of-the-art document models on your
own machine or cluster, keeps every result traceable to the page it came from,
and tells you what it could not verify instead of guessing.

```text
paper.pdf  ──►  Travers_2019_HighEnergyPulse/
                ├── paper.md          Markdown with LaTeX equations, tables, figures
                ├── document.json     every block with its page, position and source
                ├── figures/ tables/  crops, captions, tables as HTML, JSON and CSV
                ├── citation.bib      identity validated against Crossref or DataCite
                ├── validation.json   what could not be verified
                └── original/         your PDF, byte for byte
```

## Features

- **Equations, tables and figures as data.** Display and inline math as
  LaTeX, table cells exactly as printed, figures with their panels and
  captions, supplements linked to the main text.
- **Nothing invented.** Values are never repaired or filled in; original
  files are preserved with hashes; every doubt is recorded as a finding.
- **Real bibliographic identity.** DOIs, titles and authors checked against
  Crossref, DataCite and arXiv, with BibTeX, clear names, and document
  versions (preprint, accepted manuscript, published) kept apart.
- **Duplicates caught before extraction.** Identical files, copies with the
  same text and shared DOIs are found before any model runs.
- **A library you can search and share.** Full-text search, lookup by DOI or
  file, `library.bib`, portable folders with relative links, and an agent
  skill so AI assistants can find and cite your papers.
- **Figure descriptions.** Optional, clearly labelled machine descriptions of
  every figure from a local vision-language model or the Claude API.
- **From laptop to cluster.** MinerU, Docling or Marker in isolated
  environments on a Mac or Linux; GPU batches on Slurm clusters, resumable
  and published in one step. 162 papers took under an hour on four GPUs.

## Install

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/) and
macOS on Apple silicon or Linux on x86_64.

```sh
git clone https://github.com/jtravs/paperextract.git && cd paperextract
uv sync --locked
uv sync --directory workers/mineru --locked       # add --extra cuda on an NVIDIA GPU
uv run paperextract models fetch mineru           # or mineru-cuda; pinned and verified
```

## Use

```sh
uv run paperextract paper.pdf --library ~/papers          # one paper
uv run paperextract batch incoming/ --library ~/papers    # a folder, duplicates handled
uv run paperextract search "soliton self-compression" --library ~/papers
uv run paperextract describe --all --library ~/papers     # optional figure descriptions
```

## Documentation

The manual is at **[paperextract.readthedocs.io](https://paperextract.readthedocs.io)**:

- [Getting started](https://paperextract.readthedocs.io/en/latest/usage.html): installation, a first extraction and what it produces
- [Worked example](https://paperextract.readthedocs.io/en/latest/tutorial.html): a real paper followed from PDF to library, with the frozen input and output in [`examples/`](https://github.com/jtravs/paperextract/tree/main/examples)
- [Command line](https://paperextract.readthedocs.io/en/latest/cli.html): every command and setting
- [Running on a Slurm cluster](https://paperextract.readthedocs.io/en/latest/cluster.html): GPU batches
- [Figure descriptions](https://paperextract.readthedocs.io/en/latest/describe.html)
- [Known limitations](https://paperextract.readthedocs.io/en/latest/limitations.html): read before relying on a result
- [Model licences](https://paperextract.readthedocs.io/en/latest/models.html): the terms of each backend's models

To build it locally, run `uv run poe docs` and open
`docs/_build/html/index.html`.

## Status

paperextract is alpha software. The pipeline is complete and has been run on
about 170 papers on macOS and on NVIDIA A40 GPUs; accuracy has not yet been
measured against a reviewed reference set. See the
[changelog](https://github.com/jtravs/paperextract/blob/main/CHANGELOG.md).

## Contributing

```sh
uv sync --locked
uv run poe check      # lint, types, docstrings, spelling, tests, manual
```

The default test suite is offline and needs no models. See
[AGENTS.md](https://github.com/jtravs/paperextract/blob/main/AGENTS.md) for conventions and [tooling](https://paperextract.readthedocs.io/en/latest/dev/tooling.html) for
the checks.

## Licence

Apache License 2.0; see [LICENSE](https://github.com/jtravs/paperextract/blob/main/LICENSE) and [NOTICE](https://github.com/jtravs/paperextract/blob/main/NOTICE). paperextract
distributes no model weights: each backend's models keep their own licences
(summarized in [model licences](https://paperextract.readthedocs.io/en/latest/models.html)), and papers keep their
publishers' copyright.
