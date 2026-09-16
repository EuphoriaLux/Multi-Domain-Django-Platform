"""Certificate-free regression tests for PKPass manifest hashes."""

import hashlib
import json
import zipfile
from io import BytesIO

import pytest


@pytest.mark.parametrize(
    ("style", "fields"),
    [
        pytest.param("generic", {"generic": {}}, id="member-pass"),
        pytest.param("eventTicket", {"eventTicket": {}}, id="event-ticket"),
    ],
)
def test_pkpass_manifest_uses_passkit_sha1_hashes(monkeypatch, style, fields):
    from crush_lu.wallet import apple_pass

    monkeypatch.setattr(apple_pass, "_load_brand_assets", dict)
    monkeypatch.setattr(apple_pass, "_sign_manifest", lambda _manifest: b"signature")

    payload = {
        "formatVersion": 1,
        "passTypeIdentifier": "pass.lu.crush",
        "serialNumber": f"test-{style}",
        "teamIdentifier": "C5XDPB2G33",
        "organizationName": "Crush.lu",
        "description": "Manifest regression test",
        **fields,
    }
    pkpass_bytes = apple_pass._build_pkpass(payload)

    with zipfile.ZipFile(BytesIO(pkpass_bytes)) as pkpass:
        manifest = json.loads(pkpass.read("manifest.json"))
        for filename, expected_hash in manifest.items():
            assert len(expected_hash) == 40
            actual_hash = hashlib.sha1(pkpass.read(filename)).hexdigest()  # nosec B324
            assert actual_hash == expected_hash, f"Hash mismatch for {filename}"


def test_generate_pass_thumbnail_with_photo():
    from unittest.mock import MagicMock
    from PIL import Image
    from crush_lu.wallet.apple_pass import generate_pass_thumbnail

    buf = BytesIO()
    Image.new("RGB", (300, 400), color=(220, 50, 80)).save(buf, format="JPEG")
    photo_bytes = buf.getvalue()

    mock_photo = MagicMock()
    mock_photo.read.return_value = photo_bytes
    mock_photo.seek.return_value = None

    mock_profile = MagicMock()
    mock_profile.pk = 42
    mock_profile.show_photo_on_wallet = True
    mock_profile.photo_1 = mock_photo

    thumbs = generate_pass_thumbnail(mock_profile)
    assert "thumbnail.png" in thumbs
    assert "thumbnail@2x.png" in thumbs

    # Verify dimensions
    with Image.open(BytesIO(thumbs["thumbnail.png"])) as img_1x:
        assert img_1x.size == (90, 90)
    with Image.open(BytesIO(thumbs["thumbnail@2x.png"])) as img_2x:
        assert img_2x.size == (180, 180)


def test_generate_pass_thumbnail_fallback_when_no_photo():
    from unittest.mock import MagicMock
    from PIL import Image
    from crush_lu.wallet.apple_pass import generate_pass_thumbnail

    mock_profile = MagicMock()
    mock_profile.pk = 99
    mock_profile.show_photo_on_wallet = True
    mock_profile.photo_1 = None

    thumbs = generate_pass_thumbnail(mock_profile)
    assert "thumbnail.png" in thumbs
    assert "thumbnail@2x.png" in thumbs

    with Image.open(BytesIO(thumbs["thumbnail.png"])) as img_1x:
        assert img_1x.size == (90, 90)
    with Image.open(BytesIO(thumbs["thumbnail@2x.png"])) as img_2x:
        assert img_2x.size == (180, 180)


def test_wallet_status_display_and_badges():
    from unittest.mock import MagicMock
    from crush_lu.wallet_pass import get_wallet_status_display, get_wallet_verification_badge

    # Premium member
    p_prem = MagicMock()
    p_prem.has_active_premium = True
    assert get_wallet_status_display(p_prem) == "✨ VIP"
    assert get_wallet_verification_badge(p_prem) == "✨ Premium Member"

    # LuxID verified
    p_luxid = MagicMock()
    p_luxid.has_active_premium = False
    p_luxid.verification_status = "verified"
    p_luxid.verification_method = "luxid"
    assert get_wallet_status_display(p_luxid) == "🛡️ LuxID"
    assert get_wallet_verification_badge(p_luxid) == "🛡️ LuxID Verified"

    # Gold tiered
    p_gold = MagicMock()
    p_gold.has_active_premium = False
    p_gold.verification_status = "incomplete"
    p_gold.membership_tier = "gold"
    assert get_wallet_status_display(p_gold) == "🥇 Gold"
    assert get_wallet_verification_badge(p_gold) == "Gold Member"


@pytest.mark.django_db
def test_build_pass_payload_personalization_structure(settings, test_user_with_profile):
    from crush_lu.wallet import apple_pass

    settings.WALLET_APPLE_PASS_TYPE_IDENTIFIER = "pass.lu.crush"
    settings.WALLET_APPLE_TEAM_IDENTIFIER = "C5XDPB2G33"
    settings.WALLET_APPLE_ORGANIZATION_NAME = "Crush.lu"
    settings.WALLET_APPLE_WEB_SERVICE_URL = "https://crush.lu/wallet"

    user, profile = test_user_with_profile
    profile.verification_status = "verified"
    profile.verification_method = "luxid"
    profile.location = "Esch-sur-Alzette"
    profile.referral_points = 250
    profile.save()

    payload = apple_pass._build_pass_payload(
        profile,
        serial_number="test-serial-77",
        auth_token="auth-tok-12345",
    )

    # Colors
    assert payload["backgroundColor"] == "rgb(109, 40, 217)"
    assert payload["foregroundColor"] == "rgb(255, 255, 255)"
    assert payload["labelColor"] == "rgb(233, 213, 255)"

    generic = payload["generic"]

    # Header & Primary
    assert generic["headerFields"][0]["key"] == "tier"
    assert generic["headerFields"][0]["value"] == "🛡️ LuxID"
    assert generic["primaryFields"][0]["key"] == "member"
    assert generic["primaryFields"][0]["value"] == profile.display_name

    # Secondary
    sec_keys = [f["key"] for f in generic["secondaryFields"]]
    assert "member_id" in sec_keys
    assert "community" in sec_keys

    # Auxiliary
    aux_keys = [f["key"] for f in generic["auxiliaryFields"]]
    assert "member_since" in aux_keys
    assert "points" in aux_keys
    assert "location" in aux_keys

    # Back fields
    back_keys = [f["key"] for f in generic["backFields"]]
    assert "member_info" in back_keys
    assert "referral_info" in back_keys
    assert "quick_links" in back_keys
    assert "event_entry" in back_keys
    assert "tier_info" in back_keys
    assert "safety_support" in back_keys

