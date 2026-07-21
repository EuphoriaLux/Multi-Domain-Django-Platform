from datetime import timedelta

from django.db.models import Max
from django.shortcuts import render, redirect
from django.utils import timezone
from django.contrib.admin.views.decorators import staff_member_required
from .models import MeetupEvent
from .models.crush_connect import CrushConnectWaitlist
from .models.events import EventRegistration


@staff_member_required
def membership_concept_preview(request):
    """
    Staff-only concept preview of the future membership segmentation.

    Visualises the verification ladder + the LuxID / Coach / Legacy trust
    matrix, with LIVE counts derived from current data so the concept can be
    iterated against reality. The `verification_method` split is *derived*
    here (LuxID = has a luxid/openid_connect social account) because the
    dedicated field is not built yet — see memory `verification-method-planned`.
    """
    from django.db.models import Exists, OuterRef
    from allauth.socialaccount.models import SocialAccount
    from .models import CrushProfile

    active = CrushProfile.objects.filter(is_active=True)

    luxid_sub = SocialAccount.objects.filter(
        user=OuterRef("user"), provider__in=["luxid", "openid_connect"]
    )
    attended_sub = EventRegistration.objects.filter(
        user=OuterRef("user"), status="attended"
    )

    verified = active.filter(verification_status="verified").annotate(
        _has_luxid=Exists(luxid_sub),
        _has_attended=Exists(attended_sub),
    )

    total_active = active.count()
    n_incomplete = active.filter(verification_status="incomplete").count()
    n_pending = active.filter(verification_status="pending").count()
    n_rejected = active.filter(verification_status="rejected").count()
    n_verified = verified.count()

    # Derived verification-method split
    n_luxid = verified.filter(_has_luxid=True).count()
    n_coach = verified.filter(
        _has_luxid=False, assigned_coach__isnull=False
    ).count()
    n_legacy = n_verified - n_luxid - n_coach  # verified, no luxid, no coach

    # Community + premium unlocks
    n_community = verified.filter(_has_attended=True).count()
    n_premium = (
        verified.filter(user__premium_memberships__status="active")
        .distinct()
        .count()
    )

    def pct(n):
        return round(n / total_active * 100, 1) if total_active else 0

    ladder = [
        {"key": "incomplete", "label": "Incomplete", "n": n_incomplete, "pct": pct(n_incomplete),
         "desc": "Browsing only — finishing profile", "tone": "gray"},
        {"key": "pending", "label": "Pending", "n": n_pending, "pct": pct(n_pending),
         "desc": "Submitted, awaiting verification", "tone": "amber"},
        {"key": "verified", "label": "Verified", "n": n_verified, "pct": pct(n_verified),
         "desc": "Full access — can browse & buy event tickets", "tone": "green"},
        {"key": "attended", "label": "Attended an event", "n": n_community, "pct": pct(n_community),
         "desc": "Came to ≥1 event (Standard or Premium) — met people in person", "tone": "teal"},
        {"key": "premium", "label": "Premium · Crush Connect", "n": n_premium, "pct": pct(n_premium),
         "desc": "Personal coach assigned → Crush Connect + coach-only events unlocked", "tone": "purple"},
        {"key": "rejected", "label": "Rejected", "n": n_rejected, "pct": pct(n_rejected),
         "desc": "Blocked — support path", "tone": "red"},
    ]

    methods = [
        {"key": "luxid", "label": "LuxID-verified", "n": n_luxid, "pct": pct(n_luxid),
         "eligibility": "ALL event tiers", "icon": "shield"},
        {"key": "coach", "label": "Coach-verified", "n": n_coach, "pct": pct(n_coach),
         "eligibility": "Standard + coach events", "icon": "user"},
        {"key": "legacy", "label": "Legacy-verified", "n": n_legacy, "pct": pct(n_legacy),
         "eligibility": "Standard + coach events", "icon": "clock"},
    ]

    # Proposed premium perks bundle (concept — iterate freely)
    perks = [
        {"name": "Reserved event seats", "icon": "shared/icons/star.html",
         "status": "building", "effort": "medium",
         "why": "Your coach saves you a seat — join even when an event is sold out."},
        {"name": "Premium-only events", "icon": "shared/icons/lock-closed.html",
         "status": "live", "effort": "shipped",
         "why": "Exclusive events gated to members with a coach (coach_assigned tier)."},
        {"name": "Early access window", "icon": "shared/icons/clock.html",
         "status": "planned", "effort": "small",
         "why": "See & book new events 24–48h before they open to everyone."},
        {"name": "See who liked you", "icon": "shared/icons/heart.html",
         "status": "planned", "effort": "medium",
         "why": "The universal dating-app converter — reveal interest instantly."},
        {"name": "Premium badge", "icon": "shared/icons/shield-check.html",
         "status": "planned", "effort": "small",
         "why": "Status + trust signal; pairs with the planned verification_method badges."},
        {"name": "Pre-event briefing & debrief", "icon": "shared/icons/chat-bubble-left-right.html",
         "status": "planned", "effort": "small",
         "why": "Coach flags who to look for, then gives feedback afterward."},
        {"name": "+1 guest pass", "icon": "shared/icons/user-plus.html",
         "status": "planned", "effort": "medium",
         "why": "Bring a friend — built-in viral growth loop."},
    ]

    context = {
        "ladder": ladder,
        "methods": methods,
        "perks": perks,
        "total_active": total_active,
        "n_verified": n_verified,
        "n_community": n_community,
        "n_premium": n_premium,
    }
    return render(request, "crush_lu/dev/membership_concept.html", context)


