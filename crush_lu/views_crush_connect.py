"""Crush Connect onboarding, catalogue, hub, and coach-pick views."""

import logging
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from crush_lu.connect_phase import (
    candidate_access_open,
    cycle_access_open,
)
from crush_lu.decorators import coach_required, crush_login_required
from crush_lu.email_helpers import send_crush_connect_catalogue_welcome
from crush_lu.models import CrushConnectMembership, CrushProfile, EventRegistration
from crush_lu.onboarding_connect import (
    CONNECT_STEPS,
    TOTAL_STEPS,
    annotate_steps,
    clamp_step,
    form_for_step,
    progress_pct,
    step_for,
    step_for_key,
)

logger = logging.getLogger(__name__)

User = get_user_model()

ONBOARDING_EVENT_SESSION_KEY = "crush_connect_onboarding_event_id"


@staff_member_required
def dev_connect_card_preview(request, user_id: int):
    """
    Render one Connect candidate card in isolation for visual review.

    Staff-only. Not gated by the CRUSH_CONNECT_LAUNCHED flag — staff need to
    preview the card UI long before public launch.
    """
    target = get_object_or_404(
        User.objects.select_related("crushprofile", "crush_connect_membership"),
        pk=user_id,
    )
    context = {
        "target": target,
        "target_profile": getattr(target, "crushprofile", None),
        "target_membership": getattr(target, "crush_connect_membership", None),
    }
    return render(request, "crush_lu/crush_connect/dev_card_preview.html", context)


def _user_has_connect_premium(user) -> bool:
    """True for an approved profile with an active Premium membership."""
    profile = getattr(user, "crushprofile", None)
    if profile is None or not profile.is_approved:
        return False
    return profile.has_active_premium


def _user_is_connect_candidate_eligible(user) -> bool:
    """Verified profile + identity verified (LuxID or in-person event attended): may opt in to candidate catalogue.

    LuxID or verified in-person event attendance is the ticket into the catalogue.
    Members without LuxID (e.g. cross-border attendees) qualify once they have attended
    at least 1 in-person event where their identity/age was verified (Issue #539).
    """
    profile = getattr(user, "crushprofile", None)
    if profile is None or not profile.is_approved:
        return False
    return profile.is_connect_identity_verified


def _user_passes_pre_onboarding_gate(user) -> bool:
    """Identity-verified first: a verified profile with LuxID or attended event may opt in.

    Identity verification is the entry requirement for collecting ANY extended Crush Connect
    data — Premium and non-Premium members must have verified identity
    (LuxID connected or at least 1 in-person event attended) before starting the onboarding wizard.
    Already-onboarded members are grandfathered by the callers (they completed opt-in under
    the rules that applied then).
    """
    profile = getattr(user, "crushprofile", None)
    if profile is None or not profile.is_approved:
        return False
    return profile.is_connect_identity_verified


def _hub_access_blocker(user):
    """Gate for the Crush Connect hub.

    The hub is the shared landing for onboarded members and the preparation
    surface for eligible members who
    have not opted in yet. It never bounces a candidate onward to the catalogue
    page; feature routes retain their own onboarding gates.
    """
    if user.is_staff:
        return None

    if not candidate_access_open():
        return redirect("crush_lu:crush_connect_teaser")

    membership = getattr(user, "crush_connect_membership", None)
    if membership is not None and membership.excluded_by_coach:
        return redirect("crush_lu:crush_connect_teaser")

    if membership is None or membership.onboarded_at is None:
        if not _user_passes_pre_onboarding_gate(user):
            return redirect("crush_lu:crush_connect_teaser")
        # The personal hub is also the preparation surface. Members who have
        # already passed the existing beta/identity gate may see their real
        # checklist here before opting in; every actual Connect feature keeps
        # its own onboarding gate, so this does not widen access to cards.
        return None

    return None


def _connect_trust_status(profile):
    """Return the member-facing verification label, and nothing sensitive.

    LuxID claims and event/check-in provenance deliberately stay server-side.
    The hub receives only the three approved labels and the LuxID-only product
    explanation.
    """
    if profile is None:
        return None

    has_luxid = profile.has_luxid_connected
    has_event_verification = profile.has_attended_event
    if has_luxid and has_event_verification:
        return {
            "kind": "double",
            "label": _("Double verification: LuxID + Crush event"),
            "explanation": _(
                "Your identity is confirmed digitally and through an in-person Crush event."
            ),
        }
    if has_event_verification:
        return {
            "kind": "event",
            "label": _("Verified at a Crush event"),
            "explanation": _(
                "A Crush coach confirmed your participation at an in-person event."
            ),
        }
    if has_luxid:
        return {
            "kind": "luxid",
            "label": _("Identity confirmed by LuxID"),
            "explanation": _(
                "LuxID confirms your identity. Taking part in a Crush event unlocks the active Connect journey and your three daily suggestions, once the other steps are complete."
            ),
        }
    return None


