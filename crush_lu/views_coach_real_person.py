"""
Coach views for the Crush Connect real-person check.

A coach claims a Connect member no coach has met, reaches them on the coach's
own phone (``sms:`` / ``tel:`` / ``wa.me`` links; nothing is sent from the
server), and decides: a video or voice pass (the badge), unreachable, declined,
or a suspected fake. The rules live in ``services.real_person_check``; these
views parse the form, look the check up, and turn ``RealPersonCheckError``
codes into messages.

* Every view is ``@coach_required`` and answers 404 while
  ``CONNECT_REAL_PERSON_CHECK_ENABLED`` is off (checked inside the view, after
  the coach check, so a non-coach is still sent to the dashboard first).
* Every view that takes a ``check_id`` looks the check up with
  ``coach=request.coach``: another coach's check id is a 404, never a 403.
* Logs carry ids only: no phone number, message body, note or posted payload.
* The check page also hosts the in-app video call (``services.video_call``,
  kind ``real-person-check``) while the check is live and no guard blocks it;
  such a response opens the microphone for its own page only
  (``allow_microphone``).

Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md
"""

import json
import logging
from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _l
from django.utils.translation import pgettext
from django.views.decorators.http import require_GET, require_POST

from azureproject.csp_middleware import allow_microphone
from crush_lu.decorators import coach_required
from crush_lu.forms import CallAttemptForm
from crush_lu.models import (
    CallAttempt,
    ConnectRealPersonCheck,
    CrushProfile,
    EmailPreference,
)
from crush_lu.real_person_check_rules import (
    CLAIM_TTL,
    MAX_OPEN_CLAIMS_PER_COACH,
    MEDIUM_VIDEO,
    MEDIUM_VOICE,
    NOTES_MAX_LENGTH,
    REQUIRED_CHECKLIST_KEYS,
)
from crush_lu.services.photo_publication import get_public_photo_key
from crush_lu.services.real_person_check import (
    CALL_KIND,
    GUARD_ALREADY_VERIFIED,
    GUARD_BANNED,
    GUARD_BLOCKED,
    GUARD_COACH_INACTIVE,
    GUARD_FAKE_REPORT_PENDING,
    GUARD_INACTIVE,
    GUARD_NO_CONSENT,
    GUARD_NO_PHOTO,
    GUARD_NOT_CONFIRMED,
    GUARD_NOT_IN_COHORT,
    GUARD_OTHER_COACH_PREMIUM,
    GUARD_PHOTO_NOT_PUBLIC,
    GUARD_SELF,
    GUARD_STALE_PHOTO,
    OUTCOME_DECLINED,
    OUTCOME_PASS_VIDEO,
    OUTCOME_PASS_VOICE,
    OUTCOME_SUSPECTED_FAKE,
    OUTCOME_UNREACHABLE,
    RealPersonCheckError,
    check_guards,
    claim_check,
    decide_check,
    log_outreach,
    real_person_check_enabled,
    release_check,
)
from crush_lu.services.real_person_contact import (
    CONTACT_PREFERENCE_LABELS,
    contact_request_enabled,
    whatsapp_chosen_for,
    window_labels,
)
from crush_lu.services.video_call import video_call_context, video_calls_available
from crush_lu.services.whatsapp import can_send_whatsapp
from crush_lu.views_coach import _outreach_links

logger = logging.getLogger(__name__)


