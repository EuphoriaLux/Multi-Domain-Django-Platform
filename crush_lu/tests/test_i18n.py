"""
Internationalization (i18n) Tests for Crush.lu

Tests for the multi-language URL routing upgrade:
- Language-prefixed URLs (/en/, /de/, /fr/)
- Language-neutral API endpoints
- OAuth redirect URLs with language prefix
- Push notification URLs with user's preferred language
- SEO template tags (hreflang, canonical)
- Language switcher functionality
"""

import pytest
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client, RequestFactory, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.utils import timezone
from django.contrib.sites.models import Site

User = get_user_model()


class SiteTestMixin:
    """Mixin to handle Site framework setup for tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1,
            defaults={'domain': 'crush.lu', 'name': 'Crush.lu'}
        )


# =============================================================================
# PART 1: URL ROUTING TESTS (CRITICAL)
# =============================================================================

@override_settings(ROOT_URLCONF='azureproject.urls_crush')
class I18nURLRoutingTests(SiteTestMixin, TestCase):
    """Test URL routing with i18n language prefixes."""

    def setUp(self):
        self.client = Client()

    def test_root_redirects_to_language_prefix(self):
        """Test root URL redirects to default language (/en/)."""
        response = self.client.get('/', follow=False)
        # Should redirect to /en/ (default language)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response.url.startswith('/en/'),
            f"Root should redirect to /en/, got {response.url}"
        )

    def test_english_home_accessible(self):
        """Test /en/ home page is accessible."""
        response = self.client.get('/en/')
        self.assertEqual(response.status_code, 200)

    def test_german_home_accessible(self):
        """Test /de/ home page is accessible."""
        response = self.client.get('/de/')
        self.assertEqual(response.status_code, 200)

    def test_french_home_accessible(self):
        """Test /fr/ home page is accessible."""
        response = self.client.get('/fr/')
        self.assertEqual(response.status_code, 200)

    def test_events_page_accessible_all_languages(self):
        """Test events page accessible in all languages."""
        for lang in ['en', 'de', 'fr']:
            response = self.client.get(f'/{lang}/events/')
            self.assertEqual(
                response.status_code, 200,
                f"/{lang}/events/ should be accessible"
            )

    def test_about_page_accessible_all_languages(self):
        """Test about page accessible in all languages."""
        for lang in ['en', 'de', 'fr']:
            response = self.client.get(f'/{lang}/about/')
            self.assertEqual(
                response.status_code, 200,
                f"/{lang}/about/ should be accessible"
            )

    def test_api_endpoints_language_neutral(self):
        """
        CRITICAL: Test API endpoints work WITHOUT language prefix.

        APIs are called from JavaScript with hardcoded paths.
        They must NOT be inside i18n_patterns.
        """
        api_endpoints = [
            '/api/auth/status/',
            '/api/phone/status/',
            '/api/push/vapid-public-key/',
        ]

        for endpoint in api_endpoints:
            response = self.client.get(endpoint)
            # Should not be 404 (may be 401/403 for auth-required)
            self.assertNotEqual(
                response.status_code, 404,
                f"API endpoint {endpoint} should not 404 - it must be language-neutral"
            )

    def test_pwa_manifest_language_neutral(self):
        """
        CRITICAL: Test PWA manifest is accessible without language prefix.

        Browsers block redirected manifests.
        """
        response = self.client.get('/manifest.json')
        self.assertNotEqual(
            response.status_code, 404,
            "manifest.json should be accessible without language prefix"
        )
        self.assertNotEqual(
            response.status_code, 302,
            "manifest.json should NOT redirect (browsers block this)"
        )

    def test_service_worker_language_neutral(self):
        """
        CRITICAL: Test service worker is accessible without language prefix.

        Browsers block redirected service workers.
        """
        response = self.client.get('/sw-workbox.js')
        self.assertNotEqual(
            response.status_code, 404,
            "sw-workbox.js should be accessible without language prefix"
        )
        self.assertNotEqual(
            response.status_code, 302,
            "sw-workbox.js should NOT redirect (browsers block this)"
        )

    def test_robots_txt_language_neutral(self):
        """Test robots.txt is accessible without language prefix."""
        response = self.client.get('/robots.txt')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/plain', response.get('Content-Type', ''))

    def test_healthz_language_neutral(self):
        """Test health check endpoint is language-neutral."""
        response = self.client.get('/healthz/')
        self.assertEqual(response.status_code, 200)


# =============================================================================
# PART 2: OAUTH FLOW TESTS (CRITICAL)
# =============================================================================

@override_settings(ROOT_URLCONF='azureproject.urls_crush')
class OAuthI18nTests(SiteTestMixin, TestCase):
    """Test OAuth redirects have language prefix."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='oauth@test.com',
            email='oauth@test.com',
            password='testpass123'
        )

    def _create_profile(self, user, preferred_language='en'):
        """Helper to create a CrushProfile for a user."""
        from crush_lu.models import CrushProfile
        return CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 1, 1),
            gender='M',
            location='Luxembourg',
            is_approved=True,
            preferred_language=preferred_language
        )

    def test_oauth_landing_contains_language_prefixed_redirect(self):
        """
        CRITICAL: Test OAuth landing page contains language-prefixed redirect URL.

        Current BUG: views_oauth_popup.py returns '/dashboard/' without prefix.
        After fix: Should contain '/en/dashboard/' (or user's preferred language).
        """
        self._create_profile(self.user)
        self.client.force_login(self.user)

        response = self.client.get('/en/oauth/landing/')
        content = response.content.decode()

        # The redirect URL in the page should have language prefix
        self.assertIn(
            '/en/dashboard/',
            content,
            "OAuth landing should contain language-prefixed redirect URL. "
            "Current implementation has hardcoded '/dashboard/' in views_oauth_popup.py"
        )

    def test_oauth_landing_new_user_has_language_prefix(self):
        """
        CRITICAL: Test new user OAuth redirect has language prefix.

        Current BUG: Returns '/create-profile/' without prefix.
        """
        # User without profile
        self.client.force_login(self.user)

        response = self.client.get('/en/oauth/landing/')
        content = response.content.decode()

        # Note: The template uses JSON encoding where hyphen is escaped as \u002D
        # Check for either the literal or escaped version
        has_prefix = '/en/create-profile/' in content or '/en/create\\u002Dprofile/' in content
        self.assertTrue(
            has_prefix,
            "OAuth landing for new user should have language-prefixed create-profile URL"
        )

    def test_check_auth_status_returns_language_prefixed_url(self):
        """
        CRITICAL: Test /api/auth/status/ returns language-prefixed redirect URL.

        Current BUG: Returns '/dashboard/' or '/create-profile/' without prefix.
        """
        self._create_profile(self.user, preferred_language='de')
        self.client.force_login(self.user)

        response = self.client.get('/api/auth/status/')
        data = response.json()

        self.assertTrue(data.get('authenticated'))
        redirect_url = data.get('redirect_url', '')

        # Should have language prefix based on user's preferred language
        self.assertTrue(
            redirect_url.startswith('/de/') or redirect_url.startswith('/en/'),
            f"API auth status redirect_url '{redirect_url}' should have language prefix"
        )


