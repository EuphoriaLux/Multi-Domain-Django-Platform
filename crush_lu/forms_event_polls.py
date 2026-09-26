from django import forms
from django.utils.translation import gettext_lazy as _


class EventPollSuggestionForm(forms.Form):
    """A voter's idea for a new poll option; ``website`` is a honeypot."""

    text = forms.CharField(
        label=_("Your idea"),
        max_length=200,
        widget=forms.TextInput(
            attrs={
                "class": "input-crush",
                "placeholder": _("e.g. Salsa night, Escape room date…"),
            }
        ),
    )
    website = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}),
    )

    def clean_text(self):
        return " ".join(self.cleaned_data["text"].split())

    def clean_website(self):
        if self.cleaned_data.get("website"):
            raise forms.ValidationError("honeypot")
        return ""
