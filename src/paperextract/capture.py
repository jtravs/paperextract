"""Preserve saved article web pages and read their main HTML without executing it.

Two browser capture formats are accepted, recognized by content rather than
by file name: a saved HTML page, optionally with the sibling ``<name>_files``
directory that browsers write for "Web Page, Complete", and an MHTML archive
(``multipart/related``). A capture is evidence: its files are copied
byte-for-byte with their digests, and nothing in it is fetched, rendered or
run. Only the main HTML document is decoded for the HTML cross-check.
"""

from __future__ import annotations

import email
import email.policy
import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, cast

from paperextract.fields import dump, integer, items, record, string

__all__ = [
    "CAPTURE_RECORD",
    "CAPTURE_SCHEMA",
    "CAPTURE_VERSION",
    "MAX_CAPTURE_BYTES",
    "CaptureError",
    "CaptureFile",
    "CaptureRecord",
    "capture_kind",
    "preserve_capture",
    "read_capture_html",
    "read_capture_record",
    "read_page_html",
]

CAPTURE_SCHEMA = "paperextract.html-capture"
CAPTURE_VERSION = 1
CAPTURE_RECORD = "capture.json"
# A saved article with its images stays far below this; larger inputs are
# refused rather than copied into every paper directory.
MAX_CAPTURE_BYTES = 512 * 1024 * 1024
_SNIFF_BYTES = 4096
_HTML_START = re.compile(
    rb"^\s*(?:<!--.*?-->\s*)*<(?:!doctype\s+html|html)\b", re.I | re.S
)
_SAVED_FROM = re.compile(r"<!--\s*saved from url=\(\d+\)(\S+?)\s*-->", re.I)
_META_CHARSET = re.compile(rb"<meta[^>]+charset=[\"']?([A-Za-z0-9_-]+)", re.I)

Kind = Literal["html", "mhtml"]


class CaptureError(ValueError):
    """Report a file that is not a usable saved web page."""


@dataclass(frozen=True)
class CaptureFile:
    """Record one preserved file of a capture.

    Attributes
    ----------
    path : str
        POSIX path relative to the capture directory.
    sha256 : str
        Digest of the bytes.
    size_bytes : int
        Length in bytes.
    """

    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class CaptureRecord:
    """Describe a preserved capture.

    Attributes
    ----------
    kind : str
        ``html`` or ``mhtml``.
    main : str
        Path of the main file within the capture directory.
    original_name : str
        File name the user supplied.
    files : tuple of CaptureFile
        Every preserved file, the main file first.
    saved_from : str or None
        Page address recorded by the browser, if any.
    """

    kind: Kind
    main: str
    original_name: str
    files: tuple[CaptureFile, ...]
    saved_from: str | None

    @property
    def sha256(self) -> str:
        """Return the digest of the main file.

        Returns
        -------
        str
            SHA-256 of the main HTML or MHTML file.
        """
        return self.files[0].sha256

    def to_dict(self) -> dict[str, object]:
        """Serialize the record with its schema.

        Returns
        -------
        dict of str to object
            JSON-compatible record.
        """
        return {
            "schema": CAPTURE_SCHEMA,
            "schema_version": CAPTURE_VERSION,
            "kind": self.kind,
            "main": self.main,
            "original_name": self.original_name,
            "saved_from": self.saved_from,
            "files": [
                {"path": f.path, "sha256": f.sha256, "size_bytes": f.size_bytes}
                for f in self.files
            ],
        }

    @classmethod
    def from_dict(cls, value: object) -> CaptureRecord:
        """Parse a serialized record.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        CaptureRecord
            Validated record.

        Raises
        ------
        ValueError
            Another schema or version, or malformed fields.
        """
        data = record(
            value,
            "schema schema_version kind main original_name saved_from files",
        )
        if (
            data["schema"] != CAPTURE_SCHEMA
            or data["schema_version"] != CAPTURE_VERSION
        ):
            raise ValueError(f"Expected {CAPTURE_SCHEMA} {CAPTURE_VERSION}")
        saved = data["saved_from"]
        files = tuple(
            CaptureFile(
                path=string(entry["path"]),
                sha256=string(entry["sha256"]),
                size_bytes=integer(entry["size_bytes"], minimum=0),
            )
            for entry in (
                record(item, "path sha256 size_bytes") for item in items(data["files"])
            )
        )
        if not files:
            raise ValueError("A capture record lists no files")
        kind = string(data["kind"])
        if kind not in ("html", "mhtml"):
            raise ValueError(f"Unknown capture kind {kind!r}")
        return cls(
            kind=kind,
            main=string(data["main"]),
            original_name=string(data["original_name"]),
            files=files,
            saved_from=None if saved is None else string(saved),
        )


