"""
Tests for peer safety — UserBlock / UserReport (review finding C2).

Covers the model constraints, the symmetric block helpers, block enforcement in
the eligible pool and Spark gating, the user-facing block/report endpoints, and
the admin "exclude reported user" panic-button action.
"""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import IntegrityError
from django.test import RequestFactory

# View tests hit /en/… URLs which only resolve under urls_crush.
pytestmark = pytest.mark.urls("azureproject.urls_crush")

from crush_lu.models import CrushConnectMembership, UserBlock, UserReport
from crush_lu.services.blocking import (
    block_exists_subquery,
    blocked_user_ids,
    is_blocked_pair,
)
from crush_lu.services.crush_connect import can_send_spark, get_eligible_pool

# Reuse the rich Crush Connect user fixture (verified + LuxID + premium + onboarded).
from crush_lu.tests.test_crush_connect import _make_user

User = get_user_model()


def _grant_consent(user):
    """Satisfy CrushConsentMiddleware so authenticated view requests reach the view."""
    from crush_lu.models import UserDataConsent

    consent, _ = UserDataConsent.objects.get_or_create(user=user)
    consent.crushlu_consent_given = True
    consent.save(update_fields=["crushlu_consent_given"])
    return user


# ---------------------------------------------------------------------------
# Model constraints
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_block_unique_pair():
    a = _make_user(username="a")
    b = _make_user(username="b")
    UserBlock.objects.create(blocker=a, blocked=b)
    with pytest.raises(IntegrityError):
        UserBlock.objects.create(blocker=a, blocked=b)


@pytest.mark.django_db
def test_block_no_self():
    a = _make_user(username="a")
    with pytest.raises(IntegrityError):
        UserBlock.objects.create(blocker=a, blocked=a)


# ---------------------------------------------------------------------------
# Symmetric helpers
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_is_blocked_pair_is_symmetric():
    a = _make_user(username="a")
    b = _make_user(username="b")
    UserBlock.objects.create(blocker=a, blocked=b)
    assert is_blocked_pair(a, b)
    assert is_blocked_pair(b, a)


@pytest.mark.django_db
def test_blocked_user_ids_covers_both_directions():
    a = _make_user(username="a")
    b = _make_user(username="b")
    c = _make_user(username="c")
    UserBlock.objects.create(blocker=a, blocked=b)  # a blocked b
    UserBlock.objects.create(blocker=c, blocked=a)  # c blocked a
    assert blocked_user_ids(a) == {b.id, c.id}


# ---------------------------------------------------------------------------
# Pool enforcement
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_pool_excludes_blocked_target_either_direction():
    me = _make_user(username="me", preferred_genders=["F", "M"])
    other = _make_user(username="other")
    assert other in get_eligible_pool(me)

    # I blocked them.
    block = UserBlock.objects.create(blocker=me, blocked=other)
    assert other not in get_eligible_pool(me)
    assert me not in get_eligible_pool(other)

    # They blocked me (reverse direction) — still hidden both ways.
    block.delete()
    UserBlock.objects.create(blocker=other, blocked=me)
    assert other not in get_eligible_pool(me)
    assert me not in get_eligible_pool(other)


@pytest.mark.django_db
def test_block_exists_subquery_annotation():
    me = _make_user(username="me", preferred_genders=["F", "M"])
    other = _make_user(username="other")
    UserBlock.objects.create(blocker=me, blocked=other)
    flagged = (
        User.objects.filter(pk=other.pk)
        .annotate(_b=block_exists_subquery(me))
        .first()
    )
    assert flagged._b is True


# ---------------------------------------------------------------------------
# Spark gating
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_can_send_spark_blocked():
    sender = _make_user(username="sender")  # premium + onboarded → sender-eligible
    recipient = _make_user(username="recipient")
    UserBlock.objects.create(blocker=recipient, blocked=sender)
    allowed, reason = can_send_spark(sender, recipient)
    assert allowed is False
    assert reason == "blocked"


# ---------------------------------------------------------------------------
# User-facing endpoints
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_block_user_endpoint(client):
    me = _make_user(username="me")
    target = _make_user(username="target")
    _grant_consent(me)
    client.force_login(me)
    resp = client.post(f"/en/members/{target.id}/block/", {"reason": "harassment"})
    assert resp.status_code in (302, 303)
    assert UserBlock.objects.filter(blocker=me, blocked=target, reason="harassment").exists()


@pytest.mark.django_db
def test_block_user_endpoint_is_idempotent(client):
    me = _make_user(username="me")
    target = _make_user(username="target")
    _grant_consent(me)
    client.force_login(me)
    client.post(f"/en/members/{target.id}/block/")
    client.post(f"/en/members/{target.id}/block/")
    assert UserBlock.objects.filter(blocker=me, blocked=target).count() == 1


