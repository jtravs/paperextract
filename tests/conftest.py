"""Shared synthetic fixtures for offline tests."""

import hashlib
import io
import json
import tarfile
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from paperextract.document import Document
from paperextract.models import current_platform
from paperextract.normalize import normalize_mineru
from paperextract.protocol import PageCoverage
from paperextract.registry import Author, RegistryFailure, RegistryRecord


def _pdf_string(text: str) -> bytes:
    """Escape text for a PDF literal string in Latin-1."""
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return escaped.encode("latin-1")


def build_text_pdf(
    pages: Sequence[str | bytes | None],
    *,
    title: str | None = None,
    subject: str | None = None,
) -> bytes:
    """Build a small PDF whose pages carry Helvetica text or nothing at all.

    The file has no cross-reference table, so PDFium reconstructs the object
    table; that keeps the builder short while exercising a tolerant reader.
    A bytes entry is used verbatim as the page's content stream.
    """
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # page tree, filled in below
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    info = b"<< "
    if title is not None:
        info += b"/Title (" + _pdf_string(title) + b") "
    if subject is not None:
        info += b"/Subject (" + _pdf_string(subject) + b") "
    info += b">>"
    objects.append(info)
    page_numbers: list[int] = []
    for text in pages:
        if text is None:
            objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] >>")
            page_numbers.append(len(objects))
            continue
        content = (
            text
            if isinstance(text, bytes)
            else b"BT /F1 12 Tf 20 100 Td (" + _pdf_string(text) + b") Tj ET"
        )
        objects.append(
            b"<< /Length "
            + str(len(content)).encode()
            + b" >>\nstream\n"
            + content
            + b"\nendstream"
        )
        stream_number = len(objects)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents "
            + str(stream_number).encode()
            + b" 0 R >>"
        )
        page_numbers.append(len(objects))
    kids = b" ".join(f"{number} 0 R".encode() for number in page_numbers)
    objects[1] = (
        b"<< /Type /Pages /Kids ["
        + kids
        + b"] /Count "
        + str(len(pages)).encode()
        + b" >>"
    )
    out = b"%PDF-1.4\n"
    for number, body in enumerate(objects, 1):
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    out += b"trailer\n<< /Root 1 0 R /Info 4 0 R >>\n%%EOF\n"
    return out


@pytest.fixture
def pdf_builder() -> Callable[..., bytes]:
    return build_text_pdf


# --- Synthetic MinerU native output covering every block type ---------------

NATIVE_SHA = "b" * 64
NATIVE_ASSETS = {
    "native/middle_json.json",
    "native/images/page_0_equation_3.jpg",
    "native/images/p1.jpg",
}


def text(content: str, *styles: str) -> dict[str, object]:
    span: dict[str, object] = {"type": "text", "content": content}
    if styles:
        span["styles"] = list(styles)
    return span


def block(
    kind: str, index: int, bbox: list[object] | None, **fields: object
) -> dict[str, object]:
    result: dict[str, object] = {"type": kind, "index": index}
    if bbox is not None:
        result["bbox"] = bbox
    result.update(fields)
    return result


def caption(kind: str, index: int, content: str) -> dict[str, object]:
    return block(
        f"{kind}_caption", index, [0.1, 0.5, 0.4, 0.55], content=[text(content)]
    )


def visual(
    kind: str, index: int, image: str | None, *children: dict[str, object]
) -> dict[str, object]:
    body = block(f"{kind}_body", index, [0.1, 0.1, 0.4, 0.4], content="")
    if image is not None:
        body["image_path"] = image
    return block(kind, index, [0.1, 0.1, 0.4, 0.4], content=[body, *children])


