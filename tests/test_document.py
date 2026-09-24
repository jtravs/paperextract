"""Round-trip the canonical document schema through JSON."""

import json
from dataclasses import replace

import pytest

from paperextract.document import (
    SCHEMA_NAME,
    Document,
    Figure,
    Table,
    TableBody,
    block_from_dict,
)


def test_document_round_trips_through_json(normalized_document: Document) -> None:
    payload = normalized_document.to_json()
    restored = Document.from_json(payload)
    assert restored == normalized_document
    assert restored.to_json() == payload
    figures = [b for b in restored.blocks if isinstance(b, Figure)]
    tables = [b for b in restored.blocks if isinstance(b, Table)]
    assert len(figures) == 4
    assert len(tables) == 5
    assert tables[0].cells is not None


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema",), "paperextract.other"),
        (("schema_version",), "9.9"),
        (("source_sha256",), ""),
        (("pages", 0, "status"), "done"),
        (("pages", 0, "width_pt"), "wide"),
        (("blocks", 0, "kind"), "poem"),
        (("blocks", 0, "span", "page"), 0),
        (("blocks", 0, "span", "bbox_pt"), [1, 2, 3]),
        (("blocks", 0, "runs", 0, "kind"), "emoji"),
        (("blocks", 0, "runs", 0, "styles"), ["bold", 3]),
        (("findings", 0, "severity"), "fatal"),
        (("metadata", 0, "value"), ""),
    ],
)
def test_document_parsing_rejects_foreign_values(
    normalized_document: Document, path: tuple[object, ...], value: object
) -> None:
    payload = normalized_document.to_dict()
    target: object = payload
    for key in path[:-1]:
        target = target[key]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]
    with pytest.raises(ValueError):
        Document.from_dict(payload)


def test_document_parsing_requires_exact_fields(normalized_document: Document) -> None:
    payload = normalized_document.to_dict()
    del payload["findings"]
    with pytest.raises(ValueError, match="exactly these fields"):
        Document.from_dict(payload)
    with pytest.raises(ValueError, match="JSON object"):
        Document.from_dict("not a document")


def test_every_block_kind_round_trips(normalized_document: Document) -> None:
    kinds: set[object] = set()
    for block in normalized_document.blocks:
        serialized = block.to_dict()
        kinds.add(serialized["kind"])
        assert block_from_dict(json.loads(json.dumps(serialized))) == block
    assert kinds == {
        "heading",
        "paragraph",
        "list",
        "equation",
        "figure",
        "table",
        "page_furniture",
        "unclassified",
    }
    with pytest.raises(ValueError):
        block_from_dict({"kind": "table", "id": "x"})
    with pytest.raises(ValueError):
        block_from_dict(["not", "a", "block"])
    assert normalized_document.to_dict()["schema"] == SCHEMA_NAME


def test_table_alternatives_round_trip_and_version_0_1_still_reads(
    normalized_document: Document,
) -> None:
    table = next(b for b in normalized_document.blocks if isinstance(b, Table))
    alternative = TableBody(
        "native", "<table><tr><td>x</td></tr></table>", None, None, None
    )
    grid = TableBody("ocr", table.html, table.cells, table.rows, table.columns)
    chosen = replace(table, body_source="ocr", alternatives=(alternative, grid))
    assert block_from_dict(json.loads(json.dumps(chosen.to_dict()))) == chosen
    legacy = table.to_dict()
    del legacy["body_source"], legacy["alternatives"]
    assert block_from_dict(legacy) == table
    payload = normalized_document.to_dict()
    payload["schema_version"] = "0.1"
    for block in payload["blocks"]:  # type: ignore[union-attr]
        if block["kind"] == "table":  # type: ignore[index]
            del block["body_source"], block["alternatives"]  # type: ignore[attr-defined]
    assert Document.from_dict(payload) == normalized_document
    broken = chosen.to_dict()
    broken["alternatives"] = [{**alternative.to_dict(), "source": "guess"}]
    with pytest.raises(ValueError):
        block_from_dict(broken)
