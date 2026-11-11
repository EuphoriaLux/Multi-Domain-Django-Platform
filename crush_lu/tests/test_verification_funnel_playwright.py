"""Member request, familiar coach channel and recovery on a real mobile browser.

Media transport is not exercised; two-device video remains a release check.
"""

from datetime import timedelta
from pathlib import Path

import pytest
from django.core.cache import cache
from django.test import Client
from django.utils import timezone
from playwright.sync_api import expect

from crush_lu.models import CrushProfile, ProfileSubmission
from crush_lu.services.real_person_check import claim_check
from crush_lu.services.verification_funnel import (
    claim_video_request,
    request_verification,
)
from crush_lu.services.connect_verification import has_valid_video_admission
from crush_lu.real_person_check_rules import REQUIRED_CHECKLIST_KEYS
from crush_lu.tests.real_person_check_helpers import make_coach, make_member, profile_of
from crush_lu.tests.test_crush_connect import _grant_consent

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]


def login(context, url, user):
    client = Client()
    client.force_login(user)
    context.add_cookies(
        [{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": url}]
    )


def test_coach_workspace_lists_all_assignments_and_unifies_navigation(
    page, live_server, settings
):
    cache.clear()
    settings.DEBUG = True
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = True
    settings.CONNECT_VIDEO_VERIFICATION_MODE = "public"
    settings.CONNECT_WEEK_HUMAN_VERIFICATION_REQUIRED = True
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    settings.CRUSH_CONNECT_LAUNCHED = True
    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False
    url = live_server.url.replace("localhost", "crush.localhost")
    settings.CSRF_TRUSTED_ORIGINS = [url]
    coach = make_coach("workspace_browser_coach")
    _grant_consent(coach.user)
    legacy = make_member("legacy_browser_member")
    CrushProfile.objects.filter(user=legacy).update(
        is_approved=False, verification_status="pending", verification_method=""
    )
    submission = ProfileSubmission.objects.create(
        profile=profile_of(legacy), coach=coach, status="pending"
    )
    badge = claim_check(coach, profile_of(make_member("badge_browser_member")).pk)
    video_member = make_member("video_workspace_member")
    CrushProfile.objects.filter(user=video_member).update(approved_at=timezone.now())
    claim_video_request(coach, request_verification(video_member).pk)
    make_member("outreach_browser_member")
    login(page.context, url, coach.user)
    page.set_viewport_size({"width": 390, "height": 844})
    page.emulate_media(reduced_motion="reduce")
    page.route("**/media/profile/**", lambda route: route.abort())
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{url}/en/coach/channel/")
    page.locator("#cookie-btn-decline").click()
    assigned = page.locator("#assigned-work")
    expect(assigned.get_by_role("heading", name="In your queue (3)")).to_be_visible()
    expect(assigned.locator("[data-assigned-kind]")).to_have_count(3)
    expect(page.locator("nav.bottom-nav")).to_have_attribute("data-screening-count", "3")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    output = Path("screenshots/verification-workspace")
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output / "coach-channel-mobile.png"), full_page=True)
    assigned.locator('[data-assigned-kind="submission"]').get_by_role("link").click()
    page.wait_for_url(f"{url}/en/coach/review/{submission.pk}/")
    expect(page.locator("#verification-purpose-heading")).to_have_text("Profile review")
    page.get_by_role("main").get_by_role(
        "link", name="Verification Channel", exact=True
    ).click()
    assigned.locator('[data-assigned-kind="badge"]').get_by_role("link").click()
    page.wait_for_url(f"{url}/en/coach/real-person-checks/{badge.pk}/")
    expect(page.locator("#verification-purpose-heading")).to_have_text(
        "Optional real-person badge"
    )
    page.get_by_role("main").get_by_role(
        "link", name="Verification Channel", exact=True
    ).click()
    page.get_by_role("link", name="Optional badges", exact=True).click()
    expect(page.get_by_role("link", name="Optional badges")).to_have_attribute(
        "aria-current", "page"
    )
    expect(page.get_by_text("Optional badge outreach", exact=True)).to_be_visible()
    expect(assigned.locator("[data-assigned-kind]")).to_have_count(3)
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.locator("html").evaluate("el => el.classList.add('dark')")
    page.screenshot(path=str(output / "coach-badges-desktop-dark.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors


def test_mobile_member_request_coach_appointment_and_retry(
    page, browser, live_server, settings
):
    cache.clear()
    settings.DEBUG = True
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = True
    settings.CONNECT_VIDEO_VERIFICATION_MODE = "public"
    settings.CONNECT_WEEK_HUMAN_VERIFICATION_REQUIRED = True
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    settings.CRUSH_CONNECT_LAUNCHED = True
    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False
    url = live_server.url.replace("localhost", "crush.localhost")
    settings.CSRF_TRUSTED_ORIGINS = [url]
    member = make_member("video_browser_member")
    CrushProfile.objects.filter(user=member).update(approved_at=timezone.now())
    coach = make_coach("video_browser_coach")
    _grant_consent(coach.user)
    page.set_viewport_size({"width": 390, "height": 844})
    page.emulate_media(reduced_motion="reduce")
    page.set_default_timeout(15000)
    page.route("**/media/profile/**", lambda route: route.abort())
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    login(page.context, url, member)
    page.goto(f"{url}/en/crush-connect/verification/")
    page.locator("#cookie-btn-decline").click()
    expect(
        page.get_by_role("button", name="Request a video verification")
    ).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    output = Path("screenshots/verification-funnel")
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output / "member-choice-mobile.png"), full_page=True)
    page.get_by_role("button", name="Request a video verification").click()
    expect(page.locator('[data-verification-state="waiting"]')).to_be_visible()

    context = browser.new_context(
        viewport={"width": 390, "height": 844}, reduced_motion="reduce"
    )
    try:
        login(context, url, coach.user)
        coach_page = context.new_page()
        coach_page.set_default_timeout(15000)
        coach_page.route("**/media/profile/**", lambda route: route.abort())
        coach_page.on("pageerror", lambda error: errors.append(str(error)))
        coach_page.goto(f"{url}/en/coach/channel/")
        coach_page.locator("#cookie-btn-decline").click()
        coach_page.get_by_role("button", name="Claim video request").click()
        expect(
            coach_page.get_by_role("button", name="Save agreed appointment")
        ).to_be_visible()
        appointment = timezone.localtime(timezone.now() + timedelta(minutes=5))
        coach_page.get_by_label("Agreed appointment time").fill(
            appointment.strftime("%Y-%m-%dT%H:%M")
        )
        coach_page.get_by_role("button", name="Save agreed appointment").click()
        expect(
            coach_page.get_by_role("button", name="Cancel appointment", exact=True)
        ).to_be_visible()
        page.reload()
        expect(page.locator('[data-verification-state="arranged"]')).to_be_visible()
        expect(page.get_by_role("link", name="Join video verification")).to_be_visible()
        page.screenshot(
            path=str(output / "member-appointment-mobile.png"), full_page=True
        )
        assert not has_valid_video_admission(member)
        # Camera/network failure is a retry, never a voice approval.
        expect(
            coach_page.get_by_role("button", name="Voice call", exact=True)
        ).to_have_count(0)
        coach_page.get_by_role("button", name="Could not reach them").click()
        coach_page.wait_for_url(f"{url}/en/coach/channel/")
        page.reload()
        expect(page.locator('[data-verification-state="retry"]')).to_be_visible()
        expect(
            page.get_by_role("button", name="Request a video verification")
        ).to_be_visible()
        assert not has_valid_video_admission(member)
        page.get_by_role("button", name="Request a video verification").click()
        coach_page.reload()
        coach_page.get_by_role("button", name="Claim video request").click()
        coach_page.get_by_label("Agreed appointment time").fill(
            appointment.strftime("%Y-%m-%dT%H:%M")
        )
        coach_page.get_by_role("button", name="Save agreed appointment").click()
        decision = coach_page.locator('[x-data="realPersonCheckChecklist"]')
        decision.get_by_role("button", name="Video call", exact=True).click()
        approve = decision.get_by_role(
            "button", name="Approve Connect Week video verification"
        )
        expect(approve).to_be_disabled()
        for key in REQUIRED_CHECKLIST_KEYS["video"]:
            decision.locator(f'input[data-key="{key}"]').check()
        expect(approve).to_be_enabled()
        approve.click()
        decision.get_by_role("button", name="Yes, confirm").click()
        coach_page.wait_for_url(f"{url}/en/coach/channel/")
        assert has_valid_video_admission(member)
        page.reload()
        expect(page.locator('[data-verification-state="finish_setup"]')).to_be_visible()
        page.screenshot(path=str(output / "member-approved-mobile.png"), full_page=True)
        assert not errors
    finally:
        context.close()
