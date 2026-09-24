"""Configure the offline Sphinx manual."""

from importlib.metadata import version as _package_version

project = "paperextract"
author = "John C. Travers"
copyright = "2026, John C. Travers"
release = _package_version("paperextract")
extensions = ["myst_parser", "sphinx.ext.autodoc", "numpydoc"]
exclude_patterns = ["_build"]
nitpicky = True
numpydoc_show_class_members = False

html_theme = "pydata_sphinx_theme"
html_title = "paperextract"
html_theme_options = {
    "logo": {"text": "paperextract"},
    "navbar_align": "left",
    "header_links_before_dropdown": 5,
    "show_toc_level": 2,
    "navigation_with_keys": False,
    "secondary_sidebar_items": ["page-toc"],
    "footer_start": ["copyright"],
    "footer_end": ["theme-version"],
}
# Pages without subpages have no section navigation to show.
html_sidebars: dict[str, list[str]] = {"index": [], "usage": [], "tutorial": []}
