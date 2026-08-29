"""Regression: the Connect beta invite must not mail deactivated accounts.

Third companion to ``test_connect_beta_invite_pause`` (self-pause, #927) and
``test_connect_beta_invite_coach_exclusion`` (the coach panic button). This one
covers the state that is not a Connect concept at all, which is exactly why the
invite path missed it: the account itself is switched off.

Three separate flags, all invisible to the cohort query:

  * ``user.is_active=False`` — what a ban sets.
  * ``crushprofile.is_active=False`` — what a profile deactivation sets.
  * ``data_consent.crushlu_banned=True`` — what "delete my Crush.lu profile but
    keep my PowerUp account" sets. This one is the nastiest, because it leaves
    the user **active** and merely profile-less, which is a legitimate wave-3
    state, so neither of the other two guards fires.

``can_send_email`` cannot catch either: it consults ``EmailPreference`` and
nothing else, and for ``connect_beta_invite`` there is no preference column at
all, so ``can_send`` returns True for every recipient. The cohort filtered on
``beta_invited_at`` and ``notification_preference``, so a banned member stayed
a live candidate and would be mailed.

This is not a new class of bug in this file. ``send_profile_completion_
reminders`` already filters on ``is_active=True`` and ``crushprofile__is_active
=True`` and says why in a comment: a ban "keeps the CrushProfile but sets
user.is_active False ... can_send_email() only checks EmailPreference, so those
users would otherwise still be emailed. (Codex P1)". The Connect invite simply
never inherited that guard.

The trap in fixing it is the profile join. A waitlist member need not have a
``CrushProfile`` — ``test_user_without_profile_is_wave_three`` pins that they
are wave 3 — so filtering ``crushprofile__is_active=True`` would inner-join
away members who have no profile row at all. The filter is therefore written
as ``exclude(crushprofile__is_active=False)``, and
``test_a_member_without_a_profile_is_still_a_candidate`` holds that line.
"""

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.utils import timezone

from crush_lu.management.commands.send_connect_beta_invites import (
    candidates_for_wave,
    coach_excluded_candidates_for_wave,
    inactive_candidates_for_wave,
    paused_candidates_for_wave,
    wave_for_user,
)
from crush_lu.models import CrushProfile, UserDataConsent
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


def _waitlisted_member(username):
    """A wave-1 (event-verified) waitlist member in good standing."""
    user = _make_user(username=username, premium=False, has_luxid=False)
    _mark_attended(user)
    CrushConnectWaitlist.objects.create(user=user, notification_preference=True)
    return user


def _banned(user):
    user.is_active = False
    user.save(update_fields=["is_active"])
    return user


def _profile_deactivated(user):
    CrushProfile.objects.filter(user=user).update(is_active=False)
    return user


@pytest.mark.django_db
def test_banned_member_is_not_an_invite_candidate():
    user = _banned(_waitlisted_member("banned_member"))

    assert user.id not in [row.user_id for row in candidates_for_wave(1)], (
        "a banned member (user.is_active False) is still selected for the "
        "Connect beta invite campaign"
    )


@pytest.mark.django_db
def test_profile_deactivated_member_is_not_an_invite_candidate():
    user = _profile_deactivated(_waitlisted_member("deactivated_member"))

    assert user.id not in [row.user_id for row in candidates_for_wave(1)], (
        "a member with a deactivated CrushProfile is still selected for the "
        "Connect beta invite campaign"
    )


@pytest.mark.django_db
def test_inactive_reporting_takes_precedence_over_pause_and_coach_exclusion():
    """Each inactive account belongs only to the highest-precedence bucket."""
    banned_and_paused = _banned(_waitlisted_member("banned_and_paused"))
    paused_membership = CrushConnectMembership.objects.get(user=banned_and_paused)
    paused_membership.pause()

    banned_and_excluded = _banned(_waitlisted_member("banned_and_excluded"))
    excluded_membership = CrushConnectMembership.objects.get(user=banned_and_excluded)
    excluded_membership.excluded_by_coach = True
    excluded_membership.excluded_at = timezone.now()
    excluded_membership.save(update_fields=["excluded_by_coach", "excluded_at"])

    _profile_deactivated(_waitlisted_member("profile_deactivated"))
    _crushlu_banned(_waitlisted_member("crushlu_deleted"))

    out = StringIO()
    call_command("send_connect_beta_invites", "--wave", "1", "--dry-run", stdout=out)
    report = out.getvalue()

    assert "skipped, account inactive or deleted: 3" in report
    assert "skipped, paused Connect themselves" not in report
    assert "skipped, excluded by a coach" not in report

    wave_three_out = StringIO()
    call_command(
        "send_connect_beta_invites",
        "--wave",
        "3",
        "--dry-run",
        stdout=wave_three_out,
    )
    assert "skipped, account inactive or deleted: 1" in wave_three_out.getvalue()


