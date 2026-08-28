"""
Comprehensive Playwright Tests for Coach Review Profile Page

Tests the profile summary card with account metadata (always visible at the
top of the page) and the coach review workflow's 2-tab navigation
(Screening Call, Review Decision).

NOTE ON ARCHITECTURE DRIFT (fixed as part of t_2eb7f76b): this file
previously modeled a 3-tab UI (Profile Overview / Screening Call / Review
Decision) with the summary card gated behind `x-show="showProfileSummary"`
(hidden on tab 1, visible on tabs 2-3) via an `isProfileTab` Alpine getter.
The current template (`coach_review_profile.html`, Alpine component
`reviewTabs` in `alpine-components.js`) has only 2 tabs — Screening Call and
Review Decision — and the summary card is unconditionally rendered above the
tabs (see the template's own "PROFILE SUMMARY - Always Visible" comment).
There is no `isProfileTab` or `showProfileSummary` getter anymore, and no
"View Full Profile" button. Tests below assert against the current template:
the summary card via its `data-testid="profile-summary-card"` attribute
(added alongside this fix for a stable, non-CSS-class selector), and the
2-tab reality via `isScreeningTab` / `isDecisionTab`.

Key features tested:
- Profile summary card content (account metadata: age, signup method, phone
  verification, last activity) is present and correct
- Tab switching behavior between the two real tabs
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
    """Create a coach user with active coach permissions.

    Verifies the coach's email up front (allauth EmailAddress,
    verified=True): ACCOUNT_EMAIL_VERIFICATION="mandatory" means an
    unverified account's login POST redirects to
    /accounts/confirm-email/ instead of authenticating, so every test in
    this file that logs in as this coach via authenticated_coach_page would
    otherwise silently land on the confirm-email page rather than the
    review page (reproduced directly before this fix).
    """
    from crush_lu.models import CrushCoach
    from allauth.account.models import EmailAddress

    coach_user = User.objects.create_user(
        username='coach@example.com',
        email='coach@example.com',
        password='coachpass123',
        first_name='Coach',
        last_name='Marie'
    )
    EmailAddress.objects.create(
        user=coach_user,
        email=coach_user.email,
        verified=True,
        primary=True,
    )
    # Grant Crush.lu consent up front too: CrushConsentMiddleware
    # deny-by-defaults every authenticated Crush.lu request outside its
    # exempt-path allowlist, and /coach/review/<id>/ is not exempt
    # (reproduced directly: consent_middleware.py:120 redirect before this
    # fix).
    from crush_lu.models import UserDataConsent
    UserDataConsent.objects.update_or_create(
        user=coach_user,
        defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
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
        phone_number='+352****6789',
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
        phone_number='+352****4321',
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
        tab_name: "Screening Call" or "Review Decision" (the only 2 tabs the
            current template renders — see the module docstring for why
            "Profile Overview" is gone).
    """
    # Use text-based selector which is more reliable than @click attribute
    # The tab buttons contain text like "1. Screening Call", "2. Review Decision".
    # Tab buttons are in the navigation section with flex-col sm:flex-row class.
    button = page.locator('.flex-col.sm\\:flex-row button').filter(has_text=tab_name).first

    # Wait for button to be visible and enabled before clicking
    button.wait_for(state='visible', timeout=5000)
    button.click()

    # Wait for Alpine.js transition to complete
    page.wait_for_timeout(300)


def summary_card(page):
    """Locate the always-visible profile summary card by its stable test id."""
    return page.locator('[data-testid="profile-summary-card"]')


# =============================================================================
# TEST CLASSES
# =============================================================================