# Registers (spec): flash and guard messages are formal (DE Sie / FR vous).
# Several reuse msgids ``coach_verify_member`` already ships translated.
_ERROR_MESSAGES = {
    "video_admission_closed": _l(
        "Video verification is not available for this member right now."
    ),
    "video_required": _l(
        "Connect Week verification requires a successful live video call. Arrange another attempt if video failed."
    ),
    "invalid_appointment": _l(
        "Choose an agreed future appointment within the next 30 days."
    ),
    "appointment_exists": _l(
        "Cancel the existing appointment before arranging another."
    ),
    "appointment_overlap": _l(
        "This appointment overlaps another slot in your calendar."
    ),
    GUARD_SELF: _l("You cannot check your own profile."),
    GUARD_COACH_INACTIVE: _l("Your coach account is not active."),
    GUARD_INACTIVE: _l("This member's account is not active."),
    GUARD_NO_CONSENT: _l("This member has not consented to Crush.lu."),
    GUARD_BANNED: _l("This member is banned from Crush.lu."),
    GUARD_NO_PHOTO: _l("This member has no photo yet, so it cannot be checked here."),
    GUARD_STALE_PHOTO: _l("This member's photo changed. Reload and check the new one."),
    GUARD_PHOTO_NOT_PUBLIC: _l(
        "No coach has approved this member's current main photo yet, so a video "
        "pass cannot be granted. Review the photo first, or do a voice check."
    ),
    # Deliberately vague: a block is private, and a pending report is not for
    # the coach to read.
    GUARD_BLOCKED: _l("This member is not available for a check right now."),
    GUARD_FAKE_REPORT_PENDING: _l(
        "This member is not available for a check right now."
    ),
    GUARD_OTHER_COACH_PREMIUM: _l(
        "This premium member is verified by their own coach."
    ),
    GUARD_NOT_CONFIRMED: _l("Confirm your decision before saving it."),
    GUARD_ALREADY_VERIFIED: _l("This member already has a real-person badge."),
    GUARD_NOT_IN_COHORT: _l("This member is not on the real-person check list."),
    "taken": _l("Another coach has already claimed this member."),
    "cooldown": _l("This member declined a check recently and is left alone for now."),
    "not_open": _l("This check is already closed."),
    "lease_expired": _l(
        "Your claim on this member has lapsed. Claim them again from the list."
    ),
    "invalid_result": _l("Choose what you did to reach this member."),
    "invalid_failure_reason": _l("Choose why the attempt failed."),
    "invalid_medium": _l("Choose video or voice."),
    "invalid_checklist": _l(
        "The checklist could not be read. Reload the page and try again."
    ),
    "unknown_checklist_keys": _l(
        "The checklist could not be read. Reload the page and try again."
    ),
    "checklist_incomplete": _l(
        "Tick every item on the checklist before granting the badge."
    ),
    "invalid_outcome": _l("Choose what to do with this check."),
    "forbidden": _l("You do not have access to do that."),
}

# What a coach can fix on the page: these answer 400, not 403, even though the
# service raises them as guard failures.
_CORRECTABLE_CODES = {GUARD_NOT_CONFIRMED, GUARD_STALE_PHOTO, GUARD_PHOTO_NOT_PUBLIC}
# Closed or lapsed: the page the coach is on is stale, so send them to the list.
_STALE_CODES = {"not_open", "lease_expired"}

CHECKLIST_LABELS = {
    "spoke_live": _l("I spoke with them live"),
    "name_matches": _l("Name matches the profile"),
    "age_matches": _l("Age looks consistent with the profile"),
    "badge_explained": _l("I explained the badge and they agreed"),
    "face_matches_photo": _l("Their face matches photo 1"),
    "live_camera": _l("Live camera, not a recording"),
}


