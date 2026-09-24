"""Read the scientific content of a saved article page without executing it.

The page is parsed with the standard-library HTML parser into a small element
tree; scripts never run and no resource is fetched. Generic rules find the
article: ``citation_*`` meta tags give identity, the ``<article>`` (or
``<main>``) element with the most paragraph text is the body, headings and
paragraphs are read in document order, and figure captions, tables and
reference lists are recognized by their elements, class names and printed
labels. Navigation, buttons, forms, scripts and rendered math graphics are
not text. Math is kept as MathML or TeX where the page supplies it and is
left out of the plain text used for comparison.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser
from urllib.parse import unquote

from paperextract.normalize import figure_label, table_label
from paperextract.tables import TableGrid, parse_html_table

__all__ = [
    "HtmlArticle",
    "HtmlEquation",
    "HtmlFigure",
    "HtmlReference",
    "HtmlTable",
    "page_doi",
    "parse_article",
]

_VOID = frozenset(
    [
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    ]
)
# Elements whose content is never article text.
_SKIPPED = frozenset(
    [
        "script",
        "style",
        "noscript",
        "template",
        "button",
        "nav",
        "form",
        "select",
        "iframe",
        "svg",
        "mjx-assistive-mml",
        "mjx-speech",
        "canvas",
    ]
)
_MATH = frozenset({"math", "mjx-container"})
_BLOCK = frozenset(
    [
        "p",
        "div",
        "li",
        "td",
        "th",
        "tr",
        "table",
        "section",
        "article",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    ]
)
_AUTO_CLOSE = {
    "p": frozenset(
        [
            "p",
            "div",
            "ul",
            "ol",
            "table",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "section",
            "figure",
        ]
    ),
    "li": frozenset({"li"}),
    "td": frozenset({"td", "th", "tr"}),
    "th": frozenset({"td", "th", "tr"}),
    "tr": frozenset({"tr"}),
}
_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)")
_WHITESPACE = re.compile(r"\s+")
_REFERENCE_CONTAINER = re.compile(
    r"(?:^|[-_\s])(?:references?|ref-list|bibliography|bibl)(?:$|[-_\s])", re.I
)
_REFERENCE_ITEM = re.compile(r"^(?:ref|b|bib|cr)[-_]?\d+$", re.I)
_CAPTION_CLASS = re.compile(
    r"caption|figure-title|fig-title|figure-description|fig-desc", re.I
)
# Publisher widgets inside the article element that are not the article.
_FURNITURE = re.compile(
    r"recommend|related.subjects|advert|nomad|rights.and.permissions|"
    r"about.this.article|bibliographic.information|supplementary.information",
    re.I,
)
_REFERENCE_NOISE = re.compile(r"(?:^|[-_\s])links?(?:$|[-_\s])|nomad", re.I)
_MIN_BODY_CHARACTERS = 40


@dataclass
class Element:
    """Hold one parsed element with its attributes and children."""

    tag: str
    attrs: dict[str, str]
    children: list[Element | str] = field(default_factory=list["Element | str"])
    parent: Element | None = None

    def iter(self) -> Iterator[Element]:
        """Yield this element and its descendant elements in document order.

        Yields
        ------
        Element
            Elements, depth first.
        """
        yield self
        for child in self.children:
            if isinstance(child, Element):
                yield from child.iter()

    @property
    def classes(self) -> str:
        """Return the class attribute.

        Returns
        -------
        str
            Class names separated by spaces.
        """
        return self.attrs.get("class", "")


class _TreeBuilder(HTMLParser):
    """Build a tolerant element tree from HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("#root", {})
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Open an element, closing elements that HTML closes implicitly.

        Parameters
        ----------
        tag : str
            Tag name.
        attrs : list of tuple
            Attributes.
        """
        closes = next(
            (name for name, starts in _AUTO_CLOSE.items() if tag in starts), None
        )
        if closes is not None:
            for index in range(len(self.stack) - 1, 0, -1):
                name = self.stack[index].tag
                if name == closes and tag in _AUTO_CLOSE[closes]:
                    del self.stack[index:]
                    break
                if name in ("table", "ul", "ol", "div", "section", "article"):
                    break
        parent = self.stack[-1]
        element = Element(tag, {k: v or "" for k, v in attrs}, parent=parent)
        parent.children.append(element)
        if tag not in _VOID:
            self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Add a self-closing element.

        Parameters
        ----------
        tag : str
            Tag name.
        attrs : list of tuple
            Attributes.
        """
        parent = self.stack[-1]
        parent.children.append(
            Element(tag, {k: v or "" for k, v in attrs}, parent=parent)
        )

    def handle_endtag(self, tag: str) -> None:
        """Close the nearest open element with this tag, if any.

        Parameters
        ----------
        tag : str
            Tag name.
        """
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        """Append text to the current element.

        Parameters
        ----------
        data : str
            Text.
        """
        self.stack[-1].children.append(data)


