# Worked example

This page follows one real paper through paperextract and compares what went
in with what came out. The results are frozen in the repository under
[`examples/geib-2019-copra/`](https://github.com/jtravs/paperextract/tree/main/examples/geib-2019-copra):
browse them there, or clone the repository and open the files side by side.
Nothing here is recomputed when the manual is built.

The paper is N. C. Geib, M. Zilk, T. Pertsch and F. Eilenberger, "Common pulse
retrieval algorithm: a fast and universal method to retrieve ultrashort
pulses," *Optica* **6**, 495–505 (2019),
[doi:10.1364/OPTICA.6.000495](https://doi.org/10.1364/OPTICA.6.000495),
© 2019 Optical Society of America under the terms of the
[OSA Open Access Publishing Agreement](https://doi.org/10.1364/OA_License_v1)
(non-commercial reuse with attribution). It is a good test: 11 pages, 28
numbered equations, four tables whose cells are mathematics, eight
multi-panel figures, and a PDF text layer that loses some mathematical
glyphs.

## Running it

On an Apple silicon Mac, with the MinerU worker and its models installed as
in [getting started](usage.md):

```sh
uv run paperextract optica-6-4-495.pdf --library library
```

```text
INFO MinerU engine auto: llama-cpp with onnx small models
INFO [1/1] optica-6-4-495.pdf
INFO Worker request 20260924T110345Z-59efb510b214-33b48e completed with 56 files
INFO Worker request 20260924T110345Z-59efb510b214-33b48e-ocr completed with 20 files
INFO Resolving bibliographic identity
INFO [1/1] published
published  optica-6-4-495.pdf  Geib_2019_CommonPulseRetrieval  VALIDATED  COMPLETE  19 findings
```

It took three minutes. The second worker request is a targeted OCR pass over
the tables whose text layer lost glyphs; more on that below. The result is
one folder:

```text
Geib_2019_CommonPulseRetrieval/
├── paper.md          the paper as Markdown with LaTeX equations
├── document.json     every block with its page and position
├── equations/        28 crops, one per display equation
├── figures/          8 figures, 20 panels, as images
├── tables/           4 tables as HTML, JSON cells, CSV and a crop
├── descriptions/     machine descriptions of the figures (optional, below)
├── citation.bib      the validated reference
├── metadata.json     every bibliographic field with its source
├── validation.json   checks and findings
├── manifest.json     SHA-256 of every file
├── diagnostics/      review.md and the backend's raw output
└── original/         optica-6-4-495.pdf, byte for byte
```

## Identity

The folder name, `citation.bib` and the front matter of `paper.md` come from
the registry, not from guessing at the first page. paperextract read the DOI
printed in the PDF, fetched the Crossref record and checked that its title and
authors agree with the article before accepting it:

```bibtex
@article{geib2019common,
  author = {Geib, Nils C. and Zilk, Matthias and Pertsch, Thomas and Eilenberger, Falk},
  title = {Common pulse retrieval algorithm: a fast and universal method to retrieve ultrashort pulses},
  journal = {Optica},
  year = {2019},
  volume = {6},
  number = {4},
  pages = {495},
  doi = {10.1364/optica.6.000495},
  url = {https://doi.org/10.1364/optica.6.000495}
}
```

`metadata.json` records that this is the version of record, with the
evidence: the file prints the validated DOI and names the journal. The
article number is left `UNVERIFIED` because nothing supplied one; unknown is
a valid answer.

## Equations

Each display equation becomes LaTeX in `paper.md` and `document.json`, with
its number and a crop of the page so the two can be compared. Equation (1)
on page 2 as printed:

```{image} ../examples/geib-2019-copra/Geib_2019_CommonPulseRetrieval/equations/eq_488a4a970963.jpg
:alt: Equation 1 as printed in the PDF
```

and as extracted:

```latex
\tilde {E} (\omega) = \mathcal {F} [ E ] (\omega) = \frac {1}{2 \pi}
\int_ {- \infty} ^ {\infty} E (t) \mathrm{e} ^ {\mathrm{i} \omega t} \mathrm{d} t,\tag{1}
```

rendered from that LaTeX:

```{math}
\tilde {E} (\omega) = \mathcal {F} [ E ] (\omega) = \frac {1}{2 \pi} \int_ {- \infty} ^ {\infty} E (t) \mathrm{e} ^ {\mathrm{i} \omega t} \mathrm{d} t,\tag{1}
```

The block in `document.json` keeps the page and the bounding box, in points
and as a fraction of the page, so any equation can be traced back
(abbreviated):

```text
{"id": "eq_488a4a970963", "kind": "equation", "label": "1",
 "latex": "\\tilde {E} (\\omega) = … \\tag{1}",
 "span": {"page": 2, "bbox_pt": [360.47, 335.02, 567.32, 361.94], …}}
```

Display equations are reliable in this paper. Inline mathematics is weaker:
the sentence introducing Eq. (1) reads, in `paper.md`,
`$\tilde { \tilde { E } } ( \omega ) \tilde { }$` for the printed
{math}`\tilde{E}(\omega)`. That is the kind of error the
[known limitations](limitations.md) warn about. paperextract does not try to
correct it, because it cannot know the correct text.

## Tables

Table 1 lists signal operators, and every cell in its second column is an
expression. As printed:

```{image} ../examples/geib-2019-copra/Geib_2019_CommonPulseRetrieval/tables/tbl_b093eada9fc8.jpg
:alt: Table 1 as printed in the PDF
```

The PDF's text layer cannot represent it: the brackets and tildes are font
glyphs with no Unicode mapping, so the first extraction of the SHG-FROG cell
reads `F-1\u0005eiτωE˜\u0006F-1\u0005E˜\u0006` (control characters
shown as escapes). paperextract noticed the
unmapped glyphs, re-read the table image with OCR, checked that **all 7
numbers agree** with the text layer, and only then used the OCR body. It says
so in the findings:

> `TABLE_OCR_SELECTED` page 2: The body comes from an OCR re-extraction
> because the text layer lost glyphs; all 7 numbers agree with the text
> layer. Grid 5x2 became 4x2; the text-layer body is kept as an alternative.

The result, rendered from the extracted cells:

```{list-table}
:header-rows: 1

* - Method
  - {math}`\mathcal{S}_{\tau}[\tilde{E}]`
* - SHG-FROG
  - {math}`\mathcal{F}^{-1}[e^{i\tau\omega}\tilde{E}]\mathcal{F}^{-1}[\tilde{E}]`
* - PG-FROG
  - {math}`|\mathcal{F}^{-1}[e^{i\tau\omega}\tilde{E}]|^{2}\mathcal{F}^{-1}[\tilde{E}]`
* - TDP{math}`^{b}`
  - {math}`\mathcal{F}^{-1}[e^{i\tau\omega}\tilde{B}(\omega)\tilde{E}]\mathcal{F}^{-1}[\tilde{E}]`
```

The same table is in `tables/` as HTML, as a CSV and as JSON cells with row
and column positions; this is the SHG-FROG cell in
`tables/tbl_b093eada9fc8.json`, whose `alternatives` still hold the garbled
text-layer body rather than discarding it:

```json
{"row": 1, "column": 1, "text": "\\mathcal{F}^{-1}[e^{i\\tau\\omega}\\tilde{E}]\\mathcal{F}^{-1}[\\tilde{E}]",
 "html": "<eq>\\mathcal{F}^{-1}[e^{i\\tau\\omega}\\tilde{E}]\\mathcal{F}^{-1}[\\tilde{E}]</eq>"}
```

The table notes survive too, with their link to the supplement. Footnote
markers inside cells are not always separated: Table 2 has `d-Scanb` for
d-Scan with note *b*.

## Figures

Each figure keeps its published caption, its panels as separate images, and
the whole figure as one image. Figure 1 in `paper.md`:

```{image} ../examples/geib-2019-copra/Geib_2019_CommonPulseRetrieval/figures/fig_2dc2d412920d.png
:alt: Figure 1, a diagram of the COPRA algorithm
```

> **Published caption:** Fig. 1. (a) Diagram of the discrete PNPS formalism.
> (b) First stage of COPRA: local iteration. (c) Second stage of COPRA:
> global iteration.

Figure descriptions are optional and come from a separate step. Here the
local Qwen3.8-27B model described all eight figures on the Mac, about four
minutes each:

```sh
uv run paperextract describe --backend mlx --all --library library
```

The description is added below the published caption, labelled as machine
output and fenced by comments so it can never be mistaken for the paper. The
opening of the one for Figure 1:

> *Machine-generated visual description (Qwen3.8-27B), not from the paper:*
> The figure presents a three-part schematic illustrating the discrete PNPS
> formalism and the two stages of the COPRA algorithm. […]
> - (a) flowchart; A green rounded box labeled 'S_δm\[·\](t_k)' receives an
>   input arrow from 'non-collinear'. […] Inputs 'Ẽ_n' and 'ω_n' point into
>   the blue box.; An arrow connects the green box to the blue box. […]
> - (c) flowchart; […] The variable 'r' is defined by the equation
>   'r = Σ_mn (T̃_mn^meas - μT̃_mn)²'. […]

Compare it with the figure. The labels and the equation in panel (c) are
read correctly, but panel (a) has no arrow from the green box to the blue
one, and Ẽ<sub>n</sub> points into both boxes, not only the blue one. This is
why descriptions are labelled and kept apart from the text. Each one is
stored in `descriptions/` with the model's repository and revision, the
prompt, the sampling settings, the SHA-256 of the image it saw and the raw
answer. It also records a printed-string check, which looks up the strings
the model claims against the PDF text inside the figure. The check is marked
not applicable here, because these figures are images with no text layer, so
nothing could confirm the readings.

## What it could not verify

Nothing in the folder is marked as checked unless it was. `validation.json`
lists the checks, and `diagnostics/review.md` lists the 19 findings for a
reader:

| Check | Outcome |
| --- | --- |
| Page coverage, 11 of 11 | pass |
| Table grids | pass |
| Equation delimiters balanced | pass |
| Assets present | pass |
| Bibliographic identity | pass |
| Figure captions | review: two images on page 8 have no caption |
| Cross-source comparison | not checked: a single source |

The warnings are mostly `UNMAPPED_GLYPHS`, one for each block where the text
layer lost characters, such as the `�` in "uniformly distributed on
�−0.1π, 0.1π�" where the PDF printed brackets. Each names the block, so you
know which passages to compare with the page. The informational findings
explain heuristic decisions, such as which panels were grouped under a
caption and which repeated page header was left out of the text.

## Finding it again

The library is searchable straight away, and the original file is
recognized by its bytes:

```console
$ uv run paperextract search "retrieval algorithm d-scan" --library library
Geib_2019_CommonPulseRetrieval	2019	Common pulse retrieval algorithm: a fast and universal method to retrieve ultrashort pulses
	…We call it the common pulse [retrieval] [algorithm] (COPRA). Once the [algorithm]…

$ uv run paperextract lookup --file optica-6-4-495.pdf --library library
present	library/Geib_2019_CommonPulseRetrieval	identical bytes	optica-6-4-495.pdf

$ uv run paperextract lookup --doi 10.1364/OPTICA.6.000495 --library library
present	library/Geib_2019_CommonPulseRetrieval	validated DOI 10.1364/optica.6.000495	10.1364/OPTICA.6.000495
```

## How it was made

On 24 September 2026, on an Apple silicon Mac, with paperextract at commit
`b7ba984` (just after 0.1.0, so the records report `0.1.1.dev1`), MinerU 4.0.5
with llama.cpp and ONNX small models, and Qwen3.8-27B through the MLX worker
for the descriptions. Extraction took 3 minutes and the descriptions 32.
The run was made from a checkout under
`/Users/Shared/paperextract-example/`, and the records name those paths.