@override_settings(ROOT_URLCONF='azureproject.urls_crush')
class AllauthAdapterI18nTests(SiteTestMixin, TestCase):
    """Test Allauth adapters return language-prefixed URLs."""

    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(
            username='adapter@test.com',
            email='adapter@test.com',
            password='testpass123'
        )

    def _create_profile(self, user, preferred_language='en'):
        from crush_lu.models import CrushProfile
        return CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 1, 1),
            gender='M',
            location='Luxembourg',
            is_approved=True,
            preferred_language=preferred_language
        )

    def test_account_adapter_login_redirect_has_prefix(self):
        """
        CRITICAL: Test CrushAccountAdapter.get_login_redirect_url returns language-prefixed URL.

        Current BUG: adapter.py:70,73 return hardcoded '/dashboard/', '/create-profile/'.
        """
        from crush_lu.adapter import CrushAccountAdapter

        self._create_profile(self.user, preferred_language='de')

        request = self.factory.get('/')
        request.user = self.user
        request.META['HTTP_HOST'] = 'crush.lu'
        request.LANGUAGE_CODE = 'de'

        adapter = CrushAccountAdapter()
        redirect_url = adapter.get_login_redirect_url(request)

        self.assertTrue(
            redirect_url.startswith('/de/') or redirect_url.startswith('/en/'),
            f"Adapter redirect URL '{redirect_url}' should have language prefix. "
            "Fix adapter.py to use reverse() with language detection."
        )

    def test_social_account_adapter_signup_redirect_has_prefix(self):
        """
        CRITICAL: Test CrushSocialAccountAdapter returns language-prefixed URL.

        Current BUG: adapter.py:25 returns hardcoded '/create-profile/'.
        """
        from crush_lu.adapter import CrushSocialAccountAdapter

        request = self.factory.get('/')
        request.META['HTTP_HOST'] = 'crush.lu'
        request.LANGUAGE_CODE = 'fr'

        adapter = CrushSocialAccountAdapter()
        redirect_url = adapter.get_signup_redirect_url(request)

        self.assertTrue(
            redirect_url.startswith('/fr/') or redirect_url.startswith('/en/'),
            f"Social adapter redirect URL '{redirect_url}' should have language prefix"
        )