def home(request):
    """Landing page - redirects authenticated users to dashboard"""
    if request.user.is_authenticated:
        return redirect("crush_lu:dashboard")

    now = timezone.now()
    # Include events that are still in progress ("live"), not just future ones.
    # Fetch with an ORM cutoff, then keep any that haven't ended yet using the
    # model's end_time property in Python (timedelta * F() is unsupported on
    # SQLite — see views_events.event_list). The cutoff is derived from the
    # longest configured duration so an in-progress event is never dropped, even
    # if it runs longer than a day (duration_minutes is unbounded).
    published = MeetupEvent.objects.filter(is_published=True, is_cancelled=False)
    longest_minutes = published.aggregate(m=Max("duration_minutes"))["m"] or 0
    cutoff = now - timedelta(minutes=longest_minutes)
    upcoming_events = [
        e
        for e in published.filter(date_time__gte=cutoff).order_by("date_time")
        if e.end_time >= now
    ][:3]

    context = {
        "upcoming_events": upcoming_events,
    }
    return render(request, "crush_lu/home.html", context)


def test_upstair(request):
    """Test page for upstair event poster - remove after verification"""
    return render(request, "crush_lu/test_upstair.html")


def about(request):
    """About page"""
    return render(request, "crush_lu/about.html")


def how_it_works(request):
    """How it works page"""
    return render(request, "crush_lu/how_it_works.html")


def privacy_policy(request):
    """Privacy policy page"""
    return render(request, "crush_lu/privacy_policy.html")


def terms_of_service(request):
    """Terms of service page"""
    return render(request, "crush_lu/terms_of_service.html")


def support(request):
    """Support page for App Store metadata and member help."""
    return render(request, "crush_lu/support.html")


def data_deletion_request(request):
    """Data deletion instructions page"""
    return render(request, "crush_lu/data_deletion.html")


def child_safety_standards(request):
    """Child safety standards page required by Google Play / app stores for apps
    in the Dating and Social categories (CSAE policy compliance)."""
    return render(request, "crush_lu/child_safety_standards.html")


def crush_coach(request):
    """Crush Coach recruitment landing page"""
    return render(request, "crush_lu/crush_coach.html")


def crush_connect_teaser(request):
    """Crush Connect teaser page with waitlist."""
    from crush_lu.connect_phase import candidate_access_open, receiver_access_open

    # Fast-path: if the candidate track is open (full launch OR beta) and the
    # visitor can actually use it, send them past the teaser. Two tracks
    # (asymmetric model): Premium receivers land on Today's Drop, LuxID
    # candidates on the catalogue status page; either onboards first if needed.
    # Staff are never auto-redirected so they can still preview the teaser itself.
    if (
        request.user.is_authenticated
        and not request.user.is_staff
        and candidate_access_open()
    ):
        profile = getattr(request.user, "crushprofile", None)
        if profile and profile.is_approved:
            membership = getattr(request.user, "crush_connect_membership", None)
            excluded = membership and membership.excluded_by_coach
            if not excluded:
                onboarded = bool(membership and membership.is_onboarded)
                if (
                    onboarded
                    and profile.has_active_premium
                    and receiver_access_open(request.user)
                ):
                    # Onboarded Premium receiver → their Drop (grandfathered;
                    # receiving Drops doesn't require LuxID). During the beta this
                    # also requires being a selected tester; other Premium members
                    # fall through to the candidate catalogue below.
                    return redirect("crush_lu:crush_connect_home")
                if onboarded and profile.has_luxid_connected:
                    # Onboarded candidate (LuxID) → catalogue status.
                    return redirect("crush_lu:crush_connect_catalogue_status")
                if not onboarded and profile.has_luxid_connected:
                    # Eligible to opt in (LuxID-first) → into the wizard.
                    return redirect("crush_lu:crush_connect_onboarding")
                # Otherwise — a Premium/candidate member WITHOUT LuxID, or an
                # onboarded member who unlinked it — fall through and render the
                # teaser so they see the "Connect LuxID" CTA. Never redirect
                # them, or they'd loop teaser ⇄ onboarding against the gate.

    context = {
        "on_waitlist": False,
        "waitlist_position": None,
        "total_waitlist": CrushConnectWaitlist.objects.count(),
        "is_eligible": False,
        "profile_approved": False,
        "is_premium": False,
        "has_luxid_connected": False,
        "luxid_connect_url": None,
        "selected_as_tester": False,
        "tester_payment_confirmed": False,
    }

    if request.user.is_authenticated:
        try:
            entry = CrushConnectWaitlist.objects.get(user=request.user)
            context["on_waitlist"] = True
            context["waitlist_position"] = entry.waitlist_position
            context["is_eligible"] = entry.is_eligible
            context["selected_as_tester"] = entry.selected_as_tester
            context["tester_payment_confirmed"] = entry.payment_confirmed
        except CrushConnectWaitlist.DoesNotExist:
            pass

        _profile = getattr(request.user, "crushprofile", None)
        context["profile_approved"] = bool(_profile and _profile.is_approved)
        context["is_premium"] = bool(_profile and _profile.has_active_premium)
        context["has_luxid_connected"] = bool(
            _profile and _profile.has_luxid_connected
        )
        # LuxID is the entry requirement for opting into Crush Connect — offer a
        # "Connect LuxID" CTA to approved members who haven't linked it yet.
        if context["profile_approved"] and not context["has_luxid_connected"]:
            from crush_lu.luxid import get_luxid_connect_url

            context["luxid_connect_url"] = get_luxid_connect_url(request)

    return render(request, "crush_lu/crush_connect.html", context)
