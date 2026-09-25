"""Establish bibliographic identity from registry records and local evidence.

A DOI candidate found in a PDF is only a candidate. The identity stage resolves
it through a registry, compares the registration metadata with what the article
itself shows, and records the outcome field by field with the evidence used.
Fuzzy agreement can raise a warning; it never merges works or invents values.
"""

from __future__ import annotations

import difflib
import hashlib
import html
import json
import re
import unicodedata
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, cast

from paperextract.bibread import read_entries
from paperextract.document import (
    Document,
    Figure,
    Heading,
    InlineRun,
    ListBlock,
    PageFurniture,
    Paragraph,
    Table,
    plain_text,
)
from paperextract.export import plausible_title
from paperextract.fields import (
    choice,
    dump,
    integer,
    items,
    mapping,
    record,
    string,
    text,
)
from paperextract.ingest import looks_like_supplement
from paperextract.registry import (
    Lookup,
    RegistryFailure,
    RegistryRecord,
    Search,
    normalize_doi,
)

__all__ = [
    "IDENTITY_SCHEMA",
    "IDENTITY_VERSION",
    "Candidate",
    "Check",
    "Field",
    "Identity",
    "Observed",
    "arxiv_base",
    "arxiv_doi",
    "arxiv_identifier",
    "clean_title",
    "collect_candidates",
    "compare_record",
    "filename_candidates",
    "identity_from_bibtex",
    "math_as_text",
    "observe",
    "resolve_identity",
    "same_title",
    "title_agreement",
    "title_candidates",
    "title_key",
]

IDENTITY_SCHEMA = "paperextract.identity"
IDENTITY_VERSION = 1
Status = Literal[
    "VALIDATED", "VALIDATED_WITH_WARNINGS", "ASSERTED", "UNVERIFIED", "CONFLICT"
]
Outcome = Literal["pass", "warn", "fail", "not_checked"]

_DOI_IN_TEXT = re.compile(r"10\.\d{4,9}/[^\s\"<>]+")
_PII = re.compile(
    r"\bPII:?\s*S?(\d{4}-\d{3}[\dX]\s*\(\d{2}\)\s*\d{5}-[\dX])", re.IGNORECASE
)
_ARTICLES = frozenset({"a", "an", "the"})
_STRONG_SOURCES = frozenset({"pdf_information", "page_1_furniture"})
_MAX_CANDIDATES = 5
_MARKER_LENGTH = 2
_TITLE_OVERLAP_WARN = 0.7
_YEAR_TOLERANCE = 1
_FIELD_NAMES = (
    "title",
    "authors",
    "journal",
    "publisher",
    "doi",
    "volume",
    "issue",
    "pages",
    "article_number",
    "year",
    "published_online",
    "published_print",
    "url",
    "issn",
    "license",
    "article_type",
)


def title_key(title: str) -> str:
    """Normalize a title into a comparison key that never authorizes a merge.

    Parameters
    ----------
    title : str
        Observed or registry title.

    Returns
    -------
    str
        Case-folded letters and digits without diacritics or punctuation,
        single-spaced, with a leading article dropped.
    """
    decomposed = unicodedata.normalize("NFKD", title)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    words = "".join(ch if ch.isalnum() else " " for ch in stripped.casefold()).split()
    if len(words) > 1 and words[0] in _ARTICLES:
        words = words[1:]
    return " ".join(words)


@dataclass(frozen=True)
class Candidate:
    """Hold one identifier candidate and where it was seen.

    Attributes
    ----------
    value : str
        Normalized identifier.
    kind : str
        ``doi`` or ``arxiv``.
    sources : tuple of str
        Evidence locations such as ``pdf_information`` or ``page_1_text``.
    """

    value: str
    kind: Literal["doi", "arxiv"]
    sources: tuple[str, ...]

    def strong(self) -> bool:
        """Report whether the candidate comes from article-level metadata.

        Returns
        -------
        bool
            True for the information dictionary or first-page running text.
        """
        return any(source in _STRONG_SOURCES for source in self.sources)

    def to_dict(self) -> dict[str, object]:
        """Serialize the candidate.

        Returns
        -------
        dict of str to object
            JSON-compatible fields.
        """
        return {"value": self.value, "kind": self.kind, "sources": list(self.sources)}

    @classmethod
    def from_dict(cls, value: object) -> Candidate:
        """Parse a serialized candidate.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        Candidate
            Validated candidate.
        """
        data = record(value, "value kind sources")
        return cls(
            value=string(data["value"]),
            kind=cast("Literal['doi', 'arxiv']", choice(data["kind"], "doi arxiv")),
            sources=tuple(string(item) for item in items(data["sources"])),
        )


@dataclass(frozen=True)
class Observed:
    """Hold article-local metadata observations used for comparison.

    Attributes
    ----------
    title : str or None
        Observed title.
    authors : tuple of str
        Observed author strings.
    years : tuple of int
        Year hints from PDF dates; weak evidence.
    """

    title: str | None
    authors: tuple[str, ...]
    years: tuple[int, ...]

    def to_dict(self) -> dict[str, object]:
        """Serialize the observations.

        Returns
        -------
        dict of str to object
            JSON-compatible fields.
        """
        return {
            "title": self.title,
            "authors": list(self.authors),
            "years": list(self.years),
        }

    @classmethod
    def from_dict(cls, value: object) -> Observed:
        """Parse serialized observations.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        Observed
            Validated observations.
        """
        data = record(value, "title authors years")
        title = data["title"]
        return cls(
            title=None if title is None else string(title),
            authors=tuple(string(item) for item in items(data["authors"])),
            years=tuple(integer(item, minimum=1) for item in items(data["years"])),
        )


@dataclass(frozen=True)
class Check:
    """Record one comparison between registry and article evidence.

    Attributes
    ----------
    name : str
        ``title``, ``authors`` or ``year``.
    outcome : str
        ``pass``, ``warn``, ``fail`` or ``not_checked``.
    detail : str
        Explanation with the compared values.
    """

    name: str
    outcome: Outcome
    detail: str

    def to_dict(self) -> dict[str, object]:
        """Serialize the check.

        Returns
        -------
        dict of str to object
            JSON-compatible fields.
        """
        return {"name": self.name, "outcome": self.outcome, "detail": self.detail}

    @classmethod
    def from_dict(cls, value: object) -> Check:
        """Parse a serialized check.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        Check
            Validated check.
        """
        data = record(value, "name outcome detail")
        return cls(
            name=string(data["name"]),
            outcome=cast(
                "Outcome", choice(data["outcome"], "pass warn fail not_checked")
            ),
            detail=text(data["detail"]),
        )


@dataclass(frozen=True)
class Field:
    """Hold one canonical metadata field with its status and evidence.

    Attributes
    ----------
    name : str
        Field name.
    value : object
        Selected value: string, integer, list or None when unavailable.
    status : str
        Bibliographic status of the value.
    source : str or None
        Provider or evidence that supplied the value.
    """

    name: str
    value: object
    status: Status
    source: str | None

    def to_dict(self) -> dict[str, object]:
        """Serialize the field.

        Returns
        -------
        dict of str to object
            JSON-compatible fields.
        """
        return {
            "name": self.name,
            "value": self.value,
            "status": self.status,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, value: object) -> Field:
        """Parse a serialized field.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        Field
            Validated field.
        """
        data = record(value, "name value status source")
        source = data["source"]
        return cls(
            name=string(data["name"]),
            value=data["value"],
            status=cast("Status", choice(data["status"], _STATUSES)),
            source=None if source is None else string(source),
        )


_STATUSES = "VALIDATED VALIDATED_WITH_WARNINGS ASSERTED UNVERIFIED CONFLICT"


