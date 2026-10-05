"""Playwright: UX Wave 6 a11y remainders (#1150, #1117).

* The 8 gift-wizard file inputs have programmatic labels (axe ``label``).
* Without <dialog> support, confirm-sheet.js still offers the
  "Also let a Crush Coach know" option through a second native confirm(),
  so the chat block files the coach report.
* The dashboard install card drops its Install button (and frees the
  prompts queue) once the browser's install prompt is dismissed.
* No colour-contrast nodes on the account drill-down (incl. the WhatsApp
  notice in the notifications sub-section) or on the photo section that
  Connect onboarding redirects a photo-less member to, light and dark.

Excluded from the default run (``-m "not playwright"``). Run:
    pytest -m playwright crush_lu/tests/test_ux_wave6_a11y_rest_playwright.py -n 0
"""

import json

import pytest

pytest.importorskip("playwright")

from django.conf import settings  # noqa: E402
from django.core.cache import cache  # noqa: E402
from django.test import Client  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

from crush_lu.tests.test_contrast_tokens_playwright import _axe_source  # noqa: E402

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

PHONE = {"width": 390, "height": 844}
DECLINED = json.dumps({"essential": True, "analytics": False, "marketing": False})


def _page(browser, live_server, user, theme="light"):
    context = browser.new_context(viewport=PHONE, color_scheme=theme)
    context.route(
        "**://fonts.g*.com/**",
        lambda route: route.fulfill(status=200, content_type="text/css", body=""),
    )
    client = Client()
    client.force_login(user)
    context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            },
            {"name": "cookie_consent", "value": DECLINED, "url": live_server.url},
        ]
    )
    context.add_init_script(f"localStorage.setItem('theme', '{theme}');")
    return context.new_page()


def _axe(page, rules, include=None):
    source = _axe_source()
    if source is None:
        pytest.skip("axe-core not available (run npm ci or set AXE_CORE_PATH)")
    page.evaluate(source)
    return page.evaluate(
        "async ([rules, include]) => (await axe.run(include || document,"
        " {runOnly: rules})).violations.flatMap("
        "v => v.nodes.map(n => v.id + ' ' + n.target.join(' ')))",
        [rules, include],
    )


# --- 1. gift wizard file inputs --------------------------------------------

GIFT_FILE_LABELS = {
    "id_chapter1_image": "Photo Puzzle Image",
    "id_chapter3_image_1": "Slideshow Photo 1",
    "id_chapter3_image_2": "Slideshow Photo 2",
    "id_chapter3_image_3": "Slideshow Photo 3",
    "id_chapter3_image_4": "Slideshow Photo 4",
    "id_chapter3_image_5": "Slideshow Photo 5",
    "id_chapter4_video": "Video Message",
    "id_chapter5_letter_music": "Letter Music",
}


def test_gift_media_file_inputs_have_programmatic_labels(browser, live_server):
    from crush_lu.tests.test_profile_edit_connect_card import _make_member

    cache.clear()
    # Gift sender pages are staff/coach-only (UX Wave 4 decision C).
    page = _page(
        browser, live_server, _make_member("gift-labels@example.com", is_staff=True)
    )
    page.goto(f"{live_server.url}/en/journey/gift/create/")
    page.wait_for_function(
        "() => window.Alpine && document.querySelector('.step-content.active')"
    )
    page.fill("#id_recipient_name", "Marie")
    page.fill("#id_date_first_met", "2024-02-14")
    page.fill("#id_location_first_met", "Luxembourg City")
    page.get_by_role("button", name="Next: Add Media").click()
    expect(page.locator("#id_chapter1_image")).to_be_attached()

    names = {
        input_id: page.locator(f"#{input_id}").evaluate(
            "el => Array.from(el.labels).map(l => l.textContent.trim()).join(' ')"
        )
        for input_id in GIFT_FILE_LABELS
    }
    assert names == GIFT_FILE_LABELS
    nodes = _axe(page, ["label", "label-title-only"], [[".create-form"]])
    assert nodes == []


# --- 2. confirm-sheet fallback without <dialog> ----------------------------

NO_DIALOG_JS = """
delete HTMLDialogElement.prototype.showModal;
delete window.HTMLDialogElement;
"""


