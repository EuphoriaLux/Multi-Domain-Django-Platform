"""
Pytest configuration and fixtures for Crush.lu testing.
Provides fixtures for Playwright browser testing and Django integration.

This module configures mocks for external services to ensure tests run
without requiring real Azure, email, or OAuth credentials.
"""
import os
import sys
import pytest
from unittest.mock import MagicMock

# Mock pywebpush module before Django imports it
# This allows tests to run without installing pywebpush (which has complex C dependencies)
mock_pywebpush = MagicMock()
mock_pywebpush.WebPushException = type('WebPushException', (Exception,), {
    '__init__': lambda self, message, response=None: (
        setattr(self, 'response', response) or Exception.__init__(self, message)
    )
})
mock_pywebpush.webpush = MagicMock()
sys.modules['pywebpush'] = mock_pywebpush


# Force synchronous database operations for Playwright tests
# This fixes the "SynchronousOnlyOperation" error with live_server
os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')


def pytest_configure(config):
    """
    Hook called early in pytest startup to configure Django settings.
    This runs before pytest-django sets up Django.

    Configures:
    - Django settings module
    - Static files storage (avoids collectstatic requirement)
    - Email backend (uses in-memory backend for testing)
    - Media storage (uses local filesystem, not Azure)
    """
    # Ensure Django settings module is set
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'azureproject.settings')

    # Set test environment variables for services that check them
    os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-pytest')

    # Mock Azure storage variables (used by code that checks for their presence)
    os.environ.setdefault('AZURE_ACCOUNT_NAME', 'teststorageaccount')
    os.environ.setdefault('AZURE_ACCOUNT_KEY', 'dGVzdGtleQ==')  # base64 'testkey'
    os.environ.setdefault('AZURE_CONTAINER_NAME', 'testcontainer')

    # Mock email settings (Graph API)
    os.environ.setdefault('GRAPH_TENANT_ID', 'test-tenant-id')
    os.environ.setdefault('GRAPH_CLIENT_ID', 'test-client-id')
    os.environ.setdefault('GRAPH_CLIENT_SECRET', 'test-client-secret')

    # Mock push notification settings
    os.environ.setdefault('VAPID_PUBLIC_KEY', 'test-vapid-public-key')
    os.environ.setdefault('VAPID_PRIVATE_KEY', 'test-vapid-private-key')
    os.environ.setdefault('VAPID_ADMIN_EMAIL', 'test@example.com')

    # Patch staticfiles storage BEFORE Django fully initializes
    # This is needed because ManifestStaticFilesStorage fails without collectstatic
    from django.conf import settings
    if hasattr(settings, 'STORAGES'):
        settings.STORAGES['staticfiles'] = {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'
        }
        # Use local filesystem for media during tests (not Azure Blob)
        settings.STORAGES['default'] = {
            'BACKEND': 'django.core.files.storage.FileSystemStorage'
        }

    # Force console email backend for tests (no real emails sent)
    settings.EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'

    # Set SITE_ID for tests to avoid dynamic site lookup issues with live_server
    # live_server uses ports like localhost:12345, which don't match Site domains
    settings.SITE_ID = 1


@pytest.fixture(scope='session', autouse=True)
def _patch_static_storage():
    """
    Patch the static files storage to use simple storage.
    This fixture runs early to ensure storage is patched before live_server starts.
    """
    from django.contrib.staticfiles import storage as static_storage
    from django.contrib.staticfiles.storage import StaticFilesStorage

    # Replace the cached storage instance
    static_storage.staticfiles_storage = StaticFilesStorage()

    yield

    # No cleanup needed


# Import User model after Django is configured
from django.contrib.auth import get_user_model
from playwright.sync_api import Page

User = get_user_model()


@pytest.fixture(scope='session')
def django_db_modify_db_settings():
    """Allow database modifications in tests."""
    pass


@pytest.fixture(scope='session', autouse=True)
def django_db_setup_once(django_db_setup, django_db_blocker):
    """
    Ensure migrations run once before parallel test execution.
    This prevents race conditions when pytest-xdist workers try to
    apply migrations simultaneously.

    Must run after django_db_setup to ensure database is ready.
    """
    with django_db_blocker.unblock():
        # Force migrations to complete before parallel execution starts
        from django.core.management import call_command
        call_command('migrate', '--run-syncdb', verbosity=0, interactive=False)


