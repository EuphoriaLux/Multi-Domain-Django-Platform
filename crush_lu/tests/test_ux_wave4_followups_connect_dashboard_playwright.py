"""Browser checks for UX Wave 4 · WP13a (decision I, #1083):

- the Connect chat's block confirmation sheet shows an optional, unticked
  "Also let a Crush Coach know" checkbox, and ticking it files the chat
  block's existing ConnectReport escalation;
- the dashboard install card is the prompts store's "install" prompt and
  never shows on a first visit.

Run with:
    pytest crush_lu/tests/test_ux_wave4_followups_connect_dashboard_playwright.py -m playwright
"""

import time

import pytest

pytest.importorskip("playwright")

from playwright.sync_api import expect  # noqa: E402

from crush_lu.models.crush_connect_cycle import (  # noqa: E402
    ConnectReport,
    ConnectTemporaryChat,
)
from crush_lu.tests.test_connect_chat_flows import _make_open_chat  # noqa: E402
from crush_lu.tests.test_connect_review_choice_playwright import (  # noqa: E402
    _log_in,
)
from crush_lu.tests.test_crush_connect import _grant_consent  # noqa: E402
from crush_lu.tests.test_ux_wave3_dashboard import _make_member  # noqa: E402

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

MOBILE_VIEWPORT = {"width": 390, "height": 844}
IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


def _open_block_sheet(page, live_server, chat):
    response = page.goto(f"{live_server.url}/en/crush-connect/week/chats/{chat.pk}/")
    assert response is not None and response.ok
    page.locator("[data-safety-menu] summary").click()
    page.get_by_role("button", name="Just block this member").click()
    sheet = page.locator("#crush-confirm-dialog")
    expect(sheet).to_be_visible()
    return sheet


@pytest.mark.parametrize("tick", [True, False])
def test_block_sheet_coach_checkbox(page, live_server, tick):
    me, target, chat = _make_open_chat()
    _grant_consent(me)
    _log_in(page, live_server, me)
    page.set_viewport_size(MOBILE_VIEWPORT)

    sheet = _open_block_sheet(page, live_server, chat)
    box = sheet.get_by_role("checkbox", name="Also let a Crush Coach know")
    expect(box).to_be_visible()
    expect(box).not_to_be_checked()
    if tick:
        box.check()
    sheet.get_by_role("button", name="Yes, block").click()
    page.wait_for_url(f"{live_server.url}/en/crush-connect/week/chats/")

    chat.refresh_from_db()
    assert chat.status == ConnectTemporaryChat.Status.BLOCKED
    reports = ConnectReport.objects.filter(reporter=me, reported_user=target)
    assert reports.count() == (1 if tick else 0)


def test_other_confirmations_do_not_show_the_checkbox(page, live_server):
    me, _target, chat = _make_open_chat()
    _grant_consent(me)
    _log_in(page, live_server, me)
    page.set_viewport_size(MOBILE_VIEWPORT)

    sheet = _open_block_sheet(page, live_server, chat)
    sheet.get_by_role("button", name="Cancel").click()
    expect(sheet).not_to_be_visible()
    # A plain crushConfirm() call (no option) must not leave the box behind.
    page.evaluate("() => { window.crushConfirm('Sure?'); }")
    expect(sheet).to_be_visible()
    expect(sheet.locator("[data-confirm-option]")).to_be_hidden()


def _dashboard(page, live_server):
    response = page.goto(f"{live_server.url}/en/dashboard/")
    assert response is not None and response.ok
    page.wait_for_load_state("load")
    page.wait_for_function("() => Alpine.store('prompts').ready")


def test_dashboard_install_card_is_queued_and_skips_the_first_visit(
    browser, live_server
):
    context = browser.new_context(user_agent=IPHONE_UA, viewport=MOBILE_VIEWPORT)
    page = context.new_page()
    me = _make_member("wp13a-pw@example.com", verified=True)
    _log_in(page, live_server, me)

    # First visit: pwa-install.js counts session 1, so no install prompt.
    _dashboard(page, live_server)
    card = page.locator('[x-show="showInstructionsVisible"]')
    expect(card).to_be_hidden()
    assert page.evaluate("Alpine.store('prompts').active") is None
    assert page.locator("#pwa-install-banner").count() == 0

    # A later session (last page view > 30 min ago) is a returning visit.
    stale = int((time.time() - 31 * 60) * 1000)
    page.evaluate(
        f"localStorage.setItem('crush-pwa-last-seen', '{stale}');"
        "localStorage.setItem('crush-pwa-sessions', '1');"
    )
    _dashboard(page, live_server)
    expect(card).to_be_visible()
    assert page.evaluate("Alpine.store('prompts').active") == "install"
    # The global banner never renders on the dashboard: one install offer.
    assert page.locator("#pwa-install-banner").count() == 0
    context.close()