def capture_kind(path: Path) -> Kind | None:
    """Recognize a saved web page by its content.

    Parameters
    ----------
    path : Path
        Candidate file.

    Returns
    -------
    str or None
        ``mhtml`` for a MIME archive whose content type is
        ``multipart/related``, ``html`` for an HTML document, otherwise None.
    """
    try:
        with path.open("rb") as handle:
            head = handle.read(_SNIFF_BYTES)
    except OSError:
        return None
    header = head.split(b"\r\n\r\n", 1)[0].split(b"\n\n", 1)[0].lower()
    if b"mime-version:" in header and b"multipart/related" in header:
        return "mhtml"
    if _HTML_START.match(head.lstrip(b"\xef\xbb\xbf")):
        return "html"
    return None


def _digest(path: Path) -> str:
    """Hash a file.

    Parameters
    ----------
    path : Path
        Regular file.

    Returns
    -------
    str
        SHA-256 hexadecimal digest.
    """
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _members(path: Path, kind: Kind) -> list[tuple[Path, str]]:
    """List the files of a capture with their relative destinations.

    Parameters
    ----------
    path : Path
        Main file.
    kind : str
        Capture kind.

    Returns
    -------
    list of tuple
        Source file and POSIX destination, the main file first.

    Raises
    ------
    CaptureError
        The asset directory holds a symbolic link.
    """
    members = [(path, path.name)]
    assets = path.with_name(f"{path.stem}_files")
    if kind == "html" and assets.is_dir():
        for item in sorted(assets.rglob("*")):
            if item.is_symlink():
                raise CaptureError(f"Refusing a symbolic link in the capture: {item}")
            if item.is_file():
                relative = item.relative_to(path.parent).as_posix()
                members.append((item, relative))
    return members


def preserve_capture(path: Path, directory: Path) -> CaptureRecord:
    """Copy a saved web page and its assets into an empty directory.

    Parameters
    ----------
    path : Path
        Saved HTML page or MHTML archive; never modified.
    directory : Path
        New directory that receives the copies, laid out as saved, and
        ``capture.json``.

    Returns
    -------
    CaptureRecord
        What was preserved.

    Raises
    ------
    CaptureError
        The file is not a saved web page, is too large, contains a symbolic
        link, or changed while it was copied.
    FileExistsError
        The directory already exists.
    """
    kind = capture_kind(path)
    if kind is None:
        raise CaptureError(f"{path} is neither a saved HTML page nor an MHTML archive")
    members = _members(path, kind)
    total = sum(source.stat().st_size for source, _relative in members)
    if total > MAX_CAPTURE_BYTES:
        raise CaptureError(f"{path}: capture of {total} bytes exceeds the limit")
    directory.mkdir(parents=True)
    files: list[CaptureFile] = []
    for source, relative in members:
        before = _digest(source)
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        after = _digest(target)
        if after != before or _digest(source) != before:
            raise CaptureError(f"{source} changed while it was copied")
        files.append(CaptureFile(relative, after, target.stat().st_size))
    main = directory / path.name
    saved_from = _saved_from(main, kind)
    capture = CaptureRecord(kind, path.name, path.name, tuple(files), saved_from)
    (directory / CAPTURE_RECORD).write_text(dump(capture.to_dict()))
    return capture


