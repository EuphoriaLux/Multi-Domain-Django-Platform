"""
Mobile UI Tests for Crush.lu

Tests mobile-specific UI issues including:
- Profile creation form phone number field layout
- Language switcher functionality in mobile view

Run with: pytest crush_lu/tests/test_mobile_ui.py -v -m playwright
"""
import pytest
from pathlib import Path

# Skip all tests if playwright is not installed
pytest.importorskip("playwright")

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(scope="module")
def screenshot_dir():
    """Create and return the screenshots directory."""
    path = Path("crush_lu/tests/screenshots")
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.mark.playwright
class TestMobileProfileCreation:
    """Test mobile view of profile creation page."""

    MOBILE_VIEWPORT = {"width": 390, "height": 844}  # iPhone 12 Pro

    def test_phone_number_field_mobile_layout(
        self, page, live_server, screenshot_dir
    ):
        """Test phone number field layout on mobile viewport."""
        # Set mobile viewport
        page.set_viewport_size(self.MOBILE_VIEWPORT)

        # Go to signup page which has phone field
        page.goto(f"{live_server.url}/en/signup/")
        page.wait_for_load_state("networkidle")

        # Take full page screenshot
        page.screenshot(
            path=str(screenshot_dir / "mobile_profile_creation_full.png"),
            full_page=True
        )

        # Find phone number field
        phone_field = page.locator('input[name="phone"], input[id*="phone"], input[type="tel"]')

        if phone_field.count() > 0:
            phone_input = phone_field.first

            # Scroll to phone field
            phone_input.scroll_into_view_if_needed()
            page.wait_for_timeout(500)

            # Take screenshot of phone field area
            page.screenshot(
                path=str(screenshot_dir / "mobile_phone_field.png")
            )

            # Get bounding box and check if it's within viewport
            box = phone_input.bounding_box()
            if box:
                print(f"\nPhone field position: x={box['x']}, y={box['y']}")
                print(f"Phone field size: width={box['width']}, height={box['height']}")
                print(f"Viewport width: {self.MOBILE_VIEWPORT['width']}")

                # Check if field extends beyond viewport
                field_right_edge = box['x'] + box['width']
                if field_right_edge > self.MOBILE_VIEWPORT['width']:
                    print(f"WARNING: Phone field extends beyond viewport!")
                    print(f"Field right edge: {field_right_edge}px")
                    print(f"Overflow: {field_right_edge - self.MOBILE_VIEWPORT['width']}px")

            # Get computed styles
            styles = phone_input.evaluate(
                """el => {
                    const cs = window.getComputedStyle(el);
                    return {
                        width: cs.width,
                        maxWidth: cs.maxWidth,
                        padding: cs.padding,
                        boxSizing: cs.boxSizing,
                        display: cs.display
                    };
                }"""
            )
            print(f"\nPhone field styles: {styles}")

            # Check parent container
            parent_styles = phone_input.evaluate(
                """el => {
                    const parent = el.parentElement;
                    const cs = window.getComputedStyle(parent);
                    return {
                        width: cs.width,
                        maxWidth: cs.maxWidth,
                        display: cs.display,
                        className: parent.className
                    };
                }"""
            )
            print(f"Parent container styles: {parent_styles}")

            # Test if field is usable (can focus and type)
            phone_input.click()
            phone_input.fill("+352691234567")

            # Verify value was entered
            value = phone_input.input_value()
            assert "+352691234567" in value, "Could not enter phone number"

        else:
            print("\nWARNING: Phone number field not found on page")
            print(f"Page URL: {page.url}")


