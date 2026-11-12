# ruff: noqa: F811
"""Between two rounds, the player page says where the player sits next.

Nothing stores "round complete": the quiz stays on the round's last question.
The state sent on connect derives it, so a reload between rounds shows the
next table and not that question again.
"""

import json
import re
from pathlib import Path

import pytest
from asgiref.sync import async_to_sync
from django.template import Context, Template
from django.test import SimpleTestCase
from django.utils import translation

from crush_lu.tests.test_quiz import (  # noqa: F401
    _grant_consent,
    coach_user,
    quiz_event,
)
from crush_lu.tests.test_quiz_host_safety import (  # noqa: F401
    _consumer,
    _keep_test_connection_open,
    _score_all,
    running,
)

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "crush_lu" / "templates" / "crush_lu" / "quiz_live.html"
QUIZ_JS = ROOT / "crush_lu" / "static" / "crush_lu" / "js" / "quiz-live.js"


def _state(quiz):
    return async_to_sync(_consumer(quiz).get_quiz_state)()


@pytest.mark.django_db
class TestRoundCompleteState:
    def _quiz_night(self, quiz):
        quiz.event.event_type = "quiz_night"
        quiz.event.save(update_fields=["event_type"])

    def test_not_complete_while_questions_remain(self, running):
        quiz, _rounds, questions, tables = running
        self._quiz_night(quiz)
        _score_all(quiz, questions[0], tables)

        assert _state(quiz)["round_complete"] is False

    def test_not_complete_until_the_last_question_is_scored(self, running):
        quiz, _rounds, questions, tables = running
        self._quiz_night(quiz)
        quiz.current_question_index = 1
        quiz.save()
        _score_all(quiz, questions[1], tables[:1])

        assert _state(quiz)["round_complete"] is False

    def test_complete_when_the_last_question_is_scored(self, running):
        quiz, _rounds, questions, tables = running
        self._quiz_night(quiz)
        quiz.current_question_index = 1
        quiz.save()
        _score_all(quiz, questions[1], tables)

        state = _state(quiz)
        assert state["round_complete"] is True
        # The host page still reads the question from the same payload.
        assert state["question"]["id"] == questions[1].id

    def test_last_round_has_no_break(self, running):
        quiz, rounds, questions, tables = running
        self._quiz_night(quiz)
        quiz.current_round = rounds[1]
        quiz.current_question_index = 1
        quiz.save()
        _score_all(quiz, questions[3], tables)

        assert _state(quiz)["round_complete"] is False

    def test_resume_between_rounds_marks_the_resent_question(self, running):
        quiz, _rounds, questions, tables = running
        self._quiz_night(quiz)
        quiz.current_question_index = 1
        quiz.status = "paused"
        quiz.save()
        _score_all(quiz, questions[1], tables)

        consumer = _consumer(quiz)
        async_to_sync(consumer.handle_resume_quiz)()

        sent = [
            call.args[1]
            for call in consumer.channel_layer.group_send.await_args_list
            if call.args[1]["type"] == "quiz.question"
        ]
        assert len(sent) == 1
        assert sent[0]["data"]["round_complete"] is True


class RoundBreakCopyTests(SimpleTestCase):
    def test_table_messages_are_translated_and_stay_valid_json(self):
        page = PAGE.read_text(encoding="utf-8")
        attribute = re.search(r"data-i18n='([^']*)'", page).group(1)
        expected = {
            "en": (
                "Next round: you move to Table {n}",
                "Next round: you stay at your table",
                "Move to Table {n}!",
            ),
            "de": (
                "Nächste Runde: Du wechselst zu Tisch {n}",
                "Nächste Runde: Du bleibst an deinem Tisch",
                "Geh zu Tisch {n}!",
            ),
            "fr": (
                "Manche suivante : vous passez à la table {n}",
                "Manche suivante : vous restez à votre table",
                "Rendez-vous à la table {n} !",
            ),
        }
        for lang, (move, stay, rotate) in expected.items():
            with self.subTest(lang=lang), translation.override(lang):
                rendered = Template("{% load i18n %}" + attribute).render(Context())
                strings = json.loads(rendered)
                self.assertEqual(strings["nextRoundMove"], move)
                self.assertEqual(strings["nextRoundStay"], stay)
                self.assertEqual(strings["moveToTable"], rotate)
                self.assertTrue(strings["stayAtTable"])
                self.assertTrue(strings["moveToNextTable"])
                self.assertTrue(strings["nextRoundSoon"])

    def test_player_script_has_no_hardcoded_table_messages(self):
        js = QUIZ_JS.read_text(encoding="utf-8")
        player = js[
            js.index('Alpine.data("quizLive"') : js.index('Alpine.data("quizHost"')
        ]
        self.assertNotIn('"Move to Table "', player)
        self.assertNotIn('"Stay at your table!"', player)
        self.assertNotIn('"Next: Table "', player)
        self.assertIn("this._i18n.nextRoundMove", player)

    def test_round_end_no_longer_shows_the_start_screen(self):
        js = QUIZ_JS.read_text(encoding="utf-8")
        handler = js[js.index('data.status === "round_complete"') :][:200]
        self.assertIn("_enterRoundBreak()", handler)
        page = PAGE.read_text(encoding="utf-8")
        self.assertIn('x-show="isRoundBreak"', page)
        self.assertIn('x-text="roundBreakMessage"', page)
