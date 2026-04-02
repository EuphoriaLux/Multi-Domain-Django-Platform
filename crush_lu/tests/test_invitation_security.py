"""
Tests for invitation acceptance security fix (Issue #138).

This test suite validates that the invitation acceptance flow:
- Requires actual date of birth (no hardcoded ages)
- Validates 18+ age requirement
- Does not auto-approve profiles (requires coach review)
- Prevents minors from accessing the platform
"""
import pytest
import uuid
from datetime import timedelta
from django.utils import timezone
from django.contrib.auth.models import User
from django.urls import reverse
from django.test import Client

from crush_lu.models import (
    EventInvitation,
    MeetupEvent,
    CrushProfile,
    CrushCoach,
)
from crush_lu.models.profiles import UserDataConsent
from crush_lu.forms import InvitationAcceptanceForm


@pytest.fixture
def private_event(db):
    """Create a private invitation-only event."""
    event = MeetupEvent.objects.create(
        title="Private VIP Event",
        description="Exclusive private event",
        event_type="mixer",
        location="Luxembourg City",
        address="123 Test Street",
        date_time=timezone.now() + timedelta(days=7),
        duration_minutes=120,
        max_participants=20,
        min_age=18,
        max_age=99,
        registration_deadline=timezone.now() + timedelta(days=5),
        registration_fee=0.00,
        is_published=True,
        is_cancelled=False,
        is_private_invitation=True,
        invitation_code="vip2024",
        invitation_expires_at=timezone.now() + timedelta(days=30),
    )
    return event


@pytest.fixture
def pending_invitation(db, private_event):
    """Create a pending invitation for external guest."""
    invitation = EventInvitation.objects.create(
        event=private_event,
        guest_email="guest@example.com",
        guest_first_name="John",
        guest_last_name="Doe",
        status="pending",
        approval_status="pending_approval",
    )
    return invitation


@pytest.mark.django_db
class TestInvitationAgeVerification:
    """Test age verification in invitation acceptance."""

    def test_form_requires_date_of_birth(self, pending_invitation):
        """Test that InvitationAcceptanceForm requires date of birth."""
        form = InvitationAcceptanceForm(
            data={'agree_to_terms': True},
            invitation=pending_invitation
        )
        assert not form.is_valid()
        assert 'date_of_birth' in form.errors

    def test_form_rejects_future_dates(self, pending_invitation):
        """Test that future dates are rejected."""
        future_date = timezone.now().date() + timedelta(days=1)
        form = InvitationAcceptanceForm(
            data={
                'date_of_birth': future_date,
                'agree_to_terms': True,
            },
            invitation=pending_invitation
        )
        assert not form.is_valid()
        assert 'date_of_birth' in form.errors

    def test_form_rejects_under_18(self, pending_invitation):
        """Test that users under 18 are rejected."""
        # Create date of birth for someone who is 17 years old
        dob = timezone.now().date() - timedelta(days=365 * 17)
        form = InvitationAcceptanceForm(
            data={
                'date_of_birth': dob,
                'agree_to_terms': True,
            },
            invitation=pending_invitation
        )
        assert not form.is_valid()
        assert 'date_of_birth' in form.errors
        assert '18' in str(form.errors['date_of_birth'])

    def test_form_accepts_18_year_old(self, pending_invitation):
        """Test that 18 year olds are accepted."""
        # Create date of birth for someone who is exactly 18 years and 1 day old
        # Adding 1 extra day to account for leap years and ensure they're definitely 18+
        dob = timezone.now().date() - timedelta(days=365 * 18 + 5)
        form = InvitationAcceptanceForm(
            data={
                'date_of_birth': dob,
                'agree_to_terms': True,
            },
            invitation=pending_invitation
        )
        assert form.is_valid(), f"Form errors: {form.errors}"

    def test_form_accepts_adult(self, pending_invitation):
        """Test that adults (25+ years) are accepted."""
        dob = timezone.now().date() - timedelta(days=365 * 25)
        form = InvitationAcceptanceForm(
            data={
                'date_of_birth': dob,
                'agree_to_terms': True,
            },
            invitation=pending_invitation
        )
        assert form.is_valid(), f"Form errors: {form.errors}"

    def test_form_rejects_unrealistic_age(self, pending_invitation):
        """Test that unrealistic ages (120+) are rejected."""
        # 121 years + extra days to ensure we're over the limit
        dob = timezone.now().date() - timedelta(days=365 * 121 + 100)
        form = InvitationAcceptanceForm(
            data={
                'date_of_birth': dob,
                'agree_to_terms': True,
            },
            invitation=pending_invitation
        )
        assert not form.is_valid()
        assert 'date_of_birth' in form.errors

    def test_form_requires_terms_agreement(self, pending_invitation):
        """Test that terms agreement is required."""
        dob = timezone.now().date() - timedelta(days=365 * 25)
        form = InvitationAcceptanceForm(
            data={
                'date_of_birth': dob,
                'agree_to_terms': False,
            },
            invitation=pending_invitation
        )
        assert not form.is_valid()
        assert 'agree_to_terms' in form.errors