def page_zero() -> dict[str, object]:
    return {
        "page_idx": 0,
        "blocks": [
            block("header", 0, [0.1, 0.02, 0.5, 0.05], content=[text("Journal Name")]),
            block(
                "doc_title",
                1,
                [0.1, 0.1, 0.9, 0.2],
                content=[text("Synthetic Paper")],
                level=1,
            ),
            block(
                "text",
                2,
                [0.1, 0.25, 0.9, 0.4],
                content=[
                    text("Energy ", "bold"),
                    {"type": "equation_inline", "content": "E = h\\nu"},
                    {
                        "type": "hyperlink",
                        "url": "https://doi.org/10.1000/x",
                        "content": [
                            text("doi link"),
                            {"type": "equation_inline", "content": "y"},
                            "junk",
                        ],
                    },
                    {"type": "code_inline", "content": "x"},
                    {"type": "mystery", "content": "odd"},
                    {"type": "weird"},
                    42,
                ],
            ),
            block(
                "equation",
                3,
                [0.2, 0.42, 0.8, 0.48],
                content="a^{2} + b^{2} = c^{2}\\tag{1}",
                image_path="images/page_0_equation_3.jpg",
            ),
            block(
                "equation",
                4,
                [0.2, 0.5, 0.8, 0.55],
                content="\\frac{a}{b",
                image_path="images/nothere.jpg",
            ),
            block(
                "paragraph_title", 5, [0.1, 0.56, 0.5, 0.6], content=[text("Results")]
            ),
            block(
                "list",
                6,
                [0.1, 0.6, 0.9, 0.7],
                content=[
                    block("text", 7, None, content=[text("first item")]),
                    block(
                        "list",
                        8,
                        None,
                        content=[block("text", 9, None, content=[text("nested item")])],
                    ),
                    block("ref_text", 10, None, content=[text("third item")]),
                    "junk",
                ],
            ),
            visual(
                "image",
                11,
                "images/p1.jpg",
                caption("image", 12, "Fig. 1 | Single figure."),
                block(
                    "image_footnote",
                    13,
                    [0.1, 0.56, 0.4, 0.58],
                    content=[text("Footnote.")],
                ),
            ),
            block(
                "ref_text",
                14,
                [0.1, 0.72, 0.9, 0.8],
                content=[text("[1] A reference.")],
                continues_prev=True,
            ),
            block(
                "page_footnote",
                15,
                [0.1, 0.82, 0.9, 0.85],
                content=[text("* A footnote.")],
            ),
            block("aside_text", 16, [0.92, 0.3, 0.98, 0.7], content="Plain aside"),
            block("code", 17, [0.1, 0.86, 0.9, 0.9], content=[], sub_type="code"),
            block("footer", 18, [0.1, 0.95, 0.5, 0.97], content=[text("Footer")]),
            block("page_number", 19, [0.9, 0.95, 0.95, 0.97], content=[text("1")]),
        ],
    }


def page_one() -> dict[str, object]:
    ragged = "<table><tr><td>x</td><td>y</td></tr><tr><td>z</td></tr></table>"
    good = (
        '<table><tr><td rowspan="2">A</td>'
        '<td colspan="2">Energy<sup>a</sup> &micro;J</td></tr>'
        "<tr><td>1.8</td><td>0.5</td></tr></table>"
    )
    return {
        "page_idx": 1,
        "blocks": [
            visual("chart", 0, "images/c0.jpg", caption("chart", 1, "a")),
            visual("chart", 2, "images/c2.jpg"),
            visual(
                "chart",
                3,
                "images/c3.jpg",
                caption("chart", 4, "Fig. 2. Two panels and more."),
                caption("chart", 5, "Some stray caption"),
                block(
                    "chart_footnote",
                    6,
                    [0.1, 0.6, 0.4, 0.62],
                    content=[text("Chart note")],
                ),
            ),
            block("chart", 7, None, content=[caption("chart", 8, "(b)")]),
            block(
                "text",
                9,
                [0.1, 0.65, 0.9, 0.7],
                content=[text("Prose between visual runs.")],
            ),
            block(
                "table",
                10,
                [0.1, 0.7, 0.9, 0.8],
                content=[
                    block(
                        "table_body",
                        10,
                        [0.1, 0.7, 0.9, 0.8],
                        content=good,
                        image_path="images/t10.jpg",
                    ),
                    block("table_footnote", 11, None, content=[text("a Estimated.")]),
                    block(
                        "table_footnote",
                        12,
                        None,
                        content=[text("TABLE S1. Parameters.")],
                    ),
                    "junk",
                ],
            ),
            block(
                "table",
                13,
                [0.1, 0.81, 0.9, 0.85],
                content=[
                    block("table_caption", 14, None, content=[text("Table 2. Ragged")]),
                    block("table_caption", 15, None, content=[text("Second caption")]),
                    block("table_body", 13, [0.1, 0.81, 0.9, 0.85], content=ragged),
                ],
                continues_prev=True,
            ),
            block(
                "table",
                16,
                [0.1, 0.86, 0.9, 0.88],
                content=[block("table_caption", 17, None, content=[text("No body")])],
            ),
            block(
                "table",
                18,
                [0.1, 0.89, 0.9, 0.9],
                content=[
                    block(
                        "table_body", 18, [0.1, 0.89, 0.9, 0.9], content="no cells here"
                    )
                ],
            ),
            block(
                "paragraph_title",
                19,
                [0.1, 0.91, 0.5, 0.92],
                content=[text("Untitled level")],
            ),
            block(
                "doc_title", 20, [0.1, 0.93, 0.5, 0.94], content=[text("Second title")]
            ),
            block("text", 21, [0.1, "a", 0.2, 0.3], content=[text("Bad box")]),
            5,
            block("equation", 22, [0.1, 0.95, 0.9, 0.96], content=""),
            block(
                "table",
                25,
                [0.1, 0.96, 0.9, 0.97],
                content=[
                    block("table_caption", 26, None, content=[text("Parameters only")]),
                    block(
                        "table_footnote",
                        27,
                        None,
                        content=[text("Table 3. Labeled note")],
                    ),
                    block(
                        "table_body",
                        25,
                        [0.1, 0.96, 0.9, 0.97],
                        content="<table><tr><td>1</td></tr></table>",
                    ),
                ],
            ),
            visual(
                "chart",
                23,
                "images/c23.jpg",
                caption("chart", 24, "Fig. 3 | Trailing."),
            ),
        ],
    }


