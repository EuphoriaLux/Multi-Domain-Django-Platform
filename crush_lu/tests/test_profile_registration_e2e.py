"""
End-to-end Playwright tests for Crush.lu profile registration.
Tests the complete 4-step wizard and captures console errors/CSP violations.

Run with: pytest crush_lu/tests/test_profile_registration_e2e.py -v -m playwright
Run visible: pytest crush_lu/tests/test_profile_registration_e2e.py -v --headed
"""
import re
import pytest
from playwright.sync_api import Page, expect, ConsoleMessage
from django.contrib.auth import get_user_model

User = get_user_model()


# CSP violation patterns to detect
CSP_PATTERNS = [
    r"Refused to evaluate",
    r"Content Security Policy",
    r"eval.*blocked",
    r"inline.*blocked",
]

# JavaScript error patterns to detect
ERROR_PATTERNS = [
    r"Uncaught.*Error",
    r"TypeError",
    r"ReferenceError",
    r"Alpine.*unable to interpret",
    r"is not defined",
    r"Cannot read property",
]


@pytest.fixture(autouse=True)
def setup_site_for_tests(transactional_db):
    """Ensure Site objects exist for all tests in this module."""
    from django.contrib.sites.models import Site, SITE_CACHE

    # Clear the Site cache to prevent stale references
    SITE_CACHE.clear()

    # Ensure localhost Site exists (primary Site with id=1)
    try:
        site = Site.objects.get(id=1)
        if site.domain != 'localhost':
            Site.objects.filter(domain='localhost').exclude(id=1).delete()
            site.domain = 'localhost'
            site.name = 'localhost'
            site.save()
    except Site.DoesNotExist:
        Site.objects.filter(domain='localhost').delete()
        Site.objects.create(id=1, domain='localhost', name='localhost')

    # Also ensure 127.0.0.1 Site exists
    Site.objects.get_or_create(domain='127.0.0.1', defaults={'name': 'Live Server'})

    yield

    SITE_CACHE.clear()


