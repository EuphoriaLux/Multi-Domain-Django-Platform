"""Views for the pre-screening questionnaire (Phase 2).

User-facing form that fills in logistics, concept alignment, free-text, and
consents after a profile has been submitted but before the Coach reviews it.
All write endpoints are ratelimited. Everything is gated by
``settings.PRE_SCREENING_ENABLED``.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from .decorators import crush_login_required, ratelimit
from .models import CrushProfile, ProfileSubmission
from .pre_screening_schema import (
    PRE_SCREENING_SCHEMA,
    compute_readiness_score,
    get_section,
    iter_questions,
    merge_readonly_from_profile,
    validate_pre_screening_responses,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _get_active_submission(user) -> ProfileSubmission | None:
    """Return the user's latest pending ProfileSubmission, or None."""
    try:
        profile = CrushProfile.objects.get(user=user)
    except CrushProfile.DoesNotExist:
        return None
    return (
        ProfileSubmission.objects.filter(profile=profile)
        .order_by("-submitted_at")
        .first()
    )


def _can_edit(submission: ProfileSubmission | None) -> bool:
    if submission is None:
        return False
    if submission.status != "pending":
        return False
    if submission.review_call_completed:
        return False
    return True


def _coerce_form_value(question: dict, post_data) -> object:
    """Translate a raw POST field into its schema-native value."""
    qid = question["id"]
    qtype = question["type"]
    if qtype == "single_select":
        return post_data.get(qid) or None
    if qtype == "multi_select":
        return post_data.getlist(qid) or None
    if qtype == "text":
        return (post_data.get(qid) or "").strip()
    if qtype == "checkbox":
        return bool(post_data.get(qid))
    if qtype == "yes_no":
        raw = post_data.get(qid)
        if raw == "yes":
            return True
        if raw == "no":
            return False
        return None
    return None


def _parse_section_from_post(section_id: str, post_data) -> dict:
    section = get_section(section_id)
    if section is None:
        raise Http404("Unknown section")
    parsed = {}
    for question in section["questions"]:
        value = _coerce_form_value(question, post_data)
        # Drop empties so they don't trip the validator's "unknown question"
        # check when the user saves a section with nothing filled in.
        if value in (None, "", []):
            continue
        parsed[question["id"]] = value
    return parsed


def _section_is_complete(section: dict, responses: dict) -> bool:
    """A section is complete iff every required question in it is answered."""
    for question in section["questions"]:
        if not question.get("required"):
            continue
        qid = question["id"]
        value = responses.get(qid)
        if value in (None, "", []):
            return False
        if question["type"] == "checkbox" and value is not True:
            return False
    return True


def _is_complete(responses: dict) -> bool:
    try:
        validate_pre_screening_responses(
            responses, version=PRE_SCREENING_SCHEMA["version"]
        )
    except ValidationError:
        return False
    return True


def _build_sections_context(responses: dict, errors_by_qid: dict | None = None,
                            flash_section_id: str | None = None) -> list[dict]:
    errors_by_qid = errors_by_qid or {}
    output = []
    for idx, section in enumerate(PRE_SCREENING_SCHEMA["sections"], start=1):
        section_errors = {
            q["id"]: errors_by_qid[q["id"]]
            for q in section["questions"]
            if q["id"] in errors_by_qid
        }
        output.append({
            "section": section,
            "number": idx,
            "complete": _section_is_complete(section, responses),
            "errors": section_errors,
            "flash": _("Saved.") if flash_section_id == section["id"] else "",
        })
    return output


def _errors_by_qid(exc: ValidationError) -> dict:
    if hasattr(exc, "error_dict"):
        return {qid: list(errs) for qid, errs in exc.error_dict.items()}
    return {}


def _should_notify_coach(submission: ProfileSubmission, *, edit: bool) -> bool:
    """Rate-limit coach push notifications for edits to max 1 per hour."""
    if not submission.coach or not edit:
        return True  # first-submit: always notify
    cache_key = f"pre_screening_edit_notify:{submission.id}"
    if cache.get(cache_key):
        return False
    cache.set(cache_key, 1, 3600)  # 1 hour throttle
    return True


