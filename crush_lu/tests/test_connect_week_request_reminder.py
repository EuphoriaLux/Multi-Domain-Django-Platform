"""The one reminder for a Connect Week request that is close to expiry.

Measured on prod over the 90 days to 2026-11-10: 58% of requests expired
unanswered, although the request email went out for about 97% of them. The
recipient got a single notice at whatever hour the sender chose. The hourly
sweep now sends one reminder, in waking hours, before the deadline.

These tests pin when it goes out, that it goes out once, who is skipped, and
that the sweep never advances anyone's session.
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest import mock

import pytest
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client

from crush_lu.models import EmailPreference, Notification
from crush_lu.models.crush_connect_cycle import (
    ConnectWeeklyRequest,
    ConnectWeekSession,
)
from crush_lu.services import connect_cycle
from crush_lu.services.connect_cycle import (
    respond_to_weekly_request,
    send_due_request_reminders,
    send_weekly_request,
)
from crush_lu.tests.test_connect_week_experience import (
    _make_cycle_user,
    _reviewable_session_with_card,
)

REMINDER_TYPE = "connect_week_request_reminder"
INBOX_PATH = "/crush-connect/week/inbox/"

# Thursday 2026-11-12, 08:00 UTC = 09:00 in Luxembourg (winter time).
SENT_AT = datetime(2026, 11, 12, 8, 0, tzinfo=dt_timezone.utc)


def _at(moment):
    """Freeze ``timezone.now`` everywhere, the request's own clock included."""
    return mock.patch("django.utils.timezone.now", return_value=moment)


@pytest.fixture
def pending(settings):
    """A request sent at 09:00 Luxembourg time; it expires 24 hours later."""
    cache.clear()
    settings.CRUSH_CONNECT_LAUNCHED = True
    settings.ROOT_URLCONF = "azureproject.urls_crush"
    with _at(SENT_AT):
        sender = _make_cycle_user("remind_sender")
        recipient = _make_cycle_user("remind_recipient", gender="F")
        session, _ = _reviewable_session_with_card(sender, recipient)
        weekly_request = send_weekly_request(session, sender, recipient)
    mail.outbox.clear()
    return sender, recipient, weekly_request


def _reminders(recipient):
    return Notification.objects.filter(user=recipient, notification_type=REMINDER_TYPE)


def _sweep(moment):
    with _at(moment):
        return send_due_request_reminders()


@pytest.mark.django_db
def test_no_reminder_while_the_deadline_is_far(pending):
    _, recipient, _ = pending

    # 15:00 Luxembourg: 18 hours left, more than the lead.
    counts = _sweep(SENT_AT + timedelta(hours=6))

    assert counts["due"] == 0
    assert mail.outbox == []
    assert not _reminders(recipient).exists()


@pytest.mark.django_db
def test_reminder_goes_out_once_inside_the_lead(pending):
    sender, recipient, weekly_request = pending

    # 20:00 Luxembourg: 13 hours left.
    moment = SENT_AT + timedelta(hours=11)
    counts = _sweep(moment)

    assert counts["sent"] == 1
    bell = _reminders(recipient).get()
    assert bell.metadata == {"weekly_request_id": weekly_request.pk}
    assert bell.link_url == INBOX_PATH
    assert bell.dedupe_key == f"weekly-request:{weekly_request.pk}"
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == [recipient.email]
    assert sender.crushprofile.display_name in message.subject
    assert INBOX_PATH in message.alternatives[0][0]

    # The next hourly sweeps find nothing left to do.
    again = _sweep(moment + timedelta(minutes=30))
    assert again["due"] == 0
    assert len(mail.outbox) == 1
    assert _reminders(recipient).count() == 1


@pytest.mark.django_db
def test_a_lost_claim_sends_nothing(pending):
    """Two overlapping sweeps: the one that loses the bell-row claim stops."""
    _, recipient, weekly_request = pending
    Notification.objects.create(
        user=recipient,
        notification_type=REMINDER_TYPE,
        title="claimed by the other sweep",
        dedupe_key=f"weekly-request:{weekly_request.pk}",
    )

    from crush_lu.notification_service import notify_connect_week_request_reminder

    with _at(SENT_AT + timedelta(hours=11)):
        result = notify_connect_week_request_reminder(recipient, weekly_request)

    assert result.deduped
    assert mail.outbox == []