def _connect_readiness(user):
    """Build the personal, real-state checklist for the active Connect route.

    This is guidance, not a second entitlement system: the existing phase,
    opt-in, Premium and feature-specific gates remain authoritative. Recent
    activity is intentionally absent because it is not a requester-side gate
    for receiving Connect Week cards; ``last_login`` only filters people who
    may be presented as candidates, and the cycle requester gate does not read
    it.
    """
    profile = getattr(user, "crushprofile", None)
    membership = getattr(user, "crush_connect_membership", None)

    profile_approved = bool(profile and profile.is_approved)
    identity_verified = bool(profile and profile.is_connect_identity_verified)
    event_verified = bool(profile and profile.has_attended_event)
    has_photo = bool(profile and profile.photo_1)
    has_photo_consent = bool(membership and membership.photo_share_consent)
    is_onboarded = bool(membership and membership.is_onboarded)
    has_questions = bool(membership and membership.has_gate_questions)

    onboarding_url = reverse("crush_lu:crush_connect_onboarding")
    questions_url = (
        reverse("crush_lu:crush_connect_profile_edit") + "?section=questions"
        if is_onboarded
        else onboarding_url
    )
    identity_url = (
        reverse("crush_lu:profile_submitted")
        if profile_approved
        else reverse("crush_lu:edit_profile")
    )

    steps = [
        {
            "key": "identity",
            "complete": profile_approved and identity_verified,
            "title": _("Verified profile and identity"),
            "description": _(
                "Your Crush profile must be approved and your identity confirmed before Connect can show you suggestions."
            ),
            "cta_label": _("Complete verification"),
            "cta_url": identity_url,
        },
        {
            "key": "active_verification",
            "complete": event_verified,
            "title": _("Crush event verification"),
            "description": _(
                "A confirmed participation at a Crush event unlocks the active journey with three suggestions per day."
            ),
            "cta_label": _("Browse upcoming events"),
            "cta_url": reverse("crush_lu:event_list"),
        },
        {
            "key": "photo",
            "complete": has_photo,
            "title": _("Profile photo"),
            "description": _(
                "A profile photo is required for your daily Connect suggestions."
            ),
            "cta_label": _("Add a photo"),
            "cta_url": reverse("crush_lu:edit_profile") + "?section=photos",
        },
        {
            "key": "photo_consent",
            "complete": has_photo_consent,
            "title": _("Photo sharing consent"),
            "description": _(
                "Confirm that your clear photo may be shown only to the few verified members matched with you."
            ),
            "cta_label": _("Review photo consent"),
            "cta_url": questions_url,
        },
        {
            "key": "onboarding",
            "complete": is_onboarded,
            "title": _("Connect onboarding"),
            "description": _(
                "Complete the Connect onboarding to set your preferences and explicitly opt in."
            ),
            "cta_label": _("Continue onboarding"),
            "cta_url": onboarding_url,
        },
        {
            "key": "questions",
            "complete": has_questions,
            "title": _("Your three Connect questions"),
            "description": _(
                "Choose and answer three questions so matched members can discover you in the same respectful way."
            ),
            "cta_label": _("Choose my questions"),
            "cta_url": questions_url,
        },
    ]
    completed_count = sum(step["complete"] for step in steps)
    return {
        "steps": steps,
        "completed_count": completed_count,
        "total_count": len(steps),
        "is_complete": completed_count == len(steps),
        "trust_status": _connect_trust_status(profile),
    }


def _connect_done_url(user) -> str:
    """Where a finished member lands: Connect Week when open, else the Mix."""
    return (
        "crush_lu:connect_week_home"
        if cycle_access_open(user)
        else "crush_lu:crush_connect_catalogue_status"
    )


