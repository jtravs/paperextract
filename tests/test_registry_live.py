"""Opt-in live check of one real Crossref record.

Run with sockets enabled and the variable set:

    PAPEREXTRACT_LIVE_REGISTRY=1 uv run --offline --no-sync pytest -m network \\
        tests/test_registry_live.py
"""

import os
from pathlib import Path

import pytest

from paperextract.registry import RegistryRecord, default_lookup, default_search

pytestmark = [pytest.mark.network, pytest.mark.enable_socket]


def test_crossref_returns_the_hisol_record(tmp_path: Path) -> None:
    if os.environ.get("PAPEREXTRACT_LIVE_REGISTRY") != "1":
        pytest.skip("PAPEREXTRACT_LIVE_REGISTRY is not set")
    lookup = default_lookup(tmp_path / "snapshots")
    record = lookup("10.1038/s41566-019-0416-4")
    assert isinstance(record, RegistryRecord)
    assert record.provider == "crossref"
    assert record.year == 2019
    assert record.container_title == "Nature Photonics"
    assert record.authors[0].family == "Travers"
    again = lookup("10.1038/s41566-019-0416-4")
    assert isinstance(again, RegistryRecord) and again.cached is True


def test_crossref_search_ranks_an_old_paper_first(tmp_path: Path) -> None:
    if os.environ.get("PAPEREXTRACT_LIVE_REGISTRY") != "1":
        pytest.skip("PAPEREXTRACT_LIVE_REGISTRY is not set")
    search = default_search(tmp_path / "snapshots")
    records = search("Refractive index of He in the region 920-1910 A")
    assert isinstance(records, tuple)
    assert records[0].doi == "10.1364/josa.64.000390"
