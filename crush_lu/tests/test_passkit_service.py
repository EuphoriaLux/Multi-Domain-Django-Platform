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

from unittest import mock

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

    def test_poll_with_cursor_does_not_500(self, settings):
        from crush_lu.wallet.passkit_service import list_device_registrations

        # Regression: this path used django.utils.timezone.utc, removed in
        # Django 5.0, so it raised AttributeError (uncaught) -> 500. Every poll
        # after the very first carries the cursor, so updates died there.
        settings.PASSKIT_AUTH_TOKEN = ""
        self._registration()
        request = RequestFactory().get("/", {"passesUpdatedSince": "0"})
        response = list_device_registrations(request, "device-abc", "pass.lu.crush")

        assert response.status_code == 200
        import json

        assert json.loads(response.content)["serialNumbers"] == ["evt-1-reg-1-abcd"]

    def test_cursor_filters_out_unchanged_passes(self, settings):
        from crush_lu.wallet.passkit_service import list_device_registrations

        settings.PASSKIT_AUTH_TOKEN = ""
        reg = self._registration()
        # A cursor at "now" must exclude a pass last touched before it.
        cursor = reg.updated_at.timestamp() + 1
        request = RequestFactory().get("/", {"passesUpdatedSince": str(cursor)})
        assert (
            list_device_registrations(request, "device-abc", "pass.lu.crush").status_code
            == 204
        )

    def test_cursor_round_trips_losslessly(self, settings):
        import json

        from crush_lu.wallet.passkit_service import list_device_registrations

        # int() truncation put the echoed cursor BEFORE the stored updated_at,
        # so `updated_at__gt` matched the same row forever and Wallet
        # redownloaded an unchanged pass on every poll.
        settings.PASSKIT_AUTH_TOKEN = ""
        self._registration()

        first = list_device_registrations(
            RequestFactory().get("/"), "device-abc", "pass.lu.crush"
        )
        cursor = json.loads(first.content)["lastUpdated"]

        # Feeding the tag straight back must report nothing new.
        again = list_device_registrations(
            RequestFactory().get("/", {"passesUpdatedSince": str(cursor)}),
            "device-abc",
            "pass.lu.crush",
        )
        assert again.status_code == 204

    def test_legacy_integer_cursor_still_parses(self, settings):
        from crush_lu.wallet.passkit_service import list_device_registrations

        # Installed passes still hold the old truncated int tag; it must not
        # start erroring after the format change.
        settings.PASSKIT_AUTH_TOKEN = ""
        self._registration()
        request = RequestFactory().get("/", {"passesUpdatedSince": "1"})
        assert (
            list_device_registrations(request, "device-abc", "pass.lu.crush").status_code
            == 200
        )

    def test_malformed_cursor_is_a_400_not_a_500(self, settings):
        from crush_lu.wallet.passkit_service import list_device_registrations

        settings.PASSKIT_AUTH_TOKEN = ""
        self._registration()
        for bad in ("not-a-number", "1e400"):
            request = RequestFactory().get("/", {"passesUpdatedSince": bad})
            response = list_device_registrations(request, "device-abc", "pass.lu.crush")
            assert response.status_code == 400, bad


@pytest.mark.django_db
class TestMarkPassesUpdated:
    """A content change has to advance the update tag, or Wallet's next poll
    filters the pass out (204) and the rebuilt package is never fetched — the
    push fires and nothing updates."""

    def _registration(self, serial="evt-1-reg-1-abcd"):
        from crush_lu.models import PasskitDeviceRegistration

        return PasskitDeviceRegistration.objects.create(
            device_library_identifier="device-abc",
            pass_type_identifier="pass.lu.crush",
            serial_number=serial,
            push_token="push-tok",
        )

    def test_advances_the_tag(self):
        from crush_lu.wallet.passkit_apns import mark_passes_updated

        reg = self._registration()
        before = reg.updated_at

        assert mark_passes_updated("pass.lu.crush", "evt-1-reg-1-abcd") == 1
        reg.refresh_from_db()
        assert reg.updated_at > before

    def test_only_touches_the_matching_serial(self):
        from crush_lu.wallet.passkit_apns import mark_passes_updated

        reg = self._registration()
        other = self._registration(serial="evt-9-reg-9-zzzz")
        reg_before = reg.updated_at
        other_before = other.updated_at

        assert mark_passes_updated("pass.lu.crush", "evt-1-reg-1-abcd") == 1
        reg.refresh_from_db()
        other.refresh_from_db()
        assert reg.updated_at > reg_before
        assert other.updated_at == other_before

    def test_push_advances_the_tag_even_without_apns_configured(self, settings):
        from crush_lu.wallet.passkit_apns import send_passkit_push_notifications

        # Production currently has no PASSKIT_APNS_* settings. The tag must
        # still advance so Wallet's own periodic poll picks the change up.
        settings.PASSKIT_APNS_KEY_ID = ""
        settings.PASSKIT_APNS_TEAM_ID = ""
        settings.PASSKIT_APNS_PRIVATE_KEY = ""
        reg = self._registration()
        before = reg.updated_at

        send_passkit_push_notifications("pass.lu.crush", "evt-1-reg-1-abcd")
        reg.refresh_from_db()
        assert reg.updated_at > before


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


@pytest.fixture
def _apple_identity(settings):
    """The three _require_setting values a ticket build needs (no certs)."""
    settings.WALLET_APPLE_PASS_TYPE_IDENTIFIER = "pass.lu.crush"
    settings.WALLET_APPLE_TEAM_IDENTIFIER = "C5XDPB2G33"
    settings.WALLET_APPLE_ORGANIZATION_NAME = "Crush.lu"


@pytest.mark.django_db
class TestCancelledTicketIsVoided:
    """Refreshing a cancelled ticket is theatre unless the payload says so —
    it carries no status field, so a rebuild is otherwise byte-identical and
    the cancelled seat keeps rendering as a live ticket.

    Patches _build_pkpass (the only part needing Apple certs) so this runs
    unconditionally rather than joining the cert-gated module.
    """

    def _payload_for(self, registration):
        from crush_lu.wallet import apple_event_ticket

        request = RequestFactory().get("/", HTTP_HOST="crush.lu", secure=True)
        with mock.patch.object(
            apple_event_ticket, "_build_pkpass", return_value=b""
        ) as build:
            apple_event_ticket.build_apple_event_ticket(registration, request=request)
        return build.call_args[0][0]

    def test_cancelled_registration_voids_the_pass(
        self, _apple_identity, event_with_registrations
    ):
        _event, registrations = event_with_registrations
        registration = registrations[0]
        registration.status = "cancelled"
        registration.save(update_fields=["status"])

        assert self._payload_for(registration).get("voided") is True

    def test_confirmed_registration_is_not_voided(
        self, _apple_identity, event_with_registrations
    ):
        _event, registrations = event_with_registrations
        assert "voided" not in self._payload_for(registrations[0])

    def test_attended_registration_is_not_voided(
        self, _apple_identity, event_with_registrations
    ):
        # A used ticket is still a legitimate record; reuse is prevented by the
        # check-in token server-side, not by how the pass looks.
        _event, registrations = event_with_registrations
        registration = registrations[0]
        registration.status = "attended"
        registration.save(update_fields=["status"])

        assert "voided" not in self._payload_for(registration)

    def test_event_level_cancellation_voids_a_confirmed_seat(
        self, _apple_identity, event_with_registrations
    ):
        # The admin's bulk cancel sets MeetupEvent.is_cancelled and leaves every
        # registration "confirmed", so checking only the seat would let a
        # cancelled event's tickets keep reading as valid at the door.
        event, registrations = event_with_registrations
        event.is_cancelled = True
        event.save(update_fields=["is_cancelled"])
        registration = registrations[0]
        registration.refresh_from_db()

        assert registration.status == "confirmed"
        assert self._payload_for(registration).get("voided") is True


@pytest.mark.django_db
class TestClaimOnce:
    """Write-once ticket identifiers must survive concurrent downloads: a lost
    race signs a package with a value the web service can never resolve."""

    def test_second_writer_adopts_the_winning_token(self, event_with_registrations):
        from crush_lu.models import EventRegistration
        from crush_lu.wallet.apple_event_ticket import _ensure_ticket_auth_token

        _event, registrations = event_with_registrations
        registration = registrations[0]

        # Simulate the concurrent download: another request already claimed the
        # field while this in-memory instance still sees it empty.
        EventRegistration.objects.filter(pk=registration.pk).update(
            apple_wallet_auth_token="winner-token"
        )
        assert registration.apple_wallet_auth_token == ""

        # The loser must sign with the winner's token, not its own.
        assert _ensure_ticket_auth_token(registration) == "winner-token"
        assert registration.apple_wallet_auth_token == "winner-token"

    def test_second_writer_adopts_the_winning_serial(self, event_with_registrations):
        from crush_lu.models import EventRegistration
        from crush_lu.wallet.apple_event_ticket import _ensure_event_ticket_serial

        _event, registrations = event_with_registrations
        registration = registrations[0]

        EventRegistration.objects.filter(pk=registration.pk).update(
            apple_wallet_ticket_serial="evt-winner"
        )
        assert _ensure_event_ticket_serial(registration) == "evt-winner"

    def test_uncontended_claim_persists(self, event_with_registrations):
        from crush_lu.wallet.apple_event_ticket import _ensure_ticket_auth_token

        _event, registrations = event_with_registrations
        registration = registrations[0]

        token = _ensure_ticket_auth_token(registration)
        assert token
        registration.refresh_from_db()
        assert registration.apple_wallet_auth_token == token

    def test_member_pass_identifiers_are_claimed_atomically(
        self, test_user_with_profile
    ):
        from crush_lu.models import CrushProfile
        from crush_lu.wallet.apple_pass import _ensure_pass_identifiers

        _user, profile = test_user_with_profile
        # Another concurrent download already claimed both fields.
        CrushProfile.objects.filter(pk=profile.pk).update(
            apple_pass_serial="winner-serial", apple_auth_token="winner-token"
        )
        assert profile.apple_pass_serial == ""

        # The loser must sign with the winner's pair, not its own — otherwise
        # whichever package the native sheet presents may be unauthenticable.
        assert _ensure_pass_identifiers(profile) == ("winner-serial", "winner-token")


