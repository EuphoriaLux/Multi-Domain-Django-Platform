import secrets
import importlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.http import http_date, parse_http_date
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from ..models import PasskitDeviceRegistration
from .passkit_apns import send_passkit_push_notifications

logger = logging.getLogger(__name__)


@dataclass
class PasskitPass:
    pkpass_bytes: bytes
    last_updated: datetime | None = None


def _load_callable(path):
    module_path, attr = path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, attr)


def _resolve_auth_token_from_profile(pass_type_identifier, serial_number):
    """
    Resolve the PassKit auth token for a pass serial number.

    Each Apple Wallet pass is generated with a unique auth token stored on the
    owner's CrushProfile. Member passes carry their profile's apple_pass_serial;
    event tickets carry an `evt-*` serial on the EventRegistration but reuse the
    owner's profile token (see apple_event_ticket._ensure_pass_identifiers use).
    Resolve both shapes so the web service can authenticate update requests for
    either pass type — otherwise event tickets can never register for updates.
    """
    if not serial_number:
        return None

    try:
        # Event tickets: serial lives on EventRegistration; the token is the
        # owner profile's apple_auth_token.
        if serial_number.startswith("evt-"):
            from ..models import EventRegistration

            registration = (
                EventRegistration.objects.filter(
                    apple_wallet_ticket_serial=serial_number
                )
                .select_related("user__crushprofile")
                .first()
            )
            if registration and registration.user_id:
                profile = getattr(registration.user, "crushprofile", None)
                if profile and profile.apple_auth_token:
                    return profile.apple_auth_token
            return None

        # Member passes: serial is the profile's apple_pass_serial.
        from ..models import CrushProfile

        profile = CrushProfile.objects.filter(apple_pass_serial=serial_number).first()
        if profile and profile.apple_auth_token:
            return profile.apple_auth_token
    except Exception as e:
        logger.error(
            "Error resolving PassKit auth token for serial %s: %s",
            serial_number,
            e,
        )

    return None


def _get_expected_auth_token(pass_type_identifier, serial_number):
    # First, try to resolve from CrushProfile (per-pass tokens)
    profile_token = _resolve_auth_token_from_profile(pass_type_identifier, serial_number)
    if profile_token:
        return profile_token

    # Then, check for custom resolver
    resolver_path = getattr(settings, "PASSKIT_AUTH_TOKEN_RESOLVER", None)
    if resolver_path:
        resolver = _load_callable(resolver_path)
        resolved = resolver(pass_type_identifier, serial_number)
        if resolved:
            return resolved

    # Check token map
    token_map = getattr(settings, "PASSKIT_AUTH_TOKENS", {})
    if isinstance(token_map, dict) and serial_number in token_map:
        return token_map[serial_number]

    # Fall back to global token
    return getattr(settings, "PASSKIT_AUTH_TOKEN", None)


def _is_authorized(request, expected_token):
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("ApplePass "):
        return False
    token = auth_header.split(" ", 1)[1].strip()
    # compare_digest raises TypeError on non-ASCII — use strict ASCII encoding
    # and catch UnicodeEncodeError so malformed auth headers get a clean 401
    # instead of a 500. "ignore" would silently drop chars and allow token
    # mismatch (Codex P2).
    try:
        token_bytes = token.encode("ascii", "strict")
        expected_bytes = expected_token.encode("ascii", "strict")
    except (UnicodeEncodeError, AttributeError):
        return False
    return bool(token) and secrets.compare_digest(token_bytes, expected_bytes)


def _require_authorization(request, pass_type_identifier, serial_number):
    expected_token = _get_expected_auth_token(pass_type_identifier, serial_number)
    if expected_token:
        if _is_authorized(request, expected_token):
            return None
        return HttpResponse(status=401)

    # No per-pass token resolved. For the serial-less "list registrations"
    # endpoint (GET /devices/.../registrations/<passTypeIdentifier>) there is
    # no serial in the URL, so a per-profile token cannot be looked up at all;
    # fall back to the shared PASSKIT_AUTH_TOKEN, which is the documented Apple
    # pattern for authenticating that poll. Per-serial endpoints still require
    # their own per-profile token.
    if serial_number is None:
        shared_token = getattr(settings, "PASSKIT_AUTH_TOKEN", None)
        if shared_token and _is_authorized(request, shared_token):
            return None
        logger.error(
            "PassKit list-registrations auth failed: no per-pass token and no "
            "shared PASSKIT_AUTH_TOKEN configured."
        )
        return HttpResponse(status=401 if shared_token else 500)

    logger.error("PassKit authentication token is not configured for serial %s", serial_number)
    return HttpResponse(status=500)


