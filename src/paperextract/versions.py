"""Decide a document's version and relate it to papers already in the library.

A scholarly work can be held in a library as several documents: the version
of record, an accepted manuscript, an arXiv preprint. They share a DOI or a
title but not their text, so they are related, never merged. The version is
decided conservatively from evidence in the file and its identity, and stays
``unknown`` without it; a user's assertion is recorded as an assertion.

Relations follow the duplicate tiers of the plan: a paper with the same DOI
is another document of the same work (tier 3), and one with the same
normalized title, first-author family name and year but no shared DOI is a
possible duplicate for review (tier 4). Neither relation changes a paper.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal, cast

from paperextract.document import Document, Finding, Heading, PageFurniture, Paragraph
from paperextract.export import plausible_title
from paperextract.identity import Identity, title_key
from paperextract.ingest import IntakePlan

__all__ = [
    "VERSIONS",
    "Relation",
    "TitleMatch",
    "VersionDecision",
    "decide_version",
    "first_author_family",
    "intake_title_matches",
    "library_relations",
    "relation_findings",
]

Version = Literal[
    "version_of_record",
    "accepted_manuscript",
    "submitted_manuscript",
    "preprint",
    "unknown",
]
VERSIONS: tuple[str, ...] = (
    "version_of_record",
    "accepted_manuscript",
    "submitted_manuscript",
    "preprint",
    "unknown",
)
_MARKERS: tuple[tuple[Version, re.Pattern[str]], ...] = (
    (
        "accepted_manuscript",
        re.compile(
            r"\b(?:author'?s?\s+)?accepted\s+manuscript\b|\baccepted\s+author\s+"
            r"(?:manuscript|version)\b|\bpost-?print\b|\bauthor'?s?\s+final\s+"
            r"(?:version|manuscript)\b",
            re.I,
        ),
    ),
    (
        "submitted_manuscript",
        re.compile(r"\bsubmitted\s+to\b|\bunder\s+review\b", re.I),
    ),
    ("preprint", re.compile(r"\bpre-?print\b", re.I)),
)
_FIRST_PAGES = 2
# File names that say what a PDF is, such as "Travers_HISOL_2018_AAM.pdf".
_FILE_MARKERS: tuple[tuple[Version, re.Pattern[str]], ...] = (
    (
        "accepted_manuscript",
        re.compile(r"(?:^|[^a-z])(?:aam|accepted|post-?print)(?:[^a-z]|$)", re.I),
    ),
    ("submitted_manuscript", re.compile(r"(?:^|[^a-z])submitted(?:[^a-z]|$)", re.I)),
    ("preprint", re.compile(r"(?:^|[^a-z])pre-?print(?:[^a-z]|$)", re.I)),
)


@dataclass(frozen=True)
class VersionDecision:
    """Hold the version of a document with its evidence.

    Attributes
    ----------
    version : str
        One of :data:`VERSIONS`.
    evidence : tuple of str
        Why; empty for ``unknown`` without any signal.
    asserted : bool
        Whether the user asserted the version rather than the file showing it.
    """

    version: Version
    evidence: tuple[str, ...]
    asserted: bool = False

    def to_dict(self) -> dict[str, object]:
        """Serialize the decision.

        Returns
        -------
        dict of str to object
            JSON-compatible fields.
        """
        return {
            "version": self.version,
            "evidence": list(self.evidence),
            "asserted": self.asserted,
        }


def _front_text(document: Document) -> str:
    """Join the text of the first pages.

    Parameters
    ----------
    document : Document
        Extraction.

    Returns
    -------
    str
        Headings, paragraphs and running headers of the first two pages.
    """
    parts: list[str] = []
    for block in document.blocks:
        if (
            isinstance(block, Heading | Paragraph | PageFurniture)
            and block.span.page <= _FIRST_PAGES
        ):
            parts.append("".join(run.text for run in block.runs))
    return " ".join(parts)


def decide_version(
    document: Document,
    identity: Identity,
    *,
    hint: str | None = None,
    arxiv: str | None = None,
    file_name: str | None = None,
) -> VersionDecision:
    """Decide which version of a work a document is.

    Parameters
    ----------
    document : Document
        Extraction.
    identity : Identity
        Bibliographic identity.
    hint : str or None
        Version asserted by the user, one of :data:`VERSIONS`.
    arxiv : str or None
        The arXiv identifier when the identity came from arXiv or the file was
        downloaded from it.
    file_name : str or None
        The name the user gave the file, which may say ``AAM``,
        ``accepted``, ``postprint``, ``submitted`` or ``preprint``.

    Returns
    -------
    VersionDecision
        A user assertion wins and is labelled as one. Otherwise a manuscript
        marker on the first pages ("accepted manuscript", "submitted to",
        "preprint") decides, then an arXiv identity, then a file name that
        says the version; a validated DOI that the
        file prints in its metadata or running text, with the journal named
        on the first pages, is the version of record. Anything else is
        ``unknown``.

    Raises
    ------
    ValueError
        The hint is not a known version.
    """
    if hint is not None:
        if hint not in VERSIONS:
            raise ValueError(f"Unknown document version {hint!r}")
        return VersionDecision(
            cast("Version", hint), ("asserted by the user",), asserted=True
        )
    text = _front_text(document)
    for version, pattern in _MARKERS:
        match = pattern.search(text)
        if match:
            return VersionDecision(
                version, (f"the first pages say {match.group(0)!r}",)
            )
    if arxiv is not None:
        return VersionDecision("preprint", (f"an arXiv e-print, arXiv:{arxiv}",))
    for version, pattern in _FILE_MARKERS:
        if file_name and pattern.search(file_name):
            return VersionDecision(version, (f"the file is named {file_name!r}",))
    journal = identity.field("journal")
    printed = any(
        candidate.value.lower() == (identity.doi or "").lower() and candidate.strong()
        for candidate in identity.candidates
    )
    if (
        identity.validated()
        and printed
        and isinstance(journal, str)
        and journal.casefold() in text.casefold()
    ):
        return VersionDecision(
            "version_of_record",
            (
                f"the file prints the validated DOI {identity.doi}",
                f"the first pages name the journal {journal}",
            ),
        )
    return VersionDecision("unknown", ())


@dataclass(frozen=True)
class Relation:
    """Relate a paper to one already in the library.

    Attributes
    ----------
    directory : str
        The other paper's directory name, without any shard directory; the
        catalog gives its current location.
    relation : str
        ``same_work`` (same DOI) or ``possible_duplicate`` (same title key,
        first author and year without a shared DOI).
    other_version : str
        The other paper's document version.
    evidence : str
        What matched.
    """

    directory: str
    relation: Literal["same_work", "possible_duplicate"]
    other_version: str
    evidence: str

    def to_dict(self) -> dict[str, object]:
        """Serialize the relation.

        Returns
        -------
        dict of str to object
            JSON-compatible fields.
        """
        return {
            "directory": self.directory,
            "relation": self.relation,
            "other_version": self.other_version,
            "evidence": self.evidence,
        }


def first_author_family(identity: Identity) -> str | None:
    """Return the first author's validated family name.

    Parameters
    ----------
    identity : Identity
        Bibliographic identity.

    Returns
    -------
    str or None
        Family name as registered, or None when unknown.
    """
    authors = identity.field("authors")
    if not isinstance(authors, list) or not authors:
        return None
    first = cast("list[object]", authors)[0]
    if not isinstance(first, Mapping):
        return None
    family = cast("Mapping[str, object]", first).get("family")
    return family if isinstance(family, str) and family else None


def library_relations(
    identity: Identity,
    rows: Sequence[Mapping[str, object]],
    *,
    exclude: str | None = None,
) -> tuple[Relation, ...]:
    """Find the library papers related to a paper about to be published.

    Parameters
    ----------
    identity : Identity
        Identity of the new paper.
    rows : Sequence of Mapping
        Catalog rows of the library.
    exclude : str or None
        Directory the publication replaces, which is not a relation.

    Returns
    -------
    tuple of Relation
        Same-work and possible-duplicate relations in catalog order.
    """
    doi = (identity.doi or "").lower()
    title = identity.field("title")
    key = title_key(title) if isinstance(title, str) else ""
    family = (first_author_family(identity) or "").casefold() or None
    year = identity.field("year")
    relations: list[Relation] = []
    for row in rows:
        location = str(row.get("directory"))
        if location == exclude:
            continue
        # The name, not the path: names are unique across shards, and a
        # paper must not record where its neighbours live.
        directory = PurePosixPath(location).name
        other_version = str(row.get("document_version") or "unknown")
        other_doi = str(row.get("doi") or "").lower()
        if doi and other_doi == doi:
            relations.append(
                Relation(directory, "same_work", other_version, f"same DOI {doi}")
            )
            continue
        other_family = str(row.get("first_author_family") or "").casefold()
        if (
            key
            and key == row.get("title_key")
            and family
            and family == other_family
            and year is not None
            and year == row.get("year")
        ):
            relations.append(
                Relation(
                    directory,
                    "possible_duplicate",
                    other_version,
                    f"same title, first author {family} and year {year}",
                )
            )
    return tuple(relations)


def relation_findings(
    relations: Sequence[Relation], version: str
) -> tuple[Finding, ...]:
    """Describe relations as findings for review.

    Parameters
    ----------
    relations : Sequence of Relation
        Relations of the new paper.
    version : str
        The new paper's document version.

    Returns
    -------
    tuple of Finding
        ``DUPLICATE_CANDIDATE`` when both papers claim the same known version
        of one work or look like the same paper without a shared DOI;
        ``OTHER_VERSION_IN_LIBRARY`` for another version of the same work.
    """
    findings: list[Finding] = []
    for relation in relations:
        same_version = version != "unknown" and relation.other_version == version
        if relation.relation == "same_work" and not same_version:
            findings.append(
                Finding(
                    "OTHER_VERSION_IN_LIBRARY",
                    "info",
                    f"{relation.directory} holds another document of this work "
                    f"({relation.evidence}; its version: {relation.other_version}, "
                    f"this one: {version}).",
                )
            )
        else:
            findings.append(
                Finding(
                    "DUPLICATE_CANDIDATE",
                    "warning",
                    f"{relation.directory} may be the same document "
                    f"({relation.evidence}; versions {relation.other_version} and "
                    f"{version}); review both and remove one if they are.",
                )
            )
    return tuple(findings)


@dataclass(frozen=True)
class TitleMatch:
    """Group files and library papers that share an information title.

    Attributes
    ----------
    title_key : str
        Normalized title.
    paths : tuple of str
        Batch files with that title.
    library : tuple of str
        Library papers with that validated or observed title.
    """

    title_key: str
    paths: tuple[str, ...]
    library: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Serialize the match.

        Returns
        -------
        dict of str to object
            JSON-compatible fields.
        """
        return {
            "title_key": self.title_key,
            "paths": list(self.paths),
            "library": list(self.library),
        }


