"""Index a library for lookup and full-text search, and write derived catalogs.

Everything here is derived from ``catalog.jsonl`` and the paper directories and
can be rebuilt at any time. ``.paperextract/index.sqlite`` holds lookup tables for
DOIs, source digests and text digests, and an FTS5 table over titles, authors,
abstracts and the Markdown text. ``catalog.md``, ``library.bib`` and
``catalog.csl.json`` at the library root let people, agents and reference
managers browse or import the library without the database.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from paperextract.catalog import CATALOG_FILENAME, read_catalog
from paperextract.export import DESCRIPTION_BEGIN, DESCRIPTION_END
from paperextract.fields import items, mapping
from paperextract.identity import arxiv_base, arxiv_doi, title_key
from paperextract.pdf import fingerprint_pdf, page_text
from paperextract.registry import normalize_doi

__all__ = [
    "CATALOG_CSL",
    "CATALOG_MARKDOWN",
    "INDEX_PATH",
    "INDEX_SCHEMA_VERSION",
    "LIBRARY_BIB",
    "Match",
    "Query",
    "SearchHit",
    "build_index",
    "lookup",
    "queries_from_bibtex",
    "query_from_file",
    "refresh_index",
    "search",
]

INDEX_PATH = Path(".paperextract") / "index.sqlite"
# Version 2 indexes machine-generated figure descriptions in their own column.
# Version 3 adds the arXiv identifiers of papers.
INDEX_SCHEMA_VERSION = 3
CATALOG_MARKDOWN = "catalog.md"
LIBRARY_BIB = "library.bib"
CATALOG_CSL = "catalog.csl.json"
# Titles that share this share of their key tokens are worth a human look.
_CANDIDATE_OVERLAP = 0.8
_YEAR_TOLERANCE = 1
# Short titles such as "Introduction" appear on many first pages.
_MIN_PRINTED_TITLE_WORDS = 4
_FRONT_MATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_DESCRIPTION = re.compile(
    re.escape(DESCRIPTION_BEGIN) + r"\n(.*?)\n" + re.escape(DESCRIPTION_END),
    re.DOTALL,
)
_WORD = re.compile(r"\w+", re.UNICODE)
_QUOTE = re.compile(r"^> ?", re.MULTILINE)
# Snippet markers U+E000 and U+E001, replaced by brackets for display.
_MATCH_OPEN = "\ue000"
_MATCH_CLOSE = "\ue001"
_CSL_TYPES = {
    "journal-article": "article-journal",
    "proceedings-article": "paper-conference",
    "book-chapter": "chapter",
    "posted-content": "article",
    "book": "book",
}
Status = Literal["present", "related", "candidate", "absent"]

_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE papers (
    directory TEXT PRIMARY KEY,
    library_id TEXT,
    doi TEXT,
    title TEXT,
    title_key TEXT,
    title_observed TEXT,
    title_key_observed TEXT,
    first_author_family TEXT,
    authors TEXT NOT NULL,
    authors_observed TEXT NOT NULL,
    year INTEGER,
    venue TEXT,
    citation_key TEXT,
    bibliographic_status TEXT,
    processing_status TEXT,
    row TEXT NOT NULL
);
CREATE TABLE sources (sha256 TEXT NOT NULL, directory TEXT NOT NULL, role TEXT);
CREATE TABLE texts (text_sha256 TEXT NOT NULL, directory TEXT NOT NULL);
CREATE TABLE candidates (doi TEXT NOT NULL, directory TEXT NOT NULL);
CREATE TABLE arxiv (identifier TEXT NOT NULL, directory TEXT NOT NULL);
CREATE INDEX arxiv_identifier ON arxiv (identifier);
CREATE INDEX papers_doi ON papers (doi);
CREATE INDEX sources_sha ON sources (sha256);
CREATE INDEX texts_sha ON texts (text_sha256);
CREATE INDEX candidates_doi ON candidates (doi);
CREATE VIRTUAL TABLE fulltext USING fts5(
    directory UNINDEXED, title, authors, abstract, body, descriptions,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""


def _text(value: object) -> str | None:
    """Return a value as text, keeping None.

    Parameters
    ----------
    value : object
        Row value.

    Returns
    -------
    str or None
        String form.
    """
    return None if value is None else str(value)


def _body(paper: Path, row: Mapping[str, object]) -> tuple[str, str]:
    """Read the Markdown text of a paper and its supplements for full-text search.

    Parameters
    ----------
    paper : Path
        Paper directory.
    row : Mapping of str to object
        Catalog row.

    Returns
    -------
    tuple of str
        The paper's own text without front matter or machine-generated
        descriptions, and those descriptions; both empty when the files are
        missing.
    """
    paths = [paper / "paper.md"]
    paths.extend(
        paper / str(mapping(item)["markdown"])
        for item in items(row.get("supplements", []))
    )
    parts: list[str] = []
    descriptions: list[str] = []
    for path in paths:
        if path.is_file():
            text = _FRONT_MATTER.sub("", path.read_text(encoding="utf-8"))
            descriptions.extend(
                _QUOTE.sub("", match.group(1)) for match in _DESCRIPTION.finditer(text)
            )
            parts.append(_DESCRIPTION.sub("", text))
    return "\n\n".join(parts), "\n\n".join(descriptions)


def _fill(
    connection: sqlite3.Connection, library: Path, rows: Sequence[Mapping[str, object]]
) -> None:
    """Insert catalog rows into an empty index.

    Parameters
    ----------
    connection : sqlite3.Connection
        Connection with the schema created.
    library : Path
        Library root, for reading Markdown.
    rows : Sequence of Mapping
        Catalog rows.
    """
    for row in rows:
        directory = str(row["directory"])
        authors = [str(name) for name in items(row.get("authors", []))]
        observed = [str(name) for name in items(row.get("authors_observed", []))]
        year = row.get("year")
        connection.execute(
            "INSERT OR REPLACE INTO papers VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                directory,
                _text(row.get("library_id")),
                _text(row.get("doi")),
                _text(row.get("title")),
                _text(row.get("title_key")),
                _text(row.get("title_observed")),
                _text(row.get("title_key_observed")),
                _text(row.get("first_author_family")),
                json.dumps(authors, ensure_ascii=False),
                json.dumps(observed, ensure_ascii=False),
                year if isinstance(year, int) else None,
                _text(row.get("venue")),
                _text(row.get("citation_key")),
                _text(row.get("bibliographic_status")),
                _text(row.get("processing_status")),
                json.dumps(dict(row), ensure_ascii=False, sort_keys=True),
            ),
        )
        digests = [str(value) for value in items(row.get("source_sha256", []))]
        for position, digest in enumerate(digests):
            role = "main" if position == 0 else "supplement"
            connection.execute(
                "INSERT INTO sources VALUES (?, ?, ?)", (digest, directory, role)
            )
        texts = [row.get("text_sha256")]
        texts.extend(
            mapping(item).get("text_sha256")
            for item in items(row.get("supplements", []))
        )
        for text in texts:
            if isinstance(text, str):
                connection.execute("INSERT INTO texts VALUES (?, ?)", (text, directory))
        arxiv = row.get("arxiv")
        if isinstance(arxiv, str) and arxiv:
            connection.execute(
                "INSERT INTO arxiv VALUES (?, ?)", (arxiv_base(arxiv), directory)
            )
        for candidate in items(row.get("doi_candidates", [])):
            doi = normalize_doi(str(candidate))
            if doi is not None:
                connection.execute(
                    "INSERT INTO candidates VALUES (?, ?)", (doi, directory)
                )
        body, descriptions = _body(library / directory, row)
        connection.execute(
            "INSERT INTO fulltext VALUES (?, ?, ?, ?, ?, ?)",
            (
                directory,
                " ".join(
                    str(v) for v in (row.get("title"), row.get("title_observed")) if v
                ),
                " ".join(authors + observed),
                _text(row.get("abstract")) or "",
                body,
                descriptions,
            ),
        )


def build_index(
    library: Path, rows: Sequence[Mapping[str, object]] | None = None
) -> Path:
    """Build ``.paperextract/index.sqlite`` from the catalog.

    Parameters
    ----------
    library : Path
        Library root.
    rows : Sequence of Mapping or None
        Catalog rows, or None to read ``catalog.jsonl``.

    Returns
    -------
    Path
        Index file, replaced atomically.
    """
    rows = read_catalog(library) if rows is None else rows
    target = library / INDEX_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".sqlite.tmp")
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(_SCHEMA)
        catalog = library / CATALOG_FILENAME
        digest = (
            hashlib.sha256(catalog.read_bytes()).hexdigest()
            if catalog.is_file()
            else ""
        )
        connection.executemany(
            "INSERT INTO meta VALUES (?, ?)",
            [("schema_version", str(INDEX_SCHEMA_VERSION)), ("catalog_sha256", digest)],
        )
        _fill(connection, library, rows)
        connection.commit()
    finally:
        connection.close()
    temporary.replace(target)
    return target


def _csl_item(paper: Path, row: Mapping[str, object]) -> dict[str, object] | None:
    """Build a CSL-JSON item for a validated paper.

    Parameters
    ----------
    paper : Path
        Paper directory.
    row : Mapping of str to object
        Catalog row.

    Returns
    -------
    dict of str to object or None
        Item, or None when the identity is not validated.
    """
    if row.get("bibliographic_status") not in ("VALIDATED", "VALIDATED_WITH_WARNINGS"):
        return None
    fields = mapping(
        mapping(json.loads((paper / "metadata.json").read_text()))["fields"]
    )

    def value(name: str) -> object:
        """Return one metadata field value.

        Parameters
        ----------
        name : str
            Field name.

        Returns
        -------
        object
            Value, or None when the field is absent.
        """
        entry = fields.get(name)
        return None if entry is None else mapping(entry).get("value")

    authors = [
        {
            key: author[key]
            for key in ("family", "given", "literal")
            if isinstance(author.get(key), str)
        }
        for author in (mapping(item) for item in items(value("authors") or []))
    ]
    item: dict[str, object] = {
        "id": row.get("citation_key") or row["directory"],
        "type": _CSL_TYPES.get(str(value("article_type")), "article"),
        "title": value("title"),
        "author": authors,
        "container-title": value("journal"),
        "volume": value("volume"),
        "issue": value("issue"),
        "page": value("pages") or value("article_number"),
        "DOI": value("doi"),
        "URL": value("url"),
        "publisher": value("publisher"),
    }
    year = value("year")
    if isinstance(year, int):
        item["issued"] = {"date-parts": [[year]]}
    return {key: entry for key, entry in item.items() if entry not in (None, [], "")}


def _cell(value: object) -> str:
    """Format a value for a Markdown table cell.

    Parameters
    ----------
    value : object
        Value.

    Returns
    -------
    str
        Text with pipes escaped and line breaks removed.
    """
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _catalog_markdown(rows: Sequence[Mapping[str, object]]) -> str:
    """Render the human-readable catalog.

    Parameters
    ----------
    rows : Sequence of Mapping
        Catalog rows.

    Returns
    -------
    str
        Markdown table sorted by first author, year and directory.
    """
    ordered = sorted(
        rows,
        key=lambda row: (
            str(row.get("first_author_family") or "~"),
            str(row.get("year") or ""),
            str(row["directory"]),
        ),
    )
    lines = [
        "# Library catalog",
        "",
        f"Derived from `{CATALOG_FILENAME}`; {len(rows)} papers. Titles in italics "
        "are observed in the PDF, not validated.",
        "",
        "| Paper | First author | Year | Title | DOI | Identity | Processing |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in ordered:
        directory = str(row["directory"])
        title = row.get("title")
        shown = (
            _cell(title)
            if title
            else f"*{_cell(row.get('title_observed') or 'untitled')}*"
        )
        lines.append(
            f"| [{_cell(directory)}]({directory}/paper.md) "
            f"| {_cell(row.get('first_author_family'))} | {_cell(row.get('year'))} "
            f"| {shown} | {_cell(row.get('doi'))} "
            f"| {_cell(row.get('bibliographic_status'))} "
            f"| {_cell(row.get('processing_status'))} |"
        )
    return "\n".join(lines) + "\n"


def _write(path: Path, text: str) -> None:
    """Replace a derived file atomically.

    Parameters
    ----------
    path : Path
        Target.
    text : str
        Content.
    """
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def refresh_index(library: Path) -> list[dict[str, object]]:
    """Rebuild the index and the derived catalogs from ``catalog.jsonl``.

    Parameters
    ----------
    library : Path
        Library root.

    Returns
    -------
    list of dict
        Catalog rows that were indexed.
    """
    rows = list(read_catalog(library))
    build_index(library, rows)
    _write(library / CATALOG_MARKDOWN, _catalog_markdown(rows))
    entries: list[str] = []
    csl: list[dict[str, object]] = []
    for row in sorted(rows, key=lambda r: str(r["directory"])):
        paper = library / str(row["directory"])
        bib = paper / "citation.bib"
        if bib.is_file():
            entries.append(
                f"% {row['directory']}\n{bib.read_text(encoding='utf-8').strip()}\n"
            )
        item = _csl_item(paper, row)
        if item is not None:
            csl.append(item)
    _write(library / LIBRARY_BIB, "\n".join(entries))
    _write(library / CATALOG_CSL, json.dumps(csl, indent=2, ensure_ascii=False) + "\n")
    return rows


def _connect(library: Path) -> sqlite3.Connection:
    """Open the index, rebuilding it when it is missing or stale.

    Parameters
    ----------
    library : Path
        Library root.

    Returns
    -------
    sqlite3.Connection
        Read connection with row access by name.
    """
    path = library / INDEX_PATH
    catalog = library / CATALOG_FILENAME
    current = ""
    version = ""
    if path.is_file():
        probe = sqlite3.connect(path)
        try:
            meta = dict(probe.execute("SELECT key, value FROM meta").fetchall())
            current = str(meta.get("catalog_sha256", ""))
            version = str(meta.get("schema_version", ""))
        finally:
            probe.close()
    expected = (
        hashlib.sha256(catalog.read_bytes()).hexdigest() if catalog.is_file() else ""
    )
    if (
        not path.is_file()
        or current != expected
        or version != str(INDEX_SCHEMA_VERSION)
    ):
        build_index(library)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


@dataclass(frozen=True)
class Query:
    """Describe what to look up.

    Attributes
    ----------
    label : str
        What the query came from, for the report.
    doi : str or None
        Normalized DOI.
    sha256 : str or None
        Digest of a file's bytes.
    text_sha256 : str or None
        Whole-text digest of a file.
    doi_candidates : tuple of str
        DOI strings seen in a file.
    arxiv : str or None
        arXiv identifier asked for.
    arxiv_candidates : tuple of str
        arXiv identifiers seen in a file.
    title : str or None
        Title text.
    author : str or None
        First author's family name.
    year : int or None
        Publication year.
    first_page : str or None
        Text of a file's first page, in which a library paper's title may
        appear.
    """

    label: str
    doi: str | None = None
    sha256: str | None = None
    text_sha256: str | None = None
    doi_candidates: tuple[str, ...] = field(default_factory=tuple)
    arxiv: str | None = None
    arxiv_candidates: tuple[str, ...] = field(default_factory=tuple)
    title: str | None = None
    author: str | None = None
    year: int | None = None
    first_page: str | None = None


@dataclass(frozen=True)
class Match:
    """Report one library's answer to a query.

    Attributes
    ----------
    query : str
        Query label.
    status : str
        ``present``, ``related``, ``candidate`` or ``absent``.
    library : str
        Library root.
    directory : str or None
        Matching paper directory.
    reason : str
        Evidence for the status.
    doi : str or None
        DOI of the matching paper.
    title : str or None
        Validated or observed title of the matching paper.
    """

    query: str
    status: Status
    library: str
    directory: str | None
    reason: str
    doi: str | None = None
    title: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize the match.

        Returns
        -------
        dict of str to object
            JSON-compatible fields.
        """
        return {
            "query": self.query,
            "status": self.status,
            "library": self.library,
            "directory": self.directory,
            "reason": self.reason,
            "doi": self.doi,
            "title": self.title,
        }


