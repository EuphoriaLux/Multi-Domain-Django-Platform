"""Coaches on the Crush Connect candidate track.

Two rules, decided together:

1. A coach IS an ordinary candidate. Nothing excludes coaches from the pool at
   large — they are members too and may date.
2. A coach is NEVER a candidate for their OWN assigned member, in either
   direction. That coach curates the member's picks, reads their Connect data
   and holds ``is_staff``; a romantic proposition on top of that asymmetry is
   the one pairing the product must not create.

Plus the routing consequence: a coach without an active ``PremiumMembership``
belongs on the candidate track ("In the Mix"), not the receiver track. The
staff bypass previously forced every coach onto Today's Drop — a page
``get_eligible_pool`` will always render empty for them, because it has no
staff bypass and gates on ``has_active_premium``.
"""

import pytest

from crush_lu.models import CrushCoach
from crush_lu.services.crush_connect import (
    can_send_spark,
    get_eligible_pool,
    is_assigned_coach_pair,
)
from crush_lu.templatetags.crush_connect_tags import crush_connect_is_receiver
from crush_lu.tests.test_crush_connect import (
    CONNECT_HOME_URL,
    SPARKS_RECEIVED_URL,
    _get_coach,
    _login_eligible,
    _make_user,
    _mark_attended,
    _set_gate_questions,
    _surface_in_drop,
)
from crush_lu.views_crush_connect import _connect_done_url


def _make_coach_member(username, **kwargs):
    """A user who is BOTH a full Connect candidate and an active CrushCoach.

    ``premium=False`` so they are a candidate, not a receiver — the case that
    was previously invisible. Creating the CrushCoach fires
    ``manage_coach_staff_status``, which grants ``is_staff``; that is the real
    production behaviour and is exactly what these tests need to exercise.
    """
    user = _make_user(username=username, premium=False, **kwargs)
    coach = CrushCoach.objects.create(
        user=user,
        bio="Test coach",
        specializations="General",
        phone_number="+352123456",
        is_active=True,
    )
    user.refresh_from_db()
    return user, coach


def _assign_coach(member, coach):
    profile = member.crushprofile
    profile.assigned_coach = coach
    profile.save(update_fields=["assigned_coach"])
    member.refresh_from_db()
    return member


def _grant_premium(user):
    """Give an existing user the receiver entitlement.

    ``_make_user(premium=True)`` does this at construction, but a coach has to
    be built with ``premium=False`` first (``_make_coach_member``) — a coach
    holding their own Premium is exactly the case where the pool's one-way
    exclusion showed their own member to them.
    """
    from django.utils import timezone

    from crush_lu.models import PremiumMembership

    profile = user.crushprofile
    if profile.assigned_coach_id is None:
        profile.assigned_coach = _get_coach()
        profile.assigned_coach_at = timezone.now()
        profile.save(update_fields=["assigned_coach", "assigned_coach_at"])
    PremiumMembership.objects.create(
        user=user,
        coach=profile.assigned_coach,
        status="active",
        payment_confirmed=True,
        payment_date=timezone.now(),
    )
    user.refresh_from_db()
    return user


# ---------------------------------------------------------------------------
# is_assigned_coach_pair
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_assigned_coach_pair_detected_in_both_directions():
    member = _make_user(username="pairmem", gender="M", preferred_genders=["F"])
    coach_user, coach = _make_coach_member(
        "paircoach", gender="F", preferred_genders=["M"]
    )
    _assign_coach(member, coach)

    assert is_assigned_coach_pair(member, coach_user) is True
    # Symmetric: either side may be the one browsing.
    assert is_assigned_coach_pair(coach_user, member) is True


@pytest.mark.django_db
def test_unrelated_coach_is_not_an_assigned_pair():
    member = _make_user(username="unrelmem", gender="M", preferred_genders=["F"])
    coach_user, _coach = _make_coach_member(
        "unrelcoach", gender="F", preferred_genders=["M"]
    )
    # member keeps the default shared coach from _make_user(premium=True)
    assert is_assigned_coach_pair(member, coach_user) is False


