"""Consent and ban gates on /api/ and /media/ (#1217, follow-up to #1216)."""

import json

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import TestCase, override_settings

from crush_lu.consent_middleware import CrushConsentMiddleware
from crush_lu.models import CrushProfile
from crush_lu.models.profiles import UserDataConsent

User = get_user_model()

HOST = {"HTTP_HOST": "crush.lu"}

# Routes the issue's acceptance criteria name; every one must be refused.
GATED_API = [
    ("post", "/api/profile/save-step1/"),
    ("get", "/api/profile/progress/"),
    ("get", "/api/phone/status/"),
    ("post", "/api/phone/whatsapp/send/"),
    ("post", "/api/crush-connect/join/"),
    ("post", "/api/polls/1/vote/"),
    ("get", "/api/referral/me/"),
    ("post", "/api/referral/redeem/"),
]

# Allowlisted pre-consent routes. The view may answer anything (400, 404,
# 405, ...) as long as the middleware did not answer consent_required/banned.
ALLOWED_API = [
    ("get", "/api/csrf-token/"),
    ("get", "/api/auth/status/"),
    ("get", "/api/push/vapid-public-key/"),
    ("post", "/api/push/subscribe/"),
    ("post", "/api/push/unsubscribe/"),
    ("post", "/api/push/mark-pwa-user/"),
    ("get", "/api/push/pwa-status/"),
    ("post", "/api/pwa/register-installation/"),
]


