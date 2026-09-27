"""Playwright: JS behaviour changed for UX Wave 3 WP6 (event detail).

Covers the interactive changes: the description "Read more" toggle (#4-03,
plus its aria-expanded/aria-controls wiring, WP6-2), the share button's
clipboard fallback + toast (#4-18), the mobile sticky CTA bar's toast-offset
guard (WP6-1), and its fallback to a `.js-sumup-checkout-detail` payment
button for a registered-but-unpaid member (WP6-3).

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_ux_wave3_event_detail_playwright.py -n 0 --create-db
"""

import re
from datetime import timedelta

import pytest
from django.conf import settings
from django.test import Client
from django.utils import timezone
from playwright.sync_api import expect

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1280, "height": 900}

LONG_DESCRIPTION = (
    "Join us for a wonderful evening of wine tasting paired with speed "
    "dating. We'll explore some of the finest wines the Moselle valley has "
    "to offer, guided by a local sommelier, while you meet new people in a "
    "relaxed, low-pressure setting. Snacks are provided, and the format "
    "leaves plenty of time for longer conversations between rounds. Come "
    "alone or bring a friend — everyone gets matched into the rotation."
)


@pytest.fixture
def upcoming_event(transactional_db):
    from crush_lu.models import MeetupEvent

    return MeetupEvent.objects.create(
        title="Wave 3 WP6 Mixer",
        description=LONG_DESCRIPTION,
        event_type="mixer",
        date_time=timezone.now() + timedelta(days=10),
        location="Luxembourg City",
        address="1 Rue de la Gare, Luxembourg",
        max_participants=30,
        registration_deadline=timezone.now() + timedelta(days=8),
        registration_fee=0,
        is_published=True,
        profile_requirement="none",
    )


def test_read_more_expands_the_full_description(page, live_server, upcoming_event):
    page.set_viewport_size(PHONE)
    page.goto(f"{live_server.url}/en/events/{upcoming_event.id}/")

    description = page.locator(".prose")
    toggle = page.get_by_role("button", name="Read more")
    expect(toggle).to_be_visible()
    expect(description).to_have_class(re.compile(r"\bline-clamp-4\b"))

    toggle.click()
    expect(page.get_by_role("button", name="Show less")).to_be_visible()
    expect(description).not_to_have_class(re.compile(r"\bline-clamp-4\b"))


def test_share_button_falls_back_to_clipboard_copy_and_shows_toast(
    page, live_server, upcoming_event
):
    page.set_viewport_size(PHONE)
    # Simulate a browser without the Web Share API (desktop Firefox/Chrome),
    # and stub the clipboard so the test doesn't need OS clipboard access.
    page.add_init_script(
        "delete window.navigator.share;"
        "window.__copiedText = null;"
        "Object.defineProperty(window.navigator, 'clipboard', {"
        "  value: { writeText: (text) => { window.__copiedText = text; "
        "return Promise.resolve(); } },"
        "  configurable: true"
        "});"
    )
    page.goto(f"{live_server.url}/en/events/{upcoming_event.id}/")

    share_btn = page.locator("#shareEventBtn")
    expect(share_btn).to_be_visible()
    share_btn.click()

    expect(page.locator("#toast-container")).to_contain_text("Link copied")
    copied = page.evaluate("window.__copiedText")
    assert copied == page.url


def test_read_more_toggle_reports_aria_expanded_and_controls(
    page, live_server, upcoming_event
):
    """WP6-2: the toggle must announce its state, not just swap the label."""
    page.set_viewport_size(PHONE)
    page.goto(f"{live_server.url}/en/events/{upcoming_event.id}/")

    toggle = page.get_by_role("button", name="Read more")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    controls_id = toggle.get_attribute("aria-controls")
    assert controls_id, "toggle button must declare aria-controls"
    expect(page.locator(f"#{controls_id}")).to_be_visible()

    toggle.click()
    expect(page.get_by_role("button", name="Show less")).to_have_attribute(
        "aria-expanded", "true"
    )


def _log_in(page, live_server, user):
    client = Client()
    client.force_login(user)
    page.context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            }
        ]
    )


def _make_open_registration_member():
    from django.contrib.auth import get_user_model

    from crush_lu.models import UserDataConsent

    user = get_user_model().objects.create_user(
        username="sticky-cta-e2e@example.com",
        email="sticky-cta-e2e@example.com",
        password="testpass123",
        first_name="Sticky",
    )
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    return user


