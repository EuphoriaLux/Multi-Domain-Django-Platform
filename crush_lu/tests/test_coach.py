"""
Coach Tests for Crush.lu

Comprehensive tests for coach functionality including:
- Coach dashboard access
- Profile review workflow
- Workload management
- Coach session tracking

Run with: pytest crush_lu/tests/test_coach.py -v
"""
from datetime import date, timedelta
from django.test import TestCase, Client, override_settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.urls import reverse
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
        Site.objects.update_or_create(
            id=1,
            defaults={'domain': 'testserver', 'name': 'Test Server'}
        )


@override_settings(**CRUSH_LU_URL_SETTINGS)
class CoachDashboardTests(SiteTestMixin, TestCase):
    """Test coach dashboard access and functionality."""

    def setUp(self):
        """Set up coach and regular user."""
        from crush_lu.models import CrushCoach, CrushProfile

        self.client = Client()

        # Create coach user
        self.coach_user = User.objects.create_user(
            username='coach@example.com',
            email='coach@example.com',
            password='coachpass123',
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

        # Create regular user
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
            is_approved=True
        )

    def test_coach_can_access_dashboard(self):
        """Test coach can access coach dashboard."""
        self.client.login(username='coach@example.com', password='coachpass123')

        response = self.client.get(reverse('crush_lu:coach_dashboard'))

        self.assertEqual(response.status_code, 200)

    def test_non_coach_cannot_access_dashboard(self):
        """Test regular user cannot access coach dashboard."""
        self.client.login(username='regular@example.com', password='testpass123')

        response = self.client.get(reverse('crush_lu:coach_dashboard'))

        # Should redirect or show 403
        self.assertIn(response.status_code, [302, 403])

    def test_inactive_coach_access_behavior(self):
        """Test inactive coach access behavior.

        NOTE: Currently the view allows inactive coaches to access the dashboard.
        This test documents current behavior - if access should be restricted,
        the view needs to be updated to check is_active.
        """
        # Deactivate coach
        self.coach.is_active = False
        self.coach.save()

        self.client.login(username='coach@example.com', password='coachpass123')

        response = self.client.get(reverse('crush_lu:coach_dashboard'))

        # Inactive coaches are redirected to user dashboard with error message
        self.assertEqual(response.status_code, 302)
        self.assertIn('dashboard', response.url)

    def test_unauthenticated_redirects_to_login(self):
        """Test unauthenticated user is redirected to login."""
        response = self.client.get(reverse('crush_lu:coach_dashboard'))

        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)


@override_settings(**CRUSH_LU_URL_SETTINGS)
class ProfileReviewWorkflowTests(SiteTestMixin, TestCase):
    """Test profile review workflow by coaches."""

    def setUp(self):
        """Set up coach and profiles for review."""
        from crush_lu.models import CrushCoach, CrushProfile, ProfileSubmission

        self.client = Client()

        # Create coach
        self.coach_user = User.objects.create_user(
            username='reviewer@example.com',
            email='reviewer@example.com',
            password='coachpass123',
            first_name='Reviewer',
            last_name='Coach'
        )

        self.coach = CrushCoach.objects.create(
            user=self.coach_user,
            bio='Profile reviewer',
            is_active=True,
            max_active_reviews=5
        )

        # Create user needing review
        self.pending_user = User.objects.create_user(
            username='pending@example.com',
            email='pending@example.com',
            password='testpass123',
            first_name='Pending',
            last_name='User'
        )

        self.pending_profile = CrushProfile.objects.create(
            user=self.pending_user,
            date_of_birth=date(1995, 5, 15),
            gender='M',
            location='Luxembourg',
            bio='Test bio for approval',
            is_approved=False
        )

        self.submission = ProfileSubmission.objects.create(
            profile=self.pending_profile,
            coach=self.coach,
            status='pending'
        )

    def test_coach_can_view_pending_submissions(self):
        """Test coach can view pending submissions."""
        from crush_lu.models import ProfileSubmission

        pending = ProfileSubmission.objects.filter(
            coach=self.coach,
            status='pending'
        )

        self.assertEqual(pending.count(), 1)

    def test_coach_approves_profile(self):
        """Test coach can approve a profile."""
        self.submission.status = 'approved'
        self.submission.save()

        # Update profile
        self.pending_profile.is_approved = True
        self.pending_profile.approved_at = timezone.now()
        self.pending_profile.save()

        self.pending_profile.refresh_from_db()
        self.assertTrue(self.pending_profile.is_approved)
        self.assertEqual(self.submission.status, 'approved')

    def test_coach_rejects_profile(self):
        """Test coach can reject a profile."""
        self.submission.status = 'rejected'
        self.submission.feedback_to_user = 'Profile does not meet guidelines'
        self.submission.save()

        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, 'rejected')
        self.assertIsNotNone(self.submission.feedback_to_user)

    def test_coach_requests_revision(self):
        """Test coach can request revision."""
        self.submission.status = 'revision'
        self.submission.feedback_to_user = 'Please update your bio'
        self.submission.save()

        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, 'revision')


