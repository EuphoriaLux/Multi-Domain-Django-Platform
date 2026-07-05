"""
Tests for the "Read-the-Photo" question-gated matching mechanic (M8/M9):
the weekly question catalogue + rotation, the alignment gate that reframes
CuriositySpark, the anonymous aggregate stat, and the photo-share consent gate.

Reuses the helpers from ``test_crush_connect`` (``_make_user`` builds a
consented, verified, premium+LuxID member by default).
"""

from datetime import date, timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from crush_lu.models import (
    ConnectDailyDrop,
    ConnectQuestion,
    ConnectQuestionAnswer,
    ConnectQuestionWeek,
    CuriositySpark,
    MemberGateQuestion,
)
from crush_lu.services.crush_connect import (
    GATE_QUESTION_COUNT,
    WEEKLY_CATALOGUE_SIZE,
    alignment_score,
    gate_answer_stats,
    get_eligible_pool,
    get_or_create_question_week,
    submit_gate_answers,
)
from crush_lu.tests.test_crush_connect import (
    _login_eligible,
    _make_user,
    _mark_attended,
    _set_gate_questions,
    _surface_in_drop,
)

pytestmark = pytest.mark.urls("azureproject.urls_crush")


# ---------------------------------------------------------------------------
# Catalogue + weekly rotation
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_seed_catalogue_loaded():
    active = ConnectQuestion.objects.filter(is_active=True)
    assert active.count() >= 30
    # Every active question carries EN/DE/FR (mirror test_seed_prompts_loaded).
    sample = active.first()
    assert sample.text_en and sample.text_de and sample.text_fr
    # Spicy questions ship inactive.
    spicy = ConnectQuestion.objects.filter(category="spicy")
    assert spicy.exists()
    assert not spicy.filter(is_active=True).exists()


@pytest.mark.django_db
def test_weekly_rotation_deterministic_and_sized():
    week = get_or_create_question_week(date(2026, 6, 29))  # a Monday
    ids = set(week.questions.values_list("pk", flat=True))
    assert len(ids) == WEEKLY_CATALOGUE_SIZE

    # Rebuilding the SAME ISO week from scratch yields the SAME set (seeded pick).
    ConnectQuestionWeek.objects.filter(pk=week.pk).delete()
    rebuilt = get_or_create_question_week(date(2026, 6, 29))
    assert set(rebuilt.questions.values_list("pk", flat=True)) == ids


@pytest.mark.django_db
def test_different_weeks_may_differ():
    a = get_or_create_question_week(date(2026, 6, 29))
    b = get_or_create_question_week(date(2026, 7, 6))
    assert (a.iso_year, a.iso_week) != (b.iso_year, b.iso_week)


@pytest.mark.django_db
def test_rotation_command_idempotent():
    call_command("rotate_connect_questions")
    call_command("rotate_connect_questions")  # re-run must not re-roll
    iso = timezone.localdate().isocalendar()
    weeks = ConnectQuestionWeek.objects.filter(iso_year=iso.year, iso_week=iso.week)
    assert weeks.count() == 1


# ---------------------------------------------------------------------------
# Alignment scoring
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_alignment_score_counts_matches():
    me = _make_user(username="me", preferred_genders=["F"])
    her = _make_user(username="her", gender="F", premium=False)
    questions = _set_gate_questions(her, answers=[True, True, False])

    # Guess T/T/T → first two match her truth, third doesn't → 2.
    for q, guess in zip(questions, [True, True, True]):
        ConnectQuestionAnswer.objects.create(
            responder=me, profile_owner=her, question=q, answer=guess
        )
    assert alignment_score(me, her) == 2


# ---------------------------------------------------------------------------
# The gate: miss / sent / matched
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_gate_miss_records_answers_but_no_spark():
    me = _make_user(username="me", preferred_genders=["F"])
    _set_gate_questions(me)  # first movers must have their own questions
    her = _make_user(username="her", gender="F", premium=False)
    questions = _set_gate_questions(her, answers=[True, True, True])
    _surface_in_drop(me, her)

    guesses = {q.id: False for q in questions}  # 0 correct → miss
    outcome, spark = submit_gate_answers(me, her, guesses)

    assert outcome == "miss"
    assert spark is None
    # Guesses still recorded (they feed the aggregate stat).
    assert (
        ConnectQuestionAnswer.objects.filter(responder=me, profile_owner=her).count()
        == GATE_QUESTION_COUNT
    )


