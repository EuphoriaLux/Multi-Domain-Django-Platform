"""Commit ordering of the waitlist promotion notice, with real commits.

The promoted member's bell must exist before the canceller's email is
attempted: that email is a synchronous network send, and a stall there until
the worker is killed must not delay or lose the promoted member's only
guaranteed notice (review on #1241).

Its own module because it needs ``django_db(transaction=True)``: Django's
``TestCase`` defers every on_commit callback to the end of the test, so it
cannot show this ordering. Flushing tests are paired with the seeded-row
restore by ``crush_lu/tests/conftest.py``, which works per module.
"""

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

pytestmark = pytest.mark.django_db(transaction=True)

BELL_TYPE = "event_waitlist_promoted"


@pytest.fixture
def seat(django_user_model):
    """A one-seat event, its seat-holder, and a waitlisted member who has
    switched off event-reminder emails."""
    from crush_lu.models import (
        CrushProfile,
        EmailPreference,
        EventRegistration,
        MeetupEvent,
        UserDataConsent,
    )

    cache.clear()
    event = MeetupEvent.objects.create(
        title="Commit Order Mixer",
        description="Promotion notice ordering",
        event_type="mixer",
        date_time=timezone.now() + timedelta(days=7),
        location="Luxembourg",
        address="123 Test Street",
        max_participants=1,
        registration_deadline=timezone.now() + timedelta(days=5),
        is_published=True,
    )

    def member(email):
        user = django_user_model.objects.create_user(
            username=email, email=email, password="testpass123"
        )
        CrushProfile.objects.create(
            user=user, date_of_birth=date(1995, 1, 1), gender="F", location="Lux"
        )
        UserDataConsent.objects.update_or_create(
            user=user, defaults={"crushlu_consent_given": True}
        )
        return user

    holder_user = member("order-holder@example.com")
    waiter = member("order-waiter@example.com")
    prefs = EmailPreference.get_or_create_for_user(waiter)
    prefs.email_event_reminders = False
    prefs.save()
    return SimpleNamespace(
        event=event,
        holder_user=holder_user,
        holder=EventRegistration.objects.create(
            event=event, user=holder_user, status="confirmed"
        ),
        waiter=waiter,
        waiting=EventRegistration.objects.create(
            event=event, user=waiter, status="waitlist"
        ),
    )


def _bell_seen_when(seat, target):
    """Patch the canceller's email so it records whether the promoted
    member's bell already existed when it was attempted."""
    from crush_lu.models import Notification

    seen = []

    def record(*args, **kwargs):
        seen.append(
            Notification.objects.filter(
                user=seat.waiter, notification_type=BELL_TYPE
            ).exists()
        )
        return 1

    return seen, patch(target, side_effect=record)


def test_cancellation_made_outside_the_member_page(seat):
    seen, patcher = _bell_seen_when(
        seat, "crush_lu.views_payments._send_member_cancellation_safely"
    )
    with patcher:
        seat.holder.status = "cancelled"
        seat.holder.save()

    seat.waiting.refresh_from_db()
    assert seat.waiting.status == "confirmed"
    assert seen == [True]


def test_member_cancel_page(seat):
    client = Client(HTTP_HOST="crush.lu")
    client.force_login(seat.holder_user)
    seen, patcher = _bell_seen_when(
        seat, "crush_lu.views_events.send_event_cancellation_confirmation"
    )
    with patcher:
        response = client.post(f"/en/events/{seat.event.id}/cancel/")

    assert response.status_code == 302
    seat.waiting.refresh_from_db()
    assert seat.waiting.status == "confirmed"
    assert seen == [True]
