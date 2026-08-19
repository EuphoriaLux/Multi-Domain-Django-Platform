"""Codepage-safe text handling, shared by the story engine and the printer.

Lives at the top of the package because both halves need it and neither owns
it: `lore.safety` uses it to reject output the printer could not render, and
`printing.escpos` uses it to render what did get through.

The printer speaks CP858 — CP437 plus the euro sign — and nothing else.
"""

from __future__ import annotations

import unicodedata

ENCODING = "cp858"

# Characters with no CP858 mapping and no Unicode decomposition, so NFKD alone
# cannot rescue them. The typographic half of this table is not an edge case:
# language models emit em-dashes, curly quotes and ellipses constantly, and
# without these mappings essentially every model-written vignette would be
# rejected as unprintable and silently replaced by the fallback.
TRANSLIT = str.maketrans(
    {
        "—": "-",
        "–": "-",
        "‑": "-",
        "―": "-",
        "‘": "'",
        "’": "'",
        "‚": ",",
        "‛": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "…": "...",
        "•": "*",
        "·": "-",
        "′": "'",
        "″": '"',
        "ł": "l",
        "Ł": "L",
        "ø": "o",
        "Ø": "O",
        "đ": "d",
        "Đ": "D",
        "þ": "th",
        "Þ": "Th",
        "œ": "oe",
        "Œ": "OE",
        "ŧ": "t",
        "ŋ": "n",
        "š": "s",
        "Š": "S",
        "ž": "z",
        "Ž": "Z",
        "ć": "c",
        "Ć": "C",
        "✓": "OK",
        "✔": "OK",
        "✕": "X",
        "✖": "X",
        "★": "*",
        "☆": "*",
        "✦": "*",
        "✧": "*",
        "✨": "*",
        "🏆": "*",
        "🥇": "*",
        "🥈": "*",
        "🥉": "*",
        "🗳": "",
        "🎭": "",
        "⚡": "",
        "💘": "<3",
        "❤": "<3",
        "\ufe0f": "",
        "\ufe0e": "",
    }
)

# Characters that reorder or hide text when displayed. A right-to-left override
# can make a persona render as something entirely different from what is stored.
#
# Written as \u escapes rather than the literal characters on purpose: a .py
# file that *contains* raw bidi-override/invisible codepoints is exactly the
# Trojan Source profile (CVE-2021-42574) — Bandit's B613 flags this file for
# the same reason this set exists, and it can't tell "detects the attack" from
# "is the attack" by shape alone. Escaping keeps the check meaningful instead
# of just silencing it: the source stays plain ASCII, nothing to hide behind.
INVISIBLE = frozenset(
    "\u200b\u200c\u200d\u2060\ufeff"  # zero-width space/joiners, word joiner, BOM
    "\u202a\u202b\u202c\u202d\u202e"  # LRE/RLE/PDF/LRO/RLO embeddings+overrides
    "\u2066\u2067\u2068\u2069"  # LRI/RLI/FSI/PDI isolates
)


def strip_invisible(text: str, keep: frozenset[str] = frozenset()) -> str:
    """Drop control and formatting characters, preserving anything in `keep`.

    `keep` exists because a line feed is Unicode category `Cc`: guarding a
    vignette has to retain its line structure, while a persona must not keep
    any at all.
    """
    return "".join(
        ch
        for ch in text
        if ch in keep
        or (
            ch not in INVISIBLE
            and unicodedata.category(ch) not in {"Cc", "Cf", "Co", "Cs"}
        )
    )


def to_printable(text: str, encoding: str = ENCODING) -> str:
    """Coerce `text` into something the thermal printer can render.

    Three passes, cheapest first: the codepage directly, then the explicit
    transliteration table, then a decomposition that strips combining marks
    (`Straße` survives; `Ståle` becomes `Stale`).

    Raises `UnicodeEncodeError` if the text still will not fit — the caller
    decides whether that is a rejection or a fallback.
    """
    for candidate in (text, text.translate(TRANSLIT)):
        try:
            candidate.encode(encoding)
            return candidate
        except UnicodeEncodeError:
            continue

    folded = "".join(
        ch
        for ch in unicodedata.normalize("NFKD", text.translate(TRANSLIT))
        if not unicodedata.combining(ch)
    )
    folded.encode(encoding)
    return folded


def ascii_fold(text: str) -> str:
    """Aggressively fold to bare ASCII letters. **For matching only.**

    `to_printable` deliberately preserves anything the codepage supports, so
    `fúck` survives it untouched — CP858 has `ú`. A blocklist that only ever
    sees the printable form therefore misses every accented spelling. This
    folds regardless of printability so the scan sees `fuck`.

    Never use the result as output: it destroys legitimate spelling.
    """
    mapped = text.translate(TRANSLIT)
    return "".join(
        ch
        for ch in unicodedata.normalize("NFKD", mapped)
        if not unicodedata.combining(ch)
    )


def encode(text: str, encoding: str = ENCODING) -> bytes:
    """Encode for the printer. Never raises, never emits control bytes.

    **Stripping controls here is a security boundary, not tidiness.** `\\x1b`
    and `\\x1d` both encode cleanly in CP858, so without this any string that
    reaches the printer — a drink name, a table label, an item note, none of
    which pass through `lore.safety` — could carry its own ESC/POS commands. A
    cocktail called `Mojito\\x1dVA\\x00` would cut the paper mid-order.

    A ticket reading `Stale` where `Ståle` was meant still gets the drink to
    the right table; a `UnicodeEncodeError` in the print path does not.
    """
    stripped = strip_invisible(text)
    try:
        return to_printable(stripped, encoding).encode(encoding)
    except UnicodeEncodeError:
        return stripped.translate(TRANSLIT).encode(encoding, errors="replace")
