"""Parse backend table HTML into exact grids without changing cell strings."""

import pytest

from paperextract.tables import parse_html_table


def test_simple_table_keeps_every_cell_string() -> None:
    grid = parse_html_table(
        "<table><tbody><tr><td>Pressure mb</td><td>Energy<sup>a</sup>µJ</td></tr>"
        "<tr><td>230</td><td>1.8</td></tr></tbody></table>"
    )
    assert (grid.rows, grid.columns, grid.problems) == (2, 2, ())
    assert [(cell.row, cell.column, cell.text) for cell in grid.cells] == [
        (0, 0, "Pressure mb"),
        (0, 1, "EnergyaµJ"),
        (1, 0, "230"),
        (1, 1, "1.8"),
    ]
    assert grid.cells[1].html == "Energy<sup>a</sup>µJ"


def test_spans_place_cells_on_the_grid() -> None:
    grid = parse_html_table(
        '<table><tr><td rowspan="2"><eq>A</eq></td><td colspan="2">ratio</td></tr>'
        "<tr><th>1319 nm</th><th>1064 nm</th></tr>"
        "<tr><td>Ar</td><td>0.9 &plusmn; 0.1</td><td>&#177;2</td></tr></table>"
    )
    assert (grid.rows, grid.columns, grid.problems) == (3, 3, ())
    placed = {(cell.row, cell.column): cell for cell in grid.cells}
    assert placed[(0, 0)].row_span == 2
    assert placed[(0, 0)].html == "<eq>A</eq>"
    assert placed[(0, 0)].text == "A"
    assert placed[(0, 1)].column_span == 2
    assert placed[(1, 1)].text == "1319 nm"
    assert placed[(1, 2)].text == "1064 nm"
    assert placed[(2, 1)].text == "0.9 ± 0.1"
    assert placed[(2, 1)].html == "0.9 &plusmn; 0.1"
    assert placed[(2, 2)].text == "±2"


def test_ragged_rows_and_overflowing_spans_are_reported_not_repaired() -> None:
    grid = parse_html_table(
        '<table><tr><td>x</td><td>y</td></tr><tr><td rowspan="3">z</td></tr></table>'
    )
    assert grid.rows == 4
    assert grid.columns == 2
    assert grid.problems == (
        "row spans reach row 4 but the table has 2 rows",
        "row 2 covers 1 of 2 columns",
    )
    assert [cell.text for cell in grid.cells] == ["x", "y", "z"]


def test_invalid_span_attributes_and_stray_cells_are_recorded() -> None:
    grid = parse_html_table(
        '<table><td colspan="two" rowspan="0">lonely</td><tr><td>a</td></tr></table>'
    )
    assert grid.problems == (
        "cell outside a table row",
        "invalid rowspan attribute '0' treated as 1",
        "invalid colspan attribute 'two' treated as 1",
    )
    assert [(cell.row, cell.text) for cell in grid.cells] == [(0, "lonely"), (1, "a")]


def test_nested_markup_inside_cells_stays_raw() -> None:
    grid = parse_html_table(
        '<table><tr><td>5.2<br/>0.3<br>x<i>y</i></br><img src="p.png"/></td>'
        "<td><table><tr><td>inner</td></tr></table></td></tr></table>"
    )
    first, second = grid.cells
    assert first.html == '5.2<br/>0.3<br>x<i>y</i></br><img src="p.png"/>'
    assert first.text == "5.2 0.3 xy"
    assert second.html == "<table><tr><td>inner</td></tr></table>"
    assert second.text == "inner"
    assert grid.problems == ()


def test_second_top_level_table_is_ignored_and_empty_markup_rejected() -> None:
    grid = parse_html_table(
        "<table><tr><td>one</td></tr></table><table><tr><td>two</td></tr></table>"
    )
    assert [cell.text for cell in grid.cells] == ["one"]
    with pytest.raises(ValueError, match="No table cells"):
        parse_html_table("<p>no table here</p>")
    with pytest.raises(ValueError, match="No table cells"):
        parse_html_table("<table><tr></tr></table>")


def test_markup_outside_cells_is_ignored() -> None:
    grid = parse_html_table(
        "<p>intro &amp; &#65;</p>\n<table>\n  <tr><br/>&nbsp;&#160;"
        "<td>a</td>\n</tr>\n</table>"
    )
    assert [cell.text for cell in grid.cells] == ["a"]
    assert grid.problems == ()
