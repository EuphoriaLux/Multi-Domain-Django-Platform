import json
import re
from urllib.parse import urlparse, urlunparse

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from crush_lu.storage import crush_media_storage, crush_upload_path

#: Per-path upload factory for quiz media (image/video/audio).
quiz_upload_path = crush_upload_path("quiz")

#: Hosts allowed for external media embeds (YouTube/Vimeo/Spotify/SoundCloud).
#: Used by both the model validation and the client-side iframe renderer.
QUIZ_EMBED_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
    "vimeo.com",
    "player.vimeo.com",
    "open.spotify.com",
    "soundcloud.com",
    "w.soundcloud.com",
}


def is_embed_url_allowed(url):
    """Return True if *url*'s host is on the quiz embed allowlist."""
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
    except (ValueError, TypeError):
        return False
    return host in QUIZ_EMBED_HOSTS


# --- URL → embed normalization -------------------------------------------
# Each entry: (host_match, path_regex, embed_template). Tested via the model
# tests; only well-known canonical forms are rewritten. Unknown hosts already
# failed the allowlist check above, so the fallback is the URL unchanged.
_YOUTUBE_ID = r"([A-Za-z0-9_-]{11})"
_SPOTIFY_PATH = r"/(track|album|playlist|episode|show)/([A-Za-z0-9]+)"
_EMBED_RULES = [
    # youtu.be/<id>  ->  youtube-nocookie.com/embed/<id>
    (
        "youtu.be",
        re.compile(rf"^/{_YOUTUBE_ID}$"),
        "https://www.youtube-nocookie.com/embed/{0}",
    ),
    # youtube.com/watch?v=<id>  ->  youtube-nocookie.com/embed/<id>
    ("www.youtube.com", re.compile(r"^/watch$"), None),  # handled via query below
    ("youtube.com", re.compile(r"^/watch$"), None),
    # youtube.com/embed/<id>  ->  youtube-nocookie.com/embed/<id>
    (
        "www.youtube.com",
        re.compile(rf"^/embed/{_YOUTUBE_ID}$"),
        "https://www.youtube-nocookie.com/embed/{0}",
    ),
    (
        "youtube.com",
        re.compile(rf"^/embed/{_YOUTUBE_ID}$"),
        "https://www.youtube-nocookie.com/embed/{0}",
    ),
    # vimeo.com/<id>  ->  player.vimeo.com/video/<id>
    ("vimeo.com", re.compile(r"^/(\d+)$"), "https://player.vimeo.com/video/{0}"),
    # open.spotify.com/<type>/<id>  ->  embed.spotify.com/<type>/<id> via /embed/
    (
        "open.spotify.com",
        re.compile(_SPOTIFY_PATH),
        "https://open.spotify.com/embed/{0}/{1}",
    ),
]


def normalize_embed_url(url):
    """Rewrite a watch URL into its canonical embed form.

    Returns the URL unchanged when no rule matches (the host was already
    allowlisted, e.g. an existing player.vimeo.com or w.soundcloud.com URL).
    """
    if not url:
        return url
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return url
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""

    # YouTube watch?v=<id> (needs the query string)
    if host in ("www.youtube.com", "youtube.com") and path == "/watch":
        from urllib.parse import parse_qs

        vid = (parse_qs(parsed.query).get("v") or [None])[0]
        if vid and re.match(rf"^{_YOUTUBE_ID}$", vid):
            return f"https://www.youtube-nocookie.com/embed/{vid}"

    for rule_host, rule_re, tmpl in _EMBED_RULES:
        if host != rule_host or tmpl is None:
            continue
        m = rule_re.match(path)
        if m:
            return tmpl.format(*m.groups())

    return urlunparse(parsed)


