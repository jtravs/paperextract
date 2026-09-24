# paperextract

**Turn a folder of scientific PDFs into a searchable, citable,
machine-readable library, locally, without losing the equations, tables or
figures.**

paperextract runs document models on your own machine or cluster, keeps
every result traceable to the page it came from, and reports what it could
not verify instead of guessing.

```sh
uv run paperextract paper.pdf --library ~/papers
uv run paperextract search "soliton self-compression" --library ~/papers
```

## What you get

- **Equations, tables and figures as data**: LaTeX math, exact table cells
  as HTML, JSON and CSV, figures with panels and captions, supplements linked
  to the main text.
- **Nothing invented**: original files kept with hashes, values never
  repaired, every doubt recorded as a finding.
- **Validated identity**: DOIs, titles and authors checked against
  Crossref, DataCite and arXiv, with BibTeX and document versions.
- **Duplicates caught first**: identical files, same-text copies and shared
  DOIs found before any model runs.
- **A searchable, portable library**: full-text search, lookup, `library.bib`
  and an agent skill for AI assistants.
- **Figure descriptions**: optional, labelled machine descriptions.
- **Laptop to cluster**: MinerU, Docling or Marker on a Mac or Linux, and GPU
  batches on Slurm.

## Where next

- [Getting started](usage.md): install, extract a first paper, see what it produces.
- [Worked example](tutorial.md): one real paper, input against output.
- [Command line](cli.md): every command and setting.
- [Running on a Slurm cluster](cluster.md): GPU batches.
- [Known limitations](limitations.md): read before relying on a result.

```{toctree}
:hidden:

usage
tutorial
guide
reference
development
```
