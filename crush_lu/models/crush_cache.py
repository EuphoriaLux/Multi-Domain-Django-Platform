"""
Crush Cache — GPS + QR scavenger hunt played in teams at a MeetupEvent.

A hunt is an ordered sequence of stations. Each station can require
reaching a GPS location (server-side proximity check) and/or scanning a
QR code placed at the venue, then answering challenges for points.
Teams share one progress; a coach follows a live leaderboard.

Model relationships mirror the live quiz system (quiz.py): the hunt hangs
off a MeetupEvent via OneToOne, and challenge fields copy the Journey /
Advent vocabulary (see AdventDoorContent) rather than FK-ing
JourneyChallenge, which is bound to the per-person gift ownership chain.
"""

import secrets
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .profiles import get_crush_photo_storage

# Callable used by all file fields - Django calls this when needed
# This prevents migration drift between environments
crush_photo_storage = get_crush_photo_storage

# Join codes avoid ambiguous characters (0/O, 1/I) for shout-across-the-bar entry
JOIN_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
JOIN_CODE_LENGTH = 6


def generate_join_code():
    return "".join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(JOIN_CODE_LENGTH))


class CacheHunt(models.Model):
    """A scavenger hunt attached to a MeetupEvent."""

    STATUS_CHOICES = [
        ("draft", _("Draft")),
        ("live", _("Live")),
        ("finished", _("Finished")),
    ]

    # A hunt is either running or it isn't — no pause state.
    VALID_STATUS_TRANSITIONS = {
        "draft": ["live"],
        "live": ["finished"],
        "finished": [],
    }

    NAVIGATION_MODE_CHOICES = [
        ("map", _("Map (target pin shown)")),
        ("compass", _("Compass (distance + direction only)")),
        ("hidden", _("Hidden (no navigation help until QR scan)")),
    ]

    event = models.OneToOneField(
        "crush_lu.MeetupEvent",
        on_delete=models.CASCADE,
        related_name="cache_hunt",
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="draft")
    title = models.CharField(max_length=200)
    description = models.TextField(
        blank=True,
        help_text=_("Intro text shown in the lobby before the hunt starts"),
    )
    navigation_mode = models.CharField(
        max_length=10,
        choices=NAVIGATION_MODE_CHOICES,
        default="map",
        help_text=_("How players are guided to the next station"),
    )
    team_size_max = models.PositiveIntegerField(
        default=4, help_text=_("Maximum members per team")
    )
    allow_self_join = models.BooleanField(
        default=True,
        help_text=_(
            "Allow attendees to create/join teams themselves via join code. "
            "Disable to have the coach form all teams."
        ),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="created_cache_hunts",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Cache Hunt")
        verbose_name_plural = _("🗺️ 1. Cache Hunts")

    def __str__(self):
        return f"Cache Hunt for {self.event}"

    @property
    def is_live(self):
        return self.status == "live"

    def can_transition_to(self, new_status):
        return new_status in self.VALID_STATUS_TRANSITIONS.get(self.status, [])

    def ordered_stations(self):
        return self.stations.order_by("order")

    def get_leaderboard(self):
        """Team ranking: points desc, then earliest finish, then furthest along.

        Returns a list of dicts ready for templates/WS payloads.
        """
        entries = []
        progresses = (
            CacheTeamProgress.objects.filter(team__hunt=self)
            .select_related("team", "current_station")
        )
        for progress in progresses:
            entries.append(
                {
                    "team_id": progress.team_id,
                    "team_name": progress.team.name,
                    "team_color": progress.team.color,
                    "points": progress.total_points,
                    "station_order": (
                        progress.current_station.order
                        if progress.current_station
                        else None
                    ),
                    "is_finished": progress.is_finished,
                    "finished_at": progress.finished_at,
                }
            )
        entries.sort(
            key=lambda e: (
                -e["points"],
                e["finished_at"].timestamp() if e["finished_at"] else float("inf"),
                -(e["station_order"] or 0),
            )
        )
        for rank, entry in enumerate(entries, start=1):
            entry["rank"] = rank
        return entries

    def get_serialized_leaderboard(self):
        """Leaderboard with JSON-safe values — required for anything that
        crosses the channel layer or a JsonResponse (datetimes don't
        survive channels' JSON/msgpack serialization)."""
        return [
            {
                **entry,
                "finished_at": entry["finished_at"].isoformat()
                if entry["finished_at"]
                else None,
            }
            for entry in self.get_leaderboard()
        ]

    def readiness_check(self):
        """Return a list of checks with pass/fail status for hunt readiness.

        Each item: {"label": str, "ok": bool, "detail": str, "blocking": bool}.
        Blocking failures make the hunt unplayable (missing coordinates,
        unanswerable challenges) and prevent starting; non-blocking ones
        (registrations, teams) are informational — teams may legitimately
        form after the hunt goes live.
        """
        from crush_lu.models.events import EventRegistration

        checks = []

        stations = list(self.stations.prefetch_related("challenges").order_by("order"))
        checks.append({
            "label": _("Stations"),
            "blocking": True,
            "ok": len(stations) > 0,
            "detail": str(len(stations)) if stations else _("No stations created"),
        })

        missing_coords = [
            s.name
            for s in stations
            if s.unlock_mode in ("gps", "gps_qr")
            and (s.latitude is None or s.longitude is None)
        ]
        checks.append({
            "label": _("GPS coordinates"),
            "blocking": True,
            "ok": len(missing_coords) == 0,
            "detail": (
                _("All GPS stations have coordinates")
                if not missing_coords
                else ", ".join(missing_coords[:3])
                + ("..." if len(missing_coords) > 3 else "")
            ),
        })

        without_challenges = [s.name for s in stations if not s.challenges.exists()]
        checks.append({
            "label": _("Challenges"),
            "blocking": True,
            "ok": len(stations) > 0 and len(without_challenges) == 0,
            "detail": (
                _("Every station has at least one challenge")
                if stations and not without_challenges
                else ", ".join(without_challenges[:3])
                + ("..." if len(without_challenges) > 3 else "")
                if without_challenges
                else _("No stations to check")
            ),
        })

        bad_mc = [
            c.question[:40]
            for s in stations
            for c in s.challenges.all()
            if c.challenge_type == "multiple_choice" and not c.correct_answer
        ]
        checks.append({
            "label": _("Correct answers"),
            "blocking": True,
            "ok": len(bad_mc) == 0,
            "detail": (
                _("All multiple-choice challenges have a correct answer")
                if not bad_mc
                else ", ".join(bad_mc[:3]) + ("..." if len(bad_mc) > 3 else "")
            ),
        })

        reg_count = EventRegistration.objects.filter(
            event=self.event, status__in=["confirmed", "attended"]
        ).count()
        checks.append({
            "label": _("Registrations"),
            "blocking": False,
            "ok": reg_count >= 2,
            "detail": (
                _("%(count)d confirmed/attended") % {"count": reg_count}
                if reg_count >= 2
                else _("%(count)d (need at least 2)") % {"count": reg_count}
            ),
        })

        team_count = self.teams.count()
        member_count = CacheTeamMember.objects.filter(hunt=self).count()
        checks.append({
            "label": _("Teams"),
            "blocking": False,
            "ok": team_count > 0 and member_count > 0,
            "detail": (
                _("%(teams)d teams, %(members)d members")
                % {"teams": team_count, "members": member_count}
                if team_count
                else _("No teams formed yet")
            ),
        })

        return checks


class CacheStation(models.Model):
    """One stop on the hunt: a place to reach and/or a QR code to scan."""

    UNLOCK_MODE_CHOICES = [
        ("none", _("None (challenges available immediately)")),
        ("gps", _("GPS (reach the location)")),
        ("qr", _("QR (scan the code on site)")),
        ("gps_qr", _("GPS + QR (reach the location, then scan)")),
    ]

    hunt = models.ForeignKey(
        CacheHunt, on_delete=models.CASCADE, related_name="stations"
    )
    order = models.PositiveIntegerField(
        help_text=_("Position in the hunt (1 = first station)")
    )
    name = models.CharField(max_length=200)
    intro_text = models.TextField(
        blank=True,
        help_text=_("Story/clue text shown when the station becomes the target"),
    )
    photo = models.ImageField(
        upload_to="cache_hunt/stations/",
        blank=True,
        null=True,
        storage=crush_photo_storage,
        help_text=_("Optional photo shown with the station intro"),
    )
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        blank=True,
        null=True,
        help_text=_("Station latitude (required for GPS unlock modes)"),
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        blank=True,
        null=True,
        help_text=_("Station longitude (required for GPS unlock modes)"),
    )
    radius_meters = models.PositiveIntegerField(
        default=25,
        help_text=_("How close (meters) a team must be to count as arrived"),
    )
    unlock_mode = models.CharField(
        max_length=10, choices=UNLOCK_MODE_CHOICES, default="gps"
    )
    # Reusable station identifier — many teams scan the same physical sticker.
    # Authorization comes from the scanning user's team state, not the token.
    qr_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    completion_message = models.TextField(
        blank=True,
        help_text=_("Message shown when the team completes this station"),
    )

    class Meta:
        ordering = ["order"]
        unique_together = [("hunt", "order")]
        verbose_name = _("Cache Station")
        verbose_name_plural = _("📍 2. Cache Stations")

    def __str__(self):
        return f"Station {self.order}: {self.name}"

    @property
    def requires_gps(self):
        return self.unlock_mode in ("gps", "gps_qr")

    @property
    def requires_qr(self):
        return self.unlock_mode in ("qr", "gps_qr")

    def total_points(self):
        return sum(c.points_awarded for c in self.challenges.all())


