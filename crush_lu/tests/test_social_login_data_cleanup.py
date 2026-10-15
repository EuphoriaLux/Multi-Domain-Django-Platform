"""
Facebook (and other social login) data cleanup.

Disconnecting a provider and deleting the Crush.lu profile both remove the
provider photo persisted at login (users/<id>/social_cache/<provider>.jpg).
Asserted against a real storage backend, not a mock.

Run with: pytest crush_lu/tests/test_social_login_data_cleanup.py -v
"""

import shutil
import tempfile
from datetime import date
from unittest.mock import patch

from allauth.socialaccount.models import SocialAccount, SocialToken
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.test import TestCase, override_settings

from crush_lu.models import CrushProfile
from crush_lu.models.profiles import UserDataConsent

User = get_user_model()
HOST = {"HTTP_HOST": "crush.lu"}


class _SocialCacheMixin:
    """A member with a Facebook login, a stored token and a persisted photo."""

    def setUp(self):
        cache.clear()
        # The Facebook post_save handler creates a profile when this thread-
        # local flag leaks in from an earlier login test on the same worker.
        from crush_lu import signals

        signals._thread_local.is_crush_facebook_login = False

        tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        self.storage = FileSystemStorage(location=tmpdir)
        storage_patch = patch(
            "crush_lu.social_photos._get_photo_storage", return_value=self.storage
        )
        storage_patch.start()
        self.addCleanup(storage_patch.stop)

        self.user = User.objects.create_user(
            username="fb@example.com",
            email="fb@example.com",
            password="testpass123",
        )
        UserDataConsent.objects.update_or_create(
            user=self.user, defaults={"crushlu_consent_given": True}
        )
        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 5, 15),
            gender="F",
            location="Luxembourg",
        )
        self.account = SocialAccount.objects.create(
            user=self.user,
            provider="facebook",
            uid="fb-123",
            extra_data={"id": "fb-123"},
        )
        SocialToken.objects.create(account=self.account, token="fb-access-token")

        self.cache_path = f"users/{self.user.id}/social_cache/facebook.jpg"
        self.storage.save(self.cache_path, ContentFile(b"jpeg-bytes"))
        self.assertTrue(self.storage.exists(self.cache_path))


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class DisconnectSocialAccountCleanupTests(_SocialCacheMixin, TestCase):
    def _disconnect(self):
        self.client.force_login(self.user)
        return self.client.post(f"/en/account/disconnect/{self.account.id}/", **HOST)

    def test_disconnect_deletes_cached_photo_and_token(self):
        cache.set(f"social_photo_persisted_exists_{self.account.id}", True)
        cache.set(f"social_photo_facebook_{self.account.id}", "https://x/y.jpg")

        response = self._disconnect()

        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.storage.exists(self.cache_path))
        self.assertFalse(SocialAccount.objects.filter(user=self.user).exists())
        self.assertFalse(SocialToken.objects.exists())
        self.assertIsNone(cache.get(f"social_photo_persisted_exists_{self.account.id}"))
        self.assertIsNone(cache.get(f"social_photo_facebook_{self.account.id}"))

    def test_disconnect_keeps_photo_already_imported_into_the_profile(self):
        """An imported photo is the member's own profile photo now."""
        CrushProfile.objects.filter(pk=self.profile.pk).update(
            photo_1=f"users/{self.user.id}/photos/facebook_{self.user.id}.jpg"
        )

        self._disconnect()

        self.profile.refresh_from_db()
        self.assertEqual(
            self.profile.photo_1.name,
            f"users/{self.user.id}/photos/facebook_{self.user.id}.jpg",
        )

    def test_storage_failure_keeps_the_account_connected(self):
        """Fail closed: the member can retry instead of losing the copy."""
        with patch.object(self.storage, "delete", side_effect=OSError("down")):
            response = self._disconnect()

        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.storage.exists(self.cache_path))
        self.assertTrue(SocialAccount.objects.filter(pk=self.account.pk).exists())
        self.assertTrue(SocialToken.objects.filter(account=self.account).exists())


@patch("crush_lu.storage.delete_user_storage", return_value=(True, 0))
class ProfileDeletionSocialCacheTests(_SocialCacheMixin, TestCase):
    """The users/<id>/ sweep is patched out: the explicit delete must do it."""

    def test_profile_deletion_erases_cached_photo_but_keeps_the_login(self, _sweep):
        from crush_lu.views import delete_crushlu_profile_only

        delete_crushlu_profile_only(self.user)

        self.assertFalse(self.storage.exists(self.cache_path))
        # The account survives profile deletion and Facebook stays a way to
        # sign in to it, so the login records stay.
        self.assertTrue(SocialAccount.objects.filter(pk=self.account.pk).exists())
        self.assertTrue(SocialToken.objects.filter(account=self.account).exists())

    def test_profile_deletion_fails_closed_when_the_photo_cannot_be_deleted(
        self, _sweep
    ):
        from crush_lu.views import delete_crushlu_profile_only
        from crush_lu.views_account import StorageErasureError

        with patch.object(self.storage, "delete", side_effect=OSError("down")):
            with self.assertRaises(StorageErasureError):
                delete_crushlu_profile_only(self.user)

        self.assertTrue(self.storage.exists(self.cache_path))

    def test_later_login_does_not_collect_the_photo_again(self, _sweep):
        from crush_lu.social_photos import refresh_social_photo_cache
        from crush_lu.views import delete_crushlu_profile_only

        delete_crushlu_profile_only(self.user)

        with patch("crush_lu.social_photos.requests.get") as get:
            self.assertFalse(refresh_social_photo_cache(self.account))
        get.assert_not_called()
        self.assertFalse(self.storage.exists(self.cache_path))