def native_document() -> dict[str, object]:
    return {
        "schema": "docvortex.middle",
        "schema_version": "2.0",
        "is_full_document": True,
        "metadata": {
            "file_suffix": "pdf",
            "producer": {"name": "mineru", "version": "4.0.5"},
            "document": {
                "title": "Synthetic Paper",
                "subject": "",
                "authors": ["A. Author", 7],
                "identifiers": ["doi:10.1000/x"],
                "keywords": [],
                "page_count": 4,
            },
        },
        "extensions": {
            "docvortex_layout": {
                "pages": [
                    {"page_idx": 0, "width_pt": 600.0, "height_pt": 800.0},
                    {"page_idx": "x", "width_pt": 1.0, "height_pt": 1.0},
                    "junk",
                ]
            }
        },
        "pages": [
            page_zero(),
            page_one(),
            {"page_idx": 2, "blocks": []},
            {"page_idx": "bad"},
            "junk",
        ],
    }


NATIVE_COVERAGE = PageCoverage(requested=(1, 2, 3, 4), returned=(1, 2, 3), empty=(3,))
NATIVE_PAGE_SIZES = {3: (500.0, 700.0)}


@pytest.fixture
def native_builder() -> Callable[[], dict[str, object]]:
    return native_document


@pytest.fixture
def normalized_document() -> Document:
    return normalize_mineru(
        native_document(),
        source_sha256=NATIVE_SHA,
        coverage=NATIVE_COVERAGE,
        backend_version="4.0.5",
        assets=NATIVE_ASSETS,
        page_sizes=NATIVE_PAGE_SIZES,
    )


# A stand-in worker that emits the shared synthetic native document plus crops.
NATIVE_WORKER = """
import hashlib
import json
import re
import sys
from pathlib import Path

NATIVE = Path(__NATIVE__)
BEHAVIOUR = __BEHAVIOUR__
if sys.argv[1] == "--serve":
    # Serve by running this script once per request, replying as a real
    # serving worker does on the original standard output.
    import os
    import subprocess

    replies = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)
    for line in sys.stdin:
        path = Path(line.strip())
        code = subprocess.run([sys.executable, __file__, str(path)], cwd=path.parent)
        replies.write(json.dumps({"request": str(path), "status": code.returncode}))
        replies.write("\\n")
        replies.flush()
    sys.exit(0)
request = json.loads(Path(sys.argv[1]).read_text())
out = Path.cwd()
if BEHAVIOUR == "crash":
    sys.exit(3)


def record(relative, data):
    target = out / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    return {"path": relative, "sha256": digest, "size_bytes": len(data)}


files = [
    record("native/middle_json.json", NATIVE.read_bytes()),
    record("native/markdown.md", b"# md"),
    record("native/images/page_0_equation_3.jpg", b"eq"),
    record("native/images/p1.jpg", b"p1"),
    record("native/images/c0.jpg", b"c0"),
    record("native/images/t10.jpg", b"t10"),
]
count = request["expected_page_count"]
result = {
    "schema": "paperextract.worker-result",
    "protocol_version": 1,
    "request_id": request["request_id"],
    "backend": "mineru",
    "backend_version": "4.0.5",
    "status": "completed",
    "source_sha256": request["source_sha256"],
    "page_count": count,
    "coverage": {
        "requested": list(range(1, count + 1)),
        "returned": [1, 2, 3],
        "empty": [3],
    },
    "files": files,
    "configuration": {},
    "environment": {},
    "timing": {"parse_seconds": 0.1},
    "resources": {},
    "diagnostics": [],
    "failure": None,
}
failing = BEHAVIOUR == "failed" or (
    BEHAVIOUR == "fail_supplement" and "-s0" in request["request_id"]
)
if failing:
    result.update(
        status="failed",
        page_count=None,
        coverage=None,
        failure={"kind": "RuntimeError", "message": "boom", "traceback": ""},
    )
(out / "result.json").write_text(json.dumps(result))
sys.exit(1 if failing else 0)
"""