@pytest.mark.django_db
class TestTicketTokenSurvivesOwnershipChange:
    """A ticket's authentication identity must be immutable once issued.
    Profile ownership is not: account_merge moves registrations to the keeper
    and deletes the duplicate profile."""

    def test_profile_token_is_persisted_on_the_registration(
        self, _apple_identity, event_with_registrations
    ):
        from crush_lu.wallet import apple_event_ticket

        _event, registrations = event_with_registrations
        registration = registrations[0]
        assert registration.apple_wallet_auth_token == ""

        request = RequestFactory().get("/", HTTP_HOST="crush.lu", secure=True)
        with mock.patch.object(
            apple_event_ticket, "_build_pkpass", return_value=b""
        ) as build:
            apple_event_ticket.build_apple_event_ticket(registration, request=request)
        issued = build.call_args[0][0]["authenticationToken"]

        registration.refresh_from_db()
        # Persisted, so a later merge to a different profile cannot change what
        # the resolver hands back for this already-installed ticket.
        assert registration.apple_wallet_auth_token == issued
        assert registration.user.crushprofile.apple_auth_token == issued

    def test_resolver_still_finds_it_after_the_profile_token_changes(
        self, _apple_identity, event_with_registrations
    ):
        from crush_lu.wallet import apple_event_ticket
        from crush_lu.wallet.passkit_service import _resolve_auth_token_from_profile

        _event, registrations = event_with_registrations
        registration = registrations[0]
        registration.apple_wallet_ticket_serial = "evt-1-reg-1-merge"
        registration.save(update_fields=["apple_wallet_ticket_serial"])

        request = RequestFactory().get("/", HTTP_HOST="crush.lu", secure=True)
        with mock.patch.object(apple_event_ticket, "_build_pkpass", return_value=b""):
            apple_event_ticket.build_apple_event_ticket(registration, request=request)
        registration.refresh_from_db()
        issued = registration.apple_wallet_auth_token

        # Simulate the merge outcome: the owning profile now carries a
        # different token entirely.
        profile = registration.user.crushprofile
        profile.apple_auth_token = "keeper-token"
        profile.save(update_fields=["apple_auth_token"])

        assert (
            _resolve_auth_token_from_profile("pass.lu.crush", "evt-1-reg-1-merge")
            == issued
        )


@pytest.mark.django_db
class TestEventLevelTicketRefresh:
    """Event-level changes rewrite the payload but touch no registration row,
    and both paths that make them use queryset.update() — no signals at all."""

    def _ticketed(self, event_with_registrations):
        _event, registrations = event_with_registrations
        registration = registrations[0]
        registration.apple_wallet_ticket_serial = "evt-1-reg-1-abcd"
        registration.save(update_fields=["apple_wallet_ticket_serial"])
        return _event, registration

    def test_refresh_event_tickets_pushes_each_serial(
        self, _apple_identity, event_with_registrations, django_capture_on_commit_callbacks
    ):
        from crush_lu.wallet.passkit_service import refresh_event_tickets

        event, _registration = self._ticketed(event_with_registrations)

        with mock.patch(
            "crush_lu.wallet.passkit_service.trigger_pass_refresh"
        ) as refresh:
            with django_capture_on_commit_callbacks(execute=True):
                assert refresh_event_tickets(event) == 1

        # Asserted on args rather than an exact call signature: this path also
        # forwards a non-deterministic `deadline`, and pinning the full kwargs
        # made these break every time the bounding logic gained a parameter.
        assert refresh.call_count == 1
        assert refresh.call_args.args[:2] == ("pass.lu.crush", "evt-1-reg-1-abcd")
        # refresh_ticket_serials already advanced every tag in one bulk query,
        # so the pushed subset must not rewrite it.
        assert refresh.call_args.kwargs["mark_updated"] is False

    def test_skips_registrations_with_no_ticket(
        self, _apple_identity, event_with_registrations
    ):
        from crush_lu.wallet.passkit_service import refresh_event_tickets

        event, _registrations = event_with_registrations
        assert refresh_event_tickets(event) == 0

    def test_reschedule_schedules_a_refresh(
        self, _apple_identity, event_with_registrations, django_capture_on_commit_callbacks
    ):
        from datetime import timedelta

        from django.utils import timezone as dj_timezone

        event, _registration = self._ticketed(event_with_registrations)

        with mock.patch(
            "crush_lu.wallet.passkit_service.trigger_pass_refresh"
        ) as refresh:
            with django_capture_on_commit_callbacks(execute=True):
                event.date_time = dj_timezone.now() + timedelta(days=14)
                event.save()

        # Asserted on args rather than an exact call signature: this path also
        # forwards a non-deterministic `deadline`, and pinning the full kwargs
        # made these break every time the bounding logic gained a parameter.
        assert refresh.call_count == 1
        assert refresh.call_args.args[:2] == ("pass.lu.crush", "evt-1-reg-1-abcd")
        # refresh_ticket_serials already advanced every tag in one bulk query,
        # so the pushed subset must not rewrite it.
        assert refresh.call_args.kwargs["mark_updated"] is False

    def test_event_change_also_refreshes_member_passes(
        self, _apple_identity, event_with_registrations, django_capture_on_commit_callbacks
    ):
        # The member card shows the holder's NEXT event, so an event edit makes
        # it stale too — and it carries a different serial from the ticket.
        event, registrations = event_with_registrations
        registration = registrations[0]
        registration.apple_wallet_ticket_serial = "evt-1-reg-1-abcd"
        registration.save(update_fields=["apple_wallet_ticket_serial"])
        profile = registration.user.crushprofile
        profile.apple_pass_serial = "member-serial"
        profile.save(update_fields=["apple_pass_serial"])

        with mock.patch(
            "crush_lu.wallet.passkit_service.trigger_pass_refresh"
        ) as refresh:
            with django_capture_on_commit_callbacks(execute=True):
                event.location = "A different bar"
                event.save()

        pushed = {call.args[1] for call in refresh.call_args_list}
        assert pushed == {"evt-1-reg-1-abcd", "member-serial"}

    def test_no_payload_change_does_not_push(
        self, _apple_identity, event_with_registrations, django_capture_on_commit_callbacks
    ):
        event, _registration = self._ticketed(event_with_registrations)

        with mock.patch(
            "crush_lu.wallet.passkit_service.trigger_pass_refresh"
        ) as refresh:
            with django_capture_on_commit_callbacks(execute=True):
                # Not embedded in the ticket payload.
                event.is_published = not event.is_published
                event.save()

        refresh.assert_not_called()

    def test_translated_title_change_pushes(
        self, _apple_identity, event_with_registrations, django_capture_on_commit_callbacks
    ):
        # title is a modeltranslation field; admin runs in English, so editing
        # only title_fr must still be seen — French holders read that column.
        event, _registration = self._ticketed(event_with_registrations)

        with mock.patch(
            "crush_lu.wallet.passkit_service.trigger_pass_refresh"
        ) as refresh:
            with django_capture_on_commit_callbacks(execute=True):
                event.title_fr = "Soirée renommée"
                event.save()

        # Asserted on args rather than an exact call signature: this path also
        # forwards a non-deterministic `deadline`, and pinning the full kwargs
        # made these break every time the bounding logic gained a parameter.
        assert refresh.call_count == 1
        assert refresh.call_args.args[:2] == ("pass.lu.crush", "evt-1-reg-1-abcd")
        # refresh_ticket_serials already advanced every tag in one bulk query,
        # so the pushed subset must not rewrite it.
        assert refresh.call_args.kwargs["mark_updated"] is False

    @pytest.mark.parametrize(
        "field,value",
        [
            ("title", "Renamed event"),
            ("location", "A different bar"),
            ("address", "1 New Street"),
            ("duration_minutes", 999),
            ("is_cancelled", True),
        ],
    )
    def test_any_embedded_field_change_pushes(
        self,
        field,
        value,
        _apple_identity,
        event_with_registrations,
        django_capture_on_commit_callbacks,
    ):
        # The payload embeds far more than the start time: retitling, moving
        # venue, fixing the address or changing the duration all leave
        # installed passes showing something wrong.
        event, _registration = self._ticketed(event_with_registrations)

        with mock.patch(
            "crush_lu.wallet.passkit_service.trigger_pass_refresh"
        ) as refresh:
            with django_capture_on_commit_callbacks(execute=True):
                setattr(event, field, value)
                event.save()

        # Asserted on args rather than an exact call signature: this path also
        # forwards a non-deterministic `deadline`, and pinning the full kwargs
        # made these break every time the bounding logic gained a parameter.
        assert refresh.call_count == 1
        assert refresh.call_args.args[:2] == ("pass.lu.crush", "evt-1-reg-1-abcd")
        # refresh_ticket_serials already advanced every tag in one bulk query,
        # so the pushed subset must not rewrite it.
        assert refresh.call_args.kwargs["mark_updated"] is False

    def test_bulk_fanout_is_capped_but_all_tags_advance(
        self, _apple_identity, settings, event_with_registrations,
        django_capture_on_commit_callbacks,
    ):
        from crush_lu.models import PasskitDeviceRegistration
        from crush_lu.wallet.passkit_service import refresh_event_tickets

        event, registrations = event_with_registrations
        # Three ticketed registrations, but only two pushes allowed.
        serials = []
        for i, reg in enumerate(self._extra_registrations(event, registrations, 3)):
            serial = f"evt-1-reg-{i}-aaaa"
            reg.apple_wallet_ticket_serial = serial
            reg.save(update_fields=["apple_wallet_ticket_serial"])
            PasskitDeviceRegistration.objects.create(
                device_library_identifier=f"device-{i}",
                pass_type_identifier="pass.lu.crush",
                serial_number=serial,
                push_token="tok",
            )
            serials.append(serial)

        settings.PASSKIT_BULK_PUSH_LIMIT = 2
        before = list(
            PasskitDeviceRegistration.objects.order_by("pk").values_list(
                "updated_at", flat=True
            )
        )

        with mock.patch(
            "crush_lu.wallet.passkit_service.trigger_pass_refresh"
        ) as refresh:
            with django_capture_on_commit_callbacks(execute=True):
                refresh_event_tickets(event)

        # Pushes bounded — an unbounded loop could outlast the admin request.
        assert refresh.call_count == 2
        # ...but EVERY tag advanced, so the capped passes still update on
        # Wallet's next poll rather than being silently dropped.
        after = list(
            PasskitDeviceRegistration.objects.order_by("pk").values_list(
                "updated_at", flat=True
            )
        )
        assert all(a > b for a, b in zip(after, before))

    def _extra_registrations(self, event, registrations, count):
        from django.contrib.auth import get_user_model

        from crush_lu.models import EventRegistration

        out = list(registrations)
        User = get_user_model()
        while len(out) < count:
            i = len(out)
            user = User.objects.create_user(
                username=f"bulk{i}@example.com",
                email=f"bulk{i}@example.com",
                password="x",
            )
            out.append(
                EventRegistration.objects.create(
                    event=event, user=user, status="confirmed"
                )
            )
        return out[:count]


