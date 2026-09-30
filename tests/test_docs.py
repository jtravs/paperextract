"""Check that the actual API page renders, beyond a warning-free build."""

from io import StringIO
from pathlib import Path

import pytest
from sphinx.application import Sphinx
from sphinx.deprecation import RemovedInSphinx11Warning


def test_api_renders_objects_types_and_docstrings(tmp_path: Path) -> None:
    docs = Path(__file__).resolve().parents[1] / "docs"
    output = tmp_path / "html"
    warnings = StringIO()
    app = Sphinx(
        srcdir=docs,
        confdir=docs,
        outdir=output,
        doctreedir=tmp_path / "doctrees",
        buildername="html",
        status=StringIO(),
        warning=warnings,
        freshenv=True,
        warningiserror=True,
    )
    # MyST 5.1 still accesses the Sphinx transform's deprecated app property.
    with pytest.warns(RemovedInSphinx11Warning, match=r"MystReferenceResolver\.app"):
        app.build()
    assert app.statuscode == 0, warnings.getvalue()
    assert not warnings.getvalue()

    html = (output / "api.html").read_text()
    for identifier in (
        "module-paperextract",
        "paperextract.cli.ItemResult",
        "paperextract.cli.ItemResult.to_dict",
        "paperextract.cli.parse_pages",
        "paperextract.cli.EXIT_CODES",
        "paperextract.registry.Lookup",
        "paperextract.protocol.Profile",
        "paperextract.fields.string",
    ):
        assert f'id="{identifier}"' in html
    assert '<dl class="field-list' in html
    assert 'class="doctest highlight-default' in html
    path_link = 'href="https://docs.python.org/3.12/library/pathlib.html#pathlib.Path"'
    assert path_link in html
    assert 'href="#paperextract.cli.EXIT_CODES"' in html
    assert ".. py:" not in html
    assert "processed by numpydoc" not in html
    assert "&lt;factory&gt;" not in html
    assert '<span class="pre">tuple[str</span>' not in html
    assert '<span class="pre">collections.abc.Mapping[str</span>' not in html
