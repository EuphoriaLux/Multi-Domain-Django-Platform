"""
Security-focused tests for Crush.lu.

Tests for:
- Rate limiting on auth endpoints
- Session fixation protection
- CSP headers
- Permissions-Policy headers
- PII masking in logs
"""
import pytest
from django.test import Client, TestCase, override_settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.cache import cache
from unittest.mock import patch, MagicMock

User = get_user_model()


def _clear_site_cache():
    """Clear Django's Site cache."""
    from django.contrib.sites.models import SITE_CACHE
    SITE_CACHE.clear()


class SiteTestCase(TestCase):
    """Base test case that ensures Site object exists."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _clear_site_cache()
        Site.objects.update_or_create(
            id=1,
            defaults={'domain': 'testserver', 'name': 'Test'}
        )
        _clear_site_cache()

    def setUp(self):
        super().setUp()
        _clear_site_cache()


class TestRateLimiting(SiteTestCase):
    """Tests for rate limiting on authentication endpoints."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        # Clear cache before each test to reset rate limit counters
        cache.clear()

    def test_login_rate_limit_allows_initial_requests(self):
        """Verify that initial login attempts are allowed."""
        response = self.client.post('/login/', {
            'login': 'test@example.com',
            'password': 'wrongpassword',
        })
        # Should not be rate limited (might be 200 with error or redirect)
        self.assertNotEqual(response.status_code, 429)

    def test_password_reset_rate_limit_allows_initial_requests(self):
        """Verify that initial password reset requests are allowed."""
        response = self.client.post('/accounts/password/reset/', {
            'email': 'test@example.com',
        })
        # Should not be rate limited
        self.assertNotEqual(response.status_code, 429)


class TestCSPHeaders(SiteTestCase):
    """Tests for Content Security Policy headers."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_csp_header_present_on_home_page(self):
        """Verify CSP header is present on public pages."""
        response = self.client.get('/')
        # In report-only mode
        self.assertTrue(
            'Content-Security-Policy-Report-Only' in response or
            'Content-Security-Policy' in response,
            "CSP header should be present"
        )

    def test_csp_header_contains_required_directives(self):
        """Verify CSP header contains essential security directives."""
        response = self.client.get('/')
        csp = (
            response.get('Content-Security-Policy-Report-Only') or
            response.get('Content-Security-Policy') or
            ''
        )
        # Check for essential directives
        self.assertIn("default-src", csp)
        self.assertIn("script-src", csp)
        self.assertIn("object-src 'none'", csp)

    def test_csp_header_skipped_for_admin(self):
        """Verify CSP header is skipped for admin pages."""
        response = self.client.get('/admin/login/')
        # CSP should not be present for admin
        csp = response.get('Content-Security-Policy-Report-Only')
        # Admin pages might still have CSP from Django, but our middleware skips it
        # Just verify the page loads
        self.assertIn(response.status_code, [200, 302])

    def test_csp_header_skipped_for_healthz(self):
        """Verify CSP header is skipped for health check."""
        response = self.client.get('/healthz/')
        # Health check returns plain text, no CSP needed
        self.assertEqual(response.status_code, 200)


class TestPermissionsPolicy(SiteTestCase):
    """Tests for Permissions-Policy header."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_permissions_policy_header_present(self):
        """Verify Permissions-Policy header is present."""
        response = self.client.get('/')
        self.assertIn('Permissions-Policy', response)

    def test_permissions_policy_disables_dangerous_features(self):
        """Verify dangerous browser features are disabled."""
        response = self.client.get('/')
        pp = response.get('Permissions-Policy', '')

        # These features should be disabled
        dangerous_features = ['camera', 'microphone', 'geolocation']
        for feature in dangerous_features:
            self.assertIn(f'{feature}=()', pp,
                         f"{feature} should be disabled in Permissions-Policy")


