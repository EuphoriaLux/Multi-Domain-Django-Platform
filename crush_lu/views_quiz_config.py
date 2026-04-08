import json

from django.contrib import messages
from django.db.models import Max
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from crush_lu.decorators import coach_required
from crush_lu.models.events import MeetupEvent
from crush_lu.models.quiz import QuizEvent, QuizQuestion, QuizRound

LANGUAGES = ("en", "de", "fr")


def _parse_choices_json(raw_json, question_type):
    """Parse and validate choices JSON from form submission.

    Normalizes camelCase ``isCorrect`` (from Alpine) to snake_case
    ``is_correct`` (model convention).  Returns ``None`` on invalid input.
    """
    if question_type == "open_ended":
        return []
    if not raw_json:
        return None
    try:
        choices = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(choices, list) or len(choices) < 2:
        return None
    for choice in choices:
        if not isinstance(choice, dict):
            return None
        if "text" not in choice or (
            "isCorrect" not in choice and "is_correct" not in choice
        ):
            return None
    return [
        {
            "text": str(c["text"]).strip(),
            "is_correct": bool(c.get("isCorrect", c.get("is_correct", False))),
        }
        for c in choices
    ]


def _get_quiz_or_none(event):
    """Return the QuizEvent for *event*, or ``None``."""
    try:
        return event.quiz
    except QuizEvent.DoesNotExist:
        return None


def _choices_for_alpine(choices):
    """Convert model choices (snake_case) to Alpine-friendly camelCase."""
    if not choices:
        return "[]"
    return json.dumps(
        [
            {"text": c.get("text", ""), "isCorrect": c.get("is_correct", False)}
            for c in choices
        ]
    )


# ---------------------------------------------------------------------------
# Main config dashboard
# ---------------------------------------------------------------------------


@coach_required
def coach_quiz_config(request, event_id):
    event = get_object_or_404(MeetupEvent, id=event_id)
    quiz = _get_quiz_or_none(event)
    rounds = []
    total_questions = 0
    if quiz:
        rounds = quiz.rounds.prefetch_related("questions").order_by("sort_order")
        total_questions = sum(r.questions.count() for r in rounds)

    return render(
        request,
        "crush_lu/coach_quiz_config.html",
        {
            "event": event,
            "quiz": quiz,
            "rounds": rounds,
            "total_questions": total_questions,
            "coach": request.coach,
        },
    )


# ---------------------------------------------------------------------------
# Create QuizEvent
# ---------------------------------------------------------------------------


@coach_required
@require_POST
def coach_quiz_create(request, event_id):
    event = get_object_or_404(MeetupEvent, id=event_id)
    num_tables = request.POST.get("num_tables", "").strip()
    try:
        num_tables_int = int(num_tables) if num_tables else 0
    except (ValueError, TypeError):
        num_tables_int = 0
    if num_tables_int < 2:
        messages.error(request, _("Number of tables is required (minimum 2)."))
        return redirect("crush_lu:coach_quiz_config", event_id=event.id)
    quiz, created = QuizEvent.objects.get_or_create(
        event=event,
        defaults={
            "created_by": request.user,
            "status": "draft",
            "num_tables": num_tables_int,
        },
    )
    if not created and not quiz.num_tables:
        quiz.num_tables = num_tables_int
        quiz.save(update_fields=["num_tables"])
    quiz.ensure_tables()
    messages.success(request, _("Quiz created for this event."))
    return redirect("crush_lu:coach_quiz_config", event_id=event.id)


@coach_required
@require_POST
def coach_quiz_update_tables(request, event_id):
    """Update the number of tables for an existing quiz."""
    event = get_object_or_404(MeetupEvent, id=event_id)
    quiz = get_object_or_404(QuizEvent, event=event)

    num_tables = request.POST.get("num_tables", "").strip()
    try:
        num_tables_int = int(num_tables) if num_tables else 0
    except (ValueError, TypeError):
        num_tables_int = 0
    if num_tables_int < 2:
        messages.error(request, _("Number of tables is required (minimum 2)."))
        return redirect("crush_lu:coach_quiz_config", event_id=event.id)

    quiz.num_tables = num_tables_int
    quiz.save(update_fields=["num_tables"])
    quiz.ensure_tables()
    messages.success(request, _("Table count updated to %d.") % quiz.num_tables)
    return redirect("crush_lu:coach_quiz_config", event_id=event.id)


# ---------------------------------------------------------------------------
# Round CRUD
# ---------------------------------------------------------------------------


