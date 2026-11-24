"""WhatsApp link from a Premium member to the coach they chose.

A Premium member picks a coach at checkout (``PremiumMembership.coach``);
confirming the membership makes that coach ``CrushProfile.assigned_coach``.
This module builds the member-facing ``wa.me`` link to that coach.

* Only for active Premium (``CrushProfile.has_active_premium``) with an
  active assigned coach. ``assigned_coach`` alone is not an entitlement: the
  attendance auto-assign and the 0150 backfill set it without payment.
* Only to a **Crush number**: a coach whose WhatsApp moved into the Crush
  Luxembourg WABA (``CrushCoach.whatsapp_phone_number_id`` set, and
  ``phone_number`` being that number, see the coach-numbers runbook). The
  member's message then lands in that coach's inbox on crush.lu. A coach's
  personal WhatsApp is never offered.
* A coach without a Crush number: the link goes to the main Crush.lu number
  (``CrushSiteConfig.whatsapp_number``), which every coach reads in the
  shared support inbox. The prefilled text names the coach so the team can
  pass it on.
* The member writes first, so this needs no WhatsApp opt-in, and Meta bills
  nothing for the member's message. Coaches reply from the inbox within the
  24-hour window.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from django.utils.translation import gettext as _


@dataclass(frozen=True)
class CoachWhatsAppLink:
    url: str
    coach_id: int
    coach_name: str
    # True when the link goes to the main Crush.lu number instead of the
    # coach's own Crush number.
    via_team: bool


def _digits(value: str) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


def _main_number() -> str:
    from crush_lu.models import CrushSiteConfig

    config = CrushSiteConfig.get_config()
    if not config.whatsapp_enabled:
        return ""
    return _digits(config.whatsapp_number)


def premium_coach_whatsapp(user) -> CoachWhatsAppLink | None:
    """The WhatsApp link to ``user``'s chosen coach, or None.

    None when the user isn't an active Premium member, has no active
    assigned coach, or neither the coach nor the main number is reachable on
    WhatsApp.
    """
    profile = getattr(user, "crushprofile", None)
    if profile is None or not profile.has_active_premium:
        return None
    coach = profile.assigned_coach
    if coach is None or not coach.is_active:
        return None

    coach_name = (coach.user.first_name or "").strip() or _("your coach")
    member_name = (user.first_name or "").strip() or profile.display_name

    coach_number = _digits(coach.phone_number)
    if coach.whatsapp_phone_number_id and coach_number:
        text = _("Hi %(coach)s, it's %(member)s from Crush.lu Premium.") % {
            "coach": coach_name,
            "member": member_name,
        }
        return CoachWhatsAppLink(
            url=f"https://wa.me/{coach_number}?text={quote(text)}",
            coach_id=coach.pk,
            coach_name=coach_name,
            via_team=False,
        )

    main_number = _main_number()
    if not main_number:
        return None
    text = _(
        "Hi, it's %(member)s, a Crush.lu Premium member. "
        "I'd like to talk to my coach %(coach)s."
    ) % {"coach": coach_name, "member": member_name}
    return CoachWhatsAppLink(
        url=f"https://wa.me/{main_number}?text={quote(text)}",
        coach_id=coach.pk,
        coach_name=coach_name,
        via_team=True,
    )


def premium_user_ids(user_ids) -> set:
    """The subset of ``user_ids`` holding active Premium right now."""
    from crush_lu.models import PremiumMembership
    from crush_lu.services.premium_billing import entitled_q

    ids = {uid for uid in user_ids if uid}
    if not ids:
        return set()
    return set(
        PremiumMembership.objects.filter(entitled_q(), user_id__in=ids).values_list(
            "user_id", flat=True
        )
    )