@pytest.mark.django_db
def test_reporting_buckets_partition_the_uninvited_consenting_wave():
    eligible = _waitlisted_member("partition_eligible")

    paused = _waitlisted_member("partition_paused")
    CrushConnectMembership.objects.get(user=paused).pause()

    excluded = _waitlisted_member("partition_excluded")
    excluded_membership = CrushConnectMembership.objects.get(user=excluded)
    excluded_membership.excluded_by_coach = True
    excluded_membership.excluded_at = timezone.now()
    excluded_membership.save(update_fields=["excluded_by_coach", "excluded_at"])

    inactive = _banned(_waitlisted_member("partition_inactive"))
    CrushConnectMembership.objects.get(user=inactive).pause()

    bucket_ids = [
        {row.user_id for row in candidates_for_wave(1)},
        {row.user_id for row in paused_candidates_for_wave(1)},
        {row.user_id for row in coach_excluded_candidates_for_wave(1)},
        {row.user_id for row in inactive_candidates_for_wave(1)},
    ]
    all_bucket_ids = set().union(*bucket_ids)
    full_wave_ids = {
        row.user_id
        for row in CrushConnectWaitlist.objects.filter(
            beta_invited_at__isnull=True,
            notification_preference=True,
        ).select_related("user__crushprofile")
        if wave_for_user(row.user) == 1
    }

    assert full_wave_ids == {eligible.id, paused.id, excluded.id, inactive.id}
    assert all_bucket_ids == full_wave_ids
    assert sum(len(ids) for ids in bucket_ids) == len(full_wave_ids)


@pytest.mark.django_db
def test_banned_member_is_not_mailed():
    user = _banned(_waitlisted_member("banned_send"))
    mail.outbox.clear()

    call_command("send_connect_beta_invites", "--wave", "1")

    assert user.email not in [
        addr for m in mail.outbox for addr in m.to
    ], f"banned member {user.email} was mailed a Connect beta invite"


@pytest.mark.django_db
def test_banned_member_row_is_not_consumed():
    """A member who was never mailed must keep their invite slot."""
    user = _banned(_waitlisted_member("banned_slot"))

    call_command("send_connect_beta_invites", "--wave", "1")

    row = CrushConnectWaitlist.objects.get(user=user)
    assert (
        row.beta_invited_at is None
    ), "a skipped banned member had their invite slot consumed"


@pytest.mark.django_db
def test_a_member_banned_mid_run_is_not_mailed():
    """Time-of-check/time-of-use, same as the pause and exclusion guards."""
    from crush_lu.email_helpers import send_connect_beta_invite

    user = _waitlisted_member("banned_mid_run")
    assert user.id in [row.user_id for row in candidates_for_wave(1)]

    _banned(user)
    mail.outbox.clear()
    delivered = send_connect_beta_invite(user, 1)

    assert delivered == 0, "a member banned mid-run was still mailed"
    assert user.email not in [addr for m in mail.outbox for addr in m.to]


@pytest.mark.django_db
def test_a_member_deactivated_mid_run_is_not_mailed():
    from crush_lu.email_helpers import send_connect_beta_invite

    user = _waitlisted_member("deactivated_mid_run")
    assert user.id in [row.user_id for row in candidates_for_wave(1)]

    _profile_deactivated(user)
    mail.outbox.clear()
    delivered = send_connect_beta_invite(user, 1)

    assert delivered == 0, "a member deactivated mid-run was still mailed"
    assert user.email not in [addr for m in mail.outbox for addr in m.to]


@pytest.mark.django_db
def test_an_active_member_is_still_invited():
    """Guard against over-correcting: the normal path must keep working."""
    user = _waitlisted_member("active_and_clear")

    assert user.id in [
        row.user_id for row in candidates_for_wave(1)
    ], "the activity filters dropped a member in good standing"


