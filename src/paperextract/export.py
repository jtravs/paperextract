"""Render portable, human-readable artifacts from a canonical document.

Every renderer here is a pure function of the document and the asset paths
chosen by the publisher. Markdown is derived from the canonical structure, never
from a second extraction path, and no value is rewritten: LaTeX, cell strings
and captions appear exactly as normalization recorded them.
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

from paperextract.document import (
    Block,
    Document,
    Equation,
    Figure,
    Finding,
    Heading,
    ListBlock,
    PageFurniture,
    Paragraph,
    RichText,
    Table,
    TableCell,
    Unclassified,
    plain_text,
)
from paperextract.supplements import (
    SupplementIndex,
    link_references,
    reference_target,
)

__all__ = [
    "DESCRIPTION_BEGIN",
    "DESCRIPTION_END",
    "EXPORT_SCHEMA_VERSION",
    "UNMAPPED_GLYPH_MARK",
    "FigureAssets",
    "PaperAssets",
    "TableAssets",
    "cell_markdown",
    "description_block",
    "inline_markdown",
    "plausible_title",
    "processing_status",
    "render_markdown",
    "review_markdown",
    "table_csv",
    "table_has_spans",
    "table_is_simple",
    "table_markdown",
    "validation_document",
    "yaml_front_matter",
]

# Version 2 adds supplements, table body sources and page-end footnotes;
# version 3 adds machine-generated figure descriptions.
EXPORT_SCHEMA_VERSION = 3
# Comments around a machine-generated description, so that readers and the
# search index can tell it from the paper's own text.
DESCRIPTION_BEGIN = "<!-- machine-generated description: begin -->"
DESCRIPTION_END = "<!-- machine-generated description: end -->"
# Shown in Markdown for a glyph the PDF font does not map to Unicode; the
# canonical document keeps the original control character.
UNMAPPED_GLYPH_MARK = "\ufffd"
_EQ = re.compile(r"<eq>(.*?)</eq>", re.DOTALL)
_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)
_KEPT_TAGS = re.compile(r"</?(?:sub|sup)>", re.IGNORECASE)
_TAG = re.compile(r"</?[A-Za-z][^<>]*>")
_MAX_HEADING_LEVEL = 6
_IDENTITY_OUTCOMES: Mapping[str, str] = {
    "VALIDATED": "pass",
    "VALIDATED_WITH_WARNINGS": "review",
    "ASSERTED": "review",
    "CONFLICT": "fail",
    "UNVERIFIED": "not_checked",
}
_STYLE_MARKERS: Mapping[str, tuple[str, str]] = {
    "bold": ("**", "**"),
    "italic": ("*", "*"),
    "emphasis": ("*", "*"),
    "strikethrough": ("~~", "~~"),
    "superscript": ("<sup>", "</sup>"),
    "subscript": ("<sub>", "</sub>"),
}


@dataclass(frozen=True)
class FigureAssets:
    """Locate the exported files of one figure relative to the paper directory.

    Attributes
    ----------
    context : str or None
        Complete-figure crop rendered from the preserved PDF, if available.
    panels : tuple of str or None
        One entry per panel: the copied backend crop, or None when absent.
    """

    context: str | None
    panels: tuple[str | None, ...]


@dataclass(frozen=True)
class TableAssets:
    """Locate the exported files of one table relative to the paper directory.

    Attributes
    ----------
    html : str
        Standalone HTML file with the raw backend table.
    cells : str
        Exact cell JSON file.
    csv : str or None
        Lossless CSV, only for span-free rectangular grids.
    image : str or None
        Copied backend crop, if available.
    """

    html: str
    cells: str
    csv: str | None
    image: str | None


@dataclass(frozen=True)
class PaperAssets:
    """Map canonical block identifiers to exported asset paths.

    Attributes
    ----------
    figures : Mapping of str to FigureAssets
        Figure identifier to its files.
    tables : Mapping of str to TableAssets
        Table identifier to its files.
    equations : Mapping of str to str
        Equation identifier to its copied crop.
    descriptions : Mapping of str to str
        Figure identifier to its labelled machine-generated description.
    anchors : Mapping of str to str
        Block identifier to an HTML anchor written before the block, used for
        supplement objects that other documents link to.
    links : SupplementIndex or None
        Targets for references to supplementary material, or None to leave
        such references as printed text.
    """

    figures: Mapping[str, FigureAssets] = field(default_factory=dict[str, FigureAssets])
    tables: Mapping[str, TableAssets] = field(default_factory=dict[str, TableAssets])
    equations: Mapping[str, str] = field(default_factory=dict[str, str])
    anchors: Mapping[str, str] = field(default_factory=dict[str, str])
    descriptions: Mapping[str, str] = field(default_factory=dict[str, str])
    links: SupplementIndex | None = None


def _yaml_scalar(value: object) -> str:
    """Render one YAML scalar using JSON-compatible syntax.

    Parameters
    ----------
    value : object
        None, bool, int, float or str.

    Returns
    -------
    str
        YAML scalar text; strings are double-quoted JSON strings, which YAML
        accepts, so titles with colons or quotes round-trip.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    raise TypeError(f"Unsupported front matter value: {type(value).__name__}")