def _flag_required(view):
    """404 while the real-person check tooling is switched off."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not real_person_check_enabled():
            raise Http404
        return view(request, *args, **kwargs)

    return wrapper


def _error_message(exc_or_code):
    """The coach-facing text for a ``RealPersonCheckError`` code."""
    code = getattr(exc_or_code, "code", exc_or_code)
    if code == "capacity":
        return _(
            "You already hold %(max)s open checks. Release one before claiming "
            "another."
        ) % {"max": MAX_OPEN_CLAIMS_PER_COACH}
    if code == "notes_too_long":
        return _("Notes can be at most %(max)s characters.") % {"max": NOTES_MAX_LENGTH}
    message = _ERROR_MESSAGES.get(code)
    return str(message) if message else _("This check could not be saved.")


def _messages_for_codes(codes):
    """Distinct coach-facing messages for a list of error codes.

    ``not_in_cohort`` is what every other reason (inactive, no consent, no
    photo, ...) looks like from the list's side, so it is only worth saying
    when it is the whole story.
    """
    codes = [code for code in codes if code != GUARD_NOT_IN_COHORT] or list(codes)
    seen = []
    for code in codes:
        message = _error_message(code)
        if message not in seen:
            seen.append(message)
    return seen


def _error_messages(exc):
    """Every message for an error (a guard refusal can list several failures)."""
    return _messages_for_codes(exc.failures or [exc.code])


def _owned_check(request, check_id):
    """The coach's own check, or 404; refuses a check about the coach themself.

    The self refusal comes before any member data is rendered or changed.
    """
    check = get_object_or_404(
        ConnectRealPersonCheck.objects.select_related("profile__user"),
        pk=check_id,
        coach=request.coach,
    )
    if check.profile.user_id == request.user.id:
        logger.warning(
            "Real-person check %s refused: coach %s targeted their own profile",
            check.pk,
            request.coach.pk,
        )
        raise PermissionDenied
    return check


def _member_label(profile):
    user = profile.user
    return user.first_name or user.username.split("@")[0]


def _dial_number(phone):
    """``+35269...`` / ``069...`` reduced to what ``tel:`` and ``sms:`` accept."""
    phone = (phone or "").strip()
    digits = "".join(ch for ch in phone if ch.isdigit())
    return ("+" if phone.startswith("+") else "") + digits


def _member_language(profile):
    """The language the coach's text to this member is written in."""
    return (
        profile.preferred_language
        if profile.preferred_language in ("en", "de", "fr")
        else "en"
    )


def _member_call_url(request, check):
    """The absolute address of the member's call page, in their language.

    Built under the member's language so the ``/en/`` / ``/de/`` / ``/fr/``
    prefix matches the text it ends. The host is the one the coach is on.
    """
    with translation.override(_member_language(check.profile)):
        return request.build_absolute_uri(
            reverse("crush_lu:crush_connect_real_person_call", args=[check.pk])
        )


def real_person_outreach_body(
    coach, profile, *, video_available=True, call_url="", admission=False
):
    """The SMS / WhatsApp text, in the member's language.

    A gettext msgid (not a ``CrushSiteConfig`` template) rendered under the
    member's ``preferred_language``; the coach reads and sends it from their
    own phone. While a video pass is not possible (no coach-approved main
    photo yet) the invitation proposes a call without promising video: the
    only badge the coach could grant is the voice one.

    ``call_url`` is the address of the member's in-app call page. With one, the
    text ends with it (a separate msgid per variant); without one, the text is
    exactly what it was before the in-app call existed. The caller passes it
    only while the call is available (``_check_context``).
    """
    with translation.override(_member_language(profile)):
        coach_name = coach.user.first_name or pgettext(
            "real-person check text message: no coach first name", "a coach"
        )
        params = {
            "first_name": profile.user.first_name or "",
            "coach_name": coach_name,
            "call_url": call_url,
        }
        if admission:
            return (
                _(
                    "Hello %(first_name)s, this is %(coach_name)s from Crush.lu. You requested a video verification for Connect Week. Let us agree a time to meet on video. You can check your request here: %(call_url)s"
                )
                % params
            )
        if not video_available:
            if call_url:
                return (
                    _(
                        "Hi %(first_name)s, this is %(coach_name)s from Crush.lu. To "
                        "confirm every Crush Connect member is a real person, could we "
                        "have a quick 5-minute call? It's optional and adds a badge to "
                        "your card. If you prefer video, you can join from your "
                        "browser here: %(call_url)s"
                    )
                    % params
                )
            return (
                _(
                    "Hi %(first_name)s, this is %(coach_name)s from Crush.lu. To "
                    "confirm every Crush Connect member is a real person, could we "
                    "have a quick 5-minute call? It's optional and adds a badge to "
                    "your card."
                )
                % params
            )
        if call_url:
            return (
                _(
                    "Hi %(first_name)s, this is %(coach_name)s from Crush.lu. To "
                    "confirm every Crush Connect member is a real person, could we "
                    "do a quick 5-minute video call? It's optional and adds a "
                    "'verified by a coach' badge to your card. When you are ready, "
                    "join from your browser here: %(call_url)s"
                )
                % params
            )
        return (
            _(
                "Hi %(first_name)s, this is %(coach_name)s from Crush.lu. To confirm "
                "every Crush Connect member is a real person, could we do a quick "
                "5-minute video call? It's optional and adds a 'verified by a coach' "
                "badge to your card."
            )
            % params
        )