def _onboarding_gate(request):
    """
    Guard shared by the wizard routes. Returns ``(response, membership, done_url)``:
      - ``response`` is a redirect that should bounce the user, or ``None`` to
        proceed.
      - ``membership`` is the get_or_create'd ``CrushConnectMembership`` (``None``
        when bounced before it's created).
      - ``done_url`` is the named URL a finished member belongs on.

    Order mirrors the legacy inline gate: staff bypass the launch flag (not the
    eligibility-for-redirect), flag off → teaser, ineligible → teaser, excluded
    → teaser, already onboarded → done_url.
    """
    user = request.user
    done_url = _connect_done_url(user)

    if not user.is_staff and not candidate_access_open():
        return redirect("crush_lu:crush_connect_teaser"), None, done_url

    # Grandfather already-onboarded members (and bounce excluded ones) BEFORE the
    # LuxID gate, so the gate only ever guards NEW opt-ins / data collection.
    # Read the membership without creating one for an ineligible user.
    existing = getattr(user, "crush_connect_membership", None)
    if existing is not None:
        if existing.excluded_by_coach:
            return redirect("crush_lu:crush_connect_teaser"), existing, done_url
        if existing.is_onboarded:
            return redirect(done_url), existing, done_url

    # LuxID-first opt-in gate: no extended data is collected without LuxID.
    if not user.is_staff and not _user_passes_pre_onboarding_gate(user):
        return redirect("crush_lu:crush_connect_teaser"), existing, done_url

    # Check for photo_1 (which is optional for events but required for Connect)
    profile = getattr(user, "crushprofile", None)
    if not user.is_staff and profile and not profile.photo_1:
        from django.contrib import messages

        messages.warning(
            request, _("Please upload a profile photo to join Crush Connect.")
        )
        return redirect("crush_lu:edit_profile"), existing, done_url

    membership, _created = CrushConnectMembership.objects.get_or_create(user=user)
    return None, membership, done_url


@crush_login_required
def crush_connect_onboarding(request):
    """
    Smart-resume entry for the Crush Connect wizard. Routes the user to their
    current step. URL name unchanged so every existing redirect target still
    points here.
    """
    # An Event Lobby CTA carries the event it came from. Record that origin in the
    # session BEFORE any gate redirects (e.g. edit_profile for a missing photo),
    # so resuming onboarding after photo upload preserves the originating event.
    request.session.pop(ONBOARDING_EVENT_SESSION_KEY, None)
    raw_event_id = request.GET.get("event_id")
    if raw_event_id and getattr(request.user, "is_authenticated", False):
        try:
            event_id = int(raw_event_id)
        except (TypeError, ValueError):
            event_id = None
        if event_id is not None:
            registration = (
                EventRegistration.objects.filter(
                    event_id=event_id,
                    user=request.user,
                    status="attended",
                    event__is_published=True,
                    event__is_cancelled=False,
                )
                .select_related("event")
                .first()
            )
            if registration is not None:
                from crush_lu.services.event_lobby import lobby_admission_open

                if lobby_admission_open(registration.event):
                    request.session[ONBOARDING_EVENT_SESSION_KEY] = event_id

    response, membership, _done = _onboarding_gate(request)
    if response is not None:
        return response

    return redirect(
        "crush_lu:crush_connect_onboarding_step",
        step=clamp_step(membership.onboarding_step),
    )


def _emit_onboarding_complete(request, done_url):
    """Show the appropriate Connect Week/Mix welcome and send the welcome mail."""
    user = request.user
    if cycle_access_open(user):
        messages.success(
            request, _("Welcome to Crush Connect — your Connect Week is ready.")
        )
    else:
        messages.success(
            request,
            _(
                "Welcome to Crush Connect — you're in the mix and can "
                "be matched by a Crush Coach."
            ),
        )
        send_crush_connect_catalogue_welcome(user, request)


def _recompute_member_match_scores(user):
    """Refresh the member's trait-based MatchScores after their Connect traits
    change. Best-effort — scoring is a soft compatibility signal (missing pairs
    are neutral), so a failure must never block onboarding or an edit save."""
    try:
        from crush_lu.matching import update_match_scores_for_user

        update_match_scores_for_user(user)
    except Exception:  # pragma: no cover - never block the user flow
        import logging

        logging.getLogger(__name__).exception(
            "Crush Connect match-score recompute failed for user %s", user.pk
        )


def _step3_selection_context(form):
    """Selected language codes + interest ids for the step-3 / edit-section
    chip ``checked`` state, read from the bound form so an invalid re-render
    keeps the user's just-submitted choices."""
    langs = form["languages"].value() or []
    raw_ids = form["interests"].value() or []
    selected_ids = set()
    for v in raw_ids:
        try:
            selected_ids.add(int(getattr(v, "pk", v)))
        except (TypeError, ValueError):
            continue
    return {
        "selected_languages": set(langs),
        "selected_interest_ids": selected_ids,
    }


def _selected_trait_ids(form, field_name):
    """Selected Trait ids for a checkbox field, read from the bound form so an
    invalid re-render (and the legacy prefill) keep their checked state."""
    raw = form[field_name].value() or []
    out = set()
    for v in raw:
        try:
            out.add(int(getattr(v, "pk", v)))
        except (TypeError, ValueError):
            continue
    return out


def _connect_trait_context(cfg_key, form):
    """Chip ``checked`` state for the trait steps, keyed per step."""
    if cfg_key == "lifestyle":
        return {
            "selected_quality_ids": _selected_trait_ids(form, "qualities"),
            "selected_defect_ids": _selected_trait_ids(form, "defects"),
        }
    if cfg_key == "ideal_match":
        return {"selected_sought_ids": _selected_trait_ids(form, "sought_qualities")}
    return {}


