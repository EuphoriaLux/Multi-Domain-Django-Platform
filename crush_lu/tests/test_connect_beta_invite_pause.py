"""Regression: the Connect beta invite must respect a member's own pause.

``send_connect_beta_invites`` (Task 13.4, #911) and the member-controlled
Connect pause (#919) shipped alongside each other and were never cross-checked.
They overlap on a real, reachable member state:

  * ``CrushConnectMembership.pause()`` is a self-service snooze that removes a
    member from "new Connect discovery and interaction flows" (model docstring).
    It writes ``paused_at`` only — it never touches the waitlist row.
  * ``candidates_for_wave`` filters solely on ``beta_invited_at__isnull=True``
    and ``notification_preference=True``. It never looks at the membership.

Nothing deletes the ``CrushConnectWaitlist`` row when a member onboards, so an
early adopter who joined the waitlist, onboarded into Connect, then hit pause
is still a live invite candidate. The campaign then mails them "your Connect
Week is open" while their own pause is what is holding it shut.

The precedent for the fix is already in this codebase: ``pre_screening_
notifications`` gates both of its sends on ``submission.is_paused``.
"""

from io import StringIO

import pytest
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command

from crush_lu.management.commands.send_connect_beta_invites import (
    candidates_for_wave,
    paused_candidates_for_wave,
)
from crush_lu.models.crush_connect import (
    CrushConnectMembership,
    CrushConnectWaitlist,
)
from crush_lu.tests.test_crush_connect import _make_user, _mark_attended


@pytest.fixture(autouse=True)
def _clear_invite_lock():
    cache.clear()
    yield
    cache.clear()


def _waitlisted_paused_member(username="paused_member"):
    """A wave-1 (event-verified) waitlist member who has paused Connect."""
    user = _make_user(username=username, premium=False, has_luxid=False)
    _mark_attended(user)
    CrushConnectWaitlist.objects.create(user=user, notification_preference=True)

    membership, _ = CrushConnectMembership.objects.get_or_create(user=user)
    membership.pause()
    assert membership.is_paused, "fixture must actually pause the member"
    return user


@pytest.mark.django_db
def test_paused_member_is_not_an_invite_candidate():
    """A self-paused member must drop out of the cohort entirely."""
    user = _waitlisted_paused_member()

    candidates = candidates_for_wave(1)

    assert user.id not in [row.user_id for row in candidates], (
        "a member who paused Crush Connect themselves is still selected for "
        "the beta invite campaign"
    )


@pytest.mark.django_db
def test_paused_member_is_not_mailed():
    """End-to-end: the command must not send to a paused member."""
    user = _waitlisted_paused_member()
    mail.outbox.clear()

    call_command("send_connect_beta_invites", "--wave", "1")

    recipients = [addr for m in mail.outbox for addr in m.to]
    assert user.email not in recipients, (
        f"paused member {user.email} was mailed a Connect beta invite"
    )


@pytest.mark.django_db
def test_paused_member_row_is_not_consumed():
    """Skipping must not stamp ``beta_invited_at``.

    The row is left un-stamped on purpose so that resuming the membership
    restores the member to a future wave, exactly like the unsubscribe path
    which the command already leaves un-stamped ("an unsubscribe is not an
    invite, and stamping it would consume the member's slot").
    """
    user = _waitlisted_paused_member()

    call_command("send_connect_beta_invites", "--wave", "1")

    row = CrushConnectWaitlist.objects.get(user=user)
    assert row.beta_invited_at is None, (
        "a skipped paused member had their invite slot consumed"
    )


@pytest.mark.django_db
def test_unpaused_member_is_still_invited():
    """Guard against over-correcting: the normal path must keep working."""
    user = _make_user(username="active_member", premium=False, has_luxid=False)
    _mark_attended(user)
    CrushConnectWaitlist.objects.create(user=user, notification_preference=True)

    candidates = candidates_for_wave(1)

    assert user.id in [row.user_id for row in candidates]