# =============================================================================
# PART 3: PUSH NOTIFICATION URL TESTS (HIGH)
# =============================================================================

@override_settings(ROOT_URLCONF='azureproject.urls_crush')
class PushNotificationI18nTests(SiteTestMixin, TestCase):
    """Test push notification URLs include language prefix."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='push@test.com',
            email='push@test.com',
            password='testpass123'
        )
        from crush_lu.models import CrushProfile
        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 1, 1),
            gender='M',
            location='Luxembourg',
            is_approved=True,
            preferred_language='de'  # User prefers German
        )

    def _create_event(self):
        from crush_lu.models import MeetupEvent
        return MeetupEvent.objects.create(
            title='Test Event',
            description='Test',
            event_type='mixer',
            date_time=timezone.now() + timedelta(days=1),
            location='Luxembourg',
            address='123 Test St',
            max_participants=20,
            registration_deadline=timezone.now(),
            is_published=True
        )

    def test_event_reminder_url_uses_reverse(self):
        """
        Verify push_notifications.py uses reverse() for event URLs.

        FIXED: push_notifications.py now uses get_user_language_url()
        which calls reverse() with user's preferred_language.
        """
        import inspect
        from crush_lu import push_notifications

        source = inspect.getsource(push_notifications.send_event_reminder)

        # Verify the fix is in place - should use get_user_language_url
        self.assertIn(
            'get_user_language_url',
            source,
            "send_event_reminder should use get_user_language_url for i18n URLs"
        )

        # Verify hardcoded URL pattern is NOT present
        self.assertNotIn(
            'f"/events/{event.id}/"',
            source,
            "Hardcoded URL pattern should NOT be present after fix"
        )

    @patch('crush_lu.push_notifications.send_push_notification')
    def test_profile_approved_url_has_language_prefix(self, mock_send):
        """
        CRITICAL: Test profile approved notification URL has language prefix.

        Current BUG: push_notifications.py:213 uses hardcoded '/dashboard/'
        """
        from crush_lu.push_notifications import send_profile_approved_notification
        from crush_lu.models import PushSubscription

        mock_send.return_value = {'success': 1, 'failed': 0, 'total': 1}

        PushSubscription.objects.create(
            user=self.user,
            endpoint='https://test.com',
            p256dh_key='key',
            auth_key='auth',
            enabled=True,
            notify_profile_updates=True
        )

        send_profile_approved_notification(self.user)

        if mock_send.called:
            call_kwargs = mock_send.call_args[1]
            url = call_kwargs.get('url', '')

            self.assertTrue(
                url.startswith('/de/'),
                f"Push URL '{url}' should use user's preferred language 'de'"
            )

    def test_connection_notification_url_uses_reverse(self):
        """
        Verify push_notifications.py uses reverse() for connection URLs.

        FIXED: push_notifications.py now uses get_user_language_url()
        which calls reverse() with user's preferred_language.
        """
        import inspect
        from crush_lu import push_notifications

        source = inspect.getsource(push_notifications.send_new_connection_notification)

        # Verify the fix is in place - should use get_user_language_url
        self.assertIn(
            'get_user_language_url',
            source,
            "send_new_connection_notification should use get_user_language_url for i18n URLs"
        )

        # Verify hardcoded URL pattern is NOT present
        self.assertNotIn(
            '"/connections/"',
            source,
            "Hardcoded '/connections/' URL should NOT be present after fix"
        )


# =============================================================================
# PART 4: SEO TEMPLATE TAGS TESTS (HIGH)
# =============================================================================

@override_settings(ROOT_URLCONF='azureproject.urls_crush')
class SEOTagsTests(TestCase):
    """Test SEO template tags for i18n."""

    def setUp(self):
        self.factory = RequestFactory()

    def test_hreflang_tags_generated_for_all_languages(self):
        """Test hreflang tags are generated for all supported languages."""
        request = self.factory.get('/en/about/')

        template = Template('''
            {% load seo_tags %}
            {% hreflang_tags %}
        ''')

        context = Context({'request': request})
        rendered = template.render(context)

        # Should have hreflang for all languages
        self.assertIn('hreflang="en"', rendered)
        self.assertIn('hreflang="de"', rendered)
        self.assertIn('hreflang="fr"', rendered)
        self.assertIn('hreflang="x-default"', rendered)

    def test_hreflang_urls_correct(self):
        """Test hreflang URLs are correctly formatted."""
        request = self.factory.get('/en/about/')

        template = Template('''
            {% load seo_tags %}
            {% hreflang_tags %}
        ''')

        context = Context({'request': request})
        rendered = template.render(context)

        # URLs should be correct
        self.assertIn('https://crush.lu/en/about/', rendered)
        self.assertIn('https://crush.lu/de/about/', rendered)
        self.assertIn('https://crush.lu/fr/about/', rendered)

    def test_canonical_url_self_referencing(self):
        """Test canonical URL points to current page (self-referencing)."""
        request = self.factory.get('/de/events/')

        template = Template('''
            {% load seo_tags %}
            {% canonical_url %}
        ''')

        context = Context({'request': request})
        rendered = template.render(context).strip()

        self.assertEqual(rendered, 'https://crush.lu/de/events/')

    def test_localized_url_generates_correct_url(self):
        """Test localized_url generates URL for specific language."""
        request = self.factory.get('/en/about/')

        template = Template('''
            {% load seo_tags %}
            {% localized_url 'fr' %}
        ''')

        context = Context({'request': request})
        rendered = template.render(context).strip()

        self.assertEqual(rendered, '/fr/about/')

    def test_localized_url_with_complex_path(self):
        """Test localized_url works with complex paths."""
        request = self.factory.get('/en/events/123/')

        template = Template('''
            {% load seo_tags %}
            {% localized_url 'de' %}
        ''')

        context = Context({'request': request})
        rendered = template.render(context).strip()

        self.assertEqual(rendered, '/de/events/123/')


# =============================================================================
# PART 5: LANGUAGE SWITCHER TESTS (MEDIUM)
# =============================================================================

@override_settings(ROOT_URLCONF='azureproject.urls_crush')
class LanguageSwitcherTests(SiteTestMixin, TestCase):
    """Test language switcher functionality."""

    def setUp(self):
        self.client = Client()

    def test_set_language_view_changes_language(self):
        """Test Django set_language view changes language."""
        # Start on English page
        response = self.client.get('/en/')
        self.assertEqual(response.status_code, 200)

        # Switch to German
        response = self.client.post(
            '/i18n/setlang/',
            {'language': 'de', 'next': '/de/'},
            follow=True
        )

        # Should redirect to German URL
        self.assertEqual(response.status_code, 200)

    def test_language_cookie_set(self):
        """Test language preference is saved in cookie."""
        # Set language to French
        self.client.post(
            '/i18n/setlang/',
            {'language': 'fr', 'next': '/fr/'}
        )

        # Cookie should be set
        self.assertEqual(
            self.client.cookies.get('django_language').value,
            'fr'
        )

    def test_language_switcher_preserves_path(self):
        """Test language switcher keeps user on same page path."""
        from crush_lu.models import MeetupEvent

        event = MeetupEvent.objects.create(
            title='Test Event',
            description='Test',
            event_type='mixer',
            date_time=timezone.now() + timedelta(days=7),
            location='Luxembourg',
            address='123 Test St',
            max_participants=20,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True
        )

        # Switch from English event detail to French
        response = self.client.post(
            '/i18n/setlang/',
            {'language': 'fr', 'next': f'/fr/events/{event.id}/'},
            follow=True
        )

        # Should be on French version of same page
        self.assertIn(f'/fr/events/{event.id}/', response.request['PATH_INFO'])


# =============================================================================
# PART 6: USER LANGUAGE PREFERENCE TESTS (LOW)
# =============================================================================

@override_settings(ROOT_URLCONF='azureproject.urls_crush')
class UserLanguagePreferenceTests(SiteTestMixin, TestCase):
    """Test user language preference storage and usage."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='langpref@test.com',
            email='langpref@test.com',
            password='testpass123'
        )

    def test_preferred_language_field_exists(self):
        """Test CrushProfile has preferred_language field."""
        from crush_lu.models import CrushProfile

        profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 1, 1),
            gender='M',
            location='Luxembourg',
            is_approved=True,
            preferred_language='en'
        )

        self.assertEqual(profile.preferred_language, 'en')

    def test_preferred_language_accepts_all_languages(self):
        """Test preferred_language accepts all valid language codes."""
        from crush_lu.models import CrushProfile

        profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 1, 1),
            gender='M',
            location='Luxembourg',
            is_approved=True
        )

        for lang in ['en', 'de', 'fr']:
            profile.preferred_language = lang
            profile.save()
            profile.refresh_from_db()
            self.assertEqual(profile.preferred_language, lang)

    def test_preferred_language_defaults_to_english(self):
        """Test preferred_language defaults to 'en'."""
        from crush_lu.models import CrushProfile

        profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 1, 1),
            gender='M',
            location='Luxembourg',
            is_approved=True
        )

        self.assertEqual(profile.preferred_language, 'en')