def _tree(html: str) -> Element:
    """Parse HTML into an element tree.

    Parameters
    ----------
    html : str
        Document.

    Returns
    -------
    Element
        Synthetic root.
    """
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def _squash(text: str) -> str:
    """Collapse whitespace.

    Parameters
    ----------
    text : str
        Text.

    Returns
    -------
    str
        Single-spaced, stripped text.
    """
    return _WHITESPACE.sub(" ", text).strip()


def _text(element: Element, *, math: str = " ") -> str:
    """Read the visible text of an element.

    Parameters
    ----------
    element : Element
        Element.
    math : str
        Replacement for each math element.

    Returns
    -------
    str
        Text with whitespace collapsed; skipped elements contribute nothing.
    """
    parts: list[str] = []

    def walk(node: Element) -> None:
        """Append the visible text below one node.

        Parameters
        ----------
        node : Element
            Element to walk.
        """
        for child in node.children:
            if isinstance(child, str):
                parts.append(child)
            elif child.tag in _MATH:
                parts.append(math)
            elif child.tag in _SKIPPED or _hidden(child):
                continue
            else:
                if child.tag in _BLOCK or child.tag == "br":
                    parts.append(" ")
                walk(child)

    walk(element)
    return _squash("".join(parts))


def _hidden(element: Element) -> bool:
    """Tell whether markup hides an element from readers.

    Parameters
    ----------
    element : Element
        Element.

    Returns
    -------
    bool
        True for ``hidden``, ``aria-hidden="true"`` or ``display: none``.
    """
    style = element.attrs.get("style", "").replace(" ", "").lower()
    labels = " ".join(
        element.attrs.get(name, "")
        for name in ("class", "data-title", "aria-labelledby", "id")
    )
    return (
        "hidden" in element.attrs
        or element.attrs.get("aria-hidden") == "true"
        or "display:none" in style
        or bool(_FURNITURE.search(labels))
    )


def _inner_html(element: Element) -> str:
    """Serialize an element's content back to HTML.

    Parameters
    ----------
    element : Element
        Element.

    Returns
    -------
    str
        Markup of its children; skipped elements are dropped and math is
        replaced by its TeX when the page supplies it.
    """
    parts: list[str] = []
    for child in element.children:
        if isinstance(child, str):
            parts.append(escape(child, quote=False))
        elif child.tag in _MATH:
            tex = _tex(child)
            parts.append(
                f"<math>{escape(tex, quote=False)}</math>" if tex else "<math></math>"
            )
        elif child.tag in _SKIPPED:
            continue
        else:
            attributes = "".join(
                f' {name}="{escape(value)}"'
                for name, value in child.attrs.items()
                if name in ("rowspan", "colspan")
            )
            inner = "" if child.tag in _VOID else _inner_html(child)
            closing = "" if child.tag in _VOID else f"</{child.tag}>"
            parts.append(f"<{child.tag}{attributes}>{inner}{closing}")
    return "".join(parts)


