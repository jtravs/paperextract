"""Query registries through a paced, cached client with strict parsing."""

import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from email.message import Message
from pathlib import Path
from typing import cast

import pytest

from paperextract import registry
from paperextract.registry import (
    CachedClient,
    RegistryFailure,
    RegistryRecord,
    RegistryResponse,
    RegistryUnavailableError,
    ResponseCache,
    UrllibClient,
    crossref_search_url,
    crossref_url,
    datacite_url,
    default_lookup,
    default_search,
    lookup_doi,
    normalize_doi,
    parse_crossref,
    parse_datacite,
    search_bibliographic,
    split_name,
)

CROSSREF_BODY = {
    "status": "ok",
    "message": {
        "DOI": "10.1000/Example.1",
        "type": "journal-article",
        "title": ["High-energy pulses in <i>helium</i> &amp; argon"],
        "subtitle": [],
        "container-title": ["Nature Photonics"],
        "short-container-title": ["Nat. Photonics"],
        "publisher": "Springer Science and Business Media LLC",
        "volume": "13",
        "issue": "8",
        "page": "547-554",
        "issued": {"date-parts": [[2019, 4, 15]]},
        "published-print": {"date-parts": [[2019, 8]]},
        "published-online": {"date-parts": [[2019, 4, 15]]},
        "URL": "https://doi.org/10.1000/example.1",
        "ISSN": ["1749-4885", "1749-4893"],
        "license": [{"URL": "http://www.springer.com/tdm"}, {"nope": 1}],
        "author": [
            {
                "given": "John C.",
                "family": "Travers",
                "sequence": "first",
                "ORCID": "https://orcid.org/0000-0003-0350-9104",
            },
            {"given": "Teodora F.", "family": "Grigorova", "sequence": "additional"},
            {"name": "The Collaboration"},
        ],
    },
}

DATACITE_BODY = {
    "data": {
        "id": "10.5281/zenodo.1",
        "attributes": {
            "doi": "10.5281/ZENODO.1",
            "titles": [{"title": "Supplemental &amp; data"}],
            "creators": [
                {
                    "name": "Buscicchio, Riccardo",
                    "givenName": "Riccardo",
                    "familyName": "Buscicchio",
                    "nameIdentifiers": [
                        {
                            "nameIdentifier": "https://orcid.org/0000-0002-7387-6754",
                            "nameIdentifierScheme": "ORCID",
                        }
                    ],
                },
                {"name": "Some Consortium"},
            ],
            "publisher": "Zenodo",
            "publicationYear": 2019,
            "types": {"resourceTypeGeneral": "Dataset", "bibtex": "misc"},
            "url": "https://zenodo.org/record/1",
            "container": {"title": "A Series", "volume": "2"},
            "descriptions": [
                {
                    "description": "Posterior <b>samples</b>",
                    "descriptionType": "Abstract",
                },
                {"description": "Other", "descriptionType": "Other"},
            ],
            "rightsList": [
                {"rightsUri": "https://creativecommons.org/licenses/by/4.0/"}
            ],
        },
    }
}


def response(
    url: str, status: int, payload: object, **headers: str
) -> RegistryResponse:
    body = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
    return RegistryResponse(
        url,
        status,
        body,
        {k.replace("_", "-"): v for k, v in headers.items()},
        "2026-09-22T00:00:00+00:00",
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("10.1038/s41566-019-0416-4", "10.1038/s41566-019-0416-4"),
        ("doi:10.1038/S41566-019-0416-4.", "10.1038/s41566-019-0416-4"),
        ("https://doi.org/10.1000/ABC;", "10.1000/abc"),
        ("http://dx.doi.org/10.1000/x)", "10.1000/x"),
        ("10.10/short", None),
        ("not a doi", None),
        ("10.1000/", None),
    ],
)
def test_doi_normalization_is_conservative(text: str, expected: str | None) -> None:
    assert normalize_doi(text) == expected


def test_urls_quote_identifiers() -> None:
    assert crossref_url("10.1000/a b") == "https://api.crossref.org/works/10.1000/a%20b"
    assert (
        datacite_url("10.5281/zenodo.1")
        == "https://api.datacite.org/dois/10.5281/zenodo.1"
    )


