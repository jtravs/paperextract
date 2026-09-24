"""Inspect and fingerprint PDF files independently of any extraction backend."""

# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false
# pypdfium2 ships no type information. This module is the only place that calls
# it directly, and every value crossing out of it is converted to an explicit
# Python type below, so the unknown-type diagnostics are confined here.

from __future__ import annotations

import functools
import hashlib
import re
import struct
import threading
import unicodedata
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

__all__ = [
    "ContentFingerprint",
    "PageGeometry",
    "PageText",
    "PdfInspection",
    "RenderedRegion",
    "UnreadablePdfError",
    "drop_cap_letter",
    "fingerprint_pdf",
    "identifier_candidates",
    "inspect_pdf",
    "normalize_text",
    "ocr_text_layer",
    "page_text",
    "region_text",
    "render_region_png",
]

_DOI = re.compile(r"10\.\d{4,9}/[^\s\"<>]+")
# New-style (2206.01062v2) and old-style (hep-th/9901001v1) identifiers.
_ARXIV = re.compile(
    r"arXiv:\s*((?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?)",
    re.IGNORECASE,
)
_TRAILING_PUNCTUATION = ".,;:)]}'\""
PAGE_SEPARATOR = "\x0c"
_POINTS_PER_INCH = 72.0
_RGB_CHANNELS = 3
# PDFium is not thread-safe: concurrent renders crashed the process when
# several papers were described at once. Every public function that calls it
# holds this lock.
_PDFIUM_LOCK = threading.RLock()