def _tex(element: Element) -> str | None:
    """Find TeX supplied with a math element.

    Parameters
    ----------
    element : Element
        ``math`` or MathJax container.

    Returns
    -------
    str or None
        A TeX annotation, the ``alttext`` attribute, or None.
    """
    for node in element.iter():
        encoding = node.attrs.get("encoding", "").lower()
        if node.tag == "annotation" and "tex" in encoding:
            return _squash(_raw_text(node))
    alt = element.attrs.get("alttext")
    return _squash(alt) if alt else None


def _raw_text(element: Element) -> str:
    """Concatenate every text node below an element.

    Parameters
    ----------
    element : Element
        Element.

    Returns
    -------
    str
        Text, including that of skipped elements.
    """
    return "".join(
        child if isinstance(child, str) else _raw_text(child)
        for child in element.children
    )


def _serialize(element: Element) -> str:
    """Serialize an element and its descendants verbatim.

    Parameters
    ----------
    element : Element
        Element.

    Returns
    -------
    str
        Markup with all attributes.
    """
    attributes = "".join(f' {k}="{escape(v)}"' for k, v in element.attrs.items())
    if element.tag in _VOID:
        return f"<{element.tag}{attributes}>"
    inner = "".join(
        escape(child, quote=False) if isinstance(child, str) else _serialize(child)
        for child in element.children
    )
    return f"<{element.tag}{attributes}>{inner}</{element.tag}>"


@dataclass(frozen=True)
class HtmlFigure:
    """Hold a figure caption found in the page.

    Attributes
    ----------
    label : str or None
        Printed figure number.
    caption : str
        Caption text, including the printed label.
    images : tuple of str
        Image addresses given by the figure's ``img`` elements.
    """

    label: str | None
    caption: str
    images: tuple[str, ...]


@dataclass(frozen=True)
class HtmlTable:
    """Hold one data table of the page.

    Attributes
    ----------
    label : str or None
        Printed table number.
    caption : str
        Caption text found near the table.
    html : str
        Table markup with math replaced by its TeX, when supplied.
    grid : TableGrid or None
        Parsed cells, or None when the markup has no rows.
    math_cells : int
        Cells that contain math.
    """

    label: str | None
    caption: str
    html: str
    grid: TableGrid | None
    math_cells: int


@dataclass(frozen=True)
class HtmlEquation:
    """Hold one math element of the article body.

    Attributes
    ----------
    display : bool
        Whether the page shows it as a display equation.
    tex : str or None
        TeX supplied by the page.
    mathml : str or None
        MathML markup, when the page uses MathML.
    """

    display: bool
    tex: str | None
    mathml: str | None


@dataclass(frozen=True)
class HtmlReference:
    """Hold one entry of the page's reference list.

    Attributes
    ----------
    text : str
        Entry text.
    dois : tuple of str
        DOIs linked or printed in the entry.
    """

    text: str
    dois: tuple[str, ...]


@dataclass(frozen=True)
class HtmlArticle:
    """Hold the article content read from a saved page.

    Attributes
    ----------
    meta : Mapping of str to tuple of str
        ``citation_*`` and ``dc.*`` meta values by lower-case name.
    doi : str or None
        DOI declared by the page's metadata.
    title : str or None
        Declared title.
    headings : tuple of str
        Section headings of the article body.
    paragraphs : tuple of str
        Body paragraphs, without math, captions or references.
    figures : tuple of HtmlFigure
        Figure captions.
    tables : tuple of HtmlTable
        Data tables anywhere in the page.
    equations : tuple of HtmlEquation
        Math elements of the article body.
    references : tuple of HtmlReference
        Reference list entries.
    """

    meta: Mapping[str, tuple[str, ...]]
    doi: str | None
    title: str | None
    headings: tuple[str, ...]
    paragraphs: tuple[str, ...]
    figures: tuple[HtmlFigure, ...]
    tables: tuple[HtmlTable, ...]
    equations: tuple[HtmlEquation, ...]
    references: tuple[HtmlReference, ...]