def build_web_service_url(request):
    # IMPORTANT: this is the webServiceURL embedded in pass.json, and Apple
    # treats it as a BASE to which it appends its own protocol version
    # ("/v1/devices/...", "/v1/passes/...", "/v1/log"). Our routes live under
    # /wallet/v1/... (see urls_crush.py), so the base MUST be the unversioned
    # root "/wallet" — anything versioned here produces /wallet/v1/v1/... and
    # every PassKit web-service request 404s.
    base_path = getattr(settings, "PASSKIT_WEB_SERVICE_BASE_PATH", "/wallet")
    return request.build_absolute_uri(base_path.rstrip("/"))


def resolve_web_service_url(request=None, web_service_url=None):
    """
    Resolve the webServiceURL (the PassKit service ROOT) to embed in a pass.

    A pass that carries an authenticationToken MUST also advertise a
    webServiceURL, otherwise iOS silently rejects it. Apple appends its own
    "/v1/..." protocol paths to this URL, so the value must be the unversioned
    root (e.g. https://crush.lu/wallet), NOT include /v1.

    Prefer (in order):
      1. The WALLET_APPLE_WEB_SERVICE_URL setting — host-stable across
         instances and survives behind proxies, and the operator controls the
         exact root so it can't drift into the /v1/v1 trap.
      2. An explicit caller-supplied web_service_url, when it differs from the
         versioned route base (forwarded by the PassKit provider on rebuilds).
      3. Derived from the current request via build_web_service_url(request)
         (which itself produces the unversioned root).
      4. "" (nothing available) — the caller's `if url:` guard then omits the
         field, matching the long-standing behaviour.

    The setting wins over the caller arg deliberately: get_latest_pass derives
    a value from its live request and passes it here; if that derivation ever
    drifts to a versioned path, the setting must be able to override it rather
    than the rebuild silently corrupting every subsequent pass.
    """
    explicit = getattr(settings, "WALLET_APPLE_WEB_SERVICE_URL", "")
    if explicit:
        return explicit
    if web_service_url:
        return web_service_url
    if request is not None:
        try:
            return build_web_service_url(request)
        except Exception:
            # build_absolute_uri can raise on pathological host headers; never
            # let URL derivation turn a pass build into a 500.
            return ""
    return ""


def inject_web_service_fields(pass_json, request, authentication_token):
    pass_json["webServiceURL"] = build_web_service_url(request)
    pass_json["authenticationToken"] = authentication_token
    return pass_json


def _load_pass_provider():
    provider_path = getattr(settings, "PASSKIT_PASS_PROVIDER", None)
    if not provider_path:
        return None
    return _load_callable(provider_path)


def _load_pass_json_provider():
    provider_path = getattr(settings, "PASSKIT_PASS_JSON_PROVIDER", None)
    if not provider_path:
        return None
    return _load_callable(provider_path)


def _load_pass_package_builder():
    builder_path = getattr(settings, "PASSKIT_PASS_PACKAGE_BUILDER", None)
    if not builder_path:
        return None
    return _load_callable(builder_path)


def _register_device(request, device_library_identifier, pass_type_identifier, serial_number):
    auth_response = _require_authorization(request, pass_type_identifier, serial_number)
    if auth_response:
        return auth_response

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    push_token = payload.get("pushToken")
    if not push_token:
        return JsonResponse({"error": "pushToken is required"}, status=400)

    registration, created = PasskitDeviceRegistration.objects.update_or_create(
        device_library_identifier=device_library_identifier,
        serial_number=serial_number,
        defaults={
            "pass_type_identifier": pass_type_identifier,
            "push_token": push_token,
        },
    )

    status_code = 201 if created else 200
    return HttpResponse(status=status_code)


