"""Re-extract tables that lost glyphs and keep OCR only when numbers agree."""

import json
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from paperextract.document import (
    Document,
    Finding,
    InlineRun,
    Paragraph,
    SourceSpan,
    Table,
    TableBody,
    TableCell,
)
from paperextract.layout import flag_unmapped_glyphs
from paperextract.pipeline import (
    OCR_WORKER_DIRECTORY,
    ExtractionSettings,
    extract_pdf,
    rebuild_document,
)
from paperextract.protocol import MineruProfile
from paperextract.table_ocr import apply_ocr_tables, ocr_candidate_pages, table_numbers
from paperextract.worker import WorkerEnvironment

BOX = (0.1, 0.2, 0.9, 0.4)


def span(page: int, box: tuple[float, float, float, float] | None = BOX) -> SourceSpan:
    return SourceSpan(page, None, box, "table", 0)


def table(
    name: str,
    cells: list[str],
    page: int = 2,
    box: tuple[float, float, float, float] | None = BOX,
    label: str = "1",
) -> Table:
    return Table(
        id=name,
        label=label,
        caption=None,
        footnotes=(),
        html="<table>" + "".join(f"<tr><td>{c}</td></tr>" for c in cells) + "</table>",
        cells=tuple(TableCell(i, 0, 1, 1, c, c) for i, c in enumerate(cells)),
        rows=len(cells),
        columns=1,
        asset=None,
        span=span(page, box),
    )


def doc(*blocks: object, findings: tuple[Finding, ...] = ()) -> Document:
    return Document("a" * 64, "mineru", "4.0.5", "s", "1", (), blocks, (), findings)  # type: ignore[arg-type]


def test_candidate_pages_are_native_tables_with_unmapped_glyphs() -> None:
    broken = table("t1", ["F-1\x05E\x06"], page=2)
    clean = table("t2", ["1.0"], page=3)
    html_only = Table(
        "t3", None, None, (), "<td>a\x01</td>", None, None, None, None, span(5)
    )
    chosen = TableBody("native", "x", None, None, None)
    done = Table(
        "t4",
        None,
        None,
        (),
        "<td>\x01</td>",
        None,
        None,
        None,
        None,
        span(7),
        False,
        "ocr",
        (chosen,),
    )
    paragraph = Paragraph("p", "body", (InlineRun("text", "\x05"),), span(9))
    assert ocr_candidate_pages(doc(broken, clean, html_only, done, paragraph)) == (2, 5)


def test_numbers_come_from_cells_or_html_without_signs() -> None:
    assert table_numbers(table("t", ["F-1 e 2", "10-63, 1.853"])) == Counter(
        {"1": 1, "2": 1, "10": 1, "63": 1, "1.853": 1}
    )
    body = TableBody("ocr", "<td>1,000</td><td>x<sup>-2</sup></td>", None, None, None)
    assert table_numbers(body) == Counter({"1,000": 1, "2": 1})


def unmapped(name: str) -> Finding:
    return Finding("UNMAPPED_GLYPHS", "warning", "glyphs", (name,), 2)


def test_matching_numbers_select_the_ocr_body() -> None:
    native = table("t1", ["F-1\x05e\x06", "j2F-1"])
    ocr = table(
        "o1",
        ["$\\mathcal{F}^{-1}[e]$", "|x|^{2}\\mathcal{F}^{-1}"],
        box=(0.12, 0.2, 0.9, 0.41),
    )
    far = table("o2", ["1", "2"], box=(0.1, 0.6, 0.9, 0.8))
    prose = Paragraph("p", "body", (InlineRun("text", "Table 1"),), span(2))
    result = apply_ocr_tables(
        doc(native, findings=(unmapped("t1"), Finding("OTHER", "info", "kept"))),
        doc(prose, far, ocr),
    )
    (chosen,) = result.blocks
    assert isinstance(chosen, Table)
    assert chosen.body_source == "ocr"
    assert chosen.cells == ocr.cells
    assert chosen.id == "t1"
    assert chosen.alternatives == (
        TableBody("native", native.html, native.cells, native.rows, native.columns),
    )
    assert [f.code for f in result.findings] == ["OTHER", "TABLE_OCR_SELECTED"]
    assert "all 3 numbers agree" in result.findings[1].message
    assert flag_unmapped_glyphs(result).findings == result.findings


def test_ocr_body_with_glyph_gaps_keeps_the_glyph_finding() -> None:
    native = table("t1", ["a\x05 1"])
    ocr = table("o1", ["b\x06 1"])
    result = apply_ocr_tables(doc(native, findings=(unmapped("t1"),)), doc(ocr))
    assert [f.code for f in result.findings] == [
        "UNMAPPED_GLYPHS",
        "TABLE_OCR_SELECTED",
    ]


