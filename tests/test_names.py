"""Fold names to ASCII for directory names, keys and shards."""

import pytest

from paperextract.names import ascii_fold


@pytest.mark.parametrize(
    ("name", "folded"),
    [
        ("Pawłowski", "Pawlowski"),
        ("ŁUKASZ", "LUKASZ"),
        ("Møller", "Moller"),
        ("Ørsted", "Orsted"),
        ("Groß", "Gross"),
        ("Æsop and Cæsar", "Aesop and Caesar"),
        ("Œuvre œuf", "Oeuvre oeuf"),
        ("Đorđević", "Dordevic"),
        ("Þórður", "Thordur"),
        ("Y\u0131ld\u0131z", "Yildiz"),  # dotless i
        ("Börzsönyi", "Borzsonyi"),
        ("Hänsch 光", "Hansch "),
    ],
)
def test_letters_without_decompositions_are_transliterated(
    name: str, folded: str
) -> None:
    assert ascii_fold(name) == folded
