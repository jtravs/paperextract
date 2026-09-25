"""Generate and check BibTeX for a validated bibliographic identity."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from typing import cast

from paperextract.identity import Identity

__all__ = ["bibtex_entry", "citation_key", "parse_bibtex", "validate_bibtex"]

_ESCAPES = (
    ("\\", "\\textbackslash{}"),
    ("&", "\\&"),
    ("%", "\\%"),
    ("#", "\\#"),
    ("$", "\\$"),
    ("_", "\\_"),
    ("{", "\\{"),
    ("}", "\\}"),
    ("~", "\\textasciitilde{}"),
    ("^", "\\textasciicircum{}"),
)
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "on",
        "in",
        "and",
        "for",
        "to",
        "with",
        "by",
        "at",
        "from",
        "via",
    }
)
_TYPES: Mapping[str, str] = {
    "journal-article": "article",
    "proceedings-article": "inproceedings",
    "book-chapter": "incollection",
    "posted-content": "misc",
    "Dataset": "misc",
    "Software": "misc",
}
# Where the container goes for each entry type; anything else is a booktitle.
_CONTAINER_FIELDS: Mapping[str, str] = {
    "article": "journal",
    "techreport": "institution",
    "report": "institution",
    "phdthesis": "school",
    "mastersthesis": "school",
    "thesis": "school",
}
_ENTRY = re.compile(r"@(\w+)\{([^,]+),(.*)\}\s*$", re.S)
_FIELD = re.compile(r"\s*(\w+)\s*=\s*\{((?:[^{}]|\{[^{}]*\})*)\}\s*,?", re.S)
_MIN_ACRONYM = 2


def _ascii(text: str) -> str:
    """Fold text to ASCII letters and digits for keys and names.

    Parameters
    ----------
    text : str
        Unicode text.

    Returns
    -------
    str
        Lower-case ASCII letters and digits only.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if ch.isascii() and ch.isalnum()).lower()


def _escape(text: str) -> str:
    """Escape BibTeX and TeX special characters.

    Parameters
    ----------
    text : str
        Plain text.

    Returns
    -------
    str
        Text safe inside a brace-delimited BibTeX value.
    """
    result = text
    for raw, escaped in _ESCAPES:
        result = result.replace(raw, escaped)
    return result


def _protect(title: str) -> str:
    """Protect capitalization that a BibTeX style must not lower-case.

    Parameters
    ----------
    title : str
        Escaped title text.

    Returns
    -------
    str
        Title with acronyms and inner capitals wrapped in braces.
    """
    words: list[str] = []
    for word in title.split(" "):
        letters = [ch for ch in word if ch.isalpha()]
        inner_capital = any(ch.isupper() for ch in letters[1:])
        acronym = len(letters) >= _MIN_ACRONYM and all(ch.isupper() for ch in letters)
        words.append(f"{{{word}}}" if (inner_capital or acronym) and word else word)
    return " ".join(words)


def citation_key(identity: Identity) -> str:
    """Build a deterministic, human-readable citation key.

    Parameters
    ----------
    identity : Identity
        Validated identity.

    Returns
    -------
    str
        First author family name, year and the first significant title word,
        folded to ASCII, for example ``travers2019highenergy``; a missing year
        reads ``nodate``.
    """
    authors = cast("list[dict[str, object]] | None", identity.field("authors")) or []
    first = authors[0] if authors else {}
    family = str(first.get("family") or first.get("literal") or "anonymous")
    year = identity.field("year")
    title = str(identity.field("title") or "")
    words = [_ascii(word) for word in title.split()]
    words = [word for word in words if word and word not in _STOP_WORDS]
    word = words[0] if words else "untitled"
    year_text = str(year) if year is not None else "nodate"
    return f"{_ascii(family) or 'anonymous'}{year_text}{word}"


def _author_text(authors: list[dict[str, object]]) -> str:
    """Render the author list in BibTeX form.

    Parameters
    ----------
    authors : list of dict
        Serialized registry authors.

    Returns
    -------
    str
        ``Family, Given and ...`` with literal names protected.
    """
    parts: list[str] = []
    for author in authors:
        family = author.get("family")
        given = author.get("given")
        literal = author.get("literal")
        if isinstance(family, str) and family:
            given_text = (
                f", {_escape(given)}" if isinstance(given, str) and given else ""
            )
            parts.append(f"{_escape(family)}{given_text}")
        elif isinstance(literal, str) and literal:
            parts.append(f"{{{_escape(literal)}}}")
    return " and ".join(parts)


def _entry_type(article_type: str) -> str:
    """Choose the BibTeX entry type for a registry or asserted type.

    Parameters
    ----------
    article_type : str
        Registry type such as ``journal-article``, or ``bibtex:techreport``
        for an identity the user asserted.

    Returns
    -------
    str
        BibTeX entry type; an asserted identity keeps the type the user gave.
    """
    if article_type.startswith("bibtex:"):
        return article_type.removeprefix("bibtex:")
    return _TYPES.get(article_type, "misc")


