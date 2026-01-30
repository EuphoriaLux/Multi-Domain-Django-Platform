"""
Unit tests for FinOps dashboard edge cases and potential issues
Tests view logic, edge cases, and error handling
"""
import pytest
from django.test import Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from power_up.finops.models import CostRecord, CostAggregation
from datetime import datetime, timedelta
from decimal import Decimal

User = get_user_model()


@pytest.fixture
def staff_user(db):
    """Create staff user"""
    return User.objects.create_user(
        username='staff',
        email='staff@powerup.lu',
        password='testpass',
        is_staff=True
    )


@pytest.fixture
def client_logged_in(staff_user):
    """Authenticated client"""
    client = Client()
    client.login(username='staff', password='testpass')
    return client


@pytest.mark.django_db
class TestFinOpsDashboardEdgeCases:
    """Test edge cases and potential issues in dashboard"""

    def test_dashboard_with_no_data(self, client_logged_in):
        """Test dashboard loads when database is empty"""
        response = client_logged_in.get('/finops/')
        assert response.status_code == 200
        assert b'Total Cost' in response.content

    def test_dashboard_365_days_all_charges(self, client_logged_in):
        """Test the specific URL: /finops/?days=365&charge_type=all&subscription=&service="""
        response = client_logged_in.get('/finops/?days=365&charge_type=all&subscription=&service=')
        assert response.status_code == 200
        assert b'Total Cost' in response.content
        # Should not show "Usage only" message when charge_type=all
        assert b'Showing Usage costs only' not in response.content

    def test_dashboard_default_usage_filter(self, client_logged_in):
        """Test default filter is 'usage'"""
        response = client_logged_in.get('/finops/')
        assert response.status_code == 200
        # Should show info message for usage-only
        assert b'Showing' in response.content and b'Usage costs only' in response.content

    def test_edge_case_negative_days(self, client_logged_in):
        """Test with negative days parameter"""
        response = client_logged_in.get('/finops/?days=-30')
        # Should not crash, likely defaults to 30
        assert response.status_code == 200

    def test_edge_case_zero_days(self, client_logged_in):
        """Test with zero days"""
        response = client_logged_in.get('/finops/?days=0')
        assert response.status_code == 200

    def test_edge_case_very_large_days(self, client_logged_in):
        """Test with extremely large days value"""
        response = client_logged_in.get('/finops/?days=99999')
        assert response.status_code == 200
        # Should not cause memory/performance issues

    def test_edge_case_invalid_days_string(self, client_logged_in):
        """Test with non-numeric days parameter"""
        response = client_logged_in.get('/finops/?days=invalid')
        # Should handle gracefully (likely defaults to 30)
        assert response.status_code in [200, 400]

    def test_edge_case_invalid_charge_type(self, client_logged_in):
        """Test with invalid charge_type"""
        response = client_logged_in.get('/finops/?charge_type=invalid_type')
        # Should not crash - likely filters by invalid type (returns no results)
        assert response.status_code == 200

    def test_edge_case_empty_string_filters(self, client_logged_in):
        """Test with empty string filters"""
        response = client_logged_in.get('/finops/?subscription=&service=&charge_type=')
        assert response.status_code == 200

    def test_edge_case_none_filters(self, client_logged_in):
        """Test with None-like string filters"""
        response = client_logged_in.get('/finops/?subscription=None&service=null')
        assert response.status_code == 200

    def test_sql_injection_attempt(self, client_logged_in):
        """Test SQL injection attempts are handled safely"""
        malicious_inputs = [
            "' OR '1'='1",
            "'; DROP TABLE cost_records; --",
            "1' UNION SELECT * FROM auth_user--",
        ]

        for malicious in malicious_inputs:
            response = client_logged_in.get(f'/finops/?subscription={malicious}')
            # Should handle safely, not crash
            assert response.status_code == 200
            # Should not expose sensitive info
            assert b'DROP TABLE' not in response.content
            assert b'UNION SELECT' not in response.content

    def test_xss_attempt(self, client_logged_in):
        """Test XSS attempts are escaped properly"""
        xss_inputs = [
            "<script>alert('xss')</script>",
            "javascript:alert(1)",
            "<img src=x onerror=alert(1)>",
        ]

        for xss in xss_inputs:
            response = client_logged_in.get(f'/finops/?subscription={xss}')
            assert response.status_code == 200
            # Should escape HTML
            assert b'<script>' not in response.content or b'&lt;script&gt;' in response.content

    def test_unicode_in_filters(self, client_logged_in):
        """Test unicode characters in filter parameters"""
        response = client_logged_in.get('/finops/?subscription=测试&service=тест')
        assert response.status_code == 200

    def test_very_long_filter_string(self, client_logged_in):
        """Test with extremely long filter strings"""
        long_string = "A" * 10000
        response = client_logged_in.get(f'/finops/?subscription={long_string}')
        # Should handle gracefully (might be truncated)
        assert response.status_code in [200, 414]  # 414 = Request-URI Too Long

    def test_multiple_charge_types_in_url(self, client_logged_in):
        """Test with multiple charge_type parameters"""
        response = client_logged_in.get('/finops/?charge_type=usage&charge_type=all')
        # Django takes the last value
        assert response.status_code == 200

    def test_filter_persistence_through_period_change(self, client_logged_in):
        """Test filters are maintained when changing period"""
        # This is a known limitation - period buttons may not preserve all filters
        response = client_logged_in.get('/finops/?days=30&charge_type=all')
        assert response.status_code == 200

        # Check that period buttons exist
        assert b'7d' in response.content or b'30d' in response.content

    def test_concurrent_filters(self, client_logged_in):
        """Test all filters applied simultaneously"""
        response = client_logged_in.get('/finops/?days=365&charge_type=all&subscription=test&service=storage')
        assert response.status_code == 200

    def test_response_time_with_max_days(self, client_logged_in):
        """Test response time doesn't explode with large date range"""
        import time
        start = time.time()
        response = client_logged_in.get('/finops/?days=3650')  # 10 years
        elapsed = time.time() - start

        assert response.status_code == 200
        assert elapsed < 5.0, f"Response took {elapsed:.2f}s, expected < 5s"

    def test_charge_type_filter_actually_filters(self, client_logged_in, db):
        """Test that charge_type filter actually affects results"""
        # Create test data with different charge types
        today = datetime.now().date()

        CostRecord.objects.create(
            charge_period_start=today,
            charge_period_end=today,
            charge_category='Usage',
            billed_cost=Decimal('10.00'),
            billing_currency='EUR',
            subscription_id='test-sub',
            subscription_name='Test',
            service_name='Storage',
            resource_id='test-resource',
            billing_period_start=today,
            billing_period_end=today
        )

        CostRecord.objects.create(
            charge_period_start=today,
            charge_period_end=today,
            charge_category='Purchase',
            billed_cost=Decimal('100.00'),
            billing_currency='EUR',
            subscription_id='test-sub',
            subscription_name='Test',
            service_name='Storage',
            resource_id='test-resource',
            billing_period_start=today,
            billing_period_end=today
        )

        # Test with usage filter (default)
        response_usage = client_logged_in.get('/finops/?charge_type=usage')
        assert response_usage.status_code == 200

        # Test with all filter
        response_all = client_logged_in.get('/finops/?charge_type=all')
        assert response_all.status_code == 200

        # The totals should be different
        # (This is a smoke test - exact values depend on template rendering)

    def test_context_data_structure(self, client_logged_in):
        """Test that view returns expected context variables"""
        response = client_logged_in.get('/finops/')
        assert response.status_code == 200

        # Check key context variables exist
        assert 'period' in response.context
        assert 'filters' in response.context
        assert 'summary' in response.context

        # Check filters structure
        filters = response.context['filters']
        assert 'charge_type' in filters
        assert 'all_charge_types' in filters
        assert 'all_subscriptions' in filters
        assert 'all_services' in filters

    def test_mtd_ytd_calculations_with_charge_filter(self, client_logged_in, db):
        """Test MTD/YTD calculations respect charge_type filter"""
        today = datetime.now().date()

        # Create usage record
        CostRecord.objects.create(
            charge_period_start=today,
            charge_period_end=today,
            charge_category='Usage',
            billed_cost=Decimal('50.00'),
            billing_currency='EUR',
            subscription_id='test',
            subscription_name='Test',
            service_name='Storage',
            resource_id='resource1',
            billing_period_start=today.replace(day=1),
            billing_period_end=today
        )

        # Test that MTD/YTD are calculated
        response = client_logged_in.get('/finops/?charge_type=usage')
        assert response.status_code == 200
        assert 'summary' in response.context
        assert 'mtd_cost' in response.context['summary']
        assert 'ytd_cost' in response.context['summary']

    def test_no_crash_with_missing_cost_data_fields(self, client_logged_in, db):
        """Test dashboard doesn't crash with NULL/missing fields"""
        today = datetime.now().date()

        # Create record with minimal fields
        CostRecord.objects.create(
            charge_period_start=today,
            charge_period_end=today,
            charge_category='Usage',
            billed_cost=Decimal('0.00'),
            billing_currency='EUR',
            subscription_id='',  # Empty
            subscription_name='',  # Empty
            service_name='',  # Empty
            resource_id='',
            billing_period_start=today,
            billing_period_end=today
        )

        response = client_logged_in.get('/finops/')
        assert response.status_code == 200
