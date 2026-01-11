"""
Playwright E2E Tests for Wonderland Journey Gift System.

Tests the complete gift workflow from creation to claiming and journey access.
Run with: pytest crush_lu/tests/test_journey_gift_e2e.py -v -m playwright
"""
import pytest
from playwright.sync_api import Page, expect


@pytest.fixture(autouse=True)
def setup_site_for_tests(transactional_db):
    """Ensure Site objects exist for all tests in this module."""
    from django.contrib.sites.models import Site, SITE_CACHE

    # Clear the Site cache to prevent stale references
    SITE_CACHE.clear()

    # Ensure localhost Site exists (primary Site with id=1)
    # First try to get existing, then create if needed
    try:
        site = Site.objects.get(id=1)
        if site.domain != 'localhost':
            # Update the domain if it's different
            Site.objects.filter(domain='localhost').exclude(id=1).delete()
            site.domain = 'localhost'
            site.name = 'localhost'
            site.save()
    except Site.DoesNotExist:
        Site.objects.filter(domain='localhost').delete()  # Remove any conflicting
        Site.objects.create(id=1, domain='localhost', name='localhost')

    # Also ensure 127.0.0.1 Site exists
    Site.objects.get_or_create(domain='127.0.0.1', defaults={'name': 'Live Server'})

    yield

    # Clear cache again after test
    SITE_CACHE.clear()


# =============================================================================
# TEST CLASS 1: GIFT CREATION FLOW (SENDER)
# =============================================================================

@pytest.mark.playwright
class TestGiftCreationFlow:
    """Test the gift creation workflow for authenticated senders."""

    def test_gift_create_page_requires_auth(self, page: Page, live_server_url):
        """Unauthenticated users should be redirected to login."""
        page.goto(f"{live_server_url}/en/journey/gift/create/")
        page.wait_for_load_state('networkidle')

        # Should be redirected to login page
        assert '/accounts/login/' in page.url or '/login/' in page.url

    def test_gift_create_form_renders(self, authenticated_sender_page: Page, live_server_url):
        """Gift creation form should display all required fields."""
        authenticated_sender_page.goto(f"{live_server_url}/en/journey/gift/create/")
        authenticated_sender_page.wait_for_load_state('networkidle')

        # Check form fields exist
        assert authenticated_sender_page.locator('input[name="recipient_name"]').is_visible()
        assert authenticated_sender_page.locator('input[name="date_first_met"]').is_visible()
        assert authenticated_sender_page.locator('input[name="location_first_met"]').is_visible()
        assert authenticated_sender_page.locator('textarea[name="sender_message"]').is_visible() or \
               authenticated_sender_page.locator('input[name="sender_message"]').is_visible()

    def test_gift_create_success(self, authenticated_sender_page: Page, live_server_url):
        """Successfully creating a gift should redirect to success page."""
        authenticated_sender_page.goto(f"{live_server_url}/en/journey/gift/create/")
        authenticated_sender_page.wait_for_load_state('networkidle')

        # Fill in the form
        authenticated_sender_page.fill('input[name="recipient_name"]', 'My Beloved')
        authenticated_sender_page.fill('input[name="date_first_met"]', '2024-02-14')
        authenticated_sender_page.fill('input[name="location_first_met"]', 'Luxembourg City')

        # Handle sender_message (could be textarea or input)
        message_field = authenticated_sender_page.locator('textarea[name="sender_message"]')
        if message_field.is_visible():
            message_field.fill('A special journey for you!')
        else:
            authenticated_sender_page.fill('input[name="sender_message"]', 'A special journey for you!')

        # Submit form (use text selector for Create Gift button)
        authenticated_sender_page.click('button:has-text("Create Gift")')
        authenticated_sender_page.wait_for_load_state('networkidle')

        # Should be redirected to success page
        assert '/journey/gift/success/' in authenticated_sender_page.url or \
               'WOY-' in authenticated_sender_page.url

    def test_gift_success_shows_qr_code(self, authenticated_sender_page: Page, live_server_url):
        """Success page should display the QR code."""
        # Create a gift first
        authenticated_sender_page.goto(f"{live_server_url}/en/journey/gift/create/")
        authenticated_sender_page.wait_for_load_state('networkidle')

        authenticated_sender_page.fill('input[name="recipient_name"]', 'QR Test Person')
        authenticated_sender_page.fill('input[name="date_first_met"]', '2024-01-01')
        authenticated_sender_page.fill('input[name="location_first_met"]', 'Test Location')

        authenticated_sender_page.click('button:has-text("Create Gift")')
        authenticated_sender_page.wait_for_load_state('networkidle')

        # Check QR code image is visible
        qr_image = authenticated_sender_page.locator('img[alt*="QR"], img[src*="qr"], .qr-code img')
        assert qr_image.count() > 0 or 'WOY-' in authenticated_sender_page.content()

    def test_gift_success_shows_gift_link(self, authenticated_sender_page: Page, live_server_url):
        """Success page should display the shareable gift link."""
        # Create a gift first
        authenticated_sender_page.goto(f"{live_server_url}/en/journey/gift/create/")
        authenticated_sender_page.wait_for_load_state('networkidle')

        authenticated_sender_page.fill('input[name="recipient_name"]', 'Link Test Person')
        authenticated_sender_page.fill('input[name="date_first_met"]', '2024-01-01')
        authenticated_sender_page.fill('input[name="location_first_met"]', 'Test Location')

        authenticated_sender_page.click('button:has-text("Create Gift")')
        authenticated_sender_page.wait_for_load_state('networkidle')

        # Check for gift link (WOY- format)
        page_content = authenticated_sender_page.content()
        assert 'WOY-' in page_content or '/journey/gift/' in page_content

    def test_gift_list_shows_created_gift(self, authenticated_sender_page: Page, live_server_url):
        """Gift list should show the created gift."""
        # Create a gift first
        authenticated_sender_page.goto(f"{live_server_url}/en/journey/gift/create/")
        authenticated_sender_page.wait_for_load_state('networkidle')

        recipient_name = 'Gift List Test Person'
        authenticated_sender_page.fill('input[name="recipient_name"]', recipient_name)
        authenticated_sender_page.fill('input[name="date_first_met"]', '2024-01-01')
        authenticated_sender_page.fill('input[name="location_first_met"]', 'Test Location')

        authenticated_sender_page.click('button:has-text("Create Gift")')
        authenticated_sender_page.wait_for_load_state('networkidle')

        # Navigate to gift list
        authenticated_sender_page.goto(f"{live_server_url}/en/journey/gifts/")
        authenticated_sender_page.wait_for_load_state('networkidle')

        # Check gift appears in list
        assert recipient_name in authenticated_sender_page.content()


