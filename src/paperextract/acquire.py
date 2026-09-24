"""Acquire a paper from arXiv on explicit request.

``paperextract extract arXiv:2206.01062v2`` downloads that version's PDF and
extracts it like a local file. An unversioned request is resolved to the
current version through the arXiv API first, and the resolution is
recorded. Only arXiv is supported; nothing is downloaded unless the user
names an identifier, and never in offline mode. Downloads follow arXiv's
request that automated clients pause three seconds between requests.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from xml.etree import ElementTree

__all__ = [
    "ARXIV_PAUSE_SECONDS",
    "MAX_PDF_BYTES",
    "Acquisition",
    "AcquisitionError",
    "Fetch",
    "acquire_arxiv",
    "arxiv_request",
    "urllib_fetch",
]

ARXIV_PAUSE_SECONDS = 3.0
MAX_PDF_BYTES = 200 * 1024 * 1024
_API = "https://export.arxiv.org/api/query?id_list={}"
_PDF = "https://arxiv.org/pdf/{}"
_ATOM = "{http://www.w3.org/2005/Atom}"
_IDENTIFIER = re.compile(
    r"^(?:arxiv:|https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/)"
    r"((?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?)(?:\.pdf)?$",
    re.IGNORECASE,
)
_VERSIONED = re.compile(r"/abs/(.+v\d+)$")
_TIMEOUT_SECONDS = 60.0

Fetch = Callable[[str, int], bytes]


class AcquisitionError(RuntimeError):
    """Report a request that could not be downloaded or verified."""


@dataclass(frozen=True)
class Acquisition:
    """Record one downloaded paper.

    Attributes
    ----------
    path : Path
        Saved PDF.
    requested : str
        Identifier as the user gave it.
    identifier : str
        Versioned arXiv identifier that was downloaded.
    url : str
        Download address.
    sha256 : str
        Digest of the saved bytes.
    retrieved_utc : str
        Download time.
    """

    path: Path
    requested: str
    identifier: str
    url: str
    sha256: str
    retrieved_utc: str

    def hints(self) -> dict[str, str]:
        """Return the acquisition as publication hints.

        Returns
        -------
        dict of str to str
            Request, resolved identifier, address and time.
        """
        return {
            "acquired_from": self.url,
            "acquired_utc": self.retrieved_utc,
            "arxiv_requested": self.requested,
            "arxiv": self.identifier,
        }


def arxiv_request(text: str) -> tuple[str, bool] | None:
    """Recognize an arXiv request.

    Parameters
    ----------
    text : str
        Command-line argument such as ``arXiv:2206.01062v2`` or an arXiv
        abstract or PDF address.

    Returns
    -------
    tuple or None
        Identifier and whether it names a version, or None when the text is
        not an arXiv request.

    Examples
    --------
    >>> arxiv_request("arXiv:2206.01062v2")
    ('2206.01062v2', True)
    >>> arxiv_request("https://arxiv.org/abs/hep-th/9901001")
    ('hep-th/9901001', False)
    >>> arxiv_request("paper.pdf") is None
    True
    """
    match = _IDENTIFIER.match(text.strip())
    if match is None:
        return None
    return match.group(1), match.group(2) is not None


def _user_agent(contact: str | None) -> str:
    """Build the User-Agent string.

    Parameters
    ----------
    contact : str or None
        Configured contact address.

    Returns
    -------
    str
        Product token, with the contact when configured.
    """
    version = metadata.version("paperextract")
    base = f"paperextract/{version} (https://github.com/jtravs/paperextract"
    return f"{base}; mailto:{contact})" if contact else f"{base})"


def urllib_fetch(contact: str | None = None) -> Fetch:
    """Build a bounded HTTP GET function.

    Parameters
    ----------
    contact : str or None
        Contact address for the User-Agent.

    Returns
    -------
    Callable
        Function from address and byte limit to the response body.
    """

    def fetch(url: str, limit: int) -> bytes:
        """Download at most ``limit`` bytes.

        Parameters
        ----------
        url : str
            Address.
        limit : int
            Largest accepted body in bytes.

        Returns
        -------
        bytes
            Response body.
        """
        request = urllib.request.Request(
            url, headers={"User-Agent": _user_agent(contact)}
        )
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
                body = response.read(limit + 1)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise AcquisitionError(f"{url}: {exc}") from exc
        if len(body) > limit:
            raise AcquisitionError(f"{url}: response exceeds {limit} bytes")
        return body

    return fetch


def _current_version(identifier: str, fetch: Fetch) -> str:
    """Resolve an unversioned identifier through the arXiv API.

    Parameters
    ----------
    identifier : str
        Unversioned identifier.
    fetch : Callable
        HTTP GET.

    Returns
    -------
    str
        Versioned identifier.

    Raises
    ------
    AcquisitionError
        The API does not know the identifier.
    """
    body = fetch(_API.format(identifier), 1024 * 1024)
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise AcquisitionError(f"arXiv API answer is not XML: {exc}") from exc
    for entry in root.iter(f"{_ATOM}entry"):
        found = _VERSIONED.search((entry.findtext(f"{_ATOM}id") or "").strip())
        if found is not None:
            return found.group(1)
    raise AcquisitionError(f"arXiv does not know {identifier}")


def acquire_arxiv(
    text: str,
    directory: Path,
    fetch: Fetch,
    *,
    pause: Callable[[float], None] = time.sleep,
    clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> Acquisition:
    """Download an arXiv paper's PDF.

    Parameters
    ----------
    text : str
        An arXiv request, see :func:`arxiv_request`.
    directory : Path
        Directory that receives ``<identifier>.pdf``; created when missing.
    fetch : Callable
        HTTP GET, such as :func:`urllib_fetch`.
    pause : Callable
        Sleep between two requests.
    clock : Callable
        Source of the retrieval time.

    Returns
    -------
    Acquisition
        Saved file and provenance. An already downloaded version is reused
        without a request.

    Raises
    ------
    ValueError
        The text is not an arXiv request.
    AcquisitionError
        The download failed or did not return a PDF.
    """
    request = arxiv_request(text)
    if request is None:
        raise ValueError(f"{text!r} is not an arXiv identifier")
    identifier, versioned = request
    if not versioned:
        identifier = _current_version(identifier, fetch)
        pause(ARXIV_PAUSE_SECONDS)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{identifier.replace('/', '_')}.pdf"
    url = _PDF.format(identifier)
    if path.is_file():
        body = path.read_bytes()
    else:
        body = fetch(url, MAX_PDF_BYTES)
        if not body.startswith(b"%PDF-"):
            raise AcquisitionError(f"{url} did not return a PDF")
        partial = path.with_suffix(".part")
        partial.write_bytes(body)
        partial.replace(path)
    return Acquisition(
        path=path,
        requested=text,
        identifier=identifier,
        url=url,
        sha256=hashlib.sha256(body).hexdigest(),
        retrieved_utc=clock().isoformat(timespec="seconds"),
    )