def _meta(root: Element) -> dict[str, tuple[str, ...]]:
    """Collect bibliographic meta tags.

    Parameters
    ----------
    root : Element
        Document tree.

    Returns
    -------
    dict of str to tuple of str
        Values by lower-case name, in document order.
    """
    values: dict[str, list[str]] = {}
    for element in root.iter():
        if element.tag != "meta":
            continue
        name = (
            element.attrs.get("name") or element.attrs.get("property") or ""
        ).lower()
        content = _squash(element.attrs.get("content", ""))
        if content and name.startswith(("citation_", "dc.", "prism.")):
            values.setdefault(name, []).append(content)
    return {name: tuple(found) for name, found in values.items()}


def _body(root: Element) -> Element:
    """Choose the element that holds the article text.

    Parameters
    ----------
    root : Element
        Document tree.

    Returns
    -------
    Element
        The ``article`` or ``main`` element with the most paragraph text, or
        the document when there is none.
    """
    candidates = [e for e in root.iter() if e.tag in ("article", "main")]

    def weight(element: Element) -> int:
        """Measure an element's paragraph text.

        Parameters
        ----------
        element : Element
            Candidate body.

        Returns
        -------
        int
            Characters of paragraph text.
        """
        return sum(len(_text(p)) for p in element.iter() if p.tag == "p")

    best = max(candidates, key=weight, default=None)
    return best if best is not None and weight(best) > 0 else root


def _inside(element: Element, predicate: Callable[[Element], bool]) -> bool:
    """Tell whether an ancestor satisfies a predicate.

    Parameters
    ----------
    element : Element
        Element.
    predicate : Callable
        Test applied to each ancestor.

    Returns
    -------
    bool
        True when an ancestor matches.
    """
    node = element.parent
    while node is not None:
        if predicate(node):
            return True
        node = node.parent
    return False


def _is_reference_container(element: Element) -> bool:
    """Recognize a reference list container.

    Parameters
    ----------
    element : Element
        Element.

    Returns
    -------
    bool
        True for an element whose id or class names a reference list.
    """
    names = " ".join(
        (
            element.attrs.get("id", ""),
            element.classes,
            element.attrs.get("data-title", ""),
        )
    )
    return bool(_REFERENCE_CONTAINER.search(names))


def _is_reference_item(element: Element) -> bool:
    """Recognize one reference entry.

    Parameters
    ----------
    element : Element
        Element.

    Returns
    -------
    bool
        True for a list item inside a reference container, or an element
        whose id looks like ``ref12``.
    """
    if element.tag == "li" and _inside(element, _is_reference_container):
        return True
    return bool(_REFERENCE_ITEM.match(element.attrs.get("id", ""))) and element.tag in (
        "p",
        "li",
        "div",
    )


def _is_caption(element: Element) -> bool:
    """Recognize a caption element.

    Parameters
    ----------
    element : Element
        Element.

    Returns
    -------
    bool
        True for ``figcaption``, ``caption`` or a caption class.
    """
    return element.tag in ("figcaption", "caption") or bool(
        _CAPTION_CLASS.search(element.classes)
    )


def _reference_text(element: Element) -> str:
    """Read a reference entry without its link widgets.

    Parameters
    ----------
    element : Element
        Reference entry.

    Returns
    -------
    str
        Entry text; children whose class names a link list are skipped.
    """
    parts: list[str] = []
    for child in element.children:
        if isinstance(child, str):
            parts.append(child)
        elif not _REFERENCE_NOISE.search(child.classes):
            parts.append(f" {_text(child)} ")
    return _squash("".join(parts))


def _references(body: Element) -> tuple[HtmlReference, ...]:
    """Read the reference list.

    Parameters
    ----------
    body : Element
        Article element.

    Returns
    -------
    tuple of HtmlReference
        Entries in order; nested matches count once.
    """
    entries: list[HtmlReference] = []
    for element in body.iter():
        if not _is_reference_item(element) or _inside(element, _is_reference_item):
            continue
        text = _reference_text(element)
        if not text:
            continue
        found: list[str] = []
        for node in element.iter():
            values = (
                unquote(node.attrs.get("href", "")),
                node.attrs.get("data-doi", ""),
                *(c for c in node.children if isinstance(c, str)),
            )
            for value in values:
                for match in _DOI.findall(value):
                    doi = match.rstrip(".,;)").lower()
                    if doi not in found:
                        found.append(doi)
        entries.append(HtmlReference(text, tuple(found)))
    return tuple(entries)


