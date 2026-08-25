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
