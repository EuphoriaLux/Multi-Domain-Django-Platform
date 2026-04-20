"""
Canonical schema for the Crush.lu pre-screening questionnaire.

This module is the single source of truth for the pre-screening questions,
their allowed answers, the validator, and the rule-based Readiness Score.

Design notes:
- All user-facing labels use `gettext_lazy` so they resolve per-request.
- The schema is versioned; bump ``PRE_SCREENING_SCHEMA["version"]`` when
  making a breaking change to questions or choices. Existing responses keep
  their original version number for display/migration.
- The scorer is deterministic. No LLM, no heuristics beyond the rules below.
"""
from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


PRE_SCREENING_SCHEMA: dict[str, Any] = {
    # Version 2: Section A rebuilt as "Confirm your details" — residence,
    # languages, and age_confirm switched to `readonly_confirm` questions that
    # mirror the user's existing CrushProfile instead of re-asking. Only
    # `source` survives as an actual question.
    "version": 2,
    "sections": [
        # ------------------------------------------------------------------
        # Section A — Confirm your details
        # Three of the four questions here are `readonly_confirm`: they're
        # auto-populated from CrushProfile (see `derive_readonly_value` below)
        # and render as non-editable cards with a link back to the profile
        # edit page. The value still lands in pre_screening_responses on
        # finalize so the Coach sees a consistent snapshot.
        # ------------------------------------------------------------------
        {
            "id": "logistics",
            "title": _("Confirm your details"),
            "questions": [
                {
                    "id": "residence",
                    "type": "readonly_confirm",
                    "shape": "single_select",
                    "required": True,
                    "label": _("Where do you live?"),
                    "profile_field": "location",
                    "edit_section": "account",
                    "choices": [
                        {"value": "lu_city", "label": _("Luxembourg – Luxembourg City")},
                        {"value": "lu_esch", "label": _("Luxembourg – Esch-sur-Alzette")},
                        {"value": "lu_other", "label": _("Luxembourg – other canton")},
                        {"value": "fr_border", "label": _("France (border region)")},
                        {"value": "de_border", "label": _("Germany (border region)")},
                        {"value": "be_border", "label": _("Belgium (border region)")},
                        {"value": "other", "label": _("Other")},
                    ],
                },
                {
                    "id": "languages",
                    "type": "readonly_confirm",
                    "shape": "multi_select",
                    "required": True,
                    "min_choices": 1,
                    "label": _("Languages you can comfortably speak at a social event"),
                    "profile_field": "event_languages",
                    "edit_section": "preferences",
                    "choices": [
                        {"value": "en", "label": _("English")},
                        {"value": "fr", "label": _("French")},
                        {"value": "de", "label": _("German")},
                        {"value": "lu", "label": _("Luxembourgish")},
                        {"value": "other", "label": _("Other")},
                    ],
                },
                {
                    "id": "age_confirm",
                    "type": "readonly_confirm",
                    "shape": "yes_no",
                    "required": True,
                    "label": _("You are 18 or older"),
                    "profile_field": "date_of_birth",
                    "edit_section": "account",
                },
                {
                    "id": "source",
                    "type": "single_select",
                    "required": True,
                    "label": _("How did you hear about Crush.lu?"),
                    "choices": [
                        {"value": "friend", "label": _("A friend or family member")},
                        {"value": "social", "label": _("Social media (Instagram, Facebook, TikTok)")},
                        {"value": "search", "label": _("Google or other search engine")},
                        {"value": "news", "label": _("RTL, Luxemburger Wort, or other news")},
                        {"value": "event", "label": _("At an event")},
                        {"value": "ai", "label": _("ChatGPT or another AI assistant")},
                        {"value": "other", "label": _("Somewhere else")},
                    ],
                },
            ],
        },
        # ------------------------------------------------------------------
        # Section B — Concept
        # ------------------------------------------------------------------
        {
            "id": "concept",
            "title": _("Understanding Crush.lu"),
            "questions": [
                {
                    "id": "what_is_crush",
                    "type": "single_select",
                    "required": True,
                    "label": _("What is Crush.lu to you?"),
                    "choices": [
                        {"value": "events", "label": _("A way to meet people in real life through events")},
                        {"value": "tinder", "label": _("An online dating app like Tinder or Bumble")},
                        {"value": "matchmaking", "label": _("A matchmaking service that finds dates for me")},
                        {"value": "unsure", "label": _("I'm still figuring it out")},
                    ],
                },
                {
                    "id": "event_frequency",
                    "type": "single_select",
                    "required": True,
                    "label": _(
                        "Our events are small (15–25 people) and in person. How often would you realistically attend?"
                    ),
                    "choices": [
                        {"value": "weekly", "label": _("Once a week if there's a good one")},
                        {"value": "monthly", "label": _("Once or twice a month")},
                        {"value": "few_per_year", "label": _("A few times a year")},
                        {"value": "online_only", "label": _("I mostly want online dating, events not so much")},
                    ],
                },
                {
                    "id": "relationship_goal",
                    "type": "single_select",
                    "required": True,
                    "label": _("Which of these sounds most like what you want?"),
                    "choices": [
                        {"value": "many_people", "label": _("Meet many people quickly, figure it out from there")},
                        {"value": "meaningful", "label": _("A few meaningful encounters over several months")},
                        {"value": "friendship_first", "label": _("Friendship first, see if something develops")},
                        {"value": "committed", "label": _("A committed relationship as soon as possible")},
                    ],
                },
                {
                    "id": "coach_attitude",
                    "type": "single_select",
                    "required": True,
                    "label": _(
                        "Our Crush Coaches review every profile and help introduce people. "
                        "How do you feel about that?"
                    ),
                    "choices": [
                        {"value": "loves", "label": _("Love it — human help makes this feel safer")},
                        {"value": "fine", "label": _("Fine with it as long as I stay in control")},
                        {"value": "curious", "label": _("Curious but unsure what the Coach actually does")},
                        {"value": "no_intermediary", "label": _("Would prefer no intermediary")},
                    ],
                },
                {
                    "id": "looking_forward_to",
                    "type": "multi_select",
                    "required": True,
                    "min_choices": 1,
                    "max_choices": 3,
                    "label": _("What are you most looking forward to? (pick up to 3)"),
                    "choices": [
                        {"value": "events", "label": _("Meeting new people at events")},
                        {"value": "discovery", "label": _("Discovering who's nearby in my canton")},
                        {"value": "coach_match", "label": _("Being matched one-on-one by a Coach")},
                        {"value": "offline_quickly", "label": _("Taking things offline quickly")},
                        {"value": "organized_dates", "label": _("Having someone organize dates for me")},
                        {"value": "exploring", "label": _("Just exploring, no pressure")},
                    ],
                },
            ],
        },
        # ------------------------------------------------------------------
        # Section C — Own Words
        # ------------------------------------------------------------------
        {
            "id": "own_words",
            "title": _("In your own words"),
            "questions": [
                {
                    "id": "hoping_to_meet",
                    "type": "text",
                    "required": True,
                    "max_length": 200,
                    "label": _("In one sentence, what kind of person are you hoping to meet?"),
                    "placeholder": _("Someone curious, who likes walking and asking questions…"),
                },
                {
                    "id": "note_to_coach",
                    "type": "text",
                    "required": False,
                    "max_length": 300,
                    "label": _("Is there anything you'd like your Coach to know before the call? (optional)"),
                    "placeholder": _("I'm a bit shy at first — hope that's okay."),
                },
            ],
        },
        # ------------------------------------------------------------------
        # Section D — Consents
        # ------------------------------------------------------------------
        {
            "id": "consents",
            "title": _("Before we continue"),
            "questions": [
                {
                    "id": "consent_events",
                    "type": "checkbox",
                    "required": True,
                    "label": _(
                        "I understand events are the core of Crush.lu and I intend to attend at least one."
                    ),
                },
                {
                    "id": "consent_coach",
                    "type": "checkbox",
                    "required": True,
                    "label": _(
                        "I understand profiles are reviewed by a human Coach who may contact me by phone or SMS."
                    ),
                },
                {
                    "id": "consent_no_show",
                    "type": "checkbox",
                    "required": True,
                    "label": _(
                        "I understand that repeatedly not showing up at events affects my membership standing."
                    ),
                },
                {
                    "id": "consent_terms",
                    "type": "checkbox",
                    "required": True,
                    # NOTE: Template renders the bracketed phrases as links to the
                    # Code of Conduct and Privacy Policy pages.
                    "label": _(
                        "I accept the [Code of Conduct] and [Privacy Policy]."
                    ),
                },
            ],
        },
    ],
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def iter_questions(schema: dict[str, Any] | None = None):
    """Yield (section, question) tuples across the entire schema."""
    schema = schema or PRE_SCREENING_SCHEMA
    for section in schema["sections"]:
        for question in section["questions"]:
            yield section, question


def get_question(question_id: str, schema: dict[str, Any] | None = None) -> dict[str, Any] | None:
    for _section, question in iter_questions(schema):
        if question["id"] == question_id:
            return question
    return None


def get_section(section_id: str, schema: dict[str, Any] | None = None) -> dict[str, Any] | None:
    schema = schema or PRE_SCREENING_SCHEMA
    for section in schema["sections"]:
        if section["id"] == section_id:
            return section
    return None


def _allowed_values(question: dict[str, Any]) -> set[str]:
    return {c["value"] for c in question.get("choices", [])}


# --------------------------------------------------------------------------
# Readonly-confirm: derive answers from the user's CrushProfile instead of
# re-asking questions the signup form already covered.
# --------------------------------------------------------------------------

# CrushProfile.location (finer canton choices) → pre-screening residence bucket.
_CANTON_TO_RESIDENCE_BUCKET: dict[str, str] = {
    "canton-luxembourg": "lu_city",
    "canton-esch": "lu_esch",
    "canton-capellen": "lu_other",
    "canton-clervaux": "lu_other",
    "canton-diekirch": "lu_other",
    "canton-echternach": "lu_other",
    "canton-grevenmacher": "lu_other",
    "canton-mersch": "lu_other",
    "canton-redange": "lu_other",
    "canton-remich": "lu_other",
    "canton-vianden": "lu_other",
    "canton-wiltz": "lu_other",
    "border-belgium": "be_border",
    "border-germany": "de_border",
    "border-france": "fr_border",
}


def derive_readonly_value(question: dict[str, Any], profile) -> object:
    """Compute the answer for a readonly_confirm question from CrushProfile.

    Returns the schema-native value (string bucket, list of codes, bool).
    Returns None when the source field isn't populated yet — callers should
    treat that as "no answer" and leave pre_screening_responses untouched.
    """
    qid = question["id"]
    if qid == "residence":
        location = getattr(profile, "location", None)
        if not location:
            return None
        return _CANTON_TO_RESIDENCE_BUCKET.get(location, "other")
    if qid == "languages":
        langs = list(getattr(profile, "event_languages", None) or [])
        if not langs:
            return None
        allowed = _allowed_values(question)
        bucketed = [code if code in allowed else "other" for code in langs]
        # De-dupe while preserving order.
        seen: set[str] = set()
        deduped: list[str] = []
        for code in bucketed:
            if code in seen:
                continue
            seen.add(code)
            deduped.append(code)
        return deduped
    if qid == "age_confirm":
        dob = getattr(profile, "date_of_birth", None)
        if not dob:
            return None
        from django.utils import timezone

        today = timezone.now().date()
        age = today.year - dob.year - (
            (today.month, today.day) < (dob.month, dob.day)
        )
        return age >= 18
    return None


def merge_readonly_from_profile(
    responses: dict[str, Any],
    profile,
    schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a new dict with readonly_confirm values re-derived from the profile.

    Existing non-readonly keys are preserved verbatim. Readonly keys are
    overwritten with the current derived value — we never trust client input
    for them, and a profile edit mid-flow should propagate immediately.
    """
    schema = schema or PRE_SCREENING_SCHEMA
    merged = dict(responses or {})
    for _section, question in iter_questions(schema):
        if question.get("type") != "readonly_confirm":
            continue
        value = derive_readonly_value(question, profile)
        qid = question["id"]
        if value is None:
            # Don't fabricate answers if the profile field is empty; leave the
            # key unset so the UI can prompt the user to fill it in.
            merged.pop(qid, None)
        else:
            merged[qid] = value
    return merged


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def validate_pre_screening_responses(
    responses: dict[str, Any],
    version: int | None = None,
    *,
    partial: bool = False,
    section_id: str | None = None,
) -> None:
    """Validate user-submitted pre-screening responses.

    Args:
        responses: flat mapping of question_id -> answer.
        version: schema version the responses claim to answer. Must match the
            current schema version (or None, meaning current).
        partial: if True, skip "required field missing" errors. Used for
            per-section HTMX saves.
        section_id: if set, only validate questions inside this section.

    Raises:
        ValidationError: with ``code`` set to one of the error codes below.

    Error codes:
        - invalid_version
        - unknown_question
        - required_missing
        - invalid_choice
        - min_choices_not_met
        - max_choices_exceeded
        - text_too_long
        - invalid_type
    """
    if not isinstance(responses, dict):
        raise ValidationError(_("Responses must be an object."), code="invalid_type")

    if version is not None and version != PRE_SCREENING_SCHEMA["version"]:
        raise ValidationError(
            _("Pre-screening schema version mismatch."),
            code="invalid_version",
        )

    errors: dict[str, list[ValidationError]] = {}

    allowed_qids = {q["id"] for _s, q in iter_questions()}
    for qid in responses:
        if qid not in allowed_qids:
            errors.setdefault(qid, []).append(
                ValidationError(_("Unknown question."), code="unknown_question")
            )

    for section, question in iter_questions():
        if section_id is not None and section["id"] != section_id:
            continue

        qid = question["id"]
        qtype = question["type"]
        required = question.get("required", False)
        value = responses.get(qid, None)
        present = qid in responses and value not in (None, "", [])

        if not present:
            if required and not partial:
                errors.setdefault(qid, []).append(
                    ValidationError(_("This question is required."), code="required_missing")
                )
            continue

        if qtype == "readonly_confirm":
            # Validate against the underlying shape so readonly answers can't
            # carry garbage into the DB if the derivation helper is misconfigured.
            qtype = question.get("shape", "single_select")

        if qtype == "single_select":
            if value not in _allowed_values(question):
                errors.setdefault(qid, []).append(
                    ValidationError(_("Invalid choice."), code="invalid_choice")
                )
        elif qtype == "multi_select":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                errors.setdefault(qid, []).append(
                    ValidationError(_("Must be a list of choices."), code="invalid_type")
                )
                continue
            allowed = _allowed_values(question)
            if any(v not in allowed for v in value):
                errors.setdefault(qid, []).append(
                    ValidationError(_("Invalid choice."), code="invalid_choice")
                )
            min_choices = question.get("min_choices")
            max_choices = question.get("max_choices")
            if min_choices is not None and len(value) < min_choices:
                errors.setdefault(qid, []).append(
                    ValidationError(_("Pick at least %(n)d."), code="min_choices_not_met",
                                    params={"n": min_choices})
                )
            if max_choices is not None and len(value) > max_choices:
                errors.setdefault(qid, []).append(
                    ValidationError(_("Pick at most %(n)d."), code="max_choices_exceeded",
                                    params={"n": max_choices})
                )
        elif qtype == "text":
            if not isinstance(value, str):
                errors.setdefault(qid, []).append(
                    ValidationError(_("Must be text."), code="invalid_type")
                )
                continue
            max_length = question.get("max_length")
            if max_length is not None and len(value) > max_length:
                errors.setdefault(qid, []).append(
                    ValidationError(_("Too long."), code="text_too_long")
                )
        elif qtype == "checkbox":
            if value is not True:
                errors.setdefault(qid, []).append(
                    ValidationError(_("Must be checked."), code="required_missing")
                )
        elif qtype == "yes_no":
            if value not in (True, False, "yes", "no"):
                errors.setdefault(qid, []).append(
                    ValidationError(_("Must be yes or no."), code="invalid_choice")
                )
        else:
            errors.setdefault(qid, []).append(
                ValidationError(_("Unknown question type."), code="invalid_type")
            )

    if errors:
        raise ValidationError(errors)


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def _is_low_effort(text: str) -> bool:
    """Detect obviously throwaway free-text answers."""
    if not isinstance(text, str):
        return True
    stripped = text.strip()
    if len(stripped) < 20:
        return True
    # Single character repeated (e.g., "aaaaaaaa")
    if len(set(stripped.lower().replace(" ", ""))) <= 2:
        return True
    return False


def compute_readiness_score(responses: dict[str, Any]) -> tuple[int, list[str]]:
    """Compute a 0–10 readiness score plus a list of flag identifiers.

    The score is a rule-based heuristic meant to help the Coach prioritise,
    never to auto-decide.

    Returns:
        (score, flags) where ``score`` is clipped to [0, 10] and ``flags`` is
        a sorted list of stable string identifiers (see FLAG_DESCRIPTIONS).
    """
    score = 0

    what_is_crush = responses.get("what_is_crush")
    event_frequency = responses.get("event_frequency")
    coach_attitude = responses.get("coach_attitude")
    hoping_to_meet = responses.get("hoping_to_meet") or ""
    looking_forward_to = responses.get("looking_forward_to") or []

    # Positive signals
    if what_is_crush in ("events", "unsure"):
        score += 2
    if event_frequency in ("weekly", "monthly"):
        score += 2
    if coach_attitude in ("loves", "fine"):
        score += 1
    if isinstance(hoping_to_meet, str):
        score += min(3, len(hoping_to_meet) // 50)
    if isinstance(looking_forward_to, list) and "events" in looking_forward_to:
        score += 1

    # Negative signals
    if coach_attitude == "no_intermediary":
        score -= 3
    if event_frequency == "online_only":
        score -= 2

    score = max(0, min(10, score))

    flags: list[str] = []
    if what_is_crush == "tinder":
        flags.append("concept_misalignment")
    if event_frequency == "online_only":
        flags.append("events_disinterest")
    if coach_attitude == "no_intermediary":
        flags.append("coach_reluctance")
    if _is_low_effort(hoping_to_meet):
        flags.append("low_effort_text")

    return score, sorted(flags)


# Human-readable explanations shown to the Coach as flag tooltips.
FLAG_DESCRIPTIONS: dict[str, Any] = {
    "concept_misalignment": _(
        "User thinks Crush.lu is a swipe app — expect to clarify during call."
    ),
    "events_disinterest": _(
        "User prefers online-only dating. Discuss whether in-person events fit."
    ),
    "coach_reluctance": _(
        "User would prefer no intermediary. Explain the Coach's role gently."
    ),
    "low_effort_text": _(
        "Free-text answer is very short or repetitive — ask them to elaborate."
    ),
}


def readiness_score_label(score: int | None) -> str:
    """Bucket a score into one of 'high' / 'medium' / 'low' / 'pending'."""
    if score is None:
        return "pending"
    if score >= 8:
        return "high"
    if score >= 5:
        return "medium"
    return "low"
