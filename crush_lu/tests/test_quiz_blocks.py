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

    def test_a_block_does_not_change_checkin_seating(self, quiz_event_4t):  # noqa: F811
        quiz = quiz_event_4t
        man = _make_user("same_m0", "M")
        _check_in_attended(quiz, man)
        woman = _make_user("same_f0", "F")
        UserBlock.objects.create(blocker=woman, blocked=man, reason="other")

        _check_in_attended(quiz, woman)

        # A block is "not interested", not a separation: same table as without.
        assert _table_of(quiz, woman) == _table_of(quiz, man)

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


@pytest.mark.django_db
class TestQuizStartWarnsAboutLateBlock:
    @pytest.fixture(autouse=True)
    def _keep_test_connection_open(self, monkeypatch):
        monkeypatch.setattr("channels.db.close_old_connections", lambda *a, **kw: None)

    def test_block_created_after_generation_still_warns_on_start(
        self, quiz_event_4t  # noqa: F811
    ):
        from asgiref.sync import async_to_sync

        from crush_lu.consumers import QuizConsumer

        quiz = quiz_event_4t
        men = [_make_user(f"late_m{i}", "M") for i in range(2)]
        women = [_make_user(f"late_f{i}", "F") for i in range(4)]
        for user in men + women:
            _check_in_attended(quiz, user)
        # Rounds 1+ already exist before anyone blocks anyone.
        generate_rotation_rounds(quiz)
        UserBlock.objects.create(blocker=women[0], blocked=men[0], reason="other")

        consumer = QuizConsumer()
        consumer.quiz_id = quiz.id
        result = async_to_sync(consumer.start_quiz_from_first_round)()

        assert not result.get("error")
        assert any("blocked pair" in w for w in result["host_warnings"])


@pytest.mark.django_db
class TestQuizBlocksMidQuiz:
    @pytest.fixture(autouse=True)
    def _keep_test_connection_open(self, monkeypatch):
        monkeypatch.setattr("channels.db.close_old_connections", lambda *a, **kw: None)

    def _seat(self, quiz):
        men = [_make_user(f"mid_m{i}", "M") for i in range(2)]
        women = [_make_user(f"mid_f{i}", "F") for i in range(4)]
        for user in men + women:
            _check_in_attended(quiz, user)
        generate_rotation_rounds(quiz)
        return men, women

    def test_consolidate_endpoint_returns_block_warnings(
        self, client, quiz_event_4t  # noqa: F811
    ):
        quiz = quiz_event_4t
        men = [_make_user(f"api_m{i}", "M") for i in range(3)]
        women = [_make_user(f"api_f{i}", "F") for i in range(4)]
        for user in men + women:
            _check_in_attended(quiz, user)
        # Block every man against every woman: no layout can avoid a shared
        # table, so the preview must carry a warning.
        for man in men:
            for woman in women:
                UserBlock.objects.create(blocker=man, blocked=woman, reason="other")
        assert client.login(username="consol_coach@test.com", password="testpass123")

        response = client.post(
            f"/api/quiz/{quiz.id}/consolidate-tables/",
            data="{}",
            content_type="application/json",
            HTTP_HOST="crush.lu",
        )

        assert response.status_code == 200
        body = response.json()
        assert body["changed"] is True
        assert any("blocked pair" in w for w in body["warnings"])

    def test_upcoming_conflicts_are_counted_without_the_current_round(
        self, quiz_event_4t  # noqa: F811
    ):
        quiz = quiz_event_4t
        men, women = self._seat(quiz)
        quiz.current_round = quiz.rounds.order_by("sort_order")[0]
        quiz.status = "active"
        quiz.save(update_fields=["current_round", "status"])
        # Every anchor/rotator pair meets in some round of a full rotation.
        for man in men:
            for woman in women:
                UserBlock.objects.create(blocker=man, blocked=woman, reason="other")

        assert blocked_table_conflict_count(quiz, upcoming_only=True) >= 1
        assert blocked_table_conflict_count(
            quiz, upcoming_only=True
        ) < blocked_table_conflict_count(quiz)

    def test_rotate_warns_the_host_after_a_self_heal_and_still_broadcasts(
        self, quiz_event_4t  # noqa: F811
    ):
        from unittest.mock import AsyncMock

        from asgiref.sync import async_to_sync

        from crush_lu.consumers import QuizConsumer

        quiz = quiz_event_4t
        men = [_make_user(f"heal_m{i}", "M") for i in range(2)]
        women = [_make_user(f"heal_f{i}", "F") for i in range(4)]
        for user in men + women:
            _check_in_attended(quiz, user)
        # No rotation rows beyond round 0: the advance has to self-heal.
        assert not QuizRotationSchedule.objects.filter(
            quiz=quiz, round_number=1
        ).exists()
        quiz.current_round = quiz.rounds.order_by("sort_order")[0]
        quiz.status = "active"
        quiz.save(update_fields=["current_round", "status"])
        for man in men:
            for woman in women:
                UserBlock.objects.create(blocker=man, blocked=woman, reason="other")

        consumer = QuizConsumer()
        consumer.quiz_id = quiz.id
        consumer.quiz_group = f"quiz_{quiz.id}"
        consumer.send_error = AsyncMock()
        consumer.check_can_rotate = AsyncMock(return_value={})
        consumer.channel_layer = AsyncMock()

        async_to_sync(consumer.handle_rotate)()

        messages = [call.args[0] for call in consumer.send_error.await_args_list]
        assert any("blocked pair" in m for m in messages)
        # Warn-only: the room still rotates, and the warning is not in it.
        consumer.channel_layer.group_send.assert_awaited_once()
        sent = consumer.channel_layer.group_send.await_args.args[1]
        assert sent["type"] == "quiz.rotate"
        assert "blocked" not in str(sent["data"])


