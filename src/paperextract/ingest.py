"""Preserve PDF bytes and plan a batch intake before any extraction runs."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from paperextract.pdf import ContentFingerprint, UnreadablePdfError, fingerprint_pdf

__all__ = [
    "DEDUP_REPORT_SCHEMA",
    "DEDUP_REPORT_VERSION",
    "INTAKE_PLAN_SCHEMA",
    "INTAKE_PLAN_VERSION",
    "MIN_EQUIVALENT_CHARACTERS",
    "MIN_PAIRING_PREFIX",
    "CapturePairing",
    "Equivalence",
    "IntakeFile",
    "IntakePlan",
    "RejectedFile",
    "SourceArtifact",
    "SourceChangedError",
    "SourceGroup",
    "SupplementPairing",
    "content_equivalents",
    "content_key",
    "dedup_report",
    "discover_pdfs",
    "looks_like_supplement",
    "pair_captures",
    "pair_supplements",
    "plan_intake",
    "preserve_pdf",
    "shared_doi_candidates",
]

_COPY_CHUNK_BYTES = 1024 * 1024
_PDF_SIGNATURE = b"%PDF-"
INTAKE_PLAN_SCHEMA = "paperextract.intake-plan"
INTAKE_PLAN_VERSION = 1


class SourceChangedError(RuntimeError):
    """Report a source changing while its preservation copy is being verified."""


@dataclass(frozen=True)
class SourceArtifact:
    """Describe a verified source copy without inferred scientific identity.

    Attributes
    ----------
    sha256 : str
        Digest of the copied bytes, verified against a second source read.
    size_bytes : int
        Number of bytes preserved.
    original_name : str
        User-supplied source basename, without its private parent path.
    stored_path : Path
        Caller-selected local staging destination; not a portable export locator.
    """

    sha256: str
    size_bytes: int
    original_name: str
    stored_path: Path


def _fingerprint(status: os.stat_result) -> tuple[int, int, int, int, int]:
    """Compare file identity and high-resolution metadata across source reads.

    Parameters
    ----------
    status : os.stat_result
        Metadata from an open descriptor or its named filesystem entry.

    Returns
    -------
    tuple of int
        Device, inode, byte size, modification time and metadata change time.
    """
    return (
        status.st_dev,
        status.st_ino,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
    )


def preserve_pdf(source: Path, destination: Path) -> SourceArtifact:
    """Copy and verify PDF bytes into a new private staging file.

    Parameters
    ----------
    source : Path
        Original PDF. Its bytes are never rewritten or linked into the copy.
    destination : Path
        New file in an existing caller-owned private staging directory. Existing
        files, directories and symlinks are never overwritten.

    Returns
    -------
    SourceArtifact
        Verified hash, size, original basename and staging destination.

    Raises
    ------
    ValueError
        The source does not start with a PDF signature.
    SourceChangedError
        Source identity, metadata or content changes during preservation.
    OSError
        An input cannot be read, the destination exists or copying fails.

    Notes
    -----
    This is an ingest primitive, not atomic publication of a paper directory.
    The destination is uncommitted staging until this call returns. On failure
    or interruption its newly created partial file is removed. The caller must
    keep staging private and prevent concurrent replacement of that file.
    Abrupt process termination can leave staging to be recovered by its owner.

    Signature checking is not PDF validation; parsing/encryption/page checks belong
    to the worker. A second source read plus descriptor/path metadata detects
    ordinary concurrent edits and replacements, without claiming a filesystem lock.
    The preserved copy is independent of later modifications to the original.
    """
    created = False
    with source.open("rb") as original:
        before = _fingerprint(os.fstat(original.fileno()))
        if original.read(5) != b"%PDF-":
            raise ValueError("Source does not start with a PDF signature")
        original.seek(0)
        try:
            digest = hashlib.sha256()
            size_bytes = 0
            with destination.open("xb") as preserved:
                created = True
                while chunk := original.read(_COPY_CHUNK_BYTES):
                    preserved.write(chunk)
                    digest.update(chunk)
                    size_bytes += len(chunk)
                preserved.flush()
                os.fsync(preserved.fileno())
            original.seek(0)
            checked = hashlib.file_digest(original, "sha256").hexdigest()
            descriptor_after = _fingerprint(os.fstat(original.fileno()))
            path_after = _fingerprint(source.stat())
            if (
                before != descriptor_after
                or before != path_after
                or digest.hexdigest() != checked
            ):
                raise SourceChangedError(
                    f"Source changed during preservation: {source}"
                )
            return SourceArtifact(
                sha256=checked,
                size_bytes=size_bytes,
                original_name=source.name,
                stored_path=destination,
            )
        except BaseException:
            # Cleanup must also run on cancellation; the original exception propagates.
            if created:
                destination.unlink()
            raise


@dataclass(frozen=True)
class IntakeFile:
    """Record one supplied file exactly as found.

    Attributes
    ----------
    path : Path
        Absolute path as supplied, without resolving symlinks.
    size_bytes : int
        File size at hashing time.
    sha256 : str
        Digest of the file bytes.
    modified_ns : int
        Modification time in nanoseconds, kept as provenance only.
    """

    path: Path
    size_bytes: int
    sha256: str
    modified_ns: int

    def to_dict(self) -> dict[str, object]:
        """Serialize the record.

        Returns
        -------
        dict of str to object
            JSON-compatible fields.
        """
        return {
            "path": str(self.path),
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "modified_ns": self.modified_ns,
        }


@dataclass(frozen=True)
class RejectedFile:
    """Record a supplied path that cannot enter extraction, with the reason.

    Attributes
    ----------
    path : Path
        Absolute path as supplied.
    reason : str
        Actionable explanation, such as a missing PDF signature.
    """

    path: Path
    reason: str

    def to_dict(self) -> dict[str, object]:
        """Serialize the record.

        Returns
        -------
        dict of str to object
            JSON-compatible fields.
        """
        return {"path": str(self.path), "reason": self.reason}


@dataclass(frozen=True)
class SourceGroup:
    """Group every supplied copy of one byte-identical source.

    Attributes
    ----------
    sha256 : str
        Shared digest of all members.
    primary : IntakeFile
        Deterministically chosen member that represents the group: the first
        path in lexicographic order.
    aliases : tuple of IntakeFile
        Further copies under other names; recorded, never extracted separately.
    known_as : str or None
        Reference under which a library already holds this digest, if any.
    fingerprint : ContentFingerprint or None
        Text-layer fingerprint of the primary, when computed.
    """

    sha256: str
    primary: IntakeFile
    aliases: tuple[IntakeFile, ...]
    known_as: str | None
    fingerprint: ContentFingerprint | None

    @property
    def action(self) -> str:
        """Name the automatic action for this group.

        Returns
        -------
        str
            ``reuse`` when a library already holds the digest, else ``extract``.
        """
        return "extract" if self.known_as is None else "reuse"

    def to_dict(self) -> dict[str, object]:
        """Serialize the group.

        Returns
        -------
        dict of str to object
            JSON-compatible fields including the action.
        """
        return {
            "sha256": self.sha256,
            "action": self.action,
            "known_as": self.known_as,
            "primary": self.primary.to_dict(),
            "aliases": [item.to_dict() for item in self.aliases],
            "fingerprint": None
            if self.fingerprint is None
            else self.fingerprint.to_dict(),
        }


@dataclass(frozen=True)
class IntakePlan:
    """Describe what a batch contains before any model runs.

    Attributes
    ----------
    groups : tuple of SourceGroup
        Distinct sources in primary-path order.
    rejected : tuple of RejectedFile
        Paths excluded from extraction, each with its reason.
    """

    groups: tuple[SourceGroup, ...]
    rejected: tuple[RejectedFile, ...]

    def to_extract(self) -> tuple[SourceGroup, ...]:
        """Select the groups that need extraction.

        Returns
        -------
        tuple of SourceGroup
            Groups without a known library reference.
        """
        return tuple(group for group in self.groups if group.known_as is None)

    def to_dict(self) -> dict[str, object]:
        """Serialize the plan with a summary and its schema version.

        Returns
        -------
        dict of str to object
            JSON-compatible plan document.
        """
        aliases = sum(len(group.aliases) for group in self.groups)
        return {
            "schema": INTAKE_PLAN_SCHEMA,
            "schema_version": INTAKE_PLAN_VERSION,
            "summary": {
                "files": aliases + len(self.groups) + len(self.rejected),
                "groups": len(self.groups),
                "extract": len(self.to_extract()),
                "reuse": len(self.groups) - len(self.to_extract()),
                "aliases": aliases,
                "rejected": len(self.rejected),
            },
            "groups": [group.to_dict() for group in self.groups],
            "rejected": [item.to_dict() for item in self.rejected],
        }

    def to_json(self) -> str:
        """Serialize the plan as stable JSON.

        Returns
        -------
        str
            Indented JSON with sorted keys and a trailing newline.
        """
        return (
            json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)
            + "\n"
        )


def discover_pdfs(directory: Path) -> tuple[Path, ...]:
    """List the PDF files directly inside a batch directory.

    Parameters
    ----------
    directory : Path
        Existing directory; subdirectories are not searched because a child
        directory may be a multi-source bundle, not a stray paper.

    Returns
    -------
    tuple of Path
        Absolute paths with a ``.pdf`` suffix in any letter case, sorted by name.

    Raises
    ------
    NotADirectoryError
        The path is not an existing directory.
    """
    if not directory.is_dir():
        raise NotADirectoryError(f"Not a directory: {directory}")
    return tuple(
        sorted(
            path.absolute()
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() == ".pdf"
        )
    )


def _hash_file(path: Path) -> IntakeFile | RejectedFile:
    """Hash one supplied path or explain why it cannot be taken in.

    Parameters
    ----------
    path : Path
        Supplied path.

    Returns
    -------
    IntakeFile or RejectedFile
        Hashed record, or the rejection reason.
    """
    if not path.is_file():
        return RejectedFile(path, "not a regular file")
    with path.open("rb") as handle:
        if handle.read(len(_PDF_SIGNATURE)) != _PDF_SIGNATURE:
            return RejectedFile(path, "no PDF signature at the start of the file")
        handle.seek(0)
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
        status = os.fstat(handle.fileno())
    return IntakeFile(path, status.st_size, digest, status.st_mtime_ns)


def plan_intake(
    paths: Sequence[Path],
    *,
    known: Mapping[str, str] | None = None,
    fingerprint: bool = True,
) -> IntakePlan:
    """Hash a batch, group identical files and fingerprint each distinct source.

    Parameters
    ----------
    paths : Sequence of Path
        Candidate files; relative paths are made absolute, and a path supplied
        twice counts once.
    known : Mapping of str to str or None
        Digests a library already holds, mapped to the reference recorded there.
        The library catalog supplies this once it exists.
    fingerprint : bool
        Whether to compute text fingerprints for group primaries.

    Returns
    -------
    IntakePlan
        Groups in primary-path order plus rejected paths.

    Notes
    -----
    This is the first duplicate-handling tier: identical bytes are never
    extracted twice, and every extra name is kept as an alias in provenance.
    Nothing is copied, moved or deleted. A group whose primary PDFium cannot
    read is rejected as a whole, because the alias bytes are identical.
    """
    unique: dict[Path, None] = {}
    for path in paths:
        unique.setdefault(path.absolute(), None)
    rejected: list[RejectedFile] = []
    members: dict[str, list[IntakeFile]] = {}
    for path in unique:
        record = _hash_file(path)
        if isinstance(record, RejectedFile):
            rejected.append(record)
        else:
            members.setdefault(record.sha256, []).append(record)
    groups: list[SourceGroup] = []
    for digest, files in members.items():
        ordered = sorted(files, key=lambda item: str(item.path))
        primary = ordered[0]
        content: ContentFingerprint | None = None
        if fingerprint:
            try:
                content = fingerprint_pdf(primary.path)
            except UnreadablePdfError as exc:
                rejected.extend(RejectedFile(item.path, str(exc)) for item in ordered)
                continue
        groups.append(
            SourceGroup(
                sha256=digest,
                primary=primary,
                aliases=tuple(ordered[1:]),
                known_as=None if known is None else known.get(digest),
                fingerprint=content,
            )
        )
    groups.sort(key=lambda group: str(group.primary.path))
    rejected.sort(key=lambda item: str(item.path))
    return IntakePlan(groups=tuple(groups), rejected=tuple(rejected))


# Shorter texts, such as a lone cover page or a scan's stray OCR line, are too
# weak to call two different files the same document.
MIN_EQUIVALENT_CHARACTERS = 200
DEDUP_REPORT_SCHEMA = "paperextract.dedup-report"
DEDUP_REPORT_VERSION = 1


@dataclass(frozen=True)
class Equivalence:
    """Record that a source's text is identical to an earlier source's text.

    Attributes
    ----------
    sha256 : str
        Digest of the group that is held back.
    text_sha256 : str
        Shared digest of the normalized text of every page.
    equivalent_to : str
        The earlier source: an intake primary path or a library directory.
    in_library : bool
        Whether ``equivalent_to`` names a library directory.
    """

    sha256: str
    text_sha256: str
    equivalent_to: str
    in_library: bool

    def to_dict(self) -> dict[str, object]:
        """Serialize the record.

        Returns
        -------
        dict of str to object
            JSON-compatible fields.
        """
        return {
            "sha256": self.sha256,
            "text_sha256": self.text_sha256,
            "equivalent_to": self.equivalent_to,
            "in_library": self.in_library,
        }


def content_key(fingerprint: ContentFingerprint | None) -> str | None:
    """Return the digest that identifies content-equivalent files, if usable.

    Parameters
    ----------
    fingerprint : ContentFingerprint or None
        Text fingerprint of a source.

    Returns
    -------
    str or None
        The whole-text digest, which covers the page count and every page's
        normalized text, or None when the text layer is too short to compare.
    """
    if fingerprint is None:
        return None
    characters = sum(page.characters for page in fingerprint.pages)
    return fingerprint.text_sha256 if characters >= MIN_EQUIVALENT_CHARACTERS else None


def content_equivalents(
    plan: IntakePlan, known_text: Mapping[str, str] | None = None
) -> tuple[Equivalence, ...]:
    """Find groups whose text matches an earlier group or a library source.

    Parameters
    ----------
    plan : IntakePlan
        Intake plan with fingerprints.
    known_text : Mapping of str to str or None
        Whole-text digests a library already holds, mapped to the directory.

    Returns
    -------
    tuple of Equivalence
        One record per extractable group to hold back, in plan order.

    Notes
    -----
    This is the second duplicate tier of Plan §15: different bytes, identical
    page count and per-page text. It is decided on the text layer alone, so a
    re-optimized download of one article matches, while an issue with an extra
    cover page does not. Groups already known by bytes are not repeated here.
    """
    first: dict[str, str] = dict(known_text or {})
    library = set(first)
    found: list[Equivalence] = []
    for group in plan.to_extract():
        key = content_key(group.fingerprint)
        if key is None:
            continue
        if key in first:
            found.append(Equivalence(group.sha256, key, first[key], key in library))
        else:
            first[key] = str(group.primary.path)
    return tuple(found)


def shared_doi_candidates(
    plan: IntakePlan, held: Collection[str] = ()
) -> dict[str, tuple[str, ...]]:
    """Group distinct sources whose first DOI candidate is the same string.

    Parameters
    ----------
    plan : IntakePlan
        Intake plan with fingerprints.
    held : Collection of str
        Group digests already held back as content-equivalent, which are left
        out because their relationship is known more precisely.

    Returns
    -------
    dict of str to tuple of str
        Lower-cased DOI mapped to the primary paths of two or more groups.

    Notes
    -----
    A shared DOI never proves identical content: the pair may be a version of
    record beside an accepted manuscript, or a reference DOI read from page
    text. The result is for review; both sources are still extracted.
    """
    holders: dict[str, list[str]] = {}
    for group in plan.groups:
        if group.sha256 in held:
            continue
        if group.fingerprint is not None and group.fingerprint.doi_candidates:
            doi = group.fingerprint.doi_candidates[0].lower()
            holders.setdefault(doi, []).append(str(group.primary.path))
    return {doi: tuple(paths) for doi, paths in holders.items() if len(paths) > 1}


def dedup_report(
    plan: IntakePlan, known_text: Mapping[str, str] | None = None
) -> dict[str, object]:
    """Combine the intake plan with the content, supplement and identifier tiers.

    Parameters
    ----------
    plan : IntakePlan
        Intake plan with fingerprints.
    known_text : Mapping of str to str or None
        Whole-text digests a library already holds, mapped to the directory.

    Returns
    -------
    dict of str to object
        Versioned report with the plan, held content-equivalent groups,
        supplement pairings and shared DOI candidates of distinct papers.
    """
    equivalents = content_equivalents(plan, known_text)
    held = {item.sha256 for item in equivalents}
    pairing = pair_supplements(plan, exclude=held)
    supplements = pairing.supplement_digests()
    shared = shared_doi_candidates(plan, held | supplements)
    paths = {group.sha256: str(group.primary.path) for group in plan.groups}
    extractable = {group.sha256 for group in plan.to_extract()}
    return {
        "schema": DEDUP_REPORT_SCHEMA,
        "schema_version": DEDUP_REPORT_VERSION,
        "summary": {
            "content_equivalent": len(equivalents),
            "supplements": len(supplements),
            "unpaired_supplements": len(pairing.unpaired),
            "shared_doi_candidates": len(shared),
            "to_extract": len(extractable - held - supplements),
        },
        "plan": plan.to_dict(),
        "content_equivalent": [item.to_dict() for item in equivalents],
        "supplements": [
            {
                "paper": paths[paper],
                "supplement": str(member.primary.path),
                "evidence": pairing.reasons[member.sha256],
            }
            for paper, members in sorted(pairing.pairs.items())
            for member in members
        ],
        "unpaired_supplements": [
            {"path": str(group.primary.path), "evidence": pairing.reasons[group.sha256]}
            for group in pairing.unpaired
        ],
        "shared_doi_candidates": [
            {"doi": doi, "paths": list(paths_)}
            for doi, paths_ in sorted(shared.items())
        ],
    }


_SUPPLEMENT_TOKENS = frozenset({"supp", "suppl", "si", "esm", "supporting"})
_TOKEN = re.compile(r"[^a-z0-9]+")
# A shared file-name start shorter than this is not evidence of a pairing.
MIN_PAIRING_PREFIX = 10


@dataclass(frozen=True)
class SupplementPairing:
    """Relate supplement files in a batch to the papers they belong to.

    Attributes
    ----------
    pairs : Mapping of str to tuple of SourceGroup
        Paper digest to its supplement groups, in file-name order.
    unpaired : tuple of SourceGroup
        Supplement-like groups without a unique paper in the batch.
    reasons : Mapping of str to str
        Supplement digest to the evidence for its pairing.
    """

    pairs: Mapping[str, tuple[SourceGroup, ...]]
    unpaired: tuple[SourceGroup, ...]
    reasons: Mapping[str, str]

    def supplement_digests(self) -> set[str]:
        """Return every group digest that is treated as a supplement.

        Returns
        -------
        set of str
            Paired and unpaired supplement digests.
        """
        paired = {group.sha256 for groups in self.pairs.values() for group in groups}
        return paired | {group.sha256 for group in self.unpaired}


def looks_like_supplement(path: Path) -> bool:
    """Tell from a file name whether a PDF is supplementary material.

    Parameters
    ----------
    path : Path
        Supplied file.

    Returns
    -------
    bool
        True when a name token is ``supp``, ``suppl``, ``si``, ``esm`` or
        ``supporting``, or starts with ``supplement``.

    Examples
    --------
    >>> looks_like_supplement(Path("travers_2019_soliton2_supplement.pdf"))
    True
    >>> looks_like_supplement(Path("silicon_photonics.pdf"))
    False
    """
    tokens = [token for token in _TOKEN.split(path.stem.lower()) if token]
    return any(
        token in _SUPPLEMENT_TOKENS or token.startswith("supplement")
        for token in tokens
    )


def _common_prefix(first: str, second: str) -> int:
    """Measure the shared start of two file-name stems.

    Parameters
    ----------
    first : str
        Stem.
    second : str
        Stem.

    Returns
    -------
    int
        Number of equal leading characters, ignoring case.
    """
    count = 0
    for left, right in zip(first.lower(), second.lower(), strict=False):
        if left != right:
            break
        count += 1
    return count


def _dois(group: SourceGroup) -> set[str]:
    """Collect a group's DOI candidates in lower case.

    Parameters
    ----------
    group : SourceGroup
        Source group.

    Returns
    -------
    set of str
        Candidates; empty without a fingerprint.
    """
    if group.fingerprint is None:
        return set()
    return {doi.lower() for doi in group.fingerprint.doi_candidates}


def _paper_for(
    supplement: SourceGroup, papers: Sequence[SourceGroup]
) -> tuple[SourceGroup | None, str]:
    """Choose the paper a supplement belongs to.

    Parameters
    ----------
    supplement : SourceGroup
        Supplement-like group.
    papers : Sequence of SourceGroup
        Candidate paper groups.

    Returns
    -------
    tuple
        The unique paper sharing a DOI candidate, else the unique paper with
        the longest shared file-name start of at least
        :data:`MIN_PAIRING_PREFIX` characters, else None; and the evidence.
    """
    dois = _dois(supplement)
    sharing = [paper for paper in papers if dois & _dois(paper)]
    if len(sharing) == 1:
        return sharing[0], "shares a DOI candidate"
    stem = supplement.primary.path.stem
    scored = sorted(
        ((_common_prefix(stem, paper.primary.path.stem), paper) for paper in papers),
        key=lambda item: -item[0],
    )
    if (
        scored
        and scored[0][0] >= MIN_PAIRING_PREFIX
        and (len(scored) == 1 or scored[1][0] < scored[0][0])
    ):
        return scored[0][1], f"shares the first {scored[0][0]} file-name characters"
    return None, "no unique paper"


@dataclass(frozen=True)
class CapturePairing:
    """Pair saved web pages of a batch with the papers they show.

    Attributes
    ----------
    pairs : Mapping of str to tuple of Path
        Paper group digest to its captures.
    unpaired : Mapping of Path to str
        Captures without a unique paper, with the reason.
    """

    pairs: Mapping[str, tuple[Path, ...]]
    unpaired: Mapping[Path, str]


def pair_captures(
    plan: IntakePlan, captures: Sequence[tuple[Path, str | None]]
) -> CapturePairing:
    """Pair saved web pages with the papers whose DOI they declare.

    Parameters
    ----------
    plan : IntakePlan
        Intake plan with fingerprints.
    captures : Sequence of tuple
        Capture path and the DOI its page declares, or None.

    Returns
    -------
    CapturePairing
        A capture pairs with the unique paper group, library paper or new,
        whose DOI candidates contain the page's DOI; supplements are not
        candidates.
    """
    papers = [g for g in plan.groups if not looks_like_supplement(g.primary.path)]
    pairs: dict[str, list[Path]] = {}
    unpaired: dict[Path, str] = {}
    for path, doi in captures:
        if doi is None:
            unpaired[path] = "the page declares no DOI"
            continue
        sharing = [paper for paper in papers if doi.lower() in _dois(paper)]
        if len(sharing) != 1:
            unpaired[path] = (
                f"no PDF in this batch prints DOI {doi}"
                if not sharing
                else f"{len(sharing)} PDFs print DOI {doi}"
            )
            continue
        pairs.setdefault(sharing[0].sha256, []).append(path)
    return CapturePairing(
        pairs={key: tuple(value) for key, value in pairs.items()}, unpaired=unpaired
    )


def pair_supplements(
    plan: IntakePlan, exclude: Collection[str] = ()
) -> SupplementPairing:
    """Pair supplement files of a batch with their papers.

    Parameters
    ----------
    plan : IntakePlan
        Intake plan with fingerprints.
    exclude : Collection of str
        Group digests that take no part, such as copies held for identical
        text.

    Returns
    -------
    SupplementPairing
        Pairs and unpaired supplements. A file is a supplement only when its
        name says so; the paper may be a new group or one the library holds.
    """
    groups = [group for group in plan.groups if group.sha256 not in exclude]
    supplements = [g for g in groups if looks_like_supplement(g.primary.path)]
    papers = [g for g in groups if not looks_like_supplement(g.primary.path)]
    pairs: dict[str, list[SourceGroup]] = {}
    reasons: dict[str, str] = {}
    unpaired: list[SourceGroup] = []
    for supplement in supplements:
        paper, reason = _paper_for(supplement, papers)
        reasons[supplement.sha256] = reason
        if paper is None:
            unpaired.append(supplement)
        else:
            pairs.setdefault(paper.sha256, []).append(supplement)
    return SupplementPairing(
        pairs={key: tuple(value) for key, value in pairs.items()},
        unpaired=tuple(unpaired),
        reasons=reasons,
    )
