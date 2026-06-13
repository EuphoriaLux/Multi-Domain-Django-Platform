"""
Crush Connect views.

M3: a staff-only debug route that renders a single Drop card in isolation.
M4: the user-facing ``Today's Drop`` page.

The full user-facing surface (Today's Drop, Pending Sparks, journey reveal)
will continue to grow in this module across M5–M7.
"""

import json
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from crush_lu.decorators import crush_login_required
from crush_lu.email_helpers import send_crush_connect_catalogue_welcome
from crush_lu.forms_crush_connect import CrushConnectOnboardingForm
from crush_lu.models import CrushConnectMembership, CrushProfile, SparkPrompt, Trait
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
    """Either track may onboard: receivers (Premium) or candidates (LuxID)."""
    return _user_is_connect_receiver_eligible(
        user
    ) or _user_is_connect_candidate_eligible(user)


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

    if not _user_passes_pre_onboarding_gate(user):
        return redirect("crush_lu:crush_connect_teaser")

    membership = getattr(user, "crush_connect_membership", None)
    if membership is not None and membership.excluded_by_coach:
        return redirect("crush_lu:crush_connect_teaser")
    if membership is None or membership.onboarded_at is None:
        return redirect("crush_lu:crush_connect_onboarding")

    if not _user_is_connect_receiver_eligible(user):
        # Candidate-only members don't get Drops — show their catalogue state.
        return redirect("crush_lu:crush_connect_catalogue_status")

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


def _apply_connect_preferences(
    profile,
    *,
    genders=None,
    age_min=None,
    age_max=None,
    first_step=None,
    astro=None,
    quality_ids_raw=None,
):
    """
    Persist Crush Connect ideal-match (step 3) preferences onto ``CrushProfile``.

    Shared by the final onboarding submit (parsing ``request.POST``) and the
    per-step autosave endpoint (parsing JSON). Every argument is optional —
    ``None`` means "not provided, leave the stored value untouched" — so a
    partial step-3 payload only writes the fields it actually carries.
    """
    if profile is None:
        return

    update_fields = []

    if genders is not None:
        valid_genders = dict(CrushProfile.GENDER_CHOICES).keys()
        profile.preferred_genders = [g for g in genders if g in valid_genders]
        update_fields.append("preferred_genders")

    if age_min is not None or age_max is not None:
        try:
            lo = int(age_min) if age_min is not None else profile.preferred_age_min
            hi = int(age_max) if age_max is not None else profile.preferred_age_max
            if lo > hi:
                lo, hi = hi, lo
            profile.preferred_age_min = lo
            profile.preferred_age_max = hi
            update_fields += ["preferred_age_min", "preferred_age_max"]
        except (ValueError, TypeError):
            pass

    # Radio may be absent/empty when nothing was picked — keep the stored value.
    if first_step and first_step in dict(CrushProfile.FIRST_STEP_CHOICES):
        profile.first_step_preference = first_step
        update_fields.append("first_step_preference")

    if astro is not None:
        # The hidden astro toggle posts "true"/"false"; the JSON client may
        # also send a real boolean.
        profile.astro_enabled = astro is True or astro == "true"
        update_fields.append("astro_enabled")

    if update_fields:
        profile.save(update_fields=update_fields)

    if quality_ids_raw is not None:
        if isinstance(quality_ids_raw, (list, tuple)):
            ids = [int(i) for i in quality_ids_raw if str(i).strip().isdigit()]
        else:
            ids = [
                int(i) for i in str(quality_ids_raw).split(",") if i.strip().isdigit()
            ]
        profile.sought_qualities.set(
            Trait.objects.filter(pk__in=ids[:5], trait_type="quality")
        )


