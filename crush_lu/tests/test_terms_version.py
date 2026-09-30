"""Issue #1113 / owner decision C: every consent writer stamps the canonical
``legal.CURRENT_TERMS_VERSION``; existing rows are never rewritten."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from crush_lu import legal
from crush_lu.models.profiles import UserDataConsent

HOST = {"HTTP_HOST": "crush.lu"}
User = get_user_model()


def _fresh(user):
    return UserDataConsent.objects.get(user=user)


class TermsVersionStampTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("tv", "tv@example.com", "pw12345678")
        # A post_save signal may already have created the row; start clean.
        UserDataConsent.objects.filter(user=self.user).delete()

    def test_constant_matches_published_legal_pages(self):
        for path in ("/en/terms-of-service/", "/en/privacy-policy/"):
            response = self.client.get(path, **HOST)
            self.assertContains(response, f"Version {legal.CURRENT_TERMS_VERSION}")

    def test_new_row_with_consent_is_stamped(self):
        UserDataConsent.objects.create(
            user=self.user,
            powerup_consent_given=True,
            crushlu_consent_given=True,
            crushlu_consent_date=timezone.now(),
        )
        row = _fresh(self.user)
        self.assertEqual(row.powerup_terms_version, legal.CURRENT_TERMS_VERSION)
        self.assertEqual(row.crushlu_terms_version, legal.CURRENT_TERMS_VERSION)

    def test_changing_the_constant_changes_what_is_written(self):
        with mock.patch.object(legal, "CURRENT_TERMS_VERSION", "9.9"):
            UserDataConsent.objects.create(user=self.user, crushlu_consent_given=True)
        self.assertEqual(_fresh(self.user).crushlu_terms_version, "9.9")

    def test_get_or_create_defaults_are_stamped(self):
        with mock.patch.object(legal, "CURRENT_TERMS_VERSION", "7.1"):
            UserDataConsent.objects.get_or_create(
                user=self.user,
                defaults={"crushlu_consent_given": True, "powerup_consent_given": True},
            )
        row = _fresh(self.user)
        self.assertEqual(row.crushlu_terms_version, "7.1")
        self.assertEqual(row.powerup_terms_version, "7.1")

    def test_not_given_layer_is_not_stamped(self):
        UserDataConsent.objects.create(user=self.user, powerup_consent_given=True)
        row = _fresh(self.user)
        self.assertEqual(row.powerup_terms_version, legal.CURRENT_TERMS_VERSION)
        self.assertEqual(row.crushlu_terms_version, "1.0")

    def test_flip_to_given_with_update_fields_is_stamped(self):
        consent = UserDataConsent.objects.create(user=self.user)
        consent.crushlu_consent_given = True
        consent.crushlu_consent_date = timezone.now()
        with mock.patch.object(legal, "CURRENT_TERMS_VERSION", "3.0"):
            consent.save(
                update_fields=["crushlu_consent_given", "crushlu_consent_date"]
            )
        self.assertEqual(_fresh(self.user).crushlu_terms_version, "3.0")

    def test_existing_rows_keep_their_version_on_unrelated_save(self):
        consent = UserDataConsent.objects.create(user=self.user)
        UserDataConsent.objects.filter(pk=consent.pk).update(
            crushlu_consent_given=True,
            crushlu_consent_date=timezone.now(),
            crushlu_terms_version="1.0",
        )
        consent = _fresh(self.user)
        consent.marketing_consent = True
        with mock.patch.object(legal, "CURRENT_TERMS_VERSION", "5.0"):
            consent.save()
        self.assertEqual(_fresh(self.user).crushlu_terms_version, "1.0")

    def test_reconsent_with_new_date_restamps(self):
        consent = UserDataConsent.objects.create(
            user=self.user,
            crushlu_consent_given=True,
            crushlu_consent_date=timezone.now(),
        )
        consent.crushlu_consent_date = timezone.now()
        with mock.patch.object(legal, "CURRENT_TERMS_VERSION", "4.0"):
            consent.save()
        self.assertEqual(_fresh(self.user).crushlu_terms_version, "4.0")


class TermsVersionWriterTests(TestCase):
    """The real writers, not just the model."""

    def setUp(self):
        cache.clear()

    def test_retroactive_consent_view_stamps(self):
        user = User.objects.create_user("rc", "rc@example.com", "pw12345678")
        UserDataConsent.objects.update_or_create(
            user=user, defaults={"crushlu_consent_given": False}
        )
        self.client.force_login(user)
        with mock.patch.object(legal, "CURRENT_TERMS_VERSION", "6.6"):
            response = self.client.post(
                "/en/consent/confirm/", {"crushlu_consent": "on"}, **HOST
            )
        self.assertEqual(response.status_code, 302)
        row = _fresh(user)
        self.assertTrue(row.crushlu_consent_given)
        self.assertEqual(row.crushlu_terms_version, "6.6")

    def test_signup_signal_stamps_powerup_consent(self):
        with mock.patch.object(legal, "CURRENT_TERMS_VERSION", "8.8"):
            user = User.objects.create_user("su", "su@example.com", "pw12345678")
        row = UserDataConsent.objects.filter(user=user).first()
        self.assertIsNotNone(row)
        self.assertEqual(row.powerup_terms_version, "8.8")

    def test_signup_form_save_stamps_crushlu_consent(self):
        from allauth.account.forms import SignupForm
        from django.test import RequestFactory

        from crush_lu.forms import CrushSignupForm

        user = User.objects.create_user("sf", "sf@example.com", "pw12345678")
        UserDataConsent.objects.filter(user=user).delete()
        request = RequestFactory().post("/en/signup/", **HOST)
        form = CrushSignupForm()
        form.cleaned_data = {
            "first_name": "Sam",
            "last_name": "",
            "crushlu_consent": True,
            "marketing_consent": False,
        }
        with mock.patch.object(
            SignupForm, "save", return_value=user
        ), mock.patch.object(legal, "CURRENT_TERMS_VERSION", "2.2"):
            form.save(request)
        row = _fresh(user)
        self.assertTrue(row.crushlu_consent_given)
        self.assertEqual(row.crushlu_terms_version, "2.2")

    def test_oauth_implicit_consent_branch_stamps(self):
        from crush_lu import signals

        signals._thread_local.oauth_consent_data = {
            "crushlu_consent": True,
            "crushlu_consent_ip": "1.2.3.4",
        }
        try:
            with mock.patch.object(legal, "CURRENT_TERMS_VERSION", "3.3"):
                user = User.objects.create_user("oa", "oa@example.com", "pw12345678")
        finally:
            signals._thread_local.oauth_consent_data = None
        row = _fresh(user)
        self.assertTrue(row.crushlu_consent_given)
        self.assertEqual(row.crushlu_terms_version, "3.3")
        self.assertEqual(row.powerup_terms_version, "3.3")

    def test_social_signup_hook_stamps_with_update_fields(self):
        from types import SimpleNamespace

        from django.test import RequestFactory

        from crush_lu.signals import record_interactive_social_signup_consent

        user = User.objects.create_user("ss", "ss@example.com", "pw12345678")
        UserDataConsent.objects.filter(user=user).update(
            crushlu_consent_given=False, crushlu_terms_version="1.0"
        )
        request = RequestFactory().post(
            "/accounts/3rdparty/signup/", {"crushlu_consent": "on"}, **HOST
        )
        request.resolver_match = SimpleNamespace(url_name="socialaccount_signup")
        with mock.patch.object(legal, "CURRENT_TERMS_VERSION", "4.4"):
            record_interactive_social_signup_consent(
                sender=None, request=request, user=user, sociallogin=object()
            )
        row = _fresh(user)
        self.assertTrue(row.crushlu_consent_given)
        self.assertEqual(row.crushlu_terms_version, "4.4")

    def test_account_data_view_get_or_create_stamps_powerup(self):
        from django.test import RequestFactory

        from crush_lu import views_account

        user = User.objects.create_user("ad", "ad@example.com", "pw12345678")
        UserDataConsent.objects.filter(user=user).delete()
        # Call the view directly: the consent middleware would redirect a user
        # without a consent row before the view ever ran.
        request = RequestFactory().get("/en/account/gdpr/", **HOST)
        request.user = user
        with mock.patch.object(legal, "CURRENT_TERMS_VERSION", "5.5"):
            try:
                views_account.gdpr_data_management(request)
            except Exception:  # rendering needs the full middleware stack
                pass
        row = UserDataConsent.objects.filter(user=user).first()
        self.assertIsNotNone(row)
        self.assertEqual(row.powerup_terms_version, "5.5")