def whatsapp_link_allowed(profile):
    """True when the coach may be offered a ``wa.me`` link for this member.

    Spec decision D11: WhatsApp only for members who opted in to it
    (``EmailPreference.whatsapp_opt_in``, not unsubscribed from everything) and
    whose number is not known to be off WhatsApp; the rule campaigns use. A
    missing preference row means not opted in. Everyone else is reached by SMS.
    """
    return (
        can_send_whatsapp(profile)
        and EmailPreference.objects.filter(
            user_id=profile.user_id, whatsapp_opt_in=True, unsubscribed_all=False
        ).exists()
    )


def _outreach_context(
    coach,
    profile,
    *,
    blocked,
    video_available=True,
    call_url="",
    admission=False,
    whatsapp_chosen=False,
):
    """``tel:`` / ``sms:`` / ``wa.me`` links, or none while a guard fails.

    Links exist only for a verified phone number, and never while any guard
    fails: a banned or blocked member must not be one tap away. The WhatsApp
    link also needs the member's WhatsApp opt-in (``whatsapp_link_allowed``),
    or their own "WhatsApp first" answer for this check (``whatsapp_chosen``),
    which still needs a number that can receive WhatsApp.
    ``call_url`` (the member's in-app call page) goes into the text of both.
    """
    empty = {"tel_href": "", "sms_href": "", "whatsapp_url": "", "phone_display": ""}
    phone = (profile.phone_number or "").strip()
    if blocked or not phone or not profile.phone_verified:
        return empty
    body = real_person_outreach_body(
        coach,
        profile,
        video_available=video_available,
        call_url=call_url,
        admission=admission,
    )
    links = _outreach_links(phone, body)
    dial = _dial_number(phone)
    return {
        "tel_href": f"tel:{dial}" if dial else "",
        "sms_href": (
            f"sms:{dial}?body={links['sms_template_encoded']}" if dial else ""
        ),
        "whatsapp_url": (
            links["whatsapp_url"]
            if whatsapp_link_allowed(profile)
            or (whatsapp_chosen and can_send_whatsapp(profile))
            else ""
        ),
        "phone_display": phone,
    }


def _contact_context(check):
    """What the member was asked and answered about being reached, or None.

    None while the contact-request switch is off or nothing was ever asked, so
    the page then looks exactly as it did before the feature.
    """
    if not contact_request_enabled() or not (
        check.contact_requested_at or check.contact_answered_at
    ):
        return None
    return {
        "answered_at": check.contact_answered_at,
        "requested_at": check.contact_requested_at,
        "notice": check.contact_notice or "",
        "preference": check.contact_preference or "",
        "preference_label": CONTACT_PREFERENCE_LABELS.get(
            check.contact_preference or "", ""
        ),
        "windows": window_labels(check.contact_availability),
    }


