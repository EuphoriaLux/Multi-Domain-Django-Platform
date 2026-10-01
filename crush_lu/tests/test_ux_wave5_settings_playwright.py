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
