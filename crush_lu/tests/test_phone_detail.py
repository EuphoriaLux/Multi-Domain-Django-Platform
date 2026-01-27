"""Take a detailed screenshot of the phone input section."""
import pytest
from playwright.sync_api import Page
from django.contrib.auth import get_user_model

User = get_user_model()


@pytest.mark.django_db
@pytest.mark.playwright
def test_phone_input_detailed(page: Page, live_server):
    """Take detailed screenshot of phone input on mobile."""
    # Create test user
    user = User.objects.create_user(
        username='phonedetal@test.lu',
        email='phonedetail@test.lu',
        password='testpass123',
        first_name='Phone',
        last_name='Detail'
    )

    # Set mobile viewport (iPhone 12)
    page.set_viewport_size({"width": 390, "height": 844})

    # Navigate to login
    page.goto(f"{live_server.url}/accounts/login/")
    page.wait_for_load_state('networkidle')

    # Login
    page.fill('input[name="login"]', 'phonedetail@test.lu')
    page.fill('input[name="password"]', 'testpass123')
    page.locator('button:has-text("Sign In"), button:has-text("Login")').first.click()
    page.wait_for_timeout(2000)

    # Navigate to create profile
    page.goto(f"{live_server.url}/en/create-profile/")
    page.wait_for_load_state('networkidle')
    page.wait_for_timeout(1000)

    # Scroll to phone section
    phone_section = page.locator('#div_id_phone')
    phone_section.scroll_into_view_if_needed()
    page.wait_for_timeout(500)

    # Take full page screenshot
    page.screenshot(path="screenshots/phone_detail_full.png", full_page=True)
    print("Screenshot: phone_detail_full.png")

    # Take screenshot of just the phone section
    phone_section.screenshot(path="screenshots/phone_detail_section.png")
    print("Screenshot: phone_detail_section.png")

    # Also capture the parent container to see the full context
    basic_info = page.locator('.card').first
    basic_info.screenshot(path="screenshots/phone_detail_card.png")
    print("Screenshot: phone_detail_card.png")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "--headed"])
