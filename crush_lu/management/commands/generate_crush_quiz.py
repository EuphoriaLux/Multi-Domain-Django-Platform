"""
Management command to generate Crush.lu quiz rounds and questions.

Populates a QuizEvent from a named *pack* — a pure-data list of rounds with
trilingual (EN, DE, FR) questions, optionally carrying an image/video/audio
stimulus. Packs live in ``crush_lu/quiz_packs/``; see that package's docstring
for the schema. Can also be invoked as an admin action.

Usage:
    python manage.py generate_crush_quiz --quiz-id 1
    python manage.py generate_crush_quiz --quiz-id 1 --pack media-love
    python manage.py generate_crush_quiz --quiz-id 1 --pack media-love --clear
    python manage.py generate_crush_quiz --list-packs
"""

from collections import namedtuple

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from crush_lu.models.quiz import (
    QuizEvent,
    QuizQuestion,
    QuizRound,
    is_embed_url_allowed,
)
from crush_lu.quiz_packs import DEFAULT_PACK, PACKS, pack_names

# Re-exported for backwards compatibility: this module was the original home of
# the classic pack data before the pack registry existed.
from crush_lu.quiz_packs.classic import QUIZ_ROUNDS  # noqa: F401

# Map question type strings to model choices
QUESTION_TYPE_MAP = {
    "multiple_choice": "multiple_choice",
    "true_false": "true_false",
    "open_ended": "open_ended",
}

#: Media kinds a pack can seed without a manual upload step.
EMBEDDABLE_KINDS = ("video", "audio")

#: Default seconds per question when a round doesn't override it.
DEFAULT_TIME_PER_QUESTION = 30

#: What ``populate_quiz`` reports back. ``pending_uploads`` lists the image
#: questions a coach still has to attach a file to (see ``validate_pack``).
PopulateResult = namedtuple(
    "PopulateResult", ["rounds_created", "questions_created", "pending_uploads"]
)

#: One image question awaiting a manual upload through the authoring UI.
PendingUpload = namedtuple(
    "PendingUpload",
    ["round_title", "question_text", "filename", "description", "credit"],
)


def validate_pack(rounds):
    """Return a list of human-readable problems with *rounds*.

    Checked before any row is written so a malformed pack fails fast instead of
    leaving a half-seeded quiz behind. The media rules mirror
    ``QuizQuestion.clean``, which ``Model.save`` never runs — without this a bad
    embed URL would seed silently and only surface as "Media unavailable" on the
    projector mid-event.
    """
    errors = []

    # An empty pack would otherwise sail through: the loop below never runs, so
    # zero rounds means zero errors and `populate_quiz` reports success having
    # written nothing. With `--clear` that is destructive — the delete happens
    # first, so a stub pack would wipe a quiz and put nothing back.
    if not rounds:
        errors.append("pack has no rounds")

    for r_idx, round_data in enumerate(rounds):
        where_round = f"round {r_idx + 1}"
        for key in ("title_en", "title_de", "title_fr"):
            if not (round_data.get(key) or "").strip():
                errors.append(f"{where_round}: missing {key}")

        questions = round_data.get("questions") or []
        if not questions:
            errors.append(f"{where_round}: has no questions")

        for q_idx, q_data in enumerate(questions):
            where = f"{where_round} question {q_idx + 1}"

            q_type = q_data.get("type")
            if q_type not in QUESTION_TYPE_MAP:
                errors.append(f"{where}: unknown type {q_type!r}")

            for lang in ("en", "de", "fr"):
                if not (q_data.get(f"text_{lang}") or "").strip():
                    errors.append(f"{where}: missing text_{lang}")
                if not (q_data.get(f"correct_answer_{lang}") or "").strip():
                    errors.append(f"{where}: missing correct_answer_{lang}")

            # Choice-based questions need choices, exactly one of them correct,
            # and the same option count in every language.
            if q_type in ("multiple_choice", "true_false"):
                counts = set()
                for lang in ("en", "de", "fr"):
                    choices = q_data.get(f"choices_{lang}") or []
                    counts.add(len(choices))
                    if not choices:
                        errors.append(f"{where}: choices_{lang} is empty")
                        continue
                    correct = [c for c in choices if c.get("is_correct")]
                    if len(correct) != 1:
                        errors.append(
                            f"{where}: choices_{lang} needs exactly one correct "
                            f"option (found {len(correct)})"
                        )
                if len(counts) > 1:
                    errors.append(
                        f"{where}: option counts differ across languages {sorted(counts)}"
                    )
            elif q_type == "open_ended":
                for lang in ("en", "de", "fr"):
                    if q_data.get(f"choices_{lang}"):
                        errors.append(
                            f"{where}: open_ended must have empty choices_{lang}"
                        )

            errors.extend(_validate_media(q_data.get("media"), where))

    return errors