@pytest.mark.django_db
def test_assigned_coach_pair_false_for_same_user_and_none():
    member = _make_user(username="selfmem", gender="M", preferred_genders=["F"])
    assert is_assigned_coach_pair(member, member) is False
    assert is_assigned_coach_pair(member, None) is False
    assert is_assigned_coach_pair(None, member) is False


# ---------------------------------------------------------------------------
# Pool membership
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_own_assigned_coach_is_excluded_from_pool():
    member = _make_user(username="poolmem", gender="M", preferred_genders=["F"])
    coach_user, coach = _make_coach_member(
        "poolcoach", gender="F", preferred_genders=["M"]
    )
    _assign_coach(member, coach)

    assert coach_user not in list(get_eligible_pool(member))
    # And the point-lookup form used by the Coach's Pick validators agrees.
    assert not get_eligible_pool(member, candidate_pk=coach_user.pk).exists()


@pytest.mark.django_db
def test_a_coach_who_is_not_yours_stays_in_the_pool():
    """Rule 1: coaches are ordinary candidates for everyone else."""
    member = _make_user(username="othermem", gender="M", preferred_genders=["F"])
    mine_user, mine = _make_coach_member("mycoach", gender="F", preferred_genders=["M"])
    theirs_user, _theirs = _make_coach_member(
        "notmycoach", gender="F", preferred_genders=["M"]
    )
    # An ordinary member with NO CrushCoach row at all. This is the one that
    # catches the classic Django footgun: `.exclude(crushcoach__pk=X)` spans a
    # nullable reverse join, and a naive NOT(join.pk = X) would evaluate to
    # NULL — and therefore drop — every user who simply has no coach record.
    plain_user = _make_user(username="plainf", gender="F", preferred_genders=["M"])
    _assign_coach(member, mine)

    pool = list(get_eligible_pool(member))
    assert theirs_user in pool, "an unassigned coach must remain a candidate"
    assert (
        plain_user in pool
    ), "a member with no CrushCoach row must survive the exclude"
    assert mine_user not in pool


@pytest.mark.django_db
def test_pool_not_emptied_when_requester_has_no_assigned_coach():
    """The exclusion must be guarded on the id. Unguarded, it would become
    `.exclude(crushcoach__pk=None)` and drop the whole pool."""
    member = _make_user(username="nocoachmem", gender="M", preferred_genders=["F"])
    # Keep the PremiumMembership (that is the receiver entitlement) but clear
    # the service relationship, so assigned_coach_id is None while the user can
    # still legitimately receive Drops.
    member.crushprofile.assigned_coach = None
    member.crushprofile.save(update_fields=["assigned_coach"])
    member.refresh_from_db()
    assert member.crushprofile.assigned_coach_id is None
    assert member.crushprofile.has_active_premium is True

    target = _make_user(username="plaintarget", gender="F", preferred_genders=["M"])
    assert target in list(get_eligible_pool(member))


# ---------------------------------------------------------------------------
# Sparks — re-checked at the action point, not trusted from the snapshot
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_cannot_spark_own_coach_even_from_a_stale_drop_snapshot():
    """ConnectDailyDrop rows are immutable, so a pair surfaced BEFORE the coach
    was assigned (the door assigns one on first attendance) would still pass the
    'was surfaced' test. The pairwise rule has to be re-checked here."""
    member = _make_user(username="sparkmem", gender="M", preferred_genders=["F"])
    coach_user, coach = _make_coach_member(
        "sparkcoach", gender="F", preferred_genders=["M"]
    )
    # Surfaced first — then the coach assignment lands.
    _surface_in_drop(member, coach_user)
    _assign_coach(member, coach)

    allowed, reason = can_send_spark(member, coach_user)
    assert allowed is False
    assert reason == "recipient_unavailable"