@pytest.fixture(scope='session', autouse=True)
def setup_site_for_live_server(django_db_setup_once, django_db_blocker):
    """
    Create Site objects at session start for live_server tests.

    This must run before any live_server tests start to ensure the
    Site exists in the database when Django's allauth tries to look it up.

    Note: django_db_setup_once ensures migrations are fully applied before
    we try to access the Site model.
    """
    with django_db_blocker.unblock():
        from django.contrib.sites.models import Site

        # Delete any existing localhost Site that might conflict
        Site.objects.filter(domain='localhost').exclude(id=1).delete()

        # Create/update sites that live_server might use
        Site.objects.update_or_create(
            id=1,
            defaults={'domain': 'localhost', 'name': 'localhost'}
        )
        Site.objects.update_or_create(
            domain='testserver',
            defaults={'name': 'Test Server'}
        )
        Site.objects.update_or_create(
            domain='127.0.0.1',
            defaults={'name': 'Live Server'}
        )


@pytest.fixture(autouse=True)
def setup_site(db):
    """Create Site objects for non-live_server tests."""
    from django.contrib.sites.models import Site
    Site.objects.get_or_create(
        id=1,
        defaults={'domain': 'localhost', 'name': 'localhost'}
    )
    Site.objects.update_or_create(
        domain='testserver',
        defaults={'name': 'Test Server'}
    )
    Site.objects.update_or_create(
        domain='127.0.0.1',
        defaults={'name': 'Live Server'}
    )


@pytest.fixture
def live_server_url(live_server):
    """Return the URL of the live test server."""
    return live_server.url


@pytest.fixture
def test_user(db):
    """Create a test user for authentication tests."""
    user = User.objects.create_user(
        username='testuser@example.com',
        email='testuser@example.com',
        password='testpass123',
        first_name='Test',
        last_name='User'
    )
    return user


@pytest.fixture
def test_user_with_profile(db, test_user):
    """Create a test user with an approved Crush profile."""
    from crush_lu.models import CrushProfile
    from datetime import date

    profile = CrushProfile.objects.create(
        user=test_user,
        date_of_birth=date(1995, 5, 15),
        gender='M',
        location='Luxembourg City',
        bio='Test bio for testing purposes',
        interests='Testing, Coding, Coffee',
        is_approved=True,
        is_active=True
    )
    return test_user, profile


@pytest.fixture
def authenticated_page(page: Page, live_server_url, test_user):
    """
    Provide a Playwright page with an authenticated user session.
    Logs in the test user and returns the page ready for testing.
    """
    # Navigate to login page
    page.goto(f"{live_server_url}/accounts/login/")

    # Fill in login form
    page.fill('input[name="login"]', test_user.email)
    page.fill('input[name="password"]', 'testpass123')

    # Submit form
    page.click('button[type="submit"]')

    # Wait for redirect after login
    page.wait_for_load_state('networkidle')

    return page


@pytest.fixture
def coach_user(db):
    """Create a test coach user."""
    from crush_lu.models import CrushCoach

    user = User.objects.create_user(
        username='coach@example.com',
        email='coach@example.com',
        password='coachpass123',
        first_name='Coach',
        last_name='User'
    )

    coach = CrushCoach.objects.create(
        user=user,
        display_name='Coach Marie',
        specialization='General coaching',
        is_active=True,
        max_active_reviews=10
    )

    return user, coach


@pytest.fixture
def sample_event(db, coach_user):
    """Create a sample meetup event for testing."""
    from crush_lu.models import MeetupEvent
    from datetime import timedelta
    from django.utils import timezone

    user, coach = coach_user

    event = MeetupEvent.objects.create(
        title='Test Speed Dating Event',
        description='A test event for unit testing',
        event_type='speed_dating',
        date_time=timezone.now() + timedelta(days=7),
        location='Test Location, Luxembourg',
        address='123 Test Street, Luxembourg City',
        max_participants=20,
        min_age=18,
        max_age=35,
        registration_deadline=timezone.now() + timedelta(days=5),
        registration_fee=10.00,
        is_published=True
    )

    return event


@pytest.fixture(scope='session')
def browser_context_args(browser_context_args):
    """Configure browser context for all Playwright tests."""
    return {
        **browser_context_args,
        'viewport': {'width': 1280, 'height': 720},
        'ignore_https_errors': True,
    }


@pytest.fixture
def screenshot_dir(tmp_path):
    """Provide a temporary directory for test screenshots."""
    screenshots = tmp_path / 'screenshots'
    screenshots.mkdir()
    return screenshots