@pytest.mark.django_db
class TestAppleEventTicketRefreshSignal:
    """Every repo-wide Apple refresh path went through the MEMBER pass serial,
    so an event ticket was never pushed when its registration changed."""

    def _prepared(self, event_with_registrations):
        _event, registrations = event_with_registrations
        registration = registrations[0]
        registration.apple_wallet_ticket_serial = "evt-1-reg-1-abcd"
        registration.save(update_fields=["apple_wallet_ticket_serial"])
        return registration

    def test_cancelling_triggers_a_refresh(
        self, _apple_identity, event_with_registrations, django_capture_on_commit_callbacks
    ):
        registration = self._prepared(event_with_registrations)

        with mock.patch(
            "crush_lu.wallet.passkit_service.trigger_pass_refresh"
        ) as refresh:
            with django_capture_on_commit_callbacks(execute=True):
                registration.status = "cancelled"
                registration.save(update_fields=["status"])

        refresh.assert_called_once_with("pass.lu.crush", "evt-1-reg-1-abcd")

    def test_no_refresh_without_a_ticket_serial(
        self, _apple_identity, event_with_registrations, django_capture_on_commit_callbacks
    ):
        _event, registrations = event_with_registrations
        registration = registrations[0]

        with mock.patch(
            "crush_lu.wallet.passkit_service.trigger_pass_refresh"
        ) as refresh:
            with django_capture_on_commit_callbacks(execute=True):
                registration.status = "cancelled"
                registration.save(update_fields=["status"])

        refresh.assert_not_called()

    def test_attendance_does_not_push(
        self, _apple_identity, event_with_registrations, django_capture_on_commit_callbacks
    ):
        # The door writes `attended` for every scan, and on_commit runs inside
        # the request/response cycle — with APNs configured this would put a
        # synchronous 10s-timeout round trip in front of the coach's scanner.
        # The rebuilt payload is byte-identical for `attended` (only `cancelled`
        # sets `voided`), so there is nothing to gain for that cost.
        registration = self._prepared(event_with_registrations)

        with mock.patch(
            "crush_lu.wallet.passkit_service.trigger_pass_refresh"
        ) as refresh:
            with django_capture_on_commit_callbacks(execute=True):
                registration.status = "attended"
                registration.save(update_fields=["status"])

        refresh.assert_not_called()

    def test_plain_confirmed_save_does_not_push(
        self, _apple_identity, event_with_registrations, django_capture_on_commit_callbacks
    ):
        # Many saves leave a row confirmed with no transition (token/ID writes);
        # each must not fire a push. Gated on _reactivate_ticket, like Google.
        registration = self._prepared(event_with_registrations)

        with mock.patch(
            "crush_lu.wallet.passkit_service.trigger_pass_refresh"
        ) as refresh:
            with django_capture_on_commit_callbacks(execute=True):
                registration.save(update_fields=["status"])

        refresh.assert_not_called()

    def test_restoring_a_cancelled_seat_pushes_without_the_undo_flag(
        self, _apple_identity, event_with_registrations, django_capture_on_commit_callbacks
    ):
        from crush_lu.models import EventRegistration

        # Restoring a seat through the admin's list_editable status column
        # reaches the receiver but never sets _reactivate_ticket. Without the
        # transition check the holder keeps a permanently voided ticket.
        registration = self._prepared(event_with_registrations)
        registration.status = "cancelled"
        registration.save(update_fields=["status"])

        # Reload so _loaded_status reflects the stored "cancelled".
        reloaded = EventRegistration.objects.get(pk=registration.pk)

        with mock.patch(
            "crush_lu.wallet.passkit_service.trigger_pass_refresh"
        ) as refresh:
            with django_capture_on_commit_callbacks(execute=True):
                reloaded.status = "confirmed"
                reloaded.save(update_fields=["status"])

        refresh.assert_called_once_with("pass.lu.crush", "evt-1-reg-1-abcd")

    def test_editing_an_already_cancelled_row_does_not_push(
        self, _apple_identity, event_with_registrations, django_capture_on_commit_callbacks
    ):
        from crush_lu.models import EventRegistration

        # The voided state has not flipped, so a rebuild could not differ from
        # the installed pass — pushing one is pure waste.
        registration = self._prepared(event_with_registrations)
        registration.status = "cancelled"
        registration.save(update_fields=["status"])
        reloaded = EventRegistration.objects.get(pk=registration.pk)

        with mock.patch(
            "crush_lu.wallet.passkit_service.trigger_pass_refresh"
        ) as refresh:
            with django_capture_on_commit_callbacks(execute=True):
                reloaded.save(update_fields=["status"])

        refresh.assert_not_called()

    def test_multiple_transitions_on_one_instance_still_push(
        self, _apple_identity, event_with_registrations, django_capture_on_commit_callbacks
    ):
        # _previous_status is re-read before EVERY save. A snapshot taken once
        # at load would still report "confirmed" on the second save here, so
        # the restoration would emit no refresh and the ticket would stay
        # voided.
        registration = self._prepared(event_with_registrations)

        with mock.patch(
            "crush_lu.wallet.passkit_service.trigger_pass_refresh"
        ) as refresh:
            with django_capture_on_commit_callbacks(execute=True):
                registration.status = "cancelled"
                registration.save(update_fields=["status"])
                registration.status = "confirmed"
                registration.save(update_fields=["status"])

        # Once for the void, once for the un-void.
        assert refresh.call_count == 2

    def test_undo_from_attended_does_not_push(
        self, _apple_identity, event_with_registrations, django_capture_on_commit_callbacks
    ):
        from crush_lu.models import EventRegistration

        # The scanner's undo sends attended -> confirmed. BOTH are seat-holding,
        # so neither voids the pass and the rebuild is byte-identical — there is
        # nothing to push. (This used to fire off `_reactivate_ticket`; deriving
        # from validity instead makes that flag redundant for Apple.)
        registration = self._prepared(event_with_registrations)
        registration.status = "attended"
        registration.save(update_fields=["status"])
        reloaded = EventRegistration.objects.get(pk=registration.pk)

        with mock.patch(
            "crush_lu.wallet.passkit_service.trigger_pass_refresh"
        ) as refresh:
            with django_capture_on_commit_callbacks(execute=True):
                reloaded._reactivate_ticket = True
                reloaded.status = "confirmed"
                reloaded.save(update_fields=["status"])

        refresh.assert_not_called()

    @pytest.mark.parametrize("invalid_status", ["waitlist", "no_show", "cancelled"])
    def test_losing_the_seat_voids_and_pushes(
        self,
        invalid_status,
        _apple_identity,
        event_with_registrations,
        django_capture_on_commit_callbacks,
    ):
        # views_checkin rejects all three at the door, and staff can reach
        # waitlist/no_show through the admin — so all three must void the pass,
        # not just `cancelled`.
        registration = self._prepared(event_with_registrations)

        with mock.patch(
            "crush_lu.wallet.passkit_service.trigger_pass_refresh"
        ) as refresh:
            with django_capture_on_commit_callbacks(execute=True):
                registration.status = invalid_status
                registration.save(update_fields=["status"])

        assert refresh.call_count == 1

    def test_reassigning_the_owner_pushes(
        self, _apple_identity, event_with_registrations, django_capture_on_commit_callbacks
    ):
        from django.contrib.auth import get_user_model

        # account_merge moves a registration with save(update_fields=['user']).
        # The status never changes, but the attendee NAME the rebuild prints
        # does — a validity-only test would ignore it.
        registration = self._prepared(event_with_registrations)
        keeper = get_user_model().objects.create_user(
            username="keeper@example.com", email="keeper@example.com", password="x"
        )

        with mock.patch(
            "crush_lu.wallet.passkit_service.trigger_pass_refresh"
        ) as refresh:
            with django_capture_on_commit_callbacks(execute=True):
                registration.user = keeper
                registration.save(update_fields=["user"])

        assert refresh.call_count == 1


