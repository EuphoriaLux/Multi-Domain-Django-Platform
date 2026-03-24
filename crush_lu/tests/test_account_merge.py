"""
Tests for the account merge service.

Tests the merge_accounts function that handles transferring data from a
duplicate user account to a keeper account (e.g., Apple "Hide My Email"
duplicate resolution).
"""

import pytest
from datetime import date, timedelta
from django.contrib.auth.models import User
from django.utils import timezone

from crush_lu.services.account_merge import merge_accounts


@pytest.fixture
def keeper_user(db):
    return User.objects.create_user(
        username='keeper@example.com',
        email='keeper@example.com',
        password='testpass123',
        first_name='Keeper',
        last_name='User',
    )


@pytest.fixture
def duplicate_user(db):
    return User.objects.create_user(
        username='duplicate@privaterelay.appleid.com',
        email='duplicate@privaterelay.appleid.com',
        password='testpass123',
        first_name='Duplicate',
        last_name='User',
    )


@pytest.fixture
def merge_event(db):
    """Create a sample event for merge tests (avoids coach_user fixture issues)."""
    from crush_lu.models import MeetupEvent

    return MeetupEvent.objects.create(
        title='Merge Test Event',
        description='Event for merge testing',
        event_type='speed_dating',
        date_time=timezone.now() + timedelta(days=7),
        location='Test Location',
        address='123 Test Street',
        max_participants=20,
        min_age=18,
        max_age=35,
        registration_deadline=timezone.now() + timedelta(days=5),
        registration_fee=10.00,
        is_published=True,
    )


@pytest.fixture
def keeper_with_profile(keeper_user):
    from crush_lu.models import CrushProfile

    profile = CrushProfile.objects.create(
        user=keeper_user,
        date_of_birth=date(1995, 5, 15),
        gender='F',
        location='Luxembourg City',
        bio='Keeper bio',
        is_approved=True,
        is_active=True,
    )
    return keeper_user, profile


@pytest.fixture
def duplicate_with_profile(duplicate_user):
    from crush_lu.models import CrushProfile

    profile = CrushProfile.objects.create(
        user=duplicate_user,
        date_of_birth=date(1995, 5, 15),
        gender='F',
        location='Esch-sur-Alzette',
        bio='Duplicate bio',
        is_approved=False,
        is_active=True,
    )
    return duplicate_user, profile


class TestMergeAccountsBasic:
    def test_cannot_merge_user_with_itself(self, keeper_user):
        with pytest.raises(ValueError, match="Cannot merge a user with themselves"):
            merge_accounts(keeper_user, keeper_user)

    def test_deactivates_duplicate(self, keeper_user, duplicate_user):
        merge_accounts(keeper_user, duplicate_user)
        duplicate_user.refresh_from_db()
        assert duplicate_user.is_active is False

    def test_keeper_remains_active(self, keeper_user, duplicate_user):
        merge_accounts(keeper_user, duplicate_user)
        keeper_user.refresh_from_db()
        assert keeper_user.is_active is True

    def test_returns_log(self, keeper_user, duplicate_user):
        log = merge_accounts(keeper_user, duplicate_user)
        assert isinstance(log, list)
        assert len(log) > 0
        assert any("Deactivated" in entry for entry in log)


class TestMergeSocialAccounts:
    def test_moves_social_account_to_keeper(self, keeper_user, duplicate_user):
        from allauth.socialaccount.models import SocialAccount

        SocialAccount.objects.create(
            user=duplicate_user,
            provider='apple',
            uid='apple-uid-123',
            extra_data={'email': 'relay@privaterelay.appleid.com'},
        )

        merge_accounts(keeper_user, duplicate_user)

        assert SocialAccount.objects.filter(user=keeper_user, provider='apple').exists()
        assert not SocialAccount.objects.filter(user=duplicate_user).exists()

    def test_moves_different_provider(self, keeper_user, duplicate_user):
        from allauth.socialaccount.models import SocialAccount

        SocialAccount.objects.create(
            user=keeper_user, provider='google', uid='google-uid-1', extra_data={}
        )
        SocialAccount.objects.create(
            user=duplicate_user, provider='apple', uid='apple-uid-1', extra_data={}
        )

        merge_accounts(keeper_user, duplicate_user)

        assert SocialAccount.objects.filter(user=keeper_user).count() == 2
        assert not SocialAccount.objects.filter(user=duplicate_user).exists()


class TestMergeEmailAddresses:
    def test_moves_email_address(self, keeper_user, duplicate_user):
        from allauth.account.models import EmailAddress

        EmailAddress.objects.create(
            user=duplicate_user, email='relay@privaterelay.appleid.com', verified=True
        )

        merge_accounts(keeper_user, duplicate_user)

        ea = EmailAddress.objects.get(email='relay@privaterelay.appleid.com')
        assert ea.user == keeper_user
        assert ea.primary is False

    def test_skips_duplicate_email(self, keeper_user, duplicate_user):
        from allauth.account.models import EmailAddress

        EmailAddress.objects.create(
            user=keeper_user, email='shared@example.com', verified=True, primary=True
        )
        EmailAddress.objects.create(
            user=duplicate_user, email='shared@example.com', verified=False
        )

        merge_accounts(keeper_user, duplicate_user)

        assert EmailAddress.objects.filter(email='shared@example.com').count() == 1
        assert EmailAddress.objects.get(email='shared@example.com').user == keeper_user


