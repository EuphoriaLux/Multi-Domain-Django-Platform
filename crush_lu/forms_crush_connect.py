"""
Crush Connect forms.

The opt-in onboarding is a 7-step server-side wizard; each step is its own
``ModelForm`` on ``CrushConnectMembership`` so a step can ``form.save()`` (M2M
included) and persist immediately. The same step forms back the post-onboarding
profile-edit page (one section per step).

Kept in a dedicated module so onboarding/spark forms don't bloat the main
``crush_lu/forms.py`` (1k+ lines already).
"""
from django import forms
from django.utils.translation import gettext_lazy as _

from crush_lu.models import (
    CONNECT_LANGUAGE_CHOICES,
    ConnectInterest,
    CrushConnectMembership,
    CrushProfile,
    SparkPrompt,
)

# Interests: at least one, at most this many (the "n/8" counter cap).
MAX_INTERESTS = 8
MIN_INTERESTS = 1


def _require(form, *field_names):
    """Mark the given (model-derived, blank=True) fields as required at the
    form level — the wizard enforces requiredness the model intentionally
    leaves optional so migrated members stay valid."""
    for name in field_names:
        if name in form.fields:
            form.fields[name].required = True


# ---------------------------------------------------------------------------
# Step 1 — Intention
# ---------------------------------------------------------------------------
class ConnectIntentionForm(forms.ModelForm):
    class Meta:
        model = CrushConnectMembership
        fields = ("relationship_goal",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _require(self, "relationship_goal")


# ---------------------------------------------------------------------------
# Step 2 — Lifestyle
# ---------------------------------------------------------------------------
class ConnectLifestyleForm(forms.ModelForm):
    class Meta:
        model = CrushConnectMembership
        fields = ("lifestyle_energy", "lifestyle_social", "lifestyle_pace")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _require(self, "lifestyle_energy", "lifestyle_social", "lifestyle_pace")


# ---------------------------------------------------------------------------
# Step 3 — Languages & interests
# ---------------------------------------------------------------------------
class ConnectLanguagesForm(forms.ModelForm):
    languages = forms.MultipleChoiceField(
        choices=CONNECT_LANGUAGE_CHOICES,
        required=True,
        widget=forms.CheckboxSelectMultiple,
        label=_("Languages you speak"),
        error_messages={"required": _("Pick at least one language you speak.")},
    )
    interests = forms.ModelMultipleChoiceField(
        queryset=ConnectInterest.objects.filter(is_active=True),
        required=True,
        widget=forms.CheckboxSelectMultiple,
        label=_("Interests & hobbies"),
    )

    class Meta:
        model = CrushConnectMembership
        fields = ("languages", "interests")

    def clean_interests(self):
        interests = self.cleaned_data.get("interests")
        count = len(interests) if interests is not None else 0
        if count < MIN_INTERESTS:
            raise forms.ValidationError(_("Pick at least one interest."))
        if count > MAX_INTERESTS:
            raise forms.ValidationError(
                _("Pick at most %(max)d interests.") % {"max": MAX_INTERESTS}
            )
        return interests


# ---------------------------------------------------------------------------
# Step 4 — Life basics
# ---------------------------------------------------------------------------
class ConnectLifeForm(forms.ModelForm):
    class Meta:
        model = CrushConnectMembership
        fields = (
            "height_cm",
            "work_field",
            "education_level",
            "smoking",
            "drinking",
        )
        widgets = {
            "height_cm": forms.NumberInput(
                attrs={"min": 120, "max": 230, "class": "input-crush w-full"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # height stays optional; the rest are required (prefer_not_say is a
        # valid, explicit answer — see the model choices).
        _require(self, "work_field", "education_level", "smoking", "drinking")


# ---------------------------------------------------------------------------
# Step 5 — Family & future
# ---------------------------------------------------------------------------
class ConnectFamilyForm(forms.ModelForm):
    class Meta:
        model = CrushConnectMembership
        fields = ("has_children", "wants_children", "relationship_timeline")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _require(self, "has_children", "wants_children", "relationship_timeline")


# ---------------------------------------------------------------------------
# Step 6 — Ideal match (hard filters: gender + age range)
# ---------------------------------------------------------------------------
class ConnectIdealMatchForm(forms.ModelForm):
    preferred_genders = forms.MultipleChoiceField(
        choices=CrushProfile.GENDER_CHOICES,
        required=False,  # empty = open to all (preserves pool semantics)
        widget=forms.CheckboxSelectMultiple,
        label=_("Who would you like to see?"),
    )

    class Meta:
        model = CrushConnectMembership
        fields = ("preferred_genders", "preferred_age_min", "preferred_age_max")
        widgets = {
            "preferred_age_min": forms.NumberInput(attrs={"min": 18, "max": 99}),
            "preferred_age_max": forms.NumberInput(attrs={"min": 18, "max": 99}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _require(self, "preferred_age_min", "preferred_age_max")

    def clean(self):
        cleaned = super().clean()
        lo = cleaned.get("preferred_age_min")
        hi = cleaned.get("preferred_age_max")
        # A crossed range is a slider mishap, not an error — swap it.
        if lo is not None and hi is not None and lo > hi:
            cleaned["preferred_age_min"], cleaned["preferred_age_max"] = hi, lo
        return cleaned


# ---------------------------------------------------------------------------
# Step 7 — Your story (+ consent)
# ---------------------------------------------------------------------------
class ConnectStoryForm(forms.ModelForm):
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
            attrs={"rows": 3, "maxlength": 200, "class": "input-crush w-full"}
        ),
    )
    confirm_terms = forms.BooleanField(
        required=True,
        label=_(
            "I understand my Story, first name, age range, languages, interests "
            "and the life details I chose to share appear on a blurred-photo card "
            "to other members and coaches, and I can be removed from Crush Connect "
            "at any time."
        ),
    )

    class Meta:
        model = CrushConnectMembership
        fields = (
            "story_prompt",
            "story_answer",
            "story_prompt_2",
            "story_answer_2",
        )

    def __init__(self, *args, for_edit=False, **kwargs):
        """``for_edit=True`` (the profile-edit page) drops the consent gate —
        the member already consented during onboarding; re-saving their story
        shouldn't demand re-consent."""
        super().__init__(*args, **kwargs)
        if for_edit:
            self.fields["confirm_terms"].required = False
            self.fields["confirm_terms"].widget = forms.HiddenInput()

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