@pytest.mark.django_db
class TestProfileRenameRefreshesTickets:
    """The holder's name is printed on the ticket, which carries its own
    evt-* serial. The member-pass guards must not gate that refresh — an
    open-event attendee can hold a ticket and no member pass at all."""

    def _ticketed(self, event_with_registrations):
        _event, registrations = event_with_registrations
        registration = registrations[0]
        registration.apple_wallet_ticket_serial = "evt-1-reg-1-abcd"
        registration.save(update_fields=["apple_wallet_ticket_serial"])
        return registration

    def test_rename_refreshes_tickets_without_a_member_pass(
        self, _apple_identity, event_with_registrations, django_capture_on_commit_callbacks
    ):
        registration = self._ticketed(event_with_registrations)
        profile = registration.user.crushprofile
        # No member pass and no Google object: the old placement returned
        # before ever reaching the ticket query.
        assert profile.apple_pass_serial == ""
        assert not profile.google_wallet_object_id

        with mock.patch(
            "crush_lu.wallet.passkit_service.trigger_pass_refresh"
        ) as refresh:
            with django_capture_on_commit_callbacks(execute=True):
                # show_full_name feeds display_name, which is what the ticket
                # prints. (display_name/first_name/last_name are properties,
                # not fields, so they can never appear in update_fields.)
                profile.show_full_name = not profile.show_full_name
                profile.save(update_fields=["show_full_name"])

        assert "evt-1-reg-1-abcd" in {c.args[1] for c in refresh.call_args_list}

    def test_user_rename_refreshes_member_pass_and_tickets(
        self, _apple_identity, event_with_registrations, django_capture_on_commit_callbacks
    ):
        # The name actually lives on User — display_name reads
        # User.first_name/get_full_name() — and the CrushProfile receiver never
        # sees that edit.
        registration = self._ticketed(event_with_registrations)
        user = registration.user
        profile = user.crushprofile
        profile.apple_pass_serial = "member-serial"
        profile.save(update_fields=["apple_pass_serial"])

        with mock.patch(
            "crush_lu.wallet.passkit_service.trigger_pass_refresh"
        ) as refresh:
            with django_capture_on_commit_callbacks(execute=True):
                user.first_name = "Renamed"
                user.save(update_fields=["first_name"])

        pushed = {c.args[1] for c in refresh.call_args_list}
        assert pushed == {"evt-1-reg-1-abcd", "member-serial"}

    def test_profile_creation_refreshes_an_existing_ticket(
        self, _apple_identity, event_with_registrations, django_capture_on_commit_callbacks
    ):
        from datetime import date

        from crush_lu.models import CrushProfile

        # An open-event attendee can install a ticket with NO profile — the
        # name falls back to the bare username. Creating the profile switches
        # display_name to username.split("@")[0], so the installed ticket goes
        # stale. There is no previous value to compare on this path, so the
        # persisted-value guard has to treat `created` as a change in itself.
        registration = self._ticketed(event_with_registrations)
        user = registration.user
        CrushProfile.objects.filter(user=user).delete()

        with mock.patch(
            "crush_lu.wallet.passkit_service.trigger_pass_refresh"
        ) as refresh:
            with django_capture_on_commit_callbacks(execute=True):
                CrushProfile.objects.create(user=user, date_of_birth=date(1995, 5, 15))

        assert "evt-1-reg-1-abcd" in {c.args[1] for c in refresh.call_args_list}

    def test_bare_profile_save_does_not_refresh(
        self, _apple_identity, event_with_registrations, django_capture_on_commit_callbacks
    ):
        # The Microsoft sign-in path calls profile.save() with no
        # update_fields on every login. Treating that as an identity change
        # refreshed every historical ticket — synchronous fan-out on a login.
        registration = self._ticketed(event_with_registrations)
        profile = registration.user.crushprofile

        with mock.patch(
            "crush_lu.wallet.passkit_service.trigger_pass_refresh"
        ) as refresh:
            with django_capture_on_commit_callbacks(execute=True):
                profile.save()

        refresh.assert_not_called()

    def test_username_change_refreshes(
        self, _apple_identity, event_with_registrations, django_capture_on_commit_callbacks
    ):
        # display_name falls back to username in BOTH branches, so a user with
        # no first name has their username printed on the pass and the ticket.
        registration = self._ticketed(event_with_registrations)
        user = registration.user

        with mock.patch(
            "crush_lu.wallet.passkit_service.trigger_pass_refresh"
        ) as refresh:
            with django_capture_on_commit_callbacks(execute=True):
                user.username = "renamed@example.com"
                user.save(update_fields=["username"])

        assert "evt-1-reg-1-abcd" in {c.args[1] for c in refresh.call_args_list}

    def test_password_change_does_not_refresh(
        self, _apple_identity, event_with_registrations, django_capture_on_commit_callbacks
    ):
        # CrushSetPasswordForm.save() calls user.save() with no update_fields.
        # Treating every full save as a rename made password changes fan out
        # APNs pushes.
        registration = self._ticketed(event_with_registrations)
        user = registration.user

        with mock.patch(
            "crush_lu.wallet.passkit_service.trigger_pass_refresh"
        ) as refresh:
            with django_capture_on_commit_callbacks(execute=True):
                user.set_password("a-brand-new-password")
                user.save()

        refresh.assert_not_called()

    def test_login_does_not_refresh(
        self, _apple_identity, event_with_registrations, django_capture_on_commit_callbacks
    ):
        from django.utils import timezone as dj_timezone

        # Login writes update_fields=["last_login"] on every sign-in; that must
        # not fan out wallet pushes.
        registration = self._ticketed(event_with_registrations)
        user = registration.user

        with mock.patch(
            "crush_lu.wallet.passkit_service.trigger_pass_refresh"
        ) as refresh:
            with django_capture_on_commit_callbacks(execute=True):
                user.last_login = dj_timezone.now()
                user.save(update_fields=["last_login"])

        refresh.assert_not_called()

    def test_user_rename_refreshes_the_google_member_object(
        self, _apple_identity, event_with_registrations, django_capture_on_commit_callbacks
    ):
        # Google prints display_name too, but refresh_ticket_serials is
        # Apple-only. This used to ride on the next bare profile.save()
        # reaching trigger_wallet_pass_updates — exactly the save that no
        # longer counts as a change — so without an explicit call here a
        # renamed member's Google card keeps the old name indefinitely.
        registration = self._ticketed(event_with_registrations)
        user = registration.user
        profile = user.crushprofile
        profile.google_wallet_object_id = "issuer.member-1"
        profile.save(update_fields=["google_wallet_object_id"])

        with mock.patch(
            "crush_lu.wallet.google_api.update_google_wallet_pass",
            return_value={"success": True, "message": "ok"},
        ) as google_patch, mock.patch(
            "crush_lu.wallet.passkit_service.trigger_pass_refresh"
        ):
            with django_capture_on_commit_callbacks(execute=True):
                user.first_name = "Renamed"
                user.save(update_fields=["first_name"])

        assert google_patch.call_count == 1

    def test_password_change_does_not_refresh_the_google_member_object(
        self, _apple_identity, event_with_registrations, django_capture_on_commit_callbacks
    ):
        # The Google half has to sit behind the same rename guard as the Apple
        # half, or CrushSetPasswordForm.save() — a bare user.save() — starts
        # PATCHing Google on every password change.
        registration = self._ticketed(event_with_registrations)
        user = registration.user
        profile = user.crushprofile
        profile.google_wallet_object_id = "issuer.member-1"
        profile.save(update_fields=["google_wallet_object_id"])

        with mock.patch(
            "crush_lu.wallet.google_api.update_google_wallet_pass"
        ) as google_patch, mock.patch(
            "crush_lu.wallet.passkit_service.trigger_pass_refresh"
        ):
            with django_capture_on_commit_callbacks(execute=True):
                user.set_password("a-brand-new-password")
                user.save()

        google_patch.assert_not_called()

    def test_non_identity_save_does_not_refresh_tickets(
        self, _apple_identity, event_with_registrations, django_capture_on_commit_callbacks
    ):
        registration = self._ticketed(event_with_registrations)
        profile = registration.user.crushprofile

        with mock.patch(
            "crush_lu.wallet.passkit_service.trigger_pass_refresh"
        ) as refresh:
            with django_capture_on_commit_callbacks(execute=True):
                profile.bio = "A new bio"
                profile.save(update_fields=["bio"])

        refresh.assert_not_called()


