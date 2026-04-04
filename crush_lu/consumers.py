import json
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.db.models import Sum
from django.utils import timezone

logger = logging.getLogger(__name__)


_QUIZ_LANGUAGES = ("en", "de", "fr")


def _parse_choices(choices):
    """Safely parse question choices — handles list-of-dicts, list-of-strings, and JSON strings."""
    if isinstance(choices, str):
        try:
            choices = json.loads(choices)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(choices, list):
        return []
    # Normalize: if items are plain strings, wrap them in {"text": ...}
    result = []
    for c in choices:
        if isinstance(c, dict):
            result.append(c)
        elif isinstance(c, str):
            result.append({"text": c, "is_correct": False})
    return result


def _build_question_data(question, include_answers=False):
    """Build multilingual question data dict for WebSocket broadcast.

    Includes ``text_<lang>`` and ``choices_<lang>`` for each configured language
    so the JS client can pick the correct translation at display time.
    The default ``text`` and ``choices`` fields use the current active language
    (fallback for backwards compatibility).
    """
    q_data = {
        "id": question.id,
        "text": question.text,
        "question_type": question.question_type,
        "points": question.points,
    }

    # Add per-language text
    for lang in _QUIZ_LANGUAGES:
        val = getattr(question, f"text_{lang}", None)
        if val:
            q_data[f"text_{lang}"] = val

    if question.question_type in ("multiple_choice", "true_false"):
        # Default choices (active language)
        choices = _parse_choices(question.choices)
        q_data["choices"] = [
            {"text": c["text"]} for c in choices if isinstance(c, dict)
        ]
        if include_answers:
            q_data["choices_with_answers"] = choices

        # Per-language choices
        for lang in _QUIZ_LANGUAGES:
            lang_choices = getattr(question, f"choices_{lang}", None)
            if lang_choices:
                parsed = _parse_choices(lang_choices)
                q_data[f"choices_{lang}"] = [
                    {"text": c["text"]} for c in parsed if isinstance(c, dict)
                ]
                if include_answers:
                    q_data[f"choices_with_answers_{lang}"] = parsed

    elif question.question_type == "open_ended":
        q_data["correct_answer"] = question.correct_answer
        if include_answers:
            for lang in _QUIZ_LANGUAGES:
                val = getattr(question, f"correct_answer_{lang}", None)
                if val:
                    q_data[f"correct_answer_{lang}"] = val

    return q_data


def _build_round_data(round_obj):
    """Build multilingual round data dict for WebSocket broadcast."""
    data = {
        "id": round_obj.id,
        "title": round_obj.title,
        "time_per_question": round_obj.time_per_question,
        "is_bonus": round_obj.is_bonus,
    }
    for lang in _QUIZ_LANGUAGES:
        val = getattr(round_obj, f"title_{lang}", None)
        if val:
            data[f"title_{lang}"] = val
    return data


