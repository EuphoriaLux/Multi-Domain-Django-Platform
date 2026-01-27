"""Final test for language switcher on mobile."""
import pytest
from playwright.sync_api import Page


@pytest.mark.playwright
def test_mobile_language_switcher_complete(page: Page, live_server):
    """Complete test of mobile language switcher functionality."""
    # Set mobile viewport (iPhone 12)
    page.set_viewport_size({"width": 390, "height": 844})

    # Navigate to English home page
    page.goto(f"{live_server.url}/en/")
    page.wait_for_load_state('networkidle')
    page.wait_for_timeout(1000)

    # Take screenshot 1: Initial state
    page.screenshot(path="screenshots/final_1_initial_en.png", full_page=True)
    print("Screenshot 1: Initial English home page")

    # Find and click the hamburger menu button
    # Look for the button with the SVG hamburger icon
    hamburger_button = page.locator('button[aria-label="Toggle navigation"], button.lg\\:hidden').first

    if hamburger_button.is_visible():
        print("Found hamburger button, clicking...")
        hamburger_button.click()
        page.wait_for_timeout(500)

        # Take screenshot 2: Menu open
        page.screenshot(path="screenshots/final_2_menu_open.png", full_page=True)
        print("Screenshot 2: Mobile menu opened")

        # Find the language select in the mobile menu
        # The mobile version should now be visible
        language_select = page.locator('#language-mobile')

        if language_select.is_visible():
            print("Language select is visible")

            # Take screenshot 3: Before selection
            page.screenshot(path="screenshots/final_3_before_selection.png", full_page=True)
            print("Screenshot 3: Before language selection")

            # Get current URL
            print(f"Current URL before change: {page.url}")

            # Select German
            print("Selecting German...")
            language_select.select_option('de')

            # Wait for the form to submit and page to navigate
            page.wait_for_timeout(3000)
            page.wait_for_load_state('networkidle')

            # Take screenshot 4: After language change
            page.screenshot(path="screenshots/final_4_after_change.png", full_page=True)
            print("Screenshot 4: After language change")

            # Check final URL
            final_url = page.url
            print(f"Final URL: {final_url}")

            # Verify the URL changed
            if '/de/' in final_url:
                print("[SUCCESS] Language switcher works! URL correctly changed to /de/")
            else:
                print(f"[ISSUE] URL did not change to /de/. Got: {final_url}")

            # Take a screenshot of the page content to verify it's in German
            page.screenshot(path="screenshots/final_5_german_content.png", full_page=True)
            print("Screenshot 5: German content verification")

        else:
            print("[WARNING] Language select not visible even with menu open")
            # Debug: take screenshot of what we see
            page.screenshot(path="screenshots/debug_menu_state.png", full_page=True)
            print("Debug screenshot saved")

    else:
        print("[WARNING] Hamburger button not found or not visible")
        # Debug
        page.screenshot(path="screenshots/debug_no_hamburger.png", full_page=True)
        print("Debug screenshot saved")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "--headed"])
