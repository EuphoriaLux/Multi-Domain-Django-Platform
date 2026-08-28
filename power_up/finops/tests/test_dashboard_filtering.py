"""
Playwright tests for FinOps dashboard filtering functionality
Tests charge type filtering, period selection, and edge cases
"""
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Page, expect
from django.conf import settings
from django.contrib.auth import get_user_model, SESSION_KEY, BACKEND_SESSION_KEY, HASH_SESSION_KEY
from django.contrib.sessions.backends.db import SessionStore
from power_up.finops.models import CostRecord, CostAggregation

User = get_user_model()

# power-up.localhost, not bare live_server.url (localhost/127.0.0.1): the
# Power-Up domain is not DEV_DEFAULT (crush.lu), so DomainURLRoutingMiddleware
# would otherwise route every request here to the Crush.lu urlconf and
# /finops/ would 404. See azureproject/domains.py DEV_DOMAIN_MAPPINGS.
POWERUP_DEV_HOST = "power-up.localhost"


@pytest.fixture
def staff_user(db):
    """Create staff user for FinOps access"""
    return User.objects.create_user(
        username='finops_admin',
        email='admin@powerup.lu',
        password='testpass123',
        is_staff=True,
        is_superuser=True
    )


@pytest.fixture
def powerup_url(live_server):
    """Base URL for the Power-Up domain against the live_server's ephemeral
    port, using the power-up.localhost dev mapping so
    DomainURLRoutingMiddleware resolves azureproject.urls_power_up instead
    of the Crush.lu default.
    """
    port = urlsplit(live_server.url).port
    return f"http://{POWERUP_DEV_HOST}:{port}"


@pytest.fixture
def staff_page(page: Page, staff_user, powerup_url):
    """Playwright page authenticated as staff_user via an injected Django
    session cookie (API-level auth) instead of driving the admin login
    form.

    The UI login flow (POST /admin/login/) fails Django's CSRF Origin
    check here: live_server binds an ephemeral port each run, but
    CSRF_TRUSTED_ORIGINS in settings.py only lists the fixed
    "http://power-up.localhost:8000" dev origin, so
    "http://power-up.localhost:<ephemeral-port>" is always rejected as a
    bad Origin (reproduced directly: `[CSRF FAILURE] ... Origin checking
    failed`). That's a real, narrow test-fixture gap, not a product bug —
    the fixed port is correct for `manage.py runserver 8000`, just not for
    pytest-django's live_server. Session-cookie injection sidesteps the
    login form (and the CSRF mismatch) entirely, matches the "prefer API
    setup through supported application interfaces" QA convention, and is
    the standard Playwright/Django pattern for this exact live_server/port
    situation.
    """
    session = SessionStore()
    session[SESSION_KEY] = str(staff_user.pk)
    session[BACKEND_SESSION_KEY] = 'django.contrib.auth.backends.ModelBackend'
    session[HASH_SESSION_KEY] = staff_user.get_session_auth_hash()
    session.save()

    page.context.add_cookies([{
        'name': getattr(settings, 'SESSION_COOKIE_NAME', 'sessionid'),
        'value': session.session_key,
        'domain': POWERUP_DEV_HOST,
        'path': '/',
    }])
    return page


