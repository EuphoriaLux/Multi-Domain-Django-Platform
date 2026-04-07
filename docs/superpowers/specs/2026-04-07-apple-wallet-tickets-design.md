# Apple Wallet Integration Design Spec

**Date:** 2026-04-07
**Status:** Draft
**Scope:** Complete Apple Wallet member passes + event tickets for Crush.lu

---

## 1. Overview

Activate and complete Apple Wallet integration for Crush.lu, delivering two pass types:

1. **Member Pass** (generic style) -- profile card with name, tier, points, referral QR, next event
2. **Event Ticket** (eventTicket style) -- per-registration ticket with event details, check-in QR

Google Wallet equivalents already exist and are operational. This spec brings Apple Wallet to parity.

## 2. What Already Exists

| Component | File | Status |
|-----------|------|--------|
| Member pass builder | `crush_lu/wallet/apple_pass.py` | Built, uses OpenSSL subprocess |
| View (member pass) | `crush_lu/views_wallet.py:44` | Returns "Coming Soon" |
| PassKit web service | `crush_lu/wallet/passkit_service.py` | Complete |
| APNS push | `crush_lu/wallet/passkit_apns.py` | Complete |
| Device registration model | `crush_lu/models/passkit.py` | Complete |
| Profile wallet fields | `crush_lu/models/profiles.py:541-559` | Complete |
| Wallet pass data builder | `crush_lu/wallet_pass.py` | Complete |
| Settings | `azureproject/settings.py:326-334` | File path vars only |
| Wallet URLs | `azureproject/urls_crush.py` | Member pass + PassKit endpoints |
| Signal handlers | `crush_lu/signals.py` | Profile/registration change triggers |
| Admin | `crush_lu/admin/wallet.py`, `crush_lu/admin/passkit.py` | Complete |

## 3. Certificates

All certificates are generated and stored at `certs/apple/`:

| File | Purpose |
|------|---------|
| `crush-pass-cert.pem` | Pass Type ID certificate (pass.lu.crush) |
| `crush-pass-key.pem` | RSA private key (no passphrase) |
| `wwdr-g4.pem` | Apple WWDR G4 intermediate certificate |

- **Pass Type ID:** `pass.lu.crush`
- **Team ID:** `C5XDPB2G33`
- **Certificate chain:** Pass cert -> WWDR G4 -> Apple Root CA

### Production (Azure)

Certificates stored as base64-encoded environment variables:

| Variable | Purpose |
|----------|---------|
| `WALLET_APPLE_CERT_BASE64` | Base64 of crush-pass-cert.pem |
| `WALLET_APPLE_KEY_BASE64` | Base64 of crush-pass-key.pem |
| `WALLET_APPLE_WWDR_CERT_BASE64` | Base64 of wwdr-g4.pem |

Code checks `_BASE64` vars first (decodes to bytes at runtime), falls back to `_PATH` vars for local dev.

### Local Development

File paths in `.env`:

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

## 4. Architecture Changes

### 4A. Replace OpenSSL signing with pure Python

**File:** `crush_lu/wallet/apple_pass.py`

Replace `_sign_manifest()` (subprocess-based OpenSSL) with `cryptography` library PKCS#7 signing:

```python
from cryptography.hazmat.primitives.serialization import pkcs7, Encoding
from cryptography.x509 import load_pem_x509_certificate
from cryptography.hazmat.primitives.serialization import load_pem_private_key

def _sign_manifest_python(manifest_bytes):
    cert_pem, key_pem, wwdr_pem = _load_cert_bytes()
    cert = load_pem_x509_certificate(cert_pem)
    key = load_pem_private_key(key_pem, password=key_password or None)
    wwdr_cert = load_pem_x509_certificate(wwdr_pem)

    return (
        pkcs7.PKCS7SignatureBuilder()
        .set_data(manifest_bytes)
        .add_signer(cert, key, hashes.SHA256())
        .add_certificate(wwdr_cert)
        .sign(Encoding.DER, [pkcs7.PKCS7Options.DetachedSignature])
    )
```

New helper `_load_cert_bytes()` checks for `_BASE64` env vars first, falls back to reading `_PATH` files. This eliminates the OpenSSL subprocess dependency entirely.

The `build_apple_pass()` function signature and return type (bytes) remain unchanged.

### 4B. Activate member pass view

**File:** `crush_lu/views_wallet.py`

- Remove the "Coming Soon" early return in `apple_wallet_pass()`
- Uncomment the existing implementation (lines 69-96)
- The implementation already handles configuration checks, error handling, and content-type

### 4C. Build Apple Wallet event ticket

**New file:** `crush_lu/wallet/apple_event_ticket.py`

Creates `.pkpass` files with `eventTicket` pass style for event registrations.

```python
def build_apple_event_ticket(registration, request=None):
    """
    Build a .pkpass EventTicket for an event registration.

    Args:
        registration: EventRegistration (with event and user loaded)
        request: Optional HttpRequest for absolute URLs

    Returns:
        bytes: .pkpass file contents
    """
```