@pytest.fixture
def native_worker(tmp_path: Path) -> Callable[[Path, str], Path]:
    """Write stand-in worker scripts that emit the synthetic native document."""
    native = tmp_path / "native.json"
    native.write_text(json.dumps(native_document()))

    def write(script: Path, behaviour: str = "ok") -> Path:
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(
            NATIVE_WORKER.replace("__NATIVE__", repr(str(native))).replace(
                "__BEHAVIOUR__", repr(behaviour)
            )
        )
        return script

    return write


def synthetic_record(doi: str, title: str, family: str, year: int) -> RegistryRecord:
    """Build a registry record for a synthetic paper."""
    return RegistryRecord(
        provider="crossref",
        doi=doi,
        type="journal-article",
        title=title,
        title_raw=title,
        subtitle=None,
        authors=(Author("A.", family, None, None, "first"),),
        container_title="Synthetic Letters",
        publisher="Synthetic Press",
        volume="1",
        issue="2",
        pages="10-20",
        article_number=None,
        issued=(year, 1),
        published_print=(),
        published_online=(),
        url=f"https://doi.org/{doi}",
        issn=(),
        license_urls=(),
        abstract="An abstract about synthetic pulses in Börzsönyi cells.",
        source_url="u",
        retrieved_utc="2026-09-22T00:00:00+00:00",
        body_sha256="0" * 64,
        cached=False,
    )


@pytest.fixture
def synthetic_lookup() -> Callable[[str], RegistryRecord | RegistryFailure]:
    """Return a lookup that validates the synthetic document's DOI 10.1000/x."""

    def lookup(doi: str) -> RegistryRecord | RegistryFailure:
        if doi == "10.1000/x":
            return synthetic_record(doi, "Synthetic Paper", "Author", 2020)
        return RegistryFailure(doi, "not_found", "no record")

    return lookup


# --- Synthetic Docling native output ------------------------------------------


def dl_box(left: float, top: float, right: float, bottom: float) -> dict[str, object]:
    """Box with Docling's bottom-left origin."""
    return {"l": left, "t": top, "r": right, "b": bottom, "coord_origin": "BOTTOMLEFT"}


def dl_text(
    index: int,
    label: str,
    text: str,
    page: int = 1,
    box: dict[str, object] | None = None,
    **fields: object,
) -> dict[str, object]:
    item: dict[str, object] = {
        "self_ref": f"#/texts/{index}",
        "parent": {"$ref": "#/body"},
        "children": [],
        "content_layer": "body",
        "label": label,
        "prov": [{"page_no": page, "bbox": box or dl_box(10, 190, 290, 180)}],
        "orig": text,
        "text": text,
    }
    item.update(fields)
    return item


def dl_cell(
    row: int,
    column: int,
    text: str,
    *,
    rows: int = 1,
    columns: int = 1,
    header: bool = False,
) -> dict[str, object]:
    return {
        "start_row_offset_idx": row,
        "start_col_offset_idx": column,
        "row_span": rows,
        "col_span": columns,
        "text": text,
        "column_header": header,
        "row_header": False,
    }