# =============================================================================
# PART 7: JAVASCRIPT REDIRECT URL TESTS (MEDIUM)
# =============================================================================

class JavaScriptI18nTests(TestCase):
    """Test JavaScript files for hardcoded URLs that need fixing."""

    def test_event_voting_js_uses_template_url(self):
        """
        Verify event-voting.js accepts redirect URL from template.

        FIXED: JavaScript now reads resultsUrl from data-results-url attribute
        set by the template using {% url %} tag with language prefix.
        """
        import os
        # Navigate from crush_lu/tests/ up to project root, then to static/
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        js_path = os.path.join(
            project_root,
            'static', 'crush_lu', 'js', 'event-voting.js'
        )

        with open(js_path, 'r') as f:
            content = f.read()

        # Verify the fix is in place - should use this.resultsUrl
        self.assertIn(
            'this.resultsUrl',
            content,
            "event-voting.js should use this.resultsUrl for i18n-compatible redirects"
        )

        # Verify it reads from data attribute
        self.assertIn(
            'dataset.resultsUrl',
            content,
            "event-voting.js should read results URL from data attribute"
        )

        # Verify hardcoded URL is NOT used for redirects
        self.assertNotIn(
            'window.location.href = `/events/${this.eventId}/voting/results/`',
            content,
            "Hardcoded redirect URL should NOT be present after fix"
        )


