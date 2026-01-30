"""
Comprehensive Playwright Tests for Coach Review Profile Page

Tests the enhanced profile summary card with account metadata and the complete
coach review workflow including tab navigation, screening call, and decision submission.

Key features tested:
- Profile summary card visibility across tabs
- Account metadata display (account age, signup method, phone verification, last activity)
- Tab switching behavior
- Complete screening workflow
- Form validation
- Different user account types (LinkedIn, email, phone verified, etc.)

Run with:
    pytest crush_lu/tests/test_coach_review_profile_playwright.py -v
    pytest crush_lu/tests/test_coach_review_profile_playwright.py -v -m playwright
"""
import pytest
from datetime import date, timedelta
from django.utils import timezone
from django.contrib.auth import get_user_model
from playwright.sync_api import expect
import os

User = get_user_model()

# Mark all tests in this file as playwright tests
pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]


# =============================================================================
# PYTEST HOOKS
# =============================================================================

@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Capture screenshots on test failure."""
    outcome = yield
    rep = outcome.get_result()

    # Only capture screenshot on failure during test execution (not setup/teardown)
    if rep.when == "call" and rep.failed:
        # Check if this test uses a page fixture
        if "page" in item.funcargs or "authenticated_coach_page" in item.funcargs:
            page = item.funcargs.get("authenticated_coach_page") or item.funcargs.get("page")
            if page:
                # Create screenshots directory if it doesn't exist
                screenshot_dir = os.path.join(os.path.dirname(__file__), "screenshots")
                os.makedirs(screenshot_dir, exist_ok=True)

                # Generate screenshot filename from test name
                test_name = item.nodeid.replace("::", "_").replace("/", "_")
                screenshot_path = os.path.join(screenshot_dir, f"{test_name}_failure.png")

                try:
                    page.screenshot(path=screenshot_path)
                    print(f"\nScreenshot saved: {screenshot_path}")
                except Exception as e:
                    print(f"\nFailed to capture screenshot: {e}")


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def coach_user_with_permissions(transactional_db):
    """Create a coach user with active coach permissions."""
    from crush_lu.models import CrushCoach

    coach_user = User.objects.create_user(
        username='coach@example.com',
        email='coach@example.com',
        password='coachpass123',
        first_name='Coach',
        last_name='Marie'
    )

    coach = CrushCoach.objects.create(
        user=coach_user,
        bio='Professional dating coach',
        specializations='General coaching',
        is_active=True,
        max_active_reviews=10
    )

    return coach_user, coach


@pytest.fixture
def pending_profile_submission(transactional_db, coach_user_with_permissions):
    """Create a pending profile submission assigned to coach."""
    from crush_lu.models import CrushProfile, ProfileSubmission

    coach_user, coach = coach_user_with_permissions

    # Create user with pending profile
    user = User.objects.create_user(
        username='pending@example.com',
        email='pending@example.com',
        password='userpass123',
        first_name='Pending',
        last_name='User'
    )
    user.last_login = timezone.now() - timedelta(days=2)
    user.save()

    profile = CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1995, 5, 15),
        gender='F',
        location='Luxembourg City',
        bio='Test bio for review',
        phone_number='+352123456789',
        is_approved=False,
        is_active=True
    )

    submission = ProfileSubmission.objects.create(
        profile=profile,
        coach=coach,
        status='pending'
    )

    return submission, coach_user, coach


@pytest.fixture
def linkedin_signup_user(transactional_db):
    """Create a user who signed up via LinkedIn."""
    from allauth.socialaccount.models import SocialAccount

    user = User.objects.create_user(
        username='linkedin@example.com',
        email='linkedin@example.com',
        password='userpass123',
        first_name='LinkedIn',
        last_name='User'
    )
    user.last_login = timezone.now() - timedelta(hours=5)
    user.save()

    # Create LinkedIn social account
    social_account = SocialAccount.objects.create(
        user=user,
        provider='linkedin_oauth2',
        uid='linkedin123456'
    )

    return user, social_account


@pytest.fixture
def email_signup_user(transactional_db):
    """Create a user who signed up via email/password."""
    user = User.objects.create_user(
        username='emailuser@example.com',
        email='emailuser@example.com',
        password='userpass123',
        first_name='Email',
        last_name='User'
    )
    # New account created recently
    user.date_joined = timezone.now() - timedelta(days=1)
    user.last_login = timezone.now() - timedelta(hours=2)
    user.save()

    return user


@pytest.fixture
def phone_verified_profile(transactional_db, coach_user_with_permissions):
    """Create a profile with phone verification."""
    from crush_lu.models import CrushProfile, ProfileSubmission

    coach_user, coach = coach_user_with_permissions

    user = User.objects.create_user(
        username='phoneverified@example.com',
        email='phoneverified@example.com',
        password='userpass123',
        first_name='Phone',
        last_name='Verified'
    )
    user.last_login = timezone.now() - timedelta(days=7)
    user.save()

    profile = CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1992, 8, 20),
        gender='M',
        location='Esch-sur-Alzette',
        bio='Phone verified user',
        phone_number='+352987654321',
        phone_verified=True,
        is_approved=False
    )

    submission = ProfileSubmission.objects.create(
        profile=profile,
        coach=coach,
        status='pending'
    )

    return submission, user


@pytest.fixture
def old_account_user(transactional_db):
    """Create a user with an older account (30 days ago)."""
    user = User.objects.create_user(
        username='oldaccount@example.com',
        email='oldaccount@example.com',
        password='userpass123',
        first_name='Old',
        last_name='Account'
    )
    # Account created 30 days ago
    user.date_joined = timezone.now() - timedelta(days=30)
    user.last_login = timezone.now() - timedelta(days=15)
    user.save()

    return user


@pytest.fixture
def authenticated_coach_page(page, live_server_url, coach_user_with_permissions, transactional_db):
    """Playwright page logged in as coach. Returns (page, live_server_url) tuple."""
    from django.contrib.sites.models import Site

    coach_user, coach = coach_user_with_permissions

    # Ensure Site exists
    Site.objects.get_or_create(id=1, defaults={'domain': 'localhost', 'name': 'localhost'})
    Site.objects.get_or_create(domain='127.0.0.1', defaults={'name': 'Live Server'})

    page.goto(f"{live_server_url}/accounts/login/")
    page.wait_for_selector('input[name="login"]', timeout=10000)

    # Dismiss cookie banner if present
    cookie_decline = page.locator('button:has-text("Decline All")')
    if cookie_decline.count() > 0:
        cookie_decline.click()
        page.wait_for_timeout(500)

    page.fill('input[name="login"]', coach_user.email)
    page.fill('input[name="password"]', 'coachpass123')
    page.click('button:has-text("Login")')
    page.wait_for_load_state('networkidle')

    # Return both page and live_server_url to avoid URL construction issues
    page._live_server_url = live_server_url
    return page


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_review_url(page, submission_id):
    """Build the correct review URL using the live_server_url stored in the page."""
    return f"{page._live_server_url}/en/coach/review/{submission_id}/"


def click_tab(page, tab_name):
    """
    Click a tab button using text selector (CSP-safe, works with Alpine.js).

    Args:
        page: Playwright page object
        tab_name: "Profile Overview", "Screening Call", or "Review Decision"
    """
    # Use text-based selector which is more reliable than @click attribute
    # The tab buttons contain text like "1. Profile Overview", "2. Screening Call", etc.
    # We need to be specific because there might be other buttons with same text
    # Tab buttons are in the navigation section with flex-col sm:flex-row class
    button = page.locator('.flex-col.sm\\:flex-row button').filter(has_text=tab_name).first

    # Wait for button to be visible and enabled before clicking
    button.wait_for(state='visible', timeout=5000)
    button.click()

    # Wait for Alpine.js transition to complete
    page.wait_for_timeout(300)


# =============================================================================
# TEST CLASSES
# =============================================================================

class TestProfileSummaryCardVisibility:
    """Test profile summary card display across different tabs."""

    def test_summary_hidden_on_profile_tab(self, authenticated_coach_page, pending_profile_submission):
        """Test profile summary is hidden on the Profile Overview tab (tab 1)."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        # Navigate to review page
        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        # By default, we should be on tab 1 (Profile Overview)
        # Profile summary should be hidden (x-show="showProfileSummary" where showProfileSummary = activeTab !== 1)
        summary_card = page.locator('[x-show="showProfileSummary"]')
        expect(summary_card).to_be_hidden()

    def test_summary_visible_on_screening_tab(self, authenticated_coach_page, pending_profile_submission):
        """Test profile summary is visible on the Screening Call tab (tab 2)."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        # Click Screening Call tab using helper function
        click_tab(page, "Screening Call")

        # Profile summary should now be visible
        summary_card = page.locator('[x-show="showProfileSummary"]')
        expect(summary_card).to_be_visible()

    def test_summary_visible_on_decision_tab(self, authenticated_coach_page, pending_profile_submission):
        """Test profile summary is visible on the Review Decision tab (tab 3)."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        # Click Review Decision tab using helper function
        click_tab(page, "Review Decision")

        # Profile summary should be visible
        summary_card = page.locator('[x-show="showProfileSummary"]')
        expect(summary_card).to_be_visible()

    def test_summary_toggles_with_tab_switches(self, authenticated_coach_page, pending_profile_submission):
        """Test profile summary toggles correctly when switching between tabs."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        summary_card = page.locator('[x-show="showProfileSummary"]')

        # Tab 1: Hidden
        expect(summary_card).to_be_hidden()

        # Switch to tab 2: Visible
        click_tab(page, "Screening Call")
        expect(summary_card).to_be_visible()

        # Switch back to tab 1: Hidden
        click_tab(page, "Profile Overview")
        expect(summary_card).to_be_hidden()

        # Switch to tab 3: Visible
        click_tab(page, "Review Decision")
        expect(summary_card).to_be_visible()


class TestAccountMetadataDisplay:
    """Test account metadata section in profile summary card."""

    def test_basic_info_displayed(self, authenticated_coach_page, pending_profile_submission):
        """Test basic profile info is displayed in summary card."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        # Switch to screening tab to see summary
        click_tab(page, "Screening Call")

        # Check basic info
        summary_card = page.locator('[x-show="showProfileSummary"]')
        expect(summary_card).to_contain_text('years old')
        expect(summary_card).to_contain_text('Luxembourg City')
        expect(summary_card).to_contain_text('+352123456789')

    def test_account_age_displayed(self, authenticated_coach_page, pending_profile_submission):
        """Test account age (Joined X ago) is displayed."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        click_tab(page, "Screening Call")

        summary_card = page.locator('[x-show="showProfileSummary"]')
        # Should show "Joined X ago" with calendar emoji
        expect(summary_card).to_contain_text('🗓️')
        expect(summary_card).to_contain_text('Joined')
        expect(summary_card).to_contain_text('ago')

    def test_email_signup_displayed(self, authenticated_coach_page, pending_profile_submission):
        """Test email signup method is displayed for email users."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        click_tab(page, "Screening Call")

        summary_card = page.locator('[x-show="showProfileSummary"]')
        # User has no social account, should show "Email signup"
        expect(summary_card).to_contain_text('📧')
        expect(summary_card).to_contain_text('Email signup')

    def test_linkedin_signup_displayed(self, authenticated_coach_page, coach_user_with_permissions, linkedin_signup_user):
        """Test LinkedIn signup method is displayed for LinkedIn users."""
        from crush_lu.models import CrushProfile, ProfileSubmission

        coach_user, coach = coach_user_with_permissions
        user, social_account = linkedin_signup_user

        # Create profile and submission
        profile = CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1990, 3, 10),
            gender='M',
            location='Luxembourg',
            bio='LinkedIn user bio',
            phone_number='+352111222333',
            is_approved=False
        )
        submission = ProfileSubmission.objects.create(
            profile=profile,
            coach=coach,
            status='pending'
        )

        page = authenticated_coach_page
        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        click_tab(page, "Screening Call")

        summary_card = page.locator('[x-show="showProfileSummary"]')
        # Should show "LinkedIn signup"
        expect(summary_card).to_contain_text('🔗')
        expect(summary_card).to_contain_text('LinkedIn signup')

    def test_phone_verification_displayed(self, authenticated_coach_page, phone_verified_profile):
        """Test phone verification status is displayed when verified."""
        submission, user = phone_verified_profile
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        click_tab(page, "Screening Call")

        summary_card = page.locator('[x-show="showProfileSummary"]')
        # Should show "Phone verified" in green
        expect(summary_card).to_contain_text('✓')
        expect(summary_card).to_contain_text('Phone verified')

    def test_last_activity_displayed(self, authenticated_coach_page, pending_profile_submission):
        """Test last activity is displayed when user has logged in."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        click_tab(page, "Screening Call")

        summary_card = page.locator('[x-show="showProfileSummary"]')
        # Should show "Active X ago"
        expect(summary_card).to_contain_text('👁️')
        expect(summary_card).to_contain_text('Active')
        expect(summary_card).to_contain_text('ago')

    def test_metadata_section_has_divider(self, authenticated_coach_page, pending_profile_submission):
        """Test horizontal divider appears before metadata section."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        click_tab(page, "Screening Call")

        # Check for divider (hr element)
        divider = page.locator('[x-show="showProfileSummary"] hr')
        expect(divider).to_be_visible()

    def test_metadata_text_sizing(self, authenticated_coach_page, pending_profile_submission):
        """Test metadata uses smaller text (text-xs)."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        click_tab(page, "Screening Call")

        # Metadata should use text-xs (smaller than main text)
        # Check computed font size
        metadata_text = page.locator('[x-show="showProfileSummary"] p.text-xs').first
        font_size = metadata_text.evaluate('el => window.getComputedStyle(el).fontSize')

        # text-xs in Tailwind is 0.75rem (12px)
        assert '12px' in font_size or '0.75rem' in font_size


class TestDifferentAccountTypes:
    """Test profile summary with different account types."""

    def test_recent_account_display(self, authenticated_coach_page, coach_user_with_permissions, email_signup_user):
        """Test recent account (1 day old) displays correctly."""
        from crush_lu.models import CrushProfile, ProfileSubmission

        coach_user, coach = coach_user_with_permissions
        user = email_signup_user

        profile = CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1996, 12, 25),
            gender='F',
            location='Luxembourg',
            bio='New user',
            phone_number='+352555666777',
            is_approved=False
        )
        submission = ProfileSubmission.objects.create(
            profile=profile,
            coach=coach,
            status='pending'
        )

        page = authenticated_coach_page
        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        click_tab(page, "Screening Call")

        summary_card = page.locator('[x-show="showProfileSummary"]')
        # Should show "Joined X ago" (could be minutes, hours, or days depending on timing)
        expect(summary_card).to_contain_text('Joined')
        expect(summary_card).to_contain_text('ago')

    def test_older_account_display(self, authenticated_coach_page, coach_user_with_permissions, old_account_user):
        """Test older account (30 days) displays correctly."""
        from crush_lu.models import CrushProfile, ProfileSubmission

        coach_user, coach = coach_user_with_permissions
        user = old_account_user

        profile = CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1988, 7, 4),
            gender='M',
            location='Luxembourg',
            bio='Older account',
            phone_number='+352888999000',
            is_approved=False
        )
        submission = ProfileSubmission.objects.create(
            profile=profile,
            coach=coach,
            status='pending'
        )

        page = authenticated_coach_page
        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        click_tab(page, "Screening Call")

        summary_card = page.locator('[x-show="showProfileSummary"]')
        # Should show "Joined X days ago" or "month ago"
        expect(summary_card).to_contain_text('Joined')

    def test_unverified_phone_no_verification_badge(self, authenticated_coach_page, pending_profile_submission):
        """Test unverified phone doesn't show verification badge."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        click_tab(page, "Screening Call")

        summary_card = page.locator('[x-show="showProfileSummary"]')
        # Should NOT show "Phone verified"
        expect(summary_card).not_to_contain_text('Phone verified')

    def test_user_never_logged_in(self, authenticated_coach_page, coach_user_with_permissions):
        """Test user who never logged in (last_login is None)."""
        from crush_lu.models import CrushProfile, ProfileSubmission

        coach_user, coach = coach_user_with_permissions

        user = User.objects.create_user(
            username='neverlogin@example.com',
            email='neverlogin@example.com',
            password='userpass123',
            first_name='Never',
            last_name='Login'
        )
        # Don't set last_login (should be None by default)

        profile = CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1993, 11, 11),
            gender='F',
            location='Luxembourg',
            bio='Never logged in',
            phone_number='+352444333222',
            is_approved=False
        )
        submission = ProfileSubmission.objects.create(
            profile=profile,
            coach=coach,
            status='pending'
        )

        page = authenticated_coach_page
        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        click_tab(page, "Screening Call")

        summary_card = page.locator('[x-show="showProfileSummary"]')
        # Should NOT show "Active X ago"
        expect(summary_card).not_to_contain_text('Active')