def _yaml_lines(value: object, indent: int) -> list[str]:
    """Render a nested mapping or sequence as indented YAML lines.

    Parameters
    ----------
    value : object
        Mapping or sequence.
    indent : int
        Current indentation in spaces.

    Returns
    -------
    list of str
        YAML lines without trailing newlines.
    """
    pad = " " * indent
    lines: list[str] = []
    if isinstance(value, Mapping):
        for key, item in cast("Mapping[str, object]", value).items():
            name = str(key)
            if isinstance(item, Mapping) and item:
                lines.append(f"{pad}{name}:")
                lines.extend(
                    _yaml_lines(cast("Mapping[str, object]", item), indent + 2)
                )
            elif isinstance(item, Mapping):
                lines.append(f"{pad}{name}: {{}}")
            elif isinstance(item, (list, tuple)) and item:
                lines.append(f"{pad}{name}:")
                lines.extend(_yaml_lines(cast("Sequence[object]", item), indent))
            elif isinstance(item, (list, tuple)):
                lines.append(f"{pad}{name}: []")
            else:
                lines.append(f"{pad}{name}: {_yaml_scalar(item)}")
        return lines
    sequence = (
        cast("Sequence[object]", value) if isinstance(value, (list, tuple)) else ()
    )
    for item in sequence:
        if isinstance(item, Mapping):
            nested = _yaml_lines(cast("Mapping[str, object]", item), indent + 2)
            lines.append(f"{pad}- {nested[0].lstrip()}")
            lines.extend(nested[1:])
        else:
            lines.append(f"{pad}- {_yaml_scalar(item)}")
    return lines


def yaml_front_matter(values: Mapping[str, object]) -> str:
    """Render a YAML front matter block.

    Parameters
    ----------
    values : Mapping of str to object
        Nested mappings, sequences and JSON scalars.

    Returns
    -------
    str
        Front matter delimited by ``---`` lines, ending with a newline.
    """
    return "---\n" + "\n".join(_yaml_lines(values, 0)) + "\n---\n"


def _styled(piece: str, styles: Sequence[str]) -> str:
    """Wrap text in Markdown or HTML style markers, keeping outer whitespace.

    Parameters
    ----------
    piece : str
        Rendered run text.
    styles : Sequence of str
        Backend style names.

    Returns
    -------
    str
        Styled text; unknown styles such as underline are ignored.
    """
    core = piece.strip()
    if not core:
        return piece
    leading = piece[: len(piece) - len(piece.lstrip())]
    trailing = piece[len(piece.rstrip()) :]
    for style in styles:
        markers = _STYLE_MARKERS.get(style)
        if markers is not None:
            core = f"{markers[0]}{core}{markers[1]}"
    return f"{leading}{core}{trailing}"


