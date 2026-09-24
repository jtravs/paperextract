# Saved article pages

A publisher's web page of an article is a second rendering of the same paper.
paperextract keeps a saved page with the paper and compares it with the PDF
extraction, to confirm what the PDF backend read and to show what it missed.
The page is never merged into `paper.md` or `document.json`: the PDF stays the
paper, and the comparison is a report for review.

## Supplying a page

Save the full article page in the browser as "Web Page, Complete" (an HTML
file with a `<name>_files` directory) or as MHTML. Scroll through the page
first so that deferred images and tables load. Then:

```sh
paperextract extract paper.pdf --html "Article.html"
paperextract batch incoming/                  # pages are paired with PDFs by DOI
paperextract reprocess Travers_2019_HighEnergyPulse --html HISOL.mhtml
```

Pages are recognized by content, not by file name. In `batch`, a page pairs
with the one PDF whose DOI candidates contain the DOI declared in the page's
`citation_doi` meta tag. A page without a DOI, without a matching PDF, or
whose paper the library already holds is reported as `held`; the last case
names the `reprocess --html` command that adds it. Nothing on the page is
executed or fetched.

## What is kept

Each page becomes a further source of the paper, `original/source_NN/`, with
its asset directory byte for byte and every file in `manifest.json`.
`extraction.json` lists it with role `html_capture`, the address the browser
recorded and the file digests, so `reprocess` restores it and compares again.

## What is compared

The comparison runs only when the page's DOI equals the paper's validated
DOI, or, when either lacks a DOI, when the titles are identical (with an
`HTML_IDENTITY_UNCONFIRMED` warning). A page of another work or version gets
`HTML_VERSION_MISMATCH` and is not compared.

| Part | Method | Findings |
| --- | --- | --- |
| Body text | Each page paragraph of 12 or more words is split into six-word sequences; a paragraph with less than half of them in the extraction is reported. Case, accents, punctuation and math are ignored on both sides. | `HTML_TEXT_NOT_IN_PDF` |
| Headings | Page headings missing from the extraction's headings and from the start of its paragraphs (run-in headings); "Abstract" and "Main" are ignored. | `HTML_HEADINGS_NOT_IN_PDF` (info) |
| Figure captions | Matched by printed label; word-sequence agreement below 0.9 is reported. | `HTML_CAPTION_DIFFERS`, `HTML_FIGURE_NOT_IN_PDF` |
| Tables | Matched by printed label; numbers compared as in the Docling table check. Tables whose cells the page draws as math graphics without TeX cannot be compared by number; the share of their words found in the extracted table is reported instead. | `HTML_TABLE_AGREED`, `HTML_TABLE_DISAGREED`, `HTML_TABLE_NOT_COMPARABLE`, `HTML_TABLE_NOT_IN_PDF` |
| References | Count of entries against the extraction's reference paragraphs; the page's reference DOIs are kept in the report. | `HTML_REFERENCES_DIFFER` |
| Math | Counted (display, with TeX, MathML), not compared. | — |

A page with less than 3000 characters of body text gets `HTML_INCOMPLETE`,
typically an abstract page. `HTML_CHECK_SUMMARY` states the overall result.
The full report is `html_check.json` (schema `paperextract.html-checks` 1,
one `paperextract.html-check` report per page); the findings are also in
`validation.json` under `html_check` and in `diagnostics/review.md`.

## Measured on the pilot papers

On 23 September 2026, with the captures supplied for P1:

| Page | Result |
| --- | --- |
| COPRA (Optica), HTML and MHTML | 92 of 92 paragraphs found; 7 of 8 captions agree; Fig. 7 flagged, because MinerU reads that bar chart as a table; Tables 1–3 not comparable (math drawn as SVG), 69–100% of their words found |
| HISOL (Nature Photonics), HTML and MHTML | 56 of 58 paragraphs found (the two others are math-heavy definitions); 5 of 5 captions agree; 80 page references against 81 extracted |
| Wahlstrand (Phys. Rev. Lett.) against the HISOL paper | refused as a DOI mismatch |

## Current limits

- Rules are generic with a few publisher class names (Nature, Optica); other
  publishers may need small additions when examples show a need.
- Math is not compared; comparing MathML with LaTeX needs a converter.
- Paragraphs dense with symbols can be reported although present, because
  the page writes symbols as text and the PDF as math.
- A page's images are preserved but not compared with the PDF figures.
