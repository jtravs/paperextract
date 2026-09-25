"""Read user-supplied BibTeX tolerantly."""

from paperextract.bibread import read_entries


def test_entries_are_read_with_braces_quotes_and_bare_values() -> None:
    text = """
    Some comment
    @Article{one,
      Title = {Nested {H$_2$} braces with x = 2 inside},
      author = "Smith, J. and Doe, A.",
      year = 1999,
      title = {repeated fields keep the first},
    }
    @techreport{two, number = {26}, note = "unterminated
    """
    assert read_entries(text) == [
        (
            "article",
            {
                "title": "Nested H$_2$ braces with x = 2 inside",
                "author": "Smith, J. and Doe, A.",
                "year": "1999",
            },
        ),
        ("techreport", {"number": "26", "note": "unterminated"}),
    ]


def test_a_dangling_field_name_ends_the_entry() -> None:
    assert read_entries("@misc{k, year =") == [("misc", {})]
    assert read_entries("no entries here") == []