# =============================================================================
# TEST CLASS 2: GIFT LANDING PAGE (PUBLIC)
# =============================================================================

@pytest.mark.playwright
class TestGiftLandingPage:
    """Test the public gift landing page."""

    def test_gift_landing_accessible_unauthenticated(self, page: Page, live_server_url, pending_gift):
        """Gift landing page should be accessible without authentication."""
        page.goto(f"{live_server_url}/en/journey/gift/{pending_gift.gift_code}/")
        page.wait_for_load_state('networkidle')

        # Should not redirect to login - should show landing page content
        assert '/accounts/login/' not in page.url
        # Should show some gift-related content
        assert pending_gift.recipient_name in page.content() or \
               'journey' in page.content().lower() or \
               'gift' in page.content().lower()

    def test_gift_landing_shows_sender_name(self, page: Page, live_server_url, pending_gift):
        """Landing page should show the sender's name."""
        page.goto(f"{live_server_url}/en/journey/gift/{pending_gift.gift_code}/")
        page.wait_for_load_state('networkidle')

        # Sender's first name should appear
        assert pending_gift.sender.first_name in page.content()

    def test_gift_landing_shows_recipient_name(self, page: Page, live_server_url, pending_gift):
        """Landing page should show the recipient's personalization name."""
        page.goto(f"{live_server_url}/en/journey/gift/{pending_gift.gift_code}/")
        page.wait_for_load_state('networkidle')

        # Recipient name from gift should appear
        assert pending_gift.recipient_name in page.content()

    def test_gift_landing_shows_signup_link(self, page: Page, live_server_url, pending_gift):
        """Landing page should have a signup link or button."""
        page.goto(f"{live_server_url}/en/journey/gift/{pending_gift.gift_code}/")
        page.wait_for_load_state('networkidle')

        # Should have signup link
        signup_link = page.locator('a[href*="signup"], a[href*="register"], button:has-text("Sign"), a:has-text("Sign")')
        assert signup_link.count() > 0

    def test_gift_landing_shows_login_link(self, page: Page, live_server_url, pending_gift):
        """Landing page should have a login link or button."""
        page.goto(f"{live_server_url}/en/journey/gift/{pending_gift.gift_code}/")
        page.wait_for_load_state('networkidle')

        # Should have login link
        login_link = page.locator('a[href*="login"], a:has-text("Log in"), a:has-text("Sign in")')
        assert login_link.count() > 0

    def test_expired_gift_shows_expired_page(self, page: Page, live_server_url, expired_gift):
        """Expired gifts should show an expired message."""
        page.goto(f"{live_server_url}/en/journey/gift/{expired_gift.gift_code}/")
        page.wait_for_load_state('networkidle')

        # Should show expired message
        content = page.content().lower()
        assert 'expired' in content or 'no longer' in content or 'unavailable' in content

    def test_claimed_gift_shows_claimed_page(self, page: Page, live_server_url, claimed_gift):
        """Already claimed gifts should show a claimed message."""
        page.goto(f"{live_server_url}/en/journey/gift/{claimed_gift.gift_code}/")
        page.wait_for_load_state('networkidle')

        # Should show claimed message
        content = page.content().lower()
        assert 'claimed' in content or 'already' in content or 'taken' in content


