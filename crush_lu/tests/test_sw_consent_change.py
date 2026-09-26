"""
A cookie-consent change must not be undone by the service worker (8-06).

The worker keeps navigations as full HTML: "crush-pages" (every page, a day)
and "crush-tickets" (event tickets, a year). A kept page embeds the consent
state it was rendered with and the trackers that state allowed, so after a
withdrawal a copy served offline could track and re-grant (Codex P1 on #1028).
The pages themselves now hold back their trackers once the visitor refuses
(the analytics tags and the banner check the live consent flags,
test_cookie_banner.py); on top of that the worker drops "crush-pages" when a
choice is saved to django-cookie-consent's /cookies/accept|decline/ views,
and keeps the offline tickets, whose QR is needed at the door.

Those POSTs are also kept off the background-sync queue: a replay up to 24h
later reaches CookieConsentFlagSyncMiddleware, which would rewrite the
consent flags from a stale choice over a newer one.

These run the real routing table through the Node probe (sw_route_probe.js),
for the reason given in test_mobile_auth_handoff_chain.py.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE_CACHE = "crush-pages"
TICKET_CACHE = "crush-tickets"
CONSENT_POSTS = [
    "cookie_consent_accept_post_navigation",
    "cookie_consent_decline_fetch",
]


def _run_probe():
    """Shared harness with test_sw_ticket_offline.py; see sw_route_probe.js."""
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
    return json.loads(result.stdout)


def _results():
    return {r["name"]: r for r in _run_probe()["results"]}


@pytest.mark.parametrize("probe_name", CONSENT_POSTS)
def test_a_consent_change_purges_the_kept_pages(probe_name):
    probe = _results()[probe_name]
    assert PAGE_CACHE in probe["purgedCaches"], (
        f"POST {probe['url']} leaves {PAGE_CACHE!r} in place: a page kept under "
        "the old choice can still be served offline with its trackers."
    )


@pytest.mark.parametrize("probe_name", CONSENT_POSTS)
def test_a_consent_change_keeps_the_offline_tickets(probe_name):
    probe = _results()[probe_name]
    assert TICKET_CACHE not in probe["purgedCaches"], (
        f"POST {probe['url']} drops {TICKET_CACHE!r}: the offline QR would be "
        "gone at the door after a mere preference change."
    )


@pytest.mark.parametrize("probe_name", CONSENT_POSTS)
def test_a_consent_post_is_never_queued_for_background_sync(probe_name):
    probe = _results()[probe_name]
    assert not probe["claimed"], (
        f"the service worker claims POST {probe['url']} "
        f"(route={probe['matchedRoute']}). The background-sync route replays a "
        "failed POST for up to 24h, and the replayed choice would rewrite the "
        "consent flags over a newer one."
    )


def test_already_queued_consent_posts_are_dropped_not_replayed():
    """The queue is a named IndexedDB store that survives a worker update: a
    consent POST an older worker stored must be discarded on the next drain."""
    replay = _run_probe()["replay"]
    assert replay["available"], "the probe could not reach the background-sync queue"
    assert not [url for url in replay["replayed"] if "/cookies/" in url]
    assert replay["drained"], "a skipped entry must be shifted out of the queue"
    # Guards the assertion above: ordinary POSTs still replay.
    assert any("/events/" in url for url in replay["replayed"])


def test_other_requests_keep_the_kept_pages():
    """Guards the purge: only a saved consent choice drops the page cache."""
    results = _results()
    for name in (
        "cookie_status_fetch",  # the banner's CSRF/status lookup
        "ordinary_form_post",
        "ordinary_page_navigation",
        "event_detail_navigation",
    ):
        assert PAGE_CACHE not in results[name]["purgedCaches"], results[name]