def test_differing_numbers_keep_the_text_layer() -> None:
    native = table("t1", ["1.853\x05", "2.487"])
    ocr = table("o1", ["1.853", "2.481"])
    result = apply_ocr_tables(doc(native, findings=(unmapped("t1"),)), doc(ocr))
    (kept,) = result.blocks
    assert isinstance(kept, Table)
    assert kept.body_source == "native"
    assert kept.cells == native.cells
    assert kept.alternatives == (TableBody("ocr", ocr.html, ocr.cells, 2, 1),)
    rejected = result.findings[-1]
    assert rejected.code == "TABLE_OCR_REJECTED"
    assert "['2.487']" in rejected.message and "['2.481']" in rejected.message
    assert [f.code for f in result.findings] == [
        "UNMAPPED_GLYPHS",
        "TABLE_OCR_REJECTED",
    ]


def test_unmatched_tables_are_reported_and_others_untouched() -> None:
    native = table("t1", ["1\x05"])
    no_box = table("t2", ["2\x05"], box=None, label="2")
    clean = table("t3", ["3"])
    ocr_other_page = table("o1", ["1"], page=4)
    ocr_no_box = table("o2", ["2"], box=None, label="9")
    result = apply_ocr_tables(
        doc(native, no_box, clean), doc(ocr_other_page, ocr_no_box)
    )
    assert result.blocks == (native, no_box, clean)
    assert [f.code for f in result.findings] == ["TABLE_OCR_UNMATCHED"] * 2


def test_tables_without_boxes_match_a_unique_label() -> None:
    native = table("t1", ["2\x05"], box=None, label="S1")
    same = table("o1", ["2"], box=None, label="S1")
    twin = table("o2", ["2"], box=None, label="S1")
    matched = apply_ocr_tables(doc(native), doc(same))
    assert [f.code for f in matched.findings] == ["TABLE_OCR_SELECTED"]
    ambiguous = apply_ocr_tables(doc(native), doc(same, twin))
    assert [f.code for f in ambiguous.findings] == ["TABLE_OCR_UNMATCHED"]
    unlabelled = Table(
        "t9", None, None, (), "<td>2\x05</td>", None, None, None, None, span(2, None)
    )
    assert [f.code for f in apply_ocr_tables(doc(unlabelled), doc(same)).findings] == [
        "TABLE_OCR_UNMATCHED"
    ]


# A stand-in worker whose answer depends on the parse mode: the text layer
# loses the brackets of a formula, OCR returns LaTeX with the same numbers.
MODE_WORKER = """
import hashlib, json, sys
from pathlib import Path

BEHAVIOUR = __BEHAVIOUR__
request = json.loads(Path(sys.argv[1]).read_text())
out = Path.cwd()
mode = request["profile"]["parse_mode"]
if mode == "ocr" and BEHAVIOUR == "ocr_crash":
    sys.exit(3)
if mode == "auto":
    cell = "F<sup>-1</sup>\\u0005E\\u0006"
elif BEHAVIOUR == "ocr_misread":
    cell = "<eq>\\\\mathcal{F}^{-7}[E]</eq>"
else:
    cell = "<eq>\\\\mathcal{F}^{-1}[E]</eq>"
caption = [{"type": "text", "content": "Table 1. Operators"}]
body = f"<table><tr><td>{cell}</td></tr></table>"
table = {
    "type": "table",
    "index": 0,
    "bbox": [0.1, 0.2, 0.9, 0.4],
    "content": [
        {"type": "table_caption", "index": 0, "content": caption},
        {"type": "table_body", "index": 0, "content": body},
    ],
}
pages = request["pages"] or list(range(1, request["expected_page_count"] + 1))
native = {
    "schema": "docvortex.middle",
    "schema_version": "2.0",
    "pages": [
        {"page_idx": p - 1, "blocks": [table] if p == 1 else []} for p in pages
    ],
}
data = json.dumps(native).encode()
(out / "native").mkdir()
(out / "native" / "middle_json.json").write_bytes(data)
failed = mode == "ocr" and BEHAVIOUR == "ocr_failed"
digest = hashlib.sha256(data).hexdigest()
coverage = {
    "requested": pages,
    "returned": pages,
    "empty": [p for p in pages if p != 1],
}
failure = {"kind": "RuntimeError", "message": "ocr boom", "traceback": ""}
result = {
    "schema": "paperextract.worker-result",
    "protocol_version": 1,
    "request_id": request["request_id"],
    "backend": "mineru",
    "backend_version": "4.0.5",
    "status": "failed" if failed else "completed",
    "source_sha256": request["source_sha256"],
    "page_count": None if failed else request["expected_page_count"],
    "coverage": None if failed else coverage,
    "files": [
        {"path": "native/middle_json.json", "sha256": digest, "size_bytes": len(data)}
    ],
    "configuration": {},
    "environment": {},
    "timing": {},
    "resources": {},
    "diagnostics": [],
    "failure": failure if failed else None,
}
(out / "result.json").write_text(json.dumps(result))
sys.exit(1 if failed else 0)
"""


