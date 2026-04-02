"""
Tests for Event QR Check-In and Ticket system.

Tests cover:
- Token signing/verification (Signer round-trip)
- Check-in API: success, invalid token, already attended, wrong day
- Web ticket page: requires login, only owner can view, 404 for unregistered
- Event ticket JWT generation (mocked wallet)
"""

import pytest
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.core.signing import Signer
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import CrushProfile, EventRegistration, MeetupEvent
from crush_lu.models.profiles import UserDataConsent
from crush_lu.views_ticket import _generate_checkin_token

# All crush_lu HTTP tests must use the crush-specific URL config
pytestmark = pytest.mark.urls("azureproject.urls_crush")


@pytest.fixture
def event_user(db):
    """Create a user with an approved profile and consent."""
    user = User.objects.create_user(
        username="ticketuser",
        email="ticket@example.com",
        password="testpass123",
        first_name="Alice",
    )
    CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1995, 5, 15),
        gender="F",
        location="Luxembourg City",
        is_approved=True,
        is_active=True,
    )
    UserDataConsent.objects.filter(user=user).update(crushlu_consent_given=True)
    return user


@pytest.fixture
def other_user(db):
    """Create another user with consent."""
    user = User.objects.create_user(
        username="otheruser",
        email="other@example.com",
        password="testpass123",
    )
    UserDataConsent.objects.filter(user=user).update(crushlu_consent_given=True)
    return user


@pytest.fixture
def upcoming_event(db):
    """Create an upcoming published event."""
    return MeetupEvent.objects.create(
        title="Test Speed Dating",
        description="A fun event",
        event_type="speed_dating",
        location="Test Venue",
        address="123 Test St",
        canton="Luxembourg",
        date_time=timezone.now() + timedelta(hours=2),
        duration_minutes=120,
        max_participants=20,
        registration_deadline=timezone.now() + timedelta(hours=1),
        is_published=True,
    )


@pytest.fixture
def past_event(db):
    """Create a past event (outside check-in window)."""
    return MeetupEvent.objects.create(
        title="Past Event",
        description="Already happened",
        event_type="mixer",
        location="Past Venue",
        address="456 Past St",
        canton="Luxembourg",
        date_time=timezone.now() - timedelta(days=3),
        duration_minutes=120,
        max_participants=20,
        registration_deadline=timezone.now() - timedelta(days=4),
        is_published=True,
    )


@pytest.fixture
def confirmed_registration(event_user, upcoming_event):
    """Create a confirmed registration."""
    return EventRegistration.objects.create(
        event=upcoming_event,
        user=event_user,
        status="confirmed",
    )


class TestCheckinTokenGeneration:
    """Test the token signing/verification round-trip."""

    def test_generate_token_creates_valid_signed_value(self, confirmed_registration):
        token = _generate_checkin_token(confirmed_registration)
        assert token
        assert ":" in Signer().unsign(token)

    def test_generate_token_is_idempotent(self, confirmed_registration):
        token1 = _generate_checkin_token(confirmed_registration)
        token2 = _generate_checkin_token(confirmed_registration)
        assert token1 == token2

    def test_token_saved_to_registration(self, confirmed_registration):
        assert confirmed_registration.checkin_token == ""
        _generate_checkin_token(confirmed_registration)
        confirmed_registration.refresh_from_db()
        assert confirmed_registration.checkin_token != ""

    def test_token_contains_registration_and_event_ids(self, confirmed_registration):
        token = _generate_checkin_token(confirmed_registration)
        unsigned = Signer().unsign(token)
        reg_id, event_id = unsigned.split(":")
        assert int(reg_id) == confirmed_registration.id
        assert int(event_id) == confirmed_registration.event_id