@dataclass(frozen=True)
class Identity:
    """Hold the bibliographic identity decision for one document.

    Attributes
    ----------
    status : str
        Overall bibliographic status.
    work_id : str or None
        Stable local work identifier derived from the validated DOI.
    doi : str or None
        Accepted DOI.
    provider : str or None
        Registry that supplied the accepted record.
    record : Mapping of str to object or None
        Serialized accepted registry record.
    fields : tuple of Field
        Canonical fields with status and source.
    checks : tuple of Check
        Comparisons for the accepted candidate.
    candidates : tuple of Candidate
        Every identifier candidate considered.
    alternatives : tuple of Mapping
        Outcomes of candidates that were not accepted.
    observed : Observed
        Article-local observations used.
    reasons : tuple of str
        Human-readable reasons for the status.
    """

    status: Status
    work_id: str | None
    doi: str | None
    provider: str | None
    record: Mapping[str, object] | None
    fields: tuple[Field, ...]
    checks: tuple[Check, ...]
    candidates: tuple[Candidate, ...]
    alternatives: tuple[Mapping[str, object], ...]
    observed: Observed
    reasons: tuple[str, ...]

    def validated(self) -> bool:
        """Report whether a registry record confirmed the identity.

        Returns
        -------
        bool
            True for ``VALIDATED`` and ``VALIDATED_WITH_WARNINGS``.
        """
        return self.status in ("VALIDATED", "VALIDATED_WITH_WARNINGS")

    def named(self) -> bool:
        """Report whether the identity may name directories and citations.

        Returns
        -------
        bool
            True for a validated identity and for one the user asserted
            (``ASSERTED``), which is never reported as validated.
        """
        return self.validated() or self.status == "ASSERTED"

    def field(self, name: str) -> object:
        """Return a field value.

        Parameters
        ----------
        name : str
            Field name.

        Returns
        -------
        object
            The value, or None when absent.
        """
        return next((item.value for item in self.fields if item.name == name), None)

    def to_dict(self) -> dict[str, object]:
        """Serialize the identity with its schema envelope.

        Returns
        -------
        dict of str to object
            JSON-compatible document.
        """
        return {
            "schema": IDENTITY_SCHEMA,
            "schema_version": IDENTITY_VERSION,
            "status": self.status,
            "work_id": self.work_id,
            "doi": self.doi,
            "provider": self.provider,
            "record": None if self.record is None else dict(self.record),
            "fields": [item.to_dict() for item in self.fields],
            "checks": [item.to_dict() for item in self.checks],
            "candidates": [item.to_dict() for item in self.candidates],
            "alternatives": [dict(item) for item in self.alternatives],
            "observed": self.observed.to_dict(),
            "reasons": list(self.reasons),
        }

    def to_json(self) -> str:
        """Serialize as stable JSON.

        Returns
        -------
        str
            Indented JSON with sorted keys and a trailing newline.
        """
        return dump(self.to_dict())

    @classmethod
    def from_dict(cls, value: object) -> Identity:
        """Parse a serialized identity.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        Identity
            Validated identity.

        Raises
        ------
        ValueError
            The document has another schema or version.
        """
        data = record(
            value,
            "schema schema_version status work_id doi provider record fields checks "
            "candidates alternatives observed reasons",
        )
        if (
            data["schema"] != IDENTITY_SCHEMA
            or data["schema_version"] != IDENTITY_VERSION
        ):
            raise ValueError("Unsupported identity schema")
        work_id, doi, provider, registry = (
            data["work_id"],
            data["doi"],
            data["provider"],
            data["record"],
        )
        return cls(
            status=cast("Status", choice(data["status"], _STATUSES)),
            work_id=None if work_id is None else string(work_id),
            doi=None if doi is None else string(doi),
            provider=None if provider is None else string(provider),
            record=None if registry is None else mapping(registry),
            fields=tuple(Field.from_dict(item) for item in items(data["fields"])),
            checks=tuple(Check.from_dict(item) for item in items(data["checks"])),
            candidates=tuple(
                Candidate.from_dict(item) for item in items(data["candidates"])
            ),
            alternatives=tuple(mapping(item) for item in items(data["alternatives"])),
            observed=Observed.from_dict(data["observed"]),
            reasons=tuple(string(item) for item in items(data["reasons"])),
        )

    @classmethod
    def from_json(cls, payload: str) -> Identity:
        """Parse a serialized identity from JSON text.

        Parameters
        ----------
        payload : str
            JSON text.

        Returns
        -------
        Identity
            Validated identity.
        """
        return cls.from_dict(json.loads(payload))

    @classmethod
    def unverified(cls, reason: str, observed: Observed | None = None) -> Identity:
        """Build the identity used when no lookup ran or nothing resolved.

        Parameters
        ----------
        reason : str
            Why identity is unverified.
        observed : Observed or None
            Observations, if any.

        Returns
        -------
        Identity
            ``UNVERIFIED`` identity with every field null.
        """
        return cls(
            status="UNVERIFIED",
            work_id=None,
            doi=None,
            provider=None,
            record=None,
            fields=tuple(
                Field(name, None, "UNVERIFIED", None) for name in _FIELD_NAMES
            ),
            checks=(),
            candidates=(),
            alternatives=(),
            observed=observed or Observed(None, (), ()),
            reasons=(reason,),
        )


def _years(document: Document) -> tuple[int, ...]:
    """Read year hints from PDF date observations.

    Parameters
    ----------
    document : Document
        Canonical document.

    Returns
    -------
    tuple of int
        Distinct four-digit years in observation order.
    """
    years: list[int] = []
    for observation in document.metadata:
        if observation.field in ("created_at", "modified_at"):
            match = re.match(r"(\d{4})", observation.value)
            if match and int(match.group(1)) not in years:
                years.append(int(match.group(1)))
    return tuple(years)


_LATEX_COMMAND = re.compile(r"\\[A-Za-z]+\s*")
_LATEX_MARKUP = re.compile(r"[{}_^$\\]")


def math_as_text(latex: str) -> str:
    r"""Flatten simple inline LaTeX into the characters a reader sees.

    Parameters
    ----------
    latex : str
        Inline math such as a chemical formula in a title.

    Returns
    -------
    str
        Text without commands, braces and script markers, for comparing
        titles and searching registries; never used as a transcription.

    Examples
    --------
    >>> math_as_text(r"\mathrm{H}_{2}")
    'H2'
    """
    return _LATEX_MARKUP.sub("", _LATEX_COMMAND.sub("", latex)).replace(" ", "")


_HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_LOST_GLYPH = re.compile(r"\[\?\]|�")
_TRAILING_MARKERS = re.compile(r"[\s*†‡§¶]+$")
_TEX_SIGNS = frozenset("\\_^{}$")


def clean_title(title: str) -> str:
    r"""Remove markup and marks that are not words from an observed title.

    Parameters
    ----------
    title : str
        Title as observed or as a registry delivers it.

    Returns
    -------
    str
        The title without HTML tags and entities, TeX commands and script
        markers, placeholders for glyphs the PDF could not map, and trailing
        footnote markers, single-spaced. Used for comparison and search only;
        the observation keeps the original.

    Examples
    --------
    >>> clean_title("Refractive index of N<sub>2</sub>, H_{2} and O[?]*")
    'Refractive index of N2, H2 and O'
    """
    text = html.unescape(_HTML_TAG.sub("", title))
    if any(sign in text for sign in _TEX_SIGNS):
        text = _LATEX_MARKUP.sub("", _LATEX_COMMAND.sub("", text))
    text = _TRAILING_MARKERS.sub("", _LOST_GLYPH.sub(" ", text))
    return " ".join(text.split())


def _heading_text(block: Heading) -> str:
    """Give a heading's text with inline math flattened and markers dropped.

    Parameters
    ----------
    block : Heading
        Heading block.

    Returns
    -------
    str
        Text with math runs flattened by :func:`math_as_text` and superscript
        footnote markers left out.
    """
    return "".join(
        math_as_text(run.text) if run.kind == "math" else run.text
        for run in block.runs
        if not _footnote_marker(run)
    )


def _observed_title(document: Document) -> str | None:
    """Pick the observed title with inline math flattened.

    Parameters
    ----------
    document : Document
        Canonical document.

    Returns
    -------
    str or None
        The plausible PDF information title, else the first level-1 heading with math
        runs flattened by :func:`math_as_text`, else None.
    """
    titles = title_candidates(document)
    return titles[0] if titles else None


_MAX_TITLES = 4
_TITLE_PAGES = 2
_TITLE_LEVELS = 2
# Standard section names are never a title.
_SECTION_HEADING = re.compile(
    r"^\s*(?:[IVX]+\.?|\d+\.?)?\s*(?:abstract|introduction|background|theory|"
    r"methods?|experimental(?:\s+(?:section|details|methods))?|experiments?|"
    r"results(?:\s+and\s+discussion)?|discussion|conclusions?|summary|"
    r"acknowledge?ments?|references|appendix)\s*$",
    re.IGNORECASE,
)


