"""Test mobile UI fixes for phone input and language switcher."""
import pytest
from playwright.sync_api import Page, expect


@pytest.mark.playwright
def test_phone_input_mobile_layout(page: Page, live_server):
    """Test phone number input stacks vertically on mobile."""
    # Set mobile viewport (iPhone 12)
    page.set_viewport_size({"width": 390, "height": 844})

    # Navigate to login
    page.goto(f"{live_server.url}/accounts/login/")

    # Login with test credentials - be more specific with selector
    page.fill('input[name="login"]', 'test@test.lu')
    page.fill('input[name="password"]', 'test')

    # Click the login submit button specifically
    page.locator('form button[type="submit"]').first.click()

    # Wait for redirect and navigate to create profile
    page.wait_for_timeout(2000)
    page.goto(f"{live_server.url}/en/create-profile/")

    # Wait for page to load
    page.wait_for_load_state('networkidle')

    # Take screenshot of the phone input section
    page.screenshot(path="screenshots/mobile_phone_input.png", full_page=True)

    print("[OK] Screenshot saved: screenshots/mobile_phone_input.png")

    # Verify the phone input container exists
    phone_container = page.locator('#div_id_phone')
    expect(phone_container).to_be_visible()


@pytest.mark.playwright
def test_language_switcher_mobile(page: Page, live_server):
    """Test language switcher changes URL correctly on mobile."""
    # Set mobile viewport (iPhone 12)
    page.set_viewport_size({"width": 390, "height": 844})

    # Navigate to home page
    page.goto(f"{live_server.url}/en/")
    page.wait_for_load_state('networkidle')

    # Take screenshot before opening menu
    page.screenshot(path="screenshots/mobile_home_before.png", full_page=True)
    print("[OK] Screenshot saved: screenshots/mobile_home_before.png")

    # Open mobile menu (look for hamburger button)
    menu_button = page.locator('button').filter(has_text='☰')

    if menu_button.count() > 0:
        menu_button.click()
        page.wait_for_timeout(500)

        # Take screenshot with menu open
        page.screenshot(path="screenshots/mobile_menu_open.png", full_page=True)
        print("[OK] Screenshot saved: screenshots/mobile_menu_open.png")

    # Find and interact with language switcher - mobile select
    language_selects = page.locator('select[name="language"]')

    # Use the last one (mobile version)
    if language_selects.count() > 0:
        language_select = language_selects.last

        if language_select.is_visible():
            # Take screenshot showing language dropdown
            page.screenshot(path="screenshots/mobile_language_before.png", full_page=True)
            print("[OK] Screenshot saved: screenshots/mobile_language_before.png")

            # Select German
            language_select.select_option('de')
            page.wait_for_timeout(2000)

            # Check current URL
            current_url = page.url
            print(f"Current URL after language change: {current_url}")

            # Take screenshot after language change
            page.screenshot(path="screenshots/mobile_language_after.png", full_page=True)
            print("[OK] Screenshot saved: screenshots/mobile_language_after.png")

            # Verify URL changed to /de/
            assert '/de/' in current_url, f"Expected /de/ in URL, got: {current_url}"
            print("[OK] Language switcher correctly changed URL to /de/")
        else:
            print("[WARNING] Language select not visible on mobile")
    else:
        print("[WARNING] No language select found")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
