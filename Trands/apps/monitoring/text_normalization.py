"""Utilities for the search representation of collected Persian content."""

import re


_CHARACTER_TRANSLATION = str.maketrans({"ي": "ی", "ك": "ک", "\u00a0": " "})
_INVISIBLE_CONTROL_CHARACTERS = re.compile(r"[\u200b\u200d\u200e\u200f\u202a-\u202e\u2060\ufeff]")
_REPEATED_WHITESPACE = re.compile(r"[\t\n\r\f\v ]+")


def normalize_persian_text(value: str) -> str:
    """Return a conservative, searchable form while leaving the raw text intact.

    The zero-width non-joiner (\u200c) is intentionally preserved because it carries
    meaning in Persian words.
    """

    value = (value or "").translate(_CHARACTER_TRANSLATION)
    value = _INVISIBLE_CONTROL_CHARACTERS.sub("", value)
    return _REPEATED_WHITESPACE.sub(" ", value).strip()