@crush_login_required
def crush_connect_onboarding_step(request, step: int):
    """One server-side wizard step. Saves immediately, resumable, no skipping
    ahead (but completed steps stay editable)."""
    response, membership, done_url = _onboarding_gate(request)
    if response is not None:
        return response

    step = clamp_step(step)
    pointer = clamp_step(membership.onboarding_step)
    # Block skipping ahead: revisit any completed step (step <= pointer) but
    # never jump past the furthest-reached step.
    if step > pointer:
        return redirect("crush_lu:crush_connect_onboarding_step", step=pointer)

    profile = getattr(request.user, "crushprofile", None)
    cfg = step_for(step)
    form_class = form_for_step(step)

    if request.method == "POST":
        form = form_class(request.POST, instance=membership)
        if form.is_valid():
            # Stamp the start time exactly once, on first successful POST.
            if membership.onboarding_started_at is None:
                membership.onboarding_started_at = timezone.now()
            obj = form.save()  # commit=True → fields AND M2M (interests) persist
            new_pointer = max(pointer, step + 1)

            if step == TOTAL_STEPS:
                obj.onboarded_at = timezone.now()
                obj.onboarding_step = TOTAL_STEPS
                obj.save(
                    update_fields=[
                        "onboarded_at",
                        "onboarding_step",
                        "onboarding_started_at",
                    ]
                )
                _emit_onboarding_complete(request, done_url)
                # Member is now in the pool — refresh compatibility highlights.
                _recompute_member_match_scores(request.user)
                # Crush Connect Event Lobby: a checked-in guest who completes
                # onboarding while a lobby or recap is open joins immediately
                # (spec §5.3/§10.2). Best-effort — never let the lobby break
                # onboarding completion.
                originating_event_id = request.session.pop(
                    ONBOARDING_EVENT_SESSION_KEY, None
                )
                joined_participations = []
                try:
                    from crush_lu.services.event_lobby import (
                        handle_onboarding_completed,
                    )

                    joined_participations = handle_onboarding_completed(request.user)
                except Exception:
                    logger.exception(
                        "Event lobby onboarding-join failed for user %s",
                        request.user.pk,
                    )
                for participation in joined_participations:
                    try:
                        from crush_lu.views_event_lobby import (
                            broadcast_participant_joined,
                        )

                        broadcast_participant_joined(
                            participation.event_id, onboarded=True
                        )
                    except Exception:
                        logger.exception(
                            "Event lobby onboarding broadcast failed for user %s "
                            "and event %s",
                            request.user.pk,
                            participation.event_id,
                        )
                if joined_participations:
                    selected_participation = joined_participations[0]
                    if originating_event_id is not None:
                        selected_participation = next(
                            (
                                participation
                                for participation in joined_participations
                                if participation.event_id == originating_event_id
                            ),
                            None,
                        )
                        # The origin may have closed while a member completed
                        # the wizard. Do not surprise them by landing in an
                        # unrelated overlapping recap.
                        if selected_participation is None:
                            return redirect(done_url)
                    return redirect(
                        "crush_lu:event_lobby",
                        event_id=selected_participation.event_id,
                    )
                return redirect(done_url)

            obj.onboarding_step = new_pointer
            obj.save(update_fields=["onboarding_step", "onboarding_started_at"])
            return redirect("crush_lu:crush_connect_onboarding_step", step=step + 1)
        # invalid → fall through and re-render with errors
    else:
        initial = {}
        # FIRST-time-only prefill (membership field still empty), so a deliberate
        # clear on a back-edit isn't re-populated. The trait prefill is the lazy
        # migration of the legacy "Ideal Crush" data off CrushProfile: the member
        # confirms (and consents to) it through the wizard before it persists.
        if cfg.key == "languages" and not membership.languages and profile:
            initial["languages"] = list(profile.event_languages or [])
        elif cfg.key == "lifestyle" and profile:
            if not membership.qualities.exists() and profile.qualities.exists():
                initial["qualities"] = list(profile.qualities.all())
            if not membership.defects.exists() and profile.defects.exists():
                initial["defects"] = list(profile.defects.all())
        elif cfg.key == "ideal_match" and profile:
            if not membership.sought_qualities.exists():
                if profile.sought_qualities.exists():
                    initial["sought_qualities"] = list(profile.sought_qualities.all())
                if profile.first_step_preference:
                    initial["first_step_preference"] = profile.first_step_preference
                initial["astro_enabled"] = profile.astro_enabled
                # Age/gender prefs migrate from the legacy Ideal Crush too, so a
                # first-time opt-in with existing profile preferences doesn't
                # overwrite them with the wizard's open defaults.
                if profile.preferred_genders:
                    initial["preferred_genders"] = list(profile.preferred_genders)
                initial["preferred_age_min"] = profile.preferred_age_min
                initial["preferred_age_max"] = profile.preferred_age_max
        form = form_class(instance=membership, initial=initial)

    context = {
        "form": form,
        "membership": membership,
        "profile": profile,
        "step": step,
        "step_cfg": cfg,
        "step_template": cfg.template,
        "total_steps": TOTAL_STEPS,
        "progress_pct": progress_pct(step),
        "connect_steps": annotate_steps(step),
        "prev_step": step - 1 if step > 1 else None,
        "is_final_step": step == TOTAL_STEPS,
    }
    if cfg.key == "languages":
        context.update(_step3_selection_context(form))
    context.update(_connect_trait_context(cfg.key, form))
    return render(request, "crush_lu/crush_connect/onboarding.html", context)


