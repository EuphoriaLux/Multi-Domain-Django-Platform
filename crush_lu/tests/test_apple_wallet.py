"""Tests for Apple Wallet pass generation."""

import hashlib
import json
import os
import zipfile
from io import BytesIO

import pytest
from django.test import override_settings

# Certificate paths for testing (use real certs in certs/apple/)
CERT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "certs",
    "apple",
)
APPLE_WALLET_SETTINGS = {
    "WALLET_APPLE_PASS_TYPE_IDENTIFIER": "pass.lu.crush",
    "WALLET_APPLE_TEAM_IDENTIFIER": "C5XDPB2G33",
    "WALLET_APPLE_ORGANIZATION_NAME": "Crush.lu",
    "WALLET_APPLE_CERT_PATH": os.path.join(CERT_DIR, "crush-pass-cert.pem"),
    "WALLET_APPLE_KEY_PATH": os.path.join(CERT_DIR, "crush-pass-key.pem"),
    "WALLET_APPLE_KEY_PASSWORD": "",
    "WALLET_APPLE_WWDR_CERT_PATH": os.path.join(CERT_DIR, "wwdr-g4.pem"),
    "WALLET_APPLE_WEB_SERVICE_URL": "https://crush.lu/wallet/v1",
    "WALLET_APPLE_CERT_BASE64": "",
    "WALLET_APPLE_KEY_BASE64": "",
    "WALLET_APPLE_WWDR_CERT_BASE64": "",
}

# Skip all tests if certs are not present
pytestmark = pytest.mark.skipif(
    not os.path.exists(os.path.join(CERT_DIR, "crush-pass-cert.pem")),
    reason="Apple Wallet certificates not available",
)


@pytest.fixture(autouse=True)
def _wallet_settings(settings):
    """Apply wallet settings for all tests in this module."""
    for key, value in APPLE_WALLET_SETTINGS.items():
        setattr(settings, key, value)


class TestLoadCertBytes:
    """Test _load_cert_bytes loads from file paths or base64."""

    def test_loads_from_file_paths(self):
        from crush_lu.wallet.apple_pass import _load_cert_bytes

        cert, key, wwdr = _load_cert_bytes()
        assert cert.startswith(b"-----BEGIN CERTIFICATE-----")
        assert key.startswith(b"-----BEGIN RSA PRIVATE KEY-----")
        assert wwdr.startswith(b"-----BEGIN CERTIFICATE-----")

    def test_loads_from_base64(self, settings):
        import base64

        from crush_lu.wallet.apple_pass import _load_cert_bytes

        with open(APPLE_WALLET_SETTINGS["WALLET_APPLE_CERT_PATH"], "rb") as f:
            cert_b64 = base64.b64encode(f.read()).decode()
        with open(APPLE_WALLET_SETTINGS["WALLET_APPLE_KEY_PATH"], "rb") as f:
            key_b64 = base64.b64encode(f.read()).decode()
        with open(APPLE_WALLET_SETTINGS["WALLET_APPLE_WWDR_CERT_PATH"], "rb") as f:
            wwdr_b64 = base64.b64encode(f.read()).decode()

        settings.WALLET_APPLE_CERT_BASE64 = cert_b64
        settings.WALLET_APPLE_KEY_BASE64 = key_b64
        settings.WALLET_APPLE_WWDR_CERT_BASE64 = wwdr_b64
        settings.WALLET_APPLE_CERT_PATH = ""
        settings.WALLET_APPLE_KEY_PATH = ""
        settings.WALLET_APPLE_WWDR_CERT_PATH = ""

        cert, key, wwdr = _load_cert_bytes()
        assert cert.startswith(b"-----BEGIN CERTIFICATE-----")
        assert key.startswith(b"-----BEGIN RSA PRIVATE KEY-----")


class TestSignManifest:
    """Test PKCS#7 signing of manifest bytes."""

    def test_produces_der_signature(self):
        from crush_lu.wallet.apple_pass import _sign_manifest

        manifest = b'{"pass.json":"abc123"}'
        signature = _sign_manifest(manifest)

        assert isinstance(signature, bytes)
        assert len(signature) > 100
        assert signature[0] == 0x30


