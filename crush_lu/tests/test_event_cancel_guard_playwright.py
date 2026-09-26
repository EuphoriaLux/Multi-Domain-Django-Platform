"""
Playwright: the event-cancel form cannot be posted twice (UX Wave 2 · 4-06).

The page used to guard double taps with an inline ``onsubmit`` handler, which
the nonce-based CSP blocks, so nothing stopped a second POST. The guard is now
the ``eventCancelForm`` Alpine component (makeConfirm via mixin).

The first POST is answered with 204 No Content, which keeps the browser on the
page as if the server were still busy, so a second submit is observable.

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_event_cancel_guard_playwright.py -n 0 --create-db
"""

import json
from datetime import date, timedelta

import pytest
from django.utils import timezone
from playwright.sync_api import expect

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

PHONE = {"width": 390, "height": 844}


@pytest.fixture
def member_with_seat(transactional_db):
    from allauth.account.models import EmailAddress
    from django.contrib.auth import get_user_model

    from crush_lu.models import (
        CrushProfile,
        EventRegistration,
        MeetupEvent,
        UserDataConsent,
    )

    user = get_user_model().objects.create_user(
        username="cancel.guard@example.com",
        email="cancel.guard@example.com",
        password="Guard-pass-2026!",
        first_name="Mia",
    )
    EmailAddress.objects.create(
        user=user, email=user.email, verified=True, primary=True
    )
    UserDataConsent.objects.update_or_create(
        user=user,
        defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
    )
    CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1995, 5, 15),
        gender="F",
        location="Luxembourg City",
        is_approved=True,
        verification_status="verified",
        is_active=True,
    )
    event = MeetupEvent.objects.create(
        title="Guard Wine Mixer",
        description="An evening mixer",
        event_type="mixer",
        date_time=timezone.now() + timedelta(days=10),
        location="Luxembourg City",
        address="1 Rue de la Gare, Luxembourg",
        max_participants=30,
        registration_deadline=timezone.now() + timedelta(days=8),
        registration_fee=0,
        is_published=True,
    )
    EventRegistration.objects.create(event=event, user=user, status="confirmed")
    return user, event


def _log_in(page, live_server_url, user):
    from django.test import Client

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


def test_cancel_form_posts_only_once(page, live_server, member_with_seat):
    user, event = member_with_seat
    page.set_viewport_size(PHONE)
    _log_in(page, live_server.url, user)
    page.goto(f"{live_server.url}/en/events/{event.id}/cancel/")

    posts = []

    def hold_post(route):
        if route.request.method == "POST":
            posts.append(route.request.url)
            # 204 keeps the browser on this page, like a slow server would.
            route.fulfill(status=204, body="")
        else:
            route.continue_()

    page.route(f"**/events/{event.id}/cancel/", hold_post)

    form = page.locator("form", has=page.get_by_text("Yes, Cancel Registration"))
    submit = form.locator("button[type=submit]")
    expect(submit).to_be_enabled()
    expect(submit.get_by_text("Cancelling...")).to_be_hidden()

    submit.click()
    for _ in range(50):
        if posts:
            break
        page.wait_for_timeout(100)
    assert len(posts) == 1

    expect(submit).to_be_disabled()
    expect(submit.get_by_text("Cancelling...")).to_be_visible()

    # A second submit (a double tap that got through, Enter, a script) is
    # swallowed by the guard rather than posted again.
    form.evaluate("f => f.requestSubmit()")
    page.wait_for_timeout(500)
    assert len(posts) == 1
