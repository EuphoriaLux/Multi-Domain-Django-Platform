"""
File uploads must bypass the service worker's background-sync route.

Production, 2026-09-28 to 2026-10-08: iPhone members (Safari 26.6 and Chrome
on iOS) submitting the profile wizard got a CSRF 403. The failure view logged

    content_type=multipart/form-data; boundary=----WebKitFormBoundary...
    content_length=0

on every one: the whole multipart body, CSRF token included, was empty. The
service worker's background-sync route claimed the native form POST, and its
NetworkOnly strategy clones every request it sends (Workbox does so whenever a
plugin has ``fetchDidFail``). On iOS WebKit a multipart body carrying a file
did not survive that re-send. Each member got through on a later try, once the
file input had been emptied.

These run the real routing table through the Node probe (see
sw_route_probe.js) rather than grepping the source.
"""

import pytest

from crush_lu.tests.test_sw_admin_routes import _run_sw_route_probe


@pytest.mark.parametrize(
    "probe_name",
    ["profile_multipart_post_navigation", "gift_multipart_post_navigation"],
)
def test_multipart_uploads_are_left_to_the_browser(probe_name):
    probe = _run_sw_route_probe()[probe_name]
    assert not probe["claimed"], (
        f"the service worker claims multipart POST {probe['url']} "
        f"(route={probe['matchedRoute']}). Its cloned re-send reached the server "
        "with an empty body on iOS WebKit, so Django found no CSRF token."
    )


@pytest.mark.parametrize(
    "probe_name", ["urlencoded_form_post_navigation", "ordinary_form_post"]
)
def test_plain_form_posts_keep_background_sync(probe_name):
    probe = _run_sw_route_probe()[probe_name]
    assert probe["claimed"] and probe["matchedRoute"] == "NetworkOnly", (
        f"POST {probe['url']} is no longer queued (route={probe['matchedRoute']}); "
        "only file uploads were meant to leave the background-sync route."
    )
