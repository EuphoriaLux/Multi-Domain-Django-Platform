"""In-app two-person video calls: who may join a call, and the page context.

The media goes browser to browser, or through the TURN relay; it never
touches Django. Django decides who may join and relays the WebRTC call set-up
over a WebSocket (``crush_lu/consumers_video_call.py``).

Every feature that offers a call is a *kind* in ``VIDEO_CALL_KINDS``, mapped
to the dotted path of an authorizer ``(user, object_id) -> role``. The
authorizer owns the feature's guards and returns the user's role in the call
(for example ``"coach"`` or ``"member"``), or None to refuse. A call has at
most two roles, and each role has one live socket: a newer socket (a reload,
a second tab) replaces the older one. The consumer and the relay-credential
endpoint resolve the authorizer on every use, so a kind never depends on its
module having been imported first.

Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md (D12)
"""

import re

from django.conf import settings
from django.utils.module_loading import import_string

from crush_lu.services.turn_credentials import generate_ice_servers, turn_configured

VIDEO_CALL_KINDS = {
    # Superuser-only test room for checking calls and the relay on real
    # phones. Each superuser is a role, so test with two different accounts.
    "staff-test": "crush_lu.services.video_call.authorize_staff_test_call",
}

# Both end up in a channel group name, which allows only ASCII letters,
# digits, hyphens, underscores and periods, under 100 characters.
KIND_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
OBJECT_ID_RE = re.compile(r"^[A-Za-z0-9]{1,32}$")
ROLE_RE = re.compile(r"^[a-z0-9-]{1,32}$")

ICE_TRANSPORT_POLICIES = ("relay", "all")


def valid_call_address(kind, object_id) -> bool:
    return bool(
        isinstance(kind, str)
        and isinstance(object_id, str)
        and KIND_RE.match(kind)
        and OBJECT_ID_RE.match(object_id)
    )


def call_role(user, kind, object_id):
    """The role ``user`` holds in the call ``kind``/``object_id``, or None."""
    if user is None or not user.is_authenticated or not user.is_active:
        return None
    if not valid_call_address(kind, object_id):
        return None
    path = VIDEO_CALL_KINDS.get(kind)
    if path is None:
        return None
    role = import_string(path)(user, object_id)
    if not isinstance(role, str) or not ROLE_RE.match(role):
        return None
    return role


def authorize_staff_test_call(user, object_id):
    return f"user-{user.pk}" if user.is_superuser else None


def call_group_name(kind, object_id) -> str:
    return f"video_call.{kind}.{object_id}"


def call_socket_path(kind, object_id) -> str:
    return f"/ws/video-call/{kind}/{object_id}/"


def ice_transport_policy() -> str:
    policy = getattr(settings, "VIDEO_CALL_ICE_TRANSPORT_POLICY", "relay")
    return policy if policy in ICE_TRANSPORT_POLICIES else "relay"


def video_calls_available() -> bool:
    """TURN is configured, or DEBUG lets two local browsers connect directly."""
    return turn_configured() or settings.DEBUG


def video_call_context(kind, object_id):
    """Context for ``crush_lu/partials/video_call.html``.

    The page holds no credentials: the call screen fetches fresh relay
    credentials from the ``video_call_ice_servers`` endpoint when the person
    presses Start. The view rendering it must return
    ``allow_microphone(response)``.
    """
    if not video_calls_available():
        return {"video_call": {"available": False}}
    return {
        "video_call": {
            "available": True,
            "kind": kind,
            "object_id": object_id,
            "config": {"socketPath": call_socket_path(kind, object_id)},
        }
    }


def ice_servers_payload():
    """The ICE configuration for one call, freshly minted, or None."""
    ice_servers = generate_ice_servers()
    if ice_servers is None:
        if not settings.DEBUG:
            return None
        return {"iceServers": [], "iceTransportPolicy": "all"}
    return {"iceServers": ice_servers, "iceTransportPolicy": ice_transport_policy()}