def _serialized[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    """Run a function while holding the process-wide PDFium lock.

    Parameters
    ----------
    function : Callable
        Function that calls PDFium.

    Returns
    -------
    Callable
        The same function, serialized with every other PDFium call.
    """

    @functools.wraps(function)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        """Call the function under the lock.

        Parameters
        ----------
        *args : object
            Positional arguments.
        **kwargs : object
            Keyword arguments.

        Returns
        -------
        object
            The function's result.
        """
        with _PDFIUM_LOCK:
            return function(*args, **kwargs)

    return wrapper


class UnreadablePdfError(ValueError):
    """Report a file that PDFium cannot open as a PDF document."""


@dataclass(frozen=True)
class PageGeometry:
    """Describe one page as PDFium reports it, before any extraction.

    Attributes
    ----------
    number : int
        One-based page number.
    width_pt : float
        Displayed page width in points, after the page rotation is applied.
    height_pt : float
        Displayed page height in points, after the page rotation is applied.
    rotation : int
        Clockwise page rotation in degrees stored in the page dictionary.
    mediabox : tuple of float or None
        Raw MediaBox ``(x0, y0, x1, y1)`` in unrotated PDF units, or None when
        the page does not define one directly.
    """

    number: int
    width_pt: float
    height_pt: float
    rotation: int
    mediabox: tuple[float, float, float, float] | None


@dataclass(frozen=True)
class PdfInspection:
    """Summarize the page structure of one readable PDF.

    Attributes
    ----------
    path : Path
        Inspected file, as supplied by the caller.
    pdf_version : int or None
        Header version times ten, for example 17 for PDF 1.7; None if unknown.
    page_count : int
        Number of pages PDFium can enumerate; this is not a content check.
    pages : tuple of PageGeometry
        Geometry for every page, in document order.
    """

    path: Path
    pdf_version: int | None
    page_count: int
    pages: tuple[PageGeometry, ...]


@dataclass(frozen=True)
class PageText:
    """Digest the text layer of one page.

    Attributes
    ----------
    number : int
        One-based page number.
    characters : int
        Length of the normalized page text; zero for a page without a text layer.
    sha256 : str
        Digest of the normalized page text encoded as UTF-8.
    """

    number: int
    characters: int
    sha256: str


@dataclass(frozen=True)
class ContentFingerprint:
    """Summarize a PDF's text content for duplicate detection.

    Attributes
    ----------
    page_count : int
        Number of pages.
    pages : tuple of PageText
        Per-page text digests in document order.
    text_sha256 : str
        Digest of all normalized page texts joined by form feeds.
    information : Mapping of str to str
        Non-empty entries of the PDF information dictionary, such as Title.
    doi_candidates : tuple of str
        DOI-shaped strings found in the information dictionary and the first
        pages, in order of appearance; candidates, not validated identity.
    arxiv_candidates : tuple of str
        arXiv identifiers found in the same places, in order of appearance.
    """

    page_count: int
    pages: tuple[PageText, ...]
    text_sha256: str
    information: Mapping[str, str]
    doi_candidates: tuple[str, ...]
    arxiv_candidates: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Serialize the fingerprint to JSON-compatible fields.

        Returns
        -------
        dict of str to object
            Fingerprint fields with pages as objects.
        """
        return {
            "page_count": self.page_count,
            "pages": [
                {
                    "number": page.number,
                    "characters": page.characters,
                    "sha256": page.sha256,
                }
                for page in self.pages
            ],
            "text_sha256": self.text_sha256,
            "information": dict(self.information),
            "doi_candidates": list(self.doi_candidates),
            "arxiv_candidates": list(self.arxiv_candidates),
        }


def normalize_text(text: str) -> str:
    """Apply the conservative normalization used for text digests.

    Parameters
    ----------
    text : str
        Extracted page text.

    Returns
    -------
    str
        NFC-normalized text with runs of whitespace collapsed to single spaces
        and no leading or trailing whitespace. Characters are never dropped or
        folded, so scientific symbols survive unchanged.
    """
    return " ".join(unicodedata.normalize("NFC", text).split())


def identifier_candidates(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Find DOI- and arXiv-shaped strings in text.

    Parameters
    ----------
    text : str
        Any text, such as page content or an information dictionary value.

    Returns
    -------
    tuple
        DOI candidates and arXiv candidates, each in order of first appearance
        with trailing punctuation removed and case-insensitive duplicates dropped.
        A match is a candidate for the identity stage, not a validated identifier.
    """
    dois: list[str] = []
    seen: set[str] = set()
    for match in _DOI.finditer(text):
        candidate = match.group(0).rstrip(_TRAILING_PUNCTUATION)
        if candidate.lower() not in seen:
            seen.add(candidate.lower())
            dois.append(candidate)
    arxiv: list[str] = []
    for match in _ARXIV.finditer(text):
        if match.group(1) not in arxiv:
            arxiv.append(match.group(1))
    return tuple(dois), tuple(arxiv)


def _open(path: Path) -> pdfium.PdfDocument:
    """Open a PDF with PDFium, translating load failures.

    Parameters
    ----------
    path : Path
        Existing PDF file.

    Returns
    -------
    pypdfium2.PdfDocument
        Open document; the caller closes it.
    """
    try:
        return pdfium.PdfDocument(path)
    except pdfium.PdfiumError as exc:
        raise UnreadablePdfError(f"PDFium cannot open {path.name}: {exc}") from exc


def _mediabox(page: pdfium.PdfPage) -> tuple[float, float, float, float] | None:
    """Read a page's own MediaBox without PDFium's Letter-size fallback.

    Parameters
    ----------
    page : pypdfium2.PdfPage
        Loaded page handle.

    Returns
    -------
    tuple of float or None
        Explicit ``(x0, y0, x1, y1)`` box, or None when the page lacks one.
    """
    box = page.get_mediabox(fallback_ok=False)
    if box is None:
        return None
    x0, y0, x1, y1 = box
    return (float(x0), float(y0), float(x1), float(y1))


@_serialized
def inspect_pdf(path: Path) -> PdfInspection:
    """Count pages and record page geometry with PDFium.

    Parameters
    ----------
    path : Path
        Existing PDF file. The file is opened read-only and never modified.

    Returns
    -------
    PdfInspection
        Page count, version and per-page geometry.

    Raises
    ------
    FileNotFoundError
        The path is not an existing file.
    UnreadablePdfError
        PDFium cannot parse the file, including encrypted files without a
        password and files with a damaged structure.

    Notes
    -----
    A successful inspection means the page tree is readable. It does not
    establish that pages contain text, are unrotated or are complete; the
    extraction worker performs its own checks, and the coordinator compares
    this independent page count with the worker's reported coverage.
    """
    with _open(path) as document:
        page_count = len(document)
        geometries: list[PageGeometry] = []
        for index in range(page_count):
            page = document[index]
            width, height = page.get_size()
            geometry = PageGeometry(
                number=index + 1,
                width_pt=float(width),
                height_pt=float(height),
                rotation=int(page.get_rotation()),
                mediabox=_mediabox(page),
            )
            page.close()
            geometries.append(geometry)
        version = document.get_version()
        return PdfInspection(
            path=path,
            pdf_version=None if version is None else int(version),
            page_count=page_count,
            pages=tuple(geometries),
        )


@_serialized
def fingerprint_pdf(path: Path, *, identifier_pages: int = 2) -> ContentFingerprint:
    """Digest the text layer page by page and collect identifier candidates.

    Parameters
    ----------
    path : Path
        Existing PDF file, opened read-only.
    identifier_pages : int
        Number of leading pages searched for DOI and arXiv strings.

    Returns
    -------
    ContentFingerprint
        Per-page and whole-document text digests, information dictionary and
        identifier candidates.

    Raises
    ------
    FileNotFoundError
        The path is not an existing file.
    UnreadablePdfError
        PDFium cannot parse the file.

    Notes
    -----
    Two files with equal page counts and equal page digests carry the same
    text layer; this is the content-equivalence signal for re-downloaded PDFs
    that differ only in bytes. Download stamps that change between copies are
    not removed here, so such copies still differ and need a later, explicit
    stamp-aware comparison. A scan without a text layer digests as empty pages,
    which is recorded, not hidden.
    """
    with _open(path) as document:
        information = {
            str(key): str(value)
            for key, value in document.get_metadata_dict(skip_empty=True).items()
        }
        pages: list[PageText] = []
        texts: list[str] = []
        identifier_text: list[str] = list(information.values())
        for index in range(len(document)):
            page = document[index]
            textpage = page.get_textpage()
            text = normalize_text(str(textpage.get_text_range()))
            textpage.close()
            page.close()
            texts.append(text)
            if index < identifier_pages:
                identifier_text.append(text)
            pages.append(
                PageText(
                    number=index + 1,
                    characters=len(text),
                    sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                )
            )
    dois, arxiv = identifier_candidates("\n".join(identifier_text))
    return ContentFingerprint(
        page_count=len(pages),
        pages=tuple(pages),
        text_sha256=hashlib.sha256(
            PAGE_SEPARATOR.join(texts).encode("utf-8")
        ).hexdigest(),
        information=information,
        doi_candidates=dois,
        arxiv_candidates=arxiv,
    )


@dataclass(frozen=True)
class RenderedRegion:
    """Hold a rasterized page region.

    Attributes
    ----------
    png : bytes
        Complete PNG file contents, 8-bit RGB without alpha.
    width_px : int
        Bitmap width in pixels.
    height_px : int
        Bitmap height in pixels.
    dpi : int
        Requested resolution in dots per inch.
    region_pt : tuple of float
        Rendered ``(x0, y0, x1, y1)`` in points with a top-left origin, after
        the margin was added and the box was clamped to the page.
    """

    png: bytes
    width_px: int
    height_px: int
    dpi: int
    region_pt: tuple[float, float, float, float]


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    """Encode one PNG chunk.

    Parameters
    ----------
    tag : bytes
        Four-letter chunk type.
    payload : bytes
        Chunk data.

    Returns
    -------
    bytes
        Length, type, data and CRC.
    """
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def _encode_png(width: int, height: int, stride: int, buffer: memoryview) -> bytes:
    """Encode a packed RGB bitmap as PNG using the standard library.

    Parameters
    ----------
    width : int
        Bitmap width in pixels.
    height : int
        Bitmap height in pixels.
    stride : int
        Bytes per bitmap row, which may exceed three times the width.
    buffer : memoryview
        Raw RGB pixel data.

    Returns
    -------
    bytes
        PNG file contents.
    """
    row_bytes = width * _RGB_CHANNELS
    rows = b"".join(
        b"\x00" + bytes(buffer[y * stride : y * stride + row_bytes])
        for y in range(height)
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(rows, 6))
        + _png_chunk(b"IEND", b"")
    )


