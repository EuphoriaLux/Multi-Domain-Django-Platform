"""
Matching system models for Crush.lu.

Trait: Reference data for personality qualities and defects (20 each).
MatchScore: Cached compatibility scores between user pairs.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class TraitCategory(models.TextChoices):
    SOCIAL = "social", _("Social")
    EMOTIONAL = "emotional", _("Emotional")
    MINDSET = "mindset", _("Mindset")
    RELATIONAL = "relational", _("Relational")
    ENERGY = "energy", _("Energy")


class TraitType(models.TextChoices):
    QUALITY = "quality", _("Quality")
    DEFECT = "defect", _("Defect")


class Trait(models.Model):
    """A personality trait (quality or defect) used in the matching system."""

    slug = models.SlugField(
        max_length=30,
        unique=True,
        help_text=_("Unique identifier, e.g. 'patient' or 'stubborn'"),
    )
    label = models.CharField(
        max_length=50,
        help_text=_("Display label (translated via modeltranslation)"),
    )
    trait_type = models.CharField(
        max_length=10,
        choices=TraitType.choices,
        db_index=True,
    )
    category = models.CharField(
        max_length=20,
        choices=TraitCategory.choices,
        blank=True,
        default="",
    )
    opposite = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="opposite_of",
        help_text=_("Opposite trait (quality ↔ defect pair)"),
    )
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["trait_type", "sort_order"]
        verbose_name = _("Trait")
        verbose_name_plural = _("Traits")

    def __str__(self):
        return f"{self.label} ({self.get_trait_type_display()})"


class MatchScore(models.Model):
    """Cached compatibility score between two users.

    Convention: user_a.pk < user_b.pk to avoid duplicate pairs.
    """

    user_a = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="match_scores_as_a",
    )
    user_b = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="match_scores_as_b",
    )
    score_qualities = models.FloatField(default=0)
    score_zodiac_west = models.FloatField(default=0)
    score_zodiac_cn = models.FloatField(default=0)
    score_language = models.FloatField(default=0)
    score_age_fit = models.FloatField(default=0)
    score_final = models.FloatField(default=0, db_index=True)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("user_a", "user_b")]
        ordering = ["-score_final"]
        indexes = [
            models.Index(fields=["user_a", "score_final"]),
            models.Index(fields=["user_b", "score_final"]),
        ]
        verbose_name = _("Match Score")
        verbose_name_plural = _("Match Scores")

    def __str__(self):
        return f"{self.user_a} ↔ {self.user_b}: {self.score_final:.0%}"
