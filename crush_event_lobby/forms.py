from django import forms
from django.utils.translation import gettext_lazy as _

from .models import ConfirmedEncounterRemovalRequest


class EncounterRemovalRequestForm(forms.Form):
    reason = forms.ChoiceField(
        choices=ConfirmedEncounterRemovalRequest.REASON_CHOICES,
        label=_("Why would you like this person removed?"),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    details = forms.CharField(
        required=False,
        max_length=500,
        label=_("Private details (optional)"),
        help_text=_("Only your Crush Coach or authorized Support staff can see this."),
        widget=forms.Textarea(
            attrs={
                "class": "input-crush",
                "rows": 4,
                "placeholder": _("Add only the details needed for review."),
            }
        ),
    )


class EncounterRemovalReviewForm(forms.Form):
    DECISION_CHOICES = [
        ("approve", _("Approve permanent removal")),
        ("keep_hidden", _("Resolve and keep hidden")),
        ("restore", _("Explicitly restore visibility")),
    ]
    decision = forms.ChoiceField(
        choices=DECISION_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    resolution_notes = forms.CharField(
        max_length=1000,
        label=_("Private resolution notes"),
        widget=forms.Textarea(attrs={"class": "input-crush", "rows": 3}),
    )
