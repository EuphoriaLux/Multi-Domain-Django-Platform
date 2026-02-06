"""
User Segments View for Crush.lu Admin Panel.

Provides admin dashboard for viewing and managing user segments:
- Incomplete profiles by step
- Inactive users (7d, 14d, 30d)
- Pending reviews (urgent, normal)
- Approved but never registered for event
- No push subscription
- Unsubscribed from emails
- Profile reminder tracking

Access: Superadmins only (due to bulk email capability)
"""

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import render, redirect
from django.db.models import Count, Q, F, Exists, OuterRef, Case, When, Value, CharField
from django.utils import timezone
from django.contrib import messages
from django.http import HttpResponse
from datetime import timedelta, date
import csv

from crush_lu.models import (
    CrushProfile,
    ProfileSubmission,
    MeetupEvent,
    EventRegistration,
    EventConnection,
    ConnectionMessage,
    UserActivity,
    EmailPreference,
    PushSubscription,
    ProfileReminder,
    PWADeviceInstallation,
)


def is_superuser(user):
    """Check if user is a superuser (required for bulk email access)."""
    return user.is_superuser


# ============================================================================
# HELPERS
# ============================================================================


def _age_range_queryset(base_qs, min_age, max_age=None):
    """Filter a CrushProfile queryset by age range using date_of_birth."""
    today = date.today()
    max_born = date(today.year - min_age, today.month, today.day)
    qs = base_qs.exclude(date_of_birth__isnull=True)
    if max_age is not None:
        min_born = date(today.year - max_age - 1, today.month, today.day)
        return qs.filter(date_of_birth__gt=min_born, date_of_birth__lte=max_born)
    return qs.filter(date_of_birth__lte=max_born)


# ============================================================================
# DEMOGRAPHIC STATISTICS
# ============================================================================


