"""
Playwright: a failed SumUp checkout shows a toast, not alert(), and the
button re-enables (UX Wave 3 · WP8, finding 4-05).

Before this, event_detail.html's "Pay with Card" button had no loading
state (a double tap opened two checkouts) and a failure showed a browser
alert() -- untranslated for DE/FR, and inside the iOS/Android WebView an
alert() reads like a scam dialog rather than part of the app.

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_ux_wave3_pay_confirm_playwright.py -n 0 --create-db
"""

import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from playwright.sync_api import expect

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

PHONE = {"width": 390, "height": 844}


@pytest.fixture
def verified_member(transactional_db):
    from allauth.account.models import EmailAddress
    from django.contrib.auth import get_user_model

    from crush_lu.models import CrushProfile, UserDataConsent

    user = get_user_model().objects.create_user(
        username="payconfirm.pw@example.com",
        email="payconfirm.pw@example.com",
        password="Toast-pass-2026!",
        first_name="Nora",
        last_name="Weber",
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
        verification_status="verified",
        completion_status="step4",
    )
    return user


@pytest.fixture
def unpaid_event_registration(transactional_db, verified_member):
    from crush_lu.models.events import EventRegistration, MeetupEvent

    event = MeetupEvent.objects.create(
        title="Pay Confirm Playwright Event",
        description="WP8 Playwright regression event",
        event_type="speed_dating",
        location="Luxembourg City",
        address="10 Grand Rue",
        date_time=timezone.now() + timedelta(days=2),
        registration_deadline=timezone.now() + timedelta(days=1),
        registration_fee=Decimal("15.00"),
        is_published=True,
    )
    registration = EventRegistration.objects.create(
        user=verified_member,
        event=event,
        status="confirmed",
        payment_confirmed=False,
    )
    return event, registration


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
            {"name": "cookie_consent", "value": consent, "url": live_server_url},
        ]
    )


def test_failed_checkout_shows_toast_not_alert_and_reenables_button(
    page, live_server, verified_member, unpaid_event_registration
):
    event, registration = unpaid_event_registration

    page.set_viewport_size(PHONE)
    _log_in(page, live_server.url, verified_member)

    # A native alert() would block the page and this test; failing loudly
    # here is the whole point of the regression check.
    dialogs = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))

    page.goto(f"{live_server.url}/en/events/{event.id}/")

    pay_button = page.locator(
        f'button[data-sumup-reg-id="{registration.id}"][data-payment-method="card"]'
    )
    expect(pay_button).to_be_visible()
    expect(pay_button).to_be_enabled()

    def fail_checkout(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"success": False, "error": "Card declined."}),
        )

    page.route(
        f"**/payments/sumup/create-event-checkout/{registration.id}/", fail_checkout
    )

    pay_button.click()

    toasts = page.locator("#toast-container [role=alert]")
    expect(toasts).to_have_count(1)
    expect(toasts.first).to_contain_text("Card declined.")

    # The button re-enables instead of staying stuck mid-checkout, and no
    # alert() fired.
    expect(pay_button).to_be_enabled()
    assert dialogs == []