@_serialized
def render_region_png(
    path: Path,
    page_number: int,
    region_pt: tuple[float, float, float, float],
    *,
    dpi: int = 300,
    margin_pt: float = 6.0,
) -> RenderedRegion:
    """Rasterize a page region from the preserved PDF at a given resolution.

    Parameters
    ----------
    path : Path
        Readable PDF.
    page_number : int
        One-based page.
    region_pt : tuple of float
        ``(x0, y0, x1, y1)`` in points with a top-left origin, as recorded in
        canonical source spans.
    dpi : int
        Output resolution; 300 keeps small axis labels legible.
    margin_pt : float
        Border added around the region so labels at its edge survive.

    Returns
    -------
    RenderedRegion
        PNG bytes and the exact region rendered.

    Raises
    ------
    FileNotFoundError
        The path is not an existing file.
    UnreadablePdfError
        PDFium cannot parse the file.
    ValueError
        The page number is out of range or the region is empty after clamping.

    Notes
    -----
    This produces a raster preview of the original vector or raster content.
    It does not extract native image objects or vector paths; the preserved
    PDF and the region remain the authoritative figure evidence.
    """
    with _open(path) as document:
        if not 1 <= page_number <= len(document):
            raise ValueError(f"Page {page_number} is outside 1..{len(document)}")
        page = document[page_number - 1]
        width, height = (float(v) for v in page.get_size())
        x0 = max(0.0, min(region_pt[0], region_pt[2]) - margin_pt)
        y0 = max(0.0, min(region_pt[1], region_pt[3]) - margin_pt)
        x1 = min(width, max(region_pt[0], region_pt[2]) + margin_pt)
        y1 = min(height, max(region_pt[1], region_pt[3]) + margin_pt)
        if x1 <= x0 or y1 <= y0:
            page.close()
            raise ValueError(f"Region {region_pt} is empty on page {page_number}")
        bitmap = page.render(
            # pypdfium2 is untyped; pyright infers `int` from the default `1`,
            # while the documented parameter is a float scale factor.
            scale=dpi / _POINTS_PER_INCH,  # pyright: ignore[reportArgumentType]
            crop=(x0, height - y1, width - x1, y0),
            rev_byteorder=True,
            force_bitmap_format=pdfium_c.FPDFBitmap_BGR,
        )
        png = _encode_png(
            int(bitmap.width),
            int(bitmap.height),
            int(bitmap.stride),
            memoryview(bitmap.buffer),
        )
        rendered = RenderedRegion(
            png=png,
            width_px=int(bitmap.width),
            height_px=int(bitmap.height),
            dpi=dpi,
            region_pt=(x0, y0, x1, y1),
        )
        bitmap.close()
        page.close()
        return rendered