@pytest.mark.django_db
def test_report_user_endpoint_creates_report_and_blocks(client):
    me = _make_user(username="me")
    target = _make_user(username="target")
    _grant_consent(me)
    client.force_login(me)
    resp = client.post(
        f"/en/members/{target.id}/report/",
        {"reason": "harassment", "details": "rude", "also_block": "1"},
    )
    assert resp.status_code in (302, 303)
    report = UserReport.objects.get(reporter=me, reported_user=target)
    assert report.reason == "harassment"
    assert report.status == "open"
    assert UserBlock.objects.filter(blocker=me, blocked=target).exists()


@pytest.mark.django_db
def test_report_from_drop_stores_valid_source(client):
    me = _make_user(username="me")
    target = _make_user(username="target")
    _grant_consent(me)
    client.force_login(me)
    client.post(
        f"/en/members/{target.id}/report/",
        {"reason": "fake_profile", "source": "drop", "source_id": "7"},
    )
    report = UserReport.objects.get(reporter=me, reported_user=target)
    assert report.source == "drop"  # 'drop' is a valid SOURCE_CHOICES entry
    assert report.source_id == 7


@pytest.mark.django_db
def test_report_rejects_unknown_source(client):
    me = _make_user(username="me")
    target = _make_user(username="target")
    _grant_consent(me)
    client.force_login(me)
    client.post(
        f"/en/members/{target.id}/report/",
        {"reason": "spam", "source": "bogus"},
    )
    report = UserReport.objects.get(reporter=me, reported_user=target)
    assert report.source == ""  # unknown source is dropped, not persisted


@pytest.mark.django_db
def test_report_tolerates_malformed_source_id(client):
    """A tampered source_id must not turn reporting into a 500."""
    me = _make_user(username="me")
    target = _make_user(username="target")
    _grant_consent(me)
    client.force_login(me)
    resp = client.post(
        f"/en/members/{target.id}/report/",
        {"reason": "spam", "source": "drop", "source_id": "abc"},
    )
    assert resp.status_code in (302, 303)
    report = UserReport.objects.get(reporter=me, reported_user=target)
    assert report.source_id is None  # malformed value dropped, no crash


@pytest.mark.django_db
def test_report_drops_oversized_source_id(client):
    """An out-of-range source_id is dropped (no PositiveIntegerField overflow)."""
    me = _make_user(username="me")
    target = _make_user(username="target")
    _grant_consent(me)
    client.force_login(me)
    resp = client.post(
        f"/en/members/{target.id}/report/",
        {"reason": "spam", "source": "drop", "source_id": "99999999999999"},
    )
    assert resp.status_code in (302, 303)
    report = UserReport.objects.get(reporter=me, reported_user=target)
    assert report.source_id is None


@pytest.mark.django_db
def test_unblock_user_endpoint(client):
    me = _make_user(username="me")
    target = _make_user(username="target")
    UserBlock.objects.create(blocker=me, blocked=target)
    _grant_consent(me)
    client.force_login(me)
    resp = client.post(f"/en/members/{target.id}/unblock/")
    assert resp.status_code in (302, 303)
    assert not UserBlock.objects.filter(blocker=me, blocked=target).exists()


# ---------------------------------------------------------------------------
# Admin moderation action
# ---------------------------------------------------------------------------

def _request_with_messages(user):
    request = RequestFactory().post("/crush-admin/")
    request.user = user
    setattr(request, "session", {})
    setattr(request, "_messages", FallbackStorage(request))
    return request


@pytest.mark.django_db
def test_admin_exclude_action_flips_panic_button():
    from crush_lu.admin.moderation import UserReportAdmin

    staff = _make_user(username="staff")
    staff.is_staff = True
    staff.save(update_fields=["is_staff"])
    reported = _make_user(username="reported")
    report = UserReport.objects.create(
        reporter=staff, reported_user=reported, reason="harassment"
    )

    admin_obj = UserReportAdmin(UserReport, None)
    request = _request_with_messages(staff)
    admin_obj.exclude_reported_users(request, UserReport.objects.filter(pk=report.pk))

    membership = CrushConnectMembership.objects.get(user=reported)
    assert membership.excluded_by_coach is True
    report.refresh_from_db()
    assert report.status == "actioned"
    assert report.handled_by == staff


