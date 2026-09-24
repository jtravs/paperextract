"""Index a library, look papers up and search their text."""

import json
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from paperextract.catalog import read_catalog
from paperextract.index import (
    CATALOG_CSL,
    CATALOG_MARKDOWN,
    INDEX_PATH,
    LIBRARY_BIB,
    Query,
    build_index,
    lookup,
    queries_from_bibtex,
    query_from_file,
    refresh_index,
    search,
)
from paperextract.pdf import page_text
from paperextract.pipeline import ExtractionSettings, PaperSources
from paperextract.protocol import MineruProfile
from paperextract.registry import RegistryFailure, RegistryRecord
from paperextract.storage import extract_and_publish
from paperextract.worker import WorkerEnvironment

Lookup = Callable[[str], RegistryRecord | RegistryFailure]
PAGES = ["Pulse compression in Börzsönyi gas cells doi:10.1000/z", "Second", None, None]


@pytest.fixture
def library(
    tmp_path: Path,
    pdf_builder: Callable[..., bytes],
    native_worker: Callable[[Path, str], Path],
    synthetic_lookup: Lookup,
) -> Path:
    script = native_worker(tmp_path / "worker.py", "ok")
    root = tmp_path / "library"
    for index, (name, lookup_function) in enumerate(
        (("validated.pdf", synthetic_lookup), ("plain.pdf", None))
    ):
        pdf = tmp_path / name
        pdf.write_bytes(pdf_builder([f"{PAGES[0]} {index}", *PAGES[1:]], title=name))
        staging = tmp_path / f"run{index}"
        staging.mkdir()
        settings = ExtractionSettings(
            WorkerEnvironment(Path(sys.executable), script),
            tmp_path / "models",
            MineruProfile(cpu_threads=1),
            60,
            lookup_function,
        )
        extract_and_publish(
            PaperSources(pdf), staging, settings, root, request_id=f"r{index}"
        )
    refresh_index(root)
    return root


def test_refresh_writes_the_index_and_derived_catalogs(library: Path) -> None:
    rows = read_catalog(library)
    assert len(rows) == 2
    markdown = (library / CATALOG_MARKDOWN).read_text()
    assert "2 papers" in markdown
    assert (
        "| [Author_2020_SyntheticPaper](Author_2020_SyntheticPaper/paper.md) "
        "| Author | 2020 |" in markdown
    )
    assert "| *" in markdown  # the unverified paper shows its observed title
    bib = (library / LIBRARY_BIB).read_text()
    assert bib.startswith("% Author_2020_SyntheticPaper\n@article{author2020synthetic,")
    (item,) = json.loads((library / CATALOG_CSL).read_text())
    assert item["id"] == "author2020synthetic"
    assert item["type"] == "article-journal"
    assert item["issued"] == {"date-parts": [[2020]]}
    assert item["author"] == [{"family": "Author", "given": "A."}]
    connection = sqlite3.connect(library / INDEX_PATH)
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    assert {"papers", "sources", "texts", "candidates", "fulltext"} <= tables
    connection.close()


def test_lookup_distinguishes_present_related_candidate_and_absent(
    library: Path, tmp_path: Path, pdf_builder: Callable[..., bytes]
) -> None:
    validated = tmp_path / "validated.pdf"
    bytes_match, shared = lookup([library], query_from_file(validated))
    assert (bytes_match.status, bytes_match.reason) == ("present", "identical bytes")
    # The other paper prints the same DOI string, so it is related.
    assert (shared.status, shared.reason) == ("related", "both mention DOI 10.1000/z")
    resaved = tmp_path / "resaved.pdf"
    resaved.write_bytes(pdf_builder([f"{PAGES[0]} 0", *PAGES[1:]], title="Other"))
    text_match = lookup([library], query_from_file(resaved))[0]
    assert text_match.reason == "identical text on every page"
    (doi_match,) = lookup([library], Query("q", doi="10.1000/x"))
    assert doi_match.status == "present"
    assert doi_match.directory == "Author_2020_SyntheticPaper"
    related = lookup([library], Query("q", doi_candidates=("10.1000/x", "10.1000/z")))
    assert [m.status for m in related] == ["related", "related"]
    assert related[0].reason == "the file mentions the paper's DOI 10.1000/x"
    assert related[1].reason == "both mention DOI 10.1000/z"
    titled = lookup(
        [library], Query("q", title="The Synthetic Paper", author="Author", year=2021)
    )
    # Both share the observed title; only a validated DOI or bytes prove more.
    assert {m.status for m in titled} == {"candidate"}
    assert all(m.reason.startswith("title equal") for m in titled)
    assert (
        lookup([library], Query("q", title="Synthetic Paper", author="Nobody"))[
            0
        ].status
        == "absent"
    )
    # A known year excludes a paper; an unknown year cannot.
    dated = lookup([library], Query("q", title="Synthetic Paper", year=1990))
    assert [m.directory for m in dated] == [
        next(str(r["directory"]) for r in read_catalog(library) if r["year"] is None)
    ]
    assert (
        lookup([library], Query("q", title="Entirely different words"))[0].status
        == "absent"
    )
    printed = Query("q", first_page="Accepted manuscript: synthetic paper about pulses")
    assert lookup([library], printed)[0].status == "absent"  # two words only


