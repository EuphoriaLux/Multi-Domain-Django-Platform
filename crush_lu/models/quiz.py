from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class QuizEvent(models.Model):
    """Links a live quiz to a MeetupEvent for real-time play."""

    STATUS_CHOICES = [
        ("draft", _("Draft")),
        ("active", _("Active")),
        ("paused", _("Paused")),
        ("finished", _("Finished")),
    ]

    event = models.OneToOneField(
        "crush_lu.MeetupEvent",
        on_delete=models.CASCADE,
        related_name="quiz",
    )
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="draft"
    )
    current_round = models.ForeignKey(
        "QuizRound",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    current_question_index = models.IntegerField(default=-1)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="created_quizzes",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Quiz Event")
        verbose_name_plural = _("Quiz Events")

    def __str__(self):
        return f"Quiz for {self.event}"

    @property
    def is_active(self):
        return self.status == "active"

    def get_current_question(self):
        """Return the current question based on round and index."""
        if not self.current_round:
            return None
        questions = self.current_round.questions.order_by("sort_order")
        if self.current_question_index < questions.count():
            return questions[self.current_question_index]
        return None


class QuizRound(models.Model):
    """A named round within a quiz (e.g., 'Round 1: Movies')."""

    quiz = models.ForeignKey(
        QuizEvent, on_delete=models.CASCADE, related_name="rounds"
    )
    title = models.CharField(max_length=200)
    sort_order = models.PositiveIntegerField(default=0)
    time_per_question = models.PositiveIntegerField(
        default=30, help_text=_("Seconds allowed per question")
    )
    is_bonus = models.BooleanField(
        default=False, help_text=_("Bonus round: points are doubled")
    )

    class Meta:
        ordering = ["sort_order"]
        verbose_name = _("Quiz Round")
        verbose_name_plural = _("Quiz Rounds")

    def __str__(self):
        return f"{self.quiz.event} - {self.title}"


class QuizQuestion(models.Model):
    """Individual question within a round."""

    QUESTION_TYPE_CHOICES = [
        ("multiple_choice", _("Multiple Choice")),
        ("true_false", _("True / False")),
        ("open_ended", _("Open Ended")),
    ]

    round = models.ForeignKey(
        QuizRound, on_delete=models.CASCADE, related_name="questions"
    )
    text = models.CharField(max_length=500)
    question_type = models.CharField(
        max_length=20, choices=QUESTION_TYPE_CHOICES, default="multiple_choice"
    )
    choices = models.JSONField(
        default=list,
        blank=True,
        help_text=_('List of {"text": "...", "is_correct": true/false}'),
    )
    correct_answer = models.CharField(
        max_length=500,
        blank=True,
        help_text=_("Reference answer for host (used for open-ended questions)"),
    )
    sort_order = models.PositiveIntegerField(default=0)
    points = models.PositiveIntegerField(default=10)

    class Meta:
        ordering = ["sort_order"]
        verbose_name = _("Quiz Question")
        verbose_name_plural = _("Quiz Questions")

    def __str__(self):
        return self.text[:80]


class QuizTable(models.Model):
    """A group of participants at a physical table during the quiz."""

    quiz = models.ForeignKey(
        QuizEvent, on_delete=models.CASCADE, related_name="tables"
    )
    table_number = models.PositiveIntegerField()
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="QuizTableMembership",
        related_name="quiz_tables",
    )

    class Meta:
        ordering = ["table_number"]
        unique_together = [("quiz", "table_number")]
        verbose_name = _("Quiz Table")
        verbose_name_plural = _("Quiz Tables")

    def __str__(self):
        return f"Table {self.table_number} - {self.quiz.event}"

    def get_total_score(self):
        """Sum of all member scores for this table's quiz."""
        return (
            IndividualScore.objects.filter(
                quiz=self.quiz, user__in=self.members.all()
            ).aggregate(total=models.Sum("points_earned"))["total"]
            or 0
        )


class QuizTableMembership(models.Model):
    """Through model for quiz table membership."""

    table = models.ForeignKey(
        QuizTable, on_delete=models.CASCADE, related_name="memberships"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("table", "user")]
        verbose_name = _("Table Membership")
        verbose_name_plural = _("Table Memberships")

    def __str__(self):
        return f"{self.user} at {self.table}"


class QuizRotationSchedule(models.Model):
    """Per-round table assignment for quiz night gendered rotation."""

    ROLE_CHOICES = [
        ("anchor", _("Anchor (stays at table)")),
        ("rotator", _("Rotator (moves between tables)")),
    ]

    quiz = models.ForeignKey(
        QuizEvent, on_delete=models.CASCADE, related_name="rotation_schedule"
    )
    round_number = models.PositiveIntegerField()
    table = models.ForeignKey(
        QuizTable, on_delete=models.CASCADE, related_name="rotation_entries"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE
    )
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    rotation_group = models.CharField(
        max_length=1,
        blank=True,
        help_text=_("Rotation group: A or B (empty for anchors)"),
    )

    class Meta:
        unique_together = [("quiz", "round_number", "user")]
        ordering = ["round_number", "table__table_number"]
        verbose_name = _("Rotation Schedule Entry")
        verbose_name_plural = _("Rotation Schedule")

    def __str__(self):
        return (
            f"Round {self.round_number} - Table {self.table.table_number} - "
            f"{self.user}"
        )


class TableRoundScore(models.Model):
    """Host-scored table result per question (host marks tables correct/incorrect)."""

    quiz = models.ForeignKey(
        QuizEvent, on_delete=models.CASCADE, related_name="table_scores"
    )
    table = models.ForeignKey(
        QuizTable, on_delete=models.CASCADE, related_name="round_scores"
    )
    question = models.ForeignKey(
        QuizQuestion, on_delete=models.CASCADE, related_name="table_scores"
    )
    is_correct = models.BooleanField(default=False)
    scored_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("quiz", "table", "question")]
        verbose_name = _("Table Round Score")
        verbose_name_plural = _("Table Round Scores")

    def __str__(self):
        status = "correct" if self.is_correct else "incorrect"
        return f"Table {self.table.table_number} - Q{self.question.sort_order} - {status}"


class IndividualScore(models.Model):
    """Score per user per question."""

    quiz = models.ForeignKey(
        QuizEvent, on_delete=models.CASCADE, related_name="scores"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="quiz_scores",
    )
    question = models.ForeignKey(
        QuizQuestion, on_delete=models.CASCADE, related_name="scores"
    )
    answer = models.JSONField(
        default=str, blank=True, help_text=_("The chosen answer")
    )
    is_correct = models.BooleanField(default=False)
    points_earned = models.PositiveIntegerField(default=0)
    answered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("quiz", "user", "question")]
        ordering = ["-answered_at"]
        verbose_name = _("Individual Score")
        verbose_name_plural = _("Individual Scores")

    def __str__(self):
        return f"{self.user} - Q{self.question.sort_order} - {self.points_earned}pts"