def _notify_coach(submission: ProfileSubmission, *, edit: bool) -> None:
    if not submission.coach:
        return
    if not _should_notify_coach(submission, edit=edit):
        return
    try:
        from .coach_notifications import notify_coach_system_alert

        display_name = (
            submission.profile.display_name
            or submission.profile.user.first_name
            or submission.profile.user.username
        )
        score = submission.pre_screening_readiness_score
        flags = submission.pre_screening_flags or []
        flag_summary = f" ({', '.join(flags)})" if flags else ""
        if edit:
            title = str(_("%(name)s updated their answers") % {"name": display_name})
            body = str(_("New readiness: %(score)s/10") % {"score": score})
        else:
            title = str(
                _("%(name)s completed pre-screening") % {"name": display_name}
            )
            body = str(
                _("Readiness: %(score)s/10%(flags)s")
                % {"score": score, "flags": flag_summary}
            )
        notify_coach_system_alert(
            coach=submission.coach,
            title=title,
            message=body,
            url=f"/coach/review/{submission.id}/",
        )
    except Exception:
        logger.warning(
            "Failed to send pre-screening coach notification",
            extra={"submission_id": submission.id},
        )


def _gate_or_redirect(request):
    """Return a redirect response if the feature flag is off, else None."""
    if not getattr(settings, "PRE_SCREENING_ENABLED", False):
        messages.info(request, _("Pre-screening is not available yet."))
        return redirect("crush_lu:profile_submitted")
    return None


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------

@crush_login_required
def pre_screening_form(request):
    """Main form page. GET renders, POST on this URL is not expected."""
    gate = _gate_or_redirect(request)
    if gate is not None:
        return gate

    submission = _get_active_submission(request.user)
    if submission is None:
        messages.error(request, _("No profile submission found."))
        return redirect("crush_lu:create_profile")

    if not _can_edit(submission):
        if submission.review_call_completed:
            messages.info(
                request,
                _("Your Coach has already completed your screening call."),
            )
        else:
            messages.info(
                request,
                _("Pre-screening is no longer available for this submission."),
            )
        return redirect("crush_lu:profile_submitted")

    # Mirror readonly_confirm fields from the profile on every render so the
    # user sees live values and a mid-flow profile edit propagates here.
    responses = merge_readonly_from_profile(
        submission.pre_screening_responses or {}, submission.profile
    )
    sections_context = _build_sections_context(responses)
    completed_count = sum(1 for s in sections_context if s["complete"])
    total = len(sections_context)
    progress_percent = int((completed_count / total) * 100) if total else 0

    context = {
        "submission": submission,
        "responses": responses,
        "sections_context": sections_context,
        "completed_section_count": completed_count,
        "total_section_count": total,
        "progress_percent": progress_percent,
        "can_finalize": _is_complete(responses),
        "already_submitted": submission.pre_screening_submitted_at is not None,
    }
    return render(request, "crush_lu/pre_screening.html", context)


