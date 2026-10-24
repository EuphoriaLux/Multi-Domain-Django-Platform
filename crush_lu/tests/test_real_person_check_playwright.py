"""Drive the real-person check page in a browser: the CSP Alpine checklist,
the hidden inputs it fills right before the form posts, and the HTMX log form.

Run with ``pytest -m playwright crush_lu/tests/test_real_person_check_playwright.py``.

Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md
"""

import re

import pytest
from django.core.cache import cache
from django.test import Client
from playwright.sync_api import expect

from crush_lu.models import (
    CallAttempt,
    ConnectRealPersonBadge,
    ConnectRealPersonCheck,
    CrushProfile,
    ProfilePhotoReviewState,
    UserReport,
)
from crush_lu.real_person_check_rules import REQUIRED_CHECKLIST_KEYS
from crush_lu.services.real_person_check import claim_check
from crush_lu.tests.real_person_check_helpers import (
    make_coach,
    make_member,
    profile_of,
)
from crush_lu.tests.test_crush_connect import _grant_consent

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

VOICE_KEYS = set(REQUIRED_CHECKLIST_KEYS["voice"])
VIDEO_KEYS = set(REQUIRED_CHECKLIST_KEYS["video"])


def _skip_photo_requests(page):
    """The test members have no files in storage. Their photo requests would
    only 404, and on the in-memory test database they race the form posts."""
    page.route("**/media/profile/**", lambda route: route.abort())