def parse_choices(choices):
    """Safely parse question choices — handles list-of-dicts, list-of-strings, and JSON strings."""
    if isinstance(choices, str):
        try:
            choices = json.loads(choices)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(choices, list):
        return []
    result = []
    for c in choices:
        if isinstance(c, dict):
            result.append(c)
        elif isinstance(c, str):
            result.append({"text": c, "is_correct": False})
    return result


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
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="draft")
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
    num_tables = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=_(
            "Number of physical tables available. "
            "Leave blank to auto-calculate from participant count."
        ),
    )
    tables_generated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When table assignments were last generated."),
    )
    display_token = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text=_(
            "Shared secret for projector display access. "
            "Leave blank for unrestricted access."
        ),
    )
    question_started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When the current question was first displayed."),
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

    def get_round_number(self, round_obj=None):
        """Get the 0-indexed rotation round_number for a given round.

        round_number in QuizRotationSchedule is the positional index
        of the round in (sort_order, pk) ordering (0, 1, 2, ...).
        This handles duplicate sort_order values correctly by using
        pk as a tiebreaker.

        Args:
            round_obj: The round to compute for. Defaults to current_round.

        Returns:
            int: 0-indexed round number, or 0 if no round is set.
        """
        target = round_obj or self.current_round
        if not target:
            return 0
        return self.rounds.filter(
            models.Q(sort_order__lt=target.sort_order)
            | models.Q(sort_order=target.sort_order, pk__lt=target.pk)
        ).count()

    def get_current_question(self):
        """Return the current question based on round and index."""
        if not self.current_round:
            return None
        questions = self.current_round.questions.order_by("sort_order")
        if 0 <= self.current_question_index < questions.count():
            return questions[self.current_question_index]
        return None

    def ensure_tables(self):
        """Create or reconcile QuizTable objects to match self.num_tables.

        - Creates missing tables (1..num_tables)
        - Removes excess tables only if they have no scores attached
        Returns the dict of {table_number: QuizTable} objects.
        """
        if not self.num_tables:
            return {}

        existing = {t.table_number: t for t in QuizTable.objects.filter(quiz=self)}
        tables = {}
        for t in range(1, self.num_tables + 1):
            if t in existing:
                tables[t] = existing[t]
            else:
                tables[t] = QuizTable.objects.create(quiz=self, table_number=t)

        # Remove excess tables only if they have no scores
        for t_num, t_obj in existing.items():
            if t_num > self.num_tables:
                if not t_obj.round_scores.exists():
                    t_obj.delete()

        return tables

    def readiness_check(self):
        """Return a list of checks with pass/fail status for quiz readiness.

        Each item: {"label": str, "ok": bool, "detail": str}
        """
        from crush_lu.models.events import EventRegistration

        checks = []

        # 1. Has rounds?
        round_count = self.rounds.count()
        checks.append(
            {
                "label": _("Rounds"),
                "ok": round_count > 0,
                "detail": str(round_count) if round_count else _("No rounds created"),
            }
        )

        # 2. Has questions?
        question_count = sum(
            r.questions.count() for r in self.rounds.prefetch_related("questions").all()
        )
        checks.append(
            {
                "label": _("Questions"),
                "ok": question_count > 0,
                "detail": (
                    str(question_count) if question_count else _("No questions created")
                ),
            }
        )

        # 3. Questions have correct answers?
        bad_questions = []
        for r in self.rounds.prefetch_related("questions").all():
            for q in r.questions.all():
                if q.question_type in ("multiple_choice", "true_false"):
                    choices = q.choices or []
                    has_correct = any(
                        isinstance(c, dict) and c.get("is_correct") for c in choices
                    )
                    if not has_correct:
                        bad_questions.append(q.text[:40])
        checks.append(
            {
                "label": _("Correct answers"),
                "ok": len(bad_questions) == 0 and question_count > 0,
                "detail": (
                    _("All questions have a correct answer")
                    if not bad_questions and question_count > 0
                    else (
                        ", ".join(bad_questions[:3])
                        + ("..." if len(bad_questions) > 3 else "")
                        if bad_questions
                        else _("No questions to check")
                    )
                ),
            }
        )

        # 4. Tables configured?
        table_count = QuizTable.objects.filter(quiz=self).count()
        checks.append(
            {
                "label": _("Tables"),
                "ok": table_count >= 2,
                "detail": (
                    str(table_count)
                    if table_count >= 2
                    else _("Need at least 2 tables")
                ),
            }
        )

        # 5. Registrations?
        reg_count = EventRegistration.objects.filter(
            event=self.event, status__in=["confirmed", "attended"]
        ).count()
        checks.append(
            {
                "label": _("Registrations"),
                "ok": reg_count >= 4,
                "detail": (
                    _("%(count)d confirmed/attended") % {"count": reg_count}
                    if reg_count >= 4
                    else _("%(count)d (need at least 4)") % {"count": reg_count}
                ),
            }
        )

        # 6. Table members assigned?
        member_count = QuizTableMembership.objects.filter(table__quiz=self).count()
        rotation_count = QuizRotationSchedule.objects.filter(quiz=self).count()
        has_assignments = member_count > 0 or rotation_count > 0
        checks.append(
            {
                "label": _("Table assignments"),
                "ok": has_assignments,
                "detail": (
                    _("%(members)d members (%(rotations)d rotations)")
                    % {"members": member_count, "rotations": rotation_count}
                    if has_assignments
                    else _("No members assigned to tables")
                ),
            }
        )

        # 7. Members have photos?
        if has_assignments:
            from crush_lu.models import CrushProfile

            if rotation_count > 0:
                user_ids = list(
                    QuizRotationSchedule.objects.filter(quiz=self)
                    .values_list("user_id", flat=True)
                    .distinct()
                )
            else:
                user_ids = list(
                    QuizTableMembership.objects.filter(table__quiz=self)
                    .values_list("user_id", flat=True)
                    .distinct()
                )
            profiles_with_photo = (
                CrushProfile.objects.filter(user_id__in=user_ids)
                .exclude(photo_1="")
                .exclude(photo_1__isnull=True)
                .count()
            )
            checks.append(
                {
                    "label": _("Profile photos"),
                    "ok": profiles_with_photo == len(user_ids),
                    "detail": (
                        _("%(with)d/%(total)d participants have photos")
                        % {"with": profiles_with_photo, "total": len(user_ids)}
                    ),
                }
            )

        return checks


