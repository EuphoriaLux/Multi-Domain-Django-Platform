"""Event confirmation focus checks using real templates and shipped CSP scripts."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.template import Context, Template
from django.test import override_settings
from django.utils import translation

pytest.importorskip("playwright")
from playwright.sync_api import expect  # noqa: E402

pytestmark = [pytest.mark.playwright, pytest.mark.axe_smoke]
ROOT = Path(__file__).resolve().parents[2]
JS_ROOT = ROOT / "crush_lu/static/crush_lu/js"
PHOTO = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='64' height='64'%3E%3Crect width='64' height='64' fill='gray'/%3E%3C/svg%3E"


@pytest.fixture(params=["lobby", "recap"])
def kind(request):
    return request.param


@pytest.fixture
def script_errors(page):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    yield errors
    assert errors == []


def _page(page, kind, *, native=True):
    state = {
        "phase": "live" if kind == "lobby" else "recap",
        "seconds_to_end": 3600,
        "seconds_to_recap_close": 3600,
        "signals_remaining": 3,
        "signals_total": 3,
        "incoming_count": 0,
        "incoming_confirmations": 0,
    }
    roster = [{"handle": handle, "photo_url": PHOTO} for handle in ["first", "second"]]
    source = (ROOT / f"crush_lu/templates/crush_lu/event_lobby/{kind}.html").read_text(
        encoding="utf-8"
    )
    content = source.split("{% block content %}", 1)[1].split("{% endblock %}", 1)[0]
    with override_settings(ROOT_URLCONF="azureproject.urls_crush"):
        with translation.override("en"):
            rendered = Template("{% load static i18n %}" + content).render(
                Context(
                    {
                        "event": SimpleNamespace(id=1, title="Test event"),
                        "state": state,
                        "roster": roster,
                        "mutuals": [],
                    }
                )
            )
    html = (
        '<!doctype html><html lang="en"><head></head><body>'
        '<a href="#" id="outside">Outside link</a><main>'
        + rendered
        + "</main></body></html>"
    )
    page.route(
        "https://events.test/",
        lambda route: route.fulfill(content_type="text/html", body=html),
    )
    page.goto("https://events.test/")
    page.add_style_tag(
        content=(ROOT / "crush_lu/static/crush_lu/css/tailwind.css").read_text(
            encoding="utf-8"
        )
    )
    page.add_style_tag(content="[x-cloak] { display: none !important; }")
    if not native:
        page.add_script_tag(
            content="HTMLDialogElement.prototype.showModal = undefined; HTMLDialogElement.prototype.close = undefined;"
        )
    page.add_script_tag(
        content="""
        window.confirmationRequests = [];
        window.fetch = (url, options = {}) => {
            if (options.method === 'POST') {
                return new Promise((resolve, reject) => {
                    confirmationRequests.push({resolve, reject, body: JSON.parse(options.body)});
                });
            }
            return Promise.resolve({ok: true, status: 200, json: () => Promise.resolve(window.eventState)});
        };
        window.completeConfirmation = (index, data) => confirmationRequests[index].resolve({
            ok: true, status: 200, json: () => Promise.resolve(data),
        });
        """
        + "window.eventState = "
        + json.dumps({"ok": True, "state": state, "roster": roster, "mutuals": []})
        + ";"
    )
    helper = JS_ROOT / "event-confirm-dialog.js"
    page.add_script_tag(path=str(helper))
    page.add_script_tag(path=str(JS_ROOT / f"event-{kind}.js"))
    page.add_script_tag(path=str(JS_ROOT / "vendor/alpinejs-csp-3.13.3.min.js"))
    page.wait_for_function(
        f"window.Alpine && Alpine.$data(document.getElementById('event-{kind}-root'))"
    )
    return page.get_by_role("dialog")


def _component(page, kind, expression):
    return page.evaluate(
        f"() => {{ const component = Alpine.$data(document.getElementById('event-{kind}-root')); {expression} }}"
    )


def _tile(page, handle="first"):
    return page.locator(f"button[data-handle='{handle}']")


def test_initial_focus_is_visible_cancel_control(page, kind, script_errors):
    dialog = _page(page, kind)
    _tile(page).click()
    expect(dialog).to_be_visible()
    expect(dialog.get_by_role("button", name="Cancel", exact=True)).to_be_focused()


@pytest.mark.parametrize("native", [True, False], ids=["native", "webview"])
def test_keyboard_containment_cancel_and_background_restore(
    page, kind, native, script_errors
):
    if not native:
        page.set_viewport_size({"width": 390, "height": 844})
    dialog = _page(page, kind, native=native)
    page.evaluate(
        "document.body.style.overflow = 'scroll'; document.getElementById('outside').setAttribute('aria-hidden', 'false')"
    )
    opener = _tile(page)
    opener.focus()
    page.keyboard.press("Enter")
    cancel = dialog.get_by_role("button", name="Cancel", exact=True)
    confirm = dialog.locator("button").last
    expect(cancel).to_be_focused()
    assert cancel.evaluate("""element => {
        const style = getComputedStyle(element);
        return style.boxShadow !== 'none' ||
            (style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0);
    }""")
    page.keyboard.press("Shift+Tab")
    expect(confirm).to_be_focused()
    page.keyboard.press("Tab")
    expect(cancel).to_be_focused()
    page.locator("#outside").evaluate("element => element.focus()")
    expect(cancel).to_be_focused()
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(opener).to_be_focused()
    assert page.evaluate("document.body.style.overflow") == "scroll"
    expect(page.locator("#outside")).to_have_attribute("aria-hidden", "false")
    assert not page.locator("#outside").evaluate(
        "element => element.hasAttribute('inert')"
    )
    opener.click()
    cancel.click()
    expect(dialog).to_be_hidden()
    expect(opener).to_be_focused()
    if not native:
        opener.click()
        expect(cancel).to_be_focused()
        dialog.locator("div[aria-hidden='true']").first.click(position={"x": 5, "y": 5})
        expect(dialog).to_be_hidden()
        expect(opener).to_be_focused()


@pytest.mark.parametrize("outcome", ["success", "network_error"])
def test_confirmation_response_restores_focus_without_duplicate_request(
    page, kind, outcome, script_errors
):
    dialog = _page(page, kind)
    _tile(page).click()
    confirm = dialog.locator("button").last
    confirm.click()
    expect(confirm).to_be_disabled()
    page.keyboard.press("Tab")
    expect(dialog.get_by_role("button", name="Cancel", exact=True)).to_be_focused()
    assert page.evaluate("confirmationRequests.map(request => request.body)") == [
        {"handle": "first"}
    ]
    if outcome == "success":
        result = "sent" if kind == "lobby" else "confirmed"
        page.evaluate(
            "result => completeConfirmation(0, {ok: true, result, signals_remaining: 2})",
            result,
        )
    else:
        page.evaluate("confirmationRequests[0].reject(new Error('offline'))")
    expect(dialog).to_be_hidden()
    expect(_tile(page)).to_be_focused()
    if outcome == "success":
        assert _component(page, kind, "return component.banner")


@pytest.mark.parametrize("outcome", ["success", "network_error"])
def test_late_response_does_not_close_reopened_confirmation(
    page, kind, outcome, script_errors
):
    dialog = _page(page, kind)
    _tile(page).click()
    dialog.locator("button").last.click()
    page.keyboard.press("Escape")
    expect(_tile(page)).to_be_focused()
    _tile(page, "second").click()
    cancel = dialog.get_by_role("button", name="Cancel", exact=True)
    expect(cancel).to_be_focused()
    if outcome == "success":
        page.evaluate(
            "completeConfirmation(0, {ok: true, result: 'encounter', first_name: 'Old reveal', signals_remaining: 2})"
        )
    else:
        page.evaluate("confirmationRequests[0].reject(new Error('offline'))")
    # Drain the completed request and any follow-up state fetch, then verify
    # it did not alter the active sheet's handle, pending state or focus.
    page.evaluate(
        "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
    )
    expect(dialog).to_be_visible()
    expect(cancel).to_be_focused()
    assert _component(
        page,
        kind,
        "return [component.confirmHandle, component.sending, component.reveal]",
    ) == ["second", False, ""]
    dialog.locator("button").last.click()
    assert page.evaluate("confirmationRequests[1].body.handle") == "second"


@pytest.mark.parametrize(
    "replacement", [True, False], ids=["refetched_tile", "removed_tile"]
)
def test_removed_opener_returns_to_replacement_or_page_heading(
    page, kind, replacement, script_errors
):
    dialog = _page(page, kind)
    _tile(page).click()
    expect(dialog.get_by_role("button", name="Cancel", exact=True)).to_be_focused()
    _component(
        page,
        kind,
        "component.renderRoster("
        + ("window.eventState.roster" if replacement else "[]")
        + ")",
    )
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    if replacement:
        expect(_tile(page)).to_be_focused()
    else:
        expect(page.locator("[data-event-focus]")).to_be_focused()


@pytest.mark.parametrize("native", [True, False], ids=["native", "webview"])
def test_phase_expiry_closes_sheet_and_focuses_next_step(
    page, kind, native, script_errors
):
    dialog = _page(page, kind, native=native)
    _tile(page).click()
    dialog.locator("button").last.click()
    page.evaluate("eventState.state.phase = 'closed'")
    _component(page, kind, "component.refetch()")
    expect(dialog).to_be_hidden()
    expect(page.locator("[data-phase-focus]")).to_be_focused()
    page.evaluate(
        "completeConfirmation(0, {ok: true, result: 'encounter', first_name: 'Late reveal'})"
    )
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(resolve))")
    assert _component(
        page,
        kind,
        "return [component.confirmOpen, component.confirmPhotoUrl, component.reveal]",
    ) == [False, "", ""]
    assert page.evaluate("document.body.style.overflow") == ""


def test_native_close_and_queued_close_event_preserve_reopened_sheet(
    page, kind, script_errors
):
    dialog = _page(page, kind)
    _tile(page).click()
    dialog.evaluate("element => element.close()")
    page.wait_for_function(
        f"!Alpine.$data(document.getElementById('event-{kind}-root')).confirmOpen"
    )
    expect(_tile(page)).to_be_focused()
    assert page.evaluate("document.body.style.overflow") == ""
    _tile(page).click()
    _component(
        page,
        kind,
        "component.closeConfirm(); component.openConfirm('second', window.eventState.roster[1].photo_url, '');",
    )
    expect(dialog.get_by_role("button", name="Cancel", exact=True)).to_be_focused()
    expect(dialog).to_be_visible()
    assert _component(page, kind, "return component.confirmHandle") == "second"
    page.keyboard.press("Escape")
    expect(_tile(page)).to_be_focused()


def test_revoked_access_purges_photos_and_ignores_late_identity_response(
    page, kind, script_errors
):
    dialog = _page(page, kind)
    _tile(page).click()
    dialog.locator("button").last.click()
    _component(
        page,
        kind,
        "component.announceReveal('Earlier reveal'); component.readStateResponse({ok: false, status: 403});",
    )
    expect(dialog).to_be_hidden()
    expect(page.locator("[data-phase-focus]")).to_be_focused()
    expect(page.locator("button[data-handle]")).to_have_count(0)
    page.evaluate(
        "completeConfirmation(0, {ok: true, result: 'mutual', first_name: 'Late reveal'})"
    )
    _component(page, kind, "component.applyState(window.eventState)")
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(resolve))")
    assert _component(
        page,
        kind,
        "return [component.confirmPhotoUrl, component.reveal, component.banner]",
    ) == ["", "", ""]
    expect(page.locator("button[data-handle]")).to_have_count(0)


@pytest.mark.parametrize("native", [True, False], ids=["native", "webview"])
def test_local_countdown_expiry_closes_confirmation(page, kind, native, script_errors):
    dialog = _page(page, kind, native=native)
    _tile(page).click()
    expect(dialog.get_by_role("button", name="Cancel", exact=True)).to_be_focused()
    # Capture only interval scheduling; Alpine and focus rendering use the
    # normal browser clock. Run the actual production countdown callback.
    counter = "secondsToEnd" if kind == "lobby" else "secondsToClose"
    _component(
        page,
        kind,
        """
        component.stopUpdates();
        const schedule = window.setInterval;
        let tick;
        window.setInterval = callback => { tick = callback; return null; };
        component."""
        + counter
        + """ = 1;
        component.startCountdown();
        window.setInterval = schedule;
        tick();
    """,
    )
    expect(dialog).to_be_hidden()
    expect(page.locator("[data-phase-focus]")).to_be_focused()


def test_teardown_cancels_queued_open_without_locking_background(
    page, kind, script_errors
):
    _page(page, kind)
    page.locator("#outside").focus()
    _component(
        page,
        kind,
        """
        component.openConfirm('first', window.eventState.roster[0].photo_url, '');
        window.removedComponent = component;
        component.componentRoot.remove();
    """,
    )
    page.wait_for_function("window.removedComponent.accessRevoked")
    page.evaluate(
        "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
    )
    # Even a delayed helper call after teardown must be inert. In particular,
    # showModal on a detached element must not leave global focus/scroll locks.
    page.evaluate(
        "removedComponent.confirmDialog.open(document.getElementById('outside'))"
    )
    assert page.evaluate(
        "[removedComponent.confirmOpen, removedComponent.confirmHandle, removedComponent.confirmPhotoUrl]"
    ) == [False, None, ""]
    assert page.evaluate("document.body.style.overflow") == ""
    page.locator("#outside").focus()
    expect(page.locator("#outside")).to_be_focused()
