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
    Trait,
)

# Interests: at least one, at most this many (the "n/8" counter cap).
MAX_INTERESTS = 8
MIN_INTERESTS = 1

# Traits (Ideal Crush, migrated onto the membership): pick 1..5 per category.
MAX_TRAITS = 5
MIN_TRAITS = 1


class _TraitMultipleChoiceField(forms.ModelMultipleChoiceField):
    """Trait checkbox field that labels options by their display label only
    (the model ``__str__`` appends the trait type, which we don't want in
    the chip)."""

    def label_from_instance(self, obj):
        return obj.label


def _validate_trait_count(traits, *, min_msg, max_msg):
    count = len(traits) if traits is not None else 0
    if count < MIN_TRAITS:
        raise forms.ValidationError(min_msg)
    if count > MAX_TRAITS:
        raise forms.ValidationError(max_msg % {"max": MAX_TRAITS})
    return traits


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
    qualities = _TraitMultipleChoiceField(
        queryset=Trait.objects.filter(trait_type="quality"),
        required=True,
        widget=forms.CheckboxSelectMultiple,
        label=_("Your top qualities"),
    )
    defects = _TraitMultipleChoiceField(
        queryset=Trait.objects.filter(trait_type="defect"),
        required=True,
        widget=forms.CheckboxSelectMultiple,
        label=_("Your honest flaws"),
    )

    class Meta:
        model = CrushConnectMembership
        fields = (
            "lifestyle_energy",
            "lifestyle_social",
            "lifestyle_pace",
            "qualities",
            "defects",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _require(self, "lifestyle_energy", "lifestyle_social", "lifestyle_pace")

    def clean_qualities(self):
        return _validate_trait_count(
            self.cleaned_data.get("qualities"),
            min_msg=_("Pick at least one quality."),
            max_msg=_("Pick at most %(max)d qualities."),
        )

    def clean_defects(self):
        return _validate_trait_count(
            self.cleaned_data.get("defects"),
            min_msg=_("Pick at least one flaw."),
            max_msg=_("Pick at most %(max)d flaws."),
        )


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
                attrs={"min": 120, "max": 230, "class": "input-crush"}
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
    sought_qualities = _TraitMultipleChoiceField(
        queryset=Trait.objects.filter(trait_type="quality"),
        required=True,
        widget=forms.CheckboxSelectMultiple,
        label=_("Qualities you're looking for"),
    )
    first_step_preference = forms.ChoiceField(
        choices=CrushConnectMembership.FIRST_STEP_CHOICES,
        required=True,
        widget=forms.RadioSelect,
        label=_("Who makes the first step?"),
    )
    astro_enabled = forms.BooleanField(
        required=False,
        label=_("Include zodiac compatibility in my matches"),
    )

    class Meta:
        model = CrushConnectMembership
        fields = (
            "preferred_genders",
            "preferred_age_min",
            "preferred_age_max",
            "sought_qualities",
            "first_step_preference",
            "astro_enabled",
        )
        widgets = {
            "preferred_age_min": forms.NumberInput(attrs={"min": 18, "max": 99}),
            "preferred_age_max": forms.NumberInput(attrs={"min": 18, "max": 99}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _require(self, "preferred_age_min", "preferred_age_max")

    def clean_sought_qualities(self):
        return _validate_trait_count(
            self.cleaned_data.get("sought_qualities"),
            min_msg=_("Pick at least one quality you're looking for."),
            max_msg=_("Pick at most %(max)d sought qualities."),
        )

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
                "class": "input-crush",
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
            attrs={"rows": 3, "maxlength": 200, "class": "input-crush"}
        ),
    )
    confirm_terms = forms.BooleanField(
        required=True,
        label=_(
            "I understand my Story, first name, age range, languages, interests, "
            "the personality traits I picked and the life details I chose to share "
            "appear on my card to other members and coaches, and I can "
            "be removed from Crush Connect at any time."
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


# ---------------------------------------------------------------------------
# Step 7 (M8) — "Read-the-Photo": pick 3 questions + answer each about yourself
# ---------------------------------------------------------------------------
class ConnectGateQuestionsForm(forms.ModelForm):
    """
    Replaces the story step. The member picks exactly 3 questions from this
    week's catalogue and gives their own yes/no truth for each (what other
    members' guesses are scored against). Each candidate question renders as one
    three-state control — Skip / Yes / No — so selecting an answer both picks the
    question and records the truth.

    A ``ModelForm`` on the membership so ``photo_share_consent`` (the field that
    lets the clear photo be shown) persists natively and the wizard's
    ``form(request.POST, instance=membership)`` + ``form.save()`` convention is
    unchanged; the 3 picks are saved to the ``MemberGateQuestion`` through-model.
    """

    ANSWER_CHOICES = [
        ("", _("Skip")),
        ("yes", _("Yes — that's me")),
        ("no", _("No")),
    ]

    photo_share_consent = forms.BooleanField(
        required=True,
        label=_(
            "I agree my clear photo is shown to the few people matched to me each "
            "day so they can guess my questions."
        ),
    )
    confirm_terms = forms.BooleanField(
        required=True,
        label=_(
            "I understand my photo, first name, age range and the 3 questions I "
            "chose appear on my card to the members matched with me, and I can be "
            "removed from Crush Connect at any time."
        ),
    )

    class Meta:
        model = CrushConnectMembership
        fields = ("photo_share_consent",)

    def __init__(self, *args, for_edit=False, **kwargs):
        super().__init__(*args, **kwargs)
        from crush_lu.services.crush_connect import get_or_create_question_week

        self.week = get_or_create_question_week()

        # Candidate set = this week's active questions ∪ the member's existing
        # picks (which may have rotated out of the week but stay editable, so a
        # member never loses in-flight picks at a week boundary).
        week_questions = list(
            self.week.questions.filter(is_active=True).order_by("category", "id")
        )
        existing = []
        if self.instance and self.instance.pk:
            existing = list(self.instance.active_gate_questions)
        self._existing_answer = {gq.question_id: gq.owner_answer for gq in existing}
        seen = {q.id for q in week_questions}
        self.week_questions = week_questions + [
            gq.question for gq in existing if gq.question_id not in seen
        ]

        for q in self.week_questions:
            fname = f"q_{q.id}"
            self.fields[fname] = forms.ChoiceField(
                choices=self.ANSWER_CHOICES,
                required=False,
                label=q.text,
            )
            if not self.is_bound and q.id in self._existing_answer:
                self.fields[fname].initial = (
                    "yes" if self._existing_answer[q.id] else "no"
                )

        if for_edit:
            # Editing other sections shouldn't force re-accepting the terms, but
            # photo consent stays a real toggle (un-ticking it revokes surfacing).
            self.fields["confirm_terms"].required = False
            self.fields["confirm_terms"].widget = forms.HiddenInput()
            self.fields["photo_share_consent"].required = False

    def question_rows(self):
        """(question, bound field) pairs for the template to render as Skip/Yes/No.

        Django templates can't resolve the dynamic ``q_<id>`` field names, so the
        form hands the template ready-paired rows.
        """
        return [
            {"question": q, "field": self[f"q_{q.id}"]}
            for q in self.week_questions
        ]

    def clean(self):
        cleaned = super().clean()
        from crush_lu.services.crush_connect import GATE_QUESTION_COUNT

        picks = []
        for q in self.week_questions:
            val = cleaned.get(f"q_{q.id}")
            if val in ("yes", "no"):
                picks.append((q.id, val == "yes"))
        if len(picks) != GATE_QUESTION_COUNT:
            raise forms.ValidationError(
                _("Pick exactly %(n)d questions and answer each about yourself.")
                % {"n": GATE_QUESTION_COUNT}
            )
        self.cleaned_picks = picks
        return cleaned

    def save(self, commit=True):
        membership = super().save(commit=commit)
        if commit:
            self._save_picks(membership)
        return membership

    def _save_picks(self, membership):
        from django.db import transaction

        from crush_lu.models import ConnectQuestionAnswer, MemberGateQuestion

        new = {qid: owner_answer for qid, owner_answer in self.cleaned_picks}
        with transaction.atomic():
            # Guesses become stale for any question the member DROPS or whose
            # truth they CHANGE — the answer was scored against the old pick and
            # would otherwise be locked in by the unique constraint and mis-scored
            # against the new truth. Clear those so viewers can answer the fresh
            # gate; guesses for questions kept with the same truth are preserved.
            stale_qids = [
                gq.question_id
                for gq in membership.gate_questions.all()
                if new.get(gq.question_id) != gq.owner_answer
            ]
            if stale_qids:
                ConnectQuestionAnswer.objects.filter(
                    profile_owner=membership.user, question_id__in=stale_qids
                ).delete()

            membership.gate_questions.all().delete()
            MemberGateQuestion.objects.bulk_create(
                [
                    MemberGateQuestion(
                        membership=membership,
                        question_id=qid,
                        position=position,
                        owner_answer=owner_answer,
                        picked_week=self.week,
                    )
                    for position, (qid, owner_answer) in enumerate(
                        self.cleaned_picks, start=1
                    )
                ]
            )
