"""Playwright: the prompt queue on a phone (UX review Wave 2 · WP8).

``test_prompt_queue.py`` pins the markup. Only a browser can prove the
runtime rules in ``Alpine.store("prompts")``, ``pwa-install.js`` and the push
settings cards:

* 8-01 — one prompt at a time (cookie > messages > install > push); the cookie
  sheet and the push prompt sit on top of the tab bar instead of covering it;
  the install card only comes from the 2nd session, never on /account/, and
  is an overlay that shifts no content.
* 8-02 — ``PWAInstaller.init()`` returns early in a native shell.
* 8-09 — "Checking notification support…" gives up after 3 s with Try Again.

The browser is an iPhone (UA), where pwa-install.js offers the install card
without waiting for ``beforeinstallprompt``.

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_prompt_queue_playwright.py -n 0
"""

import json
import re

import pytest

pytest.importorskip("playwright")

from django.conf import settings  # noqa: E402
from django.test import Client  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

PHONE = {"width": 390, "height": 844}
IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)
DECLINED = json.dumps({"essential": True, "analytics": False, "marketing": False})
OPTIONAL_EXTERNAL_RESOURCES = (
    "**://fonts.googleapis.com/**",
    "**://fonts.gstatic.com/**",
)

# A returning visitor: one earlier session already counted.
RETURNING_VISITOR_JS = """
if (!sessionStorage.getItem("pq-seeded")) {
    sessionStorage.setItem("pq-seeded", "1");
    localStorage.setItem("crush-pwa-sessions", "1");
}
"""
# Record where the content started, to prove the install card shifts nothing.
MAIN_TOP_JS = """
document.addEventListener("DOMContentLoaded", () => {
    const main = document.getElementById("main-content");
    window.__mainTop = main ? main.getBoundingClientRect().top : null;
});
"""
# No service worker ever answers (failed registration, private mode, WebView).
HANGING_SW_JS = """
Object.defineProperty(ServiceWorkerContainer.prototype, "ready", {
    configurable: true,
    get() { return new Promise(() => {}); },
});
"""


def _member():
    from crush_lu.tests.test_profile_edit_connect_card import _make_member

    return _make_member("prompt-queue@example.com")


def _phone(browser, live_server, user, *, consent=True, returning=False, extra=None):
    context = browser.new_context(
        viewport=PHONE, user_agent=IPHONE_UA, extra_http_headers=extra or {}
    )
    for pattern in OPTIONAL_EXTERNAL_RESOURCES:
        context.route(
            pattern,
            lambda route: route.fulfill(status=200, content_type="text/css", body=""),
        )
    client = Client()
    client.force_login(user)
    cookies = [
        {
            "name": settings.SESSION_COOKIE_NAME,
            "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
            "url": live_server.url,
        }
    ]
    if consent:
        cookies.append(
            {"name": "cookie_consent", "value": DECLINED, "url": live_server.url}
        )
    context.add_cookies(cookies)
    context.add_init_script(MAIN_TOP_JS)
    if returning:
        context.add_init_script(RETURNING_VISITOR_JS)
    return context.new_page()


def _open(page, url):
    response = page.goto(url)
    assert response is not None and response.ok, response and response.status
    page.wait_for_function("() => window.Alpine && Alpine.store('prompts')")


def _nav_top(page):
    return page.locator("nav.bottom-nav").bounding_box()["y"]


def _settle(page):
    """Let the x-transition enter animations finish before measuring."""
    page.wait_for_function("() => document.getAnimations().length === 0")


def _bottom(locator):
    _settle(locator.page)
    box = locator.bounding_box()
    return box["y"] + box["height"]


def test_first_visit_cookie_sheet_sits_above_the_tab_bar_and_no_install(
    browser, live_server
):
    page = _phone(browser, live_server, _member(), consent=False)
    _open(page, f"{live_server.url}/en/dashboard/")

    sheet = page.locator("#cookie-consent-banner")
    expect(sheet).to_be_visible()
    assert abs(_bottom(sheet) - _nav_top(page)) <= 1
    assert page.evaluate("() => Alpine.store('prompts').active") == "cookie"
    # First session: the install card is not offered at all.
    page.wait_for_timeout(500)
    expect(page.locator("#pwa-install-banner")).to_be_hidden()
    assert page.evaluate("() => localStorage.getItem('crush-pwa-sessions')") == "1"


def test_second_session_install_card_waits_for_the_cookie_choice(browser, live_server):
    page = _phone(browser, live_server, _member(), consent=False, returning=True)
    _open(page, f"{live_server.url}/en/dashboard/")

    card = page.locator("#pwa-install-banner")
    expect(page.locator("#cookie-consent-banner")).to_be_visible()
    page.wait_for_timeout(500)
    expect(card).to_be_hidden()

    page.locator("#cookie-btn-decline").click()
    expect(card).to_be_visible()
    expect(page.locator("#pwa-install-button")).to_have_attribute(
        "aria-label", "Install Crush.lu App"
    )


