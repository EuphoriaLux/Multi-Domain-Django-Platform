# Apple Wallet Tickets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete Apple Wallet integration for Crush.lu — member passes and event tickets, with pure Python signing.

**Architecture:** Replace OpenSSL subprocess signing with `cryptography` PKCS#7, activate the existing member pass view, build a new EventTicket pass builder parallel to the Google Wallet event ticket, and add "Add to Apple Wallet" buttons to the ticket page.

**Tech Stack:** Django 6.0, `cryptography` library (already installed), Apple PassKit `.pkpass` format, PKCS#7 DER signing.

**Spec:** `docs/superpowers/specs/2026-04-07-apple-wallet-tickets-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `azureproject/settings.py` | Add `_BASE64` env vars for certificates |
| Modify | `crush_lu/wallet/apple_pass.py` | Replace OpenSSL signing with `cryptography`, add `_load_cert_bytes()`, add `provide_pass_for_serial()` |
| Create | `crush_lu/wallet/apple_event_ticket.py` | EventTicket `.pkpass` builder for event registrations |
| Modify | `crush_lu/wallet/__init__.py` | Export new `build_apple_event_ticket` |
| Modify | `crush_lu/models/events.py` | Add `apple_wallet_ticket_serial` field to `EventRegistration` |
| Create | Migration | `AddField` for new model field |
| Modify | `crush_lu/views_wallet.py` | Activate member pass, add event ticket view, update config check |
| Modify | `crush_lu/views_ticket.py` | Add Apple Wallet context to template |
| Modify | `azureproject/urls_crush.py` | Add Apple event ticket URL |
| Modify | `crush_lu/templates/crush_lu/event_ticket.html` | Add "Add to Apple Wallet" button |
| Create | `crush_lu/tests/test_apple_wallet.py` | All Apple Wallet tests |

---

### Task 1: Settings — Add base64 certificate env vars

**Files:**
- Modify: `azureproject/settings.py:326-334`

- [ ] **Step 1: Add base64 env var settings**

In `azureproject/settings.py`, after the existing `WALLET_APPLE_WWDR_CERT_PATH` line (line 334), add:

```python
WALLET_APPLE_CERT_BASE64 = os.getenv("WALLET_APPLE_CERT_BASE64", "")
WALLET_APPLE_KEY_BASE64 = os.getenv("WALLET_APPLE_KEY_BASE64", "")
WALLET_APPLE_WWDR_CERT_BASE64 = os.getenv("WALLET_APPLE_WWDR_CERT_BASE64", "")
```

- [ ] **Step 2: Set the PASSKIT_PASS_PROVIDER setting**

In `azureproject/settings.py`, find the line `PASSKIT_PASS_PROVIDER = os.getenv("PASSKIT_PASS_PROVIDER")` (around line 969) and change to:

```python
PASSKIT_PASS_PROVIDER = os.getenv(
    "PASSKIT_PASS_PROVIDER",
    "crush_lu.wallet.apple_pass.provide_pass_for_serial",
)
```

- [ ] **Step 3: Add local .env configuration**

Add to `.env` file:

```
WALLET_APPLE_PASS_TYPE_IDENTIFIER=pass.lu.crush
WALLET_APPLE_TEAM_IDENTIFIER=C5XDPB2G33
WALLET_APPLE_ORGANIZATION_NAME=Crush.lu
WALLET_APPLE_CERT_PATH=certs/apple/crush-pass-cert.pem
WALLET_APPLE_KEY_PATH=certs/apple/crush-pass-key.pem
WALLET_APPLE_KEY_PASSWORD=
WALLET_APPLE_WWDR_CERT_PATH=certs/apple/wwdr-g4.pem
WALLET_APPLE_WEB_SERVICE_URL=https://crush.lu/wallet/v1
```

- [ ] **Step 4: Verify server starts**

Run: `.venv/Scripts/python.exe manage.py check`

Expected: `System check identified no issues.`

- [ ] **Step 5: Commit**

```bash
git add azureproject/settings.py
git commit -m "feat(wallet): add base64 cert env vars and PassKit provider default"
```

---

### Task 2: Pure Python signing — Replace OpenSSL subprocess

**Files:**
- Modify: `crush_lu/wallet/apple_pass.py`
- Create: `crush_lu/tests/test_apple_wallet.py`

- [ ] **Step 1: Write test for `_load_cert_bytes`**

Create `crush_lu/tests/test_apple_wallet.py`:

```python
"""Tests for Apple Wallet pass generation."""

import json
import os
import zipfile
from io import BytesIO
from unittest.mock import patch

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


