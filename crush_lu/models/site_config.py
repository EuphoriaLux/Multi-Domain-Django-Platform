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

    # Social media links
    social_instagram_url = models.URLField(
        blank=True,
        verbose_name=_("Instagram URL"),
        help_text=_("e.g. https://www.instagram.com/crush.lu"),
    )
    social_facebook_url = models.URLField(
        blank=True,
        verbose_name=_("Facebook URL"),
        help_text=_("e.g. https://www.facebook.com/crush.lu"),
    )
    social_linkedin_url = models.URLField(
        blank=True,
        verbose_name=_("LinkedIn URL"),
        help_text=_("e.g. https://www.linkedin.com/company/crush-lu"),
    )
    social_google_business_url = models.URLField(
        blank=True,
        verbose_name=_("Google Business URL"),
        help_text=_("Your Google Business Profile URL"),
    )
    social_reddit_url = models.URLField(
        blank=True,
        verbose_name=_("Reddit URL"),
        help_text=_("e.g. https://www.reddit.com/r/crushlu"),
    )

    # SMS templates for coach outreach
    sms_template_en = models.CharField(
        max_length=320,
        blank=True,
        default="Hi {first_name}, this is {coach_name} from Crush.lu. I tried calling you regarding your profile verification. Could you call me back when you have a moment? Thanks!",
        verbose_name=_("SMS template (English)"),
        help_text=_("Placeholders: {first_name}, {coach_name}"),
    )
    sms_template_de = models.CharField(
        max_length=320,
        blank=True,
        default="Hallo {first_name}, hier ist {coach_name} von Crush.lu. Ich habe versucht, dich wegen deiner Profilverifizierung anzurufen. Könntest du mich zurückrufen? Danke!",
        verbose_name=_("SMS template (German)"),
        help_text=_("Placeholders: {first_name}, {coach_name}"),
    )
    sms_template_fr = models.CharField(
        max_length=320,
        blank=True,
        default="Bonjour {first_name}, c'est {coach_name} de Crush.lu. J'ai essayé de vous appeler concernant la vérification de votre profil. Pourriez-vous me rappeler ? Merci !",
        verbose_name=_("SMS template (French)"),
        help_text=_("Placeholders: {first_name}, {coach_name}"),
    )

    # SMS templates for event invites (coach outreach to unverified profiles)
    sms_event_invite_template_en = models.CharField(
        max_length=320,
        blank=True,
        default="Hi {first_name}! {coach_name} from Crush.lu here. We have an event for you: {event_title} on {event_date}. Sign up & get verified on the spot! {event_url}",
        verbose_name=_("Event invite SMS template (English)"),
        help_text=_("Placeholders: {first_name}, {coach_name}, {event_title}, {event_date}, {event_url}"),
    )
    sms_event_invite_template_de = models.CharField(
        max_length=320,
        blank=True,
        default="Hi {first_name}! Hier ist {coach_name} von Crush.lu. Wir haben ein Event fuer dich: {event_title} am {event_date}. Melde dich an und werde vor Ort verifiziert! {event_url}",
        verbose_name=_("Event invite SMS template (German)"),
        help_text=_("Placeholders: {first_name}, {coach_name}, {event_title}, {event_date}, {event_url}"),
    )
    sms_event_invite_template_fr = models.CharField(
        max_length=320,
        blank=True,
        default="Salut {first_name} ! C'est {coach_name} de Crush.lu. On a un evenement pour toi : {event_title} le {event_date}. Inscris-toi et fais-toi verifier sur place ! {event_url}",
        verbose_name=_("Event invite SMS template (French)"),
        help_text=_("Placeholders: {first_name}, {coach_name}, {event_title}, {event_date}, {event_url}"),
    )

    # SMS templates for pre-screening reminders (coach -> candidate, while profile pending)
    pre_screening_reminder_sms_en = models.CharField(
        max_length=320,
        blank=True,
        default="Hi {first_name}, {coach_name} from Crush.lu here. Before we talk, please answer a few quick questions at {link}. It takes 3 minutes and helps me help you. Thanks!",
        verbose_name=_("Pre-screening reminder SMS (English)"),
        help_text=_("Placeholders: {first_name}, {coach_name}, {link}"),
    )
    pre_screening_reminder_sms_de = models.CharField(
        max_length=320,
        blank=True,
        default="Hallo {first_name}, hier ist {coach_name} von Crush.lu. Vor unserem Gespraech beantworte bitte ein paar kurze Fragen unter {link}. Es dauert 3 Minuten und hilft mir, dir zu helfen. Danke!",
        verbose_name=_("Pre-screening reminder SMS (German)"),
        help_text=_("Placeholders: {first_name}, {coach_name}, {link}"),
    )
    pre_screening_reminder_sms_fr = models.CharField(
        max_length=320,
        blank=True,
        default="Bonjour {first_name}, c'est {coach_name} de Crush.lu. Avant notre appel, merci de repondre a quelques questions rapides sur {link}. 3 minutes et ca m'aide a t'aider. Merci !",
        verbose_name=_("Pre-screening reminder SMS (French)"),
        help_text=_("Placeholders: {first_name}, {coach_name}, {link}"),
    )

    # Status banner
    banner_enabled = models.BooleanField(
        default=False,
        verbose_name=_("Banner enabled"),
    )
    banner_message = models.TextField(
        blank=True,
        verbose_name=_("Banner message"),
        help_text=_("The message to display in the banner."),
    )
    banner_link_text = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("Banner link text"),
        help_text=_("Optional CTA button text (leave blank for no link)."),
    )
    banner_link_url = models.CharField(
        max_length=500,
        blank=True,
        verbose_name=_("Banner link URL"),
        help_text=_("Internal path (e.g. /events/) or full URL."),
    )
    BANNER_STYLE_CHOICES = [
        ('info', _('Info (blue)')),
        ('warning', _('Warning (amber)')),
        ('success', _('Success (green)')),
        ('purple', _('Purple')),
    ]
    banner_style = models.CharField(
        max_length=10,
        choices=BANNER_STYLE_CHOICES,
        default='info',
        verbose_name=_("Banner style"),
    )
    banner_target_statuses = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_("Target profile statuses"),
        help_text=_(
            "Show banner only to users with these submission statuses. "
            "Empty = show to ALL authenticated users."
        ),
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