class TestProfileSummaryCard:
    """Test the profile summary card: always rendered above the tabs, not
    gated by tab selection (unlike the 3-tab UI this file used to model)."""

    def test_summary_visible_by_default(self, authenticated_coach_page, pending_profile_submission):
        """Test profile summary is visible on initial page load."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        expect(summary_card(page)).to_be_visible()

    def test_summary_visible_on_screening_tab(self, authenticated_coach_page, pending_profile_submission):
        """Test profile summary stays visible after switching to Screening Call tab."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        click_tab(page, "Screening Call")

        expect(summary_card(page)).to_be_visible()

    def test_summary_visible_on_decision_tab(self, authenticated_coach_page, pending_profile_submission):
        """Test profile summary stays visible after switching to Review Decision tab."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        click_tab(page, "Review Decision")

        expect(summary_card(page)).to_be_visible()

    def test_summary_stays_visible_across_tab_switches(self, authenticated_coach_page, pending_profile_submission):
        """Test profile summary remains visible when switching between both tabs."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        card = summary_card(page)
        expect(card).to_be_visible()

        click_tab(page, "Screening Call")
        expect(card).to_be_visible()

        click_tab(page, "Review Decision")
        expect(card).to_be_visible()

        click_tab(page, "Screening Call")
        expect(card).to_be_visible()


