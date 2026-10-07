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
