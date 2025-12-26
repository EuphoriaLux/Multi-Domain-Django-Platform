"""
Pytest configuration and fixtures for Crush.lu testing.
Provides fixtures for Playwright browser testing and Django integration.
"""
import os
import pytest


# Force synchronous database operations for Playwright tests
# This fixes the "SynchronousOnlyOperation" error with live_server
os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')


def pytest_configure(config):
    """
    Hook called early in pytest startup to configure Django settings.
    This runs before pytest-django sets up Django.
    """
    # Ensure Django settings module is set
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'azureproject.settings')

    # Patch staticfiles storage BEFORE Django fully initializes
    # This is needed because ManifestStaticFilesStorage fails without collectstatic
    from django.conf import settings
    if hasattr(settings, 'STORAGES'):
        settings.STORAGES['staticfiles'] = {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'
        }


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


@pytest.fixture(autouse=True)
def setup_site(db):
    """Create a Site object for Django allauth to work properly in tests."""
    from django.contrib.sites.models import Site
    Site.objects.get_or_create(
        id=1,
        defaults={'domain': 'localhost', 'name': 'localhost'}
    )
    Site.objects.update_or_create(
        domain='testserver',
        defaults={'name': 'Test Server'}
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