def query_from_file(path: Path) -> Query:
    """Describe a PDF by its bytes, text and DOI strings.

    Parameters
    ----------
    path : Path
        PDF file.

    Returns
    -------
    Query
        Digest, whole-text digest, DOI candidates and first-page text.
    """
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    fingerprint = fingerprint_pdf(path)
    characters = sum(page.characters for page in fingerprint.pages)
    return Query(
        label=str(path),
        sha256=digest,
        text_sha256=fingerprint.text_sha256 if characters else None,
        doi_candidates=tuple(
            doi
            for doi in (
                *(normalize_doi(c) for c in fingerprint.doi_candidates),
                *(arxiv_doi(c) for c in fingerprint.arxiv_candidates),
            )
            if doi
        ),
        arxiv_candidates=fingerprint.arxiv_candidates,
        first_page=page_text(path) or None,
    )


def _bibtex_entries(text: str) -> list[dict[str, str]]:
    """Read the fields of every entry in a BibTeX file, tolerantly.

    Parameters
    ----------
    text : str
        File content.

    Returns
    -------
    list of dict
        Lower-case field names to values with outer braces or quotes removed.
    """
    entries: list[dict[str, str]] = []
    for start in (m.end() for m in re.finditer(r"@\w+\s*\{[^,]*,", text)):
        depth, position = 1, start
        while position < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[position], 0)
            position += 1
        body = text[start : position - 1]
        fields: dict[str, str] = {}
        for match in re.finditer(r"(\w+)\s*=\s*", body):
            index = match.end()
            if index >= len(body):
                continue
            if body[index] == "{":
                level, end = 1, index + 1
                while end < len(body) and level:
                    level += {"{": 1, "}": -1}.get(body[end], 0)
                    end += 1
                value = body[index + 1 : end - 1]
            elif body[index] == '"':
                end = body.find('"', index + 1)
                value = body[index + 1 : end if end > 0 else len(body)]
            else:
                value = re.split(r"[,\s]", body[index:], maxsplit=1)[0]
            fields.setdefault(
                match.group(1).lower(), re.sub(r"[{}]", "", value).strip()
            )
        entries.append(fields)
    return entries


