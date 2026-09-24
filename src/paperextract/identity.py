"""Establish bibliographic identity from registry records and local evidence.

A DOI candidate found in a PDF is only a candidate. The identity stage resolves
it through a registry, compares the registration metadata with what the article
itself shows, and records the outcome field by field with the evidence used.
Fuzzy agreement can raise a warning; it never merges works or invents values.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, cast

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
    "collect_candidates",
    "compare_record",
    "math_as_text",
    "observe",
    "resolve_identity",
    "same_title",
    "title_key",
]

IDENTITY_SCHEMA = "paperextract.identity"
IDENTITY_VERSION = 1
Status = Literal["VALIDATED", "VALIDATED_WITH_WARNINGS", "UNVERIFIED", "CONFLICT"]
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


_STATUSES = "VALIDATED VALIDATED_WITH_WARNINGS UNVERIFIED CONFLICT"


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
        """Report whether the identity may name directories and citations.

        Returns
        -------
        bool
            True for ``VALIDATED`` and ``VALIDATED_WITH_WARNINGS``.
        """
        return self.status in ("VALIDATED", "VALIDATED_WITH_WARNINGS")

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
    for observation in document.metadata:
        if observation.field == "title" and plausible_title(observation.value):
            return observation.value
    for block in document.blocks:
        if isinstance(block, Heading) and block.level == 1:
            return "".join(
                math_as_text(run.text) if run.kind == "math" else run.text
                for run in block.runs
                if not _footnote_marker(run)
            )
    return None


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


def collect_candidates(
    document: Document, fingerprint: Mapping[str, object]
) -> tuple[Candidate, ...]:
    """Gather DOI candidates from article-level evidence, strongest first.

    Parameters
    ----------
    document : Document
        Canonical document.
    fingerprint : Mapping of str to object
        Serialized content fingerprint with ``doi_candidates``.

    Returns
    -------
    tuple of Candidate
        Distinct normalized DOIs. Information-dictionary values and first-page
        running headers or footers count as strong sources; strings in
        first-page body text are weak; DOIs inside reference entries are
        excluded because they identify cited works.
    """
    found: dict[str, list[str]] = {}
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
    for block in document.blocks:
        if isinstance(block, PageFurniture) and block.span.page == 1:
            for match in _DOI_IN_TEXT.findall(plain_text(block.runs)):
                _add_candidate(found, match, "page_1_furniture")
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


def compare_record(registry: RegistryRecord, observed: Observed) -> tuple[Check, ...]:
    """Compare a registry record with article-local observations.

    Parameters
    ----------
    registry : RegistryRecord
        Registration metadata.
    observed : Observed
        Observations from the document.

    Returns
    -------
    tuple of Check
        Title, authors and year checks with their evidence.
    """
    checks: list[Check] = []
    if observed.title is None or registry.title is None:
        checks.append(Check("title", "not_checked", "No observed or registry title"))
    else:
        left, right = title_key(observed.title), title_key(registry.title)
        if same_title(observed.title, registry.title):
            checks.append(Check("title", "pass", f"Titles agree: {registry.title!r}"))
        else:
            overlap = _overlap(left.split(), right.split())
            outcome: Outcome = "warn" if overlap >= _TITLE_OVERLAP_WARN else "fail"
            checks.append(
                Check(
                    "title",
                    outcome,
                    f"Observed {observed.title!r} versus registry {registry.title!r} "
                    f"(token overlap {overlap:.2f})",
                )
            )
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
    """
    left, right = title_key(first), title_key(second)
    return left == right or left.replace(" ", "") == right.replace(" ", "")


_PRINTED_YEAR = re.compile(r"\b(1[5-9]\d\d|20\d\d)\b")


def _first_page_text(document: Document) -> str:
    """Collect the text of the document's first processed page.

    Parameters
    ----------
    document : Document
        Canonical document.

    Returns
    -------
    str
        Headings, prose, running headers and footers, list items and captions
        of that page, joined by spaces; equations are left out.
    """
    processed = [page.number for page in document.pages if page.status == "processed"]
    first = min(processed) if processed else 1
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
        if page == first:
            parts.extend(plain_text(item) for item in runs)
    return " ".join(parts)


