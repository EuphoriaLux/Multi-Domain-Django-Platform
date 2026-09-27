"""Browser check for the Connect Week review card's "Choose" confirmation
(UX Wave 3 · WP12 / 6-06).

A source-level string check can't prove the native ``<dialog>`` actually
opens/closes and that "Confirm and send request" really submits the form —
only a browser can. Follows ``test_shell_toggle_bell_playwright.py``'s
session-cookie login pattern.

Run with:
    pytest crush_lu/tests/test_connect_review_choice_playwright.py -m playwright
"""

import pytest

pytest.importorskip("playwright")

from django.conf import settings  # noqa: E402
from django.test import Client  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

from crush_lu.models.crush_connect_cycle import ConnectWeeklyRequest  # noqa: E402
from crush_lu.tests.test_connect_week_experience import (  # noqa: E402
    _answer_all,
    _make_cycle_user,
    _reviewable_session_with_card,
)

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

MOBILE_VIEWPORT = {"width": 390, "height": 844}


def _log_in(page, live_server, user):
    client = Client()
    client.force_login(user)
    page.context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            },
            # Suppress the cookie-consent banner: a bare "decline" flag for
            # both optional groups needs no version stamp
            # (azureproject.templatetags.analytics.stored_cookie_choice
            # returns False immediately for FLAG_DECLINE), unlike "accept"
            # which is version-checked against the rendered page.
            {
                "name": "cookie_consent_analytics",
                "value": "decline",
                "url": live_server.url,
            },
            {
                "name": "cookie_consent_marketing",
                "value": "decline",
                "url": live_server.url,
            },
        ]
    )


def test_choose_button_opens_dialog_and_confirm_sends_the_request(page, live_server):
    me = _make_cycle_user("rv_pw_me")
    target = _make_cycle_user("rv_pw_target")
    _, card = _reviewable_session_with_card(me, target)
    _answer_all(card)
    from crush_lu.tests.test_crush_connect import _grant_consent

    _grant_consent(me)
    _log_in(page, live_server, me)
    page.set_viewport_size(MOBILE_VIEWPORT)

    response = page.goto(f"{live_server.url}/en/crush-connect/week/review/")
    assert response is not None and response.ok

    dialog = page.locator(".connect-review-confirm-dialog")
    expect(dialog).not_to_be_visible()

    page.get_by_role("button", name="Choose Rv_Pw_Target").click()
    expect(dialog).to_be_visible()

    page.get_by_role("button", name="Confirm and send request").click()

    expect(page).to_have_url(f"{live_server.url}/en/crush-connect/week/review/")
    assert ConnectWeeklyRequest.objects.filter(requester=me, recipient=target).exists()