# Header emoji per edit section — mirrors the emoji each wizard step partial
# shows in its own card header, so the index list reads as the same sections.
_CONNECT_SECTION_EMOJI = {
    "intention": "🌱",
    "lifestyle": "✨",
    "languages": "🗣️",
    "life": "🧩",
    "family": "🪴",
    "ideal_match": "💘",
    "questions": "❓",
}


def _connect_section_summaries(membership):
    """One-line current-value summary per edit section, keyed by section key.

    Powers the drill-down index so a member sees what each section currently
    holds without opening it. A blank string means "nothing set yet" — the
    template renders the fallback for those.
    """
    m = membership

    intention = m.get_relationship_goal_display() if m.relationship_goal else ""

    lifestyle = " · ".join(
        getattr(m, f"get_{field}_display")()
        for field in ("lifestyle_energy", "lifestyle_social", "lifestyle_pace")
        if getattr(m, field)
    )

    lang_bits = []
    labels = [str(label) for label in m.languages_display]
    if labels:
        lang_bits.append(", ".join(labels))
    n_interests = m.interests.count()
    if n_interests:
        lang_bits.append(
            ngettext("%(count)d interest", "%(count)d interests", n_interests)
            % {"count": n_interests}
        )
    languages = " · ".join(lang_bits)

    life = " · ".join(str(part) for part in m.life_situation_display)
    family = " · ".join(str(part) for part in m.family_future_display)

    gender_labels = dict(CrushProfile.GENDER_CHOICES)
    genders = m.preferred_genders or []
    who = (
        ", ".join(str(gender_labels.get(code, code)) for code in genders)
        if genders
        else _("Open to all")
    )
    ideal_match = _("%(who)s · ages %(lo)s–%(hi)s") % {
        "who": who,
        "lo": m.preferred_age_min,
        "hi": m.preferred_age_max,
    }

    n_questions = m.gate_questions.count()
    questions = (
        ngettext("%(count)d question chosen", "%(count)d questions chosen", n_questions)
        % {"count": n_questions}
        if n_questions
        else ""
    )

    return {
        "intention": intention,
        "lifestyle": lifestyle,
        "languages": languages,
        "life": life,
        "family": family,
        "ideal_match": ideal_match,
        "questions": questions,
    }


@crush_login_required
def crush_connect_profile_edit(request):
    """
    Post-onboarding editor for Connect/catalogue answers. Mobile-first
    drill-down (mirrors the main profile editor): a tappable section index,
    each opening one focused section via ``?section=<key>``. Never touches
    ``onboarded_at`` / ``onboarding_step``.
    """
    user = request.user
    if not user.is_staff and not candidate_access_open():
        return redirect("crush_lu:crush_connect_teaser")

    membership = getattr(user, "crush_connect_membership", None)
    if membership is None or not membership.is_onboarded:
        # Not finished yet → into the wizard, not the editor.
        return redirect("crush_lu:crush_connect_onboarding")

    profile = getattr(user, "crushprofile", None)
    section_key = request.POST.get("section") or request.GET.get("section")
    active = step_for_key(section_key)

    def _build_form(cfg, data=None):
        # The questions form drops its consent gate in edit mode.
        form_kwargs = {"for_edit": True} if cfg.key == "questions" else {}
        return form_for_step(cfg.n)(data, instance=membership, **form_kwargs)

    # POST a known section → validate + save, then drop back to the index so
    # the member sees the updated summary. Invalid → re-render that section.
    if request.method == "POST" and active is not None:
        form = _build_form(active, request.POST)
        if form.is_valid():
            form.save()  # commit=True → interests/trait M2Ms included
            # Trait/preference edits change compatibility — refresh the scores.
            if active.key in ("lifestyle", "ideal_match"):
                _recompute_member_match_scores(user)
            messages.success(request, _("Your changes have been saved."))
            return redirect("crush_lu:crush_connect_profile_edit")
    else:
        form = None

    # Detail mode: a known section is targeted (GET or invalid POST).
    if active is not None:
        if form is None:
            form = _build_form(active)
        context = {
            "mode": "detail",
            "membership": membership,
            "profile": profile,
            "cfg": active,
            "form": form,
        }
        if active.key == "languages":
            context.update(_step3_selection_context(form))
        context.update(_connect_trait_context(active.key, form))
        return render(request, "crush_lu/crush_connect/profile_edit.html", context)

    # Index mode: the tappable section list with current-value summaries.
    summaries = _connect_section_summaries(membership)
    index_sections = [
        {
            "cfg": s,
            "emoji": _CONNECT_SECTION_EMOJI.get(s.key, ""),
            "summary": summaries.get(s.key, ""),
        }
        for s in CONNECT_STEPS
    ]
    context = {
        "mode": "index",
        "membership": membership,
        "profile": profile,
        "index_sections": index_sections,
    }
    return render(request, "crush_lu/crush_connect/profile_edit.html", context)


