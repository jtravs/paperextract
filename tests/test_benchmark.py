"""Exercise scientific distinctions and fail-closed benchmark validation."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from paperextract.benchmark import Observation, Reference, compare, load_benchmark


def _manifest(
    root: Path,
) -> tuple[Path, dict[str, object], dict[str, object], dict[str, object]]:
    content = b"synthetic source; no third-party paper content"
    (root / "source.pdf").write_bytes(content)
    source: dict[str, object] = {
        "id": "accepted-pdf",
        "work_id": "example",
        "version_id": "accepted",
        "path": "source.pdf",
        "sha256": hashlib.sha256(content).hexdigest(),
        "pages": 2,
        "split": "development",
    }
    reference: dict[str, object] = {
        "id": "cell-1",
        "source_id": source["id"],
        "version_id": source["version_id"],
        "source_sha256": source["sha256"],
        "page": 2,
        "locator": "table:S1/row:1/column:2",
        "kind": "table_cell",
        "expected": "2.0",
        "header_path": ["Energy", "μJ"],
        "review": "candidate",
        "provenance": "Synthetic fixture authored 2026-09-22; no paper evidence.",
    }
    record: dict[str, object] = {
        "schema": "paperextract.benchmark",
        "schema_version": "1",
        "sources": [source],
        "references": [reference],
    }
    return root / "benchmark.json", record, source, reference


def _save(path: Path, record: object) -> None:
    path.write_text(json.dumps(record), encoding="utf-8")


@pytest.fixture
def reference(tmp_path: Path) -> Reference:
    path, record, _, _ = _manifest(tmp_path)
    _save(path, record)
    return load_benchmark(path, tmp_path).references[0]


def test_manifest_fingerprints_inputs_and_preserves_review(tmp_path: Path) -> None:
    path, record, _, _ = _manifest(tmp_path)
    _save(path, record)
    benchmark = load_benchmark(path, tmp_path)
    assert benchmark.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert benchmark.references[0].source == benchmark.sources[0]
    assert benchmark.references[0].expected == "2.0"
    assert benchmark.references[0].review == "candidate"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", "other"),
        ("schema_version", "2"),
        ("schema_version", 1),
        ("sources", []),
        ("sources", {}),
        ("sources", [None]),
        ("references", [None]),
        ("extra", True),
    ],
)
def test_manifest_rejects_invalid_structure(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    path, record, _, _ = _manifest(tmp_path)
    record[field] = value
    _save(path, record)
    with pytest.raises(ValueError):
        load_benchmark(path, tmp_path)


@pytest.mark.parametrize("raw", ["[]", '{"schema":1,"schema":2}', "{invalid"])
def test_manifest_rejects_nonobject_duplicate_keys_and_invalid_json(
    tmp_path: Path,
    raw: str,
) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError):
        load_benchmark(path, tmp_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", ""),
        ("id", 7),
        ("work_id", " "),
        ("sha256", "ABC"),
        ("sha256", "0" * 64),
        ("pages", True),
        ("pages", 0),
        ("pages", 1.5),
        ("split", "tuning"),
        ("path", "../source.pdf"),
        ("path", "/source.pdf"),
        ("path", "a\\b.pdf"),
        ("path", "a:b.pdf"),
        ("path", "./source.pdf"),
        ("path", "."),
        ("path", "a//b.pdf"),
    ],
)
def test_manifest_rejects_invalid_source(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    path, record, source, _ = _manifest(tmp_path)
    source[field] = value
    _save(path, record)
    with pytest.raises(ValueError):
        load_benchmark(path, tmp_path)


def test_manifest_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    root.mkdir()
    path, record, source, _ = _manifest(root)
    (tmp_path / "outside.pdf").write_bytes(b"outside")
    (root / "escape.pdf").symlink_to(tmp_path / "outside.pdf")
    source["path"] = "escape.pdf"
    _save(path, record)
    with pytest.raises(ValueError, match="escapes"):
        load_benchmark(path, root)


def test_manifest_reports_missing_source(tmp_path: Path) -> None:
    path, record, source, _ = _manifest(tmp_path)
    source["path"] = "missing.pdf"
    _save(path, record)
    with pytest.raises(FileNotFoundError):
        load_benchmark(path, tmp_path)


@pytest.mark.parametrize("kind", ["source_id", "source_path", "reference_id"])
def test_manifest_rejects_duplicate_records(tmp_path: Path, kind: str) -> None:
    path, record, source, ref = _manifest(tmp_path)
    if kind == "reference_id":
        record["references"] = [ref, ref]
    else:
        duplicate = dict(source)
        if kind == "source_path":
            duplicate["id"] = "another"
        record["sources"] = [source, duplicate]
    _save(path, record)
    with pytest.raises(ValueError, match="Duplicate"):
        load_benchmark(path, tmp_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_id", "unknown"),
        ("version_id", "published"),
        ("source_sha256", "0" * 64),
        ("page", 3),
        ("page", False),
        ("kind", "prose"),
        ("expected", 2.0),
        ("header_path", []),
        ("header_path", [None]),
        ("review", "automatic"),
        ("provenance", ""),
    ],
)
def test_manifest_rejects_invalid_reference(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    path, record, _, ref = _manifest(tmp_path)
    ref[field] = value
    _save(path, record)
    with pytest.raises(ValueError):
        load_benchmark(path, tmp_path)


@pytest.mark.parametrize(
    ("expected", "headers", "valid"),
    [("x_1^2", [], True), ("", [], False), (" ", [], False), ("x", ["μJ"], False)],
)
def test_equation_reference_contract(
    tmp_path: Path,
    expected: str,
    headers: list[str],
    *,
    valid: bool,
) -> None:
    path, record, _, ref = _manifest(tmp_path)
    ref.update(kind="equation", expected=expected, header_path=headers)
    _save(path, record)
    if valid:
        assert load_benchmark(path, tmp_path).references[0].expected == expected
    else:
        with pytest.raises(ValueError):
            load_benchmark(path, tmp_path)


def test_manifest_allows_inventory_without_annotations(tmp_path: Path) -> None:
    path, record, _, _ = _manifest(tmp_path)
    record["references"] = []
    _save(path, record)
    assert load_benchmark(path, tmp_path).references == ()


@pytest.mark.parametrize(
    ("expected", "actual", "result"),
    [
        ("2.0", "2.0", "exact"),
        ("2.0", "2", "mismatch"),
        ("", "", "exact"),
        ("", None, "missing"),
        ("", "0", "mismatch"),
        ("", "—", "mismatch"),
        ("2.0", " 2.0 ", "mismatch"),
        ("\N{MINUS SIGN}2", "-2", "mismatch"),
        ("1e-3", "1e3", "mismatch"),
    ],
)
def test_cells_preserve_precision_signs_and_explicit_blanks(
    reference: Reference,
    expected: str,
    actual: str | None,
    result: str,
) -> None:
    ref = replace(reference, expected=expected)
    observation = Observation(
        ref.source, ref.page, ref.locator, actual, ref.header_path
    )
    assert compare(ref, observation) == result
    assert ref.review == "candidate"


@pytest.mark.parametrize("change", ["version", "hash", "source", "page", "locator"])
def test_comparison_rejects_wrong_scope(reference: Reference, change: str) -> None:
    source = reference.source
    page, locator = reference.page, reference.locator
    if change == "version":
        source = replace(source, version_id="published")
    elif change == "hash":
        source = replace(source, sha256="0" * 64)
    elif change == "source":
        source = replace(source, id="other")
    elif change == "page":
        page += 1
    else:
        locator += "-other"
    observation = Observation(source, page, locator, reference.expected)
    assert compare(reference, observation) == "scope_mismatch"


def test_comparison_requires_correct_headers(reference: Reference) -> None:
    observation = Observation(
        reference.source,
        reference.page,
        reference.locator,
        reference.expected,
        ("Energy", "mJ"),
    )
    assert compare(reference, observation) == "header_mismatch"


@pytest.mark.parametrize(
    ("expected", "actual", "result"),
    [
        ("x_1^2", "x_1^2", "exact"),
        ("x\ny", " x\r\ny\t", "normalized"),
        ("x_1^2", "x_2^1", "mismatch"),
        ("x-y", "x+y", "mismatch"),
        (r"\text{a b}", r"\text{ab}", "mismatch"),
        (r"\alpha x", r"\alphax", "mismatch"),
        ("x^2", "x^{2}", "mismatch"),
        ("x", "$x$", "mismatch"),
    ],
)
def test_equations_normalize_only_outer_whitespace_and_line_endings(
    reference: Reference,
    expected: str,
    actual: str,
    result: str,
) -> None:
    ref = replace(reference, kind="equation", expected=expected, header_path=())
    assert (
        compare(ref, Observation(ref.source, ref.page, ref.locator, actual)) == result
    )
