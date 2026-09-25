"""
The event ticket must open offline at the venue door (UX review 4-02).

The ticket page used to fall into the service worker's generic "crush-pages"
NetworkFirst cache — shared with every page, capped at 50 entries and 24 hours
— so by event night the copy was usually gone, and when it was not, the QR in
it was drawn by a jsDelivr script the worker never cached: a blank box. The QR
is now server-rendered SVG inside the HTML, and ticket navigations get their
own cache that survives from booking to event night.

That cache holds a signed check-in URL, and the ticket URL is keyed by event,
not by user: without a purge, the next account signed in on the same device
would be shown the previous account's QR when offline. Session-boundary
navigations (sign-in, sign-out, sign-up) therefore delete it.

These run the real routing table through the Node probe (sw_route_probe.js),
for the reason given in test_mobile_auth_handoff_chain.py: a source-string
assertion passes just as happily when the route it checks is not the one that
matches.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

TICKET_CACHE = "crush-tickets"


def _run_sw_route_probe():
    """Shared harness with test_sw_admin_routes.py; see sw_route_probe.js."""
    import crush_lu

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available to run the service-worker probe")

    package_dir = Path(crush_lu.__file__).parent
    result = subprocess.run(
        [
            node,
            str(package_dir / "tests" / "sw_route_probe.js"),
            str(package_dir / "static" / "crush_lu" / "sw-workbox.js"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return {r["name"]: r for r in json.loads(result.stdout)["results"]}


@pytest.mark.parametrize(
    "probe_name",
    ["ticket_navigation_en", "ticket_navigation_de", "ticket_navigation_fr"],
)
def test_ticket_navigations_use_their_own_network_first_cache(probe_name):
    probe = _run_sw_route_probe()[probe_name]
    assert probe["matchedRoute"] == "NetworkFirst", probe
    assert probe["matchedCacheName"] == TICKET_CACHE, (
        f"{probe['url']} is cached in {probe['matchedCacheName']!r}, not "
        f"{TICKET_CACHE!r}. The generic page cache evicts after 24h / 50 "
        "entries, so the ticket is gone by the time it is needed at the door."
    )


def test_ticket_route_does_not_swallow_other_event_pages():
    """Guards the pattern: the event page itself stays in the generic cache."""
    probe = _run_sw_route_probe()["event_detail_navigation"]
    assert probe["matchedRoute"] == "NetworkFirst"
    assert probe["matchedCacheName"] == "crush-pages"


@pytest.mark.parametrize("probe_name", ["logout_navigation", "login_navigation"])
def test_session_boundaries_purge_offline_tickets(probe_name):
    probe = _run_sw_route_probe()[probe_name]
    assert TICKET_CACHE in probe["purgedCaches"], (
        f"navigating to {probe['url']} leaves {TICKET_CACHE!r} in place, so the "
        "next account on this device is shown the previous account's QR offline."
    )


def test_ordinary_navigation_keeps_offline_tickets():
    """Guards the purge: browsing must not throw the offline ticket away."""
    results = _run_sw_route_probe()
    for name in ("ordinary_page_navigation", "ticket_navigation_en"):
        assert results[name]["purgedCaches"] == [], results[name]
