"""Fetch and check the pinned model snapshots and binaries the workers use.

``workers/models.json`` in a source checkout lists every model snapshot by
repository, revision and file, with each file's size and SHA-256, and the
pinned llama.cpp server archives by platform. Sets group what one backend
configuration needs. Fetching is an explicit setup step: extraction never
downloads. Every file is downloaded to a temporary name, checked against
its recorded size and digest, and only then moved into place, so an
interrupted fetch resumes and a damaged file is never used.
"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import shutil
import tarfile
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from paperextract.fields import boolean, integer, items, mapping, string, text

__all__ = [
    "MANIFEST_SCHEMA",
    "MANIFEST_VERSION",
    "Binary",
    "Download",
    "ModelFile",
    "ModelManifest",
    "ModelSet",
    "ModelSnapshot",
    "SnapshotStatus",
    "binary_installed",
    "current_platform",
    "fetch_binary",
    "fetch_snapshot",
    "load_manifest",
    "snapshot_status",
    "urllib_download",
]

logger = logging.getLogger(__name__)

MANIFEST_SCHEMA = "paperextract.model-manifest"
MANIFEST_VERSION = 2
PARTIAL_DIRECTORY = ".partial"
BINARY_RECORD = ".paperextract-binary"
_CHUNK = 1 << 20
_TIMEOUT_SECONDS = 60.0
_SHA256_LENGTH = 64

Download = Callable[[str, Path], None]
SnapshotState = Literal["complete", "partial", "absent"]


@dataclass(frozen=True)
class ModelFile:
    """Describe one file of a model snapshot.

    Attributes
    ----------
    path : str
        Relative POSIX path inside the snapshot.
    bytes : int
        Size in bytes.
    sha256 : str
        Hexadecimal SHA-256.
    """

    path: str
    bytes: int
    sha256: str


@dataclass(frozen=True)
class ModelSnapshot:
    """Describe one pinned model snapshot.

    Attributes
    ----------
    name : str
        Short name used by sets.
    backend : str
        Model directory it belongs in: ``mineru``, ``docling``, ``marker``
        or ``describe``.
    directory : str
        Relative directory inside that backend's model directory.
    repository : str
        Source repository, for provenance.
    revision : str
        Immutable revision.
    url : str
        Base URL; a file is downloaded from ``<url>/<path>``.
    complete_marker : str or None
        Empty file written after every file verified, for backends that
        require one.
    license : str or None
        License declared by the model's publisher.
    license_status : str
        How the license was established.
    files : tuple of ModelFile
        Every file of the snapshot.
    """

    name: str
    backend: str
    directory: str
    repository: str
    revision: str
    url: str
    complete_marker: str | None
    license: str | None
    license_status: str
    files: tuple[ModelFile, ...]

    @property
    def total_bytes(self) -> int:
        """Return the size of the whole snapshot.

        Returns
        -------
        int
            Sum of the file sizes.
        """
        return sum(item.bytes for item in self.files)


@dataclass(frozen=True)
class Binary:
    """Describe one pinned binary archive.

    Attributes
    ----------
    name : str
        Name and release.
    platform : str
        Platform it runs on, such as ``darwin-arm64`` or ``linux-x86_64``.
    url : str
        Archive URL (a ``.tar.gz``).
    sha256 : str
        Hexadecimal SHA-256 of the archive.
    directory : str
        Directory, relative to the checkout's ``model-cache``, to unpack into.
    license : str or None
        License of the binary.
    """

    name: str
    platform: str
    url: str
    sha256: str
    directory: str
    license: str | None


@dataclass(frozen=True)
class ModelSet:
    """Name what one backend configuration needs.

    Attributes
    ----------
    name : str
        Set name, such as ``mineru`` or ``mineru-cuda``.
    description : str
        What the set is for.
    models : tuple of str
        Snapshot names.
    binaries : bool
        Whether the set also needs the platform's pinned binary.
    """

    name: str
    description: str
    models: tuple[str, ...]
    binaries: bool


@dataclass(frozen=True)
class ModelManifest:
    """Hold a parsed ``workers/models.json``.

    Attributes
    ----------
    sets : Mapping of str to ModelSet
        Sets by name, in manifest order.
    models : Mapping of str to ModelSnapshot
        Snapshots by name, in manifest order.
    binaries : tuple of Binary
        Pinned binaries.
    """

    sets: Mapping[str, ModelSet]
    models: Mapping[str, ModelSnapshot]
    binaries: tuple[Binary, ...]

    def binary_for(self, target: str) -> Binary | None:
        """Return the pinned binary for a platform.

        Parameters
        ----------
        target : str
            Platform such as ``linux-x86_64``.

        Returns
        -------
        Binary or None
            The binary, or None when none is pinned for the platform.
        """
        return next((b for b in self.binaries if b.platform == target), None)


def _relative(value: object, where: str) -> str:
    """Require a relative POSIX path that stays inside its directory.

    Parameters
    ----------
    value : object
        Decoded JSON value.
    where : str
        Location for the message.

    Returns
    -------
    str
        The path.

    Raises
    ------
    ValueError
        The path is absolute, empty or climbs out of its directory.
    """
    path = string(value)
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts or "\\" in path or not pure.parts:
        raise ValueError(f"{where}: unsafe path {path!r}")
    return path


def _digest(value: object, where: str) -> str:
    """Require a hexadecimal SHA-256.

    Parameters
    ----------
    value : object
        Decoded JSON value.
    where : str
        Location for the message.

    Returns
    -------
    str
        The digest.

    Raises
    ------
    ValueError
        The value is not 64 lowercase hexadecimal characters.
    """
    digest = string(value)
    if len(digest) != _SHA256_LENGTH or digest.strip("0123456789abcdef"):
        raise ValueError(f"{where}: not a SHA-256 digest")
    return digest


def _snapshot(value: object) -> ModelSnapshot:
    """Parse one snapshot entry.

    Parameters
    ----------
    value : object
        Decoded JSON value.

    Returns
    -------
    ModelSnapshot
        Parsed snapshot.
    """
    data = mapping(value)
    name = string(data["name"])
    source = mapping(data["source"])
    marker = data.get("complete_marker")
    license_name = data.get("license")
    files = tuple(
        ModelFile(
            path=_relative(entry["path"], name),
            bytes=integer(entry["bytes"], minimum=0),
            sha256=_digest(entry["sha256"], name),
        )
        for entry in (mapping(item) for item in items(data["files"]))
    )
    return ModelSnapshot(
        name=name,
        backend=string(data["backend"]),
        directory=_relative(data["directory"], name),
        repository=string(source["repository"]),
        revision=string(source["revision"]),
        url=string(data["url"]).rstrip("/"),
        complete_marker=None if marker is None else _relative(marker, name),
        license=None if license_name is None else text(license_name),
        license_status=string(data["license_status"]),
        files=files,
    )


def load_manifest(path: Path) -> ModelManifest:
    """Read and validate a model manifest.

    Parameters
    ----------
    path : Path
        ``workers/models.json``.

    Returns
    -------
    ModelManifest
        Parsed manifest.

    Raises
    ------
    ValueError
        The file has another schema or version, a path is unsafe, a digest
        is malformed, or a set names an unknown snapshot.
    """
    data = mapping(json.loads(path.read_text(encoding="utf-8")))
    if (data.get("schema"), data.get("schema_version")) != (
        MANIFEST_SCHEMA,
        MANIFEST_VERSION,
    ):
        raise ValueError(
            f"{path} is not a {MANIFEST_SCHEMA} version {MANIFEST_VERSION} manifest"
        )
    models = {s.name: s for s in (_snapshot(item) for item in items(data["models"]))}
    sets: dict[str, ModelSet] = {}
    for name, value in mapping(data["sets"]).items():
        entry = mapping(value)
        members = tuple(string(item) for item in items(entry["models"]))
        unknown = [member for member in members if member not in models]
        if unknown:
            raise ValueError(f"Set {name} names unknown models: {unknown}")
        sets[name] = ModelSet(
            name, text(entry["description"]), members, boolean(entry["binaries"])
        )
    binaries = tuple(
        Binary(
            name=string(entry["name"]),
            platform=string(entry["platform"]),
            url=string(entry["url"]),
            sha256=_digest(entry["sha256"], string(entry["name"])),
            directory=_relative(entry["directory"], string(entry["name"])),
            license=None if entry.get("license") is None else text(entry["license"]),
        )
        for entry in (mapping(item) for item in items(data["binaries"]))
    )
    return ModelManifest(sets, models, binaries)


def current_platform() -> str:
    """Name this machine's platform as the manifest does.

    Returns
    -------
    str
        Such as ``darwin-arm64`` or ``linux-x86_64``.
    """
    return f"{platform.system().lower()}-{platform.machine().lower()}"


def _sha256(path: Path) -> str:
    """Hash a file.

    Parameters
    ----------
    path : Path
        File.

    Returns
    -------
    str
        Hexadecimal SHA-256.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class SnapshotStatus:
    """Report how much of a snapshot is present.

    Attributes
    ----------
    name : str
        Snapshot name.
    target : Path
        Directory checked.
    missing : tuple of str
        Files absent, or of the wrong size (or digest, when verified).
    missing_bytes : int
        Bytes still to download.
    marker_missing : bool
        The snapshot needs a completion marker that is not there.
    total : int
        Number of files in the snapshot.
    """

    name: str
    target: Path
    missing: tuple[str, ...]
    missing_bytes: int
    marker_missing: bool
    total: int

    @property
    def state(self) -> SnapshotState:
        """Summarize the snapshot.

        Returns
        -------
        str
            ``complete``, ``absent`` when no file is usable, else ``partial``.
        """
        if not self.missing and not self.marker_missing:
            return "complete"
        return "absent" if len(self.missing) == self.total else "partial"