def docling_document() -> dict[str, object]:
    """A DoclingDocument with every label the normalizer maps, on pages 1-2."""
    texts = [
        dl_text(0, "title", "A Synthetic Title"),
        dl_text(1, "section_header", "Methods", level=1),
        dl_text(
            2,
            "text",
            "Bold prose",
            formatting={"bold": True, "italic": False, "script": "super"},
        ),
        dl_text(3, "text", "A link", hyperlink="https://example.org"),
        dl_text(4, "footnote", "a Footnote."),
        dl_text(5, "code", "x = 1"),
        dl_text(6, "caption", "Orphan caption"),
        dl_text(7, "list_item", "first", marker="-", parent={"$ref": "#/groups/0"}),
        dl_text(8, "list_item", "second", marker="-", parent={"$ref": "#/groups/0"}),
        dl_text(9, "formula", "E = m c ^ { 2 }", orig="E=mc2 (1)"),
        dl_text(10, "caption", "Table 1. Energies", page=2),
        dl_text(11, "footnote", "a Estimated.", page=2),
        dl_text(12, "caption", "Fig. 2. A picture.", page=2),
        dl_text(13, "text", "axis label inside the picture", page=2),
        dl_text(14, "page_header", "Journal header", content_layer="furniture"),
        dl_text(15, "page_footer", "Page 1", content_layer="furniture"),
        dl_text(16, "checkbox_selected", "[x]"),
        dl_text(17, "section_header", "References", level=1),
        dl_text(
            18, "list_item", "Ref one.", marker="1.", parent={"$ref": "#/groups/1"}
        ),
        dl_text(19, "list_item", "Ref two.", marker="", parent={"$ref": "#/groups/1"}),
        dl_text(20, "text", "Glyphs .0133 lost .0134 here"),
        dl_text(21, "handwritten_text", "furniture?", content_layer="furniture"),
        dl_text(22, "text", "", page=2),
        dl_text(23, "text", "inside a group"),
        dl_text(24, "text", "in a list group but not an item"),
    ]
    groups: list[dict[str, object]] = [
        {
            "self_ref": "#/groups/0",
            "children": [{"$ref": "#/texts/7"}, {"$ref": "#/texts/8"}],
            "content_layer": "body",
            "label": "list",
        },
        {
            "self_ref": "#/groups/1",
            "children": [
                {"$ref": "#/texts/18"},
                {"$ref": "#/texts/19"},
                {"$ref": "#/texts/24"},
                {"$ref": "#/texts/99"},
            ],
            "content_layer": "body",
            "label": "list",
        },
        {
            "self_ref": "#/groups/2",
            "children": [{"$ref": "#/texts/23"}],
            "content_layer": "body",
            "label": "key_value_area",
        },
        {
            "self_ref": "#/groups/3",
            "children": [],
            "content_layer": "body",
            "label": "list",
        },
    ]
    tables: list[dict[str, object]] = [
        {
            "self_ref": "#/tables/0",
            "label": "table",
            "content_layer": "body",
            "prov": [{"page_no": 2, "bbox": dl_box(30, 60, 270, 40)}],
            "captions": [{"$ref": "#/texts/10"}],
            "footnotes": [{"$ref": "#/texts/11"}],
            "data": {
                "num_rows": 3,
                "num_cols": 3,
                "table_cells": [
                    dl_cell(0, 0, "A", rows=2),
                    dl_cell(0, 1, "Energy µJ", columns=2, header=True),
                    dl_cell(0, 1, "Energy µJ", columns=2, header=True),
                    dl_cell(1, 1, "1.8"),
                    dl_cell(1, 2, "0.5"),
                    dl_cell(2, 0, "x"),
                    dl_cell(1, 0, "overlap"),
                ],
            },
        },
        {
            "self_ref": "#/tables/1",
            "label": "table",
            "content_layer": "body",
            "prov": [{"page_no": 2, "bbox": dl_box(30, 30, 270, 10)}],
            "captions": [],
            "footnotes": [],
            "data": {"num_rows": 0, "num_cols": 0, "table_cells": []},
        },
        {
            "self_ref": "#/tables/2",
            "label": "table",
            "content_layer": "body",
            "prov": [{"page_no": 2, "bbox": dl_box(0, 199, 5, 195)}],
            "captions": [{"$ref": "#/texts/10"}, {"$ref": "#/texts/99"}],
            "footnotes": [],
            "data": {
                "num_rows": 1,
                "num_cols": 1,
                "table_cells": [dl_cell(0, 0, ".0137 and .0138 codes")],
            },
        },
    ]
    pictures = [
        {
            "self_ref": "#/pictures/0",
            "label": "picture",
            "content_layer": "body",
            "prov": [{"page_no": 2, "bbox": dl_box(20, 180, 280, 100)}],
            "captions": [{"$ref": "#/texts/12"}],
            "footnotes": [{"$ref": "#/texts/11"}],
            "children": [{"$ref": "#/texts/13"}],
        },
        {
            "self_ref": "#/pictures/1",
            "label": "picture",
            "content_layer": "body",
            "prov": [{"page_no": 1, "bbox": dl_box(1, 5, 2, 4)}],
            "captions": [],
            "footnotes": [],
        },
        {
            "self_ref": "#/pictures/2",
            "label": "picture",
            "content_layer": "body",
            "prov": [{"page_no": 1, "bbox": dl_box(1, 5, 2, 4)}],
            "captions": [{"$ref": "#/texts/12"}],
            "footnotes": [],
        },
    ]
    body = [
        "#/texts/0",
        "#/texts/1",
        "#/texts/2",
        "#/texts/3",
        "#/texts/4",
        "#/texts/5",
        "#/texts/6",
        "#/groups/0",
        "#/texts/9",
        "#/texts/16",
        "#/texts/20",
        "#/groups/2",
        "#/groups/3",
        "#/pictures/1",
        "#/pictures/2",
        "#/texts/14",
        "#/texts/7",
        "#/key_value_items/0",
        "#/texts/x",
        "#/tables/0",
        "#/tables/1",
        "#/tables/2",
        "#/pictures/0",
        "#/texts/22",
        "#/texts/17",
        "#/groups/1",
        "#/texts/99",
        "bogus",
    ]
    return {
        "schema_name": "DoclingDocument",
        "version": "1.10.0",
        "body": {"children": [{"$ref": ref} for ref in body]},
        "groups": groups,
        "texts": texts,
        "tables": tables,
        "pictures": pictures,
        "key_value_items": [{"self_ref": "#/key_value_items/0", "label": "form"}],
        "pages": {
            "1": {"size": {"width": 300.0, "height": 200.0}, "page_no": 1},
            "2": {"size": {"width": 300.0, "height": 200.0}, "page_no": 2},
            "x": {"size": {"width": 1.0, "height": 1.0}},
        },
    }


