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
    Resolve PassKit auth token by looking up the CrushProfile with matching serial number.

    Each Apple Wallet pass is generated with a unique auth token stored on the profile.
    This resolver looks up that token so PassKit web service requests can be authenticated.
    """
    if not serial_number:
        return None

    from ..models import CrushProfile

    try:
        profile = CrushProfile.objects.filter(apple_pass_serial=serial_number).first()
        if profile and profile.apple_auth_token:
            return profile.apple_auth_token
    except Exception as e:
        logger.error("Error resolving PassKit auth token for serial %s: %s", serial_number, e)

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
    return token and token == expected_token


def _require_authorization(request, pass_type_identifier, serial_number):
    expected_token = _get_expected_auth_token(pass_type_identifier, serial_number)
    if not expected_token:
        logger.error("PassKit authentication token is not configured.")
        return HttpResponse(status=500)
    if not _is_authorized(request, expected_token):
        return HttpResponse(status=401)
    return None


def build_web_service_url(request):
    base_path = getattr(settings, "PASSKIT_WEB_SERVICE_BASE_PATH", "/wallet/v1")
    return request.build_absolute_uri(base_path.rstrip("/"))


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
