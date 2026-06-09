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

from crush_lu.decorators import crush_login_required
from crush_lu.email_helpers import send_crush_connect_catalogue_welcome
from crush_lu.forms_crush_connect import CrushConnectOnboardingForm
from crush_lu.models import CrushConnectMembership
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

    if request.method == "POST":
        form = CrushConnectOnboardingForm(request.POST, instance=membership)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.onboarded_at = timezone.now()
            obj.save()
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

    return render(
        request,
        "crush_lu/crush_connect/onboarding.html",
        {
            "form": form,
            "membership": membership,
            "is_candidate_only": not is_receiver,
        },
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
    picks = {
        p.member_id: p
        for p in ConnectCoachPick.objects.filter(
            coach=coach, status__in=["proposed", "accepted"]
        ).select_related("candidate")
    }
    rows = []
    for m in members:
        membership = getattr(m, "crush_connect_membership", None)
        rows.append(
            {
                "member": m,
                "onboarded": bool(membership and membership.is_onboarded),
                "pick": picks.get(m.pk),
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
    respond_to_coach_pick(pick, accept=accept)
    if accept:
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
