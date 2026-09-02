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
        username="keeper@example.com",
        email="keeper@example.com",
        password="testpass123",
        first_name="Keeper",
        last_name="User",
    )


@pytest.fixture
def duplicate_user(db):
    return User.objects.create_user(
        username="duplicate@privaterelay.appleid.com",
        email="duplicate@privaterelay.appleid.com",
        password="testpass123",
        first_name="Duplicate",
        last_name="User",
    )


@pytest.fixture
def merge_event(db):
    """Create a sample event for merge tests (avoids coach_user fixture issues)."""
    from crush_lu.models import MeetupEvent

    return MeetupEvent.objects.create(
        title="Merge Test Event",
        description="Event for merge testing",
        event_type="speed_dating",
        date_time=timezone.now() + timedelta(days=7),
        location="Test Location",
        address="123 Test Street",
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
        gender="F",
        location="Luxembourg City",
        bio="Keeper bio",
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
        gender="F",
        location="Esch-sur-Alzette",
        bio="Duplicate bio",
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
            provider="apple",
            uid="apple-uid-123",
            extra_data={"email": "relay@privaterelay.appleid.com"},
        )

        merge_accounts(keeper_user, duplicate_user)

        assert SocialAccount.objects.filter(user=keeper_user, provider="apple").exists()
        assert not SocialAccount.objects.filter(user=duplicate_user).exists()

    def test_moves_different_provider(self, keeper_user, duplicate_user):
        from allauth.socialaccount.models import SocialAccount

        SocialAccount.objects.create(
            user=keeper_user, provider="google", uid="google-uid-1", extra_data={}
        )
        SocialAccount.objects.create(
            user=duplicate_user, provider="apple", uid="apple-uid-1", extra_data={}
        )

        merge_accounts(keeper_user, duplicate_user)

        assert SocialAccount.objects.filter(user=keeper_user).count() == 2
        assert not SocialAccount.objects.filter(user=duplicate_user).exists()


class TestMergeEmailAddresses:
    def test_moves_email_address(self, keeper_user, duplicate_user):
        from allauth.account.models import EmailAddress

        EmailAddress.objects.create(
            user=duplicate_user, email="relay@privaterelay.appleid.com", verified=True
        )

        merge_accounts(keeper_user, duplicate_user)

        ea = EmailAddress.objects.get(email="relay@privaterelay.appleid.com")
        assert ea.user == keeper_user
        assert ea.primary is False

    def test_skips_duplicate_email(self, keeper_user, duplicate_user):
        from allauth.account.models import EmailAddress

        EmailAddress.objects.create(
            user=keeper_user, email="shared@example.com", verified=True, primary=True
        )
        EmailAddress.objects.create(
            user=duplicate_user, email="shared@example.com", verified=False
        )

        merge_accounts(keeper_user, duplicate_user)

        assert EmailAddress.objects.filter(email="shared@example.com").count() == 1
        assert EmailAddress.objects.get(email="shared@example.com").user == keeper_user