class CacheChallenge(models.Model):
    """A question/task answered at a station for points.

    Field vocabulary copied from JourneyChallenge / AdventDoorContent so
    coaches author the same way everywhere. A blank correct_answer means
    every submission is accepted (photo tasks, "tell us..." prompts).
    """

    CHALLENGE_TYPE_CHOICES = [
        ("riddle", _("Riddle")),
        ("multiple_choice", _("Multiple Choice")),
        ("open_text", _("Open Text")),
        ("word_scramble", _("Word Scramble")),
        ("photo_task", _("Photo Task")),
    ]

    station = models.ForeignKey(
        CacheStation, on_delete=models.CASCADE, related_name="challenges"
    )
    challenge_order = models.PositiveIntegerField(default=1)
    challenge_type = models.CharField(
        max_length=20, choices=CHALLENGE_TYPE_CHOICES, default="riddle"
    )
    question = models.TextField(
        help_text=_("The question/prompt/instructions for the challenge")
    )
    options = models.JSONField(
        default=dict,
        blank=True,
        help_text=_('JSON data for options/choices: {"A": "option1", "B": "option2"}'),
    )
    correct_answer = models.TextField(
        blank=True,
        help_text=_(
            "The correct answer. Leave blank to accept every submission "
            "(photo tasks, opinion prompts)."
        ),
    )
    alternative_answers = models.JSONField(
        default=list,
        blank=True,
        help_text=_('Alternative acceptable answers: ["answer1", "answer2"]'),
    )

    hint_1 = models.TextField(blank=True, help_text=_("First hint (easiest)"))
    hint_1_cost = models.IntegerField(
        default=20, help_text=_("Points deducted for using hint 1")
    )
    hint_2 = models.TextField(blank=True, help_text=_("Second hint (medium)"))
    hint_2_cost = models.IntegerField(
        default=50, help_text=_("Points deducted for using hint 2")
    )
    hint_3 = models.TextField(blank=True, help_text=_("Third hint (biggest reveal)"))
    hint_3_cost = models.IntegerField(
        default=80, help_text=_("Points deducted for using hint 3")
    )

    points_awarded = models.IntegerField(
        default=100, help_text=_("Points for correct answer (before hint deductions)")
    )
    success_message = models.TextField(
        blank=True, help_text=_("Message shown when the team answers correctly")
    )

    class Meta:
        ordering = ["challenge_order"]
        unique_together = [("station", "challenge_order")]
        verbose_name = _("Cache Challenge")
        verbose_name_plural = _("🧩 3. Cache Challenges")

    def __str__(self):
        return f"{self.station} - Challenge {self.challenge_order}"

    @property
    def accepts_any_answer(self):
        return not self.correct_answer

    def get_hint(self, number):
        return {1: self.hint_1, 2: self.hint_2, 3: self.hint_3}.get(number, "")

    def get_hint_cost(self, number):
        return {1: self.hint_1_cost, 2: self.hint_2_cost, 3: self.hint_3_cost}.get(
            number, 0
        )

    def check_answer(self, user_answer):
        """Check if the submitted answer is correct.

        Same semantics as AdventDoorContent.check_answer: a blank
        correct_answer accepts everything.
        """
        if not self.correct_answer:
            return True

        normalized_user = user_answer.strip().lower()
        normalized_correct = self.correct_answer.strip().lower()

        if normalized_user == normalized_correct:
            return True

        if self.alternative_answers:
            for alt in self.alternative_answers:
                if normalized_user == alt.strip().lower():
                    return True

        return False