def _unregister_device(request, device_library_identifier, pass_type_identifier, serial_number):
    auth_response = _require_authorization(request, pass_type_identifier, serial_number)
    if auth_response:
        return auth_response

    deleted, _ = PasskitDeviceRegistration.objects.filter(
        device_library_identifier=device_library_identifier,
        pass_type_identifier=pass_type_identifier,
        serial_number=serial_number,
    ).delete()

    if deleted:
        return HttpResponse(status=200)

    return HttpResponse(status=404)


@csrf_exempt
@require_http_methods(["POST", "DELETE"])
def device_registration(request, device_library_identifier, pass_type_identifier, serial_number):
    if request.method == "POST":
        return _register_device(
            request,
            device_library_identifier,
            pass_type_identifier,
            serial_number,
        )
    return _unregister_device(
        request,
        device_library_identifier,
        pass_type_identifier,
        serial_number,
    )


@csrf_exempt
@require_http_methods(["GET"])
def list_device_registrations(request, device_library_identifier, pass_type_identifier):
    passes_updated_since = request.GET.get("passesUpdatedSince")
    auth_response = _require_authorization(request, pass_type_identifier, serial_number=None)
    if auth_response:
        return auth_response

    registrations = PasskitDeviceRegistration.objects.filter(
        device_library_identifier=device_library_identifier,
        pass_type_identifier=pass_type_identifier,
    )

    if passes_updated_since:
        try:
            updated_since = datetime.fromtimestamp(
                float(passes_updated_since),
                tz=timezone.utc,
            )
            registrations = registrations.filter(updated_at__gt=updated_since)
        except (ValueError, OSError):
            return JsonResponse({"error": "Invalid passesUpdatedSince"}, status=400)

    serial_numbers = list(registrations.values_list("serial_number", flat=True))
    if not serial_numbers:
        return HttpResponse(status=204)

    last_updated = registrations.order_by("-updated_at").first().updated_at
    response_payload = {
        "serialNumbers": serial_numbers,
        "lastUpdated": int(last_updated.timestamp()),
    }
    return JsonResponse(response_payload)


@csrf_exempt
@require_http_methods(["GET"])
def get_latest_pass(request, pass_type_identifier, serial_number):
    auth_response = _require_authorization(request, pass_type_identifier, serial_number)
    if auth_response:
        return auth_response

    authentication_token = _get_expected_auth_token(pass_type_identifier, serial_number)
    pass_json_provider = _load_pass_json_provider()
    pass_package_builder = _load_pass_package_builder()

    if pass_json_provider and pass_package_builder:
        pass_json = pass_json_provider(pass_type_identifier, serial_number)
        if not pass_json:
            return HttpResponse(status=404)
        inject_web_service_fields(pass_json, request, authentication_token)
        pass_result = pass_package_builder(
            pass_json,
            pass_type_identifier,
            serial_number,
        )
    else:
        provider = _load_pass_provider()
        if not provider:
            return HttpResponse(status=404)
        pass_result = provider(
            pass_type_identifier,
            serial_number,
            web_service_url=build_web_service_url(request),
            authentication_token=authentication_token,
        )

    if isinstance(pass_result, PasskitPass):
        pkpass = pass_result.pkpass_bytes
        last_updated = pass_result.last_updated
    elif isinstance(pass_result, tuple):
        pkpass, last_updated = pass_result
    else:
        pkpass = pass_result
        last_updated = None

    if pkpass is None:
        return HttpResponse(status=404)

    if last_updated and request.headers.get("If-Modified-Since"):
        try:
            if_modified_since = parse_http_date(request.headers["If-Modified-Since"])
            if if_modified_since and last_updated.timestamp() <= if_modified_since:
                return HttpResponse(status=304)
        except (ValueError, OverflowError):
            pass

    response = HttpResponse(pkpass, content_type="application/vnd.apple.pkpass")
    if last_updated:
        response["Last-Modified"] = http_date(last_updated.timestamp())
    return response


@csrf_exempt
@require_http_methods(["POST"])
def log_endpoint(request):
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    logs = payload.get("logs", [])
    for entry in logs:
        logger.info("PassKit log: %s", entry)

    return HttpResponse(status=200)


def trigger_pass_refresh(pass_type_identifier, serial_number):
    return send_passkit_push_notifications(pass_type_identifier, serial_number)
