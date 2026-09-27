"""
Playwright: UX Wave 2 follow-ups (#1052, #1053, #1058).

- The SumUp card page says a failed SDK load in the reader's language.

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_wave2_followups_playwright.py -n 0 --create-db
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from playwright.sync_api import expect

from crush_lu.tests.test_account_exit_playwright import PHONE, _log_in, _member

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]


def _pending_checkout(user, checkout_id):
    from django.utils import timezone

    from crush_lu.models.events import EventRegistration, MeetupEvent
    from crush_lu.models.payments import PaymentTransaction

    event = MeetupEvent.objects.create(
        title="Speed Dating Luxembourg",
        description="A paid evening",
        event_type="speed_dating",
        location="Luxembourg City",
        address="10 Grand Rue",
        date_time=timezone.now() + timedelta(days=5),
        registration_deadline=timezone.now() + timedelta(days=4),
        registration_fee=Decimal("15.50"),
        max_participants=10,
        is_published=True,
    )
    registration = EventRegistration.objects.create(
        event=event, user=user, status="pending"
    )
    PaymentTransaction.objects.create(
        transaction_reference=f"CRUSH-EVT-{checkout_id}",
        provider=PaymentTransaction.Provider.SUMUP,
        sumup_checkout_id=checkout_id,
        amount=Decimal("15.50"),
        currency="EUR",
        status=PaymentTransaction.Status.PENDING,
        purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
        user=user,
        event_registration=registration,
    )


def test_sumup_sdk_load_failure_is_translated(page, live_server):
    """#1052: the fallback used to be hard-coded English innerHTML."""
    me = _member("sumup.fail@example.com", "Lena")
    _pending_checkout(me, "CHK_SDK_FAIL")

    page.set_viewport_size(PHONE)
    _log_in(page, live_server.url, me)
    page.route("https://gateway.sumup.com/**", lambda route: route.abort())
    page.set_extra_http_headers({"Accept-Language": "de"})
    page.goto(
        f"{live_server.url}/payments/sumup/widget/CHK_SDK_FAIL/",
        wait_until="domcontentloaded",
    )

    loading = page.locator("#sumup-loading")
    expect(loading).to_contain_text("Das Zahlungsmodul konnte nicht geladen werden.")
    expect(loading).not_to_contain_text("Failed to load payment module")
