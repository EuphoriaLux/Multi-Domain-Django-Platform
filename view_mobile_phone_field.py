"""
Quick script to view the phone number field on profile creation page in mobile view.
Run with: pytest view_mobile_phone_field.py -v -s
"""
import pytest
from playwright.sync_api import Page


@pytest.mark.playwright
def test_view_phone_field_mobile(page: Page, live_server_url, test_user, transactional_db):
    """Navigate to profile creation page and screenshot the phone field in mobile view."""
    from django.contrib.sites.models import Site

    # Ensure Site exists for live_server
    Site.objects.get_or_create(id=1, defaults={'domain': 'localhost', 'name': 'localhost'})
    Site.objects.get_or_create(domain='127.0.0.1', defaults={'name': 'Live Server'})

    # Set mobile viewport (iPhone 12)
    page.set_viewport_size({'width': 390, 'height': 844})

    # Navigate to login page
    page.goto(f"{live_server_url}/accounts/login/")
    page.wait_for_selector('input[name="login"]', timeout=10000)

    # Dismiss cookie banner if present
    cookie_decline = page.locator('button:has-text("Decline All")')
    if cookie_decline.count() > 0:
        cookie_decline.click()
        page.wait_for_timeout(500)

    # Log in
    page.fill('input[name="login"]', test_user.email)
    page.fill('input[name="password"]', 'testpass123')
    page.click('button:has-text("Login")')
    page.wait_for_load_state('networkidle')

    # Navigate to profile creation page
    page.goto(f"{live_server_url}/en/create-profile/")
    page.wait_for_load_state('networkidle')
    page.wait_for_timeout(1000)  # Give page time to fully render

    # Print current URL for debugging
    print(f"\nCurrent URL: {page.url}")
    print(f"Page title: {page.title()}")

    # Find phone field by different possible selectors
    phone_selectors = [
        'input[type="tel"]',
        'input[name="phone"]',
        'input[placeholder*="phone"]',
        'input[placeholder*="Phone"]',
        '#id_phone',
        'div:has-text("Phone Number") input'
    ]

    phone_field = None
    for selector in phone_selectors:
        try:
            element = page.locator(selector).first
            if element.count() > 0:
                phone_field = element
                print(f"[OK] Found phone field with selector: {selector}")
                break
        except:
            continue

    if not phone_field:
        print("[WARNING] Could not find phone field with known selectors")
        print("Looking for any input fields...")
        inputs = page.locator('input').all()
        print(f"Found {len(inputs)} input fields total")

    # Take full page screenshot
    page.screenshot(path='screenshots/mobile_profile_creation_full.png', full_page=True)
    print("\n[OK] Full page screenshot saved: screenshots/mobile_profile_creation_full.png")

    # Scroll to phone section if found
    if phone_field:
        phone_field.scroll_into_view_if_needed()
        page.wait_for_timeout(500)  # Wait for scroll animation

        # Take screenshot focused on phone field area
        try:
            # Get parent container
            phone_container = page.locator('div:has-text("Phone Number")').first
            phone_container.screenshot(path='screenshots/mobile_phone_field_focused.png')
            print("[OK] Phone field screenshot saved: screenshots/mobile_phone_field_focused.png")
        except Exception as e:
            print(f"Could not take focused screenshot: {e}")

        # Print field attributes for debugging
        try:
            phone_type = phone_field.get_attribute('type')
            phone_placeholder = phone_field.get_attribute('placeholder')
            phone_name = phone_field.get_attribute('name')
            phone_id = phone_field.get_attribute('id')
            print(f"\nPhone field attributes:")
            print(f"  Name: {phone_name}")
            print(f"  ID: {phone_id}")
            print(f"  Type: {phone_type}")
            print(f"  Placeholder: {phone_placeholder}")
        except Exception as e:
            print(f"Error getting phone field attributes: {e}")

        # Get computed styles
        try:
            styles = page.evaluate("""
                (selector) => {
                    const phoneInput = document.querySelector(selector);
                    if (!phoneInput) return null;
                    const computed = window.getComputedStyle(phoneInput);
                    return {
                        width: computed.width,
                        height: computed.height,
                        fontSize: computed.fontSize,
                        padding: computed.padding,
                        border: computed.border,
                        display: computed.display,
                        visibility: computed.visibility
                    };
                }
            """, 'input[type="tel"]')
            if styles:
                print(f"\nPhone field computed styles:")
                for key, value in styles.items():
                    print(f"  {key}: {value}")
        except Exception as e:
            print(f"Error getting computed styles: {e}")

    print(f"\nPage URL: {page.url}")
    print(f"Viewport: {page.viewport_size}")