@coach_required
def coach_quiz_round_add(request, event_id):
    event = get_object_or_404(MeetupEvent, id=event_id)
    quiz = get_object_or_404(QuizEvent, event=event)

    if request.method == "POST":
        # At least one language title is required
        titles = {
            lang: request.POST.get(f"title_{lang}", "").strip() for lang in LANGUAGES
        }
        if not any(titles.values()):
            messages.error(
                request, _("Round title is required in at least one language.")
            )
            return render(
                request,
                "crush_lu/coach_quiz_round_form.html",
                {"event": event, "quiz": quiz, "coach": request.coach},
            )

        time_per_question = int(request.POST.get("time_per_question", 30) or 30)
        is_bonus = request.POST.get("is_bonus") == "on"
        next_order = (quiz.rounds.aggregate(m=Max("sort_order"))["m"] or 0) + 1

        quiz_round = QuizRound(
            quiz=quiz,
            sort_order=next_order,
            time_per_question=max(5, time_per_question),
            is_bonus=is_bonus,
        )
        for lang in LANGUAGES:
            setattr(quiz_round, f"title_{lang}", titles[lang] or None)
        quiz_round.save()

        messages.success(request, _("Round added."))
        return redirect("crush_lu:coach_quiz_config", event_id=event.id)

    return render(
        request,
        "crush_lu/coach_quiz_round_form.html",
        {"event": event, "quiz": quiz, "coach": request.coach},
    )


@coach_required
def coach_quiz_round_edit(request, event_id, round_id):
    event = get_object_or_404(MeetupEvent, id=event_id)
    quiz = get_object_or_404(QuizEvent, event=event)
    quiz_round = get_object_or_404(QuizRound, id=round_id, quiz=quiz)

    if request.method == "POST":
        titles = {
            lang: request.POST.get(f"title_{lang}", "").strip() for lang in LANGUAGES
        }
        if not any(titles.values()):
            messages.error(
                request, _("Round title is required in at least one language.")
            )
            return render(
                request,
                "crush_lu/coach_quiz_round_form.html",
                {
                    "event": event,
                    "quiz": quiz,
                    "quiz_round": quiz_round,
                    "coach": request.coach,
                },
            )

        for lang in LANGUAGES:
            setattr(quiz_round, f"title_{lang}", titles[lang] or None)
        quiz_round.time_per_question = max(
            5, int(request.POST.get("time_per_question", 30) or 30)
        )
        quiz_round.is_bonus = request.POST.get("is_bonus") == "on"
        quiz_round.sort_order = int(
            request.POST.get("sort_order", quiz_round.sort_order)
            or quiz_round.sort_order
        )
        quiz_round.save()
        messages.success(request, _("Round updated."))
        return redirect("crush_lu:coach_quiz_config", event_id=event.id)

    return render(
        request,
        "crush_lu/coach_quiz_round_form.html",
        {
            "event": event,
            "quiz": quiz,
            "quiz_round": quiz_round,
            "coach": request.coach,
        },
    )


@coach_required
@require_POST
def coach_quiz_round_delete(request, event_id, round_id):
    event = get_object_or_404(MeetupEvent, id=event_id)
    quiz = get_object_or_404(QuizEvent, event=event)
    quiz_round = get_object_or_404(QuizRound, id=round_id, quiz=quiz)

    if quiz.status not in ("draft", "paused"):
        messages.error(
            request, _("Cannot delete rounds from an active or finished quiz.")
        )
        return redirect("crush_lu:coach_quiz_config", event_id=event.id)

    quiz_round.delete()
    messages.success(request, _("Round deleted."))
    return redirect("crush_lu:coach_quiz_config", event_id=event.id)


# ---------------------------------------------------------------------------
# Question CRUD
# ---------------------------------------------------------------------------


@coach_required
def coach_quiz_question_add(request, event_id, round_id):
    event = get_object_or_404(MeetupEvent, id=event_id)
    quiz = get_object_or_404(QuizEvent, event=event)
    quiz_round = get_object_or_404(QuizRound, id=round_id, quiz=quiz)

    if request.method == "POST":
        return _save_question(request, event, quiz_round, question=None)

    return render(
        request,
        "crush_lu/coach_quiz_question_form.html",
        {
            "event": event,
            "quiz": quiz,
            "quiz_round": quiz_round,
            "coach": request.coach,
        },
    )


