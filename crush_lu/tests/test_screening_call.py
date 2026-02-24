"""
Test for the enhanced screening call guideline feature.
"""
import json
from datetime import date
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from crush_lu.models import CrushCoach, CrushProfile, ProfileSubmission
from crush_lu.models.profiles import UserDataConsent

User = get_user_model()


@override_settings(ROOT_URLCONF='azureproject.urls_crush')
class TestScreeningCallView(TestCase):
    """Test the screening call view and data saving."""

    def setUp(self):
        """Set up test data."""
        # Create the coach user
        self.coach_user = User.objects.create_user(
            username='testcoach@example.com',
            email='testcoach@example.com',
            password='coachpass123',
            first_name='Test',
            last_name='Coach'
        )

        UserDataConsent.objects.filter(user=self.coach_user).update(crushlu_consent_given=True)

        self.coach = CrushCoach.objects.create(
            user=self.coach_user,
            bio='Test Coach Bio',
            specializations='General',
            is_active=True,
            max_active_reviews=10
        )

        # Create a user with a pending profile submission
        self.profile_user = User.objects.create_user(
            username='pendinguser@example.com',
            email='pendinguser@example.com',
            password='userpass123',
            first_name='Pending',
            last_name='User'
        )

        self.profile = CrushProfile.objects.create(
            user=self.profile_user,
            date_of_birth=date(1990, 5, 15),
            gender='F',
            location='Luxembourg City',
            looking_for='dating',
            bio='Test bio for screening call test',
            phone_number='+352123456789',
            event_languages=['en', 'de'],
            is_approved=False
        )

        self.submission = ProfileSubmission.objects.create(
            profile=self.profile,
            coach=self.coach,
            status='pending',
            review_call_completed=False
        )

    def test_mark_call_complete_saves_checklist_data(self):
        """Test that checklist data is properly saved when marking call complete."""
        # Login as coach
        self.client.login(username='testcoach@example.com', password='coachpass123')

        # Prepare checklist data
        checklist_data = {
            'introduction_complete': True,
            'language_confirmed': True,
            'residence_confirmed': True,
            'residence_notes': 'Lives in Luxembourg City',
            'expectations_discussed': True,
            'expectations_notes': 'Looking for serious relationship',
            'dating_preference_asked': True,
            'dating_preference_value': 'opposite_gender',
            'crush_meaning_asked': False,
            'crush_meaning_notes': '',
            'questions_answered': True,
            'questions_notes': 'Asked about event frequency'
        }

        # Make the POST request
        url = reverse('crush_lu:coach_mark_review_call_complete', args=[self.submission.id])
        response = self.client.post(url, {
            'call_notes': 'Great call, user is ready.',
            'checklist_data': json.dumps(checklist_data)
        }, HTTP_HX_REQUEST='true')

        # Check response
        self.assertEqual(response.status_code, 200)

        # Refresh submission from database
        self.submission.refresh_from_db()

        # Verify data was saved
        self.assertTrue(self.submission.review_call_completed)
        self.assertEqual(self.submission.review_call_notes, 'Great call, user is ready.')
        self.assertEqual(self.submission.review_call_checklist, checklist_data)
        self.assertTrue(self.submission.review_call_checklist['introduction_complete'])
        self.assertEqual(self.submission.review_call_checklist['dating_preference_value'], 'opposite_gender')
        self.assertEqual(self.submission.review_call_checklist['residence_notes'], 'Lives in Luxembourg City')

    def test_mark_call_complete_with_empty_checklist(self):
        """Test that submission works even with empty checklist data."""
        self.client.login(username='testcoach@example.com', password='coachpass123')

        url = reverse('crush_lu:coach_mark_review_call_complete', args=[self.submission.id])
        response = self.client.post(url, {
            'call_notes': 'Quick call.',
            'checklist_data': ''
        }, HTTP_HX_REQUEST='true')

        self.assertEqual(response.status_code, 200)

        self.submission.refresh_from_db()
        self.assertTrue(self.submission.review_call_completed)
        self.assertEqual(self.submission.review_call_checklist, {})

    def test_mark_call_complete_with_invalid_json(self):
        """Test that submission handles invalid JSON gracefully."""
        self.client.login(username='testcoach@example.com', password='coachpass123')

        url = reverse('crush_lu:coach_mark_review_call_complete', args=[self.submission.id])
        response = self.client.post(url, {
            'call_notes': 'Call done.',
            'checklist_data': 'not valid json {'
        }, HTTP_HX_REQUEST='true')

        self.assertEqual(response.status_code, 200)

        self.submission.refresh_from_db()
        self.assertTrue(self.submission.review_call_completed)
        self.assertEqual(self.submission.review_call_checklist, {})

    def test_htmx_response_shows_completed_state(self):
        """Test that the HTMX response shows the completed state."""
        self.client.login(username='testcoach@example.com', password='coachpass123')

        checklist_data = {
            'introduction_complete': True,
            'residence_confirmed': True,
            'dating_preference_asked': True,
            'dating_preference_value': 'opposite_gender',
        }

        url = reverse('crush_lu:coach_mark_review_call_complete', args=[self.submission.id])
        response = self.client.post(url, {
            'call_notes': 'Done.',
            'checklist_data': json.dumps(checklist_data)
        }, HTTP_HX_REQUEST='true')

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        # Check that completed state is shown (green header)
        self.assertIn('bg-green-500', content)

        # Should NOT show the form anymore
        self.assertNotIn('Mark Screening Call Complete', content)

    def test_non_htmx_request_redirects(self):
        """Test that non-HTMX requests redirect after completion."""
        self.client.login(username='testcoach@example.com', password='coachpass123')

        url = reverse('crush_lu:coach_mark_review_call_complete', args=[self.submission.id])
        response = self.client.post(url, {
            'call_notes': 'Done.',
            'checklist_data': '{}'
        })

        # Should redirect
        self.assertEqual(response.status_code, 302)

    def test_unauthorized_coach_cannot_mark_complete(self):
        """Test that a different coach cannot mark another coach's submission complete."""
        # Create another coach
        other_coach_user = User.objects.create_user(
            username='othercoach@example.com',
            email='othercoach@example.com',
            password='otherpass123'
        )
        UserDataConsent.objects.filter(user=other_coach_user).update(crushlu_consent_given=True)
        CrushCoach.objects.create(
            user=other_coach_user,
            bio='Other coach',
            is_active=True
        )

        self.client.login(username='othercoach@example.com', password='otherpass123')

        url = reverse('crush_lu:coach_mark_review_call_complete', args=[self.submission.id])
        response = self.client.post(url, {
            'call_notes': 'Trying to hijack.',
            'checklist_data': '{}'
        }, HTTP_HX_REQUEST='true')

        # Should show error (submission not found for this coach)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertTrue('not found' in content.lower() or 'error' in content.lower())
