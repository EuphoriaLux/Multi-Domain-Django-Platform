from django.db import models
from django.utils.translation import gettext_lazy as _

__all__ = ["GameProfile", "BioSegment", "FLAG_TYPE_CHOICES"]


# Every one of these is a documented romance-fraud pattern, not a joke we made
# up. The comedy is in the phrasing; the tells underneath are real.
FLAG_TYPE_CHOICES = [
    ("unverifiable_job", _("High-status job that can't be checked")),
    ("never_video_calls", _("Always has a reason not to video call")),
    ("off_platform", _("Wants to move to WhatsApp/Telegram immediately")),
    ("money_request", _("Asks for money, gift cards or crypto")),
    ("love_bombing", _("Declares deep feelings far too fast")),
    ("urgency", _("Manufactured emergency, needs an answer now")),
    ("crypto", _("Unsolicited investment advice")),
    ("inconsistency", _("Story contradicts itself")),
]


class GameProfile(models.Model):
    """
    One card in the deck. Emoji avatars, not photographs — no likeness rights,
    no GDPR exposure, and no dating game built out of real faces.

    `is_scam` is the answer key. It must never be serialised to the client
    before that client has committed to an action; see services/deck.py.
    Cards must never be modelled on actually-reported Crush.lu members.
    """

    emoji = models.CharField(max_length=8)
    display_name = models.CharField(max_length=40)
    age = models.PositiveSmallIntegerField()

    is_scam = models.BooleanField(
        default=False,
        help_text=_("The answer key. Never sent to the player before they answer."),
    )
    is_active = models.BooleanField(default=True)
    weight = models.PositiveSmallIntegerField(
        default=1, help_text=_("Relative draw frequency within its kind.")
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Empire profile")
        verbose_name_plural = _("Empire profiles")
        ordering = ["is_scam", "display_name"]
        indexes = [models.Index(fields=["is_scam", "is_active"], name="empire_kind_active")]

    def __str__(self):
        return f"{'🚩' if self.is_scam else '💚'} {self.display_name}, {self.age}"

    def bio_text(self):
        return " ".join(s.text for s in self.segments.all())


class BioSegment(models.Model):
    """
    A bio is a *sequence of segments*, not a string with highlighted ranges.

    Character offsets do not survive translation — "oil rig engineer" and
    "Ingenieur auf einer Bohrinsel" don't share a span. So a red flag is a
    boolean on a row, and each language translates that row independently. The
    tier-2 modal makes each segment individually tappable.
    """

    profile = models.ForeignKey(
        GameProfile, on_delete=models.CASCADE, related_name="segments"
    )
    order = models.PositiveSmallIntegerField(default=0)

    text = models.CharField(max_length=200)
    is_red_flag = models.BooleanField(default=False)
    flag_type = models.CharField(
        max_length=32, choices=FLAG_TYPE_CHOICES, blank=True
    )
    explanation = models.CharField(
        max_length=300,
        blank=True,
        help_text=_("Shown after the player answers. This is the teaching."),
    )

    class Meta:
        verbose_name = _("Bio segment")
        verbose_name_plural = _("Bio segments")
        ordering = ["profile", "order"]

    def __str__(self):
        return f"{'🚩 ' if self.is_red_flag else ''}{self.text[:40]}"
