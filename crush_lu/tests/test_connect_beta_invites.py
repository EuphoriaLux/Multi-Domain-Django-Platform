"""
Tests for ``send_connect_beta_invites`` (Epic 13 / Task 13.4) — the hand-run
launch campaign that mails the Crush Connect waitlist in three waves.

Two properties matter more than the copy, and both have a test here that fails
loudly if they regress:

1. **An invite grants nothing.** ``selected_as_tester`` is the flag that opens
   Today's Drop and the Premium purchase funnel. Mailing a 295-person waitlist
   must never set it — see ``test_invite_grants_no_entitlement``.
2. **Nobody is mailed twice.** ``beta_invited_at`` is stamped per member right
   after their own send, so a re-run, a crash mid-wave, or a partial failure
   all converge on exactly one email per member.

Reuses the Connect suite's fixtures (see ``test_crush_connect``'s docstring for
the cross-import convention).
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command

from crush_lu.email_helpers import (
    CONNECT_BETA_WAVE_CONNECT_WEEK,
    CONNECT_BETA_WAVE_IN_THE_MIX,
    CONNECT_BETA_WAVE_UNVERIFIED,
)
from crush_lu.management.commands.send_connect_beta_invites import (
    candidates_for_wave,
    wave_for_user,
)
from crush_lu.models.crush_connect import CrushConnectWaitlist
from crush_lu.tests.test_crush_connect import _make_user, _mark_attended


@pytest.fixture(autouse=True)
def _clear_invite_lock():
    """The command takes a cross-process cache lock. SQLite rolls the user PK
    sequence back between tests but NOT the cache, so a lock left by an earlier
    test would make the next command run silently skip (see the
    sqlite-pk-reset-leaks-cache-state memory). Clear it around every test."""
    cache.clear()
    yield
    cache.clear()


def _waitlist(user, **kwargs):
    return CrushConnectWaitlist.objects.create(user=user, **kwargs)


def _event_verified(username):
    """Event-verified, non-Premium, no LuxID → wave 1."""
    user = _make_user(username=username, premium=False, has_luxid=False)
    _mark_attended(user)
    return user


def _luxid_only(username):
    """LuxID-linked, never attended an event → wave 2."""
    return _make_user(username=username, premium=False, has_luxid=True)


def _unverified(username):
    """Neither route → wave 3."""
    return _make_user(username=username, premium=False, has_luxid=False)


# ---------------------------------------------------------------------------
# Wave segmentation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_event_verified_member_is_wave_one():
    assert wave_for_user(_event_verified("w1")) == CONNECT_BETA_WAVE_CONNECT_WEEK


@pytest.mark.django_db
def test_luxid_only_member_is_wave_two():
    assert wave_for_user(_luxid_only("w2")) == CONNECT_BETA_WAVE_IN_THE_MIX


@pytest.mark.django_db
def test_unverified_member_is_wave_three():
    assert wave_for_user(_unverified("w3")) == CONNECT_BETA_WAVE_UNVERIFIED


@pytest.mark.django_db
def test_dual_verified_member_is_wave_one_not_two():
    """The audit counts dual-verified inside wave 1 (41 event + 42 dual = 83).
    Event verification is the larger capability, so it wins the tie — testing
    LuxID first would silently move 42 members into the smaller offer."""
    user = _luxid_only("dual")
    _mark_attended(user)
    assert user.crushprofile.has_luxid_connected
    assert wave_for_user(user) == CONNECT_BETA_WAVE_CONNECT_WEEK


@pytest.mark.django_db
def test_unapproved_profile_with_attendance_falls_to_wave_three():
    """``has_attended_event`` requires ``verification_status == "verified"``, so
    an attended seat on an unapproved profile is not event verification — the
    coach never completed it. That member belongs in the "get verified" wave,
    not in the one told their Connect Week is open."""
    user = _make_user(
        username="attended_unapproved",
        premium=False,
        has_luxid=False,
        is_approved=False,
    )
    _mark_attended(user)
    assert wave_for_user(user) == CONNECT_BETA_WAVE_UNVERIFIED


@pytest.mark.django_db
def test_user_without_profile_is_wave_three():
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user(
        username="noprofile", email="noprofile@example.com", password="x"
    )
    assert wave_for_user(user) == CONNECT_BETA_WAVE_UNVERIFIED


# ---------------------------------------------------------------------------
# Cohort selection
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_cohort_contains_only_its_own_wave():
    _waitlist(_event_verified("c_w1"))
    _waitlist(_luxid_only("c_w2"))
    _waitlist(_unverified("c_w3"))

    for wave in (
        CONNECT_BETA_WAVE_CONNECT_WEEK,
        CONNECT_BETA_WAVE_IN_THE_MIX,
        CONNECT_BETA_WAVE_UNVERIFIED,
    ):
        rows = candidates_for_wave(wave)
        assert len(rows) == 1
        assert wave_for_user(rows[0].user) == wave


@pytest.mark.django_db
def test_cohort_respects_notification_preference():
    """``notification_preference`` is the member's own "tell me when Connect
    launches" tick. Ignoring it would mail people who opted out."""
    _waitlist(_event_verified("optout"), notification_preference=False)
    assert candidates_for_wave(CONNECT_BETA_WAVE_CONNECT_WEEK) == []


