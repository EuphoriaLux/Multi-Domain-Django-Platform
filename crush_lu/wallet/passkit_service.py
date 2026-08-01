import secrets
import importlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse, JsonResponse
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
    event tickets carry an `evt-*` serial on the EventRegistration and normally
    reuse the owner's profile token, except for open-event attendees who have no
    CrushProfile at all — those carry a per-ticket token persisted on the
    registration. Resolve every shape so the web service can authenticate update
    requests for either pass type — otherwise those tickets can never register.
    """
    if not serial_number:
        return None

    try:
        # Event tickets: serial lives on EventRegistration. The token is the
        # registration's own apple_wallet_auth_token when set (profile-less
        # attendees), otherwise the owner profile's apple_auth_token. This
        # precedence must match build_apple_event_ticket, which keeps using an
        # already-persisted registration token even if a profile appears later.
        if serial_number.startswith("evt-"):
            from ..models import EventRegistration

            registration = (
                EventRegistration.objects.filter(
                    apple_wallet_ticket_serial=serial_number
                )
                .select_related("user__crushprofile")
                .first()
            )
            if registration:
                if registration.apple_wallet_auth_token:
                    return registration.apple_wallet_auth_token
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
    """Authorize a per-pass web-service request (register/unregister/get-pass).

    Apple sends `Authorization: ApplePass <authenticationToken>` on these
    endpoints, so we resolve the per-pass token by serial and compare.
    NOTE: the GET "list registrations" poll is NOT authenticated this way and
    must NOT call this helper — see list_device_registrations.
    """
    expected_token = _get_expected_auth_token(pass_type_identifier, serial_number)
    if not expected_token:
        logger.error(
            "PassKit authentication token is not configured for serial %s",
            serial_number,
        )
        return HttpResponse(status=500)
    if not _is_authorized(request, expected_token):
        return HttpResponse(status=401)
    return None


def build_web_service_url(request):
    # IMPORTANT: this is the webServiceURL embedded in pass.json, and Apple
    # treats it as a BASE to which it appends its own protocol version
    # ("/v1/devices/...", "/v1/passes/...", "/v1/log"). Our routes live under
    # /wallet/v1/... (see urls_crush.py), so the base MUST be the unversioned
    # root "/wallet" — anything versioned here produces /wallet/v1/v1/... and
    # every PassKit web-service request 404s.
    base_path = getattr(settings, "PASSKIT_WEB_SERVICE_BASE_PATH", "/wallet")
    return request.build_absolute_uri(base_path.rstrip("/"))


def _normalize_service_root(url):
    """Coerce a webServiceURL into a valid PassKit service ROOT.

    Apple appends its own "/v1/devices/...", "/v1/passes/..." paths to whatever
    webServiceURL a pass advertises, so a root ending in "/v1" always produces
    /wallet/v1/v1/... and 404s — there is no configuration in which that
    trailing segment is correct. A trailing slash is equally bad (it yields
    //v1/... which matches no Django route).

    This normalization exists because WALLET_APPLE_WEB_SERVICE_URL has the
    highest precedence AND the live App Service configuration has carried the
    versioned value since the feature shipped. Without this, correcting the code
    alone would leave production broken until someone also edited the setting.
    Logged at WARNING so the misconfiguration stays visible at its source.
    """
    if not url:
        return url

    trimmed = url.rstrip("/")
    if trimmed.endswith("/v1"):
        corrected = trimmed[: -len("/v1")]
        logger.warning(
            "PassKit webServiceURL %r ends in /v1, but Apple appends its own "
            "/v1 — that would make every web-service request 404. Using %r "
            "instead; drop the trailing /v1 from WALLET_APPLE_WEB_SERVICE_URL.",
            url,
            corrected,
        )
        return corrected
    return trimmed


def resolve_web_service_url(request=None, web_service_url=None):
    """
    Resolve the webServiceURL (the PassKit service ROOT) to embed in a pass.

    A pass that carries an authenticationToken MUST also advertise a
    webServiceURL, otherwise iOS silently rejects it. Apple appends its own
    "/v1/..." protocol paths to this URL, so the value must be the unversioned
    root (e.g. https://crush.lu/wallet), NOT include /v1.

    THE SERVICE ROOT IS SLOT-BOUND, so the issuing host wins. Prefer:
      1. Derived from the current request via build_web_service_url(request) —
         the host that served the pass is the only host that can answer for it.
      2. An explicit caller-supplied web_service_url, forwarded by the PassKit
         provider on rebuilds (get_latest_pass derives it from Apple's own live
         request, so it names the slot Apple is already talking to).
      3. The WALLET_APPLE_WEB_SERVICE_URL setting — the fallback for builds with
         no request context at all (management commands, background rebuilds).
      4. "" (nothing available) — the caller's `if url:` guard then omits the
         field, matching the long-standing behaviour.

    Why the request beats the setting: the whole PassKit web service resolves a
    pass by serial against the DATABASE of whichever slot Apple contacts, and
    the slots have isolated databases (prod `pythonapp`, staging
    `pythonapp_staging`). A pass downloaded from test.crush.lu that advertises
    the production root sends Apple to production, which has never heard of that
    serial — registration 500s and the pass can never update. The DEBUG iOS
    target points at test.crush.lu, so this is the normal path for every
    developer build. A host-stable setting is precisely the wrong thing here.

    This reverses the earlier "setting must win" rule, and safely: that rule
    existed because request derivation used to produce a VERSIONED base
    (PASSKIT_WEB_SERVICE_BASE_PATH defaulted to /wallet/v1), so a rebuild could
    rewrite a correct root into the /v1/v1 trap. The base path is now the
    unversioned root and _normalize_service_root strips a stray /v1 from every
    branch, so derivation can no longer drift — the setting no longer needs to
    defend against it, and the operator override survives as the no-request
    fallback.
    """
    if request is not None:
        try:
            derived = build_web_service_url(request)
        except Exception:
            # build_absolute_uri can raise on pathological host headers; never
            # let URL derivation turn a pass build into a 500. Fall through to
            # the forwarded value / setting rather than returning "" — a pass
            # with an authenticationToken and no webServiceURL is rejected.
            derived = ""
        if derived:
            return _normalize_service_root(derived)
    if web_service_url:
        return _normalize_service_root(web_service_url)
    explicit = getattr(settings, "WALLET_APPLE_WEB_SERVICE_URL", "")
    if explicit:
        return _normalize_service_root(explicit)
    return ""


def inject_web_service_fields(pass_json, request, authentication_token):
    # Route through the resolver rather than build_web_service_url directly, so
    # this path gets the same /v1 normalization and the same slot-bound
    # precedence as every other pass builder.
    pass_json["webServiceURL"] = resolve_web_service_url(request)
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
    # Per the PassKit Web Service spec, this GET poll is NOT authenticated with
    # an `Authorization: ApplePass ...` header — Apple identifies the caller
    # solely by deviceLibraryIdentifier in the URL. Requiring auth here made
    # every device poll return 500/401, so passes never received update
    # notifications. Auth happens at register/unregister/get-pass instead.
    passes_updated_since = request.GET.get("passesUpdatedSince")

    registrations = PasskitDeviceRegistration.objects.filter(
        device_library_identifier=device_library_identifier,
        pass_type_identifier=pass_type_identifier,
    )

    if passes_updated_since:
        try:
            # datetime.UTC, NOT django.utils.timezone.utc — the latter was
            # removed in Django 5.0 and raises AttributeError here, which this
            # except never caught. Every cursor-bearing poll (i.e. every poll
            # after the first) 500'd, so installed passes stopped updating.
            updated_since = datetime.fromtimestamp(
                float(passes_updated_since),
                tz=UTC,
            )
            registrations = registrations.filter(updated_at__gt=updated_since)
        except (ValueError, OSError, OverflowError):
            return JsonResponse({"error": "Invalid passesUpdatedSince"}, status=400)

    serial_numbers = list(registrations.values_list("serial_number", flat=True))
    if not serial_numbers:
        return HttpResponse(status=204)

    last_updated = registrations.order_by("-updated_at").first().updated_at
    response_payload = {
        "serialNumbers": serial_numbers,
        # Apple treats lastUpdated as an OPAQUE tag and echoes it back as
        # passesUpdatedSince, so it must round-trip losslessly. int() truncated
        # the sub-second part, which put the reconstructed cursor BEFORE the
        # stored updated_at — `updated_at__gt` then matched the same row on
        # every subsequent poll and Wallet redownloaded an unchanged pass
        # forever. float() on the way back in parses either form, so cursors
        # already held by installed passes still work.
        "lastUpdated": f"{last_updated.timestamp():.6f}",
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


def refresh_event_tickets(event):
    """Refresh every installed Apple ticket for an event.

    Event-level changes rewrite the pass payload — date, time, location, and
    `voided` — but never touch an EventRegistration row, so the per-registration
    receiver never fires. Worse, both paths that make those changes use
    `queryset.update()` (the admin's bulk cancel action, and the reschedule
    handler, which does so deliberately to avoid per-registration receivers),
    and that emits no signals at all. So this has to be called explicitly.

    Deferred to commit for the same reason as the registration receiver: the
    marker advance and the push must not be visible to Wallet before the event
    change itself is. on_commit runs inline outside a transaction.
    """
    pass_type_id = getattr(settings, "WALLET_APPLE_PASS_TYPE_IDENTIFIER", None)
    if not pass_type_id:
        return 0

    from ..models import EventRegistration

    serials = list(
        EventRegistration.objects.filter(event=event)
        .exclude(apple_wallet_ticket_serial="")
        .values_list("apple_wallet_ticket_serial", flat=True)
    )
    if not serials:
        return 0

    def _after_commit():
        for serial in serials:
            try:
                trigger_pass_refresh(pass_type_id, serial)
            except Exception:
                # One unreachable device must not strand the rest.
                logger.exception("Failed refreshing Apple event ticket %s", serial)

    transaction.on_commit(_after_commit)
    return len(serials)
