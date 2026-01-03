import base64
import json
import secrets
import time

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def _require_setting(name):
    value = getattr(settings, name, None)
    if not value:
        raise ImproperlyConfigured(f"Missing required setting: {name}")
    return value


def _base64url_encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def _load_private_key():
    key_data = getattr(settings, "WALLET_GOOGLE_PRIVATE_KEY", None)
    if not key_data:
        key_path = _require_setting("WALLET_GOOGLE_PRIVATE_KEY_PATH")
        with open(key_path, "rb") as handle:
            key_data = handle.read()
    elif isinstance(key_data, str):
        key_data = key_data.replace("\\n", "\n").encode("utf-8")

    return serialization.load_pem_private_key(key_data, password=None)


def _ensure_object_id(profile):
    if profile.google_wallet_object_id:
        return profile.google_wallet_object_id

    issuer_id = _require_setting("WALLET_GOOGLE_ISSUER_ID")
    object_suffix = secrets.token_hex(8)
    object_id = f"{issuer_id}.crush-{profile.user_id}-{object_suffix}"
    profile.google_wallet_object_id = object_id
    profile.save(update_fields=["google_wallet_object_id"])
    return object_id


def build_google_wallet_jwt(profile):
    issuer_id = _require_setting("WALLET_GOOGLE_ISSUER_ID")
    class_id = _require_setting("WALLET_GOOGLE_CLASS_ID")
    service_account_email = _require_setting("WALLET_GOOGLE_SERVICE_ACCOUNT_EMAIL")
    key_id = getattr(settings, "WALLET_GOOGLE_KEY_ID", "")

    object_id = _ensure_object_id(profile)

    issued_at = int(time.time())
    payload = {
        "iss": service_account_email,
        "aud": "google",
        "typ": "savetowallet",
        "iat": issued_at,
        "exp": issued_at + 3600,
        "payload": {
            "genericObjects": [
                {
                    "id": object_id,
                    "classId": class_id,
                    "state": "active",
                    "header": "Crush.lu",
                    "subheader": profile.display_name,
                    "textModulesData": [
                        {
                            "header": "Member",
                            "body": profile.display_name,
                        }
                    ],
                    "barcode": {
                        "type": "QR_CODE",
                        "value": object_id,
                    },
                    "cardTitle": {
                        "defaultValue": {
                            "language": "en-US",
                            "value": "Crush.lu Member",
                        }
                    },
                }
            ]
        },
    }

    header = {"alg": "RS256", "typ": "JWT"}
    if key_id:
        header["kid"] = key_id

    signing_input = b".".join(
        [
            _base64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8")),
            _base64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
        ]
    )

    private_key = _load_private_key()
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    signed_jwt = b".".join([signing_input, _base64url_encode(signature)])
    return signed_jwt.decode("utf-8")
