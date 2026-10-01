"""Playwright: contrast tokens and the always-dark journey (UX Wave 2 · WP9).

``test_contrast_tokens.py`` pins the markup and CSS. Only a browser proves:

* 7-03 — theme-manager.js keeps ``.dark`` on journey / gift pages even when
  the saved preference is light, never overwrites that preference, and the
  global chrome (tab bar) takes its dark variant; the toggle is disabled.
* 5-05 — the tab-bar labels and ``.btn-crush-solid`` reach 4.5:1 in the
  rendered page (light and dark).
* An axe ``color-contrast`` pass over the touched pages, when axe-core is
  available (``AXE_CORE_PATH`` or ``node_modules/axe-core/axe.min.js``).

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_contrast_tokens_playwright.py -n 0
"""

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("playwright")

from django.conf import settings  # noqa: E402
from django.test import Client  # noqa: E402

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

PHONE = {"width": 390, "height": 844}
DECLINED = json.dumps({"essential": True, "analytics": False, "marketing": False})
REPO_ROOT = Path(__file__).resolve().parents[2]
LOCKED_LABEL = "This experience is always in night mode"

# WCAG 2.x contrast of an element's text against the nearest opaque background.
CONTRAST_JS = """
(el) => {
    const parse = (c) => c.match(/[\\d.]+/g).map(Number);
    const lum = ([r, g, b]) => {
        const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
    };
    let node = el, bg = null;
    while (node) {
        const c = getComputedStyle(node).backgroundColor;
        const v = parse(c);
        if (v.length === 3 || v[3] === 1) { bg = v; break; }
        node = node.parentElement;
    }
    const fg = lum(parse(getComputedStyle(el).color));
    const b = lum(bg || [255, 255, 255]);
    return (Math.max(fg, b) + 0.05) / (Math.min(fg, b) + 0.05);
}
"""


def _axe_source():
    for candidate in (
        os.environ.get("AXE_CORE_PATH"),
        REPO_ROOT / "node_modules" / "axe-core" / "axe.min.js",
    ):
        if candidate and Path(candidate).is_file():
            return Path(candidate).read_text(encoding="utf-8")
    return None


def _member(*, is_staff=False, email="contrast@example.com"):
    # Gift sender pages are staff/coach-only (UX Wave 4 decision C).
    from crush_lu.tests.test_profile_edit_connect_card import _make_member

    return _make_member(email, is_staff=is_staff)


def _page(browser, live_server, user, theme):
    context = browser.new_context(viewport=PHONE, color_scheme=theme)
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
        f"if (!sessionStorage.getItem('seeded')) {{"
        f" sessionStorage.setItem('seeded', '1');"
        f" localStorage.setItem('theme', '{theme}'); }}"
    )
    return context.new_page()


def _open(page, url):
    response = page.goto(url)
    assert response is not None and response.ok, response and response.status
    page.wait_for_function("() => window.Alpine && Alpine.store('prompts')")