def _usable(path: Path, item: ModelFile, *, verify: bool) -> bool:
    """Decide whether a file on disk is the recorded one.

    Parameters
    ----------
    path : Path
        File on disk.
    item : ModelFile
        Recorded file.
    verify : bool
        Compare the digest as well as the size.

    Returns
    -------
    bool
        Whether the file can be kept.
    """
    if not path.is_file() or path.stat().st_size != item.bytes:
        return False
    return not verify or _sha256(path) == item.sha256


def snapshot_status(
    snapshot: ModelSnapshot, target: Path, *, verify: bool = False
) -> SnapshotStatus:
    """Check which files of a snapshot a directory holds.

    Parameters
    ----------
    snapshot : ModelSnapshot
        Snapshot to check.
    target : Path
        Directory the snapshot belongs in.
    verify : bool
        Hash every file instead of comparing sizes only.

    Returns
    -------
    SnapshotStatus
        Missing files and bytes.
    """
    missing = [
        item
        for item in snapshot.files
        if not _usable(target / item.path, item, verify=verify)
    ]
    marker = snapshot.complete_marker
    return SnapshotStatus(
        name=snapshot.name,
        target=target,
        missing=tuple(item.path for item in missing),
        missing_bytes=sum(item.bytes for item in missing),
        marker_missing=marker is not None and not (target / marker).is_file(),
        total=len(snapshot.files),
    )