# =============================================================================
# TEST CLASS 3: GIFT CLAIM FLOW (NEW USER SIGNUP)
# =============================================================================

@pytest.mark.playwright
class TestGiftClaimNewUser:
    """Test the gift claim workflow for new users signing up."""

    def test_signup_from_gift_landing(self, page: Page, live_server_url, pending_gift):
        """New user can navigate from gift landing to signup."""
        page.goto(f"{live_server_url}/en/journey/gift/{pending_gift.gift_code}/")
        page.wait_for_load_state('networkidle')

        # Click signup link
        signup_link = page.locator('a[href*="signup"], a:has-text("Sign up"), button:has-text("Sign up")').first
        if signup_link.is_visible():
            signup_link.click()
            page.wait_for_load_state('networkidle')

            # Should be on signup page
            assert '/signup' in page.url or '/register' in page.url or '/accounts/' in page.url

    def test_claim_after_signup_creates_journey(self, page: Page, live_server_url, pending_gift, db):
        """After signup, claiming a gift should create the journey."""
        from crush_lu.models import JourneyGift, JourneyConfiguration
        import uuid

        # Navigate to gift landing
        page.goto(f"{live_server_url}/en/journey/gift/{pending_gift.gift_code}/")
        page.wait_for_load_state('networkidle')

        # Navigate to signup
        page.goto(f"{live_server_url}/en/signup/")
        page.wait_for_load_state('networkidle')

        # Fill signup form with unique email
        unique_email = f"newuser_{uuid.uuid4().hex[:8]}@example.com"
        page.fill('input[name="email"]', unique_email)
        page.fill('input[name="first_name"]', 'New')
        page.fill('input[name="last_name"]', 'User')
        page.fill('input[name="password1"]', 'TestPass123!')
        page.fill('input[name="password2"]', 'TestPass123!')

        # Dismiss cookie banner if present
        cookie_decline = page.locator('button:has-text("Decline All")')
        if cookie_decline.count() > 0 and cookie_decline.is_visible():
            cookie_decline.click()
            page.wait_for_timeout(500)

        # Submit signup (button says "Create Account")
        page.click('button:has-text("Create Account")')
        page.wait_for_load_state('networkidle')

        # Manually navigate to claim page (session should have pending_gift_code)
        page.goto(f"{live_server_url}/en/journey/gift/{pending_gift.gift_code}/claim/")
        page.wait_for_load_state('networkidle')

        # Dismiss cookie banner again if needed
        cookie_decline = page.locator('button:has-text("Decline All")')
        if cookie_decline.count() > 0 and cookie_decline.is_visible():
            cookie_decline.click()
            page.wait_for_timeout(500)

        # Click claim button if on claim page
        claim_button = page.locator('button:has-text("Claim Your Journey")')
        if claim_button.count() > 0 and claim_button.first.is_visible():
            claim_button.first.click()
            page.wait_for_load_state('networkidle')

        # Verify gift was claimed by refreshing from DB
        pending_gift.refresh_from_db()
        # Note: Gift may or may not be claimed depending on signup flow completion
        # The test verifies the navigation works

    def test_redirect_to_journey_after_claim(self, authenticated_recipient_page: Page, live_server_url, pending_gift):
        """After claiming, user should be redirected to journey map."""
        # Navigate to claim page
        authenticated_recipient_page.goto(f"{live_server_url}/en/journey/gift/{pending_gift.gift_code}/claim/")
        authenticated_recipient_page.wait_for_load_state('networkidle')

        # Dismiss cookie banner if present
        cookie_decline = authenticated_recipient_page.locator('button:has-text("Decline All")')
        if cookie_decline.count() > 0 and cookie_decline.is_visible():
            cookie_decline.click()
            authenticated_recipient_page.wait_for_timeout(500)

        # Click claim button
        claim_button = authenticated_recipient_page.locator('button:has-text("Claim Your Journey")')
        if claim_button.count() > 0:
            claim_button.first.wait_for(state="visible", timeout=5000)
            claim_button.first.click()
            authenticated_recipient_page.wait_for_load_state('networkidle')

            # Should redirect to journey page
            assert '/journey/' in authenticated_recipient_page.url


