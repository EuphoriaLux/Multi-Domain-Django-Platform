"""Real dashboard regression checks for the shared 30-day install cooldown."""

import pytest

pytest.importorskip("playwright")

from django.conf import settings  # noqa: E402
from django.test import Client  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

from crush_lu.tests.test_prompt_queue_playwright import (  # noqa: E402
    DECLINED,
    OPTIONAL_EXTERNAL_RESOURCES,
    PHONE,
    _member,
    _open,
    _phone,
)

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

INSTALL_CARD = "[x-data='pwaInstallButton'] > .status-card:first-child"
FAKE_NATIVE_PROMPT = """
() => {
    const prompt = new Event('beforeinstallprompt', {cancelable: true});
    prompt.prompt = () => {};
    prompt.userChoice = Promise.resolve({outcome: 'dismissed'});
    window.dispatchEvent(prompt);
}
"""


def test_dashboard_native_rejection_stays_hidden_after_reload(browser, live_server):
    context = browser.new_context(viewport=PHONE)
    try:
        user = _member()
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
        for pattern in OPTIONAL_EXTERNAL_RESOURCES:
            context.route(
                pattern,
                lambda route: route.fulfill(
                    status=200, content_type="text/css", body=""
                ),
            )
        context.add_init_script("localStorage.setItem('crush-pwa-sessions', '2');")
        page = context.new_page()
        _open(page, f"{live_server.url}/en/dashboard/")
        page.evaluate(FAKE_NATIVE_PROMPT)
        card = page.locator(INSTALL_CARD)
        expect(card).to_be_visible()
        card.get_by_role("button", name="Install App", exact=True).click()
        expect(card).to_be_hidden()
        assert page.evaluate("Alpine.store('prompts').install") is False
        assert page.evaluate(
            "Number(localStorage.getItem('crush-pwa-install-dismissed')) > 0"
        )

        _open(page, f"{live_server.url}/en/dashboard/")
        page.evaluate(FAKE_NATIVE_PROMPT)
        expect(card).to_be_hidden()
        assert page.evaluate("Alpine.store('prompts').install") is False
    finally:
        context.close()


def test_ios_dashboard_honors_banner_cooldown_until_expiry(browser, live_server):
    page = _phone(browser, live_server, _member(), returning=True)
    try:
        page.add_init_script("""
            if (!sessionStorage.getItem('install-cooldown-seeded')) {
                sessionStorage.setItem('install-cooldown-seeded', '1');
                localStorage.setItem('crush-pwa-install-dismissed', String(Date.now()));
            }
            """)
        _open(page, f"{live_server.url}/en/dashboard/")
        card = page.locator(INSTALL_CARD)
        expect(card).to_be_hidden()
        assert page.evaluate("Alpine.store('prompts').install") is False

        page.evaluate(
            "localStorage.setItem('crush-pwa-install-dismissed', "
            "String(Date.now() - 30 * 24 * 60 * 60 * 1000))"
        )
        _open(page, f"{live_server.url}/en/dashboard/")
        expect(card).to_be_visible()
        expect(card.get_by_text("How to install", exact=True)).to_be_visible()
        assert page.evaluate("Alpine.store('prompts').active") == "install"
        assert (
            page.evaluate("localStorage.getItem('crush-pwa-install-dismissed')") is None
        )
    finally:
        page.context.close()


def test_ios_dashboard_storage_denial_suppresses_instructions(browser, live_server):
    page = _phone(browser, live_server, _member(), returning=True)
    try:
        page.add_init_script("""
            Object.defineProperty(window, 'localStorage', {
                get() { throw new Error('storage denied'); },
            });
            """)
        _open(page, f"{live_server.url}/en/dashboard/")
        expect(page.locator(INSTALL_CARD)).to_be_hidden()
        assert page.evaluate("Alpine.store('prompts').install") is False
        assert (
            page.evaluate(
                "Alpine.$data(document.querySelector('[x-data=pwaInstallButton]'))"
                ".showInstructions"
            )
            is False
        )
    finally:
        page.context.close()
