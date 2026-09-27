"""
Playwright: UX Wave 2 follow-ups (#1052, #1053, #1058).

- The SumUp card page says a failed SDK load in the reader's language.

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_wave2_followups_playwright.py -n 0 --create-db
"""

import re
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


def test_spark_review_buttons_ask_their_own_question(page, live_server):
    """#1058: one form, two submit buttons, two different confirmations —
    the clicked button's data-confirm wins and its name/value is submitted."""
    from django.utils import timezone

    from crush_lu.models import CrushCoach, CrushSpark
    from crush_lu.models.events import EventRegistration, MeetupEvent

    coach_user = _member("spark.coach@example.com", "Nora")
    CrushCoach.objects.create(user=coach_user, is_active=True)
    sender = _member("spark.sender@example.com", "Sam")
    recipient = _member("spark.recipient@example.com", "Rae")
    event = MeetupEvent.objects.create(
        title="Spark Night",
        description="Past event",
        event_type="speed_dating",
        location="Luxembourg City",
        address="10 Grand Rue",
        date_time=timezone.now() - timedelta(days=1),
        registration_deadline=timezone.now() - timedelta(days=2),
        max_participants=10,
        is_published=True,
    )
    for user in (sender, recipient):
        EventRegistration.objects.create(event=event, user=user, status="attended")
    spark = CrushSpark.objects.create(
        event=event,
        sender=sender,
        recipient=recipient,
        status=CrushSpark.Status.PENDING_REVIEW,
    )

    page.set_viewport_size(PHONE)
    _log_in(page, live_server.url, coach_user)
    page.goto(f"{live_server.url}/en/coach/sparks/{spark.pk}/assign/")

    dialog = page.locator("#crush-confirm-dialog")
    message = dialog.locator("[data-confirm-message]")

    # Reject asks the reject question, as a danger sheet; Cancel changes nothing.
    page.get_by_role("button", name="Reject", exact=True).click()
    expect(dialog).to_be_visible()
    expect(message).to_have_text("Reject this spark? This cannot be undone.")
    expect(dialog).to_have_class(re.compile(r"\bconfirm-danger\b"))
    dialog.locator("[data-confirm-cancel]").click()
    expect(dialog).to_be_hidden()
    spark.refresh_from_db()
    assert spark.status == CrushSpark.Status.PENDING_REVIEW

    # Approve asks its own question, neutrally, and submits action=approve.
    page.get_by_role("button", name="Approve", exact=True).click()
    expect(message).to_have_text(
        "Approve this spark? The sender will be notified to create their journey."
    )
    expect(dialog).not_to_have_class(re.compile(r"\bconfirm-danger\b"))
    with page.expect_navigation():
        dialog.locator("[data-confirm-accept]").click()
    spark.refresh_from_db()
    assert spark.status == CrushSpark.Status.COACH_APPROVED


def test_gift_landing_footer_sign_in_is_clickable(page, live_server):
    """#1053: the giant decorative heart (.cta-section::before) sat on top of
    the footer's links and swallowed the tap on "Sign in"."""
    from datetime import date

    from crush_lu.models import JourneyGift

    sender = _member("gift.sender@example.com", "Sam")
    gift = JourneyGift.objects.create(
        sender=sender,
        recipient_name="Marie",
        date_first_met=date(2024, 2, 14),
        location_first_met="Luxembourg City",
        status=JourneyGift.Status.PENDING,
    )

    page.set_viewport_size(PHONE)
    page.context.add_cookies(
        [
            {
                "name": "cookie_consent",
                "value": '{"essential": true, "analytics": false, "marketing": false}',
                "url": live_server.url,
            }
        ]
    )
    page.goto(f"{live_server.url}/en/journey/gift/{gift.gift_code}/")

    sign_in = page.locator("footer.cta-section").get_by_role("link", name="Sign in")
    sign_in.scroll_into_view_if_needed()
    # A real (non-forced) click: Playwright refuses when another element
    # would receive the pointer event at the link's centre.
    sign_in.click(timeout=3000)
    page.wait_for_url(re.compile(r"/accounts/login/"))
