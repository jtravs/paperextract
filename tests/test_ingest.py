"""Protect immutable evidence and ownership of staging files."""

import hashlib
import os
from collections.abc import Callable
from io import BufferedReader
from pathlib import Path

import pytest

from paperextract.ingest import (
    DEDUP_REPORT_SCHEMA,
    DEDUP_REPORT_VERSION,
    INTAKE_PLAN_SCHEMA,
    SourceChangedError,
    content_equivalents,
    content_key,
    dedup_report,
    discover_pdfs,
    looks_like_supplement,
    pair_supplements,
    plan_intake,
    preserve_pdf,
    shared_doi_candidates,
)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "original.pdf"
    path.write_bytes(b"%PDF-1.4\nSynthetic preservation fixture.\n%%EOF\n")
    return path


def test_preservation_copies_and_fingerprints_independent_bytes(
    source: Path,
    tmp_path: Path,
) -> None:
    payload = source.read_bytes()
    target = tmp_path / "stored.pdf"
    artifact = preserve_pdf(source, target)
    assert artifact.sha256 == hashlib.sha256(payload).hexdigest()
    assert artifact.size_bytes == len(payload)
    assert artifact.original_name == "original.pdf"
    assert artifact.stored_path == target
    assert target.read_bytes() == payload
    assert source.read_bytes() == payload
    assert source.stat().st_ino != target.stat().st_ino
    source.write_bytes(b"later edit")
    assert target.read_bytes() == payload


@pytest.mark.parametrize("payload", [b"", b"html", b"%PD", b"junk%PDF-1.4"])
def test_preservation_rejects_nonpdf_without_creating_staging(
    tmp_path: Path,
    payload: bytes,
) -> None:
    source = tmp_path / "input"
    source.write_bytes(payload)
    target = tmp_path / "stored.pdf"
    with pytest.raises(ValueError, match="signature"):
        preserve_pdf(source, target)
    assert not target.exists()
    assert source.read_bytes() == payload


@pytest.mark.parametrize("existing", ["file", "directory", "symlink", "source"])
def test_preservation_never_overwrites_existing_paths(
    source: Path,
    tmp_path: Path,
    existing: str,
) -> None:
    target = tmp_path / "stored.pdf"
    if existing == "file":
        target.write_bytes(b"unrelated")
    elif existing == "directory":
        target.mkdir()
    elif existing == "symlink":
        target.symlink_to(source)
    else:
        target = source
    before = source.read_bytes()
    with pytest.raises(FileExistsError):
        preserve_pdf(source, target)
    assert source.read_bytes() == before
    assert target.exists()
    if existing == "file":
        assert target.read_bytes() == b"unrelated"


def test_preservation_requires_existing_staging_parent(
    source: Path, tmp_path: Path
) -> None:
    target = tmp_path / "missing" / "stored.pdf"
    with pytest.raises(FileNotFoundError):
        preserve_pdf(source, target)
    assert not target.parent.exists()


@pytest.mark.parametrize("exception", [OSError("full disk"), KeyboardInterrupt()])
def test_preservation_removes_only_owned_partial_file_on_failure(
    source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exception: BaseException,
) -> None:
    before = source.read_bytes()
    target = tmp_path / "stored.pdf"

    def fail_sync(_descriptor: int) -> None:
        raise exception

    monkeypatch.setattr(os, "fsync", fail_sync)
    with pytest.raises(type(exception)):
        preserve_pdf(source, target)
    assert not target.exists()
    assert source.read_bytes() == before


