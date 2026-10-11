"""OTP focus regression using the real partial and shipped CSP Alpine bundle.

Only delivery/verification responses are simulated; all dialog transitions,
digit handlers and focus behavior execute the production core.min.js.
"""

import json
from pathlib import Path

import pytest
from django.template import Context, Template
from django.utils import translation

pytest.importorskip("playwright")
from playwright.sync_api import expect  # noqa: E402

pytestmark = [pytest.mark.playwright, pytest.mark.axe_smoke]
ROOT = Path(__file__).resolve().parents[2]
JS_ROOT = ROOT / "crush_lu/static/crush_lu/js"
PHONE = "+352621123456"
DIALOG = "[x-data='phoneVerificationModal']"

BOOTSTRAP = """
window.gettext = message => message;
window.phoneVerification = {
    csrfToken: 'test-token',
    verifyCode() {
        return new Promise(resolve => { window.finishSmsVerification = resolve; });
    },
    sendVerificationCode() {
        return new Promise(resolve => { window.finishSmsSend = resolve; });
    },
    getFailureCount() { return 0; },
};
document.getElementById('sms-opener').addEventListener('click', () => {
    window.dispatchEvent(new CustomEvent('open-phone-modal'));
});
document.getElementById('whatsapp-opener').addEventListener('click', () => {
    window.dispatchEvent(new CustomEvent('open-whatsapp-modal', {
        detail: '+352621123456',
    }));
});
"""


def _page(page, *, native=True):
    with translation.override("en"):
        modal = Template(
            '{% include "crush_lu/includes/phone_otp_modal.html" %}'
        ).render(Context({}))
    html = (
        '<!doctype html><html lang="en"><head></head><body>'
        '<button id="sms-opener">Verify by SMS</button>'
        '<button id="whatsapp-opener">Verify by WhatsApp</button>'
        '<a href="#" id="outside-link">Outside link</a>' + modal + "</body></html>"
    )
    page.route(
        "https://otp.test/",
        lambda route: route.fulfill(content_type="text/html", body=html),
    )
    page.goto("https://otp.test/")
    page.add_style_tag(
        content=(ROOT / "crush_lu/static/crush_lu/css/tailwind.css").read_text(
            encoding="utf-8"
        )
    )
    page.add_style_tag(content="[x-cloak] { display: none !important; }")
    if not native:
        page.add_script_tag(
            content="HTMLDialogElement.prototype.showModal = undefined;"
        )
    page.add_script_tag(content=BOOTSTRAP)
    page.add_script_tag(path=str(JS_ROOT / "alpine/core.min.js"))
    page.add_script_tag(path=str(JS_ROOT / "vendor/alpinejs-csp-3.13.3.min.js"))
    page.wait_for_function(
        'window.Alpine && Alpine.$data(document.querySelector("' + DIALOG + '"))'
    )
    return page.locator(DIALOG)


def _code_sent(page):
    page.evaluate(
        'phone => Alpine.$data(document.querySelector("' + DIALOG + '"))'
        ".showCodeStep(phone)",
        PHONE,
    )
    expect(page.locator("#otp-0")).to_be_focused()


def _fill_code(page):
    for index, digit in enumerate("123456"):
        page.locator(f"#otp-{index}").fill(digit)


def test_named_dialog_contains_focus_and_escape_restores_opener(page):
    dialog = _page(page)
    page.evaluate("document.body.style.overflow = 'scroll'")
    opener = page.locator("#sms-opener")
    opener.click()
    expect(page.get_by_role("dialog", name="Verify Your Phone")).to_be_visible()
    close = dialog.get_by_role("button", name="Close", exact=True)
    expect(close).to_be_focused()
    _code_sent(page)
    page.keyboard.press("Shift+Tab")
    expect(close).to_be_focused()
    page.keyboard.press("Shift+Tab")
    expect(page.locator("#otp-5")).to_be_focused()
    page.keyboard.press("Tab")
    expect(close).to_be_focused()
    page.locator("#outside-link").evaluate("element => element.focus()")
    expect(close).to_be_focused()

    # A repeated open event resets the flow without replacing the opener
    # with whatever control currently has focus inside the dialog.
    page.evaluate("window.dispatchEvent(new CustomEvent('open-phone-modal'))")
    expect(close).to_be_focused()
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(opener).to_be_focused()
    assert page.evaluate("document.body.style.overflow") == "scroll"