def queries_from_bibtex(text: str) -> list[Query]:
    """Turn every BibTeX entry into a query.

    Parameters
    ----------
    text : str
        BibTeX file content.

    Returns
    -------
    list of Query
        One query per entry with DOI, title, first author and year.
    """
    queries: list[Query] = []
    for number, fields in enumerate(_bibtex_entries(text), 1):
        author = fields.get("author", "").split(" and ")[0]
        family = author.split(",")[0] if "," in author else (author.split() or [""])[-1]
        year = fields.get("year", "")
        queries.append(
            Query(
                label=f"bibtex entry {number}",
                doi=normalize_doi(fields.get("doi", "")),
                title=fields.get("title") or None,
                author=family.strip() or None,
                year=int(year) if year.isdigit() else None,
            )
        )
    return queries


def _overlap(first: str, second: str) -> float:
    """Compute the token overlap of two title keys.

    Parameters
    ----------
    first : str
        Title key.
    second : str
        Title key.

    Returns
    -------
    float
        Intersection over union of the tokens.
    """
    left, right = set(first.split()), set(second.split())
    return len(left & right) / len(left | right) if left | right else 0.0


def _title_matches(
    connection: sqlite3.Connection, query: Query
) -> list[tuple[sqlite3.Row, str]]:
    """Find papers whose title resembles the query's.

    Parameters
    ----------
    connection : sqlite3.Connection
        Index connection.
    query : Query
        Query with a title.

    Returns
    -------
    list of tuple
        Rows with the reason, for titles with at least
        :data:`_CANDIDATE_OVERLAP` token overlap whose author and year agree
        when the query gives them.
    """
    wanted = title_key(query.title or "")
    found: list[tuple[sqlite3.Row, str]] = []
    for row in connection.execute("SELECT * FROM papers"):
        keys = [k for k in (row["title_key"], row["title_key_observed"]) if k]
        overlap = max((_overlap(wanted, key) for key in keys), default=0.0)
        if overlap < _CANDIDATE_OVERLAP:
            continue
        names = [row["first_author_family"] or "", *json.loads(row["authors_observed"])]
        if query.author and not any(
            title_key(query.author) in title_key(name) for name in names if name
        ):
            continue
        if (
            query.year
            and row["year"]
            and abs(query.year - row["year"]) > _YEAR_TOLERANCE
        ):
            continue
        exact = "equal" if overlap == 1.0 else f"{overlap:.2f} token overlap"
        found.append((row, f"title {exact}; titles alone never prove identity"))
    return found


