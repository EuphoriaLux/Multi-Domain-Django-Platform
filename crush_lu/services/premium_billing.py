"""Premium subscription lifecycle: billing periods, renewal, cancellation, lapse.

Provider-neutral on purpose. SumUp refused the platform (2026-10) and the next
provider is not chosen, so nothing here charges a card. The provider adapter
that comes with it is expected to:

- read :func:`renewals_due` and charge ``premium_monthly_fee()`` for each row;
- call :func:`record_renewal` once a renewal charge is captured.

Product rules (Tom, 2026-10-17; business-memory-hub
memories/premium-offer-definition.md):

- The first period follows the first payment: the onboarding fee buys month 1
  (free month), so it ends one month after confirmation; an event-verified
  member pays month 1 and gets month 2 free, so it ends after two months.
- Every renewal adds one month from the *previous* period end, never from the
  moment it is recorded, so a late charge does not shorten the next month.
- A renewal that does not arrive keeps Premium for ``PREMIUM_RENEWAL_GRACE_DAYS``
  (3) after the period end; paying within that window keeps the same coach,
  because nothing is released until the lapse sweep runs.
- A member who cancels keeps Premium until the period end, with no grace and
  no further charge, and can undo it until then.

A ``NULL`` ``current_period_end`` means the membership never expires: the beta
testers comped by migration 0184 and anything confirmed before this existed.

Entitlement is read lazily (:func:`entitled_q`), so an expired row stops
granting Premium the moment its time is up even if the sweep has not run; the
sweep (:func:`sweep_lapsed`) only does the side effects -- status and coach.
"""

from __future__ import annotations

import calendar
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

DEFAULT_GRACE_DAYS = 3
LAPSE_REASON = "premium period ended without renewal"
CANCELLED_REASON = "member cancelled at the end of the paid period"


def grace_period():
    return timedelta(
        days=int(getattr(settings, "PREMIUM_RENEWAL_GRACE_DAYS", DEFAULT_GRACE_DAYS))
    )


def add_months(moment, months):
    """``moment`` plus ``months`` calendar months, clamped to the month's end
    (31 Jan + 1 month = 28/29 Feb)."""
    month_index = moment.month - 1 + months
    year = moment.year + month_index // 12
    month = month_index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def first_period_months(onboarding_fee):
    """Months the first payment covers: one (onboarding, month 1 free) or two
    (event-verified: month 1 paid, month 2 free)."""
    return 1 if onboarding_fee and onboarding_fee > 0 else 2


def entitled_q(prefix="", now=None):
    """``Q`` for memberships that grant Premium right now.

    ``prefix`` scopes it through a relation, e.g. ``"premium_memberships__"``
    on a User queryset. Keep the whole ``Q`` in one ``filter()`` call there so
    every condition applies to the same membership row.
    """
    now = now or timezone.now()
    p = prefix
    return Q(**{f"{p}status": "active"}) & (
        Q(**{f"{p}current_period_end__isnull": True})
        | Q(
            **{
                f"{p}cancel_at_period_end": False,
                f"{p}current_period_end__gt": now - grace_period(),
            }
        )
        | Q(
            **{
                f"{p}cancel_at_period_end": True,
                f"{p}current_period_end__gt": now,
            }
        )
    )


def lapse_due(now=None):
    """Active memberships whose time is up: the complement of
    :func:`entitled_q` among ``active`` rows with a period end."""
    from crush_lu.models import PremiumMembership

    now = now or timezone.now()
    return PremiumMembership.objects.filter(
        status="active", current_period_end__isnull=False
    ).exclude(entitled_q(now=now))


def renewals_due(now=None, lead=timedelta(0)):
    """Memberships the provider adapter should charge: active, renewing, and
    whose period ends within ``lead`` (or has ended, still inside grace)."""
    from crush_lu.models import PremiumMembership

    now = now or timezone.now()
    return PremiumMembership.objects.filter(
        status="active",
        cancel_at_period_end=False,
        current_period_end__isnull=False,
        current_period_end__lte=now + lead,
        current_period_end__gt=now - grace_period(),
    )


def record_renewal(membership):
    """Extend ``membership`` by one month after a captured renewal charge.

    Raises ``ValueError`` when it no longer grants Premium (lapsed, cancelled
    by the member, or never-expiring): the charge then needs a refund or a new
    membership, not an extension.
    """
    from crush_lu.models import PremiumMembership

    with transaction.atomic():
        locked = (
            PremiumMembership.objects.select_for_update()
            .filter(pk=membership.pk)
            .first()
        )
        if locked is None or locked.status != "active":
            raise ValueError("Only an active membership can be renewed.")
        if locked.current_period_end is None:
            raise ValueError("This membership has no billing period to renew.")
        if locked.cancel_at_period_end:
            raise ValueError("The member cancelled; this membership does not renew.")
        if not PremiumMembership.objects.filter(entitled_q(), pk=locked.pk).exists():
            raise ValueError("The grace period is over; the membership lapsed.")
        locked.current_period_end = add_months(locked.current_period_end, 1)
        locked.save(update_fields=["current_period_end"])
    membership.current_period_end = locked.current_period_end
    return locked.current_period_end


def _set_cancel_at_period_end(membership, value):
    from crush_lu.models import PremiumMembership

    with transaction.atomic():
        locked = (
            PremiumMembership.objects.select_for_update()
            .filter(pk=membership.pk)
            .first()
        )
        if (
            locked is None
            or locked.status != "active"
            or locked.current_period_end is None
            or locked.cancel_at_period_end == value
            # In the grace after an unpaid period end there is nothing left to
            # cancel: cancelling would switch to the no-grace rule and end
            # Premium on the spot, with no way back (Codex #1277).
            or (value and locked.current_period_end <= timezone.now())
            or not PremiumMembership.objects.filter(entitled_q(), pk=locked.pk).exists()
        ):
            return False
        locked.cancel_at_period_end = value
        locked.save(update_fields=["cancel_at_period_end"])
    membership.cancel_at_period_end = value
    return True


def schedule_cancellation(membership):
    """The member stops renewing; Premium stays until the period end.
    Returns False when there is nothing to cancel."""
    return _set_cancel_at_period_end(membership, True)


def resume(membership):
    """Undo :func:`schedule_cancellation` before the period ends."""
    return _set_cancel_at_period_end(membership, False)


def sweep_lapsed(now=None):
    """End every membership whose time is up; returns how many were ended.

    ``cancel_active`` re-checks status under its own lock, so a renewal recorded
    between the read and the write is never ended: the row is re-read first.
    """
    from crush_lu.models import PremiumMembership

    now = now or timezone.now()
    ended = 0
    for membership in lapse_due(now).select_related("user"):
        with transaction.atomic():
            locked = (
                PremiumMembership.objects.select_for_update()
                .filter(pk=membership.pk)
                .first()
            )
            if locked is None or not lapse_due(now).filter(pk=locked.pk).exists():
                continue
            reason = CANCELLED_REASON if locked.cancel_at_period_end else LAPSE_REASON
            if locked.cancel_active(reason=reason, keep_payment_record=True):
                ended += 1
                logger.info(
                    "Premium membership %s (user=%s) ended: %s",
                    locked.pk,
                    locked.user_id,
                    reason,
                )
    return ended
