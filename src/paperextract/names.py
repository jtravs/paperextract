"""Fold names to ASCII for directory names, citation keys and shards."""

from __future__ import annotations

import unicodedata

__all__ = ["ascii_fold"]

# Letters that Unicode does not decompose into an ASCII base letter, so NFKD
# folding alone would drop them: Pawłowski would become "Pawowski".
_TRANSLITERATIONS = str.maketrans(
    {
        "ł": "l",
        "Ł": "L",
        "ø": "o",
        "Ø": "O",
        "æ": "ae",
        "Æ": "Ae",
        "œ": "oe",
        "Œ": "Oe",
        "ß": "ss",
        "đ": "d",
        "Đ": "D",
        "þ": "th",
        "Þ": "Th",
        "ð": "d",
        "Ð": "D",
        "\u0131": "i",  # dotless i
    }
)


def ascii_fold(text: str) -> str:
    """Fold text to its ASCII characters, transliterating where needed.

    Parameters
    ----------
    text : str
        Unicode text, such as an author's family name.

    Returns
    -------
    str
        The text with letters such as ł, ø, æ and ß transliterated, accents
        removed by NFKD decomposition, and every remaining non-ASCII
        character dropped. Case is kept. Used only for names and keys; stored
        metadata keeps the original spelling.

    Examples
    --------
    >>> ascii_fold("Pawłowski"), ascii_fold("Møller"), ascii_fold("Groß")
    ('Pawlowski', 'Moller', 'Gross')
    >>> ascii_fold("Börzsönyi Œuvre")
    'Borzsonyi Oeuvre'
    """
    decomposed = unicodedata.normalize("NFKD", text.translate(_TRANSLITERATIONS))
    return "".join(ch for ch in decomposed if ch.isascii())
