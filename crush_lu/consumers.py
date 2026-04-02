import json
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.db.models import Sum

logger = logging.getLogger(__name__)


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

        # Broadcast table scored event
        await self.channel_layer.group_send(
            self.quiz_group,
            {
                "type": "quiz.table_scored",
                "data": {
                    "table_id": table_id,
                    "table_number": result["table_number"],
                    "question_id": question_id,
                    "is_correct": is_correct,
                    "points_awarded": result["points_awarded"],
                },
            },
        )

    async def handle_rotate(self):
        rotation_data = await self.advance_round_and_rotate()
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
        data = {
            "status": quiz.status,
            "event_type": quiz.event.event_type,
            "current_round": (
                {
                    "id": quiz.current_round.id,
                    "title": quiz.current_round.title,
                    "time_per_question": quiz.current_round.time_per_question,
                    "is_bonus": quiz.current_round.is_bonus,
                }
                if quiz.current_round
                else None
            ),
            "question_index": quiz.current_question_index,
            # Round list with status for guided flow
            "rounds": [
                {
                    "id": r.id,
                    "title": r.title,
                    "sort_order": r.sort_order,
                    "is_bonus": r.is_bonus,
                    "question_count": r.questions.count(),
                    "status": (
                        "current"
                        if quiz.current_round_id == r.id
                        else ("done" if r.sort_order < current_sort else "upcoming")
                    ),
                }
                for r in quiz.rounds.order_by("sort_order")
            ],
        }
        if question and quiz.is_active:
            q_data = {
                "id": question.id,
                "text": question.text,
                "question_type": question.question_type,
                "points": question.points,
            }
            if question.question_type in ("multiple_choice", "true_false"):
                choices = _parse_choices(question.choices)
                q_data["choices"] = [
                    {"text": c["text"]} for c in choices if isinstance(c, dict)
                ]
            data["question"] = q_data
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
        quiz.save(update_fields=["current_round", "current_question_index", "status"])

        round_info = {
            "status": "active",
            "current_round": {
                "id": first_round.id,
                "title": first_round.title,
                "time_per_question": first_round.time_per_question,
                "is_bonus": first_round.is_bonus,
            },
            "message": f"Quiz started: {first_round.title}",
        }

        # Get first question
        questions = first_round.questions.order_by("sort_order")
        total = questions.count()
        question_data = None
        if total > 0:
            question = questions[0]
            question_data = {
                "id": question.id,
                "text": question.text,
                "question_type": question.question_type,
                "points": question.points,
                "time": first_round.time_per_question,
                "index": 0,
                "total": total,
                "is_bonus": first_round.is_bonus,
            }
            if question.question_type in ("multiple_choice", "true_false"):
                choices = _parse_choices(question.choices)
                question_data["choices"] = [
                    {"text": c["text"]} for c in choices if isinstance(c, dict)
                ]
                question_data["choices_with_answers"] = choices
            elif question.question_type == "open_ended":
                question_data["correct_answer"] = question.correct_answer

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

        quiz.current_round = round_obj
        # Set to -1 so the first next_question lands on index 0 (Issue #2)
        quiz.current_question_index = -1
        quiz.save(update_fields=["current_round", "current_question_index"])

        return {
            "status": quiz.status,
            "current_round": {
                "id": round_obj.id,
                "title": round_obj.title,
                "time_per_question": round_obj.time_per_question,
                "is_bonus": round_obj.is_bonus,
            },
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
        quiz.save(update_fields=["status"])
        return {"status": status}

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
        quiz.save(update_fields=["current_question_index", "status"])

        question = questions[next_index]
        q_data = {
            "id": question.id,
            "text": question.text,
            "question_type": question.question_type,
            "points": question.points,
            "time": quiz.current_round.time_per_question,
            "index": next_index,
            "total": total,
            "is_bonus": quiz.current_round.is_bonus,
        }

        if question.question_type in ("multiple_choice", "true_false"):
            choices = _parse_choices(question.choices)
            q_data["choices"] = [
                {"text": c["text"]} for c in choices if isinstance(c, dict)
            ]
            # Host gets correct answer info
            q_data["choices_with_answers"] = choices
        elif question.question_type == "open_ended":
            q_data["correct_answer"] = question.correct_answer

        return q_data

    @database_sync_to_async
    def score_table_for_question(self, table_id, question_id, is_correct):
        """Host scores a table. Creates IndividualScores for all table members."""
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

        # Create or update TableRoundScore
        TableRoundScore.objects.update_or_create(
            quiz=quiz,
            table=table,
            question=question,
            defaults={"is_correct": is_correct},
        )

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

        return {
            "table_number": table.table_number,
            "points_awarded": points,
            "members_scored": len(rotation_users),
        }

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

        # Find next round by sort_order
        current_sort = quiz.current_round.sort_order if quiz.current_round else -1
        next_round = (
            quiz.rounds.filter(sort_order__gt=current_sort)
            .order_by("sort_order")
            .first()
        )

        if next_round:
            quiz.current_round = next_round
            # Set to -1 so first next_question lands on Q0 (Issue #2)
            quiz.current_question_index = -1
            quiz.save(update_fields=["current_round", "current_question_index"])

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
        for choice in question.choices:
            if choice["text"] == answer and choice.get("is_correct"):
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

        return {
            "is_correct": is_correct,
            "points_earned": points,
            "correct_answer": next(
                (c["text"] for c in question.choices if c.get("is_correct")),
                None,
            ),
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
