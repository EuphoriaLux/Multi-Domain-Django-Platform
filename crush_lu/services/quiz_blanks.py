"""Hidden words in quiz question text (``{{word}}`` markup).

A coach marks the word guests have to guess with double braces, for example
``With the taste of the poison, {{paradise}}``. Everything that leaves the
server before the reveal carries a fixed-length run of underscores instead of
the word, so the answer never reaches a guest's browser, network log or page
source. The word comes back only in the segments sent once every table has
been scored (``revealed_segments*`` keys).

The mask is deliberately plain underscores: the clients already render any run
of three or more underscores as an animated blank, a hand-typed ``___`` keeps
working, and an old cached client still shows a readable gap. Its length is
fixed so the blank does not give away how long the word is.
"""

import re

QUIZ_LANGUAGES = ("en", "de", "fr")

# ``{{ word }}``: no nested braces, at least one non-space character.
BLANK_MARKUP = re.compile(r"\{\{([^{}]*\S[^{}]*)\}\}")

BLANK_MASK = "_" * 8


def has_blanks(text):
    """True when ``text`` contains at least one ``{{word}}`` marker."""
    return bool(text) and BLANK_MARKUP.search(text) is not None


def mask_blanks(text):
    """Replace every ``{{word}}`` with the fixed-length blank."""
    if not text:
        return text
    return BLANK_MARKUP.sub(BLANK_MASK, text)


def reveal_segments(text):
    """Split ``text`` into alternating text and word segments.

    Even indexes are plain text and odd indexes are the hidden words, so
    ``"a {{b}} c"`` gives ``["a ", "b", " c"]``. A text with no marker gives a
    single segment.
    """
    segments = []
    position = 0
    for match in BLANK_MARKUP.finditer(text or ""):
        segments.append(text[position : match.start()])
        segments.append(match.group(1).strip())
        position = match.end()
    segments.append((text or "")[position:])
    return segments


def revealed_segments_payload(question):
    """Segments for the reveal, keyed like the question's ``text`` fields.

    Returns ``{}`` when no language of the question hides a word, so a
    question without markup adds nothing to the reveal message. When one does,
    every non-empty language gets an entry (a language without markup gets a
    single segment): the client picks its language with the same fallback it
    uses for ``text``, and a gap in one language would otherwise show another
    language's answer under the wrong text.
    """
    fields = {"revealed_segments": question.text}
    for lang in QUIZ_LANGUAGES:
        value = getattr(question, f"text_{lang}", None)
        if value:
            fields[f"revealed_segments_{lang}"] = value
    if not any(has_blanks(value) for value in fields.values()):
        return {}
    return {key: reveal_segments(value) for key, value in fields.items()}
