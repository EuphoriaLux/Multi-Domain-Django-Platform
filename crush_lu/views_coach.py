from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.paginator import Paginator
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.db import transaction
from django.db.models import (
    Q,
    Count,
    Case,
    When,
    Value,
    IntegerField,
    Avg,
    F,
    ExpressionWrapper,
    DurationField,
)
from django.db.models import prefetch_related_objects
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods
import json
import logging

logger = logging.getLogger(__name__)

from .models import (
    CrushProfile,
    CrushCoach,
    ProfileSubmission,
    MeetupEvent,
    EventRegistration,
    CoachSession,
    EventConnection,
    CallAttempt,
    UserActivity,
    CrushSpark,
)
from .matching import (
    get_western_zodiac,
    get_western_element,
    get_chinese_zodiac,
    ZODIAC_SIGN_LABELS,
    ZODIAC_SIGN_EMOJIS,
    CHINESE_ANIMAL_LABELS,
    CHINESE_ANIMAL_EMOJIS,
)
from .forms import (
    CrushCoachForm,
    ProfileReviewForm,
    CoachSettingsForm,
    _validate_availability_window,
    _windows_overlap,
)
from .decorators import coach_required
from .notification_service import (
    notify_profile_approved,
    notify_profile_revision,
    notify_profile_rejected,
)
from .referrals import check_and_apply_profile_approved_reward


