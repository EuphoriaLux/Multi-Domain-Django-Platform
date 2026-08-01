"""
Tests for the PassKit web-service plumbing that do NOT require Apple signing
certificates.

The main wallet test module (test_apple_wallet.py) self-skips when
`certs/apple/crush-pass-cert.pem` is absent, so anything covered only there
runs in no CI lane. The logic here — webServiceURL resolution and auth-token
resolution by serial — is pure and must run unconditionally so regressions in
the fallback paths are caught.
"""

import pytest
from django.test import RequestFactory


# ---------------------------------------------------------------------------
# webServiceURL resolution
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _wallet_url_settings(settings):
    """Provide the handful of non-cert settings the resolver touches."""
    settings.WALLET_APPLE_WEB_SERVICE_URL = "https://crush.lu/wallet/v1"
    settings.PASSKIT_WEB_SERVICE_BASE_PATH = "/wallet/v1"


class TestResolveWebServiceUrl:
    """A pass with an authenticationToken MUST also carry a webServiceURL or
    iOS silently rejects it. resolve_web_service_url is what guarantees that."""

    def test_explicit_caller_url_takes_precedence(self, settings):
        from crush_lu.wallet.passkit_service import resolve_web_service_url

        # Highest precedence: an explicit caller-supplied URL (forwarded by the
        # PassKit update path) wins over both the setting and a request.
        request = RequestFactory().get("/", HTTP_HOST="other.example", secure=True)
        assert (
            resolve_web_service_url(request, web_service_url="https://forwarded.lu/wallet/v1")
            == "https://forwarded.lu/wallet/v1"
        )

    def test_setting_used_when_no_explicit_url(self, settings):
        from crush_lu.wallet.passkit_service import resolve_web_service_url

        settings.WALLET_APPLE_WEB_SERVICE_URL = "https://crush.lu/wallet/v1"
        # No request either — the setting alone must satisfy.
        assert resolve_web_service_url() == "https://crush.lu/wallet/v1"
        # Even with a request, the setting takes precedence over derivation.
        request = RequestFactory().get("/", HTTP_HOST="other.example", secure=True)
        assert resolve_web_service_url(request) == "https://crush.lu/wallet/v1"

    def test_derives_from_request_when_setting_unset(self, settings):
        from crush_lu.wallet.passkit_service import resolve_web_service_url

        settings.WALLET_APPLE_WEB_SERVICE_URL = ""
        # secure=True simulates the https scheme production sees behind the
        # Azure proxy (X-Forwarded-Proto -> request.is_secure()).
        request = RequestFactory().get("/en/dashboard/", HTTP_HOST="crush.lu", secure=True)
        assert resolve_web_service_url(request) == "https://crush.lu/wallet/v1"

    def test_explicit_url_used_when_setting_unset_and_no_request(self, settings):
        from crush_lu.wallet.passkit_service import resolve_web_service_url

        settings.WALLET_APPLE_WEB_SERVICE_URL = ""
        assert (
            resolve_web_service_url(None, web_service_url="https://forwarded.lu/wallet/v1")
            == "https://forwarded.lu/wallet/v1"
        )

    def test_returns_empty_when_neither_available(self, settings):
        from crush_lu.wallet.passkit_service import resolve_web_service_url

        settings.WALLET_APPLE_WEB_SERVICE_URL = ""
        assert resolve_web_service_url() == ""
        assert resolve_web_service_url(request=None) == ""

    def test_pathological_host_does_not_raise(self, settings):
        from crush_lu.wallet.passkit_service import resolve_web_service_url

        settings.WALLET_APPLE_WEB_SERVICE_URL = ""
        request = RequestFactory().get("/", HTTP_HOST="[invalid")
        # Must not raise — derivation is best-effort.
        resolve_web_service_url(request)


# ---------------------------------------------------------------------------
# Auth-token resolution by serial
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestResolveAuthToken:
    """The web service authenticates update requests with the pass's auth token.
    Member passes and event tickets store the token on the owner's profile, but
    use different serial shapes — the resolver must handle both."""

    def test_resolves_member_pass_token_by_serial(self, test_user_with_profile):
        from crush_lu.wallet.passkit_service import _resolve_auth_token_from_profile

        _user, profile = test_user_with_profile
        profile.apple_pass_serial = "abcdef0123456789"
        profile.apple_auth_token = "tok-member"
        profile.save(update_fields=["apple_pass_serial", "apple_auth_token"])

        assert (
            _resolve_auth_token_from_profile("pass.lu.crush", "abcdef0123456789")
            == "tok-member"
        )

    def test_returns_none_for_unknown_member_serial(self):
        from crush_lu.wallet.passkit_service import _resolve_auth_token_from_profile

        assert _resolve_auth_token_from_profile("pass.lu.crush", "nope") is None

    def test_resolves_event_ticket_token_via_registration(
        self, event_with_registrations
    ):
        from crush_lu.wallet.passkit_service import _resolve_auth_token_from_profile

        _event, registrations = event_with_registrations
        registration = registrations[0]
        registration.apple_wallet_ticket_serial = "evt-1-reg-2-deadbeef"
        registration.save(update_fields=["apple_wallet_ticket_serial"])

        profile = registration.user.crushprofile
        profile.apple_auth_token = "tok-event"
        profile.save(update_fields=["apple_auth_token"])

        # The evt-* serial must resolve to the owner profile's token.
        assert (
            _resolve_auth_token_from_profile("pass.lu.crush", "evt-1-reg-2-deadbeef")
            == "tok-event"
        )

    def test_returns_none_for_unknown_event_serial(self):
        from crush_lu.wallet.passkit_service import _resolve_auth_token_from_profile

        assert (
            _resolve_auth_token_from_profile("pass.lu.crush", "evt-9-reg-9-00000000")
            is None
        )
