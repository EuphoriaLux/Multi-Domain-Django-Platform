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
    EventInvitation,
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
    CoachSessionForm,
)
from .decorators import crush_login_required, coach_required
from .notification_service import (
    notify_profile_approved,
    notify_profile_revision,
    notify_profile_rejected,
)
from .referrals import check_and_apply_profile_approved_reward
from .coach_notifications import (
    notify_coach_new_submission,
    notify_coach_user_revision,
)


# Coach views
@coach_required
def coach_dashboard(request):
    """Coach dashboard - analytics and statistics hub"""
    from datetime import date

    coach = request.coach
    now = timezone.now()

    # --- Row 1: Summary stat cards ---
    approved_profiles = CrushProfile.objects.filter(is_approved=True)
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
        gender_bars.append(
            {"label": label, "count": count, "pct": pct, "color": color}
        )

    # Age distribution (with gender breakdown)
    today = date.today()
    dob_gender_list = list(
        approved_profiles.exclude(date_of_birth__isnull=True).values_list(
            "date_of_birth", "gender"
        )
    )
    age_buckets = [
        {"label": "18-24", "min": 18, "max": 24, "count": 0, "count_f": 0, "count_m": 0, "count_other": 0},
        {"label": "25-30", "min": 25, "max": 30, "count": 0, "count_f": 0, "count_m": 0, "count_other": 0},
        {"label": "31-35", "min": 31, "max": 35, "count": 0, "count_f": 0, "count_m": 0, "count_other": 0},
        {"label": "36-40", "min": 36, "max": 40, "count": 0, "count_f": 0, "count_m": 0, "count_other": 0},
        {"label": "41-50", "min": 41, "max": 50, "count": 0, "count_f": 0, "count_m": 0, "count_other": 0},
        {"label": "50+", "min": 51, "max": 999, "count": 0, "count_f": 0, "count_m": 0, "count_other": 0},
    ]
    for dob, gender in dob_gender_list:
        age = (
            today.year
            - dob.year
            - ((today.month, today.day) < (dob.month, dob.day))
        )
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
            qs = approved_profiles.exclude(gender__in=["F", "M"]).exclude(event_languages=[])
        else:
            qs = approved_profiles.filter(gender=gender_code).exclude(event_languages=[])
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
    for dob, _gender, langs in approved_profiles.exclude(
        date_of_birth__isnull=True
    ).exclude(event_languages=[]).values_list("date_of_birth", "gender", "event_languages"):
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
        sum(row["cells"][i] for row in age_lang_matrix)
        for i in range(len(lang_codes))
    ]

    # --- Row 2.5: Ideal Crush Preferences ---
    from .analytics import get_preference_stats

    pref_stats = get_preference_stats(approved_profiles)

    # --- Row 3: Membership tier distribution ---
    tier_data = approved_profiles.values("membership_tier").annotate(
        count=Count("id")
    )
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
        for ms in (
            MatchScore.objects.filter(
                Q(user_a_id__in=my_user_ids) | Q(user_b_id__in=my_user_ids),
                score_final__gte=THRESHOLD_GOOD,
                user_a__crushprofile__is_approved=True,
                user_a__crushprofile__is_active=True,
                user_b__crushprofile__is_approved=True,
                user_b__crushprofile__is_active=True,
            ).values_list("user_a_id", "user_b_id")
        ):
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
    return render(request, "crush_lu/coach_dashboard.html", context)


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
        .exclude(completion_status="submitted")
        .exclude(is_approved=True)
        .select_related("user")
        .distinct()
        .order_by("-updated_at")
    )

    # All incomplete: profiles with verified phone stuck in steps, no approved status
    all_incomplete = (
        CrushProfile.objects.filter(
            completion_status__in=["step1", "step2", "step3", "step4"],
            phone_number__isnull=False,
            phone_verified=True,
        )
        .exclude(phone_number="")
        .exclude(is_approved=True)
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

    context = {
        "coach": coach,
        "pending_submissions": pending_submissions,
        "pending_women": pending_women,
        "pending_men": pending_men,
        "pending_other": pending_other,
        "recontact_submissions": recontact_submissions,
        "revision_profiles": revision_profiles,
        "all_incomplete": all_incomplete,
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
    from crush_lu.models import Trait

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

    # Return HTMX partial or redirect
    if is_htmx:
        return render(
            request,
            "crush_lu/_screening_call_section.html",
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
    from .models import CallAttempt
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
        # Build SMS template context for re-render
        from urllib.parse import quote
        from .models.site_config import CrushSiteConfig

        sms_template_encoded = ""
        profile = submission.profile
        if profile.phone_number and profile.phone_verified:
            config = CrushSiteConfig.get_config()
            lang = getattr(profile, "preferred_language", "en") or "en"
            template_field = f"sms_template_{lang}"
            template = getattr(config, template_field, config.sms_template_en) or config.sms_template_en
            coach_name = coach.user.first_name or "Your coach"
            first_name = profile.user.first_name or ""
            sms_body = template.format(first_name=first_name, coach_name=coach_name)
            sms_template_encoded = quote(sms_body, safe="")

        context = {
            "submission": submission,
            "profile": profile,
            "sms_template_encoded": sms_template_encoded,
        }
        return render(request, "crush_lu/_screening_tab.html", context)

    return redirect("crush_lu:coach_review_profile", submission_id=submission.id)


@coach_required
def coach_review_profile(request, submission_id):
    """Review a profile submission"""
    coach = request.coach

    submission = get_object_or_404(ProfileSubmission, id=submission_id, coach=coach)

    # Block changes to already-reviewed submissions
    if submission.status in ("approved", "rejected") and submission.reviewed_at:
        messages.info(
            request,
            _("This profile has already been reviewed. No further changes are allowed."),
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

    # Build SMS template for coach outreach
    sms_template_encoded = ""
    profile = submission.profile
    prefetch_related_objects([profile], "qualities", "defects", "sought_qualities")
    if profile.phone_number and profile.phone_verified:
        from urllib.parse import quote
        from .models.site_config import CrushSiteConfig

        config = CrushSiteConfig.get_config()
        lang = getattr(profile, "preferred_language", "en") or "en"
        template_field = f"sms_template_{lang}"
        template = getattr(config, template_field, config.sms_template_en) or config.sms_template_en
        coach_name = coach.user.first_name or "Your coach"
        first_name = profile.user.first_name or ""
        sms_body = template.format(first_name=first_name, coach_name=coach_name)
        sms_template_encoded = quote(sms_body, safe="")

    context = {
        "submission": submission,
        "profile": profile,
        "form": form,
        "social_account": social_account,
        "sms_template_encoded": sms_template_encoded,
    }
    return render(request, "crush_lu/coach_review_profile.html", context)


@coach_required
def coach_preview_email(request, submission_id):
    """Preview the email that will be sent for a review decision"""
    import traceback
    from django.utils import translation
    from django.utils.translation import gettext as _
    from django.http import HttpResponse
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

    registrations = (
        EventRegistration.objects.filter(event=event)
        .exclude(status="cancelled")
        .select_related("user__crushprofile")
        .order_by("registered_at")
    )

    # Status filter
    status_filter = request.GET.get("status", "all")
    if status_filter == "confirmed":
        filtered_registrations = [
            r for r in registrations if r.status in ("confirmed", "attended")
        ]
    elif status_filter == "waitlist":
        filtered_registrations = [r for r in registrations if r.status == "waitlist"]
    elif status_filter == "other":
        filtered_registrations = [
            r for r in registrations if r.status in ("pending", "no_show")
        ]
    else:
        filtered_registrations = list(registrations)

    # Batch-query latest ProfileSubmission per registered user
    user_ids = [r.user_id for r in registrations]
    latest_submissions = {}
    if user_ids:
        for sub in ProfileSubmission.objects.filter(
            profile__user_id__in=user_ids
        ).select_related("profile").order_by("-submitted_at"):
            if sub.profile.user_id not in latest_submissions:
                latest_submissions[sub.profile.user_id] = sub

    # Attach latest submission to each registration for template use
    for reg in filtered_registrations:
        reg.latest_submission = latest_submissions.get(reg.user_id)

    # Count stats
    confirmed_count = sum(
        1 for r in registrations if r.status in ("confirmed", "attended")
    )
    waitlist_count = sum(1 for r in registrations if r.status == "waitlist")
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

    # Check if this event has a quiz
    has_quiz = hasattr(event, "quiz")

    context = {
        "coach": request.coach,
        "event": event,
        "registrations": filtered_registrations,
        "confirmed_count": confirmed_count,
        "waitlist_count": waitlist_count,
        "spots_remaining": spots_remaining,
        "total_registrations": len(registrations),
        "status_filter": status_filter,
        "connection_count": connection_count,
        "mutual_connections": mutual_connections,
        "spark_count": spark_count,
        "sparks_pending": sparks_pending,
        "has_quiz": has_quiz,
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

    context = {
        "coach": request.coach,
        "event": event,
        "registrations": confirmed,
        "confirmed_count": len(confirmed),
        "attended_count": attended_count,
    }
    return render(request, "crush_lu/coach_event_checkin.html", context)


@coach_required
def coach_event_sms_invite(request, event_id):
    """Page listing eligible profiles with verified phones for SMS event invites."""
    from datetime import date
    from urllib.parse import quote

    from django.db.models import Q
    from django.urls import reverse
    from django.utils.formats import date_format

    from .models import CallAttempt
    from .models.site_config import CrushSiteConfig

    event = get_object_or_404(MeetupEvent, id=event_id)
    coach = request.coach
    config = CrushSiteConfig.get_config()
    coach_name = coach.user.first_name or "Coach"

    # Build absolute event URL for the SMS
    event_url = request.build_absolute_uri(
        reverse("crush_lu:event_detail", args=[event.id])
    )

    # --- Age filter boundaries (same pattern as user_segments._age_range_queryset) ---
    today = date.today()
    max_dob = date(today.year - event.min_age, today.month, today.day)
    min_dob = date(today.year - event.max_age - 1, today.month, today.day)
    has_age_filter = event.min_age != 18 or event.max_age != 99

    age_q = Q(date_of_birth__gt=min_dob, date_of_birth__lte=max_dob)
    age_q_lenient = age_q | Q(date_of_birth__isnull=True)  # include profiles missing DOB

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
        pending_submissions_qs = (
            ProfileSubmission.objects.filter(
                status__in=["pending", "recontact_coach"],
            )
            .filter(sub_phone_q)
            .filter(sub_age_q)
        )
        if has_language_filter:
            pending_submissions_qs = pending_submissions_qs.filter(sub_lang_q)
        pending_submissions_qs = pending_submissions_qs.select_related(
            "profile__user"
        ).order_by("submitted_at")

        profile_pool_qs = (
            CrushProfile.objects.filter(phone_q)
            .filter(age_q_lenient)
            .exclude(
                id__in=ProfileSubmission.objects.values_list("profile_id", flat=True)
            )
        )
        if has_language_filter:
            profile_pool_qs = profile_pool_qs.filter(
                lang_q | Q(event_languages=[]) | Q(event_languages__isnull=True)
            )
        profile_pool_qs = profile_pool_qs.select_related("user")
        pool_label = _("Incomplete Profiles")

    elif event.profile_requirement == "approved":
        profile_pool_qs = (
            CrushProfile.objects.filter(phone_q, is_approved=True)
            .filter(age_q_lenient)
        )
        if has_language_filter:
            profile_pool_qs = profile_pool_qs.filter(lang_q)
        profile_pool_qs = profile_pool_qs.select_related("user")
        pool_label = _("Approved Profiles")

    elif event.profile_requirement == "profile_exists":
        profile_pool_qs = CrushProfile.objects.filter(phone_q).filter(age_q_lenient)
        if has_language_filter:
            profile_pool_qs = profile_pool_qs.filter(lang_q)
        profile_pool_qs = profile_pool_qs.select_related("user")
        pool_label = _("All Profiles")

    else:  # "none"
        profile_pool_qs = CrushProfile.objects.filter(phone_q).filter(age_q_lenient)
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

    # Get IDs of profiles (without submission) already sent an invite
    already_sent_profile_ids = set(
        CallAttempt.objects.filter(
            event=event, result="event_invite_sms", profile__isnull=False
        ).values_list("profile_id", flat=True)
    )

    # Users already registered for this event
    registered_user_ids = set(
        EventRegistration.objects.filter(event=event)
        .exclude(status="cancelled")
        .values_list("user_id", flat=True)
    )

    submitted_profiles = []
    unsubmitted_profiles = []
    gender_counts = {"F": 0, "M": 0, "other": 0}
    already_sent_count = 0
    already_registered_count = 0

    def _build_sms_uri(profile):
        """Build SMS URI for a profile."""
        lang = getattr(profile, "preferred_language", "en") or "en"
        template_field = f"sms_event_invite_template_{lang}"
        template = (
            getattr(config, template_field, config.sms_event_invite_template_en)
            or config.sms_event_invite_template_en
        )
        event_date_str = date_format(
            event.date_time, format="SHORT_DATE_FORMAT", use_l10n=True
        )
        first_name = profile.user.first_name or ""
        sms_body = template.format(
            first_name=first_name,
            coach_name=coach_name,
            event_title=event.title,
            event_date=event_date_str,
            event_url=event_url,
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
                "already_sent": sent,
                "already_registered": registered,
            }
        )

    # Process profile pool
    for profile in profile_pool_qs:
        lang, sms_uri = _build_sms_uri(profile)

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
                "already_sent": sent,
                "already_registered": registered,
            }
        )

    context = {
        "event": event,
        "submitted_profiles": submitted_profiles,
        "unsubmitted_profiles": unsubmitted_profiles,
        "total_eligible": len(submitted_profiles) + len(unsubmitted_profiles),
        "submitted_count": len(submitted_profiles),
        "unsubmitted_count": len(unsubmitted_profiles),
        "gender_counts": gender_counts,
        "already_sent_count": already_sent_count,
        "already_registered_count": already_registered_count,
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
            notes=_(
                "Event invite SMS sent for: %(event)s"
            )
            % {"event": event.title},
        )

    return render(
        request,
        "crush_lu/_sms_invite_row_sent.html",
        {"submission": submission, "event": event},
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
            notes=_(
                "Event invite SMS sent for: %(event)s"
            )
            % {"event": event.title},
        )

    return render(
        request,
        "crush_lu/_sms_invite_row_sent.html",
        {"event": event},
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

    has_traits = profile.sought_qualities.exists()
    matches = []

    if has_traits:
        match_scores = get_matches_for_user(member)

        for ms in match_scores:
            other_user = ms.user_b if ms.user_a == member else ms.user_a
            try:
                other_profile = CrushProfile.objects.get(
                    user=other_user, is_approved=True, is_active=True
                )
            except CrushProfile.DoesNotExist:
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
        profiles_by_user = {
            p.user_id: p
            for p in CrushProfile.objects.filter(
                user_id__in=all_user_ids, is_approved=True, is_active=True
            )
        }

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

    # Use mutual annotation to avoid N+1 queries
    connections_qs = connections_qs.annotate_is_mutual()

    # Paginate
    paginator = Paginator(connections_qs, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

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

    context = {
        "coach": coach,
        "connection": connection,
        "reverse_connection": reverse_connection,
        "requester_profile": requester_profile,
        "recipient_profile": recipient_profile,
        "connection_messages": connection_messages,
        "is_mutual": reverse_connection is not None,
        "show_facilitation": show_facilitation,
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
        cd
        for cd in coach_data
        if not cd["is_overloaded"] and cd["coach"].is_active
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
                    _gt("Language match: %(langs)s")
                    % {"langs": ", ".join(matching)}
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

        coach_max_map = {
            cd["coach"].id: cd["max_active_reviews"] for cd in coach_data
        }

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


def _format_duration(td):
    """Format a timedelta as a human-readable string like '2d 5h' or '3h'."""
    if td is None:
        return None
    total_seconds = int(td.total_seconds())
    if total_seconds < 0:
        return None
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h"
    minutes = (total_seconds % 3600) // 60
    return f"{minutes}m"


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
        s["is_claimable"] = (
            s["suggested_coach_id"] == coach.id
            or s["current_coach_name"] == str(_("Unassigned"))
        )

    context = {
        "coach": coach,
        "coach_data": coach_data,
        "unassigned_submissions": unassigned,
        "unassigned_count": unassigned.count(),
        "suggestions": suggestions,
        "can_claim": coach.can_accept_reviews(),
    }
    return render(request, "crush_lu/coach_team_stats.html", context)


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
    return JsonResponse(
        {
            "success": True,
            "message": str(_("Profile claimed successfully")),
            "submission_id": submission.id,
        }
    )