# ---------------------------------------------------------------------------
# Track routing — a coach without Premium is a candidate
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_coach_without_premium_lands_on_the_candidate_surface(settings):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    coach_user, _coach = _make_coach_member(
        "trackcoach", gender="F", preferred_genders=["M"]
    )
    assert coach_user.is_staff is True, "coaches are auto-granted is_staff"

    assert _connect_done_url(coach_user) == "crush_lu:crush_connect_catalogue_status"
    assert crush_connect_is_receiver(coach_user) is False


@pytest.mark.django_db
def test_premium_member_still_reads_as_receiver(settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_user(username="recvmem", gender="M", preferred_genders=["F"])
    assert _connect_done_url(member) == "crush_lu:crush_connect_home"
    assert crush_connect_is_receiver(member) is True


@pytest.mark.django_db
def test_coach_assigned_non_premium_member_is_not_a_receiver(settings):
    """The subnav bug: assigned_coach was being used as the Premium signal, so
    every member the door assigned a coach to got a 'Today's Drop' tab that
    redirected them straight back to the catalogue."""
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    member = _make_user(
        username="assignedmem", gender="M", preferred_genders=["F"], premium=False
    )
    _coach_user, coach = _make_coach_member(
        "assignedcoach", gender="F", preferred_genders=["M"]
    )
    _assign_coach(member, coach)

    assert member.crushprofile.assigned_coach_id is not None
    assert member.crushprofile.has_active_premium is False
    assert crush_connect_is_receiver(member) is False


# ---------------------------------------------------------------------------
# The pair rule has to hold on every surface, not just the live pool
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_pool_excludes_the_coachs_own_assigned_members():
    """The mirror direction — the pool exclusion was one-way.

    It removed the coach assigned to the requester, but not the members
    assigned to a requesting coach. A coach holding their own Premium could
    therefore open a Drop containing their own member's card and clear photo.
    """
    coach_user, coach = _make_coach_member(
        "poolcoach", gender="M", preferred_genders=["F"]
    )
    _grant_premium(coach_user)
    member = _make_user(
        username="poolmember", gender="F", preferred_genders=["M"], premium=False
    )
    _assign_coach(member, coach)

    assert is_assigned_coach_pair(coach_user, member) is True
    pool_ids = set(get_eligible_pool(coach_user).values_list("pk", flat=True))
    assert member.pk not in pool_ids

    # ...and the rule stays pairwise. "Coaches see nobody" would be a
    # different, wrong fix.
    other = _make_user(
        username="poolother", gender="F", preferred_genders=["M"], premium=False
    )
    pool_ids = set(get_eligible_pool(coach_user).values_list("pk", flat=True))
    assert other.pk in pool_ids


@pytest.mark.django_db
def test_persisted_drop_hides_a_pair_formed_after_the_snapshot(client, settings):
    """Drop snapshots are immutable; the assignment can arrive later.

    The door assigns a coach on first attendance, so a member surfaced this
    morning can be that coach's own member by the evening. The render-time
    revalidation re-checks blocks, exclusion, verification and photos — it has
    to re-check this too, or the card stays live for the rest of the day.
    """
    settings.CRUSH_CONNECT_LAUNCHED = True
    coach_user, coach = _make_coach_member(
        "snapcoach", gender="M", preferred_genders=["F"]
    )
    _grant_premium(coach_user)
    _mark_attended(coach_user)
    member = _make_user(
        username="snapmember", gender="F", preferred_genders=["M"], premium=False
    )

    # Surfaced BEFORE the assignment exists.
    drop = _surface_in_drop(coach_user, member)
    _assign_coach(member, coach)

    _login_eligible(client, coach_user)
    body = client.get(CONNECT_HOME_URL).content.decode()

    assert member.first_name not in body
    # The persisted snapshot stays intact for audit — only the render filters.
    assert drop.recipients.filter(pk=member.pk).exists()


@pytest.mark.django_db
def test_stale_pending_spark_cannot_be_accepted_after_assignment():
    """A Spark predating the assignment must not be acceptable.

    can_send_spark blocks NEW Sparks, but one already pending was created
    while the pair was legitimate. Responding is the other way to reach the
    same coach/member match.
    """
    from crush_lu.models import CuriositySpark
    from crush_lu.services.crush_connect import respond_to_spark

    coach_user, coach = _make_coach_member(
        "sparkcoach", gender="M", preferred_genders=["F"]
    )
    _grant_premium(coach_user)
    member = _make_user(
        username="sparkmember", gender="F", preferred_genders=["M"], premium=False
    )
    _surface_in_drop(coach_user, member)

    spark = CuriositySpark.objects.create(sender=coach_user, recipient=member)
    _assign_coach(member, coach)

    respond_to_spark(spark, accept=True)
    spark.refresh_from_db()
    assert spark.status == "pending", "an assigned pair must never match"


@pytest.mark.django_db
def test_stale_pending_spark_is_hidden_from_the_recipient(client, settings):
    """...and it must not be offered in the first place."""
    from crush_lu.models import CuriositySpark

    settings.CRUSH_CONNECT_LAUNCHED = True
    coach_user, coach = _make_coach_member(
        "hidecoach", gender="M", preferred_genders=["F"]
    )
    _grant_premium(coach_user)
    member = _make_user(
        username="hidemember", gender="F", preferred_genders=["M"], premium=False
    )
    _surface_in_drop(coach_user, member)
    CuriositySpark.objects.create(sender=coach_user, recipient=member)
    _assign_coach(member, coach)

    _login_eligible(client, member)
    response = client.get(SPARKS_RECEIVED_URL)

    assert response.status_code == 200
    assert list(response.context["sparks"]) == []


@pytest.mark.django_db
def test_answering_back_a_stale_spark_is_refused():
    """The answer-back branch of submit_gate_answers is the third way in."""
    from crush_lu.models import CuriositySpark
    from crush_lu.services.crush_connect import owner_gate_truths, submit_gate_answers

    coach_user, coach = _make_coach_member(
        "backcoach", gender="M", preferred_genders=["F"]
    )
    _grant_premium(coach_user)
    member = _make_user(
        username="backmember", gender="F", preferred_genders=["M"], premium=False
    )
    _surface_in_drop(coach_user, member)
    _set_gate_questions(coach_user)
    _set_gate_questions(member)

    CuriositySpark.objects.create(sender=coach_user, recipient=member)
    _assign_coach(member, coach)

    truths = owner_gate_truths(coach_user)
    with pytest.raises(ValueError) as excinfo:
        submit_gate_answers(member, coach_user, dict(truths))
    assert str(excinfo.value) == "recipient_unavailable"


# ---------------------------------------------------------------------------
# Onboarding copy / email follow the entitlement, not is_staff
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_onboarding_completion_treats_a_non_premium_coach_as_a_candidate(rf):
    """is_staff was making the wizard promise a Drop the coach never gets.

    _emit_onboarding_complete claimed "your first Drop is ready" and skipped
    the candidate welcome email, immediately before _connect_done_url
    redirected the same coach to the candidate page.
    """
    from unittest.mock import patch

    from django.contrib.messages.storage.fallback import FallbackStorage

    from crush_lu.views_crush_connect import _emit_onboarding_complete

    coach_user, _coach = _make_coach_member(
        "onbcoach", gender="F", preferred_genders=["M"]
    )
    assert coach_user.is_staff is True

    request = rf.post("/en/crush-connect/onboarding/")
    request.user = coach_user
    request.session = {}
    request._messages = FallbackStorage(request)

    with patch(
        "crush_lu.views_crush_connect.send_crush_connect_catalogue_welcome"
    ) as send_welcome:
        _emit_onboarding_complete(request, "crush_lu:crush_connect_catalogue_status")

    send_welcome.assert_called_once()
    texts = [str(m.message) for m in request._messages]
    assert any("in the mix" in t.lower() for t in texts), texts
    assert not any("first Drop is ready" in t for t in texts), texts