def _printed_titles(
    connection: sqlite3.Connection, first_page: str
) -> list[tuple[sqlite3.Row, str]]:
    """Find library papers whose whole title is printed on a file's first page.

    Parameters
    ----------
    connection : sqlite3.Connection
        Index connection.
    first_page : str
        First-page text of the file.

    Returns
    -------
    list of tuple
        Rows with the reason, for titles of at least four words.

    Notes
    -----
    Only the start of the title must fall on a word boundary, because a
    footnote marker is often joined to its last word, as in "fibresa".
    """
    page = f" {title_key(first_page)} "
    reason = (
        "the paper's title is printed on the file's first page; another version "
        "or a citing work is possible"
    )
    found: list[tuple[sqlite3.Row, str]] = []
    for row in connection.execute("SELECT * FROM papers"):
        keys = [row["title_key"], row["title_key_observed"]]
        if any(
            key and len(key.split()) >= _MIN_PRINTED_TITLE_WORDS and f" {key}" in page
            for key in keys
        ):
            found.append((row, reason))
    return found


def _match(
    query: Query, status: Status, library: Path, row: sqlite3.Row, reason: str
) -> Match:
    """Build a match from an index row.

    Parameters
    ----------
    query : Query
        Query.
    status : str
        Status.
    library : Path
        Library root.
    row : sqlite3.Row
        Paper row.
    reason : str
        Evidence.

    Returns
    -------
    Match
        Match record.
    """
    return Match(
        query.label,
        status,
        str(library),
        row["directory"],
        reason,
        row["doi"],
        row["title"] or row["title_observed"],
    )


