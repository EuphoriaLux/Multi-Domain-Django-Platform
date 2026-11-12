"""Hidden words in quiz questions: ``{{word}}`` is masked before the reveal."""

import json
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import RequestFactory
from django.utils import timezone

from crush_lu.models.quiz import QuizEvent, QuizQuestion, QuizRound, QuizTable
from crush_lu.services.quiz_blanks import (
    BLANK_MASK,
    has_blanks,
    mask_blanks,
    reveal_segments,
    revealed_segments_payload,
)

WORD = "paradise"
LYRIC = "With the taste of the poison, {{paradise}}\nI'm addicted to you"


class TestMarkup:
    def test_mask_replaces_the_word_with_a_fixed_length_blank(self):
        assert mask_blanks("a {{paradise}} b") == f"a {BLANK_MASK} b"
        # The blank does not give away the length of the word.
        assert mask_blanks("{{no}}") == mask_blanks("{{extraordinarily}}")

    def test_mask_handles_several_words_and_spaces_inside_the_braces(self):
        assert mask_blanks("{{ one }} and {{two}}") == f"{BLANK_MASK} and {BLANK_MASK}"

    def test_text_without_markup_is_untouched(self):
        assert mask_blanks("plain ___ text") == "plain ___ text"
        assert mask_blanks("") == ""
        assert mask_blanks(None) is None

    def test_empty_braces_are_not_a_blank(self):
        assert not has_blanks("{{}} and {{  }}")
        assert mask_blanks("{{}}") == "{{}}"

    def test_segments_alternate_text_and_words(self):
        assert reveal_segments("a {{b}} c {{d}}") == ["a ", "b", " c ", "d", ""]
        assert reveal_segments("no markup") == ["no markup"]

    def test_payload_is_empty_for_a_question_without_markup(self):
        question = QuizQuestion(text="Plain?", text_en="Plain?")
        assert revealed_segments_payload(question) == {}

    def test_payload_covers_every_language_once_one_has_markup(self):
        question = QuizQuestion(
            text=LYRIC, text_en=LYRIC, text_fr="Sans masque", text_de=""
        )
        payload = revealed_segments_payload(question)
        assert payload["revealed_segments_en"][1] == WORD
        # A language without markup still gets an entry, so a French screen
        # never falls back to the English answer under French text.
        assert payload["revealed_segments_fr"] == ["Sans masque"]
        assert "revealed_segments_de" not in payload


@pytest.fixture
def quiz(db):
    from crush_lu.models import MeetupEvent

    user = User.objects.create_user(
        username="blankcoach@test.com",
        email="blankcoach@test.com",
        password="x",
        is_staff=True,
    )
    event = MeetupEvent.objects.create(
        title="Quiz Night",
        description="d",
        event_type="quiz_night",
        date_time=timezone.now() + timedelta(days=1),
        location="Luxembourg",
        address="1 Place d'Armes",
        max_participants=30,
        registration_deadline=timezone.now() + timedelta(hours=12),
        is_published=True,
    )
    return QuizEvent.objects.create(event=event, status="active", created_by=user)


@pytest.fixture
def lyric_question(quiz):
    quiz_round = QuizRound.objects.create(
        quiz=quiz, title="Music", sort_order=0, time_per_question=30
    )
    question = QuizQuestion.objects.create(
        round=quiz_round,
        text=LYRIC,
        text_en=LYRIC,
        question_type="open_ended",
        correct_answer=WORD,
        correct_answer_en=WORD,
        points=10,
        sort_order=0,
    )
    quiz.current_round = quiz_round
    quiz.current_question_index = 0
    quiz.save(update_fields=["current_round", "current_question_index"])
    return question


