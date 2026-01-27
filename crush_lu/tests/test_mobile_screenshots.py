"""Simple screenshot tests for mobile UI verification."""
import pytest
from playwright.sync_api import Page


@pytest.mark.playwright
def test_create_profile_phone_input_screenshot(page: Page):
    """Take screenshot of phone input on create profile page - mobile view."""
    # Set mobile viewport (iPhone 12)
    page.set_viewport_size({"width": 390, "height": 844})

    # Go directly to create profile page (will redirect to login if needed)
    page.goto("http://localhost:8000/en/create-profile/")

    # Wait for page load
    page.wait_for_load_state('networkidle')
    page.wait_for_timeout(1000)

    # Check if we're on login page
    if '/login' in page.url:
        print("On login page - filling credentials...")

        # Fill login form
        login_input = page.locator('input[name="login"]')
        if login_input.is_visible():
            login_input.fill('test@test.lu')

        password_input = page.locator('input[name="password"]')
        if password_input.is_visible():
            password_input.fill('test')

        # Find and click the actual login button (not language switcher)
        # Look for button with "Sign In" or similar text
        login_buttons = page.locator('button[type="submit"]')
        for i in range(login_buttons.count()):
            btn = login_buttons.nth(i)
            if btn.is_visible() and 'sign' in btn.inner_text().lower():
                btn.click()
                print("Clicked login button")
                break

        page.wait_for_timeout(2000)
        page.goto("http://localhost:8000/en/create-profile/")
        page.wait_for_load_state('networkidle')

    # Take screenshot
    page.screenshot(path="screenshots/mobile_phone_input.png", full_page=True)
    print("Screenshot saved: screenshots/mobile_phone_input.png")


@pytest.mark.playwright
def test_home_language_switcher_screenshot(page: Page):
    """Take screenshots of language switcher on mobile."""
    # Set mobile viewport (iPhone 12)
    page.set_viewport_size({"width": 390, "height": 844})

    # Go to home page in English
    page.goto("http://localhost:8000/en/")
    page.wait_for_load_state('networkidle')
    page.wait_for_timeout(1000)

    # Screenshot 1: Home page before language change
    page.screenshot(path="screenshots/mobile_home_en.png", full_page=True)
    print("Screenshot saved: screenshots/mobile_home_en.png")

    # Try to open mobile menu if it exists
    hamburger = page.locator('button').filter(has_text='☰')
    if hamburger.count() > 0 and hamburger.first.is_visible():
        hamburger.first.click()
        page.wait_for_timeout(500)
        page.screenshot(path="screenshots/mobile_menu_open.png", full_page=True)
        print("Screenshot saved: screenshots/mobile_menu_open.png")

    # Find language select dropdown (mobile version)
    language_selects = page.locator('select[name="language"]')

    if language_selects.count() > 0:
        # Try each select until we find a visible one
        for i in range(language_selects.count()):
            select = language_selects.nth(i)
            if select.is_visible():
                print(f"Found visible language select (index {i})")

                # Take screenshot before change
                page.screenshot(path="screenshots/mobile_before_lang_change.png", full_page=True)
                print("Screenshot saved: screenshots/mobile_before_lang_change.png")

                # Change to German
                select.select_option('de')
                page.wait_for_timeout(2000)

                # Screenshot after change
                page.screenshot(path="screenshots/mobile_after_lang_change.png", full_page=True)
                print("Screenshot saved: screenshots/mobile_after_lang_change.png")

                # Check URL
                print(f"Final URL: {page.url}")

                if '/de/' in page.url:
                    print("SUCCESS: URL correctly changed to /de/")
                else:
                    print("WARNING: URL did not change to /de/")

                break
    else:
        print("No language select found")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "--headed"])
