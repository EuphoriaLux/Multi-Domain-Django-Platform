"""
The service worker must keep the admin out of its queue and out of its cache.

Staging, 2026-08-11: saving MeetupEvent #29 in the coach panel answered 500 with

    IntegrityError: duplicate key value violates unique constraint
    "crush_lu_eventregistration_event_id_user_id_52c32bb9_uniq"
    DETAIL: Key (event_id, user_id)=(29, 86) already exists.

The change form's registration inline was inserted twice. Django's formset
validation catches a duplicate that is already committed, so the two writes had
to overlap — and the service worker had two ways to produce that overlap, both
because every exclusion list was written against `/admin/` while the coach panel
is mounted at `/crush-admin/` (azureproject/urls_crush.py):

  * admin POSTs went through BackgroundSyncPlugin, which replays a failed
    request verbatim for up to 24h — re-submitting the whole change form,
    inline rows included;
  * admin GETs were NetworkFirst, so a change form could be served from cache
    with stale inline ids and INITIAL_FORMS.

These run the real routing table through the Node probe rather than grepping the
source, for the reason given in test_mobile_auth_handoff_chain.py: a
source-string assertion passes just as happily when the route it checks is not
the route that matters.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def _run_sw_route_probe():
    """Shared harness with test_mobile_auth_handoff_chain.py; see sw_route_probe.js."""
    import crush_lu

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available to run the service-worker probe")

    package_dir = Path(crush_lu.__file__).parent
    probe = package_dir / "tests" / "sw_route_probe.js"
    sw = package_dir / "static" / "crush_lu" / "sw-workbox.js"

    result = subprocess.run(
        [node, str(probe), str(sw)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return {r["name"]: r for r in json.loads(result.stdout)["results"]}


def test_admin_post_is_never_queued_for_background_sync():
    probe = _run_sw_route_probe()["crush_admin_form_post"]
    assert not probe["claimed"], (
        f"the service worker claims POST {probe['url']} "
        f"(route={probe['matchedRoute']}). BackgroundSyncPlugin replays queued "
        "POSTs for up to 24h, so the admin change form — inline rows and all — "
        "gets submitted a second time and duplicates whatever it already wrote."
    )


def test_admin_pages_are_never_cached():
    probe = _run_sw_route_probe()["crush_admin_page_navigation"]
    assert probe["matchedRoute"] == "NetworkOnly", (
        f"admin navigations resolve to {probe['matchedRoute']}, not NetworkOnly. "
        "A change form served from cache carries stale inline ids, and re-posting "
        "those rows as new ones trips the (event, user) unique index."
    )


def test_ordinary_form_posts_keep_background_sync():
    """Guards the two assertions above: if the POST router stopped matching
    anything at all they would pass vacuously, and offline form submission —
    the reason the queue exists — would be silently gone."""
    probe = _run_sw_route_probe()["ordinary_form_post"]
    assert probe["claimed"], (
        f"POST {probe['url']} is no longer claimed by the background-sync route; "
        "offline form submission has been lost."
    )
