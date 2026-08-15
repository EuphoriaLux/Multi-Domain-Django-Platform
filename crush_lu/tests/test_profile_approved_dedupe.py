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
def test_second_approval_is_skipped(member):
    notify_profile_approved(user=member, profile=member.crushprofile)

    result = notify_profile_approved(user=member, profile=member.crushprofile)

    assert result.email_skipped_reason == "duplicate"
    assert not result.any_delivered
    # No second bell row for the same one-time transition.
    assert _approved_rows(member).count() == 1