@crush_login_required
def crush_connect_catalogue_status(request):
    """
    Status page for onboarded members: confirms they are in the catalogue,
    previews their profile, and shows anonymous Read-the-Photo totals.
    """
    user = request.user

    if not user.is_staff and not candidate_access_open():
        return redirect("crush_lu:crush_connect_teaser")

    if not user.is_staff and not _user_is_connect_candidate_eligible(user):
        return redirect("crush_lu:crush_connect_teaser")

    membership = getattr(user, "crush_connect_membership", None)
    if membership is not None and membership.excluded_by_coach:
        return redirect("crush_lu:crush_connect_teaser")
    if membership is None or not membership.is_onboarded:
        return redirect("crush_lu:crush_connect_onboarding")

    from crush_lu.models import CrushConnectWaitlist

    # Before the public launch, selected waitlist testers receive beta access.
    # The teaser (the other home of the
    # join button) redirects every approved + LuxID member straight back here
    # once the candidate track opens, so this page has to carry the waitlist —
    # otherwise a beta candidate can never join the tester waitlist.
    # Tester selection stays silent — the member only ever sees their position.
    connect_launched = bool(getattr(settings, "CRUSH_CONNECT_LAUNCHED", False))
    waitlist_context = {
        "on_waitlist": False,
        "waitlist_position": None,
        "total_waitlist": 0,
    }
    if not connect_launched:
        entry = CrushConnectWaitlist.objects.filter(user=user).first()
        waitlist_context.update(
            {
                "on_waitlist": entry is not None,
                "waitlist_position": entry.waitlist_position if entry else None,
                "total_waitlist": CrushConnectWaitlist.objects.count(),
            }
        )

    return render(
        request,
        "crush_lu/crush_connect/catalogue_status.html",
        {
            "membership": membership,
            "profile": getattr(user, "crushprofile", None),
            "gate_stat_rows": _gate_stat_rows(user, membership),
            "connect_launched": connect_launched,
            **waitlist_context,
        },
    )


def _gate_stat_rows(user, membership):
    """Assemble the member's own gate questions with anonymous guess tallies.

    Returns ``[{question, yes, total}]`` for the "8 of 12 think you work in
    Finance" stat — aggregate only, with neither the member's correct answer
    nor any responder identity exposed to the template.
    """
    from crush_lu.services.crush_connect import gate_answer_stats

    if membership is None:
        return []
    stats = gate_answer_stats(user)
    rows = []
    for gq in membership.active_gate_questions:
        s = stats.get(gq.question_id, {})
        rows.append(
            {
                "question": gq.question,
                "yes": s.get("yes", 0),
                "total": s.get("total", 0),
            }
        )
    return rows