class TestMergeProfiles:
    def test_moves_profile_if_keeper_has_none(
        self, keeper_user, duplicate_with_profile
    ):
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
        assert keeper_profile.bio == "Keeper bio"
        assert not CrushProfile.objects.filter(user=dup_user).exists()

    def test_supporter_status_survives_the_merge(
        self, keeper_with_profile, duplicate_with_profile
    ):
        """It was paid for, so losing it to a merge would be taking it back.

        The duplicate's profile is the one deleted, so a donation that happened
        to land on that account would otherwise vanish while the member can
        still see the badge they bought.
        """
        keeper_user, keeper_profile = keeper_with_profile
        dup_user, dup_profile = duplicate_with_profile

        dup_profile.is_community_supporter = True
        dup_profile.save(update_fields=["is_community_supporter"])
        assert not keeper_profile.is_community_supporter

        merge_accounts(keeper_user, dup_user)

        keeper_profile.refresh_from_db()
        assert keeper_profile.is_community_supporter

    def test_pending_donation_follows_the_keeper(
        self, keeper_with_profile, duplicate_with_profile
    ):
        """A donation names nobody but tx.user, so the merge must move it.

        Left on the duplicate, a checkout paid during or after the merge looks
        up a profile that has just been deleted -- money taken, no badge -- and
        the keeper cannot open the widget for a payment they made.
        """
        from decimal import Decimal

        from crush_lu.models.payments import PaymentTransaction
        from crush_lu.views_payments import _apply_paid_checkout

        keeper_user, keeper_profile = keeper_with_profile
        dup_user, _dup_profile = duplicate_with_profile

        tx = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-DON-MERGE",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_DON_MERGE",
            amount=Decimal("10.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.DONATION,
            user=dup_user,
        )

        merge_accounts(keeper_user, dup_user)

        tx.refresh_from_db()
        assert tx.user == keeper_user

        # And the badge it was paying for still lands, on the account that lives.
        _apply_paid_checkout(tx, {"status": "PAID"})
        keeper_profile.refresh_from_db()
        assert keeper_profile.is_community_supporter

    def test_supporter_status_is_re_read_not_trusted_from_memory(
        self, keeper_with_profile, duplicate_with_profile
    ):
        """The badge may be granted after the caller loaded the profile.

        merge_accounts reads `user.crushprofile`, a cached reverse relation
        that can be arbitrarily old. A donation confirming between that load
        and the transfer would leave the in-memory flag False while the row
        says True -- and the profile is deleted immediately after, with nothing
        to recover it (_apply_paid_checkout will not re-apply a PAID checkout).
        """
        from crush_lu.models import CrushProfile

        keeper_user, keeper_profile = keeper_with_profile
        dup_user, dup_profile = duplicate_with_profile

        # Grant it straight to the row, leaving the cached instance stale --
        # exactly the shape of the race.
        CrushProfile.objects.filter(pk=dup_profile.pk).update(
            is_community_supporter=True
        )
        assert dup_profile.is_community_supporter is False  # still stale in memory

        merge_accounts(keeper_user, dup_user)

        keeper_profile.refresh_from_db()
        assert keeper_profile.is_community_supporter

    def test_event_payments_are_not_moved_to_the_keeper(
        self, keeper_with_profile, duplicate_with_profile
    ):
        """Only donations follow the account; seat payments must stay put.

        A duplicate registration for an event the keeper is already in gets
        deleted below, which SET_NULLs its payment's event_registration. Moved
        to the keeper, that becomes a pending checkout they own and can open
        but which can no longer confirm a seat -- it would take the money and
        grant nothing. Left behind it is unreachable rather than payable.
        """
        from decimal import Decimal

        from crush_lu.models.payments import PaymentTransaction

        keeper_user, _keeper_profile = keeper_with_profile
        dup_user, _dup_profile = duplicate_with_profile

        event_tx = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-MERGE",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_EVT_MERGE",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=dup_user,
        )

        merge_accounts(keeper_user, dup_user)

        event_tx.refresh_from_db()
        assert event_tx.user == dup_user

    def test_merge_does_not_invent_supporter_status(
        self, keeper_with_profile, duplicate_with_profile
    ):
        """OR, not "set" — neither side supporting must stay not supporting."""
        keeper_user, keeper_profile = keeper_with_profile
        dup_user, _dup_profile = duplicate_with_profile

        merge_accounts(keeper_user, dup_user)

        keeper_profile.refresh_from_db()
        assert not keeper_profile.is_community_supporter


class TestMergeEventRegistrations:
    def test_moves_registration(self, keeper_user, duplicate_user, merge_event):
        from crush_lu.models import EventRegistration

        EventRegistration.objects.create(
            event=merge_event, user=duplicate_user, status="confirmed"
        )

        merge_accounts(keeper_user, duplicate_user)

        assert EventRegistration.objects.filter(
            event=merge_event, user=keeper_user
        ).exists()

    def test_skips_duplicate_registration(
        self, keeper_user, duplicate_user, merge_event
    ):
        from crush_lu.models import EventRegistration

        EventRegistration.objects.create(
            event=merge_event, user=keeper_user, status="confirmed"
        )
        EventRegistration.objects.create(
            event=merge_event, user=duplicate_user, status="pending"
        )

        merge_accounts(keeper_user, duplicate_user)

        assert EventRegistration.objects.filter(user=keeper_user).count() == 1
        assert not EventRegistration.objects.filter(user=duplicate_user).exists()

    def test_deleted_historical_duplicate_keeps_returned_payment_event_revenue(
        self, keeper_user, duplicate_user, merge_event
    ):
        from decimal import Decimal

        from django.contrib.admin.sites import AdminSite

        from crush_lu.admin.events import MeetupEventAdmin
        from crush_lu.models import EventRegistration, PaymentTransaction

        EventRegistration.objects.create(
            event=merge_event, user=keeper_user, status="confirmed"
        )
        duplicate_registration = EventRegistration.objects.create(
            event=merge_event,
            user=duplicate_user,
            status="cancelled",
            payment_confirmed=False,
        )
        payment = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-MERGE-REVENUE",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK-MERGE-REVENUE",
            amount=Decimal("10.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=duplicate_user,
            event_registration=duplicate_registration,
        )
        assert payment.event == merge_event

        merge_accounts(keeper_user, duplicate_user)

        payment.refresh_from_db()
        assert payment.event_registration is None
        assert payment.event == merge_event
        admin_obj = MeetupEventAdmin(type(merge_event), AdminSite())
        assert admin_obj.get_revenue(merge_event) == "€10.00 (1 paid)"

    def test_merge_refuses_to_delete_duplicate_with_live_paid_seat_value(
        self, keeper_user, duplicate_user, merge_event
    ):
        from decimal import Decimal

        from crush_lu.models import EventRegistration, PaymentTransaction

        keeper_registration = EventRegistration.objects.create(
            event=merge_event, user=keeper_user, status="applied"
        )
        duplicate_registration = EventRegistration.objects.create(
            event=merge_event,
            user=duplicate_user,
            status="confirmed",
            payment_confirmed=True,
            payment_date=timezone.now(),
        )
        payment = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-MERGE-LIVE-VALUE",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK-MERGE-LIVE-VALUE",
            amount=Decimal("10.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=duplicate_user,
            event_registration=duplicate_registration,
        )

        with pytest.raises(ValueError, match="still holds paid seat value"):
            merge_accounts(keeper_user, duplicate_user)

        duplicate_user.refresh_from_db()
        payment.refresh_from_db()
        assert duplicate_user.is_active
        assert payment.event_registration_id == duplicate_registration.pk
        assert EventRegistration.objects.filter(pk=keeper_registration.pk).exists()
        assert EventRegistration.objects.filter(pk=duplicate_registration.pk).exists()