def docling_native(*, formulas: bool = True) -> dict[str, object]:
    """Wrapper with the synthetic document as one run over pages 1-2."""
    return {
        "schema": "paperextract.docling-native",
        "schema_version": 1,
        "backend_version": "2.129.0",
        "pdf_information": {"Title": "Info Title", "Author": " ", "Creator": "TeX"},
        "formulas": formulas,
        "runs": [
            {
                "pages": [1, 2],
                "status": "partial_success",
                "errors": ["page 3 timed out"],
                "images": {
                    "#/pictures/0": "images/r01-picture-000.png",
                    "#/tables/0": "images/r01-table-000.png",
                    "#/pictures/1": "images/r01-picture-001.png",
                    "#/bogus": 3,
                },
                "document": docling_document(),
            },
            "junk",
        ],
    }


DOCLING_ASSETS = {
    "native/docling.json",
    "native/images/r01-picture-000.png",
    "native/images/r01-table-000.png",
}


# A stand-in Docling worker; "check" writes a table matching the MinerU fixture.
DOCLING_WORKER = """
import hashlib
import json
import sys
from pathlib import Path

NATIVE = Path(__NATIVE__)
BEHAVIOUR = __BEHAVIOUR__
request = json.loads(Path(sys.argv[1]).read_text())
out = Path.cwd()
if BEHAVIOUR == "crash":
    sys.exit(3)


def record(relative, data):
    target = out / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    return {"path": relative, "sha256": digest, "size_bytes": len(data)}


pages = request["pages"] or list(range(1, request["expected_page_count"] + 1))
files = [
    record("native/docling.json", NATIVE.read_bytes()),
    record("native/images/r01-picture-000.png", b"pic"),
    record("native/images/r01-table-000.png", b"tab"),
]
result = {
    "schema": "paperextract.worker-result",
    "protocol_version": 1,
    "request_id": request["request_id"],
    "backend": "docling",
    "backend_version": "2.129.0",
    "status": "completed",
    "source_sha256": request["source_sha256"],
    "page_count": request["expected_page_count"],
    "coverage": {"requested": pages, "returned": pages, "empty": []},
    "files": files,
    "configuration": {},
    "environment": {},
    "timing": {"convert_seconds": 0.1},
    "resources": {},
    "diagnostics": [],
    "failure": None,
}
if BEHAVIOUR == "failed":
    result.update(
        status="failed",
        page_count=None,
        coverage=None,
        failure={"kind": "RuntimeError", "message": "boom", "traceback": ""},
    )
(out / "result.json").write_text(json.dumps(result))
sys.exit(1 if BEHAVIOUR == "failed" else 0)
"""


