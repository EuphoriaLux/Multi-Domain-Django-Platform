"""
Social login data cleanup (Facebook, Google, Microsoft).

The provider photo persisted at login (users/<id>/social_cache/) goes with the
SocialAccount on every deletion path (pre_delete hook), goes when the member
deletes their Crush.lu profile, and a refresh still in flight cannot put it
back. The storage here names files the way production's
CrushProfilePhotoStorage does (``<uuid>_<provider>.jpg``) and every photo is
written through the real _persist_social_photo(), so the tests see the names
production stores, not the path passed to save().

Run with: pytest crush_lu/tests/test_social_login_data_cleanup.py -v
"""

import io
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
from PIL import Image

from crush_lu.models import CrushProfile
from crush_lu.models.profiles import UserDataConsent
from crush_lu.social_photos import _persist_social_photo
from crush_lu.storage import CrushProfilePhotoStorage

User = get_user_model()
HOST = {"HTTP_HOST": "crush.lu"}


class _UuidNamedStorage(FileSystemStorage):
    """Local disk with production's naming: every save gets a uuid prefix."""

    get_available_name = CrushProfilePhotoStorage.get_available_name


def _jpeg():
    buffer = io.BytesIO()
    Image.new("RGB", (40, 40), "red").save(buffer, format="JPEG")
    return buffer.getvalue()


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
        self.storage = _UuidNamedStorage(location=tmpdir)
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

        self.folder = f"users/{self.user.id}/social_cache/"
        self.assertTrue(_persist_social_photo(self.account, _jpeg()))
        stored = self._cached("facebook")
        self.assertEqual(len(stored), 1)
        # Premise: the stored name is not the path that was passed to save().
        self.assertNotEqual(stored[0], "facebook.jpg")

    def _cached(self, provider, user=None):
        folder = f"users/{(user or self.user).id}/social_cache/"
        try:
            _dirs, files = self.storage.listdir(folder)
        except FileNotFoundError:
            return []
        return [name for name in files if name.endswith(f"{provider}.jpg")]


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class DisconnectSocialAccountCleanupTests(_SocialCacheMixin, TestCase):
    def _disconnect(self, account):
        self.client.force_login(self.user)
        return self.client.post(f"/en/account/disconnect/{account.id}/", **HOST)

    def test_disconnect_deletes_cached_photo_and_token(self):
        cache.set(f"social_photo_facebook_{self.account.id}", "https://x/y.jpg")

        response = self._disconnect(self.account)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._cached("facebook"), [])
        self.assertFalse(SocialAccount.objects.filter(user=self.user).exists())
        self.assertFalse(SocialToken.objects.exists())
        self.assertIsNone(cache.get(f"social_photo_persisted_exists_{self.account.id}"))
        self.assertIsNone(cache.get(f"social_photo_facebook_{self.account.id}"))

    def test_disconnect_keeps_photo_already_imported_into_the_profile(self):
        """An imported photo is the member's own profile photo now."""
        CrushProfile.objects.filter(pk=self.profile.pk).update(
            photo_1=f"users/{self.user.id}/photos/facebook_{self.user.id}.jpg"
        )

        self._disconnect(self.account)

        self.profile.refresh_from_db()
        self.assertEqual(
            self.profile.photo_1.name,
            f"users/{self.user.id}/photos/facebook_{self.user.id}.jpg",
        )

    def test_storage_failure_keeps_the_account_connected(self):
        """Fail closed: the member can retry instead of losing the copy."""
        with patch.object(self.storage, "delete", side_effect=OSError("down")):
            response = self._disconnect(self.account)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self._cached("facebook")), 1)
        self.assertTrue(SocialAccount.objects.filter(pk=self.account.pk).exists())
        self.assertTrue(SocialToken.objects.filter(account=self.account).exists())

    def test_provider_without_a_photo_cache_disconnects_during_an_outage(self):
        apple = SocialAccount.objects.create(
            user=self.user, provider="apple", uid="apple-1", extra_data={}
        )

        outage = OSError("down")
        with patch.object(self.storage, "listdir", side_effect=outage), patch.object(
            self.storage, "delete", side_effect=outage
        ):
            self._disconnect(apple)

        self.assertFalse(SocialAccount.objects.filter(pk=apple.pk).exists())