def _check_context(request, check, *, now=None, **extra):
    """Everything the check page and its outreach partial render."""
    coach = request.coach
    now = now or timezone.now()
    profile = check.profile
    live = check.is_live(now)
    # One guard run answers three questions: which links to blank, why, and
    # whether a video pass is possible (it needs the live photo to be the one
    # other members see; a voice pass does not).
    guards = (
        check_guards(coach, profile, medium=MEDIUM_VIDEO, purpose=check.purpose)
        if live
        else []
    )
    blocking = [code for code in guards if code != GUARD_PHOTO_NOT_PUBLIC]
    # The in-app call is possible under the same condition as the contact
    # links (a live claim, no blocking guard; a main photo other members
    # cannot see yet only rules out the *video badge*, not the conversation),
    # and only where video calls are set up at all. This is exactly what
    # ``authorize_video_call`` decides, so the page never offers a call the
    # socket would refuse.
    call_possible = bool(live and not blocking and video_calls_available())
    admission = check.purpose == "connect_week"
    if admission:
        from crush_lu.services.real_person_check import _call_role

        call_possible = (
            call_possible and _call_role(request.user, check, now) == "coach"
        )
    call_url = _member_call_url(request, check) if call_possible else ""
    if admission:
        with translation.override(_member_language(profile)):
            call_url = request.build_absolute_uri(
                reverse("crush_lu:connect_verification")
            )
    current_key = getattr(profile.photo_1, "name", "") or ""
    public_key = get_public_photo_key(profile, "photo_1") if current_key else ""
    attempts = list(
        check.call_attempts.select_related("coach__user").order_by(
            "-attempt_date", "-pk"
        )[:50]
    )
    common = list(REQUIRED_CHECKLIST_KEYS[MEDIUM_VOICE])
    video_only = [
        key for key in REQUIRED_CHECKLIST_KEYS[MEDIUM_VIDEO] if key not in common
    ]
    context = {
        "coach": coach,
        "is_admission": admission,
        "appointment": (
            check.appointments.filter(status="booked", end_at__gt=now).first()
            if admission
            else None
        ),
        "check": check,
        "profile": profile,
        "member": profile.user,
        "member_label": _member_label(profile),
        "is_live": live,
        "now": now,
        "guard_messages": _messages_for_codes(blocking),
        "links_blocked": bool(blocking),
        "video_available": live and GUARD_PHOTO_NOT_PUBLIC not in guards,
        "photo_key": current_key,
        "public_photo_missing": bool(current_key) and not public_key,
        "photo_is_public": bool(current_key) and public_key == current_key,
        "attempts": attempts,
        "attempt_form": CallAttemptForm(),
        "failure_reasons": CallAttempt.FAILURE_REASON_CHOICES,
        "notes_max": NOTES_MAX_LENGTH,
        "required_voice": ",".join(REQUIRED_CHECKLIST_KEYS[MEDIUM_VOICE]),
        "required_video": ",".join(REQUIRED_CHECKLIST_KEYS[MEDIUM_VIDEO]),
        "checklist_common": [(key, CHECKLIST_LABELS[key]) for key in common],
        "checklist_video_only": [(key, CHECKLIST_LABELS[key]) for key in video_only],
        "urls": {
            "log": reverse("crush_lu:coach_real_person_log", args=[check.pk]),
            "decide": reverse("crush_lu:coach_real_person_decide", args=[check.pk]),
            "release": reverse("crush_lu:coach_real_person_release", args=[check.pk]),
            "queue": reverse("crush_lu:coach_verification_channel"),
        },
    }
    context.update(
        _outreach_context(
            coach,
            profile,
            blocked=bool(blocking) or not live,
            video_available=context["video_available"],
            call_url=call_url,
            admission=admission,
            whatsapp_chosen=whatsapp_chosen_for(check),
        )
    )
    context["contact"] = _contact_context(check)
    if call_possible:
        # ``video_call`` (a call can take place) is not ``video_available``
        # (a video *badge* can be granted): a coach may video-call a member
        # and still record only a voice pass.
        context.update(video_call_context(CALL_KIND, str(check.pk)))
    context.update(extra)
    if admission:
        from crush_lu.views_verification_funnel import AppointmentForm

        context.setdefault("appointment_form", AppointmentForm())
        context["checklist_common"] = [
            (
                (
                    key,
                    _l("I explained Connect Week access and the remaining preparation"),
                )
                if key == "badge_explained"
                else (key, label)
            )
            for key, label in context["checklist_common"]
        ]
    return context