def inline_markdown(runs: RichText, links: SupplementIndex | None = None) -> str:
    """Render rich text as Markdown with inline LaTeX.

    Parameters
    ----------
    runs : tuple of InlineRun
        Rich text runs.
    links : SupplementIndex or None
        Supplement targets; references in prose become links, and a link run
        that names the supplement points at the converted copy, followed by
        the original link.

    Returns
    -------
    str
        Markdown text; math runs become ``$...$``, code runs become code
        spans, link runs become links, and prose is emitted verbatim.
    """
    pieces: list[str] = []
    for run in runs:
        if run.kind == "math":
            pieces.append(f"${run.text}$")
        elif run.kind == "code":
            pieces.append(f"`{run.text}`")
        elif run.kind == "link" and run.url is not None:
            local = None if links is None else reference_target(run.text, links)
            rendered = f"[{run.text}]({run.url})"
            if local is not None:
                rendered = f"[{run.text}]({local}) ([original link]({run.url}))"
            pieces.append(_styled(rendered, run.styles))
        else:
            text = run.text if links is None else link_references(run.text, links)
            pieces.append(_styled(text, run.styles))
    return _visible("".join(pieces))


def _visible(text: str) -> str:
    """Make unmapped glyphs visible instead of silently invisible.

    Parameters
    ----------
    text : str
        Rendered text.

    Returns
    -------
    str
        Text in which control characters other than tab and newline are shown
        as :data:`UNMAPPED_GLYPH_MARK`.
    """
    return "".join(
        UNMAPPED_GLYPH_MARK
        if unicodedata.category(char) == "Cc" and char not in "\t\n"
        else char
        for char in text
    )


def cell_markdown(cell: TableCell) -> str:
    r"""Render one table cell for a Markdown pipe table.

    Parameters
    ----------
    cell : TableCell
        Canonical cell with its raw HTML.

    Returns
    -------
    str
        The cell with backend math markup as ``$...$``, sub- and superscripts
        kept as HTML, other markup removed, line breaks as spaces and pipes
        escaped. The cell string in ``cells`` files is unchanged.

    Examples
    --------
    >>> from paperextract.document import TableCell
    >>> cell = TableCell(0, 0, 1, 1, "<eq>\\pm 0.2</eq> | x<sup>2</sup>", "")
    >>> cell_markdown(cell)
    '$\\pm 0.2$ \\| x<sup>2</sup>'
    """
    text = _EQ.sub(lambda m: f"${html.unescape(m.group(1)).strip()}$", cell.html)
    text = _BREAK.sub(" ", text)
    text = _TAG.sub(
        lambda m: m.group(0) if _KEPT_TAGS.fullmatch(m.group(0)) else "", text
    )
    return _visible(text.replace("|", "\\|").replace("\n", " ").strip())


def table_is_simple(table: Table) -> bool:
    """Decide whether a table can be flattened without changing its meaning.

    Parameters
    ----------
    table : Table
        Canonical table.

    Returns
    -------
    bool
        True when a complete rectangular grid exists and no cell spans rows
        or columns.
    """
    if table.cells is None or table.rows is None or table.columns is None:
        return False
    if len(table.cells) != table.rows * table.columns:
        return False
    return all(cell.row_span == 1 and cell.column_span == 1 for cell in table.cells)


def _grid(table: Table) -> list[list[str]]:
    """Arrange the cell texts of a simple table by row.

    Parameters
    ----------
    table : Table
        Simple table.

    Returns
    -------
    list of list of str
        Cell texts in row-major order.
    """
    rows = table.rows or 0
    columns = table.columns or 0
    grid = [["" for _ in range(columns)] for _ in range(rows)]
    for cell in table.cells or ():
        grid[cell.row][cell.column] = cell.text
    return grid


