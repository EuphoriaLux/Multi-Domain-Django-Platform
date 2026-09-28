"""
Playwright: UX Wave 4 · WP11 "partials" (findings 3-10, 5-08, 5-09).

- Decline on a received request asks through the branded confirm sheet
  (never window.confirm), and the page's single status region announces it.
- A pre-screening auto-save on a locked submission swaps an "answers locked"
  section in place instead of failing with only an error toast.

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_ux_wave4_partials_playwright.py -n 0 --create-db
"""

from datetime import timedelta

import pytest
from playwright.sync_api import expect

from crush_lu.tests.test_account_exit_playwright import PHONE, _log_in, _member

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]


def _pending_request():
    from django.utils import timezone

    from crush_lu.models import EventConnection
    from crush_lu.models.events import EventRegistration, MeetupEvent

    me = _member("wp11.me@example.com", "Lena")
    requester = _member("wp11.req@example.com", "Marc")
    event = MeetupEvent.objects.create(
        title="Past Mixer",
        description="Past event",
        event_type="mixer",
        location="Luxembourg City",
        address="10 Grand Rue",
        date_time=timezone.now() - timedelta(days=1),
        registration_deadline=timezone.now() - timedelta(days=2),
        max_participants=10,
        is_published=True,
    )
    for user in (me, requester):
        EventRegistration.objects.create(event=event, user=user, status="attended")
    connection = EventConnection.objects.create(
        requester=requester, recipient=me, event=event, status="pending"
    )
    return me, connection


def test_decline_asks_through_confirm_sheet_and_announces_once(page, live_server):
    me, connection = _pending_request()
    native_dialogs = []
    page.on("dialog", lambda d: (native_dialogs.append(d.message), d.dismiss()))
    page.set_viewport_size(PHONE)
    _log_in(page, live_server.url, me)
    page.goto(f"{live_server.url}/en/connections/")

    sheet = page.locator("#crush-confirm-dialog")
    card = page.locator(f"#connection-{connection.id}")
    decline = card.get_by_role("button", name="Decline")

    decline.click()
    expect(sheet).to_be_visible()
    expect(sheet.locator("[data-confirm-message]")).to_have_text(
        "Are you sure you want to decline this connection request?"
    )
    expect(sheet.locator("[data-confirm-accept]")).to_have_text("Decline")
    sheet.locator("[data-confirm-cancel]").click()
    expect(sheet).to_be_hidden()
    connection.refresh_from_db()
    assert connection.status == "pending"

    decline.click()
    sheet.locator("[data-confirm-accept]").click()
    region = page.locator("#connection-status-live")
    expect(region).to_have_text("Connection request declined")
    expect(region).to_have_attribute("role", "status")
    expect(region).to_have_count(1)
    connection.refresh_from_db()
    assert connection.status == "declined"
    assert native_dialogs == []


def test_locked_pre_screening_section_swaps_in_place(page, live_server, settings):
    from crush_lu.models import CrushCoach, ProfileSubmission

    settings.PRE_SCREENING_ENABLED = True
    coach = CrushCoach.objects.create(
        user=_member("wp11.coach@example.com", "Nora"), is_active=True
    )
    me = _member("wp11.ps@example.com", "Ana")
    profile = me.crushprofile
    profile.is_approved = False
    profile.save()
    submission = ProfileSubmission.objects.create(
        profile=profile, coach=coach, status="pending"
    )

    page.set_viewport_size(PHONE)
    _log_in(page, live_server.url, me)
    page.goto(f"{live_server.url}/en/pre-screening/")
    section = page.locator("#prescreening-section-logistics")
    expect(section.locator("form")).to_have_count(1)

    # The coach finishes the call while the member still has the page open.
    submission.review_call_completed = True
    submission.save()

    section.locator("input[type=radio]").first.check()
    expect(section).to_contain_text("Answers locked")
    expect(section).to_contain_text(
        "Your Coach has already completed your screening call."
    )
    expect(section.locator("form")).to_have_count(0)
