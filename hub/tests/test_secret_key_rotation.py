"""
Pins the confirmed gap documented in docs/ops/secret-key-rotation.md (Task
5.4b): djangorestframework-simplejwt 5.5.1's TokenBackend
(rest_framework_simplejwt/backends.py) holds a single signing/verifying key
with no fallback-list concept, unlike django.core.signing (see
CheckinTokenRotationTests in crush_lu/tests/test_secret_key_rotation.py for
the contrasting, working case).

A JWT minted under an "old" key is rejected by SimpleJWT's own AccessToken
verification even when that key is listed in SECRET_KEY_FALLBACKS. This is
not a bug this PR fixes — it's *why* a SECRET_KEY rotation forces a one-time
silent re-auth of Hub SPA staff through the session->JWT exchange
(azureproject/views_spa_auth.py) instead of the old JWT continuing to work.
If SimpleJWT ever grows native multi-key support, this test should start
failing and can be deleted alongside a comment update in settings.py.

Run with: python manage.py test hub.tests.test_secret_key_rotation
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework_simplejwt.backends import TokenBackend
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

User = get_user_model()

OLD_KEY = "an-old-key-that-signed-this-token"


class HubSsoJwtRotationGapTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="staff@example.com",
            email="staff@example.com",
            password="staffpass123",
            is_staff=True,
        )

    @override_settings(SECRET_KEY_FALLBACKS=[OLD_KEY])
    def test_jwt_signed_under_a_fallback_key_is_still_rejected(self):
        # Mint a token exactly as production code does (RefreshToken.for_user
        # in views_spa_auth.py), but sign it with what will become the "old"
        # key instead of the live SIMPLE_JWT["SIGNING_KEY"].
        old_backend = TokenBackend(algorithm="HS256", signing_key=OLD_KEY)
        token = AccessToken()
        token["user_id"] = str(self.user.pk)
        raw_token_signed_with_old_key = old_backend.encode(token.payload)

        # settings.SECRET_KEY_FALLBACKS lists OLD_KEY, yet verification
        # against the configured SIMPLE_JWT["SIGNING_KEY"] still fails:
        # SimpleJWT never consults SECRET_KEY_FALLBACKS.
        with self.assertRaises(TokenError):
            AccessToken(raw_token_signed_with_old_key)