class TestBuildApplePass:
    """Test full .pkpass generation."""

    def test_returns_valid_zip(self, test_user_with_profile):
        from crush_lu.wallet.apple_pass import build_apple_pass

        user, profile = test_user_with_profile
        pkpass_bytes = build_apple_pass(profile)

        assert isinstance(pkpass_bytes, bytes)
        zf = zipfile.ZipFile(BytesIO(pkpass_bytes))
        names = zf.namelist()
        assert "pass.json" in names
        assert "manifest.json" in names
        assert "signature" in names
        assert "icon.png" in names

    def test_pass_json_has_correct_fields(self, test_user_with_profile):
        from crush_lu.wallet.apple_pass import build_apple_pass

        user, profile = test_user_with_profile
        pkpass_bytes = build_apple_pass(profile)

        zf = zipfile.ZipFile(BytesIO(pkpass_bytes))
        pass_json = json.loads(zf.read("pass.json"))

        assert pass_json["passTypeIdentifier"] == "pass.lu.crush"
        assert pass_json["teamIdentifier"] == "C5XDPB2G33"
        assert pass_json["organizationName"] == "Crush.lu"
        assert pass_json["formatVersion"] == 1
        assert "generic" in pass_json
        assert pass_json["generic"]["primaryFields"][0]["key"] == "member"

    def test_assigns_serial_and_auth_token(self, test_user_with_profile):
        from crush_lu.wallet.apple_pass import build_apple_pass

        user, profile = test_user_with_profile
        assert profile.apple_pass_serial == ""

        build_apple_pass(profile)
        profile.refresh_from_db()

        assert profile.apple_pass_serial != ""
        assert profile.apple_auth_token != ""

    def test_manifest_matches_file_hashes(self, test_user_with_profile):
        from crush_lu.wallet.apple_pass import build_apple_pass

        user, profile = test_user_with_profile
        pkpass_bytes = build_apple_pass(profile)

        zf = zipfile.ZipFile(BytesIO(pkpass_bytes))
        manifest = json.loads(zf.read("manifest.json"))

        for filename, expected_hash in manifest.items():
            actual_hash = hashlib.sha1(
                zf.read(filename), usedforsecurity=False
            ).hexdigest()
            assert actual_hash == expected_hash, f"Hash mismatch for {filename}"


class TestProvidePassForSerial:
    """Test PassKit web service provider callback."""

    def test_returns_pkpass_for_valid_serial(self, test_user_with_profile):
        from crush_lu.wallet.apple_pass import (
            build_apple_pass,
            provide_pass_for_serial,
        )

        user, profile = test_user_with_profile
        build_apple_pass(profile)
        profile.refresh_from_db()

        result = provide_pass_for_serial("pass.lu.crush", profile.apple_pass_serial)
        assert result is not None
        assert isinstance(result, bytes)

    def test_returns_none_for_unknown_serial(self):
        from crush_lu.wallet.apple_pass import provide_pass_for_serial

        result = provide_pass_for_serial("pass.lu.crush", "nonexistent")
        assert result is None


