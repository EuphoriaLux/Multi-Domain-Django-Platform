"""Ask a claimed member how and when their coach may reach them.

After a coach claims a member for a real-person check, the member gets an
in-app notice and an email linking to a short form: phone call, SMS first or
WhatsApp first, and rough time windows. The coach's check page shows the
answer. The coach still arranges the time; this is not self-booking.

* Off unless ``CONNECT_REAL_PERSON_CONTACT_REQUEST_ENABLED`` and the tooling
  flag are both on.
* One request per member per ``CONTACT_REQUEST_COOLDOWN``, however many coaches
  claim them, and a recent answer is carried onto the new claim.
* ``contact_notice`` records what was really sent, so the coach is never told
  "asked by email" when the member's settings refused it.
* "WhatsApp first" allows the WhatsApp link for this check only. It never
  writes ``EmailPreference.whatsapp_opt_in``, which campaigns read.

Lock order: profile, then check (as everywhere in real_person_check).
Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md
"""

import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _l

from crush_lu.real_person_check_rules import (
    CONTACT_ANSWER_TTL,
    CONTACT_NOTICE_BELL,
    CONTACT_NOTICE_EMAIL,
    CONTACT_NOTICE_FAILED,
    CONTACT_PREFERENCES,
    CONTACT_REQUEST_COOLDOWN,
    CONTACT_WHATSAPP,
    CONTACT_WINDOWS,
)
from crush_lu.services.real_person_check import (
    RealPersonCheckError,
    _lock_check,
    _lock_profile,
    real_person_check_enabled,
)

logger = logging.getLogger(__name__)

CONTACT_PREFERENCE_LABELS = {
    "call": _l("Phone call"),
    "sms": _l("SMS first"),
    "whatsapp": _l("WhatsApp first"),
}
CONTACT_WINDOW_LABELS = {
    "weekday_morning": _l("Weekday mornings"),
    "weekday_lunch": _l("Weekday lunchtime"),
    "weekday_evening": _l("Weekday evenings"),
    "weekend_morning": _l("Weekend mornings"),
    "weekend_afternoon": _l("Weekend afternoons"),
    "weekend_evening": _l("Weekend evenings"),
}


def contact_request_enabled() -> bool:
    return bool(
        real_person_check_enabled()
        and getattr(settings, "CONNECT_REAL_PERSON_CONTACT_REQUEST_ENABLED", False)
    )


def window_labels(keys):
    """Labels for stored window keys, in the fixed order, unknown keys dropped."""
    keys = set(keys or [])
    return [CONTACT_WINDOW_LABELS[key] for key in CONTACT_WINDOWS if key in keys]


def whatsapp_chosen_for(check) -> bool:
    """The member picked "WhatsApp first" for this check (this check only)."""
    return contact_request_enabled() and check.contact_preference == CONTACT_WHATSAPP


def queue_contact_request(check_id):
    """Call inside the claim's transaction, on the path that sets a new coach."""
    if contact_request_enabled():
        transaction.on_commit(lambda: request_contact_details(check_id), robust=True)


def request_contact_details(check_id, *, now=None):
    """Carry a recent answer over, or ask the member once. Never raises."""
    try:
        return _request_contact_details(check_id, now or timezone.now())
    except Exception:
        logger.exception("Contact request for real-person check %s failed", check_id)
        return ""


def _request_contact_details(check_id, now):
    from crush_lu.models import ConnectRealPersonCheck

    if not contact_request_enabled():
        return ""
    check = (
        ConnectRealPersonCheck.objects.select_related("profile__user", "coach__user")
        .filter(pk=check_id)
        .first()
    )
    if check is None or check.coach is None or not check.is_live(now):
        return ""
    if check.contact_answered_at or check.contact_requested_at:
        # A video request reclaimed by another coach keeps what it has.
        return check.contact_notice or ""

    earlier = ConnectRealPersonCheck.objects.filter(profile_id=check.profile_id)
    earlier = earlier.exclude(pk=check.pk)
    answered = (
        earlier.filter(contact_answered_at__gte=now - CONTACT_ANSWER_TTL)
        .order_by("-contact_answered_at")
        .first()
    )
    if answered is not None:
        ConnectRealPersonCheck.objects.filter(pk=check.pk).update(
            contact_preference=answered.contact_preference,
            contact_availability=answered.contact_availability,
            contact_answered_at=answered.contact_answered_at,
            contact_requested_at=answered.contact_requested_at,
            contact_notice=answered.contact_notice,
        )
        return answered.contact_notice or ""
    asked = (
        earlier.filter(contact_requested_at__gte=now - CONTACT_REQUEST_COOLDOWN)
        .order_by("-contact_requested_at")
        .first()
    )
    if asked is not None:
        # Asked recently for an earlier claim: the same form answers this one.
        ConnectRealPersonCheck.objects.filter(pk=check.pk).update(
            contact_requested_at=asked.contact_requested_at,
            contact_notice=asked.contact_notice,
        )
        _write_member_notice(check)
        return asked.contact_notice or ""

    _write_member_notice(check)
    notice = CONTACT_NOTICE_BELL
    try:
        from crush_lu.email_helpers import send_real_person_contact_request

        if send_real_person_contact_request(check):
            notice = CONTACT_NOTICE_EMAIL
    except Exception:
        logger.warning(
            "Contact request email for real-person check %s failed", check.pk
        )
        notice = CONTACT_NOTICE_FAILED
    ConnectRealPersonCheck.objects.filter(pk=check.pk).update(
        contact_requested_at=now, contact_notice=notice
    )
    return notice