@override_settings(**CRUSH_LU_URL_SETTINGS)
class CoachWorkloadTests(SiteTestMixin, TestCase):
    """Test coach workload management."""

    def setUp(self):
        """Set up coaches with different workloads."""
        from crush_lu.models import CrushCoach, CrushProfile, ProfileSubmission

        # Coach with low workload
        self.coach_low = User.objects.create_user(
            username='coach_low@example.com',
            email='coach_low@example.com',
            password='coachpass123'
        )

        self.coach_low_obj = CrushCoach.objects.create(
            user=self.coach_low,
            bio='Low workload coach',
            is_active=True,
            max_active_reviews=10
        )

        # Coach at max capacity
        self.coach_full = User.objects.create_user(
            username='coach_full@example.com',
            email='coach_full@example.com',
            password='coachpass123'
        )

        self.coach_full_obj = CrushCoach.objects.create(
            user=self.coach_full,
            bio='Full workload coach',
            is_active=True,
            max_active_reviews=2  # Low limit
        )

        # Assign submissions to full coach
        for i in range(2):
            user = User.objects.create_user(
                username=f'assigned{i}@example.com',
                email=f'assigned{i}@example.com',
                password='testpass123'
            )

            profile = CrushProfile.objects.create(
                user=user,
                date_of_birth=date(1995, 5, 15),
                gender='M',
                location='Luxembourg',
                is_approved=False
            )

            ProfileSubmission.objects.create(
                profile=profile,
                coach=self.coach_full_obj,
                status='pending'
            )

    def test_coach_current_workload(self):
        """Test getting coach current workload."""
        from crush_lu.models import ProfileSubmission

        workload = ProfileSubmission.objects.filter(
            coach=self.coach_full_obj,
            status='pending'
        ).count()

        self.assertEqual(workload, 2)

    def test_coach_can_accept_reviews(self):
        """Test coach with capacity can accept reviews."""
        self.assertTrue(self.coach_low_obj.can_accept_reviews())

    def test_coach_at_max_capacity(self):
        """Test coach at max capacity check."""
        self.assertFalse(self.coach_full_obj.can_accept_reviews())


@override_settings(**CRUSH_LU_URL_SETTINGS)
class CoachAutoAssignmentTests(SiteTestMixin, TestCase):
    """Test automatic coach assignment for new submissions."""

    def setUp(self):
        """Set up multiple coaches with different workloads."""
        from crush_lu.models import CrushCoach

        # Create coaches with varying workloads
        self.coaches = []
        for i in range(3):
            user = User.objects.create_user(
                username=f'autocoach{i}@example.com',
                email=f'autocoach{i}@example.com',
                password='coachpass123'
            )

            coach = CrushCoach.objects.create(
                user=user,
                bio=f'Auto Coach {i}',
                is_active=True,
                max_active_reviews=10
            )
            self.coaches.append(coach)

    def test_auto_assign_selects_available_coach(self):
        """Test auto-assignment selects an available coach."""
        from crush_lu.models import CrushProfile, ProfileSubmission

        # Create new user needing review
        user = User.objects.create_user(
            username='newuser@example.com',
            email='newuser@example.com',
            password='testpass123'
        )

        profile = CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 5, 15),
            gender='M',
            location='Luxembourg',
            is_approved=False
        )

        # Create submission and auto-assign
        submission = ProfileSubmission.objects.create(
            profile=profile,
            status='pending'
        )

        # Use the assign_coach method
        assigned = submission.assign_coach()

        self.assertTrue(assigned)
        self.assertIsNotNone(submission.coach)
        self.assertIn(submission.coach, self.coaches)


