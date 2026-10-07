import re
from html import unescape


ARABIC_TO_PERSIAN = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک", "ة": "ه", "ۀ": "ه"})
# Directional formatting characters are useful to a browser, but unsafe in persisted
# article text.  In particular, an unmatched PDI makes python-bidi abort PDF export.
BIDI_CONTROL_TRANSLATION = dict.fromkeys(
    [0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)]
)
LATIN_ABBREVIATION_REPLACEMENTS = (
    (re.compile(r"\bU\.S\.A\.?\b", re.IGNORECASE), "USA"),
    (re.compile(r"\bU\.S\.?\b", re.IGNORECASE), "US"),
)
WHITESPACE_RE = re.compile(r"[ \t\f\v]+")
BLANK_LINES_RE = re.compile(r"\n{3,}")
TOKEN_RE = re.compile(r"[A-Za-z0-9\u0600-\u06ff]+")


def normalize_text(value: str) -> str:
    for pattern, replacement in LATIN_ABBREVIATION_REPLACEMENTS:
        value = pattern.sub(replacement, value)
    value = unescape(value).translate(ARABIC_TO_PERSIAN).translate(BIDI_CONTROL_TRANSLATION)
    lines = [WHITESPACE_RE.sub(" ", line).strip() for line in value.splitlines()]
    return BLANK_LINES_RE.sub("\n\n", "\n".join(line for line in lines if line)).strip()


def tokens(value: str) -> list[str]:
    return [
        token.casefold() for token in TOKEN_RE.findall(normalize_text(value)) if len(token) >= 2
    ]


def detect_language(value: str) -> str:
    letters = [character for character in value if character.isalpha()]
    if not letters:
        return "unknown"
    latin = sum("a" <= character.casefold() <= "z" for character in letters)
    arabic_script = sum("\u0600" <= character <= "\u06ff" for character in letters)
    if latin > arabic_script:
        return "en"
    persian_specific = sum(character in "پچژگکیی" for character in letters)
    return (
        "fa"
        if persian_specific or any(word in value for word in (" است ", " می", "های "))
        else "ar"
    )