**Pass layout (eventTicket style):**

| Field Type | Key | Label | Value |
|------------|-----|-------|-------|
| Primary | `event_name` | Event | `event.title` |
| Secondary | `date` | Date | `event.date_time` (formatted) |
| Secondary | `time` | Time | `event.date_time` (time only) |
| Auxiliary | `location` | Location | `event.location` |
| Auxiliary | `attendee` | Attendee | `profile.display_name` |
| Back | `address` | Address | `event.address` |
| Back | `event_type` | Type | `event.get_event_type_display()` |
| Back | `ticket_info` | Info | "Show QR code at entrance" |
| Barcode | QR | - | Signed check-in URL (reuses `_build_checkin_url` from google_event_ticket.py) |

**Branding:**
- `backgroundColor`: `rgb(155, 89, 182)` (crush-purple)
- `foregroundColor`: `rgb(255, 255, 255)`
- `labelColor`: `rgb(255, 220, 230)`
- `logoText`: `Crush.lu`

**Serial number format:** `evt-{event_id}-reg-{registration_id}-{hex8}`

Stored on `EventRegistration.apple_wallet_ticket_serial`.

Reuses the same signing infrastructure (`_sign_manifest_python`, `_load_cert_bytes`) from the updated `apple_pass.py`.

### 4D. New model field

**File:** `crush_lu/models/events.py` on `EventRegistration`

Add:
```python
apple_wallet_ticket_serial = models.CharField(max_length=64, blank=True, default="")
```

Parallel to existing `google_wallet_ticket_object_id`. Generated on first pass download, reused for updates.

Migration: standard `AddField`, no data migration needed.

### 4E. New view + URL

**File:** `crush_lu/views_wallet.py`

New view:
```python
@login_required
@require_GET
def apple_event_ticket_pass(request, registration_id):
    """Generate .pkpass EventTicket for an event registration."""
```

Validates:
- User owns the registration
- Status is confirmed or attended
- Apple Wallet is configured

Returns: `HttpResponse` with `content_type="application/vnd.apple.pkpass"` and `Content-Disposition: attachment; filename=crush-event-{event_id}.pkpass`

**File:** `azureproject/urls_crush.py`

Add (language-neutral, outside i18n_patterns):
```python
path("wallet/apple/event-ticket/<int:registration_id>/pass/",
     views_wallet.apple_event_ticket_pass,
     name="apple_event_ticket_pass"),
```

### 4F. Update event ticket template

**File:** `crush_lu/templates/crush_lu/event_ticket.html`

Add "Add to Apple Wallet" button alongside the existing Google Wallet button:

- Show Apple Wallet button when Apple Wallet is configured
- Use Apple's official "Add to Apple Wallet" badge (SVG, localized en/de/fr)
- The button is a direct link to the `.pkpass` download URL (iOS handles it natively)
- Platform detection: show Apple button prominently on iOS, Google button on Android, both on desktop

Template context additions (from `views_ticket.py`):
- `apple_wallet_enabled`: bool
- `apple_wallet_url`: URL to the `.pkpass` endpoint

### 4G. Pass icons

**Directory:** `crush_lu/static/crush_lu/img/wallet/apple/`

Required images for `.pkpass`:
- `icon.png` (29x29), `icon@2x.png` (58x58), `icon@3x.png` (87x87) -- app icon
- `logo.png` (160x50), `logo@2x.png` (320x100) -- shown on pass front

For now: generate from existing Crush.lu favicon/logo at `crush_lu/static/crush_lu/icons/`. The 1x1 transparent placeholder in `apple_pass.py` will be replaced.

These images are loaded from disk and included in the `.pkpass` zip. They can be stored as:
- Static files (loaded at build time)
- Base64 constants in Python (like the current placeholder, but with real images)

Recommendation: static files, loaded via `finders.find()` or absolute path.

### 4H. Wire up PassKit web service providers

**File:** `azureproject/settings.py`

Configure providers so `passkit_service.py:get_latest_pass` can serve updated passes:

```python
PASSKIT_PASS_PROVIDER = "crush_lu.wallet.apple_pass.provide_pass_for_serial"
```

New function in `apple_pass.py`:
```python
def provide_pass_for_serial(pass_type_identifier, serial_number, web_service_url=None, authentication_token=None):
    """PassKit web service provider -- rebuilds pass for a given serial."""
    from ..models import CrushProfile
    profile = CrushProfile.objects.filter(apple_pass_serial=serial_number).first()
    if not profile:
        return None
    return build_apple_pass(profile)
```

This enables Apple Wallet to pull fresh pass data when triggered by APNS push notifications (already implemented in signals).

### 4I. Settings additions

**File:** `azureproject/settings.py`