def _figures(body: Element) -> tuple[HtmlFigure, ...]:
    """Read figure captions.

    Parameters
    ----------
    body : Element
        Article element.

    Returns
    -------
    tuple of HtmlFigure
        One entry per labelled caption container, in order.
    """
    figures: list[HtmlFigure] = []
    seen: set[str] = set()
    for element in body.iter():
        if element.tag != "figure" and not (
            element.tag in ("div", "p") and "fig" in element.classes.lower()
        ):
            continue
        captions = [e for e in element.iter() if e is not element and _is_caption(e)]
        text = " ".join(
            _text(c)
            for c in captions
            if not _inside(c, _is_caption) or c.parent is element
        )
        text = _squash(text) or _squash(
            " ".join(_text(e) for e in element.iter() if e.tag == "p")
        )
        label = figure_label(text)
        if label is None or label in seen:
            continue
        seen.add(label)
        images = tuple(
            e.attrs.get("src") or e.attrs.get("data-src", "")
            for e in element.iter()
            if e.tag == "img"
        )
        figures.append(HtmlFigure(label, text, images))
    return tuple(figures)


def _table_caption(table: Element) -> str:
    """Find the caption printed with a table.

    Parameters
    ----------
    table : Element
        Table element.

    Returns
    -------
    str
        A ``caption`` element, else the headings and caption elements that
        precede the table within its nearest enclosing containers.
    """
    for child in table.children:
        if isinstance(child, Element) and child.tag == "caption":
            return _text(child)
    node = table
    ancestors: list[Element] = []
    parent = table.parent
    while parent is not None:
        ancestors.append(parent)
        parent = parent.parent
    for _level in range(4):
        container = node.parent
        if container is None:
            break
        texts: list[str] = []
        # Only elements printed before the table, not the ones that hold it.
        preceding = itertools.takewhile(lambda e: e is not table, container.iter())
        for element in preceding:
            if element in ancestors:
                continue
            if element.tag in ("h1", "h2", "h3", "h4", "h5", "h6") or _is_caption(
                element
            ):
                texts.append(_text(element))
        caption = _squash(" ".join(texts))
        if table_label(caption) is not None:
            return caption
        node = container
    return ""


def _tables(root: Element) -> tuple[HtmlTable, ...]:
    """Read the data tables of the whole page.

    Parameters
    ----------
    root : Element
        Document tree; publishers often place tables in dialogs outside the
        article element.

    Returns
    -------
    tuple of HtmlTable
        Tables with a printed label, first occurrence per label, plus every
        unlabelled table that has at least two rows.
    """
    tables: list[HtmlTable] = []
    seen: set[str] = set()
    for element in root.iter():
        if element.tag != "table" or _inside(element, lambda e: e.tag == "table"):
            continue
        caption = _table_caption(element)
        label = table_label(caption)
        if label is not None and label in seen:
            continue
        markup = f"<table>{_inner_html(element)}</table>"
        try:
            grid: TableGrid | None = parse_html_table(markup)
        except ValueError:
            grid = None
        if label is None and (grid is None or grid.rows < 2):  # noqa: PLR2004
            continue
        if label is not None:
            seen.add(label)
        math_cells = sum(
            1
            for cell in element.iter()
            if cell.tag in ("td", "th") and any(e.tag in _MATH for e in cell.iter())
        )
        tables.append(HtmlTable(label, caption, markup, grid, math_cells))
    return tuple(tables)