def intake_title_matches(
    plan: IntakePlan, rows: Sequence[Mapping[str, object]]
) -> list[TitleMatch]:
    """Find possible duplicates by title before extraction.

    Parameters
    ----------
    plan : IntakePlan
        Intake plan with fingerprints.
    rows : Sequence of Mapping
        Catalog rows of the library.

    Returns
    -------
    list of TitleMatch
        Batch files that share a normalized PDF information title with
        another batch file or a library paper; candidates for review only,
        because a title alone never proves identity. Files without a
        plausible information title take no part.
    """
    keys: dict[str, list[str]] = {}
    for group in plan.groups:
        fingerprint = group.fingerprint
        title = "" if fingerprint is None else fingerprint.information.get("Title", "")
        if group.known_as is None and plausible_title(title):
            keys.setdefault(title_key(title), []).append(str(group.primary.path))
    library: dict[str, list[str]] = {}
    for row in rows:
        for field_name in ("title_key", "title_key_observed"):
            key = row.get(field_name)
            if isinstance(key, str) and key:
                library.setdefault(key, []).append(str(row.get("directory")))
    matches: list[TitleMatch] = []
    for key, paths in sorted(keys.items()):
        others = tuple(sorted(set(library.get(key, ()))))
        if len(paths) > 1 or others:
            matches.append(TitleMatch(key, tuple(paths), others))
    return matches
