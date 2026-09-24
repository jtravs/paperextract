"""Rebuild a published paper from the output it keeps, without the worker.

Every paper directory keeps its preserved sources, the backend's native output,
the worker's request and result, and the exported crops. That is enough to
reconstruct the staging directory of the original run, so improvements to
normalization, corrections, identity or export can be applied to a library
without running the extraction backend again.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from paperextract.capture import CAPTURE_RECORD, CaptureRecord
from paperextract.describe import DESCRIPTIONS_DIRECTORY
from paperextract.document import Document, Equation, Figure, Table
from paperextract.fields import dump, items, mapping, string
from paperextract.formats import require_readable_paper
from paperextract.pipeline import (
    CAPTURES_DIRECTORY,
    DOCUMENT_FILENAME,
    EQUIVALENTS_DIRECTORY,
    OCR_WORKER_DIRECTORY,
    SOURCE_FILENAME,
    SOURCE_RECORD_FILENAME,
    SOURCE_RECORD_SCHEMA,
    SOURCE_RECORD_VERSION,
    TABLE_CHECK_DIRECTORY,
    WORKER_DIRECTORY,
    supplement_directory,
    write_hints,
)
from paperextract.protocol import REQUEST_FILENAME, RESULT_FILENAME

__all__ = [
    "KeptRun",
    "MissingOutputError",
    "kept_run",
    "stage_from_paper",
]

_NATIVE_FILES = (
    "docling.json",
    "middle_json.json",
    "structured_content.json",
    "model_output.json",
    "markdown.md",
)
_WORKER_FILES = (
    REQUEST_FILENAME,
    RESULT_FILENAME,
    "worker.stdout.log",
    "worker.stderr.log",
)
_TABLE_EXPORTS = frozenset({".html", ".json", ".csv"})
_IDENTITY = "identity.json"


class MissingOutputError(FileNotFoundError):
    """Report a paper directory that lacks the output needed to rebuild it."""


@dataclass(frozen=True)
class KeptRun:
    """Locate the kept output of one component of a paper directory.

    Attributes
    ----------
    root : Path
        Directory holding the component's ``document.json`` and assets: the
        paper directory or its ``supplement_NN`` directory.
    raw : Path
        Kept native output and worker records.
    source : dict of str to object
        Source entry from ``extraction.json``.
    original : Path
        Preserved source PDF.
    """

    root: Path
    raw: Path
    source: dict[str, object]
    original: Path


def kept_run(paper: Path) -> tuple[KeptRun, tuple[KeptRun, ...]]:
    """Find the kept output of a paper and its supplements.

    Parameters
    ----------
    paper : Path
        Published paper directory.

    Returns
    -------
    tuple
        The paper's component and its supplements in order.

    Raises
    ------
    MissingOutputError
        ``extraction.json``, the raw output or a preserved source is missing.
    """
    extraction_path = paper / "extraction.json"
    if not extraction_path.is_file():
        raise MissingOutputError(f"{paper} has no extraction.json")
    extraction = mapping(json.loads(extraction_path.read_text()))
    raw = paper / "diagnostics" / "raw" / string(extraction["run_id"])
    sources = [mapping(item) for item in items(extraction["sources"])]
    supplements = [mapping(item) for item in items(extraction.get("supplements", []))]
    components = [KeptRun(paper, raw, sources[0], paper / string(sources[0]["path"]))]
    for index, supplement in enumerate(supplements, 1):
        name = f"supplement_{index:02d}"
        source = next(s for s in sources if s["id"] == supplement["source"])
        components.append(
            KeptRun(paper / name, raw / name, source, paper / string(source["path"]))
        )
    for component in components:
        for required in (component.raw / RESULT_FILENAME, component.original):
            if not required.is_file():
                raise MissingOutputError(
                    f"{paper}: missing {required.relative_to(paper)}"
                )
    return components[0], tuple(components[1:])


def _copy_if_present(source: Path, target: Path) -> None:
    """Copy a file when it exists.

    Parameters
    ----------
    source : Path
        Candidate file.
    target : Path
        Destination; parents are created.
    """
    if source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def _exported(
    directory: Path, stem: str, *, skip: frozenset[str] = frozenset()
) -> Path | None:
    """Find the exported copy of a backend crop.

    Parameters
    ----------
    directory : Path
        Asset directory such as ``figures``.
    stem : str
        File name without suffix.
    skip : frozenset of str
        Suffixes of derived exports that are not crops.

    Returns
    -------
    Path or None
        The crop, or None when it was never exported.
    """
    matches = [
        path
        for path in sorted(directory.glob(f"{stem}.*"))
        if path.suffix.lower() not in skip
    ]
    return matches[0] if matches else None


def _restore_crops(component: KeptRun, worker: Path) -> None:
    """Put exported crops back at the native paths the backend wrote.

    Parameters
    ----------
    component : KeptRun
        Component with its previous ``document.json``.
    worker : Path
        Reconstructed worker directory.
    """
    document = Document.from_json((component.root / DOCUMENT_FILENAME).read_text())
    pairs: list[tuple[str | None, Path | None]] = []
    for block in document.blocks:
        if isinstance(block, Figure):
            pairs.extend(
                (
                    panel.asset,
                    _exported(component.root / "figures", f"{block.id}_p{index:02d}"),
                )
                for index, panel in enumerate(block.panels, 1)
            )
        elif isinstance(block, Table):
            found = _exported(component.root / "tables", block.id, skip=_TABLE_EXPORTS)
            pairs.append((block.asset, found))
        elif isinstance(block, Equation):
            pairs.append(
                (block.asset, _exported(component.root / "equations", block.id))
            )
    for native, exported in pairs:
        if native is not None and exported is not None:
            _copy_if_present(exported, worker / native)


def _stage_component(component: KeptRun, staging: Path) -> None:
    """Reconstruct one component's staging directory.

    Parameters
    ----------
    component : KeptRun
        Kept output.
    staging : Path
        New, empty staging directory.
    """
    shutil.copyfile(component.original, staging / SOURCE_FILENAME)
    source = component.source
    record = {
        "schema": SOURCE_RECORD_SCHEMA,
        "schema_version": SOURCE_RECORD_VERSION,
        "sha256": source["sha256"],
        "size_bytes": source["size_bytes"],
        "original_name": source["original_name"],
        "stored_path": SOURCE_FILENAME,
        "inspection": source["inspection"],
        "fingerprint": source["fingerprint"],
    }
    (staging / SOURCE_RECORD_FILENAME).write_text(dump(record))
    for worker_name, raw in (
        (WORKER_DIRECTORY, component.raw),
        (OCR_WORKER_DIRECTORY, component.raw / "table-ocr"),
        (TABLE_CHECK_DIRECTORY, component.raw / "table-check"),
    ):
        if not (raw / RESULT_FILENAME).is_file():
            continue
        worker = staging / worker_name
        for filename in _WORKER_FILES:
            _copy_if_present(raw / filename, worker / filename)
        for filename in _NATIVE_FILES:
            _copy_if_present(raw / filename, worker / "native" / filename)
    _restore_crops(component, staging / WORKER_DIRECTORY)
    # The previous document lets the paper be exported again unchanged;
    # rebuild_document replaces it with a fresh normalization.
    shutil.copyfile(component.root / DOCUMENT_FILENAME, staging / DOCUMENT_FILENAME)
    _copy_if_present(component.raw / _IDENTITY, staging / _IDENTITY)
    # Descriptions cost model time or money; publication keeps those whose
    # figure is still in the rebuilt document.
    for path in sorted((component.root / DESCRIPTIONS_DIRECTORY).glob("*.json")):
        _copy_if_present(path, staging / DESCRIPTIONS_DIRECTORY / path.name)


def _stage_captures(paper: Path, staging: Path) -> None:
    """Restore the preserved web-page captures of a paper.

    Parameters
    ----------
    paper : Path
        Published paper directory.
    staging : Path
        Main staging directory; captures go to ``html/NN``.

    Raises
    ------
    MissingOutputError
        A recorded capture file is missing.
    """
    extraction = mapping(json.loads((paper / "extraction.json").read_text()))
    captures = [
        mapping(item)
        for item in items(extraction["sources"])
        if mapping(item).get("role") == "html_capture"
    ]
    for index, source in enumerate(captures, 1):
        capture = CaptureRecord.from_dict(source["capture"])
        directory = staging / CAPTURES_DIRECTORY / f"{index:02d}"
        for item in capture.files:
            original = paper / "original" / string(source["id"]) / item.path
            if not original.is_file():
                raise MissingOutputError(
                    f"{paper}: missing {original.relative_to(paper)}"
                )
            target = directory / item.path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
        (directory / CAPTURE_RECORD).write_text(dump(capture.to_dict()))


def _stage_equivalents(paper: Path, staging: Path) -> None:
    """Restore the preserved content-equivalent copies of a paper.

    Parameters
    ----------
    paper : Path
        Published paper directory.
    staging : Path
        Main staging directory; copies go to ``equivalents/NN``.

    Raises
    ------
    MissingOutputError
        A recorded copy is missing.
    """
    extraction = mapping(json.loads((paper / "extraction.json").read_text()))
    copies = [
        mapping(item)
        for item in items(extraction["sources"])
        if mapping(item).get("role") == "equivalent_copy"
    ]
    for index, source in enumerate(copies, 1):
        original = paper / string(source["path"])
        if not original.is_file():
            raise MissingOutputError(f"{paper}: missing {source['path']}")
        directory = staging / EQUIVALENTS_DIRECTORY / f"{index:02d}"
        directory.mkdir(parents=True)
        shutil.copyfile(original, directory / SOURCE_FILENAME)
        record = {
            "schema": SOURCE_RECORD_SCHEMA,
            "schema_version": SOURCE_RECORD_VERSION,
            "sha256": source["sha256"],
            "size_bytes": source["size_bytes"],
            "original_name": source["original_name"],
            "stored_path": SOURCE_FILENAME,
            "inspection": source["inspection"],
            "fingerprint": source["fingerprint"],
        }
        (directory / SOURCE_RECORD_FILENAME).write_text(dump(record))


def stage_from_paper(paper: Path, staging: Path) -> None:
    """Reconstruct the staging directory of a published paper's run.

    Parameters
    ----------
    paper : Path
        Published paper directory.
    staging : Path
        Existing, empty directory that receives ``source.pdf``,
        ``source.json``, the previous ``document.json``, ``worker/`` with
        native output and crops,
        ``worker-ocr/`` when a table OCR run was kept, ``worker-check/``
        when a Docling table check was kept, ``identity.json``,
        ``descriptions/`` with kept figure descriptions,
        ``supplements/NN/`` for each supplement, ``html/NN/`` for each
        preserved web-page capture and ``equivalents/NN/`` for each
        content-equivalent copy.

    Raises
    ------
    MissingOutputError
        The paper directory lacks kept output.
    FileExistsError
        The staging directory is not empty.

    Notes
    -----
    Crops of blocks that the previous document did not reference, such as
    page images, were never exported and cannot be restored; the rebuilt
    document reports them as missing assets if it now references them.
    """
    if any(staging.iterdir()):
        raise FileExistsError(f"Staging directory is not empty: {staging}")
    require_readable_paper(paper)
    main, supplements = kept_run(paper)
    _stage_component(main, staging)
    _stage_captures(paper, staging)
    _stage_equivalents(paper, staging)
    extraction = mapping(json.loads((paper / "extraction.json").read_text()))
    write_hints(
        staging,
        {
            key: string(value)
            for key, value in mapping(extraction.get("assertions", {})).items()
        },
    )
    for index, component in enumerate(supplements, 1):
        directory = supplement_directory(staging, index)
        directory.mkdir(parents=True)
        _stage_component(component, directory)