def _publisher_and_url(
    identity: Identity, entry_type: str, container: str
) -> list[tuple[str, str]]:
    """Render the publisher and, for an asserted identity, the URL fields.

    Parameters
    ----------
    identity : Identity
        Bibliographic identity.
    entry_type : str
        BibTeX entry type.
    container : str
        Field that holds the container, such as ``institution``.

    Returns
    -------
    list of tuple of str
        Field names and values; the publisher is left out of articles and
        when it only repeats the institution or school.
    """
    fields: list[tuple[str, str]] = []
    publisher = identity.field("publisher")
    repeated = container in ("institution", "school") and publisher == identity.field(
        "journal"
    )
    if isinstance(publisher, str) and entry_type != "article" and not repeated:
        fields.append(("publisher", _escape(publisher)))
    url = identity.field("url")
    if identity.status == "ASSERTED" and isinstance(url, str):
        fields.append(("url", url))
    return fields


def bibtex_entry(identity: Identity) -> str | None:
    """Render the BibTeX entry for a validated identity.

    Parameters
    ----------
    identity : Identity
        Bibliographic identity.

    Returns
    -------
    str or None
        Entry text, or None when the identity is neither validated nor
        asserted by the user.

    Notes
    -----
    Page ranges go to ``pages`` with a double hyphen; an article number goes to
    ``eid``, which BibLaTeX and many BibTeX styles understand, so the two are
    never confused. Titles keep acronyms and inner capitals in braces. Nothing
    is inferred: absent registry fields are simply omitted.
    """
    if not identity.named():
        return None
    article_type = str(identity.field("article_type") or "")
    entry_type = _entry_type(article_type)
    container = _CONTAINER_FIELDS.get(entry_type, "booktitle")
    fields: list[tuple[str, str]] = []
    authors = cast("list[dict[str, object]] | None", identity.field("authors")) or []
    if authors:
        fields.append(("author", _author_text(authors)))
    title = identity.field("title")
    if isinstance(title, str):
        fields.append(("title", _protect(_escape(title))))
    journal = identity.field("journal")
    if isinstance(journal, str):
        fields.append((container, _escape(journal)))
    year = identity.field("year")
    if isinstance(year, int):
        fields.append(("year", str(year)))
    for name, field_name in (("volume", "volume"), ("issue", "number")):
        value = identity.field(name)
        if isinstance(value, str):
            fields.append((field_name, _escape(value)))
    pages = identity.field("pages")
    if isinstance(pages, str):
        fields.append(
            ("pages", _escape(pages.replace("--", "-").replace("-", "--", 1)))
        )
    article_number = identity.field("article_number")
    if isinstance(article_number, str):
        fields.append(("eid", _escape(article_number)))
    fields.extend(_publisher_and_url(identity, entry_type, container))
    if identity.doi is not None:
        fields.append(("doi", identity.doi))
        fields.append(("url", f"https://doi.org/{identity.doi}"))
    if article_type == "posted-content":
        fields.append(("note", "Preprint"))
    body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields)
    return f"@{entry_type}{{{citation_key(identity)},\n{body}\n}}\n"


def parse_bibtex(entry: str) -> tuple[str, str, dict[str, str]]:
    """Parse one brace-delimited BibTeX entry as written by this module.

    Parameters
    ----------
    entry : str
        Entry text.

    Returns
    -------
    tuple
        Entry type, citation key and field values.

    Raises
    ------
    ValueError
        The text is not a single well-formed entry.

    Notes
    -----
    This is a reader for the project's own output, not a general BibTeX
    parser: values must be brace-delimited with at most one nesting level.
    """
    match = _ENTRY.match(entry.strip())
    if match is None:
        raise ValueError("Not a single BibTeX entry")
    fields: dict[str, str] = {}
    body = match.group(3).strip()
    position = 0
    while position < len(body):
        field_match = _FIELD.match(body, position)
        if field_match is None:
            nearby = body[position : position + 40].strip()
            raise ValueError(f"Malformed BibTeX field near: {nearby!r}")
        fields[field_match.group(1)] = field_match.group(2)
        position = field_match.end()
    return match.group(1), match.group(2).strip(), fields


def validate_bibtex(entry: str, identity: Identity) -> tuple[str, ...]:
    """Check a generated entry against the identity it was made from.

    Parameters
    ----------
    entry : str
        Generated BibTeX.
    identity : Identity
        Source identity.

    Returns
    -------
    tuple of str
        Problems found; empty when the entry parses, carries the required
        fields for its type and repeats the identity's DOI and year.
    """
    problems: list[str] = []
    try:
        entry_type, key, fields = parse_bibtex(entry)
    except ValueError as exc:
        return (str(exc),)
    if key != citation_key(identity):
        problems.append("citation key differs from the deterministic key")
    required = {"author", "title", "year"}
    if entry_type == "article":
        required.add("journal")
    missing = sorted(required - set(fields))
    if missing:
        problems.append(f"missing required field(s): {', '.join(missing)}")
    if identity.doi is not None and fields.get("doi") != identity.doi:
        problems.append("DOI field differs from the validated DOI")
    year = identity.field("year")
    if year is not None and fields.get("year") != str(year):
        problems.append("year field differs from the validated year")
    return tuple(problems)
