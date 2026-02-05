from django.db import models
from django.utils.translation import gettext_lazy as _


class CrushSiteConfig(models.Model):
    """
    Singleton model for site-wide Crush.lu configuration.
    Only one row (pk=1) is allowed.
    """

    whatsapp_number = models.CharField(
        max_length=20,
        blank=True,
        help_text=_("Format: 352621XXXXXX (no + or spaces)"),
        verbose_name=_("WhatsApp number"),
    )
    whatsapp_enabled = models.BooleanField(
        default=True,
        verbose_name=_("WhatsApp button enabled"),
    )
    whatsapp_default_message = models.CharField(
        max_length=200,
        blank=True,
        default="Hi! I have a question about Crush.lu.",
        verbose_name=_("Default WhatsApp message"),
        help_text=_("Pre-filled message when user clicks the button"),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Site Configuration")
        verbose_name_plural = _("Site Configuration")

    def __str__(self):
        return "Crush.lu Site Configuration"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_config(cls):
        config, _ = cls.objects.get_or_create(pk=1)
        return config
