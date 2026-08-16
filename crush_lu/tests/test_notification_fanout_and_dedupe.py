"""Three notification-layer defects deferred from the PR #854 review.

Each was declined there as systemic rather than local, and each is real:

1. A per-subscription opt-out decided *whether* a member was notified but
   never *where*: the senders pre-filtered with ``.exists()`` and then handed
   ``send_push_notification`` a user, which broadcast to every enabled
   subscription. A member with one muted device got it on the muted one too.
2. The fan-out ran unbounded inside the request. Production has no task worker
   (AGENTS.md, "Background tasks"), so a pile of stale endpoints could eat the
   gunicorn window *after* a door rejection had already committed.
3. The approval dedupe was check-then-create, so two concurrent approval paths
   could both read "not sent yet" and both send.

The dedupe tests here are deliberately about the *race*; the lifecycle
semantics they must not break are pinned separately in
``test_profile_approved_dedupe.py``.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.utils import timezone

from crush_lu.models import CrushProfile, Notification, PushSubscription
from crush_lu.notification_service import (
    NotificationService,
    NotificationType,
    notify_profile_approved,
)
from crush_lu.push_notifications import send_push_notification

User = get_user_model()


@pytest.fixture
def member(db):
    user = User.objects.create_user(
        username="fanout-member", email="fanout@example.com"
    )
    CrushProfile.objects.create(
        user=user,
        date_of_birth=timezone.now().date() - timedelta(days=365 * 30),
        gender="F",
        location="Luxembourg City",
        is_active=True,
    )
    return user


def _subscription(user, index, **flags):
    defaults = {
        "notify_new_messages": True,
        "notify_event_reminders": True,
        "notify_new_connections": True,
        "notify_profile_updates": True,
    }
    defaults.update(flags)
    return PushSubscription.objects.create(
        user=user,
        endpoint=f"https://push.example.com/device-{index}",
        p256dh_key=f"p256dh-{index}",
        auth_key=f"auth-{index}",
        device_name=f"Device {index}",
        enabled=True,
        **defaults,
    )


@pytest.fixture
def vapid_settings():
    """Stand in for the real settings object the sender reads."""
    with patch("crush_lu.push_notifications.settings") as mock_settings:
        mock_settings.VAPID_PRIVATE_KEY = "test-private"
        mock_settings.VAPID_PUBLIC_KEY = "test-public"
        mock_settings.VAPID_ADMIN_EMAIL = "admin@crush.lu"
        mock_settings.CRUSH_PUSH_FANOUT_LIMIT = 100
        mock_settings.CRUSH_PUSH_FANOUT_BUDGET_SECONDS = 3600.0
        mock_settings.CRUSH_PUSH_SEND_TIMEOUT_SECONDS = 10.0
        yield mock_settings


# ---------------------------------------------------------------------------
# 1. Per-subscription opt-outs
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("crush_lu.push_notifications.webpush")
def test_muted_device_is_not_pushed_to(mock_webpush, member, vapid_settings):
    """The opted-out device is skipped while its sibling still receives.

    This is the whole finding: the member has said "not this category, not on
    this device", and before the fix both devices were pushed because the
    preference was only ever consulted as a yes/no for the member.
    """
    _subscription(member, 0, notify_profile_updates=True)
    _subscription(member, 1, notify_profile_updates=False)

    result = send_push_notification(
        member, "Title", "Body", preference_key="profile_updates"
    )

    assert result["success"] == 1
    assert result["total"] == 1
    assert mock_webpush.call_count == 1
    endpoint = mock_webpush.call_args.kwargs["subscription_info"]["endpoint"]
    assert endpoint.endswith("device-0")


@pytest.mark.django_db
@patch("crush_lu.push_notifications.webpush")
def test_no_preference_key_still_broadcasts(mock_webpush, member, vapid_settings):
    """Callers with no per-type preference keep their old reach.

    Campaigns and the "send me a test push" button mean every enabled device
    on purpose, so the filter must stay opt-in rather than become the default.
    """
    _subscription(member, 0, notify_profile_updates=True)
    _subscription(member, 1, notify_profile_updates=False)

    result = send_push_notification(member, "Title", "Body")

    assert result["success"] == 2
    assert mock_webpush.call_count == 2


@pytest.mark.django_db
@patch("crush_lu.push_notifications.webpush")
def test_every_profile_sender_passes_its_preference(
    mock_webpush, member, vapid_settings
):
    """Fixing one sender and not the others is the inconsistency to avoid.

    Each of these pre-filters on notify_profile_updates, so each must also
    scope delivery to the devices that opted in.
    """
    from crush_lu import push_notifications

    _subscription(member, 0, notify_profile_updates=True)
    _subscription(member, 1, notify_profile_updates=False)

    senders = [
        lambda: push_notifications.send_profile_approved_notification(member),
        lambda: push_notifications.send_profile_revision_notification(
            member, "some feedback"
        ),
        lambda: push_notifications.send_profile_rejected_notification(
            member, "a reason"
        ),
        lambda: push_notifications.send_profile_recontact_notification(member),
    ]

    for send in senders:
        mock_webpush.reset_mock()
        result = send()
        assert result["total"] == 1, f"{send} reached the muted device"
        assert mock_webpush.call_count == 1


# ---------------------------------------------------------------------------
# 2. Bounded fan-out
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("crush_lu.push_notifications.webpush")
def test_fanout_stops_at_the_count_limit(mock_webpush, member, vapid_settings):
    """More devices than the cap means the cap wins, and the rest are reported."""
    for i in range(5):
        _subscription(member, i)
    vapid_settings.CRUSH_PUSH_FANOUT_LIMIT = 2

    result = send_push_notification(member, "Title", "Body")

    assert mock_webpush.call_count == 2
    assert result["success"] == 2
    assert result["total"] == 5
    assert result["skipped"] == 3


@pytest.mark.django_db
@patch("crush_lu.push_notifications.webpush")
def test_fanout_stops_when_the_time_budget_is_gone(
    mock_webpush, member, vapid_settings
):
    """The wall-clock budget is the bound that matters.

    The count alone does not bound the work — each send is an HTTP call that
    can hang — so a slow provider has to stop the loop even when the device
    count is well under the limit. Simulated by making each send burn budget.
    """
    for i in range(5):
        _subscription(member, i)
    vapid_settings.CRUSH_PUSH_FANOUT_LIMIT = 100
    vapid_settings.CRUSH_PUSH_FANOUT_BUDGET_SECONDS = 10.0

    clock = {"now": 0.0}

    def slow_send(*args, **kwargs):
        clock["now"] += 4.0  # each push "takes" 4s of the 10s budget

    mock_webpush.side_effect = slow_send

    with patch(
        "crush_lu.push_notifications.time.monotonic", side_effect=lambda: clock["now"]
    ):
        result = send_push_notification(member, "Title", "Body")

    # Deadline is 10.0. Sends land at t=0, 4, 8; the fourth check sees 12.0.
    assert mock_webpush.call_count == 3
    assert result["total"] == 5
    assert result["skipped"] == 2


@pytest.mark.django_db
@patch("crush_lu.push_notifications.webpush")
def test_each_send_carries_a_timeout(mock_webpush, member, vapid_settings):
    """A deadline checked between sends does not bound a send that hangs.

    pywebpush defaults to timeout=None, so without this one unresponsive push
    endpoint holds the request open indefinitely and the loop's budget never
    gets a chance to notice — the exact door-latency failure the bound exists
    to prevent. The per-call timeout is clamped to what is left of the budget.
    """
    _subscription(member, 0)
    vapid_settings.CRUSH_PUSH_SEND_TIMEOUT_SECONDS = 4.0
    vapid_settings.CRUSH_PUSH_FANOUT_BUDGET_SECONDS = 3600.0

    send_push_notification(member, "Title", "Body")

    timeout = mock_webpush.call_args.kwargs.get("timeout")
    assert timeout == 4.0, "webpush must be given an explicit timeout"


@pytest.mark.django_db
@patch("crush_lu.push_notifications.webpush")
def test_send_timeout_never_outlives_the_budget(
    mock_webpush, member, vapid_settings
):
    """The remaining budget caps the per-send timeout, not the other way round."""
    _subscription(member, 0)
    vapid_settings.CRUSH_PUSH_SEND_TIMEOUT_SECONDS = 30.0
    vapid_settings.CRUSH_PUSH_FANOUT_BUDGET_SECONDS = 2.0

    send_push_notification(member, "Title", "Body")

    timeout = mock_webpush.call_args.kwargs.get("timeout")
    assert 0 < timeout <= 2.0


@pytest.mark.django_db
@patch("crush_lu.push_notifications.webpush")
def test_skipped_devices_are_logged_never_silent(
    mock_webpush, member, vapid_settings, caplog
):
    """A cap that truncates silently reads as "everyone was notified"."""
    for i in range(4):
        _subscription(member, i)
    vapid_settings.CRUSH_PUSH_FANOUT_LIMIT = 1

    with caplog.at_level("WARNING", logger="crush_lu.push_notifications"):
        send_push_notification(member, "Title", "Body")

    messages = [r.getMessage() for r in caplog.records]
    assert any("bounded" in m for m in messages), messages
    assert any("3 skipped" in m for m in messages), messages


# ---------------------------------------------------------------------------
# 3. Atomic approval dedupe
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_approval_claims_the_row_before_sending(member):
    """The claim must be written before external channels, not after.

    Check-then-create loses the race because both racers read "nothing" before
    either writes. Asserted structurally: at the moment the email channel is
    invoked, the claim row already exists.
    """
    seen = {}

    def observe(user, notification_type, context, request):
        seen["rows_at_send_time"] = Notification.objects.filter(
            user=user, notification_type=NotificationType.PROFILE_APPROVED.value
        ).count()
        return True

    with patch.object(NotificationService, "_send_email", side_effect=observe):
        notify_profile_approved(user=member, profile=member.crushprofile)

    assert seen["rows_at_send_time"] == 1


@pytest.mark.django_db
def test_concurrent_approval_sends_nothing(member):
    """The loser of the race delivers nothing and says so.

    Simulates the interleaving directly: the first path has already committed
    its claim when the second one tries. Without the unique constraint the
    second create succeeds and the member is notified twice.
    """
    notify_profile_approved(user=member, profile=member.crushprofile)
    assert Notification.objects.filter(
        user=member, notification_type=NotificationType.PROFILE_APPROVED.value
    ).count() == 1

    # A racer that read "not sent yet" before the row above landed: it skips
    # the read-side guard entirely and goes straight for the claim.
    with patch.object(NotificationService, "_send_email") as mock_email:
        result = NotificationService.notify(
            user=member,
            notification_type=NotificationType.PROFILE_APPROVED,
            context={"profile": member.crushprofile},
            dedupe_key="lifecycle:0",
        )

    assert result.deduped is True
    assert result.inapp_created is False
    mock_email.assert_not_called()
    assert Notification.objects.filter(
        user=member, notification_type=NotificationType.PROFILE_APPROVED.value
    ).count() == 1


@pytest.mark.django_db
def test_the_constraint_is_what_enforces_it(member):
    """Pin the database-level guarantee, not just the Python path.

    Unique constraints ARE enforced under SQLite (unlike select_for_update),
    so this is one of the few concurrency guards that is honestly testable in
    CI — worth asserting directly so a dropped migration is loud.
    """
    Notification.objects.create(
        user=member,
        notification_type=NotificationType.PROFILE_APPROVED.value,
        title="first",
        dedupe_key="lifecycle:0",
    )
    with pytest.raises(IntegrityError):
        Notification.objects.create(
            user=member,
            notification_type=NotificationType.PROFILE_APPROVED.value,
            title="second",
            dedupe_key="lifecycle:0",
        )


@pytest.mark.django_db
def test_rows_without_a_key_are_unconstrained(member):
    """Most notification types repeat legitimately and must stay unaffected.

    Every new message writes a row; a constraint on (user, type) would break
    that. Only rows that opt in by setting a key are deduped.
    """
    for _ in range(3):
        Notification.objects.create(
            user=member,
            notification_type=NotificationType.NEW_MESSAGE.value,
            title="a message",
        )

    assert Notification.objects.filter(
        user=member, notification_type=NotificationType.NEW_MESSAGE.value
    ).count() == 3