def _write_member_notice(check):
    """The bell row, in the member's language; one per check."""
    from django.urls import reverse

    from crush_lu.models import Notification

    coach_name = check.coach.user.first_name
    with translation.override(check.profile.preferred_language or "en"):
        Notification.objects.get_or_create(
            user_id=check.profile.user_id,
            notification_type="real_person_contact",
            dedupe_key=f"request:{check.pk}",
            defaults={
                "title": _("%(coach)s, a Crush coach, would like a short call")
                % {"coach": coach_name},
                "body": _("Tell them how and when to reach you."),
                "link_url": reverse(
                    "crush_lu:crush_connect_real_person_contact",
                    args=[check.pk],
                    urlconf="azureproject.urls_crush",
                ),
            },
        )


def member_contact_check(user, check_id, *, now=None):
    """The member's own live, claimed check, or None."""
    from crush_lu.models import ConnectRealPersonCheck

    if not contact_request_enabled():
        return None
    check = (
        ConnectRealPersonCheck.objects.select_related("coach__user", "profile")
        .filter(pk=check_id, profile__user_id=user.pk)
        .first()
    )
    if check is None or check.coach is None or not check.is_live(now):
        return None
    return check


def save_contact_details(user, check_id, *, preference, windows, now=None):
    """Store the member's answer on their own live check and tell the coach."""
    from crush_lu.models import ConnectRealPersonCheck, CrushProfile, Notification

    now = now or timezone.now()
    windows = [key for key in CONTACT_WINDOWS if key in set(windows or [])]
    if preference not in CONTACT_PREFERENCES:
        raise RealPersonCheckError("invalid_contact_preference", 400)
    if not contact_request_enabled():
        raise RealPersonCheckError("not_found", 404)
    profile_id = (
        CrushProfile.objects.filter(user_id=user.pk)
        .values_list("pk", flat=True)
        .first()
    )
    if profile_id is None:
        raise RealPersonCheckError("not_found", 404)
    with transaction.atomic():
        _lock_profile(profile_id)
        if not ConnectRealPersonCheck.objects.filter(
            pk=check_id, profile_id=profile_id
        ).exists():
            raise RealPersonCheckError("not_found", 404)
        check = _lock_check(check_id)
        if check.coach_id is None or not check.is_live(now):
            raise RealPersonCheckError("not_open", 409)
        check.contact_preference = preference
        check.contact_availability = windows
        check.contact_answered_at = now
        check.save(
            update_fields=[
                "contact_preference",
                "contact_availability",
                "contact_answered_at",
            ]
        )
        coach_user_id = check.coach.user_id
        coach_language = getattr(check.coach.user, "crushprofile", None)
    # The coach's bell, after commit and never a reason to fail the save.
    try:
        from django.urls import reverse

        with translation.override(
            getattr(coach_language, "preferred_language", None) or "en"
        ):
            Notification.objects.get_or_create(
                user_id=coach_user_id,
                notification_type="real_person_contact",
                dedupe_key=f"answer:{check.pk}",
                defaults={
                    "title": _("A member answered your contact request"),
                    "body": _("Open the check to see how and when to reach them."),
                    "link_url": reverse(
                        "crush_lu:coach_real_person_check",
                        args=[check.pk],
                        urlconf="azureproject.urls_crush",
                    ),
                },
            )
    except Exception:
        logger.warning("Coach notice for real-person check %s failed", check.pk)
    return check