def _equations(body: Element) -> tuple[HtmlEquation, ...]:
    """Read the math elements of the article body.

    Parameters
    ----------
    body : Element
        Article element.

    Returns
    -------
    tuple of HtmlEquation
        MathML elements and MathJax containers, outermost only.
    """
    equations: list[HtmlEquation] = []
    for element in body.iter():
        if element.tag not in _MATH or _inside(element, lambda e: e.tag in _MATH):
            continue
        display = (
            element.attrs.get("display") == "block"
            or element.attrs.get("display") == "true"
            or _inside(
                element,
                lambda e: "equation" in e.classes.lower() or e.tag == "disp-formula",
            )
        )
        mathml = _serialize(element) if element.tag == "math" else None
        equations.append(HtmlEquation(display, _tex(element), mathml))
    return tuple(equations)


def _headings_and_paragraphs(body: Element) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Read section headings and body paragraphs in order.

    Parameters
    ----------
    body : Element
        Article element.

    Returns
    -------
    tuple
        Headings and paragraphs; paragraphs inside figures, tables,
        captions and reference entries are excluded.
    """
    headings: list[str] = []
    paragraphs: list[str] = []

    def excluded(element: Element) -> bool:
        """Tell whether an element is not body text.

        Parameters
        ----------
        element : Element
            Element.

        Returns
        -------
        bool
            True for figures, tables, captions, references and hidden parts.
        """
        return (
            element.tag in ("figure", "table", "caption", "figcaption")
            or _is_caption(element)
            or _is_reference_item(element)
            or element.tag in _SKIPPED
            or _hidden(element)
        )

    for element in body.iter():
        if element.tag in ("h2", "h3", "h4"):
            if not _inside(element, excluded) and not excluded(element):
                text = _text(element)
                if text:
                    headings.append(text)
        elif (
            element.tag == "p"
            and not _inside(element, excluded)
            and not excluded(element)
        ):
            text = _text(element)
            if len(text) >= _MIN_BODY_CHARACTERS:
                paragraphs.append(text)
    return tuple(headings), tuple(paragraphs)


_META_TAG = re.compile(r"<meta\b[^>]*>", re.I)
_ATTRIBUTE = re.compile(r"""([\w:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")


def page_doi(html: str) -> str | None:
    """Read the DOI a page declares in its meta tags, without parsing the page.

    Parameters
    ----------
    html : str
        Page.

    Returns
    -------
    str or None
        Lower-case DOI from ``citation_doi`` or ``dc.identifier``.

    Examples
    --------
    >>> page_doi('<meta content="doi:10.1364/OPTICA.6.000495" name="citation_doi">')
    '10.1364/optica.6.000495'
    """
    for tag in _META_TAG.findall(html):
        attributes = {
            key.lower(): double or single
            for key, double, single in _ATTRIBUTE.findall(tag)
        }
        if attributes.get("name", "").lower() in ("citation_doi", "dc.identifier"):
            match = _DOI.search(attributes.get("content", ""))
            if match:
                return match.group(1).lower()
    return None


def parse_article(html: str) -> HtmlArticle:
    """Read the article content of a saved page.

    Parameters
    ----------
    html : str
        Main HTML document of a capture.

    Returns
    -------
    HtmlArticle
        Metadata, body text, captions, tables, equations and references.

    Examples
    --------
    >>> page = (
    ...     '<meta name="citation_doi" content="10.1000/X">'
    ...     "<article><h2>Results</h2><p>" + "Text " * 10 + "</p>"
    ...     "<figure><figcaption>Fig. 1. A plot.</figcaption></figure></article>"
    ... )
    >>> article = parse_article(page)
    >>> article.doi, article.headings, article.figures[0].label
    ('10.1000/x', ('Results',), '1')
    """
    root = _tree(html)
    meta = _meta(root)
    body = _body(root)
    doi_values = meta.get("citation_doi", ()) or meta.get("dc.identifier", ())
    doi = next(
        (match.group(1).lower() for v in doi_values if (match := _DOI.search(v))), None
    )
    titles = meta.get("citation_title", ()) or meta.get("dc.title", ())
    headings, paragraphs = _headings_and_paragraphs(body)
    return HtmlArticle(
        meta=meta,
        doi=doi,
        title=titles[0] if titles else None,
        headings=headings,
        paragraphs=paragraphs,
        figures=_figures(body),
        tables=_tables(root),
        equations=_equations(body),
        references=_references(body),
    )