# A drop capital is at least twice as tall as the text that follows it and
# sits at the paragraph's top-left corner, within this distance of it.
_DROP_CAP_HEIGHT_RATIO = 2.0
_DROP_CAP_REACH_PT = 36.0


@_serialized
def drop_cap_letter(
    path: Path,
    page_number: int,
    region_pt: tuple[float, float, float, float],
    fragment: str,
) -> str | None:
    """Read the drop capital printed before a word fragment from the text layer.

    Parameters
    ----------
    path : Path
        Readable PDF.
    page_number : int
        One-based page.
    region_pt : tuple of float
        Paragraph box ``(x0, y0, x1, y1)`` in points with a top-left origin.
    fragment : str
        Text that the backend returned without its first letter.

    Returns
    -------
    str or None
        The capital letter when the text layer has one before the fragment,
        separated at most by white space, at least twice as tall as the
        fragment's first glyph and at the paragraph's top-left corner;
        otherwise None.

    Raises
    ------
    UnreadablePdfError
        PDFium cannot parse the file.
    ValueError
        The page number is out of range.
    """
    with _open(path) as document:
        if not 1 <= page_number <= len(document):
            raise ValueError(f"Page {page_number} is outside 1..{len(document)}")
        page = document[page_number - 1]
        height = float(page.get_height())
        textpage = page.get_textpage()
        text = str(textpage.get_text_range())
        found: str | None = None
        start = text.find(fragment, 1)
        while start > 0 and found is None:
            # The capital may sit on its own text line before the fragment.
            index = start - 1
            while index > 0 and text[index] in " \r\n":
                index -= 1
            letter = text[index]
            if letter.isalpha() and letter.isupper():
                left, bottom, _right, top = textpage.get_charbox(index)
                _, next_bottom, _, next_top = textpage.get_charbox(start)
                ratio = (top - bottom) / max(next_top - next_bottom, 1e-6)
                tall = ratio >= _DROP_CAP_HEIGHT_RATIO
                near_left = abs(left - region_pt[0]) <= _DROP_CAP_REACH_PT
                top_down = height - top
                near_top = (
                    region_pt[1] - _DROP_CAP_REACH_PT
                    <= top_down
                    <= region_pt[1] + _DROP_CAP_REACH_PT
                )
                if tall and near_left and near_top:
                    found = letter
            start = text.find(fragment, start + 1)
        textpage.close()
        page.close()
    return found