@pytest.fixture
def event_with_registrations(db, sample_event, test_user_with_profile):
    """Create an event with confirmed registrations for testing."""
    from crush_lu.models import EventRegistration

    user, profile = test_user_with_profile

    registration = EventRegistration.objects.create(
        event=sample_event,
        user=user,
        status='confirmed'
    )

    return sample_event, [registration]


@pytest.fixture
def connection_pair(db):
    """Create two users with a mutual connection."""
    from crush_lu.models import CrushProfile, MeetupEvent, EventConnection
    from datetime import date, timedelta
    from django.utils import timezone

    # Create two users with profiles
    user1 = User.objects.create_user(
        username='user1@example.com',
        email='user1@example.com',
        password='testpass123',
        first_name='John',
        last_name='Doe'
    )

    user2 = User.objects.create_user(
        username='user2@example.com',
        email='user2@example.com',
        password='testpass123',
        first_name='Jane',
        last_name='Smith'
    )

    for user, gender in [(user1, 'M'), (user2, 'F')]:
        CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 5, 15),
            gender=gender,
            location='Luxembourg City',
            is_approved=True,
            is_active=True
        )

    # Create event where they met
    event = MeetupEvent.objects.create(
        title='Connection Event',
        description='Event where they met',
        event_type='mixer',
        date_time=timezone.now() - timedelta(days=1),
        location='Luxembourg',
        address='123 Test Street',
        max_participants=20,
        registration_deadline=timezone.now() - timedelta(days=3),
        is_published=True
    )

    # Create mutual connection
    connection = EventConnection.objects.create(
        event=event,
        from_user=user1,
        to_user=user2,
        status='connected',
        connected_at=timezone.now()
    )

    return user1, user2, connection, event


@pytest.fixture
def unapproved_user(db):
    """Create a user with an unapproved profile."""
    from crush_lu.models import CrushProfile
    from datetime import date

    user = User.objects.create_user(
        username='unapproved@example.com',
        email='unapproved@example.com',
        password='testpass123',
        first_name='Pending',
        last_name='User'
    )

    profile = CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1995, 5, 15),
        gender='M',
        location='Luxembourg City',
        is_approved=False,
        is_active=True
    )

    return user, profile


# =============================================================================
# EMAIL TESTING FIXTURES
# =============================================================================

@pytest.fixture
def mailbox():
    """
    Provide access to sent emails during tests.

    Usage:
        def test_email_sent(mailbox):
            # Trigger code that sends email
            send_welcome_email(user)

            # Check emails
            assert len(mailbox) == 1
            assert mailbox[0].subject == 'Welcome to Crush.lu'
            assert 'test@example.com' in mailbox[0].to
    """
    from django.core import mail
    return mail.outbox


@pytest.fixture(autouse=True)
def clear_mailbox():
    """Clear the email outbox before each test."""
    from django.core import mail
    mail.outbox = []
    yield
    mail.outbox = []


# =============================================================================
# FILE UPLOAD TESTING FIXTURES
# =============================================================================

@pytest.fixture
def temp_image(tmp_path):
    """
    Create a temporary test image for upload tests.

    Usage:
        def test_photo_upload(temp_image):
            with open(temp_image, 'rb') as f:
                response = client.post('/upload/', {'photo': f})
    """
    from PIL import Image

    # Create a simple 100x100 red image
    img = Image.new('RGB', (100, 100), color='red')
    image_path = tmp_path / 'test_image.jpg'
    img.save(image_path, 'JPEG')
    return image_path


@pytest.fixture
def mock_azure_storage(mocker):
    """
    Mock Azure Blob Storage for tests that specifically test storage behavior.

    Usage:
        def test_profile_photo_upload(mock_azure_storage, test_user):
            # Upload will use mock instead of real Azure
            profile.photo_1.save('test.jpg', content)
            mock_azure_storage.save.assert_called_once()
    """
    mock_storage = mocker.patch('storages.backends.azure_storage.AzureStorage')
    mock_instance = mock_storage.return_value
    mock_instance.save.return_value = 'mocked/path/to/file.jpg'
    mock_instance.url.return_value = 'https://mock.blob.core.windows.net/test/file.jpg'
    return mock_instance


# =============================================================================
# JOURNEY GIFT E2E TESTING FIXTURES
# =============================================================================

@pytest.fixture
def sender_user(transactional_db):
    """Create an authenticated user who will create journey gifts."""
    from allauth.account.models import EmailAddress

    user = User.objects.create_user(
        username='sender@example.com',
        email='sender@example.com',
        password='sender123',
        first_name='Alice',
        last_name='Sender'
    )
    # Create EmailAddress for Allauth (required for email login)
    EmailAddress.objects.create(
        user=user,
        email=user.email,
        verified=True,
        primary=True
    )
    return user