@pytest.fixture
def check_page(page, live_server, settings):
    """A coach's browser on the check page of a freshly claimed member."""
    cache.clear()
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = True
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = True
    url = live_server.url.replace("localhost", "crush.localhost")
    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False
    settings.CSRF_TRUSTED_ORIGINS = [url]
    coach = make_coach("rpcpw_coach")
    _grant_consent(coach.user)
    member = make_member("rpcpw_member")
    CrushProfile.objects.filter(user=member).update(
        phone_number="+352 621 000 321", phone_verified=True
    )
    check = claim_check(coach, profile_of(member).pk)
    client = Client()
    client.force_login(coach.user)
    page.context.add_cookies(
        [{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": url}]
    )
    page.set_viewport_size({"width": 390, "height": 844})
    page.set_default_timeout(10000)
    _skip_photo_requests(page)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{url}/en/coach/real-person-checks/{check.pk}/")
    page.locator("#cookie-btn-decline").click()
    return {
        "page": page,
        "url": url,
        "check": check,
        "member": member,
        "coach": coach,
        "errors": errors,
    }


def _tick(page, keys):
    for key in keys:
        page.locator(f'input[data-key="{key}"]').check()


def test_video_pass_needs_every_item_then_a_confirmation(check_page):
    page, check = check_page["page"], check_page["check"]
    root = page.locator('[x-data="realPersonCheckChecklist"]')
    grant = root.get_by_role("button", name="Grant the video badge")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

    # Nothing is open until the coach says how they spoke.
    expect(root.locator('input[data-key="spoke_live"]')).to_be_hidden()
    root.get_by_role("button", name="Video call").click()
    expect(root.get_by_role("button", name="Video call")).to_have_attribute(
        "aria-pressed", "true"
    )
    expect(root.locator("input[data-key]:visible")).to_have_count(6)
    expect(grant).to_be_disabled()

    # Five of six is not enough.
    _tick(page, sorted(VIDEO_KEYS - {"live_camera"}))
    expect(grant).to_be_disabled()
    _tick(page, ["live_camera"])
    expect(grant).to_be_enabled()

    # The confirmation sits between the click and the post.
    grant.click()
    confirm_panel = root.get_by_role("alertdialog")
    expect(confirm_panel).to_be_visible()
    confirm_panel.get_by_role("button", name="Cancel").click()
    expect(confirm_panel).to_be_hidden()
    check.refresh_from_db()
    assert check.status == "open"

    grant.click()
    confirm_panel.get_by_role("button", name="Yes, confirm").click()
    page.wait_for_url(f"{check_page['url']}/en/coach/real-person-checks/")
    check.refresh_from_db()
    assert (check.status, check.medium) == ("passed", "video")
    assert check.checklist == {key: True for key in VIDEO_KEYS}
    assert check.photo_key == profile_of(check_page["member"]).photo_1.name
    assert ConnectRealPersonBadge.objects.filter(profile__user=check_page["member"])
    assert check_page["errors"] == []


def test_voice_pass_posts_only_the_voice_keys(check_page):
    page, check = check_page["page"], check_page["check"]
    root = page.locator('[x-data="realPersonCheckChecklist"]')
    # Tick every video item, then switch to voice: the extra ticks must not post.
    root.get_by_role("button", name="Video call").click()
    _tick(page, sorted(VIDEO_KEYS))
    root.get_by_role("button", name="Voice call").click()
    expect(root.locator("input[data-key]:visible")).to_have_count(4)
    grant = root.get_by_role("button", name="Grant the voice badge")
    expect(grant).to_be_enabled()
    grant.click()
    root.get_by_role("alertdialog").get_by_role("button", name="Yes, confirm").click()
    page.wait_for_url(f"{check_page['url']}/en/coach/real-person-checks/")
    check.refresh_from_db()
    assert (check.status, check.medium) == ("passed", "voice")
    assert check.checklist == {key: True for key in VOICE_KEYS}
    assert check.photo_key == ""
    assert check_page["errors"] == []


def test_changing_the_medium_withdraws_a_pending_confirmation(check_page):
    """The confirmation was opened for one outcome. Switching medium while it
    is open used to post the old outcome with the new medium's ticks: a voice
    badge on a screen that said Video, or a 400 and every tick lost."""
    page, check = check_page["page"], check_page["check"]
    root = page.locator('[x-data="realPersonCheckChecklist"]')
    panel = root.get_by_role("alertdialog")
    root.get_by_role("button", name="Voice call").click()
    _tick(page, sorted(VOICE_KEYS))
    root.get_by_role("button", name="Grant the voice badge").click()
    expect(panel).to_be_visible()

    root.get_by_role("button", name="Video call").click()
    expect(panel).to_be_hidden()
    check.refresh_from_db()
    assert check.status == "open"
    # The two video-only items are still unticked, so the video grant waits.
    grant = root.get_by_role("button", name="Grant the video badge")
    expect(grant).to_be_disabled()
    _tick(page, sorted(VIDEO_KEYS - VOICE_KEYS))
    grant.click()
    expect(panel).to_be_visible()
    panel.get_by_role("button", name="Yes, confirm").click()
    page.wait_for_url(f"{check_page['url']}/en/coach/real-person-checks/")
    check.refresh_from_db()
    assert (check.status, check.medium) == ("passed", "video")
    assert check.checklist == {key: True for key in VIDEO_KEYS}
    assert check_page["errors"] == []


def test_unticking_while_confirming_withdraws_the_confirmation(check_page):
    page, check = check_page["page"], check_page["check"]
    root = page.locator('[x-data="realPersonCheckChecklist"]')
    panel = root.get_by_role("alertdialog")
    root.get_by_role("button", name="Voice call").click()
    _tick(page, sorted(VOICE_KEYS))
    root.get_by_role("button", name="Grant the voice badge").click()
    expect(panel).to_be_visible()
    page.locator('input[data-key="name_matches"]').uncheck()
    expect(panel).to_be_hidden()
    expect(root.get_by_role("button", name="Grant the voice badge")).to_be_disabled()
    check.refresh_from_db()
    assert check.status == "open"
    assert check_page["errors"] == []


def test_the_confirmation_comes_into_view_and_takes_focus(check_page):
    """At phone width the panel opens a screen below the button that asked."""
    page = check_page["page"]
    root = page.locator('[x-data="realPersonCheckChecklist"]')
    panel = root.get_by_role("alertdialog")
    yes = panel.get_by_role("button", name="Yes, confirm")
    root.get_by_role("button", name="Video call").click()
    _tick(page, sorted(VIDEO_KEYS))
    root.get_by_role("button", name="Grant the video badge").click()
    expect(panel).to_be_in_viewport()
    expect(yes).to_be_focused()
    expect(
        root.get_by_role("alertdialog", name=re.compile("Confirm that you spoke"))
    ).to_be_visible()
    expect(panel).to_have_attribute("aria-labelledby", "rpc-confirm-pass")
    panel.get_by_role("button", name="Cancel").click()

    # "Looks fake" names the dialog by its own question.
    root.get_by_role("button", name="Looks fake").click()
    expect(panel).to_be_in_viewport()
    expect(yes).to_be_focused()
    expect(panel).to_have_attribute("aria-labelledby", "rpc-confirm-fake")
    expect(
        root.get_by_role("alertdialog", name=re.compile("suspected fake"))
    ).to_be_visible()
    assert check_page["errors"] == []


def test_declined_and_unreachable_post_straight_away(check_page):
    page, check = check_page["page"], check_page["check"]
    root = page.locator('[x-data="realPersonCheckChecklist"]')
    root.locator("#rpc-failure-reason-decide").select_option("voicemail")
    root.get_by_role("button", name="Could not reach them").click()
    page.wait_for_url(f"{check_page['url']}/en/coach/real-person-checks/")
    check.refresh_from_db()
    assert check.status == "unreachable"
    attempt = CallAttempt.objects.get(real_person_check=check)
    assert (attempt.result, attempt.failure_reason) == ("failed", "voicemail")
    assert check_page["errors"] == []


def test_looks_fake_asks_first_and_then_opens_a_report(check_page):
    page, check = check_page["page"], check_page["check"]
    root = page.locator('[x-data="realPersonCheckChecklist"]')
    root.get_by_role("button", name="Looks fake").click()
    confirm_panel = root.get_by_role("alertdialog")
    expect(confirm_panel).to_be_visible()
    expect(confirm_panel).to_contain_text("suspected fake")
    confirm_panel.get_by_role("button", name="Cancel").click()
    assert not UserReport.objects.exists()
    root.get_by_role("button", name="Looks fake").click()
    confirm_panel.get_by_role("button", name="Yes, confirm").click()
    page.wait_for_url(f"{check_page['url']}/en/coach/real-person-checks/")
    check.refresh_from_db()
    assert check.status == "suspected_fake"
    report = UserReport.objects.get()
    assert report.details == f"Real-person check #{check.pk}"
    assert check_page["errors"] == []


def test_logging_a_text_swaps_the_history_without_leaving_the_page(check_page):
    page, check = check_page["page"], check_page["check"]
    page.get_by_role("button", name="I sent an SMS").click()
    expect(page.locator("#rpc-outreach")).to_contain_text("SMS template sent")
    expect(page.locator("#rpc-outreach")).to_contain_text("Contact attempt logged.")
    assert page.url.endswith(f"/en/coach/real-person-checks/{check.pk}/")
    assert (
        CallAttempt.objects.filter(real_person_check=check, result="sms_sent").count()
        == 1
    )
    # The decision component survived the swap next to it.
    expect(page.locator('[x-data="realPersonCheckChecklist"]')).to_be_visible()
    assert check_page["errors"] == []


def test_video_is_disabled_while_the_photo_is_not_public(page, live_server, settings):
    cache.clear()
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = True
    url = live_server.url.replace("localhost", "crush.localhost")
    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False
    settings.CSRF_TRUSTED_ORIGINS = [url]
    coach = make_coach("rpcpw_coach2")
    _grant_consent(coach.user)
    member = make_member("rpcpw_member2")
    check = claim_check(coach, profile_of(member).pk)
    ProfilePhotoReviewState.objects.filter(profile__user=member).update(
        status="pending"
    )
    client = Client()
    client.force_login(coach.user)
    page.context.add_cookies(
        [{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": url}]
    )
    page.set_default_timeout(10000)
    _skip_photo_requests(page)
    page.goto(f"{url}/en/coach/real-person-checks/{check.pk}/")
    root = page.locator('[x-data="realPersonCheckChecklist"]')
    expect(root.get_by_role("button", name="Video call")).to_be_disabled()
    expect(root.get_by_text("Video is unavailable")).to_be_visible()
    # A voice check is still possible.
    root.get_by_role("button", name="Voice call").click()
    expect(root.locator("input[data-key]:visible")).to_have_count(4)
    assert ConnectRealPersonCheck.objects.get(pk=check.pk).status == "open"