def test_member_pass_fields_are_real_columns():
    from crush_lu.models import CrushProfile
    from crush_lu.signals import (
        WALLET_MEMBER_PASS_FIELDS,
        WALLET_UPDATE_PROFILE_FIELDS,
    )

    # Everything in the member set is snapshotted with .values() and matched
    # against update_fields, so a property in here would raise FieldError on
    # every profile save. The leftovers are exactly the three that ARE
    # properties — the User's name read through the profile, which reaches
    # Wallet via refresh_wallet_passes_on_user_rename instead.
    concrete = {f.name for f in CrushProfile._meta.concrete_fields}
    assert WALLET_MEMBER_PASS_FIELDS <= concrete
    assert WALLET_MEMBER_PASS_FIELDS <= WALLET_UPDATE_PROFILE_FIELDS
    assert WALLET_UPDATE_PROFILE_FIELDS - WALLET_MEMBER_PASS_FIELDS == {
        "display_name",
        "first_name",
        "last_name",
    }


@pytest.mark.django_db
class TestMemberPassUpdateNeedsARealChange:
    """The MEMBER-pass half of the same receiver, which used to read every
    ``update_fields=None`` save as a change "to be safe". The Microsoft
    sign-in path calls a bare ``profile.save()`` on every login, so each login
    by a member with an installed pass paid for an APNs fan-out plus a Google
    Wallet round trip (token exchange, then a PATCH, 30s timeout each)."""

    def _with_member_pass(self, test_user_with_profile):
        _user, profile = test_user_with_profile
        profile.apple_pass_serial = "member-serial"
        profile.google_wallet_object_id = "issuer.member-1"
        profile.save(update_fields=["apple_pass_serial", "google_wallet_object_id"])
        return profile

    def test_bare_profile_save_does_not_refresh(self, test_user_with_profile):
        profile = self._with_member_pass(test_user_with_profile)

        with mock.patch("crush_lu.signals.trigger_wallet_pass_updates") as update:
            profile.save()

        update.assert_not_called()

    def test_rewriting_the_same_value_does_not_refresh(self, test_user_with_profile):
        # update_fields naming a wallet field is not evidence either: the value
        # is what it already was, so a rebuild would be byte-identical.
        profile = self._with_member_pass(test_user_with_profile)

        with mock.patch("crush_lu.signals.trigger_wallet_pass_updates") as update:
            profile.save(update_fields=["membership_tier"])

        update.assert_not_called()

    def test_an_existing_photo_does_not_read_as_a_change(
        self, test_user_with_profile
    ):
        from crush_lu.models import CrushProfile

        # photo_1 is the one field where the row and the instance hold
        # different types — the stored name vs a FieldFile — and an empty one
        # is NULL on some rows and "" on others. Unnormalised, a member with a
        # photo would refresh on every single save. Written with .update() so
        # no storage backend is touched.
        profile = self._with_member_pass(test_user_with_profile)
        CrushProfile.objects.filter(pk=profile.pk).update(
            photo_1="users/1/photos/a.jpg"
        )
        profile.refresh_from_db()

        with mock.patch("crush_lu.signals.trigger_wallet_pass_updates") as update:
            profile.save()

        update.assert_not_called()

    def test_a_real_change_in_a_bare_save_still_refreshes(
        self, test_user_with_profile
    ):
        # The guard has to be a comparison, not a blanket "full saves don't
        # count": the profile form posts every field, so a genuine tier or
        # privacy edit arrives with update_fields=None too.
        profile = self._with_member_pass(test_user_with_profile)

        with mock.patch("crush_lu.signals.trigger_wallet_pass_updates") as update:
            profile.membership_tier = "gold"
            profile.save()

        update.assert_called_once_with(profile)

    def test_a_new_photo_refreshes(self, test_user_with_profile):
        profile = self._with_member_pass(test_user_with_profile)

        with mock.patch("crush_lu.signals.trigger_wallet_pass_updates") as update:
            # Assigning a name (rather than an upload) keeps storage out of it;
            # the FieldFile is created already-committed, so save() writes the
            # string straight through.
            profile.photo_1 = "users/1/photos/b.jpg"
            profile.save(update_fields=["photo_1"])

        update.assert_called_once_with(profile)

    def test_google_wallet_update_waits_for_commit(
        self, test_user_with_profile, django_capture_on_commit_callbacks
    ):
        from crush_lu.signals import trigger_wallet_pass_updates

        # The Google half PATCHes content straight onto Google — there is no
        # device coming back for it — so a caller that rolls back afterwards
        # would leave Google showing state that never existed. And
        # referrals.update_membership_tier() saves from inside atomic() holding
        # a select_for_update, where two 30s HTTP calls sit on the lock.
        profile = self._with_member_pass(test_user_with_profile)

        with mock.patch(
            "crush_lu.wallet.google_api.update_google_wallet_pass",
            return_value={"success": True, "message": "ok"},
        ) as google_patch, mock.patch(
            "crush_lu.wallet.passkit_apns.send_passkit_push_notifications",
            return_value={"success": 0, "failed": 0, "total": 0},
        ):
            with django_capture_on_commit_callbacks(execute=True):
                trigger_wallet_pass_updates(profile)
                google_patch.assert_not_called()

        assert google_patch.call_count == 1

    def test_google_wallet_update_sends_committed_state_not_the_snapshot(
        self, test_user_with_profile, django_capture_on_commit_callbacks
    ):
        from crush_lu.models import CrushProfile
        from crush_lu.signals import trigger_wallet_pass_updates

        # Deferring releases the profile row lock before the PATCH, so another
        # transaction can commit a newer tier while this callback is queued.
        # The PATCH ships the payload itself (Apple only pushes "come back for
        # it"), so sending the instance captured at save time would put Google
        # back on the older tier indefinitely.
        profile = self._with_member_pass(test_user_with_profile)
        profile.membership_tier = "bronze"
        profile.save(update_fields=["membership_tier"])

        with mock.patch(
            "crush_lu.wallet.google_api.update_google_wallet_pass",
            return_value={"success": True, "message": "ok"},
        ) as google_patch, mock.patch(
            "crush_lu.wallet.passkit_apns.send_passkit_push_notifications",
            return_value={"success": 0, "failed": 0, "total": 0},
        ):
            with django_capture_on_commit_callbacks(execute=True):
                trigger_wallet_pass_updates(profile)
                # A concurrent award commits a higher tier first.
                CrushProfile.objects.filter(pk=profile.pk).update(
                    membership_tier="silver"
                )

        assert google_patch.call_args.args[0].membership_tier == "silver"
        # ...and the captured instance really was stale, so this is not a
        # tautology.
        assert profile.membership_tier == "bronze"

    def test_google_wallet_update_skips_a_profile_deleted_before_commit(
        self, test_user_with_profile, django_capture_on_commit_callbacks
    ):
        from crush_lu.models import CrushProfile
        from crush_lu.signals import trigger_wallet_pass_updates

        # Re-reading has to tolerate the row being gone: the delete receiver
        # owns that teardown, and there is nothing left to PATCH.
        profile = self._with_member_pass(test_user_with_profile)

        with mock.patch(
            "crush_lu.wallet.google_api.update_google_wallet_pass"
        ) as google_patch, mock.patch(
            "crush_lu.wallet.passkit_apns.send_passkit_push_notifications",
            return_value={"success": 0, "failed": 0, "total": 0},
        ):
            with django_capture_on_commit_callbacks(execute=True):
                trigger_wallet_pass_updates(profile)
                CrushProfile.objects.filter(pk=profile.pk).delete()

        google_patch.assert_not_called()


