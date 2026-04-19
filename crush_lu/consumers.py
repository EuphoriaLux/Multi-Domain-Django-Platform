import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.db.models import Sum
from django.utils import timezone

logger = logging.getLogger(__name__)


from crush_lu.models.quiz import parse_choices as _parse_choices

_QUIZ_LANGUAGES = ("en", "de", "fr")


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
        if include_answers:
            q_data["correct_answer"] = question.correct_answer
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
        # AUTH-01: Reject unauthenticated connections before accepting
        user = self.scope.get("user")
        self.is_display = False

        if not user or not user.is_authenticated:
            # Check for display token auth (projector display page)
            query_string = self.scope.get("query_string", b"").decode()
            params = dict(p.split("=", 1) for p in query_string.split("&") if "=" in p)
            display_token = params.get("display_token", "")
            is_display_request = params.get("display", "") == "true"
            quiz_id = self.scope["url_route"]["kwargs"]["quiz_id"]

            # Allow display connection if: valid token provided, OR display
            # mode requested and quiz has no token set (open access)
            display_auth = False
            if display_token:
                display_auth = await self._verify_display_token(quiz_id, display_token)
            elif is_display_request:
                display_auth = await self._quiz_has_no_display_token(quiz_id)

            if display_auth:
                self.is_display = True
                self.quiz_id = quiz_id
                self.quiz_group = f"quiz_{quiz_id}"
                self.display_group = f"quiz_{quiz_id}_display"
                self.table_group = None

                await self.channel_layer.group_add(self.quiz_group, self.channel_name)
                await self.channel_layer.group_add(self.display_group, self.channel_name)
                await self.accept()

                try:
                    self._total_tables = await self._get_total_tables()
                except Exception:
                    self._total_tables = 0

                # Send initial state (without answer data)
                try:
                    state = await self.get_quiz_state()
                except Exception:
                    state = None
                if state:
                    if state.get("status") in ("active", "paused", "finished"):
                        try:
                            state["leaderboard"] = await self.get_leaderboard()
                        except Exception:
                            pass
                    q = state.get("question")
                    if q and isinstance(q, dict):
                        state["question"] = {
                            k: v
                            for k, v in q.items()
                            if not k.startswith("choices_with_answers")
                            and not k.startswith("correct_answer")
                        }
                    # Enrich with table roster data (display-only)
                    try:
                        table_data = await self.get_table_display_data()
                        if table_data:
                            state.update(table_data)
                    except Exception:
                        pass
                    await self.send_json({"type": "quiz.state", "data": state})
                return

            await self.close()
            return

        self.quiz_id = self.scope["url_route"]["kwargs"]["quiz_id"]
        self.quiz_group = f"quiz_{self.quiz_id}"
        self.table_group = None

        # Join the quiz group
        await self.channel_layer.group_add(self.quiz_group, self.channel_name)

        # Try to join table-specific group
        if user.is_authenticated:
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

        # Cache total tables count (doesn't change during a quiz)
        try:
            self._total_tables = await self._get_total_tables()
        except Exception:
            logger.exception("Failed to get total tables for quiz %s", self.quiz_id)
            self._total_tables = 0

        # Send current quiz state on connect
        try:
            state = await self.get_quiz_state()
        except Exception:
            logger.exception("Failed to get quiz state for quiz %s", self.quiz_id)
            state = None
        if state:
            # Include leaderboard so attendees/coaches always see current standings
            if state.get("status") in ("active", "paused", "finished"):
                try:
                    state["leaderboard"] = await self.get_leaderboard()
                except Exception:
                    logger.exception(
                        "Failed to get leaderboard for quiz %s",
                        self.quiz_id,
                    )
            # CLIENT-02: Strip answer data from quiz state for non-host users
            if not await self.is_host(user):
                q = state.get("question")
                if q and isinstance(q, dict):
                    state["question"] = {
                        k: v
                        for k, v in q.items()
                        if not k.startswith("choices_with_answers")
                        and not k.startswith("correct_answer")
                    }
            await self.send_json({"type": "quiz.state", "data": state})

    async def disconnect(self, close_code):
        if hasattr(self, "quiz_group"):
            await self.channel_layer.group_discard(self.quiz_group, self.channel_name)
        if getattr(self, "display_group", None):
            await self.channel_layer.group_discard(self.display_group, self.channel_name)
        if getattr(self, "table_group", None):
            await self.channel_layer.group_discard(self.table_group, self.channel_name)

    async def receive_json(self, content):
        # Display connections are read-only (projector view)
        if self.is_display:
            return

        action = content.get("action")
        user = self.scope.get("user")

        # Host-only actions
        host_actions = {
            "start_quiz",
            "resume_quiz",
            "next_question",
            "set_round",
            "pause_quiz",
            "end_quiz",
            "score_table",
            "rotate",
            "show_leaderboard",
        }

        if action in host_actions:
            if not await self.is_host(user):
                await self.send_error(
                    "Not authorized. Only the host can perform this action."
                )
                return

        if action == "start_quiz":
            await self.handle_start_quiz()

        elif action == "resume_quiz":
            await self.handle_resume_quiz()

        elif action == "next_question":
            await self.handle_next_question()

        elif action == "set_round":
            await self.handle_set_round(content)

        elif action == "pause_quiz":
            await self.handle_pause_quiz()

        elif action == "end_quiz":
            await self.handle_end_quiz()

        elif action == "table_answer":
            # Legacy: individual answer submission (non-quiz-night events)
            if not user or not user.is_authenticated:
                await self.send_error("You must be logged in to submit answers.")
                return
            is_quiz_night = await self.is_quiz_night_event()
            if is_quiz_night:
                await self.send_error(
                    "Individual answers are not used in quiz night events."
                )
                return
            await self.handle_table_answer(user, content)

        elif action == "score_table":
            await self.handle_score_table(content)

        elif action == "rotate":
            await self.handle_rotate()

        elif action == "show_leaderboard":
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
        if result.get("error"):
            await self.send_error(result["error"])
            return
        # Enrich with table count for scoring grid initialization
        result["round_info"]["total_tables"] = self._total_tables
        if result["question_data"]:
            result["question_data"]["total_tables"] = self._total_tables
            result["question_data"]["scored_count"] = 0
            result["question_data"]["scored_tables"] = {}
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

    async def handle_resume_quiz(self):
        """Resume a paused quiz without resetting progress."""
        result = await self.set_quiz_status("active")
        if not result:
            return
        if result.get("error"):
            await self.send_error(result["error"])
            return
        # Re-send current question state so all clients sync up
        state = await self.get_quiz_state()
        if state:
            # Include leaderboard for reconnecting clients
            try:
                state["leaderboard"] = await self.get_leaderboard()
            except Exception:
                pass
            await self.channel_layer.group_send(
                self.quiz_group,
                {"type": "quiz.status", "data": result},
            )
            # If there's an active question, re-broadcast it
            if state.get("question"):
                q_data = state["question"]
                q_data["time"] = state.get("time", 30)
                q_data["index"] = state.get("index", 0)
                q_data["total"] = state.get("total", 0)
                q_data["is_bonus"] = state.get("is_bonus", False)
                q_data["time_remaining"] = state.get("time_remaining")
                q_data["total_tables"] = state.get("total_tables", 0)
                q_data["scored_count"] = state.get("scored_count", 0)
                q_data["scored_tables"] = state.get("scored_tables", {})
                await self.channel_layer.group_send(
                    self.quiz_group,
                    {"type": "quiz.question", "data": q_data},
                )

    async def handle_next_question(self):
        # For quiz night events, ensure all tables are scored before advancing
        all_scored = await self.check_all_tables_scored()
        if all_scored is not None and not all_scored:
            await self.send_error("All tables must be scored before advancing.")
            return

        question_data = await self.advance_question()
        if question_data:
            # Enrich with table count for scoring grid reset
            question_data["total_tables"] = self._total_tables
            question_data["scored_count"] = 0
            question_data["scored_tables"] = {}
            await self.channel_layer.group_send(
                self.quiz_group,
                {"type": "quiz.question", "data": question_data},
            )
        else:
            # Round complete — check if this was the last round
            next_exists = await self.has_next_round()
            if not next_exists:
                # Last round done — auto-finish the quiz
                result = await self.set_quiz_status("finished")
                if result and not result.get("error"):
                    leaderboard = await self.get_leaderboard()
                    result["leaderboard"] = leaderboard
                    await self.channel_layer.group_send(
                        self.quiz_group,
                        {"type": "quiz.status", "data": result},
                    )
                else:
                    # Fallback: still broadcast round_complete
                    await self.channel_layer.group_send(
                        self.quiz_group,
                        {
                            "type": "quiz.status",
                            "data": {"status": "round_complete", "is_last_round": True},
                        },
                    )
            else:
                await self.channel_layer.group_send(
                    self.quiz_group,
                    {"type": "quiz.status", "data": {"status": "round_complete"}},
                )

    async def handle_set_round(self, content):
        """Host selects a round to play — sets current_round and resets question index."""
        round_id = content.get("round_id")
        if round_id is None or not isinstance(round_id, int):
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
        if not result:
            return
        if result.get("error"):
            await self.send_error(result["error"])
            return
        await self.channel_layer.group_send(
            self.quiz_group,
            {"type": "quiz.status", "data": result},
        )

    async def handle_end_quiz(self):
        result = await self.set_quiz_status("finished")
        if not result:
            return
        if result.get("error"):
            await self.send_error(result["error"])
            return
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
        is_correct = bool(content.get("is_correct", False))

        if table_id is None or question_id is None:
            return
        # INPUT-03: Validate ID types
        if not isinstance(table_id, int) or not isinstance(question_id, int):
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

            # Auto-broadcast updated leaderboard
            leaderboard = await self.get_leaderboard()
            await self.channel_layer.group_send(
                self.quiz_group,
                {"type": "quiz.leaderboard", "data": leaderboard},
            )

    async def handle_rotate(self):
        # Server-side defense-in-depth: even though the UI only exposes
        # the rotate button after round_complete, a host with stale JS
        # state or an extra tab can still fire the action. Reject the
        # rotate unless (a) every question in the current round has been
        # shown and (b) every table has been scored on the last shown
        # question. Without this guard, rotating mid-round would skip
        # unasked questions and orphan any partial scoring.
        guard = await self.check_can_rotate()
        if guard.get("error"):
            await self.send_error(guard["error"])
            return

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
        leaderboard["spotlight"] = True
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
        # INPUT-02: Validate answer type and length
        if not isinstance(answer, str) or len(answer) > 1000:
            await self.send_error("Invalid answer format.")
            return
        # INPUT-03: Validate question_id type
        if not isinstance(question_id, int):
            return

        result = await self.score_answer(user.id, question_id, answer)
        if result is None:
            return

        if result.get("already_answered"):
            await self.send_error("You have already answered this question.")
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
        """Send question to client — strip correct answers for non-host users.

        CLIENT-02: The broadcast includes ``choices_with_answers`` and
        ``correct_answer_*`` so the host can display the reference answer.
        Attendees and display connections must NOT see this data.
        """
        data = event["data"]
        user = self.scope.get("user")
        if self.is_display or not await self.is_host(user):
            # Build a sanitized copy without answer keys
            data = {
                k: v
                for k, v in data.items()
                if not k.startswith("choices_with_answers")
                and not k.startswith("correct_answer")
            }
        await self.send_json({"type": "quiz.question", "data": data})

    async def quiz_rotate(self, event):
        data = event["data"]
        # Update table group membership based on rotation assignments
        user = self.scope.get("user")
        if user and user.is_authenticated and data.get("assignments"):
            user_key = str(user.id)
            assignment = data["assignments"].get(user_key)
            if assignment and assignment.get("table_id"):
                new_table_id = assignment["table_id"]
                new_group = f"quiz_{self.quiz_id}_table_{new_table_id}"
                if new_group != self.table_group:
                    if self.table_group:
                        await self.channel_layer.group_discard(
                            self.table_group, self.channel_name
                        )
                    self.table_group = new_group
                    await self.channel_layer.group_add(
                        self.table_group, self.channel_name
                    )
            elif assignment:
                logger.warning(
                    "Rotation assignment for user %s missing table_id in quiz %s",
                    user.id,
                    self.quiz_id,
                )
        await self.send_json({"type": "quiz.rotate", "data": data})

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

    async def quiz_table_update(self, event):
        await self.send_json({"type": "quiz.table_update", "data": event["data"]})

    # --- Database helpers ---

    @database_sync_to_async
    def get_table_display_data(self):
        """Return table roster and attendance counts (display-only)."""
        from crush_lu.views_quiz import _get_table_members_json
        from crush_lu.models.events import EventRegistration
        from crush_lu.models.quiz import QuizEvent

        try:
            quiz = QuizEvent.objects.get(id=self.quiz_id)
        except QuizEvent.DoesNotExist:
            return None
        round_number = quiz.get_round_number()
        return {
            "tables": _get_table_members_json(quiz, round_number),
            "attended_count": EventRegistration.objects.filter(
                event_id=quiz.event_id, status="attended"
            ).count(),
            "confirmed_count": EventRegistration.objects.filter(
                event_id=quiz.event_id, status__in=["confirmed", "attended"]
            ).count(),
        }

    @database_sync_to_async
    def _verify_display_token(self, quiz_id, token):
        """Verify a display_token for projector display WebSocket auth."""
        from crush_lu.models.quiz import QuizEvent

        return QuizEvent.objects.filter(id=quiz_id, display_token=token).exists()

    @database_sync_to_async
    def _quiz_has_no_display_token(self, quiz_id):
        """Allow display connections when no PIN is configured."""
        from crush_lu.models.quiz import QuizEvent

        return QuizEvent.objects.filter(id=quiz_id, display_token="").exists()

    @database_sync_to_async
    def _get_total_tables(self):
        """Get number of tables for this quiz (cached on consumer instance)."""
        from crush_lu.models.quiz import QuizTable

        return QuizTable.objects.filter(quiz_id=self.quiz_id).count()

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
        # AUTHZ-03: Cache host check per connection to avoid repeated DB queries
        if hasattr(self, "_is_host_cache"):
            return self._is_host_cache
        if not user or not user.is_authenticated:
            self._is_host_cache = False
            return False
        from crush_lu.models.quiz import QuizEvent

        try:
            quiz = QuizEvent.objects.only("created_by_id", "event_id").get(
                id=self.quiz_id
            )
        except QuizEvent.DoesNotExist:
            self._is_host_cache = False
            return False

        if quiz.created_by_id == user.id:
            self._is_host_cache = True
            return True
        # Only coaches explicitly assigned to this event can host —
        # blanket is_staff and "any active coach" bypasses removed.
        from crush_lu.models import CrushCoach

        result = CrushCoach.objects.filter(
            user=user, is_active=True, assigned_events=quiz.event_id
        ).exists()
        self._is_host_cache = result
        return result

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
                _build_round_data(quiz.current_round) if quiz.current_round else None
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
        from crush_lu.models.quiz import QuizEvent, QuizRotationSchedule

        try:
            quiz = QuizEvent.objects.get(id=self.quiz_id)
        except QuizEvent.DoesNotExist:
            return None

        # CONC-02: Only allow starting from draft — use resume_quiz for paused
        if quiz.status != "draft":
            return {
                "error": f"Cannot start quiz in '{quiz.status}' state. Use resume for paused quizzes."
            }

        first_round = quiz.rounds.order_by("sort_order").first()
        if not first_round:
            return None

        # Auto-generate rotation schedule (rounds 1+) if not yet created
        if (
            quiz.num_tables
            and not QuizRotationSchedule.objects.filter(
                quiz=quiz, round_number=1
            ).exists()
        ):
            try:
                from crush_lu.services.quiz_rotation import generate_rotation_rounds

                generate_rotation_rounds(quiz)
            except Exception:
                logger.exception("Auto-rotation generation failed for quiz %s", quiz.pk)

        quiz.current_round = first_round
        quiz.current_question_index = 0
        quiz.status = "active"
        quiz.question_started_at = timezone.now()
        quiz.save(
            update_fields=[
                "current_round",
                "current_question_index",
                "status",
                "question_started_at",
            ]
        )

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

    # STATE-01: Valid state transitions for quiz lifecycle
    VALID_STATUS_TRANSITIONS = {
        "draft": {"active"},
        "active": {"paused", "finished"},
        "paused": {"active", "finished"},
        "finished": set(),  # Terminal state — no transitions allowed
    }

    @database_sync_to_async
    def set_quiz_status(self, status):
        """Set quiz status with state transition validation (STATE-01)."""
        from crush_lu.models.quiz import QuizEvent

        try:
            quiz = QuizEvent.objects.get(id=self.quiz_id)
        except QuizEvent.DoesNotExist:
            return None

        allowed = self.VALID_STATUS_TRANSITIONS.get(quiz.status, set())
        if status not in allowed:
            return {"error": f"Cannot transition from '{quiz.status}' to '{status}'."}

        quiz.status = status
        if status == "active" and quiz.current_question_index >= 0:
            # Resuming: reset timer so clients get a fresh countdown
            quiz.question_started_at = timezone.now()
        else:
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
    def has_next_round(self):
        """Check whether there's another round after the current one."""
        from crush_lu.models.quiz import QuizEvent

        try:
            quiz = QuizEvent.objects.select_related("current_round").get(
                id=self.quiz_id
            )
        except QuizEvent.DoesNotExist:
            return False

        if not quiz.current_round:
            return False

        current_sort = quiz.current_round.sort_order
        current_pk = quiz.current_round.pk

        # Check for round with higher sort_order
        if quiz.rounds.filter(sort_order__gt=current_sort).exists():
            return True
        # Check for round with same sort_order but higher pk
        if quiz.rounds.filter(sort_order=current_sort, pk__gt=current_pk).exists():
            return True
        return False

    @database_sync_to_async
    def advance_question(self):
        """Advance to the next question in the current round.

        Uses current_question_index as the last-shown index.
        Index starts at -1 (no question shown yet), so first +1 lands on 0.

        CONC-01: Uses select_for_update to prevent race conditions when
        multiple host tabs send next_question simultaneously.
        STATE-02: Only advances if quiz is in active state.
        """
        from django.db import transaction

        from crush_lu.models.quiz import QuizEvent

        with transaction.atomic():
            try:
                # Lock QuizEvent row without select_related on nullable FK
                # (current_round is nullable → LEFT OUTER JOIN → incompatible
                # with FOR UPDATE on PostgreSQL).
                quiz = QuizEvent.objects.select_for_update().get(id=self.quiz_id)
            except QuizEvent.DoesNotExist:
                return None

            # STATE-02: Only advance questions on an active quiz
            if quiz.status != "active":
                return None

            if not quiz.current_round_id:
                return None

            # Fetch round separately (outside select_for_update scope)
            from crush_lu.models.quiz import QuizRound

            current_round = QuizRound.objects.get(pk=quiz.current_round_id)
            questions = current_round.questions.order_by("sort_order")
            total = questions.count()
            next_index = quiz.current_question_index + 1

            if next_index >= total:
                return None  # Round complete

            quiz.current_question_index = next_index
            quiz.question_started_at = timezone.now()
            quiz.save(update_fields=["current_question_index", "question_started_at"])

        question = questions[next_index]
        q_data = _build_question_data(question, include_answers=True)
        q_data["time"] = current_round.time_per_question
        q_data["index"] = next_index
        q_data["total"] = total
        q_data["is_bonus"] = current_round.is_bonus

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

        # Attribute scores to the round this question belongs to, not the
        # quiz's current round. Prevents a rotate between "question asked"
        # and "late score arrives" from crediting the new round's seatmates.
        round_number = quiz.get_round_number(question.round)

        # Try rotation schedule first
        rotation_users = list(
            QuizRotationSchedule.objects.filter(
                quiz=quiz,
                round_number=round_number,
                table=table,
            ).values_list("user_id", flat=True)
        )

        # Fall back to static membership when no rotation schedule exists
        if not rotation_users:
            rotation_users = list(
                QuizTableMembership.objects.filter(table=table).values_list(
                    "user_id", flat=True
                )
            )

        # CONC-03/DATA-01: Use get_or_create (write-once) to match REST endpoint
        # and prevent overwriting earlier scores from race conditions
        for user_id in rotation_users:
            IndividualScore.objects.get_or_create(
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
    def check_can_rotate(self):
        """Async wrapper for ``check_can_rotate`` used by the consumer.

        Delegates to the sync helper in services.quiz_rotation so tests
        can exercise the same logic synchronously without a channel
        layer or thread-scoped DB connection.
        """
        from crush_lu.services.quiz_rotation import check_can_rotate

        return check_can_rotate(self.quiz_id)

    @database_sync_to_async
    def advance_round_and_rotate(self):
        """Advance to the next round and return rotation assignments.

        CONC-05: Uses select_for_update to prevent race conditions when
        multiple host tabs send rotate simultaneously.
        """
        from django.db import transaction

        from crush_lu.models.quiz import QuizEvent, QuizRotationSchedule

        with transaction.atomic():
            try:
                # Lock QuizEvent row (same pattern as advance_question CONC-01)
                quiz = QuizEvent.objects.select_for_update().get(id=self.quiz_id)
            except QuizEvent.DoesNotExist:
                return {}

            # Fetch current_round separately (nullable FK, outside lock scope)
            from crush_lu.models.quiz import QuizRound

            current_round = None
            if quiz.current_round_id:
                current_round = QuizRound.objects.get(pk=quiz.current_round_id)

            # Find next round by (sort_order, pk) — handles duplicate sort_orders
            current_sort = current_round.sort_order if current_round else -1
            current_pk = current_round.pk if current_round else -1
            next_round = (
                quiz.rounds.filter(sort_order__gt=current_sort)
                .order_by("sort_order", "pk")
                .first()
            )
            # Same sort_order: advance by pk within that group
            if not next_round and current_round:
                next_round = (
                    quiz.rounds.filter(sort_order=current_sort, pk__gt=current_pk)
                    .order_by("pk")
                    .first()
                )

            if not next_round:
                # Issue #5: Set status to finished when no more rounds
                quiz.status = "finished"
                quiz.save(update_fields=["status"])
                return {"finished": True, "status": "finished"}

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
                "table_id": r.table_id,
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

        is_correct = False
        # Check answer against all language variants of choices
        for lang in _QUIZ_LANGUAGES:
            lang_choices = getattr(question, f"choices_{lang}", None) or []
            for choice in lang_choices:
                if (
                    isinstance(choice, dict)
                    and choice.get("text") == answer
                    and choice.get("is_correct")
                ):
                    is_correct = True
                    break
            if is_correct:
                break
        # Fallback: check the default choices field too
        if not is_correct:
            for choice in question.choices or []:
                if (
                    isinstance(choice, dict)
                    and choice.get("text") == answer
                    and choice.get("is_correct")
                ):
                    is_correct = True
                    break

        points = question.points if is_correct else 0

        # Atomic get_or_create to prevent race conditions on duplicate submissions
        _obj, created = IndividualScore.objects.get_or_create(
            quiz_id=self.quiz_id,
            user_id=user_id,
            question_id=question_id,
            defaults={
                "answer": answer,
                "is_correct": is_correct,
                "points_earned": points,
            },
        )
        if not created:
            return {"already_answered": True}

        # Return correct answer in default language
        correct_text = next(
            (
                c["text"]
                for c in (question.choices or [])
                if isinstance(c, dict) and c.get("is_correct")
            ),
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
                has_photo = bool(getattr(profile, "photo_1", None))
                photo_url = (
                    f"/api/quiz/photo/{entry['user_id']}/" if has_photo else None
                )
            except CrushProfile.DoesNotExist:
                name = "Anonymous"
                photo_url = None
            from crush_lu.views_quiz import _member_color, _member_initials

            individual_scores.append(
                {
                    "display_name": name,
                    "total_score": entry["total"],
                    "initials": _member_initials(name),
                    "color": _member_color(name),
                    "photo_url": photo_url,
                }
            )

        return {
            "tables": leaderboard,
            "individuals": individual_scores,
        }


class CheckinConsumer(AsyncJsonWebsocketConsumer):
    """WebSocket consumer for live check-in updates during events.

    Read-only consumer: coaches receive broadcasts when attendees are checked in.
    All check-in data flows from the HTTP API via channel layer group_send.
    """

    async def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            await self.close()
            return

        if not await self._is_coach(user):
            await self.close()
            return

        self.event_id = self.scope["url_route"]["kwargs"]["event_id"]
        self.checkin_group = f"checkin_{self.event_id}"

        await self.channel_layer.group_add(self.checkin_group, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "checkin_group"):
            await self.channel_layer.group_discard(
                self.checkin_group, self.channel_name
            )

    async def receive_json(self, content):
        pass

    async def checkin_update(self, event):
        """Forward check-in update to connected coaches."""
        await self.send_json({"type": "checkin.update", "data": event["data"]})

    @database_sync_to_async
    def _is_coach(self, user):
        from crush_lu.models import CrushCoach

        return CrushCoach.objects.filter(user=user, is_active=True).exists()
