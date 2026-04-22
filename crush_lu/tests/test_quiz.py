"""Tests for the live quiz feature (models, consumer, API, rotation)."""

import pytest
from datetime import date, timedelta
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APIClient

from crush_lu.models.quiz import (
    IndividualScore,
    QuizEvent,
    QuizQuestion,
    QuizRotationSchedule,
    QuizRound,
    QuizTable,
    QuizTableMembership,
    TableRoundScore,
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
        event_type="quiz_night",
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
def bonus_round(quiz_event):
    return QuizRound.objects.create(
        quiz=quiz_event,
        title="Bonus Round",
        sort_order=1,
        time_per_question=20,
        is_bonus=True,
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
    q3 = QuizQuestion.objects.create(
        round=quiz_round,
        text="Name a famous Luxembourg dish",
        question_type="open_ended",
        correct_answer="Judd mat Gaardebounen",
        sort_order=2,
        points=15,
    )
    return [q1, q2, q3]


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


class TestGetRoundNumber:
    """Test get_round_number with various sort_order configurations."""

    def test_unique_sort_orders(self, quiz_event):
        """Rounds with unique sort_orders get sequential round_numbers."""
        r0 = QuizRound.objects.create(quiz=quiz_event, title="R1", sort_order=0)
        r1 = QuizRound.objects.create(quiz=quiz_event, title="R2", sort_order=1)
        r2 = QuizRound.objects.create(quiz=quiz_event, title="R3", sort_order=2)
        r3 = QuizRound.objects.create(quiz=quiz_event, title="R4", sort_order=3)

        assert quiz_event.get_round_number(r0) == 0
        assert quiz_event.get_round_number(r1) == 1
        assert quiz_event.get_round_number(r2) == 2
        assert quiz_event.get_round_number(r3) == 3

    def test_duplicate_sort_orders(self, quiz_event):
        """Rounds with all-zero sort_orders still get unique round_numbers via pk tiebreaker."""
        r0 = QuizRound.objects.create(quiz=quiz_event, title="R1", sort_order=0)
        r1 = QuizRound.objects.create(quiz=quiz_event, title="R2", sort_order=0)
        r2 = QuizRound.objects.create(quiz=quiz_event, title="R3", sort_order=0)
        r3 = QuizRound.objects.create(quiz=quiz_event, title="R4", sort_order=0)

        assert quiz_event.get_round_number(r0) == 0
        assert quiz_event.get_round_number(r1) == 1
        assert quiz_event.get_round_number(r2) == 2
        assert quiz_event.get_round_number(r3) == 3

    def test_gapped_sort_orders(self, quiz_event):
        """Rounds with gapped sort_orders (0, 5, 10, 15) get sequential round_numbers."""
        r0 = QuizRound.objects.create(quiz=quiz_event, title="R1", sort_order=0)
        r1 = QuizRound.objects.create(quiz=quiz_event, title="R2", sort_order=5)
        r2 = QuizRound.objects.create(quiz=quiz_event, title="R3", sort_order=10)
        r3 = QuizRound.objects.create(quiz=quiz_event, title="R4", sort_order=15)

        assert quiz_event.get_round_number(r0) == 0
        assert quiz_event.get_round_number(r1) == 1
        assert quiz_event.get_round_number(r2) == 2
        assert quiz_event.get_round_number(r3) == 3

    def test_no_round_returns_zero(self, quiz_event):
        assert quiz_event.get_round_number(None) == 0

    def test_current_round_default(self, quiz_event):
        r0 = QuizRound.objects.create(quiz=quiz_event, title="R1", sort_order=0)
        r1 = QuizRound.objects.create(quiz=quiz_event, title="R2", sort_order=0)
        quiz_event.current_round = r1
        quiz_event.save()
        # Uses current_round when no arg passed
        assert quiz_event.get_round_number() == 1


class TestAdvanceRoundAndRotate:
    """Test that advance_round_and_rotate progresses through all rounds correctly."""

    def test_four_rounds_same_sort_order_no_premature_finish(self, quiz_event):
        """With 4 rounds all at sort_order=0, rotating should progress through all 4."""
        rounds = []
        for i in range(4):
            r = QuizRound.objects.create(
                quiz=quiz_event, title=f"Round {i+1}", sort_order=0
            )
            for j in range(6):
                QuizQuestion.objects.create(
                    round=r,
                    text=f"Q{j+1} of round {i+1}",
                    question_type="multiple_choice",
                    choices=[
                        {"text": "A", "is_correct": True},
                        {"text": "B", "is_correct": False},
                    ],
                    sort_order=j,
                    points=10,
                )
            rounds.append(r)

        # Start quiz at first round
        quiz_event.current_round = rounds[0]
        quiz_event.current_question_index = 5  # All questions done
        quiz_event.status = "active"
        quiz_event.save()

        # Simulate 3 rotations (round 1→2, 2→3, 3→4)
        for expected_idx in range(1, 4):
            quiz_event.refresh_from_db()
            current_sort = quiz_event.current_round.sort_order
            current_pk = quiz_event.current_round.pk

            # Find next round using the fixed logic
            next_round = (
                quiz_event.rounds.filter(sort_order__gt=current_sort)
                .order_by("sort_order", "pk")
                .first()
            )
            if not next_round:
                next_round = (
                    quiz_event.rounds.filter(sort_order=current_sort, pk__gt=current_pk)
                    .order_by("pk")
                    .first()
                )

            assert next_round is not None, (
                f"Expected to find round {expected_idx+1}, "
                f"but got None after round {expected_idx}"
            )
            assert next_round.pk == rounds[expected_idx].pk

            quiz_event.current_round = next_round
            quiz_event.current_question_index = -1
            quiz_event.status = "active"
            quiz_event.save()

        # After round 4, no more rounds should exist
        quiz_event.refresh_from_db()
        current_sort = quiz_event.current_round.sort_order
        current_pk = quiz_event.current_round.pk

        next_round = (
            quiz_event.rounds.filter(sort_order__gt=current_sort)
            .order_by("sort_order", "pk")
            .first()
        )
        if not next_round:
            next_round = (
                quiz_event.rounds.filter(sort_order=current_sort, pk__gt=current_pk)
                .order_by("pk")
                .first()
            )
        assert next_round is None, "Should be finished after 4 rounds"

    def test_four_rounds_unique_sort_orders(self, quiz_event):
        """With proper sort_orders (0,1,2,3), rotation works correctly."""
        rounds = []
        for i in range(4):
            r = QuizRound.objects.create(
                quiz=quiz_event, title=f"Round {i+1}", sort_order=i
            )
            rounds.append(r)

        quiz_event.current_round = rounds[0]
        quiz_event.status = "active"
        quiz_event.save()

        for expected_idx in range(1, 4):
            quiz_event.refresh_from_db()
            current_sort = quiz_event.current_round.sort_order

            next_round = (
                quiz_event.rounds.filter(sort_order__gt=current_sort)
                .order_by("sort_order", "pk")
                .first()
            )
            assert next_round is not None
            assert next_round.pk == rounds[expected_idx].pk

            quiz_event.current_round = next_round
            quiz_event.save()


class TestLastRoundDetection:
    """Test that the quiz correctly detects when the last round's questions are done."""

    def test_has_next_round_true(self, quiz_event, quiz_round, bonus_round):
        """When on first round with a second round existing, has_next_round is True."""
        quiz_event.current_round = quiz_round
        quiz_event.save()

        current_sort = quiz_event.current_round.sort_order
        current_pk = quiz_event.current_round.pk
        has_next = (
            quiz_event.rounds.filter(sort_order__gt=current_sort).exists()
            or quiz_event.rounds.filter(
                sort_order=current_sort, pk__gt=current_pk
            ).exists()
        )
        assert has_next is True

    def test_has_next_round_false_on_last(self, quiz_event, quiz_round, bonus_round):
        """When on the last round, has_next_round is False."""
        quiz_event.current_round = bonus_round  # sort_order=1, highest
        quiz_event.save()

        current_sort = quiz_event.current_round.sort_order
        current_pk = quiz_event.current_round.pk
        has_next = (
            quiz_event.rounds.filter(sort_order__gt=current_sort).exists()
            or quiz_event.rounds.filter(
                sort_order=current_sort, pk__gt=current_pk
            ).exists()
        )
        assert has_next is False

    def test_single_round_is_last(self, quiz_event):
        """A quiz with only one round should detect it as last."""
        single_round = QuizRound.objects.create(
            quiz=quiz_event, title="Only Round", sort_order=0
        )
        quiz_event.current_round = single_round
        quiz_event.save()

        current_sort = quiz_event.current_round.sort_order
        current_pk = quiz_event.current_round.pk
        has_next = (
            quiz_event.rounds.filter(sort_order__gt=current_sort).exists()
            or quiz_event.rounds.filter(
                sort_order=current_sort, pk__gt=current_pk
            ).exists()
        )
        assert has_next is False


class TestQuizRoundModel:
    def test_create_round(self, quiz_round):
        assert quiz_round.time_per_question == 30
        assert "Round 1" in str(quiz_round)

    def test_bonus_round(self, bonus_round):
        assert bonus_round.is_bonus is True


class TestQuizQuestionModel:
    def test_create_question(self, quiz_questions):
        assert len(quiz_questions) == 3
        assert quiz_questions[0].points == 10

    def test_choices_json(self, quiz_questions):
        q = quiz_questions[0]
        assert len(q.choices) == 3
        correct = [c for c in q.choices if c.get("is_correct")]
        assert len(correct) == 1
        assert correct[0]["text"] == "Luxembourg City"

    def test_open_ended_question(self, quiz_questions):
        q = quiz_questions[2]
        assert q.question_type == "open_ended"
        assert q.correct_answer == "Judd mat Gaardebounen"


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
        # get_total_score uses TableRoundScore (table-level results)
        TableRoundScore.objects.create(
            quiz=quiz_event,
            table=quiz_table,
            question=quiz_questions[0],
            is_correct=True,
        )
        # Question has 10 points by default
        assert quiz_table.get_total_score() == 10

    def test_get_total_score_bonus_round(self, quiz_event, quiz_user):
        """Bonus round questions should have doubled points in table score."""
        bonus = QuizRound.objects.create(
            quiz=quiz_event, title="Bonus", sort_order=5, is_bonus=True
        )
        q = QuizQuestion.objects.create(
            round=bonus,
            text="Bonus Q",
            question_type="multiple_choice",
            choices=[{"text": "A", "is_correct": True}],
            sort_order=0,
            points=10,
        )
        table = QuizTable.objects.create(quiz=quiz_event, table_number=99)
        from crush_lu.models.quiz import QuizTableMembership

        QuizTableMembership.objects.create(table=table, user=quiz_user)
        TableRoundScore.objects.create(
            quiz=quiz_event,
            table=table,
            question=q,
            is_correct=True,
        )
        # 10 points * 2 (bonus) = 20
        assert table.get_total_score() == 20

    def test_get_total_score_wrong_answer(self, quiz_table, quiz_event, quiz_questions):
        """Wrong answers should not contribute to table score."""
        TableRoundScore.objects.create(
            quiz=quiz_event,
            table=quiz_table,
            question=quiz_questions[0],
            is_correct=False,
        )
        assert quiz_table.get_total_score() == 0


class TestTableRoundScoreModel:
    def test_create_table_round_score(self, quiz_event, quiz_table, quiz_questions):
        score = TableRoundScore.objects.create(
            quiz=quiz_event,
            table=quiz_table,
            question=quiz_questions[0],
            is_correct=True,
        )
        assert score.is_correct
        assert "correct" in str(score)

    def test_unique_constraint(self, quiz_event, quiz_table, quiz_questions):
        TableRoundScore.objects.create(
            quiz=quiz_event,
            table=quiz_table,
            question=quiz_questions[0],
            is_correct=True,
        )
        with pytest.raises(Exception):
            TableRoundScore.objects.create(
                quiz=quiz_event,
                table=quiz_table,
                question=quiz_questions[0],
                is_correct=False,
            )


class TestQuizRotationScheduleModel:
    def test_create_rotation_entry(self, quiz_event, quiz_table, quiz_user):
        entry = QuizRotationSchedule.objects.create(
            quiz=quiz_event,
            round_number=0,
            table=quiz_table,
            user=quiz_user,
            role="anchor",
            rotation_group="",
        )
        assert entry.role == "anchor"
        assert "Round 0" in str(entry)

    def test_unique_per_round(self, quiz_event, quiz_table, quiz_user):
        QuizRotationSchedule.objects.create(
            quiz=quiz_event,
            round_number=0,
            table=quiz_table,
            user=quiz_user,
            role="anchor",
        )
        with pytest.raises(Exception):
            # Same user, same round — should fail
            table2 = QuizTable.objects.create(quiz=quiz_event, table_number=2)
            QuizRotationSchedule.objects.create(
                quiz=quiz_event,
                round_number=0,
                table=table2,
                user=quiz_user,
                role="rotator",
            )


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
# ROTATION ALGORITHM TESTS
# ============================================================================


@pytest.mark.django_db
class TestRotationAlgorithm:
    def _make_users(self, prefix, count):
        users = []
        for i in range(count):
            u = User.objects.create_user(
                username=f"{prefix}{i}@test.com",
                email=f"{prefix}{i}@test.com",
                password="testpass123",
            )
            _grant_consent(u)
            users.append(u)
        return users

    def test_basic_rotation_6_tables(self):
        """24 participants (12M, 12F) → 6 tables, 3 rounds."""
        from crush_lu.services.quiz_rotation import generate_rotation_schedule

        men = self._make_users("man", 12)
        women = self._make_users("woman", 12)

        result = generate_rotation_schedule(men, women, num_rounds=3)
        schedule = result["schedule"]
        assert len(schedule) > 0

        # Verify men never move
        for entry in schedule:
            if entry["role"] == "anchor":
                # All rounds should have same table for this man
                man_entries = [e for e in schedule if e["user"] == entry["user"]]
                tables = set(e["table_number"] for e in man_entries)
                assert len(tables) == 1, "Anchor should stay at same table"

    def test_women_visit_all_tables(self):
        """Every woman visits every table over N rounds."""
        from crush_lu.services.quiz_rotation import generate_rotation_schedule

        men = self._make_users("m", 8)
        women = self._make_users("w", 8)
        num_tables = 4

        result = generate_rotation_schedule(men, women, num_rounds=num_tables)
        schedule = result["schedule"]

        # Check each woman in group A visits all 4 tables
        for w in women[:num_tables]:
            w_entries = [
                e for e in schedule if e["user"] == w and e["role"] == "rotator"
            ]
            tables_visited = set(e["table_number"] for e in w_entries)
            assert (
                len(tables_visited) == num_tables
            ), f"Woman should visit all {num_tables} tables, visited {tables_visited}"

    def test_no_duplicate_women_pairing(self):
        """No two women from the same group appear at the same table in the same round."""
        from crush_lu.services.quiz_rotation import generate_rotation_schedule

        men = self._make_users("m", 8)
        women = self._make_users("w", 8)
        result = generate_rotation_schedule(men, women, num_rounds=4)
        schedule = result["schedule"]

        # For each round+table, at most 1 woman per rotation group
        from collections import defaultdict

        round_table_groups = defaultdict(list)
        for e in schedule:
            if e["role"] == "rotator":
                key = (e["round_number"], e["table_number"], e["rotation_group"])
                round_table_groups[key].append(e["user"])

        for key, users in round_table_groups.items():
            assert len(users) == 1, (
                f"Round {key[0]}, Table {key[1]}, Group {key[2]}: "
                f"got {len(users)} women, expected 1"
            )

    def test_men_anchored(self):
        """Men are assigned as anchors and don't move."""
        from crush_lu.services.quiz_rotation import generate_rotation_schedule

        men = self._make_users("m", 4)
        women = self._make_users("w", 4)
        result = generate_rotation_schedule(men, women, num_rounds=2)
        schedule = result["schedule"]

        for man in men:
            man_entries = [e for e in schedule if e["user"] == man]
            assert all(e["role"] == "anchor" for e in man_entries)
            tables = set(e["table_number"] for e in man_entries)
            assert len(tables) == 1

    def test_validation_too_few_tables_explicit(self):
        """Explicitly requesting 1 table should fail."""
        from crush_lu.services.quiz_rotation import generate_rotation_schedule

        men = self._make_users("m", 4)
        women = self._make_users("w", 4)

        with pytest.raises(ValidationError, match="at least 2 tables"):
            generate_rotation_schedule(men, women, num_tables=1)

    def test_validation_too_few_participants(self):
        """Need at least 4 participants."""
        from crush_lu.services.quiz_rotation import generate_rotation_schedule

        men = self._make_users("m", 2)
        women = self._make_users("w", 1)

        with pytest.raises(ValidationError, match="at least 4 participants"):
            generate_rotation_schedule(men, women, num_tables=2)

    def test_extra_women_all_seated(self):
        """Extra women beyond groups A/B are seated in spillover group C."""
        from crush_lu.services.quiz_rotation import generate_rotation_schedule

        men = self._make_users("m", 4)
        women = self._make_users("w", 6)

        result = generate_rotation_schedule(men, women)
        schedule = result["schedule"]
        # All 6 women should appear in the schedule
        scheduled_women = set(e["user"] for e in schedule if e["role"] == "rotator")
        assert len(scheduled_women) == 6
        assert any("spillover" in w for w in result["warnings"])

    def test_all_14_participants_seated(self):
        """7 anchors + 7 rotators with 3 tables → all 14 distributed."""
        from crush_lu.services.quiz_rotation import generate_rotation_schedule

        men = self._make_users("m14", 7)
        women = self._make_users("w14", 7)

        result = generate_rotation_schedule(men, women, num_rounds=3)
        schedule = result["schedule"]
        all_users = set(e["user"] for e in schedule)
        assert len(all_users) == 14
        assert result["num_tables"] == 3

    def test_num_tables_zero_fallback(self):
        """num_tables=0 should auto-calculate, not error."""
        from crush_lu.services.quiz_rotation import generate_rotation_schedule

        men = self._make_users("mz", 6)
        women = self._make_users("wz", 6)

        result = generate_rotation_schedule(men, women, num_tables=0)
        assert result["num_tables"] == 3  # 6 // 2 = 3

    def test_split_participants_by_gender(self):
        """NB/O/P genders go to whichever pool is smaller."""
        from crush_lu.models.profiles import CrushProfile
        from crush_lu.models.events import EventRegistration
        from crush_lu.models import MeetupEvent
        from crush_lu.services.quiz_rotation import split_participants_by_gender

        event = MeetupEvent.objects.create(
            title="Test",
            event_type="quiz_night",
            date_time=timezone.now() + timedelta(days=1),
            location="Test",
            address="Test",
            max_participants=20,
            registration_deadline=timezone.now() + timedelta(hours=12),
            is_published=True,
        )

        # Create users with profiles
        users_data = [
            ("male1", "M"),
            ("male2", "M"),
            ("female1", "F"),
            ("female2", "F"),
            ("nb1", "NB"),
        ]

        registrations = []
        for username, gender in users_data:
            u = User.objects.create_user(
                username=f"{username}@test.com",
                email=f"{username}@test.com",
                password="test",
            )
            _grant_consent(u)
            CrushProfile.objects.create(
                user=u, gender=gender, date_of_birth=date(1990, 1, 1)
            )
            reg = EventRegistration.objects.create(
                event=event, user=u, status="confirmed"
            )
            registrations.append(reg)

        regs = EventRegistration.objects.filter(event=event).select_related(
            "user__crushprofile"
        )

        men, women = split_participants_by_gender(regs)
        # 2M + 2F + 1NB: NB should go to men (equal pools, men <= women)
        # Actually: men=2, women=2, NB goes to men (2 <= 2 → True)
        assert len(men) == 3
        assert len(women) == 2

    def test_32_participants_8_tables(self):
        """Stress test: 32 participants (16M, 16F) → 8 tables."""
        from crush_lu.services.quiz_rotation import generate_rotation_schedule

        men = self._make_users("m32", 16)
        women = self._make_users("w32", 16)

        result = generate_rotation_schedule(men, women, num_rounds=8)
        schedule = result["schedule"]
        assert len(schedule) > 0

        # Verify all anchors
        anchor_entries = [e for e in schedule if e["role"] == "anchor"]
        assert len(set(e["user"].id for e in anchor_entries)) == 16


# ============================================================================
# ROTATION REGISTRATION FILTERING TESTS
# ============================================================================


@pytest.mark.django_db
class TestRotationRegistrationFiltering:
    """Regression tests for the no-show rotation bug.

    `generate_rotation_rounds` must only rotate registrations with
    status='attended'. Still-'confirmed' registrations stay as-is so
    late arrivals can still QR check-in (views_checkin.py requires
    status='confirmed'); the admin action is the explicit path for
    marking no-shows after the fact.
    """

    def _make_user_with_profile(self, username, gender):
        u = User.objects.create_user(
            username=f"{username}@test.com",
            email=f"{username}@test.com",
            password="testpass123",
        )
        _grant_consent(u)
        _create_profile(u, gender)
        return u

    def _make_attendees(self, event, men_count, women_count):
        """Create users + profiles + EventRegistration(status='attended')."""
        from crush_lu.models.events import EventRegistration

        regs = []
        for i in range(men_count):
            u = self._make_user_with_profile(f"rotm{i}", "M")
            regs.append(
                EventRegistration.objects.create(
                    event=event, user=u, status="attended"
                )
            )
        for i in range(women_count):
            u = self._make_user_with_profile(f"rotw{i}", "F")
            regs.append(
                EventRegistration.objects.create(
                    event=event, user=u, status="attended"
                )
            )
        return regs

    def _add_rounds(self, quiz, count):
        for i in range(count):
            QuizRound.objects.create(
                quiz=quiz,
                title=f"R{i + 1}",
                sort_order=i,
                time_per_question=30,
            )

    def test_no_show_excluded_from_rotation_rounds(self, quiz_event):
        """Reproduces the reported bug: 24 registrations (12M/12F), 3 still
        'confirmed' at rotation time, must be excluded from the schedule.
        Confirmed registrations are left untouched so late arrivals can
        still QR check-in."""
        from crush_lu.services.quiz_rotation import generate_rotation_rounds
        from crush_lu.models.events import EventRegistration

        quiz_event.num_tables = 6
        quiz_event.save()
        self._add_rounds(quiz_event, 3)

        regs = self._make_attendees(quiz_event.event, men_count=12, women_count=12)

        # 3 people never checked in: 1 man, 2 women
        no_show_regs = [regs[0], regs[12], regs[13]]
        for r in no_show_regs:
            r.status = "confirmed"
            r.save()
        no_show_user_ids = {r.user_id for r in no_show_regs}

        result = generate_rotation_rounds(quiz_event)

        # None of the no-show users appear anywhere in the rotation schedule
        scheduled_user_ids = set(
            QuizRotationSchedule.objects.filter(quiz=quiz_event).values_list(
                "user_id", flat=True
            )
        )
        assert scheduled_user_ids.isdisjoint(no_show_user_ids), (
            "No-show users must not appear in the rotation schedule"
        )

        # 21 users rotated (remaining attended)
        assert result["anchors"] + result["rotators"] == 21

        # Confirmed rows are preserved (not auto-flipped) so late arrivals
        # can still QR check-in through views_checkin.py
        for r in no_show_regs:
            r.refresh_from_db()
            assert r.status == "confirmed", (
                "rotation must not touch registration status; "
                "late arrivals need status='confirmed' for QR check-in"
            )

        # Totals by status: 21 attended, 3 confirmed, 0 no_show
        base_q = EventRegistration.objects.filter(event=quiz_event.event)
        assert base_q.filter(status="attended").count() == 21
        assert base_q.filter(status="confirmed").count() == 3
        assert base_q.filter(status="no_show").count() == 0

    def test_late_arrival_included_when_attended(self, quiz_event):
        """A user who checks in after round 0 (status='attended') is
        rotated; a still-'confirmed' straggler is excluded but NOT
        auto-flipped — they must remain check-in-eligible."""
        from crush_lu.services.quiz_rotation import generate_rotation_rounds
        from crush_lu.models.events import EventRegistration

        # num_tables=3 avoids the num_tables==2 special case in
        # generate_rotation_schedule which creates duplicate (round, user)
        # entries when len(group_a) is odd.
        quiz_event.num_tables = 3
        quiz_event.save()
        self._add_rounds(quiz_event, 3)

        # 6M + 6F = 12, all starting as attended
        regs = self._make_attendees(quiz_event.event, men_count=6, women_count=6)
        # regs[0..5] = men m0..m5, regs[6..11] = women f0..f5
        late_arrival_reg = regs[5]  # m5 — stays attended, joins after round 0
        no_show_reg = regs[11]  # f5 — flipped to confirmed (never shows up)
        no_show_reg.status = "confirmed"
        no_show_reg.save()

        # Seed round 0 with 10 attendees who were present at quiz start:
        # m0..m4 and f0..f4. m5 and f5 missed round 0.
        tables = [
            QuizTable.objects.create(quiz=quiz_event, table_number=i + 1)
            for i in range(3)
        ]
        seeded = [regs[0], regs[1], regs[2], regs[3], regs[4]] + [
            regs[6], regs[7], regs[8], regs[9], regs[10]
        ]
        for idx, r in enumerate(seeded):
            role = "anchor" if r.user.crushprofile.gender == "M" else "rotator"
            rotation_group = ""
            if role == "rotator":
                # First 3 women go to group A, next to group B
                rotation_group = "A" if idx < 8 else "B"
            QuizRotationSchedule.objects.create(
                quiz=quiz_event,
                round_number=0,
                table=tables[idx % 3],
                user=r.user,
                role=role,
                rotation_group=rotation_group,
            )

        generate_rotation_rounds(quiz_event)

        scheduled_user_ids = set(
            QuizRotationSchedule.objects.filter(
                quiz=quiz_event, round_number__gte=1
            ).values_list("user_id", flat=True)
        )
        assert late_arrival_reg.user_id in scheduled_user_ids, (
            "Late arrival with status='attended' must be rotated"
        )
        assert no_show_reg.user_id not in scheduled_user_ids, (
            "Still-'confirmed' registration must be excluded from rotation"
        )

        # The no-show stays 'confirmed' so they can still be QR-checked in
        no_show_reg.refresh_from_db()
        assert no_show_reg.status == "confirmed"
        assert (
            EventRegistration.objects.filter(
                event=quiz_event.event, status="no_show"
            ).count()
            == 0
        )

    def test_admin_action_marks_unattended_as_no_show(self, quiz_event):
        """The admin action flips 'confirmed' to 'no_show' and leaves
        'attended' rows alone."""
        from unittest.mock import patch

        from django.test import RequestFactory

        from crush_lu.admin.quiz import mark_unattended_as_no_show
        from crush_lu.models.events import EventRegistration

        # 2 attended + 2 confirmed on this event
        for i in range(2):
            u = self._make_user_with_profile(f"att{i}", "M")
            EventRegistration.objects.create(
                event=quiz_event.event, user=u, status="attended"
            )
        confirmed_ids = []
        for i in range(2):
            u = self._make_user_with_profile(f"conf{i}", "F")
            reg = EventRegistration.objects.create(
                event=quiz_event.event, user=u, status="confirmed"
            )
            confirmed_ids.append(reg.id)

        request = RequestFactory().get("/admin/")

        # Stub messages.success so we don't need the full messages middleware
        # stack (FallbackStorage requires session + cookies) just to test
        # the data mutation.
        with patch("crush_lu.admin.quiz.messages.success"):
            mark_unattended_as_no_show(
                None, request, QuizEvent.objects.filter(pk=quiz_event.pk)
            )

        # Confirmed → no_show
        for rid in confirmed_ids:
            assert EventRegistration.objects.get(pk=rid).status == "no_show"
        # Attended untouched
        assert (
            EventRegistration.objects.filter(
                event=quiz_event.event, status="attended"
            ).count()
            == 2
        )

    def test_late_checkin_regenerates_rounds(self, quiz_event):
        """When a late arrival checks in after rotation is already
        generated, assign_table_on_checkin must rebuild rounds 1+ so the
        new person is seated for the rest of the quiz."""
        from crush_lu.services.quiz_rotation import (
            assign_table_on_checkin,
            generate_rotation_rounds,
        )

        quiz_event.num_tables = 3
        quiz_event.save()
        self._add_rounds(quiz_event, 3)

        # 6M + 5F all attended (so rotation is well-formed); plus one
        # extra woman who arrives late (starts as confirmed, then
        # transitions to attended when assign_table_on_checkin runs).
        regs = self._make_attendees(quiz_event.event, men_count=6, women_count=5)
        late_user = self._make_user_with_profile("late", "F")

        # Generate the initial schedule — late_user is not yet in it.
        generate_rotation_rounds(quiz_event)
        # Quiz is running (assign_table_on_checkin only regenerates
        # future rounds when status is active/paused).
        quiz_event.status = "active"
        quiz_event.save(update_fields=["status"])
        assert QuizRotationSchedule.objects.filter(
            quiz=quiz_event, round_number__gte=1, user=late_user
        ).exists() is False

        # Late arrival checks in: assign_table_on_checkin should place
        # them in round 0 AND regenerate rounds 1+ to include them.
        assign_table_on_checkin(quiz_event, late_user)

        assert QuizRotationSchedule.objects.filter(
            quiz=quiz_event, round_number=0, user=late_user
        ).exists(), "late arrival must be placed in round 0"
        assert QuizRotationSchedule.objects.filter(
            quiz=quiz_event, round_number__gte=1, user=late_user
        ).exists(), (
            "late arrival must be picked up by rotation regeneration "
            "into rounds 1+"
        )
        # The original 11 attendees are still in the schedule too.
        for r in regs:
            assert QuizRotationSchedule.objects.filter(
                quiz=quiz_event, round_number__gte=1, user=r.user
            ).exists()

    def test_mid_game_late_checkin_preserves_current_round(self, quiz_event):
        """If a late arrival happens while the quiz is already in round 2,
        round 1 (past) and round 2 (current) must be preserved byte-for-byte
        so players don't get moved to different tables mid-game. Only
        rounds 3+ should be rewritten to include the new user."""
        from crush_lu.services.quiz_rotation import (
            assign_table_on_checkin,
            generate_rotation_rounds,
        )

        quiz_event.num_tables = 3
        quiz_event.save()
        rounds = []
        for i in range(4):
            rounds.append(
                QuizRound.objects.create(
                    quiz=quiz_event,
                    title=f"R{i + 1}",
                    sort_order=i,
                    time_per_question=30,
                )
            )

        self._make_attendees(quiz_event.event, men_count=6, women_count=6)
        generate_rotation_rounds(quiz_event)

        # Quiz advances into the second playable round (round_number=1).
        quiz_event.current_round = rounds[1]
        quiz_event.status = "active"
        quiz_event.save(update_fields=["current_round", "status"])
        assert quiz_event.get_round_number() == 1

        # Capture round 1 (current) as it stands before the late check-in.
        def _snapshot(round_number):
            return set(
                QuizRotationSchedule.objects.filter(
                    quiz=quiz_event, round_number=round_number
                ).values_list("user_id", "table_id", "role", "rotation_group")
            )

        snap_r0 = _snapshot(0)
        snap_r1 = _snapshot(1)
        snap_r2 = _snapshot(2)
        snap_r3 = _snapshot(3)

        # Late arrival scans in during round 2.
        late_user = self._make_user_with_profile("midgame_late", "F")
        assign_table_on_checkin(quiz_event, late_user)

        # Round 0 and round 1 (current) must be byte-identical except for
        # late_user's fresh round-0 placement.
        snap_r0_after = _snapshot(0)
        assert snap_r0_after - snap_r0 == {
            row for row in snap_r0_after if row[0] == late_user.id
        }, "round 0 should only gain the late arrival's placement"
        assert snap_r1 == _snapshot(1), (
            "current round (round_number=1) must not be rewritten mid-game"
        )

        # Rounds 2+ should be rebuilt with the late user included.
        assert _snapshot(2) != snap_r2 or _snapshot(3) != snap_r3, (
            "future rounds should be regenerated to include the late arrival"
        )
        assert QuizRotationSchedule.objects.filter(
            quiz=quiz_event, round_number__gte=2, user=late_user
        ).exists(), "late arrival must appear in future rounds"
        assert not QuizRotationSchedule.objects.filter(
            quiz=quiz_event, round_number=1, user=late_user
        ).exists(), "late arrival must NOT be injected into the current round"

    def test_generate_rotation_rounds_from_round_preserves_lower(self, quiz_event):
        """Calling generate_rotation_rounds(from_round=N) must leave rows
        with round_number < N untouched and only rebuild N and above."""
        from crush_lu.services.quiz_rotation import generate_rotation_rounds

        quiz_event.num_tables = 3
        quiz_event.save()
        self._add_rounds(quiz_event, 4)
        self._make_attendees(quiz_event.event, men_count=6, women_count=6)
        generate_rotation_rounds(quiz_event)

        before_r1 = set(
            QuizRotationSchedule.objects.filter(
                quiz=quiz_event, round_number=1
            ).values_list("user_id", "table_id")
        )

        generate_rotation_rounds(quiz_event, from_round=2)

        after_r1 = set(
            QuizRotationSchedule.objects.filter(
                quiz=quiz_event, round_number=1
            ).values_list("user_id", "table_id")
        )
        assert before_r1 == after_r1, "round 1 must be preserved when from_round=2"
        assert QuizRotationSchedule.objects.filter(
            quiz=quiz_event, round_number__gte=2
        ).exists(), "rounds 2+ must still be populated"

    def test_late_checkin_builds_rounds_when_initial_gen_failed(self, quiz_event):
        """Edge case: the host starts the quiz before enough people have
        checked in, so the initial auto-generation raised ValidationError
        and no round 1+ rows exist. As late arrivals trickle in, each
        subsequent check-in must eventually build the schedule — the
        regeneration cannot be gated on 'rounds 1+ already exist'."""
        from crush_lu.services.quiz_rotation import assign_table_on_checkin

        quiz_event.num_tables = 3
        quiz_event.save()
        self._add_rounds(quiz_event, 3)

        # Quiz is already active, but no rounds 1+ were ever generated
        # (host started the quiz too early — initial gen raised).
        quiz_event.status = "active"
        quiz_event.save(update_fields=["status"])
        assert not QuizRotationSchedule.objects.filter(
            quiz=quiz_event, round_number__gte=1
        ).exists()

        # Enough attendees exist now (6M + 6F). A late check-in must
        # trigger the rebuild even though rounds 1+ don't yet exist.
        self._make_attendees(quiz_event.event, men_count=6, women_count=6)
        straggler = self._make_user_with_profile("straggler", "F")

        assign_table_on_checkin(quiz_event, straggler)

        assert QuizRotationSchedule.objects.filter(
            quiz=quiz_event, round_number__gte=1
        ).exists(), (
            "late check-in must build rounds 1+ even when the initial "
            "auto-generation never ran"
        )
        assert QuizRotationSchedule.objects.filter(
            quiz=quiz_event, round_number__gte=1, user=straggler
        ).exists(), "straggler must appear in the newly-built rounds"

    def test_assign_table_on_checkin_skips_regen_when_draft(self, quiz_event):
        """If the quiz is still 'draft' (not yet started), check-in only
        lays down a round-0 placement and must not kick off rotation
        generation. The initial build happens at start_quiz_from_first_round."""
        from crush_lu.services.quiz_rotation import assign_table_on_checkin

        quiz_event.num_tables = 3
        quiz_event.save()
        self._add_rounds(quiz_event, 3)
        assert quiz_event.status == "draft"

        self._make_attendees(quiz_event.event, men_count=6, women_count=6)
        user = self._make_user_with_profile("early", "F")

        assign_table_on_checkin(quiz_event, user)

        assert QuizRotationSchedule.objects.filter(
            quiz=quiz_event, round_number=0, user=user
        ).exists()
        assert not QuizRotationSchedule.objects.filter(
            quiz=quiz_event, round_number__gte=1
        ).exists(), "no rounds 1+ should be generated while quiz is draft"


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
        assert data["event_type"] == "quiz_night"

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

    def test_quiz_tables_with_round_filter(self, quiz_event, quiz_table, quiz_user):
        # Create rotation entry for round 1
        QuizRotationSchedule.objects.create(
            quiz=quiz_event,
            round_number=1,
            table=quiz_table,
            user=quiz_user,
            role="anchor",
        )
        client = APIClient()
        client.force_authenticate(user=quiz_user)
        response = client.get(f"/api/quiz/{quiz_event.id}/tables/?round=1")
        assert response.status_code == 200
        data = response.json()
        assert len(data[0]["members"]) == 1
        assert data[0]["members"][0]["role"] == "anchor"

    def test_quiz_state_requires_auth(self, quiz_event):
        client = APIClient()
        response = client.get(f"/api/quiz/{quiz_event.id}/state/")
        assert response.status_code in (401, 403)

    def test_my_assignment_endpoint(self, quiz_event, quiz_table, quiz_user):
        # Create rotation entry
        QuizRotationSchedule.objects.create(
            quiz=quiz_event,
            round_number=0,
            table=quiz_table,
            user=quiz_user,
            role="anchor",
        )
        client = APIClient()
        client.force_authenticate(user=quiz_user)
        response = client.get(f"/api/quiz/{quiz_event.id}/my-assignment/")
        assert response.status_code == 200
        data = response.json()
        assert data["table_number"] == 1
        assert data["role"] == "anchor"
        assert data["personal_score"] == 0

    def test_my_assignment_not_assigned(self, quiz_event):
        other_user = User.objects.create_user(
            username="other@test.com", password="test"
        )
        _grant_consent(other_user)
        client = APIClient()
        client.force_authenticate(user=other_user)
        response = client.get(f"/api/quiz/{quiz_event.id}/my-assignment/")
        assert response.status_code == 404

    def test_score_table_endpoint(
        self, quiz_event, quiz_table, quiz_questions, quiz_user, coach_user
    ):
        # Setup: assign user to table in rotation
        quiz_event.current_round = quiz_questions[0].round
        quiz_event.save()
        QuizRotationSchedule.objects.create(
            quiz=quiz_event,
            round_number=0,
            table=quiz_table,
            user=quiz_user,
            role="anchor",
        )

        client = APIClient()
        client.force_login(coach_user)
        response = client.post(
            f"/api/quiz/{quiz_event.id}/score-table/",
            {
                "table_id": quiz_table.id,
                "question_id": quiz_questions[0].id,
                "is_correct": True,
            },
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["is_correct"] is True
        assert data["points_awarded"] == 10
        assert data["members_scored"] == 1

        # Verify IndividualScore was created
        score = IndividualScore.objects.get(
            quiz=quiz_event, user=quiz_user, question=quiz_questions[0]
        )
        assert score.is_correct
        assert score.points_earned == 10

    def test_score_table_bonus_doubles_points(
        self, quiz_event, quiz_table, quiz_user, coach_user, bonus_round
    ):
        q = QuizQuestion.objects.create(
            round=bonus_round,
            text="Bonus question",
            question_type="multiple_choice",
            choices=[{"text": "A", "is_correct": True}],
            sort_order=0,
            points=10,
        )
        quiz_event.current_round = bonus_round
        quiz_event.save()
        QuizRotationSchedule.objects.create(
            quiz=quiz_event,
            round_number=bonus_round.sort_order,
            table=quiz_table,
            user=quiz_user,
            role="anchor",
        )

        client = APIClient()
        client.force_login(coach_user)
        response = client.post(
            f"/api/quiz/{quiz_event.id}/score-table/",
            {"table_id": quiz_table.id, "question_id": q.id, "is_correct": True},
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["points_awarded"] == 20  # Doubled

    def test_score_table_rejects_non_host(
        self, quiz_event, quiz_table, quiz_questions, quiz_user
    ):
        client = APIClient()
        client.force_login(quiz_user)
        response = client.post(
            f"/api/quiz/{quiz_event.id}/score-table/",
            {
                "table_id": quiz_table.id,
                "question_id": quiz_questions[0].id,
                "is_correct": True,
            },
            format="json",
        )
        assert response.status_code == 403


# ============================================================================
# VIEW TESTS
# ============================================================================


@pytest.mark.django_db
class TestQuizViews:
    def test_quiz_live_view(self, client, quiz_event, quiz_user):
        from crush_lu.models.events import EventRegistration

        EventRegistration.objects.create(
            event=quiz_event.event, user=quiz_user, status="attended"
        )
        client.force_login(quiz_user)
        response = client.get(f"/en/events/{quiz_event.event_id}/quiz/")
        assert response.status_code == 200
        assert b"quizLive" in response.content

    def test_quiz_live_view_rejects_non_attendee(self, client, quiz_event, quiz_user):
        client.force_login(quiz_user)
        response = client.get(f"/en/events/{quiz_event.event_id}/quiz/")
        assert response.status_code == 404

    def test_quiz_coach_view_requires_coach(self, client, quiz_event, quiz_user):
        client.force_login(quiz_user)
        response = client.get(f"/en/events/{quiz_event.event_id}/quiz/coach/")
        assert response.status_code == 404  # Not a coach

    def test_quiz_coach_view_staff(self, client, quiz_event, coach_user):
        client.force_login(coach_user)
        response = client.get(f"/en/events/{quiz_event.event_id}/quiz/coach/")
        assert response.status_code == 200
        assert b"quizHost" in response.content

    def test_quiz_night_context(self, client, quiz_event, coach_user):
        """Host view passes is_quiz_night context."""
        client.force_login(coach_user)
        response = client.get(f"/en/events/{quiz_event.event_id}/quiz/coach/")
        assert response.status_code == 200
        assert b"data-quiz-night" in response.content


# ============================================================================
# ENSURE TABLES
# ============================================================================


@pytest.mark.django_db
class TestEnsureTables:
    def test_creates_tables(self, quiz_event):
        """ensure_tables creates QuizTable objects matching num_tables."""
        quiz_event.num_tables = 3
        quiz_event.save()

        tables = quiz_event.ensure_tables()

        assert len(tables) == 3
        assert QuizTable.objects.filter(quiz=quiz_event).count() == 3
        assert set(tables.keys()) == {1, 2, 3}

    def test_idempotent(self, quiz_event):
        """Calling ensure_tables twice doesn't create duplicates."""
        quiz_event.num_tables = 3
        quiz_event.save()

        quiz_event.ensure_tables()
        quiz_event.ensure_tables()

        assert QuizTable.objects.filter(quiz=quiz_event).count() == 3

    def test_removes_excess_tables(self, quiz_event):
        """Excess tables are removed when num_tables decreases."""
        quiz_event.num_tables = 4
        quiz_event.save()
        quiz_event.ensure_tables()
        assert QuizTable.objects.filter(quiz=quiz_event).count() == 4

        quiz_event.num_tables = 2
        quiz_event.save()
        quiz_event.ensure_tables()
        assert QuizTable.objects.filter(quiz=quiz_event).count() == 2

    def test_no_num_tables_returns_empty(self, quiz_event):
        """Returns empty dict when num_tables is not set."""
        assert quiz_event.ensure_tables() == {}


# ============================================================================
# ASSIGN TABLE ON CHECK-IN
# ============================================================================


def _create_profile(user, gender="M"):
    """Create a CrushProfile for the user with the given gender."""
    from crush_lu.models.profiles import CrushProfile

    profile, _ = CrushProfile.objects.get_or_create(
        user=user,
        defaults={
            "gender": gender,
            "date_of_birth": date(1995, 1, 1),
        },
    )
    if profile.gender != gender:
        profile.gender = gender
        profile.save(update_fields=["gender"])
    return profile


@pytest.mark.django_db
class TestAssignTableOnCheckin:
    def _make_users(self, gender, count, start=1):
        users = []
        for i in range(count):
            idx = start + i
            user = User.objects.create_user(
                username=f"{gender.lower()}{idx}@test.com",
                email=f"{gender.lower()}{idx}@test.com",
                password="testpass123",
            )
            _grant_consent(user)
            _create_profile(user, gender)
            users.append(user)
        return users

    def test_distributes_evenly(self, quiz_event):
        """6M + 6F with 3 tables → 2 anchors + 2 rotators per table."""
        from crush_lu.services.quiz_rotation import assign_table_on_checkin

        quiz_event.num_tables = 3
        quiz_event.save()
        quiz_event.ensure_tables()

        men = self._make_users("M", 6)
        women = self._make_users("F", 6, start=7)

        # Check in all men, then all women
        for user in men:
            result = assign_table_on_checkin(quiz_event, user)
            assert result is not None
            assert result["role"] == "anchor"

        for user in women:
            result = assign_table_on_checkin(quiz_event, user)
            assert result is not None
            assert result["role"] == "rotator"

        # Verify distribution: 2 anchors + 2 rotators per table
        for t in range(1, 4):
            table = QuizTable.objects.get(quiz=quiz_event, table_number=t)
            anchors = QuizRotationSchedule.objects.filter(
                quiz=quiz_event, round_number=0, table=table, role="anchor"
            ).count()
            rotators = QuizRotationSchedule.objects.filter(
                quiz=quiz_event, round_number=0, table=table, role="rotator"
            ).count()
            assert anchors == 2, f"Table {t} has {anchors} anchors, expected 2"
            assert rotators == 2, f"Table {t} has {rotators} rotators, expected 2"

    def test_idempotent(self, quiz_event):
        """Same user checked in twice returns same table."""
        from crush_lu.services.quiz_rotation import assign_table_on_checkin

        quiz_event.num_tables = 3
        quiz_event.save()
        quiz_event.ensure_tables()

        users = self._make_users("M", 1)
        user = users[0]

        result1 = assign_table_on_checkin(quiz_event, user)
        result2 = assign_table_on_checkin(quiz_event, user)

        assert result1["table_number"] == result2["table_number"]
        assert result1["role"] == result2["role"]

        # Only one membership created
        assert (
            QuizTableMembership.objects.filter(
                table__quiz=quiz_event, user=user
            ).count()
            == 1
        )

    def test_flexible_gender(self, quiz_event):
        """NB user is assigned to the smaller pool."""
        from crush_lu.services.quiz_rotation import assign_table_on_checkin

        quiz_event.num_tables = 2
        quiz_event.save()
        quiz_event.ensure_tables()

        # Create 2 anchors first
        men = self._make_users("M", 2)
        for u in men:
            assign_table_on_checkin(quiz_event, u)

        # Create NB user - should become rotator (anchor pool already has 2)
        nb_user = User.objects.create_user(
            username="nb1@test.com", email="nb1@test.com", password="testpass123"
        )
        _grant_consent(nb_user)
        _create_profile(nb_user, "NB")

        result = assign_table_on_checkin(quiz_event, nb_user)
        assert result["role"] == "rotator"

    def test_no_quiz_returns_none(self):
        """Returns None when quiz has no num_tables."""
        from crush_lu.services.quiz_rotation import assign_table_on_checkin

        # Create a quiz without tables
        user = User.objects.create_user(
            username="noq@test.com", email="noq@test.com", password="testpass123"
        )
        _grant_consent(user)
        event = __import__(
            "crush_lu.models", fromlist=["MeetupEvent"]
        ).MeetupEvent.objects.create(
            title="No Quiz",
            description="Test",
            event_type="mixer",
            date_time=timezone.now() + timedelta(days=1),
            location="Test",
            address="Test",
            max_participants=10,
            registration_deadline=timezone.now() + timedelta(hours=12),
        )
        quiz = QuizEvent.objects.create(event=event, status="draft", created_by=user)
        result = assign_table_on_checkin(quiz, user)
        assert result is None


# ============================================================================
# CHECKIN API WITH TABLE ASSIGNMENT
# ============================================================================


@pytest.mark.django_db
class TestCheckinAPITableAssignment:
    def test_checkin_returns_table_number(self):
        """Check-in API returns table_number for quiz night events."""
        from django.core.signing import Signer
        from django.test import Client

        from crush_lu.models import MeetupEvent
        from crush_lu.models.events import EventRegistration

        # Event must be within 12-hour check-in window
        event = MeetupEvent.objects.create(
            title="Quiz Night Checkin Test",
            description="Test",
            event_type="quiz_night",
            date_time=timezone.now() + timedelta(hours=2),
            location="Test",
            address="Test",
            max_participants=30,
            registration_deadline=timezone.now() + timedelta(hours=1),
            is_published=True,
        )
        coach = User.objects.create_user(
            username="checkincoach@test.com",
            email="checkincoach@test.com",
            password="testpass123",
        )
        _grant_consent(coach)
        quiz = QuizEvent.objects.create(
            event=event, status="draft", created_by=coach, num_tables=3
        )
        quiz.ensure_tables()

        user = User.objects.create_user(
            username="checkintest@test.com",
            email="checkintest@test.com",
            password="testpass123",
        )
        _grant_consent(user)
        _create_profile(user, "M")

        reg = EventRegistration.objects.create(
            event=event,
            user=user,
            status="confirmed",
        )

        signer = Signer()
        token = signer.sign(f"{reg.id}:{event.id}")
        reg.checkin_token = token
        reg.save()

        client = Client()
        response = client.post(f"/api/events/checkin/{reg.id}/{token}/")
        data = response.json()

        assert data["success"] is True
        assert "table_number" in data
        assert data["table_number"] in [1, 2, 3]
        assert data["role"] == "anchor"

    def test_checkin_non_quiz_no_table(self):
        """Check-in API doesn't return table_number for non-quiz events."""
        from django.core.signing import Signer
        from django.test import Client

        from crush_lu.models import MeetupEvent
        from crush_lu.models.events import EventRegistration

        event = MeetupEvent.objects.create(
            title="Mixer Event",
            description="No quiz",
            event_type="mixer",
            date_time=timezone.now() + timedelta(hours=2),
            location="Test",
            address="Test",
            max_participants=20,
            registration_deadline=timezone.now() + timedelta(hours=1),
        )

        user = User.objects.create_user(
            username="mixertest@test.com",
            email="mixertest@test.com",
            password="testpass123",
        )
        _grant_consent(user)

        reg = EventRegistration.objects.create(
            event=event, user=user, status="confirmed"
        )

        signer = Signer()
        token = signer.sign(f"{reg.id}:{event.id}")
        reg.checkin_token = token
        reg.save()

        client = Client()
        response = client.post(f"/api/events/checkin/{reg.id}/{token}/")
        data = response.json()

        assert data["success"] is True
        assert "table_number" not in data


# ============================================================================
# PUBLIC DISPLAY ENDPOINT
# ============================================================================


@pytest.mark.django_db
class TestQuizTableDisplay:
    def test_display_view_renders(self, client, quiz_event):
        """Public display view renders without auth."""
        response = client.get(f"/quiz/{quiz_event.event_id}/display/")
        assert response.status_code == 200
        assert b"quizDisplay" in response.content

    def test_display_data_returns_tables(self, client, quiz_event):
        """Display data JSON endpoint returns table info."""
        quiz_event.num_tables = 2
        quiz_event.save()
        quiz_event.ensure_tables()

        response = client.get(f"/api/quiz/{quiz_event.event_id}/display-data/")
        assert response.status_code == 200
        data = response.json()
        assert "tables" in data
        assert len(data["tables"]) == 2
        assert data["attended_count"] == 0
        assert "quiz_status" in data

    def test_display_pin_gate_shown_when_token_set(self, client, quiz_event):
        """Display shows PIN gate when display_token is configured."""
        quiz_event.display_token = "1234"
        quiz_event.save()

        response = client.get(f"/quiz/{quiz_event.event_id}/display/")
        assert response.status_code == 200
        assert response.context["pin_required"] is True

    def test_display_no_pin_gate_when_no_token(self, client, quiz_event):
        """Display renders directly when no display_token configured."""
        response = client.get(f"/quiz/{quiz_event.event_id}/display/")
        assert response.status_code == 200
        assert response.context["pin_required"] is False

    def test_display_token_bypasses_pin_gate(self, client, quiz_event):
        """Providing correct token in URL bypasses PIN gate."""
        quiz_event.display_token = "1234"
        quiz_event.save()

        response = client.get(f"/quiz/{quiz_event.event_id}/display/?token=1234")
        assert response.status_code == 200
        assert response.context["pin_required"] is False

    def test_display_wrong_token_shows_pin_gate(self, client, quiz_event):
        """Wrong token in URL still shows PIN gate."""
        quiz_event.display_token = "1234"
        quiz_event.save()

        response = client.get(f"/quiz/{quiz_event.event_id}/display/?token=9999")
        assert response.status_code == 200
        assert response.context["pin_required"] is True

    def test_verify_pin_correct(self, client, quiz_event):
        """Correct PIN returns valid=true."""
        quiz_event.display_token = "5678"
        quiz_event.save()

        response = client.post(
            f"/api/quiz/{quiz_event.event_id}/verify-pin/",
            data='{"pin": "5678"}',
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["valid"] is True

    def test_verify_pin_wrong(self, client, quiz_event):
        """Wrong PIN returns valid=false."""
        quiz_event.display_token = "5678"
        quiz_event.save()

        response = client.post(
            f"/api/quiz/{quiz_event.event_id}/verify-pin/",
            data='{"pin": "0000"}',
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["valid"] is False

    def test_verify_pin_no_token_set(self, client, quiz_event):
        """When no display_token configured, any PIN is valid."""
        response = client.post(
            f"/api/quiz/{quiz_event.event_id}/verify-pin/",
            data='{"pin": "1234"}',
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["valid"] is True


# ============================================================================
# SCORING ROUND ATTRIBUTION (P0 REGRESSION)
# ============================================================================


@pytest.mark.django_db
class TestScoringRoundAttribution:
    """When a score arrives for a question after the quiz has rotated to
    the next round, points must credit the users who were seated at that
    table *when the question was asked*, not whoever rotated in later."""

    def test_late_score_attributes_to_questions_own_round(
        self, quiz_event, coach_user
    ):
        from crush_lu.models import CrushProfile
        from datetime import date as _date

        # Two distinct rounds with one question each.
        r0 = QuizRound.objects.create(quiz=quiz_event, title="R1", sort_order=0)
        r1 = QuizRound.objects.create(quiz=quiz_event, title="R2", sort_order=1)
        q0 = QuizQuestion.objects.create(
            round=r0,
            text="Q in round 0",
            question_type="multiple_choice",
            choices=[{"text": "A", "is_correct": True}],
            sort_order=0,
            points=10,
        )

        table = QuizTable.objects.create(quiz=quiz_event, table_number=1)

        # User A sits at the table in round 0; user B sits at the same
        # table in round 1. If the scoring path uses the *current* round,
        # a late score for q0 will credit user B instead of user A.
        def _mk(name):
            u = User.objects.create_user(
                username=f"{name}@test.com",
                email=f"{name}@test.com",
                password="test",
            )
            _grant_consent(u)
            CrushProfile.objects.get_or_create(
                user=u, defaults={"gender": "M", "date_of_birth": _date(1990, 1, 1)}
            )
            return u

        user_r0 = _mk("r0user")
        user_r1 = _mk("r1user")
        QuizRotationSchedule.objects.create(
            quiz=quiz_event,
            round_number=0,
            table=table,
            user=user_r0,
            role="anchor",
        )
        QuizRotationSchedule.objects.create(
            quiz=quiz_event,
            round_number=1,
            table=table,
            user=user_r1,
            role="anchor",
        )

        # Quiz has already rotated — current round is r1.
        quiz_event.current_round = r1
        quiz_event.current_question_index = -1
        quiz_event.status = "active"
        quiz_event.save()

        client = APIClient()
        client.force_login(coach_user)  # coach_user is quiz.created_by
        response = client.post(
            f"/api/quiz/{quiz_event.id}/score-table/",
            {"table_id": table.id, "question_id": q0.id, "is_correct": True},
            format="json",
        )
        assert response.status_code == 200

        # The score must be credited to user_r0 (seated in round 0 when q0
        # was asked), NOT user_r1 (seated in round 1 after the rotate).
        assert IndividualScore.objects.filter(
            quiz=quiz_event, user=user_r0, question=q0
        ).exists(), "round-0 user must be credited for a round-0 question"
        assert not IndividualScore.objects.filter(
            quiz=quiz_event, user=user_r1, question=q0
        ).exists(), (
            "round-1 user must NOT be credited for a round-0 question "
            "even when the quiz has since rotated"
        )


# ============================================================================
# ROTATE GUARD (P1)
# ============================================================================


@pytest.mark.django_db
class TestRotateGuard:
    """The server must reject 'rotate' when the current round still has
    unasked questions or unscored tables — defense-in-depth against a
    stale host UI or a second host tab."""

    async def _make_consumer(self, quiz, user):
        from channels.testing import WebsocketCommunicator
        from crush_lu.consumers import QuizConsumer

        communicator = WebsocketCommunicator(
            QuizConsumer.as_asgi(), f"/ws/quiz/{quiz.id}/"
        )
        communicator.scope["user"] = user
        communicator.scope["url_route"] = {"kwargs": {"quiz_id": quiz.id}}
        return communicator

    def _setup_running_quiz(self, quiz_event, coach_user):
        """Create 2 rounds × 2 questions, 2 tables, 4 attendees, round 0
        rotation. Quiz starts on round 0."""
        from crush_lu.models import CrushProfile
        from crush_lu.models.events import EventRegistration
        from datetime import date as _date

        quiz_event.num_tables = 2
        quiz_event.save()

        rounds = []
        questions = []
        for i in range(2):
            r = QuizRound.objects.create(
                quiz=quiz_event, title=f"R{i + 1}", sort_order=i
            )
            rounds.append(r)
            for j in range(2):
                q = QuizQuestion.objects.create(
                    round=r,
                    text=f"Q{j} of R{i}",
                    question_type="multiple_choice",
                    choices=[{"text": "A", "is_correct": True}],
                    sort_order=j,
                    points=10,
                )
                questions.append(q)

        tables = [
            QuizTable.objects.create(quiz=quiz_event, table_number=n)
            for n in (1, 2)
        ]
        for i in range(4):
            u = User.objects.create_user(
                username=f"guard{i}@test.com",
                email=f"guard{i}@test.com",
                password="test",
            )
            _grant_consent(u)
            CrushProfile.objects.get_or_create(
                user=u,
                defaults={"gender": "M", "date_of_birth": _date(1990, 1, 1)},
            )
            EventRegistration.objects.create(
                event=quiz_event.event, user=u, status="attended"
            )
            for rn in range(2):
                QuizRotationSchedule.objects.create(
                    quiz=quiz_event,
                    round_number=rn,
                    table=tables[i % 2],
                    user=u,
                    role="anchor",
                )

        quiz_event.current_round = rounds[0]
        quiz_event.current_question_index = 0  # first question shown
        quiz_event.status = "active"
        quiz_event.save()

        return rounds, questions, tables

    def test_rotate_blocked_when_questions_remain(self, quiz_event, coach_user):
        """Host has only shown q0 of a 2-question round — rotate must fail."""
        from crush_lu.services.quiz_rotation import check_can_rotate

        self._setup_running_quiz(quiz_event, coach_user)
        guard = check_can_rotate(quiz_event.id)
        assert "error" in guard
        assert "question(s) remain" in guard["error"]

    def test_rotate_blocked_when_tables_unscored(
        self, quiz_event, coach_user
    ):
        """All questions asked but only 1/2 tables scored the last one."""
        from crush_lu.services.quiz_rotation import check_can_rotate

        _rounds, questions, tables = self._setup_running_quiz(
            quiz_event, coach_user
        )
        # Advance to last question of round 0; only score one table.
        quiz_event.current_question_index = 1
        quiz_event.save()
        TableRoundScore.objects.create(
            quiz=quiz_event,
            table=tables[0],
            question=questions[1],
            is_correct=True,
        )

        guard = check_can_rotate(quiz_event.id)
        assert "error" in guard
        assert "tables scored" in guard["error"]

    def test_rotate_allowed_when_round_complete_and_scored(
        self, quiz_event, coach_user
    ):
        """All questions asked and all tables scored — rotate may proceed."""
        from crush_lu.services.quiz_rotation import check_can_rotate

        _rounds, questions, tables = self._setup_running_quiz(
            quiz_event, coach_user
        )
        quiz_event.current_question_index = 1
        quiz_event.save()
        for t in tables:
            TableRoundScore.objects.create(
                quiz=quiz_event,
                table=t,
                question=questions[1],
                is_correct=True,
            )

        guard = check_can_rotate(quiz_event.id)
        assert guard == {}

    def test_rotate_skipped_for_non_quiz_night(self, quiz_event, coach_user):
        """Legacy (non-quiz-night) events don't use table scoring — guard
        must not block them."""
        from crush_lu.services.quiz_rotation import check_can_rotate

        quiz_event.event.event_type = "mixer"
        quiz_event.event.save()
        self._setup_running_quiz(quiz_event, coach_user)
        guard = check_can_rotate(quiz_event.id)
        assert guard == {}


# ============================================================================
# HOST AUTHORITY TIGHTENING
# ============================================================================


@pytest.mark.django_db
class TestHostAuthority:
    """Only quiz.created_by and CrushCoach objects assigned to the event
    via MeetupEvent.coaches may host/score. Unrelated staff or unassigned
    active coaches must be rejected."""

    def _make_coach(self, username, assigned_to_event=None):
        from crush_lu.models import CrushCoach

        user = User.objects.create_user(
            username=f"{username}@test.com",
            email=f"{username}@test.com",
            password="test",
        )
        _grant_consent(user)
        coach = CrushCoach.objects.create(user=user, is_active=True)
        if assigned_to_event is not None:
            assigned_to_event.coaches.add(coach)
        return user, coach

    def test_unassigned_active_coach_cannot_score(
        self, quiz_event, quiz_table, quiz_questions
    ):
        """An active CrushCoach not listed in event.coaches must get 403."""
        unassigned_user, _coach = self._make_coach("unassigned")

        client = APIClient()
        client.force_login(unassigned_user)
        response = client.post(
            f"/api/quiz/{quiz_event.id}/score-table/",
            {
                "table_id": quiz_table.id,
                "question_id": quiz_questions[0].id,
                "is_correct": True,
            },
            format="json",
        )
        assert response.status_code == 403

    def test_assigned_coach_can_score(
        self, quiz_event, quiz_table, quiz_questions, quiz_user
    ):
        """A CrushCoach listed in event.coaches must be authorized to score."""
        assigned_user, _coach = self._make_coach(
            "assigned", assigned_to_event=quiz_event.event
        )
        quiz_event.current_round = quiz_questions[0].round
        quiz_event.save()
        QuizRotationSchedule.objects.create(
            quiz=quiz_event,
            round_number=0,
            table=quiz_table,
            user=quiz_user,
            role="anchor",
        )

        client = APIClient()
        client.force_login(assigned_user)
        response = client.post(
            f"/api/quiz/{quiz_event.id}/score-table/",
            {
                "table_id": quiz_table.id,
                "question_id": quiz_questions[0].id,
                "is_correct": True,
            },
            format="json",
        )
        assert response.status_code == 200

    def test_staff_without_coach_cannot_score(
        self, quiz_event, quiz_table, quiz_questions
    ):
        """is_staff alone no longer grants host privilege."""
        rando_staff = User.objects.create_user(
            username="rando_staff@test.com",
            email="rando_staff@test.com",
            password="test",
            is_staff=True,
        )
        _grant_consent(rando_staff)

        client = APIClient()
        client.force_login(rando_staff)
        response = client.post(
            f"/api/quiz/{quiz_event.id}/score-table/",
            {
                "table_id": quiz_table.id,
                "question_id": quiz_questions[0].id,
                "is_correct": True,
            },
            format="json",
        )
        assert response.status_code == 403

    def test_coach_view_rejects_unassigned_coach(self, client, quiz_event):
        """Coach view returns 404 for a CrushCoach not assigned to the event."""
        unassigned_user, _coach = self._make_coach("viewcoach")
        client.force_login(unassigned_user)
        response = client.get(f"/en/events/{quiz_event.event_id}/quiz/coach/")
        assert response.status_code == 404

    def test_coach_view_allows_assigned_coach(self, client, quiz_event):
        """Coach view renders for a CrushCoach listed in event.coaches."""
        assigned_user, _coach = self._make_coach(
            "viewassigned", assigned_to_event=quiz_event.event
        )
        client.force_login(assigned_user)
        response = client.get(f"/en/events/{quiz_event.event_id}/quiz/coach/")
        assert response.status_code == 200


# ============================================================================
# ROTATION INVARIANTS (P3 — property-based)
# ============================================================================


@pytest.mark.django_db
class TestRotationInvariants:
    """Property-based invariants the rotation algorithm MUST satisfy across
    a wide range of (men, women, tables, rounds) configurations. These
    close the 'no workaround possible' gap: any future regression that
    lets two rotators collide or double-seats an anchor is detected here.
    """

    @staticmethod
    def _mk_users(prefix, count):
        users = []
        for i in range(count):
            u = User.objects.create_user(
                username=f"{prefix}{i}@test.com",
                email=f"{prefix}{i}@test.com",
                password="t",
            )
            _grant_consent(u)
            users.append(u)
        return users

    # Representative configs: small + large, balanced + unbalanced, edge +
    # spillover. (men, women, num_tables, num_rounds).
    CONFIGS = [
        (4, 4, 2, 2),
        (6, 6, 3, 3),
        (8, 8, 4, 4),
        (12, 12, 6, 3),
        (16, 16, 8, 4),
        (7, 7, 3, 3),   # odd counts
        (6, 9, 3, 3),   # women > 2× tables → spillover group C
        (10, 4, 5, 3),  # more anchors than rotators
        (4, 6, 2, 2),   # 2-table special case
    ]

    def test_anchor_invariance(self):
        """Every anchor stays at exactly one table across all rounds."""
        from crush_lu.services.quiz_rotation import generate_rotation_schedule

        for cfg_idx, (men_n, women_n, tables_n, rounds_n) in enumerate(self.CONFIGS):
            men = self._mk_users(f"ai_m_c{cfg_idx}_", men_n)
            women = self._mk_users(f"ai_w_c{cfg_idx}_", women_n)

            result = generate_rotation_schedule(
                men, women, num_rounds=rounds_n, num_tables=tables_n
            )
            schedule = result["schedule"]

            anchor_tables = {}
            for e in schedule:
                if e["role"] == "anchor":
                    seen = anchor_tables.setdefault(e["user"].id, set())
                    seen.add(e["table_number"])

            for user_id, tables in anchor_tables.items():
                assert len(tables) == 1, (
                    f"config=({men_n},{women_n},{tables_n},{rounds_n}) "
                    f"anchor user {user_id} seen at tables {tables}"
                )

    def test_no_group_ab_collisions(self):
        """At most one user per (round, table, rotation_group) for groups
        A and B. Group C is spillover and may double up.

        Skips ``num_tables == 2`` because the algorithm deliberately seats
        two group-A rotators per table in the 2-table special case (there
        is no group B; all women are in a single rotating group).
        """
        from collections import defaultdict

        from crush_lu.services.quiz_rotation import generate_rotation_schedule

        for cfg_idx, (men_n, women_n, tables_n, rounds_n) in enumerate(self.CONFIGS):
            if tables_n == 2:
                continue  # 2-table case intentionally groups 2 women per A-slot
            men = self._mk_users(f"cc_m_c{cfg_idx}_", men_n)
            women = self._mk_users(f"cc_w_c{cfg_idx}_", women_n)
            result = generate_rotation_schedule(
                men, women, num_rounds=rounds_n, num_tables=tables_n
            )

            buckets = defaultdict(list)
            for e in result["schedule"]:
                if e["role"] != "rotator":
                    continue
                if e["rotation_group"] in ("A", "B"):
                    key = (e["round_number"], e["table_number"], e["rotation_group"])
                    buckets[key].append(e["user"].id)

            for key, users in buckets.items():
                assert len(users) == 1, (
                    f"config=({men_n},{women_n},{tables_n},{rounds_n}) "
                    f"{key} has {len(users)} rotators: {users}"
                )

    def test_group_a_visits_every_table(self):
        """Over N rounds (N = num_tables), every Group-A rotator visits
        every table exactly once."""
        from crush_lu.services.quiz_rotation import generate_rotation_schedule

        # Use configs where num_rounds == num_tables so the property is
        # well-formed (after N rounds, every table must have been visited).
        visit_configs = [(6, 6, 3, 3), (8, 8, 4, 4), (12, 12, 6, 6)]
        for cfg_idx, (men_n, women_n, tables_n, rounds_n) in enumerate(visit_configs):
            men = self._mk_users(f"v_m_c{cfg_idx}_", men_n)
            women = self._mk_users(f"v_w_c{cfg_idx}_", women_n)
            result = generate_rotation_schedule(
                men, women, num_rounds=rounds_n, num_tables=tables_n
            )

            visits = {}
            for e in result["schedule"]:
                if e["role"] == "rotator" and e["rotation_group"] == "A":
                    visits.setdefault(e["user"].id, set()).add(
                        e["table_number"]
                    )

            for user_id, tables in visits.items():
                assert tables == set(range(1, tables_n + 1)), (
                    f"config=({men_n},{women_n},{tables_n},{rounds_n}) "
                    f"group-A user {user_id} visited {tables}"
                )

    def test_every_user_seated_every_round(self):
        """Every participant appears exactly once in every round."""
        from collections import Counter

        from crush_lu.services.quiz_rotation import generate_rotation_schedule

        for cfg_idx, (men_n, women_n, tables_n, rounds_n) in enumerate(self.CONFIGS):
            men = self._mk_users(f"s_m_c{cfg_idx}_", men_n)
            women = self._mk_users(f"s_w_c{cfg_idx}_", women_n)
            result = generate_rotation_schedule(
                men, women, num_rounds=rounds_n, num_tables=tables_n
            )
            schedule = result["schedule"]
            total_users = men_n + women_n

            for rn in range(rounds_n):
                per_user = Counter(
                    e["user"].id
                    for e in schedule
                    if e["round_number"] == rn
                )
                assert len(per_user) == total_users, (
                    f"config=({men_n},{women_n},{tables_n},{rounds_n}) "
                    f"round {rn}: {len(per_user)}/{total_users} users seated"
                )
                # Each user appears exactly once per round (the unique_together
                # DB constraint enforces this on persisted data, but the
                # algorithm's output dicts must also respect it).
                dupes = {u: c for u, c in per_user.items() if c > 1}
                assert not dupes, (
                    f"config=({men_n},{women_n},{tables_n},{rounds_n}) "
                    f"round {rn} duplicates: {dupes}"
                )

    def test_round0_check_in_placement_matches_batch_layout(self):
        """When users check in incrementally via assign_table_on_checkin,
        their round-0 placement must match what a batch
        generate_rotation_schedule call would have produced — otherwise
        the regenerated rounds 1+ would be inconsistent with round 0."""
        from crush_lu.services.quiz_rotation import (
            assign_table_on_checkin,
            generate_rotation_schedule,
        )

        # Use a balanced config for deterministic comparison.
        men_n, women_n, tables_n = 6, 6, 3
        men = self._mk_users(f"r0_m_{tables_n}_", men_n)
        women = self._mk_users(f"r0_w_{tables_n}_", women_n)

        # Give each user a matching profile so the check-in path picks
        # anchor/rotator based on gender.
        from crush_lu.models import CrushProfile
        from datetime import date as _date

        for u in men:
            CrushProfile.objects.create(
                user=u, gender="M", date_of_birth=_date(1990, 1, 1)
            )
        for u in women:
            CrushProfile.objects.create(
                user=u, gender="F", date_of_birth=_date(1990, 1, 1)
            )

        from crush_lu.models import MeetupEvent

        event = MeetupEvent.objects.create(
            title="R0 test",
            event_type="quiz_night",
            date_time=timezone.now() + timedelta(days=1),
            location="t",
            address="t",
            max_participants=30,
            registration_deadline=timezone.now() + timedelta(hours=12),
            is_published=True,
        )
        quiz = QuizEvent.objects.create(
            event=event, status="draft", created_by=men[0], num_tables=tables_n
        )

        # Incremental check-in.
        for u in men + women:
            assign_table_on_checkin(quiz, u)

        incremental = {
            (r.user_id, r.role): r.table.table_number
            for r in QuizRotationSchedule.objects.filter(
                quiz=quiz, round_number=0
            ).select_related("table")
        }

        # Compare with a one-shot batch placement using the same user
        # ordering (men then women) and num_tables.
        batch_result = generate_rotation_schedule(
            men, women, num_rounds=1, num_tables=tables_n
        )
        batch = {
            (e["user"].id, e["role"]): e["table_number"]
            for e in batch_result["schedule"]
            if e["round_number"] == 0
        }

        assert incremental == batch, (
            "incremental check-in placement diverged from batch layout"
        )