def table_has_spans(table: Table) -> bool:
    """Tell whether any cell of a table spans several rows or columns.

    Parameters
    ----------
    table : Table
        Canonical table.

    Returns
    -------
    bool
        True when at least one cell has a row or column span above one.
    """
    return any(cell.row_span > 1 or cell.column_span > 1 for cell in table.cells or ())


def table_markdown(table: Table) -> str:
    """Render a table for the primary Markdown document.

    Parameters
    ----------
    table : Table
        Canonical table.

    Returns
    -------
    str
        A pipe table whenever a cell grid was parsed, with math rendered by
        :func:`cell_markdown`. A cell spanning several rows or columns appears
        once, at its first row and column, and the positions it covers stay
        empty; the table's HTML and cells files keep the exact structure.
        Without a grid the raw backend HTML is returned.
    """
    if not table.cells or not table.rows or not table.columns:
        return table.html if table.html.strip() else "*(table body unavailable)*"
    grid = [["" for _ in range(table.columns)] for _ in range(table.rows)]
    for cell in table.cells:
        if cell.row < table.rows and cell.column < table.columns:
            grid[cell.row][cell.column] = cell_markdown(cell)
    lines = ["| " + " | ".join(row) + " |" for row in grid]
    lines.insert(1, "| " + " | ".join("---" for _ in grid[0]) + " |")
    return "\n".join(lines)


def table_csv(table: Table) -> str | None:
    """Render a lossless CSV for a simple table.

    Parameters
    ----------
    table : Table
        Canonical table.

    Returns
    -------
    str or None
        CSV text with exact cell strings, or None when flattening would change
        the meaning of the table.
    """
    if not table_is_simple(table):
        return None
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerows(_grid(table))
    return buffer.getvalue()


def _label(kind: str, label: str | None) -> str:
    """Name a figure or table for the reader.

    Parameters
    ----------
    kind : str
        ``Figure`` or ``Table``.
    label : str or None
        Printed label.

    Returns
    -------
    str
        Bold heading line.
    """
    return f"**{kind} {label}**" if label else f"**{kind} (unlabelled)**"


def description_block(description: str) -> str:
    """Quote a machine-generated description between its marker comments.

    Parameters
    ----------
    description : str
        Labelled Markdown from :func:`paperextract.describe.render_description`.

    Returns
    -------
    str
        Block quote between :data:`DESCRIPTION_BEGIN` and
        :data:`DESCRIPTION_END`.

    Examples
    --------
    >>> print(description_block("*Machine-generated:* A plot."))
    <!-- machine-generated description: begin -->
    > *Machine-generated:* A plot.
    <!-- machine-generated description: end -->
    """
    quoted = "\n".join(f"> {line}" if line else ">" for line in description.split("\n"))
    return f"{DESCRIPTION_BEGIN}\n{quoted}\n{DESCRIPTION_END}"


def _figure_markdown(
    figure: Figure,
    assets: FigureAssets | None,
    links: SupplementIndex | None,
    description: str | None = None,
) -> str:
    """Render a figure with its published caption and asset links.

    Parameters
    ----------
    figure : Figure
        Canonical figure.
    assets : FigureAssets or None
        Exported files, if any.
    links : SupplementIndex or None
        Supplement targets for the caption and notes.
    description : str or None
        Labelled machine-generated description, placed after the caption.

    Returns
    -------
    str
        Markdown section.
    """
    parts = [_label("Figure", figure.label)]
    if figure.grouping == "unresolved":
        parts[0] += " *(panel grouping unresolved; see validation.json)*"
    alt = f"Figure {figure.label}" if figure.label else "Figure"
    image = None if assets is None else assets.context
    if image is None and assets is not None:
        image = next((panel for panel in assets.panels if panel), None)
    parts.append(f"![{alt}]({image})" if image else "*(no image asset available)*")
    caption = (
        "*(none associated)*"
        if figure.caption is None
        else inline_markdown(figure.caption, links)
    )
    if assets is not None and len(figure.panels) > 1:
        crops = [
            f"[{panel.label or index}]({path})"
            for index, (panel, path) in enumerate(
                zip(figure.panels, assets.panels, strict=True), 1
            )
            if path
        ]
        if crops:
            # Kept inside the caption paragraph so it never reads as body text.
            caption += " *(Panel crops: " + ", ".join(crops) + ")*"
    parts.append(f"**Published caption:** {caption}")
    parts.extend(
        f"*Figure note:* {inline_markdown(note, links)}" for note in figure.footnotes
    )
    if description is not None:
        parts.append(description_block(description))
    return "\n\n".join(parts)


