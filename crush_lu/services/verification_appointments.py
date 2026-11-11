"""Coach-arranged video calls share ScreeningSlot with legacy screening.

The transaction advisory lock serializes allocation across BOTH owner types,
including virtual slots, without reversing profile/submission/check row locks.
"""

from datetime import timedelta, timezone as dt_timezone

from django.db import connection, transaction
from django.utils import timezone

from crush_lu.models import ScreeningSlot
from crush_lu.real_person_check_rules import CLAIM_TTL, PURPOSE_CONNECT_WEEK
from crush_lu.services.real_person_check import (
    RealPersonCheckError,
    _lock_owned_check,
)


def lock_calendar(coach_id):
    """Called inside atomic(), before allocation in either booking path."""
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s, %s)", [92517, int(coach_id)]
            )


def arrange_appointment(coach, check_id, start_at, minutes=15):
    from crush_lu.services.verification_funnel import admission_guards

    now = timezone.now()
    if not start_at or timezone.is_naive(start_at) or not 10 <= minutes <= 60:
        raise RealPersonCheckError("invalid_appointment", 400)
    start_at = start_at.astimezone(dt_timezone.utc)
    if start_at <= now or start_at > now + timedelta(days=30):
        raise RealPersonCheckError("invalid_appointment", 400)
    end_at = start_at + timedelta(minutes=minutes)
    with transaction.atomic():
        lock_calendar(coach.pk)
        profile, check = _lock_owned_check(coach, check_id)
        if check.purpose != PURPOSE_CONNECT_WEEK or not check.is_live(now):
            raise RealPersonCheckError("not_open", 409)
        failures = admission_guards(coach, profile)
        if failures:
            raise RealPersonCheckError(failures[0], 403, failures=failures)
        # A missed appointment can be replaced, but not an upcoming one.
        check.appointments.filter(status="booked", end_at__lte=now).update(
            status="no_show"
        )
        if check.appointments.filter(status="booked").exists():
            raise RealPersonCheckError("appointment_exists", 409)
        available = (
            ScreeningSlot.objects.select_for_update()
            .filter(
                coach=coach,
                start_at=start_at,
                end_at=end_at,
                status="available",
                submission__isnull=True,
                real_person_check__isnull=True,
            )
            .first()
        )
        overlaps = ScreeningSlot.objects.filter(
            coach=coach,
            start_at__lt=end_at,
            end_at__gt=start_at,
        ).exclude(status__in=("cancelled", "no_show"))
        if available:
            overlaps = overlaps.exclude(pk=available.pk)
        if overlaps.exists():
            raise RealPersonCheckError("appointment_overlap", 409)
        if available:
            slot = available
            slot.real_person_check = check
            slot.status = "booked"
            slot.save(update_fields=["real_person_check", "status", "updated_at"])
        else:
            slot = ScreeningSlot.objects.create(
                coach=coach,
                real_person_check=check,
                start_at=start_at,
                end_at=end_at,
                status="booked",
            )
        # An agreed appointment must outlive the normal outreach lease.
        check.lease_expires_at = max(now + CLAIM_TTL, end_at + timedelta(hours=24))
        check.save(update_fields=["lease_expires_at"])
        from crush_lu.services.verification_funnel import notify_outcome

        transaction.on_commit(
            lambda: notify_outcome(check.pk, f"appointment-{slot.pk}"), robust=True
        )
        return slot


def cancel_appointment(coach, check_id):
    from crush_lu.services.verification_funnel import finish_appointments

    with transaction.atomic():
        _profile, check = _lock_owned_check(coach, check_id)
        if check.purpose != PURPOSE_CONNECT_WEEK:
            raise RealPersonCheckError("not_found", 404)
        finish_appointments(check)