def test_gift_page_follows_a_light_preference(browser, live_server):
    """UX Wave 5 · WP14: journey/gift pages are no longer forced navy."""
    page = _page(browser, live_server, _member(is_staff=True), "light")
    _open(page, f"{live_server.url}/en/journey/gift/create/")

    assert not page.evaluate(
        "() => document.documentElement.classList.contains('dark')"
    )
    assert page.evaluate("() => localStorage.getItem('theme')") == "light"
    body_bg = page.evaluate("() => getComputedStyle(document.body).backgroundImage")
    assert "26, 26, 46" not in body_bg  # not the navy gradient
    toggle = page.locator("[x-data='themeToggle'] button").first
    assert toggle.get_attribute("aria-disabled") is None

    # The dark preference keeps the navy page.
    page = _page(
        browser, live_server, _member(is_staff=True, email="dark@example.com"), "dark"
    )
    _open(page, f"{live_server.url}/en/journey/gift/create/")
    assert page.evaluate("() => document.documentElement.classList.contains('dark')")
    body_bg = page.evaluate("() => getComputedStyle(document.body).backgroundImage")
    assert "26, 26, 46" in body_bg


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_tab_bar_and_solid_button_reach_aa(browser, live_server, theme):
    page = _page(browser, live_server, _member(), theme)
    _open(page, f"{live_server.url}/en/dashboard/")

    labels = page.locator(
        "nav.bottom-nav .bottom-nav-item > span:not(.bottom-nav-badge)"
    )
    assert labels.count() >= 4
    for i in range(labels.count()):
        label = labels.nth(i)
        assert label.evaluate("(el) => getComputedStyle(el).fontSize") == "11px"
        ratio = label.evaluate(CONTRAST_JS)
        assert ratio >= 4.5, (theme, label.inner_text(), ratio)

    button = page.locator(".btn-crush-solid").first
    ratio = button.evaluate(CONTRAST_JS)
    assert ratio >= 4.5, (theme, ratio)


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize(
    "path", ["/en/connections/", "/en/journey/gift/create/", "/en/journey/gifts/"]
)
def test_axe_color_contrast_is_clean(browser, live_server, theme, path):
    axe = _axe_source()
    if axe is None:
        pytest.skip("axe-core not available (set AXE_CORE_PATH)")
    page = _page(browser, live_server, _member(is_staff="/gift" in path), theme)
    _open(page, f"{live_server.url}{path}")
    page.wait_for_timeout(300)
    page.evaluate(axe)
    nodes = page.evaluate(
        "async () => (await axe.run(document, {runOnly: ['color-contrast']}))"
        ".violations.flatMap(v => v.nodes.map(n => n.target.join(' ')))"
    )
    assert nodes == [], nodes


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_axe_dashboard_touched_elements_are_clean(browser, live_server, theme):
    axe = _axe_source()
    if axe is None:
        pytest.skip("axe-core not available (set AXE_CORE_PATH)")
    page = _page(browser, live_server, _member(), theme)
    _open(page, f"{live_server.url}/en/dashboard/")
    page.wait_for_timeout(300)
    page.evaluate(axe)
    nodes = page.evaluate(
        "async () => (await axe.run({include: [['nav.bottom-nav'], ['.btn-crush-solid'],"
        " ['.text-muted-fg']]}, {runOnly: ['color-contrast']}))"
        ".violations.flatMap(v => v.nodes.map(n => n.target.join(' ')))"
    )
    assert nodes == [], nodes


REWARD_TYPES = ["future_letter", "poem", "photo_slideshow", "voice_message"]

LUMINANCE_JS = """
(el) => {
    const v = getComputedStyle(el).backgroundColor.match(/[\\d.]+/g).map(Number);
    const f = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(v[0]) + 0.7152 * f(v[1]) + 0.0722 * f(v[2]);
}
"""


def _reward_player(name):
    """A member whose completed chapter holds one reward of each type."""
    from django.utils import timezone

    from crush_lu.models import ChapterProgress, JourneyReward
    from crush_lu.tests.test_journey_api_scoping import _make_player

    player = _make_player(name, "Well done", points=100)
    ChapterProgress.objects.create(
        journey_progress=player.progress,
        chapter=player.chapter,
        is_completed=True,
        completed_at=timezone.now(),
    )
    rewards = {
        kind: JourneyReward.objects.create(
            chapter=player.chapter,
            reward_type=kind,
            title=f"{kind} title",
            message="Dear you,\n\nThis is the body of the message.",
        )
        for kind in REWARD_TYPES
    }
    return player.user, rewards


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_letter_paper_is_white_and_reward_pages_pass_axe(browser, live_server, theme):
    """Review fix: --jy-w is purple in light mode, which had turned the
    letter paper into a purple card with invisible title and signature."""
    user, rewards = _reward_player(f"Rw{theme}")
    page = _page(browser, live_server, user, theme)
    # prefers-reduced-motion shows the paper at once (no 2s slide-in delay).
    page.emulate_media(color_scheme=theme, reduced_motion="reduce")
    axe = _axe_source()
    for kind, reward in rewards.items():
        page.goto(f"{live_server.url}/en/journey/reward/{reward.id}/")
        page.wait_for_load_state("load")
        page.wait_for_timeout(300)
        if kind == "future_letter":
            paper = page.locator(".letter-paper")
            assert paper.count() == 1
            assert paper.evaluate(LUMINANCE_JS) > 0.85, theme
            for sel in (".letter-date", ".letter-signature"):
                ratio = page.locator(sel).first.evaluate(CONTRAST_JS)
                assert ratio >= 3.0, (theme, sel, ratio)
        if axe is None:
            continue
        page.evaluate(axe)
        nodes = page.evaluate(
            "async () => (await axe.run(document, {runOnly: ['color-contrast']}))"
            ".violations.flatMap(v => v.nodes.map(n => n.target.join(' ')))"
        )
        assert nodes == [], (kind, theme, nodes)
