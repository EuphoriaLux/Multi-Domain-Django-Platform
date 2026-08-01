"""
Tests for the PassKit web-service plumbing that do NOT require Apple signing
certificates.

The main wallet test module (test_apple_wallet.py) self-skips when
`certs/apple/crush-pass-cert.pem` is absent, so anything covered only there
runs in no CI lane. The logic here — webServiceURL resolution, the web-service
authentication model, and check-in host preservation — is pure and must run
unconditionally so regressions in the update flow are caught.

Key invariant tested throughout: the webServiceURL embedded in pass.json is
the PassKit service ROOT (e.g. https://crush.lu/wallet). Apple appends its own
"/v1/..." protocol paths to it; embedding a versioned path produces
/wallet/v1/v1/... and every web-service request 404s.
"""

import pytest
from django.test import RequestFactory


# ---------------------------------------------------------------------------
# webServiceURL resolution
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _wallet_url_settings(settings):
    """Provide the handful of non-cert settings the resolver touches.

    Defaults reflect the corrected invariant: the embedded root is unversioned
    (/wallet), and the documented explicit setting is also the root.
    """
    settings.WALLET_APPLE_WEB_SERVICE_URL = "https://crush.lu/wallet"
    settings.PASSKIT_WEB_SERVICE_BASE_PATH = "/wallet"