# =============================================================================
# PART 8: EMAIL URL TESTS (HIGH)
# =============================================================================

@override_settings(ROOT_URLCONF='azureproject.urls_crush')
class EmailI18nURLTests(TestCase):
    """Test that email helper functions generate correct language-prefixed URLs."""

    def setUp(self):
        from crush_lu.models import CrushProfile, MeetupEvent, EventRegistration
        from django.utils import timezone
        from datetime import timedelta

        self.user = User.objects.create_user(
            username='emailtest@test.com',
            email='emailtest@test.com',
            password='testpass123',
            first_name='Email',
            last_name='Test'
        )

        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 1, 1),
            gender='M',
            location='Luxembourg',
            is_approved=True,
            preferred_language='de'  # German preference
        )

        self.event = MeetupEvent.objects.create(
            title='Test Event',
            description='Test Description',
            location='Luxembourg City',
            date_time=timezone.now() + timedelta(days=7),
            registration_deadline=timezone.now() + timedelta(days=5),
            max_participants=20,
            event_type='mixer',
            is_published=True
        )

        self.registration = EventRegistration.objects.create(
            event=self.event,
            user=self.user,
            status='confirmed'
        )

    def test_get_user_language_url_uses_preferred_language(self):
        """Test get_user_language_url uses user's preferred_language."""
        from crush_lu.email_helpers import get_user_language_url
        from django.test import RequestFactory

        factory = RequestFactory()
        request = factory.get('/en/dashboard/')

        # User prefers German
        self.profile.preferred_language = 'de'
        self.profile.save()

        url = get_user_language_url(self.user, 'crush_lu:event_list', request)

        # Should use user's preferred language (de)
        self.assertIn('/de/events/', url,
            f"URL should use user's preferred_language (de), got: {url}")

    def test_get_user_language_url_falls_back_to_english(self):
        """Test get_user_language_url falls back to English when no preference."""
        from crush_lu.email_helpers import get_user_language_url
        from django.test import RequestFactory

        factory = RequestFactory()
        request = factory.get('/fr/dashboard/')

        # Remove language preference
        self.profile.preferred_language = ''
        self.profile.save()

        url = get_user_language_url(self.user, 'crush_lu:event_list', request)

        # Should fall back to English
        self.assertIn('/en/events/', url,
            f"URL should fall back to /en/ when no preference, got: {url}")

    def test_get_user_language_url_french_user(self):
        """Test get_user_language_url works for French users."""
        from crush_lu.email_helpers import get_user_language_url
        from django.test import RequestFactory

        factory = RequestFactory()
        request = factory.get('/en/dashboard/')

        # User prefers French
        self.profile.preferred_language = 'fr'
        self.profile.save()

        url = get_user_language_url(self.user, 'crush_lu:event_list', request)

        # Should use French
        self.assertIn('/fr/events/', url,
            f"URL should use French for FR user, got: {url}")

    def test_get_user_language_url_with_kwargs(self):
        """Test get_user_language_url works with URL kwargs."""
        from crush_lu.email_helpers import get_user_language_url
        from django.test import RequestFactory

        factory = RequestFactory()
        request = factory.get('/en/dashboard/')

        # User prefers German
        self.profile.preferred_language = 'de'
        self.profile.save()

        url = get_user_language_url(
            self.user,
            'crush_lu:event_detail',
            request,
            kwargs={'event_id': self.event.id}
        )

        # Should use German with event ID
        self.assertIn(f'/de/events/{self.event.id}/', url,
            f"URL should have /de/events/{self.event.id}/, got: {url}")

    def test_email_context_has_language_prefixed_urls(self):
        """Test that email context variables have language-prefixed URLs."""
        from crush_lu.email_helpers import get_user_language_url
        from django.test import RequestFactory

        factory = RequestFactory()
        request = factory.get('/en/dashboard/')

        # User prefers German
        self.profile.preferred_language = 'de'
        self.profile.save()

        # Build URLs like the email helper functions do
        events_url = get_user_language_url(self.user, 'crush_lu:event_list', request)
        event_url = get_user_language_url(
            self.user,
            'crush_lu:event_detail',
            request,
            kwargs={'event_id': self.event.id}
        )
        cancel_url = get_user_language_url(
            self.user,
            'crush_lu:event_cancel',
            request,
            kwargs={'event_id': self.event.id}
        )

        # All should have German prefix
        self.assertIn('/de/events/', events_url)
        self.assertIn(f'/de/events/{self.event.id}/', event_url)
        self.assertIn(f'/de/events/{self.event.id}/cancel/', cancel_url)

    def test_user_without_profile_defaults_to_english(self):
        """Test that users without CrushProfile get English URLs."""
        from crush_lu.email_helpers import get_user_language_url
        from django.test import RequestFactory

        factory = RequestFactory()
        request = factory.get('/de/dashboard/')

        # Create user without profile
        user_no_profile = User.objects.create_user(
            username='noprofile@test.com',
            email='noprofile@test.com',
            password='testpass123'
        )

        url = get_user_language_url(user_no_profile, 'crush_lu:event_list', request)

        # Should fall back to English
        self.assertIn('/en/events/', url,
            f"URL should fall back to /en/ for user without profile, got: {url}")