@pytest.fixture
def docling_worker(tmp_path: Path) -> Callable[[Path, str], Path]:
    """Write stand-in Docling worker scripts that emit the synthetic wrapper."""
    native = tmp_path / "docling-native.json"
    native.write_text(json.dumps(docling_native()))

    def write(script: Path, behaviour: str = "ok") -> Path:
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(
            DOCLING_WORKER.replace("__NATIVE__", repr(str(native))).replace(
                "__BEHAVIOUR__", repr(behaviour)
            )
        )
        return script

    return write


# --- Synthetic Marker native output --------------------------------------------


def mk(
    block_type: str, ident: str, html: str = "", **fields: object
) -> dict[str, object]:
    """A Marker JSON block on page 1 with a box in points."""
    block: dict[str, object] = {
        "id": f"/page/0/{block_type}/{ident}",
        "block_type": block_type,
        "html": html,
        "polygon": [],
        "bbox": [10, 10, 100, 20],
    }
    block.update(fields)
    return block


def marker_document() -> dict[str, object]:
    """A Marker document exercising every block type the normalizer maps."""
    table = (
        "<table><tr><th>Gas</th><th>E</th></tr>"
        "<tr><td>He</td><td>1.8</td></tr><tr><td>Ar</td><td>0.5</td></tr></table>"
    )
    page_one = mk(
        "Page",
        "0",
        bbox=[0, 0, 300, 200],
        children=[
            mk("PageHeader", "1", "<p>Journal</p>"),
            mk("SectionHeader", "2", "<h1>A Synthetic Title</h1>"),
            mk("SectionHeader", "3", "<p>No heading tag</p>"),
            mk(
                "Text",
                "4",
                "<p>Energy <math>E=mc^2</math> of <b>bold</b> x<sup>2</sup> "
                '<a href="https://example.org">link</a><br><content-ref src="x">'
                "hidden</content-ref> end</p>",
            ),
            mk("TextInlineMath", "5", "<p>  </p>"),
            mk("Code", "6", "<pre>x = 1</pre>"),
            mk("Footnote", "7", "<p>a Note.</p>"),
            mk("Reference", "8", "<p>[1] A reference.</p>"),
            mk("Caption", "9", "<p>Orphan caption</p>"),
            mk(
                "Equation", "10", '<p><math display="block">a = b \\quad (3)</math></p>'
            ),
            mk("Equation", "11", "<p></p>"),
            mk("Equation", "12", '<p><math display="block">\\frac{a</math></p>'),
            mk(
                "ListGroup",
                "13",
                children=[
                    mk("ListItem", "14", "<li>first</li>"),
                    mk("ListItem", "15", "<li>second</li>"),
                ],
            ),
            mk("ListGroup", "16", "<ul><li>flat</li></ul>", children=[]),
            mk(
                "TableGroup",
                "17",
                children=[
                    mk("Caption", "18", "<p>Table 1. Energies</p>"),
                    mk("Table", "19", table, images={"/page/0/Table/19": "x"}),
                    mk("Caption", "20", "<p>Second caption</p>"),
                    mk("Footnote", "21", "<p>b Estimated.</p>"),
                ],
            ),
            mk(
                "Table",
                "22",
                "<table><tr><td>x</td><td>y</td></tr><tr><td>z</td></tr></table>",
            ),
            mk("Table", "23", "no table here"),
            mk(
                "TableGroup",
                "24",
                children=[mk("Caption", "25", "<p>Table 9. None</p>")],
            ),
            mk(
                "FigureGroup",
                "26",
                children=[
                    mk("Figure", "27", "", images={"/page/0/Figure/27": "x"}),
                    mk("Caption", "28", "<p>Fig. 2. A figure (continued).</p>"),
                ],
            ),
            mk("Picture", "29", "", images={"/page/0/Picture/29": "x"}),
            mk(
                "PictureGroup",
                "30",
                children=[
                    mk("Picture", "31", "", images={"/page/0/Picture/31": "x"}),
                    mk("Picture", "32"),
                    mk("Caption", "33", "<p>Figure 3. Two panels.</p>"),
                ],
            ),
            mk("PictureGroup", "34", children=[mk("Caption", "35", "<p>Nothing</p>")]),
            mk("Form", "36", "<p>form</p>"),
            mk("Picture", "38", "", bbox=[1, 2]),
            mk("Text", "39", "<p>tail <b>b</b> <i> </i></p>"),
            mk("PageFooter", "37", "<p>1</p>"),
        ],
    )
    page_two: dict[str, object] = {
        "id": "/page/1/Page/0",
        "block_type": "Page",
        "html": "",
        "polygon": [],
        "bbox": None,
        "children": [],
    }
    return {
        "block_type": "Document",
        "metadata": {},
        "children": [page_one, page_two, {"id": "odd", "block_type": "Page"}],
    }