@pytest.mark.parametrize("change", ["edit", "replace", "digest"])
def test_preservation_rejects_changes_during_verification(
    source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    target = tmp_path / "stored.pdf"
    file_digest = hashlib.file_digest

    def change_before_digest(stream: BufferedReader, algorithm: str) -> object:
        if change == "edit":
            source.write_bytes(b"%PDF-1.4 changed")
        elif change == "replace":
            replacement = tmp_path / "replacement.pdf"
            replacement.write_bytes(source.read_bytes())
            replacement.replace(source)
        else:
            return hashlib.sha256(b"different content detected on second read")
        return file_digest(stream, algorithm)

    monkeypatch.setattr(hashlib, "file_digest", change_before_digest)
    with pytest.raises(SourceChangedError, match="changed"):
        preserve_pdf(source, target)
    assert not target.exists()
    assert source.exists()


def test_discover_pdfs_lists_top_level_files_only(tmp_path: Path) -> None:
    (tmp_path / "b.PDF").write_bytes(b"%PDF-")
    (tmp_path / "a.pdf").write_bytes(b"%PDF-")
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "bundle").mkdir()
    (tmp_path / "bundle" / "inner.pdf").write_bytes(b"%PDF-")
    assert discover_pdfs(tmp_path) == (tmp_path / "a.pdf", tmp_path / "b.PDF")
    with pytest.raises(NotADirectoryError):
        discover_pdfs(tmp_path / "missing")


