"""Read article content from saved publisher pages."""

from paperextract.html_article import page_doi, parse_article

BODY = "Pulse compression in gas-filled hollow capillary fibres works well. " * 2

PAGE = f"""<!DOCTYPE html><html><head>
<meta name="citation_title" content="Soliton Self-Compression">
<meta name="citation_doi" content="doi:10.1000/ABC">
<meta name="citation_author" content="A. Author">
<meta name="citation_author" content="B. Author">
<meta property="og:title" content="ignored">
<meta name="description" content="">
</head><body>
<nav><h2>Navigation heading</h2><p>{BODY}</p></nav>
<article class="teaser"><p>Short teaser.</p></article>
<article>
<h1>Title</h1>
<h2>1. Introduction</h2>
<h3></h3>
<p>{BODY}<math display="inline"><mi>x</mi></math> continues.<br/>
<span aria-hidden="true">screen-reader only</span>
<p>{BODY} second paragraph without a closing tag
<h2 hidden>Hidden heading</h2>
<section data-title="Inline Recommendations"><h3>Recommended paper</h3></section>
<p>Too short.</p>
<p style="display: none">{BODY}</p>
<div class="equation"><mjx-container><svg><text>drawn</text></svg></mjx-container></div>
<math display="block"><semantics><mi>E</mi>
<annotation encoding="application/x-tex">E = mc^2</annotation></semantics></math>
<math alttext="a+b"><mi>a</mi></math>
<figure><figcaption><b>Fig. 1: A plot.</b></figcaption>
<div class="figure-description"><p>Details of the plot.</p></div>
<img src="fig1.png"><img data-src="fig1-large.png"></figure>
<figure><figcaption>Fig. 1: A duplicate.</figcaption></figure>
<figure><figcaption>Unlabelled picture.</figcaption></figure>
<div class="fig"><p>Fig. 2. Caption in a paragraph.</p></div>
<table><caption>Table 1. Energies</caption>
<tr><th>Gas</th><th>E</th></tr><tr><td>He</td><td>1.8<br>µJ</td></tr>
<tr><td>Ar</td><td><math><mn>2</mn><img src="m.png"></math><button>copy</button></td>
</tr></table>
<table><caption>Table 3. Empty</caption></table>
<table><tr><td>layout</td></tr></table>
<h2>References</h2>
<ol class="c-article-references">
<li><p>A. Author, Journal 1, 2 (2020).</p>
<p class="c-article-references__links">
<a href="https://doi.org/10.1063%2F1.1653204">Article</a></p></li>
<li><span data-doi="10.1103/PhysRevA.1.1">B. Author</span>,
doi:10.1103/physreva.1.1.</li>
<li>   </li>
</ol>
<p id="ref3" class="reference-body">C. Author, <a href="http://dx.doi.org/10.1364/OE.1.2">Crossref</a>.</p>
</article>
<div class="modal"><h4 class="modal-title">Table 2.</h4>
<div class="modal-body"><h4>Operators</h4><div><table>
<thead><tr><td>Method</td><td>Operator</td></tr></thead>
<tbody><tr><td>SHG</td><td><mjx-container><svg></svg></mjx-container></td></tr>
<tr><td>THG</td><td><table><tr><td>inner</td></tr></table></td></tr></tbody>
</table></div></div></div>
<div class="modal"><h4>Table 2.</h4>
<table><tr><td>again</td></tr><tr><td>x</td></tr></table></div>
<p>unclosed <b>bold</i></p>
</body></html>"""


def test_metadata_and_body_text_come_from_the_article_element() -> None:
    article = parse_article(PAGE)
    assert article.doi == "10.1000/abc"
    assert article.title == "Soliton Self-Compression"
    assert article.meta["citation_author"] == ("A. Author", "B. Author")
    assert "description" not in article.meta
    assert article.headings == ("1. Introduction", "References")
    assert len(article.paragraphs) == 2
    # Math is left out of the comparison text.
    assert article.paragraphs[0].endswith("works well. continues.")
    assert "Too short" not in " ".join(article.paragraphs)


def test_figures_tables_equations_and_references_are_found() -> None:
    article = parse_article(PAGE)
    assert [(f.label, f.caption) for f in article.figures] == [
        ("1", "Fig. 1: A plot. Details of the plot."),
        ("2", "Fig. 2. Caption in a paragraph."),
    ]
    assert article.figures[0].images == ("fig1.png", "fig1-large.png")
    first, empty, second = article.tables
    assert (empty.label, empty.grid) == ("3", None)
    assert (first.label, first.caption, first.math_cells) == (
        "1",
        "Table 1. Energies",
        1,
    )
    assert first.grid is not None and (first.grid.rows, first.grid.columns) == (3, 2)
    assert "<math></math>" in first.html
    # A table in a dialog outside the article takes its caption from the
    # headings before it; its nested layout table is not a separate table.
    assert (second.label, second.caption) == ("2", "Table 2. Operators")
    assert second.math_cells == 1
    assert [(e.display, e.tex) for e in article.equations] == [
        (False, None),
        (True, None),
        (True, "E = mc^2"),
        (False, "a+b"),
        (False, None),
    ]
    assert article.equations[2].mathml is not None
    assert article.equations[1].mathml is None
    assert [r.dois for r in article.references] == [
        ("10.1063/1.1653204",),
        ("10.1103/physreva.1.1",),
        ("10.1364/oe.1.2",),
    ]
    assert article.references[0].text == "A. Author, Journal 1, 2 (2020)."


def test_pages_without_an_article_element_use_the_whole_document() -> None:
    article = parse_article(
        f"<html><body><h2>Results</h2><p>{BODY}</p>"
        "<table><tr><td>a</td></tr><tr><td>b</td></tr></table></body></html>"
    )
    assert article.headings == ("Results",)
    assert article.doi is None and article.title is None
    (table,) = article.tables
    assert table.label is None and table.caption == ""
    empty = parse_article("<html><article></article></html>")
    assert empty.paragraphs == () and empty.tables == ()
    dublin = parse_article(
        '<meta name="DC.identifier" content="https://doi.org/10.5555/X">'
    )
    assert dublin.doi == "10.5555/x"


def test_the_declared_doi_is_read_without_parsing_the_page() -> None:
    assert page_doi(PAGE) == "10.1000/abc"
    assert page_doi("<meta content='10.2222/Q' name='dc.identifier'>") == "10.2222/q"
    assert page_doi('<meta name="citation_doi" content="none">') is None
    assert page_doi("<p>no meta</p>") is None
