"""Browser check for the desktop -> mobile notification-bell bridge (8-03).

``test_shell_toggle_bell.py`` pins the server side: the mobile top bar is
seeded with the unread ``Notification`` count and both bells carry the wiring
attributes. What only a browser can prove is the runtime bridge in
``alpine-components.js``: the desktop ``notificationBell`` re-reads
``/api/notifications/`` and broadcasts each new ``unreadCount`` as a window
``notif-unread-count`` event, and ``topBarMobile.syncNotificationCount``
moves the mobile badge and the bell's aria-label with it. A source-level
string check stays green when either side is broken; this does not.

Run with:
    pytest crush_lu/tests/test_shell_toggle_bell_playwright.py -m playwright
"""

import pytest

# Skip all tests if playwright is not installed
pytest.importorskip("playwright")

from django.conf import settings  # noqa: E402
from django.test import Client  # noqa: E402
from django.utils import timezone  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

MOBILE_VIEWPORT = {"width": 390, "height": 844}

# Fraunces comes from Google Fonts behind a fallback stack; serve an empty
# stylesheet so an unreachable host can't stall `networkidle`
# (see test_visual_regression.block_optional_external_resources).
OPTIONAL_EXTERNAL_RESOURCES = (
    "**://fonts.googleapis.com/**",
    "**://fonts.gstatic.com/**",
)

DESKTOP_BELL = "[x-data='notificationBell']"


def _bell_member():
    """A member with 2 unread notifications.

    Built inside the test body, not in a fixture: the module-scoped
    ``_restore_migration_seeded_rows`` snapshot is appended to the item's
    fixtures at collection time, so it sets up *after* the test's own
    function fixtures and would snapshot (then fail to replay) their rows.
    """
    from crush_lu.models import Notification
    from crush_lu.tests.test_profile_edit_connect_card import _make_member

    user = _make_member("bell-e2e@example.com")
    for title in ("Ticket ready", "Profile approved"):
        Notification.objects.create(user=user, notification_type="test", title=title)
    return user


def _log_in(page, live_server, user):
    client = Client()
    client.force_login(user)
    page.context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            }
        ]
    )


def _refresh_desktop_bell(page):
    """Make the desktop bell re-read /api/notifications/ (as on open)."""
    page.evaluate(
        "sel => Alpine.$data(document.querySelector(sel)).refresh()", DESKTOP_BELL
    )


def test_mobile_badge_follows_the_desktop_bell(page, live_server):
    from crush_lu.models import Notification

    bell_member = _bell_member()
    for pattern in OPTIONAL_EXTERNAL_RESOURCES:
        page.route(
            pattern,
            lambda route: route.fulfill(status=200, content_type="text/css", body=""),
        )
    _log_in(page, live_server, bell_member)
    page.set_viewport_size(MOBILE_VIEWPORT)
    response = page.goto(f"{live_server.url}/en/dashboard/")
    assert response is not None and response.ok, response and response.status
    # Let the desktop bell's on-mount /api/notifications/ fetch settle so it
    # can't land after (and overwrite) the counts driven below.
    page.wait_for_load_state("networkidle")

    bell = page.locator("a.top-bar-mobile-bell")
    badge = bell.locator(".top-bar-mobile-bell-badge")
    expect(bell).to_have_attribute("href", "/en/notifications/")
    expect(badge).to_have_text("2")
    expect(bell).to_have_attribute("aria-label", "Unread notifications: 2")

    page.evaluate("""() => {
            window.__bellEvents = [];
            window.addEventListener("notif-unread-count", e => {
                window.__bellEvents.push(e.detail);
            });
        }""")

    # 12 unread -> the desktop bell learns it from the API; the mobile badge
    # caps at 9+ like the desktop one, the label speaks the full count.
    for index in range(10):
        Notification.objects.create(
            user=bell_member, notification_type="test", title=f"New {index}"
        )
    _refresh_desktop_bell(page)
    expect(badge).to_have_text("9+")
    expect(bell).to_have_attribute("aria-label", "Unread notifications: 12")

    # Everything read -> badge hidden, plain label.
    Notification.objects.filter(user=bell_member).update(read_at=timezone.now())
    _refresh_desktop_bell(page)
    expect(badge).to_be_hidden()
    expect(bell).to_have_attribute("aria-label", "Notifications")

    Notification.objects.create(user=bell_member, notification_type="test", title="x")
    _refresh_desktop_bell(page)
    expect(badge).to_have_text("1")
    expect(bell).to_have_attribute("aria-label", "Unread notifications: 1")

    # One broadcast per change: the desktop bell's init runs twice (x-data
    # auto-init + x-init), and the watcher must still be wired only once.
    assert page.evaluate("() => window.__bellEvents") == [12, 0, 1]
