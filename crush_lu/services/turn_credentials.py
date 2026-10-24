"""Short-lived TURN relay credentials for in-app video calls.

Cloudflare Realtime TURN relays the calls whose browsers cannot reach each
other directly (and, under the "relay" ICE policy, every call). Django mints
the credentials per page so the TURN key's API token stays server-side; the
browser only ever sees a username/credential pair that expires on its own.

Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md (D12)
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

GENERATE_ICE_SERVERS_URL = (
    "https://rtc.live.cloudflare.com/v1/turn/keys/{key_id}"
    "/credentials/generate-ice-servers"
)
REQUEST_TIMEOUT_SECONDS = 5


def turn_configured() -> bool:
    return bool(
        getattr(settings, "CLOUDFLARE_TURN_KEY_ID", "")
        and getattr(settings, "CLOUDFLARE_TURN_KEY_API_TOKEN", "")
    )


def _browser_urls(urls):
    """Drop port 53: browsers block it, so such a URL only adds a timeout."""
    if isinstance(urls, str):
        urls = [urls]
    return [
        url
        for url in urls
        if isinstance(url, str) and ":53?" not in url and not url.endswith(":53")
    ]


def _clean_servers(servers):
    if isinstance(servers, dict):
        servers = [servers]
    cleaned = []
    for server in servers:
        urls = _browser_urls(server.get("urls", []))
        if not urls:
            continue
        entry = {"urls": urls}
        if server.get("username") and server.get("credential"):
            entry["username"] = server["username"]
            entry["credential"] = server["credential"]
        cleaned.append(entry)
    return cleaned


def generate_ice_servers(ttl_seconds=None):
    """Return the ``iceServers`` list for ``RTCPeerConnection``, or None.

    None means TURN is not configured or Cloudflare did not answer. Callers
    then show video as unavailable rather than start a call that cannot
    connect.
    """
    if not turn_configured():
        return None
    ttl = ttl_seconds or settings.VIDEO_CALL_TURN_TTL_SECONDS
    try:
        response = requests.post(
            GENERATE_ICE_SERVERS_URL.format(key_id=settings.CLOUDFLARE_TURN_KEY_ID),
            headers={
                "Authorization": f"Bearer {settings.CLOUDFLARE_TURN_KEY_API_TOKEN}"
            },
            json={"ttl": ttl},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        servers = _clean_servers(response.json()["iceServers"])
    except (
        requests.RequestException,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
    ) as exc:
        # Log the failure class and status only: the request carries the API
        # token and the response the minted credentials.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        logger.warning(
            "TURN credentials unavailable (%s, status %s)", type(exc).__name__, status
        )
        return None
    if not any("username" in server for server in servers):
        logger.warning("TURN credentials unavailable (no relay server returned)")
        return None
    return servers