def title_candidates(document: Document) -> tuple[str, ...]:
    """List the strings that may be the article's title, most likely first.

    Parameters
    ----------
    document : Document
        Canonical document.

    Returns
    -------
    tuple of str
        The plausible PDF information title, then the level-1 headings of
        the first two processed pages, or their level-2 headings when those
        pages have no level-1 heading, distinct and at most four; standard
        section names such as "Introduction" are left out. A running
        header or a journal name set as a heading is among them as often as
        the title, so each is only a candidate that a registry record must
        match.
    """
    found: list[str] = []
    for observation in document.metadata:
        if observation.field == "title" and plausible_title(observation.value):
            found.append(observation.value)
            break
    pages = _leading_pages(document)
    headings = [
        block
        for block in document.blocks
        if isinstance(block, Heading) and block.span.page in pages
    ]
    # Some layouts set the title as a level-2 heading when the page has no
    # level-1 heading at all.
    level = min((h.level for h in headings), default=1)
    for block in headings:
        if block.level == max(level, 1) and block.level <= _TITLE_LEVELS:
            text = _heading_text(block).strip()
            if text and plausible_title(text) and not _SECTION_HEADING.match(text):
                found.append(text)
    distinct = list(dict.fromkeys(found))
    return tuple(distinct[:_MAX_TITLES])


def _leading_pages(document: Document) -> frozenset[int]:
    """Return the first processed pages, where title and authors are printed.

    Parameters
    ----------
    document : Document
        Canonical document.

    Returns
    -------
    frozenset of int
        The first two processed page numbers; two, because downloaded
        articles often begin with a publisher's cover page.
    """
    processed = sorted(p.number for p in document.pages if p.status == "processed")
    return frozenset(processed[:_TITLE_PAGES] or [1])


def _footnote_marker(run: InlineRun) -> bool:
    """Recognize a superscript footnote marker attached to a title.

    Parameters
    ----------
    run : InlineRun
        Heading run.

    Returns
    -------
    bool
        True for a superscript text run of at most two characters, such as
        ``a``, ``*`` or ``1``, which marks a footnote rather than a title word.
    """
    return (
        run.kind == "text"
        and "superscript" in run.styles
        and 0 < len(run.text.strip()) <= _MARKER_LENGTH
    )


def observe(document: Document) -> Observed:
    """Collect the article-local observations used to judge a registry record.

    Parameters
    ----------
    document : Document
        Canonical document.

    Returns
    -------
    Observed
        Title, author strings and year hints; observations, not identity.
    """
    return Observed(
        title=_observed_title(document),
        authors=tuple(m.value for m in document.metadata if m.field == "author"),
        years=_years(document),
    )


_ARXIV_VERSION = re.compile(r"v\d+$")
_ARXIV_DOI_PREFIX = "10.48550/arxiv."


def arxiv_base(identifier: str) -> str:
    """Strip the version and prefix from an arXiv identifier.

    Parameters
    ----------
    identifier : str
        Identifier such as ``arXiv:2206.01062v2``.

    Returns
    -------
    str
        Lower-case unversioned identifier.

    Examples
    --------
    >>> arxiv_base("arXiv:2206.01062v2")
    '2206.01062'
    """
    text = identifier.strip()
    if text.lower().startswith("arxiv:"):
        text = text[len("arxiv:") :]
    return _ARXIV_VERSION.sub("", text).lower()


def arxiv_doi(identifier: str) -> str:
    """Give the DataCite DOI that arXiv registers for an identifier.

    Parameters
    ----------
    identifier : str
        An arXiv identifier, with or without a version.

    Returns
    -------
    str
        DOI of the unversioned identifier.

    Examples
    --------
    >>> arxiv_doi("2206.01062v2")
    '10.48550/arxiv.2206.01062'
    >>> arxiv_doi("hep-th/9901001")
    '10.48550/arxiv.hep-th/9901001'
    """
    return _ARXIV_DOI_PREFIX + arxiv_base(identifier)


def arxiv_identifier(
    identity: Identity, fingerprint: Mapping[str, object]
) -> str | None:
    """Return the arXiv identifier of a document identified through arXiv.

    Parameters
    ----------
    identity : Identity
        Bibliographic identity.
    fingerprint : Mapping of str to object
        Serialized content fingerprint with ``arxiv_candidates``.

    Returns
    -------
    str or None
        The identifier as the file prints it, with its version when printed,
        when the accepted DOI is arXiv's; otherwise None.
    """
    doi = (identity.doi or "").lower()
    if not identity.validated() or not doi.startswith(_ARXIV_DOI_PREFIX):
        return None
    for item in items(fingerprint.get("arxiv_candidates", [])):
        if arxiv_doi(string(item)) == doi:
            return string(item)
    return doi.removeprefix(_ARXIV_DOI_PREFIX)


_OSA_JOURNALS = frozenset(
    {"josa", "josaa", "josab", "ao", "ol", "oe", "optica", "ome", "boe", "prj"}
)
_APS_FILE = re.compile(
    r"\b(PhysRev(?:Lett|Applied|Fluids|Materials|Research|[A-EX])?|RevModPhys)"
    r"\.(\d+)\.(\d+)\b",
    re.IGNORECASE,
)
_OSA_FILE = re.compile(r"\b([a-z]+)-(\d+)-(\d+)-(\d+)\b", re.IGNORECASE)
_NATURE_FILE = re.compile(r"\b(s\d{5}-\d{3}-\d{5}-[\dxyz])\b", re.IGNORECASE)
_ROYAL_FILE = re.compile(r"\b((?:rspa|rspb|rsta|rstb|rsif|rsos)\.\d{4}\.\d{4})\b")
_ACS_FILE = re.compile(r"^([a-z]{2}\d{6,7}[a-z]?)$", re.IGNORECASE)
_ELSEVIER_FILE = re.compile(r"\bS(\d{4})(\d{3}[\dX])(\d{2})(\d{5})([\dX])\b")
_ARXIV_FILE = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")
_ELSEVIER_OLD_STYLE = 60


def filename_candidates(name: str) -> tuple[str, ...]:
    """Derive DOI candidates from publisher file-naming conventions.

    Parameters
    ----------
    name : str
        File name as supplied, possibly percent-encoded.

    Returns
    -------
    tuple of str
        DOIs the name encodes by a publisher's convention: APS
        (``PhysRevA.13.1422``), Optica (``josa-61-1-89``), Springer Nature
        (``s41598-018-34641-y``), the Royal Society (``rspa.1920.0020``),
        ACS (``jp980221f``), an Elsevier PII of an article registered before
        2000 (``1-s2.0-S0092640X83710132-main``) and arXiv (``2206.01062v2``).
        A name is supplied by a person or a download service, so each DOI is
        a weak candidate that the registry record must confirm against the
        article.

    Examples
    --------
    >>> filename_candidates("PhysRevA.13.1422-2.pdf")
    ('10.1103/physreva.13.1422',)
    >>> filename_candidates("ao-47-17-3143.pdf")
    ('10.1364/ao.47.003143',)
    """
    stem = urllib.parse.unquote(PurePosixPath(name).name)
    stem = re.sub(r"\.pdf$", "", stem, flags=re.IGNORECASE)
    found: list[str] = []
    for match in _APS_FILE.finditer(stem):
        found.append(f"10.1103/{match[1]}.{match[2]}.{match[3]}")
    for match in _OSA_FILE.finditer(stem):
        if match[1].lower() in _OSA_JOURNALS:
            found.append(f"10.1364/{match[1]}.{match[2]}.{int(match[4]):06d}")
    found.extend(f"10.1038/{match[1]}" for match in _NATURE_FILE.finditer(stem))
    found.extend(f"10.1098/{match[1]}" for match in _ROYAL_FILE.finditer(stem))
    base = re.sub(r"[-_ ]\d$", "", stem)
    if (acs := _ACS_FILE.match(base)) is not None:
        found.append(f"10.1021/{acs[1]}")
    for match in _ELSEVIER_FILE.finditer(stem):
        if int(match[3]) >= _ELSEVIER_OLD_STYLE:
            found.append(
                f"10.1016/{match[1]}-{match[2]}({match[3]}){match[4]}-{match[5]}"
            )
    if (arxiv := _ARXIV_FILE.match(base)) is not None:
        found.append(arxiv_doi(arxiv[1]))
    normalized = (normalize_doi(doi) for doi in found)
    return tuple(dict.fromkeys(doi for doi in normalized if doi is not None))


