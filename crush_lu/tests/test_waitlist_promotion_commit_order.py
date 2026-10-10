"""Commit ordering of the waitlist promotion notice, with real commits.

The promoted member's bell must exist before the canceller's email is
attempted: that email is a synchronous network send, and a stall there until
the worker is killed must not delay or lose the promoted member's only
guaranteed notice (review on #1241). The same holds for the Wallet refreshes
the promoted row's own save queues on commit, ahead of any later callback.

Its own module because it needs ``django_db(transaction=True)``: Django's
``TestCase`` defers every on_commit callback to the end of the test, so it
cannot show this ordering, nor that the callbacks run inside the canceller's
request. Flushing tests are paired with the seeded-row restore by
``crush_lu/tests/conftest.py``, which works per module.
"""

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core import mail
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

pytestmark = pytest.mark.django_db(transaction=True)

BELL_TYPE = "event_waitlist_promoted"


class WorkerKilled(BaseException):
    """What a gunicorn timeout does to the request: not an Exception, so no
    ``except Exception`` on the way out can swallow it."""


def _seat(django_user_model, *, waiter_has_profile=True):
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
        # Only an open event admits an account without a CrushProfile.
        **({} if waiter_has_profile else {"profile_requirement": "none"}),
    )

    def member(email, profile=True):
        user = django_user_model.objects.create_user(
            username=email, email=email, password="testpass123"
        )
        if profile:
            CrushProfile.objects.create(
                user=user, date_of_birth=date(1995, 1, 1), gender="F", location="Lux"
            )
        UserDataConsent.objects.update_or_create(
            user=user, defaults={"crushlu_consent_given": True}
        )
        return user

    holder_user = member("order-holder@example.com")
    waiter = member("order-waiter@example.com", profile=waiter_has_profile)
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


@pytest.fixture
def seat(django_user_model):
    return _seat(django_user_model)


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


def _cancel_page(seat, language="en"):
    """The seat-holder cancels on their own page, as on crush.lu."""
    client = Client(HTTP_HOST="crush.lu")
    client.force_login(seat.holder_user)
    return client.post(f"/{language}/events/{seat.event.id}/cancel/")


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
    seen, patcher = _bell_seen_when(
        seat, "crush_lu.views_events.send_event_cancellation_confirmation"
    )
    with patcher:
        response = _cancel_page(seat)

    assert response.status_code == 302
    seat.waiting.refresh_from_db()
    assert seat.waiting.status == "confirmed"
    assert seen == [True]


@pytest.fixture
def wallet_refresh_hangs(seat):
    """The promoted member holds a Google Wallet pass, and its refresh (two
    HTTP calls at 30s each) runs until the worker is killed. The promoted
    row's own save queues it on commit, ahead of any later callback."""
    from crush_lu.models import CrushProfile

    CrushProfile.objects.filter(user=seat.waiter).update(
        google_wallet_object_id="issuer.order-waiter"
    )
    with patch(
        "crush_lu.wallet.google_api.update_google_wallet_pass",
        side_effect=WorkerKilled,
    ) as refresh:
        yield refresh


def _assert_bell_committed_with_the_seat(seat, refresh):
    from crush_lu.models import Notification

    refresh.assert_called_once()
    seat.waiting.refresh_from_db()
    assert seat.waiting.status == "confirmed"
    assert (
        Notification.objects.filter(
            user=seat.waiter, notification_type=BELL_TYPE
        ).count()
        == 1
    )
    # Email never got its turn; coaches see that only the bell reached them.
    assert [m for m in mail.outbox if seat.waiter.email in m.to] == []
    assert seat.waiting.promotion_notice == "bell"


def test_capacity_increase_bell_survives_a_hung_wallet_refresh(
    seat, wallet_refresh_hangs
):
    seat.event.max_participants = 2
    with pytest.raises(WorkerKilled):
        seat.event.save()

    _assert_bell_committed_with_the_seat(seat, wallet_refresh_hangs)


def test_cancellation_bell_survives_a_hung_wallet_refresh(seat, wallet_refresh_hangs):
    seat.holder.status = "cancelled"
    with pytest.raises(WorkerKilled):
        seat.holder.save()

    _assert_bell_committed_with_the_seat(seat, wallet_refresh_hangs)


def test_member_cancel_page_bell_survives_a_hung_wallet_refresh(
    seat, wallet_refresh_hangs
):
    with pytest.raises(WorkerKilled):
        _cancel_page(seat)

    _assert_bell_committed_with_the_seat(seat, wallet_refresh_hangs)


def test_notice_ignores_the_cancellers_language(django_user_model):
    """The notice is about another member. One without a CrushProfile has no
    stored language and gets the site default, not the French of the
    canceller whose request runs the promotion and its callbacks."""
    from crush_lu.models import CrushProfile, Notification

    seat = _seat(django_user_model, waiter_has_profile=False)
    CrushProfile.objects.filter(user=seat.holder_user).update(preferred_language="fr")

    response = _cancel_page(seat, language="fr")

    assert response.status_code == 302
    seat.waiting.refresh_from_db()
    assert seat.waiting.status == "confirmed"
    bell = Notification.objects.get(user=seat.waiter, notification_type=BELL_TYPE)
    assert bell.title == "A spot opened up: you're in for Commit Order Mixer"
    [sent] = [m for m in mail.outbox if seat.waiter.email in m.to]
    assert sent.subject == "Event Registration Confirmed - Commit Order Mixer"
    assert f"https://crush.lu/en/events/{seat.event.id}/" in sent.body
    assert "/fr/" not in sent.body
