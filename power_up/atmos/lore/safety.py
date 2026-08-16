"""Input sanitization and output guarding for the chronicle.

Two untrusted boundaries meet here, and both end at the same place — ink on
paper handed to a customer, with the venue's name at the top:

1. **Guest personas are free text.** They enter an LLM prompt.
2. **Model output is printed unreviewed.** Nobody proof-reads a drink ticket
   at 23:40 on a Friday.

The load-bearing defense against prompt injection is not the phrase blocklist;
it is `sanitize_persona`'s **28-character cap and letters-only charset**. An
attacker with no newlines, no punctuation beyond `-'.`, and 28 characters has
almost nothing to work with. The blocklist is defense in depth, and the output
guard assumes it failed.

`guard_vignette` doubles as a **hardware** guard: the printer speaks CP858 and
nothing else, so any character that cannot be encoded would print as garbage.
Safety and legibility happen to want the same check.
"""

from __future__ import annotations

import re
import unicodedata

from ..text import ascii_fold
from ..text import strip_invisible as _strip_invisible
from ..text import to_printable

#: The persona is interpolated into the prompt between these markers so the
#: model can be told, explicitly, that everything inside is data. If either
#: marker ever appears in model output, the prompt structure leaked.
DATA_OPEN = "<<<GUEST_DATA"
DATA_CLOSE = "GUEST_DATA>>>"

MAX_PERSONA_LENGTH = 28
MIN_PERSONA_LETTERS = 2

_PERSONA_PUNCT = set(" -'.")

# Placeholder stems only. The real per-language lists are curated separately and
# injected via `extra_blocklist` — see the spec: an innocuous word in one
# language is an insult in another, and that is not visible from either side.
DEFAULT_BLOCKLIST: tuple[str, ...] = (
    "fuck", "shit", "bitch", "cunt", "nazi", "hitler", "rape",
)

# Instruction-shaped fragments. Mostly unreachable inside 28 characters, kept
# because the cost is a substring scan and the failure mode is a printed ticket.
_INJECTION_MARKERS: tuple[str, ...] = (
    "ignore previous", "ignore all", "disregard", "system prompt",
    "you are now", "new instruction", "override",
)

# The model is told not to celebrate volume (see `chronicle.SYSTEM_PROMPT`).
# This catches it having done so anyway. Atmos runs in a bar; a ticket that
# congratulates someone for drinking fast is not a joke the venue wants to own.
_DISCOURAGED: tuple[str, ...] = (
    "get drunk", "getting drunk", "hammered", "wasted", "blackout",
    "chug", "down it", "keep them coming", "drink it all",
)

_URL_RE = re.compile(r"(https?://|www\.|\S+@\S+\.\w)", re.IGNORECASE)
_HANDLE_RE = re.compile(r"[@#]\w{2,}")
_DIGIT_RUN_RE = re.compile(r"\d{6,}")
_AI_TELL_RE = re.compile(
    # "here is" alone is legitimate noir ("Here is nothing but rain"), so only
    # the assistant-preamble forms are matched.
    r"\b(as an ai|language model|i cannot|i'm sorry|here (is|are) your|here's your)\b",
    re.IGNORECASE,
)

_SEPARATORS_RE = re.compile(r"[ .'\-]+")


class ContentRejected(Exception):
    """Base class. Carries a machine-readable `reason` for metrics and UI."""

    def __init__(self, reason: str, message: str = "") -> None:
        self.reason = reason
        super().__init__(message or reason)


class PersonaRejected(ContentRejected):
    """The guest's typed persona is unusable. The UI offers a rolled one."""


class VignetteRejected(ContentRejected):
    """Model output is unusable. The engine falls back to the procedural story."""


def _contains_any(haystack_cf: str, needles: tuple[str, ...]) -> str | None:
    # Needles are casefolded here, not at the call site: the curated per-language
    # lists are written by people who cannot be expected to know the convention,
    # and a capitalised entry that silently never matches is the worst outcome.
    for needle in needles:
        if needle.casefold() in haystack_cf:
            return needle
    return None


def _match_forms(printable: str) -> tuple[str, ...]:
    """Every spelling of `printable` the blocklist should be tested against.

    Three views, because an attacker only needs one to be missed: the text as
    printed, an ASCII fold that collapses accented spellings, and — when the
    text is mostly single characters — a de-separated squash.
    """
    forms = {printable.casefold(), ascii_fold(printable).casefold()}
    for form in tuple(forms):
        squashed = _deseparated(form)
        if squashed:
            forms.add(squashed)
    return tuple(forms)


def _deseparated(folded: str) -> str | None:
    """Return `folded` with separators removed, but only for spaced-out text.

    Catches `f u c k` and `f.u.c.k` without creating a Scunthorpe problem: the
    squashed form is only produced when the text is mostly single characters,
    so ordinary names like `The Locksmith` are never re-scanned (and so never
    trip on a substring that spans two innocent words).
    """
    tokens = [t for t in _SEPARATORS_RE.split(folded) if t]
    if sum(1 for t in tokens if len(t) == 1) >= 3:
        return "".join(tokens)
    return None