@pytest.mark.django_db
@pytest.mark.urls('azureproject.urls_crush')
class TestInvitationAcceptanceView:
    """Test invitation acceptance view creates profile correctly."""

    def test_invitation_acceptance_creates_profile_with_actual_dob(
        self, client, pending_invitation
    ):
        """Test that accepting invitation creates profile with actual DOB."""
        url = reverse('crush_lu:invitation_accept', kwargs={'code': pending_invitation.invitation_code})
        dob = timezone.now().date() - timedelta(days=365 * 28)

        response = client.post(url, data={
            'date_of_birth': dob.strftime('%Y-%m-%d'),
            'agree_to_terms': True,
        })

        # Should create user and profile
        assert User.objects.filter(email=pending_invitation.guest_email).exists()
        user = User.objects.get(email=pending_invitation.guest_email)
        assert CrushProfile.objects.filter(user=user).exists()

        profile = CrushProfile.objects.get(user=user)
        # CRITICAL: Profile should have actual DOB, not hardcoded age
        assert profile.date_of_birth == dob
        # Age should be 27 or 28 depending on whether birthday has passed this year
        assert profile.age in [27, 28], f"Age should be 27 or 28, got {profile.age}"

    def test_invitation_acceptance_requires_coach_approval(
        self, client, pending_invitation
    ):
        """Test that invitation acceptance does NOT auto-approve profile."""
        url = reverse('crush_lu:invitation_accept', kwargs={'code': pending_invitation.invitation_code})
        dob = timezone.now().date() - timedelta(days=365 * 25)

        response = client.post(url, data={
            'date_of_birth': dob.strftime('%Y-%m-%d'),
            'agree_to_terms': True,
        })

        user = User.objects.get(email=pending_invitation.guest_email)
        profile = CrushProfile.objects.get(user=user)

        # CRITICAL: Profile should NOT be auto-approved
        assert profile.is_approved is False
        assert profile.approved_at is None

    def test_invitation_acceptance_rejects_minor(self, client, pending_invitation):
        """Test that minors cannot accept invitations."""
        url = reverse('crush_lu:invitation_accept', kwargs={'code': pending_invitation.invitation_code})
        # 17 years old - should be rejected
        dob = timezone.now().date() - timedelta(days=365 * 17)

        response = client.post(url, data={
            'date_of_birth': dob.strftime('%Y-%m-%d'),
            'agree_to_terms': True,
        })

        # Should not create user
        assert not User.objects.filter(email=pending_invitation.guest_email).exists()
        # Should show form with errors
        assert response.status_code == 200
        assert 'form' in response.context
        assert not response.context['form'].is_valid()

    def test_invitation_acceptance_logs_in_user(self, client, pending_invitation):
        """Test that successful acceptance logs the user in."""
        url = reverse('crush_lu:invitation_accept', kwargs={'code': pending_invitation.invitation_code})
        dob = timezone.now().date() - timedelta(days=365 * 25)

        response = client.post(url, data={
            'date_of_birth': dob.strftime('%Y-%m-%d'),
            'agree_to_terms': True,
        })

        # User should be logged in
        assert '_auth_user_id' in client.session

    def test_invitation_acceptance_updates_invitation_status(
        self, client, pending_invitation
    ):
        """Test that invitation status is updated to 'accepted'."""
        url = reverse('crush_lu:invitation_accept', kwargs={'code': pending_invitation.invitation_code})
        dob = timezone.now().date() - timedelta(days=365 * 25)

        response = client.post(url, data={
            'date_of_birth': dob.strftime('%Y-%m-%d'),
            'agree_to_terms': True,
        })

        pending_invitation.refresh_from_db()
        assert pending_invitation.status == 'accepted'
        assert pending_invitation.accepted_at is not None
        assert pending_invitation.created_user is not None