@pytest.mark.django_db
def test_quiet_hours_wait_for_the_morning(pending):
    _, recipient, _ = pending

    # 22:00 Luxembourg: inside the lead, but nobody is nudged at night.
    night = _sweep(SENT_AT + timedelta(hours=13))
    assert night["quiet_hours"] is True
    # 07:59 Luxembourg the next day is still quiet.
    early = _sweep(SENT_AT + timedelta(hours=22, minutes=59))
    assert early["quiet_hours"] is True

    assert mail.outbox == []
    assert not _reminders(recipient).exists()


@pytest.mark.django_db
def test_first_waking_sweep_after_a_night_lead_sends(settings):
    cache.clear()
    settings.CRUSH_CONNECT_LAUNCHED = True
    settings.ROOT_URLCONF = "azureproject.urls_crush"
    sent_at = SENT_AT + timedelta(hours=6)  # 15:00 Luxembourg
    with _at(sent_at):
        sender = _make_cycle_user("remind_night_sender")
        recipient = _make_cycle_user("remind_night_recipient", gender="F")
        session, _ = _reviewable_session_with_card(sender, recipient)
        send_weekly_request(session, sender, recipient)
    mail.outbox.clear()

    # 03:00 Luxembourg: 12 hours left, inside the lead, still night.
    assert _sweep(sent_at + timedelta(hours=12))["quiet_hours"] is True
    # 08:25 Luxembourg: the first waking sweep, 6 h 35 left.
    counts = _sweep(sent_at + timedelta(hours=17, minutes=25))

    assert counts["sent"] == 1
    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_no_reminder_with_less_than_an_hour_left(pending):
    _, recipient, _ = pending

    # 08:30 Luxembourg the next day: 30 minutes left.
    counts = _sweep(SENT_AT + timedelta(hours=23, minutes=30))

    assert counts["quiet_hours"] is False
    assert counts["due"] == 0
    assert not _reminders(recipient).exists()


@pytest.mark.django_db
def test_an_answered_request_is_not_reminded(pending):
    _, recipient, weekly_request = pending
    with _at(SENT_AT + timedelta(hours=2)):
        respond_to_weekly_request(weekly_request, accept=False)

    counts = _sweep(SENT_AT + timedelta(hours=11))

    assert counts["due"] == 0
    assert mail.outbox == []


@pytest.mark.django_db
def test_a_request_the_inbox_hides_is_not_reminded(pending):
    """The requester was rejected after sending: the inbox no longer shows
    the request, so a reminder would send the member to an empty page."""
    sender, recipient, _ = pending
    # A queryset update: CrushProfile.save() would re-verify from is_approved.
    type(sender.crushprofile).objects.filter(pk=sender.crushprofile.pk).update(
        verification_status="rejected", is_approved=False
    )

    counts = _sweep(SENT_AT + timedelta(hours=11))

    assert counts["due"] == 1
    assert counts["hidden"] == 1
    assert counts["sent"] == 0
    assert mail.outbox == []
    assert not _reminders(recipient).exists()


@pytest.mark.django_db
def test_email_opt_out_keeps_the_bell(pending):
    _, recipient, _ = pending
    prefs = EmailPreference.get_or_create_for_user(recipient)
    prefs.email_new_connections = False
    prefs.save()

    counts = _sweep(SENT_AT + timedelta(hours=11))

    assert counts["sent"] == 1
    assert mail.outbox == []
    assert _reminders(recipient).count() == 1


@pytest.mark.django_db
def test_reminder_reads_in_the_recipients_language(pending):
    _, recipient, _ = pending
    recipient.crushprofile.preferred_language = "de"
    recipient.crushprofile.save(update_fields=["preferred_language"])

    _sweep(SENT_AT + timedelta(hours=11))

    assert _reminders(recipient).get().title == "Eine Anfrage wartet auf deine Antwort"
    assert "wartet auf deine Antwort" in mail.outbox[0].subject
    body = mail.outbox[0].alternatives[0][0]
    assert "und du hast noch nicht geantwortet" in body
    assert "Die Anfrage läuft bald ab" in body