class TestCheckinAPI:
    """Test the check-in API endpoint."""

    def test_successful_checkin(self, client, confirmed_registration):
        token = _generate_checkin_token(confirmed_registration)
        url = f"/api/events/checkin/{confirmed_registration.id}/{token}/"
        response = client.post(url)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["already_checked_in"] is False
        assert "attendee_name" in data

        confirmed_registration.refresh_from_db()
        assert confirmed_registration.status == "attended"
        assert confirmed_registration.checked_in_at is not None

    def test_already_attended(self, client, confirmed_registration):
        token = _generate_checkin_token(confirmed_registration)
        confirmed_registration.status = "attended"
        confirmed_registration.checked_in_at = timezone.now()
        confirmed_registration.save()

        url = f"/api/events/checkin/{confirmed_registration.id}/{token}/"
        response = client.post(url)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["already_checked_in"] is True

    def test_invalid_token(self, client, confirmed_registration):
        url = f"/api/events/checkin/{confirmed_registration.id}/invalid-token/"
        response = client.post(url)
        assert response.status_code == 400
        assert response.json()["success"] is False

    def test_wrong_registration_id(self, client, confirmed_registration):
        token = _generate_checkin_token(confirmed_registration)
        url = f"/api/events/checkin/99999/{token}/"
        response = client.post(url)
        assert response.status_code == 400

    def test_cancelled_registration(self, client, confirmed_registration):
        token = _generate_checkin_token(confirmed_registration)
        confirmed_registration.status = "cancelled"
        confirmed_registration.save()

        url = f"/api/events/checkin/{confirmed_registration.id}/{token}/"
        response = client.post(url)
        assert response.status_code == 400
        assert "Cancelled" in response.json()["error"]

    def test_outside_checkin_window(self, client, event_user, past_event):
        reg = EventRegistration.objects.create(
            event=past_event,
            user=event_user,
            status="confirmed",
        )
        token = _generate_checkin_token(reg)
        url = f"/api/events/checkin/{reg.id}/{token}/"
        response = client.post(url)
        assert response.status_code == 400
        error_msg = response.json()["error"].lower()
        assert "window" in error_msg or "hours" in error_msg

    def test_get_method_not_allowed(self, client, confirmed_registration):
        token = _generate_checkin_token(confirmed_registration)
        url = f"/api/events/checkin/{confirmed_registration.id}/{token}/"
        response = client.get(url)
        assert response.status_code == 405


class TestWebTicketPage:
    """Test the web ticket page view."""

    def test_ticket_page_requires_login(self, client, upcoming_event):
        url = reverse("crush_lu:event_ticket", args=[upcoming_event.id])
        response = client.get(url)
        assert response.status_code == 302
        assert "login" in response.url.lower()

    def test_ticket_page_shows_for_confirmed_user(self, client, event_user, confirmed_registration):
        client.login(username="ticketuser", password="testpass123")
        url = reverse("crush_lu:event_ticket", args=[confirmed_registration.event_id])
        response = client.get(url)
        assert response.status_code == 200

    def test_ticket_page_404_for_unregistered_user(self, client, other_user, upcoming_event):
        client.login(username="otheruser", password="testpass123")
        url = reverse("crush_lu:event_ticket", args=[upcoming_event.id])
        response = client.get(url)
        assert response.status_code == 404

    def test_ticket_page_404_for_cancelled_registration(self, client, event_user, upcoming_event):
        EventRegistration.objects.create(
            event=upcoming_event,
            user=event_user,
            status="cancelled",
        )
        client.login(username="ticketuser", password="testpass123")
        url = reverse("crush_lu:event_ticket", args=[upcoming_event.id])
        response = client.get(url)
        assert response.status_code == 404

    def test_ticket_page_other_user_cannot_view(self, client, other_user, confirmed_registration):
        client.login(username="otheruser", password="testpass123")
        url = reverse("crush_lu:event_ticket", args=[confirmed_registration.event_id])
        response = client.get(url)
        assert response.status_code == 404

    def test_ticket_page_shows_checked_in_status(self, client, event_user, confirmed_registration):
        confirmed_registration.status = "attended"
        confirmed_registration.checked_in_at = timezone.now()
        confirmed_registration.save()

        client.login(username="ticketuser", password="testpass123")
        url = reverse("crush_lu:event_ticket", args=[confirmed_registration.event_id])
        response = client.get(url)
        assert response.status_code == 200


class TestEventTicketJWTView:
    """Test the Google Wallet event ticket JWT endpoint."""

    def test_jwt_endpoint_requires_login(self, client, confirmed_registration):
        url = f"/wallet/google/event-ticket/{confirmed_registration.id}/jwt/"
        response = client.get(url)
        assert response.status_code == 302

    def test_jwt_endpoint_404_for_wrong_user(self, client, other_user, confirmed_registration):
        client.login(username="otheruser", password="testpass123")
        url = f"/wallet/google/event-ticket/{confirmed_registration.id}/jwt/"
        response = client.get(url)
        assert response.status_code == 404

    def test_jwt_endpoint_returns_503_when_not_configured(self, client, event_user, confirmed_registration):
        client.login(username="ticketuser", password="testpass123")
        url = f"/wallet/google/event-ticket/{confirmed_registration.id}/jwt/"
        response = client.get(url)
        # Without wallet configured, should return 503
        assert response.status_code == 503