def _validate_media(media, where):
    """Validate one question's optional media block."""
    if not media:
        return []

    errors = []
    kind = media.get("kind")
    if kind not in ("image", "video", "audio"):
        return [f"{where}: media kind must be image/video/audio (got {kind!r})"]

    # The accessible label is the only reason a screen-reader user isn't simply
    # locked out of a media question, so it is required rather than optional.
    if not (media.get("description") or "").strip():
        errors.append(f"{where}: media needs a description (alt text / audio label)")

    if kind in EMBEDDABLE_KINDS:
        url = (media.get("url") or "").strip()
        if not url:
            errors.append(f"{where}: {kind} media needs an external embed url")
        elif not is_embed_url_allowed(url):
            errors.append(
                f"{where}: media url host is not on the embed allowlist "
                f"(YouTube/Vimeo/Spotify/SoundCloud): {url}"
            )
    else:  # image
        if media.get("url"):
            errors.append(
                f"{where}: images cannot use an external url — the allowlist "
                f"serves embeds only. Use 'upload' with a filename instead."
            )
        if not (media.get("upload") or "").strip():
            errors.append(f"{where}: image media needs an 'upload' filename")

    return errors


@transaction.atomic
def populate_quiz(quiz, clear=False, pack=DEFAULT_PACK):
    """Populate a QuizEvent from a named question pack.

    Args:
        quiz: QuizEvent instance to populate.
        clear: If True, delete existing rounds/questions first.
        pack: Registered pack name (see ``crush_lu.quiz_packs.PACKS``).

    Returns:
        PopulateResult(rounds_created, questions_created, pending_uploads)

    Raises:
        KeyError: if *pack* is not registered.
        ValueError: if the pack fails validation (nothing is written).
    """
    rounds = PACKS[pack]

    problems = validate_pack(rounds)
    if problems:
        raise ValueError(f"Pack '{pack}' is invalid:\n  " + "\n  ".join(problems))

    if clear:
        quiz.rounds.all().delete()

    rounds_created = 0
    questions_created = 0
    pending_uploads = []

    for round_idx, round_data in enumerate(rounds):
        quiz_round = QuizRound(
            quiz=quiz,
            sort_order=round_idx,
            time_per_question=round_data.get(
                "time_per_question", DEFAULT_TIME_PER_QUESTION
            ),
            is_bonus=round_data.get("is_bonus", False),
        )
        # Set translated title fields directly
        quiz_round.title_en = round_data["title_en"]
        quiz_round.title_de = round_data["title_de"]
        quiz_round.title_fr = round_data["title_fr"]
        quiz_round.save()
        rounds_created += 1

        for q_idx, q_data in enumerate(round_data["questions"]):
            question = QuizQuestion(
                round=quiz_round,
                question_type=QUESTION_TYPE_MAP[q_data["type"]],
                sort_order=q_idx,
                points=q_data.get("points", 10),
            )
            # Set translated text fields
            question.text_en = q_data["text_en"]
            question.text_de = q_data["text_de"]
            question.text_fr = q_data["text_fr"]

            # Set translated choices fields
            question.choices_en = q_data["choices_en"]
            question.choices_de = q_data["choices_de"]
            question.choices_fr = q_data["choices_fr"]

            # Set translated correct_answer fields
            question.correct_answer_en = q_data["correct_answer_en"]
            question.correct_answer_de = q_data["correct_answer_de"]
            question.correct_answer_fr = q_data["correct_answer_fr"]

            # --- Optional media stimulus ---------------------------------
            # Embeds seed straight onto the row. Images can't: the media_url
            # allowlist serves the four embed providers only, so an image has
            # to arrive as an uploaded file. Those questions are seeded
            # media-less (a valid, playable row) and reported back so a coach
            # can attach the file through the authoring UI.
            media = q_data.get("media") or {}
            kind = media.get("kind")
            if kind in EMBEDDABLE_KINDS:
                question.media_kind = kind
                question.media_url = media["url"].strip()
                question.media_description = media.get("description", "").strip()[:300]
            elif kind == "image":
                pending_uploads.append(
                    PendingUpload(
                        round_title=round_data["title_en"],
                        question_text=q_data["text_en"],
                        filename=media["upload"],
                        description=media.get("description", ""),
                        credit=media.get("credit", ""),
                    )
                )

            question.save()
            questions_created += 1

    return PopulateResult(rounds_created, questions_created, pending_uploads)