def urllib_download(url: str, path: Path) -> None:
    """Download a URL to a file with the standard library.

    Parameters
    ----------
    url : str
        HTTPS URL.
    path : Path
        File to write; its directory must exist.

    Raises
    ------
    ValueError
        The URL is not HTTPS.
    """
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ValueError(f"Refusing a non-HTTPS download: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "paperextract"})
    with (
        urllib.request.urlopen(
            request, timeout=_TIMEOUT_SECONDS
        ) as response,  # scheme checked above
        path.open("wb") as handle,
    ):
        shutil.copyfileobj(response, handle, _CHUNK)


def _fetch_file(url: str, target: Path, item: ModelFile, download: Download) -> None:
    """Download one file, verify it and move it into place.

    Parameters
    ----------
    url : str
        File URL.
    target : Path
        Final path.
    item : ModelFile
        Recorded size and digest.
    download : Download
        Function that writes a URL to a path.

    Raises
    ------
    ValueError
        The downloaded file has another size or digest; it is removed.
    """
    partial = target.parent / PARTIAL_DIRECTORY / target.name
    partial.parent.mkdir(parents=True, exist_ok=True)
    download(url, partial)
    size = partial.stat().st_size
    if size != item.bytes or _sha256(partial) != item.sha256:
        partial.unlink()
        raise ValueError(
            f"{url}: downloaded {size} bytes that do not match the recorded "
            f"{item.bytes} bytes and SHA-256"
        )
    partial.replace(target)
    if not any(partial.parent.iterdir()):
        partial.parent.rmdir()


def fetch_snapshot(
    snapshot: ModelSnapshot, target: Path, download: Download = urllib_download
) -> int:
    """Download the missing files of a snapshot into a directory.

    Parameters
    ----------
    snapshot : ModelSnapshot
        Snapshot to fetch.
    target : Path
        Directory the snapshot belongs in; created when missing.
    download : Download
        Function that writes a URL to a path.

    Returns
    -------
    int
        Number of files downloaded; files already present with the recorded
        size are kept.

    Raises
    ------
    ValueError
        A downloaded file does not match the manifest.
    """
    fetched = 0
    for item in snapshot.files:
        path = target / item.path
        if _usable(path, item, verify=False):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"{snapshot.url}/{urllib.parse.quote(item.path)}"
        logger.info(
            "Downloading %s/%s (%d bytes)", snapshot.name, item.path, item.bytes
        )
        _fetch_file(url, path, item, download)
        fetched += 1
    if snapshot.complete_marker is not None:
        (target / snapshot.complete_marker).touch()
    return fetched


def binary_installed(binary: Binary, target: Path) -> bool:
    """Tell whether a binary archive was unpacked into a directory.

    Parameters
    ----------
    binary : Binary
        Pinned binary.
    target : Path
        Directory it is unpacked into.

    Returns
    -------
    bool
        Whether the directory records this archive's digest.
    """
    record = target / BINARY_RECORD
    return record.is_file() and record.read_text().strip() == binary.sha256


def fetch_binary(
    binary: Binary, target: Path, download: Download = urllib_download
) -> bool:
    """Download, verify and unpack a pinned binary archive.

    Parameters
    ----------
    binary : Binary
        Pinned binary.
    target : Path
        Directory to unpack into; created when missing.
    download : Download
        Function that writes a URL to a path.

    Returns
    -------
    bool
        Whether the archive was downloaded; False when already unpacked.

    Raises
    ------
    ValueError
        The archive does not match its digest.
    """
    if binary_installed(binary, target):
        return False
    target.mkdir(parents=True, exist_ok=True)
    archive = target / PARTIAL_DIRECTORY / PurePosixPath(binary.url).name
    archive.parent.mkdir(exist_ok=True)
    logger.info("Downloading %s for %s", binary.name, binary.platform)
    download(binary.url, archive)
    if _sha256(archive) != binary.sha256:
        archive.unlink()
        raise ValueError(f"{binary.url}: the archive does not match its SHA-256")
    with tarfile.open(archive) as bundle:
        # The data filter refuses absolute paths, links out of the directory
        # and device files, so an archive cannot write outside the target.
        bundle.extractall(target, filter="data")
    shutil.rmtree(archive.parent)
    (target / BINARY_RECORD).write_text(binary.sha256 + "\n")
    return True