@pytest.mark.django_db
class TestQuizBlockWarningSurfaces:
    """Every host surface (start, rotate, resume, manual assign, consolidate)
    reads the same helper; these cover the ones that regenerate or resume."""

    @pytest.fixture(autouse=True)
    def _keep_test_connection_open(self, monkeypatch):
        monkeypatch.setattr("channels.db.close_old_connections", lambda *a, **kw: None)

    def _block_all(self, men, women):
        for man in men:
            for woman in women:
                UserBlock.objects.create(blocker=man, blocked=woman, reason="other")

    def test_manual_assign_returns_regeneration_conflicts(
        self, quiz_event_4t  # noqa: F811
    ):
        from django.utils import timezone

        from crush_lu.models.events import EventRegistration
        from crush_lu.services.quiz_rotation import manual_assign_table

        quiz = quiz_event_4t
        men = [_make_user(f"reg_m{i}", "M") for i in range(2)]
        women = [_make_user(f"reg_f{i}", "F") for i in range(4)]
        for user in men + women:
            _check_in_attended(quiz, user)
        generate_rotation_rounds(quiz)
        quiz.status = "active"
        quiz.current_round = quiz.rounds.order_by("sort_order")[0]
        quiz.save(update_fields=["status", "current_round"])
        late = _make_user("reg_late", "F")
        EventRegistration.objects.create(
            event=quiz.event,
            user=late,
            status="attended",
            checked_in_at=timezone.now(),
        )
        UserBlock.objects.create(blocker=late, blocked=men[0], reason="other")
        UserBlock.objects.create(blocker=late, blocked=men[1], reason="other")
        anchor_tables = set(
            QuizRotationSchedule.objects.filter(
                quiz=quiz, round_number=0, role="anchor"
            ).values_list("table__table_number", flat=True)
        )
        safe_table = next(n for n in (1, 2, 3, 4) if n not in anchor_tables)

        result = manual_assign_table(quiz, late, safe_table)

        # Safe at round 0 (no move warning for this table), but the regenerated
        # rotation walks her past the anchors in a later round.
        assert any("blocked pair" in w for w in result.get("warnings", []))

    def test_consolidation_preview_and_apply_both_report_conflicts(
        self, client, quiz_event_4t  # noqa: F811
    ):
        import re

        quiz = quiz_event_4t
        men = [_make_user(f"cons_m{i}", "M") for i in range(3)]
        women = [_make_user(f"cons_f{i}", "F") for i in range(4)]
        for user in men + women:
            _check_in_attended(quiz, user)
        self._block_all(men, women)
        assert client.login(username="consol_coach@test.com", password="testpass123")
        url = f"/api/quiz/{quiz.id}/consolidate-tables/"

        preview = client.post(
            url, data="{}", content_type="application/json", HTTP_HOST="crush.lu"
        ).json()
        applied = client.post(
            url,
            data='{"apply": true}',
            content_type="application/json",
            HTTP_HOST="crush.lu",
        ).json()

        def count(body):
            text = next(w for w in body["warnings"] if "blocked pair" in w)
            return int(re.search(r"(\d+) blocked pair", text).group(1))

        assert count(preview) >= 1
        assert count(applied) >= 1
        # The preview simulates the same rotation that apply regenerates.
        assert count(preview) == count(applied)

    def test_resume_warns_the_host_only(self, quiz_event_4t):  # noqa: F811
        from unittest.mock import AsyncMock

        from asgiref.sync import async_to_sync

        from crush_lu.consumers import QuizConsumer

        quiz = quiz_event_4t
        men = [_make_user(f"res2_m{i}", "M") for i in range(2)]
        women = [_make_user(f"res2_f{i}", "F") for i in range(4)]
        for user in men + women:
            _check_in_attended(quiz, user)
        generate_rotation_rounds(quiz)
        quiz.status = "paused"
        quiz.current_round = quiz.rounds.order_by("sort_order")[0]
        quiz.save(update_fields=["status", "current_round"])
        # The block appears while the quiz is paused.
        self._block_all(men, women)

        consumer = QuizConsumer()
        consumer.quiz_id = quiz.id
        consumer.quiz_group = f"quiz_{quiz.id}"
        consumer.send_error = AsyncMock()
        consumer.channel_layer = AsyncMock()

        async_to_sync(consumer.handle_resume_quiz)()

        messages = [call.args[0] for call in consumer.send_error.await_args_list]
        assert any("blocked pair" in m for m in messages)
        broadcast = " ".join(
            str(call.args) for call in consumer.channel_layer.group_send.await_args_list
        )
        assert "blocked" not in broadcast


