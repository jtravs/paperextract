"""Anchor supplementary objects and link references to them.

A supplement is published inside its paper directory as its own Markdown. Its
figures, tables, equations and numbered sections receive stable anchors such as
``figure-s3``. References in the text, such as "Supplementary Fig. 3", "Fig. S3"
or "Supplementary Information", become links to those anchors. A reference whose
target is not in any supplied supplement stays as printed text and is reported.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from paperextract.document import (
    Document,
    Equation,
    Figure,
    Heading,
    ListBlock,
    Paragraph,
    RichText,
    Table,
    plain_text,
)

__all__ = [
    "DOCUMENT_KEY",
    "Reference",
    "SupplementIndex",
    "anchor_ids",
    "find_references",
    "link_references",
    "reference_target",
]

Key = tuple[str, str]
DOCUMENT_KEY: Key = ("document", "")
"""Index key of a reference to the supplementary material as a whole."""

_KINDS = (
    ("figure", r"Figures?\.?|Figs?\.?"),
    ("table", r"Tables?|Tab\.?"),
    ("equation", r"Equations?|Eqs?\.?"),
    ("section", r"Sections?|Sect?\.|Notes?"),
)
_KIND_PATTERN = "|".join(f"(?P<{kind}>{pattern})" for kind, pattern in _KINDS)
# A number in parentheses, as in "Eq. (S5)", is taken with both parentheses;
# a closing parenthesis of the surrounding text is never part of a reference.
_NUMBER = r"(?:\(\s*(?P<paren>S?\d+[a-z]?)\s*\)|(?P<bare>S?\d+[a-z]?)\b)"
_SUPPLEMENTARY = re.compile(
    rf"\b(?:Supplementary|Suppl\.)\s+(?:{_KIND_PATTERN})\s*{_NUMBER}", re.IGNORECASE
)
_S_PREFIXED = re.compile(
    rf"\b(?:{_KIND_PATTERN})\s*(?:\(\s*(?P<paren>S\d+[a-z]?)\s*\)|(?P<bare>S\d+[a-z]?)\b)",
    re.IGNORECASE,
)
_GENERIC = re.compile(
    r"\b(?:Supplementary\s+(?:Information|Materials?|Data|Text)"
    r"|Supporting\s+Information|Supplemental\s+Materials?|Supplement\s+\d+)\b"
)
_SECTION_HEADING = re.compile(
    r"^\s*(?:Supplementary\s+(?:Note|Section)\s+)?S?(\d+)(?:[.:]|\s)", re.IGNORECASE
)
_LABEL = re.compile(r"^S?(\d+[A-Za-z]?)$", re.IGNORECASE)


def _key(kind: str, label: str) -> Key | None:
    """Build an index key from an object label.

    Parameters
    ----------
    kind : str
        ``figure``, ``table``, ``equation`` or ``section``.
    label : str
        Printed label with or without the ``S`` prefix.

    Returns
    -------
    tuple of str or None
        Kind and lower-case number, or None for a label that is not numbered.
    """
    match = _LABEL.match(label.strip())
    return None if match is None else (kind, match.group(1).lower())


def _block_key(block: object) -> Key | None:
    """Return the index key of a supplement block, if it can be referenced.

    Parameters
    ----------
    block : object
        Canonical block.

    Returns
    -------
    tuple of str or None
        Key for a labelled figure, table or equation, or a numbered heading.
    """
    if isinstance(block, Figure) and block.label:
        return _key("figure", block.label)
    if isinstance(block, Table) and block.label and not block.continues_previous:
        return _key("table", block.label)
    if isinstance(block, Equation) and block.label:
        return _key("equation", block.label)
    if isinstance(block, Heading):
        match = _SECTION_HEADING.match(plain_text(block.runs))
        return None if match is None else ("section", match.group(1))
    return None


def anchor_ids(document: Document) -> dict[str, str]:
    """Assign anchors to the referenceable objects of a supplement.

    Parameters
    ----------
    document : Document
        Supplement document.

    Returns
    -------
    dict of str to str
        Block identifier to anchor, such as ``figure-s3``; the first object
        with a given label wins.
    """
    anchors: dict[str, str] = {}
    seen: set[Key] = set()
    for block in document.blocks:
        key = _block_key(block)
        if key is not None and key not in seen:
            seen.add(key)
            anchors[block.id] = f"{key[0]}-s{key[1]}"
    return anchors


@dataclass(frozen=True)
class SupplementIndex:
    """Map reference keys to link targets for one rendering context.

    Attributes
    ----------
    targets : Mapping of tuple to str
        Key to relative link, such as ``supplement_01/supplement.md#figure-s3``.
    """

    targets: Mapping[Key, str]

    @classmethod
    def build(
        cls, supplements: Sequence[tuple[str, Document]], *, own: str | None = None
    ) -> SupplementIndex:
        """Index the anchors of every supplement.

        Parameters
        ----------
        supplements : Sequence of tuple
            Relative Markdown path and document of each supplement, in order.
        own : str or None
            Path of the supplement being rendered, whose anchors link within
            the same file; references to the material as a whole are not
            linked from inside a supplement.

        Returns
        -------
        SupplementIndex
            Index in which the first supplement holding a key wins.
        """
        targets: dict[Key, str] = {}
        for path, document in supplements:
            prefix = "" if path == own else path
            if own is None and DOCUMENT_KEY not in targets:
                targets[DOCUMENT_KEY] = path
            for block in document.blocks:
                key = _block_key(block)
                if key is not None and key not in targets:
                    targets[key] = f"{prefix}#{key[0]}-s{key[1]}"
        return cls(targets)

    def without(self, target: str) -> SupplementIndex:
        """Drop one link target, such as the anchor of the block being rendered.

        Parameters
        ----------
        target : str
            Target to remove.

        Returns
        -------
        SupplementIndex
            Index without keys that point at the target, so a caption does not
            link its own label.
        """
        return SupplementIndex(
            {key: value for key, value in self.targets.items() if value != target}
        )


@dataclass(frozen=True)
class Reference:
    """Record one reference to supplementary material found in the text.

    Attributes
    ----------
    block_id : str
        Block that contains the reference.
    text : str
        Reference as printed.
    target : str or None
        Link target, or None when no supplied supplement holds it.
    """

    block_id: str
    text: str
    target: str | None


def _matches(text: str) -> list[tuple[int, int, Key]]:
    """Find non-overlapping references in plain text.

    Parameters
    ----------
    text : str
        Plain prose.

    Returns
    -------
    list of tuple
        Start, end and key of each reference, in text order.
    """
    found: list[tuple[int, int, Key]] = []
    for pattern in (_SUPPLEMENTARY, _S_PREFIXED):
        for match in pattern.finditer(text):
            kind = next(k for k, _ in _KINDS if match.group(k))
            number = (match.group("paren") or match.group("bare")).lower()
            found.append((match.start(), match.end(), (kind, number.lstrip("s"))))
    found.extend((m.start(), m.end(), DOCUMENT_KEY) for m in _GENERIC.finditer(text))
    found.sort()
    kept: list[tuple[int, int, Key]] = []
    for item in found:
        if not kept or item[0] >= kept[-1][1]:
            kept.append(item)
    return kept


def link_references(text: str, index: SupplementIndex) -> str:
    """Turn resolvable references in prose into Markdown links.

    Parameters
    ----------
    text : str
        Plain prose from one text run.
    index : SupplementIndex
        Targets for the current rendering context.

    Returns
    -------
    str
        The text with each resolvable reference wrapped as ``[text](target)``;
        unresolvable references are left unchanged.

    Examples
    --------
    >>> index = SupplementIndex({("figure", "3"): "https://example.org/s#figure-s3"})
    >>> link_references("see Supplementary Fig. 3 and Fig. S4", index)
    'see [Supplementary Fig. 3](https://example.org/s#figure-s3) and Fig. S4'
    """
    pieces: list[str] = []
    position = 0
    for start, end, key in _matches(text):
        target = index.targets.get(key)
        if target is None:
            continue
        pieces.append(text[position:start])
        pieces.append(f"[{text[start:end]}]({target})")
        position = end
    pieces.append(text[position:])
    return "".join(pieces)


def reference_target(text: str, index: SupplementIndex) -> str | None:
    """Resolve text that consists of a single supplement reference.

    Parameters
    ----------
    text : str
        Text of a link run, such as "Supplement 1".
    index : SupplementIndex
        Targets for the current rendering context.

    Returns
    -------
    str or None
        Target when the whole text is one resolvable reference.
    """
    stripped = text.strip()
    found = _matches(stripped)
    if len(found) != 1 or found[0][:2] != (0, len(stripped)):
        return None
    return index.targets.get(found[0][2])


def _prose(block: object) -> list[RichText]:
    """Collect the rich text of a block that may cite supplements.

    Parameters
    ----------
    block : object
        Canonical block.

    Returns
    -------
    list of tuple of InlineRun
        Runs of paragraphs, headings, list items, captions and notes.
    """
    if isinstance(block, Paragraph | Heading):
        return [block.runs]
    if isinstance(block, ListBlock):
        return list(block.items)
    if isinstance(block, Figure | Table):
        runs = [] if block.caption is None else [block.caption]
        return [*runs, *block.footnotes]
    return []


def find_references(document: Document, index: SupplementIndex) -> list[Reference]:
    """List every reference to supplementary material in a document.

    Parameters
    ----------
    document : Document
        Document whose prose is scanned.
    index : SupplementIndex
        Targets for the document's rendering context.

    Returns
    -------
    list of Reference
        References in reading order, with their targets or None.
    """
    references: list[Reference] = []
    for block in document.blocks:
        for runs in _prose(block):
            for run in runs:
                if run.kind in {"math", "code"}:
                    continue
                for start, end, key in _matches(run.text):
                    references.append(
                        Reference(block.id, run.text[start:end], index.targets.get(key))
                    )
    return references