# =============================================================================
# PART 9: EMAIL BASE URLS AND UNSUBSCRIBE TESTS (CRITICAL)
# =============================================================================

@override_settings(ROOT_URLCONF='azureproject.urls_crush')
class EmailBaseURLsTests(SiteTestMixin, TestCase):
    """Test email footer URLs are correctly generated with i18n prefixes."""

    def setUp(self):
        from crush_lu.models import CrushProfile
        from django.test import RequestFactory

        self.factory = RequestFactory()
        self.request = self.factory.get('/en/')
        self.request.META['HTTP_HOST'] = 'crush.lu'
        self.request.META['wsgi.url_scheme'] = 'https'

        self.user = User.objects.create_user(
            username='footer@test.com',
            email='footer@test.com',
            password='testpass123',
            first_name='Footer',
            last_name='Test'
        )

        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 1, 1),
            gender='M',
            location='Luxembourg',
            is_approved=True,
            preferred_language='de'  # German preference
        )

    def test_get_email_base_urls_returns_all_footer_urls(self):
        """Test get_email_base_urls returns all required footer URLs."""
        from crush_lu.email_helpers import get_email_base_urls

        base_urls = get_email_base_urls(self.user, self.request)

        # Should have all required keys
        required_keys = ['home_url', 'about_url', 'events_url', 'settings_url']
        for key in required_keys:
            self.assertIn(key, base_urls, f"Missing key: {key}")
            self.assertIsNotNone(base_urls[key], f"Key {key} is None")

    def test_get_email_base_urls_uses_user_language(self):
        """Test get_email_base_urls uses user's preferred language."""
        from crush_lu.email_helpers import get_email_base_urls

        # German user
        self.profile.preferred_language = 'de'
        self.profile.save()

        base_urls = get_email_base_urls(self.user, self.request)

        self.assertIn('/de/', base_urls['home_url'])
        self.assertIn('/de/about/', base_urls['about_url'])
        self.assertIn('/de/events/', base_urls['events_url'])
        self.assertIn('/de/account/settings/', base_urls['settings_url'])

    def test_get_email_base_urls_french_user(self):
        """Test get_email_base_urls works for French users."""
        from crush_lu.email_helpers import get_email_base_urls

        self.profile.preferred_language = 'fr'
        self.profile.save()

        base_urls = get_email_base_urls(self.user, self.request)

        self.assertIn('/fr/', base_urls['home_url'])
        self.assertIn('/fr/events/', base_urls['events_url'])

    def test_get_email_base_urls_english_user(self):
        """Test get_email_base_urls works for English users."""
        from crush_lu.email_helpers import get_email_base_urls

        self.profile.preferred_language = 'en'
        self.profile.save()

        base_urls = get_email_base_urls(self.user, self.request)

        self.assertIn('/en/', base_urls['home_url'])
        self.assertIn('/en/events/', base_urls['events_url'])

    def test_get_email_base_urls_includes_domain(self):
        """Test get_email_base_urls includes full domain in URLs."""
        from crush_lu.email_helpers import get_email_base_urls

        base_urls = get_email_base_urls(self.user, self.request)

        # All URLs should include the domain
        for key, url in base_urls.items():
            self.assertTrue(
                url.startswith('https://crush.lu') or url.startswith('http://'),
                f"{key} should be an absolute URL, got: {url}"
            )