@pytest.mark.playwright
class TestMobileLanguageSwitcher:
    """Test language switcher functionality in mobile view."""

    MOBILE_VIEWPORT = {"width": 390, "height": 844}  # iPhone 12 Pro

    def test_language_switcher_mobile_menu(self, page, live_server, screenshot_dir):
        """Test language switcher in mobile navigation menu."""
        # Set mobile viewport
        page.set_viewport_size(self.MOBILE_VIEWPORT)

        # Go to home page (English)
        page.goto(f"{live_server.url}/en/")
        page.wait_for_load_state("networkidle")

        # Take initial screenshot
        page.screenshot(
            path=str(screenshot_dir / "mobile_home_english.png")
        )

        # Verify we're on English page
        assert "/en/" in page.url, f"Not on English page. URL: {page.url}"

        # Look for mobile menu button (hamburger icon)
        mobile_menu_selectors = [
            "[data-mobile-menu-button]",
            "button[aria-label*='menu' i]",
            "button[aria-label*='Menu' i]",
            "button[aria-expanded]",
            ".mobile-menu-button",
            "[x-on\\:click*='mobileMenuOpen']",
            "button svg.w-6.h-6",  # Common hamburger icon style
        ]

        mobile_menu_btn = None
        for selector in mobile_menu_selectors:
            try:
                btn = page.locator(selector).first
                if btn.is_visible():
                    mobile_menu_btn = btn
                    print(f"\nFound mobile menu button with selector: {selector}")
                    break
            except:
                continue

        if mobile_menu_btn:
            # Click mobile menu button
            mobile_menu_btn.click()
            page.wait_for_timeout(500)  # Wait for Alpine.js transition

            # Take screenshot with menu open
            page.screenshot(
                path=str(screenshot_dir / "mobile_menu_open.png")
            )

            # Look for language switcher in mobile menu
            language_switcher_selectors = [
                "select[name='language']",
                "select#mobile-language-select",
                "[data-language-switcher]",
                ".language-switcher select",
                "form[action*='language'] select",
            ]

            language_select = None
            for selector in language_switcher_selectors:
                try:
                    select = page.locator(selector).first
                    if select.is_visible():
                        language_select = select
                        print(f"Found language selector: {selector}")
                        break
                except:
                    continue

            if language_select:
                # Get available options
                options = language_select.locator("option").all()
                print(f"\nAvailable language options: {len(options)}")
                for opt in options:
                    value = opt.get_attribute("value")
                    text = opt.inner_text()
                    print(f"  - {text} (value: {value})")

                # Select German
                language_select.select_option("de")
                page.wait_for_timeout(500)

                # Take screenshot after selection
                page.screenshot(
                    path=str(screenshot_dir / "mobile_language_selected_de.png")
                )

                # Look for submit button or check if auto-submit
                submit_btn = page.locator(
                    "button[type='submit']"
                ).filter(has_text="OK").first

                if submit_btn.is_visible():
                    submit_btn.click()
                    page.wait_for_load_state("networkidle")
                else:
                    # Try auto-submit (might use Alpine.js @change)
                    page.wait_for_timeout(1000)

                # Wait for navigation
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(1000)

                # Take screenshot of German page
                page.screenshot(
                    path=str(screenshot_dir / "mobile_home_german.png")
                )

                # Verify URL changed to /de/
                current_url = page.url
                print(f"\nCurrent URL after language switch: {current_url}")

                assert "/de/" in current_url, f"URL did not change to German. Current: {current_url}"

                # Verify page content changed (look for German text)
                page_content = page.content()

                # Common German indicators
                german_indicators = [
                    "Über uns",
                    "Veranstaltungen",
                    "Anmelden",
                    "Profil",
                ]

                has_german = any(indicator in page_content for indicator in german_indicators)
                print(f"Page contains German text: {has_german}")

                if not has_german:
                    print("WARNING: Page might not have changed to German")
                    print(f"First 500 chars: {page_content[:500]}")

            else:
                print("\nWARNING: Language select not found in mobile menu")

                # Debug: Print all visible select elements
                all_selects = page.locator("select").all()
                print(f"All select elements on page: {len(all_selects)}")
                for i, select in enumerate(all_selects):
                    if select.is_visible():
                        print(f"  Select {i}: visible")

        else:
            print("\nWARNING: Mobile menu button not found")

            # Debug: List all buttons
            all_buttons = page.locator("button").all()
            print(f"All buttons on page: {len(all_buttons)}")
            for i, btn in enumerate(all_buttons[:10]):  # First 10
                if btn.is_visible():
                    aria_label = btn.get_attribute("aria-label") or "no aria-label"
                    print(f"  Button {i}: {aria_label}")

    def test_language_switcher_desktop_dropdown(self, page, live_server, screenshot_dir):
        """Test desktop language switcher dropdown (should also work on mobile)."""
        # Set mobile viewport
        page.set_viewport_size(self.MOBILE_VIEWPORT)

        page.goto(f"{live_server.url}/en/")
        page.wait_for_load_state("networkidle")

        # Look for desktop language switcher (globe icon button)
        desktop_lang_btn = page.locator(
            "button[aria-label*='language' i], "
            "button svg.lucide-globe, "
            "[data-language-dropdown-button]"
        ).first

        if desktop_lang_btn.is_visible():
            print("\nDesktop language switcher visible on mobile")

            desktop_lang_btn.click()
            page.wait_for_timeout(500)

            page.screenshot(
                path=str(screenshot_dir / "mobile_desktop_lang_dropdown.png")
            )

            # Look for German link/button
            de_link = page.locator("a[href*='/de/'], button[data-lang='de']").first

            if de_link.is_visible():
                de_link.click()
                page.wait_for_load_state("networkidle")

                # Verify navigation
                assert "/de/" in page.url, f"Desktop switcher failed. URL: {page.url}"
                print(f"Desktop language switcher worked! URL: {page.url}")
            else:
                print("German option not found in dropdown")
        else:
            print("\nDesktop language switcher not visible on mobile (expected)")
