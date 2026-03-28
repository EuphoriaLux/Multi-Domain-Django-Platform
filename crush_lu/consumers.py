import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.db.models import Sum


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
            table_id = await self.get_user_table_id(user.id)
            if table_id:
                self.table_group = f"quiz_{self.quiz_id}_table_{table_id}"
                await self.channel_layer.group_add(
                    self.table_group, self.channel_name
                )

        await self.accept()

        # Send current quiz state on connect
        state = await self.get_quiz_state()
        if state:
            await self.send_json({"type": "quiz.state", "data": state})

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.quiz_group, self.channel_name
        )
        if self.table_group:
            await self.channel_layer.group_discard(
                self.table_group, self.channel_name
            )

    async def receive_json(self, content):
        action = content.get("action")
        user = self.scope.get("user")

        if action == "next_question":
            if await self.is_coach(user):
                await self.handle_next_question()

        elif action == "table_answer":
            if user and user.is_authenticated:
                await self.handle_table_answer(user, content)

        elif action == "rotate":
            if await self.is_coach(user):
                await self.handle_rotate()

        elif action == "show_leaderboard":
            if await self.is_coach(user):
                await self.handle_leaderboard()

    # --- Coach actions ---

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

    async def handle_rotate(self):
        await self.channel_layer.group_send(
            self.quiz_group,
            {"type": "quiz.rotate", "data": {}},
        )

    async def handle_leaderboard(self):
        leaderboard = await self.get_leaderboard()
        await self.channel_layer.group_send(
            self.quiz_group,
            {"type": "quiz.leaderboard", "data": leaderboard},
        )

    # --- Attendee actions ---

    async def handle_table_answer(self, user, content):
        question_id = content.get("question_id")
        answer = content.get("answer")
        if question_id is None or answer is None:
            return

        result = await self.score_answer(user.id, question_id, answer)
        if result is None:
            return  # Already answered or invalid

        # Send result to the answering user
        await self.send_json({"type": "quiz.answer_result", "data": result})

        # Broadcast updated table score to the table group
        if self.table_group:
            table_score = await self.get_table_score_for_user(user.id)
            await self.channel_layer.group_send(
                self.table_group,
                {
                    "type": "quiz.table_score",
                    "data": table_score,
                },
            )

    # --- Group message handlers (called by channel_layer.group_send) ---

    async def quiz_question(self, event):
        await self.send_json(
            {"type": "quiz.question", "data": event["data"]}
        )

    async def quiz_rotate(self, event):
        await self.send_json({"type": "quiz.rotate", "data": event["data"]})

    async def quiz_leaderboard(self, event):
        await self.send_json(
            {"type": "quiz.leaderboard", "data": event["data"]}
        )

    async def quiz_status(self, event):
        await self.send_json({"type": "quiz.status", "data": event["data"]})

    async def quiz_table_score(self, event):
        await self.send_json(
            {"type": "quiz.table_score", "data": event["data"]}
        )

    # --- Database helpers ---

    @database_sync_to_async
    def get_user_table_id(self, user_id):
        from crush_lu.models.quiz import QuizTableMembership

        membership = (
            QuizTableMembership.objects.filter(
                table__quiz_id=self.quiz_id, user_id=user_id
            )
            .select_related("table")
            .first()
        )
        return membership.table_id if membership else None

    @database_sync_to_async
    def is_coach(self, user):
        if not user or not user.is_authenticated:
            return False
        from crush_lu.models.quiz import QuizEvent

        try:
            quiz = QuizEvent.objects.select_related("event").get(
                id=self.quiz_id
            )
            return quiz.created_by_id == user.id or user.is_staff
        except QuizEvent.DoesNotExist:
            return False

    @database_sync_to_async
    def get_quiz_state(self):
        from crush_lu.models.quiz import QuizEvent

        try:
            quiz = QuizEvent.objects.select_related(
                "current_round", "event"
            ).get(id=self.quiz_id)
        except QuizEvent.DoesNotExist:
            return None

        question = quiz.get_current_question()
        data = {
            "status": quiz.status,
            "current_round": (
                {
                    "title": quiz.current_round.title,
                    "time_per_question": quiz.current_round.time_per_question,
                }
                if quiz.current_round
                else None
            ),
            "question_index": quiz.current_question_index,
        }
        if question and quiz.is_active:
            data["question"] = {
                "id": question.id,
                "text": question.text,
                "choices": [
                    {"text": c["text"]} for c in question.choices
                ],
                "points": question.points,
            }
        return data

    @database_sync_to_async
    def advance_question(self):
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
        next_index = quiz.current_question_index + 1

        if next_index >= questions.count():
            return None  # Round complete

        quiz.current_question_index = next_index
        quiz.status = "active"
        quiz.save(update_fields=["current_question_index", "status"])

        question = questions[next_index]
        return {
            "id": question.id,
            "text": question.text,
            "choices": [{"text": c["text"]} for c in question.choices],
            "points": question.points,
            "time": quiz.current_round.time_per_question,
            "index": next_index,
            "total": questions.count(),
        }

    @database_sync_to_async
    def score_answer(self, user_id, question_id, answer):
        from crush_lu.models.quiz import IndividualScore, QuizQuestion

        try:
            question = QuizQuestion.objects.get(
                id=question_id, round__quiz_id=self.quiz_id
            )
        except QuizQuestion.DoesNotExist:
            return None

        # Prevent duplicate answers
        if IndividualScore.objects.filter(
            quiz_id=self.quiz_id, user_id=user_id, question_id=question_id
        ).exists():
            return None

        # Check correctness
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
        total = (
            IndividualScore.objects.filter(
                quiz_id=self.quiz_id,
                user__quiz_tables__quiz_id=self.quiz_id,
                user__quiz_tables__table_number=table.table_number,
            ).aggregate(total=Sum("points_earned"))["total"]
            or 0
        )
        return {"table_number": table.table_number, "total_score": total}

    @database_sync_to_async
    def get_leaderboard(self):
        from crush_lu.models.quiz import QuizTable

        tables = QuizTable.objects.filter(quiz_id=self.quiz_id).order_by(
            "table_number"
        )
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

        # Add individual top scorers (using display_name for privacy)
        from crush_lu.models.quiz import IndividualScore

        top_individuals = (
            IndividualScore.objects.filter(quiz_id=self.quiz_id)
            .values("user_id")
            .annotate(total=Sum("points_earned"))
            .order_by("-total")[:10]
        )

        individual_scores = []
        for entry in top_individuals:
            from crush_lu.models import CrushProfile

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