@pytest.mark.django_db
class TestPointsChangesReachThePasses:
    """Both passes print the points balance, but every points mutation is a
    QuerySet.update() carrying an F() expression — that is what closes the
    concurrent-award race — and QuerySet.update() emits no signals. The
    balance used to self-heal on the holder's next bare profile.save(); once
    that stopped counting as a change, a points-only award left the installed
    pass showing the old total indefinitely."""

    def _referred(self, test_user_with_profile):
        from django.contrib.auth import get_user_model

        from crush_lu.models import ReferralAttribution, ReferralCode

        _user, referrer = test_user_with_profile
        referrer.apple_pass_serial = "member-serial"
        referrer.save(update_fields=["apple_pass_serial"])

        invitee = get_user_model().objects.create_user(
            username="invitee@example.com",
            email="invitee@example.com",
            password="x",
        )
        code = ReferralCode.get_or_create_for_profile(referrer)
        attribution = ReferralAttribution.objects.create(
            referral_code=code,
            referred_user=invitee,
            referrer=referrer,
            status=ReferralAttribution.Status.CONVERTED,
        )
        return referrer, attribution

    def test_points_only_award_refreshes_the_passes(self, test_user_with_profile):
        from crush_lu.referrals import apply_referral_reward

        referrer, attribution = self._referred(test_user_with_profile)

        # Asserted at refresh_ticket_serials, not the generic trigger: these
        # are request paths, so the refresh has to go through the bounded
        # helper (guaranteed marker advance + capped push) that AGENTS.md
        # calls for.
        with mock.patch(
            "crush_lu.wallet.passkit_service.refresh_ticket_serials"
        ) as refresh:
            apply_referral_reward(attribution, "signup")

        # No tier boundary crossed, so nothing saved the profile — without an
        # explicit refresh the pass keeps the old balance.
        referrer.refresh_from_db()
        assert referrer.membership_tier == "basic"
        assert "member-serial" in set(refresh.call_args.args[0])

    def test_a_tier_upgrade_refreshes_exactly_once(
        self, test_user_with_profile, settings
    ):
        from crush_lu.referrals import apply_referral_reward

        # Crossing a threshold saves membership_tier, which fires the receiver
        # on its own. Refreshing again on top of that would push a
        # byte-identical package.
        settings.REFERRAL_POINTS_PER_SIGNUP = 500
        settings.MEMBERSHIP_TIER_THRESHOLDS = {"bronze": 1, "silver": 2, "gold": 3}
        referrer, attribution = self._referred(test_user_with_profile)

        with mock.patch(
            "crush_lu.signals.trigger_wallet_pass_updates"
        ) as update, mock.patch(
            "crush_lu.wallet.passkit_service.refresh_ticket_serials"
        ) as refresh:
            apply_referral_reward(attribution, "signup")

        referrer.refresh_from_db()
        assert referrer.membership_tier != "basic"
        # The tier save fired the receiver; the points path must then stay out
        # of the way rather than adding a second, byte-identical push.
        assert update.call_count == 1
        refresh.assert_not_called()

    def test_the_profile_approval_bonus_refreshes_the_passes(
        self, test_user_with_profile, settings
    ):
        from datetime import date

        from crush_lu.models import CrushProfile
        from crush_lu.referrals import check_and_apply_profile_approved_reward

        # The bonus is a second QuerySet.update() site with the same shape as
        # the award above. Pinning the point settings rather than inheriting
        # them keeps the tier arithmetic below deterministic.
        settings.REFERRAL_POINTS_PER_SIGNUP = 100
        settings.REFERRAL_POINTS_PER_PROFILE_APPROVED = 50
        settings.MEMBERSHIP_TIER_THRESHOLDS = {
            "bronze": 200,
            "silver": 500,
            "gold": 1000,
        }
        referrer, attribution = self._referred(test_user_with_profile)
        # Reached only once the signup reward has already landed.
        attribution.reward_applied = True
        attribution.reward_points = 100
        attribution.save(update_fields=["reward_applied", "reward_points"])
        referred_profile = CrushProfile.objects.create(
            user=attribution.referred_user, date_of_birth=date(1995, 5, 15)
        )

        with mock.patch(
            "crush_lu.wallet.passkit_service.refresh_ticket_serials"
        ) as refresh:
            check_and_apply_profile_approved_reward(referred_profile)

        referrer.refresh_from_db()
        assert referrer.referral_points == 50
        # Well short of bronze, so no tier save fired the receiver for us.
        assert referrer.membership_tier == "basic"
        assert "member-serial" in set(refresh.call_args.args[0])

    def test_redeeming_points_refreshes_the_passes(self, test_user_with_profile):
        from crush_lu.referrals import refresh_passes_after_points_change

        # The redemption endpoint deducts with QuerySet.update() too, so the
        # balance drops on the pass with no signal behind it.
        _user, profile = test_user_with_profile
        profile.apple_pass_serial = "member-serial"
        profile.save(update_fields=["apple_pass_serial"])

        with mock.patch(
            "crush_lu.wallet.passkit_service.refresh_ticket_serials"
        ) as refresh:
            refresh_passes_after_points_change(profile)

        assert "member-serial" in set(refresh.call_args.args[0])

    def test_a_failing_push_never_costs_the_points(self, test_user_with_profile):
        from crush_lu.referrals import apply_referral_reward

        # The award is the user's money; a wallet push is not. If the push
        # raises, the points must still be committed.
        referrer, attribution = self._referred(test_user_with_profile)

        with mock.patch(
            "crush_lu.wallet.passkit_service.refresh_ticket_serials",
            side_effect=RuntimeError("APNs down"),
        ):
            points, _total = apply_referral_reward(attribution, "signup")

        referrer.refresh_from_db()
        assert points > 0
        assert referrer.referral_points == points


@pytest.mark.django_db
class TestRegistrationDeletionRefreshesTheMemberPass:
    """The member card prints the holder's NEXT event. The post_save receiver
    covers create and status changes; deletion had no equivalent and used to
    ride on the holder's next bare profile.save()."""

    def _member(self, event_with_registrations):
        _event, registrations = event_with_registrations
        registration = registrations[0]
        profile = registration.user.crushprofile
        profile.apple_pass_serial = "member-serial"
        profile.save(update_fields=["apple_pass_serial"])
        return registration, profile

    def test_deleting_one_registration_refreshes(
        self, _apple_identity, event_with_registrations
    ):
        registration, _profile = self._member(event_with_registrations)

        with mock.patch(
            "crush_lu.wallet.passkit_service.refresh_ticket_serials"
        ) as refresh:
            registration.delete()

        pushed = {s for c in refresh.call_args_list for s in c.args[0]}
        assert "member-serial" in pushed

    def test_an_event_cascade_marks_instead_of_pushing(
        self, _apple_identity, event_with_registrations
    ):
        event, _registrations = event_with_registrations
        _registration, _profile = self._member(event_with_registrations)

        # One push per row is not bounded in aggregate: each carries its own
        # budget, so N rows multiply the deadline N times — the pathology
        # cancel_events was already fixed for by batching. The marker advance
        # is the guaranteed half on its own: one query, no network, and the
        # card still rebuilds on Wallet's next poll.
        with mock.patch(
            "crush_lu.wallet.passkit_service.refresh_ticket_serials"
        ) as refresh, mock.patch(
            "crush_lu.wallet.passkit_apns.mark_passes_updated_bulk"
        ) as mark:
            event.delete()

        refresh.assert_not_called()
        assert "member-serial" in set(mark.call_args.args[1])