# ---------------------------------------------------------------------------
# Block enforcement on already-created records (post-block defense)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_respond_connection_refused_after_block(client):
    """A block placed after a request arrived must stop the accept transition."""
    from datetime import timedelta

    from django.utils import timezone

    from crush_lu.models import EventConnection, EventRegistration, MeetupEvent

    me = _make_user(username="me")
    requester = _make_user(username="requester")
    event = MeetupEvent.objects.create(
        title="Past", description="x", event_type="mixer",
        date_time=timezone.now() - timedelta(days=2), location="Luxembourg",
        address="1 St", max_participants=20,
        registration_deadline=timezone.now() - timedelta(days=4), is_published=True,
    )
    for u in (me, requester):
        EventRegistration.objects.create(event=event, user=u, status="attended")
    conn = EventConnection.objects.create(
        event=event, requester=requester, recipient=me, status="pending"
    )

    UserBlock.objects.create(blocker=me, blocked=requester)
    _grant_consent(me)
    client.force_login(me)
    client.post(f"/en/connections/{conn.id}/accept/")

    conn.refresh_from_db()
    assert conn.status == "pending"  # block stopped the accept


@pytest.mark.django_db
def test_event_attendees_hint_excludes_blocked_requester(client):
    """A blocked requester's pending request must not surface via the hint/count."""
    from datetime import timedelta

    from django.utils import timezone

    from crush_lu.models import EventConnection, EventRegistration, MeetupEvent

    me = _make_user(username="me")
    requester = _make_user(username="requester")
    event = MeetupEvent.objects.create(
        title="Past", description="x", event_type="mixer",
        date_time=timezone.now() - timedelta(hours=2), location="Luxembourg",
        address="1 St", max_participants=20,
        registration_deadline=timezone.now() - timedelta(days=2), is_published=True,
    )
    for u in (me, requester):
        EventRegistration.objects.create(event=event, user=u, status="attended")
    EventConnection.objects.create(
        event=event, requester=requester, recipient=me, status="pending"
    )
    UserBlock.objects.create(blocker=me, blocked=requester)

    _grant_consent(me)
    client.force_login(me)
    resp = client.get(f"/en/events/{event.id}/attendees/")
    assert resp.status_code == 200
    assert resp.context["incoming_pending_count"] == 0
    attendee_users = {a["user"].id for a in resp.context["attendees"]}
    assert requester.id not in attendee_users


@pytest.mark.django_db
def test_block_terminates_active_connection(client):
    """Blocking declines an in-flight connection so the coach queue can't facilitate it."""
    from datetime import timedelta

    from django.utils import timezone

    from crush_lu.models import EventConnection, MeetupEvent

    me = _make_user(username="me")
    other = _make_user(username="other")
    event = MeetupEvent.objects.create(
        title="Past", description="x", event_type="mixer",
        date_time=timezone.now() - timedelta(days=2), location="Luxembourg",
        address="1 St", max_participants=20,
        registration_deadline=timezone.now() - timedelta(days=4), is_published=True,
    )
    conn = EventConnection.objects.create(
        event=event, requester=other, recipient=me, status="accepted"
    )

    _grant_consent(me)
    client.force_login(me)
    client.post(f"/en/members/{other.id}/block/")

    conn.refresh_from_db()
    assert conn.status == "declined"  # no longer in the coach queue
    # The coach queue filters accepted/coach_reviewing — declined falls out.
    assert not EventConnection.objects.filter(
        pk=conn.pk, status__in=["accepted", "coach_reviewing"]
    ).exists()


@pytest.mark.django_db
def test_pending_sparks_count_excludes_blocked_sender():
    from django.utils import timezone

    from crush_lu.models import ConnectDailyDrop, CuriositySpark

    recipient = _make_user(username="recipient")
    blocked = _make_user(username="blocked")
    ok = _make_user(username="ok")
    # A spark needs a drop that surfaced the recipient to the sender.
    for sender in (blocked, ok):
        drop = ConnectDailyDrop.objects.create(
            user=sender, drop_date=timezone.localdate()
        )
        drop.recipients.add(recipient)
        CuriositySpark.objects.create(sender=sender, recipient=recipient, drop=drop)
    UserBlock.objects.create(blocker=recipient, blocked=blocked)

    from crush_lu.services.blocking import blocked_user_ids

    count = (
        CuriositySpark.objects.filter(recipient=recipient, status="pending")
        .exclude(sender_id__in=blocked_user_ids(recipient))
        .count()
    )
    assert count == 1  # only the non-blocked sender's spark is counted


@pytest.mark.django_db
def test_drop_render_excludes_blocked_recipient():
    """A member blocked after the Drop was generated drops off the rendered Drop."""
    from django.utils import timezone

    from crush_lu.models import ConnectDailyDrop

    viewer = _make_user(username="viewer", preferred_genders=["F", "M"])
    shown = _make_user(username="shown")
    blocked = _make_user(username="blocked")

    drop = ConnectDailyDrop.objects.create(user=viewer, drop_date=timezone.localdate())
    drop.recipients.add(shown, blocked)
    UserBlock.objects.create(blocker=viewer, blocked=blocked)

    rendered = list(
        drop.recipients.exclude(id__in=blocked_user_ids(viewer))
    )
    assert shown in rendered
    assert blocked not in rendered
