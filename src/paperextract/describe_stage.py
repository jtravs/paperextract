"""Describe the figures of an extraction staging directory.

The stage renders each figure from the preserved PDF at the resolution the P3
trial used, builds the prompt from the published caption, panel labels and the
first citing sentence, and skips figures that already have a parsed
description from the same model, prompt and image. Replies are parsed,
checked against the PDF text layer and appended to the figure's description
file, so earlier descriptions from other models are kept.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from paperextract.describe import (
    PROMPT_VERSION,
    FigureDescription,
    append_description,
    build_prompt,
    citing_sentence,
    description_path,
    has_description,
    parse_claims,
    prompt_sha256,
    read_descriptions,
    verify_printed,
)
from paperextract.describers import Describer, DescribeRequest
from paperextract.document import Document, Figure, plain_text
from paperextract.pdf import (
    UnreadablePdfError,
    ocr_text_layer,
    region_text,
    render_region_png,
)
from paperextract.pipeline import (
    DOCUMENT_FILENAME,
    SOURCE_FILENAME,
    SUPPLEMENTS_DIRECTORY,
)

__all__ = [
    "MAX_DPI",
    "MAX_SIDE_PX",
    "PendingFigure",
    "StageReport",
    "describe_staging",
    "pending_figures",
]

# The trial rendered figures at up to 1536 px on the long side and 200 dpi;
# larger images cost more tokens without better readings.
MAX_SIDE_PX = 1536
MAX_DPI = 200


@dataclass(frozen=True)
class PendingFigure:
    """Hold one figure that still needs a description.

    Attributes
    ----------
    component : Path
        Staging directory of the paper or supplement that holds the figure.
    request : DescribeRequest
        Image and prompt for the model.
    image_sha256 : str
        Digest of the rendered image.
    caption : str or None
        Published caption as plain text.
    region_text : str
        PDF text layer inside the figure region.
    ocr_layer : bool
        Whether that text layer is invisible OCR.
    """

    component: Path
    request: DescribeRequest
    image_sha256: str
    caption: str | None
    region_text: str
    ocr_layer: bool


@dataclass(frozen=True)
class StageReport:
    """Summarize one staging directory's description run.

    Attributes
    ----------
    described : int
        Figures that received a parsed description.
    skipped : int
        Figures that already had one from this model, prompt and image.
    failed : int
        Figures whose request failed or whose answer did not parse; failed
        answers are still recorded when the model returned text.
    unrenderable : int
        Figures without geometry or whose region could not be rendered.
    usd : float
        Metered cost of this run's paid calls.
    recorded : int
        Records written, including answers that did not parse.
    errors : tuple of str
        One message per failed figure.
    """

    described: int = 0
    skipped: int = 0
    failed: int = 0
    unrenderable: int = 0
    usd: float = 0.0
    recorded: int = 0
    errors: tuple[str, ...] = ()


def _components(staging: Path) -> list[Path]:
    """List the staging directories of the paper and its supplements.

    Parameters
    ----------
    staging : Path
        Paper staging directory.

    Returns
    -------
    list of Path
        The paper first, then each supplement in order.
    """
    directory = staging / SUPPLEMENTS_DIRECTORY
    supplements = (
        sorted(p for p in directory.iterdir() if p.is_dir())
        if directory.is_dir()
        else []
    )
    return [staging, *supplements]


def _render(pdf: Path, figure: Figure) -> bytes | None:
    """Render a figure's complete region for the model.

    Parameters
    ----------
    pdf : Path
        Preserved source PDF.
    figure : Figure
        Canonical figure.

    Returns
    -------
    bytes or None
        PNG, or None without geometry or when rendering fails.
    """
    box = figure.context_bbox_pt
    if box is None:
        return None
    longest = max(box[2] - box[0], box[3] - box[1], 1.0)
    dpi = int(min(MAX_DPI, MAX_SIDE_PX * 72.0 / longest))
    try:
        return render_region_png(pdf, figure.page, box, dpi=dpi).png
    except (ValueError, UnreadablePdfError):
        return None


def pending_figures(
    staging: Path,
    model: Mapping[str, object],
    *,
    prompt_version: str = PROMPT_VERSION,
    force: bool = False,
) -> tuple[list[PendingFigure], int, int]:
    """Find the figures of a staging directory that need a description.

    Parameters
    ----------
    staging : Path
        Paper staging directory with ``source.pdf`` and ``document.json``,
        and supplement directories below ``supplements/``.
    model : Mapping of str to object
        Provenance of the describing model, compared with existing records.
    prompt_version : str
        Prompt template version.
    force : bool
        Describe again even when an equivalent description exists.

    Returns
    -------
    tuple
        Pending figures, the number skipped as already described, and the
        number that could not be rendered.
    """
    pending: list[PendingFigure] = []
    skipped = unrenderable = 0
    for component in _components(staging):
        document = Document.from_json((component / DOCUMENT_FILENAME).read_text())
        pdf = component / SOURCE_FILENAME
        for block in document.blocks:
            if not isinstance(block, Figure):
                continue
            png = _render(pdf, block)
            if png is None:
                unrenderable += 1
                continue
            digest = hashlib.sha256(png).hexdigest()
            records = read_descriptions(description_path(component, block.id))
            if not force and has_description(records, model, prompt_version, digest):
                skipped += 1
                continue
            caption = None if block.caption is None else plain_text(block.caption)
            prompt = build_prompt(
                caption,
                [panel.label for panel in block.panels if panel.label],
                citing_sentence(document, block.label),
                version=prompt_version,
            )
            box = block.context_bbox_pt
            assert box is not None  # rendering succeeded, so geometry exists
            pending.append(
                PendingFigure(
                    component=component,
                    request=DescribeRequest(block.id, png, prompt),
                    image_sha256=digest,
                    caption=caption,
                    region_text=region_text(pdf, block.page, box),
                    ocr_layer=ocr_text_layer(pdf, block.page),
                )
            )
    return pending, skipped, unrenderable


def _utc_now() -> str:
    """Return the current time for description records.

    Returns
    -------
    str
        ISO 8601 UTC to the second.
    """
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def describe_staging(
    staging: Path,
    describer: Describer,
    *,
    prompt_version: str = PROMPT_VERSION,
    force: bool = False,
    clock: Callable[[], str] = _utc_now,
) -> StageReport:
    """Describe the pending figures of a staging directory and record them.

    Parameters
    ----------
    staging : Path
        Paper staging directory.
    describer : Describer
        Model runtime.
    prompt_version : str
        Prompt template version.
    force : bool
        Describe again even when an equivalent description exists.
    clock : Callable
        Source of the ``created_utc`` time stamp.

    Returns
    -------
    StageReport
        Counts, cost and failures; descriptions are appended to
        ``descriptions/<figure_id>.json`` in each component directory.
    """
    model = dict(describer.model)
    pending, skipped, unrenderable = pending_figures(
        staging, model, prompt_version=prompt_version, force=force
    )
    replies = describer.describe([item.request for item in pending]) if pending else []
    described = failed = recorded = 0
    usd = 0.0
    errors: list[str] = []
    for item, reply in zip(pending, replies, strict=True):
        figure_id = item.request.figure_id
        usd += reply.usd or 0.0
        if reply.error is not None:
            failed += 1
            errors.append(f"{figure_id}: {reply.error}")
            continue
        try:
            claims, parse_error = parse_claims(reply.text), None
        except ValueError as exc:
            claims, parse_error = None, str(exc)
        record = FigureDescription(
            figure_id=figure_id,
            model=model,
            prompt_version=prompt_version,
            prompt_sha256=prompt_sha256(item.request.prompt),
            image_sha256=item.image_sha256,
            sampling=dict(describer.sampling),
            seconds=reply.seconds,
            prompt_tokens=reply.prompt_tokens,
            generation_tokens=reply.generation_tokens,
            raw_text=reply.text,
            claims=claims,
            parse_error=parse_error,
            printed_check=None
            if claims is None
            else verify_printed(
                claims,
                item.region_text,
                caption=item.caption,
                ocr_layer=item.ocr_layer,
            ),
            created_utc=clock(),
            finish_reason=reply.finish_reason,
            usd=reply.usd,
        )
        append_description(description_path(item.component, figure_id), record)
        recorded += 1
        if claims is None:
            failed += 1
            errors.append(f"{figure_id}: {parse_error}")
        else:
            described += 1
    return StageReport(
        described=described,
        skipped=skipped,
        failed=failed,
        unrenderable=unrenderable,
        usd=round(usd, 6),
        recorded=recorded,
        errors=tuple(errors),
    )
