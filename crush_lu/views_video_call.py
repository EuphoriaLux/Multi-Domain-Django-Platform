"""Superuser test room for the in-app video call.

Two superusers open the same room code on two devices to check a real call,
and the Cloudflare relay, before any feature offers calls to members.
Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md (D12)
"""

from django.http import Http404
from django.shortcuts import render
from django.views.decorators.cache import never_cache

from azureproject.csp_middleware import allow_microphone
from crush_lu.decorators import crush_login_required
from crush_lu.services.video_call import valid_call_address, video_call_context

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