def _render_check_page(request, check, *, status=200, **extra):
    """The check page; it may host the call, so it may use the microphone.

    Used by the page itself and by the decide form when it answers an error,
    which renders the same page. The microphone opens only on a response that
    really carries the call, never on the closed or blocked states.
    """
    context = _check_context(request, check, **extra)
    response = render(
        request, "crush_lu/coach_real_person_check.html", context, status=status
    )
    if context.get("video_call", {}).get("available"):
        allow_microphone(response)
    return response


def _redirect_stale(request, exc, *, admission=False):
    """Flash the reason and send the coach back to the list (HTMX-aware)."""
    messages.error(request, _error_message(exc))
    queue_url = reverse(
        "crush_lu:coach_verification_channel"
        if admission
        else "crush_lu:coach_real_person_queue"
    )
    if request.headers.get("HX-Request"):
        response = HttpResponse(status=204)
        response["HX-Redirect"] = queue_url
        return response
    return redirect(queue_url)


# ---------------------------------------------------------------------------
# Queue and claim
# ---------------------------------------------------------------------------


@coach_required
@_flag_required
@require_GET
def coach_real_person_queue(request):
    """Keep existing bookmarks and action redirects in the shared workspace."""
    from urllib.parse import urlencode

    query = {"work": "badges"}
    if request.GET.get("page"):
        query["page"] = request.GET["page"]
    return redirect(
        f"{reverse('crush_lu:coach_verification_channel')}?{urlencode(query)}"
    )


@coach_required
@_flag_required
@require_POST
def coach_real_person_claim(request, user_id):
    """Claim a member for this coach and open the check page."""
    profile = get_object_or_404(CrushProfile, user_id=user_id)
    try:
        check = claim_check(request.coach, profile.pk)
    except RealPersonCheckError as exc:
        if exc.code == "not_found":
            raise Http404 from None
        messages.error(request, " ".join(_error_messages(exc)))
        return redirect("crush_lu:coach_real_person_queue")
    return redirect("crush_lu:coach_real_person_check", check_id=check.pk)


# ---------------------------------------------------------------------------
# The check page
# ---------------------------------------------------------------------------


@coach_required
@_flag_required
@require_GET
def coach_real_person_check(request, check_id):
    check = _owned_check(request, check_id)
    return _render_check_page(request, check)