@pytest.mark.django_db
def test_cohort_excludes_already_invited():
    from django.utils import timezone

    _waitlist(
        _event_verified("already"),
        beta_invited_at=timezone.now(),
        beta_invite_wave=CONNECT_BETA_WAVE_CONNECT_WEEK,
    )
    assert candidates_for_wave(CONNECT_BETA_WAVE_CONNECT_WEEK) == []


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_dry_run_sends_nothing_and_stamps_nothing():
    row = _waitlist(_event_verified("dry"))
    mail.outbox = []

    call_command("send_connect_beta_invites", wave=1, dry_run=True)

    assert mail.outbox == []
    row.refresh_from_db()
    assert row.beta_invited_at is None
    assert row.beta_invite_wave is None


@pytest.mark.django_db
def test_send_delivers_one_email_and_stamps_the_row():
    row = _waitlist(_event_verified("send1"))
    mail.outbox = []

    call_command("send_connect_beta_invites", wave=1)

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [row.user.email]
    row.refresh_from_db()
    assert row.beta_invited_at is not None
    assert row.beta_invite_wave == CONNECT_BETA_WAVE_CONNECT_WEEK


@pytest.mark.django_db
def test_rerunning_a_wave_mails_nobody_twice():
    _waitlist(_event_verified("twice"))

    mail.outbox = []
    call_command("send_connect_beta_invites", wave=1)
    assert len(mail.outbox) == 1

    mail.outbox = []
    call_command("send_connect_beta_invites", wave=1)
    assert mail.outbox == []


@pytest.mark.django_db
def test_limit_caps_the_run_and_leaves_the_rest_for_the_next():
    for i in range(3):
        _waitlist(_event_verified(f"cap{i}"))

    mail.outbox = []
    call_command("send_connect_beta_invites", wave=1, limit=2)
    assert len(mail.outbox) == 2
    assert len(candidates_for_wave(CONNECT_BETA_WAVE_CONNECT_WEEK)) == 1

    mail.outbox = []
    call_command("send_connect_beta_invites", wave=1)
    assert len(mail.outbox) == 1
    assert candidates_for_wave(CONNECT_BETA_WAVE_CONNECT_WEEK) == []


@pytest.mark.django_db
def test_one_wave_never_mails_another_waves_members():
    w1 = _waitlist(_event_verified("only_w1"))
    w2 = _waitlist(_luxid_only("only_w2"))

    mail.outbox = []
    call_command("send_connect_beta_invites", wave=2)

    assert [m.to for m in mail.outbox] == [[w2.user.email]]
    w1.refresh_from_db()
    assert w1.beta_invited_at is None


