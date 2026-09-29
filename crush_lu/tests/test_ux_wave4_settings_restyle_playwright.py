"""axe on the restyled account drill-down (UX Wave 4 · WP9c, finding 8-07).

Every account page at 390px, light and dark: no heading-order or
color-contrast violations, and one h1. Needs axe-core (AXE_CORE_PATH or
node_modules/axe-core/axe.min.js); skips without it.

Run with:
    pytest crush_lu/tests/test_ux_wave4_settings_restyle_playwright.py -m playwright
"""

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("playwright")

from django.conf import settings  # noqa: E402
from django.test import Client  # noqa: E402

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

REPO_ROOT = Path(__file__).resolve().parents[2]
DECLINED = json.dumps({"essential": True, "analytics": False, "marketing": False})
SUBS = ("", "&sub=settings", "&sub=notifications", "&sub=danger")
RULES = ["heading-order", "color-contrast", "page-has-heading-one"]


def _axe_source():
    for candidate in (
        os.environ.get("AXE_CORE_PATH"),
        REPO_ROOT / "node_modules" / "axe-core" / "axe.min.js",
    ):
        if candidate and Path(candidate).is_file():
            return Path(candidate).read_text(encoding="utf-8")
    return None


def _member():
    from crush_lu.models import CrushCoach
    from crush_lu.tests.test_profile_edit_connect_card import _make_member

    user = _make_member("restyle-pw@example.com")
    profile = user.crushprofile
    profile.phone_number = "+352621123456"
    profile.phone_verified = True
    profile.verification_status = "verified"
    profile.save()
    CrushCoach.objects.create(user=user, is_active=True)  # coach push card too
    return user


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_account_pages_pass_axe_at_390(browser, live_server, theme):
    from django.core.cache import cache

    cache.clear()
    axe = _axe_source()
    if axe is None:
        pytest.skip("axe-core not available (set AXE_CORE_PATH)")
    client = Client()
    client.force_login(_member())
    context = browser.new_context(
        viewport={"width": 390, "height": 844}, color_scheme=theme
    )
    context.route(
        "**://fonts.g*.com/**",
        lambda route: route.fulfill(status=200, content_type="text/css", body=""),
    )
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
    context.add_init_script(f"localStorage.setItem('theme', '{theme}');")
    page = context.new_page()

    found = {}
    for sub in SUBS:
        response = page.goto(f"{live_server.url}/en/profile/edit/?section=account{sub}")
        assert response is not None and response.ok, sub
        page.wait_for_function("() => window.Alpine && Alpine.store('drawer')")
        page.wait_for_timeout(400)  # push cards settle out of "Checking…"
        page.evaluate(axe)
        violations = page.evaluate(
            "async (rules) => (await axe.run(document, {runOnly: rules}))"
            ".violations.map(v => v.id + ': ' + v.nodes.map(n => n.target.join(' ')).join(' | '))",
            RULES,
        )
        if violations:
            found[sub] = violations
    context.close()
    assert found == {}