@crush_login_required
def crush_connect_onboarding(request):
    """
    One-page Crush Connect opt-in, shared by both tracks:
      - RECEIVER (Premium): finishing lands on Today's Drop.
      - CANDIDATE (LuxID, no Premium): finishing lands on the catalogue
        status page — they're discoverable, not receiving.

    Pre-conditions: approved profile + (Premium coach OR LuxID linked),
    flag on (or staff). If already onboarded, forward to the right page.
    """
    user = request.user

    if not user.is_staff and not getattr(settings, "CRUSH_CONNECT_LAUNCHED", False):
        return redirect("crush_lu:crush_connect_teaser")
    if not user.is_staff and not _user_passes_pre_onboarding_gate(user):
        return redirect("crush_lu:crush_connect_teaser")

    is_receiver = _user_is_connect_receiver_eligible(user) or user.is_staff
    done_url = (
        "crush_lu:crush_connect_home"
        if is_receiver
        else "crush_lu:crush_connect_catalogue_status"
    )

    membership, _created = CrushConnectMembership.objects.get_or_create(user=user)
    if membership.excluded_by_coach:
        return redirect("crush_lu:crush_connect_teaser")
    if membership.is_onboarded:
        return redirect(done_url)

    profile = getattr(user, "crushprofile", None)

    if request.method == "POST":
        form = CrushConnectOnboardingForm(request.POST, instance=membership)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.onboarded_at = timezone.now()
            obj.save()

            # Persist ideal-match preferences back onto CrushProfile —
            # everything _preferences_fields.html posts, not just a subset.
            # Same writer the per-step autosave endpoint uses (single source).
            _apply_connect_preferences(
                profile,
                genders=request.POST.getlist("preferred_genders"),
                age_min=request.POST.get("preferred_age_min", 18),
                age_max=request.POST.get("preferred_age_max", 99),
                first_step=request.POST.get("first_step_preference", ""),
                astro=request.POST.get("astro_enabled"),
                quality_ids_raw=request.POST.get("sought_qualities_ids", ""),
            )

            if is_receiver:
                messages.success(
                    request,
                    _("Welcome to Crush Connect — your first Drop is ready."),
                )
            else:
                messages.success(
                    request,
                    _(
                        "Welcome to Crush Connect — you're now in the "
                        "catalogue and can be matched by a Crush Coach."
                    ),
                )
                send_crush_connect_catalogue_welcome(user, request)
            return redirect(done_url)
    else:
        form = CrushConnectOnboardingForm(instance=membership)

    # Build rich context for the 4-step wizard (Step 3: Ideal Match).
    # _render_preferences_section also returns "form" (IdealCrushPreferencesForm)
    # and "profile" — we drop those to avoid overwriting our onboarding form.
    from crush_lu.views import _render_preferences_section
    prefs_ctx = {}
    if profile is not None:
        raw = _render_preferences_section(request, profile)
        prefs_ctx = {k: v for k, v in raw.items() if k not in ("form", "profile", "section")}

    # Resume support: which step to land on, and whether there's saved progress
    # worth announcing with a "welcome back" banner.
    has_saved_progress = (
        membership.draft_step > 1
        or bool(membership.relationship_goal)
        or bool(membership.lifestyle_energy)
        or bool(membership.lifestyle_social)
        or bool(membership.lifestyle_pace)
        or bool(membership.story_answer)
        or membership.story_prompt_id is not None
    )

    return render(
        request,
        "crush_lu/crush_connect/onboarding.html",
        {
            "form": form,
            "membership": membership,
            "is_candidate_only": not is_receiver,
            "profile": profile,
            "draft_step": membership.draft_step,
            "has_saved_progress": has_saved_progress,
            "last_saved": membership.updated_at,
            **prefs_ctx,
        },
    )


def _set_story_prompt(membership, field, raw, touched_fields):
    """
    Set a story-prompt FK (``story_prompt`` / ``story_prompt_2``) from a raw id
    during autosave. ``""`` clears it; an unknown id is ignored so a stray value
    never raises an FK integrity error. ``field`` (not its ``_id`` attname) is
    appended to ``touched_fields`` for ``update_fields``.
    """
    if raw is None:
        return
    field_id = f"{field}_id"
    if raw == "":
        setattr(membership, field_id, None)
        touched_fields.append(field)
        return
    try:
        pk = int(raw)
    except (TypeError, ValueError):
        return
    if SparkPrompt.objects.filter(pk=pk).exists():
        setattr(membership, field_id, pk)
        touched_fields.append(field)


