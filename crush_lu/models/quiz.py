from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class QuizEvent(models.Model):
    """Links a live quiz to a MeetupEvent for real-time play during speed dating."""

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
    current_question_index = models.PositiveIntegerField(default=0)
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
    """A named round within a quiz (e.g., 'Round 1: Icebreaker')."""

    quiz = models.ForeignKey(
        QuizEvent, on_delete=models.CASCADE, related_name="rounds"
    )
    title = models.CharField(max_length=200)
    sort_order = models.PositiveIntegerField(default=0)
    time_per_question = models.PositiveIntegerField(
        default=30, help_text=_("Seconds allowed per question")
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
    ]

    round = models.ForeignKey(
        QuizRound, on_delete=models.CASCADE, related_name="questions"
    )
    text = models.CharField(max_length=500)
    question_type = models.CharField(
        max_length=20, choices=QUESTION_TYPE_CHOICES, default="multiple_choice"
    )
    choices = models.JSONField(
        help_text=_('List of {"text": "...", "is_correct": true/false}')
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
    answer = models.JSONField(help_text=_("The chosen answer"))
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
