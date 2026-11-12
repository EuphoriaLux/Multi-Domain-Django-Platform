"""Staging-only Quiz Night rehearsal tooling."""

import json

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client

from crush_lu.models.events import EventRegistration
from crush_lu.models.quiz import (
    QuizEvent,
    QuizRotationSchedule,
    QuizTableMembership,
    TableRoundScore,
)
from crush_lu.tests import test_quiz as _quiz_tests

pytestmark = pytest.mark.django_db

# Reuse the quiz suite's fixtures. Aliased (not imported by name) so the test
# arguments below do not look like redefinitions to the linter.
coach_user = _quiz_tests.coach_user
quiz_event = _quiz_tests.quiz_event
quiz_user = _quiz_tests.quiz_user


@pytest.fixture
def staging(monkeypatch):
    monkeypatch.setenv("DJANGO_ENV", "staging")


@pytest.fixture
def seated_quiz(quiz_event, staging):
    from crush_lu.services.quiz_rehearsal import add_rehearsal_guests

    quiz_event.num_tables = 3
    quiz_event.save(update_fields=["num_tables"])
    add_rehearsal_guests(quiz_event, 6)
    return quiz_event


def test_add_guests_attends_and_seats_them(seated_quiz):
    quiz = seated_quiz
    assert (
        EventRegistration.objects.filter(event=quiz.event, status="attended").count()
        == 6
    )
    assert QuizTableMembership.objects.filter(table__quiz=quiz).count() == 6
    assert QuizRotationSchedule.objects.filter(quiz=quiz, round_number=0).count() == 6


def test_add_guests_is_idempotent(seated_quiz):
    from crush_lu.services.quiz_rehearsal import add_rehearsal_guests

    add_rehearsal_guests(seated_quiz, 6)
    assert EventRegistration.objects.filter(event=seated_quiz.event).count() == 6
    assert QuizTableMembership.objects.filter(table__quiz=seated_quiz).count() == 6


@pytest.mark.parametrize("state", ["draft", "active", "paused", "finished"])
def test_reset_works_from_any_state(seated_quiz, state):
    from crush_lu.services.quiz_rehearsal import reset_quiz_for_rehearsal

    quiz = seated_quiz
    quiz.status = state
    quiz.current_question_index = 4
    quiz.save()

    result = reset_quiz_for_rehearsal(quiz, reseed=True)

    assert result["status"] == "draft"
    quiz.refresh_from_db()
    assert quiz.status == "draft"
    assert quiz.current_round_id is None
    assert quiz.current_question_index == -1
    assert not TableRoundScore.objects.filter(quiz=quiz).exists()
    # Fresh seating for the same attended guests.
    assert QuizTableMembership.objects.filter(table__quiz=quiz).count() == 6


def test_reset_without_reseed_clears_seating(seated_quiz):
    from crush_lu.services.quiz_rehearsal import reset_quiz_for_rehearsal

    reset_quiz_for_rehearsal(seated_quiz, reseed=False)
    assert not QuizTableMembership.objects.filter(table__quiz=seated_quiz).exists()
    assert not QuizRotationSchedule.objects.filter(quiz=seated_quiz).exists()


def test_service_refuses_off_staging(quiz_event, monkeypatch):
    from crush_lu.services.quiz_rehearsal import (
        add_rehearsal_guests,
        reset_quiz_for_rehearsal,
    )

    monkeypatch.setenv("DJANGO_ENV", "production")
    with pytest.raises(PermissionDenied):
        reset_quiz_for_rehearsal(quiz_event)
    with pytest.raises(PermissionDenied):
        add_rehearsal_guests(quiz_event, 2)


def test_endpoints_404_off_staging(quiz_event, coach_user, monkeypatch):
    monkeypatch.setenv("DJANGO_ENV", "production")
    client = Client()
    client.force_login(coach_user)
    for path in ("reset", "add-guests"):
        resp = client.post(f"/api/quiz/{quiz_event.id}/rehearsal/{path}/")
        assert resp.status_code == 404


def test_endpoints_refuse_non_host(quiz_event, quiz_user, staging):
    client = Client()
    client.force_login(quiz_user)
    for path in ("reset", "add-guests"):
        resp = client.post(f"/api/quiz/{quiz_event.id}/rehearsal/{path}/")
        assert resp.status_code == 403


