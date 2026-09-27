"""
Playwright: account-exit confirmations and the Unblock undo toast (UX Wave 2 WP7).

- A plain POST form carrying ``data-confirm`` asks through the global confirm
  sheet (confirm-sheet.js) before submitting; Cancel submits nothing.
- Unblocking raises a toast whose Undo button re-blocks the member.

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_account_exit_playwright.py -n 0 --create-db
"""

import json
import re
from datetime import date

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

PHONE = {"width": 390, "height": 844}
OVERLAY_SHOWN = re.compile(r"\bshow\b")


def _member(username, first_name):
    from allauth.account.models import EmailAddress
    from django.contrib.auth import get_user_model

    from crush_lu.models import CrushProfile, UserDataConsent

    user = get_user_model().objects.create_user(
        username=username,
        email=username,
        password="Exit-pass-2026!",
        first_name=first_name,
    )
    EmailAddress.objects.create(
        user=user, email=user.email, verified=True, primary=True
    )
    UserDataConsent.objects.update_or_create(
        user=user,
        defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
    )
    CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1995, 5, 15),
        gender="F",
        location="Luxembourg City",
        is_approved=True,
        is_active=True,
    )
    return user


def _log_in(page, live_server_url, user):
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


def test_unblock_confirms_then_undo_reblocks(page, live_server):
    from crush_lu.models import UserBlock

    me = _member("exit.me@example.com", "Lena")
    target = _member("exit.target@example.com", "Marc")
    UserBlock.objects.create(blocker=me, blocked=target, reason="harassment")

    page.set_viewport_size(PHONE)
    _log_in(page, live_server.url, me)
    page.goto(f"{live_server.url}/en/settings/blocked/")

    dialog = page.locator("#crush-confirm-dialog")
    unblock = page.get_by_role("button", name="Unblock Marc")
    box = unblock.bounding_box()
    assert box["height"] >= 44

    # Cancel keeps the block.
    unblock.click()
    expect(dialog).to_be_visible()
    expect(dialog.locator("[data-confirm-message]")).to_contain_text("Unblock Marc?")
    dialog.locator("[data-confirm-cancel]").click()
    expect(dialog).to_be_hidden()
    # The navigation overlay must not come up for the held-back submit.
    page.wait_for_timeout(800)
    expect(page.locator("#page-loading-overlay")).not_to_have_class(OVERLAY_SHOWN)
    assert UserBlock.objects.filter(blocker=me, blocked=target).exists()

    # Confirm unblocks and offers Undo.
    unblock.click()
    with page.expect_navigation():
        dialog.locator("[data-confirm-accept]").click()
    assert not UserBlock.objects.filter(blocker=me, blocked=target).exists()
    toast = page.locator("#toast-container [role=alert]")
    expect(toast).to_contain_text("You unblocked Marc.")
    # Outlives the 5s default: the only Undo must not time out (WCAG 2.2.1).
    page.wait_for_timeout(5600)
    expect(toast).to_be_visible()

    with page.expect_navigation():
        toast.get_by_role("button", name="Undo").click()
    assert UserBlock.objects.filter(
        blocker=me, blocked=target, reason="harassment"
    ).exists()
    expect(page.get_by_role("button", name="Unblock Marc")).to_be_visible()


def test_gdpr_profile_deletion_asks_through_confirm_sheet(page, live_server):
    from crush_lu.models import CrushProfile

    me = _member("exit.gdpr@example.com", "Lena")
    page.set_viewport_size(PHONE)
    _log_in(page, live_server.url, me)
    page.goto(f"{live_server.url}/en/account/gdpr/")

    page.get_by_label("Type your email address to confirm").first.fill(me.email)
    page.get_by_role("button", name="Delete Crush.lu Profile Only").click()

    dialog = page.locator("#crush-confirm-dialog")
    expect(dialog).to_be_visible()
    expect(dialog.locator("[data-confirm-accept]")).to_have_text("Delete profile")
    dialog.locator("[data-confirm-cancel]").click()
    expect(dialog).to_be_hidden()
    # The navigation overlay must not come up for the held-back submit.
    page.wait_for_timeout(800)
    expect(page.locator("#page-loading-overlay")).not_to_have_class(OVERLAY_SHOWN)
    assert page.url.endswith("/en/account/gdpr/")
    assert CrushProfile.objects.filter(user=me).exists()