@crush_login_required
def crush_connect_hub(request):
    """Crush Connect home — the member's hub that aggregates every Connect
    surface (Connect Week, catalogue, Coach's Pick, and profile) with quick
    links and status badges, and explains preparation before opt-in.
    The dedicated nav menu and the mobile bottom-nav 'Connect' tab point here.
    """
    user = request.user
    profile = getattr(user, "crushprofile", None)

    from crush_lu.services.crush_connect import get_active_coach_pick

    user = request.user
    blocker = _hub_access_blocker(user)
    if blocker is not None:
        return blocker

    membership = getattr(user, "crush_connect_membership", None)
    coach = getattr(user, "crushcoach", None)

    # Connect Week still requires onboarding like every Connect surface.
    cycle_access = bool(
        (user.is_staff or cycle_access_open(user))
        and membership is not None
        and membership.is_onboarded
    )
    # The week access blocker fully bypasses onboarding for staff, so keep an
    # explicit preview link for an unonboarded staff account.
    cycle_staff_preview = bool(user.is_staff and not cycle_access)
    coach_pick = get_active_coach_pick(user)

    # Event Lobby hub card (spec §2 Navigation): shown only while the member
    # is an eligible participant of a currently-live event lobby. People I've
    # Met is a permanent hub section (§7.8).
    from crush_lu.services.event_lobby import (
        get_active_live_lobby,
        get_people_ive_met,
        lobby_feature_enabled,
    )

    event_lobby_enabled = lobby_feature_enabled()
    people_ive_met_count = len(get_people_ive_met(user)) if event_lobby_enabled else 0

    context = {
        "membership": membership,
        "cycle_access": cycle_access,
        "cycle_staff_preview": cycle_staff_preview,
        "is_coach": bool(coach and coach.is_active),
        "coach_pick": coach_pick,
        "active_lobby": get_active_live_lobby(user) if event_lobby_enabled else None,
        "event_lobby_enabled": event_lobby_enabled,
        "people_ive_met_count": people_ive_met_count,
        "has_premium": bool(profile and profile.has_active_premium),
        # Naming the coach is most of the point: it is the thing being sold, and
        # the hub never told the member who theirs is.
        "premium_coach": getattr(profile, "assigned_coach", None) if profile else None,
        "connect_readiness": _connect_readiness(user),
    }
    return render(request, "crush_lu/crush_connect/hub.html", context)


@crush_login_required
def crush_connect_home(request):
    """Backward-compatible redirect from the retired ``/today/`` route."""
    return redirect("crush_lu:crush_connect_hub")


@crush_login_required
def crush_connect_legacy_spark_redirect(request, **_kwargs):
    """Keep retired Connect Spark notification and email links non-breaking."""
    return redirect("crush_lu:crush_connect_hub")


@crush_login_required
def crush_connect_coach_pick(request):
    """Show the active human-curated Premium pick, if one is available."""
    blocker = _hub_access_blocker(request.user)
    if blocker is not None:
        return blocker
    if not _user_has_connect_premium(request.user):
        return redirect("crush_lu:crush_connect_catalogue_status")

    from crush_lu.services.crush_connect import (
        get_active_coach_pick,
        is_premium_connect_eligible,
    )

    if not is_premium_connect_eligible(request.user):
        profile = getattr(request.user, "crushprofile", None)
        if profile is not None and not profile.photo_1:
            messages.warning(
                request,
                _(
                    "Your profile photo is missing. It blocks access to your daily "
                    "Connect suggestions; add it now in Photos."
                ),
            )
            return redirect(reverse("crush_lu:edit_profile") + "?section=photos")
        return redirect("crush_lu:crush_connect_hub")

    return render(
        request,
        "crush_lu/crush_connect/coach_pick.html",
        {"coach_pick": get_active_coach_pick(request.user)},
    )


# ---------------------------------------------------------------------------
# Coach Picks (M7) — coach curation interface + member response
# ---------------------------------------------------------------------------


@coach_required
def coach_connect_members(request):
    """The coach's Crush Connect curation hub: their assigned Premium
    members with Connect status and current pick, ready to curate."""
    from crush_lu.models import ConnectCoachPick

    coach = request.coach
    members = (
        # Active premium members only — coach assignment alone (backfill,
        # attendance auto-assign) doesn't put a member in the curation hub.
        User.objects.filter(
            crushprofile__assigned_coach=coach,
            premium_memberships__status="active",
        )
        .select_related("crushprofile", "crush_connect_membership")
        .order_by("first_name", "pk")
        .distinct()
    )
    # Iterate oldest-first so the NEWEST row wins the per-member map —
    # default model ordering is newest-first, which would let an older
    # accepted pick mask a fresher proposal.
    picks = {
        p.member_id: p
        for p in ConnectCoachPick.objects.filter(
            coach=coach, status__in=["proposed", "accepted"]
        )
        .select_related("candidate")
        .order_by("created_at")
    }
    from crush_lu.services.crush_connect import get_active_coach_pick

    rows = []
    for m in members:
        membership = getattr(m, "crush_connect_membership", None)
        pick = picks.get(m.pk)
        if pick is not None and pick.status == "proposed":
            # A proposed pick whose candidate left the pool is hidden from
            # the member — show the coach "no open pick" so they re-pick
            # instead of waiting forever on an answer that can't come.
            pick = get_active_coach_pick(m)
        rows.append(
            {
                "member": m,
                "onboarded": bool(membership and membership.is_onboarded),
                "pick": pick,
            }
        )
    return render(
        request,
        "crush_lu/crush_connect/coach_members.html",
        {"rows": rows},
    )