class CacheTeam(models.Model):
    """A group of attendees hunting together with shared progress."""

    # Marker colors for the coach map / leaderboard
    COLOR_CHOICES = [
        ("#ef4444", _("Red")),
        ("#f97316", _("Orange")),
        ("#eab308", _("Yellow")),
        ("#22c55e", _("Green")),
        ("#06b6d4", _("Cyan")),
        ("#3b82f6", _("Blue")),
        ("#8b5cf6", _("Violet")),
        ("#ec4899", _("Pink")),
    ]

    hunt = models.ForeignKey(CacheHunt, on_delete=models.CASCADE, related_name="teams")
    name = models.CharField(max_length=100)
    join_code = models.CharField(
        max_length=JOIN_CODE_LENGTH,
        default=generate_join_code,
        help_text=_("Short code members use to join this team"),
    )
    color = models.CharField(max_length=7, choices=COLOR_CHOICES, default="#ef4444")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        unique_together = [("hunt", "join_code")]
        verbose_name = _("Cache Team")
        verbose_name_plural = _("👥 4. Cache Teams")

    def __str__(self):
        return f"{self.name} ({self.hunt.event})"

    def member_count(self):
        return self.members.count()


class CacheTeamMember(models.Model):
    """Membership of an event registration in a hunt team.

    FK to EventRegistration (not User) guarantees the player is registered
    for this event; the denormalized hunt FK makes "one team per hunt" a
    database constraint instead of app logic.
    """

    hunt = models.ForeignKey(
        CacheHunt, on_delete=models.CASCADE, related_name="memberships"
    )
    team = models.ForeignKey(
        CacheTeam, on_delete=models.CASCADE, related_name="members"
    )
    registration = models.ForeignKey(
        "crush_lu.EventRegistration",
        on_delete=models.CASCADE,
        related_name="cache_memberships",
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("hunt", "registration")]
        verbose_name = _("Cache Team Member")
        verbose_name_plural = _("👥 5. Cache Team Members")

    def __str__(self):
        return f"{self.registration.user} in {self.team.name}"

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.team_id and self.hunt_id and self.team.hunt_id != self.hunt_id:
            raise ValidationError(_("Team does not belong to this hunt."))
        if (
            self.registration_id
            and self.hunt_id
            and self.registration.event_id != self.hunt.event_id
        ):
            raise ValidationError(
                _("Registration does not belong to this hunt's event.")
            )