class TestBuildAppleEventTicket:
    """Test Apple Wallet EventTicket .pkpass generation."""

    def test_returns_valid_zip(self, event_with_registrations):
        from crush_lu.wallet.apple_event_ticket import build_apple_event_ticket

        _event, registrations = event_with_registrations
        registration = registrations[0]
        pkpass_bytes = build_apple_event_ticket(registration)

        assert isinstance(pkpass_bytes, bytes)
        zf = zipfile.ZipFile(BytesIO(pkpass_bytes))
        names = zf.namelist()
        assert "pass.json" in names
        assert "manifest.json" in names
        assert "signature" in names

    def test_pass_uses_event_ticket_style(self, event_with_registrations):
        from crush_lu.wallet.apple_event_ticket import build_apple_event_ticket

        _event, registrations = event_with_registrations
        registration = registrations[0]
        pkpass_bytes = build_apple_event_ticket(registration)

        zf = zipfile.ZipFile(BytesIO(pkpass_bytes))
        pass_json = json.loads(zf.read("pass.json"))

        assert "eventTicket" in pass_json
        assert "generic" not in pass_json
        assert pass_json["passTypeIdentifier"] == "pass.lu.crush"

    def test_pass_contains_event_details(self, event_with_registrations):
        from crush_lu.wallet.apple_event_ticket import build_apple_event_ticket

        _event, registrations = event_with_registrations
        registration = registrations[0]
        pkpass_bytes = build_apple_event_ticket(registration)

        zf = zipfile.ZipFile(BytesIO(pkpass_bytes))
        pass_json = json.loads(zf.read("pass.json"))

        event_ticket = pass_json["eventTicket"]
        primary_keys = [f["key"] for f in event_ticket["primaryFields"]]
        assert "event_name" in primary_keys

    def test_pass_has_checkin_qr_code(self, event_with_registrations):
        from crush_lu.wallet.apple_event_ticket import build_apple_event_ticket

        _event, registrations = event_with_registrations
        registration = registrations[0]
        pkpass_bytes = build_apple_event_ticket(registration)

        zf = zipfile.ZipFile(BytesIO(pkpass_bytes))
        pass_json = json.loads(zf.read("pass.json"))

        assert pass_json["barcode"]["format"] == "PKBarcodeFormatQR"
        assert "/api/events/checkin/" in pass_json["barcode"]["message"]

    def test_assigns_serial_number(self, event_with_registrations):
        from crush_lu.wallet.apple_event_ticket import build_apple_event_ticket

        _event, registrations = event_with_registrations
        registration = registrations[0]
        assert registration.apple_wallet_ticket_serial == ""

        build_apple_event_ticket(registration)
        registration.refresh_from_db()

        assert registration.apple_wallet_ticket_serial != ""
        assert registration.apple_wallet_ticket_serial.startswith("evt-")


def _grant_consent(user):
    """Grant Crush.lu consent for a user so views aren't blocked by middleware."""
    from crush_lu.models.profiles import UserDataConsent

    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )


class TestAppleWalletPassView:
    """Test the member pass download view."""

    def test_returns_pkpass_for_authenticated_user(self, test_user_with_profile):
        from django.test import Client

        user, profile = test_user_with_profile
        _grant_consent(user)
        client = Client()
        client.force_login(user)

        response = client.get("/wallet/apple/pass/")

        assert response.status_code == 200
        assert response["Content-Type"] == "application/vnd.apple.pkpass"
        assert "crushlu.pkpass" in response["Content-Disposition"]

    def test_requires_authentication(self):
        from django.test import Client

        client = Client()
        response = client.get("/wallet/apple/pass/")

        assert response.status_code == 302  # Redirect to login

    def test_returns_503_when_not_configured(self, test_user_with_profile, settings):
        from django.test import Client

        user, profile = test_user_with_profile
        _grant_consent(user)
        client = Client()
        client.force_login(user)

        settings.WALLET_APPLE_PASS_TYPE_IDENTIFIER = ""
        response = client.get("/wallet/apple/pass/")

        assert response.status_code == 503


class TestAppleEventTicketView:
    """Test the event ticket .pkpass download view."""

    def test_returns_pkpass_for_confirmed_registration(self, event_with_registrations):
        from django.test import Client

        _event, registrations = event_with_registrations
        registration = registrations[0]
        _grant_consent(registration.user)
        client = Client()
        client.force_login(registration.user)

        response = client.get(
            f"/wallet/apple/event-ticket/{registration.id}/pass/"
        )

        assert response.status_code == 200
        assert response["Content-Type"] == "application/vnd.apple.pkpass"

    def test_rejects_other_users_registration(self, event_with_registrations, db):
        from django.contrib.auth.models import User
        from django.test import Client

        _event, registrations = event_with_registrations
        registration = registrations[0]
        other_user = User.objects.create_user(
            username="other@example.com", password="pass123"
        )
        _grant_consent(other_user)
        client = Client()
        client.force_login(other_user)

        response = client.get(
            f"/wallet/apple/event-ticket/{registration.id}/pass/"
        )

        assert response.status_code == 404

    def test_rejects_cancelled_registration(self, event_with_registrations):
        from django.test import Client

        _event, registrations = event_with_registrations
        registration = registrations[0]
        registration.status = "cancelled"
        registration.save()

        _grant_consent(registration.user)
        client = Client()
        client.force_login(registration.user)

        response = client.get(
            f"/wallet/apple/event-ticket/{registration.id}/pass/"
        )

        assert response.status_code == 400