def _paper(connection: sqlite3.Connection, directory: str) -> sqlite3.Row:
    """Fetch a paper row.

    Parameters
    ----------
    connection : sqlite3.Connection
        Index connection.
    directory : str
        Paper directory.

    Returns
    -------
    sqlite3.Row
        Row.
    """
    return cast(
        "sqlite3.Row",
        connection.execute(
            "SELECT * FROM papers WHERE directory = ?", (directory,)
        ).fetchone(),
    )


def _evidence(query: Query) -> list[tuple[Status, str, tuple[str, ...], str]]:
    """List the index queries that answer a lookup, strongest first.

    Parameters
    ----------
    query : Query
        Lookup query.

    Returns
    -------
    list of tuple
        Status, SQL selecting ``directory``, parameters and reason.
    """
    steps: list[tuple[Status, str, tuple[str, ...], str]] = []
    if query.sha256:
        steps.append(
            (
                "present",
                "SELECT directory FROM sources WHERE sha256 = ?",
                (query.sha256,),
                "identical bytes",
            )
        )
    if query.text_sha256:
        steps.append(
            (
                "present",
                "SELECT directory FROM texts WHERE text_sha256 = ?",
                (query.text_sha256,),
                "identical text on every page",
            )
        )
    if query.arxiv:
        steps.append(
            (
                "present",
                "SELECT directory FROM arxiv WHERE identifier = ?",
                (arxiv_base(query.arxiv),),
                f"arXiv identifier {query.arxiv}",
            )
        )
    for identifier in query.arxiv_candidates:
        steps.append(
            (
                "related",
                "SELECT directory FROM arxiv WHERE identifier = ?",
                (arxiv_base(identifier),),
                f"the file mentions the paper's arXiv identifier {identifier}",
            )
        )
    if query.doi:
        steps.append(
            (
                "present",
                "SELECT directory FROM papers WHERE doi = ?",
                (query.doi,),
                f"validated DOI {query.doi}",
            )
        )
    for doi in query.doi_candidates:
        steps.append(
            (
                "related",
                "SELECT directory FROM papers WHERE doi = ?",
                (doi,),
                f"the file mentions the paper's DOI {doi}",
            )
        )
        steps.append(
            (
                "related",
                "SELECT directory FROM candidates WHERE doi = ?",
                (doi,),
                f"both mention DOI {doi}",
            )
        )
    return steps