@pytest.mark.django_db
class TestConsolidationIgnoresBlocksForPlacement:
    def test_blocks_do_not_change_the_planned_moves(self, quiz_event_4t):  # noqa: F811
        from crush_lu.services.quiz_rotation import consolidate_tables

        quiz = quiz_event_4t
        men = [_make_user(f"plan_m{i}", "M") for i in range(3)]
        women = [_make_user(f"plan_f{i}", "F") for i in range(4)]
        for user in men + women:
            _check_in_attended(quiz, user)
        before = consolidate_tables(quiz, apply=False)["moves"]
        for man in men:
            for woman in women:
                UserBlock.objects.create(blocker=man, blocked=woman, reason="other")

        result = consolidate_tables(quiz, apply=False)

        assert [(m["user_id"], m["to_table"]) for m in result["moves"]] == [
            (m["user_id"], m["to_table"]) for m in before
        ]
        # ... while the host is still told, neutrally.
        assert any("may not want to be together" in w for w in result["warnings"])


@pytest.mark.django_db
class TestDistinctPairCounting:
    def test_one_pair_seated_together_in_several_rounds_counts_once(
        self, quiz_event_4t  # noqa: F811
    ):
        quiz = quiz_event_4t
        man = _make_user("once_m0", "M")
        woman = _make_user("once_f0", "F")
        _check_in_attended(quiz, man)
        _check_in_attended(quiz, woman)
        table = QuizRotationSchedule.objects.get(
            quiz=quiz, round_number=0, user=man
        ).table
        for round_number in (1, 2):
            for user, role in ((man, "anchor"), (woman, "rotator")):
                QuizRotationSchedule.objects.create(
                    quiz=quiz,
                    round_number=round_number,
                    table=table,
                    user=user,
                    role=role,
                )
        UserBlock.objects.create(blocker=woman, blocked=man, reason="other")

        # Three rounds at one table is still one blocked pair.
        assert blocked_table_conflict_count(quiz) == 1