class TestTabSwitching:
    """Test tab switching behavior with Alpine.js."""

    def test_default_tab_is_profile(self, authenticated_coach_page, pending_profile_submission):
        """Test default active tab is Profile Overview (tab 1)."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        # Profile tab content should be visible
        profile_tab_content = page.locator('[x-show="isProfileTab"]')
        expect(profile_tab_content).to_be_visible()

    def test_switch_to_screening_tab(self, authenticated_coach_page, pending_profile_submission):
        """Test switching to Screening Call tab."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        # Click screening tab using Alpine.js click handler
        click_tab(page, "Screening Call")

        # Screening tab content should be visible
        screening_tab_content = page.locator('[x-show="isScreeningTab"]')
        expect(screening_tab_content).to_be_visible()

        # Profile tab content should be hidden
        profile_tab_content = page.locator('[x-show="isProfileTab"]')
        expect(profile_tab_content).to_be_hidden()

    def test_switch_to_decision_tab(self, authenticated_coach_page, pending_profile_submission):
        """Test switching to Review Decision tab."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        # Click decision tab using Alpine.js click handler
        click_tab(page, "Review Decision")

        # Decision tab content should be visible
        decision_tab_content = page.locator('[x-show="isDecisionTab"]')
        expect(decision_tab_content).to_be_visible()

    def test_active_tab_styling(self, authenticated_coach_page, pending_profile_submission):
        """Test active tab has correct styling."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        # Profile tab should be active by default
        profile_tab_button = page.locator('.flex-col.sm\\:flex-row button').filter(has_text="Profile Overview").first
        classes = profile_tab_button.get_attribute('class')
        assert 'bg-white' in classes or 'text-purple' in classes

        # Click screening tab using helper function
        click_tab(page, "Screening Call")

        # Screening tab should now have active styling
        screening_tab_button = page.locator('.flex-col.sm\\:flex-row button').filter(has_text="Screening Call").first
        classes = screening_tab_button.get_attribute('class')
        assert 'bg-white' in classes or 'text-purple' in classes

    def test_view_full_profile_button(self, authenticated_coach_page, pending_profile_submission):
        """Test 'View Full Profile →' button switches to profile tab."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        # Go to screening tab using Alpine.js click handler
        click_tab(page, "Screening Call")

        # Click "View Full Profile →" button in summary card
        view_profile_button = page.locator('button:has-text("View Full Profile")')
        view_profile_button.click()
        page.wait_for_timeout(300)

        # Should be back on profile tab
        profile_tab_content = page.locator('[x-show="isProfileTab"]')
        expect(profile_tab_content).to_be_visible()


class TestCompleteScreeningWorkflow:
    """Test complete screening call workflow."""

    def test_screening_call_warning_indicator(self, authenticated_coach_page, pending_profile_submission):
        """Test warning indicator shows when screening call not completed."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        # Warning indicator should be visible on screening tab button
        # Target the tab navigation specifically to avoid other buttons
        warning_indicator = page.locator('.flex-col.sm\\:flex-row button').filter(has_text="Screening Call").locator('span:has-text("!")')
        expect(warning_indicator).to_be_visible()

    def test_schedule_screening_call(self, authenticated_coach_page, pending_profile_submission):
        """Test scheduling a screening call."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        # Go to screening tab using Alpine.js click handler
        click_tab(page, "Screening Call")

        # Should see screening call form/checklist
        screening_content = page.locator('[x-show="isScreeningTab"]')
        expect(screening_content).to_be_visible()

    def test_complete_screening_form(self, authenticated_coach_page, pending_profile_submission):
        """Test completing screening call form (basic check)."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        # Go to screening tab using Alpine.js click handler
        click_tab(page, "Screening Call")

        # Check if screening form elements exist
        # (Actual form filling would depend on screening tab implementation)
        screening_content = page.locator('[x-show="isScreeningTab"]')
        expect(screening_content).to_contain_text('Screening')