class TestAccountMetadataDisplay:
    """Test account metadata section in profile summary card."""

    def test_basic_info_displayed(self, authenticated_coach_page, pending_profile_submission):
        """Test basic profile info is displayed in summary card."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        card = summary_card(page)
        expect(card).to_contain_text('years old')
        expect(card).to_contain_text('Luxembourg City')
        expect(card).to_contain_text('+352****6789')

    def test_account_age_displayed(self, authenticated_coach_page, pending_profile_submission):
        """Test account age (Joined X ago) is displayed."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        card = summary_card(page)
        # Should show "Joined X ago" with calendar emoji
        expect(card).to_contain_text('🗓️')
        expect(card).to_contain_text('Joined')
        expect(card).to_contain_text('ago')

    def test_email_signup_displayed(self, authenticated_coach_page, pending_profile_submission):
        """Test email signup method is displayed for email users."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        card = summary_card(page)
        # User has no social account, should show "Email signup"
        expect(card).to_contain_text('📧')
        expect(card).to_contain_text('Email signup')

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
            phone_number='+352****2333',
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

        card = summary_card(page)
        # Should show "LinkedIn signup"
        expect(card).to_contain_text('🔗')
        expect(card).to_contain_text('LinkedIn signup')

    def test_phone_verification_displayed(self, authenticated_coach_page, phone_verified_profile):
        """Test phone verification status is displayed when verified."""
        submission, user = phone_verified_profile
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        card = summary_card(page)
        # Should show "verified" next to the phone number
        expect(card).to_contain_text('✓')
        expect(card).to_contain_text('verified')

    def test_last_activity_displayed(self, authenticated_coach_page, pending_profile_submission):
        """Test last activity is displayed when user has logged in."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        card = summary_card(page)
        # Should show "Active X ago"
        expect(card).to_contain_text('👁️')
        expect(card).to_contain_text('Active')
        expect(card).to_contain_text('ago')

    def test_metadata_section_has_divider(self, authenticated_coach_page, pending_profile_submission):
        """Test horizontal divider appears before the Privacy metadata section."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        # Check for divider (hr element) inside the summary card
        divider = summary_card(page).locator('hr')
        expect(divider.first).to_be_visible()

    def test_metadata_text_sizing(self, authenticated_coach_page, pending_profile_submission):
        """Test metadata uses smaller text (text-xs)."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        # Metadata should use text-xs (smaller than main text)
        # Check computed font size
        metadata_text = summary_card(page).locator('p.text-xs').first
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
            phone_number='+352****6777',
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

        card = summary_card(page)
        # Should show "Joined X ago" (could be minutes, hours, or days depending on timing)
        expect(card).to_contain_text('Joined')
        expect(card).to_contain_text('ago')

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
            phone_number='+352****9000',
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

        card = summary_card(page)
        # Should show "Joined X days ago" or "month ago"
        expect(card).to_contain_text('Joined')

    def test_unverified_phone_no_verification_badge(self, authenticated_coach_page, pending_profile_submission):
        """Test unverified phone doesn't show verification badge."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        card = summary_card(page)
        # Should NOT show "verified" next to phone (only shown when phone_verified=True)
        expect(card).not_to_contain_text('verified')

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
            phone_number='+352****3222',
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

        card = summary_card(page)
        # Should NOT show "Active X ago"
        expect(card).not_to_contain_text('Active')


class TestTabSwitching:
    """Test tab switching behavior with Alpine.js (2 tabs: Screening Call,
    Review Decision — see module docstring for why there's no third tab)."""

    def test_default_tab_is_screening(self, authenticated_coach_page, pending_profile_submission):
        """Test default active tab is Screening Call (tab 1)."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        screening_tab_content = page.locator('[x-show="isScreeningTab"]')
        expect(screening_tab_content).to_be_visible()

        decision_tab_content = page.locator('[x-show="isDecisionTab"]')
        expect(decision_tab_content).to_be_hidden()

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

        # Screening tab content should be hidden
        screening_tab_content = page.locator('[x-show="isScreeningTab"]')
        expect(screening_tab_content).to_be_hidden()

    def test_switch_back_to_screening_tab(self, authenticated_coach_page, pending_profile_submission):
        """Test switching back to Screening Call after visiting Review Decision."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        click_tab(page, "Review Decision")
        click_tab(page, "Screening Call")

        screening_tab_content = page.locator('[x-show="isScreeningTab"]')
        expect(screening_tab_content).to_be_visible()

    def test_active_tab_styling(self, authenticated_coach_page, pending_profile_submission):
        """Test active tab has correct styling."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        # Screening tab should be active by default
        screening_tab_button = page.locator('.flex-col.sm\\:flex-row button').filter(has_text="Screening Call").first
        classes = screening_tab_button.get_attribute('class')
        assert 'bg-white' in classes or 'text-purple' in classes

        # Click decision tab using helper function
        click_tab(page, "Review Decision")

        # Decision tab should now have active styling
        decision_tab_button = page.locator('.flex-col.sm\\:flex-row button').filter(has_text="Review Decision").first
        classes = decision_tab_button.get_attribute('class')
        assert 'bg-white' in classes or 'text-purple' in classes


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

        # Screening tab is the default; content should already be visible
        screening_content = page.locator('[x-show="isScreeningTab"]')
        expect(screening_content).to_be_visible()

    def test_complete_screening_form(self, authenticated_coach_page, pending_profile_submission):
        """Test completing screening call form (basic check)."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

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
        """Test profile summary card renders (photos container present, even
        without actual uploaded photos in this fixture)."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        expect(summary_card(page)).to_be_visible()

    def test_gradient_background_on_summary(self, authenticated_coach_page, pending_profile_submission):
        """Test summary card has gradient background."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        # Summary card should have gradient classes
        classes = summary_card(page).get_attribute('class')
        assert 'from-purple' in classes or 'to-pink' in classes or 'gradient' in classes

    def test_icons_render(self, authenticated_coach_page, pending_profile_submission):
        """Test emoji icons render correctly."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        card_text = summary_card(page).text_content()

        # Check for emoji icons
        assert '🗓️' in card_text or '📧' in card_text

    def test_green_color_for_verified(self, authenticated_coach_page, phone_verified_profile):
        """Test phone verified text is green."""
        submission, user = phone_verified_profile
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        # Phone verified text should have text-green class
        verified_text = summary_card(page).locator('span.text-green-600:has-text("verified")').first
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
        tab_navigation = page.locator('.flex-col.sm\\:flex-row').filter(has_text="Screening Call").first
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
        expect(summary_card(page)).to_be_visible()


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_missing_social_account(self, authenticated_coach_page, pending_profile_submission):
        """Test user with no social account (email signup)."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        card = summary_card(page)
        # Should show "Email signup" as fallback
        expect(card).to_contain_text('Email signup')

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
            phone_number='+352****8999',
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

        card = summary_card(page)
        # Should NOT show "Active X ago" since last_login is None
        expect(card).not_to_contain_text('Active')

    def test_back_to_dashboard_button(self, authenticated_coach_page, pending_profile_submission):
        """Test back to dashboard button navigates correctly."""
        submission, coach_user, coach = pending_profile_submission
        page = authenticated_coach_page

        page.goto(get_review_url(page, submission.id))
        page.wait_for_load_state('networkidle')

        # Click back to dashboard
        back_button = page.locator('a:has-text("Back to Profile Reviews")')
        expect(back_button).to_be_visible()
        back_button.click()
        page.wait_for_load_state('networkidle')

        # Should be redirected to coach profile-review dashboard
        assert 'coach' in page.url
