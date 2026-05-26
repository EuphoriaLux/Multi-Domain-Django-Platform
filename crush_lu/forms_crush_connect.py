"""
Crush Connect forms.

Kept in a dedicated module so onboarding/spark forms don't bloat the main
``crush_lu/forms.py`` (1k+ lines already).
"""
from django import forms
from django.utils.translation import gettext_lazy as _

from crush_lu.models import CrushConnectMembership, SparkPrompt


class CrushConnectOnboardingForm(forms.ModelForm):
    """
    Single-page Crush Connect onboarding.

    The user picks one coach-authored Story prompt and writes their one-line
    answer (≤200 chars). On save, ``onboarded_at`` is stamped by the view —
    this form only owns the content fields.
    """

    story_prompt = forms.ModelChoiceField(
        queryset=SparkPrompt.objects.filter(is_active=True),
        label=_("Pick a prompt"),
        widget=forms.RadioSelect,
        empty_label=None,
    )
    story_answer = forms.CharField(
        max_length=200,
        label=_("Your one-line answer"),
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "maxlength": 200,
                "placeholder": _(
                    "Keep it human — one sentence the right kind of person will recognise."
                ),
                "class": "input-crush w-full",
            }
        ),
    )
    confirm_terms = forms.BooleanField(
        required=True,
        label=_(
            "I understand my Story and first name appear on a blurred-photo card, "
            "and I can be removed from Crush Connect at any time."
        ),
    )

    class Meta:
        model = CrushConnectMembership
        fields = ("story_prompt", "story_answer")

    def clean_story_answer(self):
        value = (self.cleaned_data.get("story_answer") or "").strip()
        if len(value) < 10:
            raise forms.ValidationError(
                _("Give us at least a full sentence — under 10 characters won't tell anyone much.")
            )
        return value