def _lookup_one(library: Path, query: Query) -> list[Match]:
    """Answer one query in one library.

    Parameters
    ----------
    library : Path
        Library root.
    query : Query
        Query.

    Returns
    -------
    list of Match
        Matches, strongest evidence first and one per paper, or one
        ``absent`` match.
    """
    connection = _connect(library)
    try:
        found: list[tuple[Status, str, str]] = []
        for status, sql, parameters, reason in _evidence(query):
            found.extend(
                (status, row["directory"], reason)
                for row in connection.execute(sql, parameters)
            )
        if query.title:
            found.extend(
                ("candidate", row["directory"], reason)
                for row, reason in _title_matches(connection, query)
            )
        if query.first_page:
            found.extend(
                ("candidate", row["directory"], reason)
                for row, reason in _printed_titles(connection, query.first_page)
            )
        matches: list[Match] = []
        seen: set[str] = set()
        for status, directory, reason in found:
            if directory not in seen:
                seen.add(directory)
                row = _paper(connection, directory)
                matches.append(_match(query, status, library, row, reason))
        if not matches:
            matches.append(
                Match(query.label, "absent", str(library), None, "no evidence")
            )
        return matches
    finally:
        connection.close()


def lookup(libraries: Iterable[Path], query: Query) -> list[Match]:
    """Answer "is this paper already in my libraries?".

    Parameters
    ----------
    libraries : Iterable of Path
        Library roots.
    query : Query
        What to look for.

    Returns
    -------
    list of Match
        Every match in every library. ``present`` means identical bytes,
        identical text or the same validated DOI; ``related`` means the file
        mentions a paper's DOI, as another version or a citing work might;
        ``candidate`` means a similar title, which never proves identity.
    """
    return [match for library in libraries for match in _lookup_one(library, query)]


