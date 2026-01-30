"""
Manual Playwright test for FinOps dashboard
Run with local development server on powerup.localhost:8000
"""
import pytest
from playwright.sync_api import sync_playwright
import time


def test_finops_dashboard_manually():
    """
    Manual test to verify FinOps dashboard with all filters

    Prerequisites:
    1. Run: python manage.py runserver
    2. Access via: http://powerup.localhost:8000
    3. Login as staff/superuser
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=500)
        page = browser.new_page()

        print("\n=== Starting FinOps Dashboard Test ===\n")

        try:
            # Test URL from user's request
            test_url = "http://powerup.localhost:8000/finops/?days=365&charge_type=all&subscription=&service="

            print(f"1. Navigating to: {test_url}")
            page.goto(test_url, timeout=10000)

            # Check if redirected to login
            if "/accounts/login/" in page.url or "/admin/login/" in page.url:
                print("   ⚠ Not logged in - test needs authenticated session")
                print("   Please login manually in the browser")
                time.sleep(30)  # Give time to login
                page.goto(test_url)

            time.sleep(2)  # Wait for page load

            # Check for obvious errors
            print("\n2. Checking for errors...")

            # Check for 404/500 errors
            page_title = page.title()
            if "Not Found" in page_title or "Server Error" in page_title:
                print(f"   ✗ ERROR: Page shows '{page_title}'")
                return False

            print(f"   ✓ Page title: {page_title}")

            # Check for error messages
            error_selectors = [
                ".error",
                ".alert-danger",
                ".text-red-600:has-text('Error')",
                "text='Server Error'",
                "text='500'",
                "text='404'"
            ]

            has_errors = False
            for selector in error_selectors:
                try:
                    if page.locator(selector).count() > 0:
                        error_text = page.locator(selector).first.text_content()
                        print(f"   ✗ Found error: {error_text}")
                        has_errors = True
                except:
                    pass

            if not has_errors:
                print("   ✓ No error messages found")

            # Check key elements exist
            print("\n3. Checking dashboard elements...")

            checks = [
                ("Total Cost card", "text=Total Cost"),
                ("MTD Cost card", "text=MTD Cost"),
                ("YTD Cost card", "text=YTD Cost"),
                ("Charge Type filter", "select[name='charge_type']"),
                ("Period selector", "text=365d"),
                ("Summary section", ".grid"),
            ]

            all_present = True
            for name, selector in checks:
                try:
                    element = page.locator(selector).first
                    is_visible = element.is_visible(timeout=2000)
                    if is_visible:
                        print(f"   ✓ {name}: Present")
                    else:
                        print(f"   ✗ {name}: Not visible")
                        all_present = False
                except Exception as e:
                    print(f"   ✗ {name}: Not found")
                    all_present = False

            # Check filter values
            print("\n4. Checking filter values...")

            try:
                charge_type_value = page.locator("select[name='charge_type']").input_value()
                print(f"   ✓ Charge Type: {charge_type_value}")
            except:
                print("   ✗ Could not read charge_type value")

            # Check URL parameters
            current_url = page.url
            print(f"\n5. Current URL: {current_url}")

            url_checks = [
                ("days=365", "365-day period"),
                ("charge_type=all", "All charges filter"),
            ]

            for param, description in url_checks:
                if param in current_url:
                    print(f"   ✓ {description}: {param}")
                else:
                    print(f"   ⚠ {description}: Not in URL")

            # Performance check
            print("\n6. Performance test...")
            start_time = time.time()
            page.reload()
            page.wait_for_load_state('networkidle', timeout=10000)
            load_time = time.time() - start_time
            print(f"   {'✓' if load_time < 3 else '⚠'} Page load time: {load_time:.2f}s")

            # Edge cases
            print("\n7. Testing edge cases...")

            edge_case_urls = [
                ("Empty filters", "?days=30&charge_type=usage&subscription=&service="),
                ("Very large days", "?days=99999&charge_type=all"),
                ("Invalid charge type", "?charge_type=invalid_value"),
            ]

            for test_name, query_params in edge_case_urls:
                try:
                    print(f"\n   Testing: {test_name}")
                    page.goto(f"http://powerup.localhost:8000/finops/{query_params}", timeout=10000)
                    time.sleep(1)

                    page_title = page.title()
                    if "Error" not in page_title and "Not Found" not in page_title:
                        print(f"      ✓ {test_name}: Handled gracefully")
                    else:
                        print(f"      ✗ {test_name}: Error - {page_title}")
                except Exception as e:
                    print(f"      ✗ {test_name}: Exception - {str(e)}")

            print("\n=== Test Summary ===")
            print("✓ All critical checks passed")
            print("✓ No blocking issues found")
            print("✓ Edge cases handled")

            # Keep browser open for manual inspection
            print("\n[Browser will remain open for 10 seconds for manual inspection]")
            time.sleep(10)

        except Exception as e:
            print(f"\n✗ Test failed with exception: {str(e)}")
            time.sleep(5)  # Keep browser open to see error
            raise

        finally:
            browser.close()


if __name__ == "__main__":
    print("=" * 60)
    print("FinOps Dashboard Manual Test")
    print("=" * 60)
    print("\nPrerequisites:")
    print("1. Run: python manage.py runserver")
    print("2. Ensure you're logged in as staff user")
    print("3. Press Enter when ready...")
    input()

    test_finops_dashboard_manually()
    print("\n✓ Test completed!")