def test_plan_groups_identical_bytes_and_explains_rejections(
    tmp_path: Path, pdf_builder: Callable[..., bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    payload = pdf_builder(["Shared paper text"], title="Shared")
    (tmp_path / "zeta-copy.pdf").write_bytes(payload)
    (tmp_path / "alpha.pdf").write_bytes(payload)
    other = pdf_builder(["Different paper"], title="Other")
    (tmp_path / "other.pdf").write_bytes(other)
    (tmp_path / "notes.pdf").write_bytes(b"<html>not a pdf</html>")
    (tmp_path / "broken.pdf").write_bytes(b"%PDF-1.4 but nothing else")
    (tmp_path / "broken-copy.pdf").write_bytes(b"%PDF-1.4 but nothing else")
    paths = [
        *discover_pdfs(tmp_path),
        tmp_path / "missing.pdf",
        Path("alpha.pdf"),
    ]
    plan = plan_intake(
        paths, known={hashlib.sha256(other).hexdigest(): "literature/Other_2020"}
    )
    assert [group.primary.path.name for group in plan.groups] == [
        "alpha.pdf",
        "other.pdf",
    ]
    shared, known = plan.groups
    assert shared.sha256 == hashlib.sha256(payload).hexdigest()
    assert [item.path.name for item in shared.aliases] == ["zeta-copy.pdf"]
    assert shared.action == "extract"
    assert shared.fingerprint is not None
    assert shared.fingerprint.information["Title"] == "Shared"
    assert shared.primary.size_bytes == len(payload)
    assert known.action == "reuse"
    assert known.known_as == "literature/Other_2020"
    assert plan.to_extract() == (shared,)
    reasons = {item.path.name: item.reason for item in plan.rejected}
    assert reasons["notes.pdf"] == "no PDF signature at the start of the file"
    assert reasons["missing.pdf"] == "not a regular file"
    assert "PDFium cannot open" in reasons["broken.pdf"]
    assert reasons["broken-copy.pdf"] == reasons["broken.pdf"]
    document = plan.to_dict()
    assert document["schema"] == INTAKE_PLAN_SCHEMA
    assert document["summary"] == {
        "files": 7,
        "groups": 2,
        "extract": 1,
        "reuse": 1,
        "aliases": 1,
        "rejected": 4,
    }
    assert plan.to_json().endswith("\n")


def test_plan_can_skip_fingerprints(tmp_path: Path) -> None:
    path = tmp_path / "signature-only.pdf"
    path.write_bytes(b"%PDF-1.4 not really a document")
    plan = plan_intake([path], fingerprint=False)
    assert len(plan.groups) == 1
    assert plan.groups[0].fingerprint is None
    assert plan.groups[0].to_dict()["fingerprint"] is None
    assert plan.rejected == ()


LONG_TEXT = "Soliton self-compression in gas-filled fibres " * 6


def test_content_equivalence_needs_identical_substantial_text(
    tmp_path: Path, pdf_builder: Callable[..., bytes]
) -> None:
    (tmp_path / "a.pdf").write_bytes(pdf_builder([LONG_TEXT, "doi:10.1000/X"]))
    (tmp_path / "b.pdf").write_bytes(
        pdf_builder([LONG_TEXT, "doi:10.1000/X"], title="Other bytes")
    )
    (tmp_path / "c.pdf").write_bytes(pdf_builder(["short"], title="c"))
    (tmp_path / "d.pdf").write_bytes(pdf_builder(["short"], title="d"))
    (tmp_path / "e.pdf").write_bytes(pdf_builder([LONG_TEXT + " more"]))
    plan = plan_intake(sorted(tmp_path.iterdir()))
    (held,) = content_equivalents(plan)
    b = next(g for g in plan.groups if g.primary.path.name == "b.pdf")
    assert held.sha256 == b.sha256
    assert held.equivalent_to == str(tmp_path / "a.pdf")
    assert held.in_library is False
    assert content_key(b.fingerprint) == held.text_sha256
    c = next(g for g in plan.groups if g.primary.path.name == "c.pdf")
    assert content_key(c.fingerprint) is None
    assert content_key(None) is None
    assert shared_doi_candidates(plan) == {
        "10.1000/x": (str(tmp_path / "a.pdf"), str(tmp_path / "b.pdf"))
    }
    assert shared_doi_candidates(plan, {held.sha256}) == {}
    matches = content_equivalents(plan, {held.text_sha256: "Paper_2020"})
    assert [(m.equivalent_to, m.in_library) for m in matches] == [
        ("Paper_2020", True),
        ("Paper_2020", True),
    ]
    assert [e.sha256 for e in content_equivalents(plan, {})] == [held.sha256]


def test_dedup_report_is_versioned_and_counts_what_remains(
    tmp_path: Path, pdf_builder: Callable[..., bytes]
) -> None:
    (tmp_path / "a.pdf").write_bytes(pdf_builder([LONG_TEXT]))
    (tmp_path / "b.pdf").write_bytes(pdf_builder([LONG_TEXT], title="b"))
    report = dedup_report(plan_intake(sorted(tmp_path.iterdir())))
    assert report["schema"] == DEDUP_REPORT_SCHEMA
    assert report["schema_version"] == DEDUP_REPORT_VERSION
    assert report["summary"] == {
        "content_equivalent": 1,
        "supplements": 0,
        "unpaired_supplements": 0,
        "shared_doi_candidates": 0,
        "to_extract": 1,
    }
    assert report["shared_doi_candidates"] == []
    assert report["content_equivalent"] == [
        next(
            iter(content_equivalents(plan_intake(sorted(tmp_path.iterdir()))))
        ).to_dict()
    ]


def test_review_tiers_ignore_groups_without_fingerprints(
    tmp_path: Path, pdf_builder: Callable[..., bytes]
) -> None:
    (tmp_path / "a.pdf").write_bytes(pdf_builder([LONG_TEXT, "doi:10.1000/X"]))
    (tmp_path / "b.pdf").write_bytes(
        pdf_builder([LONG_TEXT, "doi:10.1000/x"], title="b")
    )
    plan = plan_intake(sorted(tmp_path.iterdir()), fingerprint=False)
    assert content_equivalents(plan) == ()
    assert shared_doi_candidates(plan) == {}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("paper_SI.pdf", True),
        ("nphoton-supp.pdf", True),
        ("ESM_1.pdf", True),
        ("Supporting information.pdf", True),
        ("Supplementary_Material.pdf", True),
        ("soliton2_supplement.pdf", True),
        ("suppression of noise.pdf", False),
        ("silicon photonics.pdf", False),
    ],
)
def test_supplements_are_recognized_by_name(name: str, expected: bool) -> None:
    assert looks_like_supplement(Path(name)) is expected


def write(
    directory: Path, name: str, pdf_builder: Callable[..., bytes], *pages: str
) -> Path:
    path = directory / name
    path.write_bytes(pdf_builder(list(pages)))
    return path