@pytest.mark.django_db
class TestQuizBlockWarningsFromCheckinAndLowAttendance:
    @pytest.fixture(autouse=True)
    def _keep_test_connection_open(self, monkeypatch):
        monkeypatch.setattr("channels.db.close_old_connections", lambda *a, **kw: None)

    def test_low_attendance_message_still_carries_block_conflicts(
        self, quiz_event_4t  # noqa: F811
    ):
        from crush_lu.models.events import EventRegistration

        quiz = quiz_event_4t
        men = [_make_user(f"low_m{i}", "M") for i in range(2)]
        women = [_make_user(f"low_f{i}", "F") for i in range(4)]
        for user in men + women:
            _check_in_attended(quiz, user)
        for man in men:
            for woman in women:
                UserBlock.objects.create(blocker=man, blocked=woman, reason="other")
        generate_rotation_rounds(quiz)
        # Undo check-ins down to three attendees; the schedule rows persist.
        EventRegistration.objects.filter(
            event=quiz.event, user__in=women[2:] + men[:1]
        ).update(status="confirmed")

        warnings = compute_rotation_warnings(quiz)

        assert any("Only 3 attended" in w for w in warnings)
        assert any("blocked pair" in w for w in warnings)

    def test_late_checkin_returns_host_warnings_for_the_blocked_pair(
        self, quiz_event_4t  # noqa: F811
    ):
        from crush_lu.services.quiz_rotation import assign_table_on_checkin

        quiz = quiz_event_4t
        men = [_make_user(f"late_m{i}", "M") for i in range(2)]
        women = [_make_user(f"late_f{i}", "F") for i in range(3)]
        for user in men + women:
            _check_in_attended(quiz, user)
        generate_rotation_rounds(quiz)
        quiz.current_round = quiz.rounds.order_by("sort_order")[0]
        quiz.status = "active"
        quiz.save(update_fields=["current_round", "status"])
        late = _make_user("late_f9", "F")
        for man in men:
            UserBlock.objects.create(blocker=man, blocked=late, reason="other")
        from crush_lu.models.events import EventRegistration
        from django.utils import timezone

        EventRegistration.objects.update_or_create(
            event=quiz.event,
            user=late,
            defaults={"status": "attended", "checked_in_at": timezone.now()},
        )

        assignment = assign_table_on_checkin(quiz, late)

        assert any("blocked pair" in w for w in assignment["warnings"])

    def test_checkin_broadcast_sends_one_host_only_warning(self):
        from unittest.mock import MagicMock, patch

        from crush_lu import views_checkin

        layer = MagicMock()
        layer.group_send = MagicMock()
        event = MagicMock(id=7)
        event.quiz.id = 11

        with patch.object(views_checkin, "get_channel_layer", return_value=layer), patch.object(
            views_checkin, "async_to_sync", side_effect=lambda f: f
        ), patch("crush_lu.models.quiz.QuizTable") as table_model:
            table_model.objects.filter.return_value.first.return_value = None
            views_checkin._broadcast_quiz_table_update(
                event,
                {"table_number": None, "warnings": ["a blocked pair", "second"]},
            )

        errors = [
            call.args
            for call in layer.group_send.call_args_list
            if call.args[1].get("type") == "quiz.error"
        ]
        assert errors == [
            ("quiz_11_host", {"type": "quiz.error", "data": {"message": "a blocked pair second"}})
        ]

    def test_start_warnings_reach_the_host_as_one_message(self):
        from unittest.mock import AsyncMock

        from asgiref.sync import async_to_sync

        from crush_lu.consumers import QuizConsumer

        consumer = QuizConsumer()
        consumer.quiz_group = "quiz_1"
        consumer._total_tables = 2
        consumer.send_error = AsyncMock()
        consumer.channel_layer = AsyncMock()
        consumer.start_quiz_from_first_round = AsyncMock(
            return_value={
                "round_info": {},
                "question_data": None,
                "host_warnings": ["no anchors", "a blocked pair"],
            }
        )

        async_to_sync(consumer.handle_start_quiz)()

        consumer.send_error.assert_awaited_once_with("no anchors a blocked pair")

    def test_undo_returns_regenerated_warnings_for_the_host(
        self, quiz_event_4t  # noqa: F811
    ):
        from crush_lu.models.events import EventRegistration
        from crush_lu.services.quiz_rotation import release_table_on_undo

        quiz = quiz_event_4t
        men = [_make_user(f"undo_m{i}", "M") for i in range(2)]
        women = [_make_user(f"undo_f{i}", "F") for i in range(5)]
        for user in men + women:
            _check_in_attended(quiz, user)
        generate_rotation_rounds(quiz)
        quiz.current_round = quiz.rounds.order_by("sort_order")[0]
        quiz.status = "active"
        quiz.save(update_fields=["current_round", "status"])
        for man in men:
            for woman in women:
                UserBlock.objects.create(blocker=man, blocked=woman, reason="other")
        EventRegistration.objects.filter(
            event=quiz.event, user=women[-1]
        ).update(status="confirmed")

        released = release_table_on_undo(quiz, women[-1])

        assert any("blocked pair" in w for w in released["warnings"])
