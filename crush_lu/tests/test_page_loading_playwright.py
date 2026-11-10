"""Playwright: the navigation progress bar and the submit guard (page-loading.js).

The full-screen loading overlay became a slim bar at the top of the page. The
overlay was also, by covering everything, what stopped a second click on a form
that was already on its way. The bar does not cover anything, so page-loading.js
now guards navigating forms explicitly. These tests answer the request with "no
content", so the page stays put, and check what a member can still do.

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_page_loading_playwright.py -n 0 --create-db
"""

import json
import re

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

SHOWN = re.compile(r"\bshow\b")
SLOW_PATH = "/__slow-target__/"

ADD_FORM = """
() => {
    const form = document.createElement('form');
    form.id = 'probe-form';
    form.method = 'get';
    form.action = '%s';
    form.innerHTML = '<button type="submit" id="probe-submit" name="go" value="1">Go</button>';
    // Pinned above the page so the hero section cannot sit on top of it.
    form.style.cssText = 'position:fixed;top:120px;left:16px;z-index:99999';
    document.body.append(form);
}
""" % SLOW_PATH


@pytest.fixture
def browser_context_args(browser_context_args):
    # A request the service worker fetches never reaches page.route().
    return {**browser_context_args, "service_workers": "block"}


@pytest.fixture
def home(page, live_server, transactional_db):
    consent = json.dumps({"essential": True, "analytics": False, "marketing": False})
    page.context.add_cookies(
        [{"name": "cookie_consent", "value": consent, "url": live_server.url}]
    )
    page.goto(f"{live_server.url}/en/")
    page.wait_for_load_state("load")
    # page-loading.js ignores a "show" in the 100ms after it last hid the bar,
    # and it hides on load. No member clicks that fast; a test does.
    page.wait_for_timeout(250)
    return page


def _hold_slow_requests(page):
    """Answer SLOW_PATH with "no content"; return the requests that reached it.

    A 204 leaves the member on the page they were on with no load or pageshow
    event, exactly like a click that turns out to be a file download. So the
    bar and the guard stay as the click left them, and Playwright can still
    read the page (it cannot while a navigation is pending).
    """
    seen = []

    def hold(route):
        seen.append(route.request.url)
        route.fulfill(status=204)

    page.route(re.compile(re.escape(SLOW_PATH)), hold)
    return seen


def test_slow_navigation_shows_a_slim_bar_that_covers_nothing(home):
    page = home
    _hold_slow_requests(page)
    page.evaluate(ADD_FORM)
    bar = page.locator("#page-loading-overlay")
    expect(bar).not_to_have_class(SHOWN)

    page.locator("#probe-submit").click(no_wait_after=True)

    expect(bar).to_have_class(SHOWN)
    box = bar.bounding_box()
    assert box["height"] <= 4
    assert bar.evaluate("el => getComputedStyle(el).pointerEvents") == "none"


def test_second_submit_is_dropped_while_the_first_is_on_its_way(home):
    page = home
    seen = _hold_slow_requests(page)
    page.evaluate(ADD_FORM)
    submit = page.locator("#probe-submit")

    submit.click(no_wait_after=True)
    expect(page.locator("#probe-form")).to_have_attribute("aria-busy", "true")
    # The button stays enabled on purpose (a disabled one drops its name/value
    # from the POST), so these clicks do reach the form.
    submit.click(no_wait_after=True)
    submit.click(no_wait_after=True)
    page.wait_for_timeout(300)

    assert len(seen) == 1
    # The clicked button's own value went out with the one request.
    assert "go=1" in seen[0]
    expect(submit).to_have_attribute("aria-busy", "true")


def test_cancelled_submit_shows_no_bar_and_does_not_arm_the_guard(home):
    page = home
    seen = _hold_slow_requests(page)
    page.evaluate(ADD_FORM)
    # A script that handles the submit itself, the way fetch-driven forms do.
    page.evaluate("""
        () => {
            window.__cancel = true;
            document.getElementById('probe-form').addEventListener('submit', e => {
                if (window.__cancel) e.preventDefault();
            });
        }
        """)
    submit = page.locator("#probe-submit")

    submit.click(no_wait_after=True)
    page.wait_for_timeout(600)
    expect(page.locator("#page-loading-overlay")).not_to_have_class(SHOWN)
    expect(page.locator("#probe-form")).not_to_have_attribute("aria-busy", "true")
    assert seen == []

    # The next, uncancelled submit goes through: the guard was never armed.
    page.evaluate("window.__cancel = false")
    submit.click(no_wait_after=True)
    page.wait_for_timeout(300)
    assert len(seen) == 1


def test_modified_click_on_a_link_shows_no_bar(home):
    page = home
    _hold_slow_requests(page)
    page.evaluate(
        """
        (path) => {
            const link = document.createElement('a');
            link.id = 'probe-link';
            link.href = path;
            link.textContent = 'Elsewhere';
            link.style.cssText = 'position:fixed;top:120px;left:16px;z-index:99999';
            document.body.append(link);
        }
        """,
        SLOW_PATH,
    )

    # Ctrl-click opens the link in another tab; this page stays where it is.
    page.locator("#probe-link").click(modifiers=["Control"], no_wait_after=True)
    page.wait_for_timeout(600)

    expect(page.locator("#page-loading-overlay")).not_to_have_class(SHOWN)
