"""Query DOI registries with throttling, response snapshots and strict parsing.

Crossref and DataCite are queried by identifier only. Every response is stored
as a snapshot with its retrieval time and body digest, so later runs reuse it,
offline runs can still read it, and a changed registry answer never rewrites
reviewed metadata silently. Requests are serialized and paced below the limit
the service advertises; identifiers, not article content, are sent.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http.client import HTTPResponse
from importlib import metadata
from pathlib import Path
from typing import Literal, Protocol, cast

from paperextract.fields import dump, mapping

__all__ = [
    "SEARCH_ROWS",
    "Author",
    "CachedClient",
    "RegistryClient",
    "RegistryFailure",
    "RegistryRecord",
    "RegistryResponse",
    "RegistryUnavailableError",
    "ResponseCache",
    "Search",
    "UrllibClient",
    "crossref_search_url",
    "crossref_url",
    "datacite_url",
    "default_lookup",
    "default_search",
    "lookup_doi",
    "normalize_doi",
    "parse_crossref",
    "parse_datacite",
    "search_bibliographic",
]

Provider = Literal["crossref", "datacite"]
FailureKind = Literal["not_found", "unavailable", "malformed"]
Lookup = Callable[[str], "RegistryRecord | RegistryFailure"]
Search = Callable[[str], "tuple[RegistryRecord, ...] | RegistryFailure"]

_DOI = re.compile(r"^10\.\d{4,9}/\S+$")
_DOI_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi:",
)
_TAGS = re.compile(r"<[^>]+>")
_MATH_ELEMENT = re.compile(r"<(?:mml:)?math\b.*?</(?:mml:)?math>", re.DOTALL)
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_MAX_RETRY_AFTER_SECONDS = 60.0
_HTTP_OK = 200
_HTTP_NOT_FOUND = 404


class RegistryUnavailableError(RuntimeError):
    """Report that a registry could not be reached or kept refusing requests."""


@dataclass(frozen=True)
class RegistryResponse:
    """Hold one HTTP response from a registry.

    Attributes
    ----------
    url : str
        Requested URL.
    status : int
        HTTP status code.
    body : bytes
        Raw response body.
    headers : Mapping of str to str
        Response headers with lower-case names.
    retrieved_utc : str
        Retrieval time in ISO 8601 UTC.
    cached : bool
        Whether the response came from a stored snapshot.
    """

    url: str
    status: int
    body: bytes
    headers: Mapping[str, str]
    retrieved_utc: str
    cached: bool = False


class RegistryClient(Protocol):
    """Fetch registry URLs."""

    def get(self, url: str, accept: str) -> RegistryResponse:
        """Perform one GET request.

        Parameters
        ----------
        url : str
            Absolute URL.
        accept : str
            Value of the ``Accept`` header.

        Returns
        -------
        RegistryResponse
            Response with any HTTP status; transport failures raise.
        """
        ...


def _utc_now() -> str:
    """Return the current UTC time as ISO 8601 text.

    Returns
    -------
    str
        Timestamp with second precision.
    """
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def _open(request: urllib.request.Request, timeout: float) -> HTTPResponse:
    """Open a URL; separated so tests can substitute the transport.

    Parameters
    ----------
    request : urllib.request.Request
        Prepared request.
    timeout : float
        Socket timeout in seconds.

    Returns
    -------
    http.client.HTTPResponse
        Open response.
    """
    return cast("HTTPResponse", urllib.request.urlopen(request, timeout=timeout))


@dataclass
class UrllibClient:
    """Fetch registry URLs with the standard library, paced and retried.

    Attributes
    ----------
    contact : str or None
        Email placed in the User-Agent for Crossref's polite pool; explicit
        configuration only, never borrowed from package metadata.
    timeout_seconds : float
        Socket timeout per attempt.
    min_interval_seconds : float
        Minimum spacing between requests; tightened further when a response
        advertises a lower rate limit.
    max_attempts : int
        Attempts per request for retryable statuses.
    sleep : Callable
        Sleep function, replaceable in tests.
    """

    contact: str | None = None
    timeout_seconds: float = 30.0
    min_interval_seconds: float = 0.25
    max_attempts: int = 3
    sleep: Callable[[float], None] = time.sleep
    _next_allowed: float = field(default=0.0, init=False, repr=False)

    def user_agent(self) -> str:
        """Build the User-Agent string.

        Returns
        -------
        str
            Product token with the configured contact when present.
        """
        version = metadata.version("paperextract")
        base = f"paperextract/{version} (https://github.com/jtravs/paperextract"
        if self.contact:
            return f"{base}; mailto:{self.contact})"
        return f"{base})"

    def _pace(self) -> None:
        """Wait until the next request is allowed."""
        now = time.monotonic()
        if now < self._next_allowed:
            self.sleep(self._next_allowed - now)
        self._next_allowed = time.monotonic() + self.min_interval_seconds

    def _learn_limit(self, headers: Mapping[str, str]) -> None:
        """Slow down when the service advertises a lower rate limit.

        Parameters
        ----------
        headers : Mapping of str to str
            Response headers with lower-case names.
        """
        limit = headers.get("x-rate-limit-limit")
        interval = headers.get("x-rate-limit-interval", "1s")
        if limit and limit.isdigit() and int(limit) > 0 and interval.endswith("s"):
            seconds = interval[:-1]
            if seconds.isdigit():
                advertised = int(seconds) / int(limit)
                self.min_interval_seconds = max(self.min_interval_seconds, advertised)

    def get(self, url: str, accept: str) -> RegistryResponse:
        """Perform a paced GET with bounded retries on transient statuses.

        Parameters
        ----------
        url : str
            Absolute URL.
        accept : str
            ``Accept`` header value.

        Returns
        -------
        RegistryResponse
            Final response, including ``404`` for unknown identifiers.

        Raises
        ------
        RegistryUnavailableError
            The transport failed or the service kept returning a retryable
            status after every attempt.
        """
        request = urllib.request.Request(
            url, headers={"User-Agent": self.user_agent(), "Accept": accept}
        )
        last_error = "no attempt made"
        for attempt in range(1, self.max_attempts + 1):
            self._pace()
            try:
                with _open(request, self.timeout_seconds) as response:
                    status = int(response.status)
                    headers = {k.lower(): v for k, v in response.headers.items()}
                    body = response.read()
            except urllib.error.HTTPError as exc:
                # An HTTP error holds the response; close it, or Python 3.14
                # warns when it is garbage collected.
                with exc:
                    status = exc.code
                    headers = {k.lower(): v for k, v in exc.headers.items()}
                    body = exc.read()
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < self.max_attempts:
                    self.sleep(float(2 ** (attempt - 1)))
                continue
            self._learn_limit(headers)
            if status in _RETRY_STATUSES and attempt < self.max_attempts:
                self.sleep(_retry_delay(headers.get("retry-after"), attempt))
                last_error = f"HTTP {status}"
                continue
            if status in _RETRY_STATUSES:
                raise RegistryUnavailableError(f"{url} kept returning HTTP {status}")
            return RegistryResponse(url, status, body, headers, _utc_now())
        raise RegistryUnavailableError(f"{url} unreachable: {last_error}")


def _retry_delay(retry_after: str | None, attempt: int) -> float:
    """Choose a backoff delay from a Retry-After header or the attempt number.

    Parameters
    ----------
    retry_after : str or None
        Header value in seconds, if any.
    attempt : int
        One-based attempt number.

    Returns
    -------
    float
        Delay in seconds, capped to a minute.
    """
    if retry_after and retry_after.strip().isdigit():
        return min(float(retry_after.strip()), _MAX_RETRY_AFTER_SECONDS)
    return float(2**attempt)


class ResponseCache:
    """Store registry responses as JSON snapshots keyed by URL.

    Parameters
    ----------
    directory : Path
        Snapshot directory, created when missing.
    """

    def __init__(self, directory: Path) -> None:
        """Create the cache at a directory, making it when needed.

        Parameters
        ----------
        directory : Path
            Snapshot directory.
        """
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def path(self, url: str) -> Path:
        """Compute the snapshot file for a URL.

        Parameters
        ----------
        url : str
            Requested URL.

        Returns
        -------
        Path
            File named by the URL digest.
        """
        return self.directory / f"{hashlib.sha256(url.encode()).hexdigest()[:24]}.json"

    def load(self, url: str) -> RegistryResponse | None:
        """Read a snapshot if one exists.

        Parameters
        ----------
        url : str
            Requested URL.

        Returns
        -------
        RegistryResponse or None
            Stored response marked as cached, or None.
        """
        path = self.path(url)
        if not path.is_file():
            return None
        data = mapping(json.loads(path.read_text(encoding="utf-8")))
        body = str(data["body"]).encode("utf-8")
        return RegistryResponse(
            url=str(data["url"]),
            status=int(cast("int", data["status"])),
            body=body,
            headers={str(k): str(v) for k, v in mapping(data["headers"]).items()},
            retrieved_utc=str(data["retrieved_utc"]),
            cached=True,
        )

    def store(self, response: RegistryResponse) -> None:
        """Write a snapshot for a response.

        Parameters
        ----------
        response : RegistryResponse
            Response to persist; the body is stored as UTF-8 text.
        """
        record: dict[str, object] = {
            "schema": "paperextract.registry-snapshot",
            "schema_version": 1,
            "url": response.url,
            "status": response.status,
            "retrieved_utc": response.retrieved_utc,
            "headers": dict(response.headers),
            "body_sha256": hashlib.sha256(response.body).hexdigest(),
            "body": response.body.decode("utf-8", errors="replace"),
        }
        self.path(response.url).write_text(dump(record), encoding="utf-8")


@dataclass
class CachedClient:
    """Serve responses from snapshots before asking the network.

    Attributes
    ----------
    client : RegistryClient
        Transport used on a cache miss.
    cache : ResponseCache
        Snapshot store.
    offline : bool
        When True a cache miss raises instead of fetching.
    """

    client: RegistryClient
    cache: ResponseCache
    offline: bool = False

    def get(self, url: str, accept: str) -> RegistryResponse:
        """Return the stored response or fetch and store a new one.

        Parameters
        ----------
        url : str
            Absolute URL.
        accept : str
            ``Accept`` header value.

        Returns
        -------
        RegistryResponse
            Cached or fresh response.

        Raises
        ------
        RegistryUnavailableError
            Offline with no snapshot, or the transport failed.
        """
        cached = self.cache.load(url)
        if cached is not None:
            return cached
        if self.offline:
            raise RegistryUnavailableError(f"offline and no snapshot for {url}")
        response = self.client.get(url, accept)
        if response.status in (_HTTP_OK, _HTTP_NOT_FOUND):
            self.cache.store(response)
        return response


@dataclass(frozen=True)
class Author:
    """Hold one registry author.

    Attributes
    ----------
    given : str or None
        Given names.
    family : str or None
        Family name.
    literal : str or None
        Unparsed or collective name when parts are unavailable.
    orcid : str or None
        ORCID URL or identifier as supplied.
    sequence : str or None
        Provider ordering hint such as ``first``.
    """

    given: str | None
    family: str | None
    literal: str | None
    orcid: str | None
    sequence: str | None

    def to_dict(self) -> dict[str, object]:
        """Serialize the author.

        Returns
        -------
        dict of str to object
            JSON-compatible fields.
        """
        return {
            "given": self.given,
            "family": self.family,
            "literal": self.literal,
            "orcid": self.orcid,
            "sequence": self.sequence,
        }


@dataclass(frozen=True)
class RegistryRecord:
    """Hold the registration metadata of one DOI, as the provider reports it.

    Attributes
    ----------
    provider : str
        ``crossref`` or ``datacite``.
    doi : str
        Normalized lower-case DOI.
    type : str
        Provider resource type such as ``journal-article``.
    title : str or None
        Plain title with markup removed and entities decoded.
    title_raw : str or None
        Title as delivered, possibly with markup.
    subtitle : str or None
        Plain subtitle.
    authors : tuple of Author
        Ordered authors.
    container_title : str or None
        Journal or container title.
    publisher : str or None
        Publisher name.
    volume : str or None
        Volume.
    issue : str or None
        Issue.
    pages : str or None
        Page range as delivered.
    article_number : str or None
        Article number when the provider gives one.
    issued : tuple of int
        Issued date parts, year first, as delivered.
    published_print : tuple of int
        Print publication date parts.
    published_online : tuple of int
        Online publication date parts.
    url : str or None
        Resolver or landing URL.
    issn : tuple of str
        ISSNs.
    license_urls : tuple of str
        License URLs.
    abstract : str or None
        Plain abstract when delivered.
    source_url : str
        Query URL.
    retrieved_utc : str
        Retrieval time of the response.
    body_sha256 : str
        Digest of the raw response body.
    cached : bool
        Whether the response came from a snapshot.
    """

    provider: Provider
    doi: str
    type: str
    title: str | None
    title_raw: str | None
    subtitle: str | None
    authors: tuple[Author, ...]
    container_title: str | None
    publisher: str | None
    volume: str | None
    issue: str | None
    pages: str | None
    article_number: str | None
    issued: tuple[int, ...]
    published_print: tuple[int, ...]
    published_online: tuple[int, ...]
    url: str | None
    issn: tuple[str, ...]
    license_urls: tuple[str, ...]
    abstract: str | None
    source_url: str
    retrieved_utc: str
    body_sha256: str
    cached: bool

    @property
    def year(self) -> int | None:
        """Return the issued year.

        Returns
        -------
        int or None
            First issued date part, or None.
        """
        return self.issued[0] if self.issued else None

    def to_dict(self) -> dict[str, object]:
        """Serialize the record.

        Returns
        -------
        dict of str to object
            JSON-compatible fields.
        """
        return {
            "provider": self.provider,
            "doi": self.doi,
            "type": self.type,
            "title": self.title,
            "title_raw": self.title_raw,
            "subtitle": self.subtitle,
            "authors": [author.to_dict() for author in self.authors],
            "container_title": self.container_title,
            "publisher": self.publisher,
            "volume": self.volume,
            "issue": self.issue,
            "pages": self.pages,
            "article_number": self.article_number,
            "issued": list(self.issued),
            "published_print": list(self.published_print),
            "published_online": list(self.published_online),
            "url": self.url,
            "issn": list(self.issn),
            "license_urls": list(self.license_urls),
            "abstract": self.abstract,
            "source_url": self.source_url,
            "retrieved_utc": self.retrieved_utc,
            "body_sha256": self.body_sha256,
            "cached": self.cached,
        }


@dataclass(frozen=True)
class RegistryFailure:
    """Explain why a DOI produced no usable record.

    Attributes
    ----------
    doi : str
        Normalized DOI.
    kind : str
        ``not_found``, ``unavailable`` or ``malformed``.
    message : str
        Explanation.
    """

    doi: str
    kind: FailureKind
    message: str

    def to_dict(self) -> dict[str, object]:
        """Serialize the failure.

        Returns
        -------
        dict of str to object
            JSON-compatible fields.
        """
        return {"doi": self.doi, "kind": self.kind, "message": self.message}


def normalize_doi(text: str) -> str | None:
    """Normalize a DOI string conservatively.

    Parameters
    ----------
    text : str
        Candidate such as ``doi:10.1000/ABC`` or a resolver URL.

    Returns
    -------
    str or None
        Lower-case DOI without prefixes, or None when the shape is wrong.
        The original spelling stays in the candidate evidence.
    """
    value = text.strip()
    lowered = value.lower()
    for prefix in _DOI_PREFIXES:
        if lowered.startswith(prefix):
            value = value[len(prefix) :]
            lowered = value.lower()
            break
    value = value.rstrip(".,;:)]}'\"")
    return value.lower() if _DOI.match(value) else None


def crossref_url(doi: str) -> str:
    """Build the Crossref works URL for a DOI.

    Parameters
    ----------
    doi : str
        Normalized DOI.

    Returns
    -------
    str
        API URL.
    """
    return f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='/')}"


def datacite_url(doi: str) -> str:
    """Build the DataCite DOIs URL for a DOI.

    Parameters
    ----------
    doi : str
        Normalized DOI.

    Returns
    -------
    str
        API URL.
    """
    return f"https://api.datacite.org/dois/{urllib.parse.quote(doi, safe='/')}"


def _plain(text: object) -> str | None:
    """Strip markup and decode entities from a provider string.

    Parameters
    ----------
    text : object
        Provider value.

    Returns
    -------
    str or None
        Collapsed plain text, or None when absent or blank.
    """
    if not isinstance(text, str):
        return None
    # A MathML element is a word of its own even when the provider omits the
    # surrounding spaces, as Crossref does in "of<mml:math>...</mml:math>and".
    spaced = _MATH_ELEMENT.sub(lambda m: f" {_TAGS.sub('', m.group(0))} ", text)
    cleaned = " ".join(html.unescape(_TAGS.sub("", spaced)).split())
    return cleaned or None


def _first(value: object) -> object:
    """Return the first element of a provider list, or the value itself.

    Parameters
    ----------
    value : object
        Provider value, often a one-element list.

    Returns
    -------
    object
        First element, the scalar, or None for an empty list.
    """
    if isinstance(value, list):
        items = cast("list[object]", value)
        return items[0] if items else None
    return value


def _date_parts(value: object) -> tuple[int, ...]:
    """Read Crossref ``date-parts`` into a tuple of integers.

    Parameters
    ----------
    value : object
        Crossref date object.

    Returns
    -------
    tuple of int
        Year, month, day as far as given.
    """
    if not isinstance(value, dict):
        return ()
    parts = _first(cast("dict[str, object]", value).get("date-parts"))
    if not isinstance(parts, list):
        return ()
    return tuple(int(part) for part in cast("list[object]", parts) if type(part) is int)


def _strings(value: object) -> tuple[str, ...]:
    """Read a provider list of strings.

    Parameters
    ----------
    value : object
        Provider value.

    Returns
    -------
    tuple of str
        Non-empty strings.
    """
    if not isinstance(value, list):
        return ()
    return tuple(
        item for item in cast("list[object]", value) if isinstance(item, str) and item
    )


def _crossref_authors(value: object) -> tuple[Author, ...]:
    """Read Crossref authors.

    Parameters
    ----------
    value : object
        Crossref ``author`` list.

    Returns
    -------
    tuple of Author
        Authors in delivered order.
    """
    authors: list[Author] = []
    for item in _sequence(value):
        entry = mapping(item)
        authors.append(
            Author(
                given=_plain(entry.get("given")),
                family=_plain(entry.get("family")),
                literal=_plain(entry.get("name")),
                orcid=_plain(entry.get("ORCID")),
                sequence=_plain(entry.get("sequence")),
            )
        )
    return tuple(authors)


def _sequence(value: object) -> list[object]:
    """Return a provider list, or an empty list.

    Parameters
    ----------
    value : object
        Provider value.

    Returns
    -------
    list of object
        Items.
    """
    return cast("list[object]", value) if isinstance(value, list) else []


def parse_crossref(response: RegistryResponse) -> RegistryRecord:
    """Parse a Crossref works response.

    Parameters
    ----------
    response : RegistryResponse
        Successful response.

    Returns
    -------
    RegistryRecord
        Parsed record.

    Raises
    ------
    ValueError
        The body is not the expected JSON shape.
    """
    payload = mapping(json.loads(response.body.decode("utf-8")))
    return _crossref_work(mapping(payload.get("message")), response)


def _crossref_work(
    message: Mapping[str, object], response: RegistryResponse
) -> RegistryRecord:
    """Parse one Crossref work message.

    Parameters
    ----------
    message : Mapping of str to object
        Work object from a works or search response.
    response : RegistryResponse
        Response that carried it, for provenance.

    Returns
    -------
    RegistryRecord
        Parsed record.

    Raises
    ------
    ValueError
        The work lacks a DOI.
    """
    doi = normalize_doi(str(message.get("DOI", "")))
    if doi is None:
        raise ValueError("Crossref message lacks a DOI")
    license_urls = tuple(
        url
        for url in (
            _plain(mapping(item).get("URL"))
            for item in _sequence(message.get("license"))
        )
        if url
    )
    return RegistryRecord(
        provider="crossref",
        doi=doi,
        type=_plain(message.get("type")) or "unknown",
        title=_plain(_first(message.get("title"))),
        title_raw=cast("str | None", _first(message.get("title"))),
        subtitle=_plain(_first(message.get("subtitle"))),
        authors=_crossref_authors(message.get("author")),
        container_title=_plain(_first(message.get("container-title"))),
        publisher=_plain(message.get("publisher")),
        volume=_plain(message.get("volume")),
        issue=_plain(message.get("issue")),
        pages=_plain(message.get("page")),
        article_number=_plain(message.get("article-number")),
        issued=_date_parts(message.get("issued")),
        published_print=_date_parts(message.get("published-print")),
        published_online=_date_parts(message.get("published-online")),
        url=_plain(message.get("URL")),
        issn=_strings(message.get("ISSN")),
        license_urls=license_urls,
        abstract=_plain(message.get("abstract")),
        source_url=response.url,
        retrieved_utc=response.retrieved_utc,
        body_sha256=hashlib.sha256(response.body).hexdigest(),
        cached=response.cached,
    )


def _datacite_authors(value: object) -> tuple[Author, ...]:
    """Read DataCite creators.

    Parameters
    ----------
    value : object
        DataCite ``creators`` list.

    Returns
    -------
    tuple of Author
        Authors in delivered order.
    """
    authors: list[Author] = []
    for item in _sequence(value):
        entry = mapping(item)
        orcid = None
        for identifier in _sequence(entry.get("nameIdentifiers")):
            record = mapping(identifier)
            if str(record.get("nameIdentifierScheme", "")).upper() == "ORCID":
                orcid = _plain(record.get("nameIdentifier"))
        authors.append(
            Author(
                given=_plain(entry.get("givenName")),
                family=_plain(entry.get("familyName")),
                literal=_plain(entry.get("name")),
                orcid=orcid,
                sequence=None,
            )
        )
    return tuple(authors)


def parse_datacite(response: RegistryResponse) -> RegistryRecord:
    """Parse a DataCite DOI response.

    Parameters
    ----------
    response : RegistryResponse
        Successful response.

    Returns
    -------
    RegistryRecord
        Parsed record.

    Raises
    ------
    ValueError
        The body is not the expected JSON:API shape.
    """
    payload = mapping(json.loads(response.body.decode("utf-8")))
    attributes = mapping(mapping(payload.get("data")).get("attributes"))
    doi = normalize_doi(str(attributes.get("doi", "")))
    if doi is None:
        raise ValueError("DataCite attributes lack a DOI")
    titles = [
        _plain(mapping(item).get("title"))
        for item in _sequence(attributes.get("titles"))
    ]
    year = attributes.get("publicationYear")
    types = (
        mapping(attributes.get("types"))
        if isinstance(attributes.get("types"), dict)
        else {}
    )
    container = (
        mapping(attributes.get("container"))
        if isinstance(attributes.get("container"), dict)
        else {}
    )
    abstract = next(
        (
            _plain(mapping(item).get("description"))
            for item in _sequence(attributes.get("descriptions"))
            if str(mapping(item).get("descriptionType", "")) == "Abstract"
        ),
        None,
    )
    rights = tuple(
        url
        for url in (
            _plain(mapping(item).get("rightsUri"))
            for item in _sequence(attributes.get("rightsList"))
        )
        if url
    )
    return RegistryRecord(
        provider="datacite",
        doi=doi,
        type=_plain(types.get("resourceTypeGeneral")) or "unknown",
        title=titles[0] if titles else None,
        title_raw=titles[0] if titles else None,
        subtitle=None,
        authors=_datacite_authors(attributes.get("creators")),
        container_title=_plain(container.get("title")),
        publisher=_plain(attributes.get("publisher")),
        volume=_plain(container.get("volume")),
        issue=_plain(container.get("issue")),
        pages=None,
        article_number=None,
        issued=(int(year),) if type(year) is int else (),
        published_print=(),
        published_online=(),
        url=_plain(attributes.get("url")),
        issn=(),
        license_urls=rights,
        abstract=abstract,
        source_url=response.url,
        retrieved_utc=response.retrieved_utc,
        body_sha256=hashlib.sha256(response.body).hexdigest(),
        cached=response.cached,
    )


def lookup_doi(doi: str, client: RegistryClient) -> RegistryRecord | RegistryFailure:
    """Resolve a DOI through Crossref, then DataCite.

    Parameters
    ----------
    doi : str
        DOI in any accepted spelling.
    client : RegistryClient
        Transport, usually a :class:`CachedClient`.

    Returns
    -------
    RegistryRecord or RegistryFailure
        The first provider record found, or why none was.

    Notes
    -----
    A record proves only that the DOI is registered with that metadata. It does
    not prove that the DOI describes the file in hand; the identity stage
    compares the record with article-local evidence before accepting it.
    """
    normalized = normalize_doi(doi)
    if normalized is None:
        return RegistryFailure(doi, "malformed", f"{doi!r} is not a DOI")
    providers: tuple[
        tuple[str, str, Callable[[RegistryResponse], RegistryRecord]], ...
    ] = (
        (crossref_url(normalized), "application/json", parse_crossref),
        (datacite_url(normalized), "application/vnd.api+json", parse_datacite),
    )
    unavailable: list[str] = []
    for url, accept, parser in providers:
        try:
            response = client.get(url, accept)
        except RegistryUnavailableError as exc:
            unavailable.append(str(exc))
            continue
        if response.status == _HTTP_NOT_FOUND:
            continue
        if response.status != _HTTP_OK:
            unavailable.append(f"{url} returned HTTP {response.status}")
            continue
        try:
            return parser(response)
        except (ValueError, TypeError, KeyError) as exc:
            return RegistryFailure(normalized, "malformed", f"{url}: {exc}")
    if unavailable:
        return RegistryFailure(normalized, "unavailable", "; ".join(unavailable))
    return RegistryFailure(
        normalized, "not_found", "not registered with Crossref or DataCite"
    )


SEARCH_ROWS = 5


def crossref_search_url(query: str, rows: int = SEARCH_ROWS) -> str:
    """Build the Crossref bibliographic search URL for a query.

    Parameters
    ----------
    query : str
        Free text such as a title.
    rows : int
        Number of results.

    Returns
    -------
    str
        Search URL.

    Examples
    --------
    >>> crossref_search_url("Hollow fibres", rows=2)
    'https://api.crossref.org/works?query.bibliographic=Hollow%20fibres&rows=2'
    """
    return (
        "https://api.crossref.org/works?query.bibliographic="
        f"{urllib.parse.quote(query, safe='')}&rows={rows}"
    )


def search_bibliographic(
    query: str, client: RegistryClient
) -> tuple[RegistryRecord, ...] | RegistryFailure:
    """Search Crossref for works matching a bibliographic query.

    Parameters
    ----------
    query : str
        Free text, usually the observed title.
    client : RegistryClient
        Transport, usually a :class:`CachedClient`.

    Returns
    -------
    tuple of RegistryRecord or RegistryFailure
        Records in Crossref's relevance order, skipping items without a DOI,
        or why the search could not run.

    Notes
    -----
    A search hit is a candidate only. The identity stage accepts one only
    when the article itself corroborates its title, first author and year.
    """
    url = crossref_search_url(query)
    try:
        response = client.get(url, "application/json")
    except RegistryUnavailableError as exc:
        return RegistryFailure(query, "unavailable", str(exc))
    if response.status != _HTTP_OK:
        return RegistryFailure(
            query, "unavailable", f"{url} returned HTTP {response.status}"
        )
    try:
        payload = mapping(json.loads(response.body.decode("utf-8")))
        found = _sequence(mapping(payload.get("message")).get("items"))
    except (ValueError, TypeError) as exc:
        return RegistryFailure(query, "malformed", f"{url}: {exc}")
    records: list[RegistryRecord] = []
    for item in found:
        try:
            records.append(_crossref_work(mapping(item), response))
        except (ValueError, TypeError):
            continue
    return tuple(records)


def default_search(
    snapshot_directory: Path, *, contact: str | None = None, offline: bool = False
) -> Search:
    """Build the standard bibliographic search behind the snapshot cache.

    Parameters
    ----------
    snapshot_directory : Path
        Where response snapshots live.
    contact : str or None
        Polite-pool contact email; explicit configuration only.
    offline : bool
        Use snapshots only.

    Returns
    -------
    Callable
        Function from query text to records or failure.
    """
    client = CachedClient(
        UrllibClient(contact=contact), ResponseCache(snapshot_directory), offline
    )
    return lambda query: search_bibliographic(query, client)


def default_lookup(
    snapshot_directory: Path, *, contact: str | None = None, offline: bool = False
) -> Lookup:
    """Build the standard lookup: paced network client behind a snapshot cache.

    Parameters
    ----------
    snapshot_directory : Path
        Where response snapshots live, typically inside the library's private
        directory.
    contact : str or None
        Polite-pool contact email; explicit configuration only.
    offline : bool
        Use snapshots only; a miss reports the registry as unavailable.

    Returns
    -------
    Callable
        Function from DOI to record or failure.
    """
    client = CachedClient(
        UrllibClient(contact=contact), ResponseCache(snapshot_directory), offline
    )
    return lambda doi: lookup_doi(doi, client)