def _table_markdown(
    table: Table, assets: TableAssets | None, links: SupplementIndex | None
) -> str:
    """Render a table with caption, body, notes and asset links.

    Parameters
    ----------
    table : Table
        Canonical table.
    assets : TableAssets or None
        Exported files, if any.
    links : SupplementIndex or None
        Supplement targets for the caption and notes.

    Returns
    -------
    str
        Markdown section.
    """
    parts = [_label("Table", table.label)]
    if table.continues_previous:
        parts[0] += " *(continued)*"
    if table.caption is not None:
        parts.append(f"**Published caption:** {inline_markdown(table.caption, links)}")
    parts.append(table_markdown(table))
    parts.extend(
        f"*Table note:* {inline_markdown(note, links)}" for note in table.footnotes
    )
    if table_has_spans(table):
        parts.append(
            "*Merged cells are shown once, at their first row and column; the "
            "HTML and cells files keep the exact structure.*"
        )
    if assets is not None:
        files = [f"[HTML]({assets.html})", f"[cells]({assets.cells})"]
        if assets.csv:
            files.append(f"[CSV]({assets.csv})")
        if assets.image:
            files.append(f"[image]({assets.image})")
        parts.append("Table files: " + ", ".join(files))
    return "\n\n".join(parts)


def _caption_links(
    block: Figure | Table, assets: PaperAssets
) -> SupplementIndex | None:
    """Return the link targets for a caption, without the block's own anchor.

    Parameters
    ----------
    block : Figure or Table
        Captioned block.
    assets : PaperAssets
        Rendering inputs with anchors and links.

    Returns
    -------
    SupplementIndex or None
        Targets in which the caption's own label does not link to itself.
    """
    anchor = assets.anchors.get(block.id)
    if assets.links is None or anchor is None:
        return assets.links
    return assets.links.without(f"#{anchor}")


def _block_markdown(block: Block, assets: PaperAssets) -> str:
    """Render one block that starts a new Markdown paragraph.

    Parameters
    ----------
    block : Block
        Canonical block.
    assets : PaperAssets
        Exported asset paths.

    Returns
    -------
    str
        Markdown text; empty for page furniture, which the document omits.
    """
    if isinstance(block, Heading):
        level = min(block.level, _MAX_HEADING_LEVEL)
        rendered = "#" * level + " " + inline_markdown(block.runs, assets.links)
    elif isinstance(block, Paragraph):
        rendered = inline_markdown(block.runs, assets.links)
        if block.role == "aside":
            rendered = f"> {rendered}"
    elif isinstance(block, ListBlock):
        rendered = "\n".join(
            f"- {inline_markdown(item, assets.links)}" for item in block.items
        )
    elif isinstance(block, Equation):
        rendered = f"$$\n{block.latex}\n$$"
    elif isinstance(block, Figure):
        rendered = _figure_markdown(
            block,
            assets.figures.get(block.id),
            _caption_links(block, assets),
            assets.descriptions.get(block.id),
        )
    elif isinstance(block, Table):
        rendered = _table_markdown(
            block, assets.tables.get(block.id), _caption_links(block, assets)
        )
    elif isinstance(block, Unclassified):
        rendered = (
            f"<!-- retained backend block {block.backend_type!r}; see document.json -->"
        )
    else:
        rendered = ""
    return rendered


