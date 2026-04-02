"""
Google Identity Platform / Firebase ID Token Verification

This module provides KEYLESS verification of Firebase/Identity Platform ID tokens
using Google's public JWKS (JSON Web Key Set) endpoint.

NO SERVICE ACCOUNT KEYS REQUIRED - uses only public signing keys from Google.

Security:
- Verifies token signature using RS256 algorithm
- Validates audience matches Firebase project ID
- Validates issuer is Google's secure token service
- Checks token expiration
- Extracts phone_number from verified claims only

Usage:
    from crush_lu.google_idp_verify import verify_firebase_id_token

    try:
        decoded = verify_firebase_id_token(id_token)
        phone_number = decoded.get('phone_number')
    except Exception as e:
        # Token invalid
        pass
"""
import time
import logging
import jwt
from jwt import PyJWKClient
from django.conf import settings

logger = logging.getLogger(__name__)

# Google's JWKS endpoint for Firebase/Identity Platform tokens
GOOGLE_JWKS_URL = "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"

# Cache the JWK client and refresh periodically to handle key rotation
_jwk_client = None
_last_init = 0
_CACHE_TTL_SECONDS = 3600  # Refresh keys every hour


def _get_jwk_client():
    """
    Get or create a JWK client for fetching Google's public signing keys.

    The client is cached and refreshed periodically to handle key rotation
    while minimizing network requests.
    """
    global _jwk_client, _last_init
    now = int(time.time())

    if _jwk_client is None or (now - _last_init) > _CACHE_TTL_SECONDS:
        logger.debug("Initializing JWK client with Google's public keys")
        _jwk_client = PyJWKClient(GOOGLE_JWKS_URL)
        _last_init = now

    return _jwk_client


def verify_firebase_id_token(id_token: str) -> dict:
    """
    Verify a Firebase/Identity Platform ID token using Google's public keys.

    This is a KEYLESS verification - no service account keys required.
    It uses Google's publicly available JWKS endpoint to fetch signing keys.

    Args:
        id_token: The Firebase ID token (JWT) from the client

    Returns:
        dict: Decoded token claims including:
            - sub: Firebase user ID
            - phone_number: Verified phone number (if present)
            - aud: Audience (should match project ID)
            - iss: Issuer
            - exp: Expiration timestamp
            - iat: Issued at timestamp

    Raises:
        RuntimeError: If FIREBASE_PROJECT_ID is not configured
        jwt.ExpiredSignatureError: If token is expired
        jwt.InvalidAudienceError: If audience doesn't match project ID
        jwt.InvalidIssuerError: If issuer is not Google's secure token service
        jwt.InvalidTokenError: If token signature or format is invalid
        ValueError: If token is missing required claims
    """
    # Get project ID from settings
    project_id = getattr(settings, "FIREBASE_PROJECT_ID", None)
    if not project_id:
        raise RuntimeError(
            "Missing FIREBASE_PROJECT_ID in Django settings. "
            "Set it to your Google Cloud project ID."
        )

    # Get the JWK client (cached)
    jwk_client = _get_jwk_client()

    try:
        # Get the signing key from the token header
        signing_key = jwk_client.get_signing_key_from_jwt(id_token)

        # Decode and verify the token
        decoded = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=project_id,
            issuer=f"https://securetoken.google.com/{project_id}",
            options={
                "require": ["exp", "iat", "aud", "iss", "sub"],
            },
        )

        # Additional validation
        if not decoded.get("sub"):
            raise ValueError("Invalid token: missing 'sub' (subject) claim")

        logger.debug(
            f"Token verified successfully for Firebase UID: {decoded.get('sub')}"
        )

        return decoded

    except jwt.ExpiredSignatureError:
        logger.warning("Firebase ID token has expired")
        raise
    except jwt.InvalidAudienceError:
        logger.warning(f"Token audience doesn't match project: {project_id}")
        raise
    except jwt.InvalidIssuerError:
        logger.warning("Token issuer is not Google's secure token service")
        raise
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid Firebase ID token: {e}")
        raise


def get_phone_from_token(decoded_token: dict) -> str:
    """
    Extract the phone number from a verified token.

    Args:
        decoded_token: The decoded claims from verify_firebase_id_token()

    Returns:
        str: The phone number in E.164 format (e.g., "+352123456789")
             or empty string if not present
    """
    return decoded_token.get("phone_number", "")


def get_firebase_uid_from_token(decoded_token: dict) -> str:
    """
    Extract the Firebase user ID from a verified token.

    This can be stored for audit purposes and to prevent token replay.

    Args:
        decoded_token: The decoded claims from verify_firebase_id_token()

    Returns:
        str: The Firebase user ID (sub claim)
    """
    return decoded_token.get("sub", "")
