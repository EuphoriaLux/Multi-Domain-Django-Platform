"""Tests for the live quiz feature (models, consumer, API)."""
import pytest
from datetime import date, timedelta
from django.contrib.auth.models import User
from django.test import RequestFactory
from django.utils import timezone
from rest_framework.test import APIClient

from crush_lu.models.quiz import (
    IndividualScore,
    QuizEvent,
    QuizQuestion,
    QuizRound,
    QuizTable,
    QuizTableMembership,
)


def _grant_consent(user):
    """Grant GDPR consent for a user (created by signal on user creation)."""
    from crush_lu.models.profiles import UserDataConsent

    consent, _ = UserDataConsent.objects.get_or_create(user=user)
    consent.crushlu_consent_given = True
    consent.save()


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def quiz_user(db):
    user = User.objects.create_user(
        username="quizplayer@test.com",
        email="quizplayer@test.com",
        password="testpass123",
    )
    _grant_consent(user)
    return user


@pytest.fixture
def coach_user(db):
    user = User.objects.create_user(
        username="quizcoach@test.com",
        email="quizcoach@test.com",
        password="testpass123",
        is_staff=True,
    )
    _grant_consent(user)
    return user


@pytest.fixture
def quiz_event(db, coach_user):
    from crush_lu.models import MeetupEvent

    event = MeetupEvent.objects.create(
        title="Quiz Night",
        description="A fun quiz event",
        event_type="mixer",
        date_time=timezone.now() + timedelta(days=1),
        location="Luxembourg City",
        address="1 Place d'Armes",
        max_participants=30,
        registration_deadline=timezone.now() + timedelta(hours=12),
        is_published=True,
    )
    quiz = QuizEvent.objects.create(
        event=event,
        status="draft",
        created_by=coach_user,
    )
    return quiz


@pytest.fixture
def quiz_round(quiz_event):
    return QuizRound.objects.create(
        quiz=quiz_event,
        title="Round 1: Icebreaker",
        sort_order=0,
        time_per_question=30,
    )


@pytest.fixture
def quiz_questions(quiz_round):
    q1 = QuizQuestion.objects.create(
        round=quiz_round,
        text="What is the capital of Luxembourg?",
        question_type="multiple_choice",
        choices=[
            {"text": "Luxembourg City", "is_correct": True},
            {"text": "Esch-sur-Alzette", "is_correct": False},
            {"text": "Differdange", "is_correct": False},
        ],
        sort_order=0,
        points=10,
    )
    q2 = QuizQuestion.objects.create(
        round=quiz_round,
        text="Is Luxembourg a Grand Duchy?",
        question_type="true_false",
        choices=[
            {"text": "True", "is_correct": True},
            {"text": "False", "is_correct": False},
        ],
        sort_order=1,
        points=5,
    )
    return [q1, q2]


@pytest.fixture
def quiz_table(quiz_event, quiz_user):
    table = QuizTable.objects.create(
        quiz=quiz_event,
        table_number=1,
    )
    QuizTableMembership.objects.create(table=table, user=quiz_user)
    return table


# ============================================================================
# MODEL TESTS
# ============================================================================


class TestQuizEventModel:
    def test_create_quiz_event(self, quiz_event):
        assert quiz_event.status == "draft"
        assert str(quiz_event).startswith("Quiz for")

    def test_is_active(self, quiz_event):
        assert not quiz_event.is_active
        quiz_event.status = "active"
        quiz_event.save()
        assert quiz_event.is_active

    def test_get_current_question_no_round(self, quiz_event):
        assert quiz_event.get_current_question() is None

    def test_get_current_question(self, quiz_event, quiz_round, quiz_questions):
        quiz_event.current_round = quiz_round
        quiz_event.current_question_index = 0
        quiz_event.save()
        question = quiz_event.get_current_question()
        assert question is not None
        assert question.text == "What is the capital of Luxembourg?"

    def test_get_current_question_out_of_range(
        self, quiz_event, quiz_round, quiz_questions
    ):
        quiz_event.current_round = quiz_round
        quiz_event.current_question_index = 99
        quiz_event.save()
        assert quiz_event.get_current_question() is None