@pytest.mark.django_db
class TestReferralCodeChangeRefreshesTheMemberPass:
    """The member pass QR *is* the referral link. The code lives on another
    model, so it reaches neither WALLET_MEMBER_PASS_FIELDS nor any profile
    save — it was only ever picked up by the next bare profile.save()."""

    def _with_code(self, test_user_with_profile):
        from crush_lu.models import ReferralCode

        _user, profile = test_user_with_profile
        profile.apple_pass_serial = "member-serial"
        profile.google_wallet_object_id = "issuer.member-1"
        profile.save(update_fields=["apple_pass_serial", "google_wallet_object_id"])
        return profile, ReferralCode.get_or_create_for_profile(profile)

    def test_deactivating_a_code_refreshes(
        self, _apple_identity, test_user_with_profile
    ):
        # The one that actually bites: get_or_create_for_profile only returns
        # active codes, so the next build mints a different one while every
        # installed pass keeps scanning to the dead code — which capture
        # filters out, so it attributes nothing.
        profile, code = self._with_code(test_user_with_profile)

        with mock.patch(
            "crush_lu.wallet.passkit_service.refresh_ticket_serials"
        ) as refresh:
            code.is_active = False
            code.save(update_fields=["is_active"])

        assert "member-serial" in set(refresh.call_args.args[0])

    def test_editing_the_code_refreshes(
        self, _apple_identity, test_user_with_profile
    ):
        profile, code = self._with_code(test_user_with_profile)

        with mock.patch(
            "crush_lu.wallet.passkit_service.refresh_ticket_serials"
        ) as refresh:
            code.code = "NEWCODE1"
            code.save(update_fields=["code"])

        assert "member-serial" in set(refresh.call_args.args[0])

    def test_touching_an_unrelated_field_does_not_refresh(
        self, _apple_identity, test_user_with_profile
    ):
        from django.utils import timezone as dj_timezone

        # Capture stamps last_used_at on every scan. That must not fan out a
        # push per scan.
        profile, code = self._with_code(test_user_with_profile)

        with mock.patch(
            "crush_lu.wallet.passkit_service.refresh_ticket_serials"
        ) as refresh:
            code.last_used_at = dj_timezone.now()
            code.save(update_fields=["last_used_at"])

        refresh.assert_not_called()

    def test_creating_a_code_does_not_refresh(
        self, _apple_identity, test_user_with_profile
    ):
        from crush_lu.models import ReferralCode

        # get_or_create_for_profile is called FROM the pass build, so pushing
        # here would notify the device that just downloaded the pass.
        _user, profile = test_user_with_profile
        profile.apple_pass_serial = "member-serial"
        profile.save(update_fields=["apple_pass_serial"])

        with mock.patch(
            "crush_lu.wallet.passkit_service.refresh_ticket_serials"
        ) as refresh:
            ReferralCode.objects.create(referrer=profile)

        refresh.assert_not_called()

    def test_a_holder_with_no_pass_costs_nothing(
        self, _apple_identity, test_user_with_profile
    ):
        from crush_lu.models import ReferralCode

        _user, profile = test_user_with_profile
        assert not profile.apple_pass_serial
        code = ReferralCode.get_or_create_for_profile(profile)

        with mock.patch(
            "crush_lu.wallet.passkit_service.refresh_ticket_serials"
        ) as refresh:
            code.is_active = False
            code.save(update_fields=["is_active"])

        refresh.assert_not_called()

    def test_reassigning_the_owner_refreshes_both_holders(
        self, _apple_identity, test_user_with_profile
    ):
        from datetime import date

        from django.contrib.auth import get_user_model

        from crush_lu.models import CrushProfile

        # The old holder is the one that actually matters: their installed QR
        # now credits somebody else entirely.
        old_holder, code = self._with_code(test_user_with_profile)
        new_user = get_user_model().objects.create_user(
            username="new@example.com", email="new@example.com", password="x"
        )
        new_holder = CrushProfile.objects.create(
            user=new_user, date_of_birth=date(1995, 5, 15)
        )
        new_holder.apple_pass_serial = "new-holder-serial"
        new_holder.save(update_fields=["apple_pass_serial"])

        with mock.patch(
            "crush_lu.wallet.passkit_service.refresh_ticket_serials"
        ) as refresh:
            code.referrer = new_holder
            code.save(update_fields=["referrer"])

        pushed = {s for c in refresh.call_args_list for s in c.args[0]}
        assert pushed == {"member-serial", "new-holder-serial"}
        assert old_holder.apple_pass_serial == "member-serial"

    def test_deleting_a_code_refreshes(
        self, _apple_identity, test_user_with_profile
    ):
        # capture_referral needs a matching row, so once it is gone the QR
        # attributes nothing — and the next build mints a different code, so
        # the installed pass never converges on its own.
        profile, code = self._with_code(test_user_with_profile)

        with mock.patch(
            "crush_lu.wallet.passkit_service.refresh_ticket_serials"
        ) as refresh:
            code.delete()

        assert "member-serial" in set(refresh.call_args.args[0])

    def test_admin_bulk_delete_marks_instead_of_pushing(
        self, _apple_identity, test_user_with_profile
    ):
        from crush_lu.models import ReferralCode

        # ReferralCodeAdmin exposes Django's default "Delete selected", whose
        # delete_queryset() calls QuerySet.delete() — so origin is the
        # queryset, not each row, and pushing per row would fan out.
        profile, code = self._with_code(test_user_with_profile)

        with mock.patch(
            "crush_lu.wallet.passkit_service.refresh_ticket_serials"
        ) as refresh, mock.patch(
            "crush_lu.wallet.passkit_apns.mark_passes_updated_bulk"
        ) as mark:
            ReferralCode.objects.filter(pk=code.pk).delete()

        refresh.assert_not_called()
        assert "member-serial" in set(mark.call_args.args[1])

    def test_deleting_the_profile_does_not_refresh_its_own_code(
        self, _apple_identity, test_user_with_profile
    ):
        # The usual reason a code disappears is the profile going with it.
        # Refreshing a pass whose profile no longer exists is pointless.
        profile, _code = self._with_code(test_user_with_profile)

        with mock.patch(
            "crush_lu.wallet.passkit_service.refresh_ticket_serials"
        ) as refresh:
            profile.delete()

        refresh.assert_not_called()


@pytest.mark.django_db
class TestTicketStampsAreSignalFree:
    """Stamping ticket-only metadata must not emit post_save: the generic
    registration receiver treats any non-created save as a next-event change
    and synchronously refreshes both wallet backends — two network rounds
    hung off a ticket download."""

    def test_stamps_do_not_fire_the_registration_receiver(
        self, _apple_identity, event_with_registrations
    ):
        from crush_lu.wallet import apple_event_ticket

        _event, registrations = event_with_registrations
        registration = registrations[0]
        request = RequestFactory().get("/", HTTP_HOST="test.crush.lu", secure=True)

        with mock.patch(
            "crush_lu.signals.trigger_wallet_pass_updates"
        ) as wallet_update:
            with mock.patch.object(
                apple_event_ticket, "_build_pkpass", return_value=b""
            ):
                apple_event_ticket.build_apple_event_ticket(
                    registration, request=request
                )

        wallet_update.assert_not_called()
        # ...and the stamps still landed.
        registration.refresh_from_db()
        assert registration.apple_wallet_checkin_origin == "https://test.crush.lu"
        assert registration.apple_wallet_language


class TestLegacyDoubledVersionRoutes:
    """Passes installed before the webServiceURL fix carry the versioned root,
    so Apple requests /wallet/v1/v1/... — and that is the ONLY address those
    devices will ever use. Without these aliases an existing holder can never
    receive the corrected package; a push just sends them back to a 404."""

    @pytest.mark.parametrize(
        "path",
        [
            "/wallet/v1/v1/log",
            "/wallet/v1/v1/passes/pass.lu.crush/abc",
            "/wallet/v1/v1/devices/dev/registrations/pass.lu.crush",
            "/wallet/v1/v1/devices/dev/registrations/pass.lu.crush/abc",
        ],
    )
    def test_legacy_paths_resolve(self, path):
        from django.urls import resolve

        # Resolving at all is the point — previously every one of these 404'd.
        assert resolve(path, urlconf="azureproject.urls_crush") is not None

    def test_legacy_and_current_paths_share_a_view(self):
        from django.urls import resolve

        legacy = resolve("/wallet/v1/v1/log", urlconf="azureproject.urls_crush")
        current = resolve("/wallet/v1/log", urlconf="azureproject.urls_crush")
        assert legacy.func is current.func


@pytest.mark.django_db
class TestAdminBulkActionsRefreshWallets:
    """Every one of these actions uses QuerySet.update(), which emits no
    signals — so each has to schedule its own refresh, and they must agree
    with what the payload actually voids."""

    def _admin(self, model):
        # These live on the project's custom admin site, not django's default.
        from crush_lu.admin.site import crush_admin_site

        return crush_admin_site._registry[model]

    def _ticketed(self, registration, serial):
        registration.apple_wallet_ticket_serial = serial
        registration.save(update_fields=["apple_wallet_ticket_serial"])
        return registration

    def test_confirm_restores_tickets_from_any_invalid_status(
        self, _apple_identity, event_with_registrations
    ):
        from crush_lu.models import EventRegistration

        # waitlist is invalid, so its ticket is voided — confirming it must
        # un-void. Collecting only "cancelled" left it visibly dead.
        _event, registrations = event_with_registrations
        registration = self._ticketed(registrations[0], "evt-1-reg-1-abcd")
        registration.status = "waitlist"
        registration.save(update_fields=["status"])
        profile = registration.user.crushprofile
        profile.apple_pass_serial = "member-serial"
        profile.save(update_fields=["apple_pass_serial"])

        with mock.patch(
            "crush_lu.wallet.passkit_service.refresh_ticket_serials"
        ) as refresh:
            with mock.patch("crush_lu.admin.events.django_messages"):
                self._admin(EventRegistration).confirm_registrations(
                    RequestFactory().post("/"),
                    EventRegistration.objects.filter(pk=registration.pk),
                )

        pushed = set(refresh.call_args.args[0])
        assert "evt-1-reg-1-abcd" in pushed
        # ...and the member card: restoring the seat makes this event eligible
        # for get_next_event_for_pass again.
        assert "member-serial" in pushed

    def test_cancel_events_batches_into_one_refresh(
        self, _apple_identity, event_with_registrations
    ):
        from crush_lu.models import MeetupEvent

        # One call for the whole action: each helper starts its own push
        # budget, so a per-event loop restarted the deadline 2N times and let
        # request time grow with the selection.
        event, registrations = event_with_registrations
        self._ticketed(registrations[0], "evt-1-reg-1-abcd")
        profile = registrations[0].user.crushprofile
        profile.apple_pass_serial = "member-serial"
        profile.save(update_fields=["apple_pass_serial"])

        with mock.patch(
            "crush_lu.wallet.passkit_service.refresh_ticket_serials"
        ) as refresh:
            with mock.patch("crush_lu.admin.events.django_messages"):
                self._admin(MeetupEvent).cancel_events(
                    RequestFactory().post("/"),
                    MeetupEvent.objects.filter(pk=event.pk),
                )

        assert refresh.call_count == 1
        assert set(refresh.call_args.args[0]) == {
            "evt-1-reg-1-abcd",
            "member-serial",
        }

    def test_quiz_no_show_action_voids_tickets(
        self, _apple_identity, event_with_registrations
    ):
        from crush_lu.admin.quiz import mark_unattended_as_no_show
        from crush_lu.models.quiz import QuizEvent

        # mark_unattended_as_no_show uses .update(status="no_show") per quiz,
        # bypassing the receiver — and no_show now voids the ticket.
        event, registrations = event_with_registrations
        registration = self._ticketed(registrations[0], "evt-1-reg-1-abcd")
        assert registration.status == "confirmed"
        profile = registration.user.crushprofile
        profile.apple_pass_serial = "member-serial"
        profile.save(update_fields=["apple_pass_serial"])
        quiz = QuizEvent.objects.create(event=event, created_by=registration.user)

        with mock.patch(
            "crush_lu.wallet.passkit_service.refresh_ticket_serials"
        ) as refresh:
            with mock.patch("crush_lu.admin.quiz.messages"):
                mark_unattended_as_no_show(
                    None,
                    RequestFactory().post("/"),
                    QuizEvent.objects.filter(pk=quiz.pk),
                )

        assert refresh.call_count == 1
        pushed = set(refresh.call_args.args[0])
        assert "evt-1-reg-1-abcd" in pushed
        # ...and the member card, which stops showing this quiz as the
        # holder's next event once they are marked absent.
        assert "member-serial" in pushed

    def test_move_to_waitlist_voids_the_ticket(
        self, _apple_identity, event_with_registrations
    ):
        from crush_lu.models import EventRegistration

        _event, registrations = event_with_registrations
        registration = self._ticketed(registrations[0], "evt-1-reg-1-abcd")

        with mock.patch(
            "crush_lu.wallet.passkit_service.refresh_ticket_serials"
        ) as refresh:
            with mock.patch("crush_lu.admin.events.django_messages"):
                self._admin(EventRegistration).move_to_waitlist(
                    RequestFactory().post("/"),
                    EventRegistration.objects.filter(pk=registration.pk),
                )

        assert "evt-1-reg-1-abcd" in refresh.call_args.args[0]