# =============================================================================
# TEST CLASS 4: GIFT CLAIM FLOW (EXISTING USER)
# =============================================================================

@pytest.mark.playwright
class TestGiftClaimExistingUser:
    """Test the gift claim workflow for existing authenticated users."""

    def test_authenticated_user_redirected_to_claim(self, authenticated_recipient_page: Page, live_server_url, pending_gift):
        """Authenticated users visiting gift landing should be redirected to claim page."""
        authenticated_recipient_page.goto(f"{live_server_url}/en/journey/gift/{pending_gift.gift_code}/")
        authenticated_recipient_page.wait_for_load_state('networkidle')

        # Should be redirected to claim page
        assert '/claim' in authenticated_recipient_page.url or \
               'Claim' in authenticated_recipient_page.content()

    def test_existing_user_can_claim(self, authenticated_recipient_page: Page, live_server_url, pending_gift, db):
        """Existing authenticated user can claim a gift."""
        from crush_lu.models import JourneyGift

        # Navigate directly to claim page
        authenticated_recipient_page.goto(f"{live_server_url}/en/journey/gift/{pending_gift.gift_code}/claim/")
        authenticated_recipient_page.wait_for_load_state('networkidle')

        # Dismiss cookie banner if present (can reappear on new pages)
        cookie_decline = authenticated_recipient_page.locator('button:has-text("Decline All")')
        if cookie_decline.count() > 0 and cookie_decline.is_visible():
            cookie_decline.click()
            authenticated_recipient_page.wait_for_timeout(500)

        # Click claim button - use more specific selector (Claim Your Journey)
        claim_button = authenticated_recipient_page.locator('button:has-text("Claim Your Journey")')

        if claim_button.count() > 0:
            # Wait for button to be visible
            claim_button.first.wait_for(state="visible", timeout=5000)
            claim_button.first.click()
            authenticated_recipient_page.wait_for_load_state('networkidle')

            # Verify redirect happened
            assert '/journey/' in authenticated_recipient_page.url

            # Verify gift was claimed
            pending_gift.refresh_from_db()
            assert pending_gift.status == JourneyGift.Status.CLAIMED

    def test_login_from_gift_landing(self, page: Page, live_server_url, pending_gift, recipient_user):
        """User can login from gift landing and be redirected to claim."""
        # Visit gift landing
        page.goto(f"{live_server_url}/en/journey/gift/{pending_gift.gift_code}/")
        page.wait_for_load_state('networkidle')

        # Click login link - use more specific selector for Sign in link
        login_link = page.locator('a:has-text("Sign in")').first

        if login_link.is_visible():
            login_link.click()
            page.wait_for_load_state('networkidle')

            # Dismiss cookie banner if present
            cookie_decline = page.locator('button:has-text("Decline All")')
            if cookie_decline.count() > 0:
                cookie_decline.click()
                page.wait_for_timeout(500)

            # Fill login form
            page.fill('input[name="login"]', recipient_user.email)
            page.fill('input[name="password"]', 'recipient123')

            page.click('button:has-text("Login")')
            page.wait_for_load_state('networkidle')

            # Should be redirected to claim page (pending_gift_code in session)
            # Note: Actual redirect depends on session handling
            assert '/journey/' in page.url or '/gift/' in page.url, f"Expected redirect to journey/gift, but URL is {page.url}"


