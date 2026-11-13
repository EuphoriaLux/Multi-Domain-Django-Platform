# ruff: noqa: F811, RUF059
"""Host-safety guards for the live quiz night (review 2026-11-12, M1-M4).

Next / Rotate refuse on the server when the quiz is not active or the last
question is not fully scored, Reopen brings a finished quiz back without
touching scores, and Reset logs the standings before it deletes them.

The consumer is driven through its sync helpers (no WebSocket), the same way
test_quiz.py does it.
"""

import logging
from unittest.mock import AsyncMock

import pytest
from asgiref.sync import async_to_sync

from crush_lu.consumers import QuizConsumer
from crush_lu.models.quiz import (
    IndividualScore,
    QuizQuestion,
    QuizRound,
    QuizTable,
    TableRoundScore,
)
from crush_lu.tests.test_quiz import (  # noqa: F401
    _grant_consent,
    coach_user,
    quiz_event,
)


@pytest.fixture(autouse=True)
def _keep_test_connection_open(monkeypatch):
    monkeypatch.setattr("channels.db.close_old_connections", lambda *a, **kw: None)


def _consumer(quiz):
    consumer = QuizConsumer()
    consumer.quiz_id = quiz.id
    consumer.quiz_group = f"quiz_{quiz.id}"
    consumer.send_error = AsyncMock()
    consumer.channel_layer = AsyncMock()
    consumer._total_tables = 2
    return consumer


@pytest.fixture
def running(quiz_event):
    """2 rounds x 2 questions, 2 tables, active on round 0, question 0 shown."""
    rounds = []
    questions = []
    for i in range(2):
        rnd = QuizRound.objects.create(quiz=quiz_event, title=f"R{i}", sort_order=i)
        rounds.append(rnd)
        for j in range(2):
            questions.append(
                QuizQuestion.objects.create(
                    round=rnd,
                    text=f"Q{j} of R{i}",
                    question_type="multiple_choice",
                    choices=[{"text": "A", "is_correct": True}],
                    sort_order=j,
                    points=10,
                )
            )
    tables = [QuizTable.objects.create(quiz=quiz_event, table_number=n) for n in (1, 2)]
    quiz_event.num_tables = 2
    quiz_event.current_round = rounds[0]
    quiz_event.current_question_index = 0
    quiz_event.status = "active"
    quiz_event.save()
    return quiz_event, rounds, questions, tables


def _score_all(quiz, question, tables, correct=True):
    for t in tables:
        TableRoundScore.objects.create(
            quiz=quiz, table=t, question=question, is_correct=correct
        )


class TestScoreHoldRelay:
    """The held last score is shown on every host screen, not only one."""

    def _host(self, quiz):
        consumer = _consumer(quiz)
        consumer.host_group = f"quiz_{quiz.id}_host"
        consumer.channel_name = "host-a"
        consumer.send_json = AsyncMock()
        return consumer

    def test_hold_goes_to_the_host_group_only(self):
        quiz = type("Q", (), {"id": 7})()
        consumer = self._host(quiz)

        async_to_sync(consumer.handle_score_hold)(
            {"table_id": 3, "question_id": 9, "is_correct": True, "seconds": 99},
            active=True,
        )

        group, message = consumer.channel_layer.group_send.await_args.args
        assert group == "quiz_7_host"
        assert message["type"] == "quiz.score_hold"
        assert message["sender"] == "host-a"
        assert message["data"] == {
            "table_id": 3,
            "question_id": 9,
            "active": True,
            "seconds": 10,
            "is_correct": True,
        }

    def test_undo_is_relayed(self):
        quiz = type("Q", (), {"id": 7})()
        consumer = self._host(quiz)

        async_to_sync(consumer.handle_score_hold)(
            {"table_id": 3, "question_id": 9}, active=False
        )

        message = consumer.channel_layer.group_send.await_args.args[1]
        assert message["data"] == {"table_id": 3, "question_id": 9, "active": False}

    def test_bad_ids_and_non_hosts_send_nothing(self):
        quiz = type("Q", (), {"id": 7})()
        consumer = self._host(quiz)
        async_to_sync(consumer.handle_score_hold)(
            {"table_id": "3", "question_id": 9}, active=True
        )
        consumer.host_group = None
        async_to_sync(consumer.handle_score_hold)(
            {"table_id": 3, "question_id": 9}, active=True
        )
        consumer.channel_layer.group_send.assert_not_awaited()

    def test_the_holding_host_does_not_get_its_own_hold_back(self):
        quiz = type("Q", (), {"id": 7})()
        consumer = self._host(quiz)
        event = {"data": {"table_id": 3}, "sender": "host-a"}

        async_to_sync(consumer.quiz_score_hold)(event)
        consumer.send_json.assert_not_awaited()

        event["sender"] = "host-b"
        async_to_sync(consumer.quiz_score_hold)(event)
        consumer.send_json.assert_awaited_once_with(
            {"type": "quiz.score_hold", "data": {"table_id": 3}}
        )

    def test_hold_actions_are_host_only(self):
        import inspect

        source = inspect.getsource(QuizConsumer.receive_json)
        host_actions = source[source.index("host_actions = {") :]
        host_actions = host_actions[: host_actions.index("}")]
        assert '"hold_score"' in host_actions
        assert '"cancel_hold"' in host_actions