def test_sticky_cta_offsets_toast_on_mobile_and_never_on_desktop(
    page, live_server, upcoming_event
):
    """WP6-1: a toast must not render on top of the sticky CTA bar on
    mobile, and the sticky bar's own (md:hidden) inline offset must never
    leak into the desktop toast stack.

    Drives the component's ``visible`` flag directly through Alpine's
    ``$data`` rather than scrolling: real scroll-driven IntersectionObserver
    timing in a headless viewport is flaky to assert on and isn't what
    changed here — the ``$watch`` wired up on ``visible`` is.
    """
    member = _make_open_registration_member()
    _log_in(page, live_server, member)

    page.set_viewport_size(PHONE)
    page.goto(f"{live_server.url}/en/events/{upcoming_event.id}/")

    sticky_bar = page.locator("#event-sticky-cta")

    def set_visible(value):
        page.evaluate(
            "v => Alpine.$data(document.getElementById('event-sticky-cta')).visible = v",
            value,
        )

    def toast_bottom_style():
        return page.evaluate("document.getElementById('toast-container').style.bottom")

    def wait_for_toast_bottom(predicate, description):
        # `page.wait_for_function` runs its predicate via an in-page
        # `new Function`, which this app's CSP blocks (script-src has no
        # 'unsafe-eval') — poll through one-shot `page.evaluate` calls
        # instead, which go through the CDP Runtime.evaluate channel and
        # aren't subject to that restriction.
        for _ in range(20):
            if predicate(toast_bottom_style()):
                return
            page.wait_for_timeout(50)
        raise AssertionError(description)

    set_visible(True)
    expect(sticky_bar).to_be_visible()
    bar_height = sticky_bar.bounding_box()["height"]
    assert bar_height > 0

    # $watch's callback runs on Alpine's own effect flush, a tick after the
    # x-show DOM patch that expect(...).to_be_visible() already waited for
    # — poll rather than assert immediately.
    wait_for_toast_bottom(
        lambda v: v != "", "toast-container never received a bottom offset"
    )
    inline_bottom = toast_bottom_style()
    assert str(round(bar_height)) in inline_bottom, (
        "toast-container's bottom offset must be derived from the bar's own "
        "rendered height so a toast can never render on top of it"
    )

    set_visible(False)
    expect(sticky_bar).to_be_hidden()
    wait_for_toast_bottom(
        lambda v: v == "", "toast-container's bottom offset was not cleared"
    )

    # On >=768px the bar is display:none (md:hidden), so offsetHeight is 0
    # even if `visible` is flipped true — the watcher must never write a
    # bottom offset in that case, or the desktop toast stack (which uses
    # lg:bottom-auto lg:top-4, not .toast-above-nav) would inherit one.
    page.set_viewport_size(DESKTOP)
    set_visible(True)
    page.wait_for_timeout(200)
    assert toast_bottom_style() == ""


def test_sticky_cta_falls_back_to_pay_button_for_unpaid_registration(
    page, live_server, upcoming_event
):
    """WP6-3: a registered-but-unpaid member has no `a.btn-crush-primary`
    inside #event-cta-panel — only `.js-sumup-checkout-detail` <button>s —
    so init() must fall back to one of those instead of leaving the bar
    permanently hidden, and tapping the sticky bar's own button must trigger
    the same SumUp checkout as tapping the in-panel one.
    """
    from crush_lu.models import EventRegistration

    upcoming_event.registration_fee = 25
    upcoming_event.save(update_fields=["registration_fee"])
    member = _make_open_registration_member()
    EventRegistration.objects.create(
        event=upcoming_event,
        user=member,
        status="pending",
        payment_confirmed=False,
    )
    _log_in(page, live_server, member)

    page.set_viewport_size(PHONE)
    page.goto(f"{live_server.url}/en/events/{upcoming_event.id}/")

    sticky_bar = page.locator("#event-sticky-cta")
    sticky_button = sticky_bar.locator("button")
    expect(sticky_button).to_be_attached()
    expect(sticky_button).to_contain_text("Pay with Card")

    checkout_request = {"seen": False}

    def handle_checkout(route):
        checkout_request["seen"] = True
        route.fulfill(
            status=200,
            content_type="application/json",
            body='{"success": true, "widget_url": "about:blank#checkout"}',
        )

    page.route("**/payments/sumup/create-event-checkout/**", handle_checkout)

    page.evaluate(
        "Alpine.$data(document.getElementById('event-sticky-cta')).visible = true"
    )
    expect(sticky_bar).to_be_visible()
    # The bar sits fixed above the mobile tab bar (--bottom-nav-height); at
    # the 390x844 test viewport that can place its bounding box just past
    # what Playwright considers the visible viewport even though it's
    # genuinely on-screen. dispatch_event bypasses that actionability check —
    # the click *handler*, not scroll/hit-testing, is what's under test here.
    sticky_button.dispatch_event("click")

    for _ in range(20):
        if checkout_request["seen"]:
            break
        page.wait_for_timeout(50)
    assert checkout_request["seen"], (
        "tapping the sticky bar's payment button must replay a click onto "
        "the real .js-sumup-checkout-detail button so the existing "
        "document-level checkout listener fires"
    )