@_serialized
def page_text(path: Path, page_number: int = 1) -> str:
    """Read the normalized text layer of one page.

    Parameters
    ----------
    path : Path
        Readable PDF.
    page_number : int
        One-based page.

    Returns
    -------
    str
        Text after :func:`normalize_text`; empty for a page without a text
        layer or a page beyond the end of the document.

    Raises
    ------
    UnreadablePdfError
        PDFium cannot parse the file.
    """
    with _open(path) as document:
        if not 1 <= page_number <= len(document):
            return ""
        page = document[page_number - 1]
        textpage = page.get_textpage()
        text = normalize_text(str(textpage.get_text_range()))
        textpage.close()
        page.close()
    return text


@_serialized
def region_text(
    path: Path, page_number: int, region_pt: tuple[float, float, float, float]
) -> str:
    """Read the text layer inside a rectangle of one page.

    Parameters
    ----------
    path : Path
        Readable PDF.
    page_number : int
        One-based page.
    region_pt : tuple of float
        ``(x0, y0, x1, y1)`` in points with a top-left origin.

    Returns
    -------
    str
        Normalized text of the characters inside the region; empty for a
        region without a text layer or a page beyond the document.

    Raises
    ------
    UnreadablePdfError
        PDFium cannot parse the file.
    """
    with _open(path) as document:
        if not 1 <= page_number <= len(document):
            return ""
        page = document[page_number - 1]
        height = float(page.get_height())
        textpage = page.get_textpage()
        text = str(
            textpage.get_text_bounded(
                left=region_pt[0],
                bottom=height - region_pt[3],
                right=region_pt[2],
                top=height - region_pt[1],
            )
        )
        textpage.close()
        page.close()
    return normalize_text(text)


@_serialized
def ocr_text_layer(path: Path, page_number: int) -> bool:
    """Tell whether a page's text layer is invisible text over a scan.

    OCR tools lay the recognized text over the page image in the invisible
    render mode, so readers can search and copy it without seeing it.

    Parameters
    ----------
    path : Path
        Readable PDF.
    page_number : int
        One-based page.

    Returns
    -------
    bool
        True when most of the page's text objects are invisible; False for a
        page without text or beyond the document.

    Raises
    ------
    UnreadablePdfError
        PDFium cannot parse the file.
    """
    with _open(path) as document:
        if not 1 <= page_number <= len(document):
            return False
        page = document[page_number - 1]
        modes = [
            pdfium_c.FPDFTextObj_GetTextRenderMode(obj.raw)
            for obj in page.get_objects(
                filter=(pdfium_c.FPDF_PAGEOBJ_TEXT,), max_depth=8
            )
        ]
        page.close()
    invisible = sum(mode == pdfium_c.FPDF_TEXTRENDERMODE_INVISIBLE for mode in modes)
    return 2 * invisible > len(modes)
