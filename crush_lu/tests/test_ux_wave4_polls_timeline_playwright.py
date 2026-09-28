"""Playwright: JS behaviour changed for UX Wave 4 WP4 (polls + timeline).

- 7-08/7-09: a poll is votable by keyboard alone; the vote goes out via fetch
  and the results swap in place with a toast (no alert(), no reload); a 429
  and a non-JSON answer show an inline error and re-enable the button.
- 7-16: the timeline challenge reorders with Move up/down buttons, keeps focus
  on the moved item and announces its new position politely.

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_ux_wave4_polls_timeline_playwright.py -n 0 --create-db
"""

import json

import pytest
from django.conf import settings
from django.test import Client
from playwright.sync_api import expect

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

PHONE = {"width": 390, "height": 844}


def _add_session(page, live_server, client):
    # A decided cookie_consent keeps the consent banner from covering buttons.
    consent = json.dumps({"essential": True, "analytics": False, "marketing": False})
    page.context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            },
            {"name": "cookie_consent", "value": consent, "url": live_server.url},
        ]
    )


def _log_in(page, live_server, user):
    client = Client()
    client.force_login(user)
    _add_session(page, live_server, client)


def _no_dialogs(page):
    dialogs = []

    def record(dialog):
        dialogs.append(dialog.message)
        dialog.dismiss()

    page.on("dialog", record)
    return dialogs


@pytest.fixture
def poll_page(page, live_server, transactional_db):
    from crush_lu.tests.test_ux_wave4_polls_timeline import make_member, make_poll

    user = make_member()
    poll = make_poll()
    _log_in(page, live_server, user)
    page.set_viewport_size(PHONE)
    page.goto(f"{live_server.url}/en/polls/{poll.id}/")
    page.wait_for_load_state("load")
    # Survives only if the page is never reloaded.
    page.evaluate("window.__noReload = true")
    return page, poll, user


def test_keyboard_vote_swaps_results_in_place_with_a_toast(poll_page):
    page, poll, user = poll_page
    dialogs = _no_dialogs(page)
    submit = page.get_by_role("button", name="Submit Vote")
    expect(submit).to_be_disabled()
    header_count = page.locator("[data-poll-total-votes]")
    expect(header_count).to_have_text("0")

    first = page.get_by_role("radio", name="Wine Night")
    first.focus()
    page.keyboard.press("Space")
    expect(first).to_be_checked()
    # Arrow keys move the single choice, as with any native radio group.
    page.keyboard.press("ArrowDown")
    expect(page.get_by_role("radio", name="Board Games")).to_be_checked()
    expect(submit).to_be_enabled()

    submit.focus()
    page.keyboard.press("Enter")

    expect(page.locator("#toast-container")).to_contain_text("Thanks, your vote is in!")
    expect(page.get_by_text("Your vote has been recorded. Thank you!")).to_be_visible()
    expect(page.get_by_role("img", name="Board Games: 100%, 1 vote")).to_be_visible()
    # The header count follows the same response as the results.
    expect(header_count).to_have_text("1")
    expect(page.get_by_role("radio")).to_have_count(0)
    assert page.evaluate("window.__noReload") is True
    # The page-loading overlay must not cover a submit that never navigates.
    page.wait_for_timeout(1000)
    expect(page.locator("#page-loading-overlay")).to_be_hidden()
    assert dialogs == []

    from crush_lu.models.event_polls import EventPollVote

    assert list(
        EventPollVote.objects.filter(user=user).values_list("option__name", flat=True)
    ) == ["Board Games"]


def test_rate_limited_vote_shows_inline_message_without_reload(poll_page):
    page, poll, _user = poll_page
    dialogs = _no_dialogs(page)
    page.route(
        f"**/api/polls/{poll.id}/vote/",
        lambda route: route.fulfill(
            status=429,
            content_type="application/json",
            body=json.dumps({"error": "x", "error_code": "rate_limited"}),
        ),
    )
    page.get_by_role("radio", name="Wine Night").check()
    submit = page.get_by_role("button", name="Submit Vote")
    submit.click()

    alert = page.get_by_role("alert").filter(has_text="Too many attempts")
    expect(alert).to_have_text(
        "Too many attempts. Take a breath and try again in a minute."
    )
    expect(submit).to_be_enabled()
    expect(page.get_by_role("radio", name="Wine Night")).to_be_checked()
    assert page.evaluate("window.__noReload") is True
    assert dialogs == []

    # Changing the choice clears the stale error.
    page.get_by_role("radio", name="Board Games").check()
    expect(page.locator("p[role=alert][x-text=errorMessage]")).to_have_text("")


