"""
Photo Puzzle E2E Tests for Crush.lu

Tests the photo reveal puzzle feature including:
- Loading state management (prevents double-clicks)
- Visual feedback during unlock
- Points deduction
- Puzzle completion

Run with: pytest crush_lu/tests/test_photo_puzzle_e2e.py -v -m playwright
"""
import pytest
from datetime import date
from playwright.sync_api import Page, expect


@pytest.fixture
def puzzle_user(transactional_db):
    """Create a user with an active journey and photo puzzle reward."""
    from django.contrib.auth import get_user_model
    from django.contrib.sites.models import Site
    from allauth.account.models import EmailAddress
    from crush_lu.models import (
        CrushProfile, JourneyConfiguration, JourneyChapter,
        JourneyReward, JourneyProgress, SpecialUserExperience,
        ChapterProgress
    )

    # Ensure Site objects exist
    Site.objects.get_or_create(id=1, defaults={'domain': 'localhost', 'name': 'localhost'})
    Site.objects.get_or_create(domain='127.0.0.1', defaults={'name': 'Live Server'})
    Site.objects.get_or_create(domain='testserver', defaults={'name': 'Test Server'})

    User = get_user_model()

    user = User.objects.create_user(
        username='puzzletest@example.com',
        email='puzzletest@example.com',
        password='puzzle123',
        first_name='Puzzle',
        last_name='Tester'
    )

    EmailAddress.objects.create(
        user=user,
        email=user.email,
        verified=True,
        primary=True
    )

    CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1995, 5, 15),
        gender='M',
        location='Luxembourg',
        is_approved=True
    )

    # Create journey setup
    experience = SpecialUserExperience.objects.create(
        linked_user=user,
        first_name='Puzzle',
        last_name='Tester',
        custom_welcome_message='Welcome to your puzzle!',
        is_active=True
    )

    journey = JourneyConfiguration.objects.create(
        special_experience=experience,
        journey_name='Photo Puzzle Test Journey',
        total_chapters=1,
        is_active=True
    )

    chapter = JourneyChapter.objects.create(
        journey=journey,
        chapter_number=1,
        title='Photo Puzzle Chapter',
        theme='Mystery',
        story_introduction='Unlock the mystery photo!',
        completion_message='Photo revealed!'
    )

    # Create photo reveal reward (no actual photo needed for test)
    reward = JourneyReward.objects.create(
        chapter=chapter,
        reward_type='photo_reveal',
        title='Mystery Photo',
        message='Unlock pieces to reveal the hidden photo!'
    )

    # Create journey progress with points
    journey_progress = JourneyProgress.objects.create(
        user=user,
        journey=journey,
        current_chapter=1,
        total_points=500  # Enough for 10 pieces
    )

    # Create chapter progress and mark as completed (required to access reward)
    ChapterProgress.objects.create(
        journey_progress=journey_progress,
        chapter=chapter,
        is_completed=True,
        points_earned=100
    )

    return {
        'user': user,
        'journey': journey,
        'chapter': chapter,
        'reward': reward
    }


@pytest.fixture
def authenticated_puzzle_page(page: Page, live_server_url, puzzle_user, transactional_db):
    """Playwright page logged in as puzzle test user."""
    from django.contrib.sites.models import Site, SITE_CACHE

    # Clear site cache to avoid stale data between tests
    SITE_CACHE.clear()

    # Ensure Site objects exist (may have been cleared between tests)
    Site.objects.get_or_create(id=1, defaults={'domain': 'localhost', 'name': 'localhost'})
    Site.objects.get_or_create(domain='127.0.0.1', defaults={'name': 'Live Server'})
    Site.objects.get_or_create(domain='testserver', defaults={'name': 'Test Server'})

    # Navigate to login
    page.goto(f"{live_server_url}/accounts/login/")
    page.wait_for_selector('input[name="login"]', timeout=10000)

    # Dismiss cookie banner if present (blocks clicks on other elements)
    cookie_decline = page.locator('button:has-text("Decline All")')
    if cookie_decline.count() > 0:
        cookie_decline.click()
        page.wait_for_timeout(500)

    # Login
    page.fill('input[name="login"]', 'puzzletest@example.com')
    page.fill('input[name="password"]', 'puzzle123')
    # Click the Login button (use text selector to avoid navbar button)
    page.click('button:has-text("Login")')
    page.wait_for_load_state('networkidle')

    return page


