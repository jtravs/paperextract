"""Read BibTeX supplied by users, tolerantly and without interpreting it."""

from __future__ import annotations

import re

__all__ = ["read_entries"]

_ENTRY_START = re.compile(r"@(\w+)\s*\{[^,]*,")
_FIELD_NAME = re.compile(r"(\w+)\s*=\s*")


def read_entries(text: str) -> list[tuple[str, dict[str, str]]]:
    """Read the type and fields of every entry in a BibTeX file.

    Parameters
    ----------
    text : str
        File content.

    Returns
    -------
    list of tuple
        Lower-case entry type and a mapping of lower-case field names to
        values with outer braces or quotes and inner braces removed, in file
        order. The first value of a repeated field is kept.

    Examples
    --------
    >>> read_entries('@techreport{k, title = {A {JILA} report}, year = 1977}')
    [('techreport', {'title': 'A JILA report', 'year': '1977'})]
    """
    entries: list[tuple[str, dict[str, str]]] = []
    for start in _ENTRY_START.finditer(text):
        depth, position = 1, start.end()
        while position < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[position], 0)
            position += 1
        body = text[start.end() : position - 1]
        entries.append((start.group(1).lower(), _fields(body)))
    return entries


def _fields(body: str) -> dict[str, str]:
    """Read the fields of one entry body.

    Parameters
    ----------
    body : str
        Text between the citation key and the closing brace.

    Returns
    -------
    dict of str to str
        Field values.
    """
    fields: dict[str, str] = {}
    position = 0
    while (match := _FIELD_NAME.search(body, position)) is not None:
        index = match.end()
        if index >= len(body):
            break
        if body[index] == "{":
            level, end = 1, index + 1
            while end < len(body) and level:
                level += {"{": 1, "}": -1}.get(body[end], 0)
                end += 1
            value, position = body[index + 1 : end - 1], end
        elif body[index] == '"':
            end = body.find('"', index + 1)
            end = end if end > 0 else len(body)
            value, position = body[index + 1 : end], end + 1
        else:
            value = re.split(r"[,\s]", body[index:], maxsplit=1)[0]
            position = index + len(value)
        fields.setdefault(match.group(1).lower(), re.sub(r"[{}]", "", value).strip())
    return fields
