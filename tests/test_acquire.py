"""Download arXiv papers on explicit request, with fake HTTP."""

import datetime as dt
import io
import urllib.error
from pathlib import Path

import pytest

import paperextract.acquire
from paperextract.acquire import (
    AcquisitionError,
    acquire_arxiv,
    arxiv_request,
    urllib_fetch,
)

FEED = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Error</title></entry>
<entry><id>http://arxiv.org/abs/2206.01062v3</id></entry></feed>"""
CLOCK = dt.datetime(2026, 9, 23, tzinfo=dt.UTC)


class FakeFetch:
    def __init__(self, answers: dict[str, bytes]) -> None:
        self.answers = answers
        self.urls: list[str] = []

    def __call__(self, url: str, limit: int) -> bytes:
        self.urls.append(url)
        assert limit > 0
        return self.answers[url]


def test_requests_are_recognized() -> None:
    assert arxiv_request("arxiv:2206.01062") == ("2206.01062", False)
    assert arxiv_request("https://arxiv.org/pdf/2206.01062v2.pdf") == (
        "2206.01062v2",
        True,
    )
    assert arxiv_request("10.1000/x") is None


def test_an_unversioned_request_is_resolved_and_reused(tmp_path: Path) -> None:
    fetch = FakeFetch(
        {
            "https://export.arxiv.org/api/query?id_list=2206.01062": FEED,
            "https://arxiv.org/pdf/2206.01062v3": b"%PDF-1.7 body",
        }
    )
    pauses: list[float] = []
    acquisition = acquire_arxiv(
        "arXiv:2206.01062", tmp_path, fetch, pause=pauses.append, clock=lambda: CLOCK
    )
    assert acquisition.identifier == "2206.01062v3"
    assert acquisition.path == tmp_path / "2206.01062v3.pdf"
    assert pauses == [3.0]
    assert acquisition.hints() == {
        "acquired_from": "https://arxiv.org/pdf/2206.01062v3",
        "acquired_utc": "2026-09-23T00:00:00+00:00",
        "arxiv_requested": "arXiv:2206.01062",
        "arxiv": "2206.01062v3",
    }
    again = acquire_arxiv("arXiv:2206.01062v3", tmp_path, fetch, clock=lambda: CLOCK)
    assert again.sha256 == acquisition.sha256
    assert len(fetch.urls) == 2
    old = acquire_arxiv(
        "arXiv:hep-th/9901001v1",
        tmp_path,
        FakeFetch({"https://arxiv.org/pdf/hep-th/9901001v1": b"%PDF-1.4"}),
    )
    assert old.path.name == "hep-th_9901001v1.pdf"


@pytest.mark.parametrize(
    ("answers", "message"),
    [
        ({"https://export.arxiv.org/api/query?id_list=1234.5678": b"<not"}, "not XML"),
        (
            {"https://export.arxiv.org/api/query?id_list=1234.5678": b"<feed/>"},
            "does not know",
        ),
    ],
)
def test_unknown_identifiers_are_reported(
    tmp_path: Path, answers: dict[str, bytes], message: str
) -> None:
    with pytest.raises(AcquisitionError, match=message):
        acquire_arxiv("arXiv:1234.5678", tmp_path, FakeFetch(answers), pause=print)


def test_only_pdfs_are_saved(tmp_path: Path) -> None:
    fetch = FakeFetch({"https://arxiv.org/pdf/1234.5678v1": b"<html>captcha"})
    with pytest.raises(AcquisitionError, match="did not return a PDF"):
        acquire_arxiv("arXiv:1234.5678v1", tmp_path, fetch)
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(ValueError, match="not an arXiv identifier"):
        acquire_arxiv("paper.pdf", tmp_path, fetch)


def test_http_downloads_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def opener(request: object, timeout: float) -> io.BytesIO:
        seen.append(request.get_header("User-agent"))  # type: ignore[attr-defined]
        assert timeout > 0
        return io.BytesIO(b"x" * 20)

    monkeypatch.setattr(paperextract.acquire.urllib.request, "urlopen", opener)
    assert urllib_fetch("me@example.org")("https://arxiv.org/x", 100) == b"x" * 20
    assert "mailto:me@example.org" in seen[0]
    with pytest.raises(AcquisitionError, match="exceeds 10 bytes"):
        urllib_fetch()("https://arxiv.org/x", 10)
    assert "mailto" not in seen[1]

    def failing(request: object, timeout: float) -> io.BytesIO:
        raise urllib.error.URLError(f"no route for {request} within {timeout}")

    monkeypatch.setattr(paperextract.acquire.urllib.request, "urlopen", failing)
    with pytest.raises(AcquisitionError, match="no route"):
        urllib_fetch()("https://arxiv.org/x", 10)
