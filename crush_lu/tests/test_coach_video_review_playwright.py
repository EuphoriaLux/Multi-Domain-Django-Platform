"""Native-shell pages with actual Chromium media and relayed signalling.

The WebSocket transport is routed in Playwright; RTCPeerConnection negotiates
real audio/video tracks from Chromium's fake devices. Native OS permission
prompts and physical-device networking remain mobile release checks.
"""

from pathlib import Path

import pytest
from django.core.cache import cache
from django.test import Client
from playwright.sync_api import expect

from crush_lu.models import ConnectRealPersonBadge
from crush_lu.services.real_person_check import claim_check
from crush_lu.tests.real_person_check_helpers import make_coach, make_member, profile_of
from crush_lu.tests.test_crush_connect import _grant_consent

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    return {
        **browser_type_launch_args,
        "args": [
            *browser_type_launch_args.get("args", []),
            "--use-fake-device-for-media-stream",
            "--use-fake-ui-for-media-stream",
        ],
    }


@pytest.fixture
def video_review(live_server, settings):
    cache.clear()
    settings.DEBUG = True
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = True
    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False
    url = live_server.url.replace("localhost", "crush.localhost")
    settings.CSRF_TRUSTED_ORIGINS = [url]
    coach = make_coach("app_video_coach")
    _grant_consent(coach.user)
    member = make_member("app_video_member")
    check = claim_check(coach, profile_of(member).pk)
    return url, coach, member, check


def _signed_in_page(browser, url, user, agent):
    context = browser.new_context(
        user_agent=agent,
        viewport={"width": 390, "height": 844},
        permissions=["camera", "microphone"],
        reduced_motion="reduce",
        color_scheme="dark" if "Android" in agent else "light",
    )
    client = Client()
    client.force_login(user)
    context.add_cookies(
        [{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": url}]
    )
    page = context.new_page()
    page.set_default_timeout(15000)
    page.route("**/media/profile/**", lambda route: route.abort())
    page.route(
        "**/ice-servers/",
        lambda route: route.fulfill(
            json={"iceServers": [], "iceTransportPolicy": "all"}
        ),
    )
    return context, page


@pytest.mark.parametrize("agent", ["CrushLUAndroid/1.0.4", "CrushLUApp/1.0.2"])
def test_native_pages_connect_video_and_keep_questions_during_outreach(
    browser, video_review, agent
):
    url, coach, member, check = video_review
    coach_context, coach_page = _signed_in_page(browser, url, coach.user, agent)
    member_context, member_page = _signed_in_page(browser, url, member, agent)
    sockets = []
    errors = []

    def relay(socket):
        sockets.append(socket)

        def forward(message):
            for peer in sockets:
                if peer is not socket:
                    peer.send(message)

        socket.on_message(forward)

    try:
        for page in (coach_page, member_page):
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route_web_socket("**/ws/video-call/**", relay)
        coach_page.goto(f"{url}/en/coach/real-person-checks/{check.pk}/")
        member_page.goto(f"{url}/en/crush-connect/real-person-call/{check.pk}/")
        for page in (coach_page, member_page):
            consent = page.locator("#cookie-btn-decline")
            if consent.is_visible():
                consent.click()
            page.get_by_role("button", name="Start video", exact=True).click()
        for page in (coach_page, member_page):
            expect(
                page.locator('[x-data="videoCall"] [role="status"]').first
            ).to_have_text("Connected", timeout=30000)
            page.wait_for_function("""() => {
                    const video = document.querySelector('[x-data="videoCall"] video');
                    return video.videoWidth > 0 && video.readyState >= 2 &&
                        video.srcObject.getAudioTracks().some(t => t.readyState === 'live');
                }""")
        questions = coach_page.locator("[data-video-review-questions]")
        expect(questions.locator("details")).to_have_count(5)
        questions.get_by_text("4. Crush Definition", exact=True).click()
        expect(
            questions.get_by_text("This open question helps", exact=False)
        ).to_be_visible()
        expect(
            coach_page.locator('[x-data="videoCall"] video').first
        ).to_be_in_viewport()
        assert coach_page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        # Logging outreach must not replace the running call or its script.
        coach_page.get_by_role("button", name="I sent an SMS", exact=True).click()
        expect(coach_page.locator("#rpc-outreach")).to_contain_text("SMS template sent")
        expect(
            coach_page.locator('[x-data="videoCall"] [role="status"]').first
        ).to_have_text("Connected")
        check.refresh_from_db()
        assert check.status == "open"
        assert not ConnectRealPersonBadge.objects.filter(profile=check.profile).exists()
        output = Path("screenshots/coach-video-review")
        output.mkdir(parents=True, exist_ok=True)
        questions.get_by_text(
            "This open question helps", exact=False
        ).scroll_into_view_if_needed()
        coach_page.locator('[x-data="videoCall"]').scroll_into_view_if_needed()
        expect(
            questions.get_by_text("This open question helps", exact=False)
        ).to_be_in_viewport()
        expect(
            coach_page.locator('[x-data="videoCall"] video').first
        ).to_be_in_viewport()
        coach_page.screenshot(
            path=str(output / ("android.png" if "Android" in agent else "ios.png"))
        )
        coach_page.get_by_role("button", name="Hang up", exact=True).click()
        expect(
            coach_page.locator('[x-data="videoCall"] [role="status"]').first
        ).to_have_text("Call ended")
        assert errors == []
    finally:
        coach_context.close()
        member_context.close()


@pytest.mark.parametrize("failure", ["denied", "unsupported"])
def test_old_app_capture_failures_show_recovery_without_approving(
    browser, video_review, failure
):
    url, coach, member, check = video_review
    context, page = _signed_in_page(browser, url, coach.user, "CrushLUAndroid/1.0.4")
    page.add_init_script(
        """Object.defineProperty(navigator, 'mediaDevices', {value: %s});"""
        % (
            "{getUserMedia: () => Promise.reject(new DOMException('Denied', 'NotAllowedError'))}"
            if failure == "denied"
            else "undefined"
        )
    )
    try:
        page.goto(f"{url}/en/coach/real-person-checks/{check.pk}/")
        page.get_by_role("button", name="Start video", exact=True).click()
        recovery = page.locator('[x-show="hasMediaError"]')
        expect(recovery).to_be_visible()
        expect(recovery).to_contain_text("update Crush.lu")
        expect(recovery).to_contain_text(f"/en/coach/real-person-checks/{check.pk}/")
        expect(page.locator("[data-video-review-questions]")).to_be_visible()
        check.refresh_from_db()
        assert check.status == "open"
        assert not ConnectRealPersonBadge.objects.filter(profile__user=member).exists()
    finally:
        context.close()
