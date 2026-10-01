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