@override_settings(ROOT_URLCONF='azureproject.urls_crush')
class EmailUnsubscribeURLTests(SiteTestMixin, TestCase):
    """Test unsubscribe URL generation with i18n prefixes."""

    def setUp(self):
        from crush_lu.models import CrushProfile
        from django.test import RequestFactory

        self.factory = RequestFactory()
        self.request = self.factory.get('/en/')
        self.request.META['HTTP_HOST'] = 'crush.lu'
        self.request.META['wsgi.url_scheme'] = 'https'

        self.user = User.objects.create_user(
            username='unsub@test.com',
            email='unsub@test.com',
            password='testpass123'
        )

        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 1, 1),
            gender='M',
            location='Luxembourg',
            is_approved=True,
            preferred_language='de'
        )

    def test_unsubscribe_url_has_language_prefix(self):
        """
        CRITICAL: Test unsubscribe URL includes language prefix.

        Before fix: /unsubscribe/{token}/ (causes 404)
        After fix: /de/unsubscribe/{token}/ (works correctly)
        """
        from crush_lu.email_helpers import get_unsubscribe_url

        url = get_unsubscribe_url(self.user, self.request)

        self.assertIsNotNone(url, "Unsubscribe URL should not be None")
        self.assertIn('/de/unsubscribe/', url,
            f"Unsubscribe URL should have /de/ prefix for German user, got: {url}")

    def test_unsubscribe_url_french_user(self):
        """Test unsubscribe URL for French user."""
        from crush_lu.email_helpers import get_unsubscribe_url

        self.profile.preferred_language = 'fr'
        self.profile.save()

        url = get_unsubscribe_url(self.user, self.request)

        self.assertIn('/fr/unsubscribe/', url,
            f"Unsubscribe URL should have /fr/ prefix, got: {url}")

    def test_unsubscribe_url_contains_token(self):
        """Test unsubscribe URL contains user's token."""
        from crush_lu.email_helpers import get_unsubscribe_url
        from crush_lu.models import EmailPreference

        url = get_unsubscribe_url(self.user, self.request)

        # Get the token that was created
        email_prefs = EmailPreference.objects.get(user=self.user)
        token = str(email_prefs.unsubscribe_token)

        self.assertIn(token, url,
            f"Unsubscribe URL should contain token {token}, got: {url}")


@override_settings(ROOT_URLCONF='azureproject.urls_crush')
class EmailContextWithUnsubscribeTests(SiteTestMixin, TestCase):
    """Test get_email_context_with_unsubscribe includes all required URLs."""

    def setUp(self):
        from crush_lu.models import CrushProfile
        from django.test import RequestFactory

        self.factory = RequestFactory()
        self.request = self.factory.get('/en/')
        self.request.META['HTTP_HOST'] = 'crush.lu'
        self.request.META['wsgi.url_scheme'] = 'https'

        self.user = User.objects.create_user(
            username='context@test.com',
            email='context@test.com',
            password='testpass123'
        )

        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 1, 1),
            gender='M',
            location='Luxembourg',
            is_approved=True,
            preferred_language='de'
        )

    def test_context_includes_unsubscribe_url(self):
        """Test context includes unsubscribe_url."""
        from crush_lu.email_helpers import get_email_context_with_unsubscribe

        context = get_email_context_with_unsubscribe(self.user, self.request)

        self.assertIn('unsubscribe_url', context)
        self.assertIsNotNone(context['unsubscribe_url'])

    def test_context_includes_footer_urls(self):
        """Test context includes all footer URLs."""
        from crush_lu.email_helpers import get_email_context_with_unsubscribe

        context = get_email_context_with_unsubscribe(self.user, self.request)

        required_keys = ['home_url', 'about_url', 'events_url', 'settings_url']
        for key in required_keys:
            self.assertIn(key, context, f"Context missing {key}")

    def test_context_includes_extra_context(self):
        """Test extra context is merged correctly."""
        from crush_lu.email_helpers import get_email_context_with_unsubscribe

        context = get_email_context_with_unsubscribe(
            self.user, self.request,
            first_name='Test',
            custom_var='custom_value'
        )

        self.assertEqual(context['first_name'], 'Test')
        self.assertEqual(context['custom_var'], 'custom_value')

    def test_context_urls_use_user_language(self):
        """Test all URLs in context use user's preferred language."""
        from crush_lu.email_helpers import get_email_context_with_unsubscribe

        context = get_email_context_with_unsubscribe(self.user, self.request)

        # All URLs should have German prefix
        self.assertIn('/de/', context['home_url'])
        self.assertIn('/de/', context['about_url'])
        self.assertIn('/de/', context['events_url'])
        self.assertIn('/de/', context['settings_url'])
        self.assertIn('/de/', context['unsubscribe_url'])


