"""Stage and read the complete-figure crops a rebuilt paper keeps."""

import json
from pathlib import Path

import pytest

from paperextract.crops import (
    KEPT_CROPS_DIRECTORY,
    KEPT_CROPS_FILENAME,
    RENDER_VERSION,
    keep_crops,
    read_kept_crops,
)
from paperextract.document import Document, Figure

SOURCE = "a" * 64


def figure(identifier: str, box: tuple[float, float, float, float] | None) -> Figure:
    return Figure(identifier, None, None, None, (), (), box, "single", 2)


def document(*blocks: Figure) -> Document:
    return Document(SOURCE, "mineru", "1", "s", "1", (), blocks, (), ())


def test_only_figures_with_a_rendered_crop_are_kept(tmp_path: Path) -> None:
    figures = tmp_path / "figures"
    figures.mkdir()
    (figures / "fig_a.png").write_bytes(b"a")
    (figures / "fig_b.png").write_bytes(b"b")
    staging = tmp_path / "staging"
    staging.mkdir()
    box = (1.0, 2.0, 3.0, 4.0)
    keep_crops(
        document(figure("fig_a", box), figure("fig_b", None), figure("fig_c", box)),
        figures,
        staging,
        300,
    )
    kept = read_kept_crops(staging)
    assert kept is not None
    assert set(kept.crops) == {"fig_a"}
    assert kept.crop_for(figure("fig_a", box), source_sha256=SOURCE, dpi=300) == (
        staging / KEPT_CROPS_DIRECTORY / "fig_a.png"
    )
    assert kept.crop_for(figure("fig_c", box), source_sha256=SOURCE, dpi=300) is None
    assert kept.crop_for(figure("fig_a", box), source_sha256="c" * 64, dpi=300) is None


def test_a_component_without_crops_stages_nothing(tmp_path: Path) -> None:
    keep_crops(document(figure("fig_a", None)), tmp_path / "figures", tmp_path, 300)
    assert not (tmp_path / KEPT_CROPS_DIRECTORY).exists()
    assert read_kept_crops(tmp_path) is None


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        ({"schema": "other"}, "is not a paperextract.kept-contexts"),
        ({"schema_version": 2}, "is not a paperextract.kept-contexts"),
        ({"extra": 1}, "Expected exactly these fields"),
        ({"dpi": 0}, "Expected an integer of at least 1"),
        ({"figures": [{"id": "fig_a"}]}, "Expected exactly these fields"),
        (
            {
                "figures": [
                    {
                        "id": "fig_a",
                        "page": 1,
                        "context_bbox_pt": [1.0, 2.0],
                        "sha256": "x",
                    }
                ]
            },
            "Expected a box of four numbers",
        ),
    ],
)
def test_malformed_records_are_refused(
    tmp_path: Path, edit: dict[str, object], message: str
) -> None:
    directory = tmp_path / KEPT_CROPS_DIRECTORY
    directory.mkdir()
    record: dict[str, object] = {
        "schema": "paperextract.kept-contexts",
        "schema_version": 1,
        "source_sha256": SOURCE,
        "dpi": 300,
        "render_version": RENDER_VERSION,
        "figures": [],
        **edit,
    }
    (directory / KEPT_CROPS_FILENAME).write_text(json.dumps(record))
    with pytest.raises(ValueError, match=message):
        read_kept_crops(tmp_path)