class TestQuizRoundModel:
    def test_create_round(self, quiz_round):
        assert quiz_round.time_per_question == 30
        assert "Round 1" in str(quiz_round)


class TestQuizQuestionModel:
    def test_create_question(self, quiz_questions):
        assert len(quiz_questions) == 2
        assert quiz_questions[0].points == 10

    def test_choices_json(self, quiz_questions):
        q = quiz_questions[0]
        assert len(q.choices) == 3
        correct = [c for c in q.choices if c.get("is_correct")]
        assert len(correct) == 1
        assert correct[0]["text"] == "Luxembourg City"


class TestQuizTableModel:
    def test_create_table(self, quiz_table, quiz_user):
        assert quiz_table.table_number == 1
        assert quiz_user in quiz_table.members.all()

    def test_table_unique_together(self, quiz_event, quiz_table):
        with pytest.raises(Exception):
            QuizTable.objects.create(quiz=quiz_event, table_number=1)

    def test_get_total_score_empty(self, quiz_table):
        assert quiz_table.get_total_score() == 0

    def test_get_total_score(self, quiz_table, quiz_event, quiz_questions, quiz_user):
        IndividualScore.objects.create(
            quiz=quiz_event,
            user=quiz_user,
            question=quiz_questions[0],
            answer="Luxembourg City",
            is_correct=True,
            points_earned=10,
        )
        assert quiz_table.get_total_score() == 10


class TestIndividualScoreModel:
    def test_create_score(self, quiz_event, quiz_user, quiz_questions):
        score = IndividualScore.objects.create(
            quiz=quiz_event,
            user=quiz_user,
            question=quiz_questions[0],
            answer="Luxembourg City",
            is_correct=True,
            points_earned=10,
        )
        assert score.points_earned == 10
        assert score.is_correct

    def test_unique_constraint(self, quiz_event, quiz_user, quiz_questions):
        IndividualScore.objects.create(
            quiz=quiz_event,
            user=quiz_user,
            question=quiz_questions[0],
            answer="Luxembourg City",
            is_correct=True,
            points_earned=10,
        )
        with pytest.raises(Exception):
            IndividualScore.objects.create(
                quiz=quiz_event,
                user=quiz_user,
                question=quiz_questions[0],
                answer="Esch",
                is_correct=False,
                points_earned=0,
            )


# ============================================================================
# API TESTS
# ============================================================================


@pytest.mark.django_db
class TestQuizAPI:
    def test_quiz_state_endpoint(self, quiz_event, quiz_user):
        client = APIClient()
        client.force_authenticate(user=quiz_user)
        response = client.get(f"/api/quiz/{quiz_event.id}/state/")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "draft"

    def test_quiz_state_not_found(self, quiz_user):
        client = APIClient()
        client.force_authenticate(user=quiz_user)
        response = client.get("/api/quiz/9999/state/")
        assert response.status_code == 404

    def test_quiz_tables_endpoint(self, quiz_event, quiz_table, quiz_user):
        client = APIClient()
        client.force_authenticate(user=quiz_user)
        response = client.get(f"/api/quiz/{quiz_event.id}/tables/")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["table_number"] == 1

    def test_quiz_state_requires_auth(self, quiz_event):
        client = APIClient()
        response = client.get(f"/api/quiz/{quiz_event.id}/state/")
        assert response.status_code in (401, 403)


# ============================================================================
# VIEW TESTS
# ============================================================================


@pytest.mark.django_db
class TestQuizViews:
    def test_quiz_live_view(self, client, quiz_event, quiz_user):
        client.force_login(quiz_user)
        response = client.get(
            f"/en/events/{quiz_event.event_id}/quiz/"
        )
        assert response.status_code == 200
        assert b"quizLive" in response.content

    def test_quiz_coach_view_requires_coach(self, client, quiz_event, quiz_user):
        client.force_login(quiz_user)
        response = client.get(
            f"/en/events/{quiz_event.event_id}/quiz/coach/"
        )
        assert response.status_code == 404  # Not a coach

    def test_quiz_coach_view_staff(self, client, quiz_event, coach_user):
        client.force_login(coach_user)
        response = client.get(
            f"/en/events/{quiz_event.event_id}/quiz/coach/"
        )
        assert response.status_code == 200
        assert b"quizCoach" in response.content