@coach_required
def coach_quiz_question_edit(request, event_id, question_id):
    event = get_object_or_404(MeetupEvent, id=event_id)
    quiz = get_object_or_404(QuizEvent, event=event)
    question = get_object_or_404(QuizQuestion, id=question_id, round__quiz=quiz)

    if request.method == "POST":
        return _save_question(request, event, question.round, question=question)

    # Build per-language choices JSON for the Alpine component
    choices_by_lang = {}
    for lang in LANGUAGES:
        lang_choices = getattr(question, f"choices_{lang}", None)
        choices_by_lang[lang] = (
            _choices_for_alpine(lang_choices) if lang_choices else "[]"
        )

    return render(
        request,
        "crush_lu/coach_quiz_question_form.html",
        {
            "event": event,
            "quiz": quiz,
            "quiz_round": question.round,
            "question": question,
            "choices_json_en": choices_by_lang["en"],
            "choices_json_de": choices_by_lang["de"],
            "choices_json_fr": choices_by_lang["fr"],
            "coach": request.coach,
        },
    )


@coach_required
@require_POST
def coach_quiz_question_delete(request, event_id, question_id):
    event = get_object_or_404(MeetupEvent, id=event_id)
    quiz = get_object_or_404(QuizEvent, event=event)
    question = get_object_or_404(QuizQuestion, id=question_id, round__quiz=quiz)

    if quiz.status not in ("draft", "paused"):
        messages.error(
            request, _("Cannot delete questions from an active or finished quiz.")
        )
        return redirect("crush_lu:coach_quiz_config", event_id=event.id)

    question.delete()
    messages.success(request, _("Question deleted."))
    return redirect("crush_lu:coach_quiz_config", event_id=event.id)


# ---------------------------------------------------------------------------
# Shared helper for question create / update
# ---------------------------------------------------------------------------


def _save_question(request, event, quiz_round, question=None):
    """Validate and save a quiz question from POST data (multilingual)."""
    # Gather per-language text fields
    texts = {lang: request.POST.get(f"text_{lang}", "").strip() for lang in LANGUAGES}
    question_type = request.POST.get("question_type", "multiple_choice")
    points = int(request.POST.get("points", 10) or 10)
    correct_answers = {
        lang: request.POST.get(f"correct_answer_{lang}", "").strip()
        for lang in LANGUAGES
    }

    # At least one language text is required
    if not any(texts.values()):
        messages.error(
            request, _("Question text is required in at least one language.")
        )
        return _render_question_form(request, event, quiz_round, question)

    # Parse choices per language
    choices_by_lang = {}
    if question_type in ("multiple_choice", "true_false"):
        for lang in LANGUAGES:
            choices_raw = request.POST.get(f"choices_json_{lang}", "[]")
            parsed = _parse_choices_json(choices_raw, question_type)
            if parsed is not None and len(parsed) >= 2:
                if not any(c["is_correct"] for c in parsed):
                    messages.error(
                        request, _("At least one choice must be marked as correct.")
                    )
                    return _render_question_form(request, event, quiz_round, question)
            choices_by_lang[lang] = parsed

        # At least one language must have valid choices
        if not any(
            v for v in choices_by_lang.values() if v is not None and len(v) >= 2
        ):
            messages.error(
                request, _("Please provide answer choices in at least one language.")
            )
            return _render_question_form(request, event, quiz_round, question)
    else:
        for lang in LANGUAGES:
            choices_by_lang[lang] = []

    if question is None:
        next_order = (quiz_round.questions.aggregate(m=Max("sort_order"))["m"] or 0) + 1
        q = QuizQuestion(
            round=quiz_round,
            question_type=question_type,
            sort_order=next_order,
            points=max(1, points),
        )
    else:
        q = question
        q.question_type = question_type
        q.points = max(1, points)
        sort_order = request.POST.get("sort_order")
        if sort_order:
            q.sort_order = int(sort_order)

    for lang in LANGUAGES:
        setattr(q, f"text_{lang}", texts[lang] or None)
        setattr(q, f"correct_answer_{lang}", correct_answers[lang] or "")
        if choices_by_lang[lang] is not None:
            setattr(q, f"choices_{lang}", choices_by_lang[lang])
        elif question is None:
            setattr(q, f"choices_{lang}", [])

    q.save()

    if question is None:
        messages.success(request, _("Question added."))
    else:
        messages.success(request, _("Question updated."))

    return redirect("crush_lu:coach_quiz_config", event_id=event.id)


def _render_question_form(request, event, quiz_round, question):
    """Re-render the question form preserving POST data."""
    choices_by_lang = {}
    for lang in LANGUAGES:
        choices_by_lang[lang] = request.POST.get(f"choices_json_{lang}", "[]")

    return render(
        request,
        "crush_lu/coach_quiz_question_form.html",
        {
            "event": event,
            "quiz": quiz_round.quiz,
            "quiz_round": quiz_round,
            "question": question,
            "choices_json_en": choices_by_lang.get("en", "[]"),
            "choices_json_de": choices_by_lang.get("de", "[]"),
            "choices_json_fr": choices_by_lang.get("fr", "[]"),
            "coach": request.coach,
        },
    )