def test_printed_titles_of_four_words_or_more_are_candidates(
    library: Path, tmp_path: Path
) -> None:
    connection = sqlite3.connect(library / INDEX_PATH)
    connection.execute(
        "UPDATE papers SET title_key = 'hollow capillary soliton compression' "
        "WHERE directory = 'Author_2020_SyntheticPaper'"
    )
    connection.commit()
    connection.close()
    # Keep the modified index: its catalog digest still matches.
    page = "HOLLOW capillary soliton compressiona J. Author"
    (match,) = lookup([library], Query("q", first_page=page))
    assert match.status == "candidate"
    assert "printed on the file's first page" in match.reason
    assert tmp_path.exists()


def test_bibtex_files_become_queries() -> None:
    text = """
    @article{a, title = {The {Synthetic} Paper}, author = {Author, A. and Other, B.},
      year = 2020, doi = "10.1000/X"}
    @misc{b, title="Second one", author = {Carl Friedrich Gauss}, year = {18x}}
    @book{c, note = }
    """
    first, second, third = queries_from_bibtex(text)
    assert (first.doi, first.title, first.author, first.year) == (
        "10.1000/x",
        "The Synthetic Paper",
        "Author",
        2020,
    )
    assert (second.author, second.year, second.doi) == ("Gauss", None, None)
    assert third.title is None and third.author is None


def test_search_ranks_text_and_ignores_diacritics(library: Path) -> None:
    hits = search([library], "borzsonyi cells")
    assert [hit.directory for hit in hits] == ["Author_2020_SyntheticPaper"]
    assert "Börzsönyi" not in hits[0].snippet or "[" in hits[0].snippet
    both = search([library], "synthetic energy")
    assert len(both) == 2
    assert both[0].title == "Synthetic Paper"
    assert len(search([library], "synthetic", limit=1)) == 1
    assert search([library], "?!") == []
    assert search([library], "absentword") == []
    assert hits[0].to_dict()["library"] == str(library)


def test_a_stale_or_missing_index_is_rebuilt(library: Path) -> None:
    (library / INDEX_PATH).unlink()
    assert lookup([library], Query("q", doi="10.1000/x"))[0].status == "present"
    catalog = library / "catalog.jsonl"
    rows = catalog.read_text().splitlines()
    catalog.write_text(rows[0] + "\n")
    assert len(search([library], "synthetic")) == 1
    build_index(library, [])
    (library / "catalog.jsonl").unlink()
    assert lookup([library], Query("q", doi="10.1000/x"))[0].status == "absent"


def test_sparse_rows_and_missing_years_are_indexed(
    library: Path, tmp_path: Path
) -> None:
    sparse = {
        "directory": "Sparse",
        "doi_candidates": ["not a doi"],
        "text_sha256": None,
        "supplements": [
            {"text_sha256": None, "markdown": "supplement_01/supplement.md"}
        ],
    }
    build_index(library, [sparse])
    connection = sqlite3.connect(library / INDEX_PATH)
    assert connection.execute("SELECT count(*) FROM texts").fetchone() == (0,)
    assert connection.execute("SELECT count(*) FROM candidates").fetchone() == (0,)
    connection.close()
    metadata_path = library / "Author_2020_SyntheticPaper" / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["fields"]["year"]["value"] = None
    metadata_path.write_text(json.dumps(metadata))
    refresh_index(library)
    (item,) = json.loads((library / CATALOG_CSL).read_text())
    assert "issued" not in item
    assert page_text(tmp_path / "validated.pdf", 99) == ""


def test_hyphenated_and_quoted_words_are_phrases(library: Path) -> None:
    assert search([library], '"borzsonyi cells"')
    assert search([library], "borzsonyi-cells")
    assert search([library], "cells-borzsonyi") == []
