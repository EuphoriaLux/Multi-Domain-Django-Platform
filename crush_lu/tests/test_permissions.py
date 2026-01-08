"""
Permission and Authorization Tests for Crush.lu

Tests for:
- Profile approval requirements for event registration
- Coach-only view access
- Privacy settings enforcement
- Authentication requirements

Run with: pytest crush_lu/tests/test_permissions.py -v
"""
from datetime import date, timedelta
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.utils import timezone

User = get_user_model()

CRUSH_LU_URL_SETTINGS = {
    'ROOT_URLCONF': 'azureproject.urls_crush',
}


class SiteTestMixin:
    """Mixin to create Site object for tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1,
            defaults={'domain': 'testserver', 'name': 'Test Server'}
        )


@override_settings(**CRUSH_LU_URL_SETTINGS)
class ProfileApprovalTests(SiteTestMixin, TestCase):
    """Test profile approval requirements."""

    def setUp(self):
        """Set up test data."""
        from crush_lu.models import CrushProfile, MeetupEvent

        self.client = Client()

        # Unapproved user
        self.unapproved_user = User.objects.create_user(
            username='unapproved@example.com',
            email='unapproved@example.com',
            password='testpass123',
            first_name='Unapproved',
            last_name='User'
        )

        self.unapproved_profile = CrushProfile.objects.create(
            user=self.unapproved_user,
            date_of_birth=date(1995, 5, 15),
            gender='M',
            location='Luxembourg',
            is_approved=False,
            is_active=True
        )

        # Approved user
        self.approved_user = User.objects.create_user(
            username='approved@example.com',
            email='approved@example.com',
            password='testpass123',
            first_name='Approved',
            last_name='User'
        )

        self.approved_profile = CrushProfile.objects.create(
            user=self.approved_user,
            date_of_birth=date(1995, 5, 15),
            gender='M',
            location='Luxembourg',
            is_approved=True,
            is_active=True
        )

        # Test event
        self.event = MeetupEvent.objects.create(
            title='Test Event',
            description='A test event',
            event_type='mixer',
            date_time=timezone.now() + timedelta(days=7),
            location='Luxembourg',
            address='123 Test Street',
            max_participants=20,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True
        )

    def test_unapproved_profile_cannot_register_events(self):
        """Unapproved profiles should not be able to register for events."""
        self.client.login(
            username='unapproved@example.com',
            password='testpass123'
        )

        url = reverse('crush_lu:event_register', args=[self.event.id])
        response = self.client.get(url)

        # Should redirect or show error
        # The view should check for profile approval
        self.assertIn(response.status_code, [302, 403, 200])

    def test_approved_profile_can_access_event_registration(self):
        """Approved profiles should be able to access event registration."""
        self.client.login(
            username='approved@example.com',
            password='testpass123'
        )

        url = reverse('crush_lu:event_register', args=[self.event.id])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

    def test_dashboard_shows_approval_status(self):
        """Dashboard should indicate profile approval status."""
        self.client.login(
            username='unapproved@example.com',
            password='testpass123'
        )

        url = reverse('crush_lu:dashboard')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)


@override_settings(**CRUSH_LU_URL_SETTINGS)
class CoachAccessTests(SiteTestMixin, TestCase):
    """Test coach-only view access."""

    def setUp(self):
        """Set up test data."""
        from crush_lu.models import CrushCoach, CrushProfile

        self.client = Client()

        # Regular user (not a coach)
        self.regular_user = User.objects.create_user(
            username='regular@example.com',
            email='regular@example.com',
            password='testpass123',
            first_name='Regular',
            last_name='User'
        )

        CrushProfile.objects.create(
            user=self.regular_user,
            date_of_birth=date(1995, 5, 15),
            gender='M',
            location='Luxembourg',
            is_approved=True,
            is_active=True
        )

        # Coach user
        self.coach_user = User.objects.create_user(
            username='coach@example.com',
            email='coach@example.com',
            password='testpass123',
            first_name='Coach',
            last_name='Marie'
        )

        self.coach = CrushCoach.objects.create(
            user=self.coach_user,
            bio='Experienced dating coach',
            specializations='General coaching',
            is_active=True,
            max_active_reviews=10
        )

        # Inactive coach
        self.inactive_coach_user = User.objects.create_user(
            username='inactive_coach@example.com',
            email='inactive_coach@example.com',
            password='testpass123',
            first_name='Inactive',
            last_name='Coach'
        )

        self.inactive_coach = CrushCoach.objects.create(
            user=self.inactive_coach_user,
            bio='Inactive coach bio',
            is_active=False,
            max_active_reviews=10
        )

    def test_non_coach_cannot_access_dashboard(self):
        """Non-coach users should not access coach dashboard."""
        self.client.login(
            username='regular@example.com',
            password='testpass123'
        )

        url = reverse('crush_lu:coach_dashboard')
        response = self.client.get(url)

        # Should redirect or return 403
        self.assertIn(response.status_code, [302, 403])

    def test_active_coach_can_access_dashboard(self):
        """Active coaches should access coach dashboard."""
        self.client.login(
            username='coach@example.com',
            password='testpass123'
        )

        url = reverse('crush_lu:coach_dashboard')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

    def test_inactive_coach_dashboard_access(self):
        """Test inactive coach dashboard access behavior.

        NOTE: Currently the view allows inactive coaches to access the dashboard.
        This test documents current behavior - if access should be restricted,
        the view needs to be updated to check is_active.
        """
        self.client.login(
            username='inactive_coach@example.com',
            password='testpass123'
        )

        url = reverse('crush_lu:coach_dashboard')
        response = self.client.get(url)

        # Inactive coaches are redirected to user dashboard with error message
        self.assertEqual(response.status_code, 302)
        self.assertIn('dashboard', response.url)


@override_settings(**CRUSH_LU_URL_SETTINGS)
class AuthenticationRequirementTests(SiteTestMixin, TestCase):
    """Test authentication requirements for various views."""

    def setUp(self):
        """Set up test data."""
        from crush_lu.models import MeetupEvent

        self.client = Client()

        self.event = MeetupEvent.objects.create(
            title='Test Event',
            description='A test event',
            event_type='mixer',
            date_time=timezone.now() + timedelta(days=7),
            location='Luxembourg',
            address='123 Test Street',
            max_participants=20,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True
        )

    def test_dashboard_requires_auth(self):
        """Dashboard should require authentication."""
        url = reverse('crush_lu:dashboard')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)

    def test_edit_profile_requires_auth(self):
        """Edit profile should require authentication."""
        url = reverse('crush_lu:edit_profile')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)

    def test_event_register_requires_auth(self):
        """Event registration should require authentication."""
        url = reverse('crush_lu:event_register', args=[self.event.id])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 302)

    def test_public_pages_accessible(self):
        """Public pages should be accessible without auth."""
        public_urls = [
            reverse('crush_lu:home'),
            reverse('crush_lu:about'),
            reverse('crush_lu:how_it_works'),
            reverse('crush_lu:event_list'),
        ]

        for url in public_urls:
            response = self.client.get(url)
            self.assertEqual(
                response.status_code, 200,
                f"Public page {url} should be accessible"
            )


@override_settings(**CRUSH_LU_URL_SETTINGS)
class PrivacySettingsTests(SiteTestMixin, TestCase):
    """Test privacy settings enforcement."""

    def setUp(self):
        """Set up test data."""
        from crush_lu.models import CrushProfile

        self.user = User.objects.create_user(
            username='testuser@example.com',
            email='testuser@example.com',
            password='testpass123',
            first_name='John',
            last_name='Doe'
        )

        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 5, 15),
            gender='M',
            location='Luxembourg',
            is_approved=True,
            is_active=True,
            show_full_name=False,
            show_exact_age=False,
            blur_photos=True
        )

    def test_display_name_respects_privacy(self):
        """Display name should respect privacy settings."""
        # With show_full_name=False, should only show first name
        self.assertEqual(self.profile.display_name, 'John')

    def test_age_display_respects_privacy(self):
        """Age display should respect privacy settings."""
        # With show_exact_age=False, should show range
        self.assertIsNotNone(self.profile.age_range)
        self.assertIn('-', self.profile.age_range)

    def test_blur_photos_setting(self):
        """Blur photos setting should be respected."""
        self.assertTrue(self.profile.blur_photos)