class QuizConsumer(AsyncJsonWebsocketConsumer):
    """WebSocket consumer for live quiz during Crush.lu events."""

    async def connect(self):
        self.quiz_id = self.scope["url_route"]["kwargs"]["quiz_id"]
        self.quiz_group = f"quiz_{self.quiz_id}"
        self.table_group = None

        # Join the quiz group
        await self.channel_layer.group_add(self.quiz_group, self.channel_name)

        # Try to join table-specific group
        user = self.scope.get("user")
        if user and user.is_authenticated:
            try:
                table_id = await self.get_user_table_id(user.id)
            except Exception:
                logger.exception(
                    "Failed to get table_id for user %s in quiz %s",
                    user.id,
                    self.quiz_id,
                )
                table_id = None
            if table_id:
                self.table_group = f"quiz_{self.quiz_id}_table_{table_id}"
                await self.channel_layer.group_add(self.table_group, self.channel_name)

        await self.accept()

        # Send current quiz state on connect
        try:
            state = await self.get_quiz_state()
        except Exception:
            logger.exception("Failed to get quiz state for quiz %s", self.quiz_id)
            state = None
        if state:
            # Include leaderboard for finished quizzes so attendees see final results
            if state.get("status") == "finished":
                try:
                    state["leaderboard"] = await self.get_leaderboard()
                except Exception:
                    logger.exception(
                        "Failed to get leaderboard for finished quiz %s",
                        self.quiz_id,
                    )
            await self.send_json({"type": "quiz.state", "data": state})

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.quiz_group, self.channel_name)
        if self.table_group:
            await self.channel_layer.group_discard(self.table_group, self.channel_name)

    async def receive_json(self, content):
        action = content.get("action")
        user = self.scope.get("user")

        if action == "start_quiz":
            if await self.is_host(user):
                await self.handle_start_quiz()

        elif action == "next_question":
            if await self.is_host(user):
                await self.handle_next_question()

        elif action == "set_round":
            if await self.is_host(user):
                await self.handle_set_round(content)

        elif action == "pause_quiz":
            if await self.is_host(user):
                await self.handle_pause_quiz()

        elif action == "end_quiz":
            if await self.is_host(user):
                await self.handle_end_quiz()

        elif action == "table_answer":
            # Legacy: individual answer submission (non-quiz-night events)
            if user and user.is_authenticated:
                is_quiz_night = await self.is_quiz_night_event()
                if not is_quiz_night:
                    await self.handle_table_answer(user, content)

        elif action == "score_table":
            # Host scores a table for a question
            if await self.is_host(user):
                await self.handle_score_table(content)

        elif action == "rotate":
            if await self.is_host(user):
                await self.handle_rotate()

        elif action == "show_leaderboard":
            if await self.is_host(user):
                await self.handle_leaderboard()

    async def send_error(self, message):
        """Send an error message back to the client."""
        await self.send_json({"type": "quiz.error", "data": {"message": message}})

    # --- Host actions ---

    async def handle_start_quiz(self):
        """Start quiz: auto-select first round and send first question."""
        result = await self.start_quiz_from_first_round()
        if result is None:
            await self.send_error("No rounds available to start.")
            return
        # Broadcast round info, then the first question
        await self.channel_layer.group_send(
            self.quiz_group,
            {"type": "quiz.status", "data": result["round_info"]},
        )
        if result["question_data"]:
            await self.channel_layer.group_send(
                self.quiz_group,
                {"type": "quiz.question", "data": result["question_data"]},
            )

    async def handle_next_question(self):
        # For quiz night events, ensure all tables are scored before advancing
        unscored = await self.check_all_tables_scored()
        if unscored is not None and not unscored:
            await self.send_error("All tables must be scored before advancing.")
            return

        question_data = await self.advance_question()
        if question_data:
            await self.channel_layer.group_send(
                self.quiz_group,
                {"type": "quiz.question", "data": question_data},
            )
        else:
            await self.channel_layer.group_send(
                self.quiz_group,
                {"type": "quiz.status", "data": {"status": "round_complete"}},
            )

    async def handle_set_round(self, content):
        """Host selects a round to play — sets current_round and resets question index."""
        round_id = content.get("round_id")
        if round_id is None:
            return

        result = await self.set_current_round(round_id)
        if result and result.get("error"):
            await self.send_error(result["error"])
            return
        if result:
            await self.channel_layer.group_send(
                self.quiz_group,
                {"type": "quiz.status", "data": result},
            )

    async def handle_pause_quiz(self):
        result = await self.set_quiz_status("paused")
        if result:
            await self.channel_layer.group_send(
                self.quiz_group,
                {"type": "quiz.status", "data": result},
            )

    async def handle_end_quiz(self):
        result = await self.set_quiz_status("finished")
        if result:
            # Include final leaderboard so attendees see results on the finished screen
            leaderboard = await self.get_leaderboard()
            result["leaderboard"] = leaderboard
            await self.channel_layer.group_send(
                self.quiz_group,
                {"type": "quiz.status", "data": result},
            )

    async def handle_score_table(self, content):
        """Host marks a table correct/incorrect for a question."""
        table_id = content.get("table_id")
        question_id = content.get("question_id")
        is_correct = content.get("is_correct", False)

        if table_id is None or question_id is None:
            return

        result = await self.score_table_for_question(table_id, question_id, is_correct)
        if result is None:
            return

        # Already scored — silently ignore re-scores
        if result.get("already_scored"):
            return

        # Broadcast table scored event (no correctness info — deferred until all scored)
        await self.channel_layer.group_send(
            self.quiz_group,
            {
                "type": "quiz.table_scored",
                "data": {
                    "table_id": table_id,
                    "table_number": result["table_number"],
                    "question_id": question_id,
                    "scored_count": result["scored_count"],
                    "total_tables": result["total_tables"],
                },
            },
        )

        # When all tables scored, reveal results to everyone
        if result.get("all_scored"):
            await self.channel_layer.group_send(
                self.quiz_group,
                {
                    "type": "quiz.reveal_scores",
                    "data": {
                        "question_id": question_id,
                        "results": result["reveal_results"],
                    },
                },
            )

    async def handle_rotate(self):
        rotation_data = await self.advance_round_and_rotate()
        if rotation_data.get("finished"):
            # No more rounds — broadcast finished status with leaderboard
            leaderboard = await self.get_leaderboard()
            rotation_data["leaderboard"] = leaderboard
            await self.channel_layer.group_send(
                self.quiz_group,
                {"type": "quiz.status", "data": rotation_data},
            )
        else:
            await self.channel_layer.group_send(
                self.quiz_group,
                {"type": "quiz.rotate", "data": rotation_data},
            )

    async def handle_leaderboard(self):
        leaderboard = await self.get_leaderboard()
        await self.channel_layer.group_send(
            self.quiz_group,
            {"type": "quiz.leaderboard", "data": leaderboard},
        )

    # --- Legacy attendee actions ---

    async def handle_table_answer(self, user, content):
        question_id = content.get("question_id")
        answer = content.get("answer")
        if question_id is None or answer is None:
            return

        result = await self.score_answer(user.id, question_id, answer)
        if result is None:
            return

        await self.send_json({"type": "quiz.answer_result", "data": result})

        if self.table_group:
            table_score = await self.get_table_score_for_user(user.id)
            await self.channel_layer.group_send(
                self.table_group,
                {"type": "quiz.table_score", "data": table_score},
            )

    # --- Group message handlers ---

    async def quiz_question(self, event):
        await self.send_json({"type": "quiz.question", "data": event["data"]})

    async def quiz_rotate(self, event):
        await self.send_json({"type": "quiz.rotate", "data": event["data"]})

    async def quiz_leaderboard(self, event):
        await self.send_json({"type": "quiz.leaderboard", "data": event["data"]})

    async def quiz_status(self, event):
        await self.send_json({"type": "quiz.status", "data": event["data"]})

    async def quiz_table_score(self, event):
        await self.send_json({"type": "quiz.table_score", "data": event["data"]})

    async def quiz_table_scored(self, event):
        await self.send_json({"type": "quiz.table_scored", "data": event["data"]})

    async def quiz_reveal_scores(self, event):
        await self.send_json({"type": "quiz.reveal_scores", "data": event["data"]})

    async def quiz_answer_result(self, event):
        await self.send_json({"type": "quiz.answer_result", "data": event["data"]})

    async def quiz_error(self, event):
        await self.send_json({"type": "quiz.error", "data": event["data"]})

    # --- Database helpers ---

    @database_sync_to_async
    def get_user_table_id(self, user_id):
        """Get user's table for the current round (rotation-aware)."""
        from crush_lu.models.quiz import (
            QuizEvent,
            QuizRotationSchedule,
            QuizTableMembership,
        )

        # Try rotation schedule first (quiz night events)
        try:
            quiz = QuizEvent.objects.select_related("current_round").get(
                id=self.quiz_id
            )
            round_number = quiz.get_round_number()

            rotation = (
                QuizRotationSchedule.objects.filter(
                    quiz_id=self.quiz_id,
                    round_number=round_number,
                    user_id=user_id,
                )
                .select_related("table")
                .first()
            )
            if rotation:
                return rotation.table_id
        except QuizEvent.DoesNotExist:
            pass

        # Fall back to static membership
        membership = (
            QuizTableMembership.objects.filter(
                table__quiz_id=self.quiz_id, user_id=user_id
            )
            .select_related("table")
            .first()
        )
        return membership.table_id if membership else None

    @database_sync_to_async
    def is_host(self, user):
        if not user or not user.is_authenticated:
            return False
        from crush_lu.models.quiz import QuizEvent

        try:
            quiz = QuizEvent.objects.select_related("event").get(id=self.quiz_id)
            if quiz.created_by_id == user.id or user.is_staff:
                return True
            # Active coaches can also host
            from crush_lu.models import CrushCoach

            return CrushCoach.objects.filter(user=user, is_active=True).exists()
        except QuizEvent.DoesNotExist:
            return False

    # Backward compat alias
    is_coach = is_host

    @database_sync_to_async
    def is_quiz_night_event(self):
        from crush_lu.models.quiz import QuizEvent

        try:
            quiz = QuizEvent.objects.select_related("event").get(id=self.quiz_id)
            return quiz.event.event_type == "quiz_night"
        except QuizEvent.DoesNotExist:
            return False

    @database_sync_to_async
    def get_quiz_state(self):
        from crush_lu.models.quiz import QuizEvent

        try:
            quiz = QuizEvent.objects.select_related("current_round", "event").get(
                id=self.quiz_id
            )
        except QuizEvent.DoesNotExist:
            return None

        question = quiz.get_current_question()
        current_sort = quiz.current_round.sort_order if quiz.current_round else -1
        current_pk = quiz.current_round.pk if quiz.current_round else -1
        data = {
            "status": quiz.status,
            "event_type": quiz.event.event_type,
            "current_round": (
                _build_round_data(quiz.current_round)
                if quiz.current_round
                else None
            ),
            "question_index": quiz.current_question_index,
            # Round list with status for guided flow
            "rounds": [],
        }
        for r in quiz.rounds.order_by("sort_order", "pk"):
            r_data = _build_round_data(r)
            r_data["sort_order"] = r.sort_order
            r_data["question_count"] = r.questions.count()
            r_data["status"] = (
                "current"
                if quiz.current_round_id == r.id
                else (
                    "done"
                    if (
                        r.sort_order < current_sort
                        or (r.sort_order == current_sort and r.pk < current_pk)
                    )
                    else "upcoming"
                )
            )
            data["rounds"].append(r_data)

        if question and quiz.is_active:
            from crush_lu.models.quiz import QuizTable, TableRoundScore

            questions = quiz.current_round.questions.order_by("sort_order")
            total = questions.count()

            q_data = _build_question_data(question, include_answers=True)
            data["question"] = q_data

            # Include fields that showQuestion() expects at top level
            data["time"] = quiz.current_round.time_per_question
            data["index"] = quiz.current_question_index
            data["total"] = total
            data["question_count"] = total
            data["is_bonus"] = quiz.current_round.is_bonus

            # Calculate remaining time so refreshing doesn't reset the timer
            if quiz.question_started_at:
                elapsed = (timezone.now() - quiz.question_started_at).total_seconds()
                remaining = max(0, quiz.current_round.time_per_question - elapsed)
                data["time_remaining"] = int(remaining)
            else:
                data["time_remaining"] = quiz.current_round.time_per_question

            # Include scoring state so coach can resume after reconnect
            total_tables = QuizTable.objects.filter(quiz=quiz).count()
            scored_qs = TableRoundScore.objects.filter(
                quiz=quiz, question=question
            ).select_related("table")
            scored_count = scored_qs.count()
            all_scored = scored_count >= total_tables and total_tables > 0

            data["total_tables"] = total_tables
            data["scored_count"] = scored_count

            # Build scored_tables map: {table_id: is_correct} if revealed,
            # otherwise {table_id: "scored"}
            scored_tables = {}
            if all_scored:
                for s in scored_qs:
                    scored_tables[str(s.table_id)] = s.is_correct
            else:
                for s in scored_qs:
                    scored_tables[str(s.table_id)] = "scored"
            data["scored_tables"] = scored_tables

        return data

    @database_sync_to_async
    def start_quiz_from_first_round(self):
        """Set quiz to active, select first round by sort_order, advance to first question."""
        from crush_lu.models.quiz import QuizEvent

        try:
            quiz = QuizEvent.objects.get(id=self.quiz_id)
        except QuizEvent.DoesNotExist:
            return None

        first_round = quiz.rounds.order_by("sort_order").first()
        if not first_round:
            return None

        quiz.current_round = first_round
        quiz.current_question_index = 0
        quiz.status = "active"
        quiz.question_started_at = timezone.now()
        quiz.save(update_fields=["current_round", "current_question_index", "status", "question_started_at"])

        round_info = {
            "status": "active",
            "current_round": _build_round_data(first_round),
            "message": f"Quiz started: {first_round.title}",
        }

        # Get first question
        questions = first_round.questions.order_by("sort_order")
        total = questions.count()
        question_data = None
        if total > 0:
            question = questions[0]
            question_data = _build_question_data(question, include_answers=True)
            question_data["time"] = first_round.time_per_question
            question_data["index"] = 0
            question_data["total"] = total
            question_data["is_bonus"] = first_round.is_bonus

        return {"round_info": round_info, "question_data": question_data}

    @database_sync_to_async
    def set_current_round(self, round_id):
        """Set the quiz to a specific round, reset question index to -1."""
        from crush_lu.models.quiz import QuizEvent, QuizRound

        try:
            quiz = QuizEvent.objects.get(id=self.quiz_id)
            round_obj = QuizRound.objects.get(id=round_id, quiz=quiz)
        except (QuizEvent.DoesNotExist, QuizRound.DoesNotExist):
            return None

        # Block round changes during active play with a current question
        if (
            quiz.status == "active"
            and quiz.current_question_index >= 0
            and quiz.current_round_id != round_obj.id
        ):
            return {"error": "Cannot change rounds while a question is active."}

        quiz.current_round = round_obj
        # Set to -1 so the first next_question lands on index 0 (Issue #2)
        quiz.current_question_index = -1
        quiz.save(update_fields=["current_round", "current_question_index"])

        return {
            "status": quiz.status,
            "current_round": _build_round_data(round_obj),
            "message": f"Round set: {round_obj.title}",
        }

    @database_sync_to_async
    def set_quiz_status(self, status):
        """Set quiz status to paused or finished."""
        from crush_lu.models.quiz import QuizEvent

        try:
            quiz = QuizEvent.objects.get(id=self.quiz_id)
        except QuizEvent.DoesNotExist:
            return None

        quiz.status = status
        quiz.question_started_at = None
        quiz.save(update_fields=["status", "question_started_at"])
        return {"status": status}

    @database_sync_to_async
    def check_all_tables_scored(self):
        """Return True if all tables scored for current question, False if not.

        Returns None for non-quiz-night events or if no active question.
        """
        from crush_lu.models.quiz import QuizEvent, QuizTable, TableRoundScore

        try:
            quiz = QuizEvent.objects.select_related("current_round", "event").get(
                id=self.quiz_id
            )
        except QuizEvent.DoesNotExist:
            return None

        if quiz.event.event_type != "quiz_night":
            return None  # No table scoring for non-quiz-night events

        question = quiz.get_current_question()
        if not question:
            return None

        total_tables = QuizTable.objects.filter(quiz=quiz).count()
        if total_tables == 0:
            return None

        scored_count = TableRoundScore.objects.filter(
            quiz=quiz, question=question
        ).count()
        return scored_count >= total_tables

    @database_sync_to_async
    def advance_question(self):
        """Advance to the next question in the current round.

        Uses current_question_index as the last-shown index.
        Index starts at -1 (no question shown yet), so first +1 lands on 0.
        """
        from crush_lu.models.quiz import QuizEvent

        try:
            quiz = QuizEvent.objects.select_related("current_round").get(
                id=self.quiz_id
            )
        except QuizEvent.DoesNotExist:
            return None

        if not quiz.current_round:
            return None

        questions = quiz.current_round.questions.order_by("sort_order")
        total = questions.count()
        next_index = quiz.current_question_index + 1

        if next_index >= total:
            return None  # Round complete

        quiz.current_question_index = next_index
        quiz.status = "active"
        quiz.question_started_at = timezone.now()
        quiz.save(update_fields=["current_question_index", "status", "question_started_at"])

        question = questions[next_index]
        q_data = _build_question_data(question, include_answers=True)
        q_data["time"] = quiz.current_round.time_per_question
        q_data["index"] = next_index
        q_data["total"] = total
        q_data["is_bonus"] = quiz.current_round.is_bonus

        return q_data

    @database_sync_to_async
    def score_table_for_question(self, table_id, question_id, is_correct):
        """Host scores a table. Creates IndividualScores for all table members.

        Returns None on error, {"already_scored": True} if table was already
        scored for this question, or a dict with scoring results including
        whether all tables have now been scored.
        """
        from crush_lu.models.quiz import (
            IndividualScore,
            QuizEvent,
            QuizQuestion,
            QuizRotationSchedule,
            QuizTable,
            QuizTableMembership,
            TableRoundScore,
        )

        try:
            quiz = QuizEvent.objects.select_related("current_round").get(
                id=self.quiz_id
            )
            table = QuizTable.objects.get(id=table_id, quiz=quiz)
            question = QuizQuestion.objects.select_related("round").get(
                id=question_id, round__quiz=quiz
            )
        except (
            QuizEvent.DoesNotExist,
            QuizTable.DoesNotExist,
            QuizQuestion.DoesNotExist,
        ):
            return None

        # Only allow scoring once per table per question (no re-scores)
        _obj, created = TableRoundScore.objects.get_or_create(
            quiz=quiz,
            table=table,
            question=question,
            defaults={"is_correct": is_correct},
        )
        if not created:
            return {"already_scored": True}

        # Issue #4: Read bonus from question's own round, not quiz.current_round
        points = question.points if is_correct else 0
        if is_correct and question.round.is_bonus:
            points *= 2

        # Get users at this table for the current round
        round_number = quiz.get_round_number()
        is_quiz_night = quiz.event.event_type == "quiz_night"

        # Try rotation schedule first
        rotation_users = list(
            QuizRotationSchedule.objects.filter(
                quiz=quiz,
                round_number=round_number,
                table=table,
            ).values_list("user_id", flat=True)
        )

        # Fall back to static membership only for non-quiz-night events
        if not rotation_users and not is_quiz_night:
            rotation_users = list(
                QuizTableMembership.objects.filter(table=table).values_list(
                    "user_id", flat=True
                )
            )

        # Create IndividualScore for each table member
        for user_id in rotation_users:
            IndividualScore.objects.update_or_create(
                quiz=quiz,
                user_id=user_id,
                question=question,
                defaults={
                    "is_correct": is_correct,
                    "points_earned": points,
                    "answer": f"table_scored:{table.table_number}",
                },
            )

        # Check if all tables have been scored for this question
        total_tables = QuizTable.objects.filter(quiz=quiz).count()
        scored_count = TableRoundScore.objects.filter(
            quiz=quiz, question=question
        ).count()
        all_scored = scored_count >= total_tables

        result = {
            "table_number": table.table_number,
            "points_awarded": points,
            "members_scored": len(rotation_users),
            "scored_count": scored_count,
            "total_tables": total_tables,
            "all_scored": all_scored,
        }

        # When all tables scored, include full results for the reveal
        if all_scored:
            all_scores = TableRoundScore.objects.filter(
                quiz=quiz, question=question
            ).select_related("table", "question", "question__round")
            reveal_results = []
            for score in all_scores:
                pts = score.question.points if score.is_correct else 0
                if score.is_correct and score.question.round.is_bonus:
                    pts *= 2
                reveal_results.append(
                    {
                        "table_id": score.table_id,
                        "table_number": score.table.table_number,
                        "is_correct": score.is_correct,
                        "points_awarded": pts,
                    }
                )
            result["reveal_results"] = reveal_results

        return result

    @database_sync_to_async
    def advance_round_and_rotate(self):
        """Advance to the next round and return rotation assignments."""
        from crush_lu.models.quiz import QuizEvent, QuizRotationSchedule

        try:
            quiz = QuizEvent.objects.select_related("current_round").get(
                id=self.quiz_id
            )
        except QuizEvent.DoesNotExist:
            return {}

        # Find next round by (sort_order, pk) — handles duplicate sort_orders
        current_sort = quiz.current_round.sort_order if quiz.current_round else -1
        current_pk = quiz.current_round.pk if quiz.current_round else -1
        next_round = (
            quiz.rounds.filter(sort_order__gt=current_sort)
            .order_by("sort_order", "pk")
            .first()
        )
        # Same sort_order: advance by pk within that group
        if not next_round and quiz.current_round:
            next_round = (
                quiz.rounds.filter(
                    sort_order=current_sort, pk__gt=current_pk
                )
                .order_by("pk")
                .first()
            )

        if next_round:
            quiz.current_round = next_round
            # Set to -1 so first next_question lands on Q0 (Issue #2)
            quiz.current_question_index = -1
            quiz.status = "active"
            quiz.save(
                update_fields=["current_round", "current_question_index", "status"]
            )

            # Get the round_number for rotation lookup
            round_number = quiz.get_round_number(next_round)

            # Build per-user assignment map from rotation schedule
            assignments = {}
            rotations = QuizRotationSchedule.objects.filter(
                quiz=quiz, round_number=round_number
            ).select_related("table", "user__crushprofile")

            for r in rotations:
                profile = getattr(r.user, "crushprofile", None)
                # Use string keys — msgpack (channel layer) forbids integer map keys
                assignments[str(r.user_id)] = {
                    "table_number": r.table.table_number,
                    "role": r.role,
                    "display_name": (profile.display_name if profile else "Anonymous"),
                }

            if not assignments:
                logger.warning(
                    "No rotation assignments for quiz %s round_number %d. "
                    "Rotation schedule may need regeneration.",
                    quiz.id,
                    round_number,
                )

            return {
                "round_title": next_round.title,
                "round_number": round_number,
                "is_bonus": next_round.is_bonus,
                "assignments": assignments,
            }

        # Issue #5: Set status to finished when no more rounds
        quiz.status = "finished"
        quiz.save(update_fields=["status"])
        return {"finished": True, "status": "finished"}

    @database_sync_to_async
    def score_answer(self, user_id, question_id, answer):
        """Legacy: individual answer scoring for non-quiz-night events."""
        from crush_lu.models.quiz import IndividualScore, QuizQuestion

        try:
            question = QuizQuestion.objects.get(
                id=question_id, round__quiz_id=self.quiz_id
            )
        except QuizQuestion.DoesNotExist:
            return None

        if IndividualScore.objects.filter(
            quiz_id=self.quiz_id, user_id=user_id, question_id=question_id
        ).exists():
            return None

        is_correct = False
        # Check answer against all language variants of choices
        for lang in _QUIZ_LANGUAGES:
            lang_choices = getattr(question, f"choices_{lang}", None) or []
            for choice in lang_choices:
                if isinstance(choice, dict) and choice.get("text") == answer and choice.get("is_correct"):
                    is_correct = True
                    break
            if is_correct:
                break
        # Fallback: check the default choices field too
        if not is_correct:
            for choice in (question.choices or []):
                if isinstance(choice, dict) and choice.get("text") == answer and choice.get("is_correct"):
                    is_correct = True
                    break

        points = question.points if is_correct else 0

        IndividualScore.objects.create(
            quiz_id=self.quiz_id,
            user_id=user_id,
            question_id=question_id,
            answer=answer,
            is_correct=is_correct,
            points_earned=points,
        )

        # Return correct answer in default language
        correct_text = next(
            (c["text"] for c in (question.choices or []) if isinstance(c, dict) and c.get("is_correct")),
            None,
        )

        return {
            "is_correct": is_correct,
            "points_earned": points,
            "correct_answer": correct_text,
        }

    @database_sync_to_async
    def get_table_score_for_user(self, user_id):
        """Get total score for a user's current table (legacy path)."""
        from crush_lu.models.quiz import IndividualScore, QuizTableMembership

        membership = (
            QuizTableMembership.objects.filter(
                table__quiz_id=self.quiz_id, user_id=user_id
            )
            .select_related("table")
            .first()
        )
        if not membership:
            return {"table_number": 0, "total_score": 0}

        table = membership.table
        # Issue #7: Sum scores for all members of this table via membership
        member_ids = list(
            QuizTableMembership.objects.filter(table=table).values_list(
                "user_id", flat=True
            )
        )
        total = (
            IndividualScore.objects.filter(
                quiz_id=self.quiz_id, user_id__in=member_ids
            ).aggregate(total=Sum("points_earned"))["total"]
            or 0
        )
        return {"table_number": table.table_number, "total_score": total}

    @database_sync_to_async
    def get_leaderboard(self):
        from crush_lu.models.quiz import IndividualScore, QuizTable

        tables = QuizTable.objects.filter(quiz_id=self.quiz_id).order_by("table_number")
        leaderboard = []
        for table in tables:
            score = table.get_total_score()
            leaderboard.append(
                {
                    "table_number": table.table_number,
                    "total_score": score,
                }
            )
        leaderboard.sort(key=lambda x: x["total_score"], reverse=True)

        # Individual top scorers (using display_name for privacy)
        from crush_lu.models import CrushProfile

        top_individuals = (
            IndividualScore.objects.filter(quiz_id=self.quiz_id)
            .values("user_id")
            .annotate(total=Sum("points_earned"))
            .order_by("-total")[:10]
        )

        individual_scores = []
        for entry in top_individuals:
            try:
                profile = CrushProfile.objects.get(user_id=entry["user_id"])
                name = profile.display_name
            except CrushProfile.DoesNotExist:
                name = "Anonymous"
            individual_scores.append(
                {"display_name": name, "total_score": entry["total"]}
            )

        return {
            "tables": leaderboard,
            "individuals": individual_scores,
        }