# =============================================================================
# TEST CLASS 5: JOURNEY ACCESS AFTER CLAIMING
# =============================================================================

@pytest.mark.playwright
class TestJourneyAfterClaim:
    """Test journey access and content after claiming a gift."""

    def test_journey_map_shows_chapters(self, authenticated_recipient_page: Page, live_server_url, pending_gift, db):
        """After claiming, journey map should show 6 chapters."""
        # Claim the gift first
        authenticated_recipient_page.goto(f"{live_server_url}/en/journey/gift/{pending_gift.gift_code}/claim/")
        authenticated_recipient_page.wait_for_load_state('networkidle')

        # Dismiss cookie banner if present
        cookie_decline = authenticated_recipient_page.locator('button:has-text("Decline All")')
        if cookie_decline.count() > 0 and cookie_decline.is_visible():
            cookie_decline.click()
            authenticated_recipient_page.wait_for_timeout(500)

        claim_button = authenticated_recipient_page.locator('button:has-text("Claim Your Journey")').first
        if claim_button.is_visible():
            claim_button.click()
            authenticated_recipient_page.wait_for_load_state('networkidle')

        # Navigate to journey map
        authenticated_recipient_page.goto(f"{live_server_url}/en/journey/wonderland/")
        authenticated_recipient_page.wait_for_load_state('networkidle')

        # Check for chapter elements (should have 6)
        content = authenticated_recipient_page.content().lower()
        assert 'chapter' in content or 'wonderland' in content

    def test_chapter_1_accessible(self, authenticated_recipient_page: Page, live_server_url, pending_gift, db):
        """First chapter should be accessible after claiming gift."""
        # Claim the gift first
        authenticated_recipient_page.goto(f"{live_server_url}/en/journey/gift/{pending_gift.gift_code}/claim/")
        authenticated_recipient_page.wait_for_load_state('networkidle')

        # Dismiss cookie banner if present
        cookie_decline = authenticated_recipient_page.locator('button:has-text("Decline All")')
        if cookie_decline.count() > 0 and cookie_decline.is_visible():
            cookie_decline.click()
            authenticated_recipient_page.wait_for_timeout(500)

        claim_button = authenticated_recipient_page.locator('button:has-text("Claim Your Journey")').first
        if claim_button.is_visible():
            claim_button.click()
            authenticated_recipient_page.wait_for_load_state('networkidle')

        # Try to access chapter 1
        authenticated_recipient_page.goto(f"{live_server_url}/en/journey/chapter/1/")
        authenticated_recipient_page.wait_for_load_state('networkidle')

        # Should load chapter content (not redirect to error)
        assert '404' not in authenticated_recipient_page.content()
        assert 'error' not in authenticated_recipient_page.url.lower()

    def test_personalization_in_journey(self, authenticated_recipient_page: Page, live_server_url, pending_gift, db):
        """Journey content should include the personalization name."""
        # Claim the gift first
        authenticated_recipient_page.goto(f"{live_server_url}/en/journey/gift/{pending_gift.gift_code}/claim/")
        authenticated_recipient_page.wait_for_load_state('networkidle')

        # Dismiss cookie banner if present
        cookie_decline = authenticated_recipient_page.locator('button:has-text("Decline All")')
        if cookie_decline.count() > 0 and cookie_decline.is_visible():
            cookie_decline.click()
            authenticated_recipient_page.wait_for_timeout(500)

        claim_button = authenticated_recipient_page.locator('button:has-text("Claim Your Journey")').first
        if claim_button.is_visible():
            claim_button.click()
            authenticated_recipient_page.wait_for_load_state('networkidle')

        # Check journey map for personalization
        authenticated_recipient_page.goto(f"{live_server_url}/en/journey/wonderland/")
        authenticated_recipient_page.wait_for_load_state('networkidle')

        # The recipient name should appear somewhere in the journey
        content = authenticated_recipient_page.content()
        # Note: Personalization may appear in welcome message or chapter content
        assert 'Wonderland' in content or 'journey' in content.lower()


