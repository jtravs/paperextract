"""Inspect PDF page structure independently of extraction backends."""

# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false
# pypdfium2 is untyped; it is used here only to build synthetic fixtures.

import hashlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from paperextract.pdf import (
    RenderedRegion,
    UnreadablePdfError,
    drop_cap_letter,
    fingerprint_pdf,
    identifier_candidates,
    inspect_pdf,
    normalize_text,
    render_region_png,
)

# A page without its own MediaBox, in a file without a cross-reference table
# so that PDFium has to reconstruct the object table.
MINIMAL_PDF = b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R >> endobj
trailer << /Root 1 0 R >>
%%EOF
"""


def make_pdf(path: Path, sizes: list[tuple[float, float]]) -> None:
    document = pdfium.PdfDocument.new()
    for width, height in sizes:
        document.new_page(width, height)
    document.save(path)
    document.close()


def test_inspection_reports_every_page_in_order(tmp_path: Path) -> None:
    path = tmp_path / "pages.pdf"
    make_pdf(path, [(595.0, 842.0), (842.0, 595.0)])
    inspection = inspect_pdf(path)
    assert inspection.path == path
    assert inspection.page_count == 2
    assert inspection.pdf_version == 17
    assert [page.number for page in inspection.pages] == [1, 2]
    assert [(page.width_pt, page.height_pt) for page in inspection.pages] == [
        (595.0, 842.0),
        (842.0, 595.0),
    ]
    assert {page.rotation for page in inspection.pages} == {0}
    assert inspection.pages[0].mediabox == (0.0, 0.0, 595.0, 842.0)


def test_inspection_reports_missing_mediabox_as_none(tmp_path: Path) -> None:
    path = tmp_path / "minimal.pdf"
    path.write_bytes(MINIMAL_PDF)
    inspection = inspect_pdf(path)
    assert inspection.page_count == 1
    assert inspection.pages[0].mediabox is None
    assert (inspection.pages[0].width_pt, inspection.pages[0].height_pt) == (
        612.0,
        792.0,
    )


@pytest.mark.parametrize("payload", [b"", b"%PDF-1.4 but nothing else", b"html"])
def test_inspection_rejects_unreadable_files(tmp_path: Path, payload: bytes) -> None:
    path = tmp_path / "broken.pdf"
    path.write_bytes(payload)
    with pytest.raises(UnreadablePdfError, match=r"broken\.pdf"):
        inspect_pdf(path)


def test_inspection_requires_an_existing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        inspect_pdf(tmp_path / "absent.pdf")


def test_fingerprint_digests_pages_and_collects_identifier_candidates(
    tmp_path: Path, pdf_builder: Callable[..., bytes]
) -> None:
    path = tmp_path / "paper.pdf"
    path.write_bytes(
        pdf_builder(
            [
                "See doi:10.1000/abc.123 and arXiv:2206.01062v1 "
                "or 10.1103/PhysRevA.85.043820.",
                None,
                "Late DOI 10.9999/late.page",
            ],
            title="Sample   Title",
            subject="doi:10.1000/XYZ, also 10.1000/xyz",
        )
    )
    fingerprint = fingerprint_pdf(path)
    assert fingerprint.page_count == 3
    assert [page.number for page in fingerprint.pages] == [1, 2, 3]
    assert fingerprint.pages[0].characters > 0
    assert fingerprint.pages[1].characters == 0
    assert fingerprint.pages[1].sha256 == hashlib.sha256(b"").hexdigest()
    assert fingerprint.information["Title"] == "Sample   Title"
    assert fingerprint.doi_candidates == (
        "10.1000/XYZ",
        "10.1000/abc.123",
        "10.1103/PhysRevA.85.043820",
    )
    assert fingerprint.arxiv_candidates == ("2206.01062v1",)
    document = fingerprint.to_dict()
    assert document["page_count"] == 3
    assert document["doi_candidates"] == list(fingerprint.doi_candidates)
    wider = fingerprint_pdf(path, identifier_pages=3)
    assert "10.9999/late.page" in wider.doi_candidates
    assert wider.text_sha256 == fingerprint.text_sha256


def test_fingerprint_depends_on_text_not_bytes(
    tmp_path: Path, pdf_builder: Callable[..., bytes]
) -> None:
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(pdf_builder(["Same words"], title="one"))
    second.write_bytes(pdf_builder(["Same  words"], title="two"))
    assert first.read_bytes() != second.read_bytes()
    assert fingerprint_pdf(first).text_sha256 == fingerprint_pdf(second).text_sha256
    assert fingerprint_pdf(first).pages == fingerprint_pdf(second).pages
    third = tmp_path / "third.pdf"
    third.write_bytes(pdf_builder(["Other words"]))
    assert fingerprint_pdf(third).text_sha256 != fingerprint_pdf(first).text_sha256


def test_fingerprint_rejects_unreadable_files(tmp_path: Path) -> None:
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.4 but nothing else")
    with pytest.raises(UnreadablePdfError):
        fingerprint_pdf(path)


def test_normalize_text_collapses_whitespace_without_folding_symbols() -> None:
    assert normalize_text("  aµm \n\t 10⁻¹ Å ") == ("aµm 10⁻¹ Å")


@pytest.mark.parametrize(
    ("text", "dois", "arxiv"),
    [
        ("plain prose", (), ()),
        ("doi:10.1038/s41566-019-0416-4.", ("10.1038/s41566-019-0416-4",), ()),
        ("(10.1000/a) and (10.1000/A);", ("10.1000/a",), ()),
        ('href="https://doi.org/10.1000/b"', ("10.1000/b",), ()),
        (
            "arXiv:1234.56789 arxiv: 1234.56789v2 arXiv:1234.56789",
            (),
            ("1234.56789", "1234.56789v2"),
        ),
    ],
)
def test_identifier_candidates_are_conservative(
    text: str, dois: tuple[str, ...], arxiv: tuple[str, ...]
) -> None:
    assert identifier_candidates(text) == (dois, arxiv)


def test_region_rendering_produces_png_with_margin_and_clamping(
    tmp_path: Path, pdf_builder: Callable[..., bytes]
) -> None:
    path = tmp_path / "paper.pdf"
    path.write_bytes(pdf_builder(["Region text"]))
    rendered = render_region_png(
        path, 1, (10.0, 20.0, 110.0, 70.0), dpi=72, margin_pt=5.0
    )
    assert rendered.png[:8] == b"\x89PNG\r\n\x1a\n"
    assert rendered.region_pt == (5.0, 15.0, 115.0, 75.0)
    assert (rendered.width_px, rendered.height_px) == (110, 60)
    assert rendered.dpi == 72
    clamped = render_region_png(
        path, 1, (290.0, 190.0, 400.0, 400.0), dpi=144, margin_pt=20.0
    )
    assert clamped.region_pt == (270.0, 170.0, 300.0, 200.0)
    assert (clamped.width_px, clamped.height_px) == (60, 60)
    reversed_box = render_region_png(
        path, 1, (110.0, 70.0, 10.0, 20.0), dpi=72, margin_pt=0.0
    )
    assert reversed_box.region_pt == (10.0, 20.0, 110.0, 70.0)
    with pytest.raises(ValueError, match="outside"):
        render_region_png(path, 2, (0.0, 0.0, 10.0, 10.0))
    with pytest.raises(ValueError, match="empty"):
        render_region_png(path, 1, (300.0, 0.0, 400.0, 10.0), margin_pt=0.0)


# A 40 pt drop capital "H" at the top left, then 12 pt text in a 300x200 page.
DROP_CAP_PAGE = (
    b"BT /F1 40 Tf 20 150 Td (H) Tj ET "
    b"BT /F1 12 Tf 52 170 Td (ollow capillary fibres) Tj ET "
    b"BT /F1 12 Tf 20 60 Td (Xollow small) Tj ET"
)


@pytest.mark.parametrize(
    ("region", "fragment", "letter"),
    [
        ((20.0, 20.0, 280.0, 120.0), "ollow capillary", "H"),
        ((20.0, 20.0, 280.0, 120.0), "ollow small", None),
        ((200.0, 20.0, 280.0, 120.0), "ollow capillary", None),
        ((20.0, 120.0, 280.0, 190.0), "ollow capillary", None),
        ((20.0, 20.0, 280.0, 120.0), "absent", None),
        ((20.0, 20.0, 280.0, 120.0), "llow capillary", None),
    ],
)
def test_drop_caps_are_read_from_the_text_layer(
    tmp_path: Path,
    pdf_builder: Callable[..., bytes],
    region: tuple[float, float, float, float],
    fragment: str,
    letter: str | None,
) -> None:
    pdf = tmp_path / "dropcap.pdf"
    pdf.write_bytes(pdf_builder([DROP_CAP_PAGE]))
    assert drop_cap_letter(pdf, 1, region, fragment) == letter


def test_drop_cap_lookup_rejects_pages_outside_the_document(
    tmp_path: Path, pdf_builder: Callable[..., bytes]
) -> None:
    pdf = tmp_path / "dropcap.pdf"
    pdf.write_bytes(pdf_builder([DROP_CAP_PAGE]))
    with pytest.raises(ValueError, match="outside"):
        drop_cap_letter(pdf, 2, (0.0, 0.0, 1.0, 1.0), "ollow")


def test_concurrent_renders_are_serialized(
    tmp_path: Path, pdf_builder: Callable[..., bytes]
) -> None:
    # Regression: two threads rendering at once crashed PDFium, which is
    # not thread-safe, when several papers were described in parallel.
    path = tmp_path / "paper.pdf"
    path.write_bytes(pdf_builder(["one", "two", "three", "four"]))
    box = (0.0, 0.0, 200.0, 200.0)

    def render(page: int) -> RenderedRegion:
        return render_region_png(path, page % 4 + 1, box, dpi=72)

    with ThreadPoolExecutor(max_workers=8) as pool:
        images = list(pool.map(render, range(64)))
    assert len(images) == 64
    assert all(image.png.startswith(b"\x89PNG") for image in images)
