"""Preserve saved web pages and read their main HTML."""

import json
from pathlib import Path

import pytest

import paperextract.capture
from paperextract.capture import (
    CAPTURE_RECORD,
    CaptureError,
    CaptureRecord,
    capture_kind,
    preserve_capture,
    read_capture_html,
    read_capture_record,
    read_page_html,
)

PAGE = (
    "<!-- saved from url=(0027)https://example.org/article -->\n"
    '<!DOCTYPE html><html><head><meta charset="utf-8">'
    '<meta name="citation_doi" content="10.1000/Z"></head>'
    "<body><p>Größe</p></body></html>"
)


def mhtml(html: str, *, location: str | None = "https://example.org/a") -> bytes:
    snapshot = f"Snapshot-Content-Location: {location}\r\n" if location else ""
    part_location = location or "https://example.org/other"
    return (
        "From: <Saved by Blink>\r\n"
        f"{snapshot}"
        "Subject: Article\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/related; type="text/html"; boundary="B"\r\n'
        "\r\n"
        "--B\r\n"
        "Content-Type: image/png\r\n"
        "Content-Transfer-Encoding: base64\r\n"
        "Content-Location: https://example.org/logo.png\r\n"
        "\r\n"
        "iVBORw0KGgo=\r\n"
        "--B\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "Content-Transfer-Encoding: quoted-printable\r\n"
        f"Content-Location: {part_location}\r\n"
        "\r\n"
        f"{html}\r\n"
        "--B--\r\n"
    ).encode()


def test_captures_are_recognized_by_content(tmp_path: Path) -> None:
    html = tmp_path / "page.txt"
    html.write_text(PAGE)
    archive = tmp_path / "page.bin"
    archive.write_bytes(mhtml("<html></html>"))
    other = tmp_path / "paper.html"
    other.write_bytes(b"%PDF-1.4")
    bom = tmp_path / "bom.htm"
    bom.write_bytes(b"\xef\xbb\xbf  <html><body></body></html>")
    assert capture_kind(html) == "html"
    assert capture_kind(archive) == "mhtml"
    assert capture_kind(bom) == "html"
    assert capture_kind(other) is None
    assert capture_kind(tmp_path / "missing.html") is None


def test_a_complete_page_keeps_its_asset_directory(tmp_path: Path) -> None:
    source = tmp_path / "in" / "Article.html"
    source.parent.mkdir()
    source.write_text(PAGE)
    assets = tmp_path / "in" / "Article_files"
    (assets / "nested").mkdir(parents=True)
    (assets / "fig1.png").write_bytes(b"png")
    (assets / "nested" / "style.css").write_text("p {}")
    record = preserve_capture(source, tmp_path / "kept")
    assert record.kind == "html"
    assert record.saved_from == "https://example.org/article"
    assert [f.path for f in record.files] == [
        "Article.html",
        "Article_files/fig1.png",
        "Article_files/nested/style.css",
    ]
    assert (tmp_path / "kept" / "Article_files" / "nested" / "style.css").is_file()
    assert read_capture_record(tmp_path / "kept") == record
    text = read_capture_html(tmp_path / "kept", record)
    assert "Größe" in text
    assert read_page_html(source) == text
    with pytest.raises(FileExistsError):
        preserve_capture(source, tmp_path / "kept")


def test_an_mhtml_archive_decodes_its_main_part(tmp_path: Path) -> None:
    source = tmp_path / "page.mhtml"
    source.write_bytes(mhtml("<html><p>na=C3=AFve</p></html>"))
    record = preserve_capture(source, tmp_path / "kept")
    assert (record.kind, record.saved_from, len(record.files)) == (
        "mhtml",
        "https://example.org/a",
        1,
    )
    assert "naïve" in read_capture_html(tmp_path / "kept", record)
    # Without a snapshot location the first HTML part is the page.
    other = tmp_path / "other.mhtml"
    other.write_bytes(mhtml("<html><p>first</p></html>", location=None))
    assert "first" in read_page_html(other)
    record = preserve_capture(other, tmp_path / "kept-other")
    assert record.saved_from is None


def test_unusable_captures_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = tmp_path / "notes.html"
    text.write_text("plain notes")
    with pytest.raises(CaptureError, match="neither"):
        preserve_capture(text, tmp_path / "a")
    with pytest.raises(CaptureError, match="neither"):
        read_page_html(text)
    empty = tmp_path / "empty.mhtml"
    empty.write_bytes(
        mhtml("<p>x</p>").replace(b"text/html; charset=utf-8", b"text/plain")
    )
    with pytest.raises(CaptureError, match="no HTML part"):
        read_page_html(empty)
    source = tmp_path / "linked.html"
    source.write_text(PAGE)
    (tmp_path / "linked_files").mkdir()
    (tmp_path / "linked_files" / "link").symlink_to(source)
    with pytest.raises(CaptureError, match="symbolic link"):
        preserve_capture(source, tmp_path / "b")
    page = tmp_path / "page.html"
    page.write_text(PAGE)
    monkeypatch.setattr(paperextract.capture, "MAX_CAPTURE_BYTES", 10)
    with pytest.raises(CaptureError, match="exceeds the limit"):
        preserve_capture(page, tmp_path / "c")


def test_a_capture_that_changes_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = tmp_path / "page.html"
    page.write_text(PAGE)
    calls: list[Path] = []
    real = paperextract.capture._digest  # pyright: ignore[reportPrivateUsage]

    def changing(path: Path) -> str:
        calls.append(path)
        return "0" * 64 if len(calls) == 3 else real(path)

    monkeypatch.setattr(paperextract.capture, "_digest", changing)
    with pytest.raises(CaptureError, match="changed while it was copied"):
        preserve_capture(page, tmp_path / "kept")
    monkeypatch.undo()
    record = preserve_capture(page, tmp_path / "later")
    (tmp_path / "later" / "page.html").write_text("<html>edited</html>")
    with pytest.raises(CaptureError, match="changed since"):
        read_capture_html(tmp_path / "later", record)


def test_character_sets_come_from_the_page(tmp_path: Path) -> None:
    latin = tmp_path / "latin.html"
    latin.write_bytes(
        b'<html><head><meta charset="iso-8859-1"></head><p>na\xefve</p></html>'
    )
    assert "naïve" in read_page_html(latin)
    unknown = tmp_path / "unknown.html"
    unknown.write_bytes(b'<html><meta charset="klingon"><p>ok \xff</p></html>')
    assert "ok �" in read_page_html(unknown)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"schema": "other"}, "Expected"),
        ({"files": []}, "no files"),
        ({"kind": "pdf"}, "Unknown capture kind"),
    ],
)
def test_capture_records_are_validated(
    tmp_path: Path, change: dict[str, object], message: str
) -> None:
    page = tmp_path / "page.html"
    page.write_text(PAGE)
    record = preserve_capture(page, tmp_path / "kept")
    data = {**record.to_dict(), **change}
    with pytest.raises(ValueError, match=message):
        CaptureRecord.from_dict(data)
    assert (
        json.loads((tmp_path / "kept" / CAPTURE_RECORD).read_text())["kind"] == "html"
    )