@coach_required
@_flag_required
@require_POST
def coach_real_person_log(request, check_id):
    """Record that the coach reached out (HTMX), renewing the claim."""
    check = _owned_check(request, check_id)
    htmx = bool(request.headers.get("HX-Request"))
    form = CallAttemptForm(request.POST)
    errors = []
    notice = ""
    if not form.is_valid():
        errors = [_error_message("invalid_failure_reason")]
    else:
        try:
            log_outreach(
                request.coach,
                check.pk,
                result=request.POST.get("result", ""),
                failure_reason=form.cleaned_data.get("failure_reason") or "",
                notes=form.cleaned_data.get("notes") or "",
            )
        except RealPersonCheckError as exc:
            if exc.code == "not_found":
                raise Http404 from None
            if exc.code in _STALE_CODES:
                return _redirect_stale(
                    request, exc, admission=check.purpose == "connect_week"
                )
            errors = _error_messages(exc)
        else:
            notice = _(
                "Contact attempt logged. Your claim now runs for another "
                "%(hours)s hours."
            ) % {"hours": int(CLAIM_TTL.total_seconds() // 3600)}
    if htmx:
        # htmx swaps nothing for a 4xx, so a refused attempt is answered with
        # the partial and its message (the screening-call endpoints do too).
        check = _owned_check(request, check_id)
        return render(
            request,
            "crush_lu/coach_real_person_outreach.html",
            _check_context(request, check, log_errors=errors, notice=notice),
        )
    if errors:
        messages.error(request, " ".join(errors))
    else:
        messages.success(request, notice)
    return redirect("crush_lu:coach_real_person_check", check_id=check.pk)


# ---------------------------------------------------------------------------
# Decide and release
# ---------------------------------------------------------------------------


# Six boolean keys fit in well under 200 bytes. The cap keeps a crafted body
# (a hundred thousand nested brackets) away from the JSON parser, which would
# otherwise recurse until it raises.
CHECKLIST_MAX_BYTES = 2048


def _parse_checklist(raw):
    """The posted JSON checklist; the service validates keys and values."""
    raw = (raw or "").strip()
    if not raw:
        return {}
    if len(raw) > CHECKLIST_MAX_BYTES:
        raise RealPersonCheckError("invalid_checklist", 400)
    try:
        return json.loads(raw)
    except (ValueError, RecursionError):
        raise RealPersonCheckError("invalid_checklist", 400) from None


def _decide_status(exc):
    codes = set(exc.failures or [exc.code])
    if codes & _CORRECTABLE_CODES or exc.status == 400:
        return 400
    return exc.status


def _decided_message(outcome, name):
    if outcome == OUTCOME_PASS_VIDEO:
        return _("Video check passed for %(name)s.") % {"name": name}
    if outcome == OUTCOME_PASS_VOICE:
        return _("Voice check passed for %(name)s.") % {"name": name}
    if outcome == OUTCOME_UNREACHABLE:
        return _("%(name)s is marked as unreachable and stays on the list.") % {
            "name": name
        }
    if outcome == OUTCOME_DECLINED:
        return _("%(name)s declined. They are left alone for a while.") % {"name": name}
    return _(
        "%(name)s was reported as a suspected fake. An administrator will review it."
    ) % {"name": name}


@coach_required
@_flag_required
@require_POST
def coach_real_person_decide(request, check_id):
    """Close the check with one outcome (plain form post)."""
    check = _owned_check(request, check_id)
    outcome = (request.POST.get("action") or "").strip()
    confirmed = request.POST.get("confirm") == "yes"
    notes = request.POST.get("notes", "")
    name = _member_label(check.profile)
    try:
        if outcome == OUTCOME_SUSPECTED_FAKE and not confirmed:
            # The service asks for a confirmation only on a pass; reporting
            # someone as fake gets the same deliberate second step.
            raise RealPersonCheckError(GUARD_NOT_CONFIRMED, 403)
        decide_check(
            request.coach,
            check.pk,
            outcome,
            checklist=_parse_checklist(request.POST.get("checklist")),
            notes=notes,
            # Only a video pass compares a face with photo 1; any other
            # outcome must not be refused because the photo moved meanwhile.
            photo_key=(
                request.POST.get("photo_key") if outcome == OUTCOME_PASS_VIDEO else None
            ),
            confirmed=confirmed,
            failure_reason=request.POST.get("failure_reason") or "no_answer",
        )
    except RealPersonCheckError as exc:
        if exc.code == "not_found":
            raise Http404 from None
        if exc.code in _STALE_CODES:
            return _redirect_stale(
                request, exc, admission=check.purpose == "connect_week"
            )
        # Fresh rows: a stale photo key means the photo moved on.
        check = _owned_check(request, check_id)
        return _render_check_page(
            request,
            check,
            status=_decide_status(exc),
            error_messages=_error_messages(exc),
            notes_value=notes,
        )
    messages.success(request, _decided_message(outcome, name))
    return redirect(
        "crush_lu:coach_verification_channel"
        if check.purpose == "connect_week"
        else "crush_lu:coach_real_person_queue"
    )


@coach_required
@_flag_required
@require_POST
def coach_real_person_release(request, check_id):
    """Give the claim back; the member returns to the list."""
    check = _owned_check(request, check_id)
    name = _member_label(check.profile)
    try:
        release_check(request.coach, check.pk)
    except RealPersonCheckError as exc:
        if exc.code == "not_found":
            raise Http404 from None
        messages.error(request, _error_message(exc))
        return redirect("crush_lu:coach_real_person_queue")
    messages.success(
        request, _("You released %(name)s. They are back on the list.") % {"name": name}
    )
    return redirect(
        "crush_lu:coach_verification_channel"
        if check.purpose == "connect_week"
        else "crush_lu:coach_real_person_queue"
    )
