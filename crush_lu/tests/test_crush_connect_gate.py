"""Tests for Connect's weekly Read-the-Photo question catalogue and stats."""

from datetime import date

import pytest
from django.core.management import call_command
from django.utils import timezone

from crush_lu.models import (
    ConnectCycleCard,
    ConnectQuestion,
    ConnectQuestionWeek,
    ConnectWeekSession,
)
from crush_lu.services.crush_connect import (
    WEEKLY_CATALOGUE_SIZE,
    gate_answer_stats,
    get_eligible_pool,
    get_or_create_question_week,
)
from crush_lu.tests.test_crush_connect import (
    _make_user,
    _mark_attended,
    _set_gate_questions,
)

pytestmark = pytest.mark.urls("azureproject.urls_crush")


@pytest.mark.django_db
def test_seed_catalogue_loaded():
    active = ConnectQuestion.objects.filter(is_active=True)
    assert active.count() >= 30
    sample = active.first()
    assert sample.text_en and sample.text_de and sample.text_fr
    spicy = ConnectQuestion.objects.filter(category="spicy")
    assert spicy.exists()
    assert not spicy.filter(is_active=True).exists()


@pytest.mark.django_db
def test_weekly_rotation_deterministic_and_sized():
    week = get_or_create_question_week(date(2026, 6, 29))
    ids = set(week.questions.values_list("pk", flat=True))
    assert len(ids) == WEEKLY_CATALOGUE_SIZE

    ConnectQuestionWeek.objects.filter(pk=week.pk).delete()
    rebuilt = get_or_create_question_week(date(2026, 6, 29))
    assert set(rebuilt.questions.values_list("pk", flat=True)) == ids


@pytest.mark.django_db
def test_different_weeks_may_differ():
    first = get_or_create_question_week(date(2026, 6, 29))
    second = get_or_create_question_week(date(2026, 7, 6))
    assert (first.iso_year, first.iso_week) != (second.iso_year, second.iso_week)


@pytest.mark.django_db
def test_rotation_command_idempotent():
    call_command("rotate_connect_questions")
    call_command("rotate_connect_questions")
    iso = timezone.localdate().isocalendar()
    weeks = ConnectQuestionWeek.objects.filter(iso_year=iso.year, iso_week=iso.week)
    assert weeks.count() == 1


@pytest.mark.django_db
def test_gate_answer_stats_aggregate_completed_cycle_cards_anonymously():
    profile_owner = _make_user(username="owner", gender="F", premium=False)
    questions = _set_gate_questions(profile_owner, answers=[True, True, True])
    guesses = [
        [True, False, True],
        [True, True, False],
        [False, False, False],
    ]

    for index, answers in enumerate(guesses):
        responder = _make_user(username=f"reader{index}", premium=False)
        session = ConnectWeekSession.objects.create(user=responder)
        ConnectCycleCard.objects.create(
            session=session,
            day_number=1,
            card_index=1,
            target_user=profile_owner,
            generated_date=timezone.localdate(),
            answers_json={
                "guesses": {
                    str(question.pk): answer
                    for question, answer in zip(questions, answers)
                },
                "gate_align": 0,
            },
            is_completed=True,
            completed_at=timezone.now(),
        )

    incomplete_reader = _make_user(username="incomplete", premium=False)
    incomplete_session = ConnectWeekSession.objects.create(user=incomplete_reader)
    ConnectCycleCard.objects.create(
        session=incomplete_session,
        day_number=1,
        card_index=1,
        target_user=profile_owner,
        generated_date=timezone.localdate(),
        answers_json={"guesses": {str(questions[0].pk): True}},
        is_completed=False,
    )

    stats = gate_answer_stats(profile_owner)
    assert stats[questions[0].pk] == {"yes": 2, "total": 3}
    assert stats[questions[1].pk] == {"yes": 1, "total": 3}
    assert stats[questions[2].pk] == {"yes": 1, "total": 3}
    assert "responder" not in repr(stats)

    from crush_lu.views_crush_connect import _gate_stat_rows

    membership = profile_owner.crush_connect_membership
    rows = _gate_stat_rows(profile_owner, membership)
    assert [row["total"] for row in rows] == [3, 3, 3]
    assert all("owner_answer" not in row for row in rows)


@pytest.mark.django_db
def test_no_consent_excludes_from_pool():
    me = _make_user(username="me", preferred_genders=["F"])
    _mark_attended(me)
    consenting = _make_user(username="yes_c", gender="F", preferred_genders=["M"])
    not_consenting = _make_user(
        username="no_c",
        gender="F",
        preferred_genders=["M"],
        photo_share_consent=False,
    )

    pool = get_eligible_pool(me)
    assert consenting in pool
    assert not_consenting not in pool
