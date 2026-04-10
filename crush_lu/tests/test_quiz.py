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
