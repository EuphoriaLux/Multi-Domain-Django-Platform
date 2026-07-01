"""Tests for the 'not on WhatsApp' detection + email-fallback gate (issue #519).

Run with: pytest crush_lu/tests/test_whatsapp_fallback.py -v
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from crush_lu.models import CrushProfile
from crush_lu.services.whatsapp import can_send_whatsapp, mark_not_on_whatsapp

User = get_user_model()


class MarkNotOnWhatsAppTests(TestCase):
    def _profile(self, username, phone, verified=True, flagged=False):
        user = User.objects.create_user(username=username, email=username, password="x")
        return CrushProfile.objects.create(
            user=user,
            gender="F",
            location="Luxembourg",
            phone_number=phone,
            phone_verified=verified,
            not_on_whatsapp=flagged,
        )

    def test_flags_matching_profile(self):
        profile = self._profile("a@example.com", "+352123456789")
        updated = mark_not_on_whatsapp("+352123456789")
        self.assertEqual(updated, 1)
        profile.refresh_from_db()
        self.assertTrue(profile.not_on_whatsapp)

    def test_matches_plus_stripped_form_from_meta(self):
        # Meta echoes the recipient back without the leading '+'.
        profile = self._profile("b@example.com", "+352123456789")
        updated = mark_not_on_whatsapp("352123456789")
        self.assertEqual(updated, 1)
        profile.refresh_from_db()
        self.assertTrue(profile.not_on_whatsapp)

    def test_no_match_leaves_others_untouched(self):
        other = self._profile("c@example.com", "+352999999999")
        updated = mark_not_on_whatsapp("+352123456789")
        self.assertEqual(updated, 0)
        other.refresh_from_db()
        self.assertFalse(other.not_on_whatsapp)

    def test_empty_number_is_noop(self):
        self.assertEqual(mark_not_on_whatsapp(""), 0)


class CanSendWhatsAppTests(TestCase):
    def _profile(self, **kwargs):
        user = User.objects.create_user(
            username="gate@example.com", email="gate@example.com", password="x"
        )
        defaults = dict(
            gender="F",
            location="Luxembourg",
            phone_number="+352123456789",
            phone_verified=True,
            not_on_whatsapp=False,
        )
        defaults.update(kwargs)
        return CrushProfile.objects.create(user=user, **defaults)

    def test_true_for_verified_unflagged_profile(self):
        self.assertTrue(can_send_whatsapp(self._profile()))

    def test_false_when_flagged_not_on_whatsapp(self):
        self.assertFalse(can_send_whatsapp(self._profile(not_on_whatsapp=True)))

    def test_false_when_phone_unverified(self):
        self.assertFalse(can_send_whatsapp(self._profile(phone_verified=False)))

    def test_false_for_none(self):
        self.assertFalse(can_send_whatsapp(None))