@override_settings(**APPLE_WALLET_SETTINGS)
class TestLoadCertBytes:
    """Test _load_cert_bytes loads from file paths or base64."""

    def test_loads_from_file_paths(self):
        from crush_lu.wallet.apple_pass import _load_cert_bytes

        cert, key, wwdr = _load_cert_bytes()
        assert cert.startswith(b"-----BEGIN CERTIFICATE-----")
        assert key.startswith(b"-----BEGIN RSA PRIVATE KEY-----")
        assert wwdr.startswith(b"-----BEGIN CERTIFICATE-----")

    def test_loads_from_base64(self):
        import base64

        from crush_lu.wallet.apple_pass import _load_cert_bytes

        # Read files and encode as base64
        with open(APPLE_WALLET_SETTINGS["WALLET_APPLE_CERT_PATH"], "rb") as f:
            cert_b64 = base64.b64encode(f.read()).decode()
        with open(APPLE_WALLET_SETTINGS["WALLET_APPLE_KEY_PATH"], "rb") as f:
            key_b64 = base64.b64encode(f.read()).decode()
        with open(APPLE_WALLET_SETTINGS["WALLET_APPLE_WWDR_CERT_PATH"], "rb") as f:
            wwdr_b64 = base64.b64encode(f.read()).decode()

        with override_settings(
            WALLET_APPLE_CERT_BASE64=cert_b64,
            WALLET_APPLE_KEY_BASE64=key_b64,
            WALLET_APPLE_WWDR_CERT_BASE64=wwdr_b64,
            WALLET_APPLE_CERT_PATH="",
            WALLET_APPLE_KEY_PATH="",
            WALLET_APPLE_WWDR_CERT_PATH="",
        ):
            cert, key, wwdr = _load_cert_bytes()
            assert cert.startswith(b"-----BEGIN CERTIFICATE-----")
            assert key.startswith(b"-----BEGIN RSA PRIVATE KEY-----")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/pytest.exe crush_lu/tests/test_apple_wallet.py::TestLoadCertBytes -v`

Expected: FAIL — `_load_cert_bytes` does not exist yet.

- [ ] **Step 3: Implement `_load_cert_bytes` and `_sign_manifest_python`**

Replace the entire `crush_lu/wallet/apple_pass.py` with:

```python
import base64
import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509 import load_pem_x509_certificate
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from ..wallet_pass import build_wallet_pass_data

# Placeholder 1x1 transparent PNG for icon (required by Apple)
ICON_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwAB"
    "BAEAu1fE3wAAAABJRU5ErkJggg=="
)


def _require_setting(name):
    value = getattr(settings, name, None)
    if not value:
        raise ImproperlyConfigured(f"Missing required setting: {name}")
    return value


def _load_cert_bytes():
    """
    Load certificate, key, and WWDR cert as bytes.

    Checks _BASE64 env vars first (for Azure production),
    falls back to _PATH file reads (for local development).

    Returns:
        tuple: (cert_pem_bytes, key_pem_bytes, wwdr_pem_bytes)
    """
    cert_b64 = getattr(settings, "WALLET_APPLE_CERT_BASE64", "")
    key_b64 = getattr(settings, "WALLET_APPLE_KEY_BASE64", "")
    wwdr_b64 = getattr(settings, "WALLET_APPLE_WWDR_CERT_BASE64", "")

    if cert_b64 and key_b64 and wwdr_b64:
        return (
            base64.b64decode(cert_b64),
            base64.b64decode(key_b64),
            base64.b64decode(wwdr_b64),
        )

    cert_path = _require_setting("WALLET_APPLE_CERT_PATH")
    key_path = _require_setting("WALLET_APPLE_KEY_PATH")
    wwdr_path = _require_setting("WALLET_APPLE_WWDR_CERT_PATH")

    with open(cert_path, "rb") as f:
        cert_bytes = f.read()
    with open(key_path, "rb") as f:
        key_bytes = f.read()
    with open(wwdr_path, "rb") as f:
        wwdr_bytes = f.read()

    return cert_bytes, key_bytes, wwdr_bytes


def _sign_manifest(manifest_bytes):
    """
    Sign manifest.json bytes using PKCS#7 detached signature.

    Uses the cryptography library instead of subprocess OpenSSL.
    Returns DER-encoded PKCS#7 signature bytes.
    """
    cert_pem, key_pem, wwdr_pem = _load_cert_bytes()

    key_password = getattr(settings, "WALLET_APPLE_KEY_PASSWORD", "") or None
    if key_password:
        key_password = key_password.encode("utf-8")

    cert = load_pem_x509_certificate(cert_pem)
    private_key = serialization.load_pem_private_key(key_pem, password=key_password)
    wwdr_cert = load_pem_x509_certificate(wwdr_pem)

    return (
        pkcs7.PKCS7SignatureBuilder()
        .set_data(manifest_bytes)
        .add_signer(cert, private_key, hashes.SHA256())
        .add_certificate(wwdr_cert)
        .sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.DetachedSignature])
    )


def _ensure_pass_identifiers(profile):
    updated_fields = []
    if not profile.apple_pass_serial:
        profile.apple_pass_serial = secrets.token_hex(8)
        updated_fields.append("apple_pass_serial")
    if not profile.apple_auth_token:
        profile.apple_auth_token = secrets.token_hex(16)
        updated_fields.append("apple_auth_token")
    if updated_fields:
        profile.save(update_fields=updated_fields)
    return profile.apple_pass_serial, profile.apple_auth_token


