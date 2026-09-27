"""
Playwright: blocking from a Connect chat asks for confirmation first.

The chat block closes the chat and excludes the pair from Connect Week
matching for good, so "Just block this member" opens the branded confirm
sheet (confirm-sheet.js, ``form[data-confirm]``) instead of posting at once.
Cancel keeps the chat open; accept blocks.

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_connect_chat_block_confirm_playwright.py -n 0 --create-db
"""

import json

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]


def _log_in(page, live_server_url, user):
    """Reuse a Django session instead of driving the login form."""
    from django.test import Client

    client = Client()
    client.force_login(user)
    consent = json.dumps({"essential": True, "analytics": False, "marketing": False})
    page.context.add_cookies(
        [
            {
                "name": "sessionid",
                "value": client.cookies["sessionid"].value,
                "url": live_server_url,
            },
            {"name": "cookie_consent", "value": consent, "url": live_server_url},
        ]
    )


def test_chat_block_confirm_cancel_keeps_chat_and_accept_blocks(page, live_server):
    from django.core.cache import cache

    from crush_lu.models.crush_connect_cycle import (
        ConnectPairExclusion,
        ConnectTemporaryChat,
    )
    from crush_lu.tests.test_connect_chat_flows import _make_open_chat
    from crush_lu.tests.test_crush_connect import _grant_consent

    cache.clear()
    me, target, chat = _make_open_chat()
    _grant_consent(me)
    _log_in(page, live_server.url, me)
    page.goto(f"{live_server.url}/en/crush-connect/week/chats/{chat.pk}/")

    dialog = page.locator("#crush-confirm-dialog")
    block = page.get_by_role("button", name="Just block this member")

    # Cancel: nothing posted, chat stays open.
    page.locator("[data-safety-menu] > summary").click()
    block.click()
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("can't be undone")
    expect(dialog).to_contain_text("closes the chat")
    dialog.locator("[data-confirm-cancel]").click()
    expect(dialog).to_be_hidden()
    # page-loading.js must not strand its overlay over the page on cancel:
    # wait out its 500 ms show delay, then the block button must still take
    # a click (a shown overlay would intercept it).
    page.wait_for_timeout(800)
    expect(page.locator("#page-loading-overlay")).not_to_be_visible()
    block.click(trial=True, timeout=1000)
    chat.refresh_from_db()
    assert chat.status == ConnectTemporaryChat.Status.ACTIVE
    assert not ConnectPairExclusion.are_excluded(me, target)

    # Accept: the block posts and the chat closes.
    block.click()
    expect(dialog).to_be_visible()
    accept = dialog.locator("[data-confirm-accept]")
    expect(accept).to_have_text("Yes, block")
    with page.expect_navigation():
        accept.click()
    chat.refresh_from_db()
    assert chat.status == ConnectTemporaryChat.Status.BLOCKED
    assert ConnectPairExclusion.are_excluded(me, target)
