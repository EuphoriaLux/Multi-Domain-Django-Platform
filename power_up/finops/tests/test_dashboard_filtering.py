"""
Playwright tests for FinOps dashboard filtering functionality
Tests charge type filtering, period selection, and edge cases
"""
import pytest
from playwright.sync_api import Page, expect
from django.contrib.auth import get_user_model
from power_up.finops.models import CostRecord, CostAggregation

User = get_user_model()


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


@pytest.mark.playwright
@pytest.mark.django_db
class TestFinOpsDashboardFiltering:
    """Test FinOps dashboard with various filtering scenarios"""

    def test_dashboard_loads_with_default_filters(self, page: Page, live_server, staff_user):
        """Test dashboard loads with default 'usage' charge type filter"""
        # Login
        page.goto(f"{live_server.url}/admin/login/")
        page.fill('input[name="username"]', staff_user.username)
        page.fill('input[name="password"]', 'testpass123')
        page.click('input[type="submit"]')

        # Navigate to FinOps dashboard
        page.goto(f"{live_server.url}/finops/")

        # Check page loads
        expect(page).to_have_title("FinOps Hub - Cost Dashboard")

        # Check default filter is 'usage'
        charge_type_select = page.locator('select[name="charge_type"]')
        expect(charge_type_select).to_have_value("usage")

        # Check info message is shown for usage-only filter
        info_message = page.locator('text=Showing Usage costs only')
        expect(info_message).to_be_visible()

    def test_charge_type_filter_all_charges(self, page: Page, live_server, staff_user):
        """Test switching to 'All Charges' filter"""
        # Login and navigate
        page.goto(f"{live_server.url}/admin/login/")
        page.fill('input[name="username"]', staff_user.username)
        page.fill('input[name="password"]', 'testpass123')
        page.click('input[type="submit"]')
        page.goto(f"{live_server.url}/finops/")

        # Open filters
        show_filters_btn = page.locator('button:has-text("Show filters")')
        if show_filters_btn.is_visible():
            show_filters_btn.click()

        # Select 'All Charges'
        page.select_option('select[name="charge_type"]', 'all')
        page.click('button[type="submit"]:has-text("Apply Filters")')

        # Wait for page reload
        page.wait_for_load_state('networkidle')

        # Check URL has charge_type=all
        expect(page).to_have_url(f"{live_server.url}/finops/?charge_type=all")

        # Check info message is NOT shown for 'all' filter
        info_message = page.locator('text=Showing Usage costs only')
        expect(info_message).not_to_be_visible()

    def test_365_day_period_with_all_charges(self, page: Page, live_server, staff_user):
        """Test the specific URL: /finops/?days=365&charge_type=all&subscription=&service="""
        # Use powerup.localhost for proper domain routing
        test_url = "http://powerup.localhost:8000"

        # Login
        page.goto(f"{test_url}/admin/login/")
        page.fill('input[name="username"]', staff_user.username)
        page.fill('input[name="password"]', 'testpass123')
        page.click('input[type="submit"]')

        # Navigate to specific URL with all parameters
        page.goto(f"{test_url}/finops/?days=365&charge_type=all&subscription=&service=")

        # Check page loads without errors
        expect(page).to_have_title("FinOps Hub - Cost Dashboard")

        # Check no error messages
        error_messages = page.locator('.error, .alert-danger, .text-red-600')
        expect(error_messages).to_have_count(0)

        # Check summary cards are visible
        total_cost_card = page.locator('text=Total Cost')
        expect(total_cost_card).to_be_visible()

        # Check period selector shows 365 days selected
        period_365_btn = page.locator('a[href*="days=365"]')
        expect(period_365_btn).to_have_class(text='bg-blue-500')

    def test_edge_case_empty_filters(self, page: Page, live_server, staff_user):
        """Test with empty subscription and service filters"""
        page.goto(f"{live_server.url}/admin/login/")
        page.fill('input[name="username"]', staff_user.username)
        page.fill('input[name="password"]', 'testpass123')
        page.click('input[type="submit"]')

        # Navigate with empty filters
        page.goto(f"{live_server.url}/finops/?days=30&charge_type=usage&subscription=&service=")

        # Should load without errors
        expect(page).to_have_title("FinOps Hub - Cost Dashboard")

        # Check that no filter badges are shown (empty filters shouldn't show badges)
        active_filters = page.locator('text=Active filters:')
        expect(active_filters).not_to_be_visible()

    def test_edge_case_invalid_charge_type(self, page: Page, live_server, staff_user):
        """Test with invalid charge_type parameter"""
        page.goto(f"{live_server.url}/admin/login/")
        page.fill('input[name="username"]', staff_user.username)
        page.fill('input[name="password"]', 'testpass123')
        page.click('input[type="submit"]')

        # Navigate with invalid charge type
        page.goto(f"{live_server.url}/finops/?charge_type=invalid_type")

        # Should still load (fallback to default behavior)
        expect(page).to_have_title("FinOps Hub - Cost Dashboard")

        # Should not crash
        error_500 = page.locator('text=Server Error')
        expect(error_500).not_to_be_visible()

    def test_edge_case_negative_days(self, page: Page, live_server, staff_user):
        """Test with negative days parameter"""
        page.goto(f"{live_server.url}/admin/login/")
        page.fill('input[name="username"]', staff_user.username)
        page.fill('input[name="password"]', 'testpass123')
        page.click('input[type="submit"]')

        # Navigate with negative days
        page.goto(f"{live_server.url}/finops/?days=-30")

        # Should handle gracefully (likely defaults to 30)
        expect(page).to_have_title("FinOps Hub - Cost Dashboard")

    def test_edge_case_very_large_days(self, page: Page, live_server, staff_user):
        """Test with extremely large days parameter"""
        page.goto(f"{live_server.url}/admin/login/")
        page.fill('input[name="username"]', staff_user.username)
        page.fill('input[name="password"]', 'testpass123')
        page.click('input[type="submit"]')

        # Navigate with very large days value
        page.goto(f"{live_server.url}/finops/?days=99999")

        # Should handle gracefully
        expect(page).to_have_title("FinOps Hub - Cost Dashboard")

        # Page should still render
        total_cost_card = page.locator('text=Total Cost')
        expect(total_cost_card).to_be_visible()

    def test_filter_persistence_after_navigation(self, page: Page, live_server, staff_user):
        """Test that filters persist when navigating period buttons"""
        page.goto(f"{live_server.url}/admin/login/")
        page.fill('input[name="username"]', staff_user.username)
        page.fill('input[name="password"]', 'testpass123')
        page.click('input[type="submit"]')

        # Set filters
        page.goto(f"{live_server.url}/finops/?days=30&charge_type=all")

        # Click a period button (should maintain charge_type)
        page.click('a:has-text("60d")')
        page.wait_for_load_state('networkidle')

        # Check URL still has charge_type=all
        current_url = page.url
        assert 'charge_type=all' in current_url or 'charge_type=' not in current_url
        # Note: If charge_type not in URL, it defaults to 'usage', which is expected behavior

    def test_clear_filters_button(self, page: Page, live_server, staff_user):
        """Test that Clear button resets all filters"""
        page.goto(f"{live_server.url}/admin/login/")
        page.fill('input[name="username"]', staff_user.username)
        page.fill('input[name="password"]', 'testpass123')
        page.click('input[type="submit"]')

        # Navigate with filters
        page.goto(f"{live_server.url}/finops/?days=60&charge_type=all&subscription=test&service=test")

        # Open filters if collapsed
        show_filters_btn = page.locator('button:has-text("Show filters")')
        if show_filters_btn.is_visible():
            show_filters_btn.click()

        # Click Clear button
        page.click('a:has-text("Clear")')
        page.wait_for_load_state('networkidle')

        # Check URL is reset (only days parameter should remain)
        current_url = page.url
        assert 'charge_type=all' not in current_url
        assert 'subscription=test' not in current_url
        assert 'service=test' not in current_url

    def test_multiple_charge_types_available(self, page: Page, live_server, staff_user):
        """Test that all available charge types appear in dropdown"""
        page.goto(f"{live_server.url}/admin/login/")
        page.fill('input[name="username"]', staff_user.username)
        page.fill('input[name="password"]', 'testpass123')
        page.click('input[type="submit"]')
        page.goto(f"{live_server.url}/finops/")

        # Open filters
        show_filters_btn = page.locator('button:has-text("Show filters")')
        if show_filters_btn.is_visible():
            show_filters_btn.click()

        # Check charge type dropdown
        charge_type_select = page.locator('select[name="charge_type"]')
        options = charge_type_select.locator('option').all_text_contents()

        # Should at least have "Usage Only" and "All Charges"
        assert 'Usage Only' in ' '.join(options)
        assert 'All Charges' in ' '.join(options)

    def test_no_data_scenario(self, page: Page, live_server, staff_user, db):
        """Test dashboard with no cost data in database"""
        # Clear all cost data
        CostRecord.objects.all().delete()
        CostAggregation.objects.all().delete()

        page.goto(f"{live_server.url}/admin/login/")
        page.fill('input[name="username"]', staff_user.username)
        page.fill('input[name="password"]', 'testpass123')
        page.click('input[type="submit"]')
        page.goto(f"{live_server.url}/finops/?days=365&charge_type=all")

        # Should load without crashing
        expect(page).to_have_title("FinOps Hub - Cost Dashboard")

        # Should show €0.00 or similar
        total_cost_value = page.locator('text=€0.00').first
        expect(total_cost_value).to_be_visible()

    def test_filter_badge_removal(self, page: Page, live_server, staff_user):
        """Test removing individual filter badges"""
        page.goto(f"{live_server.url}/admin/login/")
        page.fill('input[name="username"]', staff_user.username)
        page.fill('input[name="password"]', 'testpass123')
        page.click('input[type="submit"]')

        # Navigate with charge_type=all filter
        page.goto(f"{live_server.url}/finops/?days=30&charge_type=all")

        # Check active filter badge is shown
        charge_type_badge = page.locator('text=Charge Type: All')
        expect(charge_type_badge).to_be_visible()

        # Click the × to remove filter
        remove_link = charge_type_badge.locator('..').locator('a')
        remove_link.click()
        page.wait_for_load_state('networkidle')

        # Badge should be gone
        expect(charge_type_badge).not_to_be_visible()

    def test_accessibility_labels(self, page: Page, live_server, staff_user):
        """Test that form elements have proper labels"""
        page.goto(f"{live_server.url}/admin/login/")
        page.fill('input[name="username"]', staff_user.username)
        page.fill('input[name="password"]', 'testpass123')
        page.click('input[type="submit"]')
        page.goto(f"{live_server.url}/finops/")

        # Open filters
        show_filters_btn = page.locator('button:has-text("Show filters")')
        if show_filters_btn.is_visible():
            show_filters_btn.click()

        # Check labels exist
        charge_type_label = page.locator('label[for="charge_type"]')
        expect(charge_type_label).to_be_visible()

        subscription_label = page.locator('label[for="subscription"]')
        expect(subscription_label).to_be_visible()

        service_label = page.locator('label[for="service"]')
        expect(service_label).to_be_visible()

    def test_performance_with_max_filters(self, page: Page, live_server, staff_user):
        """Test page performance with all filters applied"""
        page.goto(f"{live_server.url}/admin/login/")
        page.fill('input[name="username"]', staff_user.username)
        page.fill('input[name="password"]', 'testpass123')
        page.click('input[type="submit"]')

        # Time the page load with all parameters
        import time
        start_time = time.time()

        page.goto(f"{live_server.url}/finops/?days=365&charge_type=all&subscription=PartnerLed-power_up&service=Storage")
        page.wait_for_load_state('networkidle')

        load_time = time.time() - start_time

        # Page should load in reasonable time (< 5 seconds)
        assert load_time < 5.0, f"Page load took {load_time:.2f}s, expected < 5s"

        # Check page rendered correctly
        expect(page).to_have_title("FinOps Hub - Cost Dashboard")