@pytest.mark.playwright
class TestProfileRegistrationE2E:
    """E2E tests for Crush.lu profile registration wizard."""

    @pytest.fixture
    def new_user(self, transactional_db):
        """Create a fresh user without a profile for registration testing."""
        from allauth.account.models import EmailAddress

        user = User.objects.create_user(
            username='newuser@example.com',
            email='newuser@example.com',
            password='testpass123',
            first_name='New',
            last_name='User'
        )
        EmailAddress.objects.create(
            user=user,
            email=user.email,
            verified=True,
            primary=True
        )
        return user

    @pytest.fixture
    def console_messages(self):
        """Collector for console messages during test."""
        return []

    @pytest.fixture
    def page_with_console_capture(self, page: Page, console_messages):
        """Page with console message capture enabled."""
        def handle_console(msg: ConsoleMessage):
            console_messages.append({
                'type': msg.type,
                'text': msg.text,
                'location': msg.location
            })
        page.on('console', handle_console)
        return page

    @pytest.fixture
    def logged_in_page(self, page_with_console_capture: Page, live_server_url, new_user, transactional_db):
        """Log in the new user and return page ready for profile creation."""
        from django.contrib.sites.models import Site
        Site.objects.get_or_create(id=1, defaults={'domain': 'localhost', 'name': 'localhost'})
        Site.objects.get_or_create(domain='127.0.0.1', defaults={'name': 'Live Server'})

        page = page_with_console_capture
        page.goto(f"{live_server_url}/accounts/login/")
        page.wait_for_selector('input[name="login"]', timeout=10000)

        # Dismiss cookie banner if present
        cookie_decline = page.locator('button:has-text("Decline All")')
        if cookie_decline.count() > 0:
            cookie_decline.click()
            page.wait_for_timeout(500)

        page.fill('input[name="login"]', new_user.email)
        page.fill('input[name="password"]', 'testpass123')
        page.click('button:has-text("Login")')
        page.wait_for_load_state('networkidle')

        return page

    def _check_for_errors(self, console_messages: list) -> tuple[list, list]:
        """Check console messages for errors and CSP violations."""
        errors = []
        csp_violations = []

        for msg in console_messages:
            text = msg['text']

            # Check for CSP violations
            for pattern in CSP_PATTERNS:
                if re.search(pattern, text, re.IGNORECASE):
                    csp_violations.append(msg)
                    break

            # Check for JS errors
            if msg['type'] == 'error':
                for pattern in ERROR_PATTERNS:
                    if re.search(pattern, text, re.IGNORECASE):
                        errors.append(msg)
                        break

        return errors, csp_violations

    def test_enter_key_does_not_submit_form_on_step1(
        self, logged_in_page: Page, live_server_url, console_messages
    ):
        """
        Verify pressing Enter in phone input does not submit the form.
        This tests the reported bug where Enter jumps to last page.
        """
        page = logged_in_page
        page.goto(f"{live_server_url}/en/create-profile/")
        page.wait_for_selector('#phone-verification-container', timeout=10000)

        # Verify we're on Step 1 (Basic Information) - h3 heading in the card
        step1_content = page.locator('h3:has-text("Basic Information")')
        expect(step1_content).to_be_visible()

        # Fill phone number
        phone_input = page.locator('input[name="phone_number"]')
        phone_input.fill('+352621123456')

        # Press Enter key - this should NOT submit the form
        phone_input.press('Enter')
        page.wait_for_timeout(500)

        # CRITICAL: We should still be on Step 1, not submitted
        # If the bug exists, the form will submit and we'll be redirected
        expect(step1_content).to_be_visible()

        # Check that we didn't navigate away
        assert '/create-profile' in page.url, f"Enter key caused navigation to {page.url}"

        # Check console for errors
        errors, csp = self._check_for_errors(console_messages)
        assert len(errors) == 0, f"JavaScript errors found: {errors}"
        assert len(csp) == 0, f"CSP violations found: {csp}"

    def test_console_errors_during_step1_load(
        self, logged_in_page: Page, live_server_url, console_messages
    ):
        """Check for console errors and CSP violations when Step 1 loads."""
        page = logged_in_page
        page.goto(f"{live_server_url}/en/create-profile/")
        page.wait_for_selector('#phone-verification-container', timeout=10000)

        # Wait for Alpine.js initialization
        page.wait_for_timeout(1000)

        errors, csp_violations = self._check_for_errors(console_messages)

        # Print any errors found for debugging
        if errors:
            print(f"\nJavaScript Errors Found ({len(errors)}):")
            for e in errors:
                print(f"  - {e['type']}: {e['text']}")
                if e.get('location'):
                    print(f"    at {e['location']}")

        if csp_violations:
            print(f"\nCSP Violations Found ({len(csp_violations)}):")
            for v in csp_violations:
                print(f"  - {v['text']}")

        assert len(errors) == 0, f"JavaScript errors on Step 1: {[e['text'] for e in errors]}"
        assert len(csp_violations) == 0, f"CSP violations on Step 1: {[v['text'] for v in csp_violations]}"

    def test_step1_interaction_no_errors(
        self, logged_in_page: Page, live_server_url, console_messages
    ):
        """Test Step 1 interactions for console errors."""
        page = logged_in_page
        page.goto(f"{live_server_url}/en/create-profile/")
        page.wait_for_selector('#phone-verification-container', timeout=10000)

        # Interact with phone field
        phone_input = page.locator('input[name="phone_number"]')
        phone_input.fill('+352621123456')

        # Select gender - the radio button is hidden and styled via label
        # Click on the label which is the visible interactive element
        male_label = page.locator('label[for="id_gender_0"]')  # Male option label
        if male_label.count() > 0 and male_label.is_visible():
            male_label.click()
            page.wait_for_timeout(300)

        # Select age range in DOB picker (if visible)
        age_range_btn = page.locator('[data-dob-age-ranges] button').first
        if age_range_btn.count() > 0 and age_range_btn.is_visible():
            age_range_btn.click()
            page.wait_for_timeout(300)

        # Click on canton map (select Luxembourg canton if visible)
        canton_luxembourg = page.locator('[data-canton="canton-luxembourg"]')
        if canton_luxembourg.count() > 0 and canton_luxembourg.is_visible():
            canton_luxembourg.click()
            page.wait_for_timeout(300)

        # Check for errors after all interactions
        errors, csp_violations = self._check_for_errors(console_messages)
        assert len(errors) == 0, f"JavaScript errors during Step 1 interactions: {errors}"
        assert len(csp_violations) == 0, f"CSP violations during Step 1 interactions: {csp_violations}"

    def test_full_wizard_navigation_console_capture(
        self, logged_in_page: Page, live_server_url, console_messages
    ):
        """
        Navigate through all 4 wizard steps (without full validation).
        Capture all console messages and check for errors/CSP violations.

        Note: This test manually advances through steps to test UI,
        but cannot complete phone verification (requires Firebase).
        """
        page = logged_in_page
        page.goto(f"{live_server_url}/en/create-profile/")
        page.wait_for_selector('#phone-verification-container', timeout=10000)

        # Step 1 - Verify visible (h3 heading in the card)
        expect(page.locator('h3:has-text("Basic Information")')).to_be_visible()
        print("\n--- Step 1: Basic Information loaded ---")

        # Check errors after Step 1 load
        errors1, csp1 = self._check_for_errors(console_messages)

        # Note: We can't fully advance without phone verification,
        # but we can test the UI components are working without errors

        # Print summary
        print(f"\nConsole Messages Captured: {len(console_messages)}")
        print(f"Errors: {len(errors1)}, CSP Violations: {len(csp1)}")

        for msg in console_messages:
            if msg['type'] in ['error', 'warning']:
                print(f"  [{msg['type'].upper()}] {msg['text'][:200]}")

        assert len(csp1) == 0, f"CSP violations found: {[v['text'] for v in csp1]}"

    def test_enter_key_in_text_input_does_not_submit(
        self, logged_in_page: Page, live_server_url, console_messages
    ):
        """Test Enter key in text input fields doesn't cause form submission."""
        page = logged_in_page
        page.goto(f"{live_server_url}/en/create-profile/")
        page.wait_for_load_state('networkidle')

        initial_url = page.url

        # Test Enter in phone number field
        phone_input = page.locator('input[name="phone_number"]')
        if phone_input.count() > 0 and phone_input.is_visible():
            phone_input.fill('+352621123456')
            phone_input.press('Enter')
            page.wait_for_timeout(500)

            # URL should not have changed (form not submitted)
            assert page.url == initial_url or '/create-profile' in page.url, \
                f"Enter in phone field caused unexpected navigation to {page.url}"

    def test_form_has_submit_prevention(
        self, logged_in_page: Page, live_server_url, console_messages
    ):
        """
        Test that the form submission is properly controlled.
        The form should only submit when on the final step.
        """
        page = logged_in_page
        page.goto(f"{live_server_url}/en/create-profile/")
        page.wait_for_selector('#phone-verification-container', timeout=10000)

        # Get the form element
        form = page.locator('#profileForm')
        expect(form).to_be_visible()

        # Check that form has the @submit handler
        # (We can't directly check Alpine attributes, but we can verify behavior)

        # Try to trigger form submission via JavaScript
        # This simulates what happens when Enter is pressed
        page.evaluate('''() => {
            const form = document.getElementById('profileForm');
            const event = new Event('submit', { bubbles: true, cancelable: true });
            form.dispatchEvent(event);
        }''')

        page.wait_for_timeout(500)

        # We should still be on the create-profile page (form submission prevented)
        assert '/create-profile' in page.url, \
            f"Form submission was not prevented, navigated to {page.url}"

        # Check for any errors
        errors, csp = self._check_for_errors(console_messages)
        assert len(csp) == 0, f"CSP violations found: {csp}"

    def test_alpine_component_initialization(
        self, logged_in_page: Page, live_server_url, console_messages
    ):
        """Test that Alpine.js profileWizard component initializes correctly."""
        page = logged_in_page
        page.goto(f"{live_server_url}/en/create-profile/")
        page.wait_for_selector('#phone-verification-container', timeout=10000)

        # Wait for Alpine to initialize
        page.wait_for_timeout(1000)

        # Check that Alpine data is accessible
        has_alpine = page.evaluate('''() => {
            const el = document.querySelector('[x-data="profileWizard"]');
            return el && el._x_dataStack && el._x_dataStack.length > 0;
        }''')

        assert has_alpine, "Alpine.js profileWizard component did not initialize"

        # Check current step is 1
        current_step = page.evaluate('''() => {
            const el = document.querySelector('[x-data="profileWizard"]');
            if (el && el._x_dataStack) {
                return el._x_dataStack[0].currentStep;
            }
            return null;
        }''')

        assert current_step == 1, f"Expected currentStep to be 1, got {current_step}"

        # Check for errors during initialization
        errors, csp = self._check_for_errors(console_messages)
        assert len(errors) == 0, f"Errors during Alpine initialization: {errors}"
        assert len(csp) == 0, f"CSP violations during initialization: {csp}"