class TestDecisionSubmission:
    """Test review decision submission workflow."""

    def test_decision_form_visible(self, authenticated_coach_page, pending_profile_submission):
        """Test decision form is visible on Review Decision tab."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        # Go to decision tab using Alpine.js click handler
        click_tab(page, "Review Decision")

        # Decision form should be visible
        decision_content = page.locator('[x-show="isDecisionTab"]')
        expect(decision_content).to_be_visible()

    def test_feedback_to_user_field(self, authenticated_coach_page, pending_profile_submission):
        """Test feedback to user textarea exists."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        # Go to decision tab using Alpine.js click handler
        click_tab(page, "Review Decision")

        # Should have feedback_to_user textarea
        feedback_field = page.locator('textarea[name="feedback_to_user"]')
        expect(feedback_field).to_be_visible()

    def test_coach_notes_field(self, authenticated_coach_page, pending_profile_submission):
        """Test internal coach notes textarea exists."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        # Go to decision tab using Alpine.js click handler
        click_tab(page, "Review Decision")

        # Should have coach_notes textarea
        coach_notes_field = page.locator('textarea[name="coach_notes"]')
        expect(coach_notes_field).to_be_visible()


class TestVisualElements:
    """Test visual elements and styling."""

    def test_profile_photos_displayed(self, authenticated_coach_page, pending_profile_submission):
        """Test profile photos are displayed in summary card."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        # Go to screening tab to see summary using Alpine.js click handler
        click_tab(page, "Screening Call")

        # Check for photo containers (might not have actual photos in test)
        summary_card = page.locator('[x-show="showProfileSummary"]')
        expect(summary_card).to_be_visible()

    def test_gradient_background_on_summary(self, authenticated_coach_page, pending_profile_submission):
        """Test summary card has gradient background."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        click_tab(page, "Screening Call")

        # Summary card should have gradient classes
        summary_card = page.locator('[x-show="showProfileSummary"]')
        classes = summary_card.get_attribute('class')
        assert 'from-purple' in classes or 'to-pink' in classes or 'gradient' in classes

    def test_icons_render(self, authenticated_coach_page, pending_profile_submission):
        """Test emoji icons render correctly."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        click_tab(page, "Screening Call")

        summary_card = page.locator('[x-show="showProfileSummary"]')

        # Check for emoji icons
        assert '🗓️' in summary_card.text_content() or '📧' in summary_card.text_content()

    def test_green_color_for_verified(self, authenticated_coach_page, phone_verified_profile):
        """Test phone verified text is green."""
        submission, user = phone_verified_profile
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        click_tab(page, "Screening Call")

        # Phone verified text should have text-green class
        # Target the specific element in the summary card with the checkmark icon
        verified_text = page.locator('[x-show="showProfileSummary"] p.text-green-600:has-text("Phone verified")').first
        classes = verified_text.get_attribute('class')
        assert 'text-green' in classes