@pytest.mark.django_db
def test_resumed_member_becomes_a_candidate_again():
    """Pause is reversible, so exclusion from the cohort must be too."""
    user = _waitlisted_paused_member(username="resumer")
    membership = CrushConnectMembership.objects.get(user=user)
    # reactivate(), not resume(): it also rebuilds the MatchScore cache that
    # pause() dropped, which is why the un-pause is not just a flag flip.
    membership.reactivate()
    assert not membership.is_paused

    candidates = candidates_for_wave(1)

    assert user.id in [row.user_id for row in candidates]


@pytest.mark.django_db
def test_the_skipped_count_is_reported_and_wave_scoped():
    """An operator must be able to see the cohort shrank, and why.

    Wave-scoped on purpose: a flat count of every paused row would report
    wave-2 members while wave 1 is being sent.
    """
    paused = _waitlisted_paused_member(username="paused_w1")

    assert [row.user_id for row in paused_candidates_for_wave(1)] == [paused.id]
    # ...and they are not double-counted into a wave they do not belong to.
    assert paused.id not in [row.user_id for row in paused_candidates_for_wave(2)]

    out = StringIO()
    call_command("send_connect_beta_invites", "--wave", "1", "--dry-run", stdout=out)
    assert "skipped, paused Connect themselves: 1" in out.getvalue()


@pytest.mark.django_db
def test_a_member_who_pauses_mid_run_is_not_mailed():
    """The cohort filter alone leaves a time-of-check/time-of-use hole.

    ``candidates_for_wave`` materialises a list, then the command works through
    up to 200 sends. A member who hits pause while the run is still on earlier
    recipients is already in that list, so the send itself has to re-check.
    """
    from crush_lu.email_helpers import send_connect_beta_invite

    user = _make_user(username="mid_run", premium=False, has_luxid=False)
    _mark_attended(user)
    CrushConnectWaitlist.objects.create(user=user, notification_preference=True)

    # In the cohort at check time...
    assert user.id in [row.user_id for row in candidates_for_wave(1)]

    # ...then they pause while the run is under way.
    membership, _ = CrushConnectMembership.objects.get_or_create(user=user)
    membership.pause()

    mail.outbox.clear()
    delivered = send_connect_beta_invite(user, 1)

    assert delivered == 0, "a member who paused mid-run was still mailed"
    assert user.email not in [addr for m in mail.outbox for addr in m.to]


def _waitlisted_without_membership(username="no_membership"):
    """A waitlist member who never onboarded — the majority of the waitlist.

    ``_make_user`` always creates a CrushConnectMembership, so the row has to
    be removed explicitly to model someone who only ever joined the waitlist.
    """
    user = _make_user(username=username, premium=False, has_luxid=False)
    _mark_attended(user)
    CrushConnectMembership.objects.filter(user=user).delete()
    CrushConnectWaitlist.objects.create(user=user, notification_preference=True)
    assert not CrushConnectMembership.objects.filter(user=user).exists()
    return user


@pytest.mark.django_db
def test_a_member_without_a_membership_row_is_still_a_candidate():
    """Most of the waitlist never onboarded; they must stay in the wave.

    The cohort now reaches across an optional OneToOne, and the risk in doing
    that is excluding everyone who simply has no row on the far side —
    silently emptying the campaign, which is far worse than the bug being
    fixed. As it happens both spellings survive it (``exclude(...isnull=False)``
    and ``filter(...isnull=True)`` both emit a LEFT OUTER JOIN, since Django
    promotes the join for ``__isnull=True``), so this is not guarding one
    spelling against the other — it pins the behaviour itself, for whoever
    edits this filter next.
    """
    user = _waitlisted_without_membership()

    assert user.id in [row.user_id for row in candidates_for_wave(1)]


@pytest.mark.django_db
def test_a_member_without_a_membership_row_is_still_mailed():
    """The same trap at the send-time guard: "no row" must mean "not paused"."""
    from crush_lu.email_helpers import send_connect_beta_invite

    user = _waitlisted_without_membership(username="no_membership_send")

    mail.outbox.clear()
    delivered = send_connect_beta_invite(user, 1)

    assert delivered == 1
    assert user.email in [addr for m in mail.outbox for addr in m.to]
