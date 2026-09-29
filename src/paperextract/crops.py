"""Keep complete-figure crops when a paper is published again.

A complete-figure crop is a raster of the preserved PDF, rendered at
publication. PDFium draws a font the PDF does not embed, such as the Arial or
Times New Roman of many supplements, with a font the host provides, so the
same region of the same file renders differently on machines with different
fonts installed. Rebuilding a paper on another machine would then change its
crops although nothing that defines them changed.

Rebuilding therefore stages the crops a paper already has together with what
they were rendered from: the source digest, page, region, resolution and
renderer version. Publication copies a staged crop instead of rendering it
again when all of these still match, and renders a new one otherwise.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from paperextract.document import Box, Document, Figure
from paperextract.fields import dump, integer, items, number, record, string

__all__ = [
    "KEPT_CROPS_DIRECTORY",
    "KEPT_CROPS_FILENAME",
    "RENDER_VERSION",
    "KeptCrop",
    "KeptCrops",
    "keep_crops",
    "read_kept_crops",
]

KEPT_CROPS_DIRECTORY = "contexts"
KEPT_CROPS_FILENAME = "contexts.json"
_SCHEMA = "paperextract.kept-contexts"
_SCHEMA_VERSION = 1
_FIELDS = "schema schema_version source_sha256 dpi render_version figures"
_FIGURE_FIELDS = "id page context_bbox_pt sha256"
_BOX_SIZE = 4
# Bump when a change to rendering, such as the margin added around the region
# or the PNG encoding, should replace the crops of rebuilt papers.
RENDER_VERSION = 1


@dataclass(frozen=True)
class KeptCrop:
    """Hold what one kept crop was rendered from.

    Attributes
    ----------
    page : int
        One-based page.
    context_bbox_pt : tuple of float
        The figure's complete-context box in points.
    sha256 : str
        Digest of the crop.
    """

    page: int
    context_bbox_pt: Box
    sha256: str


@dataclass(frozen=True)
class KeptCrops:
    """Hold the crops staged for one component, with what they came from.

    Attributes
    ----------
    directory : Path
        Directory holding ``<figure id>.png``.
    source_sha256 : str
        Digest of the PDF the crops were rendered from.
    dpi : int
        Resolution they were rendered at.
    render_version : int
        :data:`RENDER_VERSION` of the release that rendered them.
    crops : Mapping of str to KeptCrop
        Kept crops by figure id.
    """

    directory: Path
    source_sha256: str
    dpi: int
    render_version: int
    crops: Mapping[str, KeptCrop]

    def crop_for(self, figure: Figure, *, source_sha256: str, dpi: int) -> Path | None:
        """Find the kept crop that rendering this figure would reproduce.

        Parameters
        ----------
        figure : Figure
            Figure about to be published.
        source_sha256 : str
            Digest of the PDF it would be rendered from.
        dpi : int
            Resolution it would be rendered at.

        Returns
        -------
        Path or None
            The kept crop, or None when the figure has no kept crop or its
            source, page, region, resolution or renderer changed.

        Raises
        ------
        ValueError
            The kept crop no longer has the digest it was staged with.
        """
        crop = self.crops.get(figure.id)
        if (
            crop is None
            or (self.source_sha256, self.dpi, self.render_version)
            != (source_sha256, dpi, RENDER_VERSION)
            or (crop.page, crop.context_bbox_pt)
            != (figure.page, figure.context_bbox_pt)
        ):
            return None
        path = self.directory / f"{figure.id}.png"
        if hashlib.sha256(path.read_bytes()).hexdigest() != crop.sha256:
            raise ValueError(f"Kept crop of figure {figure.id} changed")
        return path


def keep_crops(document: Document, figures: Path, staging: Path, dpi: int) -> None:
    """Stage the complete-figure crops a published component has.

    Parameters
    ----------
    document : Document
        The component's published document.
    figures : Path
        Its ``figures`` directory.
    staging : Path
        Staging directory; the crops go to ``contexts/`` with
        ``contexts.json``.
    dpi : int
        Resolution the crops were rendered at.
    """
    directory = staging / KEPT_CROPS_DIRECTORY
    kept: list[dict[str, object]] = []
    for block in document.blocks:
        path = figures / f"{block.id}.png"
        if (
            not isinstance(block, Figure)
            or block.context_bbox_pt is None
            or not path.is_file()
        ):
            continue
        directory.mkdir(exist_ok=True)
        shutil.copyfile(path, directory / path.name)
        kept.append(
            {
                "id": block.id,
                "page": block.page,
                "context_bbox_pt": list(block.context_bbox_pt),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    if not kept:
        return
    (directory / KEPT_CROPS_FILENAME).write_text(
        dump(
            {
                "schema": _SCHEMA,
                "schema_version": _SCHEMA_VERSION,
                "source_sha256": document.source_sha256,
                "dpi": dpi,
                "render_version": RENDER_VERSION,
                "figures": kept,
            }
        )
    )


def _box(value: object) -> Box:
    """Read a four-number box.

    Parameters
    ----------
    value : object
        Decoded JSON value.

    Returns
    -------
    tuple of float
        The box.

    Raises
    ------
    ValueError
        The value is not four finite numbers.
    """
    values = [number(item) for item in items(value)]
    if len(values) != _BOX_SIZE:
        raise ValueError("Expected a box of four numbers")
    x0, y0, x1, y1 = values
    return (x0, y0, x1, y1)


def read_kept_crops(staging: Path) -> KeptCrops | None:
    """Read the crops staged for one component.

    Parameters
    ----------
    staging : Path
        Staging directory of the paper or of one supplement.

    Returns
    -------
    KeptCrops or None
        The kept crops, or None when none were staged.

    Raises
    ------
    ValueError
        ``contexts.json`` is malformed or has another schema.
    """
    directory = staging / KEPT_CROPS_DIRECTORY
    path = directory / KEPT_CROPS_FILENAME
    if not path.is_file():
        return None
    data = record(json.loads(path.read_text()), _FIELDS)
    if (data["schema"], data["schema_version"]) != (_SCHEMA, _SCHEMA_VERSION):
        raise ValueError(f"{path} is not a {_SCHEMA} version {_SCHEMA_VERSION}")
    crops: dict[str, KeptCrop] = {}
    for item in items(data["figures"]):
        figure = record(item, _FIGURE_FIELDS)
        crops[string(figure["id"])] = KeptCrop(
            page=integer(figure["page"], minimum=1),
            context_bbox_pt=_box(figure["context_bbox_pt"]),
            sha256=string(figure["sha256"]),
        )
    return KeptCrops(
        directory=directory,
        source_sha256=string(data["source_sha256"]),
        dpi=integer(data["dpi"], minimum=1),
        render_version=integer(data["render_version"], minimum=1),
        crops=crops,
    )