def collect_candidates(
    document: Document,
    fingerprint: Mapping[str, object],
    file_names: Sequence[str] = (),
) -> tuple[Candidate, ...]:
    """Gather DOI candidates from article-level evidence, strongest first.

    Parameters
    ----------
    document : Document
        Canonical document.
    fingerprint : Mapping of str to object
        Serialized content fingerprint with ``doi_candidates``.
    file_names : Sequence of str
        Names the source files were supplied under, read by
        :func:`filename_candidates`.

    Returns
    -------
    tuple of Candidate
        Distinct normalized DOIs. Information-dictionary values and first-page
        running headers or footers count as strong sources; strings in
        first-page body text and DOIs encoded in file names are weak; DOIs
        inside reference entries are excluded because they identify cited
        works.
    """
    found: dict[str, list[str]] = {}
    _information_candidates(document, found)
    for block in document.blocks:
        if isinstance(block, PageFurniture) and block.span.page == 1:
            for match in _DOI_IN_TEXT.findall(plain_text(block.runs)):
                _add_candidate(found, match, "page_1_furniture")
    # A publisher's file name is weak but seldom cites another work, so it
    # precedes the page-text scans among the weak candidates.
    for name in file_names:
        for doi in filename_candidates(name):
            _add_candidate(found, doi, "file_name")
    for item in items(fingerprint.get("doi_candidates", [])):
        _add_candidate(found, string(item), "fingerprint")
    # arXiv registers a DataCite DOI for every identifier, so a printed
    # identifier is resolved and checked like a DOI.
    for item in items(fingerprint.get("arxiv_candidates", [])):
        _add_candidate(found, arxiv_doi(string(item)), "arxiv_identifier")
    for block in document.blocks:
        if (
            isinstance(block, Paragraph)
            and block.role == "body"
            and block.span.page == 1
        ):
            for match in _DOI_IN_TEXT.findall(plain_text(block.runs)):
                _add_candidate(found, match, "page_1_text")
    ordered = [Candidate(doi, "doi", tuple(sources)) for doi, sources in found.items()]
    strong = [candidate for candidate in ordered if candidate.strong()]
    weak = [candidate for candidate in ordered if not candidate.strong()]
    return tuple(strong + weak)


def _information_candidates(document: Document, found: dict[str, list[str]]) -> None:
    """Add the DOIs of the PDF information dictionary.

    Parameters
    ----------
    document : Document
        Canonical document.
    found : dict of str to list of str
        Accumulated candidates, extended in place.
    """
    for observation in document.metadata:
        if observation.field in ("identifier", "subject", "description", "title"):
            for match in _DOI_IN_TEXT.findall(observation.value):
                _add_candidate(found, match, "pdf_information")
            # An Elsevier PII such as 0022-4073(81)90057-1 is the suffix of
            # the article's DOI; the derived DOI is weak until the registry
            # record's title agrees with the article.
            for pii in _PII.findall(observation.value):
                _add_candidate(
                    found, "10.1016/" + pii.replace(" ", ""), "pdf_information_pii"
                )


def _add_candidate(found: dict[str, list[str]], raw: str, source: str) -> None:
    """Record a DOI string under its normalized form with its evidence source.

    Parameters
    ----------
    found : dict of str to list of str
        Accumulated candidates.
    raw : str
        DOI string as seen.
    source : str
        Evidence location.
    """
    doi = normalize_doi(raw)
    if doi is not None:
        sources = found.setdefault(doi, [])
        if source not in sources:
            sources.append(source)


def _family_key(name: str) -> str:
    """Normalize a name fragment for containment checks.

    Parameters
    ----------
    name : str
        Name text.

    Returns
    -------
    str
        Title key of the name.
    """
    return title_key(name)


def _title_check(registry_title: str, titles: Sequence[str]) -> Check:
    """Check a registry title against the article's title candidates.

    Parameters
    ----------
    registry_title : str
        Registry title.
    titles : Sequence of str
        Observed title candidates, most likely first; not empty.

    Returns
    -------
    Check
        ``pass`` when a candidate is the same title, ``warn`` when one agrees
        only apart from markup, lost characters or an appended journal name,
        or when the best overlap is high; otherwise ``fail``.
    """
    grades = [(title_agreement(title, registry_title), title) for title in titles]
    if any(grade == "same" for grade, _ in grades):
        return Check("title", "pass", f"Titles agree: {registry_title!r}")
    for grade, title in grades:
        if grade == "near":
            return Check(
                "title",
                "warn",
                f"Observed {title!r} agrees with registry {registry_title!r} apart "
                "from markup, lost characters or an appended name",
            )
    right = title_key(clean_title(registry_title)).split()
    overlap, title = max(
        (_overlap(title_key(clean_title(title)).split(), right), title)
        for title in titles
    )
    outcome: Outcome = "warn" if overlap >= _TITLE_OVERLAP_WARN else "fail"
    return Check(
        "title",
        outcome,
        f"Observed {title!r} versus registry {registry_title!r} "
        f"(token overlap {overlap:.2f})",
    )


def compare_record(
    registry: RegistryRecord, observed: Observed, titles: Sequence[str] = ()
) -> tuple[Check, ...]:
    """Compare a registry record with article-local observations.

    Parameters
    ----------
    registry : RegistryRecord
        Registration metadata.
    observed : Observed
        Observations from the document.
    titles : Sequence of str
        Further title candidates, such as other headings of the first pages,
        tried after the observed title.

    Returns
    -------
    tuple of Check
        Title, authors and year checks with their evidence.
    """
    checks: list[Check] = []
    candidates = [
        *([] if observed.title is None else [observed.title]),
        *(title for title in titles if title != observed.title),
    ]
    if not candidates or registry.title is None:
        checks.append(Check("title", "not_checked", "No observed or registry title"))
    else:
        checks.append(_title_check(registry.title, candidates))
    families = [_family_key(a.family or a.literal or "") for a in registry.authors]
    if not observed.authors or not any(families):
        checks.append(
            Check("authors", "not_checked", "No observed or registry authors")
        )
    else:
        matched = [
            name
            for name in observed.authors
            if any(family and family in _family_key(name) for family in families)
        ]
        if len(matched) == len(observed.authors):
            outcome = "pass"
        elif matched:
            outcome = "warn"
        else:
            outcome = "fail"
        checks.append(
            Check(
                "authors",
                outcome,
                f"{len(matched)} of {len(observed.authors)} observed author(s) appear "
                f"among {len(families)} registry author(s)",
            )
        )
    checks[0] = _secondary_title_guard(checks[0], checks[1], registry, candidates)
    year = registry.year
    if year is None or not observed.years:
        checks.append(Check("year", "not_checked", "No registry year or PDF date hint"))
    else:
        nearest = min(abs(hint - year) for hint in observed.years)
        outcome = "pass" if nearest <= _YEAR_TOLERANCE else "warn"
        checks.append(
            Check(
                "year",
                outcome,
                f"Registry year {year}; PDF date hints {list(observed.years)}",
            )
        )
    return tuple(checks)


def _secondary_title_guard(
    title: Check, authors: Check, registry: RegistryRecord, candidates: Sequence[str]
) -> Check:
    """Refuse a title that only a secondary candidate matches without authors.

    Parameters
    ----------
    title : Check
        Title check.
    authors : Check
        Author check.
    registry : RegistryRecord
        Registry record.
    candidates : Sequence of str
        Title candidates, the main one first.

    Returns
    -------
    Check
        The title check, turned into a failure when the record agrees only
        with a later candidate and no observed author agrees with it: a page
        that carries two letters shows both titles, and the other letter's
        DOI must not be accepted for this one.
    """
    if (
        title.outcome == "fail"
        or registry.title is None
        or authors.outcome != "fail"
        or title_agreement(candidates[0], registry.title) != "different"
    ):
        return title
    return Check(
        "title",
        "fail",
        f"Registry title {registry.title!r} matches only a secondary heading, and "
        "no observed author agrees",
    )


def _overlap(left: Sequence[str], right: Sequence[str]) -> float:
    """Compute the Jaccard overlap of two token sequences.

    Parameters
    ----------
    left : Sequence of str
        Tokens.
    right : Sequence of str
        Tokens.

    Returns
    -------
    float
        Intersection over union, zero when both are empty.
    """
    a, b = set(left), set(right)
    return len(a & b) / len(a | b) if a | b else 0.0