@pytest.mark.django_db
def test_french_reminder_uses_the_formal_register(pending):
    _, recipient, _ = pending
    recipient.crushprofile.preferred_language = "fr"
    recipient.crushprofile.save(update_fields=["preferred_language"])

    _sweep(SENT_AT + timedelta(hours=11))

    assert _reminders(recipient).get().title == "Une demande attend votre réponse"
    assert "attend votre réponse" in mail.outbox[0].subject
    body = mail.outbox[0].alternatives[0][0]
    assert "pas encore répondu" in body
    assert "La demande expire bientôt" in body


@pytest.mark.django_db
def test_a_paused_recipient_is_not_reminded(pending):
    """The inbox redirects a paused member away, so it must not be linked."""
    _, recipient, _ = pending
    recipient.crush_connect_membership.pause()

    counts = _sweep(SENT_AT + timedelta(hours=11))

    assert counts["hidden"] == 1
    assert mail.outbox == []


@pytest.mark.django_db
def test_no_reminder_when_connect_is_closed(pending, settings):
    _, recipient, _ = pending
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = False

    counts = _sweep(SENT_AT + timedelta(hours=11))

    assert counts["hidden"] == 1
    assert mail.outbox == []


@pytest.mark.django_db
def test_web_push_replaces_the_requests_own_push(pending):
    from crush_lu.models import PushSubscription

    sender, recipient, weekly_request = pending
    PushSubscription.objects.create(
        user=recipient,
        endpoint="https://push.example.test/sub",
        p256dh_key="p256dh",
        auth_key="auth",
    )

    with mock.patch(
        "crush_lu.push_notifications.send_push_notification",
        return_value={"success": 1, "failed": 0, "total": 1},
    ) as mocked:
        _sweep(SENT_AT + timedelta(hours=11))

    mocked.assert_called_once()
    kwargs = mocked.call_args.kwargs
    assert kwargs["user"] == recipient
    assert kwargs["tag"] == f"connect-week-request-{weekly_request.pk}"
    assert kwargs["url"].endswith(INBOX_PATH)
    assert sender.crushprofile.display_name in kwargs["body"]


@pytest.mark.django_db
def test_the_sweep_never_advances_a_session(pending):
    """Syncing a dormant session opens its 24h review while the member is
    away; the sweep must only read requests."""
    _, recipient, _ = pending
    with _at(SENT_AT - timedelta(days=20)):
        dormant = ConnectWeekSession.objects.create(user=recipient)
    ConnectWeekSession.objects.filter(pk=dormant.pk).update(
        started_at=SENT_AT - timedelta(days=20)
    )

    with mock.patch.object(connect_cycle, "sync_session_state") as synced:
        _sweep(SENT_AT + timedelta(hours=11))

    synced.assert_not_called()
    dormant.refresh_from_db()
    assert dormant.status == ConnectWeekSession.Status.ACTIVE
    assert dormant.review_opened_at is None


@pytest.mark.django_db
def test_one_failing_request_does_not_stop_the_sweep(pending):
    _, recipient, weekly_request = pending

    with mock.patch(
        "crush_lu.notification_service.NotificationService.notify",
        side_effect=RuntimeError("notification stack down"),
    ):
        counts = _sweep(SENT_AT + timedelta(hours=11))

    assert counts["failed"] == 1
    assert ConnectWeeklyRequest.objects.filter(
        pk=weekly_request.pk, status=ConnectWeeklyRequest.Status.PENDING
    ).exists()


@pytest.mark.django_db
def test_dry_run_counts_without_sending(pending):
    _, recipient, _ = pending

    with _at(SENT_AT + timedelta(hours=11)):
        call_command("send_connect_request_reminders", "--dry-run")

    assert mail.outbox == []
    assert not _reminders(recipient).exists()


@pytest.mark.django_db
def test_the_hourly_recaps_endpoint_sends_the_reminder(pending, settings):
    _, recipient, _ = pending
    settings.ADMIN_API_KEY = "test-admin-api-key"

    with _at(SENT_AT + timedelta(hours=11)):
        response = Client(HTTP_HOST="crush.lu").post(
            "/api/admin/event-recaps/",
            HTTP_AUTHORIZATION="Bearer test-admin-api-key",
        )

    assert response.status_code == 202
    assert _reminders(recipient).count() == 1
    assert len(mail.outbox) == 1