@pytest.mark.django_db
def test_gate_sent_creates_pending_spark():
    me = _make_user(username="me", preferred_genders=["F"])
    _set_gate_questions(me)  # first movers must have their own questions
    her = _make_user(username="her", gender="F", premium=False)
    questions = _set_gate_questions(her, answers=[True, True, True])
    _surface_in_drop(me, her)

    guesses = {q.id: True for q in questions}  # 3 correct ≥ threshold
    outcome, spark = submit_gate_answers(me, her, guesses)

    assert outcome == "sent"
    assert spark.status == "pending"
    assert spark.sender_id == me.pk and spark.recipient_id == her.pk


@pytest.mark.django_db
def test_mutual_read_matches_and_accepts():
    me = _make_user(username="me", preferred_genders=["F"])
    her = _make_user(username="her", gender="F", premium=False)
    my_qs = _set_gate_questions(me, answers=[True, False, True])
    her_qs = _set_gate_questions(her, answers=[False, True, False])
    _surface_in_drop(me, her)

    # First mover reads her well → pending spark me→her.
    out1, spark1 = submit_gate_answers(
        me, her, {q.id: a for q, a in zip(her_qs, [False, True, False])}
    )
    assert out1 == "sent"

    # Candidate answers back well (never had a Drop of her own) → mutual match.
    out2, spark2 = submit_gate_answers(
        her, me, {q.id: a for q, a in zip(my_qs, [True, False, True])}
    )
    assert out2 == "matched"
    spark2.refresh_from_db()
    assert spark2.status == "accepted"


@pytest.mark.django_db
def test_answer_back_below_threshold_does_not_match():
    me = _make_user(username="me", preferred_genders=["F"])
    her = _make_user(username="her", gender="F", premium=False)
    my_qs = _set_gate_questions(me, answers=[True, True, True])
    her_qs = _set_gate_questions(her, answers=[True, True, True])
    _surface_in_drop(me, her)

    submit_gate_answers(me, her, {q.id: True for q in her_qs})  # sent
    # She misreads me (all wrong) → stays pending, silent.
    out, spark = submit_gate_answers(her, me, {q.id: False for q in my_qs})
    assert out == "miss"
    spark.refresh_from_db()
    assert spark.status == "pending"


@pytest.mark.django_db
def test_gate_requires_matching_question_set():
    me = _make_user(username="me", preferred_genders=["F"])
    her = _make_user(username="her", gender="F", premium=False)
    _set_gate_questions(her, answers=[True, True, True])
    _surface_in_drop(me, her)

    # Guess keys that don't match her current 3 questions → rejected.
    bogus = {q.id: True for q in ConnectQuestion.objects.all()[:3]}
    her_ids = set(
        MemberGateQuestion.objects.filter(
            membership=her.crush_connect_membership
        ).values_list("question_id", flat=True)
    )
    if set(bogus.keys()) == her_ids:  # extremely unlikely, but keep the test honest
        bogus = {list(her_ids)[0]: True}
    with pytest.raises(ValueError):
        submit_gate_answers(me, her, bogus)


@pytest.mark.django_db
def test_first_mover_without_own_questions_refused():
    """A first mover who hasn't picked their own 3 questions can't create a Spark
    (the recipient could never answer them back)."""
    me = _make_user(username="me", preferred_genders=["F"])  # no gate questions
    her = _make_user(username="her", gender="F", premium=False)
    questions = _set_gate_questions(her, answers=[True, True, True])
    _surface_in_drop(me, her)

    with pytest.raises(ValueError):
        submit_gate_answers(me, her, {q.id: True for q in questions})
    assert not CuriositySpark.objects.filter(sender=me, recipient=her).exists()


# ---------------------------------------------------------------------------
# One read per Drop
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_read_locks_other_cards_in_same_drop():
    """The first gate submission from a Drop — even a MISS — consumes the
    Drop's single read; answering any other card from it is refused."""
    me = _make_user(username="me", preferred_genders=["F"])
    _set_gate_questions(me)
    her = _make_user(username="her", gender="F", premium=False)
    other = _make_user(username="other", gender="F", premium=False)
    her_qs = _set_gate_questions(her, answers=[True, True, True])
    other_qs = _set_gate_questions(other, answers=[True, True, True])
    drop = _surface_in_drop(me, her)
    _surface_in_drop(me, other)  # same drop (same user, same date)

    outcome, _spark = submit_gate_answers(me, her, {q.id: False for q in her_qs})
    assert outcome == "miss"
    drop.refresh_from_db()
    assert drop.read_target_id == her.pk

    with pytest.raises(ValueError, match="drop_read_used"):
        submit_gate_answers(me, other, {q.id: True for q in other_qs})
    assert not CuriositySpark.objects.filter(sender=me, recipient=other).exists()