class SocialAccountDeleteHookTests(_SocialCacheMixin, TestCase):
    """Every path that deletes a SocialAccount takes the photo with it."""

    def test_queryset_delete_removes_the_cached_photo(self):
        # The path of allauth's connections page, the admin and full-account
        # deletion: none of them goes through the crush.lu disconnect view.
        SocialAccount.objects.filter(pk=self.account.pk).delete()

        self.assertEqual(self._cached("facebook"), [])

    def test_only_the_deleted_providers_photo_goes(self):
        google = SocialAccount.objects.create(
            user=self.user, provider="google", uid="g-1", extra_data={}
        )
        self.assertTrue(_persist_social_photo(google, _jpeg()))

        self.account.delete()

        self.assertEqual(self._cached("facebook"), [])
        self.assertEqual(len(self._cached("google")), 1)

    def test_storage_failure_rolls_the_delete_back(self):
        from django.db import transaction

        with patch.object(self.storage, "delete", side_effect=OSError("down")):
            with self.assertRaises(OSError):
                with transaction.atomic():
                    self.account.delete()

        self.assertTrue(SocialAccount.objects.filter(pk=self.account.pk).exists())

    def test_merge_moving_the_account_leaves_no_photo_behind(self):
        from crush_lu.services.account_merge import merge_accounts

        keeper = User.objects.create_user(
            username="keeper@example.com",
            email="keeper@example.com",
            password="testpass123",
        )

        merge_accounts(keeper, self.user)

        self.assertTrue(
            SocialAccount.objects.filter(pk=self.account.pk, user=keeper).exists()
        )
        self.assertEqual(self._cached("facebook"), [])

    def test_post_commit_pass_removes_a_copy_written_before_the_commit(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.account.delete()
        self.assertEqual(self._cached("facebook"), [])

        # A login refresh in flight lands between the hook and the commit.
        self.storage.save(self.folder + "facebook.jpg", ContentFile(b"late"))
        self.assertEqual(len(self._cached("facebook")), 1)
        for callback in callbacks:
            callback()

        self.assertEqual(self._cached("facebook"), [])


class InFlightRefreshTests(_SocialCacheMixin, TestCase):
    """A refresh whose provider fetch overlaps a disconnect or a deletion."""

    def test_refresh_finishing_after_disconnect_writes_nothing(self):
        stale = SocialAccount.objects.get(pk=self.account.pk)
        self.account.delete()

        self.assertFalse(_persist_social_photo(stale, _jpeg()))
        self.assertEqual(self._cached("facebook"), [])

    def test_deletion_landing_during_the_write_is_undone(self):
        real_save = self.storage.save

        def save_while_profile_deletion_bans(name, content, **kwargs):
            saved = real_save(name, content, **kwargs)
            UserDataConsent.objects.filter(user=self.user).update(crushlu_banned=True)
            return saved

        with patch.object(
            self.storage, "save", side_effect=save_while_profile_deletion_bans
        ):
            self.assertFalse(_persist_social_photo(self.account, _jpeg()))

        self.assertEqual(self._cached("facebook"), [])


@patch("crush_lu.storage.delete_user_storage", return_value=(True, 0))
class ProfileDeletionSocialCacheTests(_SocialCacheMixin, TestCase):
    """The users/<id>/ sweep is patched out: the explicit delete must do it."""

    def test_profile_deletion_erases_cached_photo_but_keeps_the_login(self, _sweep):
        from crush_lu.views import delete_crushlu_profile_only

        delete_crushlu_profile_only(self.user)

        self.assertEqual(self._cached("facebook"), [])
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

        self.assertEqual(len(self._cached("facebook")), 1)

    def test_later_login_does_not_collect_the_photo_again(self, _sweep):
        from crush_lu.social_photos import refresh_social_photo_cache
        from crush_lu.views import delete_crushlu_profile_only

        delete_crushlu_profile_only(self.user)

        with patch("crush_lu.social_photos.requests.get") as get:
            self.assertFalse(refresh_social_photo_cache(self.account))
        get.assert_not_called()
        self.assertEqual(self._cached("facebook"), [])