@pytest.mark.django_db
class TestNextQuestionGuards:
    def test_next_refused_while_paused_does_not_end_the_quiz(self, running):
        quiz, rounds, questions, tables = running
        # Last round, last question shown and fully scored: the exact setup
        # in which a stray Next used to read as "round complete" and finish.
        quiz.current_round = rounds[1]
        quiz.current_question_index = 1
        quiz.status = "paused"
        quiz.save()
        _score_all(quiz, questions[3], tables)

        consumer = _consumer(quiz)
        async_to_sync(consumer.handle_next_question)()

        quiz.refresh_from_db()
        assert quiz.status == "paused"
        assert quiz.current_question_index == 1
        consumer.send_error.assert_awaited_once()
        consumer.channel_layer.group_send.assert_not_awaited()

    def test_next_refused_when_finished_broadcasts_nothing(self, running):
        quiz, rounds, questions, tables = running
        quiz.current_round = rounds[1]
        quiz.current_question_index = 1
        quiz.status = "finished"
        quiz.save()
        _score_all(quiz, questions[3], tables)

        consumer = _consumer(quiz)
        async_to_sync(consumer.handle_next_question)()

        quiz.refresh_from_db()
        assert quiz.status == "finished"
        assert quiz.current_question_index == 1
        consumer.send_error.assert_awaited_once()
        consumer.channel_layer.group_send.assert_not_awaited()

    def test_next_refused_when_a_table_is_unscored(self, running):
        quiz, rounds, questions, tables = running
        TableRoundScore.objects.create(
            quiz=quiz, table=tables[0], question=questions[0], is_correct=True
        )

        consumer = _consumer(quiz)
        async_to_sync(consumer.handle_next_question)()

        quiz.refresh_from_db()
        assert quiz.current_question_index == 0
        consumer.send_error.assert_awaited_once()
        assert "scored" in consumer.send_error.await_args.args[0]
        consumer.channel_layer.group_send.assert_not_awaited()

    def test_next_advances_when_all_scored(self, running):
        quiz, rounds, questions, tables = running
        _score_all(quiz, questions[0], tables)

        consumer = _consumer(quiz)
        outcome = async_to_sync(consumer.advance_question)()

        assert outcome["question"]["index"] == 1
        quiz.refresh_from_db()
        assert quiz.current_question_index == 1

    def test_round_complete_requires_last_question_scored(self, running):
        quiz, rounds, questions, tables = running
        quiz.current_question_index = 1  # last question of round 0, unscored
        quiz.save()

        consumer = _consumer(quiz)
        outcome = async_to_sync(consumer.advance_question)()
        assert "error" in outcome
        assert "round_complete" not in outcome

        _score_all(quiz, questions[1], tables)
        outcome = async_to_sync(consumer.advance_question)()
        assert outcome == {"round_complete": True}

    def test_last_round_exhausted_and_scored_still_finishes(self, running):
        quiz, rounds, questions, tables = running
        quiz.current_round = rounds[1]
        quiz.current_question_index = 1
        quiz.save()
        _score_all(quiz, questions[3], tables)

        consumer = _consumer(quiz)
        consumer.get_leaderboard = AsyncMock(return_value={"tables": []})
        async_to_sync(consumer.handle_next_question)()

        quiz.refresh_from_db()
        assert quiz.status == "finished"
        consumer.send_error.assert_not_awaited()


@pytest.mark.django_db
class TestRotateGuards:
    def test_rotate_refused_while_paused(self, running):
        quiz, rounds, questions, tables = running
        quiz.current_question_index = 1
        quiz.status = "paused"
        quiz.save()
        _score_all(quiz, questions[1], tables)

        consumer = _consumer(quiz)
        result = async_to_sync(consumer.advance_round_and_rotate)()

        assert "error" in result
        quiz.refresh_from_db()
        assert quiz.current_round_id == rounds[0].pk
        assert quiz.status == "paused"

    def test_rotate_refused_when_check_can_rotate_fails_under_lock(self, running):
        quiz, rounds, questions, tables = running
        quiz.current_question_index = 1  # last question, nobody scored it
        quiz.save()

        consumer = _consumer(quiz)
        result = async_to_sync(consumer.advance_round_and_rotate)()

        assert "error" in result
        assert "scored" in result["error"]
        quiz.refresh_from_db()
        assert quiz.current_round_id == rounds[0].pk
        assert quiz.current_question_index == 1