class TestMergeProfiles:
    def test_moves_profile_if_keeper_has_none(self, keeper_user, duplicate_with_profile):
        from crush_lu.models import CrushProfile

        dup_user, dup_profile = duplicate_with_profile

        merge_accounts(keeper_user, dup_user)

        assert CrushProfile.objects.filter(user=keeper_user).exists()
        assert not CrushProfile.objects.filter(user=dup_user).exists()

    def test_keeps_keeper_profile_if_both_have_profiles(
        self, keeper_with_profile, duplicate_with_profile
    ):
        from crush_lu.models import CrushProfile

        keeper_user, keeper_profile = keeper_with_profile
        dup_user, dup_profile = duplicate_with_profile

        merge_accounts(keeper_user, dup_user)

        keeper_profile.refresh_from_db()
        assert keeper_profile.bio == 'Keeper bio'
        assert not CrushProfile.objects.filter(user=dup_user).exists()


class TestMergeEventRegistrations:
    def test_moves_registration(self, keeper_user, duplicate_user, merge_event):
        from crush_lu.models import EventRegistration

        EventRegistration.objects.create(
            event=merge_event, user=duplicate_user, status='confirmed'
        )

        merge_accounts(keeper_user, duplicate_user)

        assert EventRegistration.objects.filter(
            event=merge_event, user=keeper_user
        ).exists()

    def test_skips_duplicate_registration(self, keeper_user, duplicate_user, merge_event):
        from crush_lu.models import EventRegistration

        EventRegistration.objects.create(
            event=merge_event, user=keeper_user, status='confirmed'
        )
        EventRegistration.objects.create(
            event=merge_event, user=duplicate_user, status='pending'
        )

        merge_accounts(keeper_user, duplicate_user)

        assert EventRegistration.objects.filter(user=keeper_user).count() == 1
        assert not EventRegistration.objects.filter(user=duplicate_user).exists()


class TestMergeConnections:
    def test_moves_connection_as_requester(self, keeper_user, duplicate_user, merge_event):
        from crush_lu.models import EventConnection

        third_user = User.objects.create_user(
            username='third@example.com', email='third@example.com', password='pass123'
        )

        EventConnection.objects.create(
            requester=duplicate_user,
            recipient=third_user,
            event=merge_event,
            status='pending',
        )

        merge_accounts(keeper_user, duplicate_user)

        conn = EventConnection.objects.get(recipient=third_user, event=merge_event)
        assert conn.requester == keeper_user

    def test_deletes_self_connection(self, keeper_user, duplicate_user, merge_event):
        from crush_lu.models import EventConnection

        # Duplicate requested connection to keeper - after merge becomes self-connection
        EventConnection.objects.create(
            requester=duplicate_user,
            recipient=keeper_user,
            event=merge_event,
            status='pending',
        )

        merge_accounts(keeper_user, duplicate_user)

        assert not EventConnection.objects.filter(event=merge_event).exists()

    def test_skips_duplicate_connection(self, keeper_user, duplicate_user, merge_event):
        from crush_lu.models import EventConnection

        third_user = User.objects.create_user(
            username='third@example.com', email='third@example.com', password='pass123'
        )

        # Both users have connection to same person on same event
        EventConnection.objects.create(
            requester=keeper_user,
            recipient=third_user,
            event=merge_event,
            status='accepted',
        )
        EventConnection.objects.create(
            requester=duplicate_user,
            recipient=third_user,
            event=merge_event,
            status='pending',
        )

        merge_accounts(keeper_user, duplicate_user)

        conns = EventConnection.objects.filter(recipient=third_user, event=merge_event)
        assert conns.count() == 1
        assert conns.first().requester == keeper_user
        assert conns.first().status == 'accepted'  # Keeper's original status preserved


class TestMergeAtomicity:
    def test_merge_is_atomic(self, keeper_user, duplicate_user):
        """If an error occurs mid-merge, nothing should be committed."""
        from allauth.socialaccount.models import SocialAccount
        from unittest.mock import patch

        SocialAccount.objects.create(
            user=duplicate_user, provider='apple', uid='test-uid', extra_data={}
        )

        with patch(
            'crush_lu.models.ProfileReminder.objects'
        ) as mock_manager:
            mock_manager.filter.side_effect = Exception("Simulated error")

            with pytest.raises(Exception, match="Simulated error"):
                merge_accounts(keeper_user, duplicate_user)

        # Social account should NOT have been moved (rolled back)
        assert SocialAccount.objects.filter(user=duplicate_user, provider='apple').exists()
        # Duplicate user should NOT be deactivated (rolled back)
        duplicate_user.refresh_from_db()
        assert duplicate_user.is_active is True
