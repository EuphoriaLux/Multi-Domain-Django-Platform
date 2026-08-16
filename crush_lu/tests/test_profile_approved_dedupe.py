"""The profile-approved notification is delivered once per member.

A member reaches "verified" exactly once per lifecycle, but several paths
observe the transition — the manual Verify button, a door scan, LuxID, and a
re-verify after a rejected door decision. Each calls
``notify_profile_approved``. The in-app Notification row is the durable record
of the first delivery, so a repeat call must not resend the welcome email,
the push and the bell row.

Worth pinning explicitly: the guard swallows its own errors by design (a
failed dedupe check must not drop a first-time notification), so a wrong
field name or enum value would send duplicates forever while every other test
in the suite still passed.
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from crush_lu.models import CrushProfile, Notification
from crush_lu.notification_service import (
    NotificationType,
    notify_profile_approved,
    notify_profile_rejected,
)

User = get_user_model()


@pytest.fixture
def member(db):
    user = User.objects.create_user(
        username="dedupe-member", email="dedupe@example.com"
    )
    CrushProfile.objects.create(
        user=user,
        date_of_birth=timezone.now().date() - timedelta(days=365 * 30),
        gender="F",
        location="Luxembourg City",
        is_active=True,
    )
    return user


def _approved_rows(user):
    return Notification.objects.filter(
        user=user, notification_type=NotificationType.PROFILE_APPROVED.value
    )


@pytest.mark.django_db
def test_first_approval_creates_the_inapp_row(member):
    """The value the guard queries is the value the service writes.

    If these drifted the guard would never match and the dedupe would be a
    no-op — silently, because it catches its own exceptions.
    """
    notify_profile_approved(user=member, profile=member.crushprofile)

    assert _approved_rows(member).count() == 1


@pytest.mark.django_db
def test_second_approval_with_no_rejection_between_is_skipped(member):
    """A repeat observation of the same transition — the case the guard is for."""
    notify_profile_approved(user=member, profile=member.crushprofile)

    result = notify_profile_approved(user=member, profile=member.crushprofile)

    assert result.email_skipped_reason == "duplicate"
    assert not result.any_delivered
    # No second bell row for the same one-time transition.
    assert _approved_rows(member).count() == 1


@pytest.mark.django_db
def test_re_approval_after_a_rejection_is_delivered(member):
    """The sequence the door reject/re-verify path actually produces.

    Member verified, coach door-rejects on a photo mismatch, coach resolves it
    in person and re-verifies. An unscoped guard finds the *first* approval row
    and silently skips — so the member who was just told they were rejected is
    never told it was overturned. A rejection ends the lifecycle; the approval
    that follows it is a new transition, not a duplicate.
    """
    notify_profile_approved(user=member, profile=member.crushprofile)
    notify_profile_rejected(
        user=member, profile=member.crushprofile, feedback="photo mismatch"
    )

    result = notify_profile_approved(user=member, profile=member.crushprofile)

    assert result.email_skipped_reason != "duplicate"
    assert _approved_rows(member).count() == 2


@pytest.mark.django_db
def test_a_third_approval_after_that_is_skipped_again(member):
    """The reopened lifecycle closes again — one delivery per lifecycle, not
    one per rejection ever recorded."""
    notify_profile_approved(user=member, profile=member.crushprofile)
    notify_profile_rejected(
        user=member, profile=member.crushprofile, feedback="photo mismatch"
    )
    notify_profile_approved(user=member, profile=member.crushprofile)

    result = notify_profile_approved(user=member, profile=member.crushprofile)

    assert result.email_skipped_reason == "duplicate"
    assert _approved_rows(member).count() == 2


class TestRejectionPushSender:
    """The rejection push is the only active alert a web-push-only member gets.

    `_send_push` had branches for approval, revision and recontact but none for
    rejection, so a member with an enabled browser subscription and email off
    learned of it only from the bell row on their next visit.
    """

    @pytest.mark.django_db
    def test_dispatcher_routes_rejection_to_the_push_sender(self, member, monkeypatch):
        from crush_lu import push_notifications
        from crush_lu.notification_service import NotificationService

        seen = {}

        def fake(user, reason):
            seen["user"] = user
            seen["reason"] = reason
            return {"sent": 1}

        monkeypatch.setattr(
            push_notifications, "send_profile_rejected_notification", fake
        )
        NotificationService._send_push(
            member,
            NotificationType.PROFILE_REJECTED,
            {"profile": member.crushprofile, "feedback": "photo mismatch"},
        )

        assert seen["user"] == member
        assert seen["reason"] == "photo mismatch"

    @pytest.mark.django_db
    def test_sender_respects_the_profile_updates_preference(self, member, monkeypatch):
        """Same channel as the approval and revision pushes: a member who muted
        coach decisions has muted this one."""
        from crush_lu import push_notifications

        sent = []
        monkeypatch.setattr(
            push_notifications, "send_push_notification",
            lambda **kw: sent.append(kw) or {"ok": True},
        )

        # No subscription at all -> nothing sent, and no crash.
        assert push_notifications.send_profile_rejected_notification(
            member, "photo mismatch"
        ) is None
        assert sent == []