def _fields(registry: RegistryRecord, status: Status) -> tuple[Field, ...]:
    """Project an accepted registry record onto the canonical fields.

    Parameters
    ----------
    registry : RegistryRecord
        Accepted record.
    status : Status
        Status applied to present values.

    Returns
    -------
    tuple of Field
        One field per canonical name; absent values are null and unverified.
    """
    values: dict[str, object] = {
        "title": registry.title,
        "authors": [author.to_dict() for author in registry.authors] or None,
        "journal": registry.container_title,
        "publisher": registry.publisher,
        "doi": registry.doi,
        "volume": registry.volume,
        "issue": registry.issue,
        "pages": registry.pages,
        "article_number": registry.article_number,
        "year": registry.year,
        "published_online": list(registry.published_online) or None,
        "published_print": list(registry.published_print) or None,
        "url": registry.url,
        "issn": list(registry.issn) or None,
        "license": list(registry.license_urls) or None,
        "article_type": registry.type,
    }
    return tuple(
        Field(
            name,
            value,
            status if value is not None else "UNVERIFIED",
            registry.provider if value is not None else None,
        )
        for name, value in values.items()
    )


def _decide(candidate: Candidate, checks: Sequence[Check]) -> tuple[Status | None, str]:
    """Decide whether a resolved candidate is accepted.

    Parameters
    ----------
    candidate : Candidate
        Candidate whose record was compared.
    checks : Sequence of Check
        Comparison outcomes.

    Returns
    -------
    tuple
        Accepted status or None, and the reason.
    """
    by_name = {check.name: check for check in checks}
    title = by_name["title"]
    if title.outcome == "fail":
        return None, f"registry title differs: {title.detail}"
    if title.outcome == "not_checked" and not candidate.strong():
        return None, "no observed title to corroborate a DOI seen only in page text"
    warnings = [check for check in checks if check.outcome in ("warn", "fail")]
    if title.outcome == "not_checked":
        warnings.append(title)
    if warnings:
        return "VALIDATED_WITH_WARNINGS", "; ".join(
            f"{check.name} {check.outcome}: {check.detail}" for check in warnings
        )
    return "VALIDATED", "registry record agrees with the article's title and authors"


_SEARCH_SOURCE = "bibliographic_search"


def same_title(first: str, second: str) -> bool:
    """Decide whether two titles are the same once normalized.

    Parameters
    ----------
    first : str
        Title.
    second : str
        Title.

    Returns
    -------
    bool
        True when the title keys are equal, or equal with spaces removed;
        registries sometimes drop the spaces around stripped math markup,
        as in "ofH2andD2accurately".

    Examples
    --------
    >>> same_title("Susceptibility of H2 and D2", "Susceptibility ofH2andD2")
    True
    >>> same_title("Dispersion of N<sub>2</sub>*", "Dispersion of N2")
    True
    """
    return _same_key(title_key(clean_title(first)), title_key(clean_title(second)))


def _same_key(left: str, right: str) -> bool:
    """Compare two title keys, also with their spaces removed.

    Parameters
    ----------
    left : str
        Title key.
    right : str
        Title key.

    Returns
    -------
    bool
        True when the keys are equal with or without spaces.
    """
    return left == right or left.replace(" ", "") == right.replace(" ", "")


Agreement = Literal["same", "near", "different"]
_MIN_NEAR_WORDS = 4
_MAX_DROPPED_WORDS = 2
_TITLE_SEPARATORS = re.compile(r"\s+(?:-|\u2013|\u2014|\|)\s+")


def title_agreement(first: str, second: str) -> Agreement:
    """Grade how closely two titles agree.

    Parameters
    ----------
    first : str
        Title, usually observed.
    second : str
        Title, usually from a registry.

    Returns
    -------
    str
        ``same`` when :func:`same_title` holds. ``near`` when they agree
        apart from at most two one-letter or non-ASCII words on each side,
        such as a Greek letter the PDF lost, with at least four words left;
        or when the part of either title before a " - " or " | " separator
        agrees, as in a title followed by its journal's name. Otherwise
        ``different``.

    Examples
    --------
    >>> title_agreement("Scattering of Lyman light", "Scattering of Lyman \u03b1 light")
    'near'
    >>> title_agreement("Raman gain of hydrogen - IEEE J. QE", "Raman gain of hydrogen")
    'near'
    """
    if same_title(first, second):
        return "same"
    lefts = _title_parts(clean_title(first))
    rights = _title_parts(clean_title(second))
    for left in lefts:
        for right in rights:
            if (
                _same_key(left, right)
                or _near_key(left, right)
                or _ocr_key(left, right)
            ):
                return "near"
    return "different"


def _title_parts(title: str) -> tuple[str, ...]:
    """Give a title's key and the key of its part before a separator.

    Parameters
    ----------
    title : str
        Cleaned title.

    Returns
    -------
    tuple of str
        The whole key, then the key of the text before the first separator
        when there is one.
    """
    keys = [title_key(title)]
    head = _TITLE_SEPARATORS.split(title, maxsplit=1)[0]
    if head != title:
        keys.append(title_key(head))
    return tuple(keys)


def _near_key(left: str, right: str) -> bool:
    """Compare title keys ignoring a few one-letter or non-ASCII words.

    Parameters
    ----------
    left : str
        Title key.
    right : str
        Title key.

    Returns
    -------
    bool
        True when the remaining words agree, at least four remain, at most
        two were dropped from each side, and one side lost its dropped
        words or shows only ASCII letters in their place. Titles that differ
        only in one Greek letter, alpha against beta, therefore stay
        different, while a lost Greek letter, or an ASCII letter in its
        place, is tolerated.
    """
    kept_left, dropped_left = _essential_words(left)
    kept_right, dropped_right = _essential_words(right)
    substitute = any(
        all(word.isascii() for word in dropped)
        for dropped in (dropped_left, dropped_right)
    )
    return (
        len(kept_left) >= _MIN_NEAR_WORDS
        and max(len(dropped_left), len(dropped_right)) <= _MAX_DROPPED_WORDS
        and substitute
        and "".join(kept_left) == "".join(kept_right)
    )


_CONFUSABLE = frozenset({frozenset("0o"), frozenset("1l"), frozenset("1i")})
_MAX_OCR_EDITS = 2
_MIN_OCR_CHARACTERS = 20


def _ocr_key(left: str, right: str) -> bool:
    """Compare title keys allowing a couple of typical reading errors.

    Parameters
    ----------
    left : str
        Title key.
    right : str
        Title key.

    Returns
    -------
    bool
        True when the keys, with spaces removed and at least twenty
        characters long, differ only in at most two characters, each either
        a confusable pair (zero and letter o, one and letter l or i) or one
        extra or missing digit, such as a footnote number read into a
        formula. A letter for another letter, as in H2 against D2, is never
        tolerated.
    """
    a, b = left.replace(" ", ""), right.replace(" ", "")
    if min(len(a), len(b)) < _MIN_OCR_CHARACTERS:
        return False
    edits = 0
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        old, new = a[i1:i2], b[j1:j2]
        if tag == "replace" and len(old) == len(new):
            pairs = zip(old, new, strict=True)
            if not all(frozenset(pair) in _CONFUSABLE for pair in pairs):
                return False
            edits += len(old)
        elif tag != "replace" and len(old + new) == 1 and (old + new).isdigit():
            edits += 1
        else:
            return False
    return 0 < edits <= _MAX_OCR_EDITS


def _essential_words(key: str) -> tuple[list[str], list[str]]:
    """Split a key into its ASCII words of two or more characters.

    Parameters
    ----------
    key : str
        Title key.

    Returns
    -------
    tuple
        The kept words and the dropped ones.
    """
    words = key.split()
    kept = [word for word in words if len(word) > 1 and word.isascii()]
    dropped = [word for word in words if not (len(word) > 1 and word.isascii())]
    return kept, dropped


_PRINTED_YEAR = re.compile(r"\b(1[5-9]\d\d|20\d\d)\b")


def _leading_text(document: Document) -> str:
    """Collect the text of the document's first two processed pages.

    Parameters
    ----------
    document : Document
        Canonical document.

    Returns
    -------
    str
        Headings, prose, running headers and footers, list items and captions
        of those pages, joined by spaces; equations are left out. Two pages,
        because a publisher's cover page often precedes the article.
    """
    pages = _leading_pages(document)
    parts: list[str] = []
    for block in document.blocks:
        if isinstance(block, Figure | Table):
            page = block.page if isinstance(block, Figure) else block.span.page
            runs = [] if block.caption is None else [block.caption]
        elif isinstance(block, ListBlock):
            page, runs = block.span.page, list(block.items)
        elif isinstance(block, Heading | Paragraph | PageFurniture):
            page, runs = block.span.page, [block.runs]
        else:
            continue
        if page in pages:
            parts.extend(plain_text(item) for item in runs)
    return " ".join(parts)