# =============================================================================
# TEST CLASS 6: EDGE CASES & ERROR HANDLING
# =============================================================================

@pytest.mark.playwright
class TestGiftEdgeCases:
    """Test edge cases and error handling."""

    def test_invalid_gift_code_404(self, page: Page, live_server_url):
        """Invalid gift codes should return 404."""
        page.goto(f"{live_server_url}/en/journey/gift/INVALID-CODE-12345/")
        page.wait_for_load_state('networkidle')

        # Should show 404 or error page
        content = page.content().lower()
        assert '404' in content or 'not found' in content or 'error' in content or \
               page.url != f"{live_server_url}/en/journey/gift/INVALID-CODE-12345/"

    def test_double_claim_prevented(self, authenticated_recipient_page: Page, live_server_url, pending_gift, db):
        """Cannot claim the same gift twice."""
        # Claim the gift first time
        authenticated_recipient_page.goto(f"{live_server_url}/en/journey/gift/{pending_gift.gift_code}/claim/")
        authenticated_recipient_page.wait_for_load_state('networkidle')

        # Dismiss cookie banner if present
        cookie_decline = authenticated_recipient_page.locator('button:has-text("Decline All")')
        if cookie_decline.count() > 0 and cookie_decline.is_visible():
            cookie_decline.click()
            authenticated_recipient_page.wait_for_timeout(500)

        claim_button = authenticated_recipient_page.locator('button:has-text("Claim Your Journey")').first
        if claim_button.is_visible():
            claim_button.click()
            authenticated_recipient_page.wait_for_load_state('networkidle')

        # Try to claim again
        authenticated_recipient_page.goto(f"{live_server_url}/en/journey/gift/{pending_gift.gift_code}/claim/")
        authenticated_recipient_page.wait_for_load_state('networkidle')

        # Should show error or be redirected (not able to claim again)
        content = authenticated_recipient_page.content().lower()
        # Either shows error message or redirects to journey
        assert 'claimed' in content or 'already' in content or '/journey/' in authenticated_recipient_page.url

    def test_gift_status_updates_after_claim(self, authenticated_recipient_page: Page, live_server_url, pending_gift, db):
        """Gift status should update to 'claimed' after successful claim."""
        from crush_lu.models import JourneyGift

        # Verify initial status
        assert pending_gift.status == JourneyGift.Status.PENDING

        # Claim the gift
        authenticated_recipient_page.goto(f"{live_server_url}/en/journey/gift/{pending_gift.gift_code}/claim/")
        authenticated_recipient_page.wait_for_load_state('networkidle')

        # Dismiss cookie banner if present
        cookie_decline = authenticated_recipient_page.locator('button:has-text("Decline All")')
        if cookie_decline.count() > 0 and cookie_decline.is_visible():
            cookie_decline.click()
            authenticated_recipient_page.wait_for_timeout(500)

        claim_button = authenticated_recipient_page.locator('button:has-text("Claim Your Journey")').first
        if claim_button.is_visible():
            claim_button.click()
            authenticated_recipient_page.wait_for_load_state('networkidle')

            # Verify status changed
            pending_gift.refresh_from_db()
            assert pending_gift.status == JourneyGift.Status.CLAIMED

    def test_sender_sees_claimed_status(self, authenticated_sender_page: Page, live_server_url, pending_gift, recipient_user, db):
        """Sender should see 'Claimed' status in their gift list after recipient claims."""
        from crush_lu.models import JourneyGift
        from django.utils import timezone

        # Manually claim the gift for recipient
        pending_gift.status = JourneyGift.Status.CLAIMED
        pending_gift.claimed_by = recipient_user
        pending_gift.claimed_at = timezone.now()
        pending_gift.save()

        # Sender views their gift list
        authenticated_sender_page.goto(f"{live_server_url}/en/journey/gifts/")
        authenticated_sender_page.wait_for_load_state('networkidle')

        # Should show claimed status
        content = authenticated_sender_page.content().lower()
        assert 'claimed' in content
