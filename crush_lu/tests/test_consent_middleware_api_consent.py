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
    # Create/re-enable/edit a push subscription or record device tracking.
    ("post", "/api/push/subscribe/"),
    ("post", "/api/push/refresh-subscription/"),
    ("post", "/api/push/preferences/"),
    ("post", "/api/push/mark-pwa-user/"),
    ("post", "/api/pwa/register-installation/"),
    # Linked-device metadata: not part of the pre-consent mobile bootstrap.
    ("get", "/api/mobile/ios/devices/"),
    ("post", "/api/mobile/ios/devices/register/"),
    ("post", "/api/mobile/ios/devices/preferences/"),
    ("get", "/api/mobile/android/devices/"),
    ("post", "/api/mobile/android/devices/register/"),
    ("post", "/api/mobile/android/devices/preferences/"),
]

# Allowlisted pre-consent routes. The view may answer anything (400, 404,
# 405, ...) as long as the middleware did not answer consent_required/banned.
ALLOWED_API = [
    ("get", "/api/csrf-token/"),
    ("get", "/api/auth/status/"),
    ("get", "/api/push/vapid-public-key/"),
    ("post", "/api/push/validate-subscription/"),
    ("post", "/api/push/unsubscribe/"),
    ("post", "/api/push/delete-subscription/"),
    ("get", "/api/push/subscriptions/"),
    ("get", "/api/push/pwa-status/"),
    ("get", "/api/mobile/ios/config/"),
    ("get", "/api/mobile/android/config/"),
    ("post", "/api/mobile/ios/devices/unregister/"),
    ("post", "/api/mobile/android/devices/unregister/"),
    ("post", "/api/coach/push/unsubscribe/"),
    ("post", "/api/coach/push/delete-subscription/"),
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

    def test_consentless_coach_can_revoke_but_not_create_coach_push(self):
        user = _make_user("coach@example.com", crushlu_consent_given=False)
        self.client.force_login(user)
        blocked = self._call("post", "/api/coach/push/subscribe/")
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.json(), {"code": "consent_required"})

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
        self.assertNotIn(
            "/api/mobile/", CrushConsentMiddleware.API_CONSENT_EXEMPT_PATHS
        )


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class ApiBearerTokenGateTests(TestCase):
    """DRF views accept JWT bearer tokens; the gates must see that user too."""

    def setUp(self):
        cache.clear()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )

    def _get(self, user, path="/api/referral/me/"):
        from rest_framework_simplejwt.tokens import AccessToken

        token = AccessToken.for_user(user)
        return self.client.get(path, HTTP_AUTHORIZATION=f"Bearer {token}", **HOST)

    def test_consentless_bearer_user_is_gated(self):
        user = _make_user("jwt1@example.com", crushlu_consent_given=False)
        response = self._get(user)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"code": "consent_required"})

    def test_banned_bearer_user_is_blocked(self):
        user = _make_user(
            "jwt2@example.com", crushlu_consent_given=True, crushlu_banned=True
        )
        response = self._get(user)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "banned"})

    def test_consenting_bearer_user_reaches_the_view(self):
        user = _make_user("jwt3@example.com", crushlu_consent_given=True)
        response = self._get(user)
        self.assertNotEqual(response.status_code, 403)

    def test_consentless_bearer_user_keeps_allowlisted_route(self):
        user = _make_user("jwt4@example.com", crushlu_consent_given=False)
        response = self._get(user, "/api/csrf-token/")
        self.assertEqual(response.status_code, 200)

    def test_clean_session_cannot_shield_a_banned_bearer_user(self):
        banned = _make_user(
            "jwt5@example.com", crushlu_consent_given=True, crushlu_banned=True
        )
        clean = _make_user("jwt6@example.com", crushlu_consent_given=True)
        self.client.force_login(clean)
        response = self._get(banned)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "banned"})

    def test_clean_bearer_cannot_shield_a_consentless_session_user(self):
        consentless = _make_user("jwt7@example.com", crushlu_consent_given=False)
        clean = _make_user("jwt8@example.com", crushlu_consent_given=True)
        self.client.force_login(consentless)
        response = self._get(clean)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"code": "consent_required"})

    def test_invalid_bearer_token_is_left_to_the_view(self):
        response = self.client.get(
            "/api/referral/me/", HTTP_AUTHORIZATION="Bearer not-a-jwt", **HOST
        )
        self.assertEqual(response.status_code, 401)


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