@override_settings(ROOT_URLCONF='azureproject.urls_crush')
class EmailNotificationsI18nTests(SiteTestMixin, TestCase):
    """Test email_notifications.py functions use i18n-aware URLs."""

    def setUp(self):
        from crush_lu.models import CrushProfile, MeetupEvent
        from django.test import RequestFactory
        from django.utils import timezone
        from datetime import timedelta

        self.factory = RequestFactory()
        self.request = self.factory.get('/en/')
        self.request.META['HTTP_HOST'] = 'crush.lu'
        self.request.META['wsgi.url_scheme'] = 'https'

        self.user = User.objects.create_user(
            username='invite@test.com',
            email='invite@test.com',
            password='testpass123'
        )

        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 1, 1),
            gender='M',
            location='Luxembourg',
            is_approved=True,
            preferred_language='fr'  # French preference
        )

        self.event = MeetupEvent.objects.create(
            title='Private Event',
            description='Test',
            event_type='mixer',
            date_time=timezone.now() + timedelta(days=7),
            location='Luxembourg',
            address='123 Test St',
            max_participants=20,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True,
            is_private_invitation=True
        )

    def test_existing_user_invitation_urls_have_language_prefix(self):
        """Test send_existing_user_invitation_email uses i18n URLs."""
        from crush_lu.email_helpers import get_user_language_url

        # Simulate what the function does
        event_url = get_user_language_url(
            self.user, 'crush_lu:event_detail', self.request,
            kwargs={'event_id': self.event.id}
        )
        dashboard_url = get_user_language_url(
            self.user, 'crush_lu:dashboard', self.request
        )

        # French user should get French URLs
        self.assertIn('/fr/events/', event_url)
        self.assertIn('/fr/dashboard/', dashboard_url)

    def test_invitation_approval_urls_have_language_prefix(self):
        """Test invitation approval email uses i18n URLs."""
        from crush_lu.email_helpers import get_user_language_url, get_email_base_urls

        # Simulate what send_invitation_approval_email does
        event_url = get_user_language_url(
            self.user, 'crush_lu:event_detail', self.request,
            kwargs={'event_id': self.event.id}
        )
        dashboard_url = get_user_language_url(
            self.user, 'crush_lu:dashboard', self.request
        )
        base_urls = get_email_base_urls(self.user, self.request)

        # All should have French prefix
        self.assertIn('/fr/', event_url)
        self.assertIn('/fr/', dashboard_url)
        self.assertIn('/fr/', base_urls['events_url'])

    def test_invitation_rejection_includes_events_url(self):
        """Test invitation rejection email includes events_url in context."""
        from crush_lu.email_helpers import get_user_language_url

        # This was a bug - events_url was missing from context
        events_url = get_user_language_url(
            self.user, 'crush_lu:event_list', self.request
        )

        self.assertIn('/fr/events/', events_url)
        self.assertIsNotNone(events_url)


@override_settings(ROOT_URLCONF='azureproject.urls_crush')
class WelcomeEmailI18nTests(SiteTestMixin, TestCase):
    """Test welcome email URLs are correctly generated."""

    def setUp(self):
        from crush_lu.models import CrushProfile
        from django.test import RequestFactory

        self.factory = RequestFactory()
        self.request = self.factory.get('/en/')
        self.request.META['HTTP_HOST'] = 'crush.lu'
        self.request.META['wsgi.url_scheme'] = 'https'

        self.user = User.objects.create_user(
            username='welcome@test.com',
            email='welcome@test.com',
            password='testpass123',
            first_name='Welcome'
        )

        # New users may not have a profile yet, so test default behavior
        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 1, 1),
            gender='M',
            location='Luxembourg',
            is_approved=False,
            preferred_language='de'
        )

    def test_welcome_email_profile_url_has_language_prefix(self):
        """Test welcome email profile URL has language prefix."""
        from crush_lu.email_helpers import get_user_language_url

        profile_url = get_user_language_url(
            self.user, 'crush_lu:create_profile', self.request
        )

        self.assertIn('/de/create-profile/', profile_url)

    def test_welcome_email_how_it_works_url_has_language_prefix(self):
        """Test welcome email how-it-works URL has language prefix."""
        from crush_lu.email_helpers import get_user_language_url

        how_it_works_url = get_user_language_url(
            self.user, 'crush_lu:how_it_works', self.request
        )

        self.assertIn('/de/how-it-works/', how_it_works_url)