@coach_required
def coach_connect_member(request, user_id: int):
    """Browse one member's eligible pool (full profiles) and propose a pick."""
    from crush_lu.models import ConnectCoachPick
    from crush_lu.services.crush_connect import (
        get_eligible_pool,
        propose_coach_pick,
    )

    coach = request.coach
    member = get_object_or_404(
        User.objects.select_related(
            "crushprofile", "crush_connect_membership"
        ).distinct(),
        pk=user_id,
        crushprofile__assigned_coach=coach,
        premium_memberships__status="active",
    )

    if request.method == "POST":
        candidate = get_object_or_404(User, pk=request.POST.get("candidate_id"))
        note = (request.POST.get("note") or "").strip()[:300]
        try:
            propose_coach_pick(coach, member, candidate, note=note)
        except ValueError as exc:
            reasons = {
                "already_picked": _("You already proposed this candidate to them."),
                "candidate_not_eligible": _(
                    "This candidate isn't in their eligible pool."
                ),
                "member_not_ready": _("This member isn't Connect-onboarded yet."),
            }
            messages.error(
                request, reasons.get(str(exc), _("This pick isn't possible right now."))
            )
        else:
            messages.success(
                request,
                _("Pick proposed — %(name)s will see it on their next visit.")
                % {"name": member.first_name or member.username},
            )
        return redirect("crush_lu:coach_connect_member", user_id=member.pk)

    pool = list(
        get_eligible_pool(member).select_related(
            "crushprofile", "crush_connect_membership"
        )
        # Event Identity chips render per candidate card — prefetch the taxonomy
        # M2M to keep the pool render N+1-free (spec §7).
        .prefetch_related("crushprofile__interests_new")[:60]
    )
    already_picked_ids = set(
        ConnectCoachPick.objects.filter(member=member).values_list(
            "candidate_id", flat=True
        )
    )
    picks = list(
        ConnectCoachPick.objects.filter(member=member)
        .select_related("candidate")
        .order_by("-created_at")[:10]
    )
    return render(
        request,
        "crush_lu/crush_connect/coach_member_detail.html",
        {
            "member": member,
            "pool": pool,
            "already_picked_ids": already_picked_ids,
            "picks": picks,
        },
    )


@crush_login_required
def crush_connect_pick_respond(request, pick_id: int):
    """Member accepts/declines their coach's pick (POST only)."""
    from crush_lu.models import ConnectCoachPick
    from crush_lu.services.crush_connect import respond_to_coach_pick

    if request.method != "POST":
        return redirect("crush_lu:crush_connect_coach_pick")

    pick = get_object_or_404(
        ConnectCoachPick.objects.select_related("coach__user", "member"),
        pk=pick_id,
        member=request.user,
    )
    accept = request.POST.get("action") == "accept"
    pick = respond_to_coach_pick(pick, accept=accept)
    if accept and pick.status != "accepted":
        # Stale pick (eligibility lost / coach reassigned) — the accept was
        # a no-op, so don't promise a date that isn't being arranged.
        messages.info(
            request,
            _(
                "This pick is no longer available — your coach will propose someone new."
            ),
        )
    elif accept:
        messages.success(
            request,
            _("Wonderful — your Crush Coach will contact them and arrange your date."),
        )
    else:
        messages.info(
            request,
            _("No problem — your coach will pick someone else for you."),
        )
    return redirect("crush_lu:crush_connect_coach_pick")


# ─── Experience explainers ──────────────────────────────────────────────────
# Member-facing landing pages, one per Crush Connect experience. Educational,
# so they are deliberately softer-gated than the live surfaces: any logged-in
# member may read them (no onboarding required) once the flag is on.
# Canonical taxonomy and copy rules: docs/products/crush-connect.md.

CONNECT_EXPERIENCES = {
    "coach-pick": {
        "name": _("Your Coach's Pick"),
        "tagline": _("One match a week, picked by a human."),
    },
    "read-the-photo": {
        "name": _("Read the Photo"),
        "tagline": _("No bios. Three questions. One photo."),
    },
    "in-the-mix": {
        "name": _("In the Mix"),
        "tagline": _("Verified, discoverable, free — forever."),
    },
}


@crush_login_required
def crush_connect_experience(request, slug):
    """One experience explainer page (name, description, how it works, CTA)."""
    if slug not in CONNECT_EXPERIENCES:
        raise Http404

    if not request.user.is_staff and not candidate_access_open():
        return redirect("crush_lu:crush_connect_teaser")

    membership = getattr(request.user, "crush_connect_membership", None)
    context = {
        "experience": CONNECT_EXPERIENCES[slug],
        "has_premium": _user_has_connect_premium(request.user),
        "is_onboarded": membership is not None and membership.onboarded_at is not None,
        "other_experiences": [
            {"slug": s, **exp} for s, exp in CONNECT_EXPERIENCES.items() if s != slug
        ],
    }
    return render(request, f"crush_lu/crush_connect/experiences/{slug}.html", context)
