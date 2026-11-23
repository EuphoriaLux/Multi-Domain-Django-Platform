"""A pending member asks for a coach call to get verified.

The request opens an unassigned pending ProfileSubmission, which is what the
coach Verification Channel lists. Off unless
VERIFICATION_COACH_CALL_REQUESTS_ENABLED is set.
"""

from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from crush_lu.models import ProfileSubmission
from crush_lu.services import verification_call_request as call_request
from crush_lu.services import verification_nudges as nudges
from crush_lu.tests.test_verification_nudges import make_pending_user

URL = "/en/profile-submitted/request-call/"
PAGE = "/en/id/verify/"
BROADCAST = "crush_lu.coach_notifications.broadcast_new_submission_to_channel"


class CoachCallRequestTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_pending_user("caller")
        self.profile = self.user.crushprofile
        self.client = Client()
        self.client.force_login(self.user)

    def test_off_by_default(self):
        self.assertFalse(call_request.can_request_coach_call(self.profile))
        response = self.client.post(URL, HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ProfileSubmission.objects.exists())
        page = self.client.get(PAGE, HTTP_HOST="crush.lu").content.decode()
        self.assertNotIn('data-verification-option="coach-call"', page)

    @override_settings(VERIFICATION_COACH_CALL_REQUESTS_ENABLED=True)
    def test_page_offers_the_call_when_enabled(self):
        page = self.client.get(PAGE, HTTP_HOST="crush.lu").content.decode()
        self.assertIn('data-verification-option="coach-call"', page)
        self.assertIn(URL, page)

    @override_settings(VERIFICATION_COACH_CALL_REQUESTS_ENABLED=True)
    def test_request_opens_one_unassigned_submission_and_tells_coaches(self):
        with patch(BROADCAST) as broadcast, self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(URL, HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 302)
        submission = ProfileSubmission.objects.get(profile=self.profile)
        self.assertEqual(submission.status, "pending")
        self.assertIsNone(submission.coach)
        broadcast.assert_called_once_with(submission)
        # The profile stays pending: only a coach's approval verifies it.
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.verification_status, "pending")

        # A second tap opens nothing new, and the card is gone.
        with patch(BROADCAST):
            self.client.post(URL, HTTP_HOST="crush.lu")
        self.assertEqual(ProfileSubmission.objects.count(), 1)
        page = self.client.get(PAGE, HTTP_HOST="crush.lu").content.decode()
        self.assertNotIn('data-verification-option="coach-call"', page)

    @override_settings(VERIFICATION_COACH_CALL_REQUESTS_ENABLED=True)
    def test_expired_legacy_submission_does_not_block(self):
        ProfileSubmission.objects.create(profile=self.profile, status="expired")
        self.assertTrue(call_request.can_request_coach_call(self.profile))
        with patch(BROADCAST):
            self.assertIsNotNone(call_request.request_coach_call(self.profile))

    @override_settings(VERIFICATION_COACH_CALL_REQUESTS_ENABLED=True)
    def test_only_pending_profiles(self):
        for status in ("incomplete", "verified", "rejected"):
            self.profile.verification_status = status
            self.profile.save(update_fields=["verification_status"])
            self.assertFalse(call_request.can_request_coach_call(self.profile))
            self.assertIsNone(call_request.request_coach_call(self.profile))
        self.assertFalse(ProfileSubmission.objects.exists())

    @override_settings(VERIFICATION_COACH_CALL_REQUESTS_ENABLED=True)
    def test_reminder_email_mentions_the_call_only_when_enabled(self):
        context = nudges.build_context(self.user, self.profile, "en")
        self.assertTrue(context["coach_call_available"])
        with override_settings(VERIFICATION_COACH_CALL_REQUESTS_ENABLED=False):
            context = nudges.build_context(self.user, self.profile, "en")
        self.assertFalse(context["coach_call_available"])
