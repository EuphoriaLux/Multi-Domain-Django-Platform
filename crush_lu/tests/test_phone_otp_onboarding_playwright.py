"""OTP modal on the actual onboarding page, using the shipped CSP bundle."""

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import expect  # noqa: E402

from crush_lu.tests.test_phone_otp_dialog_playwright import (
    PHONE,
    _fill_code,
)  # noqa: E402

pytestmark = [
    pytest.mark.playwright,
    pytest.mark.axe_smoke,
    pytest.mark.django_db(transaction=True),
]


def test_real_onboarding_success_focuses_continue_when_opener_disappears(
    browser, live_server, settings
):
    from django.contrib.auth import get_user_model
    from django.utils import timezone

    from crush_lu.models import CrushProfile
    from crush_lu.tests.test_onboarding_journey_e2e import _grant_consent
    from crush_lu.tests.test_prompt_queue_playwright import _open, _phone

    settings.DEBUG = False  # The page must load core.min.js, as production does.
    user = get_user_model().objects.create_user(
        username="otp-dialog@example.com",
        email="otp-dialog@example.com",
        password="test-password",
    )
    _grant_consent(user)
    CrushProfile.objects.create(
        user=user, phone_number=PHONE, welcome_seen_at=timezone.now()
    )
    page = _phone(browser, live_server, user)
    try:
        for pattern in (
            "**://www.gstatic.com/firebasejs/**",
            "**://cdn.jsdelivr.net/npm/intl-tel-input@*/**",
        ):
            page.route(
                pattern,
                lambda route: route.fulfill(content_type="text/javascript", body=""),
            )
        page.route(
            "**/static/crush_lu/js/phone-verification.js",
            lambda route: route.fulfill(
                content_type="text/javascript",
                body="""
                    window.PhoneVerification = class {
                        constructor(options) {
                            this.options = options;
                            this.csrfToken = options.csrfToken;
                        }
                        sendVerificationCode(phone) {
                            this.options.onCodeSent(phone);
                            return Promise.resolve({success: true});
                        }
                        verifyCode() {
                            return Promise.resolve({
                                success: true, phone_number: '+352621123456',
                            });
                        }
                    };
                """,
            ),
        )
        page.route(
            "**/api/phone/check-available/",
            lambda route: route.fulfill(
                content_type="application/json", body='{"available": true}'
            ),
        )
        _open(page, f"{live_server.url}/en/onboarding/phone/")
        assert page.locator('script[src*="/alpine/core.min.js"]').count() == 1
        page.locator("#onboarding_phone_input").fill(PHONE)
        opener = page.get_by_role("button", name="Verify", exact=True)
        opener.click()
        dialog = page.get_by_role("dialog", name="Verify Your Phone")
        expect(dialog).to_be_visible()
        expect(page.locator("#otp-0")).to_be_focused()
        _fill_code(page)
        expect(dialog.get_by_role("heading", name="Phone Verified!")).to_be_focused()
        expect(opener).to_be_hidden()
        expect(dialog).to_be_hidden()
        expect(page.get_by_role("link", name="Continue", exact=False)).to_be_focused()
    finally:
        page.context.close()