def _build_pass_payload(profile, serial_number, auth_token, request=None):
    """
    Build the pass.json payload for Apple Wallet.

    The pass includes:
    - Member name and tier (primary fields)
    - Next event info if registered (secondary fields)
    - Member since and points (auxiliary fields)
    - Referral QR code (barcode)
    """
    pass_type_identifier = _require_setting("WALLET_APPLE_PASS_TYPE_IDENTIFIER")
    team_identifier = _require_setting("WALLET_APPLE_TEAM_IDENTIFIER")
    organization_name = _require_setting("WALLET_APPLE_ORGANIZATION_NAME")
    web_service_url = getattr(settings, "WALLET_APPLE_WEB_SERVICE_URL", "")

    pass_data = build_wallet_pass_data(profile, request=request)

    primary_fields = [
        {
            "key": "member",
            "label": "Member",
            "value": pass_data["display_name"],
        }
    ]

    header_fields = [
        {
            "key": "tier",
            "label": "Status",
            "value": pass_data["tier_display"],
        }
    ]

    secondary_fields = []
    if pass_data["next_event"]:
        secondary_fields.append({
            "key": "next_event",
            "label": "Next Event",
            "value": pass_data["next_event"]["title"],
        })
        secondary_fields.append({
            "key": "event_date",
            "label": "Date",
            "value": pass_data["next_event"]["date"],
        })
    else:
        secondary_fields.append({
            "key": "next_event",
            "label": "Next Event",
            "value": "No upcoming events",
        })

    auxiliary_fields = [
        {
            "key": "member_since",
            "label": "Member since",
            "value": pass_data["member_since"] or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        },
        {
            "key": "points",
            "label": "Points",
            "value": str(pass_data["referral_points"]),
        },
    ]

    back_fields = [
        {
            "key": "referral_info",
            "label": "Share & Earn",
            "value": "Invite friends using the QR code on this card. Earn 100 points for each signup!",
        },
        {
            "key": "tier_info",
            "label": "Membership Tiers",
            "value": "Basic (0pts) > Bronze (200pts) > Silver (500pts) > Gold (1000pts)",
        },
    ]

    if pass_data["next_event"] and pass_data["next_event"].get("location"):
        back_fields.append({
            "key": "event_location",
            "label": "Event Location",
            "value": pass_data["next_event"]["location"],
        })

    payload = {
        "formatVersion": 1,
        "passTypeIdentifier": pass_type_identifier,
        "serialNumber": serial_number,
        "teamIdentifier": team_identifier,
        "organizationName": organization_name,
        "description": "Crush.lu member pass",
        "authenticationToken": auth_token,
        "logoText": "Crush.lu",
        "generic": {
            "primaryFields": primary_fields,
            "headerFields": header_fields,
            "secondaryFields": secondary_fields,
            "auxiliaryFields": auxiliary_fields,
            "backFields": back_fields,
        },
        "backgroundColor": "rgb(155, 89, 182)",
        "foregroundColor": "rgb(255, 255, 255)",
        "labelColor": "rgb(255, 220, 230)",
        "barcode": {
            "format": "PKBarcodeFormatQR",
            "message": pass_data["referral_url"],
            "messageEncoding": "iso-8859-1",
            "altText": "Scan to join Crush.lu",
        },
        "barcodes": [
            {
                "format": "PKBarcodeFormatQR",
                "message": pass_data["referral_url"],
                "messageEncoding": "iso-8859-1",
                "altText": "Scan to join Crush.lu",
            }
        ],
    }

    if web_service_url:
        payload["webServiceURL"] = web_service_url

    return payload


def _build_pkpass(pass_payload, files=None):
    """
    Build a .pkpass ZIP file from a pass payload and optional extra files.

    Args:
        pass_payload: dict — the pass.json content
        files: dict of {filename: bytes} — extra files to include (icon.png, etc.)

    Returns:
        bytes: The .pkpass file contents
    """
    if files is None:
        files = {}

    # Add default icon if not provided
    if "icon.png" not in files:
        files["icon.png"] = base64.b64decode(ICON_PNG_BASE64)

    # Serialize pass.json
    pass_json_bytes = json.dumps(
        pass_payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")

    # Build manifest (SHA1 hashes of all files)
    manifest = {}
    manifest["pass.json"] = hashlib.sha1(
        pass_json_bytes, usedforsecurity=False
    ).hexdigest()
    for filename, content in files.items():
        manifest[filename] = hashlib.sha1(
            content, usedforsecurity=False
        ).hexdigest()

    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")

    # Sign manifest
    signature_bytes = _sign_manifest(manifest_bytes)

    # Build ZIP
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as zf:
        zf.writestr("pass.json", pass_json_bytes)
        zf.writestr("manifest.json", manifest_bytes)
        zf.writestr("signature", signature_bytes)
        for filename, content in files.items():
            zf.writestr(filename, content)

    return buffer.getvalue()


def build_apple_pass(profile, request=None):
    """
    Build a complete Apple Wallet .pkpass file for the given profile.

    Args:
        profile: CrushProfile instance
        request: Optional HttpRequest for building absolute URLs

    Returns:
        bytes: The .pkpass file contents
    """
    serial_number, auth_token = _ensure_pass_identifiers(profile)
    pass_payload = _build_pass_payload(
        profile, serial_number, auth_token, request=request
    )
    return _build_pkpass(pass_payload)


def provide_pass_for_serial(
    pass_type_identifier, serial_number, web_service_url=None, authentication_token=None
):
    """
    PassKit web service provider — rebuilds a member pass for a given serial.

    Called by passkit_service.get_latest_pass() when Apple Wallet requests
    an updated pass after an APNS push notification.
    """
    from ..models import CrushProfile

    profile = CrushProfile.objects.filter(apple_pass_serial=serial_number).first()
    if not profile:
        return None
    return build_apple_pass(profile)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest.exe crush_lu/tests/test_apple_wallet.py::TestLoadCertBytes -v`

Expected: PASS (both tests)

- [ ] **Step 5: Write test for `_sign_manifest`**

Add to `crush_lu/tests/test_apple_wallet.py`:

```python
@override_settings(**APPLE_WALLET_SETTINGS)
class TestSignManifest:
    """Test PKCS#7 signing of manifest bytes."""

    def test_produces_der_signature(self):
        from crush_lu.wallet.apple_pass import _sign_manifest

        manifest = b'{"pass.json":"abc123"}'
        signature = _sign_manifest(manifest)

        # DER-encoded PKCS#7 starts with ASN.1 SEQUENCE tag (0x30)
        assert isinstance(signature, bytes)
        assert len(signature) > 100
        assert signature[0] == 0x30
```

- [ ] **Step 6: Run test**

Run: `.venv/Scripts/pytest.exe crush_lu/tests/test_apple_wallet.py::TestSignManifest -v`

Expected: PASS

- [ ] **Step 7: Write test for `build_apple_pass`**

Add to `crush_lu/tests/test_apple_wallet.py`:

```python
@override_settings(**APPLE_WALLET_SETTINGS)
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
        import hashlib

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
```

- [ ] **Step 8: Run tests**

Run: `.venv/Scripts/pytest.exe crush_lu/tests/test_apple_wallet.py::TestBuildApplePass -v`

Expected: PASS (all 4 tests)

- [ ] **Step 9: Write test for `provide_pass_for_serial`**

Add to `crush_lu/tests/test_apple_wallet.py`:

```python
@override_settings(**APPLE_WALLET_SETTINGS)
class TestProvidePassForSerial:
    """Test PassKit web service provider callback."""

    def test_returns_pkpass_for_valid_serial(self, test_user_with_profile):
        from crush_lu.wallet.apple_pass import build_apple_pass, provide_pass_for_serial

        user, profile = test_user_with_profile
        build_apple_pass(profile)  # Assigns serial
        profile.refresh_from_db()

        result = provide_pass_for_serial("pass.lu.crush", profile.apple_pass_serial)
        assert result is not None
        assert isinstance(result, bytes)

    def test_returns_none_for_unknown_serial(self):
        from crush_lu.wallet.apple_pass import provide_pass_for_serial

        result = provide_pass_for_serial("pass.lu.crush", "nonexistent")
        assert result is None
```

- [ ] **Step 10: Run tests**

Run: `.venv/Scripts/pytest.exe crush_lu/tests/test_apple_wallet.py::TestProvidePassForSerial -v`

Expected: PASS

- [ ] **Step 11: Commit**

```bash
git add crush_lu/wallet/apple_pass.py crush_lu/tests/test_apple_wallet.py
git commit -m "feat(wallet): replace OpenSSL signing with pure Python cryptography

Replaces subprocess-based OpenSSL smime signing with cryptography
library PKCS#7. Adds _load_cert_bytes() supporting both base64 env
vars (Azure) and file paths (local dev). Extracts _build_pkpass()
as reusable ZIP builder. Adds provide_pass_for_serial() for PassKit
web service pass refresh."
```

---

### Task 3: Model — Add `apple_wallet_ticket_serial` field

**Files:**
- Modify: `crush_lu/models/events.py:534-540`

- [ ] **Step 1: Add field to EventRegistration**

In `crush_lu/models/events.py`, after the `google_wallet_ticket_object_id` field (line 540), add:

```python
    # Apple Wallet Event Ticket
    apple_wallet_ticket_serial = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=_("Apple Wallet event ticket serial number"),
    )
