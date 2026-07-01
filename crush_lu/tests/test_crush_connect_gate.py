"""
Tests for the "Read-the-Photo" question-gated matching mechanic (M8/M9):
the weekly question catalogue + rotation, the alignment gate that reframes
CuriositySpark, the anonymous aggregate stat, and the photo-share consent gate.

Reuses the helpers from ``test_crush_connect`` (``_make_user`` builds a
consented, verified, premium+LuxID member by default).
"""
from datetime import date

import pytest
from django.core.management import call_command
from django.utils import timezone

pytestmark = pytest.mark.urls("azureproject.urls_crush")

from crush_lu.models import (
    ConnectQuestion,
    ConnectQuestionAnswer,
    ConnectQuestionWeek,
    CuriositySpark,
    MemberGateQuestion,
)
from crush_lu.services.crush_connect import (
    GATE_ALIGN_MIN,
    GATE_QUESTION_COUNT,
    WEEKLY_CATALOGUE_SIZE,
    alignment_score,
    gate_answer_stats,
    get_eligible_pool,
    get_or_create_question_week,
    submit_gate_answers,
)
from crush_lu.tests.test_crush_connect import (
    _make_user,
    _mark_attended,
    _set_gate_questions,
    _surface_in_drop,
)


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
    out1, spark1 = submit_gate_answers(me, her, {q.id: a for q, a in zip(her_qs, [False, True, False])})
    assert out1 == "sent"

    # Candidate answers back well (never had a Drop of her own) → mutual match.
    out2, spark2 = submit_gate_answers(her, me, {q.id: a for q, a in zip(my_qs, [True, False, True])})
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
        MemberGateQuestion.objects.filter(membership=her.crush_connect_membership)
        .values_list("question_id", flat=True)
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