def _search_checks(
    registry: RegistryRecord, titles: Sequence[str], page_text: str
) -> tuple[Check, ...]:
    """Check a search hit against the article's own first pages.

    Parameters
    ----------
    registry : RegistryRecord
        Search hit.
    titles : Sequence of str
        Observed title candidates.
    page_text : str
        Text of the first processed pages.

    Returns
    -------
    tuple of Check
        Title, first-author and year checks. The title passes when a
        candidate is the same title or agrees apart from markup, lost
        characters or an appended name; author and year pass only when
        printed on those pages.
    """
    registry_titles = [registry.title or ""]
    if registry.subtitle:
        registry_titles.append(f"{registry.title} {registry.subtitle}")
    grades = {
        title_agreement(title, candidate)
        for title in titles
        for candidate in registry_titles
    }
    agrees = bool(grades & {"same", "near"})
    title_check = Check(
        "title",
        "pass" if agrees else "fail",
        f"Observed {titles[0]!r} versus registry {registry.title!r}"
        + ("" if "same" in grades or not agrees else " (agree apart from markup)"),
    )
    first = registry.authors[0] if registry.authors else None
    family = _family_key((first.family or first.literal or "") if first else "")
    page_key = f" {title_key(page_text)} "
    author_check = Check(
        "authors",
        "pass" if family and f" {family} " in page_key else "fail",
        f"First registry author {family or 'none'!r} "
        + ("appears" if family and f" {family} " in page_key else "does not appear")
        + " on the first pages",
    )
    years = {
        year
        for year in (
            registry.year,
            registry.published_print[0] if registry.published_print else None,
            registry.published_online[0] if registry.published_online else None,
        )
        if year is not None
    }
    printed = {int(value) for value in _PRINTED_YEAR.findall(page_text)}
    year_check = Check(
        "year",
        "pass" if years & printed else "fail",
        f"Registry years {sorted(years)}; years printed on the first pages "
        f"{sorted(printed)}",
    )
    return (title_check, author_check, year_check)


_QUERY_CHARACTERS = 300
_HINT_CHARACTERS = 160
_FILE_NOISE = frozenset({"online", "main", "sm", "si", "pdf", "supplementary"})
_MIN_HINT_WORDS = 2
_MIN_WORD_LETTERS = 3
_SEARCH_TITLES = 3


def _file_hint(name: str) -> str:
    """Turn a descriptive file name into search words.

    Parameters
    ----------
    name : str
        File name as supplied.

    Returns
    -------
    str
        The decoded stem with separators as spaces, such as
        ``J A R Samson 1994 J. Phys. B At. Mol. Opt. Phys. 27 887``, or an
        empty string for a name without at least two words of three or more
        letters, such as ``3321_1_online.pdf``.
    """
    stem = urllib.parse.unquote(PurePosixPath(name).name)
    stem = re.sub(r"\.pdf$", "", stem, flags=re.IGNORECASE)
    words = [w for w in re.split(r"[\s_]+", stem) if w.lower() not in _FILE_NOISE]
    long_words = sum(
        len(re.sub(r"[^A-Za-z]", "", word)) >= _MIN_WORD_LETTERS for word in words
    )
    if long_words < _MIN_HINT_WORDS:
        return ""
    return " ".join(words)


def _author_hint(document: Document) -> str:
    """Take the first page's author line to strengthen a title search.

    Parameters
    ----------
    document : Document
        Canonical document.

    Returns
    -------
    str
        The text of the first short paragraph after the first level-1
        heading of the first pages, where authors are printed, or an empty
        string.
    """
    pages = _leading_pages(document)
    seen_title = False
    for block in document.blocks:
        if isinstance(block, Heading) and block.level == 1 and block.span.page in pages:
            seen_title = True
        elif seen_title and isinstance(block, Paragraph) and block.span.page in pages:
            text = plain_text(block.runs)
            return text if len(text) <= _HINT_CHARACTERS else ""
    return ""


def _running_text(document: Document) -> str:
    """Collect running headers and footers of the first pages.

    Parameters
    ----------
    document : Document
        Canonical document.

    Returns
    -------
    str
        Their text, where journals print a citation line such as
        ``PHYSICAL REVIEW A VOLUME 13, NUMBER 4 APRIL 1976``.
    """
    pages = _leading_pages(document)
    return " ".join(
        plain_text(block.runs)
        for block in document.blocks
        if isinstance(block, PageFurniture) and block.span.page in pages
    )


def _queries(
    titles: Sequence[str], document: Document, file_names: Sequence[str]
) -> tuple[str, ...]:
    """Build the search queries, plain titles first.

    Parameters
    ----------
    titles : Sequence of str
        Observed title candidates.
    document : Document
        Canonical document.
    file_names : Sequence of str
        Names the sources were supplied under.

    Returns
    -------
    tuple of str
        Each of the first three cleaned titles, then the first title with
        the author line, the running citation line and descriptive file
        names appended, which ranks a generic title's work first.
    """
    cleaned = [clean_title(title) for title in titles[:_SEARCH_TITLES]]
    hints = [
        _author_hint(document),
        _running_text(document)[:_HINT_CHARACTERS],
        *(_file_hint(name) for name in file_names),
    ]
    enriched = " ".join([cleaned[0], *(hint for hint in hints if hint)])
    return tuple(dict.fromkeys([*cleaned, enriched[:_QUERY_CHARACTERS]]))


def _cites(hit: RegistryRecord, hint_tokens: frozenset[str]) -> bool:
    """Tell whether the article's pages or file names cite a hit's location.

    Parameters
    ----------
    hit : RegistryRecord
        Search hit.
    hint_tokens : frozenset of str
        Words of the first pages' text and the file names.

    Returns
    -------
    bool
        True when both the hit's volume and its first page or article number
        appear among the words.
    """
    first_page = (hit.pages or "").split("-")[0].strip() or hit.article_number
    return bool(
        hit.volume
        and first_page
        and hit.volume.lower() in hint_tokens
        and first_page.lower() in hint_tokens
    )


def _choose(
    accepted: Sequence[tuple[RegistryRecord, tuple[Check, ...]]],
    hint_tokens: frozenset[str],
) -> tuple[list[tuple[RegistryRecord, tuple[Check, ...]]], list[dict[str, object]]]:
    """Narrow the search hits that agree with the article.

    Parameters
    ----------
    accepted : Sequence of tuple
        Agreeing hits with their checks, distinct by DOI.
    hint_tokens : frozenset of str
        Words of the first pages' text and the file names.

    Returns
    -------
    tuple
        The remaining hits and the set-aside ones as alternatives.
        Components such as supplementary files are set aside; an original
        is set aside in favour of its registered translation, so an English
        translation is the identity and the original a recorded
        alternative; among several left, those whose volume and first page
        the article or its file name cites are kept, when there are any.
    """
    remaining = [pair for pair in accepted if pair[0].type != "component"]
    aside: list[dict[str, object]] = [
        {"doi": hit.doi, "outcome": "search_component", "detail": "a component"}
        for hit, _ in accepted
        if hit.type == "component"
    ]
    dois = {hit.doi for hit, _ in remaining}
    originals = {
        target: hit.doi
        for hit, _ in remaining
        for kind, target in hit.relations
        if kind == "is-translation-of" and target in dois
    }
    if originals:
        aside.extend(
            {
                "doi": hit.doi,
                "outcome": "translation_original",
                "detail": f"original of the translation {originals[hit.doi]}",
            }
            for hit, _ in remaining
            if hit.doi in originals
        )
        remaining = [pair for pair in remaining if pair[0].doi not in originals]
    if len(remaining) > 1:
        cited = {hit.doi for hit, _ in remaining if _cites(hit, hint_tokens)}
        if cited:
            aside.extend(
                {
                    "doi": hit.doi,
                    "outcome": "search_not_cited",
                    "detail": "the article does not print this volume and page",
                }
                for hit, _ in remaining
                if hit.doi not in cited
            )
            remaining = [pair for pair in remaining if pair[0].doi in cited]
    return remaining, aside