@crush_login_required
@ratelimit(key="user", rate="30/h", method="POST", block=True)
@require_http_methods(["POST"])
def pre_screening_save_section(request, section_id: str):
    """HTMX endpoint: save one section's answers (partial / non-final)."""
    gate = _gate_or_redirect(request)
    if gate is not None:
        return gate

    section = get_section(section_id)
    if section is None:
        raise Http404("Unknown section")

    submission = _get_active_submission(request.user)
    if submission is None or not _can_edit(submission):
        return HttpResponse(status=410)

    try:
        parsed = _parse_section_from_post(section_id, request.POST)
    except Http404:
        raise

    responses = dict(submission.pre_screening_responses or {})
    # Remove any prior answers inside this section so users can un-select.
    for q in section["questions"]:
        responses.pop(q["id"], None)
    responses.update(parsed)
    # readonly_confirm questions aren't in POST (they're not editable here).
    # Re-derive them from the profile so the DB snapshot stays current.
    responses = merge_readonly_from_profile(responses, submission.profile)

    errors_by_qid: dict = {}
    try:
        validate_pre_screening_responses(
            responses,
            version=None,
            partial=True,
            section_id=section_id,
        )
    except ValidationError as exc:
        errors_by_qid = _errors_by_qid(exc)

    submission.pre_screening_responses = responses
    submission.save(update_fields=["pre_screening_responses"])

    section_complete = _section_is_complete(section, responses) and not errors_by_qid
    section_number = next(
        (i + 1 for i, s in enumerate(PRE_SCREENING_SCHEMA["sections"])
         if s["id"] == section_id),
        1,
    )

    logger.info(
        "pre_screening.section_saved",
        extra={
            "submission_id": submission.id,
            "section_id": section_id,
            "has_errors": bool(errors_by_qid),
        },
    )

    # Stats for the out-of-band progress + finalize refresh.
    sections_context = _build_sections_context(responses)
    completed_count = sum(1 for s in sections_context if s["complete"])
    total = len(sections_context)

    context = {
        "section": section,
        "section_number": section_number,
        "section_complete": section_complete,
        "responses": responses,
        "errors": errors_by_qid,
        "section_flash": _("Saved.") if not errors_by_qid else "",
        # Out-of-band swaps keep the page's progress indicator and finalize
        # button in sync without a full-page reload.
        "oob_updates": True,
        "completed_section_count": completed_count,
        "total_section_count": total,
        "progress_percent": int((completed_count / total) * 100) if total else 0,
        "can_finalize": _is_complete(responses),
        "already_submitted": submission.pre_screening_submitted_at is not None,
    }
    return render(request, "crush_lu/pre_screening/_section.html", context)


@crush_login_required
@ratelimit(key="user", rate="10/h", method="POST", block=True)
@require_http_methods(["POST"])
def pre_screening_finalize(request):
    """Final submit: validate everything, compute score, notify Coach."""
    gate = _gate_or_redirect(request)
    if gate is not None:
        return gate

    submission = _get_active_submission(request.user)
    if submission is None or not _can_edit(submission):
        messages.error(request, _("Cannot finalize pre-screening."))
        return redirect("crush_lu:profile_submitted")

    # Parse every section from POST (top-level finalize form has its own
    # inputs, but we mostly rely on what's already saved by per-section HTMX).
    # Re-derive readonly_confirm answers from the profile right before validation
    # so the finalize snapshot reflects any late profile edits.
    responses = merge_readonly_from_profile(
        submission.pre_screening_responses or {}, submission.profile
    )

    is_edit = submission.pre_screening_submitted_at is not None

    try:
        validate_pre_screening_responses(
            responses, version=PRE_SCREENING_SCHEMA["version"]
        )
    except ValidationError:
        messages.error(
            request,
            _("Please complete every required question before submitting."),
        )
        return redirect("crush_lu:pre_screening")

    score, flags = compute_readiness_score(responses)
    submission.pre_screening_responses = responses
    submission.pre_screening_submitted_at = timezone.now()
    submission.pre_screening_version = PRE_SCREENING_SCHEMA["version"]
    submission.pre_screening_readiness_score = score
    submission.pre_screening_flags = flags
    # Completing pre-screening automatically opts the Coach into the shorter
    # 3-section calibration call. Coaches can manually revert to legacy via
    # the screening tab if they prefer the full 5-section flow.
    if submission.screening_call_mode == "legacy":
        submission.screening_call_mode = "calibration"
    submission.save(update_fields=[
        "pre_screening_responses",
        "pre_screening_submitted_at",
        "pre_screening_version",
        "pre_screening_readiness_score",
        "pre_screening_flags",
        "screening_call_mode",
    ])

    logger.info(
        "pre_screening.finalized",
        extra={
            "submission_id": submission.id,
            "score": score,
            "flags": flags,
            "version": PRE_SCREENING_SCHEMA["version"],
            "edit": is_edit,
        },
    )

    _notify_coach(submission, edit=is_edit)

    messages.success(
        request,
        _("Your pre-screening answers have been sent to your Coach. Thank you!"),
    )
    return redirect("crush_lu:profile_submitted")
