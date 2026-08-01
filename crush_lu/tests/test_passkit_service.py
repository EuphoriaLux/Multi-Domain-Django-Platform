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

    def test_issuing_request_wins_over_setting(self, settings):
        from crush_lu.wallet.passkit_service import resolve_web_service_url

        # THE slot-routing invariant. The web service resolves a pass by serial
        # against the database of whichever slot Apple contacts, and the slots
        # have isolated databases. A ticket downloaded from test.crush.lu that
        # advertised the production root would send Apple to production, which
        # has never heard of that serial -> registration 500s forever.
        request = RequestFactory().get("/", HTTP_HOST="test.crush.lu", secure=True)
        assert resolve_web_service_url(request) == "https://test.crush.lu/wallet"

    def test_issuing_request_wins_over_forwarded_arg_too(self, settings):
        from crush_lu.wallet.passkit_service import resolve_web_service_url

        request = RequestFactory().get("/", HTTP_HOST="test.crush.lu", secure=True)
        assert (
            resolve_web_service_url(
                request, web_service_url="https://forwarded.lu/wallet"
            )
            == "https://test.crush.lu/wallet"
        )

    def test_forwarded_arg_wins_over_setting_on_requestless_rebuild(self, settings):
        from crush_lu.wallet.passkit_service import resolve_web_service_url

        # get_latest_pass derives the forwarded value from Apple's own live
        # request, so it names the slot Apple is already talking to — which is
        # the slot that just resolved this serial. The setting must not drag the
        # rebuilt pass to a different slot.
        assert (
            resolve_web_service_url(
                None, web_service_url="https://test.crush.lu/wallet"
            )
            == "https://test.crush.lu/wallet"
        )

    def test_setting_used_when_no_request_and_no_forwarded_url(self, settings):
        from crush_lu.wallet.passkit_service import resolve_web_service_url

        settings.WALLET_APPLE_WEB_SERVICE_URL = "https://crush.lu/wallet"
        # The operator override survives as the no-request-context fallback
        # (management commands, background rebuilds).
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

    def test_unusable_request_host_falls_back_instead_of_dropping_the_field(
        self, settings
    ):
        from crush_lu.wallet.passkit_service import resolve_web_service_url

        settings.WALLET_APPLE_WEB_SERVICE_URL = "https://crush.lu/wallet"
        request = RequestFactory().get("/", HTTP_HOST="[invalid")
        # A pass carrying an authenticationToken with no webServiceURL is
        # rejected by iOS, so a broken host must fall through to the setting,
        # not return "".
        assert resolve_web_service_url(request) == "https://crush.lu/wallet"

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


class TestNormalizeServiceRoot:
    """The live App Service configuration has carried the legacy versioned
    value (https://crush.lu/wallet/v1) since the feature shipped, and the
    setting has HIGHEST precedence — so without normalization, fixing the code
    would leave production 404ing until someone also edited the App Setting."""

    def test_legacy_versioned_setting_is_corrected(self, settings):
        from crush_lu.wallet.passkit_service import resolve_web_service_url

        settings.WALLET_APPLE_WEB_SERVICE_URL = "https://crush.lu/wallet/v1"
        assert resolve_web_service_url() == "https://crush.lu/wallet"

    def test_trailing_slash_is_stripped(self, settings):
        from crush_lu.wallet.passkit_service import resolve_web_service_url

        # "https://crush.lu/wallet/" + "/v1/devices/..." => "//v1/..." — matches
        # no Django route either.
        settings.WALLET_APPLE_WEB_SERVICE_URL = "https://crush.lu/wallet/"
        assert resolve_web_service_url() == "https://crush.lu/wallet"

    def test_versioned_with_trailing_slash_is_corrected(self, settings):
        from crush_lu.wallet.passkit_service import resolve_web_service_url

        settings.WALLET_APPLE_WEB_SERVICE_URL = "https://crush.lu/wallet/v1/"
        assert resolve_web_service_url() == "https://crush.lu/wallet"

    def test_forwarded_arg_is_normalized_too(self, settings):
        from crush_lu.wallet.passkit_service import resolve_web_service_url

        settings.WALLET_APPLE_WEB_SERVICE_URL = ""
        assert (
            resolve_web_service_url(None, web_service_url="https://test.crush.lu/wallet/v1")
            == "https://test.crush.lu/wallet"
        )

    def test_correct_root_is_left_alone(self, settings):
        from crush_lu.wallet.passkit_service import resolve_web_service_url

        settings.WALLET_APPLE_WEB_SERVICE_URL = "https://crush.lu/wallet"
        assert resolve_web_service_url() == "https://crush.lu/wallet"


# ---------------------------------------------------------------------------
# Web-service authorization
# ---------------------------------------------------------------------------


def _authed_request(token):
    return RequestFactory().get(
        "/", HTTP_AUTHORIZATION=f"ApplePass {token}"
    )