@pytest.mark.django_db
class TestTheWordNeverLeavesBeforeTheReveal:
    def test_broadcast_payload_does_not_carry_the_word(self, lyric_question):
        from crush_lu.consumers import _build_question_data

        # include_answers=True is the payload broadcast to the whole group
        # (non-hosts only lose the answer keys), so it is the one that matters.
        data = _build_question_data(lyric_question, include_answers=True)
        broadcast = {
            k: v
            for k, v in data.items()
            if not k.startswith(("choices_with_answers", "correct_answer"))
        }
        assert WORD not in json.dumps(broadcast)
        assert BLANK_MASK in broadcast["text"]
        assert BLANK_MASK in broadcast["text_en"]
        # The host still gets the reference answer, as before.
        assert data["correct_answer"] == WORD

    def test_state_endpoint_does_not_carry_the_word(self, quiz, lyric_question):
        from rest_framework.test import APIRequestFactory, force_authenticate

        from crush_lu import api_quiz

        request = APIRequestFactory().get(f"/api/quiz/{quiz.id}/state/")
        force_authenticate(request, user=quiz.created_by)
        response = api_quiz.quiz_state(request, quiz.id)
        assert response.status_code == 200
        assert WORD not in json.dumps(response.data)
        assert BLANK_MASK in response.data["question"]["text"]

    def test_display_polling_carries_the_word_only_after_the_last_table(
        self, quiz, lyric_question
    ):
        from crush_lu import views_quiz
        from crush_lu.models.quiz import TableRoundScore

        quiz.num_tables = 1
        quiz.save(update_fields=["num_tables"])
        quiz.ensure_tables()
        (table,) = QuizTable.objects.filter(quiz=quiz)
        request = RequestFactory().get(f"/api/quiz/{quiz.event_id}/tables/")

        before = views_quiz.quiz_table_display_data(request, quiz.event_id)
        assert WORD not in before.content.decode()

        TableRoundScore.objects.create(
            quiz=quiz, table=table, question=lyric_question, is_correct=True
        )
        after = json.loads(
            views_quiz.quiz_table_display_data(request, quiz.event_id).content
        )
        assert after["revealed_segments_en"] == [
            "With the taste of the poison, ",
            WORD,
            "\nI'm addicted to you",
        ]
        assert BLANK_MASK in after["question"]["text"]
        assert WORD not in after["question"]["text"]


@pytest.mark.django_db
class TestRevealOnScoring:
    @pytest.fixture(autouse=True)
    def _keep_test_connection_open(self, monkeypatch):
        monkeypatch.setattr("channels.db.close_old_connections", lambda *a, **kw: None)

    def _consumer(self, quiz):
        from unittest.mock import AsyncMock

        from crush_lu.consumers import QuizConsumer

        consumer = QuizConsumer()
        consumer.quiz_id = quiz.id
        consumer.channel_layer = AsyncMock()
        return consumer

    def test_the_word_comes_with_the_last_table_only(self, quiz, lyric_question):
        from asgiref.sync import async_to_sync

        quiz.num_tables = 2
        quiz.save(update_fields=["num_tables"])
        quiz.ensure_tables()
        first, second = QuizTable.objects.filter(quiz=quiz).order_by("table_number")
        consumer = self._consumer(quiz)

        one = async_to_sync(consumer.score_table_for_question)(
            first.id, lyric_question.id, True
        )
        assert not one["all_scored"]
        assert "revealed_segments" not in one
        assert WORD not in json.dumps(one)

        two = async_to_sync(consumer.score_table_for_question)(
            second.id, lyric_question.id, False
        )
        assert two["all_scored"]
        assert two["revealed_segments"]["revealed_segments_en"][1] == WORD

    def test_a_question_without_markup_adds_nothing_to_the_reveal(self, quiz):
        from asgiref.sync import async_to_sync

        quiz_round = QuizRound.objects.create(
            quiz=quiz, title="R", sort_order=0, time_per_question=30
        )
        question = QuizQuestion.objects.create(
            round=quiz_round, text="Plain?", question_type="open_ended", sort_order=0
        )
        quiz.num_tables = 1
        quiz.current_round = quiz_round
        quiz.save(update_fields=["num_tables", "current_round"])
        quiz.ensure_tables()
        (table,) = QuizTable.objects.filter(quiz=quiz)

        result = async_to_sync(self._consumer(quiz).score_table_for_question)(
            table.id, question.id, True
        )
        assert result["all_scored"]
        assert result["revealed_segments"] == {}