def test_html_error_page_is_reported_inline_not_as_network_error(poll_page):
    page, poll, _user = poll_page
    dialogs = _no_dialogs(page)
    page.route(
        f"**/api/polls/{poll.id}/vote/",
        lambda route: route.fulfill(
            status=403, content_type="text/html", body="<h1>CSRF failed</h1>"
        ),
    )
    page.get_by_role("radio", name="Wine Night").check()
    page.get_by_role("button", name="Submit Vote").click()

    expect(page.locator("p[role=alert][x-text=errorMessage]")).to_have_text(
        "Failed to submit vote"
    )
    assert page.evaluate("window.__noReload") is True
    assert dialogs == []


@pytest.fixture
def timeline_page(page, live_server, transactional_db):
    from crush_lu.tests.test_ux_wave4_polls_timeline import TimelineMoveButtonsTests

    case = TimelineMoveButtonsTests()
    case.client = Client()
    case.setUp()
    _add_session(page, live_server, case.client)
    page.set_viewport_size(PHONE)
    page.goto(f"{live_server.url}/en/journey/chapter/1/challenge/{case.challenge.id}/")
    page.wait_for_load_state("load")
    expect(page.locator(".timeline-item")).to_have_count(3)
    return page


def _order(page):
    return page.locator(".timeline-item .timeline-text").all_inner_texts()


def test_move_buttons_reorder_keep_focus_and_announce(timeline_page):
    page = timeline_page
    order = _order(page)
    first, second = order[0], order[1]

    # Edge buttons are disabled.
    expect(page.get_by_role("button", name=f"Move “{first}” up")).to_be_disabled()
    expect(page.get_by_role("button", name=f"Move “{order[2]}” down")).to_be_disabled()

    down = page.get_by_role("button", name=f"Move “{first}” down")
    down.focus()
    page.keyboard.press("Enter")

    assert _order(page) == [second, first, order[2]]
    numbers = page.locator(".timeline-item .timeline-number").all_inner_texts()
    assert numbers == ["1", "2", "3"]
    announcer = page.locator("p[aria-live=polite]")
    expect(announcer).to_have_text(f"{first} moved to position 2 of 3")
    expect(down).to_be_focused()

    # Moving to the last slot disables "down": focus falls back to "up".
    page.keyboard.press("Enter")
    assert _order(page) == [second, order[2], first]
    expect(announcer).to_have_text(f"{first} moved to position 3 of 3")
    expect(page.get_by_role("button", name=f"Move “{first}” up")).to_be_focused()

    page.keyboard.press("Enter")
    assert _order(page) == [second, first, order[2]]
    expect(announcer).to_have_text(f"{first} moved to position 2 of 3")


def test_announcement_inserts_event_text_literally(timeline_page):
    page = timeline_page
    # "$&"/"$1" are String.replace patterns; "{total}" is a placeholder.
    label = "Rock $& $1 {total} night"
    page.locator(".timeline-item .timeline-text").first.evaluate(
        "(el, text) => { el.textContent = text; }", label
    )
    item = page.locator(".timeline-item").first
    item.locator(".timeline-move-down").click()
    expect(page.locator("p[aria-live=polite]")).to_have_text(
        f"{label} moved to position 2 of 3"
    )


def test_submit_sends_the_button_sorted_order(timeline_page):
    page = timeline_page
    # Sort into the correct order ("First", "Second", "Third") with buttons only.
    for _ in range(3):
        for target, text in enumerate(["First", "Second", "Third"]):
            while _order(page).index(text) > target:
                page.get_by_role("button", name=f"Move “{text}” up").click()
    assert _order(page) == ["First", "Second", "Third"]

    page.locator("button.journey-btn-primary").click()
    expect(page.get_by_text("Perfect!")).to_be_visible()
    expect(page.locator(".timeline-move").first).to_be_disabled()


def test_vote_results_follow_the_page_language_not_the_browser(
    page, live_server, transactional_db
):
    from crush_lu.tests.test_ux_wave4_polls_timeline import make_member, make_poll

    user = make_member()
    poll = make_poll()
    _log_in(page, live_server, user)
    # An English browser on the German page: the vote URL is language-neutral.
    page.set_extra_http_headers({"Accept-Language": "en"})
    page.set_viewport_size(PHONE)
    page.goto(f"{live_server.url}/de/polls/{poll.id}/")
    page.wait_for_load_state("load")

    page.locator("input[name=option_ids]").first.check()
    page.locator('form[action*="/vote/"] button[type=submit]').click()

    expect(page.get_by_text("Deine Stimme wurde aufgezeichnet. Danke!")).to_be_visible()
    expect(page.get_by_text("Your vote has been recorded")).to_have_count(0)
