"""Configure the offline Sphinx manual."""

from __future__ import annotations

import re
from importlib.metadata import version as _package_version
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sphinx.application import Sphinx

project = "paperextract"
author = "John C. Travers"
copyright = "2026, John C. Travers"
release = _package_version("paperextract")
extensions = ["myst_parser", "sphinx.ext.autodoc", "sphinx.ext.intersphinx", "numpydoc"]
exclude_patterns = ["_build"]
nitpicky = True
numpydoc_show_class_members = False
# Use a checked-in inventory so standard-library links resolve offline.
intersphinx_mapping = {
    "python": ("https://docs.python.org/3.12", "_intersphinx/python.inv"),
}

html_theme = "pydata_sphinx_theme"
html_title = "paperextract"
html_theme_options = {
    "logo": {"text": "paperextract"},
    "github_url": "https://github.com/jtravs/paperextract",
    "use_edit_page_button": True,
    "navbar_align": "left",
    "header_links_before_dropdown": 5,
    "show_toc_level": 2,
    "navigation_with_keys": False,
    "secondary_sidebar_items": ["edit-this-page", "page-toc"],
    "footer_start": ["copyright"],
    "footer_end": ["theme-version"],
}
html_context = {
    "github_user": "jtravs",
    "github_repo": "paperextract",
    # Propose documentation changes on main, including from released manuals.
    "github_version": "main",
    "doc_path": "docs",
}
# Pages without subpages have no section navigation to show.
html_sidebars: dict[str, list[str]] = {"index": [], "usage": [], "tutorial": []}


def _format_signature(
    _app: Sphinx,
    _what: str,
    _name: str,
    _obj: object,
    _options: object,
    signature: str | None,
    return_annotation: str | None,
) -> tuple[str | None, str | None]:
    # Dataclasses use <factory>, which is not valid Python syntax. Sphinx's
    # fallback parser then splits generic types at commas into extra parameters.
    if signature is not None:
        signature = signature.replace("<factory>", "...")
        # Forward aliases can leave Path unevaluated and unqualified; the
        # external inventory needs its full standard-library name.
        signature = re.sub(r"(?<![\w.])Path\b", "pathlib.Path", signature)
    return signature, return_annotation


def setup(app: Sphinx) -> None:
    app.connect("autodoc-process-signature", _format_signature)