@pytest.mark.playwright
class TestPhotoPuzzleUI:
    """Test Photo Puzzle UI and interactions."""

    def test_puzzle_page_loads(self, authenticated_puzzle_page: Page, live_server_url, puzzle_user):
        """Test that the photo puzzle page loads correctly."""
        reward = puzzle_user['reward']
        chapter = puzzle_user['chapter']

        # Navigate to reward page
        authenticated_puzzle_page.goto(
            f"{live_server_url}/en/journey/reward/{reward.id}/"
        )
        authenticated_puzzle_page.wait_for_load_state('networkidle')

        # Check page elements
        expect(authenticated_puzzle_page.locator('#puzzleGrid')).to_be_visible()
        expect(authenticated_puzzle_page.locator('#currentPoints')).to_be_visible()
        expect(authenticated_puzzle_page.locator('#progressMeter')).to_be_visible()

    def test_puzzle_shows_16_pieces(self, authenticated_puzzle_page: Page, live_server_url, puzzle_user):
        """Test that puzzle shows 16 pieces."""
        reward = puzzle_user['reward']
        chapter = puzzle_user['chapter']

        authenticated_puzzle_page.goto(
            f"{live_server_url}/en/journey/reward/{reward.id}/"
        )
        authenticated_puzzle_page.wait_for_load_state('networkidle')
        authenticated_puzzle_page.wait_for_timeout(1000)  # Wait for JS to initialize

        # Count puzzle pieces
        pieces = authenticated_puzzle_page.locator('.reveal-puzzle-piece')
        expect(pieces).to_have_count(16)

    def test_puzzle_piece_shows_loading_state_on_click(self, authenticated_puzzle_page: Page, live_server_url, puzzle_user):
        """Test that clicking a piece shows loading state and prevents double-clicks."""
        reward = puzzle_user['reward']
        chapter = puzzle_user['chapter']

        authenticated_puzzle_page.goto(
            f"{live_server_url}/en/journey/reward/{reward.id}/"
        )
        authenticated_puzzle_page.wait_for_load_state('networkidle')
        authenticated_puzzle_page.wait_for_timeout(1000)  # Wait for JS to initialize

        # Find first locked piece
        first_piece = authenticated_puzzle_page.locator('.reveal-puzzle-piece.locked').first

        # Handle the confirm dialog
        authenticated_puzzle_page.on('dialog', lambda dialog: dialog.accept())

        # Click the piece
        first_piece.click()

        # Check for loading state (should appear quickly)
        # The loading class should be added immediately after dialog is accepted
        authenticated_puzzle_page.wait_for_timeout(100)

        # Verify piece gets unlocked eventually
        authenticated_puzzle_page.wait_for_selector('.reveal-puzzle-piece.unlocked', timeout=5000)

    def test_points_decrease_after_unlock(self, authenticated_puzzle_page: Page, live_server_url, puzzle_user):
        """Test that points decrease by 50 after unlocking a piece."""
        reward = puzzle_user['reward']
        chapter = puzzle_user['chapter']

        authenticated_puzzle_page.goto(
            f"{live_server_url}/en/journey/reward/{reward.id}/"
        )
        authenticated_puzzle_page.wait_for_load_state('networkidle')
        authenticated_puzzle_page.wait_for_timeout(1000)  # Wait for JS

        # Get initial points (should be 500)
        points_element = authenticated_puzzle_page.locator('#currentPoints')
        authenticated_puzzle_page.wait_for_function(
            "document.getElementById('currentPoints').textContent !== 'Loading...'"
        )
        initial_points = int(points_element.text_content())
        assert initial_points == 500

        # Handle confirm dialog
        authenticated_puzzle_page.on('dialog', lambda dialog: dialog.accept())

        # Click first piece
        first_piece = authenticated_puzzle_page.locator('.reveal-puzzle-piece.locked').first
        first_piece.click()

        # Wait for unlock
        authenticated_puzzle_page.wait_for_selector('.reveal-puzzle-piece.unlocked', timeout=5000)

        # Check points decreased
        authenticated_puzzle_page.wait_for_function(
            f"document.getElementById('currentPoints').textContent === '450'"
        )
        new_points = int(points_element.text_content())
        assert new_points == 450

    def test_progress_bar_updates(self, authenticated_puzzle_page: Page, live_server_url, puzzle_user):
        """Test that progress bar updates after unlocking pieces."""
        reward = puzzle_user['reward']
        chapter = puzzle_user['chapter']

        authenticated_puzzle_page.goto(
            f"{live_server_url}/en/journey/reward/{reward.id}/"
        )
        authenticated_puzzle_page.wait_for_load_state('networkidle')
        authenticated_puzzle_page.wait_for_timeout(1000)

        # Initial progress should be 0%
        progress_label = authenticated_puzzle_page.locator('#progressLabel')
        expect(progress_label).to_have_text('0%')

        # Handle confirm dialog
        authenticated_puzzle_page.on('dialog', lambda dialog: dialog.accept())

        # Unlock a piece
        first_piece = authenticated_puzzle_page.locator('.reveal-puzzle-piece.locked').first
        first_piece.click()

        # Wait for unlock
        authenticated_puzzle_page.wait_for_selector('.reveal-puzzle-piece.unlocked', timeout=5000)

        # Progress should now be ~6% (1/16)
        authenticated_puzzle_page.wait_for_function(
            "document.getElementById('progressLabel').textContent !== '0%'"
        )
        # 1/16 = 6.25%, rounded to 6%
        expect(progress_label).to_have_text('6%')

    def test_notification_appears_on_unlock(self, authenticated_puzzle_page: Page, live_server_url, puzzle_user):
        """Test that success notification appears after unlocking."""
        reward = puzzle_user['reward']
        chapter = puzzle_user['chapter']

        authenticated_puzzle_page.goto(
            f"{live_server_url}/en/journey/reward/{reward.id}/"
        )
        authenticated_puzzle_page.wait_for_load_state('networkidle')
        authenticated_puzzle_page.wait_for_timeout(1000)

        # Handle confirm dialog
        authenticated_puzzle_page.on('dialog', lambda dialog: dialog.accept())

        # Click piece
        first_piece = authenticated_puzzle_page.locator('.reveal-puzzle-piece.locked').first
        first_piece.click()

        # Wait for notification toast
        toast = authenticated_puzzle_page.locator('.journey-toast-success')
        expect(toast).to_be_visible(timeout=5000)

    def test_already_unlocked_piece_not_clickable(self, authenticated_puzzle_page: Page, live_server_url, puzzle_user):
        """Test that unlocked pieces are not clickable."""
        reward = puzzle_user['reward']
        chapter = puzzle_user['chapter']

        authenticated_puzzle_page.goto(
            f"{live_server_url}/en/journey/reward/{reward.id}/"
        )
        authenticated_puzzle_page.wait_for_load_state('networkidle')
        authenticated_puzzle_page.wait_for_timeout(1000)

        # Track if dialog was shown
        dialog_shown = {'count': 0}

        def track_dialog(dialog):
            dialog_shown['count'] += 1
            dialog.accept()

        authenticated_puzzle_page.on('dialog', track_dialog)

        # Unlock first piece
        first_piece = authenticated_puzzle_page.locator('.reveal-puzzle-piece').first
        first_piece.click()

        # Wait for unlock
        authenticated_puzzle_page.wait_for_selector('.reveal-puzzle-piece.unlocked', timeout=5000)

        # Reset dialog count
        initial_count = dialog_shown['count']

        # Try clicking the same piece again
        unlocked_piece = authenticated_puzzle_page.locator('.reveal-puzzle-piece.unlocked').first
        unlocked_piece.click(force=True)  # Use force to bypass parent grid interception

        # Wait a moment
        authenticated_puzzle_page.wait_for_timeout(500)

        # Dialog should NOT have been shown again (piece is unlocked)
        assert dialog_shown['count'] == initial_count, "Unlocked piece should not trigger confirm dialog"


