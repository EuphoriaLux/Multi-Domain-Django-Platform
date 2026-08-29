"""Regression: the Connect beta invite must respect the coach panic button.

Companion to ``test_connect_beta_invite_pause``. That file closed the half of
the #911 x #919 gap where a member had paused Connect themselves; this file
closes the other half, which shipped unfixed.

``CrushConnectMembership.excluded_by_coach`` is described by the model as a
"Coach panic-button" that "removes a member from every other user's pool and
blocks their Connect surfaces without revoking core profile approval". It is
the moderation control, and the model is explicit that it is kept separate
from ``paused_at`` "so a voluntary break is never represented as a moderation
action".

That separation is exactly why fixing the pause did not fix this: the two
states share no column, so ``exclude(paused_at__isnull=False)`` matches
nothing on an excluded member. ``candidates_for_wave`` filtered on
``beta_invited_at``, ``notification_preference`` and the pause, and never
looked at the exclusion -- so a member a coach had panic-buttoned was still a
live candidate and would be mailed "your Connect Week is open", inviting them
into the surfaces the exclusion exists to shut.

Note that ``is_onboarded`` *does* already check ``excluded_by_coach``; the
invite cohort simply never consulted it, which is what made this easy to miss.
"""

from io import StringIO

import pytest
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.utils import timezone

from crush_lu.management.commands.send_connect_beta_invites import (
    candidates_for_wave,
    coach_excluded_candidates_for_wave,
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


def _waitlisted_excluded_member(username="excluded_member"):
    """A wave-1 (event-verified) waitlist member a coach has excluded."""
    user = _make_user(username=username, premium=False, has_luxid=False)
    _mark_attended(user)
    CrushConnectWaitlist.objects.create(user=user, notification_preference=True)

    membership, _ = CrushConnectMembership.objects.get_or_create(user=user)
    membership.excluded_by_coach = True
    membership.excluded_at = timezone.now()
    membership.exclusion_reason = "fixture: coach panic button"
    membership.save(
        update_fields=["excluded_by_coach", "excluded_at", "exclusion_reason"]
    )
    assert not membership.is_paused, (
        "fixture must isolate exclusion from pause, or this suite would pass "
        "on the pause filter that already exists"
    )
    return user


@pytest.mark.django_db
def test_coach_excluded_member_is_not_an_invite_candidate():
    """A panic-buttoned member must drop out of the cohort entirely."""
    user = _waitlisted_excluded_member()

    candidates = candidates_for_wave(1)

    assert user.id not in [row.user_id for row in candidates], (
        "a member excluded by a coach is still selected for the beta invite "
        "campaign"
    )


@pytest.mark.django_db
def test_coach_excluded_member_is_not_mailed():
    """End-to-end: the command must not send to an excluded member."""
    user = _waitlisted_excluded_member()
    mail.outbox.clear()

    call_command("send_connect_beta_invites", "--wave", "1")

    recipients = [addr for m in mail.outbox for addr in m.to]
    assert user.email not in recipients, (
        f"coach-excluded member {user.email} was mailed a Connect beta invite"
    )


@pytest.mark.django_db
def test_coach_excluded_member_row_is_not_consumed():
    """Skipping must not stamp ``beta_invited_at``.

    Matches the paused and unsubscribed paths: a member who was never invited
    must keep their slot, so lifting the exclusion restores them to a future
    wave rather than silently burning the invite.
    """
    user = _waitlisted_excluded_member()

    call_command("send_connect_beta_invites", "--wave", "1")

    row = CrushConnectWaitlist.objects.get(user=user)
    assert row.beta_invited_at is None, (
        "a skipped coach-excluded member had their invite slot consumed"
    )


@pytest.mark.django_db
def test_member_whose_exclusion_is_lifted_becomes_a_candidate_again():
    """The guard must be reversible, mirroring pause/reactivate."""
    user = _waitlisted_excluded_member()
    assert user.id not in [row.user_id for row in candidates_for_wave(1)]

    membership = CrushConnectMembership.objects.get(user=user)
    membership.excluded_by_coach = False
    membership.excluded_at = None
    membership.save(update_fields=["excluded_by_coach", "excluded_at"])

    assert user.id in [row.user_id for row in candidates_for_wave(1)], (
        "lifting a coach exclusion did not return the member to the cohort"
    )


@pytest.mark.django_db
def test_a_non_excluded_member_is_still_invited():
    """Guard against over-correcting: the normal path must keep working."""
    user = _make_user(username="clear_member", premium=False, has_luxid=False)
    _mark_attended(user)
    CrushConnectWaitlist.objects.create(user=user, notification_preference=True)
    CrushConnectMembership.objects.get_or_create(user=user)

    assert user.id in [row.user_id for row in candidates_for_wave(1)], (
        "the exclusion filter dropped a member who was never excluded"
    )


@pytest.mark.django_db
def test_a_member_without_a_membership_row_is_still_a_candidate():
    """The LEFT JOIN trap, pinned.

    ``exclude(excluded_by_coach=True)`` must not silently drop members who
    never onboarded and therefore have no ``CrushConnectMembership`` row at
    all -- which is most of the waitlist, and the entire point of the campaign.
    The equivalent pause test exists for the same reason; this one guards the
    new filter rather than trusting that it joins the same way.
    """
    user = _make_user(username="no_membership_row", premium=False, has_luxid=False)
    _mark_attended(user)
    # ``_make_user`` always creates a membership, so it has to be removed
    # explicitly to model someone who only ever joined the waitlist.
    CrushConnectMembership.objects.filter(user=user).delete()
    CrushConnectWaitlist.objects.create(user=user, notification_preference=True)
    assert not CrushConnectMembership.objects.filter(user=user).exists()

    assert user.id in [row.user_id for row in candidates_for_wave(1)], (
        "the coach-exclusion filter dropped a member who has no membership "
        "row, which would empty most of the waitlist"
    )


@pytest.mark.django_db
def test_exclusion_and_pause_are_counted_separately():
    """The two skip reasons must not be conflated in the report.

    They are stored separately on purpose, and a coach reading the run output
    needs to see that the panic button held -- not a merged "skipped" total
    that a voluntary pause could account for.
    """
    excluded = _waitlisted_excluded_member(username="counted_excluded")

    paused = _make_user(username="counted_paused", premium=False, has_luxid=False)
    _mark_attended(paused)
    CrushConnectWaitlist.objects.create(user=paused, notification_preference=True)
    paused_membership, _ = CrushConnectMembership.objects.get_or_create(user=paused)
    paused_membership.pause()

    excluded_rows = coach_excluded_candidates_for_wave(1)
    paused_rows = paused_candidates_for_wave(1)

    assert [row.user_id for row in excluded_rows] == [excluded.id]
    assert [row.user_id for row in paused_rows] == [paused.id]


@pytest.mark.django_db
def test_the_excluded_skip_is_reported():
    """A cohort that silently shrank looks identical to one that was small."""
    _waitlisted_excluded_member()
    out = StringIO()

    call_command("send_connect_beta_invites", "--wave", "1", "--dry-run", stdout=out)

    assert "excluded by a coach: 1" in out.getvalue(), (
        "the run did not report that a member was skipped for coach exclusion"
    )
