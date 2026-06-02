"""
Tests for the verification_method field stamping.

The LuxID path is stamped in ``crush_lu.signals._execute_luxid_direct_verify``;
the coach/premium paths are covered in test_coach_mark_verified.py.
"""

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import TestCase, override_settings

User = get_user_model()

CRUSH_LU_URL_SETTINGS = {"ROOT_URLCONF": "azureproject.urls_crush"}


class SiteTestMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )


@override_settings(**CRUSH_LU_URL_SETTINGS)
class VerificationMethodTests(SiteTestMixin, TestCase):
    def setUp(self):
        from crush_lu.models import CrushProfile

        self.user = User.objects.create_user(
            username="lux@example.com", email="lux@example.com", password="pass12345"
        )
        self.profile = CrushProfile.objects.create(
            user=self.user,
            gender="M",
            location="Luxembourg",
            verification_status="pending",
        )

    def test_luxid_direct_verify_stamps_method(self):
        from crush_lu.signals import _execute_luxid_direct_verify

        _execute_luxid_direct_verify(
            self.user, self.profile, submission=None, request=None
        )

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.verification_status, "verified")
        self.assertEqual(self.profile.verification_method, "luxid")
        self.assertTrue(self.profile.is_approved)