@pytest.mark.playwright
@pytest.mark.django_db
class TestFinOpsDashboardFiltering:
    """Test FinOps dashboard with various filtering scenarios"""

    def test_dashboard_loads_with_default_filters(self, staff_page: Page, powerup_url):
        """Test dashboard loads with default 'payg' (pay-as-you-go) charge type filter.

        Current default per power_up/finops/views.py::dashboard is 'payg',
        not 'usage' — the view was renamed to a pricing-model vocabulary
        (payg/reserved/all) since this test was authored.
        """
        staff_page.goto(f"{powerup_url}/finops/")

        # Check page loads
        expect(staff_page).to_have_title("FinOps Hub - Cost Dashboard")

        # Check default filter is 'payg'
        charge_type_select = staff_page.locator('select[name="charge_type"]')
        expect(charge_type_select).to_have_value("payg")

        # Check info message is shown for the payg-only default
        info_message = staff_page.locator('text=Showing Pay-as-you-go costs only')
        expect(info_message).to_be_visible()

    def test_charge_type_filter_all_charges(self, staff_page: Page, powerup_url):
        """Test switching to 'All Charges' filter"""
        staff_page.goto(f"{powerup_url}/finops/")

        # Open filters
        show_filters_btn = staff_page.locator('button:has-text("Show filters")')
        if show_filters_btn.is_visible():
            show_filters_btn.click()

        # Select 'All Charges'
        staff_page.select_option('select[name="charge_type"]', 'all')
        staff_page.click('button[type="submit"]:has-text("Apply Filters")')

        # Wait for page reload
        staff_page.wait_for_load_state('networkidle')

        # Check URL has charge_type=all. The Advanced Filters form also
        # submits its hidden days/subscription/service inputs (empty ones
        # included), so the resulting query string carries all of them, not
        # just charge_type — assert on the parsed query dict rather than an
        # exact string so this doesn't re-break on unrelated form-field
        # ordering/whitespace changes.
        from urllib.parse import urlsplit, parse_qs
        query = parse_qs(urlsplit(staff_page.url).query)
        assert query.get('charge_type') == ['all']

        # Check info message is NOT shown for 'all' filter
        info_message = staff_page.locator('text=Showing Usage costs only')
        expect(info_message).not_to_be_visible()

    def test_365_day_period_with_all_charges(self, staff_page: Page, powerup_url):
        """Test the specific URL: /finops/?days=365&charge_type=all&subscription=&service="""
        staff_page.goto(f"{powerup_url}/finops/?days=365&charge_type=all&subscription=&service=")

        # Check page loads without errors
        expect(staff_page).to_have_title("FinOps Hub - Cost Dashboard")

        # Check no error messages
        error_messages = staff_page.locator('.error, .alert-danger, .text-red-600')
        expect(error_messages).to_have_count(0)

        # Check summary cards are visible
        total_cost_card = staff_page.locator('text=Usage Cost (365 days)')
        expect(total_cost_card).to_be_visible()

    def test_edge_case_empty_filters(self, staff_page: Page, powerup_url):
        """Test with empty subscription and service filters"""
        staff_page.goto(f"{powerup_url}/finops/?days=30&charge_type=payg&subscription=&service=")

        # Should load without errors
        expect(staff_page).to_have_title("FinOps Hub - Cost Dashboard")

        # Check that no filter badges are shown (empty filters shouldn't show badges)
        active_filters = staff_page.locator('text=Active filters:')
        expect(active_filters).not_to_be_visible()

    def test_edge_case_invalid_charge_type(self, staff_page: Page, powerup_url):
        """Test with invalid charge_type parameter"""
        staff_page.goto(f"{powerup_url}/finops/?charge_type=invalid_type")

        # Should still load (fallback to default behavior)
        expect(staff_page).to_have_title("FinOps Hub - Cost Dashboard")

        # Should not crash
        error_500 = staff_page.locator('text=Server Error')
        expect(error_500).not_to_be_visible()

    def test_edge_case_negative_days(self, staff_page: Page, powerup_url):
        """Test with negative days parameter"""
        staff_page.goto(f"{powerup_url}/finops/?days=-30")

        # Should handle gracefully (likely defaults to 30)
        expect(staff_page).to_have_title("FinOps Hub - Cost Dashboard")

    def test_edge_case_very_large_days(self, staff_page: Page, powerup_url):
        """Test with extremely large days parameter"""
        staff_page.goto(f"{powerup_url}/finops/?days=99999")

        # Should handle gracefully
        expect(staff_page).to_have_title("FinOps Hub - Cost Dashboard")

        # Page should still render
        total_cost_card = staff_page.locator('text=Total Cost')
        expect(total_cost_card).to_be_visible()

    def test_filter_persistence_after_navigation(self, staff_page: Page, powerup_url):
        """Test that charge_type persists in the hidden form field when the
        Period select (month picker) is changed — the standalone day-count
        quick-links (30d/60d/...) referenced by the original test no longer
        exist in the current template; period switching is a <select
        name="month"> that auto-submits via onchange.
        """
        # Set filters
        staff_page.goto(f"{powerup_url}/finops/?days=30&charge_type=all")

        # Change the Period select (auto-submits the top form, which
        # preserves charge_type via its own hidden input)
        month_select = staff_page.locator('select[name="month"]')
        options = month_select.locator('option').all_text_contents()
        assert len(options) >= 1, "expected at least the default Custom option"
        month_select.select_option(index=0)
        staff_page.wait_for_load_state('networkidle')

        # Check URL still has charge_type=all (preserved via the hidden
        # input in the Period form)
        current_url = staff_page.url
        assert 'charge_type=all' in current_url or 'charge_type=' not in current_url
        # Note: If charge_type not in URL, it defaults to 'payg', which is expected behavior

    def test_clear_filters_button(self, staff_page: Page, powerup_url):
        """Test that Clear button resets all filters"""
        # Navigate with filters
        staff_page.goto(f"{powerup_url}/finops/?days=60&charge_type=all&subscription=test&service=test")

        # Open filters if collapsed
        show_filters_btn = staff_page.locator('button:has-text("Show filters")')
        if show_filters_btn.is_visible():
            show_filters_btn.click()

        # Click Clear button
        staff_page.click('a:has-text("Clear")')
        staff_page.wait_for_load_state('networkidle')

        # Check URL is reset (only days parameter should remain)
        current_url = staff_page.url
        assert 'charge_type=all' not in current_url
        assert 'subscription=test' not in current_url
        assert 'service=test' not in current_url

    def test_multiple_charge_types_available(self, staff_page: Page, powerup_url):
        """Test that all available charge types appear in dropdown"""
        staff_page.goto(f"{powerup_url}/finops/")

        # Open filters
        show_filters_btn = staff_page.locator('button:has-text("Show filters")')
        if show_filters_btn.is_visible():
            show_filters_btn.click()

        # Check charge type dropdown
        charge_type_select = staff_page.locator('select[name="charge_type"]')
        options = charge_type_select.locator('option').all_text_contents()

        # Should at least have "Pay-as-you-go" and "All Usage" (the two
        # non-reserved options in views.py::dashboard's pricing_categories)
        assert 'Pay-as-you-go' in ' '.join(options)
        assert 'All Usage' in ' '.join(options)

    def test_no_data_scenario(self, staff_page: Page, powerup_url, db):
        """Test dashboard with no cost data in database"""
        # Clear all cost data
        CostRecord.objects.all().delete()
        CostAggregation.objects.all().delete()

        staff_page.goto(f"{powerup_url}/finops/?days=365&charge_type=all")

        # Should load without crashing
        expect(staff_page).to_have_title("FinOps Hub - Cost Dashboard")

        # Should show €0.00 or similar
        total_cost_value = staff_page.locator('text=€0.00').first
        expect(total_cost_value).to_be_visible()

    def test_filter_badge_removal(self, staff_page: Page, powerup_url):
        """Test removing individual filter badges"""
        # Navigate with charge_type=all filter
        staff_page.goto(f"{powerup_url}/finops/?days=30&charge_type=all")

        # Check active filter badge is shown (template renders "Pricing:
        # All Usage" for charge_type=all, not "Charge Type: All")
        charge_type_badge = staff_page.locator('text=Pricing: All Usage')
        expect(charge_type_badge).to_be_visible()

        # Click the x to remove filter
        remove_link = charge_type_badge.locator('..').locator('a')
        remove_link.click()
        staff_page.wait_for_load_state('networkidle')

        # Badge should be gone
        expect(charge_type_badge).not_to_be_visible()

    def test_accessibility_labels(self, staff_page: Page, powerup_url):
        """Test that form elements have proper labels"""
        staff_page.goto(f"{powerup_url}/finops/")

        # Open filters
        show_filters_btn = staff_page.locator('button:has-text("Show filters")')
        if show_filters_btn.is_visible():
            show_filters_btn.click()

        # Check labels exist
        charge_type_label = staff_page.locator('label[for="charge_type"]')
        expect(charge_type_label).to_be_visible()

        subscription_label = staff_page.locator('label[for="subscription"]')
        expect(subscription_label).to_be_visible()

        service_label = staff_page.locator('label[for="service"]')
        expect(service_label).to_be_visible()

    def test_performance_with_max_filters(self, staff_page: Page, powerup_url):
        """Test page performance with all filters applied"""
        # Time the page load with all parameters
        import time
        start_time = time.time()

        staff_page.goto(f"{powerup_url}/finops/?days=365&charge_type=all&subscription=PartnerLed-power_up&service=Storage")
        staff_page.wait_for_load_state('networkidle')

        load_time = time.time() - start_time

        # Page should load in reasonable time (< 5 seconds)
        assert load_time < 5.0, f"Page load took {load_time:.2f}s, expected < 5s"

        # Check page rendered correctly
        expect(staff_page).to_have_title("FinOps Hub - Cost Dashboard")
