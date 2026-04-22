"""
Changelog / patch notes models for crush.lu.

Powers the public-facing /changelog/ page and is fed by the
`generate_patch_notes` management command, which reads the Git
history and produces human-friendly marketing copy.

Editorial flow:
1. Command drafts PatchRelease + PatchNote rows with is_published=False.
2. Editor polishes copy in Django admin and toggles is_published.
3. Public page shows only published releases, newest first.
"""

from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _


class PatchNoteCategory(models.TextChoices):
    FEATURE = "feature", _("New Features")
    IMPROVEMENT = "improvement", _("Improvements")
    FIX = "fix", _("Fixes")
    UNDER_HOOD = "under_hood", _("Under the Hood")


CATEGORY_EMOJI = {
    PatchNoteCategory.FEATURE: "\u2728",
    PatchNoteCategory.IMPROVEMENT: "\U0001F680",
    PatchNoteCategory.FIX: "\U0001F41B",
    PatchNoteCategory.UNDER_HOOD: "\U0001F512",
}


class PatchRelease(models.Model):
    """A single release on the changelog timeline."""

    version = models.CharField(
        max_length=20,
        help_text=_("Semantic version string, e.g. 'v1.1'."),
    )
    slug = models.SlugField(
        max_length=80,
        unique=True,
        help_text=_("URL slug, e.g. 'v1-1-quiz-night'."),
    )
    title = models.CharField(
        max_length=140,
        help_text=_("Headline for the release, e.g. 'Quiz Night goes live'."),
    )
    hero_summary = models.CharField(
        max_length=280,
        blank=True,
        help_text=_("One-line lede shown at the top of the release card."),
    )
    released_on = models.DateField(
        help_text=_("Public release date."),
    )
    is_published = models.BooleanField(
        default=False,
        db_index=True,
        help_text=_("Toggle to show on the public /changelog/ page."),
    )
    commit_range_start = models.CharField(max_length=40, blank=True)
    commit_range_end = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-released_on", "-version"]
        verbose_name = _("Patch Release")
        verbose_name_plural = _("Patch Releases")
        indexes = [
            # Hot path: public list filters by is_published and orders by -released_on.
            models.Index(
                fields=["is_published", "-released_on"],
                name="patchrel_pub_date_idx",
            ),
        ]

    def __str__(self):
        return f"{self.version} \u2014 {self.title}"

    def get_absolute_url(self):
        return reverse("crush_lu:changelog_detail", args=[self.slug])


class PatchNote(models.Model):
    """One line item inside a release (feature, fix, improvement, …)."""

    release = models.ForeignKey(
        PatchRelease,
        related_name="notes",
        on_delete=models.CASCADE,
    )
    category = models.CharField(
        max_length=20,
        choices=PatchNoteCategory.choices,
        db_index=True,
    )
    title = models.CharField(
        max_length=160,
        help_text=_("Short, warm, user-facing headline."),
    )
    body = models.TextField(
        blank=True,
        help_text=_("Longer plain-text description. Newlines are preserved."),
    )
    related_commits = models.JSONField(
        default=list,
        blank=True,
        help_text=_("List of commit SHAs that back this note."),
    )
    order = models.PositiveIntegerField(default=0)
    # Cleared when a curator edits the row in admin so the generator never
    # overwrites hand-polished copy on a re-run.
    auto_generated = models.BooleanField(
        default=True,
        db_index=True,
        help_text=_(
            "Internal: True for notes produced by generate_patch_notes, "
            "False once a human has edited them. The generator only "
            "replaces rows where this is True."
        ),
    )

    class Meta:
        ordering = ["release", "category", "order", "id"]
        verbose_name = _("Patch Note")
        verbose_name_plural = _("Patch Notes")

    def __str__(self):
        return f"[{self.get_category_display()}] {self.title}"

    @property
    def category_emoji(self):
        return CATEGORY_EMOJI.get(self.category, "")