class CacheTeamProgress(models.Model):
    """Shared per-team hunt state — the row locked for state transitions."""

    team = models.OneToOneField(
        CacheTeam, on_delete=models.CASCADE, related_name="progress"
    )
    current_station = models.ForeignKey(
        CacheStation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    total_points = models.IntegerField(default=0)
    is_finished = models.BooleanField(default=False)
    finished_at = models.DateTimeField(null=True, blank=True)

    # Last reported position — feeds the coach map, never shown to other teams
    last_lat = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    last_lng = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    last_accuracy = models.FloatField(null=True, blank=True)
    last_position_at = models.DateTimeField(null=True, blank=True)

    started_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("Cache Team Progress")
        verbose_name_plural = _("📊 6. Cache Team Progress")

    def __str__(self):
        return f"Progress for {self.team.name}"


class CacheStationAttempt(models.Model):
    """Per-team state at one station. Unlock state derives from which
    timestamps are set versus the station's unlock_mode."""

    team = models.ForeignKey(
        CacheTeam, on_delete=models.CASCADE, related_name="station_attempts"
    )
    station = models.ForeignKey(
        CacheStation, on_delete=models.CASCADE, related_name="attempts"
    )
    arrived_at = models.DateTimeField(
        null=True, blank=True, help_text=_("GPS proximity satisfied")
    )
    scanned_at = models.DateTimeField(
        null=True, blank=True, help_text=_("QR code scanned")
    )
    completed_at = models.DateTimeField(
        null=True, blank=True, help_text=_("All challenges answered")
    )
    points_earned = models.IntegerField(default=0)
    started_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("team", "station")]
        ordering = ["station__order"]
        verbose_name = _("Cache Station Attempt")
        verbose_name_plural = _("🚩 7. Cache Station Attempts")

    def __str__(self):
        return f"{self.team.name} @ {self.station}"

    @property
    def is_unlocked(self):
        """Challenges become available once every unlock requirement is met."""
        if self.station.requires_gps and self.arrived_at is None:
            return False
        if self.station.requires_qr and self.scanned_at is None:
            return False
        return True


class CacheChallengeAttempt(models.Model):
    """Per-team answer state for one challenge — the idempotency anchor."""

    station_attempt = models.ForeignKey(
        CacheStationAttempt,
        on_delete=models.CASCADE,
        related_name="challenge_attempts",
    )
    challenge = models.ForeignKey(
        CacheChallenge, on_delete=models.CASCADE, related_name="attempts"
    )
    last_answer = models.TextField(blank=True)
    is_correct = models.BooleanField(default=False)
    attempts_count = models.PositiveIntegerField(default=0)
    hints_used = models.JSONField(
        default=list, blank=True, help_text=_("Hint numbers revealed, e.g. [1, 2]")
    )
    points_earned = models.IntegerField(default=0)
    answered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    photo = models.ImageField(
        upload_to="cache_hunt/attempts/",
        blank=True,
        null=True,
        storage=crush_photo_storage,
        help_text=_("Submitted photo for photo tasks"),
    )
    answered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("station_attempt", "challenge")]
        verbose_name = _("Cache Challenge Attempt")
        verbose_name_plural = _("✏️ 8. Cache Challenge Attempts")

    def __str__(self):
        return f"{self.station_attempt.team.name} - {self.challenge}"

    def hint_cost_total(self):
        return sum(self.challenge.get_hint_cost(n) for n in self.hints_used or [])