@pytest.mark.django_db
class TestRequireAuthorization:
    """register / unregister / get-pass carry `Authorization: ApplePass <token>`
    and authenticate with the pass's own per-profile (or per-ticket) token."""

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

    def test_unresolvable_serial_500s(self, settings):
        from crush_lu.wallet.passkit_service import _require_authorization

        settings.PASSKIT_AUTH_TOKEN = ""
        request = _authed_request("anything")
        resp = _require_authorization(request, "pass.lu.crush", "no-such-serial")
        assert resp.status_code == 500


@pytest.mark.django_db
class TestListDeviceRegistrations:
    """The GET 'list updatable passes' poll is the one PassKit endpoint Apple
    does NOT send an Authorization header on — it identifies the caller solely
    by the opaque deviceLibraryIdentifier in the URL. Requiring ApplePass auth
    here made every poll 401/500, so passes never pulled updates."""

    def _registration(self, serial="evt-1-reg-1-abcd"):
        from crush_lu.models import PasskitDeviceRegistration

        return PasskitDeviceRegistration.objects.create(
            device_library_identifier="device-abc",
            pass_type_identifier="pass.lu.crush",
            serial_number=serial,
            push_token="push-tok",
        )

    def test_poll_succeeds_without_authorization_header(self, settings):
        from crush_lu.wallet.passkit_service import list_device_registrations

        settings.PASSKIT_AUTH_TOKEN = ""
        self._registration()
        # No Authorization header at all — exactly what Apple sends.
        request = RequestFactory().get("/")
        response = list_device_registrations(request, "device-abc", "pass.lu.crush")

        assert response.status_code == 200
        import json

        assert json.loads(response.content)["serialNumbers"] == ["evt-1-reg-1-abcd"]

    def test_poll_succeeds_even_with_shared_token_configured(self, settings):
        from crush_lu.wallet.passkit_service import list_device_registrations

        # A configured shared token must not resurrect the auth requirement.
        settings.PASSKIT_AUTH_TOKEN = "shared-tok"
        self._registration()
        request = RequestFactory().get("/")
        assert (
            list_device_registrations(request, "device-abc", "pass.lu.crush").status_code
            == 200
        )

    def test_unknown_device_returns_204(self, settings):
        from crush_lu.wallet.passkit_service import list_device_registrations

        settings.PASSKIT_AUTH_TOKEN = ""
        request = RequestFactory().get("/")
        response = list_device_registrations(request, "other-device", "pass.lu.crush")
        # 204, not 401 — an unknown device is "nothing to update", not a
        # rejected caller.
        assert response.status_code == 204


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

    def test_resolves_per_ticket_token_for_profileless_attendee(
        self, event_with_registrations
    ):
        from crush_lu.models import CrushProfile
        from crush_lu.wallet.passkit_service import _resolve_auth_token_from_profile

        _event, registrations = event_with_registrations
        registration = registrations[0]
        # Open-event registration allows an attendee with no CrushProfile; the
        # ticket's token then lives on the registration itself.
        CrushProfile.objects.filter(user=registration.user).delete()
        registration.apple_wallet_ticket_serial = "evt-1-reg-3-cafebabe"
        registration.apple_wallet_auth_token = "tok-ticket"
        registration.save(
            update_fields=["apple_wallet_ticket_serial", "apple_wallet_auth_token"]
        )

        assert (
            _resolve_auth_token_from_profile("pass.lu.crush", "evt-1-reg-3-cafebabe")
            == "tok-ticket"
        )

    def test_registration_token_wins_over_a_later_profile_token(
        self, event_with_registrations
    ):
        from crush_lu.wallet.passkit_service import _resolve_auth_token_from_profile

        _event, registrations = event_with_registrations
        registration = registrations[0]
        registration.apple_wallet_ticket_serial = "evt-1-reg-4-0badf00d"
        registration.apple_wallet_auth_token = "tok-ticket"
        registration.save(
            update_fields=["apple_wallet_ticket_serial", "apple_wallet_auth_token"]
        )
        profile = registration.user.crushprofile
        profile.apple_auth_token = "tok-profile"
        profile.save(update_fields=["apple_auth_token"])

        # The installed pass carries the registration token; a profile created
        # (or given a token) afterwards must not invalidate it.
        assert (
            _resolve_auth_token_from_profile("pass.lu.crush", "evt-1-reg-4-0badf00d")
            == "tok-ticket"
        )


# ---------------------------------------------------------------------------
# Check-in host preservation
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