@pytest.mark.django_db
def test_gate_claims_originating_drop_when_newer_drop_exists():
    """A stale form must spend the Drop that rendered it, not a newer Drop."""
    me = _make_user(username="me", preferred_genders=["F"])
    _set_gate_questions(me)
    her = _make_user(username="her", gender="F", premium=False)
    her_qs = _set_gate_questions(her, answers=[True, True, True])

    old_drop = ConnectDailyDrop.objects.create(
        user=me, drop_date=timezone.localdate() - timedelta(days=1)
    )
    old_drop.recipients.add(her)
    newer_drop = ConnectDailyDrop.objects.create(
        user=me, drop_date=timezone.localdate()
    )
    newer_drop.recipients.add(her)

    outcome, spark = submit_gate_answers(
        me,
        her,
        {q.id: True for q in her_qs},
        drop_id=old_drop.pk,
    )

    assert outcome == "sent"
    assert spark.drop_id == old_drop.pk
    old_drop.refresh_from_db()
    newer_drop.refresh_from_db()
    assert old_drop.read_target_id == her.pk
    assert newer_drop.read_target_id is None


@pytest.mark.django_db
def test_losing_read_claim_aborts_before_spark(monkeypatch):
    """If another card claims the Drop between pre-check and UPDATE, abort."""
    me = _make_user(username="me", preferred_genders=["F"])
    _set_gate_questions(me)
    her = _make_user(username="her", gender="F", premium=False)
    other = _make_user(username="other", gender="F", premium=False)
    her_qs = _set_gate_questions(her, answers=[True, True, True])
    drop = _surface_in_drop(me, her)
    drop.recipients.add(other)

    from django.db.models.query import QuerySet

    original_update = QuerySet.update

    def raced_update(queryset, **kwargs):
        if (
            queryset.model is ConnectDailyDrop
            and kwargs.get("read_target_id") == her.pk
        ):
            original_update(
                ConnectDailyDrop.objects.filter(pk=drop.pk),
                read_target_id=other.pk,
                read_at=timezone.now(),
            )
            return 0
        return original_update(queryset, **kwargs)

    monkeypatch.setattr(QuerySet, "update", raced_update)

    with pytest.raises(ValueError, match="drop_read_used"):
        submit_gate_answers(
            me,
            her,
            {q.id: True for q in her_qs},
            drop_id=drop.pk,
        )

    assert not CuriositySpark.objects.filter(sender=me, recipient=her).exists()
    assert not ConnectQuestionAnswer.objects.filter(
        responder=me, profile_owner=her
    ).exists()


@pytest.mark.django_db
def test_read_repost_same_target_still_idempotent():
    """Re-POSTing the SAME target after the read is spent stays idempotent —
    the lock only guards OTHER cards."""
    me = _make_user(username="me", preferred_genders=["F"])
    _set_gate_questions(me)
    her = _make_user(username="her", gender="F", premium=False)
    her_qs = _set_gate_questions(her, answers=[True, True, True])
    _surface_in_drop(me, her)

    out1, spark1 = submit_gate_answers(me, her, {q.id: True for q in her_qs})
    out2, spark2 = submit_gate_answers(me, her, {q.id: True for q in her_qs})
    assert (out1, out2) == ("sent", "sent")
    assert spark1.pk == spark2.pk


@pytest.mark.django_db
def test_answer_back_ignores_read_lock():
    """Answering a received Spark back is never a Drop read: it must work even
    when the responder's OWN Drop read is already spent on someone else."""
    me = _make_user(username="me", preferred_genders=["F"])
    her = _make_user(username="her", gender="F")  # premium: has her own Drop
    him = _make_user(username="him", gender="M", premium=False)
    my_qs = _set_gate_questions(me, answers=[True, False, True])
    her_qs = _set_gate_questions(her, answers=[False, True, False])
    him_qs = _set_gate_questions(him, answers=[True, True, True])

    # Her Drop read is spent on him (a miss — still consumes).
    her_drop = _surface_in_drop(her, him)
    submit_gate_answers(her, him, {q.id: False for q in him_qs})
    her_drop.refresh_from_db()
    assert her_drop.read_target_id == him.pk

    # Me reads her well → pending Spark me→her.
    _surface_in_drop(me, her)
    out1, _ = submit_gate_answers(
        me, her, {q.id: a for q, a in zip(her_qs, [False, True, False])}
    )
    assert out1 == "sent"

    # She answers me back despite her spent Drop read → mutual match.
    out2, spark = submit_gate_answers(
        her, me, {q.id: a for q, a in zip(my_qs, [True, False, True])}
    )
    assert out2 == "matched"
    spark.refresh_from_db()
    assert spark.status == "accepted"


