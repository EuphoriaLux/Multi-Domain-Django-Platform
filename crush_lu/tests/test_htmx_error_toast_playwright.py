"""
Playwright: a failed HTMX event-registration submit recovers and says so
(UX review finding 4-04).

Before the global handler (crush_lu/static/crush_lu/js/htmx-error-toast.js),
a 5xx or a dropped connection on the registration hx-post left the button
disabled on "Processing..." for good and told the member nothing. The page is
the German one on purpose: the toast copy is rendered server-side in the page
language, so this also proves the script shows the translated text.

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_htmx_error_toast_playwright.py -n 0 --create-db
"""

import json
from datetime import date, timedelta

import pytest
from django.utils import timezone
from playwright.sync_api import expect

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

SERVER_ERROR_DE = "Ein Fehler ist aufgetreten. Bitte versuche es erneut."
NETWORK_ERROR_DE = (
    "Netzwerkfehler. Bitte überprüfe deine Verbindung und versuche es erneut."
)


@pytest.fixture
def verified_member(transactional_db):
    """A member who may register: verified email, Crush consent, verified profile."""
    from allauth.account.models import EmailAddress
    from django.contrib.auth import get_user_model

    from crush_lu.models import CrushProfile, UserDataConsent

    user = get_user_model().objects.create_user(
        username="toast.member@example.com",
        email="toast.member@example.com",
        password="Toast-pass-2026!",
        first_name="Lena",
        last_name="Schmit",
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
        phone_number="+352621123456",
        phone_verified=True,
        is_approved=True,
        verification_status="verified",
        is_active=True,
        preferred_language="de",
    )
    return user


@pytest.fixture
def upcoming_event(transactional_db):
    from crush_lu.models import MeetupEvent

    return MeetupEvent.objects.create(
        title="Toast Wine Mixer",
        description="An evening mixer",
        event_type="mixer",
        date_time=timezone.now() + timedelta(days=10),
        location="Luxembourg City",
        address="1 Rue de la Gare, Luxembourg",
        max_participants=30,
        registration_deadline=timezone.now() + timedelta(days=8),
        registration_fee=0,
        is_published=True,
        profile_requirement="approved",
    )


def _log_in(page, live_server_url, user):
    """Reuse a Django session instead of driving the login form."""
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
            # Keep the cookie banner from covering the submit button.
            {"name": "cookie_consent", "value": consent, "url": live_server_url},
        ]
    )


def test_failed_registration_submit_reenables_button_and_shows_toast(
    page, live_server, verified_member, upcoming_event
):
    from crush_lu.models import EventRegistration

    _log_in(page, live_server.url, verified_member)
    page.goto(f"{live_server.url}/de/events/{upcoming_event.id}/register/")

    submit = page.locator("#registration-form-container button[type=submit]")
    resting_label = submit.locator('[x-show="showSubmitText"]')
    processing_label = submit.locator('[x-show="showProcessingText"]')
    toasts = page.locator("#toast-container [role=alert]")
    expect(submit).to_be_enabled()
    expect(processing_label).to_be_hidden()

    failure = {"mode": "500"}
    posts = []

    def fail_post(route):
        if route.request.method != "POST":
            return route.continue_()
        posts.append(failure["mode"])
        if failure["mode"] == "network":
            return route.abort("internetdisconnected")
        return route.fulfill(status=500, content_type="text/html", body="boom")

    page.route(f"**/events/{upcoming_event.id}/register/", fail_post)

    # 1. Server error: htmx:responseError.
    submit.click()
    expect(toasts).to_have_count(1)
    expect(toasts.first).to_be_visible()
    expect(toasts.first).to_contain_text(SERVER_ERROR_DE)
    expect(submit).to_be_enabled()
    expect(processing_label).to_be_hidden()
    expect(resting_label).to_be_visible()

    # 2. The member retries and the connection drops: htmx:sendError.
    failure["mode"] = "network"
    submit.click()
    network_toast = toasts.filter(has_text=NETWORK_ERROR_DE)
    expect(network_toast).to_have_count(1)
    expect(network_toast).to_be_visible()
    expect(submit).to_be_enabled()
    expect(processing_label).to_be_hidden()
    expect(resting_label).to_be_visible()

    # Both clicks really went out as HTMX posts (the button was not simply
    # dead), and neither registered anyone.
    assert posts == ["500", "network"]
    assert not EventRegistration.objects.filter(event=upcoming_event).exists()
