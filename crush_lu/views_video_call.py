"""In-app video calls: fresh relay credentials, and the superuser test room.

Two superusers open the same room code on two devices to check a real call,
and the Cloudflare relay, before any feature offers calls to members.
Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md (D12)
"""

from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.utils.cache import add_never_cache_headers
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from azureproject.csp_middleware import allow_microphone
from crush_lu.decorators import crush_login_required, ratelimit
from crush_lu.services.video_call import (
    call_role,
    ice_servers_payload,
    valid_call_address,
    video_call_context,
)

TEST_KIND = "staff-test"


@crush_login_required
@never_cache
def video_call_test(request, room):
    if not request.user.is_superuser or not valid_call_address(TEST_KIND, room):
        raise Http404
    context = video_call_context(TEST_KIND, room)
    context["room"] = room
    response = render(request, "crush_lu/video_call_test.html", context)
    return allow_microphone(response)


@crush_login_required
# Each call mints relay credentials with one Cloudflare request; a person
# presses Start a few times at most.
@ratelimit(key="user", rate="20/m", method="POST")
@require_POST
def video_call_ice_servers(request, kind, object_id):
    """Fresh relay credentials for one call, minted when the person presses Start."""
    if not call_role(request.user, kind, object_id):
        raise Http404
    payload = ice_servers_payload()
    if payload is None:
        response = JsonResponse({"error": "unavailable"}, status=503)
    else:
        response = JsonResponse(payload)
    add_never_cache_headers(response)
    return response