def test_host_can_reset_and_add_guests(quiz_event, coach_user, staging):
    quiz_event.num_tables = 3
    quiz_event.status = "active"
    quiz_event.save()
    client = Client()
    client.force_login(coach_user)

    resp = client.post(
        f"/api/quiz/{quiz_event.id}/rehearsal/add-guests/",
        data=json.dumps({"count": 5}),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    assert resp.json()["guests"] == 5

    resp = client.post(f"/api/quiz/{quiz_event.id}/rehearsal/reset/")
    assert resp.status_code == 200, resp.content
    quiz_event.refresh_from_db()
    assert quiz_event.status == "draft"
    assert QuizTableMembership.objects.filter(table__quiz=quiz_event).count() == 5


def test_create_rehearsal_command_refuses_off_staging(monkeypatch):
    monkeypatch.setenv("DJANGO_ENV", "production")
    with pytest.raises(CommandError):
        call_command("create_quiz_rehearsal")


def test_create_rehearsal_command_is_idempotent(staging):
    User.objects.create_superuser("rehearsal-admin", "ra@example.com", "x")

    call_command("create_quiz_rehearsal", guests=6, tables=3)
    call_command("create_quiz_rehearsal", guests=6, tables=3)

    quiz = QuizEvent.objects.get(event__title="Quiz Night REHEARSAL")
    assert quiz.event.registration_fee == 0
    assert quiz.rounds.count() > 0
    assert (
        EventRegistration.objects.filter(event=quiz.event, status="attended").count()
        == 6
    )
    assert QuizTableMembership.objects.filter(table__quiz=quiz).count() == 6


# --- Any coach can host the permanent rehearsal event -----------------------


def _make_coach(username):
    from crush_lu.models import CrushCoach

    user = User.objects.create_user(username, f"{username}@test.com", "x")
    _quiz_tests._grant_consent(user)
    CrushCoach.objects.create(user=user, is_active=True)
    return user


def test_any_coach_can_host_rehearsal_but_not_real_events(
    quiz_event, coach_user, staging
):
    from crush_lu.api_quiz import _is_quiz_host

    other_coach = _make_coach("otherrehearsalcoach")

    # A normal quiz: an unassigned coach is NOT a host.
    assert not _is_quiz_host(quiz_event, other_coach)

    # The rehearsal quiz: any active coach is.
    quiz_event.event.title = "Quiz Night REHEARSAL"
    quiz_event.event.save(update_fields=["title"])
    assert _is_quiz_host(quiz_event, other_coach)

    # Non-coaches never are.
    plain = User.objects.create_user("plainuser", "plain@test.com", "x")
    assert not _is_quiz_host(quiz_event, plain)


def test_rehearsal_host_rule_off_on_production(quiz_event, monkeypatch):
    from crush_lu.api_quiz import _is_quiz_host

    monkeypatch.setenv("DJANGO_ENV", "production")
    monkeypatch.setattr("django.conf.settings.DEBUG", False)
    other_coach = _make_coach("prodcoach")
    quiz_event.event.title = "Quiz Night REHEARSAL"
    quiz_event.event.save(update_fields=["title"])
    assert not _is_quiz_host(quiz_event, other_coach)


def test_coach_entry_creates_reuses_and_opens_host_panel(staging):
    coach = _make_coach("entrycoach")
    client = Client()
    client.force_login(coach)

    first = client.get("/en/coach/quiz-rehearsal/")
    assert first.status_code == 302
    assert first["Location"].endswith("/quiz/coach/")

    second = client.get("/en/coach/quiz-rehearsal/")
    assert second["Location"] == first["Location"]

    from crush_lu.models.events import MeetupEvent

    assert MeetupEvent.objects.filter(title="Quiz Night REHEARSAL").count() == 1
    # The host panel itself opens for this coach, who was never assigned by hand.
    panel = client.get(first["Location"])
    assert panel.status_code == 200
    # A second coach can open the same panel.
    second_coach = _make_coach("secondentrycoach")
    client2 = Client()
    client2.force_login(second_coach)
    assert client2.get(first["Location"]).status_code == 200


def test_coach_entry_404_off_staging(monkeypatch):
    monkeypatch.setenv("DJANGO_ENV", "production")
    monkeypatch.setattr("django.conf.settings.DEBUG", False)
    coach = _make_coach("prodentrycoach")
    client = Client()
    client.force_login(coach)
    assert client.get("/en/coach/quiz-rehearsal/").status_code == 404