def get_demographic_stats():
    """
    Return demographic statistics for active CrushProfiles.
    Includes gender, looking-for, age range, language, location distributions,
    and a gender x age cross-tabulation matrix.
    """
    active_profiles = CrushProfile.objects.filter(is_active=True)
    approved_profiles = active_profiles.filter(is_approved=True)

    total_active = active_profiles.count()
    total_approved = approved_profiles.count()

    # Gender distribution
    gender_labels = dict(CrushProfile.GENDER_CHOICES)
    gender_all = list(
        active_profiles.exclude(gender="")
        .values("gender")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    gender_approved = list(
        approved_profiles.exclude(gender="")
        .values("gender")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    gender_all_total = sum(g["count"] for g in gender_all)
    gender_approved_total = sum(g["count"] for g in gender_approved)

    for g in gender_all:
        g["label"] = str(gender_labels.get(g["gender"], g["gender"]))
        g["pct"] = (
            round(g["count"] / gender_all_total * 100, 1) if gender_all_total else 0
        )

    for g in gender_approved:
        g["label"] = str(gender_labels.get(g["gender"], g["gender"]))
        g["pct"] = (
            round(g["count"] / gender_approved_total * 100, 1)
            if gender_approved_total
            else 0
        )

    # Looking-for distribution
    looking_for_labels = dict(CrushProfile.LOOKING_FOR_CHOICES)
    looking_for_dist = list(
        active_profiles.exclude(looking_for="")
        .values("looking_for")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    lf_total = sum(lf["count"] for lf in looking_for_dist)

    for lf in looking_for_dist:
        lf["label"] = str(looking_for_labels.get(lf["looking_for"], lf["looking_for"]))
        lf["pct"] = round(lf["count"] / lf_total * 100, 1) if lf_total else 0

    # Age range distribution (using helper)
    age_ranges = [
        ("18-24", 18, 24),
        ("25-29", 25, 29),
        ("30-34", 30, 34),
        ("35-39", 35, 39),
        ("40+", 40, None),
    ]
    age_dist = []
    profiles_with_dob = active_profiles.exclude(date_of_birth__isnull=True)
    age_total = profiles_with_dob.count()

    for label, min_age, max_age in age_ranges:
        count = _age_range_queryset(active_profiles, min_age, max_age).count()
        pct = round(count / age_total * 100, 1) if age_total else 0
        age_dist.append({"label": label, "count": count, "pct": pct})

    # Gender x Age matrix (approved profiles only)
    matrix_genders = [
        ("M", "Male"),
        ("F", "Female"),
        ("NB", "Non-Binary"),
        ("O", "Other"),
        ("P", "Prefer Not to Say"),
    ]
    matrix_age_ranges = [
        ("18-24", 18, 24),
        ("25-29", 25, 29),
        ("30-34", 30, 34),
        ("35-39", 35, 39),
        ("40+", 40, None),
    ]
    gender_age_matrix = []
    for gender_code, gender_label in matrix_genders:
        row = {
            "gender": gender_code,
            "label": gender_label,
            "cells": [],
            "total": 0,
        }
        gender_qs = approved_profiles.filter(gender=gender_code)
        for age_label, min_age, max_age in matrix_age_ranges:
            count = _age_range_queryset(gender_qs, min_age, max_age).count()
            row["cells"].append(count)
            row["total"] += count
        gender_age_matrix.append(row)

    # Column totals for the matrix
    matrix_col_totals = []
    for i in range(len(matrix_age_ranges)):
        matrix_col_totals.append(sum(row["cells"][i] for row in gender_age_matrix))

    # Language distribution
    language_flags = {"en": "🇬🇧", "de": "🇩🇪", "fr": "🇫🇷"}
    lang_dist = list(
        active_profiles.exclude(preferred_language="")
        .values("preferred_language")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    lang_total = sum(l["count"] for l in lang_dist)
    for l in lang_dist:
        l["label"] = (
            language_flags.get(l["preferred_language"], "")
            + " "
            + l["preferred_language"].upper()
        )
        l["pct"] = round(l["count"] / lang_total * 100, 1) if lang_total else 0

    # Location distribution (top 10 cities)
    location_dist = list(
        active_profiles.exclude(location="")
        .values("location")
        .annotate(count=Count("id"))
        .order_by("-count")[:10]
    )
    loc_total = active_profiles.exclude(location="").count()
    for loc in location_dist:
        loc["label"] = loc["location"]
        loc["pct"] = round(loc["count"] / loc_total * 100, 1) if loc_total else 0

    return {
        "total_active": total_active,
        "total_approved": total_approved,
        "gender_all": gender_all,
        "gender_approved": gender_approved,
        "looking_for": looking_for_dist,
        "age_ranges": age_dist,
        "gender_age_matrix": gender_age_matrix,
        "matrix_age_labels": [label for label, _, _ in matrix_age_ranges],
        "matrix_col_totals": matrix_col_totals,
        "matrix_grand_total": sum(matrix_col_totals),
        "languages": lang_dist,
        "locations": location_dist,
    }


# ============================================================================
# SEGMENT DEFINITIONS
# ============================================================================


def get_segment_definitions():
    """
    Return all segment definitions with their queries and metadata.
    Each segment has: name, description, query, count, action_url

    Categories (16 total):
    1-6: Operational (profile, reviews, activity, engagement, email, reminders)
    7-11: Demographics (gender, age, gender x age, looking-for, language)
    12-16: Behavioral (events, connections, membership, lifecycle, device)
    """
    now = timezone.now()
    seven_days_ago = now - timedelta(days=7)
    fourteen_days_ago = now - timedelta(days=14)
    thirty_days_ago = now - timedelta(days=30)

    # Base querysets
    active = CrushProfile.objects.filter(is_active=True)
    approved = active.filter(is_approved=True)

    # Profile completion segments
    incomplete_not_started = active.filter(completion_status="not_started")
    incomplete_step1 = active.filter(completion_status="step1")
    incomplete_step2 = active.filter(completion_status="step2")
    incomplete_step3 = active.filter(completion_status="step3")

    # Pending review segments
    pending_reviews_urgent = ProfileSubmission.objects.filter(
        status="pending", submitted_at__lt=now - timedelta(hours=72)
    )
    pending_reviews_normal = ProfileSubmission.objects.filter(
        status="pending", submitted_at__gte=now - timedelta(hours=72)
    )

    # Inactive user segments (based on UserActivity.last_seen)
    inactive_7d = UserActivity.objects.filter(
        last_seen__lt=seven_days_ago, last_seen__gte=fourteen_days_ago
    )
    inactive_14d = UserActivity.objects.filter(
        last_seen__lt=fourteen_days_ago, last_seen__gte=thirty_days_ago
    )
    inactive_30d = UserActivity.objects.filter(last_seen__lt=thirty_days_ago)

    # Approved but never registered for event
    approved_no_events = approved.exclude(user__eventregistration__isnull=False)

    # No push subscription
    no_push_subscription = approved.exclude(user__push_subscriptions__isnull=False)

    # Unsubscribed from all emails
    unsubscribed_all = EmailPreference.objects.filter(unsubscribed_all=True)

    # Reminder eligible segments
    eligible_24h_reminder = active.filter(
        completion_status__in=["not_started", "step1", "step2"],
        created_at__lte=now - timedelta(hours=24),
        created_at__gte=now - timedelta(hours=48),
    ).exclude(user__profile_reminders__reminder_type="24h")
    eligible_72h_reminder = active.filter(
        completion_status__in=["not_started", "step1", "step2"],
        created_at__lte=now - timedelta(hours=72),
        created_at__gte=now - timedelta(hours=96),
        user__profile_reminders__reminder_type="24h",
    ).exclude(user__profile_reminders__reminder_type="72h")
    eligible_7d_reminder = active.filter(
        completion_status__in=["not_started", "step1", "step2"],
        created_at__lte=now - timedelta(hours=168),
        created_at__gte=now - timedelta(hours=192),
        user__profile_reminders__reminder_type="72h",
    ).exclude(user__profile_reminders__reminder_type="7d")

    # Gender segments
    gender_male = active.filter(gender="M")
    gender_female = active.filter(gender="F")
    gender_nonbinary = active.filter(gender="NB")
    gender_other = active.filter(gender="O")
    gender_prefer_not = active.filter(gender="P")

    # Age segments (active profiles)
    age_18_24 = _age_range_queryset(active, 18, 24)
    age_25_29 = _age_range_queryset(active, 25, 29)
    age_30_34 = _age_range_queryset(active, 30, 34)
    age_35_39 = _age_range_queryset(active, 35, 39)
    age_40_plus = _age_range_queryset(active, 40)

    # Gender x Age cross-segments (approved profiles, wider bands)
    gender_m_age_18_24 = _age_range_queryset(approved.filter(gender="M"), 18, 24)
    gender_m_age_25_34 = _age_range_queryset(approved.filter(gender="M"), 25, 34)
    gender_m_age_35_plus = _age_range_queryset(approved.filter(gender="M"), 35)
    gender_f_age_18_24 = _age_range_queryset(approved.filter(gender="F"), 18, 24)
    gender_f_age_25_34 = _age_range_queryset(approved.filter(gender="F"), 25, 34)
    gender_f_age_35_plus = _age_range_queryset(approved.filter(gender="F"), 35)
    gender_nb_all = approved.filter(gender="NB").exclude(date_of_birth__isnull=True)

    # Language segments
    lang_en = active.filter(preferred_language="en")
    lang_de = active.filter(preferred_language="de")
    lang_fr = active.filter(preferred_language="fr")

    # Looking-for segments
    lf_friends = active.filter(looking_for="friends")
    lf_dating = active.filter(looking_for="dating")
    lf_both = active.filter(looking_for="both")
    lf_networking = active.filter(looking_for="networking")

    # Event engagement segments
    attended_filter = Q(user__eventregistration__status="attended")
    event_super_attendee = approved.annotate(
        attended_count=Count(
            "user__eventregistration",
            filter=attended_filter,
            distinct=True,
        )
    ).filter(attended_count__gte=3)
    event_single_attendee = approved.annotate(
        attended_count=Count(
            "user__eventregistration",
            filter=attended_filter,
            distinct=True,
        )
    ).filter(attended_count=1)
    event_registered_never_attended = (
        approved.filter(
            user__eventregistration__isnull=False,
        )
        .exclude(
            user__eventregistration__status="attended",
        )
        .distinct()
    )
    event_upcoming_registrants = approved.filter(
        user__eventregistration__status__in=["confirmed", "waitlist"],
        user__eventregistration__event__date_time__gt=now,
        user__eventregistration__event__is_cancelled=False,
    ).distinct()

    # Connection activity segments
    conn_has_accepted = approved.filter(
        Q(user__connection_requests_sent__status="accepted")
        | Q(user__connection_requests_received__status="accepted")
    ).distinct()
    conn_none = approved.exclude(
        Q(user__connection_requests_sent__isnull=False)
        | Q(user__connection_requests_received__isnull=False)
    )
    conn_has_messaged = approved.filter(
        user__connectionmessage__isnull=False,
    ).distinct()
    conn_active_3plus = approved.annotate(
        sent_count=Count("user__connection_requests_sent", distinct=True)
    ).filter(sent_count__gte=3)

    # Membership tier segments
    tier_basic = approved.filter(membership_tier="basic")
    tier_bronze = approved.filter(membership_tier="bronze")
    tier_silver = approved.filter(membership_tier="silver")
    tier_gold = approved.filter(membership_tier="gold")

    # Lifecycle segments
    lifecycle_new = active.filter(created_at__gte=now - timedelta(days=7))
    lifecycle_recently_approved = approved.filter(
        profilesubmission__status="approved",
        profilesubmission__reviewed_at__gte=now - timedelta(days=7),
    ).distinct()
    lifecycle_established = approved.filter(
        created_at__lt=now - timedelta(days=30),
    )
    lifecycle_vip = approved.filter(
        Q(membership_tier="gold")
        | Q(
            pk__in=approved.annotate(
                attended_count=Count(
                    "user__eventregistration",
                    filter=attended_filter,
                    distinct=True,
                )
            )
            .filter(attended_count__gte=5)
            .values("pk")
        )
    ).distinct()

    # Device & platform segments
    device_pwa = approved.filter(
        user__pwa_installations__isnull=False,
    ).distinct()
    device_ios = approved.filter(
        user__pwa_installations__os_type="ios",
    ).distinct()
    device_android = approved.filter(
        user__pwa_installations__os_type="android",
    ).distinct()
    device_desktop = approved.filter(
        user__pwa_installations__form_factor="desktop",
    ).distinct()

    return {
        # ── Operational segments ──────────────────────────────────────
        "profile_completion": {
            "title": "Profile Completion",
            "icon": "👤",
            "group": "operational",
            "segments": [
                {
                    "name": "Not Started",
                    "key": "not_started",
                    "description": "Users who created account but never started profile",
                    "queryset": incomplete_not_started,
                    "count": incomplete_not_started.count(),
                    "color": "red",
                },
                {
                    "name": "Step 1 Incomplete",
                    "key": "step1",
                    "description": "Started profile basics, stopped before personal info",
                    "queryset": incomplete_step1,
                    "count": incomplete_step1.count(),
                    "color": "orange",
                },
                {
                    "name": "Step 2 Incomplete",
                    "key": "step2",
                    "description": "Completed personal info, stopped before photos",
                    "queryset": incomplete_step2,
                    "count": incomplete_step2.count(),
                    "color": "yellow",
                },
                {
                    "name": "Step 3 Incomplete",
                    "key": "step3",
                    "description": "Added photos, never submitted for review",
                    "queryset": incomplete_step3,
                    "count": incomplete_step3.count(),
                    "color": "blue",
                },
            ],
        },
        "pending_reviews": {
            "title": "Pending Reviews",
            "icon": "📝",
            "group": "operational",
            "segments": [
                {
                    "name": "Urgent (>72h)",
                    "key": "pending_urgent",
                    "description": "Profiles waiting for review more than 72 hours",
                    "queryset": pending_reviews_urgent,
                    "count": pending_reviews_urgent.count(),
                    "color": "red",
                    "is_urgent": True,
                },
                {
                    "name": "Normal (<72h)",
                    "key": "pending_normal",
                    "description": "Profiles waiting for review less than 72 hours",
                    "queryset": pending_reviews_normal,
                    "count": pending_reviews_normal.count(),
                    "color": "green",
                },
            ],
        },
        "user_activity": {
            "title": "User Activity",
            "icon": "📊",
            "group": "operational",
            "segments": [
                {
                    "name": "Inactive 7-14 days",
                    "key": "inactive_7d",
                    "description": "Users not seen for 7-14 days",
                    "queryset": inactive_7d,
                    "count": inactive_7d.count(),
                    "color": "yellow",
                },
                {
                    "name": "Inactive 14-30 days",
                    "key": "inactive_14d",
                    "description": "Users not seen for 14-30 days",
                    "queryset": inactive_14d,
                    "count": inactive_14d.count(),
                    "color": "orange",
                },
                {
                    "name": "Churned (>30 days)",
                    "key": "inactive_30d",
                    "description": "Users not seen for over 30 days",
                    "queryset": inactive_30d,
                    "count": inactive_30d.count(),
                    "color": "red",
                },
            ],
        },
        "engagement": {
            "title": "Engagement",
            "icon": "💕",
            "group": "operational",
            "segments": [
                {
                    "name": "Approved, No Events",
                    "key": "approved_no_events",
                    "description": "Approved profiles who never registered for an event",
                    "queryset": approved_no_events,
                    "count": approved_no_events.count(),
                    "color": "orange",
                },
                {
                    "name": "No Push Subscription",
                    "key": "no_push",
                    "description": "Approved users without push notifications",
                    "queryset": no_push_subscription,
                    "count": no_push_subscription.count(),
                    "color": "blue",
                },
            ],
        },
        "email_preferences": {
            "title": "Email Preferences",
            "icon": "📧",
            "group": "operational",
            "segments": [
                {
                    "name": "Fully Unsubscribed",
                    "key": "unsubscribed_all",
                    "description": "Users who unsubscribed from all emails",
                    "queryset": unsubscribed_all,
                    "count": unsubscribed_all.count(),
                    "color": "gray",
                },
            ],
        },
        "reminder_eligible": {
            "title": "Reminder Eligible",
            "icon": "🔔",
            "group": "operational",
            "segments": [
                {
                    "name": "24h Reminder Due",
                    "key": "reminder_24h",
                    "description": "Incomplete profiles signed up 24-48h ago, no reminder sent",
                    "queryset": eligible_24h_reminder,
                    "count": eligible_24h_reminder.count(),
                    "color": "green",
                },
                {
                    "name": "72h Reminder Due",
                    "key": "reminder_72h",
                    "description": "Incomplete profiles, 72-96h ago, received 24h reminder",
                    "queryset": eligible_72h_reminder,
                    "count": eligible_72h_reminder.count(),
                    "color": "yellow",
                },
                {
                    "name": "7d Final Reminder Due",
                    "key": "reminder_7d",
                    "description": "Incomplete profiles, 7-8 days ago, received 72h reminder",
                    "queryset": eligible_7d_reminder,
                    "count": eligible_7d_reminder.count(),
                    "color": "orange",
                },
            ],
        },
        # ── Demographic segments ──────────────────────────────────────
        "demographics_gender": {
            "title": "Demographics: Gender",
            "icon": "⚧",
            "group": "demographic",
            "segments": [
                {
                    "name": "Male",
                    "key": "gender_male",
                    "description": "Active profiles identifying as male",
                    "queryset": gender_male,
                    "count": gender_male.count(),
                    "color": "blue",
                },
                {
                    "name": "Female",
                    "key": "gender_female",
                    "description": "Active profiles identifying as female",
                    "queryset": gender_female,
                    "count": gender_female.count(),
                    "color": "pink",
                },
                {
                    "name": "Non-Binary",
                    "key": "gender_nonbinary",
                    "description": "Active profiles identifying as non-binary",
                    "queryset": gender_nonbinary,
                    "count": gender_nonbinary.count(),
                    "color": "purple",
                },
                {
                    "name": "Other",
                    "key": "gender_other",
                    "description": "Active profiles with gender set to other",
                    "queryset": gender_other,
                    "count": gender_other.count(),
                    "color": "green",
                },
                {
                    "name": "Prefer Not to Say",
                    "key": "gender_prefer_not",
                    "description": "Active profiles who prefer not to disclose gender",
                    "queryset": gender_prefer_not,
                    "count": gender_prefer_not.count(),
                    "color": "gray",
                },
            ],
        },
        "demographics_age": {
            "title": "Demographics: Age",
            "icon": "🎂",
            "group": "demographic",
            "segments": [
                {
                    "name": "Age 18-24",
                    "key": "age_18_24",
                    "description": "Active profiles aged 18-24",
                    "queryset": age_18_24,
                    "count": age_18_24.count(),
                    "color": "green",
                },
                {
                    "name": "Age 25-29",
                    "key": "age_25_29",
                    "description": "Active profiles aged 25-29",
                    "queryset": age_25_29,
                    "count": age_25_29.count(),
                    "color": "blue",
                },
                {
                    "name": "Age 30-34",
                    "key": "age_30_34",
                    "description": "Active profiles aged 30-34",
                    "queryset": age_30_34,
                    "count": age_30_34.count(),
                    "color": "purple",
                },
                {
                    "name": "Age 35-39",
                    "key": "age_35_39",
                    "description": "Active profiles aged 35-39",
                    "queryset": age_35_39,
                    "count": age_35_39.count(),
                    "color": "orange",
                },
                {
                    "name": "Age 40+",
                    "key": "age_40_plus",
                    "description": "Active profiles aged 40 and over",
                    "queryset": age_40_plus,
                    "count": age_40_plus.count(),
                    "color": "red",
                },
            ],
        },
        "demographics_gender_age": {
            "title": "Demographics: Gender x Age",
            "icon": "🔀",
            "group": "demographic",
            "segments": [
                {
                    "name": "Males 18-24",
                    "key": "gender_m_age_18_24",
                    "description": "Approved males aged 18-24",
                    "queryset": gender_m_age_18_24,
                    "count": gender_m_age_18_24.count(),
                    "color": "blue",
                },
                {
                    "name": "Males 25-34",
                    "key": "gender_m_age_25_34",
                    "description": "Approved males aged 25-34",
                    "queryset": gender_m_age_25_34,
                    "count": gender_m_age_25_34.count(),
                    "color": "blue",
                },
                {
                    "name": "Males 35+",
                    "key": "gender_m_age_35_plus",
                    "description": "Approved males aged 35 and over",
                    "queryset": gender_m_age_35_plus,
                    "count": gender_m_age_35_plus.count(),
                    "color": "blue",
                },
                {
                    "name": "Females 18-24",
                    "key": "gender_f_age_18_24",
                    "description": "Approved females aged 18-24",
                    "queryset": gender_f_age_18_24,
                    "count": gender_f_age_18_24.count(),
                    "color": "pink",
                },
                {
                    "name": "Females 25-34",
                    "key": "gender_f_age_25_34",
                    "description": "Approved females aged 25-34",
                    "queryset": gender_f_age_25_34,
                    "count": gender_f_age_25_34.count(),
                    "color": "pink",
                },
                {
                    "name": "Females 35+",
                    "key": "gender_f_age_35_plus",
                    "description": "Approved females aged 35 and over",
                    "queryset": gender_f_age_35_plus,
                    "count": gender_f_age_35_plus.count(),
                    "color": "pink",
                },
                {
                    "name": "Non-Binary (all ages)",
                    "key": "gender_nb_all",
                    "description": "Approved non-binary profiles (all ages)",
                    "queryset": gender_nb_all,
                    "count": gender_nb_all.count(),
                    "color": "purple",
                },
            ],
        },
        "demographics_looking_for": {
            "title": "Demographics: Looking For",
            "icon": "💫",
            "group": "demographic",
            "segments": [
                {
                    "name": "New Friends",
                    "key": "lf_friends",
                    "description": "Active profiles looking for new friends",
                    "queryset": lf_friends,
                    "count": lf_friends.count(),
                    "color": "green",
                },
                {
                    "name": "Dating",
                    "key": "lf_dating",
                    "description": "Active profiles looking for dating",
                    "queryset": lf_dating,
                    "count": lf_dating.count(),
                    "color": "red",
                },
                {
                    "name": "Both (Friends & Dating)",
                    "key": "lf_both",
                    "description": "Active profiles open to both friends and dating",
                    "queryset": lf_both,
                    "count": lf_both.count(),
                    "color": "purple",
                },
                {
                    "name": "Social Networking",
                    "key": "lf_networking",
                    "description": "Active profiles interested in social networking",
                    "queryset": lf_networking,
                    "count": lf_networking.count(),
                    "color": "blue",
                },
            ],
        },
        "demographics_language": {
            "title": "Demographics: Language",
            "icon": "🌍",
            "group": "demographic",
            "segments": [
                {
                    "name": "English",
                    "key": "lang_en",
                    "description": "Active profiles with preferred language English",
                    "queryset": lang_en,
                    "count": lang_en.count(),
                    "color": "blue",
                },
                {
                    "name": "Deutsch",
                    "key": "lang_de",
                    "description": "Active profiles with preferred language German",
                    "queryset": lang_de,
                    "count": lang_de.count(),
                    "color": "orange",
                },
                {
                    "name": "Francais",
                    "key": "lang_fr",
                    "description": "Active profiles with preferred language French",
                    "queryset": lang_fr,
                    "count": lang_fr.count(),
                    "color": "red",
                },
            ],
        },
        # ── Behavioral segments ───────────────────────────────────────
        "event_engagement": {
            "title": "Event Engagement",
            "icon": "🎉",
            "group": "behavioral",
            "segments": [
                {
                    "name": "Super Attendee (3+)",
                    "key": "event_super_attendee",
                    "description": "Approved profiles who attended 3 or more events",
                    "queryset": event_super_attendee,
                    "count": event_super_attendee.count(),
                    "color": "green",
                },
                {
                    "name": "Single Attendee",
                    "key": "event_single_attendee",
                    "description": "Approved profiles who attended exactly 1 event",
                    "queryset": event_single_attendee,
                    "count": event_single_attendee.count(),
                    "color": "blue",
                },
                {
                    "name": "Registered, Never Attended",
                    "key": "event_registered_never_attended",
                    "description": "Registered for events but never marked as attended",
                    "queryset": event_registered_never_attended,
                    "count": event_registered_never_attended.count(),
                    "color": "orange",
                },
                {
                    "name": "Upcoming Registrants",
                    "key": "event_upcoming_registrants",
                    "description": "Currently registered for a future event",
                    "queryset": event_upcoming_registrants,
                    "count": event_upcoming_registrants.count(),
                    "color": "purple",
                },
            ],
        },
        "connection_activity": {
            "title": "Connection Activity",
            "icon": "🤝",
            "group": "behavioral",
            "segments": [
                {
                    "name": "Has Accepted Connection",
                    "key": "conn_has_accepted",
                    "description": "Has at least one accepted connection",
                    "queryset": conn_has_accepted,
                    "count": conn_has_accepted.count(),
                    "color": "green",
                },
                {
                    "name": "No Connections",
                    "key": "conn_none",
                    "description": "Approved but zero connection requests (sent or received)",
                    "queryset": conn_none,
                    "count": conn_none.count(),
                    "color": "red",
                },
                {
                    "name": "Has Messaged",
                    "key": "conn_has_messaged",
                    "description": "Sent at least one connection message",
                    "queryset": conn_has_messaged,
                    "count": conn_has_messaged.count(),
                    "color": "blue",
                },
                {
                    "name": "Active Requester (3+)",
                    "key": "conn_active_3plus",
                    "description": "Sent 3 or more connection requests",
                    "queryset": conn_active_3plus,
                    "count": conn_active_3plus.count(),
                    "color": "purple",
                },
            ],
        },
        "membership_tier": {
            "title": "Membership Tier",
            "icon": "🏅",
            "group": "behavioral",
            "segments": [
                {
                    "name": "Basic",
                    "key": "tier_basic",
                    "description": "Approved profiles on Basic tier",
                    "queryset": tier_basic,
                    "count": tier_basic.count(),
                    "color": "gray",
                },
                {
                    "name": "Bronze",
                    "key": "tier_bronze",
                    "description": "Approved profiles on Bronze tier",
                    "queryset": tier_bronze,
                    "count": tier_bronze.count(),
                    "color": "orange",
                },
                {
                    "name": "Silver",
                    "key": "tier_silver",
                    "description": "Approved profiles on Silver tier",
                    "queryset": tier_silver,
                    "count": tier_silver.count(),
                    "color": "blue",
                },
                {
                    "name": "Gold",
                    "key": "tier_gold",
                    "description": "Approved profiles on Gold tier",
                    "queryset": tier_gold,
                    "count": tier_gold.count(),
                    "color": "yellow",
                },
            ],
        },
        "lifecycle": {
            "title": "User Lifecycle",
            "icon": "🔄",
            "group": "behavioral",
            "segments": [
                {
                    "name": "New (< 7 days)",
                    "key": "lifecycle_new",
                    "description": "Created account within last 7 days",
                    "queryset": lifecycle_new,
                    "count": lifecycle_new.count(),
                    "color": "green",
                },
                {
                    "name": "Recently Approved",
                    "key": "lifecycle_recently_approved",
                    "description": "Approved in the last 7 days",
                    "queryset": lifecycle_recently_approved,
                    "count": lifecycle_recently_approved.count(),
                    "color": "blue",
                },
                {
                    "name": "Established (30d+)",
                    "key": "lifecycle_established",
                    "description": "Approved more than 30 days ago",
                    "queryset": lifecycle_established,
                    "count": lifecycle_established.count(),
                    "color": "purple",
                },
                {
                    "name": "VIP",
                    "key": "lifecycle_vip",
                    "description": "Gold tier or attended 5+ events",
                    "queryset": lifecycle_vip,
                    "count": lifecycle_vip.count(),
                    "color": "yellow",
                },
            ],
        },
        "device_platform": {
            "title": "Device & Platform",
            "icon": "📱",
            "group": "behavioral",
            "segments": [
                {
                    "name": "PWA Users",
                    "key": "device_pwa",
                    "description": "Users who have installed the PWA",
                    "queryset": device_pwa,
                    "count": device_pwa.count(),
                    "color": "blue",
                },
                {
                    "name": "iOS",
                    "key": "device_ios",
                    "description": "PWA installed on iOS",
                    "queryset": device_ios,
                    "count": device_ios.count(),
                    "color": "gray",
                },
                {
                    "name": "Android",
                    "key": "device_android",
                    "description": "PWA installed on Android",
                    "queryset": device_android,
                    "count": device_android.count(),
                    "color": "green",
                },
                {
                    "name": "Desktop",
                    "key": "device_desktop",
                    "description": "PWA installed on desktop",
                    "queryset": device_desktop,
                    "count": device_desktop.count(),
                    "color": "purple",
                },
            ],
        },
    }


# ============================================================================
# VIEW FUNCTIONS
# ============================================================================


@login_required
@user_passes_test(is_superuser)
def user_segments_dashboard(request):
    """
    Main user segments dashboard.
    Shows all segment categories with counts and quick actions.

    Access: Superadmins only.
    """
    segments = get_segment_definitions()
    demographics = get_demographic_stats()

    # Calculate totals
    total_incomplete = sum(
        seg["count"] for seg in segments["profile_completion"]["segments"]
    )
    total_pending = sum(seg["count"] for seg in segments["pending_reviews"]["segments"])
    total_inactive = sum(seg["count"] for seg in segments["user_activity"]["segments"])
    total_reminder_eligible = sum(
        seg["count"] for seg in segments["reminder_eligible"]["segments"]
    )

    context = {
        "segments": segments,
        "demographics": demographics,
        "total_profiles": demographics["total_active"],
        "total_approved": demographics["total_approved"],
        "total_incomplete": total_incomplete,
        "total_pending": total_pending,
        "total_inactive": total_inactive,
        "total_reminder_eligible": total_reminder_eligible,
        "title": "User Segments",
        "site_header": "💕 Crush.lu Administration",
    }

    return render(request, "admin/crush_lu/user_segments.html", context)


@login_required
@user_passes_test(is_superuser)
def segment_detail(request, segment_key):
    """
    Detailed view of a specific segment with user list.
    Allows CSV export and viewing individual users.

    Access: Superadmins only.
    """
    segments = get_segment_definitions()

    # Find the segment
    target_segment = None
    category_title = None

    for category_key, category in segments.items():
        for segment in category["segments"]:
            if segment["key"] == segment_key:
                target_segment = segment
                category_title = category["title"]
                break
        if target_segment:
            break

    if not target_segment:
        messages.error(request, f"Segment '{segment_key}' not found.")
        return redirect("user_segments_dashboard")

    # Get the queryset
    queryset = target_segment["queryset"]

    # Handle CSV export
    if request.GET.get("export") == "csv":
        return export_segment_csv(queryset, segment_key, target_segment["name"])

    # Prepare user list based on queryset model
    users = []
    is_profile_segment = False
    if hasattr(queryset, "model"):
        model = queryset.model
        if model == CrushProfile:
            is_profile_segment = True
            users = queryset.select_related("user").annotate(
                event_count=Count("user__eventregistration", distinct=True),
                sent_connections=Count("user__connection_requests_sent", distinct=True),
                received_connections=Count(
                    "user__connection_requests_received", distinct=True
                ),
            )[:100]
        elif model == ProfileSubmission:
            users = queryset.select_related("profile__user", "coach__user")[:100]
        elif model == UserActivity:
            users = queryset.select_related("user")[:100]
        elif model == EmailPreference:
            users = queryset.select_related("user")[:100]
        else:
            users = queryset[:100]

    context = {
        "segment": target_segment,
        "category_title": category_title,
        "users": users,
        "is_profile_segment": is_profile_segment,
        "total_count": target_segment["count"],
        "title": f"Segment: {target_segment['name']}",
        "site_header": "💕 Crush.lu Administration",
    }

    return render(request, "admin/crush_lu/segment_detail.html", context)


def export_segment_csv(queryset, segment_key, segment_name):
    """
    Export segment users to CSV file.
    """
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="segment_{segment_key}_{timezone.now().strftime("%Y%m%d")}.csv"'
    )

    writer = csv.writer(response)
    model = queryset.model

    if model == CrushProfile:
        writer.writerow(
            [
                "Email",
                "First Name",
                "Last Name",
                "Gender",
                "Age",
                "Location",
                "Phone Verified",
                "Looking For",
                "Language",
                "Is Approved",
                "Event Count",
                "Connection Count",
                "Created At",
                "Segment",
            ]
        )
        gender_labels = dict(CrushProfile.GENDER_CHOICES)
        lf_labels = dict(CrushProfile.LOOKING_FOR_CHOICES)
        profiles = queryset.select_related("user").annotate(
            event_count=Count("user__eventregistration", distinct=True),
            sent_connections=Count("user__connection_requests_sent", distinct=True),
            received_connections=Count(
                "user__connection_requests_received", distinct=True
            ),
        )
        for profile in profiles:
            try:
                age = profile.age
            except Exception:
                age = ""
            writer.writerow(
                [
                    profile.user.email,
                    profile.user.first_name,
                    profile.user.last_name,
                    str(gender_labels.get(profile.gender, profile.gender)),
                    age if age else "",
                    profile.location,
                    "Yes" if profile.phone_verified else "No",
                    str(lf_labels.get(profile.looking_for, profile.looking_for)),
                    profile.preferred_language,
                    "Yes" if profile.is_approved else "No",
                    profile.event_count,
                    profile.sent_connections + profile.received_connections,
                    profile.created_at.strftime("%Y-%m-%d %H:%M"),
                    segment_name,
                ]
            )
    elif model == ProfileSubmission:
        writer.writerow(["Email", "First Name", "Last Name", "Created At", "Segment"])
        for submission in queryset.select_related("profile__user"):
            writer.writerow(
                [
                    submission.profile.user.email,
                    submission.profile.user.first_name,
                    submission.profile.user.last_name,
                    (
                        submission.submitted_at.strftime("%Y-%m-%d %H:%M")
                        if submission.submitted_at
                        else ""
                    ),
                    segment_name,
                ]
            )
    elif model == UserActivity:
        writer.writerow(["Email", "First Name", "Last Name", "Created At", "Segment"])
        for activity in queryset.select_related("user"):
            writer.writerow(
                [
                    activity.user.email,
                    activity.user.first_name,
                    activity.user.last_name,
                    (
                        activity.last_seen.strftime("%Y-%m-%d %H:%M")
                        if activity.last_seen
                        else ""
                    ),
                    segment_name,
                ]
            )
    elif model == EmailPreference:
        writer.writerow(["Email", "First Name", "Last Name", "Created At", "Segment"])
        for pref in queryset.select_related("user"):
            writer.writerow(
                [
                    pref.user.email,
                    pref.user.first_name,
                    pref.user.last_name,
                    (
                        pref.created_at.strftime("%Y-%m-%d %H:%M")
                        if hasattr(pref, "created_at")
                        else ""
                    ),
                    segment_name,
                ]
            )

    return response