def _crushlu_banned(user):
    """The state ``delete_crushlu_profile_only`` leaves behind.

    That path is "delete my Crush.lu presence, keep my PowerUp account": it
    deletes the CrushProfile, **keeps the Django user active**, and sets
    ``crushlu_banned=True`` as a permanent bar on re-creating the profile.
    """
    CrushProfile.objects.filter(user=user).delete()
    consent, _ = UserDataConsent.objects.get_or_create(user=user)
    consent.crushlu_consent_given = False
    consent.crushlu_banned = True
    consent.crushlu_ban_reason = "user_deletion"
    consent.save()
    user.refresh_from_db()
    assert user.is_active, (
        "fixture must keep the user active — that is precisely why the "
        "is_active guard does not catch this case"
    )
    return user


@pytest.mark.django_db
def test_a_deleted_crushlu_member_is_not_an_invite_candidate():
    """Deleting your Crush.lu profile must not leave you on the invite list.

    ``CrushConnectWaitlist.user`` is a OneToOne on **User** with
    on_delete=CASCADE, and ``delete_crushlu_profile_only`` retains that user —
    so the waitlist row survives the deletion with ``notification_preference``
    intact. The member is then active and profile-less, which is wave 3, and
    neither the ``is_active`` nor the ``crushprofile.is_active`` guard sees
    anything wrong. Mailing them would be a launch invite to someone who
    deleted their account.
    """
    user = _crushlu_banned(_waitlisted_member("deleted_crushlu"))

    assert user.id not in [row.user_id for row in candidates_for_wave(3)], (
        "a member who deleted their Crush.lu profile (crushlu_banned) is "
        "still selected for the Connect beta invite campaign"
    )


@pytest.mark.django_db
def test_a_deleted_crushlu_member_is_not_mailed():
    user = _crushlu_banned(_waitlisted_member("deleted_crushlu_send"))
    mail.outbox.clear()

    call_command("send_connect_beta_invites", "--wave", "3")

    assert user.email not in [
        addr for m in mail.outbox for addr in m.to
    ], f"banned member {user.email} was mailed a Connect beta invite"


@pytest.mark.django_db
def test_a_member_banned_mid_run_is_not_mailed_either():
    """The ban is re-checked at send time, like every other guard here."""
    from crush_lu.email_helpers import send_connect_beta_invite

    user = _waitlisted_member("banned_flag_mid_run")
    assert user.id in [row.user_id for row in candidates_for_wave(1)]

    _crushlu_banned(user)
    mail.outbox.clear()
    delivered = send_connect_beta_invite(user, 3)

    assert delivered == 0, "a member banned mid-run was still mailed"
    assert user.email not in [addr for m in mail.outbox for addr in m.to]


@pytest.mark.django_db
def test_a_member_without_a_consent_row_is_still_a_candidate():
    """The third join, and the third chance to empty the campaign.

    Not every user has a ``UserDataConsent`` row, so the ban filter is written
    as ``exclude(crushlu_banned=True)`` rather than
    ``filter(crushlu_banned=False)``.
    """
    user = _waitlisted_member("no_consent_row")
    UserDataConsent.objects.filter(user=user).delete()
    assert not UserDataConsent.objects.filter(user=user).exists()

    assert user.id in [
        row.user_id for row in candidates_for_wave(1)
    ], "the ban filter dropped a member who has no UserDataConsent row"


@pytest.mark.django_db
def test_a_member_without_a_profile_is_still_a_candidate():
    """The inner-join trap, pinned.

    A waitlist member with no ``CrushProfile`` row is wave 3, not "excluded" --
    filtering ``crushprofile__is_active=True`` instead of excluding the False
    rows would drop them silently, which is why the filter is written the way
    it is.
    """
    user = get_user_model().objects.create_user(
        username="no_profile_row",
        email="no_profile_row@example.com",
        password="testpass123",
    )
    CrushProfile.objects.filter(user=user).delete()
    CrushConnectWaitlist.objects.create(user=user, notification_preference=True)
    assert not CrushProfile.objects.filter(user=user).exists()

    assert user.id in [row.user_id for row in candidates_for_wave(3)], (
        "the profile-activity filter dropped a member who has no CrushProfile "
        "row, who belongs in wave 3"
    )


@pytest.mark.django_db
def test_a_member_without_profile_or_consent_rows_is_still_a_candidate():
    """Both optional activity relations may be absent on one live member."""
    user = get_user_model().objects.create_user(
        username="no_profile_or_consent_rows",
        email="no_profile_or_consent_rows@example.com",
        password="testpass123",
    )
    CrushProfile.objects.filter(user=user).delete()
    UserDataConsent.objects.filter(user=user).delete()
    CrushConnectWaitlist.objects.create(user=user, notification_preference=True)

    assert user.id in [row.user_id for row in candidates_for_wave(3)]
