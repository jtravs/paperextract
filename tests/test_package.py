"""Check the installable project's public packaging contract."""

from email.utils import parseaddr
from importlib.metadata import metadata, version
from importlib.resources import files

import paperextract


def test_package_installation_exposes_distribution_metadata() -> None:
    distribution = metadata("paperextract")

    assert version("paperextract")
    assert distribution["Name"] == "paperextract"
    assert distribution["License-Expression"] == "Apache-2.0"
    assert parseaddr(distribution["Author-email"]) == (
        "John C. Travers",
        "jtravs@gmail.com",
    )


def test_package_distributes_typing_marker() -> None:
    assert files(paperextract).joinpath("py.typed").is_file()