New env vars for base64 certificate storage:
```python
WALLET_APPLE_CERT_BASE64 = os.getenv("WALLET_APPLE_CERT_BASE64", "")
WALLET_APPLE_KEY_BASE64 = os.getenv("WALLET_APPLE_KEY_BASE64", "")
WALLET_APPLE_WWDR_CERT_BASE64 = os.getenv("WALLET_APPLE_WWDR_CERT_BASE64", "")
```

Existing `_PATH` settings remain for local development.

## 5. Data Flow

### Member Pass Download
```
User taps "Add to Apple Wallet" on dashboard
  -> GET /wallet/apple/pass/
  -> apple_wallet_pass() view
  -> build_apple_pass(profile)
    -> _ensure_pass_identifiers() -- assigns serial + auth token
    -> _build_pass_payload() -- JSON with profile data, referral QR
    -> _sign_manifest_python() -- PKCS#7 signing via cryptography
    -> ZIP into .pkpass
  -> Response: application/vnd.apple.pkpass
  -> iOS adds to Wallet
  -> Wallet registers device via POST /wallet/v1/devices/.../registrations/...
```

### Event Ticket Download
```
User taps "Add to Apple Wallet" on event ticket page
  -> GET /wallet/apple/event-ticket/{registration_id}/pass/
  -> apple_event_ticket_pass() view
    -> Validates ownership + status
  -> build_apple_event_ticket(registration)
    -> _ensure_event_ticket_serial() -- assigns serial
    -> _build_event_ticket_payload() -- JSON with event data, check-in QR
    -> Same signing + ZIP pipeline
  -> Response: application/vnd.apple.pkpass
  -> iOS adds to Wallet
```

### Pass Updates (already implemented)
```
Profile/registration changes (signals.py)
  -> trigger_wallet_pass_update_on_profile_change
  -> send_passkit_push_notifications(pass_type_id, serial)
  -> APNS pushes to all registered devices
  -> Wallet pulls GET /wallet/v1/passes/{type}/{serial}
  -> get_latest_pass() -> provide_pass_for_serial()
  -> Fresh .pkpass returned
```

## 6. Files to Create/Modify

| Action | File | Description |
|--------|------|-------------|
| Modify | `crush_lu/wallet/apple_pass.py` | Replace OpenSSL with cryptography, add `_load_cert_bytes()`, add `provide_pass_for_serial()` |
| Create | `crush_lu/wallet/apple_event_ticket.py` | EventTicket .pkpass builder |
| Modify | `crush_lu/views_wallet.py` | Activate member pass view, add event ticket view |
| Modify | `crush_lu/views_ticket.py` | Add Apple Wallet context to template |
| Modify | `crush_lu/models/events.py` | Add `apple_wallet_ticket_serial` field |
| Create | Migration | `AddField` for `apple_wallet_ticket_serial` |
| Modify | `crush_lu/templates/crush_lu/event_ticket.html` | Add Apple Wallet button |
| Modify | `azureproject/urls_crush.py` | Add event ticket URL |
| Modify | `azureproject/settings.py` | Add `_BASE64` env var settings |
| Add | `crush_lu/static/crush_lu/img/wallet/apple/` | Pass icon/logo images |
| Add | `crush_lu/static/crush_lu/img/wallet/apple_wallet_badge_*.svg` | "Add to Apple Wallet" badges (en/de/fr) |

## 7. Testing Strategy

### Unit Tests
- `_sign_manifest_python()` produces valid PKCS#7 DER signature
- `build_apple_pass()` returns valid ZIP with required files (pass.json, manifest.json, signature, icon.png)
- `build_apple_event_ticket()` returns valid ZIP with eventTicket fields
- `_load_cert_bytes()` handles both base64 and file path modes
- Pass JSON contains correct fields for member and event ticket styles
- `provide_pass_for_serial()` returns pass for valid serial, None for unknown

### Integration Tests
- `apple_wallet_pass` view returns 200 + correct content-type for authenticated user with profile
- `apple_event_ticket_pass` view validates ownership and registration status
- PassKit web service endpoints work end-to-end (register, get latest, unregister)
- View returns 503 when Apple Wallet not configured

### Manual Testing
- Download .pkpass on iOS device/simulator and verify it opens in Wallet
- Verify pass displays correctly (fields, colors, QR code)
- Scan QR code and verify check-in works
- Test pass update flow (change profile, verify pass refreshes)

## 8. Security Considerations

- Private key never logged or exposed in responses
- Auth tokens per-profile (not global) for PassKit web service
- Registration ownership validated before generating event tickets
- Base64 cert env vars treated as secrets in Azure
- `certs/` directory in `.gitignore`
- Check-in QR uses Django's Signer (same as existing web tickets and Google Wallet)

## 9. Out of Scope

- Apple Wallet order tracking (different from passes)
- Apple Wallet boarding pass or coupon styles
- Automatic pass creation on registration (users explicitly add to wallet)
- APNS configuration (separate Apple Key for push -- already scaffolded, will configure separately)
- Sharing passes between users