@dataclass(frozen=True)
class SearchHit:
    """Report one full-text search result.

    Attributes
    ----------
    library : str
        Library root.
    directory : str
        Paper directory.
    title : str or None
        Validated or observed title.
    year : int or None
        Year.
    doi : str or None
        DOI.
    score : float
        BM25 rank; smaller is more relevant.
    snippet : str
        Matching passage with the terms in ``[...]``.
    in_description : bool
        The passage comes from a machine-generated figure description, not
        from the paper's own text.
    """

    library: str
    directory: str
    title: str | None
    year: int | None
    doi: str | None
    score: float
    snippet: str
    in_description: bool = False

    def to_dict(self) -> dict[str, object]:
        """Serialize the hit.

        Returns
        -------
        dict of str to object
            JSON-compatible fields.
        """
        return {
            "library": self.library,
            "directory": self.directory,
            "title": self.title,
            "year": self.year,
            "doi": self.doi,
            "score": self.score,
            "snippet": self.snippet,
            "in_description": self.in_description,
        }


_QUERY_PART = re.compile(r'"([^"]*)"|(\S+)')


def _fts_query(text: str) -> str:
    """Turn a free-text query into an FTS5 query.

    Parameters
    ----------
    text : str
        User query; ``"quoted words"`` and hyphenated words such as
        ``self-compression`` are phrases.

    Returns
    -------
    str
        Query of quoted terms and phrases, all required.

    Examples
    --------
    >>> _fts_query('self-compression "hollow fibre" HCF')
    '"self compression" "hollow fibre" "HCF"'
    """
    parts: list[str] = []
    for quoted, token in _QUERY_PART.findall(text):
        words = _WORD.findall(quoted or token)
        if words:
            parts.append('"' + " ".join(words) + '"')
    return " ".join(parts)


# Title matches weigh most, then authors, abstract, body text and, least,
# machine-generated figure descriptions.
_SEARCH_SQL = (
    "SELECT f.directory AS directory, "
    "bm25(fulltext, 0, 10, 5, 3, 1, 0.5) AS score, "
    "snippet(fulltext, 4, char(57344), char(57345), '…', 12) AS snippet, "
    "snippet(fulltext, 5, char(57344), char(57345), '…', 12) AS described, "
    "p.title AS title, p.title_observed AS observed, p.year AS year, "
    "p.doi AS doi FROM fulltext f "
    "JOIN papers p ON p.directory = f.directory "
    "WHERE fulltext MATCH ? ORDER BY score LIMIT ?"
)


def search(libraries: Iterable[Path], text: str, *, limit: int = 10) -> list[SearchHit]:
    """Search titles, authors, abstracts and text of every paper.

    Parameters
    ----------
    libraries : Iterable of Path
        Library roots.
    text : str
        Words that must all occur; diacritics and case are ignored.
    limit : int
        Results per library.

    Returns
    -------
    list of SearchHit
        Hits ordered by relevance across libraries.
    """
    terms = _fts_query(text)
    if not terms:
        return []
    hits: list[SearchHit] = []
    for library in libraries:
        connection = _connect(library)
        try:
            rows = connection.execute(_SEARCH_SQL, (terms, limit)).fetchall()
        finally:
            connection.close()
        for row in rows:
            # Matches are marked with private-use characters, which paper text
            # does not contain, so a column without a match is recognizable.
            described = _MATCH_OPEN not in row["snippet"] and (
                _MATCH_OPEN in row["described"]
            )
            passage = row["described"] if described else row["snippet"]
            hits.append(
                SearchHit(
                    str(library),
                    row["directory"],
                    row["title"] or row["observed"],
                    row["year"],
                    row["doi"],
                    float(row["score"]),
                    passage.replace(_MATCH_OPEN, "[").replace(_MATCH_CLOSE, "]"),
                    in_description=described,
                )
            )
    return sorted(hits, key=lambda hit: hit.score)
