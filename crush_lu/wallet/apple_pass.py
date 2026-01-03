import base64
import hashlib
import json
import os
import secrets
import subprocess
import tempfile
from datetime import datetime, timezone
from zipfile import ZIP_DEFLATED, ZipFile

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

ICON_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwAB"
    "BAEAu1fE3wAAAABJRU5ErkJggg=="
)


def _require_setting(name):
    value = getattr(settings, name, None)
    if not value:
        raise ImproperlyConfigured(f"Missing required setting: {name}")
    return value


def _get_pass_paths():
    cert_path = _require_setting("WALLET_APPLE_CERT_PATH")
    key_path = _require_setting("WALLET_APPLE_KEY_PATH")
    wwdr_path = _require_setting("WALLET_APPLE_WWDR_CERT_PATH")
    key_password = getattr(settings, "WALLET_APPLE_KEY_PASSWORD", "")
    return cert_path, key_path, wwdr_path, key_password


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


def _build_pass_payload(profile, serial_number, auth_token):
    pass_type_identifier = _require_setting("WALLET_APPLE_PASS_TYPE_IDENTIFIER")
    team_identifier = _require_setting("WALLET_APPLE_TEAM_IDENTIFIER")
    organization_name = _require_setting("WALLET_APPLE_ORGANIZATION_NAME")
    web_service_url = getattr(settings, "WALLET_APPLE_WEB_SERVICE_URL", "")

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
            "primaryFields": [
                {
                    "key": "member",
                    "label": "Member",
                    "value": profile.display_name,
                }
            ],
            "secondaryFields": [
                {
                    "key": "member_since",
                    "label": "Member since",
                    "value": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                }
            ],
        },
        "backgroundColor": "rgb(239, 68, 68)",
        "foregroundColor": "rgb(255, 255, 255)",
        "labelColor": "rgb(255, 255, 255)",
    }

    if web_service_url:
        payload["webServiceURL"] = web_service_url

    return payload


def _sign_manifest(manifest_path, signature_path):
    cert_path, key_path, wwdr_path, key_password = _get_pass_paths()

    command = [
        "openssl",
        "smime",
        "-binary",
        "-sign",
        "-certfile",
        wwdr_path,
        "-signer",
        cert_path,
        "-inkey",
        key_path,
        "-outform",
        "DER",
        "-in",
        manifest_path,
        "-out",
        signature_path,
    ]

    if key_password:
        command.extend(["-passin", f"pass:{key_password}"])

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            "Failed to sign Apple Wallet pass manifest: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


def build_apple_pass(profile):
    serial_number, auth_token = _ensure_pass_identifiers(profile)
    pass_payload = _build_pass_payload(profile, serial_number, auth_token)

    with tempfile.TemporaryDirectory() as temp_dir:
        pass_json_path = os.path.join(temp_dir, "pass.json")
        manifest_path = os.path.join(temp_dir, "manifest.json")
        signature_path = os.path.join(temp_dir, "signature")
        icon_path = os.path.join(temp_dir, "icon.png")

        with open(pass_json_path, "w", encoding="utf-8") as handle:
            json.dump(pass_payload, handle, ensure_ascii=False, separators=(",", ":"))

        with open(icon_path, "wb") as handle:
            handle.write(base64.b64decode(ICON_PNG_BASE64))

        manifest = {}
        for filename in ("pass.json", "icon.png"):
            file_path = os.path.join(temp_dir, filename)
            with open(file_path, "rb") as handle:
                manifest[filename] = hashlib.sha1(handle.read()).hexdigest()

        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, separators=(",", ":"))

        _sign_manifest(manifest_path, signature_path)

        pass_buffer = tempfile.NamedTemporaryFile(suffix=".pkpass", delete=False)
        try:
            with ZipFile(pass_buffer.name, "w", ZIP_DEFLATED) as pass_zip:
                pass_zip.write(pass_json_path, "pass.json")
                pass_zip.write(manifest_path, "manifest.json")
                pass_zip.write(signature_path, "signature")
                pass_zip.write(icon_path, "icon.png")
            with open(pass_buffer.name, "rb") as handle:
                return handle.read()
        finally:
            pass_buffer.close()
            os.unlink(pass_buffer.name)