def _search_checks(
    registry: RegistryRecord, title: str, page_text: str
) -> tuple[Check, ...]:
    """Check a search hit against the article's own first page.

    Parameters
    ----------
    registry : RegistryRecord
        Search hit.
    title : str
        Observed title.
    page_text : str
        Text of the first processed page.

    Returns
    -------
    tuple of Check
        Title, first-author and year checks; each passes only on exact
        agreement.
    """
    candidates = [registry.title or ""]
    if registry.subtitle:
        candidates.append(f"{registry.title} {registry.subtitle}")
    agrees = any(same_title(title, candidate) for candidate in candidates)
    title_check = Check(
        "title",
        "pass" if agrees else "fail",
        f"Observed {title!r} versus registry {registry.title!r}",
    )
    first = registry.authors[0] if registry.authors else None
    family = _family_key((first.family or first.literal or "") if first else "")
    page_key = f" {title_key(page_text)} "
    author_check = Check(
        "authors",
        "pass" if family and f" {family} " in page_key else "fail",
        f"First registry author {family or 'none'!r} "
        + ("appears" if family and f" {family} " in page_key else "does not appear")
        + " on the first page",
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
        f"Registry years {sorted(years)}; years printed on the first page "
        f"{sorted(printed)}",
    )
    return (title_check, author_check, year_check)


def _search(
    document: Document,
    observed: Observed,
    search: Search,
) -> tuple[RegistryRecord | None, tuple[Check, ...], list[dict[str, object]], str]:
    """Look for a unique search hit that the article corroborates.

    Parameters
    ----------
    document : Document
        Canonical document.
    observed : Observed
        Observations with a title.
    search : Callable
        Bibliographic search.

    Returns
    -------
    tuple
        The accepted hit or None, its checks, the rejected hits as
        alternatives, and the reason.
    """
    title = observed.title or ""
    result = search(title)
    if isinstance(result, RegistryFailure):
        return None, (), [], f"bibliographic search {result.kind}: {result.message}"
    page_text = _first_page_text(document)
    accepted: list[tuple[RegistryRecord, tuple[Check, ...]]] = []
    alternatives: list[dict[str, object]] = []
    for hit in result:
        checks = _search_checks(hit, title, page_text)
        if all(check.outcome == "pass" for check in checks):
            accepted.append((hit, checks))
        else:
            failed = "; ".join(c.detail for c in checks if c.outcome != "pass")
            alternatives.append(
                {"doi": hit.doi, "outcome": "search_rejected", "detail": failed}
            )
    dois = {hit.doi for hit, _ in accepted}
    if len(dois) == 1:
        hit, checks = accepted[0]
        return (
            hit,
            checks,
            alternatives,
            (
                "the PDF prints no usable DOI; a bibliographic search found one work "
                "whose title, first author and year agree with the first page"
            ),
        )
    alternatives.extend(
        {
            "doi": hit.doi,
            "outcome": "search_ambiguous",
            "detail": "several hits agree",
        }
        for hit, _ in accepted
    )
    reason = (
        "bibliographic search found several works that agree with the first page"
        if accepted
        else "no bibliographic search result agrees with the first page"
    )
    return None, (), alternatives, reason


def resolve_identity(
    document: Document,
    fingerprint: Mapping[str, object],
    lookup: Lookup,
    search: Search | None = None,
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
    candidates = collect_candidates(document, fingerprint)
    if not candidates and search is None:
        return Identity.unverified("no DOI candidate in the PDF", observed)
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
        checks = compare_record(result, observed)
        status, reason = _decide(candidate, checks)
        if status is None:
            alternatives.append(
                {"doi": candidate.value, "outcome": "rejected", "detail": reason}
            )
            if candidate.strong() and conflict is None:
                conflict = f"{candidate.value}: {reason}"
            continue
        return Identity(
            status=status,
            work_id=f"work_{hashlib.sha256(result.doi.encode()).hexdigest()[:12]}",
            doi=result.doi,
            provider=result.provider,
            record=result.to_dict(),
            fields=_fields(result, status),
            checks=checks,
            candidates=candidates,
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
        if search is not None and observed.title:
            hit, checks, rejected, found = _search(document, observed, search)
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
        candidates=candidates,
        alternatives=tuple(alternatives),
        observed=observed,
        reasons=(reason,),
    )
