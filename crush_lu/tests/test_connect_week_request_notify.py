"""The Connect Week request-received notification: email + push + bell, in
the recipient's language, and never able to block the send.

Before this, ``_notify_weekly_request_received`` wrote a bell row only,
rendered in whatever locale the *sender's* view had active. On prod
(2026-09-17) 17 of 24 weekly requests expired without the recipient ever
seeing them. These tests pin the three channels and the two things that make
them safe: preference opt-outs are honoured per channel, and a notification
failure cannot roll back or block the request itself.
"""

from unittest.mock import patch

import pytest
from django.core import mail
from django.utils import translation

from crush_lu.models import EmailPreference, Notification, PushSubscription
from crush_lu.models.crush_connect_cycle import ConnectWeeklyRequest
from crush_lu.services.connect_cycle import send_weekly_request
from crush_lu.tests.test_connect_week_experience import (
    _make_cycle_user,
    _reviewable_session_with_card,
)

BELL_TYPE = "connect_week_request_received"
INBOX_PATH = "/crush-connect/week/inbox/"


def _pair(settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    # Every channel builds its deep link with reverse("crush_lu:…").
    settings.ROOT_URLCONF = "azureproject.urls_crush"
    sender = _make_cycle_user("notify_a_sender")
    recipient = _make_cycle_user("notify_a_recipient", gender="F")
    session, _ = _reviewable_session_with_card(sender, recipient)
    return sender, recipient, session


def _bell(recipient):
    return Notification.objects.get(user=recipient, notification_type=BELL_TYPE)


@pytest.mark.django_db
def test_request_writes_bell_and_sends_email(settings):
    sender, recipient, session = _pair(settings)

    weekly_request = send_weekly_request(session, sender, recipient)

    bell = _bell(recipient)
    assert bell.metadata == {"weekly_request_id": weekly_request.pk}
    assert bell.link_url == INBOX_PATH

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == [recipient.email]
    assert sender.crushprofile.display_name in message.subject
    html = message.alternatives[0][0]
    # No request object was passed: the link is absolute on the canonical domain.
    assert "crush.lu/" in html
    assert INBOX_PATH in html
    assert sender.crushprofile.display_name in html


@pytest.mark.django_db
def test_email_honours_new_connections_opt_out_but_bell_stays(settings):
    sender, recipient, session = _pair(settings)
    prefs = EmailPreference.get_or_create_for_user(recipient)
    prefs.email_new_connections = False
    prefs.save()

    send_weekly_request(session, sender, recipient)

    assert mail.outbox == []
    assert Notification.objects.filter(user=recipient, notification_type=BELL_TYPE).count() == 1


@pytest.mark.django_db
def test_web_push_reaches_an_opted_in_subscription(settings):
    sender, recipient, session = _pair(settings)
    PushSubscription.objects.create(
        user=recipient,
        endpoint="https://push.example.test/sub",
        p256dh_key="p256dh",
        auth_key="auth",
    )

    with patch(
        "crush_lu.push_notifications.send_push_notification",
        return_value={"success": 1, "failed": 0, "total": 1},
    ) as mocked:
        weekly_request = send_weekly_request(session, sender, recipient)

    mocked.assert_called_once()
    kwargs = mocked.call_args.kwargs
    assert kwargs["user"] == recipient
    assert kwargs["preference_key"] == "new_connections"
    assert kwargs["tag"] == f"connect-week-request-{weekly_request.pk}"
    assert kwargs["url"].endswith(INBOX_PATH)
    assert sender.crushprofile.display_name in kwargs["body"]


@pytest.mark.django_db
def test_web_push_skips_a_subscription_that_opted_out(settings):
    sender, recipient, session = _pair(settings)
    PushSubscription.objects.create(
        user=recipient,
        endpoint="https://push.example.test/sub",
        p256dh_key="p256dh",
        auth_key="auth",
        notify_new_connections=False,
    )

    with patch("crush_lu.push_notifications.send_push_notification") as mocked:
        send_weekly_request(session, sender, recipient)

    mocked.assert_not_called()


@pytest.mark.django_db
def test_channels_render_in_the_recipients_language_not_the_senders(settings):
    sender, recipient, session = _pair(settings)
    recipient.crushprofile.preferred_language = "de"
    recipient.crushprofile.save(update_fields=["preferred_language"])

    # The sender posts from an English page; the recipient reads German.
    with translation.override("en"):
        send_weekly_request(session, sender, recipient)

    assert _bell(recipient).title == "Jemand möchte dich kennenlernen"
    assert "möchte dich kennenlernen" in mail.outbox[0].subject


@pytest.mark.django_db
def test_notification_failure_never_blocks_the_request(settings):
    sender, recipient, session = _pair(settings)

    with patch(
        "crush_lu.notification_service.NotificationService.notify",
        side_effect=RuntimeError("notification stack down"),
    ):
        weekly_request = send_weekly_request(session, sender, recipient)

    assert ConnectWeeklyRequest.objects.filter(
        pk=weekly_request.pk, status=ConnectWeeklyRequest.Status.PENDING
    ).exists()