def read_capture_record(directory: Path) -> CaptureRecord:
    """Read the record of a preserved capture.

    Parameters
    ----------
    directory : Path
        Capture directory written by :func:`preserve_capture`.

    Returns
    -------
    CaptureRecord
        Parsed record.
    """
    return CaptureRecord.from_dict(json.loads((directory / CAPTURE_RECORD).read_text()))


def _decode(data: bytes, charset: str | None) -> str:
    """Decode HTML bytes with a declared or sniffed character set.

    Parameters
    ----------
    data : bytes
        HTML bytes.
    charset : str or None
        Charset declared by a MIME header.

    Returns
    -------
    str
        Text; undecodable bytes become replacement characters.
    """
    if charset is None:
        match = _META_CHARSET.search(data[:_SNIFF_BYTES])
        declared = match.group(1).decode("ascii") if match else "utf-8"
    else:
        declared = charset
    try:
        return data.decode(declared, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def _mhtml_main(path: Path) -> tuple[str, str | None]:
    """Decode the main HTML part of an MHTML archive.

    Parameters
    ----------
    path : Path
        MHTML file.

    Returns
    -------
    tuple
        HTML text and the snapshot location.

    Raises
    ------
    CaptureError
        The archive holds no HTML part.
    """
    with path.open("rb") as handle:
        message = email.message_from_binary_file(handle, policy=email.policy.default)
    location = message["Snapshot-Content-Location"]
    parts = [
        part
        for part in message.walk()
        if part.get_content_type() == "text/html" and not part.is_multipart()
    ]
    main = next(
        (p for p in parts if location and p["Content-Location"] == location), None
    )
    if main is None:
        main = parts[0] if parts else None
    if main is None:
        raise CaptureError(f"{path}: the archive has no HTML part")
    payload = cast("bytes", main.get_payload(decode=True))
    return _decode(
        payload, main.get_content_charset()
    ), None if location is None else str(location)


def _saved_from(path: Path, kind: Kind) -> str | None:
    """Read the address a browser recorded for the saved page.

    Parameters
    ----------
    path : Path
        Main file.
    kind : str
        Capture kind.

    Returns
    -------
    str or None
        Address, or None when none was recorded.
    """
    if kind == "mhtml":
        return _mhtml_main(path)[1]
    head = path.read_bytes()[:_SNIFF_BYTES].decode("utf-8", errors="replace")
    match = _SAVED_FROM.search(head)
    return match.group(1) if match else None


def read_page_html(path: Path) -> str:
    """Decode the main HTML of a saved web page that is not yet preserved.

    Parameters
    ----------
    path : Path
        Saved HTML page or MHTML archive.

    Returns
    -------
    str
        Main HTML document as text.

    Raises
    ------
    CaptureError
        The file is not a saved web page.
    """
    kind = capture_kind(path)
    if kind is None:
        raise CaptureError(f"{path} is neither a saved HTML page nor an MHTML archive")
    if kind == "mhtml":
        return _mhtml_main(path)[0]
    return _decode(path.read_bytes(), None)


def read_capture_html(directory: Path, capture: CaptureRecord) -> str:
    """Decode the main HTML of a preserved capture after checking its digest.

    Parameters
    ----------
    directory : Path
        Capture directory.
    capture : CaptureRecord
        Its record.

    Returns
    -------
    str
        Main HTML document as text.

    Raises
    ------
    CaptureError
        The main file changed since it was preserved.
    """
    main = directory / PurePosixPath(capture.main)
    if _digest(main) != capture.sha256:
        raise CaptureError(f"{main} changed since it was preserved")
    if capture.kind == "mhtml":
        return _mhtml_main(main)[0]
    return _decode(main.read_bytes(), None)