@pytest.mark.django_db
class TestLegacyTicketTokenBackfill:
    """Migration 0211 copies the profile token onto tickets issued before 0208,
    so an account merge can no longer orphan their authentication."""

    def test_backfill_copies_the_profile_token(self, event_with_registrations):
        _0211 = __import__(
            "crush_lu.migrations.0211_backfill_event_ticket_auth_tokens",
            fromlist=["backfill_ticket_tokens"],
        )
        from django.apps import apps as global_apps

        _event, registrations = event_with_registrations
        registration = registrations[0]
        registration.apple_wallet_ticket_serial = "evt-1-reg-1-legacy"
        registration.apple_wallet_auth_token = ""  # pre-0208 state
        registration.save(
            update_fields=["apple_wallet_ticket_serial", "apple_wallet_auth_token"]
        )
        profile = registration.user.crushprofile
        profile.apple_auth_token = "tok-from-profile"
        profile.save(update_fields=["apple_auth_token"])

        _0211.backfill_ticket_tokens(global_apps, None)

        registration.refresh_from_db()
        assert registration.apple_wallet_auth_token == "tok-from-profile"

    def test_backfill_leaves_already_stamped_tickets_alone(
        self, event_with_registrations
    ):
        _0211 = __import__(
            "crush_lu.migrations.0211_backfill_event_ticket_auth_tokens",
            fromlist=["backfill_ticket_tokens"],
        )
        from django.apps import apps as global_apps

        _event, registrations = event_with_registrations
        registration = registrations[0]
        registration.apple_wallet_ticket_serial = "evt-1-reg-1-stamped"
        registration.apple_wallet_auth_token = "tok-already-issued"
        registration.save(
            update_fields=["apple_wallet_ticket_serial", "apple_wallet_auth_token"]
        )
        profile = registration.user.crushprofile
        profile.apple_auth_token = "tok-from-profile"
        profile.save(update_fields=["apple_auth_token"])

        _0211.backfill_ticket_tokens(global_apps, None)

        registration.refresh_from_db()
        # The installed pass carries the stamped token; overwriting it would
        # break a working ticket.
        assert registration.apple_wallet_auth_token == "tok-already-issued"


@pytest.mark.django_db
class TestPasskitRowsAreCleanedUp:
    """PasskitDeviceRegistration keys off the pass serial with no FK, so
    nothing cascades. Orphans keep an APNs push token alive past the
    account-deletion flow and keep answering the poll with a serial the web
    service can only 500 on."""

    def _row(self, serial):
        from crush_lu.models import PasskitDeviceRegistration

        return PasskitDeviceRegistration.objects.create(
            device_library_identifier="device-abc",
            pass_type_identifier="pass.lu.crush",
            serial_number=serial,
            push_token="push-tok",
        )

    def test_deleting_a_registration_drops_its_device_rows(
        self, event_with_registrations
    ):
        from crush_lu.models import PasskitDeviceRegistration

        _event, registrations = event_with_registrations
        registration = registrations[0]
        registration.apple_wallet_ticket_serial = "evt-1-reg-1-abcd"
        registration.save(update_fields=["apple_wallet_ticket_serial"])
        self._row("evt-1-reg-1-abcd")

        registration.delete()
        assert not PasskitDeviceRegistration.objects.filter(
            serial_number="evt-1-reg-1-abcd"
        ).exists()

    def test_deleting_a_profile_drops_its_member_device_rows(
        self, test_user_with_profile
    ):
        from crush_lu.models import PasskitDeviceRegistration

        _user, profile = test_user_with_profile
        profile.apple_pass_serial = "member-serial"
        profile.save(update_fields=["apple_pass_serial"])
        self._row("member-serial")

        profile.delete()
        assert not PasskitDeviceRegistration.objects.filter(
            serial_number="member-serial"
        ).exists()

    def test_unrelated_rows_survive(self, event_with_registrations):
        from crush_lu.models import PasskitDeviceRegistration

        _event, registrations = event_with_registrations
        registration = registrations[0]
        registration.apple_wallet_ticket_serial = "evt-1-reg-1-abcd"
        registration.save(update_fields=["apple_wallet_ticket_serial"])
        self._row("evt-1-reg-1-abcd")
        self._row("someone-elses-serial")

        registration.delete()
        assert PasskitDeviceRegistration.objects.filter(
            serial_number="someone-elses-serial"
        ).exists()


@pytest.mark.django_db
class TestNextEventExcludesCancelled:
    """A cancelled event is nobody's next event. Without this the member card
    kept advertising it — and cancelling rebuilt a byte-identical pass, so the
    refresh that cancellation triggers accomplished nothing."""

    def test_cancelled_event_is_not_the_next_event(self, event_with_registrations):
        from crush_lu.wallet_pass import get_next_event_for_pass

        event, registrations = event_with_registrations
        profile = registrations[0].user.crushprofile
        assert get_next_event_for_pass(profile) is not None

        event.is_cancelled = True
        event.save(update_fields=["is_cancelled"])

        assert get_next_event_for_pass(profile) is None


@pytest.mark.django_db
class TestRebuildLanguage:
    """Apple's update request carries no locale. Without an explicit
    activation the rebuild reads modeltranslation descriptors under `en`, so a
    French holder's first refresh would silently return an English pass."""

    def test_issuance_language_is_stamped(
        self, _apple_identity, event_with_registrations
    ):
        from django.utils import translation

        from crush_lu.wallet import apple_event_ticket

        _event, registrations = event_with_registrations
        registration = registrations[0]
        request = RequestFactory().get("/", HTTP_HOST="crush.lu", secure=True)

        with translation.override("fr"):
            with mock.patch.object(
                apple_event_ticket, "_build_pkpass", return_value=b""
            ):
                apple_event_ticket.build_apple_event_ticket(
                    registration, request=request
                )

        registration.refresh_from_db()
        assert registration.apple_wallet_language == "fr"

    def test_rebuild_uses_the_stamped_language(
        self, _apple_identity, event_with_registrations
    ):
        from crush_lu.wallet.apple_pass import _ticket_language

        _event, registrations = event_with_registrations
        registration = registrations[0]
        registration.apple_wallet_language = "de"
        registration.save(update_fields=["apple_wallet_language"])

        assert _ticket_language(registration) == "de"

    def test_falls_back_to_the_profile_preference(
        self, _apple_identity, event_with_registrations
    ):
        from crush_lu.wallet.apple_pass import _ticket_language

        # Tickets issued before the field existed have no stamp.
        _event, registrations = event_with_registrations
        registration = registrations[0]
        profile = registration.user.crushprofile
        profile.preferred_language = "fr"
        profile.save(update_fields=["preferred_language"])

        assert _ticket_language(registration) == "fr"


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
