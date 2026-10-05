"""Playwright: instant WhatsApp save and the "Pause all emails" switch (WP14).

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_ux_wave5_settings_playwright.py -n 0
"""

import json

import pytest

pytest.importorskip("playwright")

from django.conf import settings  # noqa: E402
from django.test import Client  # noqa: E402

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

PHONE = {"width": 390, "height": 844}
DECLINED = json.dumps({"essential": True, "analytics": False, "marketing": False})
NOTIFICATIONS = "/en/profile/edit/?section=account&sub=notifications"


def _page(browser, live_server, user):
    context = browser.new_context(viewport=PHONE)
    context.route(
        "**://fonts.g*.com/**",
        lambda route: route.fulfill(status=200, content_type="text/css", body=""),
    )
    client = Client()
    client.force_login(user)
    context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            },
            {"name": "cookie_consent", "value": DECLINED, "url": live_server.url},
        ]
    )
    page = context.new_page()
    response = page.goto(f"{live_server.url}{NOTIFICATIONS}")
    assert response is not None and response.ok
    page.wait_for_function("() => window.Alpine && Alpine.store('toasts')")
    return page


def _member():
    from crush_lu.models import CrushProfile
    from crush_lu.tests.test_profile_edit_connect_card import _make_member

    user = _make_member("w5pw@example.com")
    CrushProfile.objects.filter(user=user).update(
        phone_number="+352621123456", phone_verified=True
    )
    return user


def test_whatsapp_switch_saves_instantly_with_a_toast(browser, live_server):
    from crush_lu.models import EmailPreference

    user = _member()
    page = _page(browser, live_server, user)

    switch = page.locator("input[name='whatsapp_opt_in']")
    switch.evaluate("el => el.click()")
    page.get_by_text("WhatsApp notification preference updated.").wait_for()
    assert EmailPreference.objects.get(user=user).whatsapp_opt_in is True
    # Still on the same page: no form submission round trip.
    assert "sub=notifications" in page.url

    switch.evaluate("el => el.click()")
    page.wait_for_function(
        "() => document.querySelectorAll('#toast-container > *').length >= 1"
    )
    page.wait_for_timeout(500)
    assert EmailPreference.objects.get(user=user).whatsapp_opt_in is False


def test_pause_all_emails_switch_persists_and_dims_the_list(browser, live_server):
    from crush_lu.models import EmailPreference

    user = _member()
    page = _page(browser, live_server, user)

    pause = page.get_by_role("switch", name="Pause all emails")
    pause.evaluate("el => el.click()")
    page.wait_for_function("() => document.querySelector('.opacity-50')")
    page.wait_for_timeout(500)
    assert EmailPreference.objects.get(user=user).unsubscribed_all is True


def test_whatsapp_rapid_flips_reach_the_server_in_click_order(browser, live_server):
    """Codex review: a slow first write must not land after the second."""
    from crush_lu.models import EmailPreference

    user = _member()
    page = _page(browser, live_server, user)
    seen = []
    pending = []

    def handle(route):
        seen.append(json.loads(route.request.post_data)["value"])
        if len(seen) == 1:
            pending.append(route)  # hold the first write open
        else:
            route.continue_()

    page.route("**/api/email/preferences/", handle)
    switch = page.locator("input[name='whatsapp_opt_in']")
    switch.evaluate("el => el.click()")
    switch.evaluate("el => el.click()")
    page.wait_for_timeout(700)
    # Second write is queued behind the first, not racing it.
    assert seen == [True]
    pending[0].continue_()
    page.wait_for_function(
        "() => document.querySelectorAll('#toast-container > *').length >= 2"
    )
    assert seen == [True, False]
    assert EmailPreference.objects.get(user=user).whatsapp_opt_in is False


TOASTS = "() => document.querySelectorAll('#toast-container > *').length >= 3"


