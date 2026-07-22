"""
Crush Connect — "Read-the-Photo" question-gated matching models (M8/M9).

Replaces the story-card presentation with a viral, weekly-rotating game: each
member picks 3 yes/no questions from the active week's catalogue and privately
answers them about themselves (the hidden "truth"). Other members, shown the
member's clear photo in their curated Drop, GUESS those 3 answers. A viewer
"reads" someone when at least ``GATE_ALIGN_MIN`` of 3 guesses match the owner's
truth; a match needs both directions to clear the bar (the alignment logic lives
in ``services.crush_connect``).

Models:
- ``ConnectQuestion``      — the catalogue (mirrors ``SparkPrompt`` + ``Interest``).
- ``ConnectQuestionWeek``  — the immutable per-ISO-week snapshot that drives rotation.
- ``MemberGateQuestion``   — a member's 3 picks WITH their own truth answer.
- ``ConnectQuestionAnswer``— a viewer's guess (the gate record + the aggregate stat).
"""
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

__all__ = [
    "ConnectQuestion",
    "ConnectQuestionWeek",
    "MemberGateQuestion",
    "ConnectQuestionAnswer",
]


class ConnectQuestion(models.Model):
    """
    A catalogue yes/no question, phrased from the profile-owner's POV and judged
    from their photo (e.g. "Do I work in Finance?", "Am I funnier than you?").

    Mirrors ``SparkPrompt``: ``text`` is translated via modeltranslation, ``weight``
    controls rotation likelihood, and ``is_active=False`` retires a question
    without deleting it (historical answers reference it via PROTECT). ``slug`` (a
    stable id ``SparkPrompt`` lacks) makes the seed + weekly rotation idempotent and
    lets tests reference a fixed question without knowing its pk.

    ``tier`` gates spiciness: mild/medium ship active; spicy questions are seeded
    inactive so the coach team can promote a themed "spicy week" when ready.
    """

    class Category(models.TextChoices):
        LIFESTYLE = "lifestyle", _("Lifestyle")
        CAREER = "career", _("Career & money")
        PERSONALITY = "personality", _("Personality")
        DATING = "dating", _("Dating & romance")
        SPICY = "spicy", _("Spicy")

    class Tier(models.IntegerChoices):
        MILD = 1, _("Mild")
        MEDIUM = 2, _("Medium")
        SPICY = 3, _("Spicy")

    slug = models.SlugField(
        max_length=60,
        unique=True,
        help_text=_("Stable identifier for seeds/rotation, e.g. 'work-finance'"),
    )
    text = models.CharField(
        max_length=200,
        help_text=_("Yes/no question, profile-owner POV (translated via modeltranslation)"),
    )
    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        db_index=True,
    )
    tier = models.PositiveSmallIntegerField(
        choices=Tier.choices,
        default=Tier.MILD,
        db_index=True,
        help_text=_("Spiciness tier; spicy questions ship inactive"),
    )
    is_active = models.BooleanField(
        default=True,
        help_text=_("Inactive questions stop being offered but stay linked from history"),
    )
    weight = models.PositiveSmallIntegerField(
        default=1,
        help_text=_("Rotation weight (higher = more likely to be in a week's set)"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_active", "category", "-weight", "id"]
        verbose_name = _("Connect Question")
        verbose_name_plural = _("Connect Questions")

    def __str__(self):
        return self.text


class ConnectQuestionWeek(models.Model):
    """
    Immutable snapshot of which questions are "in rotation" for one ISO week.

    Mirrors the ``ConnectDailyDrop`` pin-once idiom: the set is computed once per
    ISO week (deterministically, from the active catalogue) and pinned, so members
    pick from a stable weekly set and the rotation job is idempotent. The selection
    itself lives in ``services.crush_connect.get_or_create_question_week``.
    """

    iso_year = models.PositiveSmallIntegerField()
    iso_week = models.PositiveSmallIntegerField(help_text=_("ISO week number, 1..53"))
    week_start = models.DateField(help_text=_("Monday of this ISO week (local)"))
    questions = models.ManyToManyField(
        "crush_lu.ConnectQuestion",
        related_name="active_weeks",
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-week_start"]
        verbose_name = _("Connect Question Week")
        verbose_name_plural = _("Connect Question Weeks")
        constraints = [
            models.UniqueConstraint(
                fields=["iso_year", "iso_week"], name="connect_qweek_unique"
            )
        ]

    def __str__(self):
        return f"{self.iso_year}-W{self.iso_week:02d} ({self.questions.count()} questions)"


class MemberGateQuestion(models.Model):
    """
    One of a member's 3 gate questions, WITH the member's own truth answer.

    The through-model (rather than a plain M2M) is needed to store display
    ``position``, the owner's ``owner_answer`` (the hidden truth alignment is
    scored against), and which week the pick came from. The "exactly 3" rule is
    enforced in the pick form (``ConnectGateQuestionsForm``), mirroring the
    interest/trait caps — Django can't express a row-count constraint portably.
    """

    membership = models.ForeignKey(
        "crush_lu.CrushConnectMembership",
        on_delete=models.CASCADE,
        related_name="gate_questions",
    )
    question = models.ForeignKey(
        "crush_lu.ConnectQuestion",
        on_delete=models.PROTECT,
        related_name="+",
    )
    position = models.PositiveSmallIntegerField(help_text=_("Display order, 1..3"))
    owner_answer = models.BooleanField(
        help_text=_("The member's own truthful yes/no — what guesses are scored against"),
    )
    picked_week = models.ForeignKey(
        "crush_lu.ConnectQuestionWeek",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("Which week's catalogue this pick came from"),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["membership_id", "position"]
        verbose_name = _("Member Gate Question")
        verbose_name_plural = _("Member Gate Questions")
        constraints = [
            models.UniqueConstraint(
                fields=["membership", "position"], name="member_gate_unique_position"
            ),
            models.UniqueConstraint(
                fields=["membership", "question"], name="member_gate_unique_question"
            ),
        ]

    def __str__(self):
        return f"{self.membership_id} #{self.position}: {self.question} = {'Yes' if self.owner_answer else 'No'}"


class ConnectQuestionAnswer(models.Model):
    """
    A viewer's GUESS at one of a profile owner's gate questions, judged from the
    photo. Doubles as the match-gate record and the anonymous aggregate stat.

    Uniqueness ``(responder, profile_owner, question)`` = you guess each of their 3
    questions once. A completed guess set = 3 rows for a (responder, owner) pair
    matching the owner's current gate questions. Aggregate stat =
    ``filter(profile_owner=u, question=q)`` grouped by ``answer`` — shown as
    "N of M think you …", never per-responder identity.
    """

    responder = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="connect_answers_given",
    )
    profile_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="connect_answers_received",
    )
    question = models.ForeignKey(
        "crush_lu.ConnectQuestion",
        on_delete=models.PROTECT,
        related_name="+",
    )
    answer = models.BooleanField(help_text=_("The guess: Yes = True, No = False"))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Connect Question Answer")
        verbose_name_plural = _("Connect Question Answers")
        constraints = [
            models.UniqueConstraint(
                fields=["responder", "profile_owner", "question"],
                name="connect_answer_unique",
            ),
            models.CheckConstraint(
                condition=~models.Q(responder=models.F("profile_owner")),
                name="connect_answer_no_self",
            ),
        ]
        indexes = [
            models.Index(
                fields=["profile_owner", "question"],
                name="connect_answer_owner_q_idx",
            ),
        ]

    def __str__(self):
        return f"{self.responder_id} → {self.profile_owner_id}: {self.question_id} = {'Yes' if self.answer else 'No'}"