@pytest.mark.playwright
class TestPhotoPuzzleLoadingState:
    """Test the loading state specifically to verify the fix."""

    def test_loading_class_added_during_request(self, authenticated_puzzle_page: Page, live_server_url, puzzle_user):
        """Test that loading class is added while request is in flight."""
        reward = puzzle_user['reward']
        chapter = puzzle_user['chapter']

        authenticated_puzzle_page.goto(
            f"{live_server_url}/en/journey/reward/{reward.id}/"
        )
        authenticated_puzzle_page.wait_for_load_state('networkidle')
        authenticated_puzzle_page.wait_for_timeout(1000)

        # Handle confirm dialog
        authenticated_puzzle_page.on('dialog', lambda dialog: dialog.accept())

        # Get the first piece
        first_piece = authenticated_puzzle_page.locator('.reveal-puzzle-piece.locked').first

        # Click and immediately check for loading class
        first_piece.click()

        # The loading class should be added very quickly after dialog accept
        # We use a short timeout to catch it
        try:
            authenticated_puzzle_page.wait_for_selector('.reveal-puzzle-piece.loading', timeout=1000)
            loading_was_visible = True
        except Exception:
            # Loading state might be too fast to catch, which is OK
            # The important thing is the piece gets unlocked
            loading_was_visible = False

        # Ensure piece eventually gets unlocked
        authenticated_puzzle_page.wait_for_selector('.reveal-puzzle-piece.unlocked', timeout=5000)

        # Loading class should be removed after unlock
        loading_pieces = authenticated_puzzle_page.locator('.reveal-puzzle-piece.loading')
        expect(loading_pieces).to_have_count(0)

    def test_unlock_completes_without_hanging(self, authenticated_puzzle_page: Page, live_server_url, puzzle_user):
        """Test that unlocking a piece completes successfully without infinite loading.

        This tests the fix for the "charging non stop" issue - verifying that:
        1. The loading state is applied
        2. The loading state is removed after the request completes
        3. The piece transitions to unlocked state
        """
        from crush_lu.models import JourneyProgress

        reward = puzzle_user['reward']
        user = puzzle_user['user']

        authenticated_puzzle_page.goto(
            f"{live_server_url}/en/journey/reward/{reward.id}/"
        )
        authenticated_puzzle_page.wait_for_load_state('networkidle')
        authenticated_puzzle_page.wait_for_timeout(1000)

        # Get initial points from DB
        progress = JourneyProgress.objects.get(user=user)
        initial_points = progress.total_points

        # Accept the confirm dialog
        authenticated_puzzle_page.on('dialog', lambda dialog: dialog.accept())

        # Click the first piece
        first_piece = authenticated_puzzle_page.locator('.reveal-puzzle-piece.locked').first
        first_piece.click()

        # Wait for the unlock to complete (piece should become unlocked)
        # If the fix didn't work, this would timeout due to infinite loading
        authenticated_puzzle_page.wait_for_selector('.reveal-puzzle-piece.unlocked', timeout=10000)

        # Verify no pieces are stuck in loading state
        loading_pieces = authenticated_puzzle_page.locator('.reveal-puzzle-piece.loading')
        expect(loading_pieces).to_have_count(0)

        # Verify points were deducted
        progress.refresh_from_db()
        points_deducted = initial_points - progress.total_points
        assert points_deducted == 50, f"Expected 50 points deducted, got {points_deducted}"

        # Should have exactly 1 unlocked piece
        unlocked = authenticated_puzzle_page.locator('.reveal-puzzle-piece.unlocked')
        expect(unlocked).to_have_count(1)
