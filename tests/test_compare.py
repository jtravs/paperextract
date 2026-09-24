"""Compare several backends' readings of one PDF, and run Marker as a backend."""

import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from paperextract.compare import compare_documents, latex_key
from paperextract.document import (
    Document,
    Equation,
    Figure,
    Heading,
    InlineRun,
    ListBlock,
    PageRecord,
    Paragraph,
    SourceSpan,
    Table,
    TableCell,
)
from paperextract.pipeline import ExtractionSettings, extract_pdf
from paperextract.protocol import (
    ExtractionRequest,
    MarkerProfile,
    native_document_path,
)
from paperextract.worker import WorkerEnvironment, worker_environment_variables

TEXT = (
    "Soliton self-compression in gas-filled hollow capillary fibres reaches few-cycle"
)
OTHER = "An entirely different paragraph that the other backend never found at all here"


def span(
    page: int = 1, box: tuple[float, float, float, float] | None = None
) -> SourceSpan:
    return SourceSpan(page, box, box, "text", 0)


def runs(text: str) -> tuple[InlineRun, ...]:
    return (InlineRun("text", text),)


def table(label: str | None, values: tuple[str, ...], box: tuple[float, ...]) -> Table:
    return Table(
        id=f"t{label}{values}",
        label=label,
        caption=None,
        footnotes=(),
        html="",
        cells=tuple(TableCell(0, i, 1, 1, v, v) for i, v in enumerate(values)),
        rows=1,
        columns=len(values),
        asset=None,
        span=span(1, box),  # type: ignore[arg-type]
    )


def figure(label: str, caption: str) -> Figure:
    return Figure(f"f{label}", label, runs(caption), span(), (), (), None, "single", 1)


def document(backend: str, *blocks: object, sha: str = "a" * 64) -> Document:
    return Document(
        source_sha256=sha,
        backend=backend,
        backend_version="1",
        native_schema="x",
        native_schema_version="1",
        pages=(PageRecord(1, 600.0, 800.0, "processed"),),
        blocks=blocks,  # type: ignore[arg-type]
        metadata=(),
        findings=(),
    )


def reference() -> Document:
    return document(
        "mineru",
        Heading("h1", 2, runs("Results"), span()),
        Heading("h2", 2, runs("Methods"), span()),
        Paragraph("p1", "body", (*runs(TEXT), InlineRun("math", "x")), span()),
        Paragraph("p2", "body", runs(OTHER), span()),
        ListBlock("l1", (runs("short item"),), span()),
        figure("1", "Fig. 1. Setup of the compression experiment."),
        figure("2", "Fig. 2. Spectra."),
        table("1", ("1.8", "0.5"), (0.1, 0.1, 0.5, 0.2)),
        table("2", ("3.0",), (0.1, 0.5, 0.5, 0.6)),
        table(None, ("7",), (0.1, 0.8, 0.5, 0.9)),
        Equation("e1", r"a = b\tag{1}", "1", None, span()),
        Equation("e2", "c = d", "2", None, span()),
        Equation("e3", "x", "3", None, span()),
    )


def other() -> Document:
    return document(
        "docling",
        Heading("h1", 2, runs("Results"), span()),
        Heading("h3", 2, runs("Appendix"), span()),
        Paragraph("p1", "body", runs(TEXT), span()),
        figure("1", "Fig. 1. Setup of the compression experiment."),
        figure("2", "Fig. 2. A much longer caption with many more words."),
        figure("4", "Fig. 4. Only here."),
        table("1", ("1.8", "0.5"), (0.1, 0.1, 0.5, 0.2)),
        table("2", ("3.1",), (0.1, 0.5, 0.5, 0.6)),
        table("5", ("9",), (0.6, 0.1, 0.9, 0.2)),
        Equation("e1", r"a=b \quad (1)", "1", None, span()),
        Equation("e2", "c = e", "2", None, span()),
        Equation("e4", "y", "4", None, span()),
    )


def test_readings_are_compared_with_the_reference() -> None:
    report = compare_documents({"mineru": reference(), "docling": other()})
    assert report["reference"] == "mineru"
    summary = report["backends"]["mineru"]  # type: ignore[index]
    assert summary["inline_math"] == 1
    assert summary["labelled_tables"] == ["1", "2"]
    comparison = report["comparisons"]["docling"]  # type: ignore[index]
    assert comparison["reference_text_in_other"] == {
        "compared": 2,
        "absent": 1,
        "examples": [OTHER[:100]],
    }
    assert comparison["other_text_in_reference"]["absent"] == 0
    assert comparison["headings"] == {
        "only_reference": ["Methods"],
        "only_other": ["Appendix"],
    }
    figures = cast("list[dict[str, object]]", comparison["figures"])
    assert [(f["label"], f["status"]) for f in figures] == [
        ("1", "agrees"),
        ("2", "differs"),
        ("4", "only_other"),
    ]
    tables = cast("list[dict[str, object]]", comparison["tables"])
    statuses = [(t["label"], t["status"]) for t in tables]
    assert statuses == [
        ("1", "agrees"),
        ("2", "differs"),
        (None, "only_reference"),
        ("5", "only_other"),
    ]
    assert tables[1]["only_reference"] == ["3.0"]
    assert comparison["equations"] == {
        "counts": [3, 3],
        "only_reference": ["3"],
        "only_other": ["4"],
        "same_latex": ["1"],
        "different_latex": ["2"],
    }


def test_a_comparison_needs_two_readings_of_one_source() -> None:
    with pytest.raises(ValueError, match="at least two"):
        compare_documents({"mineru": reference()})
    with pytest.raises(ValueError, match="different sources"):
        compare_documents(
            {"mineru": reference(), "docling": replace(other(), source_sha256="b" * 64)}
        )
    assert latex_key(r"\left( x \right) \, \text{m}") == latex_key("(x)m")


def test_marker_requests_run_through_the_worker_boundary(
    tmp_path: Path,
    pdf_builder: Callable[..., bytes],
    marker_worker: Callable[[Path, str], Path],
) -> None:
    server = tmp_path / "llama-server"
    profile = MarkerProfile(server_binary=server, cpu_threads=2)
    assert MarkerProfile.from_dict(profile.to_dict()) == profile
    with pytest.raises(ValueError, match="absolute path"):
        MarkerProfile(server_binary=Path("llama-server"))
    assert native_document_path("marker") == "native/marker.json"
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(pdf_builder(["Body", "Two", None, None]))
    staging = tmp_path / "run"
    staging.mkdir()
    settings = ExtractionSettings(
        environment=WorkerEnvironment(
            Path(sys.executable), marker_worker(tmp_path / "marker.py", "ok")
        ),
        model_dir=tmp_path / "models",
        profile=profile,
        timeout_seconds=60,
    )
    outcome = extract_pdf(pdf, staging, settings, request_id="m")
    assert outcome.request.backend == "marker"
    assert outcome.document is not None and outcome.document.backend == "marker"
    request = ExtractionRequest.from_json(
        (staging / "worker" / "request.json").read_text()
    )
    assert request.profile == profile
    variables = worker_environment_variables(request)
    assert variables["MODEL_CACHE_DIR"] == str(tmp_path / "models" / "cache")
    assert variables["HF_HOME"].endswith("marker-home/huggingface")