def test_sms_verifying_and_recoverable_error_keep_focus_in_visible_step(page):
    dialog = _page(page)
    page.locator("#sms-opener").click()
    _code_sent(page)
    _fill_code(page)
    expect(dialog.get_by_role("button", name="Close", exact=True)).to_be_focused()
    expect(page.locator("#otp-0")).to_be_hidden()
    page.evaluate("finishSmsVerification({success: false, error: 'Incorrect code'})")
    expect(page.locator("#otp-0")).to_be_focused()
    expect(dialog.get_by_role("alert")).to_have_text("Incorrect code")
    expect(page.locator("#otp-0")).to_have_value("")
    dialog.get_by_role("button", name="Close", exact=True).click()
    expect(page.locator("#sms-opener")).to_be_focused()


def test_sms_success_announces_completion_then_restores_opener(page):
    dialog = _page(page)
    page.locator("#sms-opener").click()
    _code_sent(page)
    _fill_code(page)
    page.evaluate(
        "phone => finishSmsVerification({success: true, phone_number: phone})", PHONE
    )
    expect(dialog.get_by_role("heading", name="Phone Verified!")).to_be_focused()
    expect(dialog).to_be_hidden()
    expect(page.locator("#sms-opener")).to_be_focused()


def test_whatsapp_send_error_focuses_fallback_and_cancel_restores_opener(page):
    page.route(
        "https://otp.test/api/phone/whatsapp/send/",
        lambda route: route.fulfill(
            content_type="application/json",
            body=json.dumps({"success": False, "error": "Delivery unavailable"}),
        ),
    )
    dialog = _page(page)
    opener = page.locator("#whatsapp-opener")
    opener.click()
    expect(dialog.get_by_role("button", name="Verify by SMS instead")).to_be_focused()
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    expect(dialog).to_be_hidden()
    expect(opener).to_be_focused()


def test_whatsapp_code_error_returns_focus_to_first_digit(page):
    page.route(
        "https://otp.test/api/phone/whatsapp/send/",
        lambda route: route.fulfill(
            content_type="application/json", body=json.dumps({"success": True})
        ),
    )
    page.route(
        "https://otp.test/api/phone/whatsapp/verify/",
        lambda route: route.fulfill(
            content_type="application/json",
            body=json.dumps({"success": False, "error": "Incorrect code"}),
        ),
    )
    dialog = _page(page)
    page.locator("#whatsapp-opener").click()
    expect(page.locator("#otp-0")).to_be_focused()
    _fill_code(page)
    expect(dialog.get_by_role("alert")).to_have_text("Incorrect code")
    expect(page.locator("#otp-0")).to_be_focused()
    page.keyboard.press("Escape")
    expect(page.locator("#whatsapp-opener")).to_be_focused()


def test_native_close_cleans_up_and_restores_focus(page):
    dialog = _page(page)
    page.locator("#sms-opener").click()
    _code_sent(page)
    page.locator(DIALOG).evaluate("element => element.close()")
    expect(dialog).to_be_hidden()
    expect(page.locator("#sms-opener")).to_be_focused()
    assert page.evaluate("document.body.style.overflow") == ""


def test_late_initial_code_callback_does_not_restart_closed_dialog(page):
    dialog = _page(page)
    page.locator("#sms-opener").click()
    page.keyboard.press("Escape")
    page.evaluate(
        'phone => Alpine.$data(document.querySelector("' + DIALOG + '"))'
        ".showCodeStep(phone)",
        PHONE,
    )
    expect(dialog).to_be_hidden()
    assert (
        page.evaluate(
            'Alpine.$data(document.querySelector("' + DIALOG + '")).resendTimer'
        )
        is None
    )


@pytest.mark.parametrize("native", [True, False])
def test_escape_during_sms_sending_restores_opener(page, native):
    dialog = _page(page, native=native)
    opener = page.locator("#sms-opener")
    opener.click()
    expect(dialog).to_have_attribute("aria-modal", "false")
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(opener).to_be_focused()