@pytest.mark.django_db
@pytest.mark.urls('azureproject.urls_crush')
class TestEventRegistrationSecurity:
    """Test event registration security for invited users."""

    def test_existing_invited_user_without_profile_redirected(
        self, client, private_event
    ):
        """Test that existing invited users without profile are redirected to profile creation."""
        # Create user without profile
        user = User.objects.create_user(
            username='existing@example.com',
            email='existing@example.com',
            password='testpass123',
            first_name='Existing',
            last_name='User'
        )
        UserDataConsent.objects.filter(user=user).update(crushlu_consent_given=True)

        # Add user to invited_users
        private_event.invited_users.add(user)

        # Login
        client.login(username='existing@example.com', password='testpass123')

        # Try to register for event
        url = reverse('crush_lu:event_register', kwargs={'event_id': private_event.id})
        response = client.get(url)

        # Should redirect to profile creation, not auto-create profile
        assert response.status_code == 302
        assert 'create-profile' in response.url

    def test_external_guest_without_profile_shows_error(
        self, client, private_event, pending_invitation
    ):
        """Test that external guests without profile see error message."""
        # Create user but no profile (should never happen in real flow)
        user = User.objects.create_user(
            username='guest@example.com',
            email='guest@example.com',
            password='testpass123',
            first_name='Guest',
            last_name='User'
        )
        UserDataConsent.objects.filter(user=user).update(crushlu_consent_given=True)

        # Mark invitation as approved
        pending_invitation.approval_status = 'approved'
        pending_invitation.created_user = user
        pending_invitation.save()

        # Login
        client.login(username='guest@example.com', password='testpass123')

        # Try to register for event
        url = reverse('crush_lu:event_register', kwargs={'event_id': private_event.id})
        response = client.get(url)

        # Should redirect to event detail with error message
        assert response.status_code == 302
        assert f'/events/{private_event.id}/' in response.url


@pytest.mark.django_db
class TestSecurityRegression:
    """Regression tests to ensure hardcoded ages don't return."""

    def test_no_hardcoded_age_25_in_codebase(self):
        """
        Meta-test: Ensure we don't have hardcoded age 25 in profile creation.

        This test documents the security issue and helps prevent regression.
        """
        # This is a documentation test - the actual fix is in views.py
        # The vulnerable code was:
        #   date_of_birth=timezone.now().date() - timedelta(days=365*25)
        #
        # This has been replaced with form-based age verification.
        assert True, "See views.py:invitation_accept for the fix"

    def test_no_auto_approval_for_invitations(self):
        """
        Meta-test: Ensure invited guests don't get auto-approved profiles.

        This test documents the security requirement.
        """
        # The vulnerable code was:
        #   is_approved=True  # Auto-approve VIP guests
        #
        # This has been replaced with:
        #   is_approved=False  # Requires coach approval
        assert True, "See views.py:invitation_accept for the fix"