class TestMergeConnections:
    def test_moves_connection_as_requester(
        self, keeper_user, duplicate_user, merge_event
    ):
        from crush_lu.models import EventConnection

        third_user = User.objects.create_user(
            username="third@example.com", email="third@example.com", password="pass123"
        )

        EventConnection.objects.create(
            requester=duplicate_user,
            recipient=third_user,
            event=merge_event,
            status="pending",
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
            status="pending",
        )

        merge_accounts(keeper_user, duplicate_user)

        assert not EventConnection.objects.filter(event=merge_event).exists()

    def test_skips_duplicate_connection(self, keeper_user, duplicate_user, merge_event):
        from crush_lu.models import EventConnection

        third_user = User.objects.create_user(
            username="third@example.com", email="third@example.com", password="pass123"
        )

        # Both users have connection to same person on same event
        EventConnection.objects.create(
            requester=keeper_user,
            recipient=third_user,
            event=merge_event,
            status="accepted",
        )
        EventConnection.objects.create(
            requester=duplicate_user,
            recipient=third_user,
            event=merge_event,
            status="pending",
        )

        merge_accounts(keeper_user, duplicate_user)

        conns = EventConnection.objects.filter(recipient=third_user, event=merge_event)
        assert conns.count() == 1
        assert conns.first().requester == keeper_user
        assert conns.first().status == "accepted"  # Keeper's original status preserved


class TestMergeAtomicity:
    def test_merge_is_atomic(self, keeper_user, duplicate_user):
        """If an error occurs mid-merge, nothing should be committed."""
        from allauth.socialaccount.models import SocialAccount
        from unittest.mock import patch

        SocialAccount.objects.create(
            user=duplicate_user, provider="apple", uid="test-uid", extra_data={}
        )

        with patch("crush_lu.models.ProfileReminder.objects") as mock_manager:
            mock_manager.filter.side_effect = Exception("Simulated error")

            with pytest.raises(Exception, match="Simulated error"):
                merge_accounts(keeper_user, duplicate_user)

        # Social account should NOT have been moved (rolled back)
        assert SocialAccount.objects.filter(
            user=duplicate_user, provider="apple"
        ).exists()
        # Duplicate user should NOT be deactivated (rolled back)
        duplicate_user.refresh_from_db()
        assert duplicate_user.is_active is True


class TestLockOrderInvariant:
    """The one thing about this merge that no runtime test can check.

    ``merge_accounts`` and ``views_payments._apply_paid_checkout`` both touch a
    PaymentTransaction row and a CrushProfile row under a lock. If they take
    those two in opposite orders, a donation confirming while an admin merges
    the donor's account is an ABBA deadlock, and PostgreSQL breaks it by
    aborting one of the transactions -- possibly the payment callback, halfway
    through confirming a real charge.

    This cannot be caught by exercising the code: the test suite (and CI) run
    on SQLite, which ignores ``select_for_update`` entirely, so a reversed
    order passes every functional test and only fails in production. Hence a
    structural assertion on the source itself.
    """

    def test_merge_takes_the_payment_lock_before_the_profile_lock(self):
        import inspect

        from crush_lu.services import account_merge

        src = inspect.getsource(account_merge.merge_accounts)
        payment = src.index("PaymentTransaction.objects.filter")
        profile = src.index("CrushProfile.objects.select_for_update")
        assert payment < profile, (
            "merge_accounts must reassign donation PaymentTransactions BEFORE "
            "locking CrushProfiles — see the LOCK ORDER note in that function."
        )

    def test_payment_confirmation_takes_them_in_the_same_order(self):
        import inspect

        from crush_lu import views_payments

        src = inspect.getsource(views_payments._apply_paid_checkout)
        payment = src.index("PaymentTransaction.objects.select_for_update")
        profile = src.index("CrushProfile.objects.select_for_update")
        assert payment < profile, (
            "_apply_paid_checkout must lock the PaymentTransaction BEFORE the "
            "CrushProfile — reversing it deadlocks against merge_accounts."
        )