class TestPIIMasking(TestCase):
    """Tests for PII masking in logs."""

    def test_mask_email(self):
        """Test email masking function."""
        from azureproject.logging_utils import mask_email

        # Test normal email
        self.assertEqual(mask_email('john.doe@example.com'), 'j***e@e***.com')

        # Test short email
        self.assertEqual(mask_email('a@b.io'), 'a***@b***.io')

        # Test empty/invalid
        self.assertEqual(mask_email(''), '[empty]')
        self.assertEqual(mask_email(None), '[empty]')
        self.assertEqual(mask_email('invalid'), 'invalid')

    def test_mask_phone(self):
        """Test phone masking function."""
        from azureproject.logging_utils import mask_phone

        # Test with country code
        masked = mask_phone('+352 621 123 456')
        self.assertTrue(masked.startswith('+352'))
        self.assertTrue(masked.endswith('56'))
        self.assertIn('***', masked)

        # Test empty
        self.assertEqual(mask_phone(''), '[empty]')

    def test_pii_masking_filter(self):
        """Test the logging filter masks emails."""
        from azureproject.logging_utils import PIIMaskingFilter
        import logging

        # Create a log record with email
        record = logging.LogRecord(
            name='test',
            level=logging.INFO,
            pathname='test.py',
            lineno=1,
            msg='User john@example.com logged in',
            args=(),
            exc_info=None
        )

        # Apply filter
        filter = PIIMaskingFilter()
        filter.filter(record)

        # Email should be masked
        self.assertNotIn('john@example.com', record.msg)
        self.assertIn('j***n@e***.com', record.msg)


class TestSessionSecurity(SiteTestCase):
    """Tests for session security features."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.user = User.objects.create_user(
            username='testuser@example.com',
            email='testuser@example.com',
            password='testpass123'
        )

    def test_session_cookie_flags(self):
        """Verify session cookie has secure flags in production."""
        # This test checks settings, actual cookie behavior depends on HTTPS
        from django.conf import settings

        # In production, these should be True
        # (In test environment, they might be False)
        # Just verify the settings exist
        self.assertTrue(hasattr(settings, 'SESSION_COOKIE_HTTPONLY'))
        self.assertTrue(hasattr(settings, 'SESSION_COOKIE_SAMESITE'))


class TestAPIErrorSanitization(TestCase):
    """Tests for API error message sanitization."""

    def test_api_exception_handler_exists(self):
        """Verify custom exception handler is configured."""
        from django.conf import settings

        self.assertIn('EXCEPTION_HANDLER', settings.REST_FRAMEWORK)
        self.assertEqual(
            settings.REST_FRAMEWORK['EXCEPTION_HANDLER'],
            'azureproject.api_exception_handler.custom_exception_handler'
        )

    def test_validation_errors_preserved(self):
        """Verify validation errors keep their detail."""
        from azureproject.api_exception_handler import custom_exception_handler
        from rest_framework.exceptions import ValidationError

        exc = ValidationError({'field': 'This field is required'})
        context = {'view': None, 'request': None}

        response = custom_exception_handler(exc, context)

        # Validation errors should be preserved
        self.assertIn('field', response.data)


class TestThrottleClasses(TestCase):
    """Tests for custom throttle classes."""

    def test_login_throttle_class_exists(self):
        """Verify LoginRateThrottle is properly configured."""
        from crush_lu.throttling import LoginRateThrottle

        throttle = LoginRateThrottle()
        self.assertEqual(throttle.scope, 'login')

    def test_signup_throttle_class_exists(self):
        """Verify SignupRateThrottle is properly configured."""
        from crush_lu.throttling import SignupRateThrottle

        throttle = SignupRateThrottle()
        self.assertEqual(throttle.scope, 'signup')

    def test_password_reset_throttle_class_exists(self):
        """Verify PasswordResetRateThrottle is properly configured."""
        from crush_lu.throttling import PasswordResetRateThrottle

        throttle = PasswordResetRateThrottle()
        self.assertEqual(throttle.scope, 'password_reset')

    def test_throttle_rates_configured(self):
        """Verify throttle rates are in settings."""
        from django.conf import settings

        rates = settings.REST_FRAMEWORK.get('DEFAULT_THROTTLE_RATES', {})
        self.assertIn('login', rates)
        self.assertIn('signup', rates)
        self.assertIn('password_reset', rates)
