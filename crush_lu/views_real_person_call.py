"""The member's page for the in-app video call of a Connect real-person check.

A coach who claimed a Connect member sends them a link (SMS or WhatsApp) to
this page, and the hub shows a card that links here too. The page hosts the
generic call screen (``crush_lu/partials/video_call.html``); the signalling
socket and the relay credentials are the generic video-call endpoints, which
ask the same authorizer (``services.real_person_check.authorize_video_call``).

* Only the member the check is about may open it, and only while the check is
  a live claim and every coach-side guard still clears. Anyone else gets a
  plain 404 (no hint that a check exists), and so does everyone while the
  tooling flag is off. The member themself, at the address of their own
  check, gets a short "not open right now" page once the call cannot be
  joined (decided, released, lapsed or closed by a safeguard): their text
  message or a reload must not end in "Page not found", and the page says
  nothing about which reason applies.
* Where video calls are not set up (no TURN relay) the page says the coach will
  phone instead, and carries no call.
* Inside the native apps the call partial points to the phone's browser (the
  app WebViews cannot use the microphone yet).
* A response that carries the call opens the microphone for its own page only.

Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md (D12)
"""

from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache

from azureproject.csp_middleware import allow_microphone
from crush_lu.decorators import crush_login_required
from crush_lu.models import ConnectRealPersonCheck
from crush_lu.services.real_person_check import (
    CALL_KIND,
    CALL_ROLE_MEMBER,
    real_person_check_enabled,
)
from crush_lu.services.video_call import call_role, video_call_context

# The URL converter takes any number of digits; a primary key is a 64-bit integer.
_MAX_CHECK_ID = 2**63 - 1


def _not_open(request, check_id):
    """The 404 for everyone, the "not open right now" page for the check's member.

    The member owns the check, so telling them it is not open leaks nothing;
    the page is the same for every reason. With the tooling flag off the
    feature does not exist, for the member as for everyone.
    """
    if (
        check_id <= _MAX_CHECK_ID
        and real_person_check_enabled()
        and ConnectRealPersonCheck.objects.filter(
            pk=check_id, profile__user_id=request.user.pk
        ).exists()
    ):
        return render(request, "crush_lu/crush_connect/real_person_call_ended.html")
    raise Http404


@crush_login_required
@never_cache
def crush_connect_real_person_call(request, check_id):
    """Where the member joins their coach's video call."""
    # The authorizer is the single rule, here as on the socket: the right
    # member, a live claim, the flag on, every coach-side guard clear.
    if call_role(request.user, CALL_KIND, str(check_id)) != CALL_ROLE_MEMBER:
        return _not_open(request, check_id)
    check = (
        ConnectRealPersonCheck.objects.select_related("coach__user")
        .filter(pk=check_id)
        .first()
    )
    if check is None or check.coach is None:
        return _not_open(request, check_id)
    context = video_call_context(CALL_KIND, str(check.pk))
    context["coach_name"] = check.coach.user.first_name
    context["is_admission"] = check.purpose == "connect_week"
    response = render(request, "crush_lu/crush_connect/real_person_call.html", context)
    if context["video_call"]["available"]:
        allow_microphone(response)
    return response


@crush_login_required
@never_cache
def crush_connect_real_person_contact(request, check_id):
    """Where a claimed member says how and when their coach may reach them.

    Only the check's own member, only while a coach holds a live claim, only
    with the contact-request switch on. Everyone else gets a 404; the member
    of a check that is no longer open gets the same "not open" page as the
    call link.
    """
    from crush_lu.real_person_check_rules import CONTACT_PREFERENCES, CONTACT_WINDOWS
    from crush_lu.services.real_person_check import RealPersonCheckError
    from crush_lu.services.real_person_contact import (
        CONTACT_PREFERENCE_LABELS,
        CONTACT_WINDOW_LABELS,
        contact_request_enabled,
        member_contact_check,
        save_contact_details,
    )

    if check_id > _MAX_CHECK_ID or not contact_request_enabled():
        raise Http404
    check = member_contact_check(request.user, check_id)
    if check is None:
        return _not_open(request, check_id)
    error = ""
    chosen = check.contact_preference or ""
    windows = list(check.contact_availability or [])
    if request.method == "POST":
        chosen = request.POST.get("preference", "")
        windows = request.POST.getlist("windows")
        try:
            save_contact_details(
                request.user, check.pk, preference=chosen, windows=windows
            )
        except RealPersonCheckError as exc:
            if exc.code == "invalid_contact_preference":
                error = "preference"
            else:
                return _not_open(request, check_id)
        else:
            return redirect(
                "crush_lu:crush_connect_real_person_contact", check_id=check.pk
            )
    return render(
        request,
        "crush_lu/crush_connect/real_person_contact.html",
        {
            "check": check,
            "coach_name": check.coach.user.first_name,
            "is_admission": check.purpose == "connect_week",
            "answered": bool(check.contact_answered_at) and not error,
            "error": error,
            "preferences": [
                (key, CONTACT_PREFERENCE_LABELS[key], key == chosen)
                for key in CONTACT_PREFERENCES
            ],
            "windows": [
                (key, CONTACT_WINDOW_LABELS[key], key in windows)
                for key in CONTACT_WINDOWS
            ],
        },
        status=400 if error else 200,
    )
