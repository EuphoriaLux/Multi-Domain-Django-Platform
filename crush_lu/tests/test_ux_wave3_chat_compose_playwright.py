"""
Playwright: chat compose behaviour changed for finding 5-15.

- The first message's HTMX response removes the "No messages yet" placeholder
  via an out-of-band delete (`hx-swap-oob="delete"` on `#chat-empty`) — this
  only proves out browser-side, since it depends on htmx actually applying
  the OOB swap.
- The compose form resets after a successful send via connection-chat.js
  listening for the `connection-message-sent` HX-Trigger, not an inline
  `hx-on::after-request` (CSP-unsafe once SECURE_CSP is enforced — see
  AGENTS.md and connection-chat.js's own docstring).
- A failed send (message too long) retargets an inline error near the
  textarea instead of the whole redirected page landing in the thread, and
  must not clear what the member typed.

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_ux_wave3_chat_compose_playwright.py -n 0 --create-db
"""

import json
from datetime import date, timedelta

import pytest
from django.test import Client
from django.utils import timezone
from playwright.sync_api import expect

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

PHONE = {"width": 390, "height": 844}


def _log_in(page, live_server_url, user):
    client = Client()
    client.force_login(user)
    consent = json.dumps({"essential": True, "analytics": False, "marketing": False})
    page.context.add_cookies(
        [
            {
                "name": "sessionid",
                "value": client.cookies["sessionid"].value,
                "url": live_server_url,
            },
            {"name": "cookie_consent", "value": consent, "url": live_server_url},
        ]
    )


@pytest.fixture
def chat_pair(transactional_db):
    from crush_lu.models import (
        CrushProfile,
        EventConnection,
        MeetupEvent,
        UserDataConsent,
    )

    from django.contrib.auth import get_user_model

    User = get_user_model()
    sender = User.objects.create_user(
        username="pw-chat-sender@example.com",
        email="pw-chat-sender@example.com",
        password="Wp10-pass-2026!",
    )
    other = User.objects.create_user(
        username="pw-chat-other@example.com",
        email="pw-chat-other@example.com",
        password="Wp10-pass-2026!",
    )
    for user, gender in [(sender, "M"), (other, "F")]:
        UserDataConsent.objects.update_or_create(
            user=user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 5, 15),
            gender=gender,
            location="Luxembourg City",
            is_approved=True,
            verification_status="verified",
            is_active=True,
        )
    event = MeetupEvent.objects.create(
        title="PW Chat Event",
        description="desc",
        event_type="mixer",
        date_time=timezone.now() - timedelta(days=1),
        location="Luxembourg",
        address="1 Rue de la Gare",
        max_participants=20,
        registration_deadline=timezone.now() - timedelta(days=3),
        is_published=True,
    )
    connection = EventConnection.objects.create(
        event=event,
        requester=sender,
        recipient=other,
        status="accepted",
        responded_at=timezone.now(),
    )
    return sender, other, connection


def test_first_message_clears_placeholder_and_resets_form(page, live_server, chat_pair):
    sender, other, connection = chat_pair
    page.set_viewport_size(PHONE)
    _log_in(page, live_server.url, sender)
    page.goto(f"{live_server.url}/en/connections/{connection.id}/")

    empty_placeholder = page.locator("#chat-empty")
    expect(empty_placeholder).to_be_visible()

    textarea = page.locator("#connection-compose-form textarea")
    textarea.fill("Hello there, excited to meet you!")
    page.locator("#connection-compose-form button[type=submit]").click()

    # The out-of-band delete removes #chat-empty entirely.
    expect(page.locator("#chat-empty")).to_have_count(0)
    # connection-chat.js reset the form on the HX-Trigger, not eval.
    expect(textarea).to_have_value("")
    expect(page.locator("#messages-container")).to_contain_text(
        "Hello there, excited to meet you!"
    )


def test_failed_send_shows_inline_error_and_keeps_draft(page, live_server, chat_pair):
    sender, other, connection = chat_pair
    page.set_viewport_size(PHONE)
    _log_in(page, live_server.url, sender)
    page.goto(f"{live_server.url}/en/connections/{connection.id}/")

    textarea = page.locator("#connection-compose-form textarea")
    too_long = "x" * 501
    # Bypass any client-side maxlength so the server's validation path (the
    # one under test) actually runs.
    textarea.evaluate(
        "(el, v) => { el.removeAttribute('maxlength'); el.value = v; }", too_long
    )

    page.locator("#connection-compose-form button[type=submit]").click()

    error_box = page.locator("#chat-compose-error")
    expect(error_box).to_contain_text("valid message")
    # The draft must survive a failed send.
    expect(textarea).to_have_value(too_long)