def test_second_session_install_card_is_an_overlay_above_the_tab_bar(
    browser, live_server
):
    page = _phone(browser, live_server, _member(), returning=True)
    _open(page, f"{live_server.url}/en/dashboard/")

    card = page.locator("#pwa-install-banner")
    expect(card).to_be_visible()
    assert card.evaluate("el => getComputedStyle(el).position") == "fixed"
    # 8px (mb-2) above the tab bar.
    assert abs(_nav_top(page) - _bottom(card) - 8) <= 1
    main_top = page.evaluate(
        "() => document.getElementById('main-content').getBoundingClientRect().top"
    )
    assert main_top == page.evaluate("() => window.__mainTop")
    dismiss = page.locator("#pwa-dismiss-button").bounding_box()
    assert dismiss["width"] >= 44 and dismiss["height"] >= 44


def test_flash_message_holds_the_install_card_until_dismissed(browser, live_server):
    from django.contrib.messages import constants
    from django.contrib.messages.storage.base import Message
    from django.contrib.messages.storage.cookie import CookieStorage
    from django.http import HttpRequest

    page = _phone(browser, live_server, _member(), returning=True)
    storage = CookieStorage(HttpRequest())
    encoded = storage._encode([Message(constants.ERROR, "Something went wrong")])
    page.context.add_cookies(
        [{"name": storage.cookie_name, "value": encoded, "url": live_server.url}]
    )
    _open(page, f"{live_server.url}/en/dashboard/")

    message = page.get_by_role("alert").filter(has_text="Something went wrong")
    expect(message).to_be_visible()
    page.wait_for_timeout(500)
    expect(page.locator("#pwa-install-banner")).to_be_hidden()

    message.get_by_role("button", name="Dismiss this message").click()
    expect(page.locator("#pwa-install-banner")).to_be_visible()


def test_no_install_card_on_account_pages(browser, live_server):
    page = _phone(browser, live_server, _member(), returning=True)
    _open(page, f"{live_server.url}/en/account/settings/")
    page.wait_for_timeout(500)
    expect(page.locator("#pwa-install-banner")).to_be_hidden()
    assert page.evaluate("() => Alpine.store('prompts').install") is False


def test_installer_returns_early_in_a_native_shell(browser, live_server):
    page = _phone(browser, live_server, _member(), returning=True)
    _open(page, f"{live_server.url}/en/dashboard/")
    shown = page.evaluate("""() => {
        let shown = 0;
        window.addEventListener("pwa-show-install", () => { shown += 1; });
        new PWAInstaller();
        const browserShown = shown;
        document.documentElement.setAttribute("data-native-app", "");
        new PWAInstaller();
        return [browserShown, shown];
    }""")
    assert shown == [1, 1]


def test_push_prompt_waits_behind_the_install_card_and_clears_the_tab_bar(
    browser, live_server
):
    page = _phone(browser, live_server, _member(), returning=True)
    _open(page, f"{live_server.url}/en/dashboard/")
    expect(page.locator("#pwa-install-banner")).to_be_visible()

    prompt = page.locator("[x-data='pushActivationPrompt']")
    prompt.evaluate("el => { Alpine.$data(el).shouldShowPrompt = true; }")
    page.wait_for_timeout(400)
    expect(prompt).to_be_hidden()

    page.locator("#pwa-dismiss-button").click()
    expect(prompt).to_be_visible()
    assert abs(_bottom(prompt) - _nav_top(page)) <= 1


def test_push_settings_check_times_out_with_try_again(browser, live_server):
    page = _phone(browser, live_server, _member())
    page.context.add_init_script(HANGING_SW_JS)
    # Headless Chromium reports "denied" by default; this visitor has not
    # blocked anything, so only Retry can help.
    page.context.add_init_script(PERMISSION_DEFAULT_JS)
    _open(page, f"{live_server.url}/en/account/settings/")

    card = page.locator("[x-data='pushPreferences']")
    checking = card.get_by_text("Checking notification support...")
    timed_out = card.get_by_text(
        "We couldn't check notification support on this device."
    )
    expect(timed_out).to_be_visible(timeout=6000)
    expect(checking).to_be_hidden()

    card.get_by_role("button", name="Try Again").click()
    expect(checking).to_be_visible()
    expect(timed_out).to_be_visible(timeout=6000)