class TestResolveWebServiceUrl:
    """A pass with an authenticationToken MUST also carry a webServiceURL or
    iOS silently rejects it. resolve_web_service_url is what guarantees that,
    and it MUST produce the unversioned PassKit root (Apple appends /v1)."""

    def test_setting_wins_over_forwarded_arg_and_request(self, settings):
        from crush_lu.wallet.passkit_service import resolve_web_service_url

        # The setting is canonical (operator-controlled, can't drift into /v1).
        # It must beat the caller arg forwarded from get_latest_pass, otherwise
        # a rebuild could rewrite a correct root to a versioned path.
        request = RequestFactory().get("/", HTTP_HOST="other.example", secure=True)
        assert (
            resolve_web_service_url(
                request, web_service_url="https://forwarded.lu/wallet"
            )
            == "https://crush.lu/wallet"
        )

    def test_setting_used_when_no_explicit_url(self, settings):
        from crush_lu.wallet.passkit_service import resolve_web_service_url

        settings.WALLET_APPLE_WEB_SERVICE_URL = "https://crush.lu/wallet"
        # No request either — the setting alone must satisfy.
        assert resolve_web_service_url() == "https://crush.lu/wallet"

    def test_derives_unversioned_root_from_request(self, settings):
        from crush_lu.wallet.passkit_service import resolve_web_service_url

        settings.WALLET_APPLE_WEB_SERVICE_URL = ""
        # secure=True simulates the https scheme production sees behind the
        # Azure proxy (X-Forwarded-Proto -> request.is_secure()).
        request = RequestFactory().get("/en/dashboard/", HTTP_HOST="crush.lu", secure=True)
        # Must be the ROOT — Apple appends /v1 itself.
        assert resolve_web_service_url(request) == "https://crush.lu/wallet"

    def test_forwarded_arg_used_when_setting_unset_and_no_request(self, settings):
        from crush_lu.wallet.passkit_service import resolve_web_service_url

        settings.WALLET_APPLE_WEB_SERVICE_URL = ""
        assert (
            resolve_web_service_url(None, web_service_url="https://forwarded.lu/wallet")
            == "https://forwarded.lu/wallet"
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

    def test_build_web_service_url_is_unversioned_root(self):
        from crush_lu.wallet.passkit_service import build_web_service_url

        request = RequestFactory().get("/", HTTP_HOST="crush.lu", secure=True)
        # The embedded webServiceURL root — NOT /wallet/v1, which would make
        # Apple hit /wallet/v1/v1/devices/... and 404.
        assert build_web_service_url(request) == "https://crush.lu/wallet"


# ---------------------------------------------------------------------------
# Web-service authorization (per-serial + shared token for the list endpoint)
# ---------------------------------------------------------------------------


def _authed_request(token):
    return RequestFactory().get(
        "/", HTTP_AUTHORIZATION=f"ApplePass {token}"
    )


@pytest.mark.django_db
class TestRequireAuthorization:
    """Per-serial endpoints authenticate with the pass's per-profile token.
    The serial-less GET 'list registrations' endpoint can't do that (there's no
    serial in the URL), so it falls back to a shared PASSKIT_AUTH_TOKEN."""

    def test_per_serial_endpoint_accepts_profile_token(
        self, settings, test_user_with_profile
    ):
        from crush_lu.wallet.passkit_service import _require_authorization

        settings.PASSKIT_AUTH_TOKEN = ""  # no shared fallback
        _user, profile = test_user_with_profile
        profile.apple_pass_serial = "member-serial"
        profile.apple_auth_token = "tok-member"
        profile.save(update_fields=["apple_pass_serial", "apple_auth_token"])

        request = _authed_request("tok-member")
        assert _require_authorization(request, "pass.lu.crush", "member-serial") is None

    def test_per_serial_endpoint_rejects_wrong_token(
        self, settings, test_user_with_profile
    ):
        from crush_lu.wallet.passkit_service import _require_authorization

        settings.PASSKIT_AUTH_TOKEN = ""  # no shared fallback
        # A real serial with a real token, then we send the wrong token.
        _user, profile = test_user_with_profile
        profile.apple_pass_serial = "member-serial"
        profile.apple_auth_token = "tok-member"
        profile.save(update_fields=["apple_pass_serial", "apple_auth_token"])

        request = _authed_request("not-the-token")
        resp = _require_authorization(request, "pass.lu.crush", "member-serial")
        assert resp.status_code == 401

    def test_list_endpoint_accepts_shared_token_when_no_serial(
        self, settings, test_user_with_profile
    ):
        from crush_lu.wallet.passkit_service import _require_authorization

        # serial_number=None is the GET /devices/.../registrations/<passType>
        # call, which has no per-pass token to look up.
        settings.PASSKIT_AUTH_TOKEN = "shared-tok"
        request = _authed_request("shared-tok")
        assert _require_authorization(request, "pass.lu.crush", None) is None

    def test_list_endpoint_500s_without_any_token_configured(
        self, settings, test_user_with_profile
    ):
        from crush_lu.wallet.passkit_service import _require_authorization

        settings.PASSKIT_AUTH_TOKEN = ""
        request = _authed_request("anything")
        resp = _require_authorization(request, "pass.lu.crush", None)
        assert resp.status_code == 500

    def test_list_endpoint_401s_with_wrong_shared_token(
        self, settings, test_user_with_profile
    ):
        from crush_lu.wallet.passkit_service import _require_authorization

        settings.PASSKIT_AUTH_TOKEN = "shared-tok"
        request = _authed_request("wrong")
        resp = _require_authorization(request, "pass.lu.crush", None)
        assert resp.status_code == 401


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


# ---------------------------------------------------------------------------
# Check-in host preservation on rebuild (finding E)
# ---------------------------------------------------------------------------


class TestOriginFromUrl:
    """When a ticket is rebuilt via the PassKit web service, the check-in URL
    must keep its original host (staging vs production), derived from the
    forwarded web_service_url rather than flipping to the hardcoded crush.lu."""

    def test_extracts_origin(self):
        from crush_lu.wallet.apple_event_ticket import _origin_from_url

        assert _origin_from_url("https://test.crush.lu/wallet") == "https://test.crush.lu"
        assert _origin_from_url("https://crush.lu/wallet") == "https://crush.lu"

    def test_returns_none_for_empty_or_unparseable(self):
        from crush_lu.wallet.apple_event_ticket import _origin_from_url

        assert _origin_from_url("") is None
        assert _origin_from_url(None) is None
        assert _origin_from_url("not-a-url") is None