@override_settings(**CRUSH_LU_URL_SETTINGS)
class CoachSessionTests(SiteTestMixin, TestCase):
    """Test coach session tracking."""

    def setUp(self):
        """Set up coach and user for session tests."""
        from crush_lu.models import CrushCoach, CrushProfile

        self.coach_user = User.objects.create_user(
            username='sessioncoach@example.com',
            email='sessioncoach@example.com',
            password='coachpass123',
            first_name='Session',
            last_name='Coach'
        )

        self.coach = CrushCoach.objects.create(
            user=self.coach_user,
            bio='Session tracking coach',
            is_active=True,
            max_active_reviews=10
        )

        self.user = User.objects.create_user(
            username='sessionuser@example.com',
            email='sessionuser@example.com',
            password='testpass123',
            first_name='Session',
            last_name='User'
        )

        CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 5, 15),
            gender='M',
            location='Luxembourg',
            is_approved=True
        )

    def test_create_coach_session(self):
        """Test creating a coach session."""
        from crush_lu.models import CoachSession

        session = CoachSession.objects.create(
            coach=self.coach,
            user=self.user,
            session_type='feedback',
            notes='Reviewed profile changes'
        )

        self.assertIsNotNone(session)
        self.assertEqual(session.coach, self.coach)
        self.assertEqual(session.user, self.user)

    def test_session_tracking_multiple(self):
        """Test tracking multiple sessions for same user."""
        from crush_lu.models import CoachSession

        # Create multiple sessions
        for i in range(3):
            CoachSession.objects.create(
                coach=self.coach,
                user=self.user,
                session_type='followup',
                notes=f'Follow-up session {i}'
            )

        sessions = CoachSession.objects.filter(
            coach=self.coach,
            user=self.user
        )

        self.assertEqual(sessions.count(), 3)


@override_settings(**CRUSH_LU_URL_SETTINGS)
class CoachSpecializationTests(SiteTestMixin, TestCase):
    """Test coach specialization matching."""

    def setUp(self):
        """Set up coaches with different specializations."""
        from crush_lu.models import CrushCoach

        # Young professionals specialist
        self.coach_young = User.objects.create_user(
            username='coachy@example.com',
            email='coachy@example.com',
            password='coachpass123'
        )

        self.coach_young_obj = CrushCoach.objects.create(
            user=self.coach_young,
            bio='Young professionals coach',
            specializations='Young professionals (25-35)',
            is_active=True,
            max_active_reviews=10
        )

        # Senior specialist
        self.coach_senior = User.objects.create_user(
            username='coachs@example.com',
            email='coachs@example.com',
            password='coachpass123'
        )

        self.coach_senior_obj = CrushCoach.objects.create(
            user=self.coach_senior,
            bio='Senior professionals coach',
            specializations='35+ professionals',
            is_active=True,
            max_active_reviews=10
        )

    def test_coach_specializations_exist(self):
        """Test coaches have specializations."""
        self.assertIsNotNone(self.coach_young_obj.specializations)
        self.assertIsNotNone(self.coach_senior_obj.specializations)

    def test_filter_by_specialization(self):
        """Test filtering coaches by specialization."""
        from crush_lu.models import CrushCoach

        young_coaches = CrushCoach.objects.filter(
            specializations__icontains='Young'
        )

        self.assertEqual(young_coaches.count(), 1)
        self.assertEqual(young_coaches.first(), self.coach_young_obj)