def test_whatsapp_superseded_failure_does_not_roll_back_the_switch(
    browser, live_server
):
    """#1147 ABA race: on -> off -> on while the first save is pending, and
    that first save fails. The two newer writes succeed, so the database ends
    on and the switch must too (the old rollback compared checkbox.checked,
    which is "on" again, and flipped it to off)."""
    from crush_lu.models import EmailPreference

    user = _member()
    page = _page(browser, live_server, user)
    seen = []
    pending = []

    def handle(route):
        seen.append(json.loads(route.request.post_data)["value"])
        if len(seen) == 1:
            pending.append(route)  # hold the first write open, then fail it
        else:
            route.continue_()

    page.route("**/api/email/preferences/", handle)
    switch = page.locator("input[name='whatsapp_opt_in']")
    for _ in range(3):
        switch.evaluate("el => el.click()")
    assert switch.evaluate("el => el.checked") is True
    page.wait_for_timeout(300)
    pending[0].fulfill(
        status=200, content_type="application/json", body='{"success": false}'
    )
    page.wait_for_function(TOASTS)
    page.wait_for_timeout(300)
    assert seen == [True, False, True]
    assert EmailPreference.objects.get(user=user).whatsapp_opt_in is True
    assert switch.evaluate("el => el.checked") is True


def test_whatsapp_latest_failure_reconciles_to_the_confirmed_value(
    browser, live_server
):
    """#1147: when the latest write fails, the switch shows what the server
    last confirmed."""
    from crush_lu.models import EmailPreference

    user = _member()
    page = _page(browser, live_server, user)
    calls = []

    def handle(route):
        calls.append(json.loads(route.request.post_data)["value"])
        if len(calls) == 3:
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"success": false}',
            )
        else:
            route.continue_()

    page.route("**/api/email/preferences/", handle)
    switch = page.locator("input[name='whatsapp_opt_in']")
    for _ in range(3):
        switch.evaluate("el => el.click()")
    page.wait_for_function(TOASTS)
    page.wait_for_timeout(300)
    assert calls == [True, False, True]
    assert EmailPreference.objects.get(user=user).whatsapp_opt_in is False
    assert switch.evaluate("el => el.checked") is False


BLOCKED_UAS = {
    "desktop": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36",
        "click the lock icon in your browser's address bar and allow",
    ),
    "android": (
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36",
        "open Permissions, and allow notifications",
    ),
    "ios": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 "
        "Safari/604.1",
        "open Settings, then Notifications, choose Crush.lu",
    ),
}


@pytest.mark.parametrize("platform", sorted(BLOCKED_UAS))
def test_coach_card_shows_platform_specific_blocked_guidance(
    browser, live_server, platform
):
    """Codex review: the Coach card must not fall back to generic copy."""
    from crush_lu.models import CrushCoach

    user = _member()
    CrushCoach.objects.create(user=user, is_active=True)
    ua, expected = BLOCKED_UAS[platform]
    context = browser.new_context(viewport=PHONE, user_agent=ua)
    context.route(
        "**://fonts.g*.com/**",
        lambda route: route.fulfill(status=200, content_type="text/css", body=""),
    )
    client = Client()
    client.force_login(user)
    context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            },
            {"name": "cookie_consent", "value": DECLINED, "url": live_server.url},
        ]
    )
    context.add_init_script(
        "Object.defineProperty(Notification, 'permission', {get: () => 'denied'});"
    )
    page = context.new_page()
    page.goto(f"{live_server.url}{NOTIFICATIONS}")
    page.wait_for_function("() => window.Alpine && Alpine.store('toasts')")
    card = page.locator("#coach-push-notifications, [x-data='coachPushPreferences']")
    card.get_by_text("Notifications blocked").wait_for()
    visible = card.locator("p:visible", has_text="blocked").all_inner_texts()
    visible += card.locator("p:visible", has_text="allow").all_inner_texts()
    text = " ".join(visible)
    assert expected in text, text
    assert "Allow notifications in your browser settings" not in text
    # Exactly one platform paragraph is shown.
    shown = [t for t in card.locator("p:visible").all_inner_texts() if "allow" in t]
    assert len(shown) == 1, shown