def _page(block: Block) -> int:
    """Return the source page of a block.

    Parameters
    ----------
    block : Block
        Canonical block.

    Returns
    -------
    int
        One-based page.
    """
    return block.page if isinstance(block, Figure) else block.span.page


def _footnote_markdown(block: Paragraph, links: SupplementIndex | None) -> str:
    """Render a page footnote for the end of its page.

    Parameters
    ----------
    block : Paragraph
        Paragraph with the footnote role.
    links : SupplementIndex or None
        Supplement targets.

    Returns
    -------
    str
        Labelled footnote line.
    """
    text = inline_markdown(block.runs, links)
    return f"*Footnote (page {block.span.page}):* {text}"


def render_markdown(
    document: Document, front_matter: Mapping[str, object], assets: PaperAssets
) -> str:
    """Render the primary Markdown document.

    Parameters
    ----------
    document : Document
        Canonical document.
    front_matter : Mapping of str to object
        Values for the YAML front matter.
    assets : PaperAssets
        Exported asset paths relative to the paper directory.

    Returns
    -------
    str
        UTF-8 Markdown with front matter, page anchors as HTML comments, and
        every block except page furniture in reading order. A paragraph the
        backend marks as a continuation joins the preceding paragraph without
        a blank line, so line wrapping across pages does not split prose. Page
        footnotes, such as affiliations, follow the content of their page with
        a label instead of interrupting the text.
    """
    out = [yaml_front_matter(front_matter)]
    page: int | None = None
    previous_paragraph = False
    footnotes: list[str] = []
    for block in document.blocks:
        if isinstance(block, Paragraph) and block.role == "footnote":
            footnotes.append(_footnote_markdown(block, assets.links))
            continue
        rendered = _block_markdown(block, assets)
        if not rendered:
            continue
        if block.id in assets.anchors:
            rendered = f'<a id="{assets.anchors[block.id]}"></a>\n\n{rendered}'
        anchor = ""
        continuing = (
            isinstance(block, Paragraph)
            and block.continues_previous
            and previous_paragraph
        )
        if _page(block) != page:
            if not continuing:
                out.extend(f"\n\n{note}" for note in footnotes)
                footnotes.clear()
            page = _page(block)
            anchor = f"<!-- source: pdf page {page} -->\n"
        if continuing:
            # A paragraph split across pages is rejoined before the previous
            # page's footnotes, which then follow the completed paragraph.
            assert isinstance(block, Paragraph)  # implied by `continuing`
            out.append("\n" + anchor + inline_markdown(block.runs, assets.links))
            out.extend(f"\n\n{note}" for note in footnotes)
            footnotes.clear()
        else:
            out.append("\n\n" + anchor + rendered)
        previous_paragraph = isinstance(block, Paragraph) and block.role != "aside"
    out.extend(f"\n\n{note}" for note in footnotes)
    return "".join(out).rstrip("\n") + "\n"


def processing_status(document: Document) -> str:
    """Summarize page completion.

    Parameters
    ----------
    document : Document
        Canonical document.

    Returns
    -------
    str
        ``COMPLETE`` when every requested page was processed with content,
        else ``PARTIAL``. Neither value certifies scientific quality.
    """
    return (
        "COMPLETE"
        if all(page.status == "processed" for page in document.pages)
        else "PARTIAL"
    )


def _check(name: str, outcome: str, detail: str) -> dict[str, object]:
    """Build one validation check record.

    Parameters
    ----------
    name : str
        Check name.
    outcome : str
        ``pass``, ``fail``, ``review`` or ``not_checked``.
    detail : str
        Explanation.

    Returns
    -------
    dict of str to object
        Check record.
    """
    return {"name": name, "outcome": outcome, "detail": detail}