class Command(BaseCommand):
    help = "Generate Crush.lu quiz rounds and questions for a QuizEvent"

    def add_arguments(self, parser):
        parser.add_argument(
            "--quiz-id",
            type=int,
            help="ID of the QuizEvent to populate with rounds and questions",
        )
        parser.add_argument(
            "--pack",
            default=DEFAULT_PACK,
            choices=pack_names(),
            help=f"Question pack to seed (default: {DEFAULT_PACK})",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Remove existing rounds and questions before generating",
        )
        parser.add_argument(
            "--list-packs",
            action="store_true",
            help="List the available packs with their round/question counts and exit",
        )

    def handle(self, *args, **options):
        if options["list_packs"]:
            self._list_packs()
            return

        quiz_id = options["quiz_id"]
        if quiz_id is None:
            raise CommandError("--quiz-id is required (or pass --list-packs).")

        pack = options["pack"]
        clear = options["clear"]

        try:
            quiz = QuizEvent.objects.get(pk=quiz_id)
        except QuizEvent.DoesNotExist:
            raise CommandError(f"QuizEvent with id={quiz_id} does not exist.")

        existing_rounds = quiz.rounds.count()
        if existing_rounds and not clear:
            raise CommandError(
                f"QuizEvent {quiz_id} already has {existing_rounds} rounds. "
                f"Use --clear to remove them first."
            )

        try:
            result = populate_quiz(quiz, clear=clear, pack=pack)
        except ValueError as exc:
            raise CommandError(str(exc))

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully created {result.rounds_created} rounds and "
                f"{result.questions_created} questions for QuizEvent {quiz_id} "
                f'("{quiz.event}") from pack "{pack}".'
            )
        )
        self._report_pending_uploads(result.pending_uploads)

    def _list_packs(self):
        for name in pack_names():
            rounds = PACKS[name]
            questions = sum(len(r.get("questions") or []) for r in rounds)
            with_media = sum(
                1 for r in rounds for q in (r.get("questions") or []) if q.get("media")
            )
            problems = validate_pack(rounds)
            state = (
                self.style.SUCCESS("ok")
                if not problems
                else self.style.ERROR(f"{len(problems)} problem(s)")
            )
            self.stdout.write(
                f"{name}: {len(rounds)} rounds, {questions} questions, "
                f"{with_media} with media — {state}"
            )

    def _report_pending_uploads(self, pending_uploads):
        """Print the manual-upload manifest for image questions."""
        if not pending_uploads:
            return
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                f"{len(pending_uploads)} image question(s) need a file uploaded "
                f"through the quiz authoring UI — they were seeded without media:"
            )
        )
        for item in pending_uploads:
            self.stdout.write(f"  [{item.round_title}] {item.question_text}")
            self.stdout.write(f"      file: {item.filename}")
            self.stdout.write(f"      alt : {item.description}")
            if item.credit:
                self.stdout.write(f"      src : {item.credit}")