@pytest.mark.django_db
def test_compose_view_redirects_when_read_spent(client, settings):
    """GET on another card's compose page after the Drop read is spent bounces
    back to Today with the 'read used' notice."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"])
    _set_gate_questions(me)
    her = _make_user(username="her", gender="F", premium=False)
    other = _make_user(username="other", gender="F", premium=False)
    her_qs = _set_gate_questions(her, answers=[True, True, True])
    _set_gate_questions(other, answers=[True, True, True])
    _surface_in_drop(me, her)
    _surface_in_drop(me, other)

    submit_gate_answers(me, her, {q.id: True for q in her_qs})

    _login_eligible(client, me)
    resp = client.get(f"/en/crush-connect/spark/{other.pk}/")
    assert resp.status_code in (301, 302)
    assert "/crush-connect/today/" in resp.url
    # The spent read never blocks revisiting the chosen card's page.
    resp = client.get(f"/en/crush-connect/spark/{her.pk}/")
    assert resp.status_code in (301, 302)  # already answered → home, not teaser
    assert "/crush-connect/today/" in resp.url


@pytest.mark.django_db
def test_compose_post_uses_submitted_drop_id(client, settings):
    """The POSTed form id controls which surfaced Drop is claimed."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"])
    _set_gate_questions(me)
    her = _make_user(username="her", gender="F", premium=False)
    other = _make_user(username="other", gender="F", premium=False)
    her_qs = _set_gate_questions(her, answers=[True, True, True])
    old_drop = ConnectDailyDrop.objects.create(
        user=me, drop_date=timezone.localdate() - timedelta(days=1)
    )
    old_drop.recipients.add(her)
    newer_drop = ConnectDailyDrop.objects.create(
        user=me, drop_date=timezone.localdate()
    )
    newer_drop.recipients.add(her)
    newer_drop.recipients.add(other)
    newer_drop.read_target = other
    newer_drop.read_at = timezone.now()
    newer_drop.save(update_fields=["read_target", "read_at"])
    _login_eligible(client, me)

    data = {f"answer_{q.id}": "yes" for q in her_qs}
    data["drop_id"] = str(old_drop.pk)
    resp = client.post(f"/en/crush-connect/spark/{her.pk}/", data=data)

    assert resp.status_code in (301, 302)
    spark = CuriositySpark.objects.get(sender=me, recipient=her)
    assert spark.drop_id == old_drop.pk
    old_drop.refresh_from_db()
    newer_drop.refresh_from_db()
    assert old_drop.read_target_id == her.pk
    assert newer_drop.read_target_id == other.pk


# ---------------------------------------------------------------------------
# Aggregate stats (anonymous)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_gate_answer_stats_aggregates_without_identity():
    her = _make_user(username="her", gender="F", premium=False)
    questions = _set_gate_questions(her, answers=[True, True, True])
    q0 = questions[0]

    # Three different responders guess q0: two Yes, one No.
    for i, guess in enumerate([True, True, False]):
        r = _make_user(username=f"r{i}", preferred_genders=["F"])
        ConnectQuestionAnswer.objects.create(
            responder=r, profile_owner=her, question=q0, answer=guess
        )

    stats = gate_answer_stats(her)
    assert stats[q0.id]["yes"] == 2
    assert stats[q0.id]["total"] == 3


# ---------------------------------------------------------------------------
# Photo-share consent gate
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_no_consent_excludes_from_pool():
    me = _make_user(username="me", preferred_genders=["F"])
    _mark_attended(me)
    consenting = _make_user(username="yes_c", gender="F", preferred_genders=["M"])
    not_consenting = _make_user(
        username="no_c", gender="F", preferred_genders=["M"], photo_share_consent=False
    )

    pool = get_eligible_pool(me)
    assert consenting in pool
    assert not_consenting not in pool