def _outcome(findings: Sequence[Finding], codes: Sequence[str]) -> tuple[str, str]:
    """Derive a check outcome from the presence of finding codes.

    Parameters
    ----------
    findings : Sequence of Finding
        Document findings.
    codes : Sequence of str
        Codes that put the check under review.

    Returns
    -------
    tuple of str
        Outcome and detail.
    """
    hits = [finding for finding in findings if finding.code in codes]
    if not hits:
        return "pass", "No findings"
    return "review", f"{len(hits)} finding(s): " + ", ".join(
        sorted({f.code for f in hits})
    )


def validation_document(
    document: Document,
    *,
    bibliographic_status: str = "UNVERIFIED",
    identity_detail: str = "Identity stage did not run",
) -> dict[str, object]:
    """Build the structured quality report for one document.

    Parameters
    ----------
    document : Document
        Canonical document.
    bibliographic_status : str
        Outcome of the identity stage.
    identity_detail : str
        Reason recorded with the identity check.

    Returns
    -------
    dict of str to object
        JSON-compatible report with processing status, counts, checks and
        every finding.
    """
    blocks = document.blocks
    paragraphs = [b for b in blocks if isinstance(b, Paragraph)]
    tables = [b for b in blocks if isinstance(b, Table)]
    figures = [b for b in blocks if isinstance(b, Figure)]
    findings = document.findings
    missing = [p.number for p in document.pages if p.status == "missing"]
    empty = [p.number for p in document.pages if p.status == "empty"]
    coverage = (
        ("pass", "Every requested page returned content")
        if not missing and not empty
        else ("fail", f"missing pages {missing}, empty pages {empty}")
    )
    unresolved = sum(1 for f in figures if f.grouping == "unresolved")
    severity_counts = {
        level: sum(1 for f in findings if f.severity == level)
        for level in ("error", "warning", "info")
    }
    checks = [
        _check("page_coverage", *coverage),
        _check(
            "figure_captions",
            "pass" if not unresolved else "review",
            f"{unresolved} figure group(s) without an anchoring caption",
        ),
        _check(
            "table_grids",
            *_outcome(findings, ("TABLE_STRUCTURE_UNRESOLVED", "TABLE_BODY_MISSING")),
        ),
        _check("equation_balance", *_outcome(findings, ("EQUATION_UNBALANCED",))),
        _check("assets_present", *_outcome(findings, ("ASSET_MISSING",))),
        _check(
            "bibliographic_identity",
            _IDENTITY_OUTCOMES.get(bibliographic_status, "not_checked"),
            identity_detail,
        ),
        _check("cross_source_comparison", "not_checked", "Single source supplied"),
    ]
    return {
        "schema": "paperextract.validation",
        "schema_version": EXPORT_SCHEMA_VERSION,
        "processing_status": processing_status(document),
        "bibliographic_status": bibliographic_status,
        "pages": {
            "requested": len(document.pages),
            "processed": [p.number for p in document.pages if p.status == "processed"],
            "empty": empty,
            "missing": missing,
        },
        "counts": {
            "headings": sum(1 for b in blocks if isinstance(b, Heading)),
            "paragraphs": sum(1 for p in paragraphs if p.role == "body"),
            "references": sum(1 for p in paragraphs if p.role == "reference"),
            "footnotes": sum(1 for p in paragraphs if p.role == "footnote"),
            "asides": sum(1 for p in paragraphs if p.role == "aside"),
            "lists": sum(1 for b in blocks if isinstance(b, ListBlock)),
            "equations": sum(1 for b in blocks if isinstance(b, Equation)),
            "figures": len(figures),
            "panels": sum(len(f.panels) for f in figures),
            "tables": len(tables),
            "tables_with_grid": sum(1 for t in tables if t.cells is not None),
            "unclassified": sum(1 for b in blocks if isinstance(b, Unclassified)),
            "page_furniture_omitted": sum(
                1 for b in blocks if isinstance(b, PageFurniture)
            ),
        },
        "severity_counts": severity_counts,
        "checks": checks,
        "findings": [finding.to_dict() for finding in findings],
    }