class QuizRound(models.Model):
    """A named round within a quiz (e.g., 'Round 1: Movies')."""

    quiz = models.ForeignKey(QuizEvent, on_delete=models.CASCADE, related_name="rounds")
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

    MEDIA_KIND_CHOICES = [
        ("none", _("No media")),
        ("image", _("Image")),
        ("video", _("Video")),
        ("audio", _("Audio")),
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
    # --- Optional media stimulus (orthogonal to question_type) ---------------
    # Media is language-neutral, so these fields are NOT translated.
    media_kind = models.CharField(
        max_length=10,
        choices=MEDIA_KIND_CHOICES,
        default="none",
        help_text=_("Optional media (image/video/audio) shown alongside the prompt."),
    )
    media_file = models.FileField(
        upload_to=quiz_upload_path,
        storage=crush_media_storage,
        blank=True,
        null=True,
        help_text=_(
            "Uploaded image/video/audio (public Azure Blob). Recommended for "
            "images and short clips — use an external embed URL for long video/audio."
        ),
    )
    media_url = models.URLField(
        max_length=500,
        blank=True,
        help_text=_(
            "External embed URL (YouTube / Vimeo / Spotify / SoundCloud). "
            "Watch URLs are normalized to their embed form on save."
        ),
    )
    sort_order = models.PositiveIntegerField(default=0)
    points = models.PositiveIntegerField(default=10)

    class Meta:
        ordering = ["sort_order"]
        verbose_name = _("Quiz Question")
        verbose_name_plural = _("Quiz Questions")

    def __str__(self):
        return self.text[:80]

    # ------------------------------------------------------------------ media
    def clean(self):
        """Validate media: a non-none kind requires a file or an external URL."""
        super().clean()
        if self.media_kind != "none" and not self.media_file and not self.media_url:
            raise ValidationError(
                {
                    "media_file": _(
                        "Upload a file or provide an external URL for this media kind."
                    )
                }
            )
        if self.media_url and not is_embed_url_allowed(self.media_url):
            raise ValidationError(
                {
                    "media_url": _(
                        "External media URLs must come from YouTube, Vimeo, "
                        "Spotify, or SoundCloud."
                    )
                }
            )

    def get_media_payload(self):
        """Normalized media dict broadcast to clients.

        Always returns the same shape so every broadcast site (WebSocket
        consumer, REST state endpoint, projector display JSON) stays in sync.
        A file upload takes precedence over an external URL when both exist.

        External URLs are normalized to their embed form so the client only
        ever receives a ready-to-embed URL from a trusted host.
        """
        if self.media_kind == "none":
            return {"kind": "none", "url": None, "source": None}
        if self.media_file:
            return {
                "kind": self.media_kind,
                "url": self.media_file.url,
                "source": "upload",
            }
        if self.media_url:
            normalized = normalize_embed_url(self.media_url)
            return {
                "kind": self.media_kind,
                "url": normalized,
                "source": "external",
            }
        # Inconsistent state (kind set but no file/URL) — degrade gracefully.
        return {"kind": "none", "url": None, "source": None}


class QuizTable(models.Model):
    """A group of participants at a physical table during the quiz."""

    quiz = models.ForeignKey(QuizEvent, on_delete=models.CASCADE, related_name="tables")
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
        """Sum of points for questions this table answered correctly.

        Uses TableRoundScore (table-level results) rather than
        IndividualScore (user-level) so that rotation-based quiz nights
        correctly attribute scores to the table, not to users who may
        have moved to other tables in later rounds.
        """
        total = 0
        for score in TableRoundScore.objects.filter(
            quiz=self.quiz, table=self, is_correct=True
        ).select_related("question__round"):
            pts = score.question.points
            if score.question.round.is_bonus:
                pts *= 2
            total += pts
        return total


class QuizTableMembership(models.Model):
    """Through model for quiz table membership."""

    table = models.ForeignKey(
        QuizTable, on_delete=models.CASCADE, related_name="memberships"
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
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
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
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
        return (
            f"Table {self.table.table_number} - Q{self.question.sort_order} - {status}"
        )


class IndividualScore(models.Model):
    """Score per user per question."""

    quiz = models.ForeignKey(QuizEvent, on_delete=models.CASCADE, related_name="scores")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="quiz_scores",
    )
    question = models.ForeignKey(
        QuizQuestion, on_delete=models.CASCADE, related_name="scores"
    )
    answer = models.JSONField(default=str, blank=True, help_text=_("The chosen answer"))
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