def _search(
    document: Document,
    titles: Sequence[str],
    search: Search,
    file_names: Sequence[str] = (),
) -> tuple[RegistryRecord | None, tuple[Check, ...], list[dict[str, object]], str]:
    """Look for a unique search hit that the article corroborates.

    Parameters
    ----------
    document : Document
        Canonical document.
    titles : Sequence of str
        Observed title candidates; not empty.
    search : Callable
        Bibliographic search.
    file_names : Sequence of str
        Names the sources were supplied under, used as search hints.

    Returns
    -------
    tuple
        The accepted hit or None, its checks, the rejected hits as
        alternatives, and the reason.
    """
    page_text = _leading_text(document)
    hint_tokens = frozenset(
        re.split(r"[\s,;:()\[\]_.]+", f"{page_text} {' '.join(file_names)}".lower())
    )
    seen: set[str] = set()
    accepted: list[tuple[RegistryRecord, tuple[Check, ...]]] = []
    alternatives: list[dict[str, object]] = []
    failure: str | None = None
    answered = False
    for query in _queries(titles, document, file_names):
        result = search(query)
        if isinstance(result, RegistryFailure):
            failure = f"bibliographic search {result.kind}: {result.message}"
            continue
        answered = True
        for hit in result:
            if hit.doi in seen:
                continue
            seen.add(hit.doi)
            checks = _search_checks(hit, titles, page_text)
            if all(check.outcome == "pass" for check in checks):
                accepted.append((hit, checks))
            else:
                failed = "; ".join(c.detail for c in checks if c.outcome != "pass")
                alternatives.append(
                    {"doi": hit.doi, "outcome": "search_rejected", "detail": failed}
                )
        remaining, aside = _choose(accepted, hint_tokens)
        if len(remaining) == 1:
            hit, checks = remaining[0]
            return (
                hit,
                checks,
                [*alternatives, *aside],
                (
                    "the PDF prints no usable DOI; a bibliographic search found one "
                    "work whose title, first author and year agree with the first "
                    "pages"
                ),
            )
    if failure is not None and not answered:
        return None, (), alternatives, failure
    remaining, aside = _choose(accepted, hint_tokens)
    alternatives.extend(aside)
    alternatives.extend(
        {
            "doi": hit.doi,
            "outcome": "search_ambiguous",
            "detail": "several hits agree",
        }
        for hit, _ in remaining
    )
    reason = (
        "bibliographic search found several works that agree with the first pages"
        if remaining
        else "no bibliographic search result agrees with the first pages"
    )
    return None, (), alternatives, reason


_ASSERTED_SOURCE = "user_assertion"


def _asserted(
    doi: str,
    lookup: Lookup,
    observed: Observed,
    titles: Sequence[str],
    candidates: Sequence[Candidate],
) -> Identity:
    """Resolve a DOI the user asserts for the document.

    Parameters
    ----------
    doi : str
        Asserted DOI.
    lookup : Callable
        Registry lookup.
    observed : Observed
        Article observations.
    titles : Sequence of str
        Observed title candidates.
    candidates : Sequence of Candidate
        Candidates found in the document, recorded beside the assertion.

    Returns
    -------
    Identity
        ``VALIDATED_WITH_WARNINGS`` with the registry record and an
        ``identifier`` warning naming the assertion, when the DOI is
        registered; the comparison with the article is recorded and a
        disagreement becomes a further warning, not a refusal. ``UNVERIFIED``
        when the DOI is malformed, unregistered or the registry unreachable.
    """
    asserted = Candidate(normalize_doi(doi) or doi, "doi", (_ASSERTED_SOURCE,))
    result = lookup(asserted.value)
    if isinstance(result, RegistryFailure):
        return Identity(
            status="UNVERIFIED",
            work_id=None,
            doi=None,
            provider=None,
            record=None,
            fields=tuple(
                Field(name, None, "UNVERIFIED", None) for name in _FIELD_NAMES
            ),
            checks=(),
            candidates=(asserted, *candidates),
            alternatives=(
                {
                    "doi": asserted.value,
                    "outcome": result.kind,
                    "detail": result.message,
                },
            ),
            observed=observed,
            reasons=(f"asserted DOI {asserted.value}: {result.message}",),
        )
    checks = compare_record(result, observed, titles)
    status: Status = "VALIDATED_WITH_WARNINGS"
    identifier = Check("identifier", "warn", "DOI asserted by the user")
    return Identity(
        status=status,
        work_id=f"work_{hashlib.sha256(result.doi.encode()).hexdigest()[:12]}",
        doi=result.doi,
        provider=result.provider,
        record=result.to_dict(),
        fields=_fields(result, status),
        checks=(*checks, identifier),
        candidates=(asserted, *candidates),
        alternatives=(),
        observed=observed,
        reasons=("the user asserted this DOI; the registry record is recorded",),
    )


def resolve_identity(
    document: Document,
    fingerprint: Mapping[str, object],
    lookup: Lookup,
    search: Search | None = None,
    *,
    file_names: Sequence[str] = (),
    asserted_doi: str | None = None,
) -> Identity:
    """Resolve and judge the bibliographic identity of one document.

    Parameters
    ----------
    document : Document
        Canonical document.
    fingerprint : Mapping of str to object
        Serialized content fingerprint.
    lookup : Callable
        Function from DOI to registry record or failure.
    search : Callable or None
        Bibliographic search used when no DOI candidate is accepted.
    file_names : Sequence of str
        Names the sources were supplied under: weak DOI candidates by
        publisher convention, and search hints.
    asserted_doi : str or None
        A DOI the user asserts for the document. It is tried first and
        accepted when registered, with a warning that the user asserted it,
        and a further warning when its record disagrees with the article.

    Returns
    -------
    Identity
        Accepted identity with field-level status, or an unverified or
        conflicting identity with the reasons and every alternative recorded.

    Notes
    -----
    Candidates are tried strongest first. A record whose title matches the
    observed title is accepted; author or year disagreement only adds
    warnings. A strong candidate whose registry title differs produces
    ``CONFLICT`` so a wrong embedded DOI is never published silently; a weak
    candidate that differs is merely recorded, because first-page text often
    cites other works. Registry unavailability leaves the identity
    ``UNVERIFIED`` for a later retry rather than fabricating anything.

    Without an accepted candidate, conflict or outage, a bibliographic search
    by the observed title may identify the paper. A hit is accepted only when
    its title equals the observed title, its first author's family name and
    its year appear on the first page, and no other hit agrees as well; the
    identity is then ``VALIDATED_WITH_WARNINGS``, because the file itself
    names no DOI.
    """
    observed = observe(document)
    titles = title_candidates(document)
    candidates = collect_candidates(document, fingerprint, file_names)
    if asserted_doi is not None:
        return _asserted(asserted_doi, lookup, observed, titles, candidates)
    if not candidates and search is None:
        return Identity.unverified("no DOI candidate in the PDF", observed)
    identity = _resolve(
        document,
        lookup,
        search,
        observed=observed,
        titles=titles,
        candidates=candidates,
        file_names=file_names,
    )
    if identity.validated() and _supplementary(titles, file_names):
        return _as_supplement(identity)
    return identity


_FILE_NAME_SOURCE = "file_name"
_SUPPLEMENT_TITLE = re.compile(
    r"^\s*(?:electronic\s+)?(?:supplementary|supplemental|supporting)\s+"
    r"(?:information|materials?|data)\b",
    re.IGNORECASE,
)


def _supplementary(titles: Sequence[str], file_names: Sequence[str]) -> bool:
    """Tell whether the document is supplementary material of an article.

    Parameters
    ----------
    titles : Sequence of str
        Observed title candidates.
    file_names : Sequence of str
        Names the sources were supplied under.

    Returns
    -------
    bool
        True when a title candidate reads like "Supplementary Materials for"
        or "Supporting Information", or a file name is a supplement's, such
        as ``abb5375_sm.pdf``.
    """
    return any(_SUPPLEMENT_TITLE.match(title) for title in titles) or any(
        looks_like_supplement(Path(name)) for name in file_names
    )


def _as_supplement(identity: Identity) -> Identity:
    """Turn an accepted identity into the article a supplement belongs to.

    Parameters
    ----------
    identity : Identity
        Identity accepted for the document.

    Returns
    -------
    Identity
        ``UNVERIFIED``, with the accepted DOI recorded as the article the
        supplement belongs to, because a supplement shares its article's
        title and authors but is not the article.
    """
    title = identity.field("title")
    return Identity(
        status="UNVERIFIED",
        work_id=None,
        doi=None,
        provider=None,
        record=None,
        fields=tuple(Field(name, None, "UNVERIFIED", None) for name in _FIELD_NAMES),
        checks=(),
        candidates=identity.candidates,
        alternatives=(
            *identity.alternatives,
            {
                "doi": identity.doi,
                "outcome": "supplement_of",
                "detail": f"the article {title!r}",
            },
        ),
        observed=identity.observed,
        reasons=(
            f"looks like supplementary material of {identity.doi}; publish it "
            "with that paper as a supplement",
        ),
    )