def test_crossref_records_are_parsed_and_cleaned() -> None:
    record = parse_crossref(
        response(crossref_url("10.1000/example.1"), 200, CROSSREF_BODY)
    )
    assert record.provider == "crossref"
    assert record.doi == "10.1000/example.1"
    assert record.title == "High-energy pulses in helium & argon"
    assert record.title_raw == "High-energy pulses in <i>helium</i> &amp; argon"
    assert record.subtitle is None
    assert [(a.given, a.family, a.literal, a.sequence) for a in record.authors] == [
        ("John C.", "Travers", None, "first"),
        ("Teodora F.", "Grigorova", None, "additional"),
        (None, None, "The Collaboration", None),
    ]
    assert record.authors[0].orcid == "https://orcid.org/0000-0003-0350-9104"
    assert record.container_title == "Nature Photonics"
    assert (record.volume, record.issue, record.pages, record.article_number) == (
        "13",
        "8",
        "547-554",
        None,
    )
    assert record.issued == (2019, 4, 15)
    assert record.published_print == (2019, 8)
    assert record.year == 2019
    assert record.issn == ("1749-4885", "1749-4893")
    assert record.license_urls == ("http://www.springer.com/tdm",)
    assert record.abstract is None
    serialized = record.to_dict()["authors"]
    assert isinstance(serialized, list) and serialized[0]["family"] == "Travers"
    assert len(record.body_sha256) == 64


def test_datacite_records_are_parsed() -> None:
    record = parse_datacite(
        response(datacite_url("10.5281/zenodo.1"), 200, DATACITE_BODY)
    )
    assert record.provider == "datacite"
    assert record.doi == "10.5281/zenodo.1"
    assert record.title == "Supplemental & data"
    assert record.type == "Dataset"
    assert record.authors[0].family == "Buscicchio"
    assert record.authors[0].orcid == "https://orcid.org/0000-0002-7387-6754"
    assert record.authors[1].literal == "Some Consortium"
    assert record.publisher == "Zenodo"
    assert record.issued == (2019,)
    assert record.container_title == "A Series"
    assert record.volume == "2"
    assert record.abstract == "Posterior samples"
    assert record.license_urls == ("https://creativecommons.org/licenses/by/4.0/",)


@pytest.mark.parametrize(
    ("parser", "payload"),
    [
        (parse_crossref, {"message": {"title": ["no doi"]}}),
        (parse_crossref, {"message": {"DOI": "bad"}}),
        (parse_datacite, {"data": {"attributes": {"doi": "bad"}}}),
    ],
)
def test_parsers_reject_records_without_a_doi(
    parser: Callable[[RegistryResponse], RegistryRecord], payload: dict[str, object]
) -> None:
    with pytest.raises(ValueError):
        parser(response("u", 200, payload))


def test_datacite_year_must_be_an_integer() -> None:
    payload = {
        "data": {"attributes": {"doi": "10.5281/zenodo.1", "publicationYear": "2019"}}
    }
    record = parse_datacite(response("u", 200, payload))
    assert record.issued == () and record.year is None and record.authors == ()