def review_markdown(document: Document) -> str:
    """Render a readable review list of findings by severity.

    Parameters
    ----------
    document : Document
        Canonical document.

    Returns
    -------
    str
        Markdown text for ``diagnostics/review.md``.
    """
    lines = ["# Review", "", f"Processing status: {processing_status(document)}.", ""]
    for level in ("error", "warning", "info"):
        selected = [f for f in document.findings if f.severity == level]
        lines.append(f"## {level.capitalize()} ({len(selected)})")
        lines.append("")
        if not selected:
            lines.append("- none")
        for finding in selected:
            where = f" page {finding.page}" if finding.page is not None else ""
            blocks = f" [{', '.join(finding.block_ids)}]" if finding.block_ids else ""
            lines.append(f"- `{finding.code}`{where}: {finding.message}{blocks}")
        lines.append("")
    tables = [t for t in document.blocks if isinstance(t, Table)]
    figures = [f for f in document.blocks if isinstance(f, Figure)]
    lines.append("## Objects")
    lines.append("")
    lines.append(
        f"- Figures: {len(figures)}; unresolved groups: "
        f"{sum(1 for f in figures if f.grouping == 'unresolved')}"
    )
    lines.append(
        f"- Tables: {len(tables)}; without a cell grid: "
        f"{sum(1 for t in tables if t.cells is None)}"
    )
    lines.append(
        "- Titles and captions above are backend transcriptions; compare with the "
        "preserved PDF before relying on them."
    )
    return "\n".join(lines) + "\n"


# PDF information titles that name an identifier or the authoring file rather
# than the article, as in "PII: 0022-4073(81)90057-1" or "Microsoft Word - x".
# Identifiers, authoring files and typesetting or template leftovers seen in
# PDF information titles, such as "acs_JX_jp-2011-094438 1..7",
# "rsc_cp_b701020f 2044..2064", "vyk90e3.tmp" and "Using JCP format".
_IMPLAUSIBLE_TITLE = re.compile(
    r"^\s*(?:unknown|unbekannt|inconnu|sin t\u00edtulo|senza titolo|title)\s*$|"
    r"^\s*(?:PII\b|doi\b|Microsoft Word\b|untitled\b|mhtml:|file:|"
    r"(?:acs|rsc|aip|iop|els|wiley)_\w+|using\s+\S+(?:\s+\S+)?\s+(?:format|style)|"
    r"using\s+standard\b)"
    r"|\.(?:pdf|docx?|tex|dvi|ps|tmp|indd|qxd)\s*$"
    r"|\b\d+\.\.\d+\s*$",
    re.IGNORECASE,
)
_MIN_TITLE_LETTERS = 3


def plausible_title(text: str) -> bool:
    """Tell whether a PDF information title can be an article title.

    Parameters
    ----------
    text : str
        Title entry of the PDF information dictionary.

    Returns
    -------
    bool
        False for identifiers, authoring file names and near-empty strings.

    Examples
    --------
    >>> plausible_title("PII: 0022-4073(81)90057-1")
    False
    >>> plausible_title("Refraction and dispersion of neon")
    True
    """
    letters = sum(char.isalpha() for char in text)
    return letters >= _MIN_TITLE_LETTERS and not _IMPLAUSIBLE_TITLE.search(text)


def title_observation(document: Document) -> str | None:
    """Pick the best observed title without validating it.

    Parameters
    ----------
    document : Document
        Canonical document.

    Returns
    -------
    str or None
        The PDF information title if it is plausible, else the first level-1
        heading text, else None. This is an observation for the identity
        stage.
    """
    for observation in document.metadata:
        if observation.field == "title" and plausible_title(observation.value):
            return observation.value
    for block in document.blocks:
        if isinstance(block, Heading) and block.level == 1:
            return plain_text(block.runs)
    return None
