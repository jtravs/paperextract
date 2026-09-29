"""Data files attached to a paper byte for byte, without extraction.

A paper's data, such as supporting-information spreadsheets, an author's
tabulated values or a database table the paper documents, can be kept with
the paper. Each file is preserved unchanged below ``data/NN/`` and recorded
with its digest and the provenance the user gave. Attachments are evidence,
not sources: they are never extracted, parsed or rewritten, and they do not
identify the paper to duplicate detection.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from paperextract.fields import dump, integer, items, mapping, record, string
from paperextract.ingest import preserve_file

__all__ = [
    "ATTACHMENTS_FILENAME",
    "DATA_DIRECTORY",
    "Attachment",
    "Provenance",
    "attach_file",
    "attachment_path",
    "parse_attachments",
    "read_attachments",
    "write_attachments",
]

DATA_DIRECTORY = "data"
ATTACHMENTS_FILENAME = "attachments.json"
_ID = re.compile(r"data_(\d{2,})")
_FIELDS = "id path original_name sha256 size_bytes url retrieved_utc attached_utc note"


def _optional(value: object) -> str | None:
    """Read an optional provenance string.

    Parameters
    ----------
    value : object
        Decoded JSON value.

    Returns
    -------
    str or None
        The string unchanged, or None.
    """
    return None if value is None else string(value)


def attachment_path(index: int, name: str) -> str:
    """Give the path of an attached file inside a paper directory.

    Parameters
    ----------
    index : int
        One-based attachment number.
    name : str
        File name as supplied, without a directory.

    Returns
    -------
    str
        ``data/NN/<name>``.

    Raises
    ------
    ValueError
        The name is empty, ``.``, ``..`` or contains a directory.

    Examples
    --------
    >>> attachment_path(3, "table2.xlsx")
    'data/03/table2.xlsx'
    """
    if name in {"", ".", ".."} or PurePosixPath(name).name != name or "\\" in name:
        raise ValueError(f"Not a plain file name: {name!r}")
    return f"{DATA_DIRECTORY}/{index:02d}/{name}"


@dataclass(frozen=True)
class Provenance:
    """Record where attached data came from, as the user states it.

    Attributes
    ----------
    url : str or None
        Address the file was downloaded from.
    retrieved_utc : str or None
        UTC date or time of the download, as given.
    note : str or None
        Free text, such as what the file holds.
    """

    url: str | None = None
    retrieved_utc: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class Attachment:
    """Describe one data file preserved with a paper.

    Attributes
    ----------
    id : str
        ``data_NN``, numbered from 1 in the order of attachment.
    path : str
        ``data/NN/<original name>`` inside the paper directory.
    original_name : str
        File name as supplied, without its private parent path.
    sha256 : str
        Digest of the preserved bytes.
    size_bytes : int
        Number of bytes preserved.
    provenance : Provenance
        Address, retrieval time and note as given; unknown fields are None.
    attached_utc : str
        When the file was attached, in UTC.
    """

    id: str
    path: str
    original_name: str
    sha256: str
    size_bytes: int
    provenance: Provenance
    attached_utc: str

    def to_dict(self) -> dict[str, object]:
        """Serialize the attachment.

        Returns
        -------
        dict of str to object
            JSON-compatible entry of ``extraction.json``.
        """
        return {
            "id": self.id,
            "path": self.path,
            "original_name": self.original_name,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "url": self.provenance.url,
            "retrieved_utc": self.provenance.retrieved_utc,
            "attached_utc": self.attached_utc,
            "note": self.provenance.note,
        }

    @classmethod
    def from_dict(cls, value: object) -> Attachment:
        """Parse a serialized attachment.

        Parameters
        ----------
        value : object
            Decoded JSON value.

        Returns
        -------
        Attachment
            Validated attachment.

        Raises
        ------
        ValueError
            Missing or extra fields, or an identifier and path that disagree.
        """
        data = record(value, _FIELDS)
        identifier = string(data["id"])
        match = _ID.fullmatch(identifier)
        if match is None:
            raise ValueError(f"Not an attachment identifier: {identifier!r}")
        name = string(data["original_name"])
        path = string(data["path"])
        if path != attachment_path(int(match.group(1)), name):
            raise ValueError(f"Attachment {identifier} has the path {path!r}")
        digest = string(data["sha256"])
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError(f"Attachment {identifier} has no SHA-256 digest")
        return cls(
            id=identifier,
            path=path,
            original_name=name,
            sha256=digest,
            size_bytes=integer(data["size_bytes"], minimum=0),
            provenance=Provenance(
                url=_optional(data["url"]),
                retrieved_utc=_optional(data["retrieved_utc"]),
                note=_optional(data["note"]),
            ),
            attached_utc=string(data["attached_utc"]),
        )


def parse_attachments(value: object) -> tuple[Attachment, ...]:
    """Parse a list of attachments and check their numbering.

    Parameters
    ----------
    value : object
        Decoded JSON array, such as ``extraction.json``'s ``attachments``.

    Returns
    -------
    tuple of Attachment
        Attachments in order.

    Raises
    ------
    ValueError
        An entry is malformed, or they are not numbered ``data_01``,
        ``data_02`` and so on.
    """
    parsed = tuple(Attachment.from_dict(entry) for entry in items(value))
    for index, attachment in enumerate(parsed, 1):
        if attachment.id != f"data_{index:02d}":
            raise ValueError(f"Attachment {attachment.id} is out of order")
    return parsed


def read_attachments(staging: Path) -> tuple[Attachment, ...]:
    """Read the data files attached in a staging directory.

    Parameters
    ----------
    staging : Path
        Main extraction staging directory.

    Returns
    -------
    tuple of Attachment
        Attachments in order, empty when none were attached.
    """
    path = staging / ATTACHMENTS_FILENAME
    if not path.is_file():
        return ()
    return parse_attachments(mapping(json.loads(path.read_text()))["attachments"])


def write_attachments(staging: Path, attachments: Sequence[Attachment]) -> None:
    """Record the data files attached in a staging directory.

    Parameters
    ----------
    staging : Path
        Main extraction staging directory.
    attachments : Sequence of Attachment
        Every attachment in order; an empty sequence writes nothing.
    """
    if attachments:
        (staging / ATTACHMENTS_FILENAME).write_text(
            dump({"attachments": [item.to_dict() for item in attachments]})
        )


def attach_file(
    staging: Path, source: Path, provenance: Provenance, *, attached_utc: str
) -> Attachment:
    """Preserve a data file in a staging directory, after any already there.

    Parameters
    ----------
    staging : Path
        Main extraction staging directory.
    source : Path
        File to attach; its bytes are copied, verified and never modified.
    provenance : Provenance
        Where the file came from, as the user states it.
    attached_utc : str
        Time of attachment.

    Returns
    -------
    Attachment
        The new attachment, stored at ``data/NN/<name>`` in the staging
        directory and recorded in ``attachments.json``.

    Raises
    ------
    ValueError
        The same bytes are already attached, or the name is unusable.
    paperextract.ingest.SourceChangedError
        The file changed while it was copied.
    OSError
        The file cannot be read or copied.
    """
    existing = read_attachments(staging)
    index = len(existing) + 1
    relative = attachment_path(index, source.name)
    target = staging / relative
    target.parent.mkdir(parents=True)
    try:
        artifact = preserve_file(source, target)
    except BaseException:
        shutil.rmtree(target.parent)
        raise
    same = next((item for item in existing if item.sha256 == artifact.sha256), None)
    if same is not None:
        shutil.rmtree(target.parent)
        raise ValueError(f"{source.name} has the same bytes as {same.path}")
    attachment = Attachment(
        id=f"data_{index:02d}",
        path=relative,
        original_name=source.name,
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
        provenance=provenance,
        attached_utc=attached_utc,
    )
    write_attachments(staging, (*existing, attachment))
    return attachment
