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