def sanitize_persona(
    raw: str | None,
    *,
    max_length: int = MAX_PERSONA_LENGTH,
    extra_blocklist: tuple[str, ...] = (),
) -> str:
    """Clean and validate a guest-typed persona, or raise `PersonaRejected`.

    On rejection the UI shows one line — *"Let's try a noir one instead"* — and
    a pre-rolled persona. It never explains which rule was broken; explaining
    the filter turns the filter into a game.
    """
    if raw is None:
        raise PersonaRejected("empty")

    # Whitespace collapses to spaces *before* control characters are dropped,
    # so "Vance\nobey me" becomes "Vance obey me" rather than "Vanceobey me".
    text = unicodedata.normalize("NFKC", raw)
    text = re.sub(r"\s+", " ", text)
    text = _strip_invisible(text)
    text = re.sub(r"\s+", " ", text).strip(" -'.")

    if not text:
        raise PersonaRejected("empty")
    # Cheap pre-check so a pathological input cannot be expanded by
    # transliteration before it is bounded at all.
    if len(text) > max_length * 2:
        raise PersonaRejected("too_long")

    for ch in text:
        if not (ch.isalpha() or ch in _PERSONA_PUNCT):
            raise PersonaRejected("charset")

    # Everything below inspects the *printable* form, which is what actually
    # reaches the prompt and the paper. Scanning the pre-transliteration text
    # would let `šhit` and `fúck` through to be normalised afterwards, and
    # `þ`/`œ`/`…` expand, so a 26-character input can print at 52.
    try:
        printable = to_printable(text)
    except UnicodeEncodeError:
        raise PersonaRejected("unprintable") from None

    if len(printable) > max_length:
        raise PersonaRejected("too_long")
    if sum(1 for ch in printable if ch.isalpha()) < MIN_PERSONA_LETTERS:
        raise PersonaRejected("too_short")

    blocklist = DEFAULT_BLOCKLIST + tuple(extra_blocklist)
    for haystack in _match_forms(printable):
        if _contains_any(haystack, _INJECTION_MARKERS):
            raise PersonaRejected("injection")
        if _contains_any(haystack, blocklist):
            raise PersonaRejected("blocklist")

    return printable


def guard_vignette(
    raw: str | None,
    *,
    min_lines: int = 2,
    max_lines: int = 5,
    max_chars: int = 340,
    extra_blocklist: tuple[str, ...] = (),
) -> str:
    """Validate model output before it reaches paper, or raise `VignetteRejected`.

    Every rejection is recoverable: the engine prints the procedural fallback
    instead. A ticket must always print, so nothing here is allowed to be fatal.
    """
    # Providers are *typed* `-> str`, but OpenAI-compatible endpoints
    # increasingly return a list of content parts. Without this the engine's
    # "never raises" contract dies on a TypeError inside `normalize`.
    if not isinstance(raw, str):
        raise VignetteRejected("not_text")

    text = unicodedata.normalize("NFKC", raw)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _strip_invisible(text, keep=frozenset("\n"))
    text = re.sub(r"[ \t]+", " ", text)
    text = text.replace("```", " ").replace("**", "").replace("*", "")

    lines = [ln.strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]

    if not lines:
        raise VignetteRejected("empty")
    if len(lines) < min_lines:
        raise VignetteRejected("too_few_lines")
    if len(lines) > max_lines:
        raise VignetteRejected("too_many_lines")

    # Same rule as `sanitize_persona`: inspect what will be printed, not what
    # arrived. `Þe fuċk is that` normalises into something quite different.
    try:
        printable = to_printable("\n".join(lines))
    except UnicodeEncodeError:
        raise VignetteRejected("unprintable") from None

    if len(printable) > max_chars:
        raise VignetteRejected("too_long")

    if DATA_OPEN in printable or DATA_CLOSE in printable:
        raise VignetteRejected("prompt_leak")
    if _URL_RE.search(printable):
        raise VignetteRejected("contains_url")
    if _HANDLE_RE.search(printable):
        raise VignetteRejected("contains_handle")
    if _DIGIT_RUN_RE.search(printable):
        raise VignetteRejected("contains_digits")
    if _AI_TELL_RE.search(printable):
        raise VignetteRejected("assistant_voice")

    blocklist = DEFAULT_BLOCKLIST + tuple(extra_blocklist)
    for haystack in _match_forms(printable):
        if _contains_any(haystack, blocklist):
            raise VignetteRejected("blocklist")
        if _contains_any(haystack, _DISCOURAGED):
            raise VignetteRejected("discouraged_motif")

    return printable