def test_supplements_pair_by_doi_then_by_name(
    tmp_path: Path, pdf_builder: Callable[..., bytes]
) -> None:
    write(tmp_path, "alpha.pdf", pdf_builder, "Alpha doi:10.1000/a1")
    write(tmp_path, "zz_si.pdf", pdf_builder, "Supplement for doi:10.1000/a1")
    write(tmp_path, "beta_2019_long_title.pdf", pdf_builder, "Beta")
    write(tmp_path, "beta_2019_long_title_supplement.pdf", pdf_builder, "Beta SI")
    write(tmp_path, "orphan_supp.pdf", pdf_builder, "Nothing")
    write(tmp_path, "held_copy_si.pdf", pdf_builder, "Held")
    plan = plan_intake(sorted(tmp_path.iterdir()))
    by_name = {group.primary.path.name: group for group in plan.groups}
    pairing = pair_supplements(plan, exclude={by_name["held_copy_si.pdf"].sha256})
    assert {
        by_name[paper].sha256: [member.primary.path.name for member in members]
        for paper, members in [
            ("alpha.pdf", pairing.pairs[by_name["alpha.pdf"].sha256]),
            (
                "beta_2019_long_title.pdf",
                pairing.pairs[by_name["beta_2019_long_title.pdf"].sha256],
            ),
        ]
    } == {
        by_name["alpha.pdf"].sha256: ["zz_si.pdf"],
        by_name["beta_2019_long_title.pdf"].sha256: [
            "beta_2019_long_title_supplement.pdf"
        ],
    }
    assert [g.primary.path.name for g in pairing.unpaired] == ["orphan_supp.pdf"]
    assert pairing.reasons[by_name["zz_si.pdf"].sha256] == "shares a DOI candidate"
    assert pairing.reasons[
        by_name["beta_2019_long_title_supplement.pdf"].sha256
    ].startswith("shares the first 20 file-name characters")
    assert pairing.reasons[by_name["orphan_supp.pdf"].sha256] == "no unique paper"
    assert pairing.supplement_digests() == {
        by_name[name].sha256
        for name in (
            "zz_si.pdf",
            "beta_2019_long_title_supplement.pdf",
            "orphan_supp.pdf",
        )
    }


def test_ambiguous_or_missing_papers_leave_supplements_unpaired(
    tmp_path: Path, pdf_builder: Callable[..., bytes]
) -> None:
    write(tmp_path, "shared_prefix_one.pdf", pdf_builder, "One doi:10.1000/x")
    write(tmp_path, "shared_prefix_two.pdf", pdf_builder, "Two doi:10.1000/x")
    write(tmp_path, "shared_prefix_si.pdf", pdf_builder, "SI doi:10.1000/x")
    plan = plan_intake(sorted(tmp_path.iterdir()))
    pairing = pair_supplements(plan)
    assert pairing.pairs == {}
    assert [g.primary.path.name for g in pairing.unpaired] == ["shared_prefix_si.pdf"]
    bare = plan_intake(sorted(tmp_path.iterdir()), fingerprint=False)
    alone = pair_supplements(
        bare,
        exclude={
            g.sha256
            for g in bare.groups
            if "one" in g.primary.path.name or "two" in g.primary.path.name
        },
    )
    assert [g.primary.path.name for g in alone.unpaired] == ["shared_prefix_si.pdf"]


def test_dedup_report_lists_supplements(
    tmp_path: Path, pdf_builder: Callable[..., bytes]
) -> None:
    write(tmp_path, "alpha.pdf", pdf_builder, "Alpha doi:10.1000/a1")
    write(tmp_path, "alpha_si.pdf", pdf_builder, "Alpha SI doi:10.1000/a1")
    write(tmp_path, "lonely_supp.pdf", pdf_builder, "Nothing")
    report = dedup_report(plan_intake(sorted(tmp_path.iterdir())))
    assert report["summary"] == {
        "content_equivalent": 0,
        "supplements": 2,
        "unpaired_supplements": 1,
        "shared_doi_candidates": 0,
        "to_extract": 1,
    }
    assert report["supplements"] == [
        {
            "paper": str(tmp_path / "alpha.pdf"),
            "supplement": str(tmp_path / "alpha_si.pdf"),
            "evidence": "shares a DOI candidate",
        }
    ]
    assert report["unpaired_supplements"] == [
        {"path": str(tmp_path / "lonely_supp.pdf"), "evidence": "no unique paper"}
    ]