def test_webview_fallback_contains_focus_and_restores_background(page):
    dialog = _page(page, native=False)
    page.locator("#outside-link").evaluate(
        "element => element.setAttribute('aria-hidden', 'false')"
    )
    opener = page.locator("#sms-opener")
    opener.click()
    expect(page.get_by_role("dialog", name="Verify Your Phone")).to_be_visible()
    _code_sent(page)
    expect(opener).to_have_attribute("inert", "")
    expect(opener).to_have_attribute("aria-hidden", "true")
    close = dialog.get_by_role("button", name="Close", exact=True)
    page.keyboard.press("Shift+Tab")
    expect(close).to_be_focused()
    page.keyboard.press("Shift+Tab")
    expect(page.locator("#otp-5")).to_be_focused()
    page.keyboard.press("Tab")
    expect(close).to_be_focused()
    # Simulate an older WebView without inert support: the manual focus guard
    # still redirects a programmatic attempt to leave the overlay.
    page.locator("#outside-link").evaluate(
        "element => { element.removeAttribute('inert'); element.focus(); }"
    )
    expect(page.locator("#otp-0")).to_be_focused()
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(opener).to_be_focused()
    assert opener.get_attribute("inert") is None
    assert opener.get_attribute("aria-hidden") is None
    expect(page.locator("#outside-link")).to_have_attribute("aria-hidden", "false")


def test_sms_challenge_iframe_is_usable_during_initial_send_and_resend(page):
    dialog = _page(page)
    page.route(
        "https://challenge.test/",
        lambda route: route.fulfill(
            content_type="text/html", body="<button>Complete challenge</button>"
        ),
    )
    page.locator("#sms-opener").click()
    page.evaluate("""
        const challenge = document.createElement('iframe');
        challenge.id = 'challenge';
        challenge.src = 'https://challenge.test/';
        challenge.style.cssText = 'position:fixed;top:0;left:0;z-index:2147483647';
        document.body.appendChild(challenge);
    """)
    challenge_button = page.frame_locator("#challenge").get_by_role("button")
    challenge_button.click()
    expect(page.locator("#challenge")).to_be_focused()
    expect(dialog).to_have_attribute("aria-modal", "false")
    _code_sent(page)
    expect(dialog).to_have_attribute("aria-modal", "true")
    page.locator("#challenge").evaluate("element => element.focus()")
    expect(page.locator("#otp-0")).to_be_focused()
    page.evaluate(
        'Alpine.$data(document.querySelector("' + DIALOG + '")).resendCountdown = 0'
    )
    dialog.get_by_role("button", name="Resend Code", exact=True).click()
    expect(dialog).to_have_attribute("aria-modal", "false")
    challenge_button.click()
    expect(page.locator("#challenge")).to_be_focused()
    _code_sent(page)
    expect(dialog).to_have_attribute("aria-modal", "true")


@pytest.mark.parametrize("result", [{"success": False}, {"success": True}])
def test_late_verification_response_does_not_take_over_reopened_dialog(page, result):
    dialog = _page(page)
    page.locator("#sms-opener").click()
    _code_sent(page)
    _fill_code(page)
    page.keyboard.press("Escape")
    page.locator("#sms-opener").click()
    page.evaluate(
        "result => finishSmsVerification(result)",
        dict(result, error="Old error", phone_number=PHONE),
    )
    expect(dialog.locator('[x-show="isSendingStep"]')).to_be_visible()
    expect(dialog.get_by_role("button", name="Close", exact=True)).to_be_focused()
    page.wait_for_timeout(2100)  # Past the previous flow's success-close deadline.
    expect(dialog).to_be_visible()


def test_success_close_timer_does_not_close_a_new_flow(page):
    dialog = _page(page)
    page.locator("#sms-opener").click()
    _code_sent(page)
    _fill_code(page)
    page.evaluate(
        "phone => finishSmsVerification({success: true, phone_number: phone})", PHONE
    )
    expect(dialog.get_by_role("heading", name="Phone Verified!")).to_be_focused()
    page.keyboard.press("Escape")
    page.locator("#sms-opener").click()
    page.wait_for_timeout(2100)
    expect(dialog).to_be_visible()
    expect(dialog.get_by_role("button", name="Close", exact=True)).to_be_focused()
