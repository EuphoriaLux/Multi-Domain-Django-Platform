"""Playwright: UX Wave 4 WP14 layout behaviour a template test can't prove.

* 1-08 leftovers — the legal "Back to contents" pill is not covered by the
  install card or the cookie sheet on a 390 px phone, and is hidden in print.
* 1-08 leftovers — the How It Works timeline connector runs through the icon
  centres at 1280 px.

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_ux_wave4_design_sweep_playwright.py -n 0
"""

import json
from datetime import date

import pytest

pytest.importorskip("playwright")

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1280, "height": 900}
DECLINED = json.dumps({"essential": True, "analytics": False, "marketing": False})

RECT_JS = """
(sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return {
        display: cs.display, top: r.top, bottom: r.bottom,
        left: r.left, right: r.right, width: r.width, height: r.height,
    };
}
"""

CONNECTOR_JS = """
() => {
    const grid = document.querySelector(".step-timeline");
    const g = grid.getBoundingClientRect();
    const after = getComputedStyle(grid, "::after");
    const icons = [...grid.querySelectorAll(".step-icon")].map((i) => {
        const r = i.getBoundingClientRect();
        return {x: r.left + r.width / 2 - g.left, y: r.top + r.height / 2 - g.top};
    });
    const left = parseFloat(after.left);
    return {
        top: parseFloat(after.top),
        left: left,
        right: left + parseFloat(after.width),
        icons: icons,
    };
}
"""


def _overlap(a, b):
    return not (
        a["right"] <= b["left"]
        or a["left"] >= b["right"]
        or a["bottom"] <= b["top"]
        or a["top"] >= b["bottom"]
    )


def _log_in_member(page, live_server):
    """An approved member: install-card eligible, and the bottom tab bar shows."""
    from allauth.account.models import EmailAddress
    from django.contrib.auth import get_user_model
    from django.test import Client

    from crush_lu.models.profiles import CrushProfile, UserDataConsent

    user = get_user_model().objects.create_user(
        username="wp14.pill@example.com",
        email="wp14.pill@example.com",
        password="Pill-pass-2026!",
        first_name="Lena",
    )
    EmailAddress.objects.create(
        user=user, email=user.email, verified=True, primary=True
    )
    UserDataConsent.objects.update_or_create(
        user=user,
        defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
    )
    CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1995, 5, 15),
        gender="F",
        location="Luxembourg City",
        is_approved=True,
        is_active=True,
    )
    client = Client()
    client.force_login(user)
    page.context.add_cookies(
        [
            {
                "name": "sessionid",
                "value": client.cookies["sessionid"].value,
                "url": live_server.url,
            }
        ]
    )


def _goto(page, live_server, path, consent=True, sessions=None):
    if consent:
        page.context.add_cookies(
            [{"name": "cookie_consent", "value": DECLINED, "url": live_server.url}]
        )
    if sessions is not None:
        page.add_init_script(
            f"try {{ localStorage.setItem('crush-pwa-sessions', '{sessions}'); }} catch (e) {{}}"
        )
    page.goto(f"{live_server.url}{path}")
    page.wait_for_load_state("load")
    page.wait_for_function("() => window.Alpine && Alpine.store('prompts')")


def test_pill_is_visible_with_no_prompt(page, live_server):
    page.set_viewport_size(PHONE)
    _goto(page, live_server, "/en/privacy-policy/")
    assert page.locator("a.legal-back-to-contents").is_visible()


def test_pill_not_covered_by_install_banner(page, live_server):
    page.set_viewport_size(PHONE)
    _log_in_member(page, live_server)
    _goto(page, live_server, "/en/privacy-policy/", sessions=3)
    page.evaluate(
        "() => window.dispatchEvent(new CustomEvent('pwa-show-install',"
        " {detail: {platform: 'other'}}))"
    )
    page.wait_for_selector("#pwa-install-banner", state="visible")
    banner = page.evaluate(RECT_JS, "#pwa-install-banner")
    pill = page.evaluate(RECT_JS, "a.legal-back-to-contents")
    assert banner["width"] > 0, banner
    assert pill["display"] == "none" or not _overlap(pill, banner), (pill, banner)
    assert not page.locator("a.legal-back-to-contents").is_visible()

    # Dismissing the prompt gives the pill back.
    page.evaluate("() => window.dispatchEvent(new CustomEvent('pwa-hide-install'))")
    page.wait_for_selector("#pwa-install-banner", state="hidden")
    assert page.locator("a.legal-back-to-contents").is_visible()


def test_pill_not_covered_by_cookie_banner(page, live_server):
    page.set_viewport_size(PHONE)
    _goto(page, live_server, "/en/privacy-policy/", consent=False)
    page.wait_for_selector("#cookie-consent-banner", state="visible")
    banner = page.evaluate(RECT_JS, "#cookie-consent-banner")
    pill = page.evaluate(RECT_JS, "a.legal-back-to-contents")
    assert pill["display"] == "none" or not _overlap(pill, banner), (pill, banner)
    assert not page.locator("a.legal-back-to-contents").is_visible()

    page.evaluate("() => Alpine.store('prompts').set('cookie', false)")
    page.wait_for_selector("a.legal-back-to-contents", state="visible")


@pytest.mark.parametrize("path", ["/en/privacy-policy/", "/en/terms-of-service/"])
def test_pill_hidden_in_print(page, live_server, path):
    page.set_viewport_size(PHONE)
    _goto(page, live_server, path)
    assert page.locator("a.legal-back-to-contents").is_visible()
    page.emulate_media(media="print")
    display = page.evaluate(
        "() => getComputedStyle(document.querySelector('a.legal-back-to-contents')).display"
    )
    assert display == "none", display
    assert not page.locator("a.legal-back-to-contents").is_visible()


def test_timeline_connector_passes_through_icon_centres(page, live_server):
    page.set_viewport_size(DESKTOP)
    _goto(page, live_server, "/en/how-it-works/")
    # The icons float on an endless animation; measure their resting layout.
    page.evaluate("() => document.getAnimations().forEach((a) => a.cancel())")
    m = page.evaluate(CONNECTOR_JS)
    assert len(m["icons"]) == 4, m
    for icon in m["icons"]:
        assert abs(icon["y"] - m["top"]) <= 1.5, m
    assert abs(m["icons"][0]["x"] - m["left"]) <= 1.5, m
    assert abs(m["icons"][-1]["x"] - m["right"]) <= 1.5, m
