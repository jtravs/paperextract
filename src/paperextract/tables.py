"""Parse backend table HTML into an exact cell grid without altering strings."""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from html.parser import HTMLParser

from paperextract.document import TableCell

__all__ = ["TableGrid", "parse_html_table"]

_CELL_TAGS = frozenset({"td", "th"})
_VOID_TAGS = frozenset({"br", "img", "hr", "wbr", "col", "input"})


@dataclass(frozen=True)
class TableGrid:
    """Hold the parsed cells of one table together with structural problems.

    Attributes
    ----------
    cells : tuple of TableCell
        Cells in document order with their grid placement.
    rows : int
        Number of grid rows, including rows covered only by spans.
    columns : int
        Number of grid columns.
    problems : tuple of str
        Structural inconsistencies found while placing cells, such as ragged
        rows or spans reaching beyond the table. Values are never adjusted.
    """

    cells: tuple[TableCell, ...]
    rows: int
    columns: int
    problems: tuple[str, ...]


@dataclass
class _Cell:
    """Accumulate one cell while parsing."""

    row_span: int
    column_span: int
    raw: list[str] = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    depth: int = 0


def _span(attrs: list[tuple[str, str | None]], name: str, problems: list[str]) -> int:
    """Read a span attribute, defaulting to one and recording invalid values.

    Parameters
    ----------
    attrs : list of tuple
        Start-tag attributes.
    name : str
        ``rowspan`` or ``colspan``.
    problems : list of str
        Problem log to extend.

    Returns
    -------
    int
        Span of at least one.
    """
    for key, value in attrs:
        if key != name:
            continue
        if value is not None and value.strip().isdigit() and int(value) >= 1:
            return int(value)
        problems.append(f"invalid {name} attribute {value!r} treated as 1")
    return 1


class _Collector(HTMLParser):
    """Collect rows of raw cells from the first top-level table in the markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.rows: list[list[_Cell]] = []
        self.problems: list[str] = []
        self._row: list[_Cell] | None = None
        self._cell: _Cell | None = None
        self._tables = 0
        self._done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Open a table, row or cell, or record markup nested inside a cell.

        Parameters
        ----------
        tag : str
            Lower-case tag name.
        attrs : list of tuple
            Attribute name and value pairs.
        """
        if self._cell is not None:
            self._cell.raw.append(self.get_starttag_text() or f"<{tag}>")
            if tag == "br":
                self._cell.text.append(" ")
            if tag not in _VOID_TAGS:
                self._cell.depth += 1
            return
        if self._done:
            return
        if tag == "table":
            self._tables += 1
        elif self._tables != 1:
            return
        elif tag == "tr":
            self._row = []
            self.rows.append(self._row)
        elif tag in _CELL_TAGS:
            if self._row is None:
                self.problems.append("cell outside a table row")
                self._row = []
                self.rows.append(self._row)
            self._cell = _Cell(
                row_span=_span(attrs, "rowspan", self.problems),
                column_span=_span(attrs, "colspan", self.problems),
            )
            self._row.append(self._cell)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record self-closing markup inside a cell without changing depth.

        Parameters
        ----------
        tag : str
            Lower-case tag name.
        attrs : list of tuple
            Attribute pairs; unused because the raw tag text is recorded.
        """
        del attrs
        if self._cell is not None:
            self._cell.raw.append(self.get_starttag_text() or f"<{tag}/>")
            if tag == "br":
                self._cell.text.append(" ")

    def handle_endtag(self, tag: str) -> None:
        """Close a cell, row or table, or record an end tag nested in a cell.

        Parameters
        ----------
        tag : str
            Lower-case tag name.
        """
        if self._cell is not None:
            if tag in _CELL_TAGS and self._cell.depth == 0:
                self._cell = None
                return
            if tag not in _VOID_TAGS:
                self._cell.depth = max(0, self._cell.depth - 1)
            self._cell.raw.append(f"</{tag}>")
            return
        if tag == "tr" and self._tables == 1:
            self._row = None
        elif tag == "table":
            self._tables -= 1
            self._done = self._tables == 0

    def handle_data(self, data: str) -> None:
        """Record literal text inside a cell.

        Parameters
        ----------
        data : str
            Literal character data.
        """
        if self._cell is not None:
            self._cell.raw.append(data)
            self._cell.text.append(data)

    def handle_entityref(self, name: str) -> None:
        """Record a named entity verbatim and decoded.

        Parameters
        ----------
        name : str
            Entity name without the ampersand and semicolon.
        """
        if self._cell is not None:
            self._cell.raw.append(f"&{name};")
            self._cell.text.append(html.unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        """Record a numeric character reference verbatim and decoded.

        Parameters
        ----------
        name : str
            Decimal or hexadecimal code between ``&#`` and the semicolon.
        """
        if self._cell is not None:
            self._cell.raw.append(f"&#{name};")
            self._cell.text.append(html.unescape(f"&#{name};"))


def parse_html_table(markup: str) -> TableGrid:
    """Place the cells of an HTML table on a grid, honoring row and column spans.

    Parameters
    ----------
    markup : str
        HTML containing one table; nested tables inside cells stay raw.

    Returns
    -------
    TableGrid
        Cells with grid positions, grid dimensions and structural problems.

    Raises
    ------
    ValueError
        The markup contains no table cells.

    Notes
    -----
    Cell strings are never modified: ``html`` keeps the inner markup verbatim
    and ``text`` only strips tags, decodes entities and collapses whitespace.
    Ragged rows and spans that reach past the last row are reported as
    problems for review, not repaired, because a shifted column changes the
    meaning of numerical data.
    """
    collector = _Collector()
    collector.feed(markup)
    collector.close()
    if not any(collector.rows):
        raise ValueError("No table cells found in markup")
    problems = list(collector.problems)
    occupied: set[tuple[int, int]] = set()
    cells: list[TableCell] = []
    extents: list[int] = []
    for row_index, row in enumerate(collector.rows):
        column = 0
        for cell in row:
            while (row_index, column) in occupied:
                column += 1
            for r in range(row_index, row_index + cell.row_span):
                for c in range(column, column + cell.column_span):
                    occupied.add((r, c))
            cells.append(
                TableCell(
                    row=row_index,
                    column=column,
                    row_span=cell.row_span,
                    column_span=cell.column_span,
                    html="".join(cell.raw),
                    text=" ".join("".join(cell.text).split()),
                )
            )
            column += cell.column_span
        extents.append(max([column, *(c + 1 for r, c in occupied if r == row_index)]))
    rows = max(r for r, _ in occupied) + 1
    columns = max(extents)
    if rows > len(collector.rows):
        problems.append(
            f"row spans reach row {rows} but the table has {len(collector.rows)} rows"
        )
    for row_index, extent in enumerate(extents):
        if extent != columns:
            problems.append(f"row {row_index + 1} covers {extent} of {columns} columns")
    return TableGrid(
        cells=tuple(cells), rows=rows, columns=columns, problems=tuple(problems)
    )
