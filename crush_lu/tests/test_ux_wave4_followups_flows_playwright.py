"""Browser check for the per-address resend cooldown (UX Wave 4 · WP12, #1059):
while the countdown for the cooled address runs, typing a different address
into "Use a different address" re-enables the button; typing the cooled
address again disables it.

Run with:
    pytest crush_lu/tests/test_ux_wave4_followups_flows_playwright.py -m playwright
"""

import pytest

pytest.importorskip("playwright")

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]


def test_corrected_address_reenables_the_resend_button(page, live_server):
    from django.core.cache import cache

    cache.clear()
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{live_server.url}/accounts/confirm-email/")
    page.fill("#id_resend_email", "Typo@Example.com")
    page.click("form[x-data='resendCooldown'] button[type=submit]")
    page.wait_for_load_state("load")

    button = page.locator("form[x-data='resendCooldown'] button[type=submit]")
    page.wait_for_function(
        "document.querySelector(\"form[x-data='resendCooldown'] "
        'button[type=submit]").disabled'
    )
    assert button.is_disabled()

    page.click("summary")
    page.fill("#id_resend_email", "fixed@example.com")
    page.wait_for_function(
        "!document.querySelector(\"form[x-data='resendCooldown'] "
        'button[type=submit]").disabled'
    )
    assert button.is_enabled()

    # The cooled address (any case, stray spaces) keeps the button held.
    page.fill("#id_resend_email", " typo@example.com")
    page.wait_for_function(
        "document.querySelector(\"form[x-data='resendCooldown'] "
        'button[type=submit]").disabled'
    )
    assert button.is_disabled()

    page.fill("#id_resend_email", "")
    assert button.is_disabled()
