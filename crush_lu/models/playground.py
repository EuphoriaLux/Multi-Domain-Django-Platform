"""Coach playground accounts (staging only).

Spec: ai-memory-hub/specs/2026-11-13-crush-coach-playground.md

One row per account the playground knows about:

* ``coach``: a staging coach account that signed in with crush.lu. The row
  links it to the production user id, so a renamed email still finds it.
* ``practice``: the practice member a coach plays on test.crush.lu. ``owner``
  is the coach's user. Only the owner may switch into it; the foreign key, not
  a username pattern, decides that.
* ``cast``: a synthetic member that answers when a coach presses a button.

The table stays empty on production: nothing outside the staging gate writes
to it.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q


class PlaygroundAccount(models.Model):
    KIND_COACH = "coach"
    KIND_PRACTICE = "practice"
    KIND_CAST = "cast"
    KIND_CHOICES = [
        (KIND_COACH, "Coach signed in with crush.lu"),
        (KIND_PRACTICE, "Practice member"),
        (KIND_CAST, "Cast member"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="playground_account",
    )
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="practice_accounts",
        help_text="The coach who plays this practice member.",
    )
    production_account_id = models.PositiveBigIntegerField(
        null=True,
        blank=True,
        unique=True,
        help_text="The crush.lu (production) user id of a signed-in coach.",
    )
    persona_key = models.CharField(max_length=40, blank=True)
    preset = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    retired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Playground account"
        verbose_name_plural = "Playground accounts"
        constraints = [
            models.UniqueConstraint(
                fields=["owner"],
                condition=Q(kind="practice", retired_at__isnull=True),
                name="playground_one_live_practice_per_owner",
            ),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} #{self.user_id}"


class PlaygroundMail(models.Model):
    """An email a practice or cast member would have received (staging only).

    ``send_domain_email`` diverts mail for playground accounts here instead of
    sending it, so a coach can read what a member gets. Nothing leaves the
    server. Kept short: the newest 50 per member, at most 14 days.
    """

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="playground_mails",
    )
    to_address = models.CharField(max_length=254)
    from_address = models.CharField(max_length=254, blank=True)
    subject = models.CharField(max_length=255, blank=True)
    body_text = models.TextField(blank=True)
    body_html = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "Playground mail"
        verbose_name_plural = "Playground mails"

    def __str__(self):
        return f"{self.subject} → #{self.recipient_id}"
