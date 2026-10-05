"""Playwright: WP5 flows-rest JS behaviour (#1116 copy fallback, #1058 sheet).

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_ux_wave6_flows_rest_playwright.py -n 0
"""

import json
import pytest

pytest.importorskip("playwright")

from django.conf import settings  # noqa: E402
from django.test import Client  # noqa: E402

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

PHONE = {"width": 390, "height": 844}
DECLINED = json.dumps({"essential": True, "analytics": False, "marketing": False})


def _page(browser, live_server, user, path):
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
    response = page.goto(f"{live_server.url}{path}")
    assert response is not None and response.ok
    page.wait_for_function("() => window.Alpine && Alpine.store('toasts')")
    return page


def _member(username):
    from crush_lu.models import UserDataConsent
    from crush_lu.tests.test_event_lobby import _make_member

    user = _make_member(username, membership=False)
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    return user


def test_copy_failure_toast_shows_the_referral_share_url(browser, live_server):
    """#1116: with no Web Share, no Clipboard API and a failing execCommand,
    the toast shows the member's own share link (which carries the referral)
    instead of pointing at the address bar, and stays until dismissed."""
    from crush_lu.tests.test_event_lobby import _make_event

    from crush_lu.models import ReferralCode

    user = _member("wp6pw_share")
    ReferralCode.get_or_create_for_profile(user.crushprofile)
    event = _make_event(starts_in_minutes=60 * 24 * 5)
    page = _page(browser, live_server, user, f"/en/events/{event.pk}/")
    share_url = page.locator("#shareEventBtn").get_attribute("data-share-url")
    assert "/r/" in share_url  # a member's referral link, not the bare page
    page.evaluate(
        "() => {"
        " Object.defineProperty(navigator, 'share', {value: undefined, configurable: true});"
        " Object.defineProperty(navigator, 'clipboard', {value: undefined, configurable: true});"
        " document.execCommand = () => false; }"
    )
    page.locator("#shareEventBtn").click()
    toast = page.locator("#toast-container [role=alert]")
    toast.wait_for()
    text = toast.inner_text()
    assert "Long-press or select it here instead:" in text
    assert share_url in text
    assert "address bar" not in text
    page.wait_for_timeout(5500)  # past the default 5 s auto-dismiss
    assert toast.is_visible()
    width = page.evaluate("() => document.documentElement.scrollWidth")
    assert width <= PHONE["width"]


def test_take_a_break_asks_through_the_confirm_sheet(browser, live_server):
    """#1058: the button opens the branded sheet (no window.confirm); cancel
    keeps the member active, confirm submits the form."""
    from crush_lu.models import CrushProfile

    user = _member("wp6pw_break")
    page = _page(browser, live_server, user, "/en/account/take-a-break/")
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    sheet = page.locator("#crush-confirm-dialog")

    page.locator("main button[type=submit]", has_text="Take a Break").click()
    sheet.get_by_text("Take a break now?").wait_for()
    sheet.get_by_role("button", name="Cancel").click()
    page.wait_for_timeout(300)
    assert not CrushProfile.objects.get(user=user).is_on_break

    page.locator("main button[type=submit]", has_text="Take a Break").click()
    with page.expect_navigation():
        sheet.get_by_role("button", name="Take a Break").click()
    assert CrushProfile.objects.get(user=user).is_on_break
    assert dialogs == []
