from django import forms
from django.utils.translation import gettext_lazy as _

from .models.crush_spark import CrushSpark


class SparkRequestForm(forms.ModelForm):
    """Form for requesting a Crush Spark (describing the person you liked)."""

    class Meta:
        model = CrushSpark
        fields = ["sender_description"]
        widgets = {
            "sender_description": forms.Textarea(
                attrs={
                    "rows": 4,
                    "maxlength": 1000,
                    "placeholder": _(
                        "Describe the person you liked (e.g. 'person in red dress "
                        "who talked about hiking, sat next to me during dinner')"
                    ),
                }
            ),
        }
        labels = {
            "sender_description": _("Who caught your eye?"),
        }


class SparkJourneyForm(forms.ModelForm):
    """Form for creating the journey content (media + message)."""

    class Meta:
        model = CrushSpark
        fields = [
            "sender_message",
            "chapter1_image",
            "chapter3_image_1",
            "chapter3_image_2",
            "chapter3_image_3",
            "chapter3_image_4",
            "chapter3_image_5",
            "chapter4_video",
            "chapter5_letter_music",
        ]
        widgets = {
            "sender_message": forms.Textarea(
                attrs={
                    "rows": 4,
                    "maxlength": 2000,
                    "placeholder": _(
                        "Write a personal message that will be revealed in Chapter 6..."
                    ),
                }
            ),
        }
        labels = {
            "sender_message": _("Your personal message (revealed with your identity)"),
            "chapter1_image": _("Photo Puzzle (Chapter 1)"),
            "chapter3_image_1": _("Slideshow Photo 1 (Chapter 3)"),
            "chapter3_image_2": _("Slideshow Photo 2"),
            "chapter3_image_3": _("Slideshow Photo 3"),
            "chapter3_image_4": _("Slideshow Photo 4"),
            "chapter3_image_5": _("Slideshow Photo 5"),
            "chapter4_video": _("Video Message (Chapter 4)"),
            "chapter5_letter_music": _("Background Music (Chapter 5)"),
        }


class CoachSparkAssignForm(forms.Form):
    """Form for coach to assign a recipient to a spark."""

    recipient_user_id = forms.IntegerField(widget=forms.HiddenInput())
    coach_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 2,
                "placeholder": _("Optional notes about this assignment..."),
            }
        ),
        label=_("Coach Notes"),
    )