@crush_login_required
@require_http_methods(["POST"])
def crush_connect_onboarding_autosave(request):
    """
    Per-step autosave for the Crush Connect onboarding wizard.

    Each selection (and debounced text input) POSTs the active step's data here
    so an abandoned wizard resumes exactly where the user left off. Writes land
    directly on the live ``CrushConnectMembership`` / ``CrushProfile`` fields,
    but ``onboarded_at`` is *never* set here — completion only happens on the
    final form submit, so a half-finished member stays out of every pool.

    Request JSON:  ``{"step": 1, "data": {"relationship_goal": "serious"}}``
    Response JSON: ``{"success": true, "saved_at": "2026-06-13T08:42:10Z"}``
    """
    user = request.user
    membership, _created = CrushConnectMembership.objects.get_or_create(user=user)

    # Never mutate a completed or coach-excluded membership — no-op so the
    # client's pill still flips to "saved" without surfacing the exclusion.
    if membership.excluded_by_coach or membership.is_onboarded:
        return JsonResponse(
            {"success": True, "saved_at": membership.updated_at.isoformat()}
        )

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)

    try:
        step = int(payload.get("step", 0))
    except (TypeError, ValueError):
        step = 0
    if step < 1 or step > 4:
        return JsonResponse(
            {"success": False, "error": "Invalid step (1-4)."}, status=400
        )

    data = payload.get("data")
    if not isinstance(data, dict):
        data = {}

    # Re-fetch under a row lock and re-check completion inside the transaction.
    # The early check above can go stale: a final submit may land while a
    # debounced step-4 autosave is in flight, and that stale draft must not
    # clobber the finished Story / onboarded state. Under the lock a concurrent
    # final submit either already committed (we observe is_onboarded and skip)
    # or blocks on the same row until we're done — so the final answer always
    # wins. The submit writes onboarded_at before its CrushProfile prefs, so
    # holding this lock guards the step-3 profile writes too.
    with transaction.atomic():
        membership = CrushConnectMembership.objects.select_for_update().get(
            pk=membership.pk
        )
        if membership.excluded_by_coach or membership.is_onboarded:
            return JsonResponse(
                {"success": True, "saved_at": membership.updated_at.isoformat()}
            )

        touched = ["draft_step", "updated_at"]

        if step == 1:
            goal = data.get("relationship_goal", "")
            if goal in dict(CrushConnectMembership.RELATIONSHIP_GOAL_CHOICES):
                membership.relationship_goal = goal
                touched.append("relationship_goal")

        elif step == 2:
            axes = {
                "lifestyle_energy": CrushConnectMembership.LIFESTYLE_ENERGY_CHOICES,
                "lifestyle_social": CrushConnectMembership.LIFESTYLE_SOCIAL_CHOICES,
                "lifestyle_pace": CrushConnectMembership.LIFESTYLE_PACE_CHOICES,
            }
            for field, choices in axes.items():
                value = data.get(field, "")
                if value in dict(choices):
                    setattr(membership, field, value)
                    touched.append(field)

        elif step == 3:
            # Step-3 preferences live on CrushProfile; reuse the shared writer.
            _apply_connect_preferences(
                getattr(user, "crushprofile", None),
                genders=data.get("preferred_genders"),
                age_min=data.get("preferred_age_min"),
                age_max=data.get("preferred_age_max"),
                first_step=data.get("first_step_preference"),
                astro=data.get("astro_enabled"),
                quality_ids_raw=data.get("sought_qualities_ids"),
            )

        elif step == 4:
            _set_story_prompt(
                membership, "story_prompt", data.get("story_prompt"), touched
            )
            _set_story_prompt(
                membership, "story_prompt_2", data.get("story_prompt_2"), touched
            )
            if "story_answer" in data:
                membership.story_answer = str(data.get("story_answer") or "")[:200]
                touched.append("story_answer")
            if "story_answer_2" in data:
                membership.story_answer_2 = str(data.get("story_answer_2") or "")[:200]
                touched.append("story_answer_2")

        # Advance the resume cursor monotonically. Two autosaves can be in
        # flight at once (e.g. a step-1 select POST racing the step-2 Next
        # POST); if the older one committed last, a plain assignment would drag
        # draft_step backward and resume the user on an earlier step. Reading
        # max() under the select_for_update lock above makes the cursor
        # never-decreasing without needing a client revision/timestamp.
        membership.draft_step = max(membership.draft_step, step)
        membership.save(update_fields=list(dict.fromkeys(touched)))

    return JsonResponse(
        {"success": True, "saved_at": membership.updated_at.isoformat()}
    )


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
