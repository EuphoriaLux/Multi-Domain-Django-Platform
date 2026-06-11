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
    Four-step Crush Connect onboarding.

    Steps:
      1. Relationship intent (relationship_goal)
      2. Lifestyle signals (lifestyle_energy/social/pace)
      3. Ideal match preferences (CrushProfile fields, handled in view)
      4. Story prompt + answer + optional second story + confirm_terms

    On save, ``onboarded_at`` is stamped by the view — this form only owns
    the CrushConnectMembership content fields.
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
    story_prompt_2 = forms.ModelChoiceField(
        queryset=SparkPrompt.objects.filter(is_active=True),
        required=False,
        label=_("Add a second prompt (optional)"),
        widget=forms.RadioSelect,
        empty_label=None,
    )
    story_answer_2 = forms.CharField(
        max_length=200,
        required=False,
        label=_("Your second answer"),
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "maxlength": 200,
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
        fields = (
            "relationship_goal",
            "lifestyle_energy",
            "lifestyle_social",
            "lifestyle_pace",
            "story_prompt",
            "story_answer",
            "story_prompt_2",
            "story_answer_2",
        )

    def clean_story_answer(self):
        value = (self.cleaned_data.get("story_answer") or "").strip()
        if len(value) < 10:
            raise forms.ValidationError(
                _("Give us at least a full sentence — under 10 characters won't tell anyone much.")
            )
        return value

    def clean_story_answer_2(self):
        return (self.cleaned_data.get("story_answer_2") or "").strip()

    def clean(self):
        cleaned = super().clean()
        prompt_2 = cleaned.get("story_prompt_2")
        answer_2 = cleaned.get("story_answer_2", "")
        if bool(prompt_2) != bool(answer_2):
            if prompt_2 and not answer_2:
                self.add_error("story_answer_2", _("Please write an answer for your second prompt."))
            elif answer_2 and not prompt_2:
                self.add_error("story_prompt_2", _("Please select a prompt for your second answer."))
        return cleaned