class FakeClient:
    def __init__(self, responses: dict[str, RegistryResponse | Exception]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    def get(self, url: str, accept: str) -> RegistryResponse:
        self.calls.append((url, accept))
        result = self.responses[url]
        if isinstance(result, Exception):
            raise result
        return result


def test_lookup_prefers_crossref_then_datacite_then_reports() -> None:
    cross = crossref_url("10.1000/example.1")
    data = datacite_url("10.1000/example.1")
    client = FakeClient({cross: response(cross, 200, CROSSREF_BODY)})
    record = lookup_doi("DOI:10.1000/Example.1", client)
    assert isinstance(record, RegistryRecord) and record.provider == "crossref"
    client = FakeClient(
        {
            cross: response(cross, 404, b"Resource not found."),
            data: response(data, 200, DATACITE_BODY),
        }
    )
    record = lookup_doi("10.1000/example.1", client)
    assert isinstance(record, RegistryRecord) and record.provider == "datacite"
    client = FakeClient(
        {cross: response(cross, 404, b""), data: response(data, 404, b"")}
    )
    failure = lookup_doi("10.1000/example.1", client)
    assert isinstance(failure, RegistryFailure) and failure.kind == "not_found"
    client = FakeClient(
        {cross: RegistryUnavailableError("down"), data: response(data, 500, b"")}
    )
    failure = lookup_doi("10.1000/example.1", client)
    assert isinstance(failure, RegistryFailure) and failure.kind == "unavailable"
    assert "down" in failure.message and "HTTP 500" in failure.message
    client = FakeClient({cross: response(cross, 200, b"{not json")})
    failure = lookup_doi("10.1000/example.1", client)
    assert isinstance(failure, RegistryFailure) and failure.kind == "malformed"
    failure = lookup_doi("nonsense", client)
    assert isinstance(failure, RegistryFailure) and failure.kind == "malformed"
    assert failure.to_dict()["kind"] == "malformed"


def test_cache_serves_snapshots_and_respects_offline(tmp_path: Path) -> None:
    cross = crossref_url("10.1000/example.1")
    client = FakeClient(
        {cross: response(cross, 200, CROSSREF_BODY, x_rate_limit_limit="5")}
    )
    cached = CachedClient(client, ResponseCache(tmp_path / "snapshots"))
    first = cached.get(cross, "application/json")
    second = cached.get(cross, "application/json")
    assert first.cached is False and second.cached is True
    assert second.body == first.body and second.headers["x-rate-limit-limit"] == "5"
    assert [url for url, _accept in client.calls] == [cross]
    snapshot = json.loads(next((tmp_path / "snapshots").glob("*.json")).read_text())
    assert snapshot["schema"] == "paperextract.registry-snapshot"
    assert snapshot["status"] == 200
    offline = CachedClient(
        FakeClient({}), ResponseCache(tmp_path / "snapshots"), offline=True
    )
    assert offline.get(cross, "application/json").cached is True
    with pytest.raises(RegistryUnavailableError, match="offline"):
        offline.get(datacite_url("10.1000/other"), "application/json")
    transient = FakeClient({cross: response(cross, 503, b"")})
    CachedClient(transient, ResponseCache(tmp_path / "other")).get(
        cross, "application/json"
    )
    assert not list((tmp_path / "other").glob("*.json"))


class FakeHTTPResponse:
    def __init__(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.status = status
        self._body = body
        self.headers = Message()
        for key, value in headers.items():
            self.headers[key] = value

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def http_error(
    status: int, body: bytes = b"", **headers: str
) -> urllib.error.HTTPError:
    message = Message()
    for key, value in headers.items():
        message[key.replace("_", "-")] = value
    return urllib.error.HTTPError("https://x", status, "err", message, io.BytesIO(body))


def transport(
    *results: FakeHTTPResponse | Exception,
) -> Callable[[urllib.request.Request, float], FakeHTTPResponse]:
    """Return a fake opener yielding the results in order, repeating the last."""
    queue: list[FakeHTTPResponse | Exception | tuple[int, bytes, dict[str, str]]] = []
    for result in results:
        if isinstance(result, urllib.error.HTTPError):
            # Each attempt gets its own error, as from a server, since the
            # client closes the one it handled; the template is closed here.
            with result:
                queue.append((result.code, result.read(), dict(result.headers.items())))
        else:
            queue.append(result)

    def open_(request: urllib.request.Request, timeout: float) -> FakeHTTPResponse:
        del request, timeout
        result = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(result, tuple):
            code, body, headers = result
            raise http_error(code, body, **headers)
        if isinstance(result, Exception):
            raise result
        return result

    return open_


def test_urllib_client_paces_retries_and_learns_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(
        registry,
        "_open",
        transport(
            http_error(429, retry_after="2"),
            FakeHTTPResponse(
                200, b"{}", {"X-Rate-Limit-Limit": "2", "X-Rate-Limit-Interval": "1s"}
            ),
        ),
    )
    client = UrllibClient(
        contact="someone@example.org", sleep=sleeps.append, min_interval_seconds=0.0
    )
    assert "mailto:someone@example.org" in client.user_agent()
    result = client.get("https://api.example.org/works/1", "application/json")
    assert result.status == 200 and result.body == b"{}"
    assert result.headers["x-rate-limit-limit"] == "2"
    assert 2.0 in sleeps
    assert client.min_interval_seconds == 0.5
    assert UrllibClient().user_agent().endswith(")")


def test_urllib_client_reports_persistent_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(registry, "_open", transport(http_error(503)))
    client = UrllibClient(sleep=sleeps.append, min_interval_seconds=0.0, max_attempts=2)
    with pytest.raises(RegistryUnavailableError, match="HTTP 503"):
        client.get("https://api.example.org/works/1", "application/json")
    assert sleeps == [2.0]
    monkeypatch.setattr(registry, "_open", transport(urllib.error.URLError("dns")))
    with pytest.raises(RegistryUnavailableError, match="URLError"):
        UrllibClient(sleep=sleeps.append, min_interval_seconds=0.0, max_attempts=2).get(
            "https://x", "a"
        )
    monkeypatch.setattr(
        registry, "_open", transport(FakeHTTPResponse(404, b"missing", {}))
    )
    assert UrllibClient(min_interval_seconds=0.0).get("https://x", "a").status == 404


def test_pacing_waits_between_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(registry, "_open", transport(FakeHTTPResponse(200, b"{}", {})))
    client = UrllibClient(sleep=sleeps.append, min_interval_seconds=5.0)
    client.get("https://x/1", "a")
    client.get("https://x/2", "a")
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 5.0


def test_default_lookup_uses_snapshots(tmp_path: Path) -> None:
    cross = crossref_url("10.1000/example.1")
    ResponseCache(tmp_path / "snap").store(response(cross, 200, CROSSREF_BODY))
    lookup = default_lookup(tmp_path / "snap", offline=True)
    record = lookup("10.1000/example.1")
    assert isinstance(record, RegistryRecord) and record.cached is True
    failure = lookup("10.1000/unknown")
    assert isinstance(failure, RegistryFailure) and failure.kind == "unavailable"


def test_open_reads_local_files_without_sockets(tmp_path: Path) -> None:
    path = tmp_path / "body.json"
    path.write_bytes(b'{"ok": true}')
    request = urllib.request.Request(path.as_uri())
    # The transport seam is exercised without sockets through a file URL.
    with registry._open(request, 5.0) as handle:  # pyright: ignore[reportPrivateUsage]
        assert handle.read() == b'{"ok": true}'


def test_rate_limit_headers_are_ignored_when_unparseable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fetch(headers: dict[str, str]) -> UrllibClient:
        monkeypatch.setattr(
            registry, "_open", transport(FakeHTTPResponse(200, b"{}", headers))
        )
        client = UrllibClient(min_interval_seconds=0.1, sleep=lambda _seconds: None)
        client.get("https://x", "a")
        return client

    assert fetch({"X-Rate-Limit-Limit": "x"}).min_interval_seconds == 0.1
    limits = {"X-Rate-Limit-Limit": "4", "X-Rate-Limit-Interval": "1.5s"}
    assert fetch(limits).min_interval_seconds == 0.1
    limits = {"X-Rate-Limit-Limit": "4", "X-Rate-Limit-Interval": "1m"}
    assert fetch(limits).min_interval_seconds == 0.1
    limits = {"X-Rate-Limit-Limit": "4", "X-Rate-Limit-Interval": "2s"}
    assert fetch(limits).min_interval_seconds == 0.5


def test_parsers_tolerate_sparse_and_odd_shapes() -> None:
    sparse: dict[str, object] = {
        "message": {
            "DOI": "10.1000/sparse",
            "title": [],
            "issued": "2019",
            "published-print": {"date-parts": "x"},
            "published-online": {"date-parts": [["2019", 4]]},
            "ISSN": "1749-4885",
            "author": "nobody",
        }
    }
    record = parse_crossref(response("u", 200, sparse))
    assert record.title is None and record.title_raw is None
    assert (
        record.issued == ()
        and record.published_print == ()
        and record.published_online == (4,)
    )
    assert record.issn == () and record.authors == () and record.type == "unknown"
    datacite: dict[str, object] = {
        "data": {
            "attributes": {
                "doi": "10.5281/zenodo.2",
                "creators": [
                    {
                        "name": "Only Name",
                        "nameIdentifiers": [
                            {"nameIdentifier": "x", "nameIdentifierScheme": "ISNI"}
                        ],
                    }
                ],
                "descriptions": [{"description": "Other", "descriptionType": "Other"}],
                "titles": [],
            }
        }
    }
    record = parse_datacite(response("u", 200, datacite))
    assert (
        record.title is None
        and record.type == "unknown"
        and record.container_title is None
    )
    assert record.authors[0].orcid is None and record.authors[0].literal == "Only Name"
    assert record.abstract is None and record.license_urls == ()


def test_lookup_reports_datacite_transport_failure_after_crossref_not_found() -> None:
    cross = crossref_url("10.1000/example.1")
    data = datacite_url("10.1000/example.1")
    client = FakeClient(
        {cross: response(cross, 404, b""), data: RegistryUnavailableError("down")}
    )
    failure = lookup_doi("10.1000/example.1", client)
    assert isinstance(failure, RegistryFailure) and failure.kind == "unavailable"


def test_bibliographic_search_parses_items_and_reports_failures(tmp_path: Path) -> None:
    url = crossref_search_url("Pulse title")
    work = CROSSREF_BODY["message"]
    body = {"message": {"items": [work, {"title": ["No DOI"]}, "odd"]}}
    client = FakeClient({url: response(url, 200, body)})
    records = search_bibliographic("Pulse title", client)
    assert isinstance(records, tuple)
    assert [record.doi for record in records] == ["10.1000/example.1"]
    assert records[0].source_url == url
    for result, kind in (
        (response(url, 500, {}), "unavailable"),
        (RegistryUnavailableError("down"), "unavailable"),
        (response(url, 200, b"not json"), "malformed"),
    ):
        failure = search_bibliographic("Pulse title", FakeClient({url: result}))
        assert isinstance(failure, RegistryFailure) and failure.kind == kind
    ResponseCache(tmp_path / "snap").store(response(url, 200, body))
    search = default_search(tmp_path / "snap", offline=True)
    found = search("Pulse title")
    assert isinstance(found, tuple) and found[0].cached is True
    missing = search("Other title")
    assert isinstance(missing, RegistryFailure) and missing.kind == "unavailable"


def test_mathml_elements_stay_separate_words_in_plain_titles() -> None:
    title = (
        "Susceptibility of<mml:math><mml:msub><mml:mi>H</mml:mi><mml:mn>2</mml:mn>"
        "</mml:msub></mml:math>and D<sub>2</sub>"
    )
    work = dict(cast("dict[str, object]", CROSSREF_BODY["message"]))
    work["title"] = [title]
    url = crossref_url("10.1000/example.1")
    record = parse_crossref(response(url, 200, {"message": work}))
    assert record.title == "Susceptibility of H2 and D2"


@pytest.mark.parametrize(
    ("name", "parts"),
    [
        ("D V Willetts", ("D V", "Willetts")),
        ("D.V. Willetts", ("D.V.", "Willetts")),
        ("J.-P. Wolf", ("J.-P.", "Wolf")),
        ("JP McDonald", ("JP", "McDonald")),
        ("Willetts", None),
        ("P Th van Duijnen", None),
        ("D V WILLETTS", None),
        ("A B C D E Smith", None),
    ],
)
def test_whole_names_split_only_into_initials_and_one_surname(
    name: str, parts: tuple[str, str] | None
) -> None:
    assert split_name(name) == parts


def test_crossref_whole_names_and_translation_links_are_read() -> None:
    message: dict[str, object] = {
        **cast("dict[str, object]", CROSSREF_BODY["message"]),
        "author": [
            {"family": "D V Willetts", "sequence": "first"},
            {"given": "M. R.", "family": "Harris"},
            {"family": "Collaboration"},
            {"family": "M R Harris", "name": "M R Harris"},
        ],
        "relation": {
            "is-translation-of": [
                {"id-type": "doi", "id": "10.3367/UFNr.0154.198802a.0177"},
                {"id-type": "uri", "id": "https://example.org"},
                {"id-type": "doi", "id": "not a doi"},
            ]
        },
    }
    record = parse_crossref(
        response(crossref_url("10.1000/example.1"), 200, {"message": message})
    )
    assert [(a.given, a.family, a.literal) for a in record.authors] == [
        ("D V", "Willetts", "D V Willetts"),
        ("M. R.", "Harris", None),
        (None, "Collaboration", None),
        (None, "M R Harris", "M R Harris"),
    ]
    assert record.relations == (
        ("is-translation-of", "10.3367/ufnr.0154.198802a.0177"),
    )
    assert record.to_dict()["relations"] == [
        ["is-translation-of", "10.3367/ufnr.0154.198802a.0177"]
    ]
    plain = parse_crossref(
        response(
            crossref_url("10.1000/example.1"),
            200,
            {
                "message": {
                    **cast("dict[str, object]", CROSSREF_BODY["message"]),
                    "relation": [],
                }
            },
        )
    )
    assert plain.relations == ()
