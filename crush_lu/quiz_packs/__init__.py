"""Named question packs for Quiz Night.

A *pack* is pure data: a list of rounds, each with trilingual titles and a list
of trilingual questions. ``generate_crush_quiz`` turns a pack into QuizRound /
QuizQuestion rows for a given QuizEvent.

Pack schema
-----------
Each round is a dict::

    {
        "title_en": str, "title_de": str, "title_fr": str,
        "time_per_question": int,   # optional, defaults to 30 seconds
        "is_bonus": bool,           # optional, defaults to False
        "questions": [ ... ],
    }

Each question is a dict::

    {
        "type": "multiple_choice" | "true_false" | "open_ended",
        "text_en": str, "text_de": str, "text_fr": str,
        "correct_answer_en": str, "correct_answer_de": str, "correct_answer_fr": str,
        "choices_en": [{"text": str, "is_correct": bool}, ...],   # [] for open_ended
        "choices_de": [...], "choices_fr": [...],
        "media": { ... },           # optional, see below
    }

The optional ``media`` block is language-neutral (a song is a song in every
language) and takes one of two forms:

*External embed* — seeded directly, no manual step::

    "media": {
        "kind": "video" | "audio",
        "url": "https://www.youtube.com/watch?v=...",
        "description": "Accessible label, required.",
    }

Only YouTube / Vimeo / Spotify / SoundCloud hosts pass validation; watch URLs
are rewritten to their embed form on save (see ``models.quiz.normalize_embed_url``).

*Pending upload* — an image, which the embed allowlist cannot serve::

    "media": {
        "kind": "image",
        "upload": "suggested-filename.jpg",
        "description": "Alt text, required.",
        "credit": "Source URL / licence, for the upload manifest.",
    }

Images have no external-URL path: ``media_url`` rejects every host that isn't
one of the four embed providers, so an image must be a ``media_file`` upload.
The seeder therefore leaves these questions with ``media_kind="none"`` (a valid,
immediately playable row) and reports them as a manifest for a coach to upload
through the quiz authoring UI.
"""

from crush_lu.quiz_packs import classic, media_love

#: Registry of available packs: name -> list of round dicts.
PACKS = {
    "classic": classic.QUIZ_ROUNDS,
    "media-love": media_love.QUIZ_ROUNDS,
}

#: The pack used when none is named — preserves the original command behaviour.
DEFAULT_PACK = "classic"


def get_pack(name):
    """Return the rounds for pack *name*.

    Raises:
        KeyError: if *name* is not a registered pack.
    """
    return PACKS[name]


def pack_names():
    """Return the registered pack names, sorted for stable help text."""
    return sorted(PACKS)
