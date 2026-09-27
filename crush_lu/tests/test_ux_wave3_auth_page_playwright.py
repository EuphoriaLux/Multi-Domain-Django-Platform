"""
Playwright: browser-level behaviour for UX Wave 3 WP2 (auth page) that a
source-grep can't prove — the ARIA tab switch, the bfcache pageshow reset,
and the pointer-gated autofocus.

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_ux_wave3_auth_page_playwright.py -n 0 --create-db
"""

import pytest
from django.contrib.sites.models import Site
from django.core.cache import cache

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


@pytest.fixture
def google_social_app(transactional_db):
    """A Google SocialApp so the auth page renders a social login button.

    See test_luxid_primary_ui.py's _SiteMixin for why every Site gets it:
    allauth filters providers by the *current* site, and which Site id
    that resolves to differs by test runner.
    """
    from allauth.socialaccount.models import SocialApp

    Site.objects.get_or_create(
        id=1, defaults={"domain": "testserver", "name": "Test Server"}
    )
    app = SocialApp.objects.create(
        provider="google", name="Google", client_id="g", secret="g"
    )
    app.sites.set(Site.objects.all())
    return app


def test_tab_click_switches_aria_selected_and_panel_visibility(page, live_server):
    page.goto(f"{live_server.url}/en/login/")
    login_tab = page.locator("#auth-tab-login")
    signup_tab = page.locator("#auth-tab-signup")
    # Alpine binds aria-selected after init; wait for it rather than racing.
    page.wait_for_function(
        "document.getElementById('auth-tab-login').getAttribute('aria-selected') !== null"
    )
    assert login_tab.get_attribute("aria-selected") == "true"
    assert signup_tab.get_attribute("aria-selected") == "false"

    signup_tab.click()

    assert signup_tab.get_attribute("aria-selected") == "true"
    assert login_tab.get_attribute("aria-selected") == "false"
    # x-transition takes 300ms to fade the panel in; wait for the visible
    # state rather than racing Alpine's DOM update.
    page.locator("#signup-panel").wait_for(state="visible")
    page.locator("#login-panel").wait_for(state="hidden")


_BLOCK_SOCIAL_NAVIGATION_JS = """
// The click handler in auth.html doesn't preventDefault on desktop (a real
// redirect to allauth's OAuth entry point is the point) — a real
// navigation would unload this document before the test can inspect its
// aria-busy state. A capturing document-level listener runs before any
// bubble-phase listener the page's own script attaches to the button, so
// calling preventDefault() here cancels the navigation regardless of
// registration order.
document.addEventListener('click', function (e) {
    if (e.target.closest('.social-login-btn')) e.preventDefault();
}, true);
"""


def test_social_button_shows_busy_and_keeps_its_logo(
    page, live_server, google_social_app
):
    """2-05: clicking a social button no longer overwrites its textContent,
    so the provider logo/icon stays in the DOM while aria-busy is set."""
    page.add_init_script(_BLOCK_SOCIAL_NAVIGATION_JS)
    page.goto(f"{live_server.url}/en/login/")
    google_btn = page.locator(".social-login-btn.google-btn")
    google_btn.first.locator("svg").wait_for(state="attached")
    google_btn.first.click()
    assert google_btn.first.get_attribute("aria-busy") == "true"
    # The logo is still in the DOM — a textContent overwrite would remove it.
    assert google_btn.first.locator("svg").count() == 1


def test_pageshow_persisted_resets_busy_state(page, live_server, google_social_app):
    """2-05: simulating a bfcache restore (back-out of the provider consent
    screen) clears the busy/disabled state a real click left behind."""
    page.add_init_script(_BLOCK_SOCIAL_NAVIGATION_JS)
    page.goto(f"{live_server.url}/en/login/")
    google_btn = page.locator(".social-login-btn.google-btn")
    google_btn.first.click()
    assert google_btn.first.get_attribute("aria-busy") == "true"

    page.evaluate(
        "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"
    )
    assert google_btn.first.get_attribute("aria-busy") is None


def test_autofocus_focuses_the_email_field_on_a_fine_pointer(page, live_server):
    """2-08 positive case: on a normal desktop/mouse context, the field
    marked data-autofocus-candidate IS focused — proves the JS actually
    runs the focus(), not just that the literal `autofocus` attribute (an
    entirely separate mechanism the test below can't distinguish from a
    JS bug that never focuses anything) is gone."""
    page.goto(f"{live_server.url}/en/login/")
    page.wait_for_function(
        "document.activeElement && document.activeElement.id === 'id_login'"
    )
    assert page.evaluate("document.activeElement.id") == "id_login"


def test_failed_signup_focuses_first_invalid_field(page, live_server):
    """2-09: after a server-rendered signup error (duplicate email), the
    first field marked aria-invalid="true" receives focus on load — for
    keyboard and screen-reader users who would otherwise only see red text
    appear somewhere on the page."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    User.objects.create_user(
        username="wave3-dupe@example.com",
        email="wave3-dupe@example.com",
        password="Str0ng-pass-2026!",
    )
    page.goto(f"{live_server.url}/en/signup/")
    page.locator("#id_first_name").fill("Dup")
    page.locator("#id_email").fill("wave3-dupe@example.com")
    page.locator("#id_password1").fill("Str0ng-pass-2026!")
    page.locator("#id_password2").fill("Str0ng-pass-2026!")
    page.locator("#id_crushlu_consent").check()
    page.locator("#signup-submit-btn").click()

    page.wait_for_selector('#signup-panel [aria-invalid="true"]')
    first_invalid_id = page.eval_on_selector(
        '#signup-panel [aria-invalid="true"]', "el => el.id"
    )
    page.wait_for_function(
        "id => document.activeElement && document.activeElement.id === id",
        arg=first_invalid_id,
    )
    assert page.evaluate("document.activeElement.id") == first_invalid_id


def test_autofocus_is_skipped_on_a_coarse_touch_pointer(page, live_server):
    """2-08: on a touch/mobile context, matchMedia('(pointer: fine)') is
    false, so the email field is never auto-focused (no keyboard popping
    over the LuxID/Google buttons)."""
    context = page.context
    mobile_page = context.browser.new_context(
        viewport={"width": 390, "height": 844},
        has_touch=True,
        is_mobile=True,
    ).new_page()
    try:
        mobile_page.goto(f"{live_server.url}/en/login/")
        mobile_page.wait_for_timeout(100)
        focused_id = mobile_page.evaluate(
            "document.activeElement && document.activeElement.id"
        )
        assert focused_id != "id_login"
    finally:
        mobile_page.close()