def marker_native() -> dict[str, object]:
    """Wrapper around the synthetic Marker document."""
    return {
        "schema": "paperextract.marker-native",
        "schema_version": 1,
        "backend_version": "2.0.0",
        "pdf_information": {"Title": "Info Title", "Author": " "},
        "images": {
            "/page/0/Table/19": "images/page_0_Table_19.jpg",
            "/page/0/Figure/27": "images/page_0_Figure_27.jpg",
            "/page/0/Picture/29": "images/page_0_Picture_29.jpg",
            "/page/0/Picture/31": "images/page_0_Picture_31.jpg",
            "bad": 3,
        },
        "document": marker_document(),
    }


MARKER_ASSETS = {
    "native/marker.json",
    "native/images/page_0_Table_19.jpg",
    "native/images/page_0_Figure_27.jpg",
    "native/images/page_0_Picture_29.jpg",
}


MARKER_WORKER = (
    DOCLING_WORKER.replace('"native/docling.json"', '"native/marker.json"')
    .replace('"backend": "docling"', '"backend": "marker"')
    .replace(
        'record("native/images/r01-picture-000.png", b"pic"),\n'
        '    record("native/images/r01-table-000.png", b"tab"),',
        'record("native/images/page_0_Table_19.jpg", b"tab"),\n'
        '    record("native/images/page_0_Figure_27.jpg", b"fig"),\n'
        '    record("native/images/page_0_Picture_29.jpg", b"pic"),',
    )
)


@pytest.fixture
def marker_worker(tmp_path: Path) -> Callable[[Path, str], Path]:
    """Write stand-in Marker worker scripts that emit the synthetic wrapper."""
    native = tmp_path / "marker-native.json"
    native.write_text(json.dumps(marker_native()))

    def write(script: Path, behaviour: str = "ok") -> Path:
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(
            MARKER_WORKER.replace("__NATIVE__", repr(str(native))).replace(
                "__BEHAVIOUR__", repr(behaviour)
            )
        )
        return script

    return write


# A synthetic model manifest with one snapshot and one binary, for the model
# fetching tests.
BASE = "https://models.example.org/repo/resolve/abc"
FILES = {"config.json": b'{"a": 1}', "weights/model bin": b"weights" * 100}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def archive() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        data = b"#!/bin/sh\n"
        info = tarfile.TarInfo("llama-b1/llama-server")
        info.size = len(data)
        info.mode = 0o755
        bundle.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


ARCHIVE = archive()


def manifest_document(**changes: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema": "paperextract.model-manifest",
        "schema_version": 2,
        "note": "test",
        "sets": {
            "demo": {"description": "Demo set", "models": ["demo"], "binaries": True}
        },
        "models": [
            {
                "name": "demo",
                "backend": "mineru",
                "directory": "Demo",
                "source": {
                    "kind": "huggingface",
                    "repository": "o/demo",
                    "revision": "abc",
                },
                "url": BASE + "/",
                "complete_marker": ".done",
                "license": "apache-2.0",
                "license_status": "model_card_declared",
                "files": [
                    {"path": path, "bytes": len(data), "sha256": sha(data)}
                    for path, data in FILES.items()
                ],
            }
        ],
        "binaries": [
            {
                "name": "llama.cpp b1",
                "platform": current_platform(),
                "url": "https://example.org/llama-b1.tar.gz",
                "sha256": sha(ARCHIVE),
                "directory": "llama.cpp/b1",
                "license": "MIT",
            }
        ],
    }
    document.update(changes)
    return document


def fake_download(
    served: dict[str, bytes], calls: list[str] | None = None
) -> Callable[[str, Path], None]:
    def download(url: str, path: Path) -> None:
        if calls is not None:
            calls.append(url)
        path.write_bytes(served[url])

    return download


def served_files() -> dict[str, bytes]:
    served = {f"{BASE}/config.json": FILES["config.json"]}
    served[f"{BASE}/weights/model%20bin"] = FILES["weights/model bin"]
    served["https://example.org/llama-b1.tar.gz"] = ARCHIVE
    return served