@pytest.fixture
def recipient_user(transactional_db):
    """Create a user who will claim a journey gift."""
    from allauth.account.models import EmailAddress

    user = User.objects.create_user(
        username='recipient@example.com',
        email='recipient@example.com',
        password='recipient123',
        first_name='Bob',
        last_name='Recipient'
    )
    # Create EmailAddress for Allauth (required for email login)
    EmailAddress.objects.create(
        user=user,
        email=user.email,
        verified=True,
        primary=True
    )
    return user


@pytest.fixture
def pending_gift(transactional_db, sender_user):
    """Create a pending JourneyGift ready to be claimed."""
    from crush_lu.models import JourneyGift
    from datetime import date

    gift = JourneyGift.objects.create(
        sender=sender_user,
        recipient_name='My Special Person',
        date_first_met=date(2024, 2, 14),
        location_first_met='Luxembourg City',
        sender_message='A journey created with love!'
    )
    return gift


@pytest.fixture
def expired_gift(transactional_db, sender_user):
    """Create an expired JourneyGift."""
    from crush_lu.models import JourneyGift
    from datetime import date
    from django.utils import timezone
    from datetime import timedelta

    gift = JourneyGift.objects.create(
        sender=sender_user,
        recipient_name='Expired Person',
        date_first_met=date(2024, 1, 1),
        location_first_met='Old Location',
        expires_at=timezone.now() - timedelta(days=1)  # Already expired
    )
    return gift


@pytest.fixture
def claimed_gift(transactional_db, sender_user, recipient_user):
    """Create a JourneyGift that has already been claimed."""
    from crush_lu.models import JourneyGift
    from datetime import date
    from django.utils import timezone

    gift = JourneyGift.objects.create(
        sender=sender_user,
        recipient_name='Already Claimed',
        date_first_met=date(2024, 3, 15),
        location_first_met='Claimed Location',
        status=JourneyGift.Status.CLAIMED,
        claimed_by=recipient_user,
        claimed_at=timezone.now()
    )
    return gift


@pytest.fixture
def authenticated_sender_page(page: Page, live_server_url, sender_user, transactional_db):
    """Playwright page logged in as the gift sender."""
    # Note: transactional_db commits data so live_server can see it
    # Ensure Site exists for live_server
    from django.contrib.sites.models import Site
    Site.objects.get_or_create(id=1, defaults={'domain': 'localhost', 'name': 'localhost'})
    Site.objects.get_or_create(domain='127.0.0.1', defaults={'name': 'Live Server'})

    page.goto(f"{live_server_url}/accounts/login/")
    # Wait for login form to be ready
    page.wait_for_selector('input[name="login"]', timeout=10000)

    # Dismiss cookie banner if present (blocks clicks on other elements)
    cookie_decline = page.locator('button:has-text("Decline All")')
    if cookie_decline.count() > 0:
        cookie_decline.click()
        page.wait_for_timeout(500)  # Wait for banner to disappear

    page.fill('input[name="login"]', sender_user.email)
    page.fill('input[name="password"]', 'sender123')
    # Click the Login button (use text selector to avoid navbar button)
    page.click('button:has-text("Login")')
    page.wait_for_load_state('networkidle')
    return page


@pytest.fixture
def authenticated_recipient_page(page: Page, live_server_url, recipient_user, transactional_db):
    """Playwright page logged in as the gift recipient."""
    # Note: transactional_db commits data so live_server can see it
    # Ensure Site exists for live_server
    from django.contrib.sites.models import Site
    Site.objects.get_or_create(id=1, defaults={'domain': 'localhost', 'name': 'localhost'})
    Site.objects.get_or_create(domain='127.0.0.1', defaults={'name': 'Live Server'})

    page.goto(f"{live_server_url}/accounts/login/")
    # Wait for login form to be ready
    page.wait_for_selector('input[name="login"]', timeout=10000)

    # Dismiss cookie banner if present (blocks clicks on other elements)
    cookie_decline = page.locator('button:has-text("Decline All")')
    if cookie_decline.count() > 0:
        cookie_decline.click()
        page.wait_for_timeout(500)  # Wait for banner to disappear

    page.fill('input[name="login"]', recipient_user.email)
    page.fill('input[name="password"]', 'recipient123')
    # Click the Login button (use text selector to avoid navbar button)
    page.click('button:has-text("Login")')
    page.wait_for_load_state('networkidle')
    return page
