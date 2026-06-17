"""
Crush Connect views.

M3: a staff-only debug route that renders a single Drop card in isolation.
M4: the user-facing ``Today's Drop`` page.

The full user-facing surface (Today's Drop, Pending Sparks, journey reveal)
will continue to grow in this module across M5–M7.
"""

from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from crush_lu.decorators import crush_login_required
from crush_lu.email_helpers import send_crush_connect_catalogue_welcome
from crush_lu.models import CrushConnectMembership, CrushProfile
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
from crush_lu.services import get_or_create_daily_drop


User = get_user_model()


@staff_member_required
def dev_connect_card_preview(request, user_id: int):
    """
    Render one Drop card for the given user, in isolation, for visual review.

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
        "show_spark_placeholder": True,
    }
    return render(request, "crush_lu/crush_connect/dev_card_preview.html", context)


def _user_is_connect_receiver_eligible(user) -> bool:
    """Verified profile + PREMIUM (personal coach assigned).

    Receiving Drops is the Premium product: it unlocks when a coach is
    assigned, not on plain event attendance or LuxID alone.
    """
    profile = getattr(user, "crushprofile", None)
    if profile is None or not profile.is_approved:
        return False
    return profile.assigned_coach_id is not None


def _user_is_connect_candidate_eligible(user) -> bool:
    """Verified profile + LuxID linked: may opt in to the candidate catalogue.

    LuxID is the ticket into the catalogue — members verified at an event
    stay verified for events/connections but must link LuxID before they
    can be picked for a Premium member's Drop.
    """
    profile = getattr(user, "crushprofile", None)
    if profile is None or not profile.is_approved:
        return False
    return profile.has_luxid_connected


def _user_passes_pre_onboarding_gate(user) -> bool:
    """LuxID-first: a verified profile with LuxID connected may opt in.

    LuxID is the entry requirement for collecting ANY extended Crush Connect
    data — both tracks (Premium receivers and candidate-only members) must
    connect LuxID before they can start the onboarding wizard. The Premium-
    coach distinction only decides where a finished member lands afterwards
    (Today's Drop vs. catalogue status) — see ``_connect_done_url`` /
    ``_connect_access_blocker``. Already-onboarded members are grandfathered
    by the callers (they completed opt-in under the rules that applied then).
    """
    profile = getattr(user, "crushprofile", None)
    if profile is None or not profile.is_approved:
        return False
    return profile.has_luxid_connected


def _connect_access_blocker(user):
    """
    Return ``None`` when the user may access Today's Drop, otherwise return
    a redirect/response that should be sent back to them.

    Staff users bypass all checks so they can preview the UI in any state.
    Two tracks exist (asymmetric model):
      - RECEIVER (Premium, coach assigned): onboarded → Today's Drop.
      - CANDIDATE (LuxID, no Premium): onboarded → catalogue status page;
        they appear in others' Drops but never receive their own.
    Routes:
      - flag off / not approved / neither track → teaser
      - eligible but not onboarded → onboarding (so they can opt in)
      - excluded by coach → teaser (silent — don't expose the exclusion)
    """
    if user.is_staff:
        return None

    if not getattr(settings, "CRUSH_CONNECT_LAUNCHED", False):
        return redirect("crush_lu:crush_connect_teaser")

    membership = getattr(user, "crush_connect_membership", None)
    if membership is not None and membership.excluded_by_coach:
        return redirect("crush_lu:crush_connect_teaser")

    # Not onboarded yet → the LuxID-first opt-in gate decides teaser vs. wizard.
    # (Onboarded members are grandfathered past the gate below.)
    if membership is None or membership.onboarded_at is None:
        if not _user_passes_pre_onboarding_gate(user):
            return redirect("crush_lu:crush_connect_teaser")
        return redirect("crush_lu:crush_connect_onboarding")

    if not _user_is_connect_receiver_eligible(user):
        # Candidate-only members don't get Drops — show their catalogue state.
        return redirect("crush_lu:crush_connect_catalogue_status")

    return None


def _hub_access_blocker(user):
    """Gate for the Crush Connect hub.

    Mirrors ``_connect_access_blocker`` but the hub is the shared landing for
    BOTH onboarded tracks, so it never bounces a candidate onward to the
    catalogue page — both receivers and candidates render the hub.
    """
    if user.is_staff:
        return None

    if not getattr(settings, "CRUSH_CONNECT_LAUNCHED", False):
        return redirect("crush_lu:crush_connect_teaser")

    membership = getattr(user, "crush_connect_membership", None)
    if membership is not None and membership.excluded_by_coach:
        return redirect("crush_lu:crush_connect_teaser")

    if membership is None or membership.onboarded_at is None:
        if not _user_passes_pre_onboarding_gate(user):
            return redirect("crush_lu:crush_connect_teaser")
        return redirect("crush_lu:crush_connect_onboarding")

    return None


def _next_drop_at(now=None):
    """
    Return the next time tomorrow's Drop becomes available — used by the
    home template to render a countdown line under empty/all-viewed states.
    Locked to 06:00 in the project's configured timezone so the rhythm feels
    like a morning ritual rather than an arbitrary midnight refresh.
    """
    now = now or timezone.localtime()
    tomorrow_six = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if now >= tomorrow_six:
        tomorrow_six = tomorrow_six + timedelta(days=1)
    return tomorrow_six


def _connect_done_url(user) -> str:
    """Where a finished member lands: receivers (Premium) → Today's Drop,
    candidates (LuxID-only) → catalogue status. Staff count as receivers so
    they can preview the Drop."""
    is_receiver = _user_is_connect_receiver_eligible(user) or user.is_staff
    return (
        "crush_lu:crush_connect_home"
        if is_receiver
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

    if not user.is_staff and not getattr(settings, "CRUSH_CONNECT_LAUNCHED", False):
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

    membership, _created = CrushConnectMembership.objects.get_or_create(user=user)
    return None, membership, done_url


@crush_login_required
def crush_connect_onboarding(request):
    """
    Smart-resume entry for the Crush Connect wizard. Routes the user to their
    current step. URL name unchanged so every existing redirect target still
    points here.
    """
    response, membership, _done = _onboarding_gate(request)
    if response is not None:
        return response
    return redirect(
        "crush_lu:crush_connect_onboarding_step",
        step=clamp_step(membership.onboarding_step),
    )


def _emit_onboarding_complete(request, done_url):
    """Success message (per track) + candidate welcome email. Ported from the
    legacy single-page view."""
    user = request.user
    is_receiver = _user_is_connect_receiver_eligible(user) or user.is_staff
    if is_receiver:
        messages.success(
            request, _("Welcome to Crush Connect — your first Drop is ready.")
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
    change. Best-effort — scoring is a soft signal for the Drop (missing pairs
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
                obj.save(update_fields=[
                    "onboarded_at", "onboarding_step", "onboarding_started_at",
                ])
                _emit_onboarding_complete(request, done_url)
                # Member is now in the pool — score them against other members so
                # their first Drop (and theirs in others') reflects their traits.
                _recompute_member_match_scores(request.user)
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

    is_receiver = _user_is_connect_receiver_eligible(request.user) or request.user.is_staff
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
        "is_candidate_only": not is_receiver,
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
    "story": "✍️",
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

    story = m.story_answer or ""

    return {
        "intention": intention,
        "lifestyle": lifestyle,
        "languages": languages,
        "life": life,
        "family": family,
        "ideal_match": ideal_match,
        "story": story,
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
    if not user.is_staff and not getattr(settings, "CRUSH_CONNECT_LAUNCHED", False):
        return redirect("crush_lu:crush_connect_teaser")

    membership = getattr(user, "crush_connect_membership", None)
    if membership is None or not membership.is_onboarded:
        # Not finished yet → into the wizard, not the editor.
        return redirect("crush_lu:crush_connect_onboarding")

    profile = getattr(user, "crushprofile", None)
    section_key = request.POST.get("section") or request.GET.get("section")
    active = step_for_key(section_key)

    def _build_form(cfg, data=None):
        # The story form drops its consent gate in edit mode.
        form_kwargs = {"for_edit": True} if cfg.key == "story" else {}
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
    Status page for candidate-track members (LuxID, no Premium): confirms
    they're in the catalogue, previews their Story card, and offers the
    Premium upgrade for receiving their own coach-curated matches.

    Premium receivers are forwarded to Today's Drop instead.
    """
    user = request.user

    if not user.is_staff and not getattr(settings, "CRUSH_CONNECT_LAUNCHED", False):
        return redirect("crush_lu:crush_connect_teaser")

    if _user_is_connect_receiver_eligible(user):
        return redirect("crush_lu:crush_connect_home")
    if not user.is_staff and not _user_is_connect_candidate_eligible(user):
        return redirect("crush_lu:crush_connect_teaser")

    membership = getattr(user, "crush_connect_membership", None)
    if membership is not None and membership.excluded_by_coach:
        return redirect("crush_lu:crush_connect_teaser")
    if membership is None or not membership.is_onboarded:
        return redirect("crush_lu:crush_connect_onboarding")

    from crush_lu.models import CuriositySpark

    pending_sparks_count = CuriositySpark.objects.filter(
        recipient=user, status="pending"
    ).count()

    return render(
        request,
        "crush_lu/crush_connect/catalogue_status.html",
        {
            "membership": membership,
            "pending_sparks_count": pending_sparks_count,
        },
    )


@crush_login_required
def crush_connect_hub(request):
    """Crush Connect home — the member's hub that aggregates every Connect
    surface (Today's Drop / catalogue, Sparks, Coach's Pick, profile) with
    quick links and status badges. Shared landing for both onboarded tracks;
    the dedicated nav menu and the mobile bottom-nav 'Connect' tab point here.
    """
    from crush_lu.models import CuriositySpark
    from crush_lu.services.crush_connect import get_active_coach_pick

    user = request.user
    blocker = _hub_access_blocker(user)
    if blocker is not None:
        return blocker

    is_receiver = _user_is_connect_receiver_eligible(user) or user.is_staff
    membership = getattr(user, "crush_connect_membership", None)
    coach = getattr(user, "crushcoach", None)

    pending_sparks_count = CuriositySpark.objects.filter(
        recipient=user, status="pending"
    ).count()
    # Coach pick replaces the Drop for receivers; surface it on the hub too.
    coach_pick = get_active_coach_pick(user) if is_receiver else None

    context = {
        "membership": membership,
        "track": "receiver" if is_receiver else "candidate",
        "is_receiver": is_receiver,
        "is_coach": bool(coach and coach.is_active),
        "pending_sparks_count": pending_sparks_count,
        "coach_pick": coach_pick,
        "next_drop_at": _next_drop_at(),
    }
    return render(request, "crush_lu/crush_connect/hub.html", context)


@crush_login_required
def crush_connect_home(request):
    """
    Render ``Today's Drop`` — the user-facing Crush Connect landing page.

    M4 deliberately does NOT include the Spark CTA on cards (that comes in M5).
    Cards are read-only; the page also renders an empty state when the
    eligible pool yielded zero candidates for the day.
    """
    user = request.user
    blocker = _connect_access_blocker(user)
    if blocker is not None:
        return blocker

    # Coach pick REPLACES the algorithmic Drop — the coach-curated match is
    # the product promise; the algorithm only fills in when no pick is open.
    from crush_lu.services.crush_connect import get_active_coach_pick

    coach_pick = get_active_coach_pick(user)

    # Don't create/persist an algorithmic Drop while a pick is open — drop
    # snapshots authorize Sparks, so hidden recipients would become
    # sparkable by id and bypass the "pick replaces the Drop" flow.
    drop = None
    recipients = []
    if not coach_pick:
        drop = get_or_create_daily_drop(user)
        recipients = list(
            drop.recipients.select_related(
                "crushprofile", "crush_connect_membership"
            ).all()
        )

    # Card CTA state: which of today's cards this user has already Sparked.
    from crush_lu.models import CuriositySpark

    sparked_ids = set(
        CuriositySpark.objects.filter(sender=user).values_list(
            "recipient_id", flat=True
        )
    )

    context = {
        "coach_pick": coach_pick,
        "drop": drop,
        "recipients": recipients,
        "sparked_ids": sparked_ids,
        "next_drop_at": _next_drop_at(),
        "is_staff_preview": user.is_staff
        and (
            not getattr(settings, "CRUSH_CONNECT_LAUNCHED", False)
            or not getattr(user, "crush_connect_membership", None)
            or not user.crush_connect_membership.is_onboarded
        ),
    }
    return render(request, "crush_lu/crush_connect/home.html", context)

# ---------------------------------------------------------------------------
# Curiosity Sparks (M5)
# ---------------------------------------------------------------------------


@crush_login_required
def crush_connect_spark_compose(request, user_id: int):
    """
    Compose-and-send a Curiosity Spark to someone from the sender's Drop.

    Only Drop receivers (Premium track) can send. The service enforces the
    cardinal rule: the target must actually have appeared in one of the
    sender's Drop snapshots.
    """
    from crush_lu.services.crush_connect import can_send_spark, send_spark

    user = request.user
    if not user.is_staff and not getattr(settings, "CRUSH_CONNECT_LAUNCHED", False):
        return redirect("crush_lu:crush_connect_teaser")

    target = get_object_or_404(
        User.objects.select_related("crushprofile", "crush_connect_membership"),
        pk=user_id,
    )

    allowed, reason = can_send_spark(user, target)
    if not allowed:
        if reason == "already_sparked":
            messages.info(
                request, _("You've already sent a Spark to this member.")
            )
            return redirect("crush_lu:crush_connect_home")
        if reason == "not_receiver":
            return redirect("crush_lu:crush_connect_teaser")
        # not_surfaced / recipient_unavailable — don't leak details
        messages.info(
            request, _("This member isn't available for a Spark right now.")
        )
        return redirect("crush_lu:crush_connect_home")

    if request.method == "POST":
        message = (request.POST.get("message") or "").strip()
        if len(message) > 200:
            messages.error(
                request, _("Keep your message under 200 characters.")
            )
        else:
            try:
                send_spark(user, target, message=message, request=request)
            except ValueError:
                messages.info(
                    request,
                    _("This member isn't available for a Spark right now."),
                )
                return redirect("crush_lu:crush_connect_home")
            messages.success(
                request,
                _("Spark sent. If they're curious too, your coach takes it from there."),
            )
            return redirect("crush_lu:crush_connect_home")

    return render(
        request,
        "crush_lu/crush_connect/spark_compose.html",
        {
            "target": target,
            "target_profile": getattr(target, "crushprofile", None),
            "target_membership": getattr(target, "crush_connect_membership", None),
        },
    )


@crush_login_required
def crush_connect_sparks_received(request):
    """
    The recipient's response surface — works for BOTH tracks.

    Candidates never receive Drops, so this page (reached from the bell or
    the notification email) is where they meet the Sparks sent to them.
    """
    from crush_lu.models import CuriositySpark
    from crush_lu.services.crush_connect import (
        is_catalogue_eligible,
        is_sender_eligible,
    )

    user = request.user
    if not user.is_staff and not getattr(settings, "CRUSH_CONNECT_LAUNCHED", False):
        return redirect("crush_lu:crush_connect_teaser")

    # Pending Sparks are immutable records — a recipient who lost catalogue
    # eligibility since they arrived (rejection, LuxID unlink, exclusion)
    # must not be able to view or act on them.
    if not user.is_staff and not is_catalogue_eligible(user):
        return redirect("crush_lu:crush_connect_teaser")

    sparks = (
        CuriositySpark.objects.filter(recipient=user, status="pending")
        .select_related(
            "sender__crushprofile", "sender__crush_connect_membership"
        )
        .order_by("-created_at")
    )
    # Hide Sparks whose sender lost eligibility since sending (rejection,
    # Premium loss, exclusion) — accepting them is a no-op anyway, so they
    # must not be offered.
    visible_sparks = [s for s in sparks if is_sender_eligible(s.sender)]
    return render(
        request,
        "crush_lu/crush_connect/sparks_received.html",
        {"sparks": visible_sparks},
    )


@crush_login_required
def crush_connect_spark_respond(request, spark_id: int):
    """Accept or decline a pending Spark (POST only, recipient only)."""
    from crush_lu.models import CuriositySpark
    from crush_lu.services.crush_connect import (
        is_catalogue_eligible,
        respond_to_spark,
    )

    if request.method != "POST":
        return redirect("crush_lu:crush_connect_sparks_received")

    if not is_catalogue_eligible(request.user):
        return redirect("crush_lu:crush_connect_teaser")

    spark = get_object_or_404(
        CuriositySpark.objects.select_related("sender", "recipient"),
        pk=spark_id,
        recipient=request.user,
    )
    accept = request.POST.get("action") == "accept"
    respond_to_spark(spark, accept=accept, request=request)
    if accept:
        messages.success(
            request,
            _("It's mutual! Your Crush Coach will be in touch to arrange your date."),
        )
    else:
        messages.info(request, _("Spark declined — they won't be told."))
    return redirect("crush_lu:crush_connect_sparks_received")

# ---------------------------------------------------------------------------
# Coach Picks (M7) — coach curation interface + member response
# ---------------------------------------------------------------------------

from crush_lu.decorators import coach_required


@coach_required
def coach_connect_members(request):
    """The coach's Crush Connect curation hub: their assigned Premium
    members with Connect status and current pick, ready to curate."""
    from crush_lu.models import ConnectCoachPick, CrushProfile

    coach = request.coach
    members = (
        User.objects.filter(crushprofile__assigned_coach=coach)
        .select_related("crushprofile", "crush_connect_membership")
        .order_by("first_name", "pk")
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
        User.objects.select_related("crushprofile", "crush_connect_membership"),
        pk=user_id,
        crushprofile__assigned_coach=coach,
    )

    if request.method == "POST":
        candidate = get_object_or_404(User, pk=request.POST.get("candidate_id"))
        note = (request.POST.get("note") or "").strip()[:300]
        try:
            propose_coach_pick(coach, member, candidate, note=note)
        except ValueError as exc:
            reasons = {
                "already_picked": _("You already proposed this candidate to them."),
                "candidate_not_eligible": _("This candidate isn't in their eligible pool."),
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
        )[:60]
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
        return redirect("crush_lu:crush_connect_home")

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
            _("This pick is no longer available — your coach will propose someone new."),
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
    return redirect("crush_lu:crush_connect_home")
