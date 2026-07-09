from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

__all__ = ["EmpireState"]


class EmpireState(models.Model):
    """
    One row per player: the whole save.

    Balances here are authoritative. The client simulates locally for a smooth
    counter, but never submits `points` — every mutation goes through a server
    endpoint that prices it from this row. The economy tables (costs, rates,
    unlock thresholds) live in code, not here, so they can be rebalanced without
    a data migration.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="empire_state",
    )

    # Currencies
    points = models.BigIntegerField(_("crushes"), default=0)
    total_earned = models.BigIntegerField(_("total crushes earned"), default=0)
    hearts = models.IntegerField(_("hearts"), default=0)  # prestige
    flags = models.IntegerField(_("red flags"), default=0)  # scam-detection currency

    # Progress. Keyed by the string form of the tier index, because JSON object
    # keys are always strings — reading these back as ints is a bug magnet.
    generators = models.JSONField(default=dict, blank=True)  # {"0": 12, "1": 3}
    upgrades = models.JSONField(default=list, blank=True)  # [0, 1, 3]
    safety_upgrades = models.JSONField(default=list, blank=True)  # bought with flags

    # Scam layer
    streak = models.IntegerField(default=0)
    best_streak = models.IntegerField(default=0)
    debuff_until = models.DateTimeField(null=True, blank=True)
    scam_savvy_earned_at = models.DateTimeField(null=True, blank=True)

    # Meta. `last_tick` is the server's idle-accrual clock — offline earnings are
    # always computed from it, never from a client-reported elapsed time.
    last_tick = models.DateTimeField(default=timezone.now)
    schema_version = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Empire save")
        verbose_name_plural = _("Empire saves")
        ordering = ["-total_earned"]
        indexes = [models.Index(fields=["-total_earned"], name="empire_total_earned")]

    def __str__(self):
        return f"{self.user} — {self.total_earned} 💘"

    @property
    def is_debuffed(self):
        return self.debuff_until is not None and self.debuff_until > timezone.now()

    @property
    def has_scam_savvy(self):
        return self.scam_savvy_earned_at is not None

    def generator_count(self, tier):
        """Owned count for a generator tier. JSON keys are strings; normalise here."""
        return int(self.generators.get(str(tier), 0))