def _resolve(  # noqa: PLR0913 - the evidence gathered by resolve_identity
    document: Document,
    lookup: Lookup,
    search: Search | None,
    *,
    observed: Observed,
    titles: Sequence[str],
    candidates: Sequence[Candidate],
    file_names: Sequence[str],
) -> Identity:
    """Try the DOI candidates, then a bibliographic search.

    Parameters
    ----------
    document : Document
        Canonical document.
    lookup : Callable
        Registry lookup.
    search : Callable or None
        Bibliographic search.
    observed : Observed
        Article observations.
    titles : Sequence of str
        Observed title candidates.
    candidates : Sequence of Candidate
        DOI candidates, strongest first.
    file_names : Sequence of str
        Names the sources were supplied under.

    Returns
    -------
    Identity
        The identity decided as :func:`resolve_identity` describes.
    """
    alternatives: list[dict[str, object]] = []
    conflict: str | None = None
    unavailable: str | None = None
    for candidate in candidates[:_MAX_CANDIDATES]:
        result = lookup(candidate.value)
        if isinstance(result, RegistryFailure):
            alternatives.append(
                {
                    "doi": candidate.value,
                    "outcome": result.kind,
                    "detail": result.message,
                }
            )
            if result.kind == "unavailable":
                unavailable = result.message
            continue
        checks = compare_record(result, observed, titles)
        status, reason = _decide(candidate, checks)
        if status is None:
            alternatives.append(
                {"doi": candidate.value, "outcome": "rejected", "detail": reason}
            )
            if candidate.strong() and conflict is None:
                conflict = f"{candidate.value}: {reason}"
            continue
        if candidate.sources == (_FILE_NAME_SOURCE,):
            status = "VALIDATED_WITH_WARNINGS"
            checks = (
                *checks,
                Check(
                    "identifier",
                    "warn",
                    "The file prints no usable DOI; the DOI comes from the "
                    "publisher's naming of the file",
                ),
            )
        return Identity(
            status=status,
            work_id=f"work_{hashlib.sha256(result.doi.encode()).hexdigest()[:12]}",
            doi=result.doi,
            provider=result.provider,
            record=result.to_dict(),
            fields=_fields(result, status),
            checks=checks,
            candidates=tuple(candidates),
            alternatives=tuple(alternatives),
            observed=observed,
            reasons=(reason,),
        )
    if conflict is not None:
        status_value: Status = "CONFLICT"
        reason = f"embedded DOI resolves to a different work: {conflict}"
    elif unavailable is not None:
        status_value = "UNVERIFIED"
        reason = f"registry unavailable: {unavailable}"
    else:
        status_value = "UNVERIFIED"
        reason = (
            "no candidate resolved to a matching registry record"
            if candidates
            else "no DOI candidate in the PDF"
        )
        if search is not None and titles:
            hit, checks, rejected, found = _search(document, titles, search, file_names)
            alternatives.extend(rejected)
            if hit is not None:
                status = "VALIDATED_WITH_WARNINGS"
                identifier = Check(
                    "identifier",
                    "warn",
                    "The file prints no usable DOI; identified by bibliographic search",
                )
                searched = Candidate(hit.doi, "doi", (_SEARCH_SOURCE,))
                return Identity(
                    status=status,
                    work_id=f"work_{hashlib.sha256(hit.doi.encode()).hexdigest()[:12]}",
                    doi=hit.doi,
                    provider=hit.provider,
                    record=hit.to_dict(),
                    fields=_fields(hit, status),
                    checks=(*checks, identifier),
                    candidates=(*candidates, searched),
                    alternatives=tuple(alternatives),
                    observed=observed,
                    reasons=(found,),
                )
            reason = f"{reason}; {found}"
    return Identity(
        status=status_value,
        work_id=None,
        doi=None,
        provider=None,
        record=None,
        fields=tuple(Field(name, None, "UNVERIFIED", None) for name in _FIELD_NAMES),
        checks=(),
        candidates=tuple(candidates),
        alternatives=tuple(alternatives),
        observed=observed,
        reasons=(reason,),
    )


_CONTAINERS = ("journal", "booktitle", "institution", "school", "series")
_PUBLISHERS = ("publisher", "institution", "school", "organization")
_ASSERTED_PROVIDER = "user"


def _bibtex_authors(text: str) -> list[dict[str, object]]:
    """Read a BibTeX author list into structured names.

    Parameters
    ----------
    text : str
        ``author`` field, names joined by ``and``.

    Returns
    -------
    list of dict
        Authors with ``given`` and ``family`` from ``Family, Given`` or
        ``Given Family``; a single word is a family name.
    """
    authors: list[dict[str, object]] = []
    for number, name in enumerate(re.split(r"\s+and\s+", text.strip())):
        if "," in name:
            family, given = (part.strip() for part in name.split(",", 1))
        else:
            words = name.split()
            family, given = words[-1], " ".join(words[:-1])
        authors.append(
            {
                "given": given or None,
                "family": family,
                "literal": None,
                "orcid": None,
                "sequence": "first" if number == 0 else "additional",
            }
        )
    return authors


def identity_from_bibtex(entry: str, document: Document) -> Identity:
    """Build the identity a user asserts with a BibTeX entry.

    Parameters
    ----------
    entry : str
        One BibTeX entry with at least ``author``, ``title`` and ``year``,
        for a work no registry holds, such as a report or a thesis.
    document : Document
        Canonical document the entry describes.

    Returns
    -------
    Identity
        ``ASSERTED`` identity: every supplied field has status ``ASSERTED``
        and source ``user``; the title and year are compared with the
        article's pages and recorded as checks, as evidence only.

    Raises
    ------
    ValueError
        The text is not exactly one entry, lacks a required field, or
        carries a DOI, which is asserted with ``--doi`` instead.
    """
    entries = read_entries(entry)
    if len(entries) != 1:
        raise ValueError("Give exactly one BibTeX entry")
    entry_type, fields = entries[0]
    missing = [name for name in ("author", "title", "year") if not fields.get(name)]
    if missing:
        raise ValueError(f"The BibTeX entry lacks {', '.join(missing)}")
    if fields.get("doi"):
        raise ValueError("The entry has a DOI; assert it with --doi instead")
    year = fields["year"]
    if not year.isdigit():
        raise ValueError(f"The BibTeX year {year!r} is not a number")
    values: dict[str, object] = {
        "title": fields["title"],
        "authors": _bibtex_authors(fields["author"]),
        "journal": next((fields[k] for k in _CONTAINERS if fields.get(k)), None),
        "publisher": next((fields[k] for k in _PUBLISHERS if fields.get(k)), None),
        "doi": None,
        "volume": fields.get("volume") or None,
        "issue": fields.get("number") or None,
        "pages": fields.get("pages") or None,
        "article_number": None,
        "year": int(year),
        "published_online": None,
        "published_print": None,
        "url": fields.get("url") or None,
        "issn": None,
        "license": None,
        "article_type": f"bibtex:{entry_type}",
    }
    observed = observe(document)
    titles = title_candidates(document)
    page_text = _leading_text(document)
    checks = (
        _title_check(fields["title"], titles)
        if titles
        else Check("title", "not_checked", "No observed title"),
        Check(
            "year",
            "pass" if year in _PRINTED_YEAR.findall(page_text) else "warn",
            f"Asserted year {year}; years printed on the first pages "
            f"{sorted(set(_PRINTED_YEAR.findall(page_text)))}",
        ),
        Check("identifier", "warn", "Identity asserted by the user; no registry"),
    )
    key = f"asserted:{title_key(fields['title'])}:{year}"
    return Identity(
        status="ASSERTED",
        work_id=f"work_{hashlib.sha256(key.encode()).hexdigest()[:12]}",
        doi=None,
        provider=_ASSERTED_PROVIDER,
        record=None,
        fields=tuple(
            Field(
                name,
                values[name],
                "ASSERTED" if values[name] is not None else "UNVERIFIED",
                _ASSERTED_PROVIDER if values[name] is not None else None,
            )
            for name in _FIELD_NAMES
        ),
        checks=checks,
        candidates=(),
        alternatives=(),
        observed=observed,
        reasons=("the user asserted this identity with a BibTeX entry",),
    )
