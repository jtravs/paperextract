"""Opt-in real MinerU worker run on a locally supplied PDF.

Run from the repository root with the pinned worker environment installed:

    PAPEREXTRACT_SMOKE_PDF=/path/to/paper.pdf \\
    uv run --offline --no-sync pytest -m backend tests/test_mineru_smoke.py

Optional variables: PAPEREXTRACT_SMOKE_PAGES="1,15" selects pages;
PAPEREXTRACT_SMOKE_OUTPUT names a private directory that keeps the staging
output instead of pytest's temporary directory.
"""

import os
from pathlib import Path

import pytest

from paperextract.ingest import preserve_pdf
from paperextract.pdf import inspect_pdf
from paperextract.protocol import ExtractionRequest, MineruProfile
from paperextract.worker import WorkerEnvironment, run_worker

pytestmark = pytest.mark.backend

REPOSITORY = Path(__file__).resolve().parents[1]


def test_real_worker_extracts_a_supplied_pdf(tmp_path: Path) -> None:
    supplied = os.environ.get("PAPEREXTRACT_SMOKE_PDF")
    if not supplied:
        pytest.skip("PAPEREXTRACT_SMOKE_PDF is not set")
    environment = WorkerEnvironment.for_repository(REPOSITORY)
    if not environment.python.exists():
        pytest.skip("workers/mineru environment is not installed")
    output_root = Path(os.environ.get("PAPEREXTRACT_SMOKE_OUTPUT", tmp_path))
    output_root.mkdir(parents=True, exist_ok=True)
    staging = output_root / "staging"
    staging.mkdir()
    source = preserve_pdf(Path(supplied), output_root / "source.pdf")
    inspection = inspect_pdf(source.stored_path)
    selection = os.environ.get("PAPEREXTRACT_SMOKE_PAGES")
    pages = None if not selection else tuple(int(p) for p in selection.split(","))
    request = ExtractionRequest(
        request_id="smoke",
        source_path=source.stored_path,
        source_sha256=source.sha256,
        expected_page_count=inspection.page_count,
        pages=pages,
        profile=MineruProfile(),
        model_dir=REPOSITORY / "model-cache" / "mineru" / "models",
        output_dir=staging,
    )
    result = run_worker(request, environment, timeout_seconds=1800)
    assert result.status == "completed", result.failure
    assert result.coverage is not None
    assert result.coverage.missing == ()
    assert result.timing["parse_seconds"] > 0