def settings_for(
    tmp_path: Path, behaviour: str, table_ocr: bool = True
) -> ExtractionSettings:
    script = tmp_path / f"mode_worker_{behaviour}.py"
    script.write_text(MODE_WORKER.replace("__BEHAVIOUR__", repr(behaviour)))
    return ExtractionSettings(
        environment=WorkerEnvironment(Path(sys.executable), script),
        model_dir=tmp_path / "models",
        profile=MineruProfile(cpu_threads=1),
        timeout_seconds=60,
        table_ocr=table_ocr,
    )


@pytest.fixture
def pdf(tmp_path: Path, pdf_builder: Callable[..., bytes]) -> Path:
    path = tmp_path / "paper.pdf"
    path.write_bytes(pdf_builder(["Operators", "Second"]))
    return path


def run(
    tmp_path: Path, pdf: Path, behaviour: str, table_ocr: bool = True
) -> tuple[Document, Path, object]:
    staging = tmp_path / f"staging-{behaviour}-{table_ocr}"
    staging.mkdir()
    outcome = extract_pdf(
        pdf, staging, settings_for(tmp_path, behaviour, table_ocr), request_id="r"
    )
    assert outcome.document is not None
    return outcome.document, staging, outcome.ocr_result


def test_the_pipeline_reextracts_only_affected_pages(tmp_path: Path, pdf: Path) -> None:
    document, staging, ocr_result = run(tmp_path, pdf, "ok")
    (chosen,) = [b for b in document.blocks if isinstance(b, Table)]
    assert chosen.body_source == "ocr"
    assert (
        chosen.cells is not None
        and chosen.cells[0].html == "<eq>\\mathcal{F}^{-1}[E]</eq>"
    )
    request = json.loads((staging / OCR_WORKER_DIRECTORY / "request.json").read_text())
    assert request["pages"] == [1]
    assert request["profile"]["parse_mode"] == "ocr"
    assert request["request_id"] == "r-ocr"
    assert ocr_result is not None
    codes = [f.code for f in document.findings]
    assert "TABLE_OCR_SELECTED" in codes and "UNMAPPED_GLYPHS" not in codes
    saved = Document.from_json((staging / "document.json").read_text())
    assert saved == document


@pytest.mark.parametrize(
    ("behaviour", "code", "message"),
    [
        ("ocr_misread", "TABLE_OCR_REJECTED", "only in OCR: ['7']"),
        ("ocr_failed", "TABLE_OCR_FAILED", "(worker failed)"),
        ("ocr_crash", "TABLE_OCR_FAILED", "WorkerProcessError"),
    ],
)
def test_ocr_problems_keep_the_text_layer(
    tmp_path: Path, pdf: Path, behaviour: str, code: str, message: str
) -> None:
    document, _staging, _ocr = run(tmp_path, pdf, behaviour)
    (kept,) = [b for b in document.blocks if isinstance(b, Table)]
    assert kept.body_source == "native"
    finding = next(f for f in document.findings if f.code == code)
    assert message in finding.message
    assert "UNMAPPED_GLYPHS" in [f.code for f in document.findings]


def test_table_ocr_can_be_switched_off(tmp_path: Path, pdf: Path) -> None:
    document, staging, ocr_result = run(tmp_path, pdf, "ok", table_ocr=False)
    assert ocr_result is None
    assert not (staging / OCR_WORKER_DIRECTORY).exists()
    assert "UNMAPPED_GLYPHS" in [f.code for f in document.findings]


def test_rebuilding_reapplies_kept_ocr_and_reports_missing_ocr(
    tmp_path: Path, pdf: Path
) -> None:
    document, staging, _ocr = run(tmp_path, pdf, "ok")
    assert rebuild_document(staging) == document
    plain, staging_off, _ = run(tmp_path, pdf, "ok", table_ocr=False)
    rebuilt = rebuild_document(staging_off)
    assert rebuilt.findings[:-1] == plain.findings
    assert rebuilt.findings[-1].code == "TABLE_OCR_NOT_RUN"
    assert "pages [1]" in rebuilt.findings[-1].message
    failed, staging_failed, _ = run(tmp_path, pdf, "ocr_failed")
    assert "TABLE_OCR_NOT_RUN" in [
        f.code for f in rebuild_document(staging_failed).findings
    ]
    assert failed.blocks == rebuild_document(staging_failed).blocks


def test_lost_glyphs_in_table_notes_keep_their_finding() -> None:
    native = replace(
        table("t1", ["a\x05 1"]), footnotes=((InlineRun("text", "b\x05 note"),),)
    )
    ocr = table("o1", ["a 1"])
    result = apply_ocr_tables(doc(native, findings=(unmapped("t1"),)), doc(ocr))
    assert "UNMAPPED_GLYPHS" in [f.code for f in result.findings]