class TestResolveCheckinBaseUrl:
    """A check-in token only validates in the environment that issued it, so
    the QR host must follow the REQUEST, never WALLET_APPLE_WEB_SERVICE_URL —
    otherwise a ticket downloaded from test.crush.lu check-ins against prod."""

    def test_live_request_wins_over_configured_service_root(self):
        from crush_lu.wallet.apple_event_ticket import _resolve_checkin_base_url

        request = RequestFactory().get(
            "/", HTTP_HOST="test.crush.lu", secure=True
        )
        # Returning None hands the decision to _build_checkin_url, which uses
        # the request host. The production-pointing resolved URL must not win.
        assert (
            _resolve_checkin_base_url(
                request,
                forwarded_web_service_url=None,
                resolved_web_service_url="https://crush.lu/wallet",
            )
            is None
        )

    def test_persisted_issuing_origin_wins_on_rebuild(self):
        from crush_lu.wallet.apple_event_ticket import _resolve_checkin_base_url

        # THE case the forwarded URL cannot cover: the ticket was issued from
        # staging, but it embedded the setting's production service root, so
        # Apple asks PRODUCTION for the update and forwards a production URL.
        # Only the origin stamped at issue time still knows where the ticket
        # (and its check-in token) actually came from.
        assert (
            _resolve_checkin_base_url(
                None,
                issued_origin="https://test.crush.lu",
                forwarded_web_service_url="https://crush.lu/wallet",
                resolved_web_service_url="https://crush.lu/wallet",
            )
            == "https://test.crush.lu"
        )

    def test_requestless_rebuild_prefers_forwarded_origin(self):
        from crush_lu.wallet.apple_event_ticket import _resolve_checkin_base_url

        # Fallback for tickets issued before apple_wallet_checkin_origin
        # existed: the forwarded value (derived from Apple's own live request)
        # still beats the possibly cross-slot setting.
        assert (
            _resolve_checkin_base_url(
                None,
                issued_origin="",
                forwarded_web_service_url="https://test.crush.lu/wallet",
                resolved_web_service_url="https://crush.lu/wallet",
            )
            == "https://test.crush.lu"
        )

    def test_requestless_rebuild_falls_back_to_resolved_origin(self):
        from crush_lu.wallet.apple_event_ticket import _resolve_checkin_base_url

        assert (
            _resolve_checkin_base_url(
                None,
                forwarded_web_service_url=None,
                resolved_web_service_url="https://crush.lu/wallet",
            )
            == "https://crush.lu"
        )

    def test_returns_none_when_nothing_available(self):
        from crush_lu.wallet.apple_event_ticket import _resolve_checkin_base_url

        assert _resolve_checkin_base_url(None) is None


@pytest.mark.django_db
class TestBuildCheckinUrl:
    """The other half of the same guarantee: with no explicit base_url, the
    live request's host is what lands in the QR."""

    def test_uses_request_host(self, event_with_registrations):
        from crush_lu.wallet.apple_event_ticket import _build_checkin_url

        _event, registrations = event_with_registrations
        registration = registrations[0]
        request = RequestFactory().get("/", HTTP_HOST="test.crush.lu", secure=True)

        url = _build_checkin_url(registration, request)
        assert url.startswith(
            f"https://test.crush.lu/api/events/checkin/{registration.id}/"
        )

    def test_explicit_base_url_wins_when_there_is_no_request(
        self, event_with_registrations
    ):
        from crush_lu.wallet.apple_event_ticket import _build_checkin_url

        _event, registrations = event_with_registrations
        registration = registrations[0]

        url = _build_checkin_url(
            registration, None, base_url="https://test.crush.lu"
        )
        assert url.startswith(
            f"https://test.crush.lu/api/events/checkin/{registration.id}/"
        )


@pytest.mark.django_db
class TestEnsureCheckinOrigin:
    """The issuing origin has to be persisted at download time — it is the only
    thing that survives into a request-less PassKit rebuild."""

    def test_stamps_the_request_host(self, event_with_registrations):
        from crush_lu.wallet.apple_event_ticket import _ensure_checkin_origin

        _event, registrations = event_with_registrations
        registration = registrations[0]
        request = RequestFactory().get("/", HTTP_HOST="test.crush.lu", secure=True)

        assert _ensure_checkin_origin(registration, request) == "https://test.crush.lu"
        registration.refresh_from_db()
        assert registration.apple_wallet_checkin_origin == "https://test.crush.lu"

    def test_redownload_from_another_host_updates_the_stamp(
        self, event_with_registrations
    ):
        from crush_lu.wallet.apple_event_ticket import _ensure_checkin_origin

        _event, registrations = event_with_registrations
        registration = registrations[0]
        registration.apple_wallet_checkin_origin = "https://test.crush.lu"
        registration.save(update_fields=["apple_wallet_checkin_origin"])

        # Re-downloading from production issues a fresh QR on that host, so the
        # stamp must follow it — otherwise the rebuild would revert to staging.
        request = RequestFactory().get("/", HTTP_HOST="crush.lu", secure=True)
        assert _ensure_checkin_origin(registration, request) == "https://crush.lu"
        registration.refresh_from_db()
        assert registration.apple_wallet_checkin_origin == "https://crush.lu"