def _make_user(email, **consent):
    user = User.objects.create_user(
        username=email, email=email, password="test-password"
    )
    UserDataConsent.objects.update_or_create(user=user, defaults=consent)
    return user


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class ApiConsentGateTests(TestCase):
    def setUp(self):
        cache.clear()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )

    def _call(self, method, path):
        if method == "get":
            return self.client.get(path, **HOST)
        return self.client.post(
            path, data="{}", content_type="application/json", **HOST
        )

    def test_unauthenticated_api_is_unchanged(self):
        # Anonymous callers get the view's own answer, never the middleware's.
        for method, path in GATED_API + ALLOWED_API:
            with self.subTest(path=path):
                response = self._call(method, path)
                if response.get("Content-Type", "").startswith("application/json"):
                    body = response.json()
                    self.assertNotIn("consent_required", json.dumps(body))
                    self.assertNotEqual(body, {"error": "banned"})

    def test_consentless_member_gets_json_403_on_gated_routes(self):
        user = _make_user("noconsent@example.com", crushlu_consent_given=False)
        self.client.force_login(user)
        for method, path in GATED_API:
            with self.subTest(path=path):
                response = self._call(method, path)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json(), {"code": "consent_required"})
        self.assertFalse(CrushProfile.objects.filter(user=user).exists())

    def test_consentless_member_keeps_allowlisted_routes(self):
        user = _make_user("noconsent2@example.com", crushlu_consent_given=False)
        self.client.force_login(user)
        for method, path in ALLOWED_API:
            with self.subTest(path=path):
                response = self._call(method, path)
                body = (
                    response.json()
                    if response.get("Content-Type", "").startswith("application/json")
                    else {}
                )
                self.assertNotEqual(body.get("code"), "consent_required")
                self.assertNotEqual(body, {"error": "banned"})

    def test_member_without_consent_record_is_gated(self):
        user = User.objects.create_user(
            username="norecord@example.com", password="test-password"
        )
        UserDataConsent.objects.filter(user=user).delete()
        user = User.objects.get(pk=user.pk)
        self.client.force_login(user)
        response = self._call("get", "/api/referral/me/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"code": "consent_required"})

    def test_consenting_member_passes_the_middleware(self):
        user = _make_user("consent@example.com", crushlu_consent_given=True)
        self.client.force_login(user)
        for method, path in GATED_API:
            with self.subTest(path=path):
                response = self._call(method, path)
                self.assertNotEqual(response.status_code, 403, path)

    def test_deletion_tombstone_keeps_banned_answer(self):
        user = _make_user(
            "deleted2@example.com",
            crushlu_consent_given=False,
            crushlu_banned=True,
            crushlu_ban_reason="user_deletion",
        )
        self.client.force_login(user)
        response = self._call("post", "/api/profile/save-step1/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "banned"})

    def test_banned_member_blocked_on_push_allowlist(self):
        # Pre-consent allowlist is not a ban exemption.
        user = _make_user(
            "banned2@example.com",
            crushlu_consent_given=True,
            crushlu_banned=True,
            crushlu_ban_reason="admin",
        )
        self.client.force_login(user)
        response = self._call("post", "/api/push/subscribe/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "banned"})

    def test_allowlist_covers_exactly_the_documented_extras(self):
        extras = set(CrushConsentMiddleware.API_CONSENT_EXEMPT_PATHS) - set(
            CrushConsentMiddleware.API_BAN_EXEMPT_PATHS
        )
        self.assertNotIn("/api/phone/", extras)
        self.assertFalse(any(p.startswith("/api/phone/") for p in extras))
        self.assertNotIn("/api/push/", extras)  # no blanket prefix


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class NonApiExemptionAuditTests(TestCase):
    """#1217 audit: /membership/ is gated, /oauth/ and /invite/ stay exempt."""

    def setUp(self):
        cache.clear()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )

    def test_membership_redirects_consentless_member(self):
        user = _make_user("m1@example.com", crushlu_consent_given=False)
        self.client.force_login(user)
        response = self.client.get("/en/membership/", **HOST)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/consent/confirm/", response["Location"])

    def test_membership_redirects_banned_member(self):
        user = _make_user(
            "m2@example.com", crushlu_consent_given=True, crushlu_banned=True
        )
        self.client.force_login(user)
        response = self.client.get("/en/membership/", **HOST)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/account/banned/", response["Location"])

    def test_membership_stays_public_for_anonymous(self):
        response = self.client.get("/en/membership/", **HOST)
        self.assertEqual(response.status_code, 200)

    def test_oauth_and_invite_remain_exempt(self):
        user = _make_user("m3@example.com", crushlu_consent_given=False)
        self.client.force_login(user)
        for path in ("/en/oauth/landing/", "/en/oauth/popup-error/"):
            with self.subTest(path=path):
                response = self.client.get(path, **HOST)
                self.assertNotIn("/consent/confirm/", response.get("Location", ""))
        mw = CrushConsentMiddleware(lambda r: None)
        self.assertTrue(mw._is_exempt_path("/en/invite/abc/accept/"))
        self.assertTrue(mw._is_exempt_path("/oauth/landing/"))
        self.assertFalse(mw._is_exempt_path("/membership/"))


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class BannedViewerMediaTests(TestCase):
    def setUp(self):
        cache.clear()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )
        self.owner = _make_user("owner@example.com", crushlu_consent_given=True)
        CrushProfile.objects.create(
            user=self.owner, date_of_birth="1990-01-01", is_approved=True
        )

    def _get(self, user):
        self.client.force_login(user)
        return self.client.get(f"/en/media/profile/{self.owner.pk}/photo_1/", **HOST)

    def test_banned_viewer_gets_403(self):
        viewer = _make_user(
            "bv@example.com", crushlu_consent_given=True, crushlu_banned=True
        )
        self.assertEqual(self._get(viewer).status_code, 403)

    def test_banned_owner_cannot_fetch_own_photo(self):
        UserDataConsent.objects.filter(user=self.owner).update(crushlu_banned=True)
        self.assertEqual(self._get(User.objects.get(pk=self.owner.pk)).status_code, 403)

    def test_unbanned_owner_not_blocked_by_ban_check(self):
        # Reaches the "no photo uploaded" 404 instead of the ban 403.
        self.assertEqual(self._get(self.owner).status_code, 404)

    def test_banned_viewer_cannot_fetch_coach_photo(self):
        viewer = _make_user(
            "bv2@example.com", crushlu_consent_given=True, crushlu_banned=True
        )
        self.client.force_login(viewer)
        response = self.client.get("/en/media/coach/1/", **HOST)
        self.assertEqual(response.status_code, 403)

    def test_anonymous_media_stays_database_free(self):
        with self.assertNumQueries(0):
            CrushConsentMiddleware(lambda r: None)._is_exempt_path(
                "/media/profile/1/photo_1/"
            )