# The same visit in a new tab: sessionStorage is fresh, the last page view
# was a minute ago.
SAME_VISIT_NEW_TAB_JS = """
if (!sessionStorage.getItem("pq-seeded")) {
    sessionStorage.setItem("pq-seeded", "1");
    localStorage.setItem("crush-pwa-sessions", "1");
    localStorage.setItem("crush-pwa-last-seen", String(Date.now() - 60 * 1000));
}
"""
# Notifications neither granted nor blocked yet.
PERMISSION_DEFAULT_JS = """
Object.defineProperty(Notification, "permission", {
    configurable: true,
    get() { return "default"; },
});
"""
# A browser with a service worker but no Push API (e.g. an iOS Safari tab).
NO_PUSH_MANAGER_JS = "delete window.PushManager;"


def test_new_tab_in_the_same_visit_is_not_a_second_session(browser, live_server):
    page = _phone(browser, live_server, _member())
    page.context.add_init_script(SAME_VISIT_NEW_TAB_JS)
    _open(page, f"{live_server.url}/en/dashboard/")

    page.wait_for_timeout(500)
    expect(page.locator("#pwa-install-banner")).to_be_hidden()
    assert page.evaluate("() => localStorage.getItem('crush-pwa-sessions')") == "1"


def test_ios_guide_stays_hidden_without_javascript(browser, live_server):
    context = browser.new_context(
        viewport=PHONE, user_agent=IPHONE_UA, java_script_enabled=False
    )
    page = context.new_page()
    page.goto(f"{live_server.url}/en/")
    expect(page.get_by_text("Step 1: Tap Share")).to_be_hidden()


def test_coach_card_without_push_api_says_not_supported_not_retry(browser, live_server):
    from crush_lu.models import CrushCoach

    user = _member()
    CrushCoach.objects.create(user=user, bio="Coach", is_active=True)
    page = _phone(browser, live_server, user)
    page.context.add_init_script(HANGING_SW_JS)
    page.context.add_init_script(NO_PUSH_MANAGER_JS)
    _open(page, f"{live_server.url}/en/account/settings/")

    card = page.locator("[x-data='coachPushPreferences']")
    expect(card).to_be_visible()
    page.wait_for_timeout(3500)
    expect(
        card.get_by_text("We couldn't check notification support on this device.")
    ).to_be_hidden()
    expect(card.get_by_text("Try Again")).to_be_hidden()
    expect(card.get_by_text("Push notifications not available")).to_be_visible()


# A tab the browser kept (or restored) from a visit two hours ago.
RESTORED_TAB_JS = """
if (!sessionStorage.getItem("pq-seeded")) {
    sessionStorage.setItem("pq-seeded", "1");
    sessionStorage.setItem("crush-pwa-session", "1");
    localStorage.setItem("crush-pwa-sessions", "1");
    localStorage.setItem(
        "crush-pwa-last-seen", String(Date.now() - 2 * 60 * 60 * 1000)
    );
}
"""
# The visitor blocked notifications for the site in browser settings.
PERMISSION_DENIED_JS = """
Object.defineProperty(Notification, "permission", {
    configurable: true,
    get() { return "denied"; },
});
"""


def test_restored_tab_after_inactivity_is_a_new_session(browser, live_server):
    page = _phone(browser, live_server, _member())
    page.context.add_init_script(RESTORED_TAB_JS)
    _open(page, f"{live_server.url}/en/dashboard/")

    expect(page.locator("#pwa-install-banner")).to_be_visible()
    assert page.evaluate("() => localStorage.getItem('crush-pwa-sessions')") == "2"


def test_blocked_notifications_win_over_try_again(browser, live_server):
    page = _phone(browser, live_server, _member())
    page.context.add_init_script(HANGING_SW_JS)
    page.context.add_init_script(PERMISSION_DENIED_JS)
    _open(page, f"{live_server.url}/en/account/settings/")

    card = page.locator("[x-data='pushPreferences']")
    expect(card.get_by_text("Notifications blocked")).to_be_visible(timeout=6000)
    page.wait_for_timeout(3500)
    expect(card.get_by_text("Notifications blocked")).to_be_visible()
    expect(card.get_by_role("button", name="Try Again")).to_have_count(0)


def test_whatsapp_button_tucks_away_while_the_install_card_shows(browser, live_server):
    from crush_lu import context_processors
    from crush_lu.models import CrushSiteConfig

    config = CrushSiteConfig.get_config()
    config.whatsapp_enabled = True
    config.whatsapp_number = "352000000"
    config.save()
    context_processors._site_config_cache["config"] = None
    try:
        page = _phone(browser, live_server, _member(), returning=True)
        _open(page, f"{live_server.url}/en/dashboard/")

        fab = page.locator(".crush-whatsapp-btn")
        expect(page.locator("#pwa-install-banner")).to_be_visible()
        expect(fab).to_have_class(re.compile(r"crush-whatsapp-btn--tucked"))

        page.locator("#pwa-dismiss-button").click()
        expect(page.locator("#pwa-install-banner")).to_be_hidden()
        expect(fab).not_to_have_class(re.compile(r"crush-whatsapp-btn--tucked"))
    finally:
        context_processors._site_config_cache["config"] = None