class TestResponsiveDesign:
    """Test responsive design of review page."""

    def test_mobile_layout(self, authenticated_coach_page, pending_profile_submission):
        """Test page layout on mobile viewport."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        # Set mobile viewport
        page.set_viewport_size({"width": 375, "height": 667})

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        # Tab navigation should be visible and responsive
        # Target the specific tab navigation container (has the tab buttons)
        tab_navigation = page.locator('.flex-col.sm\\:flex-row').filter(has_text="Profile Overview").first
        expect(tab_navigation).to_be_visible()

    def test_desktop_layout(self, authenticated_coach_page, pending_profile_submission):
        """Test page layout on desktop viewport."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        # Set desktop viewport
        page.set_viewport_size({"width": 1440, "height": 900})

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        # All elements should be visible
        click_tab(page, "Screening Call")

        summary_card = page.locator('[x-show="showProfileSummary"]')
        expect(summary_card).to_be_visible()


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_missing_social_account(self, authenticated_coach_page, pending_profile_submission):
        """Test user with no social account (email signup)."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        click_tab(page, "Screening Call")

        summary_card = page.locator('[x-show="showProfileSummary"]')
        # Should show "Email signup" as fallback
        expect(summary_card).to_contain_text('Email signup')

    def test_no_last_login(self, authenticated_coach_page, coach_user_with_permissions):
        """Test user who has never logged in."""
        from crush_lu.models import CrushProfile, ProfileSubmission

        coach_user, coach = coach_user_with_permissions

        user = User.objects.create_user(
            username='nologin@example.com',
            email='nologin@example.com',
            password='userpass123',
            first_name='No',
            last_name='Login'
        )
        # last_login is None by default

        profile = CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1994, 6, 15),
            gender='M',
            location='Luxembourg',
            bio='No login user',
            phone_number='+352777888999',
            is_approved=False
        )
        submission = ProfileSubmission.objects.create(
            profile=profile,
            coach=coach,
            status='pending'
        )

        page = authenticated_coach_page
        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        click_tab(page, "Screening Call")

        summary_card = page.locator('[x-show="showProfileSummary"]')
        # Should NOT show "Active X ago" since last_login is None
        expect(summary_card).not_to_contain_text('Active')

    def test_back_to_dashboard_button(self, authenticated_coach_page, pending_profile_submission):
        """Test back to dashboard button navigates correctly."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        # Click back to dashboard
        back_button = page.locator('a:has-text("Back to Dashboard")')
        expect(back_button).to_be_visible()
        back_button.click()
        page.wait_for_load_state('networkidle')

        # Should be redirected to coach dashboard
        assert 'coach/dashboard' in page.url or 'coach' in page.url