@pytest.mark.parametrize("escalate", [True, False])
def test_block_without_dialog_support_still_asks_about_the_coach(
    browser, live_server, escalate
):
    from crush_lu.models.crush_connect_cycle import (
        ConnectReport,
        ConnectTemporaryChat,
    )
    from crush_lu.tests.test_connect_chat_flows import _make_open_chat
    from crush_lu.tests.test_crush_connect import _grant_consent

    cache.clear()
    me, target, chat = _make_open_chat()
    _grant_consent(me)
    page = _page(browser, live_server, me)
    page.context.add_init_script(NO_DIALOG_JS)
    asked = []

    def answer(dialog):
        asked.append(dialog.message)
        if len(asked) == 1 or escalate:
            dialog.accept()
        else:
            dialog.dismiss()

    page.on("dialog", answer)
    page.goto(f"{live_server.url}/en/crush-connect/week/chats/{chat.pk}/")
    assert page.evaluate("() => typeof window.HTMLDialogElement") == "undefined"

    page.locator("[data-safety-menu] > summary").click()
    with page.expect_navigation():
        page.get_by_role("button", name="Just block this member").click()

    assert len(asked) == 2, asked
    assert "can't be undone" in asked[0]
    assert asked[1] == "Also let a Crush Coach know"
    chat.refresh_from_db()
    assert chat.status == ConnectTemporaryChat.Status.BLOCKED
    reports = ConnectReport.objects.filter(reporter=me, reported_user=target)
    assert reports.count() == (1 if escalate else 0)


# --- 3. dashboard install card after a dismissed prompt ---------------------

RETURNING_VISITOR_JS = """
if (!sessionStorage.getItem("a11y-seeded")) {
    sessionStorage.setItem("a11y-seeded", "1");
    localStorage.setItem("crush-pwa-sessions", "1");
}
"""
FAKE_INSTALL_PROMPT_JS = """
() => {
    const e = new Event("beforeinstallprompt", {cancelable: true});
    e.prompt = () => { window.__promptShown = true; };
    e.userChoice = Promise.resolve({outcome: "dismissed", platform: "web"});
    window.dispatchEvent(e);
}
"""


def test_dismissed_install_prompt_removes_the_dashboard_install_button(
    browser, live_server
):
    from crush_lu.tests.test_prompt_queue import _make_eligible_member

    cache.clear()
    page = _page(browser, live_server, _make_eligible_member("pwa-dismiss@example.com"))
    page.context.add_init_script(RETURNING_VISITOR_JS)
    response = page.goto(f"{live_server.url}/en/dashboard/")
    assert response is not None and response.ok
    page.wait_for_function(
        "() => window.Alpine && Alpine.store('prompts') && Alpine.store('prompts').ready"
    )
    page.evaluate(FAKE_INSTALL_PROMPT_JS)

    button = page.get_by_role("button", name="Install App")
    expect(button).to_be_visible()
    assert page.evaluate("() => Alpine.store('prompts').install") is True

    button.click()
    expect(button).to_be_hidden()
    assert page.evaluate("() => window.__promptShown") is True
    page.wait_for_function("() => Alpine.store('prompts').install === false")


# --- 4. colour contrast: account drill-down and Connect onboarding photos ---


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_account_drilldown_and_connect_photos_have_no_contrast_nodes(
    browser, live_server, settings, theme
):
    from crush_lu.models import CrushProfile
    from crush_lu.tests.test_crush_connect import _grant_consent, _make_user

    cache.clear()
    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_user(username="a11y_contrast", onboarded=False)
    _grant_consent(member)
    # No photo, no verified phone: Connect onboarding redirects to the photo
    # section and the notifications sub-section shows the WhatsApp notice.
    CrushProfile.objects.filter(user=member).update(photo_1="")
    page = _page(browser, live_server, member, theme)

    found = {}
    for sub in ("", "&sub=settings", "&sub=notifications", "&sub=danger"):
        path = f"/en/profile/edit/?section=account{sub}"
        response = page.goto(f"{live_server.url}{path}")
        assert response is not None and response.ok
        page.wait_for_timeout(300)
        found[path] = _axe(page, ["color-contrast"])

    response = page.goto(f"{live_server.url}/en/crush-connect/onboarding/")
    assert response is not None and response.ok
    assert "section=photos" in page.url, page.url
    page.wait_for_timeout(300)
    found["connect-photos"] = _axe(page, ["color-contrast"])

    assert {path: nodes for path, nodes in found.items() if nodes} == {}