@pytest.mark.django_db
class TestReopen:
    def test_reopen_only_from_finished(self, running):
        quiz, rounds, questions, tables = running
        consumer = _consumer(quiz)
        result = async_to_sync(consumer.reopen_quiz)()
        assert "error" in result
        quiz.refresh_from_db()
        assert quiz.status == "active"

        quiz.status = "paused"
        quiz.save()
        assert "error" in async_to_sync(consumer.reopen_quiz)()

    def test_reopen_keeps_scores_and_position(self, running):
        quiz, rounds, questions, tables = running
        quiz.current_round = rounds[1]
        quiz.current_question_index = 1
        quiz.status = "finished"
        quiz.save()
        _score_all(quiz, questions[3], tables)

        consumer = _consumer(quiz)
        async_to_sync(consumer.handle_reopen_quiz)()

        quiz.refresh_from_db()
        assert quiz.status == "paused"
        assert quiz.current_round_id == rounds[1].pk
        assert quiz.current_question_index == 1
        assert TableRoundScore.objects.filter(quiz=quiz).count() == 2
        consumer.send_error.assert_not_awaited()
        consumer.channel_layer.group_send.assert_awaited_once()
        sent = consumer.channel_layer.group_send.await_args.args[1]
        assert sent["type"] == "quiz.status"
        assert sent["data"]["status"] == "paused"

    def test_reopen_error_goes_to_host_only(self, running):
        quiz, *_ = running
        consumer = _consumer(quiz)
        async_to_sync(consumer.handle_reopen_quiz)()
        consumer.send_error.assert_awaited_once()
        consumer.channel_layer.group_send.assert_not_awaited()

    def test_reopen_is_a_host_only_action(self):
        # Registered in the host-action gate: read the source rather than
        # drive a socket.
        import inspect

        source = inspect.getsource(QuizConsumer.receive_json)
        assert '"reopen_quiz",' in source.split("if action in host_actions")[0]

    def test_resume_after_reopen_works(self, running):
        quiz, rounds, questions, tables = running
        quiz.status = "finished"
        quiz.save()
        consumer = _consumer(quiz)
        async_to_sync(consumer.reopen_quiz)()
        result = async_to_sync(consumer.set_quiz_status)("active")
        assert result == {"status": "active"}


@pytest.mark.django_db
class TestResetLogging:
    def test_reset_logs_standings_without_member_data_and_still_clears(
        self, running, coach_user, caplog
    ):
        from crush_lu.services.quiz_rotation import reset_quiz_to_draft

        quiz, rounds, questions, tables = running
        TableRoundScore.objects.create(
            quiz=quiz, table=tables[0], question=questions[0], is_correct=True
        )
        TableRoundScore.objects.create(
            quiz=quiz, table=tables[1], question=questions[0], is_correct=False
        )
        IndividualScore.objects.create(
            quiz=quiz,
            user=coach_user,
            question=questions[0],
            is_correct=True,
            points_earned=10,
            answer="table_scored:1",
        )
        quiz.status = "finished"
        quiz.save()

        with caplog.at_level(logging.INFO, logger="crush_lu.services.quiz_rotation"):
            result = reset_quiz_to_draft(quiz, clear_scores=True)

        records = [r for r in caplog.records if "table standings" in r.getMessage()]
        assert len(records) == 1
        message = records[0].getMessage()
        assert str(quiz.pk) in message
        assert "'table': 1" in message and "'total': 10" in message
        assert "'table': 2" in message and "'total': 0" in message
        assert coach_user.email not in message
        assert str(coach_user.pk) not in message.replace(str(quiz.pk), "")

        assert result["status"] == "draft"
        assert TableRoundScore.objects.filter(quiz=quiz).count() == 0
        assert IndividualScore.objects.filter(quiz=quiz).count() == 0

    def test_reset_without_clear_scores_logs_nothing(self, running, caplog):
        from crush_lu.services.quiz_rotation import reset_quiz_to_draft

        quiz, *_ = running
        quiz.status = "finished"
        quiz.save()
        with caplog.at_level(logging.INFO, logger="crush_lu.services.quiz_rotation"):
            reset_quiz_to_draft(quiz, clear_scores=False)
        assert not [r for r in caplog.records if "table standings" in r.getMessage()]