@pytest.mark.django_db
def test_invite_grants_no_entitlement():
    """The beta-policy leak this command must never introduce.

    ``selected_as_tester`` opens Today's Drop AND lets a member past
    ``PREMIUM_REDIRECTS_TO_BETA`` to buy Premium. An invite is communication,
    not permission: every recipient reaches only the surfaces their existing
    verification already opens.
    """
    row = _waitlist(_event_verified("nogrant"))
    assert not row.selected_as_tester

    call_command("send_connect_beta_invites", wave=1)

    row.refresh_from_db()
    assert row.beta_invited_at is not None
    assert not row.selected_as_tester
    assert row.selected_at is None
    assert not row.payment_confirmed


@pytest.mark.django_db
def test_a_failing_send_does_not_abandon_the_wave_or_stamp_the_row():
    """No batch-wide transaction: one bad address must cost exactly one
    member, and that member must stay un-stamped so the next run retries."""
    bad = _waitlist(_event_verified("bad_addr"))
    good = _waitlist(_event_verified("good_addr"))

    def _fail_for_bad(user, wave, request=None):
        if user.pk == bad.user.pk:
            raise RuntimeError("SMTP exploded")
        return 1

    with patch(
        "crush_lu.management.commands.send_connect_beta_invites."
        "send_connect_beta_invite",
        side_effect=_fail_for_bad,
    ):
        call_command("send_connect_beta_invites", wave=1)

    bad.refresh_from_db()
    good.refresh_from_db()
    assert bad.beta_invited_at is None
    assert good.beta_invited_at is not None


@pytest.mark.django_db
def test_a_zero_delivery_leaves_the_row_unstamped():
    """An unsubscribed member returns 0 from the helper. Stamping that would
    consume their slot without ever having told them anything."""
    row = _waitlist(_event_verified("unsub"))

    with patch(
        "crush_lu.management.commands.send_connect_beta_invites."
        "send_connect_beta_invite",
        return_value=0,
    ):
        call_command("send_connect_beta_invites", wave=1)

    row.refresh_from_db()
    assert row.beta_invited_at is None


@pytest.mark.django_db
def test_master_unsubscribe_stops_the_invite():
    from crush_lu.models import EmailPreference

    row = _waitlist(_event_verified("nomail"))
    prefs = EmailPreference.get_or_create_for_user(row.user)
    prefs.unsubscribed_all = True
    prefs.save()

    mail.outbox = []
    call_command("send_connect_beta_invites", wave=1)

    assert mail.outbox == []
    row.refresh_from_db()
    assert row.beta_invited_at is None


@pytest.mark.django_db
def test_marketing_off_does_not_suppress_the_invite():
    """``email_marketing`` defaults to False for GDPR and almost nobody turns
    it on. Routing the invite through that flag would deliver ~zero emails to
    a waitlist that individually asked to be told — the per-row
    ``notification_preference`` is the consent that governs here."""
    from crush_lu.models import EmailPreference

    row = _waitlist(_event_verified("mktoff"))
    prefs = EmailPreference.get_or_create_for_user(row.user)
    assert not prefs.email_marketing  # the default this test exists to pin

    mail.outbox = []
    call_command("send_connect_beta_invites", wave=1)

    assert len(mail.outbox) == 1
    row.refresh_from_db()
    assert row.beta_invited_at is not None


@pytest.mark.django_db
def test_wave_body_matches_the_cohort():
    """Each wave must state what that cohort can actually do — wave 2 members
    cannot browse the Week, so their mail must not invite them to."""
    _waitlist(_event_verified("body_w1"))
    _waitlist(_luxid_only("body_w2"))

    mail.outbox = []
    call_command("send_connect_beta_invites", wave=1)
    w1_body = mail.outbox[0].body
    assert "Connect Week" in w1_body

    mail.outbox = []
    call_command("send_connect_beta_invites", wave=2)
    w2_body = mail.outbox[0].body
    assert "in the Mix" in w2_body
    assert "Start my Connect Week" not in w2_body


@pytest.mark.django_db
def test_a_second_run_is_skipped_while_the_lock_is_held():
    """Two overlapping runs must not both select the same un-stamped member."""
    _waitlist(_event_verified("locked"))
    cache.add("connect_beta_invite_sweep_lock", "1", 900)

    mail.outbox = []
    call_command("send_connect_beta_invites", wave=1)

    assert mail.outbox == []