# Coach views
@coach_required
def coach_dashboard(request):
    """Coach dashboard - analytics and statistics hub"""
    from datetime import date

    coach = request.coach
    now = timezone.now()

    # --- Row 1: Summary stat cards ---
    approved_profiles = CrushProfile.objects.filter(verification_status="verified")
    total_approved = approved_profiles.count()

    pending_reviews = ProfileSubmission.objects.filter(
        coach=coach, status="pending"
    ).count()

    coach_stats = ProfileSubmission.objects.filter(coach=coach).aggregate(
        total_approved=Count("id", filter=Q(status="approved")),
        total_rejected=Count("id", filter=Q(status="rejected")),
        total_revision=Count("id", filter=Q(status="revision")),
        total_recontact=Count("id", filter=Q(status="recontact_coach")),
    )
    your_total_reviews = (
        coach_stats["total_approved"]
        + coach_stats["total_rejected"]
        + coach_stats["total_revision"]
        + coach_stats["total_recontact"]
    )

    pending_connections_count = EventConnection.objects.filter(
        status__in=["accepted", "coach_reviewing"]
    ).count()

    # --- Row 2: Demographics ---
    # Gender breakdown
    gender_data = approved_profiles.values("gender").annotate(count=Count("id"))
    gender_counts = {"F": 0, "M": 0, "other": 0}
    for item in gender_data:
        if item["gender"] == "F":
            gender_counts["F"] = item["count"]
        elif item["gender"] == "M":
            gender_counts["M"] = item["count"]
        else:
            gender_counts["other"] += item["count"]
    gender_total = sum(gender_counts.values()) or 1

    gender_bars = []
    for key, label, color in [
        ("F", _("Women"), "bg-pink-500"),
        ("M", _("Men"), "bg-blue-500"),
        ("other", _("Other"), "bg-purple-500"),
    ]:
        count = gender_counts[key]
        pct = round(count * 100 / gender_total) if gender_total else 0
        gender_bars.append({"label": label, "count": count, "pct": pct, "color": color})

    # Age distribution (with gender breakdown)
    today = date.today()
    dob_gender_list = list(
        approved_profiles.exclude(date_of_birth__isnull=True).values_list(
            "date_of_birth", "gender"
        )
    )
    # 5-year age bands with only 60+ grouped together (see issue #190).
    age_buckets = [
        {
            "label": label,
            "min": lo,
            "max": hi,
            "count": 0,
            "count_f": 0,
            "count_m": 0,
            "count_other": 0,
        }
        for label, lo, hi in [
            ("18-24", 18, 24),
            ("25-29", 25, 29),
            ("30-34", 30, 34),
            ("35-39", 35, 39),
            ("40-44", 40, 44),
            ("45-49", 45, 49),
            ("50-54", 50, 54),
            ("55-59", 55, 59),
            ("60+", 60, 999),
        ]
    ]
    for dob, gender in dob_gender_list:
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        for bucket in age_buckets:
            if bucket["min"] <= age <= bucket["max"]:
                bucket["count"] += 1
                if gender == "F":
                    bucket["count_f"] += 1
                elif gender == "M":
                    bucket["count_m"] += 1
                else:
                    bucket["count_other"] += 1
                break
    max_age_count = max((b["count"] for b in age_buckets), default=1) or 1
    for bucket in age_buckets:
        bucket["pct"] = round(bucket["count"] * 100 / max_age_count)
        if bucket["count"] > 0:
            bucket["pct_f"] = round(bucket["count_f"] * 100 / bucket["count"])
            bucket["pct_m"] = round(bucket["count_m"] * 100 / bucket["count"])
            bucket["pct_other"] = 100 - bucket["pct_f"] - bucket["pct_m"]
        else:
            bucket["pct_f"] = bucket["pct_m"] = bucket["pct_other"] = 0

    # Event language distribution (approved profiles)
    lang_label_map = dict(CrushProfile.EVENT_LANGUAGE_CHOICES)
    lang_flags = {"en": "🇬🇧", "de": "🇩🇪", "fr": "🇫🇷", "lu": "🇱🇺"}
    lang_counts = {}
    for langs in approved_profiles.exclude(event_languages=[]).values_list(
        "event_languages", flat=True
    ):
        if isinstance(langs, list):
            for code in langs:
                lang_counts[code] = lang_counts.get(code, 0) + 1
    lang_total = sum(lang_counts.values()) or 1
    language_stats = sorted(
        [
            {
                "code": code,
                "label": f"{lang_flags.get(code, '')} {lang_label_map.get(code, code)}",
                "count": count,
                "pct": round(count * 100 / lang_total),
            }
            for code, count in lang_counts.items()
        ],
        key=lambda x: x["count"],
        reverse=True,
    )

    # Gender x Event Language matrix
    lang_codes = [code for code, _ in CrushProfile.EVENT_LANGUAGE_CHOICES]
    lang_labels = [
        f"{lang_flags.get(code, '')} {label}"
        for code, label in CrushProfile.EVENT_LANGUAGE_CHOICES
    ]
    gender_lang_matrix = []
    for gender_code, gender_label, _color in [
        ("F", _("Women"), "bg-pink-500"),
        ("M", _("Men"), "bg-blue-500"),
        ("other", _("Other"), "bg-purple-500"),
    ]:
        row = {"label": gender_label, "cells": [], "total": 0}
        if gender_code == "other":
            qs = approved_profiles.exclude(gender__in=["F", "M"]).exclude(
                event_languages=[]
            )
        else:
            qs = approved_profiles.filter(gender=gender_code).exclude(
                event_languages=[]
            )
        gender_lang_counts = {}
        for langs in qs.values_list("event_languages", flat=True):
            if isinstance(langs, list):
                for code in langs:
                    gender_lang_counts[code] = gender_lang_counts.get(code, 0) + 1
        for code in lang_codes:
            count = gender_lang_counts.get(code, 0)
            row["cells"].append(count)
            row["total"] += count
        gender_lang_matrix.append(row)
    gender_lang_col_totals = [
        sum(row["cells"][i] for row in gender_lang_matrix)
        for i in range(len(lang_codes))
    ]

    # Age x Event Language matrix
    age_lang_matrix = [
        {"label": bucket["label"], "cells": [0] * len(lang_codes), "total": 0}
        for bucket in age_buckets
    ]
    for dob, _gender, langs in (
        approved_profiles.exclude(date_of_birth__isnull=True)
        .exclude(event_languages=[])
        .values_list("date_of_birth", "gender", "event_languages")
    ):
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        if isinstance(langs, list):
            for idx, bucket in enumerate(age_buckets):
                if bucket["min"] <= age <= bucket["max"]:
                    for code in langs:
                        if code in lang_codes:
                            age_lang_matrix[idx]["cells"][lang_codes.index(code)] += 1
                            age_lang_matrix[idx]["total"] += 1
                    break
    age_lang_col_totals = [
        sum(row["cells"][i] for row in age_lang_matrix) for i in range(len(lang_codes))
    ]

    # --- Row 2.5: Ideal Crush Preferences ---
    from .analytics import get_preference_stats

    pref_stats = get_preference_stats(approved_profiles)

    # --- Row 3: Membership tier distribution ---
    tier_data = approved_profiles.values("membership_tier").annotate(count=Count("id"))
    tier_map = {item["membership_tier"]: item["count"] for item in tier_data}
    tier_cards = [
        {
            "label": _("Basic"),
            "key": "basic",
            "count": tier_map.get("basic", 0),
            "color": "bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-200",
        },
        {
            "label": _("Bronze"),
            "key": "bronze",
            "count": tier_map.get("bronze", 0),
            "color": "bg-amber-100 dark:bg-amber-900/30 text-amber-800 dark:text-amber-200",
        },
        {
            "label": _("Silver"),
            "key": "silver",
            "count": tier_map.get("silver", 0),
            "color": "bg-slate-200 dark:bg-slate-700 text-slate-800 dark:text-slate-200",
        },
        {
            "label": _("Gold"),
            "key": "gold",
            "count": tier_map.get("gold", 0),
            "color": "bg-yellow-100 dark:bg-yellow-900/30 text-yellow-800 dark:text-yellow-200",
        },
    ]

    # --- Row 4: Quick actions ---
    from .models import CrushSpark

    pending_sparks_count = CrushSpark.objects.filter(status="pending_review").count()

    # Count of approved members for this coach
    approved_members_count = (
        ProfileSubmission.objects.filter(coach=coach, status="approved")
        .values("profile")
        .distinct()
        .count()
    )

    # Count of strong match pairs for this coach's profiles
    from .matching import THRESHOLD_GOOD
    from .models import MatchScore

    my_user_ids = set(
        ProfileSubmission.objects.filter(coach=coach, status="approved")
        .values_list("profile__user_id", flat=True)
        .distinct()
    )
    # Count how many of this coach's profiles have at least one strong match
    # (matches the deduplicated view on the match-pairs page)
    if my_user_ids:
        matched_ids = set()
        for ms in MatchScore.objects.filter(
            Q(user_a_id__in=my_user_ids) | Q(user_b_id__in=my_user_ids),
            score_final__gte=THRESHOLD_GOOD,
            user_a__crushprofile__verification_status="verified",
            user_a__crushprofile__is_active=True,
            user_b__crushprofile__verification_status="verified",
            user_b__crushprofile__is_active=True,
        ).values_list("user_a_id", "user_b_id"):
            if ms[0] in my_user_ids:
                matched_ids.add(ms[0])
            if ms[1] in my_user_ids:
                matched_ids.add(ms[1])
        match_pairs_count = len(matched_ids)
    else:
        match_pairs_count = 0

    context = {
        "coach": coach,
        "total_approved": total_approved,
        "pending_reviews": pending_reviews,
        "your_total_reviews": your_total_reviews,
        "pending_connections_count": pending_connections_count,
        "gender_bars": gender_bars,
        "age_buckets": age_buckets,
        "tier_cards": tier_cards,
        "coach_stats": coach_stats,
        "pending_sparks_count": pending_sparks_count,
        "pref_stats": pref_stats,
        "approved_members_count": approved_members_count,
        "match_pairs_count": match_pairs_count,
        "language_stats": language_stats,
        "lang_labels": lang_labels,
        "gender_lang_matrix": gender_lang_matrix,
        "gender_lang_col_totals": gender_lang_col_totals,
        "age_lang_matrix": age_lang_matrix,
        "age_lang_col_totals": age_lang_col_totals,
    }
    # Hybrid Review: upcoming booked screening calls for this coach.
    from .models import ScreeningSlot

    upcoming_screening_slots = (
        ScreeningSlot.objects.filter(coach=coach, status="booked", start_at__gte=now)
        .select_related("submission__profile__user")
        .order_by("start_at")[:5]
    )
    context["upcoming_screening_slots"] = upcoming_screening_slots

    # Pending submissions for this coach, sorted by SLA urgency (most urgent first).
    # Uses ProfileSubmission.sla_state ('ok'|'warning'|'urgent'|'breach'|'escalated').
    pending_submissions_qs = (
        ProfileSubmission.objects.filter(coach=coach, status="pending")
        .select_related("profile__user")
        .order_by("sla_deadline", "submitted_at")
    )
    state_priority = {
        "escalated": 0,
        "breach": 1,
        "urgent": 2,
        "warning": 3,
        "ok": 4,
    }
    pending_submissions = []
    for sub in pending_submissions_qs:
        state = sub.sla_state
        hours_remaining = None
        if sub.sla_deadline and state not in ("breach", "escalated"):
            hours_remaining = max(
                0, int((sub.sla_deadline - now).total_seconds() // 3600)
            )
        pending_submissions.append(
            {
                "submission": sub,
                "sla_state": state,
                "hours_remaining": hours_remaining,
                "_priority": state_priority.get(state, 5),
            }
        )
    pending_submissions.sort(key=lambda r: (r["_priority"], r["submission"].submitted_at))
    context["pending_submissions"] = pending_submissions
    context["pending_sla_breach_count"] = sum(
        1 for r in pending_submissions if r["sla_state"] in ("breach", "escalated")
    )

    return render(request, "crush_lu/coach_dashboard.html", context)


@coach_required
def coach_action_queue(request):
    """
    Unified coach inbox: pending profile reviews + upcoming screening calls
    + connections awaiting coach review, all sorted by urgency.

    Each entry has a `kind` (profile / screening / connection), a `deadline`
    used for sorting, and a `priority` bucket for visual treatment.
    """
    from .models import ScreeningSlot

    coach = request.coach
    now = timezone.now()
    items = []

    state_priority = {
        "escalated": 0,
        "breach": 1,
        "urgent": 2,
        "warning": 3,
        "ok": 4,
    }

    # --- Profile reviews ---
    pending_subs = (
        ProfileSubmission.objects.filter(coach=coach, status="pending")
        .select_related("profile__user")
    )
    for sub in pending_subs:
        state = sub.sla_state
        items.append(
            {
                "kind": "profile",
                "title": sub.profile.user.get_full_name() or sub.profile.user.username,
                "subtitle": _("Profile review"),
                "deadline": sub.sla_deadline,
                "sla_state": state,
                "priority": state_priority.get(state, 5),
                "url_name": "crush_lu:coach_review_profile",
                "url_kwargs": {"submission_id": sub.id},
                "submitted_at": sub.submitted_at,
            }
        )

    # --- Upcoming screening calls ---
    booked_slots = (
        ScreeningSlot.objects.filter(coach=coach, status="booked", start_at__gte=now)
        .select_related("submission__profile__user")
        .order_by("start_at")
    )
    for slot in booked_slots:
        sub = slot.submission
        if not sub:
            continue
        # Hours until call: <2h => urgent, <24h => warning, else ok
        delta_hours = (slot.start_at - now).total_seconds() / 3600
        if delta_hours <= 2:
            state = "urgent"
        elif delta_hours <= 24:
            state = "warning"
        else:
            state = "ok"
        items.append(
            {
                "kind": "screening",
                "title": sub.profile.user.get_full_name() or sub.profile.user.username,
                "subtitle": _("Screening call"),
                "deadline": slot.start_at,
                "sla_state": state,
                "priority": state_priority.get(state, 5),
                "url_name": "crush_lu:coach_review_profile",
                "url_kwargs": {"submission_id": sub.id},
                "submitted_at": sub.submitted_at,
            }
        )

    # --- Connections awaiting coach review (assigned to this coach OR unassigned) ---
    pending_connections = (
        EventConnection.objects.filter(
            status__in=["accepted", "coach_reviewing"],
        )
        .filter(Q(assigned_coach=coach) | Q(assigned_coach__isnull=True))
        .select_related("requester", "recipient", "event")
        .order_by("requested_at")
    )
    for conn in pending_connections:
        # Connections aren't SLA-tracked the same way. Use age as proxy:
        # >= 5 days = urgent, >= 2 days = warning, otherwise ok.
        age_days = (now - conn.requested_at).total_seconds() / 86400
        if age_days >= 5:
            state = "urgent"
        elif age_days >= 2:
            state = "warning"
        else:
            state = "ok"
        requester_name = (
            conn.requester.get_full_name() or conn.requester.username
        )
        recipient_name = (
            conn.recipient.get_full_name() or conn.recipient.username
        )
        items.append(
            {
                "kind": "connection",
                "title": f"{requester_name} ↔ {recipient_name}",
                "subtitle": _("Connection — %(event)s") % {"event": conn.event.title},
                "deadline": conn.requested_at,
                "sla_state": state,
                "priority": state_priority.get(state, 5),
                "url_name": "crush_lu:coach_connection_review",
                "url_kwargs": {"connection_id": conn.id},
                "submitted_at": conn.requested_at,
            }
        )

    # Most urgent first; tie-break by oldest submission
    items.sort(key=lambda i: (i["priority"], i["submitted_at"]))

    counts = {
        "profile": sum(1 for i in items if i["kind"] == "profile"),
        "screening": sum(1 for i in items if i["kind"] == "screening"),
        "connection": sum(1 for i in items if i["kind"] == "connection"),
        "total": len(items),
        "urgent_or_worse": sum(
            1 for i in items if i["sla_state"] in ("escalated", "breach", "urgent")
        ),
    }

    context = {
        "coach": coach,
        "items": items,
        "counts": counts,
    }
    return render(request, "crush_lu/coach_action_queue.html", context)


# ---------------------------------------------------------------------------
# Hybrid Coach Review System — Coach Settings UI (Phase 2)
# ---------------------------------------------------------------------------

DAY_CHOICES = [
    ("monday", _("Monday")),
    ("tuesday", _("Tuesday")),
    ("wednesday", _("Wednesday")),
    ("thursday", _("Thursday")),
    ("friday", _("Friday")),
    ("saturday", _("Saturday")),
    ("sunday", _("Sunday")),
]


@coach_required
def coach_settings(request):
    """Render and update the coach's hybrid-review preferences."""
    coach = request.coach

    if request.method == "POST":
        form = CoachSettingsForm(request.POST, instance=coach)
        if form.is_valid():
            form.save()
            messages.success(request, _("Settings updated."))
            return redirect("crush_lu:coach_settings")
    else:
        form = CoachSettingsForm(instance=coach)

    context = {
        "coach": coach,
        "form": form,
        "availability_windows": coach.availability_windows or [],
        "day_choices": DAY_CHOICES,
    }
    return render(request, "crush_lu/coach_settings.html", context)


def _availability_partial(request, coach):
    """Shared renderer for HTMX partial responses."""
    return render(
        request,
        "crush_lu/partials/_availability_window_list.html",
        {
            "availability_windows": coach.availability_windows or [],
            "day_choices": DAY_CHOICES,
        },
    )


@coach_required
@require_http_methods(["POST"])
def coach_settings_availability_add(request):
    """HTMX: append a new availability window."""
    coach = request.coach
    windows = list(coach.availability_windows or [])

    try:
        cleaned = _validate_availability_window(request.POST)
    except ValueError as exc:
        # CodeQL: don't echo the raw exception string into the response — in
        # practice `str(exc)` is always a safe short code, but we still map it
        # to a stable user-facing message so no internal detail leaks if the
        # validator ever gets extended with richer error text.
        messages_by_code = {
            "invalid_day": _("Please pick a valid day of the week."),
            "invalid_time": _("Please enter valid start and end times (HH:MM)."),
            "start_not_before_end": _("Start time must be before end time."),
        }
        return HttpResponse(
            messages_by_code.get(str(exc), _("Invalid window.")),
            status=400,
        )

    if _windows_overlap(windows, cleaned):
        return HttpResponse(
            _("This window overlaps an existing one on the same day."),
            status=400,
        )

    windows.append(cleaned)
    # Keep stable ordering: by weekday, then start time.
    day_order = {d: i for i, (d, _label) in enumerate(DAY_CHOICES)}
    windows.sort(key=lambda w: (day_order.get(w.get("day"), 99), w.get("start", "")))
    coach.availability_windows = windows
    coach.save(update_fields=["availability_windows"])
    return _availability_partial(request, coach)


@coach_required
@require_http_methods(["POST"])
def coach_settings_availability_remove(request, index):
    """HTMX: remove availability window at the given index."""
    coach = request.coach
    windows = list(coach.availability_windows or [])
    if 0 <= index < len(windows):
        windows.pop(index)
        coach.availability_windows = windows
        coach.save(update_fields=["availability_windows"])
    return _availability_partial(request, coach)


@coach_required
def coach_profiles(request):
    """Profile verification queue - pending submissions, recontact, and incomplete profiles"""
    coach = request.coach
    now = timezone.now()

    # --- Section 1: Pending submissions ---
    pending_submissions = (
        ProfileSubmission.objects.filter(coach=coach, status="pending")
        .select_related("profile__user")
        .annotate(
            call_attempt_count=Count(
                "call_attempts",
                filter=Q(call_attempts__result__in=["success", "failed"]),
            ),
            sms_attempt_count=Count(
                "call_attempts",
                filter=Q(call_attempts__result__in=["sms_sent", "event_invite_sms"]),
            ),
        )
        .order_by("submitted_at")
    )

    for submission in pending_submissions:
        hours_waiting = (now - submission.submitted_at).total_seconds() / 3600
        submission.is_urgent = hours_waiting > 48
        submission.is_warning = 24 < hours_waiting <= 48

    pending_women = [s for s in pending_submissions if s.profile.gender == "F"]
    pending_men = [s for s in pending_submissions if s.profile.gender == "M"]
    pending_other = [
        s for s in pending_submissions if s.profile.gender in ["NB", "O", "P", ""]
    ]

    # --- Section 2: Recontact submissions ---
    recontact_submissions = (
        ProfileSubmission.objects.filter(coach=coach, status="recontact_coach")
        .select_related("profile__user")
        .annotate(
            call_attempt_count=Count(
                "call_attempts",
                filter=Q(call_attempts__result__in=["success", "failed"]),
            ),
            sms_attempt_count=Count(
                "call_attempts",
                filter=Q(call_attempts__result__in=["sms_sent", "event_invite_sms"]),
            ),
        )
        .order_by("submitted_at")
    )

    for submission in recontact_submissions:
        hours_waiting = (now - submission.submitted_at).total_seconds() / 3600
        submission.is_urgent = hours_waiting > 48
        submission.is_warning = 24 < hours_waiting <= 48

    # --- Batch-query event data for pending + recontact ---
    submission_user_ids = [s.profile.user_id for s in pending_submissions] + [
        s.profile.user_id for s in recontact_submissions
    ]
    upcoming_event_regs = {}
    past_event_counts = {}
    if submission_user_ids:
        upcoming_regs = (
            EventRegistration.objects.filter(
                user_id__in=submission_user_ids,
                event__date_time__gte=now,
            )
            .exclude(status="cancelled")
            .select_related("event")
            .order_by("event__date_time")
        )
        for reg in upcoming_regs:
            upcoming_event_regs.setdefault(reg.user_id, []).append(reg)

        past_counts = (
            EventRegistration.objects.filter(
                user_id__in=submission_user_ids,
                event__date_time__lt=now,
            )
            .exclude(status="cancelled")
            .values("user_id")
            .annotate(count=Count("id"))
        )
        past_event_counts = {item["user_id"]: item["count"] for item in past_counts}

    for submission in pending_submissions:
        submission.upcoming_events = upcoming_event_regs.get(
            submission.profile.user_id, []
        )
        submission.past_event_count = past_event_counts.get(
            submission.profile.user_id, 0
        )
    for submission in recontact_submissions:
        submission.upcoming_events = upcoming_event_regs.get(
            submission.profile.user_id, []
        )
        submission.past_event_count = past_event_counts.get(
            submission.profile.user_id, 0
        )

    # --- Section 3: Incomplete profiles ---
    # Revision profiles: submission with this coach, status=revision, profile not resubmitted
    revision_profiles = (
        CrushProfile.objects.filter(
            profilesubmission__coach=coach,
            profilesubmission__status="revision",
        )
        .exclude(verification_status="pending")
        .exclude(verification_status="verified")
        .select_related("user")
        .distinct()
        .order_by("-updated_at")
    )

    # All incomplete: profiles stuck in the wizard (not yet pending or verified)
    all_incomplete = (
        CrushProfile.objects.filter(
            verification_status="incomplete",
            phone_number__isnull=False,
            phone_verified=True,
        )
        .exclude(phone_number="")
        .select_related("user")
        .order_by("-updated_at")[:50]
    )

    # --- Batch-query event data for revision + incomplete profiles ---
    incomplete_user_ids = [p.user_id for p in revision_profiles] + [
        p.user_id for p in all_incomplete
    ]
    incomplete_upcoming_regs = {}
    incomplete_past_counts = {}
    if incomplete_user_ids:
        for reg in (
            EventRegistration.objects.filter(
                user_id__in=incomplete_user_ids,
                event__date_time__gte=now,
            )
            .exclude(status="cancelled")
            .select_related("event")
            .order_by("event__date_time")
        ):
            incomplete_upcoming_regs.setdefault(reg.user_id, []).append(reg)

        for item in (
            EventRegistration.objects.filter(
                user_id__in=incomplete_user_ids,
                event__date_time__lt=now,
            )
            .exclude(status="cancelled")
            .values("user_id")
            .annotate(count=Count("id"))
        ):
            incomplete_past_counts[item["user_id"]] = item["count"]

    for profile in revision_profiles:
        profile.upcoming_events = incomplete_upcoming_regs.get(profile.user_id, [])
        profile.past_event_count = incomplete_past_counts.get(profile.user_id, 0)
    for profile in all_incomplete:
        profile.upcoming_events = incomplete_upcoming_regs.get(profile.user_id, [])
        profile.past_event_count = incomplete_past_counts.get(profile.user_id, 0)

    from django.conf import settings as _settings

    context = {
        "coach": coach,
        "pending_submissions": pending_submissions,
        "pending_women": pending_women,
        "pending_men": pending_men,
        "pending_other": pending_other,
        "recontact_submissions": recontact_submissions,
        "revision_profiles": revision_profiles,
        "all_incomplete": all_incomplete,
        "pre_screening_enabled": getattr(_settings, "PRE_SCREENING_ENABLED", False),
    }
    return render(request, "crush_lu/coach_profiles.html", context)


@coach_required
def coach_members(request):
    """Coach view of their approved members - profiles they reviewed and approved."""
    coach = request.coach

    # Get all profiles this coach approved (via ProfileSubmission)
    approved_submissions = (
        ProfileSubmission.objects.filter(coach=coach, status="approved")
        .select_related("profile__user")
        .order_by("-reviewed_at")
    )

    # Build member list with unique profiles (a profile may have multiple submissions)
    seen_profiles = set()
    members = []
    for submission in approved_submissions:
        profile = submission.profile
        if profile.id in seen_profiles:
            continue
        if not profile.is_approved or not profile.is_active:
            continue
        seen_profiles.add(profile.id)
        members.append(profile)

    # Annotate matching readiness for each profile
    profile_ids = [p.id for p in members]

    # Batch-check M2M fields to avoid N+1
    from django.db.models import Count

    readiness = (
        CrushProfile.objects.filter(id__in=profile_ids)
        .annotate(
            n_qualities=Count("qualities"),
            n_defects=Count("defects"),
            n_sought=Count("sought_qualities"),
        )
        .values("id", "n_qualities", "n_defects", "n_sought")
    )
    readiness_map = {r["id"]: r for r in readiness}
    for profile in members:
        r = readiness_map.get(profile.id, {})
        has_q = r.get("n_qualities", 0) > 0
        has_d = r.get("n_defects", 0) > 0
        has_s = r.get("n_sought", 0) > 0
        profile.match_ready = has_q and has_d and has_s
        profile.missing_fields = []
        if not has_q:
            profile.missing_fields.append(_("qualities"))
        if not has_d:
            profile.missing_fields.append(_("defects"))
        if not has_s:
            profile.missing_fields.append(_("ideal crush"))

    paginator = Paginator(members, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "coach": coach,
        "page_obj": page_obj,
        "members": page_obj.object_list,
        "total_members": len(members),
    }
    return render(request, "crush_lu/coach_members.html", context)


@coach_required
@require_http_methods(["POST"])
def coach_mark_review_call_complete(request, submission_id):
    """Mark screening call as complete during profile review"""
    is_htmx = request.headers.get("HX-Request")
    coach = request.coach

    # Handle submission not found or not assigned to this coach
    # Use select_related to prefetch profile and user in single query (reduces latency)
    try:
        submission = ProfileSubmission.objects.select_related(
            "profile", "profile__user"
        ).get(id=submission_id, coach=coach)
    except ProfileSubmission.DoesNotExist:
        if is_htmx:
            return render(
                request,
                "crush_lu/_htmx_error.html",
                {
                    "message": _("Submission not found or not assigned to you."),
                    "target_id": "screening-call-section",
                },
            )
        messages.error(request, _("Submission not found."))
        return redirect("crush_lu:coach_dashboard")

    submission.review_call_completed = True
    submission.review_call_date = timezone.now()
    submission.review_call_notes = request.POST.get("call_notes", "")

    # Parse and save checklist data
    checklist_data_str = request.POST.get("checklist_data", "{}")
    try:
        checklist_data = json.loads(checklist_data_str) if checklist_data_str else {}
    except json.JSONDecodeError:
        checklist_data = {}
    submission.review_call_checklist = checklist_data

    # Only update specific fields (faster than full model save)
    submission.save(
        update_fields=[
            "review_call_completed",
            "review_call_date",
            "review_call_notes",
            "review_call_checklist",
        ]
    )

    # Return HTMX partial or redirect. The calibration flow's wrapping div id
    # is `#screening-call-section-content`; the legacy flow swaps the nested
    # `#screening-call-section` partial. Pick the template the submitting form
    # is actually targeting.
    if is_htmx:
        template_name = (
            "crush_lu/_screening_tab_calibration.html"
            if submission.screening_call_mode == "calibration"
            else "crush_lu/_screening_call_section.html"
        )
        return render(
            request,
            template_name,
            {
                "submission": submission,
                "profile": submission.profile,
            },
        )

    messages.success(
        request,
        f"Screening call marked complete for {submission.profile.user.first_name}. You can now approve the profile.",
    )
    return redirect("crush_lu:coach_review_profile", submission_id=submission.id)


@coach_required
def coach_log_failed_call(request, submission_id):
    """Log a failed call attempt - HTMX endpoint"""
    from .forms import CallAttemptForm

    coach = request.coach

    submission = get_object_or_404(
        ProfileSubmission.objects.select_related("profile__user"),
        id=submission_id,
        coach=coach,
    )

    if request.method == "POST":
        form = CallAttemptForm(request.POST)
        if form.is_valid():
            # Create failed call attempt
            attempt = form.save(commit=False)
            attempt.submission = submission
            attempt.result = "failed"
            attempt.coach = coach
            attempt.save()

            messages.success(request, _("Failed call attempt logged."))

            # Return updated screening section via HTMX
            if request.headers.get("HX-Request"):
                context = {
                    "submission": submission,
                    "profile": submission.profile,
                }
                return render(request, "crush_lu/_screening_call_section.html", context)

            return redirect(
                "crush_lu:coach_review_profile", submission_id=submission.id
            )

    # For GET or invalid POST, return form
    context = {
        "submission": submission,
        "profile": submission.profile,
        "form": CallAttemptForm(),
    }

    if request.headers.get("HX-Request"):
        return render(request, "crush_lu/_call_attempt_form.html", context)

    return redirect("crush_lu:coach_review_profile", submission_id=submission.id)


@coach_required
@require_http_methods(["POST"])
def coach_log_sms_sent(request, submission_id):
    """Log an SMS template sent attempt - HTMX endpoint"""
    from .models import CallAttempt

    coach = request.coach
    submission = get_object_or_404(
        ProfileSubmission.objects.select_related("profile__user"),
        id=submission_id,
        coach=coach,
    )

    CallAttempt.objects.create(
        submission=submission,
        result="sms_sent",
        coach=coach,
        notes=_("SMS template sent via coach review page"),
    )

    messages.success(request, _("SMS attempt logged."))

    if request.headers.get("HX-Request"):
        profile = submission.profile
        context = {
            "submission": submission,
            "profile": profile,
        }
        context.update(_build_outreach_context(coach, profile))
        return render(request, "crush_lu/_screening_tab.html", context)

    return redirect("crush_lu:coach_review_profile", submission_id=submission.id)


@coach_required
@require_http_methods(["POST"])
def coach_log_whatsapp_sent(request, submission_id):
    """Log a WhatsApp template sent attempt - HTMX endpoint."""
    from .models import CallAttempt

    coach = request.coach
    submission = get_object_or_404(
        ProfileSubmission.objects.select_related("profile__user"),
        id=submission_id,
        coach=coach,
    )

    CallAttempt.objects.create(
        submission=submission,
        result="whatsapp_sent",
        coach=coach,
        notes=_("WhatsApp template sent via coach review page"),
    )

    messages.success(request, _("WhatsApp attempt logged."))

    if request.headers.get("HX-Request"):
        profile = submission.profile
        context = {
            "submission": submission,
            "profile": profile,
        }
        context.update(_build_outreach_context(coach, profile))
        return render(request, "crush_lu/_screening_tab.html", context)

    return redirect("crush_lu:coach_review_profile", submission_id=submission.id)


@coach_required
def coach_review_profile(request, submission_id):
    """Review a profile submission"""
    coach = request.coach

    submission = get_object_or_404(ProfileSubmission, id=submission_id, coach=coach)

    # "expired" rows were closed out by the post-pivot cleanup and their
    # users routed to self-serve verification. A stale /coach/review/<id>/
    # link must not let a coach re-open one and pull the user back into
    # the legacy review flow — nor an older assigned row hidden behind a
    # newer expired row (latest_for_profile None ⇒ the member's newest
    # submission is expired, so they are a self-serve case).
    if (
        submission.status == "expired"
        or ProfileSubmission.latest_for_profile(submission.profile) is None
    ):
        messages.info(
            request,
            _(
                "This submission was closed out — the member now verifies "
                "their identity self-serve. It can no longer be reviewed."
            ),
        )
        return redirect("crush_lu:coach_profiles")

    # Block changes to already-reviewed submissions
    if submission.status in ("approved", "rejected") and submission.reviewed_at:
        messages.info(
            request,
            _(
                "This profile has already been reviewed. No further changes are allowed."
            ),
        )
        return redirect("crush_lu:coach_profiles")

    if request.method == "POST":
        form = ProfileReviewForm(request.POST, instance=submission)
        if form.is_valid():
            submission = form.save(commit=False)
            submission.reviewed_at = timezone.now()

            # Update profile approval status and send notifications
            if submission.status == "approved":
                # REQUIRE screening call before approval
                if not submission.review_call_completed:
                    messages.error(
                        request,
                        _(
                            "You must complete a screening call before approving this profile."
                        ),
                    )
                    form = ProfileReviewForm(instance=submission)
                    context = {
                        "coach": coach,
                        "submission": submission,
                        "form": form,
                    }
                    return render(
                        request, "crush_lu/coach_review_profile.html", context
                    )
                submission.profile.is_approved = True
                submission.profile.approved_at = timezone.now()
                submission.profile.verification_status = "verified"
                submission.profile.save()
                messages.success(request, _("Profile approved!"))

                # Award referral bonus points to the referrer (if this user was referred)
                check_and_apply_profile_approved_reward(submission.profile)

                # Send approval notification to user (push first, email fallback)
                try:
                    result = notify_profile_approved(
                        user=submission.profile.user,
                        profile=submission.profile,
                        coach_notes=submission.feedback_to_user,
                        request=request,
                    )
                    if result.any_delivered:
                        logger.info(
                            f"Profile approval notification sent: push={result.push_success}, email={result.email_sent}"
                        )
                except Exception as e:
                    logger.error(f"Failed to send profile approval notification: {e}")

            elif submission.status == "rejected":
                submission.profile.is_approved = False
                submission.profile.verification_status = "rejected"
                submission.profile.save()
                messages.info(request, _("Profile rejected."))

                # Send rejection notification to user (push first, email fallback)
                try:
                    result = notify_profile_rejected(
                        user=submission.profile.user,
                        profile=submission.profile,
                        feedback=submission.feedback_to_user,
                        request=request,
                    )
                    if result.any_delivered:
                        logger.info(
                            f"Profile rejection notification sent: push={result.push_success}, email={result.email_sent}"
                        )
                except Exception as e:
                    logger.error(f"Failed to send profile rejection notification: {e}")

            elif submission.status == "revision":
                messages.info(request, _("Revision requested."))

                # Track that this submission has been through a coach review
                # cycle. The next time a coach opens it (after the user
                # resubmits) the review template surfaces a "resubmission"
                # banner with the prior feedback already saved on the row.
                submission.revision_round = (submission.revision_round or 0) + 1

                # Release the submission back to the verification channel so any
                # coach can pick it up again once the user resubmits. The user's
                # resubmission flow also clears coach=None, but setting it here
                # keeps state consistent between request and resubmit.
                submission.coach = None

                # Unlock the profile so the user can edit and resubmit.
                submission.profile.verification_status = 'incomplete'
                submission.profile.save()

                # Send revision request to user (push first, email fallback)
                try:
                    result = notify_profile_revision(
                        user=submission.profile.user,
                        profile=submission.profile,
                        feedback=submission.feedback_to_user,
                        request=request,
                    )
                    if result.any_delivered:
                        logger.info(
                            f"Profile revision notification sent: push={result.push_success}, email={result.email_sent}"
                        )
                except Exception as e:
                    logger.error(f"Failed to send profile revision request: {e}")

            elif submission.status == "recontact_coach":
                messages.info(request, _("User asked to recontact coach."))

                # Send notification to user
                try:
                    from .notification_service import notify_profile_recontact

                    result = notify_profile_recontact(
                        user=submission.profile.user,
                        profile=submission.profile,
                        coach=coach,
                        request=request,
                    )
                    if result.any_delivered:
                        logger.info(
                            f"Recontact notification sent: push={result.push_success}, email={result.email_sent}"
                        )
                except Exception as e:
                    logger.error(f"Failed to send recontact notification: {e}")

            submission.save()
            return redirect("crush_lu:coach_profiles")
    else:
        form = ProfileReviewForm(instance=submission)

    # Get social login provider if exists
    social_account = submission.profile.user.socialaccount_set.first()

    profile = submission.profile
    prefetch_related_objects([profile], "qualities", "defects", "sought_qualities")
    outreach = _build_outreach_context(coach, profile)

    from django.conf import settings as _settings

    pre_screening_enabled = getattr(_settings, "PRE_SCREENING_ENABLED", False)
    can_send_prescreening_sms = bool(profile.phone_number and profile.phone_verified)

    # Status-strip lookups: latest call attempt + next/most-recent booked slot.
    # Keep these out of the template so the strip stays cheap to render.
    last_call_attempt = (
        submission.call_attempts.order_by("-attempt_date").first()
    )
    latest_booked_slot = (
        submission.booked_slots.filter(status="booked")
        .order_by("start_at")
        .first()
    )

    context = {
        "submission": submission,
        "profile": profile,
        "form": form,
        "social_account": social_account,
        "pre_screening_enabled": pre_screening_enabled,
        "can_send_prescreening_sms": can_send_prescreening_sms,
        "last_call_attempt": last_call_attempt,
        "latest_booked_slot": latest_booked_slot,
    }
    context.update(outreach)
    return render(request, "crush_lu/coach_review_profile.html", context)


@coach_required
@require_http_methods(["POST"])
def coach_set_screening_mode(request, submission_id):
    """Let a Coach switch between legacy (5-section) and calibration (3-section).

    Called from both tab templates. Re-renders the tab via HTMX so the coach
    can toggle mid-flow without losing the page.
    """
    coach = request.coach
    submission = get_object_or_404(
        ProfileSubmission.objects.select_related("profile__user"),
        id=submission_id,
        coach=coach,
    )
    if submission.review_call_completed:
        return HttpResponse(status=410)
    mode = request.POST.get("mode")
    if mode not in ("legacy", "calibration"):
        return HttpResponse(status=400)
    # Legacy-only path: only allow calibration if pre-screening was submitted.
    if mode == "calibration" and not submission.pre_screening_submitted_at:
        return HttpResponse(status=400)
    submission.screening_call_mode = mode
    submission.save(update_fields=["screening_call_mode"])

    profile = submission.profile
    context = {
        "submission": submission,
        "profile": profile,
    }
    context.update(_build_outreach_context(coach, profile))
    return render(request, "crush_lu/_screening_tab.html", context)


@coach_required
@require_http_methods(["POST"])
def coach_send_pre_screening_reminder(request, submission_id):
    """HTMX: log that the Coach opened an SMS reminder for pre-screening.

    Returns a small HTML fragment containing the tel: SMS link the Coach's
    device can open, and records a CallAttempt so the outreach is auditable.
    """
    from django.conf import settings as _settings
    from django.urls import reverse
    from django.utils import translation
    from urllib.parse import quote
    from .models import CallAttempt
    from .models.site_config import CrushSiteConfig

    if not getattr(_settings, "PRE_SCREENING_ENABLED", False):
        return HttpResponse(status=410)

    coach = request.coach
    submission = get_object_or_404(
        ProfileSubmission.objects.select_related("profile__user"),
        id=submission_id,
        coach=coach,
    )
    profile = submission.profile

    # Mirror the eligibility checks in the automated invite/reminder path
    # (pre_screening_notifications.send_pre_screening_invite_email) so a
    # direct POST can't send stale reminders or log misleading
    # `sms_sent` audit rows for closed flows.
    closed = (
        submission.pre_screening_submitted_at is not None
        or submission.status != "pending"
        or submission.review_call_completed
        or submission.is_paused
    )
    if closed:
        return HttpResponse(
            '<p class="text-xs text-red-600 dark:text-red-400">'
            + str(_("Pre-screening is no longer active for this submission."))
            + "</p>",
            status=400,
        )

    if not (profile.phone_number and profile.phone_verified):
        return HttpResponse(
            '<p class="text-xs text-red-600 dark:text-red-400">'
            + str(_("No verified phone number on file."))
            + "</p>",
            status=400,
        )

    config = CrushSiteConfig.get_config()
    lang = getattr(profile, "preferred_language", "en") or "en"
    template_field = f"pre_screening_reminder_sms_{lang}"
    template = (
        getattr(config, template_field, config.pre_screening_reminder_sms_en)
        or config.pre_screening_reminder_sms_en
    )
    coach_name = coach.user.first_name or "Your coach"
    first_name = profile.user.first_name or ""
    with translation.override(lang):
        link = request.build_absolute_uri(reverse("crush_lu:pre_screening"))
    sms_body = template.format(first_name=first_name, coach_name=coach_name, link=link)
    sms_href = f"sms:{profile.phone_number}?&body={quote(sms_body)}"

    CallAttempt.objects.create(
        submission=submission,
        profile=profile,
        result="sms_sent",
        coach=coach,
        notes=_("Pre-screening reminder SMS drafted"),
    )

    html = (
        '<a href="' + sms_href + '" '
        'class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold '
        'bg-green-600 text-white hover:bg-green-700">'
        + str(_("Open SMS app →"))
        + "</a>"
        '<p class="text-xs text-gray-500 dark:text-gray-400 mt-1">'
        + str(_("Attempt logged."))
        + "</p>"
    )
    return HttpResponse(html)


@coach_required
@require_http_methods(["POST"])
def coach_offer_self_booking(request, submission_id):
    """HTMX: coach manually offers self-booking — bypasses 48h SLA wait.

    Mints booking_token, sets fallback_offered_at, logs system action, and
    enqueues the same email the SLA sweep would send. Idempotent: returns
    a status partial without re-sending if fallback_offered_at is already
    set. Mirrors the eligibility gates of api_admin_hybrid.sla_sweep so a
    direct POST can't bypass the same closed-flow guards.
    """
    import uuid
    from django.conf import settings as _settings
    from .api_admin_hybrid import FALLBACK_TOKEN_TTL
    from .tasks import send_sla_fallback_email_task

    coach = request.coach
    submission = get_object_or_404(
        ProfileSubmission.objects.select_related("profile__user"),
        id=submission_id,
        coach=coach,
    )

    if not getattr(_settings, "HYBRID_COACH_SYSTEM_ENABLED", False):
        return HttpResponse(
            '<p class="text-xs text-red-600 dark:text-red-400">'
            + str(_("Hybrid coach system is disabled."))
            + "</p>",
            status=410,
        )

    # Already offered — render current status, do not re-send.
    if submission.fallback_offered_at:
        return render(
            request,
            "crush_lu/_self_booking_offer.html",
            {"submission": submission},
        )

    closed = (
        submission.status != "pending"
        or submission.review_call_completed
        or submission.is_paused
    )
    if closed:
        return HttpResponse(
            '<p class="text-xs text-red-600 dark:text-red-400">'
            + str(_("Self-booking can no longer be offered for this submission."))
            + "</p>",
            status=400,
        )

    now = timezone.now()
    try:
        with transaction.atomic():
            submission.fallback_offered_at = now
            submission.booking_token = uuid.uuid4()
            submission.booking_token_expires_at = now + FALLBACK_TOKEN_TTL
            submission.log_system_action(
                "fallback_offered",
                actor=f"coach:{coach.user.username}",
                reason="coach_initiated",
            )
            submission.save(
                update_fields=[
                    "fallback_offered_at",
                    "booking_token",
                    "booking_token_expires_at",
                    "system_actions",
                ]
            )
            send_sla_fallback_email_task.enqueue(
                submission_id=submission.pk,
                host=request.get_host(),
                is_secure=request.is_secure(),
            )
    except Exception:
        logger.exception(
            "[coach_offer_self_booking] Failed for submission %s", submission.pk
        )
        return HttpResponse(
            '<p class="text-xs text-red-600 dark:text-red-400">'
            + str(_("Could not send the booking offer. Try again."))
            + "</p>",
            status=500,
        )

    return render(
        request,
        "crush_lu/_self_booking_offer.html",
        {"submission": submission},
    )


@coach_required
def coach_preview_email(request, submission_id):
    """Preview the email that will be sent for a review decision"""
    import traceback
    from django.utils import translation
    from django.utils.translation import gettext as _
    from .utils.i18n import get_user_preferred_language
    from .email_helpers import (
        get_email_context_with_unsubscribe,
        get_email_base_urls,
        get_user_language_url,
    )
    from django.template.loader import render_to_string

    coach = request.coach

    # Wrap entire function in try-except to catch any errors
    try:
        submission = get_object_or_404(ProfileSubmission, id=submission_id, coach=coach)

        # Get parameters from request
        status = request.GET.get("status", "")
        feedback = request.GET.get("feedback_to_user", "")
        coach_notes = request.GET.get("coach_notes", "")

        profile = submission.profile
        user = profile.user

        # Get user's preferred language
        lang = get_user_preferred_language(user=user, request=request, default="en")

        # If no valid status selected, show a helpful message
        if not status or status == "pending":
            preview_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Email Preview</title>
            <style>
                body {
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    margin: 0;
                    padding: 40px;
                    background: #f3f4f6;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    min-height: 100vh;
                }
                .message {
                    background: white;
                    padding: 32px;
                    border-radius: 12px;
                    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                    text-align: center;
                    max-width: 500px;
                }
                .message h2 {
                    margin: 0 0 16px 0;
                    color: #6366f1;
                    font-size: 24px;
                }
                .message p {
                    margin: 0;
                    color: #6b7280;
                    line-height: 1.6;
                }
            </style>
        </head>
        <body>
            <div class="message">
                <h2>📧 No Preview Available</h2>
                <p>Please select a decision (Approved, Rejected, Revision Requested, or Recontact Coach) to preview the email that will be sent.</p>
            </div>
        </body>
        </html>
            """
            response = HttpResponse(preview_html, content_type="text/html")
            response["X-Frame-Options"] = "SAMEORIGIN"
            return response

        # Build context based on decision type
        if status == "approved":
            events_url = get_user_language_url(user, "crush_lu:event_list", request)
            context = get_email_context_with_unsubscribe(
                user,
                request,
                first_name=user.first_name,
                coach_notes=feedback or coach_notes,
                events_url=events_url,
            )
            template = "crush_lu/emails/profile_approved.html"
            with translation.override(lang):
                subject = _("Welcome to Crush.lu - Your Profile is Approved!")

        elif status == "rejected":
            base_urls = get_email_base_urls(user, request)
            context = {
                "user": user,
                "first_name": user.first_name,
                "reason": feedback,
                "LANGUAGE_CODE": lang,
                **base_urls,
            }
            template = "crush_lu/emails/profile_rejected.html"
            with translation.override(lang):
                subject = _("Profile Not Approved - Crush.lu")

        elif status == "revision":
            edit_profile_url = get_user_language_url(
                user, "crush_lu:edit_profile", request
            )
            base_urls = get_email_base_urls(user, request)
            context = {
                "user": user,
                "first_name": user.first_name,
                "feedback": feedback,
                "edit_profile_url": edit_profile_url,
                "LANGUAGE_CODE": lang,
                **base_urls,
            }
            template = "crush_lu/emails/profile_revision_request.html"
            with translation.override(lang):
                subject = _("Profile Review Feedback - Crush.lu")

        elif status == "recontact_coach":
            base_urls = get_email_base_urls(user, request)
            context = {
                "user": user,
                "first_name": user.first_name,
                "coach": coach,
                "LANGUAGE_CODE": lang,
                **base_urls,
            }
            template = "crush_lu/emails/profile_recontact.html"
            with translation.override(lang):
                subject = _("Your Crush Coach Needs to Speak With You")
        else:
            return HttpResponse("Invalid status", status=400)

        # Render preview with language override
        try:
            with translation.override(lang):
                html_content = render_to_string(template, context)
        except Exception as e:
            logger.error(f"Error rendering email template: {e}")
            logger.error(traceback.format_exc())
            return HttpResponse(
                "An error occurred. Please try again later.",
                status=500,
            )

        # Wrap in preview container
        preview_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Email Preview</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                margin: 0;
                padding: 20px;
                background: #f3f4f6;
            }}
            .preview-header {{
                background: white;
                padding: 16px 24px;
                border-radius: 8px;
                margin-bottom: 16px;
                box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            }}
            .preview-header h2 {{
                margin: 0 0 8px 0;
                font-size: 18px;
                color: #111827;
            }}
            .preview-header p {{
                margin: 0;
                font-size: 14px;
                color: #6b7280;
            }}
            .email-container {{
                background: white;
                border-radius: 8px;
                box-shadow: 0 1px 3px rgba(0,0,0,0.1);
                overflow: hidden;
            }}
        </style>
    </head>
    <body>
        <div class="preview-header">
            <h2>📧 {subject}</h2>
            <p>Language: {lang.upper()} | To: {user.email}</p>
        </div>
        <div class="email-container">
            {html_content}
        </div>
    </body>
    </html>
    """

        response = HttpResponse(preview_html, content_type="text/html")
        response["X-Frame-Options"] = "SAMEORIGIN"
        return response

    except Exception as e:
        logger.error(f"Error in coach_preview_email: {e}")
        logger.error(traceback.format_exc())
        error_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Preview Error</title>
            <style>
                body {
                    font-family: monospace;
                    padding: 20px;
                    background: #fee;
                }
                .error {
                    background: white;
                    padding: 20px;
                    border: 2px solid #f00;
                    border-radius: 8px;
                }
                h2 { color: #c00; }
            </style>
        </head>
        <body>
            <div class="error">
                <h2>Preview Error</h2>
                <p>An error occurred while generating the email preview. Please check the server logs for details.</p>
            </div>
        </body>
        </html>
        """
        return HttpResponse(error_html, status=500)


@coach_required
def coach_sessions(request):
    """View and manage coach sessions"""
    coach = request.coach

    sessions = CoachSession.objects.filter(coach=coach).order_by("-created_at")

    context = {
        "coach": coach,
        "sessions": sessions,
    }
    return render(request, "crush_lu/coach_sessions.html", context)


@coach_required
def coach_edit_profile(request):
    """Edit coach profile (bio, specializations, photo) - separate from dating profile"""
    coach = request.coach

    if request.method == "POST":
        form = CrushCoachForm(request.POST, request.FILES, instance=coach)
        if form.is_valid():
            form.save()
            messages.success(request, _("Coach profile updated successfully!"))
            return redirect("crush_lu:coach_dashboard")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(
                        request, f"{field.replace('_', ' ').title()}: {error}"
                    )
    else:
        form = CrushCoachForm(instance=coach)

    # Check if coach also has a dating profile
    try:
        profile = request.user.crushprofile
        has_dating_profile = True
    except CrushProfile.DoesNotExist:
        has_dating_profile = False

    context = {
        "coach": coach,
        "form": form,
        "has_dating_profile": has_dating_profile,
    }
    return render(request, "crush_lu/coach_edit_profile.html", context)


# ============================================================================
# COACH JOURNEY MANAGEMENT - Manage Wonderland Journey Experiences
# ============================================================================


@coach_required
def coach_journey_dashboard(request):
    """Coach dashboard for managing all active journeys and their challenges"""
    coach = request.coach

    from .models import JourneyConfiguration, JourneyProgress
    from django.db.models import Count

    # Get all active journeys with aggregated progress counts (single query)
    active_journeys = (
        JourneyConfiguration.objects.filter(is_active=True)
        .select_related("special_experience")
        .prefetch_related("chapters__challenges")
        .annotate(
            total_users=Count("journeyprogress"),
            completed_users=Count(
                "journeyprogress", filter=Q(journeyprogress__is_completed=True)
            ),
        )
    )

    # Get recent progress per journey (1 query per journey, but avoids 3N pattern)
    journeys_with_progress = []
    for journey in active_journeys:
        progress_list = (
            JourneyProgress.objects.filter(journey=journey)
            .select_related("user")
            .order_by("-last_activity")[:5]
        )

        journeys_with_progress.append(
            {
                "journey": journey,
                "recent_progress": progress_list,
                "total_users": journey.total_users,
                "completed_users": journey.completed_users,
            }
        )

    context = {
        "coach": coach,
        "journeys_with_progress": journeys_with_progress,
    }
    return render(request, "crush_lu/coach_journey_dashboard.html", context)


@coach_required
def coach_edit_journey(request, journey_id):
    """Edit a journey's chapters and challenges"""
    coach = request.coach

    from .models import JourneyConfiguration

    journey = get_object_or_404(JourneyConfiguration, id=journey_id)

    # Get all chapters with challenges
    chapters = journey.chapters.all().prefetch_related("challenges", "rewards")

    context = {
        "coach": coach,
        "journey": journey,
        "chapters": chapters,
    }
    return render(request, "crush_lu/coach_edit_journey.html", context)


@coach_required
def coach_edit_challenge(request, challenge_id):
    """Edit an individual challenge's question and content"""
    coach = request.coach

    from .models import JourneyChallenge, ChallengeAttempt

    challenge = get_object_or_404(JourneyChallenge, id=challenge_id)

    if request.method == "POST":
        # Update challenge fields
        challenge.question = request.POST.get("question", challenge.question)
        challenge.correct_answer = request.POST.get(
            "correct_answer", challenge.correct_answer
        )
        challenge.success_message = request.POST.get(
            "success_message", challenge.success_message
        )

        # Update hints
        challenge.hint_1 = request.POST.get("hint_1", challenge.hint_1)
        challenge.hint_2 = request.POST.get("hint_2", challenge.hint_2)
        challenge.hint_3 = request.POST.get("hint_3", challenge.hint_3)

        # Update points
        try:
            challenge.points_awarded = int(
                request.POST.get("points_awarded", challenge.points_awarded)
            )
            challenge.hint_1_cost = int(
                request.POST.get("hint_1_cost", challenge.hint_1_cost)
            )
            challenge.hint_2_cost = int(
                request.POST.get("hint_2_cost", challenge.hint_2_cost)
            )
            challenge.hint_3_cost = int(
                request.POST.get("hint_3_cost", challenge.hint_3_cost)
            )
        except ValueError:
            messages.error(request, _("Points must be valid numbers."))
            return redirect("crush_lu:coach_edit_challenge", challenge_id=challenge_id)

        challenge.save()
        messages.success(
            request, f'Challenge "{challenge.question[:50]}..." updated successfully!'
        )
        return redirect(
            "crush_lu:coach_edit_journey", journey_id=challenge.chapter.journey.id
        )

    # Get all user answers for this challenge
    all_attempts = (
        ChallengeAttempt.objects.filter(challenge=challenge)
        .select_related("chapter_progress__journey_progress__user")
        .order_by("-attempted_at")
    )

    context = {
        "coach": coach,
        "challenge": challenge,
        "all_attempts": all_attempts,
        "total_responses": all_attempts.count(),
    }
    return render(request, "crush_lu/coach_edit_challenge.html", context)


def _attach_registration_stats(events):
    """Attach gender_stats, avg_age, and per-gender avg ages to each event."""
    for event in events:
        regs = EventRegistration.objects.filter(
            event=event, status__in=["confirmed", "attended"]
        ).select_related("user__crushprofile")

        gender_counts = {"M": 0, "F": 0, "other": 0}
        ages = {"M": [], "F": [], "other": [], "all": []}
        for r in regs:
            profile = getattr(r.user, "crushprofile", None)
            if not profile:
                continue
            if profile.gender == "M":
                gender_counts["M"] += 1
                key = "M"
            elif profile.gender == "F":
                gender_counts["F"] += 1
                key = "F"
            else:
                gender_counts["other"] += 1
                key = "other"
            if profile.age is not None:
                ages[key].append(profile.age)
                ages["all"].append(profile.age)

        def _avg(lst):
            return round(sum(lst) / len(lst), 1) if lst else None

        event.gender_stats = gender_counts
        event.avg_age = _avg(ages["all"])
        event.avg_age_m = _avg(ages["M"])
        event.avg_age_f = _avg(ages["F"])
        event.avg_age_other = _avg(ages["other"])
    return events


@coach_required
def coach_event_list(request):
    """Coach dashboard for managing events and viewing attendees"""
    now = timezone.now()

    upcoming_events = list(
        MeetupEvent.objects.filter(date_time__gte=now, is_published=True)
        .with_registration_counts()
        .order_by("date_time")
    )

    past_events = list(
        MeetupEvent.objects.filter(date_time__lt=now, is_published=True)
        .with_registration_counts()
        .order_by("-date_time")[:10]
    )

    _attach_registration_stats(upcoming_events)
    _attach_registration_stats(past_events)

    context = {
        "coach": request.coach,
        "upcoming_events": upcoming_events,
        "past_events": past_events,
    }
    return render(request, "crush_lu/coach_event_list.html", context)


@coach_required
def coach_event_detail(request, event_id):
    """Detailed view of event registrations for coaches"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    all_regs = list(
        EventRegistration.objects.filter(event=event)
        .exclude(status="cancelled")
        .select_related("user__crushprofile")
        .order_by("registered_at")
    )

    # Split by status group (order preserved = FIFO within each group)
    all_confirmed = [r for r in all_regs if r.status in ("confirmed", "attended")]
    all_waitlisted = [r for r in all_regs if r.status == "waitlist"]
    all_other = [r for r in all_regs if r.status in ("pending", "no_show")]

    # Annotate each waitlisted registration with its global queue position and
    # per-gender-pool position so the coach can see who is next in line.
    pool_counters = {}
    for i, reg in enumerate(all_waitlisted, start=1):
        reg.waitlist_position = i
        if event.gender_limits_active:
            gender = getattr(getattr(reg.user, "crushprofile", None), "gender", None)
            pool = event.get_gender_pool(gender) if gender else None
            reg.waitlist_pool = pool
            if pool:
                pool_counters[pool] = pool_counters.get(pool, 0) + 1
                reg.waitlist_position_in_pool = pool_counters[pool]
            else:
                reg.waitlist_position_in_pool = None
        else:
            reg.waitlist_pool = None
            reg.waitlist_position_in_pool = None

    # Gender pool stats — one entry per active pool, shown as summary pills
    gender_pool_stats = None
    if event.gender_limits_active:
        pool_confirmed_counts = {}
        for r in all_confirmed:
            gender = getattr(getattr(r.user, "crushprofile", None), "gender", None)
            pool = event.get_gender_pool(gender) if gender else None
            if pool:
                pool_confirmed_counts[pool] = pool_confirmed_counts.get(pool, 0) + 1
        pool_waitlist_counts = {p: c for p, c in pool_counters.items() if p}
        gender_pool_stats = [
            entry for entry in [
                {"key": "m",  "symbol": "♂", "label": _("Male"),       "confirmed": pool_confirmed_counts.get("m",  0), "waitlist": pool_waitlist_counts.get("m",  0), "limit": event.max_participants_m},
                {"key": "f",  "symbol": "♀", "label": _("Female"),     "confirmed": pool_confirmed_counts.get("f",  0), "waitlist": pool_waitlist_counts.get("f",  0), "limit": event.max_participants_f},
                {"key": "nb", "symbol": "⚬", "label": _("Non-binary"), "confirmed": pool_confirmed_counts.get("nb", 0), "waitlist": pool_waitlist_counts.get("nb", 0), "limit": event.max_participants_nb},
            ]
            if entry["limit"]
        ]

    # Status filter — controls which section(s) the template renders
    status_filter = request.GET.get("status", "all")
    if status_filter not in ("all", "confirmed", "waitlist", "other"):
        status_filter = "all"

    # Batch-query latest ProfileSubmission per registered user
    user_ids = [r.user_id for r in all_regs]
    latest_submissions = {}
    if user_ids:
        for sub in (
            ProfileSubmission.objects.filter(profile__user_id__in=user_ids)
            .select_related("profile")
            .order_by("-submitted_at")
        ):
            if sub.profile.user_id not in latest_submissions:
                latest_submissions[sub.profile.user_id] = sub

    for reg in all_confirmed + all_waitlisted + all_other:
        reg.latest_submission = latest_submissions.get(reg.user_id)

    confirmed_count = len(all_confirmed)
    waitlist_count = len(all_waitlisted)
    other_count = len(all_other)
    spots_remaining = max(0, event.max_participants - confirmed_count)

    # Post-event activity: connections and sparks
    from .models.crush_spark import CrushSpark

    connections = EventConnection.objects.filter(event=event)
    connection_count = connections.count()
    mutual_connections = connections.filter(
        status__in=["accepted", "coach_reviewing", "coach_approved", "shared"]
    ).count()

    sparks = CrushSpark.objects.filter(event=event)
    spark_count = sparks.count()
    sparks_pending = sparks.filter(
        status__in=[CrushSpark.Status.PENDING_REVIEW, CrushSpark.Status.REQUESTED]
    ).count()

    has_quiz = hasattr(event, "quiz")

    # Crush Cache Control card: only render it for coaches who can actually
    # open the hunt dashboard. The event type alone isn't enough — the hunt
    # row may not exist yet (the dashboard 404s), and cache_coach_dashboard
    # denies coaches who neither created the hunt nor are assigned to the
    # event (_can_manage_hunt), which would make the card a dead link.
    can_manage_cache_hunt = False
    if event.event_type == "crush_cache" and hasattr(event, "cache_hunt"):
        from .views_crush_cache import _can_manage_hunt

        can_manage_cache_hunt = _can_manage_hunt(request.user, event.cache_hunt)

    # Per-coach recap: of users this coach onboarded (approved), how many
    # attended, how many sent connection requests, how many had a mutual
    # match, and how many connections of theirs still need an intro approved.
    coach = request.coach
    onboarded_user_ids = set(
        ProfileSubmission.objects.filter(
            coach=coach, status='approved'
        ).values_list('profile__user_id', flat=True)
    )
    coach_recap = None
    if onboarded_user_ids:
        attended_user_ids = {
            r.user_id for r in all_confirmed
            if r.status == 'attended' and r.user_id in onboarded_user_ids
        }
        attended_count_mine = len(attended_user_ids)

        # Connections from this event involving the coach's onboarded users
        connections_for_event = EventConnection.objects.filter(event=event)
        senders_with_mine = connections_for_event.filter(
            requester_id__in=attended_user_ids
        ).values('requester_id').distinct()
        senders_count = senders_with_mine.count()

        annotated_conns = (
            connections_for_event
            .annotate_is_mutual()
            .filter(requester_id__in=attended_user_ids)
        )
        mutual_user_ids = {
            c.requester_id for c in annotated_conns if c.is_mutual_annotated
        }
        mutual_count = len(mutual_user_ids)

        pending_intros_mine = connections_for_event.filter(
            assigned_coach=coach,
            status__in=['accepted', 'coach_reviewing'],
        ).count()

        coach_recap = {
            'onboarded_count': len(onboarded_user_ids),
            'attended_count': attended_count_mine,
            'senders_count': senders_count,
            'mutual_count': mutual_count,
            'pending_intros': pending_intros_mine,
        }

    # Feedback aggregates (only meaningful after the event has ended).
    from .models import EventFeedback

    feedback_qs = EventFeedback.objects.filter(event=event)
    feedback_total = feedback_qs.count()
    feedback_summary = None
    feedback_responses = []
    if feedback_total:
        promoters = sum(1 for f in feedback_qs if f.is_promoter)
        detractors = sum(1 for f in feedback_qs if f.is_detractor)
        nps = round(((promoters - detractors) * 100.0) / feedback_total)
        avg_score = round(
            sum(f.nps_score for f in feedback_qs) / feedback_total, 1
        )
        recommend_count = sum(1 for f in feedback_qs if f.would_recommend)
        feedback_summary = {
            "total": feedback_total,
            "nps": nps,
            "avg_score": avg_score,
            "promoters": promoters,
            "detractors": detractors,
            "recommend_count": recommend_count,
            "recommend_pct": round(recommend_count * 100 / feedback_total),
        }
        feedback_responses = list(
            feedback_qs.select_related("user").order_by("-created_at")
        )

    context = {
        "coach": request.coach,
        "event": event,
        "confirmed_registrations": all_confirmed,
        "waitlist_registrations": all_waitlisted,
        "other_registrations": all_other,
        "confirmed_count": confirmed_count,
        "waitlist_count": waitlist_count,
        "other_count": other_count,
        "spots_remaining": spots_remaining,
        "total_registrations": confirmed_count + waitlist_count + other_count,
        "status_filter": status_filter,
        "gender_pool_stats": gender_pool_stats,
        "connection_count": connection_count,
        "mutual_connections": mutual_connections,
        "spark_count": spark_count,
        "sparks_pending": sparks_pending,
        "has_quiz": has_quiz,
        "can_manage_cache_hunt": can_manage_cache_hunt,
        "feedback_summary": feedback_summary,
        "feedback_responses": feedback_responses,
        "coach_recap": coach_recap,
    }
    return render(request, "crush_lu/coach_event_detail.html", context)


@coach_required
def coach_event_checkin(request, event_id):
    """Coach check-in scanner page for scanning attendee QR codes."""
    event = get_object_or_404(MeetupEvent, id=event_id)

    registrations = (
        EventRegistration.objects.filter(event=event)
        .exclude(status="cancelled")
        .select_related("user__crushprofile")
        .order_by("registered_at")
    )

    confirmed = [r for r in registrations if r.status in ("confirmed", "attended")]
    attended_count = sum(1 for r in confirmed if r.status == "attended")

    # Ensure all confirmed registrations have check-in tokens for manual fallback
    from crush_lu.views_ticket import _generate_checkin_token

    for reg in confirmed:
        if not reg.checkin_token:
            _generate_checkin_token(reg)

    # Pre-fetch coach assignments for all profiles in one query (avoids N+1)
    from django.urls import reverse as _reverse
    from crush_lu.models import ProfileSubmission

    profile_ids = [
        reg.user.crushprofile.id
        for reg in confirmed
        if hasattr(reg.user, "crushprofile") and reg.user.crushprofile.id
    ]
    submissions_by_profile = {}
    for sub in (
        ProfileSubmission.objects.filter(profile_id__in=profile_ids)
        .select_related("coach__user")
        .order_by("-submitted_at")
    ):
        submissions_by_profile.setdefault(sub.profile_id, sub)

    for reg in confirmed:
        try:
            profile = reg.user.crushprofile
            reg.photo_url = (
                _reverse(
                    "crush_lu:serve_profile_photo",
                    kwargs={"user_id": reg.user_id, "photo_field": "photo_1"},
                )
                if profile.photo_1
                else None
            )
            sub = submissions_by_profile.get(profile.id)
            reg.coach_name = (
                f"{sub.coach.user.first_name} {sub.coach.user.last_name}".strip()
                if sub and sub.coach else None
            )
        except Exception:
            reg.photo_url = None
            reg.coach_name = None

    # Quiz table assignment data
    import json

    is_quiz_night = event.event_type == "quiz_night"
    table_assignments = {}
    num_tables = 0
    table_fill = []
    quiz_event = None
    if is_quiz_night:
        try:
            quiz_event = event.quiz
        except Exception:
            pass
    if quiz_event and quiz_event.num_tables:
        from crush_lu.models.quiz import QuizTableMembership

        num_tables = quiz_event.num_tables
        for m in QuizTableMembership.objects.filter(
            table__quiz=quiz_event
        ).select_related("table"):
            table_assignments[m.user_id] = m.table.table_number

        # Build table fill summary
        from collections import Counter

        fill_counts = Counter(table_assignments.values())
        for t in range(1, num_tables + 1):
            table_fill.append({"number": t, "count": fill_counts.get(t, 0)})

    context = {
        "coach": request.coach,
        "event": event,
        "registrations": confirmed,
        "confirmed_count": len(confirmed),
        "attended_count": attended_count,
        "is_quiz_night": is_quiz_night and quiz_event is not None,
        "table_assignments_json": json.dumps(
            {str(k): v for k, v in table_assignments.items()}
        ),
        "num_tables": num_tables,
        "table_fill_json": json.dumps(table_fill),
    }
    return render(request, "crush_lu/coach_event_checkin.html", context)


@coach_required
def coach_event_sms_invite(request, event_id):
    """Page listing eligible profiles with verified phones for SMS event invites."""
    from datetime import date
    from urllib.parse import quote

    from django.db.models import OuterRef, Q, Subquery
    from django.urls import reverse
    from django.utils.formats import date_format

    from .models import CallAttempt
    from .models.site_config import CrushSiteConfig

    event = get_object_or_404(MeetupEvent, id=event_id)
    coach = request.coach
    config = CrushSiteConfig.get_config()
    coach_name = coach.user.first_name or "Coach"

    # event_url is built per-profile inside _build_sms_uri using the recipient's language

    # --- Age filter boundaries (same pattern as user_segments._age_range_queryset) ---
    today = date.today()
    max_dob = date(today.year - event.min_age, today.month, today.day)
    min_dob = date(today.year - event.max_age - 1, today.month, today.day)
    has_age_filter = event.min_age != 18 or event.max_age != 99

    age_q = Q(date_of_birth__gt=min_dob, date_of_birth__lte=max_dob)
    age_q_lenient = age_q | Q(
        date_of_birth__isnull=True
    )  # include profiles missing DOB
    # For age-restricted events use strict filter — NULL DOB profiles are rejected
    # at registration and would bounce on click-through if invited.
    age_filter = age_q if has_age_filter else age_q_lenient

    # Age filter through profile__ FK (for ProfileSubmission queries)
    sub_age_q = Q(
        profile__date_of_birth__gt=min_dob, profile__date_of_birth__lte=max_dob
    ) | Q(profile__date_of_birth__isnull=True)

    # --- Language filter ---
    lang_q = Q()
    sub_lang_q = Q()
    has_language_filter = bool(event.languages)
    if has_language_filter:
        for code in event.languages:
            lang_q |= Q(event_languages__contains=[code])
            sub_lang_q |= Q(profile__event_languages__contains=[code])

    # --- Base phone filters ---
    phone_q = Q(phone_number__isnull=False, phone_verified=True) & ~Q(phone_number="")
    sub_phone_q = Q(
        profile__phone_number__isnull=False, profile__phone_verified=True
    ) & ~Q(profile__phone_number="")

    # --- Build profile pools based on event.profile_requirement ---
    pending_submissions_qs = ProfileSubmission.objects.none()
    profile_pool_qs = CrushProfile.objects.none()
    pool_label = ""

    if event.profile_requirement == "unverified":
        # Current behavior: pending/recontact submissions + profiles with no submission
        latest_status_for_submission = Subquery(
            ProfileSubmission.objects.filter(profile=OuterRef("profile_id"))
            .order_by("-submitted_at")
            .values("status")[:1]
        )
        pending_submissions_qs = (
            ProfileSubmission.objects.filter(
                status__in=["pending", "recontact_coach"],
            )
            .filter(sub_phone_q)
            .filter(sub_age_q)
            # De-dupe against the expired-latest pool below: a profile whose
            # newest row is expired lands there as "no submission", so an
            # older surviving pending/recontact row must not list the same
            # member a second time here.
            .annotate(profile_latest_status=latest_status_for_submission)
            .exclude(profile_latest_status="expired")
        )
        if has_language_filter:
            pending_submissions_qs = pending_submissions_qs.filter(sub_lang_q)
        pending_submissions_qs = pending_submissions_qs.select_related(
            "profile__user", "coach__user"
        ).order_by("submitted_at")

        # "No submission" here follows the expired-latest invariant: a profile
        # whose newest submission is expired (closed out by the pivot cleanup)
        # behaves like one with no submission at all, so that cohort stays
        # invitable to unverified/entry events. Approved profiles are excluded
        # to mirror event_register, which rejects is_approved users for
        # unverified events — e.g. a cleanup user who has since verified via
        # LuxID while their expired row remains the latest.
        latest_submission_status = Subquery(
            ProfileSubmission.objects.filter(profile=OuterRef("pk"))
            .order_by("-submitted_at")
            .values("status")[:1]
        )
        profile_pool_qs = (
            CrushProfile.objects.filter(phone_q)
            .filter(age_filter)
            .filter(is_approved=False)
            .annotate(latest_submission_status=latest_submission_status)
            .filter(
                Q(latest_submission_status__isnull=True)
                | Q(latest_submission_status="expired")
            )
        )
        if has_language_filter:
            profile_pool_qs = profile_pool_qs.filter(
                lang_q | Q(event_languages=[]) | Q(event_languages__isnull=True)
            )
        profile_pool_qs = profile_pool_qs.select_related("user")
        pool_label = _("Incomplete Profiles")

    elif event.profile_requirement == "completed":
        # Entry event: anyone with a completed (phone-verified) profile is a
        # candidate to invite — verified or not. They get verified in person
        # when they attend. Mirror the event_register allowlist: only verified
        # or pending (submitted) profiles qualify — never incomplete/rejected,
        # who would be bounced at registration even with a verified phone.
        profile_pool_qs = (
            CrushProfile.objects.filter(phone_q)
            .filter(verification_status__in=["verified", "pending"])
            .filter(age_filter)
        )
        if has_language_filter:
            # Strict match — registration calls user_meets_language_requirement,
            # which rejects profiles with no overlapping event language (empty or
            # NULL included). Don't invite users who'd be bounced on click-through.
            profile_pool_qs = profile_pool_qs.filter(lang_q)
        profile_pool_qs = profile_pool_qs.select_related("user")
        pool_label = _("Completed Profiles (entry event)")

    elif event.profile_requirement == "approved":
        profile_pool_qs = CrushProfile.objects.filter(phone_q, verification_status="verified").filter(
            age_filter
        )
        if has_language_filter:
            profile_pool_qs = profile_pool_qs.filter(lang_q)
        profile_pool_qs = profile_pool_qs.select_related("user")
        pool_label = _("Approved Profiles")

    elif event.profile_requirement == "coach_assigned":
        profile_pool_qs = CrushProfile.objects.filter(
            phone_q, assigned_coach__isnull=False
        ).filter(age_filter)
        if has_language_filter:
            profile_pool_qs = profile_pool_qs.filter(lang_q)
        profile_pool_qs = profile_pool_qs.select_related("user", "assigned_coach__user")
        pool_label = _("Premium Members (Coach assigned)")

    elif event.profile_requirement == "profile_exists":
        profile_pool_qs = CrushProfile.objects.filter(phone_q).filter(age_filter)
        if has_language_filter:
            profile_pool_qs = profile_pool_qs.filter(lang_q)
        profile_pool_qs = profile_pool_qs.select_related("user")
        pool_label = _("All Profiles")

    else:  # "none"
        profile_pool_qs = CrushProfile.objects.filter(phone_q).filter(age_filter)
        if has_language_filter:
            profile_pool_qs = profile_pool_qs.filter(
                lang_q | Q(event_languages=[]) | Q(event_languages__isnull=True)
            )
        profile_pool_qs = profile_pool_qs.select_related("user")
        pool_label = _("All Profiles")

    # Get IDs of submissions already sent an invite for this event
    already_sent_submission_ids = set(
        CallAttempt.objects.filter(
            event=event, result="event_invite_sms", submission__isnull=False
        ).values_list("submission_id", flat=True)
    )

    # Get IDs of profiles (without submission) already sent an invite.
    # Also counts attempts logged against a submission — e.g. an invite sent
    # while the row was still pending before the pivot cleanup expired it —
    # so an expired-latest profile landing in the no-submission pool keeps
    # its sent state instead of inviting the member twice.
    already_sent_profile_ids = set(
        CallAttempt.objects.filter(
            event=event, result="event_invite_sms", profile__isnull=False
        ).values_list("profile_id", flat=True)
    ) | set(
        CallAttempt.objects.filter(
            event=event, result="event_invite_sms", submission__isnull=False
        ).values_list("submission__profile_id", flat=True)
    )

    # Users already registered for this event
    registered_user_ids = set(
        EventRegistration.objects.filter(event=event)
        .exclude(status="cancelled")
        .values_list("user_id", flat=True)
    )

    # Bulk-load assigned coaches for pool profiles via their latest submission
    pool_profile_ids = list(profile_pool_qs.values_list("id", flat=True))
    profile_to_coach = {}
    if pool_profile_ids:
        for sub in (
            ProfileSubmission.objects.filter(profile_id__in=pool_profile_ids)
            .select_related("coach__user")
            .order_by("-submitted_at")
        ):
            profile_to_coach.setdefault(sub.profile_id, sub.coach)

    submitted_profiles = []
    unsubmitted_profiles = []
    waitlisted_profiles = []
    gender_counts = {"F": 0, "M": 0, "other": 0}
    already_sent_count = 0
    already_registered_count = 0

    def _build_sms_uri(profile, template_prefix="sms_event_invite_template"):
        """Build SMS URI for a profile using the given template prefix."""
        from django.utils import translation as _translation

        lang = getattr(profile, "preferred_language", "en") or "en"
        field = f"{template_prefix}_{lang}"
        fallback_field = f"{template_prefix}_en"
        template = getattr(config, field, None) or getattr(
            config, fallback_field, None
        )
        if not template:
            template = config.sms_event_invite_template_en
        first_name = profile.user.first_name or ""
        # Activate recipient's language so event.title (modeltranslation) and
        # the URL prefix both use their language, not the coach's.
        with _translation.override(lang):
            profile_event_url = request.build_absolute_uri(
                reverse("crush_lu:event_detail", args=[event.id])
            )
            event_title = event.title
            event_date_str = date_format(
                event.date_time, format="SHORT_DATE_FORMAT", use_l10n=True
            )
        sms_body = template.format(
            first_name=first_name,
            coach_name=coach_name,
            event_title=event_title,
            event_date=event_date_str,
            event_url=profile_event_url,
        )
        return lang, f"sms:{profile.phone_number}?body={quote(sms_body, safe='')}"

    def _count_gender(gender):
        if gender == "F":
            gender_counts["F"] += 1
        elif gender == "M":
            gender_counts["M"] += 1
        else:
            gender_counts["other"] += 1

    # Process submissions (only for "unverified" events)
    for sub in pending_submissions_qs:
        profile = sub.profile

        lang, sms_uri = _build_sms_uri(profile)
        _lm_lang, sms_uri_lm = _build_sms_uri(profile, "sms_last_minute_invite_template")

        sent = sub.id in already_sent_submission_ids
        if sent:
            already_sent_count += 1

        registered = profile.user_id in registered_user_ids
        if registered:
            already_registered_count += 1

        gender = profile.gender
        _count_gender(gender)

        submitted_profiles.append(
            {
                "submission": sub,
                "profile": profile,
                "row_id": f"sub-{sub.id}",
                "display_name": profile.display_name,
                "gender": gender,
                "age": profile.age,
                "language": lang,
                "status": sub.status,
                "sms_uri": sms_uri,
                "sms_uri_last_minute": sms_uri_lm,
                "already_sent": sent,
                "already_registered": registered,
                "assigned_coach": sub.coach,
            }
        )

    # Process profile pool
    for profile in profile_pool_qs:
        lang, sms_uri = _build_sms_uri(profile)
        _lm_lang, sms_uri_lm = _build_sms_uri(profile, "sms_last_minute_invite_template")

        sent = profile.id in already_sent_profile_ids
        if sent:
            already_sent_count += 1

        registered = profile.user_id in registered_user_ids
        if registered:
            already_registered_count += 1

        gender = profile.gender
        _count_gender(gender)

        unsubmitted_profiles.append(
            {
                "submission": None,
                "profile": profile,
                "row_id": f"prof-{profile.id}",
                "display_name": profile.display_name,
                "gender": gender,
                "age": profile.age,
                "language": lang,
                "status": "no_submission",
                "sms_uri": sms_uri,
                "sms_uri_last_minute": sms_uri_lm,
                "already_sent": sent,
                "already_registered": registered,
                "assigned_coach": profile_to_coach.get(profile.id),
            }
        )

    # Build waitlisted profiles list (priority targets for last-minute mode)
    waitlisted_qs = EventRegistration.objects.filter(
        event=event, status="waitlist"
    ).select_related("user__crushprofile")
    for reg in waitlisted_qs:
        profile = getattr(reg.user, "crushprofile", None)
        if not profile or not profile.phone_number or not profile.phone_verified:
            continue
        lang, sms_uri = _build_sms_uri(profile)
        _lm_lang, sms_uri_lm = _build_sms_uri(profile, "sms_last_minute_invite_template")
        already_sent = profile.id in already_sent_profile_ids
        waitlisted_profiles.append(
            {
                "submission": None,
                "profile": profile,
                "row_id": f"wait-{profile.id}",
                "display_name": profile.display_name,
                "gender": profile.gender,
                "age": profile.age,
                "language": lang,
                "status": "waitlist",
                "sms_uri": sms_uri,
                "sms_uri_last_minute": sms_uri_lm,
                "already_sent": already_sent,
                "already_registered": True,
                "assigned_coach": profile_to_coach.get(profile.id),
            }
        )

    cancelled_count = EventRegistration.objects.filter(
        event=event, status="cancelled"
    ).count()

    context = {
        "event": event,
        "submitted_profiles": submitted_profiles,
        "unsubmitted_profiles": unsubmitted_profiles,
        "waitlisted_profiles": waitlisted_profiles,
        "total_eligible": len(submitted_profiles) + len(unsubmitted_profiles),
        "submitted_count": len(submitted_profiles),
        "unsubmitted_count": len(unsubmitted_profiles),
        "waitlisted_count": len(waitlisted_profiles),
        "gender_counts": gender_counts,
        "already_sent_count": already_sent_count,
        "already_registered_count": already_registered_count,
        "cancelled_count": cancelled_count,
        "coach": coach,
        "profile_requirement": event.profile_requirement,
        "profile_requirement_display": event.get_profile_requirement_display(),
        "has_age_filter": has_age_filter,
        "min_age": event.min_age,
        "max_age": event.max_age,
        "has_language_filter": has_language_filter,
        "event_languages_display": event.get_languages_display,
        "pool_label": pool_label,
    }
    return render(request, "crush_lu/coach_event_sms_invite.html", context)


def _build_last_minute_sms_uri(request, event, profile, coach_name):
    """Build the last-minute SMS URI independently of the main view's nested helper."""
    from urllib.parse import quote

    from django.urls import reverse
    from django.utils import translation as _translation
    from django.utils.formats import date_format

    from .models.site_config import CrushSiteConfig

    config = CrushSiteConfig.get_config()
    template_prefix = "sms_last_minute_invite_template"
    lang = getattr(profile, "preferred_language", "en") or "en"
    field = f"{template_prefix}_{lang}"
    fallback_field = f"{template_prefix}_en"
    template = getattr(config, field, None) or getattr(config, fallback_field, None)
    if not template:
        template = config.sms_event_invite_template_en
    first_name = profile.user.first_name or ""
    with _translation.override(lang):
        profile_event_url = request.build_absolute_uri(
            reverse("crush_lu:event_detail", args=[event.id])
        )
        event_title = event.title
        event_date_str = date_format(
            event.date_time, format="SHORT_DATE_FORMAT", use_l10n=True
        )
    sms_body = template.format(
        first_name=first_name,
        coach_name=coach_name,
        event_title=event_title,
        event_date=event_date_str,
        event_url=profile_event_url,
    )
    return f"sms:{profile.phone_number}?body={quote(sms_body, safe='')}"


@coach_required
@require_http_methods(["POST"])
def coach_log_event_sms_sent(request, event_id, submission_id):
    """Log that an event invite SMS was sent - HTMX endpoint."""
    from .models import CallAttempt

    event = get_object_or_404(MeetupEvent, id=event_id)
    coach = request.coach
    submission = get_object_or_404(
        ProfileSubmission.objects.select_related("profile__user"),
        id=submission_id,
    )

    # Prevent duplicate logging
    already_exists = CallAttempt.objects.filter(
        submission=submission, event=event, result="event_invite_sms"
    ).exists()

    if not already_exists:
        CallAttempt.objects.create(
            submission=submission,
            result="event_invite_sms",
            coach=coach,
            event=event,
            notes=_("Event invite SMS sent for: %(event)s") % {"event": event.title},
        )

    sms_uri_last_minute = _build_last_minute_sms_uri(
        request, event, submission.profile, coach.user.first_name or "Coach"
    )
    return render(
        request,
        "crush_lu/_sms_invite_row_sent.html",
        {"submission": submission, "event": event, "sms_uri_last_minute": sms_uri_last_minute},
    )


@coach_required
@require_http_methods(["POST"])
def coach_log_event_sms_sent_by_profile(request, event_id, profile_id):
    """Log SMS sent for a profile that has no submission yet."""
    from .models import CallAttempt

    event = get_object_or_404(MeetupEvent, id=event_id)
    coach = request.coach
    profile = get_object_or_404(CrushProfile, id=profile_id)

    # Prevent duplicate logging
    already_exists = CallAttempt.objects.filter(
        profile=profile, event=event, result="event_invite_sms"
    ).exists()

    if not already_exists:
        CallAttempt.objects.create(
            profile=profile,
            result="event_invite_sms",
            coach=coach,
            event=event,
            notes=_("Event invite SMS sent for: %(event)s") % {"event": event.title},
        )

    sms_uri_last_minute = _build_last_minute_sms_uri(
        request, event, profile, coach.user.first_name or "Coach"
    )
    return render(
        request,
        "crush_lu/_sms_invite_row_sent.html",
        {"event": event, "sms_uri_last_minute": sms_uri_last_minute},
    )


@coach_required
def coach_member_overview(request, user_id):
    """Coach view of a member's profile, event history, and connections"""
    from django.contrib.auth.models import User

    member = get_object_or_404(User, id=user_id)

    # Get profile (may not exist for invitation-only guests)
    try:
        profile = member.crushprofile
    except CrushProfile.DoesNotExist:
        profile = None

    # All profile submissions (for full history) + latest
    all_submissions = (
        ProfileSubmission.objects.filter(profile=profile)
        .select_related("coach")
        .order_by("-submitted_at")
        if profile
        else ProfileSubmission.objects.none()
    )
    latest_submission = all_submissions.first() if profile else None

    # Social login provider (same pattern as coach_review_profile)
    social_account = member.socialaccount_set.first() if profile else None

    # Event history
    event_registrations = (
        EventRegistration.objects.filter(user=member)
        .select_related("event")
        .order_by("-event__date_time")
    )

    # Connections made
    connections = (
        EventConnection.objects.filter(Q(requester=member) | Q(recipient=member))
        .select_related("event", "requester", "recipient")
        .order_by("-requested_at")[:10]
    )

    # Available coaches for reassignment dropdown
    all_coaches = CrushCoach.objects.filter(is_active=True).select_related("user")

    # Personality traits
    if profile:
        qualities = profile.qualities.all()
        defects = profile.defects.all()
        sought_qualities = profile.sought_qualities.all()
    else:
        from .models import Trait

        qualities = defects = sought_qualities = Trait.objects.none()

    # Zodiac info (computed from date_of_birth)
    zodiac_info = None
    if profile and profile.astro_enabled and profile.date_of_birth:
        western_sign = get_western_zodiac(profile.date_of_birth)
        chinese_animal = get_chinese_zodiac(profile.date_of_birth)
        zodiac_info = {
            "western_sign": ZODIAC_SIGN_LABELS.get(western_sign, western_sign),
            "western_emoji": ZODIAC_SIGN_EMOJIS.get(western_sign, ""),
            "western_element": get_western_element(western_sign),
            "chinese_animal": CHINESE_ANIMAL_LABELS.get(chinese_animal, chinese_animal),
            "chinese_emoji": CHINESE_ANIMAL_EMOJIS.get(chinese_animal, ""),
        }

    # Call attempts
    call_attempts = (
        CallAttempt.objects.filter(profile=profile)
        .select_related("coach", "event")
        .order_by("-attempt_date")[:20]
        if profile
        else CallAttempt.objects.none()
    )

    # Coach sessions
    coach_sessions = (
        CoachSession.objects.filter(user=member)
        .select_related("coach")
        .order_by("-created_at")[:20]
    )

    # User activity
    try:
        user_activity = member.activity
    except UserActivity.DoesNotExist:
        user_activity = None

    # Crush sparks
    crush_sparks = (
        CrushSpark.objects.filter(Q(sender=member) | Q(recipient=member))
        .select_related("event", "sender", "recipient", "assigned_coach")
        .order_by("-created_at")[:10]
    )

    context = {
        "coach": request.coach,
        "member": member,
        "profile": profile,
        "latest_submission": latest_submission,
        "all_submissions": all_submissions,
        "social_account": social_account,
        "event_registrations": event_registrations,
        "connections": connections,
        "all_coaches": all_coaches,
        "qualities": qualities,
        "defects": defects,
        "sought_qualities": sought_qualities,
        "zodiac_info": zodiac_info,
        "call_attempts": call_attempts,
        "coach_sessions": coach_sessions,
        "user_activity": user_activity,
        "crush_sparks": crush_sparks,
    }
    return render(request, "crush_lu/coach_member_overview.html", context)


def _verified_profiles_by_user(user_ids):
    """Batch-fetch verified+active profiles keyed by user_id.

    One query instead of one CrushProfile lookup per match score; the
    verified + is_active filter is the coach-facing eligibility rule and
    must stay identical everywhere match counterparts are displayed.
    """
    return {
        p.user_id: p
        for p in CrushProfile.objects.filter(
            user_id__in=user_ids,
            verification_status="verified",
            is_active=True,
        )
    }


@coach_required
def coach_member_matches(request, user_id):
    """Show top matches for a member's profile (coach matchmaking view)."""
    from django.contrib.auth.models import User
    from .matching import get_matches_for_user, get_score_display

    member = get_object_or_404(User, id=user_id)

    try:
        profile = member.crushprofile
    except CrushProfile.DoesNotExist:
        messages.error(request, _("This member does not have a profile."))
        return redirect("crush_lu:coach_dashboard")

    if not profile.is_approved:
        messages.warning(request, _("This profile is not yet approved."))
        return redirect("crush_lu:coach_member_overview", user_id=user_id)

    # Trait matching is Crush Connect-only now — sought-qualities live on the
    # membership (not the profile), and that's what the scorer reads. Gate the
    # match list on the membership so coaches see the matches that actually exist.
    membership = getattr(member, "crush_connect_membership", None)
    has_traits = bool(membership and membership.sought_qualities.exists())
    matches = []

    if has_traits:
        match_scores = get_matches_for_user(member)

        profiles_by_user = _verified_profiles_by_user(
            {
                ms.user_b_id if ms.user_a_id == member.pk else ms.user_a_id
                for ms in match_scores
            }
        )

        for ms in match_scores:
            other_user = ms.user_b if ms.user_a == member else ms.user_a
            other_profile = profiles_by_user.get(other_user.pk)
            if other_profile is None:
                continue

            score_display = get_score_display(ms.score_final)
            if score_display:
                matches.append(
                    {
                        "profile": other_profile,
                        "user": other_user,
                        "score": ms.score_final,
                        "score_percent": int(ms.score_final * 100),
                        "display": score_display,
                    }
                )

    paginator = Paginator(matches, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "coach": request.coach,
        "member": member,
        "profile": profile,
        "page_obj": page_obj,
        "matches": page_obj.object_list,
        "has_traits": has_traits,
    }
    return render(request, "crush_lu/coach_member_matches.html", context)


@coach_required
def coach_match_pairs(request):
    """Show the top match for each of this coach's assigned profiles."""
    from .matching import get_score_display, THRESHOLD_GOOD
    from .models import MatchScore

    coach = request.coach

    # Step 1: Find user IDs of profiles assigned to this coach
    my_user_ids = set(
        ProfileSubmission.objects.filter(coach=coach, status="approved")
        .values_list("profile__user_id", flat=True)
        .distinct()
    )

    if not my_user_ids:
        pairs = []
    else:
        # Step 2: Get good+ match scores where at least one side is this coach's profile
        match_scores = list(
            MatchScore.objects.filter(
                Q(user_a_id__in=my_user_ids) | Q(user_b_id__in=my_user_ids),
                score_final__gte=THRESHOLD_GOOD,
            )
            .select_related("user_a", "user_b")
            .order_by("-score_final")
        )

        # Step 3: Collect all user IDs involved
        all_user_ids = set()
        for ms in match_scores:
            all_user_ids.add(ms.user_a_id)
            all_user_ids.add(ms.user_b_id)

        # Step 4: Batch-fetch profiles (only approved+active)
        profiles_by_user = _verified_profiles_by_user(all_user_ids)

        # Step 5: Batch-fetch assigned coaches via latest approved submission
        coach_map = {}
        seen_profiles = set()
        for sub in (
            ProfileSubmission.objects.filter(
                profile__user_id__in=all_user_ids,
                status="approved",
            )
            .select_related("coach__user", "profile")
            .order_by("-reviewed_at")
        ):
            uid = sub.profile.user_id
            if uid not in seen_profiles:
                seen_profiles.add(uid)
                coach_map[uid] = sub.coach

        # Step 6: Build pairs — one per coach's profile (their top match only)
        # Since match_scores is sorted by score desc, the first valid pair
        # for each of the coach's profiles is their best match.
        seen_my_profiles = set()
        pairs = []
        for ms in match_scores:
            profile_a = profiles_by_user.get(ms.user_a_id)
            profile_b = profiles_by_user.get(ms.user_b_id)
            if not profile_a or not profile_b:
                continue

            score_display = get_score_display(ms.score_final)
            if not score_display:
                continue

            # Determine which side is the coach's profile
            # A pair may have both sides as coach's profiles
            my_uid = None
            if ms.user_a_id in my_user_ids and ms.user_a_id not in seen_my_profiles:
                my_uid = ms.user_a_id
            elif ms.user_b_id in my_user_ids and ms.user_b_id not in seen_my_profiles:
                my_uid = ms.user_b_id

            if my_uid is None:
                continue

            seen_my_profiles.add(my_uid)

            # Always put the coach's profile on the left (profile_a)
            if my_uid == ms.user_a_id:
                pa, pb = profile_a, profile_b
                ca, cb = coach_map.get(ms.user_a_id), coach_map.get(ms.user_b_id)
            else:
                pa, pb = profile_b, profile_a
                ca, cb = coach_map.get(ms.user_b_id), coach_map.get(ms.user_a_id)

            pairs.append(
                {
                    "profile_a": pa,
                    "profile_b": pb,
                    "coach_a": ca,
                    "coach_b": cb,
                    "score": ms.score_final,
                    "score_percent": int(ms.score_final * 100),
                    "score_qualities": int(ms.score_qualities * 100),
                    "score_zodiac_west": int(ms.score_zodiac_west * 100),
                    "score_zodiac_cn": int(ms.score_zodiac_cn * 100),
                    "score_language": int(ms.score_language * 100),
                    "score_age_fit": int(ms.score_age_fit * 100),
                    "display": score_display,
                }
            )

    paginator = Paginator(pairs, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "coach": coach,
        "page_obj": page_obj,
        "pairs": page_obj.object_list,
        "total_pairs": len(pairs),
    }
    return render(request, "crush_lu/coach_match_pairs.html", context)


@coach_required
@require_http_methods(["POST"])
def coach_reassign_submission(request, submission_id):
    """Claim or reassign a profile submission to a different coach"""
    submission = get_object_or_404(
        ProfileSubmission.objects.select_related("profile__user", "coach"),
        id=submission_id,
    )

    action = request.POST.get("action")

    if action == "claim":
        # Current coach claims this submission for themselves
        old_coach = submission.coach
        submission.coach = request.coach
        submission.save(update_fields=["coach"])
        logger.info(
            f"Coach {request.coach} claimed submission #{submission.id} "
            f"(was: {old_coach})"
        )
        messages.success(
            request,
            _("You have claimed this profile review."),
        )
    elif action == "reassign":
        # Reassign to a specific coach
        target_coach_id = request.POST.get("target_coach_id")
        if not target_coach_id:
            messages.error(request, _("Please select a coach."))
            return redirect(
                "crush_lu:coach_member_overview",
                user_id=submission.profile.user.id,
            )
        target_coach = get_object_or_404(CrushCoach, id=target_coach_id, is_active=True)
        old_coach = submission.coach
        submission.coach = target_coach
        submission.save(update_fields=["coach"])
        logger.info(
            f"Coach {request.coach} reassigned submission #{submission.id} "
            f"from {old_coach} to {target_coach}"
        )
        messages.success(
            request,
            _("Profile review reassigned to %(coach)s.")
            % {"coach": target_coach.user.get_full_name()},
        )
    else:
        messages.error(request, _("Invalid action."))

    return redirect(
        "crush_lu:coach_member_overview", user_id=submission.profile.user.id
    )


@coach_required
def coach_view_user_progress(request, progress_id):
    """View a specific user's journey progress and answers - Enhanced Report View"""
    coach = request.coach

    from .models import (
        JourneyProgress,
        ChallengeAttempt,
        JourneyChapter,
        JourneyChallenge,
    )
    from collections import defaultdict

    progress = get_object_or_404(
        JourneyProgress.objects.select_related("user", "journey"), id=progress_id
    )

    # Get all chapters for this journey with their challenges
    chapters = (
        JourneyChapter.objects.filter(journey=progress.journey)
        .prefetch_related("challenges")
        .order_by("chapter_number")
    )

    # Get all challenge attempts for this journey
    all_attempts = (
        ChallengeAttempt.objects.filter(chapter_progress__journey_progress=progress)
        .select_related("challenge", "challenge__chapter", "chapter_progress__chapter")
        .order_by("attempted_at")
    )

    # Build a structured report: for each challenge, get the FINAL successful attempt
    # or the last attempt if none were successful
    challenge_results = {}  # challenge_id -> best attempt
    challenge_attempt_counts = defaultdict(int)  # challenge_id -> total attempts

    for attempt in all_attempts:
        challenge_id = attempt.challenge_id
        challenge_attempt_counts[challenge_id] += 1

        # Keep the attempt if:
        # 1. No attempt recorded yet for this challenge
        # 2. This attempt is correct (overrides incorrect)
        # 3. This attempt earned more points
        if challenge_id not in challenge_results:
            challenge_results[challenge_id] = attempt
        elif attempt.is_correct and not challenge_results[challenge_id].is_correct:
            challenge_results[challenge_id] = attempt
        elif attempt.points_earned > challenge_results[challenge_id].points_earned:
            challenge_results[challenge_id] = attempt

    # Build chapter data with challenges and results
    chapter_data = []
    total_challenges = 0
    completed_challenges = 0
    questionnaire_responses = []

    for chapter in chapters:
        chapter_info = {
            "chapter": chapter,
            "challenges": [],
            "chapter_points": 0,
            "is_completed": False,
        }

        # Check if chapter is completed
        chapter_progress = progress.chapter_completions.filter(chapter=chapter).first()
        if chapter_progress:
            chapter_info["is_completed"] = chapter_progress.is_completed
            chapter_info["chapter_points"] = chapter_progress.points_earned

        for challenge in chapter.challenges.all():
            total_challenges += 1
            result = challenge_results.get(challenge.id)
            attempt_count = challenge_attempt_counts.get(challenge.id, 0)

            # Determine if this is a questionnaire challenge (no correct answer)
            is_questionnaire = (
                not challenge.correct_answer
                or challenge.challenge_type in ["open_text", "would_you_rather"]
            )

            # Parse the user's answer for multiple choice to show the full option text
            display_answer = None
            if result:
                completed_challenges += 1
                display_answer = result.user_answer

                # For multiple choice, map the letter to the full option
                if challenge.challenge_type == "multiple_choice" and challenge.options:
                    answer_key = result.user_answer.strip().upper()
                    if answer_key in challenge.options:
                        display_answer = (
                            f"{answer_key}: {challenge.options[answer_key]}"
                        )

                # For timeline sorting, show as readable list
                if challenge.challenge_type == "timeline_sort" and challenge.options:
                    try:
                        order = result.user_answer.split(",")
                        items = challenge.options.get("items", [])
                        if items:
                            display_answer = [
                                items[int(i)]
                                for i in order
                                if i.strip().isdigit() and int(i) < len(items)
                            ]
                    except (ValueError, IndexError):
                        pass

                # Collect questionnaire responses for insights section
                if is_questionnaire and result.user_answer:
                    questionnaire_responses.append(
                        {
                            "chapter": chapter,
                            "challenge": challenge,
                            "answer": result.user_answer,
                            "display_answer": display_answer,
                        }
                    )

            challenge_info = {
                "challenge": challenge,
                "result": result,
                "attempt_count": attempt_count,
                "is_questionnaire": is_questionnaire,
                "display_answer": display_answer,
                "options": challenge.options,
            }
            chapter_info["challenges"].append(challenge_info)

        chapter_data.append(chapter_info)

    # Calculate journey statistics
    journey_duration = None
    if progress.started_at and progress.final_response_at:
        journey_duration = progress.final_response_at - progress.started_at

    stats = {
        "total_challenges": total_challenges,
        "completed_challenges": completed_challenges,
        "total_attempts": sum(challenge_attempt_counts.values()),
        "avg_attempts_per_challenge": round(
            sum(challenge_attempt_counts.values()) / max(completed_challenges, 1), 1
        ),
        "journey_duration": journey_duration,
        "hardest_challenge": (
            max(challenge_attempt_counts.items(), key=lambda x: x[1])
            if challenge_attempt_counts
            else None
        ),
    }

    # Find the hardest challenge details
    if stats["hardest_challenge"]:
        hardest_id = stats["hardest_challenge"][0]
        hardest_challenge = JourneyChallenge.objects.filter(id=hardest_id).first()
        stats["hardest_challenge_obj"] = hardest_challenge
        stats["hardest_challenge_attempts"] = stats["hardest_challenge"][1]

    context = {
        "coach": coach,
        "progress": progress,
        "chapter_data": chapter_data,
        "stats": stats,
        "questionnaire_responses": questionnaire_responses,
        "all_attempts": all_attempts,  # Keep for backward compatibility
    }
    return render(request, "crush_lu/coach_view_user_progress.html", context)


@coach_required
def coach_verification_history(request):
    """Browse all past profile verifications with filters"""
    coach = request.coach

    submissions = (
        ProfileSubmission.objects.filter(
            coach=coach,
            status__in=["approved", "rejected", "revision", "recontact_coach"],
        )
        .select_related("profile__user")
        .order_by("-reviewed_at")
    )

    # Filter by status
    status_filter = request.GET.get("status", "all")
    if status_filter in ("approved", "rejected", "revision", "recontact_coach"):
        submissions = submissions.filter(status=status_filter)

    # Filter by gender
    gender_filter = request.GET.get("gender", "all")
    if gender_filter == "F":
        submissions = submissions.filter(profile__gender="F")
    elif gender_filter == "M":
        submissions = submissions.filter(profile__gender="M")
    elif gender_filter == "other":
        submissions = submissions.filter(profile__gender__in=["NB", "O", "P", ""])

    # Filter by date range
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    if date_from:
        try:
            from datetime import datetime

            dt = datetime.strptime(date_from, "%Y-%m-%d")
            submissions = submissions.filter(reviewed_at__date__gte=dt.date())
        except ValueError:
            pass
    if date_to:
        try:
            from datetime import datetime

            dt = datetime.strptime(date_to, "%Y-%m-%d")
            submissions = submissions.filter(reviewed_at__date__lte=dt.date())
        except ValueError:
            pass

    paginator = Paginator(submissions, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "coach": coach,
        "page_obj": page_obj,
        "status_filter": status_filter,
        "gender_filter": gender_filter,
        "date_from": date_from,
        "date_to": date_to,
        "total_count": paginator.count,
    }
    return render(request, "crush_lu/coach_verification_history.html", context)


# ============================================================================
# COACH CONNECTION MANAGEMENT - Review and approve post-event connections
# ============================================================================


def _profile_display_name(user):
    profile = getattr(user, "crushprofile", None)
    if profile and getattr(profile, "display_name", None):
        return profile.display_name
    return user.first_name or user.username


def _compute_connection_next_action(conn, current_coach):
    """Plain-language summary of who needs to act next on a connection.

    Returns a dict consumed by coach_connections.html so the row can show
    "Waiting on Sarah to consent" instead of just "Approved" — much faster
    triage for a coach scanning the inbox.
    """
    requester_name = _profile_display_name(conn.requester)
    recipient_name = _profile_display_name(conn.recipient)

    if conn.status == "shared":
        return {
            "kind": "done",
            "who_label": "",
            "what_label": _("Contact info shared"),
            "is_for_coach": False,
            "icon": "check",
        }

    if conn.status == "declined":
        return {
            "kind": "declined",
            "who_label": "",
            "what_label": _("Declined"),
            "is_for_coach": False,
            "icon": "x",
        }

    if conn.status == "pending":
        # Awaiting the recipient's response (accept/decline). When mutual,
        # the reverse row exists in 'pending' too and the system will flip
        # both to 'accepted' — but from the recipient's perspective on this
        # row, they still owe a response.
        return {
            "kind": "wait_recipient",
            "who_label": recipient_name,
            "what_label": _("Respond to the request"),
            "is_for_coach": False,
            "icon": "user",
        }

    if conn.status == "accepted":
        if conn.assigned_coach is None:
            return {
                "kind": "coach_claim",
                "who_label": _("Any coach"),
                "what_label": _("Claim & start review"),
                "is_for_coach": True,
                "icon": "hand",
            }
        is_for_me = (
            current_coach is not None
            and conn.assigned_coach_id == current_coach.id
        )
        coach_label = (
            _("You") if is_for_me else (
                conn.assigned_coach.user.first_name
                or conn.assigned_coach.user.username
            )
        )
        return {
            "kind": "coach_review",
            "who_label": coach_label,
            "what_label": _("Start the review"),
            "is_for_coach": is_for_me,
            "icon": "review",
        }

    if conn.status == "coach_reviewing":
        is_for_me = (
            current_coach is not None
            and conn.assigned_coach_id == current_coach.id
        )
        coach_label = _("You")
        if not is_for_me and conn.assigned_coach:
            coach_label = (
                conn.assigned_coach.user.first_name
                or conn.assigned_coach.user.username
            )
        if not (conn.coach_introduction or "").strip():
            return {
                "kind": "coach_write_intro",
                "who_label": coach_label,
                "what_label": _("Write the introduction"),
                "is_for_coach": is_for_me,
                "icon": "pencil",
            }
        return {
            "kind": "coach_approve",
            "who_label": coach_label,
            "what_label": _("Approve to share contacts"),
            "is_for_coach": is_for_me,
            "icon": "check",
        }

    if conn.status == "coach_approved":
        missing = []
        if not conn.requester_consents_to_share:
            missing.append(requester_name)
        if not conn.recipient_consents_to_share:
            missing.append(recipient_name)
        if missing:
            return {
                "kind": "await_consent",
                "who_label": " & ".join(missing),
                "what_label": _("Consent to share contact info"),
                "is_for_coach": False,
                "icon": "user",
            }
        return {
            "kind": "ready_to_share",
            "who_label": _("System"),
            "what_label": _("Both consented — ready to share"),
            "is_for_coach": False,
            "icon": "check",
        }

    return {
        "kind": "unknown",
        "who_label": "",
        "what_label": "",
        "is_for_coach": False,
        "icon": "info",
    }


# Stage progress: returns a list of (label, completed) tuples for the dot bar.
_CONNECTION_STAGES = [
    ("requested", "pending"),  # always reached
    ("accepted", "accepted"),
    ("reviewed", "coach_reviewing"),
    ("approved", "coach_approved"),
    ("shared", "shared"),
]
_STAGE_RANK = {
    "pending": 0,
    "accepted": 1,
    "coach_reviewing": 2,
    "coach_approved": 3,
    "shared": 4,
    "declined": -1,
}


def _compute_connection_stages(conn):
    """Five-step progress dots for the row (requested → shared)."""
    rank = _STAGE_RANK.get(conn.status, -1)
    intro_done = bool((conn.coach_introduction or "").strip())
    return [
        {"label": _("Requested"), "done": rank >= 0},
        {"label": _("Accepted"), "done": rank >= 1},
        {"label": _("Reviewed"), "done": rank >= 2 and intro_done},
        {"label": _("Approved"), "done": rank >= 3},
        {"label": _("Shared"), "done": rank >= 4},
    ]


@coach_required
def coach_connections(request):
    """Coach view to review and manage post-event connections."""
    coach = request.coach

    # Filter by event if specified
    event_id = request.GET.get("event")
    status_filter = request.GET.get("status", "needs_review")

    # Validate status filter
    valid_statuses = {"pending", "needs_review", "approved", "shared", "all"}
    if status_filter not in valid_statuses:
        status_filter = "needs_review"

    # Base queryset: connections assigned to this coach (or all for now)
    connections_qs = EventConnection.objects.select_related(
        "requester__crushprofile",
        "recipient__crushprofile",
        "event",
        "assigned_coach__user",
    ).order_by("-requested_at")

    # Status filter
    if status_filter == "pending":
        # One-way requests not yet reciprocated
        connections_qs = connections_qs.filter(status="pending")
    elif status_filter == "needs_review":
        # Accepted (mutual) connections that need coach to review and approve
        connections_qs = connections_qs.filter(
            status__in=["accepted", "coach_reviewing"]
        )
    elif status_filter == "approved":
        connections_qs = connections_qs.filter(status="coach_approved")
    elif status_filter == "shared":
        connections_qs = connections_qs.filter(status="shared")
    elif status_filter == "all":
        connections_qs = connections_qs.exclude(status="declined")

    # Event filter
    event = None
    if event_id:
        try:
            event = MeetupEvent.objects.get(id=event_id)
            connections_qs = connections_qs.filter(event=event)
        except (MeetupEvent.DoesNotExist, ValueError):
            pass

    # Coach filter - show connections assigned to this coach, plus unassigned
    my_connections_filter = request.GET.get("mine", "")
    if my_connections_filter == "1":
        connections_qs = connections_qs.filter(
            Q(assigned_coach=coach) | Q(assigned_coach__isnull=True)
        )

    # Use mutual annotation to avoid N+1 queries, then push mutual matches to the
    # top of the list so coaches triage the most actionable rows first.
    connections_qs = connections_qs.annotate_is_mutual().order_by(
        "-is_mutual_annotated", "-requested_at"
    )

    # Paginate
    paginator = Paginator(connections_qs, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # Annotate page items with next-action and progress dots so the template
    # can show "Waiting on Sarah to consent" instead of just "Approved".
    for conn in page_obj.object_list:
        conn.next_action = _compute_connection_next_action(conn, coach)
        conn.stages = _compute_connection_stages(conn)
        conn.requester_display_name = _profile_display_name(conn.requester)
        conn.recipient_display_name = _profile_display_name(conn.recipient)

    # Batch-fetch the onboarding coach per user (the coach who approved their
    # ProfileSubmission). Surfaced next to each avatar so the reviewing coach
    # knows who originally onboarded the people involved.
    user_ids = set()
    for conn in page_obj.object_list:
        user_ids.add(conn.requester_id)
        user_ids.add(conn.recipient_id)
    onboarding_by_user = {}
    if user_ids:
        approved_subs = (
            ProfileSubmission.objects.filter(
                profile__user_id__in=user_ids, status="approved"
            )
            .select_related("coach__user", "profile")
            .order_by("-reviewed_at", "-id")
        )
        for sub in approved_subs:
            uid = sub.profile.user_id
            if uid in onboarding_by_user:
                continue  # keep the most-recent approval per user
            onboarding_by_user[uid] = sub.coach
    for conn in page_obj.object_list:
        conn.requester_onboarding_coach = onboarding_by_user.get(conn.requester_id)
        conn.recipient_onboarding_coach = onboarding_by_user.get(conn.recipient_id)

    # Stats for the header (single query instead of 4)
    stats = EventConnection.objects.aggregate(
        pending_count=Count(
            Case(
                When(status="pending", then=Value(1)),
                output_field=IntegerField(),
            )
        ),
        needs_review_count=Count(
            Case(
                When(status__in=["accepted", "coach_reviewing"], then=Value(1)),
                output_field=IntegerField(),
            )
        ),
        approved_count=Count(
            Case(
                When(status="coach_approved", then=Value(1)),
                output_field=IntegerField(),
            )
        ),
        shared_count=Count(
            Case(
                When(status="shared", then=Value(1)),
                output_field=IntegerField(),
            )
        ),
    )
    pending_count = stats["pending_count"]
    needs_review_count = stats["needs_review_count"]
    approved_count = stats["approved_count"]
    shared_count = stats["shared_count"]

    # Get events that have connections for the filter dropdown
    events_with_connections = MeetupEvent.objects.filter(
        id__in=EventConnection.objects.values_list("event_id", flat=True).distinct()
    ).order_by("-date_time")[:20]

    context = {
        "coach": coach,
        "page_obj": page_obj,
        "status_filter": status_filter,
        "event_filter": event,
        "event_id": event_id or "",
        "mine_filter": my_connections_filter,
        "pending_count": pending_count,
        "needs_review_count": needs_review_count,
        "approved_count": approved_count,
        "shared_count": shared_count,
        "events_with_connections": events_with_connections,
    }
    return render(request, "crush_lu/coach_connections.html", context)


@coach_required
@require_http_methods(["GET", "POST"])
def coach_connection_review(request, connection_id):
    """Coach review of an individual connection - write introduction and approve."""
    coach = request.coach

    connection = get_object_or_404(
        EventConnection.objects.select_related(
            "requester__crushprofile",
            "recipient__crushprofile",
            "event",
            "assigned_coach__user",
        ),
        id=connection_id,
    )

    # Get messages for this connection
    connection_messages = connection.messages.select_related("sender").order_by(
        "sent_at"
    )

    # Check for the reverse connection (mutual)
    reverse_connection = EventConnection.objects.filter(
        requester=connection.recipient,
        recipient=connection.requester,
        event=connection.event,
    ).first()

    if request.method == "POST":
        action = request.POST.get("action")

        # Ownership check: only the assigned coach (or unassigned) can act
        if action != "claim":
            if connection.assigned_coach and connection.assigned_coach != coach:
                messages.error(
                    request, _("This connection is assigned to another coach.")
                )
                return redirect("crush_lu:coach_connections")

        if action == "start_review":
            # Coach starts reviewing - transition from accepted to coach_reviewing
            if connection.status == "accepted":
                with transaction.atomic():
                    connection.status = "coach_reviewing"
                    connection.assigned_coach = coach
                    connection.save(update_fields=["status", "assigned_coach"])
                    if reverse_connection and reverse_connection.status == "accepted":
                        reverse_connection.status = "coach_reviewing"
                        reverse_connection.assigned_coach = coach
                        reverse_connection.save(
                            update_fields=["status", "assigned_coach"]
                        )
                messages.success(request, _("Connection is now under your review."))

        elif action == "save_notes":
            # Save coach notes and introduction without changing status
            connection.coach_notes = request.POST.get("coach_notes", "").strip()
            connection.coach_introduction = request.POST.get(
                "coach_introduction", ""
            ).strip()
            connection.assigned_coach = coach
            connection.save(
                update_fields=["coach_notes", "coach_introduction", "assigned_coach"]
            )
            messages.success(request, _("Notes saved."))

        elif action == "approve":
            # Approve the connection - move to coach_approved
            with transaction.atomic():
                connection.coach_notes = request.POST.get("coach_notes", "").strip()
                connection.coach_introduction = request.POST.get(
                    "coach_introduction", ""
                ).strip()
                connection.status = "coach_approved"
                connection.assigned_coach = coach
                connection.coach_approved_at = timezone.now()
                connection.save(
                    update_fields=[
                        "coach_notes",
                        "coach_introduction",
                        "status",
                        "assigned_coach",
                        "coach_approved_at",
                    ]
                )

                # Also approve the reverse connection if it exists
                if reverse_connection and reverse_connection.status in [
                    "accepted",
                    "coach_reviewing",
                ]:
                    reverse_connection.status = "coach_approved"
                    reverse_connection.assigned_coach = coach
                    reverse_connection.coach_approved_at = timezone.now()
                    reverse_connection.coach_notes = connection.coach_notes
                    reverse_connection.coach_introduction = (
                        connection.coach_introduction
                    )
                    reverse_connection.save(
                        update_fields=[
                            "status",
                            "assigned_coach",
                            "coach_approved_at",
                            "coach_notes",
                            "coach_introduction",
                        ]
                    )

            messages.success(
                request,
                _(
                    "Connection approved! Both users can now consent to share contact info."
                ),
            )
            return redirect("crush_lu:coach_connections")

        elif action == "claim":
            # Claim unassigned connection
            with transaction.atomic():
                connection.assigned_coach = coach
                connection.save(update_fields=["assigned_coach"])
                if reverse_connection:
                    reverse_connection.assigned_coach = coach
                    reverse_connection.save(update_fields=["assigned_coach"])
            messages.success(request, _("Connection claimed."))

        elif action == "send_message":
            # Coach sends a facilitation message
            message_text = request.POST.get("message", "").strip()
            if message_text and len(message_text) <= 500:
                from .models import ConnectionMessage

                ConnectionMessage.objects.create(
                    connection=connection,
                    sender=request.user,
                    message=message_text,
                    is_coach_message=True,
                )
                messages.success(request, _("Coach message sent."))
            else:
                messages.error(
                    request, _("Please enter a valid message (max 500 characters).")
                )

        return redirect("crush_lu:coach_connection_review", connection_id=connection_id)

    # GET: Show review page
    requester_profile = getattr(connection.requester, "crushprofile", None)
    recipient_profile = getattr(connection.recipient, "crushprofile", None)

    # Show facilitation form when reviewing, approved, or accepted with assigned coach
    show_facilitation = connection.status in (
        "coach_reviewing",
        "coach_approved",
    ) or (connection.status == "accepted" and connection.assigned_coach)

    # Coach intro starter templates from site config (with sensible defaults)
    from .models import CrushSiteConfig

    site_config = CrushSiteConfig.get_config()
    intro_templates = site_config.get_connection_intro_templates()
    category_labels = dict(CrushSiteConfig.INTRO_TEMPLATE_CATEGORIES)
    intro_templates_for_picker = [
        {
            "id": idx,
            "category": tpl.get("category", "other"),
            "category_label": category_labels.get(
                tpl.get("category", "other"), tpl.get("category", "other")
            ),
            "language": tpl.get("language", "en"),
            "body": tpl.get("body", ""),
        }
        for idx, tpl in enumerate(intro_templates)
        if tpl.get("body")
    ]

    context = {
        "coach": coach,
        "connection": connection,
        "reverse_connection": reverse_connection,
        "requester_profile": requester_profile,
        "recipient_profile": recipient_profile,
        "connection_messages": connection_messages,
        "is_mutual": reverse_connection is not None,
        "show_facilitation": show_facilitation,
        "intro_templates": intro_templates_for_picker,
    }
    return render(request, "crush_lu/coach_connection_review.html", context)


# ============================================================================
# Coach Team Stats
# ============================================================================


def _compute_reassignment_suggestions(coach_data, unassigned_submissions):
    """
    Compute suggested reassignments for unassigned and overloaded submissions.

    Scores available coaches by language match (+10) and load headroom (+capacity remaining).
    Returns a list of suggestion dicts for the Alpine component.
    """
    from django.utils.translation import gettext as _gt

    suggestions = []

    # Build list of coaches with capacity
    available_coaches = [
        cd for cd in coach_data if not cd["is_overloaded"] and cd["coach"].is_active
    ]

    if not available_coaches:
        return suggestions

    def _score_coach(cd, profile_languages):
        """Score a coach for a given profile based on language match and headroom."""
        score = 0
        reason_parts = []
        coach_langs = cd["coach"].spoken_languages or []

        # Language match: +10 per matching language
        if profile_languages:
            matching = set(profile_languages) & set(coach_langs)
            if matching:
                score += 10 * len(matching)
                reason_parts.append(
                    _gt("Language match: %(langs)s") % {"langs": ", ".join(matching)}
                )

        # Load headroom: prefer emptier coaches
        headroom = cd["max_active_reviews"] - cd["pending_count"]
        score += headroom
        if not reason_parts:
            reason_parts.append(_gt("Load balancing"))

        return score, "; ".join(reason_parts)

    # Suggest coaches for unassigned submissions
    for submission in unassigned_submissions:
        profile_langs = getattr(submission.profile, "event_languages", None) or []
        best_score = -1
        best_coach = None
        best_reason = ""

        for cd in available_coaches:
            score, reason = _score_coach(cd, profile_langs)
            if score > best_score:
                best_score = score
                best_coach = cd
                best_reason = reason

        if best_coach:
            suggestions.append(
                {
                    "submission_id": submission.id,
                    "profile_display_name": submission.profile.display_name,
                    "current_coach_name": _gt("Unassigned"),
                    "suggested_coach_id": best_coach["coach"].id,
                    "suggested_coach_name": best_coach["coach"].user.get_full_name()
                    or best_coach["coach"].user.username,
                    "reason": best_reason,
                }
            )

    # Suggest redistribution for overloaded coaches
    overloaded_ids = [cd["coach"].id for cd in coach_data if cd["is_overloaded"]]
    if overloaded_ids:
        overloaded_submissions = ProfileSubmission.objects.filter(
            status="pending", coach_id__in=overloaded_ids
        ).select_related("profile", "profile__user", "coach", "coach__user")

        # Group by coach, take excess submissions
        from collections import defaultdict

        by_coach = defaultdict(list)
        for sub in overloaded_submissions:
            by_coach[sub.coach_id].append(sub)

        coach_max_map = {cd["coach"].id: cd["max_active_reviews"] for cd in coach_data}

        for coach_id, subs in by_coach.items():
            max_reviews = coach_max_map.get(coach_id, 10)
            target = int(max_reviews * 0.75)
            excess = len(subs) - target
            if excess <= 0:
                continue

            # Take the most recent excess submissions as candidates
            candidates = sorted(subs, key=lambda s: s.submitted_at, reverse=True)[
                :excess
            ]
            for submission in candidates:
                profile_langs = (
                    getattr(submission.profile, "event_languages", None) or []
                )
                best_score = -1
                best_coach = None
                best_reason = ""

                for cd in available_coaches:
                    if cd["coach"].id == coach_id:
                        continue
                    score, reason = _score_coach(cd, profile_langs)
                    if score > best_score:
                        best_score = score
                        best_coach = cd
                        best_reason = reason

                if best_coach:
                    current_coach = submission.coach
                    suggestions.append(
                        {
                            "submission_id": submission.id,
                            "profile_display_name": submission.profile.display_name,
                            "current_coach_name": current_coach.user.get_full_name()
                            or current_coach.user.username,
                            "suggested_coach_id": best_coach["coach"].id,
                            "suggested_coach_name": best_coach[
                                "coach"
                            ].user.get_full_name()
                            or best_coach["coach"].user.username,
                            "reason": best_reason,
                        }
                    )

    return suggestions


from crush_lu.utils.formatting import format_duration as _format_duration


@coach_required
def coach_team_stats(request):
    """Coach team stats - cross-coach performance and workload view."""
    coach = request.coach

    # Query 1: All active coaches with review count annotations
    coaches = (
        CrushCoach.objects.filter(is_active=True)
        .annotate(
            pending_count=Count(
                "profilesubmission",
                filter=Q(profilesubmission__status="pending"),
            ),
            total_approved=Count(
                "profilesubmission",
                filter=Q(profilesubmission__status="approved"),
            ),
            total_rejected=Count(
                "profilesubmission",
                filter=Q(profilesubmission__status="rejected"),
            ),
            total_revision=Count(
                "profilesubmission",
                filter=Q(profilesubmission__status="revision"),
            ),
            total_recontact=Count(
                "profilesubmission",
                filter=Q(profilesubmission__status="recontact_coach"),
            ),
        )
        .select_related("user")
        .order_by("user__first_name")
    )

    # Query 2: Avg time-to-review per coach
    review_times = (
        ProfileSubmission.objects.filter(
            coach__is_active=True,
            reviewed_at__isnull=False,
        )
        .values("coach_id")
        .annotate(
            avg_review_time=Avg(
                ExpressionWrapper(
                    F("reviewed_at") - F("submitted_at"),
                    output_field=DurationField(),
                )
            )
        )
    )
    review_time_map = {rt["coach_id"]: rt["avg_review_time"] for rt in review_times}

    # Query 3: Unassigned pending submissions
    unassigned = ProfileSubmission.objects.filter(
        status="pending", coach__isnull=True
    ).select_related("profile", "profile__user")

    # Build coach data for template
    coach_data = []
    for c in coaches:
        total_completed = (
            c.total_approved + c.total_rejected + c.total_revision + c.total_recontact
        )
        approval_rate = (
            round(c.total_approved * 100 / total_completed) if total_completed else 0
        )
        rejection_rate = (
            round(c.total_rejected * 100 / total_completed) if total_completed else 0
        )
        avg_time = review_time_map.get(c.id)
        is_overloaded = c.pending_count >= c.max_active_reviews
        capacity_pct = (
            round(c.pending_count * 100 / c.max_active_reviews)
            if c.max_active_reviews
            else 100
        )

        coach_data.append(
            {
                "coach": c,
                "pending_count": c.pending_count,
                "max_active_reviews": c.max_active_reviews,
                "capacity_pct": min(capacity_pct, 100),
                "total_completed": total_completed,
                "total_approved": c.total_approved,
                "total_rejected": c.total_rejected,
                "total_revision": c.total_revision,
                "total_recontact": c.total_recontact,
                "approval_rate": approval_rate,
                "rejection_rate": rejection_rate,
                "avg_review_time": _format_duration(avg_time),
                "languages": c.get_spoken_languages_display,
                "specializations": c.specializations,
                "is_overloaded": is_overloaded,
                "is_current": c.id == coach.id,
            }
        )

    # Compute suggestions and mark which ones the current coach can claim
    suggestions = _compute_reassignment_suggestions(coach_data, unassigned)
    for s in suggestions:
        s["is_claimable"] = s["suggested_coach_id"] == coach.id or s[
            "current_coach_name"
        ] == str(_("Unassigned"))

    context = {
        "coach": coach,
        "coach_data": coach_data,
        "unassigned_submissions": unassigned,
        "unassigned_count": unassigned.count(),
        "suggestions": suggestions,
        "can_claim": coach.can_accept_reviews(),
    }
    return render(request, "crush_lu/coach_team_stats.html", context)


SORT_CHOICES = ("urgency", "name", "match")


def _build_outreach_context(coach, profile):
    """SMS-template + WhatsApp URL context for the screening tab partial.

    Returns ``sms_template_encoded`` and ``whatsapp_url`` together so callers
    don't drift apart. Both are empty strings when the candidate's phone
    isn't verified — the gate matches the existing SMS path.
    """
    if not (profile.phone_number and profile.phone_verified):
        return {"sms_template_encoded": "", "whatsapp_url": ""}

    from urllib.parse import quote
    import re
    from .models.site_config import CrushSiteConfig

    config = CrushSiteConfig.get_config()
    lang = getattr(profile, "preferred_language", "en") or "en"
    template_field = f"sms_template_{lang}"
    template = (
        getattr(config, template_field, config.sms_template_en)
        or config.sms_template_en
    )
    coach_name = coach.user.first_name or "Your coach"
    first_name = profile.user.first_name or ""
    sms_body = template.format(first_name=first_name, coach_name=coach_name)
    sms_template_encoded = quote(sms_body, safe="")

    digits = re.sub(r"[^\d]", "", profile.phone_number)
    whatsapp_url = (
        f"https://wa.me/{digits}?text={sms_template_encoded}" if digits else ""
    )

    return {
        "sms_template_encoded": sms_template_encoded,
        "whatsapp_url": whatsapp_url,
    }


def _score_submission_for_coach(coach, submission, hours_waiting):
    """Phase-1 coach matching score (0-100) plus human reason chips.

    Pure function so it's easy to test and to relocate later if we promote
    this into a CoachMatchSuggestion model.
    """
    score = 0
    reasons = []

    coach_langs = set(coach.spoken_languages or [])
    profile_langs = set(submission.profile.event_languages or [])
    if coach_langs & profile_langs:
        score += 50
        reasons.append("language")

    waiting_score = min(hours_waiting, 72.0) / 72.0 * 30.0
    score += waiting_score
    if hours_waiting > 48:
        reasons.append("waiting")

    profile = submission.profile
    completeness = 0
    if profile.photo_1:
        completeness += 10
    if profile.bio:
        completeness += 5
    if profile.location:
        completeness += 5
    score += completeness
    if completeness >= 15:
        reasons.append("complete")

    return round(score), reasons


@coach_required
def coach_verification_channel(request):
    """Coach-facing channel: every active coach sees all pending, unclaimed profiles
    and claims the ones they want. Race-safe claim logic lives in api_coach_claim_submission.
    """
    coach = request.coach
    now = timezone.now()

    sort_mode = request.GET.get("sort", "urgency")
    if sort_mode not in SORT_CHOICES:
        sort_mode = "urgency"
    filter_lang = (request.GET.get("lang") or "").strip().lower()
    filter_region = (request.GET.get("region") or "").strip()

    def _parse_age(raw):
        # Clamp to [18, 120] so crafted query params can't push date.replace()
        # past the year-range limits and 500 the page.
        try:
            value = int(raw or 0)
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        return max(18, min(value, 120))

    filter_age_min = _parse_age(request.GET.get("age_min"))
    filter_age_max = _parse_age(request.GET.get("age_max"))
    if filter_age_min and filter_age_max and filter_age_min > filter_age_max:
        filter_age_min, filter_age_max = filter_age_max, filter_age_min

    qs = ProfileSubmission.objects.filter(
        status="pending", coach__isnull=True
    ).select_related("profile__user")

    if filter_region:
        qs = qs.filter(profile__location__icontains=filter_region)
    if filter_age_min or filter_age_max:
        today = now.date()

        def _shift_year(d, years_back):
            try:
                return d.replace(year=d.year - years_back)
            except ValueError:
                return d.replace(year=d.year - years_back, day=28)

        if filter_age_max:
            earliest_dob = _shift_year(today, filter_age_max + 1)
            qs = qs.filter(profile__date_of_birth__gt=earliest_dob)
        if filter_age_min:
            latest_dob = _shift_year(today, filter_age_min)
            qs = qs.filter(profile__date_of_birth__lte=latest_dob)

    channel = list(qs)

    if filter_lang:
        channel = [
            s for s in channel
            if filter_lang in (s.profile.event_languages or [])
        ]

    coach_langs = set(coach.spoken_languages or [])
    for s in channel:
        hours = (now - s.submitted_at).total_seconds() / 3600
        s.is_urgent = hours > 48
        s.is_warning = 24 < hours <= 48
        s.hours_waiting = hours
        profile_langs = set(s.profile.event_languages or [])
        s.matched_languages = sorted(coach_langs & profile_langs)
        s.language_match = bool(s.matched_languages)
        s.match_score, s.match_reasons = _score_submission_for_coach(coach, s, hours)
        s.is_recommended = False

    if sort_mode == "name":
        channel.sort(key=lambda s: (s.profile.display_name or "").casefold())
    elif sort_mode == "match":
        channel.sort(key=lambda s: (-s.match_score, s.submitted_at))
        for s in channel[:3]:
            s.is_recommended = True
    else:
        channel.sort(key=lambda s: s.submitted_at)

    my_pending = ProfileSubmission.objects.filter(coach=coach, status="pending").count()

    context = {
        "coach": coach,
        "channel": channel,
        "channel_count": len(channel),
        "my_pending": my_pending,
        "at_capacity": not coach.can_accept_reviews(),
        "sort_mode": sort_mode,
        "sort_choices": SORT_CHOICES,
        "filter_lang": filter_lang,
        "filter_region": filter_region,
        "filter_age_min": filter_age_min,
        "filter_age_max": filter_age_max,
        "has_filters": bool(filter_lang or filter_region or filter_age_min or filter_age_max),
        "language_options": list(CrushCoach.LANGUAGE_DISPLAY.items()),
    }
    return render(request, "crush_lu/coach_verification_channel.html", context)


@coach_required
@require_http_methods(["POST"])
def api_coach_claim_submission(request):
    """API: Coach claims a pending submission for themselves."""
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)

    submission_id = data.get("submission_id")
    if not submission_id:
        return JsonResponse(
            {"success": False, "error": "Missing submission_id"}, status=400
        )

    coach = request.coach

    # Pre-check capacity (fast path)
    if not coach.can_accept_reviews():
        return JsonResponse(
            {"success": False, "error": str(_("You are at capacity"))}, status=409
        )

    with transaction.atomic():
        submission = (
            ProfileSubmission.objects.select_for_update()
            .filter(id=submission_id, status="pending")
            .first()
        )

        if not submission:
            return JsonResponse(
                {
                    "success": False,
                    "error": str(_("Submission not found or no longer pending")),
                },
                status=404,
            )

        # Re-check capacity inside the lock
        current_pending = ProfileSubmission.objects.filter(
            coach=coach, status="pending"
        ).count()
        if current_pending >= coach.max_active_reviews:
            return JsonResponse(
                {"success": False, "error": str(_("You are at capacity"))}, status=409
            )

        old_coach = submission.coach
        submission.coach = coach
        submission.save(update_fields=["coach"])

    logger.info(
        "Coach %s claimed submission #%d (was: %s)", coach, submission.id, old_coach
    )

    # Follow-up broadcast: replace the "new in channel" banner on other coaches.
    try:
        from .coach_notifications import broadcast_submission_claimed

        broadcast_submission_claimed(submission, claimed_by=coach)
    except Exception as e:
        logger.warning(f"Failed to broadcast submission claim: {e}")

    return JsonResponse(
        {
            "success": True,
            "message": str(_("Profile claimed successfully")),
            "submission_id": submission.id,
        }
    )