```

- [ ] **Step 2: Generate migration**

Run: `.venv/Scripts/python.exe manage.py makemigrations crush_lu --name add_apple_wallet_ticket_serial`

Expected: `Migrations for 'crush_lu': crush_lu/migrations/XXXX_add_apple_wallet_ticket_serial.py`

- [ ] **Step 3: Apply migration**

Run: `.venv/Scripts/python.exe manage.py migrate`

Expected: `Applying crush_lu.XXXX_add_apple_wallet_ticket_serial... OK`

- [ ] **Step 4: Commit**

```bash
git add crush_lu/models/events.py crush_lu/migrations/*_add_apple_wallet_ticket_serial.py
git commit -m "feat(wallet): add apple_wallet_ticket_serial to EventRegistration"
```

---

### Task 4: Apple Event Ticket builder

**Files:**
- Create: `crush_lu/wallet/apple_event_ticket.py`
- Modify: `crush_lu/wallet/__init__.py`
- Test: `crush_lu/tests/test_apple_wallet.py`

- [ ] **Step 1: Write tests for event ticket builder**

Add to `crush_lu/tests/test_apple_wallet.py`:

```python
@override_settings(**APPLE_WALLET_SETTINGS)
class TestBuildAppleEventTicket:
    """Test Apple Wallet EventTicket .pkpass generation."""

    def test_returns_valid_zip(self, event_with_registrations):
        from crush_lu.wallet.apple_event_ticket import build_apple_event_ticket

        registration = event_with_registrations
        pkpass_bytes = build_apple_event_ticket(registration)

        assert isinstance(pkpass_bytes, bytes)
        zf = zipfile.ZipFile(BytesIO(pkpass_bytes))
        names = zf.namelist()
        assert "pass.json" in names
        assert "manifest.json" in names
        assert "signature" in names

    def test_pass_uses_event_ticket_style(self, event_with_registrations):
        from crush_lu.wallet.apple_event_ticket import build_apple_event_ticket

        registration = event_with_registrations
        pkpass_bytes = build_apple_event_ticket(registration)

        zf = zipfile.ZipFile(BytesIO(pkpass_bytes))
        pass_json = json.loads(zf.read("pass.json"))

        assert "eventTicket" in pass_json
        assert "generic" not in pass_json
        assert pass_json["passTypeIdentifier"] == "pass.lu.crush"

    def test_pass_contains_event_details(self, event_with_registrations):
        from crush_lu.wallet.apple_event_ticket import build_apple_event_ticket

        registration = event_with_registrations
        pkpass_bytes = build_apple_event_ticket(registration)

        zf = zipfile.ZipFile(BytesIO(pkpass_bytes))
        pass_json = json.loads(zf.read("pass.json"))

        event_ticket = pass_json["eventTicket"]
        primary_keys = [f["key"] for f in event_ticket["primaryFields"]]
        assert "event_name" in primary_keys

    def test_pass_has_checkin_qr_code(self, event_with_registrations):
        from crush_lu.wallet.apple_event_ticket import build_apple_event_ticket

        registration = event_with_registrations
        pkpass_bytes = build_apple_event_ticket(registration)

        zf = zipfile.ZipFile(BytesIO(pkpass_bytes))
        pass_json = json.loads(zf.read("pass.json"))

        assert pass_json["barcode"]["format"] == "PKBarcodeFormatQR"
        assert "/api/events/checkin/" in pass_json["barcode"]["message"]

    def test_assigns_serial_number(self, event_with_registrations):
        from crush_lu.wallet.apple_event_ticket import build_apple_event_ticket

        registration = event_with_registrations
        assert registration.apple_wallet_ticket_serial == ""

        build_apple_event_ticket(registration)
        registration.refresh_from_db()

        assert registration.apple_wallet_ticket_serial != ""
        assert registration.apple_wallet_ticket_serial.startswith("evt-")
```

Also update the `event_with_registrations` fixture usage. The existing conftest fixture returns just the registration object. Check and adapt — the fixture at `conftest.py:307` creates and returns a registration. We need to use it:

```python
# At the top of test file, add this fixture adapter if needed:
@pytest.fixture
def event_with_registrations(event_with_registrations):
    """Adapt conftest fixture — returns the EventRegistration instance."""
    return event_with_registrations
```

Actually, looking at the conftest, `event_with_registrations` already returns the registration. We're good.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe crush_lu/tests/test_apple_wallet.py::TestBuildAppleEventTicket -v`

Expected: FAIL — `crush_lu.wallet.apple_event_ticket` does not exist.

- [ ] **Step 3: Create `apple_event_ticket.py`**

Create `crush_lu/wallet/apple_event_ticket.py`:

```python
"""
Apple Wallet EventTicket .pkpass builder.

Builds .pkpass files with eventTicket style for event registrations.
Each ticket contains a QR code with the signed check-in URL, event
details, and Crush.lu branding.

Reuses signing infrastructure from apple_pass.py.
"""

import secrets

from django.conf import settings

from .apple_pass import _build_pkpass, _ensure_pass_identifiers, _require_setting


def _ensure_event_ticket_serial(registration):
    """
    Ensure an EventRegistration has an Apple Wallet ticket serial number.

    Format: evt-{event_id}-reg-{reg_id}-{hex8}
    """
    if registration.apple_wallet_ticket_serial:
        return registration.apple_wallet_ticket_serial

    suffix = secrets.token_hex(8)
    serial = f"evt-{registration.event_id}-reg-{registration.id}-{suffix}"
    registration.apple_wallet_ticket_serial = serial
    registration.save(update_fields=["apple_wallet_ticket_serial"])
    return serial


def _build_checkin_url(registration, request=None):
    """
    Build the signed check-in URL for a registration.

    Reuses the same token generation as the web ticket page and Google Wallet.
    """
    from crush_lu.views_ticket import _generate_checkin_token

    token = _generate_checkin_token(registration)

    base_url = "https://crush.lu"
    if request:
        base_url = f"{request.scheme}://{request.get_host()}"

    return f"{base_url}/api/events/checkin/{registration.id}/{token}/"


def build_apple_event_ticket(registration, request=None):
    """
    Build a .pkpass EventTicket for an event registration.

    Args:
        registration: EventRegistration instance (with event and user loaded)
        request: Optional HttpRequest for building absolute URLs

    Returns:
        bytes: .pkpass file contents
    """
    pass_type_identifier = _require_setting("WALLET_APPLE_PASS_TYPE_IDENTIFIER")
    team_identifier = _require_setting("WALLET_APPLE_TEAM_IDENTIFIER")
    organization_name = _require_setting("WALLET_APPLE_ORGANIZATION_NAME")
    web_service_url = getattr(settings, "WALLET_APPLE_WEB_SERVICE_URL", "")

    event = registration.event
    serial_number = _ensure_event_ticket_serial(registration)
    checkin_url = _build_checkin_url(registration, request)

    # Get display name (privacy-aware)
    try:
        profile = registration.user.crushprofile
        display_name = profile.display_name
        # Reuse profile auth token for PassKit web service
        _, auth_token = _ensure_pass_identifiers(profile)
    except Exception:
        display_name = registration.user.first_name or registration.user.username
        auth_token = secrets.token_hex(16)

    # Format date/time
    event_date = event.date_time.strftime("%a, %b %d, %Y")
    event_time = event.date_time.strftime("%I:%M %p")

    payload = {
        "formatVersion": 1,
        "passTypeIdentifier": pass_type_identifier,
        "serialNumber": serial_number,
        "teamIdentifier": team_identifier,
        "organizationName": organization_name,
        "description": f"Crush.lu Event: {event.title}",
        "authenticationToken": auth_token,
        "logoText": "Crush.lu",
        "eventTicket": {
            "primaryFields": [
                {
                    "key": "event_name",
                    "label": "Event",
                    "value": event.title,
                }
            ],
            "secondaryFields": [
                {
                    "key": "date",
                    "label": "Date",
                    "value": event_date,
                },
                {
                    "key": "time",
                    "label": "Time",
                    "value": event_time,
                },
            ],
            "auxiliaryFields": [
                {
                    "key": "location",
                    "label": "Location",
                    "value": event.location,
                },
                {
                    "key": "attendee",
                    "label": "Attendee",
                    "value": display_name,
                },
            ],
            "backFields": [
                {
                    "key": "address",
                    "label": "Address",
                    "value": event.address,
                },
                {
                    "key": "event_type",
                    "label": "Type",
                    "value": event.get_event_type_display(),
                },
                {
                    "key": "ticket_info",
                    "label": "Check-in",
                    "value": "Show the QR code to the coach at the event entrance.",
                },
            ],
        },
        "backgroundColor": "rgb(155, 89, 182)",
        "foregroundColor": "rgb(255, 255, 255)",
        "labelColor": "rgb(255, 220, 230)",
        "barcode": {
            "format": "PKBarcodeFormatQR",
            "message": checkin_url,
            "messageEncoding": "iso-8859-1",
            "altText": "Scan at entrance",
        },
        "barcodes": [
            {
                "format": "PKBarcodeFormatQR",
                "message": checkin_url,
                "messageEncoding": "iso-8859-1",
                "altText": "Scan at entrance",
            }
        ],
    }

    if web_service_url:
        payload["webServiceURL"] = web_service_url

    return _build_pkpass(payload)
```

- [ ] **Step 4: Update `__init__.py`**

In `crush_lu/wallet/__init__.py`, add the new export:

```python
"""Wallet utilities for Apple PassKit and Google Wallet."""

from .apple_pass import build_apple_pass
from .apple_event_ticket import build_apple_event_ticket
from .google_wallet import build_google_wallet_jwt
from .google_event_ticket import build_google_event_ticket_jwt

__all__ = [
    "build_apple_pass",
    "build_apple_event_ticket",
    "build_google_wallet_jwt",
    "build_google_event_ticket_jwt",
]
```

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/pytest.exe crush_lu/tests/test_apple_wallet.py::TestBuildAppleEventTicket -v`

Expected: PASS (all 5 tests)

- [ ] **Step 6: Commit**

```bash
git add crush_lu/wallet/apple_event_ticket.py crush_lu/wallet/__init__.py crush_lu/tests/test_apple_wallet.py
git commit -m "feat(wallet): add Apple Wallet EventTicket builder

Creates .pkpass files with eventTicket style for event registrations.
Includes event details, check-in QR code, attendee name, and
Crush.lu branding. Reuses signing infrastructure from apple_pass.py."
```

---

### Task 5: Views — Activate member pass + add event ticket view

**Files:**
- Modify: `crush_lu/views_wallet.py`
- Test: `crush_lu/tests/test_apple_wallet.py`

- [ ] **Step 1: Write view tests**

Add to `crush_lu/tests/test_apple_wallet.py`:

```python
from django.test import Client


@override_settings(**APPLE_WALLET_SETTINGS)
class TestAppleWalletPassView:
    """Test the member pass download view."""

    def test_returns_pkpass_for_authenticated_user(self, test_user_with_profile):
        user, profile = test_user_with_profile
        client = Client()
        client.force_login(user)

        response = client.get("/wallet/apple/pass/")

        assert response.status_code == 200
        assert response["Content-Type"] == "application/vnd.apple.pkpass"
        assert "crushlu.pkpass" in response["Content-Disposition"]

    def test_requires_authentication(self):
        client = Client()
        response = client.get("/wallet/apple/pass/")

        assert response.status_code == 302  # Redirect to login

    def test_returns_503_when_not_configured(self, test_user_with_profile):
        user, profile = test_user_with_profile
        client = Client()
        client.force_login(user)

        with override_settings(WALLET_APPLE_PASS_TYPE_IDENTIFIER=""):
            response = client.get("/wallet/apple/pass/")

        assert response.status_code == 503


@override_settings(**APPLE_WALLET_SETTINGS)
class TestAppleEventTicketView:
    """Test the event ticket .pkpass download view."""

    def test_returns_pkpass_for_confirmed_registration(self, event_with_registrations):
        registration = event_with_registrations
        client = Client()
        client.force_login(registration.user)

        response = client.get(
            f"/wallet/apple/event-ticket/{registration.id}/pass/"
        )

        assert response.status_code == 200
        assert response["Content-Type"] == "application/vnd.apple.pkpass"

    def test_rejects_other_users_registration(self, event_with_registrations, db):
        from django.contrib.auth.models import User

        registration = event_with_registrations
        other_user = User.objects.create_user(
            username="other@example.com", password="pass123"
        )
        client = Client()
        client.force_login(other_user)

        response = client.get(
            f"/wallet/apple/event-ticket/{registration.id}/pass/"
        )

        assert response.status_code == 404

    def test_rejects_cancelled_registration(self, event_with_registrations):
        registration = event_with_registrations
        registration.status = "cancelled"
        registration.save()

        client = Client()
        client.force_login(registration.user)

        response = client.get(
            f"/wallet/apple/event-ticket/{registration.id}/pass/"
        )

        assert response.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe crush_lu/tests/test_apple_wallet.py::TestAppleWalletPassView crush_lu/tests/test_apple_wallet.py::TestAppleEventTicketView -v`

Expected: FAIL — views not yet updated.

- [ ] **Step 3: Update `_is_apple_wallet_configured`**

In `crush_lu/views_wallet.py`, replace `_is_apple_wallet_configured` (lines 14-24) to support both base64 and path modes:

```python
def _is_apple_wallet_configured():
    """Check if Apple Wallet settings are configured."""
    required_settings = [
        "WALLET_APPLE_PASS_TYPE_IDENTIFIER",
        "WALLET_APPLE_TEAM_IDENTIFIER",
        "WALLET_APPLE_ORGANIZATION_NAME",
    ]
    if not all(getattr(settings, s, None) for s in required_settings):
        return False
    # Need either base64 certs OR file path certs
    has_base64 = all(
        getattr(settings, s, None)
        for s in [
            "WALLET_APPLE_CERT_BASE64",
            "WALLET_APPLE_KEY_BASE64",
            "WALLET_APPLE_WWDR_CERT_BASE64",
        ]
    )
    has_paths = all(
        getattr(settings, s, None)
        for s in [
            "WALLET_APPLE_CERT_PATH",
            "WALLET_APPLE_KEY_PATH",
            "WALLET_APPLE_WWDR_CERT_PATH",
        ]
    )
    return has_base64 or has_paths
```

- [ ] **Step 4: Activate the member pass view**

Replace the `apple_wallet_pass` function (lines 42-96) with:

```python
@login_required
@require_GET
def apple_wallet_pass(request):
    """Generate and return an Apple Wallet .pkpass file for the current user."""
    if not _is_apple_wallet_configured():
        logger.warning("Apple Wallet pass requested but not configured")
        return JsonResponse(
            {"error": "Apple Wallet is not configured on this server."},
            status=503,
        )

    try:
        from .wallet import build_apple_pass

        profile, _ = CrushProfile.objects.get_or_create(user=request.user)
        pkpass_data = build_apple_pass(profile, request=request)
        response = HttpResponse(pkpass_data, content_type="application/vnd.apple.pkpass")
        response["Content-Disposition"] = "attachment; filename=crushlu.pkpass"
        return response
    except ImproperlyConfigured as e:
        logger.error("Apple Wallet configuration error: %s", e)
        return JsonResponse(
            {"error": "Apple Wallet is not properly configured."},
            status=503,
        )
    except Exception as e:
        logger.exception("Error generating Apple Wallet pass: %s", e)
        return JsonResponse(
            {"error": "Failed to generate Apple Wallet pass."},
            status=500,
        )
```

- [ ] **Step 5: Add the event ticket view**

Add at the end of `crush_lu/views_wallet.py`:

```python
@login_required
@require_GET
def apple_event_ticket_pass(request, registration_id):
    """Generate and return an Apple Wallet .pkpass EventTicket."""
    try:
        registration = EventRegistration.objects.select_related(
            "event", "user", "user__crushprofile"
        ).get(id=registration_id, user=request.user)
    except EventRegistration.DoesNotExist:
        return JsonResponse({"error": "Registration not found."}, status=404)

    if registration.status not in ("confirmed", "attended"):
        return JsonResponse(
            {"error": "Only confirmed registrations can be added to wallet."},
            status=400,
        )

    if not _is_apple_wallet_configured():
        return JsonResponse(
            {"error": "Apple Wallet is not configured on this server."},
            status=503,
        )

    try:
        from .wallet.apple_event_ticket import build_apple_event_ticket

        pkpass_data = build_apple_event_ticket(registration, request=request)
        response = HttpResponse(pkpass_data, content_type="application/vnd.apple.pkpass")
        response["Content-Disposition"] = (
            f'attachment; filename=crush-event-{registration.event_id}.pkpass'
        )
        return response
    except ImproperlyConfigured as e:
        logger.error("Apple Wallet event ticket config error: %s", e)
        return JsonResponse(
            {"error": "Apple Wallet is not properly configured."},
            status=503,
        )
    except Exception as e:
        logger.exception("Error generating Apple event ticket: %s", e)
        return JsonResponse(
            {"error": "Failed to generate Apple Wallet event ticket."},
            status=500,
        )
```

- [ ] **Step 6: Add URL**

In `azureproject/urls_crush.py`, after the existing `wallet/google/event-ticket/` line (around line 197), add:

```python
    path('wallet/apple/event-ticket/<int:registration_id>/pass/', views_wallet.apple_event_ticket_pass, name='apple_event_ticket_pass'),
```

- [ ] **Step 7: Run tests**

Run: `.venv/Scripts/pytest.exe crush_lu/tests/test_apple_wallet.py::TestAppleWalletPassView crush_lu/tests/test_apple_wallet.py::TestAppleEventTicketView -v`

Expected: PASS (all 6 tests)

- [ ] **Step 8: Commit**

```bash
git add crush_lu/views_wallet.py azureproject/urls_crush.py crush_lu/tests/test_apple_wallet.py
git commit -m "feat(wallet): activate Apple Wallet member pass and add event ticket view

Removes 'Coming Soon' response, activates real .pkpass generation.
Adds apple_event_ticket_pass view for event tickets. Updates config
check to support both base64 and file path certificate modes."
```

---

### Task 6: Template — Add "Add to Apple Wallet" button

**Files:**
- Modify: `crush_lu/views_ticket.py:78-90`
- Modify: `crush_lu/templates/crush_lu/event_ticket.html:81-96`

- [ ] **Step 1: Update `event_ticket` view context**

In `crush_lu/views_ticket.py`, replace the context section (lines 78-90):

```python
    # Check if Google Wallet event tickets are enabled
    wallet_enabled = getattr(settings, "WALLET_GOOGLE_EVENT_TICKET_ENABLED", True)

    # Check if Apple Wallet is configured
    from .views_wallet import _is_apple_wallet_configured

    apple_wallet_enabled = _is_apple_wallet_configured()
    apple_wallet_url = (
        f"/wallet/apple/event-ticket/{registration.id}/pass/"
        if apple_wallet_enabled
        else ""
    )

    context = {
        "event": event,
        "registration": registration,
        "checkin_url": checkin_url,
        "display_name": display_name,
        "already_checked_in": already_checked_in,
        "wallet_enabled": wallet_enabled,
        "apple_wallet_enabled": apple_wallet_enabled,
        "apple_wallet_url": apple_wallet_url,
    }

    return render(request, "crush_lu/event_ticket.html", context)
```

- [ ] **Step 2: Update template**

In `crush_lu/templates/crush_lu/event_ticket.html`, replace the wallet button section (lines 81-96) with:

```html
            {% if apple_wallet_enabled or wallet_enabled %}
            {# Wallet buttons #}
            <div class="px-6 pb-6 text-center space-y-3">
                {% if apple_wallet_enabled %}
                <div>
                    <a href="{{ apple_wallet_url }}" class="inline-block p-2 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-gray-500">
                        <img src="{% static 'crush_lu/img/wallet/apple_wallet_badge.svg' %}" alt="{% trans 'Add to Apple Wallet' %}" class="h-12 w-auto">
                    </a>
                </div>
                {% endif %}

                {% if wallet_enabled %}
                <div x-data="eventTicketButton" data-registration-id="{{ registration.id }}">
                    <button type="button" @click="saveToWallet" x-bind:disabled="isLoading" class="google-wallet-btn p-2 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-gray-500 disabled:opacity-50 disabled:cursor-not-allowed">
                        {% get_current_language as LANGUAGE_CODE %}
                        {% if LANGUAGE_CODE == 'de' %}
                        <img src="{% static 'crush_lu/img/wallet/de_add_to_google_wallet.svg' %}" alt="{% trans 'Add to Google Wallet' %}" class="h-12 w-auto">
                        {% elif LANGUAGE_CODE == 'fr' %}
                        <img src="{% static 'crush_lu/img/wallet/fr_add_to_google_wallet.svg' %}" alt="{% trans 'Add to Google Wallet' %}" class="h-12 w-auto">
                        {% else %}
                        <img src="{% static 'crush_lu/img/wallet/en_add_to_google_wallet.svg' %}" alt="{% trans 'Add to Google Wallet' %}" class="h-12 w-auto">
                        {% endif %}
                    </button>
                    <p x-show="hasError" x-text="errorMessage" class="text-sm text-red-500 mt-2" style="display: none;"></p>
                </div>
                {% endif %}
            </div>
            {% endif %}
```

- [ ] **Step 3: Create Apple Wallet badge SVG**

Create `crush_lu/static/crush_lu/img/wallet/apple_wallet_badge.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" width="187" height="48" viewBox="0 0 187 48">
  <rect width="187" height="48" rx="8" fill="#000"/>
  <text x="93.5" y="20" text-anchor="middle" fill="#fff" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="9" font-weight="400">Add to</text>
  <text x="93.5" y="35" text-anchor="middle" fill="#fff" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="14" font-weight="600">Apple Wallet</text>
  <path d="M37.8 12.5c-.1-1.3.5-2.6 1.3-3.5.8-1 2.1-1.6 3.3-1.7.1 1.4-.5 2.7-1.3 3.6-.8 1-2 1.6-3.3 1.6zm3.3 1.8c-1.8-.1-3.4 1-4.2 1s-2.2-1-3.6-1c-1.9 0-3.6 1.1-4.5 2.7-1.9 3.3-.5 8.3 1.4 11 .9 1.3 2 2.8 3.5 2.8 1.4-.1 1.9-.9 3.6-.9s2.1.9 3.6.9 2.4-1.3 3.3-2.7c.7-1 1.2-2 1.5-3.1-1.7-.7-2.8-2.3-2.8-4.1 0-1.6.8-3.1 2.2-4-.8-1.2-2.1-2-3.5-2.1l-.5-.1z" fill="#fff"/>
</svg>
```

Note: For production, you should use Apple's official "Add to Apple Wallet" badge. Download the official assets from Apple's Human Interface Guidelines and place them at the same path. The SVG above is a placeholder that follows Apple's style.

- [ ] **Step 4: Verify template renders**

Run: `.venv/Scripts/python.exe manage.py check`

Expected: `System check identified no issues.`

- [ ] **Step 5: Commit**

```bash
git add crush_lu/views_ticket.py crush_lu/templates/crush_lu/event_ticket.html crush_lu/static/crush_lu/img/wallet/apple_wallet_badge.svg
git commit -m "feat(wallet): add 'Add to Apple Wallet' button to event ticket page

Shows Apple Wallet button alongside existing Google Wallet button.
Apple button is a direct link to .pkpass download (iOS handles natively).
Both buttons visible on desktop, contextual on mobile."
```

---

### Task 7: Run full test suite + final verification

**Files:** None (verification only)

- [ ] **Step 1: Run all Apple Wallet tests**

Run: `.venv/Scripts/pytest.exe crush_lu/tests/test_apple_wallet.py -v`

Expected: All tests PASS (approximately 14 tests)

- [ ] **Step 2: Run full project tests to check for regressions**

Run: `.venv/Scripts/pytest.exe crush_lu/tests/ -v --timeout=120`

Expected: No regressions. All existing tests still pass.

- [ ] **Step 3: Verify server starts and key URLs respond**

Run: `.venv/Scripts/python.exe manage.py runserver`

Test these URLs manually:
- `http://localhost:8000/wallet/apple/pass/` — should prompt login, then return .pkpass
- `http://localhost:8000/wallet/apple/event-ticket/1/pass/` — should return 404 (no registration) or .pkpass

- [ ] **Step 4: Format code**

Run: `.venv/Scripts/black.exe . && .venv/Scripts/ruff.exe check . --fix`

Expected: No errors.

- [ ] **Step 5: Final commit if formatting changed anything**

```bash
git add -u
git commit -m "style: format Apple Wallet code"
```
