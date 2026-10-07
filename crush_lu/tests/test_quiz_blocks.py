# ruff: noqa: F811
"""Quiz rotation honours UserBlock best-effort and warns about the residual."""

import pytest

from crush_lu.models import UserBlock
from crush_lu.models.quiz import QuizRotationSchedule
from crush_lu.services.quiz_rotation import (
    blocked_table_conflict_count,
    compute_rotation_warnings,
    generate_rotation_rounds,
)
from crush_lu.tests.test_quiz_consolidation import (  # noqa: F401
    _check_in_attended,
    _make_user,
    quiz_event_4t,
)


def _table_of(quiz, user):
    return QuizRotationSchedule.objects.get(
        quiz=quiz, round_number=0, user=user
    ).table_id


@pytest.mark.django_db
class TestQuizBlocks:
    def test_checkin_avoids_the_table_of_a_blocked_member(
        self, quiz_event_4t
    ):  # noqa: F811
        quiz = quiz_event_4t
        man = _make_user("blk_m0", "M")
        _check_in_attended(quiz, man)
        woman = _make_user("blk_f0", "F")
        # Without the block the first rotator goes to the lowest table (1),
        # which is where the lone anchor sits.
        UserBlock.objects.create(blocker=woman, blocked=man, reason="other")

        _check_in_attended(quiz, woman)

        assert _table_of(quiz, woman) != _table_of(quiz, man)

    def test_block_direction_is_symmetric_at_checkin(self, quiz_event_4t):  # noqa: F811
        quiz = quiz_event_4t
        man = _make_user("sym_m0", "M")
        _check_in_attended(quiz, man)
        woman = _make_user("sym_f0", "F")
        UserBlock.objects.create(blocker=man, blocked=woman, reason="other")

        _check_in_attended(quiz, woman)

        assert _table_of(quiz, woman) != _table_of(quiz, man)

    def test_unblocked_checkin_behaviour_is_unchanged(
        self, quiz_event_4t
    ):  # noqa: F811
        quiz = quiz_event_4t
        man = _make_user("plain_m0", "M")
        _check_in_attended(quiz, man)
        woman = _make_user("plain_f0", "F")

        _check_in_attended(quiz, woman)

        assert _table_of(quiz, woman) == _table_of(quiz, man)

    def test_residual_conflicts_are_counted_and_warned(
        self, quiz_event_4t
    ):  # noqa: F811
        quiz = quiz_event_4t
        men = [_make_user(f"res_m{i}", "M") for i in range(2)]
        women = [_make_user(f"res_f{i}", "F") for i in range(4)]
        for user in men + women:
            _check_in_attended(quiz, user)
        # Every rotator visits every table over the quiz, so a block between an
        # anchor and a rotator cannot be fully avoided by rotation alone.
        UserBlock.objects.create(blocker=women[0], blocked=men[0], reason="other")

        result = generate_rotation_rounds(quiz)

        assert blocked_table_conflict_count(quiz) >= 1
        assert any("blocked pair" in w for w in result["warnings"])
        assert any("blocked pair" in w for w in compute_rotation_warnings(quiz))

    def test_no_blocks_means_no_conflict_warning(self, quiz_event_4t):  # noqa: F811
        quiz = quiz_event_4t
        for i in range(2):
            _check_in_attended(quiz, _make_user(f"nb_m{i}", "M"))
        for i in range(4):
            _check_in_attended(quiz, _make_user(f"nb_f{i}", "F"))

        result = generate_rotation_rounds(quiz)

        assert blocked_table_conflict_count(quiz) == 0
        assert not any("blocked pair" in w for w in result["warnings"])


@pytest.mark.django_db
class TestQuizBlockPolicies:
    def _unseated_attendee(self, quiz, name, gender):
        from django.utils import timezone

        from crush_lu.models.events import EventRegistration

        user = _make_user(name, gender)
        EventRegistration.objects.create(
            event=quiz.event,
            user=user,
            status="attended",
            checked_in_at=timezone.now(),
        )
        return user

    def test_manual_assign_warns_but_still_places(self, quiz_event_4t):  # noqa: F811
        from crush_lu.services.quiz_rotation import manual_assign_table

        quiz = quiz_event_4t
        man = _make_user("man_m0", "M")
        _check_in_attended(quiz, man)
        woman = self._unseated_attendee(quiz, "man_f0", "F")
        UserBlock.objects.create(blocker=woman, blocked=man, reason="other")
        man_table = QuizRotationSchedule.objects.get(
            quiz=quiz, round_number=0, user=man
        ).table.table_number

        result = manual_assign_table(quiz, woman, man_table)

        assert result["table_number"] == man_table
        assert result["warnings"]
        assert _table_of(quiz, woman) == _table_of(quiz, man)

    def test_manual_assign_without_block_has_no_warning(
        self, quiz_event_4t
    ):  # noqa: F811
        from crush_lu.services.quiz_rotation import manual_assign_table

        quiz = quiz_event_4t
        man = _make_user("nw_m0", "M")
        _check_in_attended(quiz, man)
        woman = self._unseated_attendee(quiz, "nw_f0", "F")

        result = manual_assign_table(quiz, woman, 1)

        assert "warnings" not in result

    def test_consolidate_avoids_a_table_with_a_blocked_member(
        self, quiz_event_4t
    ):  # noqa: F811
        from crush_lu.services.quiz_rotation import consolidate_tables

        quiz = quiz_event_4t
        men = [_make_user(f"con_m{i}", "M") for i in range(3)]
        women = [_make_user(f"con_f{i}", "F") for i in range(4)]
        for user in men + women:
            _check_in_attended(quiz, user)
        mover = QuizRotationSchedule.objects.get(
            quiz=quiz, round_number=0, table__table_number=4
        ).user
        plan = consolidate_tables(quiz, apply=False)
        planned_table = next(
            m["to_table"] for m in plan["moves"] if m["user_id"] == mover.pk
        )
        anchor_there = QuizRotationSchedule.objects.get(
            quiz=quiz,
            round_number=0,
            role="anchor",
            table__table_number=planned_table,
        ).user
        UserBlock.objects.create(blocker=anchor_there, blocked=mover, reason="other")

        replanned = consolidate_tables(quiz, apply=False)

        new_table = next(
            m["to_table"] for m in replanned["moves"] if m["user_id"] == mover.pk
        )
        assert new_table != planned_table

    def test_conflicts_in_finished_rounds_stop_warning(
        self, quiz_event_4t
    ):  # noqa: F811
        quiz = quiz_event_4t
        man = _make_user("past_m0", "M")
        woman = _make_user("past_f0", "F")
        # Both land on table 1 at check-in (no block yet).
        _check_in_attended(quiz, man)
        _check_in_attended(quiz, woman)
        UserBlock.objects.create(blocker=woman, blocked=man, reason="other")
        assert blocked_table_conflict_count(quiz) == 1

        # Round index 1 is now live: round 0 is over and cannot be fixed.
        quiz.current_round = quiz.rounds.order_by("sort_order")[1]
        quiz.status = "active"
        quiz.save(update_fields=["current_round", "status"])

        assert blocked_table_conflict_count(quiz) == 0
