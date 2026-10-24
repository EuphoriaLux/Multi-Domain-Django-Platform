"""In-app two-person video calls: who may join a call, and the page context.

The media goes browser to browser, or through the TURN relay; it never
touches Django. Django decides who may join and relays the WebRTC call set-up
over a WebSocket (``crush_lu/consumers_video_call.py``).

Every feature that offers a call is a *kind* in ``VIDEO_CALL_KINDS``, mapped
to the dotted path of an authorizer ``(user, object_id) -> bool``. The
consumer resolves it on every connect, so a kind never depends on its module
having been imported first. The authorizer owns the feature's guards and must
admit exactly the two people of the call.

Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md (D12)
"""

import re

from django.conf import settings
from django.utils.module_loading import import_string

from crush_lu.services.turn_credentials import generate_ice_servers

VIDEO_CALL_KINDS = {
    # Superuser-only test room for checking calls and the relay on real phones.
    "staff-test": "crush_lu.services.video_call.authorize_staff_test_call",
}

# Both end up in a channel group name, which allows only ASCII letters,
# digits, hyphens, underscores and periods, under 100 characters.
KIND_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
OBJECT_ID_RE = re.compile(r"^[A-Za-z0-9]{1,32}$")

ICE_TRANSPORT_POLICIES = ("relay", "all")


def valid_call_address(kind, object_id) -> bool:
    return bool(
        isinstance(kind, str)
        and isinstance(object_id, str)
        and KIND_RE.match(kind)
        and OBJECT_ID_RE.match(object_id)
    )


def can_join_call(user, kind, object_id) -> bool:
    """Whether ``user`` may join the call ``kind``/``object_id`` right now."""
    if user is None or not user.is_authenticated or not user.is_active:
        return False
    if not valid_call_address(kind, object_id):
        return False
    path = VIDEO_CALL_KINDS.get(kind)
    if path is None:
        return False
    return bool(import_string(path)(user, object_id))


def authorize_staff_test_call(user, object_id) -> bool:
    return user.is_superuser


def call_group_name(kind, object_id) -> str:
    return f"video_call.{kind}.{object_id}"


def call_socket_path(kind, object_id) -> str:
    return f"/ws/video-call/{kind}/{object_id}/"


def ice_transport_policy() -> str:
    policy = getattr(settings, "VIDEO_CALL_ICE_TRANSPORT_POLICY", "relay")
    return policy if policy in ICE_TRANSPORT_POLICIES else "relay"


def video_call_context(kind, object_id):
    """Context for ``crush_lu/partials/video_call.html``.

    Mints fresh relay credentials, so the view rendering it must not be
    cached, and must call ``allow_microphone(response)``. Without TURN the
    call is unavailable, except under DEBUG where two local browsers can
    connect directly.
    """
    ice_servers = generate_ice_servers()
    policy = ice_transport_policy()
    if ice_servers is None:
        if not settings.DEBUG:
            return {"video_call": {"available": False}}
        ice_servers, policy = [], "all"
    return {
        "video_call": {
            "available": True,
            "config": {
                "socketPath": call_socket_path(kind, object_id),
                "iceServers": ice_servers,
                "iceTransportPolicy": policy,
            },
        }
    }
