import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

__all__ = ["CardChallenge", "ACTION_CHOICES", "OUTCOME_CHOICES"]

ACTION_LIKE = "like"
ACTION_NOPE = "nope"
ACTION_REPORT = "report"

ACTION_CHOICES = [
    (ACTION_LIKE, _("Like")),
    (ACTION_NOPE, _("Nope")),
    (ACTION_REPORT, _("Report")),
]

OUTCOME_CHOICES = [
    ("correct", _("Correct")),
    ("missed", _("Missed a scam")),
    ("catfished", _("Fell for a scam")),
    ("false_report", _("Reported a genuine profile")),
    ("neutral", _("Neutral")),
]

# A dealt card is good for this long. Long enough that a player can walk away
# mid-swipe; short enough that a stockpile of open challenges is useless.
CHALLENGE_TTL_SECONDS = 60 * 60


class CardChallenge(models.Model):
    """
    One dealt card, and the server's memory of what the answer was.

    The client is handed `id` and the card's display fields. It is *not* handed
    `profile.is_scam`, nor which segments are red flags. Those are revealed only
    in the response to resolve(), once the action is already recorded — so there
    is no request in which knowing the answer helps.

    The uuid primary key matters: a sequential id would let a player enumerate
    other people's challenges, and, worse, correlate their own draws.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="empire_challenges"
    )
    profile = models.ForeignKey("crush_empire.GameProfile", on_delete=models.PROTECT)

    issued_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    # Null until answered. Non-null means spent: a challenge resolves exactly once.
    resolved_at = models.DateTimeField(null=True, blank=True)
    action = models.CharField(max_length=16, choices=ACTION_CHOICES, blank=True)
    outcome = models.CharField(max_length=16, choices=OUTCOME_CHOICES, blank=True)

    reward_points = models.IntegerField(default=0)
    reward_flags = models.IntegerField(default=0)
    streak_after = models.IntegerField(default=0)

    class Meta:
        verbose_name = _("Card challenge")
        verbose_name_plural = _("Card challenges")
        ordering = ["-issued_at"]
        indexes = [
            # The hot query: "does this player have an open card?"
            models.Index(fields=["user", "resolved_at"], name="empire_open_challenge"),
        ]

    def __str__(self):
        return f"{self.user_id} · {self.profile.display_name} · {self.outcome or 'open'}"

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = timezone.now() + timezone.timedelta(
                seconds=CHALLENGE_TTL_SECONDS
            )
        super().save(*args, **kwargs)

    @property
    def is_open(self):
        return self.resolved_at is None and self.expires_at > timezone.now()
