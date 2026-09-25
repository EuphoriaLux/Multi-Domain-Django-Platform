"""Read-only, pseudonymized production analytics behind the Crush Data MCP.

Spec: ai-memory-hub/specs/2026-09-25-crush-data-mcp.md

AI agents reach these functions through ``GET /api/analytics/<tool>/``
(``crush_lu/api_analytics.py``) via a local stdio MCP server. Every query runs
on the ``settings.ANALYTICS_DB_ALIAS`` connection, which in production logs in
as ``crush_analytics_ro``: a read-only role that holds only the column grants
listed in ``GRANTS``. Two rules follow, and both are enforced by tests:

* Never load model instances (``.get()``, iterating a queryset,
  ``select_related``). They SELECT every column, most of which the role cannot
  read, so the query fails in production while passing on SQLite (which has no
  grants). Use ``.values()`` / ``.values_list()`` / aggregates, always with an
  explicit ``.order_by()`` so a model's ``Meta.ordering`` cannot drag an
  ungranted column into the SQL. ``test_api_analytics.SqlColumnAuditTests``
  parses every statement and fails on any column outside ``GRANTS``.
* ``GRANTS`` is the single source of truth for the database boundary:
  ``manage.py setup_analytics_role`` turns it into the GRANT statements.

Nothing returned here identifies a person: members appear as keyed pseudonyms,
ages as bands, dates at day precision, and small demographic cross-tab cells
are suppressed.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from statistics import median

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.utils import timezone

from crush_lu.models import (
    CreditRedemption,
    CrushConnectMembership,
    CrushCredit,
    CrushProfile,
    EventRegistration,
    MeetupEvent,
    PaymentTransaction,
    PremiumMembership,
    ProfileSubmission,
    UserDataConsent,
    WeeklyMetricsSnapshot,
)
from crush_lu.models.crush_connect_cycle import ConnectWeeklyRequest, ConnectWeekSession
from crush_lu.models.events import SEAT_HOLDING_STATUSES

User = get_user_model()

# ---------------------------------------------------------------------------
# The database boundary. Column-level SELECT grants for crush_analytics_ro in
# the production ``pythonapp`` database, and nothing else. Adding a column here
# is a privacy decision: never add names, email, username, phone, photos,
# tokens, IPs, free text, SumUp ids or payloads, or GDPR Art. 9-adjacent fields
# (preferred_genders, Connect lifestyle answers).
# ---------------------------------------------------------------------------
GRANTS: dict[str, tuple[str, ...]] = {
    "auth_user": ("id", "is_staff", "is_superuser", "is_active", "date_joined"),
    "crush_lu_crushprofile": (
        "id",
        "user_id",
        "gender",
        "date_of_birth",  # only ever turned into an age band, never returned
        "location",  # canton-* / border-* codes
        "verification_status",
        "verification_method",
        "is_approved",
        "is_active",
        "approved_at",
        "phone_verified",
        "created_at",
        "membership_tier",
    ),
    "crush_lu_userdataconsent": (
        "id",
        "user_id",
        "crushlu_consent_given",
        "crushlu_banned",
        "marketing_consent",
    ),
    "crush_lu_profilesubmission": (
        "id",
        "profile_id",
        "status",
        "submitted_at",
        "reviewed_at",
    ),
    "crush_lu_meetupevent": (
        "id",
        # modeltranslation rewrites .values("title") to the active language's
        # column (with fallbacks), so every translation column needs the grant.
        "title",
        "title_en",
        "title_de",
        "title_fr",
        "event_type",
        "canton",
        "date_time",
        "registration_fee",
        "max_participants",
        "max_participants_m",
        "max_participants_f",
        "max_participants_nb",
        "reserved_premium_seats",
        "min_age",
        "max_age",
        "registration_mode",
        "profile_requirement",
        "is_published",
        "is_cancelled",
        "created_at",
    ),
    "crush_lu_eventregistration": (
        "id",
        "event_id",
        "user_id",
        "status",
        "registered_at",
        "cancelled_at",
        "payment_confirmed",
        "payment_date",
        "checked_in_at",
    ),
    "crush_lu_paymenttransaction": (
        "id",
        "user_id",
        "event_id",
        "event_registration_id",
        "premium_membership_id",
        "provider",
        "status",
        "purpose",
        "amount",
        "currency",
        "created_at",
        "paid_at",
    ),
    "crush_lu_crushcredit": (
        "id",
        "user_id",
        "reason",
        "status",
        "amount_cents",
        "issued_at",
        "expires_at",
    ),
    "crush_lu_creditredemption": (
        "id",
        "credit_id",
        "event_registration_id",
        "amount_cents",
        "redeemed_at",
    ),
    "crush_lu_premiummembership": (
        "id",
        "user_id",
        "status",
        "payment_confirmed",
        "payment_date",
        "created_at",
    ),
    "crush_lu_crushconnectmembership": (
        "id",
        "user_id",
        "created_at",
        "onboarding_started_at",
        "onboarded_at",
        "onboarding_step",
        "paused_at",
        "excluded_by_coach",
    ),
    "crush_lu_connectweeksession": (
        "id",
        "user_id",
        "status",
        "started_at",
        "completed_at",
    ),
    "crush_lu_connectweeklyrequest": (
        "id",
        "session_id",
        "requester_id",
        "recipient_id",
        "status",
        "sent_at",
        "responded_at",
    ),
    "crush_lu_weeklymetricssnapshot": (
        "id",
        "week_start",
        "week_end",
        "metrics",
        "computed_at",
    ),
    # LuxID flag only: never ``uid`` / ``extra_data``, never token values.
    "socialaccount_socialaccount": ("id", "user_id", "provider"),
    "socialaccount_socialtoken": ("id", "account_id", "app_id"),
    "socialaccount_socialapp": ("id", "provider", "provider_id"),
}

AGE_BANDS = (
    (18, 24, "18-24"),
    (25, 29, "25-29"),
    (30, 34, "30-34"),
    (35, 39, "35-39"),
    (40, 44, "40-44"),
    (45, 49, "45-49"),
    (50, 200, "50+"),
)
SUPPRESSION_FLOOR = 5
DEMOGRAPHIC_DIMENSIONS = ("gender", "age_band", "canton")
GROUPABLE_DIMENSIONS = DEMOGRAPHIC_DIMENSIONS + ("verification_status", "luxid")
MAX_RANGE_DAYS = 400
DEFAULT_RANGE_DAYS = 90
MAX_EVENTS = 500
MAX_MEMBER_ROWS = 2000
DEFAULT_MEMBER_ROWS = 500
MAX_KPI_WEEKS = 52

_LOCATION_CODE = re.compile(r"^(canton|border)-[a-z]+$")
_ATTENDED = Q(status="attended") | Q(checked_in_at__isnull=False)

# Seeded / QA accounts (see crush_lu/management/commands/create_connect_test_users).
_TEST_USERNAME = (
    Q(username__startswith="connect_premium_")
    | Q(username__startswith="connect_candidate_")
    | Q(username__startswith="beta_nolux")
    | Q(username__regex=r"^testuser[0-9]+$")
)

REAL_MEMBER_RULE = (
    "A real member has a CrushProfile and is not staff, not superuser, not banned "
    "(UserDataConsent.crushlu_banned), and not a test account (email domain in "
    "TEST_EMAIL_DOMAINS or a seeded QA username). Every tool except kpi_weekly "
    "counts real members only; excluded accounts are reported separately where "
    "they touch money or seats."
)


class NotConfigured(Exception):
    """The analytics alias, API key or pseudonym key is missing."""


class NotFound(Exception):
    """The requested object does not exist."""


class ParamError(ValueError):
    """A request parameter is missing, malformed or out of bounds."""


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------


def is_configured() -> bool:
    return bool(
        getattr(settings, "ANALYTICS_API_KEY", "")
        and getattr(settings, "ANALYTICS_PSEUDONYM_KEY", "")
        and getattr(settings, "ANALYTICS_DB_ALIAS", "") in settings.DATABASES
    )


def _alias() -> str:
    if not is_configured():
        raise NotConfigured("analytics is not configured")
    return settings.ANALYTICS_DB_ALIAS


def pseudonym(user_id: int) -> str:
    """Stable, non-reversible member key (HMAC-SHA256, 16 hex chars)."""
    key = settings.ANALYTICS_PSEUDONYM_KEY.encode()
    return hmac.new(key, f"user:{user_id}".encode(), hashlib.sha256).hexdigest()[:16]


def age_band(dob: date | None, on: date) -> str | None:
    if not dob:
        return None
    age = on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))
    if age < 18:
        return "under-18"
    for low, high, label in AGE_BANDS:
        if low <= age <= high:
            return label
    return None


def canton_code(location: str | None) -> str | None:
    if not location:
        return None
    return location if _LOCATION_CODE.match(location) else "other"


def _local_date(value: datetime | None) -> date | None:
    return timezone.localtime(value).date() if value else None


def _day(value: datetime | None) -> str | None:
    local = _local_date(value)
    return local.isoformat() if local else None


def _bucket(day: date, grain: str) -> str:
    if grain == "month":
        return day.strftime("%Y-%m")
    return (day - timedelta(days=day.weekday())).isoformat()


def _window(start: date, end: date) -> tuple[datetime, datetime]:
    tz = timezone.get_current_timezone()
    return (
        timezone.make_aware(datetime.combine(start, time.min), tz),
        timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz),
    )


def _euros(value) -> float:
    return round(float(value or Decimal("0")), 2)


def _cents_to_euros(cents) -> float:
    return round((cents or 0) / 100, 2)


def _test_account_user_ids() -> set[int]:
    """Test/QA accounts, identified by email domain or seeded username.

    Deliberately runs on ``default``: the analytics role holds no grant on
    ``auth_user.email`` / ``username``, and these identifiers must never enter
    the analytics connection. Only the resulting ids are used, as an exclusion
    list, and they never leave the process.
    """
    from crush_lu.signals import TEST_EMAIL_DOMAINS

    by_domain = Q()
    for domain in TEST_EMAIL_DOMAINS:
        by_domain |= Q(email__iendswith=f"@{domain}")
    return set(
        User.objects.using("default")
        .filter(by_domain | _TEST_USERNAME)
        .order_by()
        .values_list("id", flat=True)
    )


def excluded_user_ids(alias: str) -> set[int]:
    staff = set(
        User.objects.using(alias)
        .filter(Q(is_staff=True) | Q(is_superuser=True))
        .order_by()
        .values_list("id", flat=True)
    )
    banned = set(
        UserDataConsent.objects.using(alias)
        .filter(crushlu_banned=True)
        .order_by()
        .values_list("user_id", flat=True)
    )
    return staff | banned | _test_account_user_ids()


def _luxid_user_ids(alias: str, user_ids) -> set[int]:
    """Mirror of ``CrushProfile.luxid_account_querysets`` on the analytics alias."""
    from allauth.socialaccount.models import SocialAccount, SocialToken

    user_ids = list(user_ids)
    if not user_ids:
        return set()
    native = set(
        SocialAccount.objects.using(alias)
        .filter(user_id__in=user_ids, provider="luxid")
        .order_by()
        .values_list("user_id", flat=True)
    )
    oidc = set(
        SocialToken.objects.using(alias)
        .filter(
            account__user_id__in=user_ids,
            account__provider="openid_connect",
            app__provider="openid_connect",
            app__provider_id="luxid",
        )
        .order_by()
        .values_list("account__user_id", flat=True)
    )
    return native | oidc


def _real_member_profiles(alias: str, excluded: set[int], **filters) -> list[dict]:
    return list(
        CrushProfile.objects.using(alias)
        .filter(**filters)
        .exclude(user_id__in=excluded)
        .order_by("created_at", "id")
        .values(
            "id",
            "user_id",
            "gender",
            "date_of_birth",
            "location",
            "verification_status",
            "verification_method",
            "phone_verified",
            "created_at",
        )
    )


def _attended_user_ids(alias: str, user_ids) -> set[int]:
    return set(
        EventRegistration.objects.using(alias)
        .filter(_ATTENDED, user_id__in=list(user_ids))
        .order_by()
        .values_list("user_id", flat=True)
    )


def _paid_event_user_ids(alias: str, user_ids) -> set[int]:
    return set(
        PaymentTransaction.objects.using(alias)
        .paid_event_registrations()
        .filter(user_id__in=list(user_ids))
        .order_by()
        .values_list("user_id", flat=True)
    )


def _flag_user_ids(model, alias: str, user_ids, **filters) -> set[int]:
    return set(
        model.objects.using(alias)
        .filter(user_id__in=list(user_ids), **filters)
        .order_by()
        .values_list("user_id", flat=True)
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def definitions() -> dict:
    return {
        "purpose": (
            "Read-only, pseudonymized Crush.lu production analytics (database "
            "pythonapp, production only). Members appear as 16-hex pseudonyms "
            "that are stable across calls; they cannot be mapped back to a person."
        ),
        "real_member_rule": REAL_MEMBER_RULE,
        "timezone": "Dates and buckets use Europe/Luxembourg; weeks start on Monday.",
        "age_bands": [label for _, _, label in AGE_BANDS],
        "suppression": (
            f"In demographics, a cell crossing two or more of {list(DEMOGRAPHIC_DIMENSIONS)} "
            f"with fewer than {SUPPRESSION_FLOOR} members is returned as null and counted "
            "in suppressed_cells."
        ),
        "limits": {
            "max_range_days": MAX_RANGE_DAYS,
            "default_range_days": DEFAULT_RANGE_DAYS,
            "max_events": MAX_EVENTS,
            "max_member_rows": MAX_MEMBER_ROWS,
            "max_kpi_weeks": MAX_KPI_WEEKS,
            "cache_seconds": 300,
        },
        "definitions": {
            "seat_holder": f"registration status in {SEAT_HOLDING_STATUSES}",
            "checked_in": "registration status 'attended' or checked_in_at set",
            "paid_event_revenue": (
                "PaymentTransaction.paid_event_registrations(): providers sumup/manual, "
                "purpose event_registration, status paid. Crush Credit redemptions are a "
                "payment method, not new revenue, and are reported separately."
            ),
            "verified": "CrushProfile.verification_status == 'verified'",
            "luxid_linked": "a LuxID social account (native or LuxID OIDC app) is connected",
            "connect_onboarded": "CrushConnectMembership.onboarded_at is set",
            "premium_active": "a PremiumMembership with status 'active'",
            "kpi_weekly": (
                "Persisted WeeklyMetricsSnapshot payloads from the Monday job; these are "
                "NOT filtered to real members, so they can differ from the other tools."
            ),
        },
        "enums": {
            "event_type": [value for value, _ in MeetupEvent.EVENT_TYPE_CHOICES],
            "canton": [value for value, _ in MeetupEvent.CANTON_CHOICES],
            "registration_status": [
                "applied",
                "pending",
                "confirmed",
                "waitlist",
                "cancelled",
                "attended",
                "no_show",
            ],
            "verification_status": ["incomplete", "pending", "verified", "rejected"],
            "gender": ["M", "F", "NB", "O", "P"],
        },
        "tools": {
            "definitions": "this document",
            "kpi_weekly": "weeks (1-52, default 12)",
            "funnel": "from, to (YYYY-MM-DD), grain=week|month: signup cohorts and how far they got",
            "events": "from, to, event_type?, canton?: per-event fill, gender mix, check-ins, revenue",
            "event_detail": "event_id: the event plus one pseudonymized row per registration",
            "payments": "from, to, grain: money by purpose/provider/status, credits, Premium",
            "connect": "from, to: Crush Connect onboarding, weekly sessions and requests",
            "retention": "from, to: repeat attendance of members who attended events in the window",
            "demographics": "group_by (comma list of gender,age_band,canton,verification_status,luxid), verification_status?",
            "members": "signup_from?, signup_to?, verification_status?, gender?, age_band?, canton?, luxid?, attended?, limit? (<=2000)",
        },
    }


def kpi_weekly(weeks: int = 12) -> dict:
    from crush_lu.services.weekly_kpis import compute_deltas

    alias = _alias()
    rows = list(
        WeeklyMetricsSnapshot.objects.using(alias)
        .order_by("-week_start")
        .values("week_start", "week_end", "metrics", "computed_at")[: weeks + 1]
    )
    rows.reverse()
    out = []
    for index, row in enumerate(rows):
        previous = rows[index - 1]["metrics"] if index else None
        if index == 0 and len(rows) > weeks:
            continue  # fetched only to compute the first returned week's deltas
        out.append(
            {
                "week_start": row["week_start"].isoformat(),
                "week_end": row["week_end"].isoformat(),
                "computed_at": (
                    row["computed_at"].isoformat() if row["computed_at"] else None
                ),
                "metrics": row["metrics"],
                "deltas": compute_deltas(row["metrics"], previous),
            }
        )
    return {"real_member_filtered": False, "weeks": out}


def funnel(start: date, end: date, grain: str = "week") -> dict:
    alias = _alias()
    excluded = excluded_user_ids(alias)
    low, high = _window(start, end)
    profiles = _real_member_profiles(
        alias, excluded, created_at__gte=low, created_at__lt=high
    )
    user_ids = [p["user_id"] for p in profiles]
    submitted = set(
        ProfileSubmission.objects.using(alias)
        .filter(profile_id__in=[p["id"] for p in profiles])
        .order_by()
        .values_list("profile_id", flat=True)
    )
    luxid = _luxid_user_ids(alias, user_ids)
    attended = _attended_user_ids(alias, user_ids)
    paid_event = _paid_event_user_ids(alias, user_ids)
    connect = _flag_user_ids(
        CrushConnectMembership, alias, user_ids, onboarded_at__isnull=False
    )
    premium = _flag_user_ids(PremiumMembership, alias, user_ids, status="active")

    stages = (
        "signups",
        "phone_verified",
        "submitted",
        "verified",
        "luxid_linked",
        "attended_event",
        "paid_event",
        "connect_onboarded",
        "premium_active",
    )
    cohorts: dict[str, Counter] = defaultdict(Counter)
    methods: dict[str, Counter] = defaultdict(Counter)
    for p in profiles:
        key = _bucket(_local_date(p["created_at"]), grain)
        uid = p["user_id"]
        row = cohorts[key]
        row["signups"] += 1
        row["phone_verified"] += bool(p["phone_verified"])
        row["submitted"] += p["id"] in submitted
        if p["verification_status"] == "verified":
            row["verified"] += 1
            methods[key][p["verification_method"] or "unknown"] += 1
        row["luxid_linked"] += uid in luxid
        row["attended_event"] += uid in attended
        row["paid_event"] += uid in paid_event
        row["connect_onboarded"] += uid in connect
        row["premium_active"] += uid in premium

    totals = Counter()
    for row in cohorts.values():
        totals.update(row)
    return {
        "grain": grain,
        "cohort_basis": "CrushProfile.created_at; stages count what the cohort has reached as of now",
        "stages": list(stages),
        "cohorts": [
            {
                "cohort": key,
                **{s: cohorts[key][s] for s in stages},
                "verified_by_method": dict(methods[key]),
            }
            for key in sorted(cohorts)
        ],
        "totals": {s: totals[s] for s in stages},
    }


def events(
    start: date, end: date, event_type: str | None = None, canton: str | None = None
) -> dict:
    alias = _alias()
    excluded = excluded_user_ids(alias)
    low, high = _window(start, end)
    qs = MeetupEvent.objects.using(alias).filter(date_time__gte=low, date_time__lt=high)
    if event_type:
        qs = qs.filter(event_type=event_type)
    if canton:
        qs = qs.filter(canton=canton)
    event_rows = list(
        qs.order_by("date_time", "id").values(
            "id",
            "title",
            "event_type",
            "canton",
            "date_time",
            "registration_fee",
            "max_participants",
            "max_participants_m",
            "max_participants_f",
            "max_participants_nb",
            "reserved_premium_seats",
            "registration_mode",
            "is_published",
            "is_cancelled",
        )[: MAX_EVENTS + 1]
    )
    truncated = len(event_rows) > MAX_EVENTS
    event_rows = event_rows[:MAX_EVENTS]
    event_ids = [e["id"] for e in event_rows]

    registrations = list(
        EventRegistration.objects.using(alias)
        .filter(event_id__in=event_ids)
        .order_by()
        .values("event_id", "user_id", "status", "checked_in_at")
    )
    genders = dict(
        CrushProfile.objects.using(alias)
        .filter(user_id__in={r["user_id"] for r in registrations})
        .order_by()
        .values_list("user_id", "gender")
    )
    revenue = {
        row["event_id"]: row
        for row in PaymentTransaction.objects.using(alias)
        .paid_event_registrations()
        .filter(event_id__in=event_ids)
        .exclude(user_id__in=excluded)
        .order_by()
        .values("event_id")
        .annotate(n=Count("id"), total=Sum("amount"))
    }
    credits = {
        row["event_registration__event_id"]: row
        for row in CreditRedemption.objects.using(alias)
        .filter(event_registration__event_id__in=event_ids)
        .order_by()
        .values("event_registration__event_id")
        .annotate(n=Count("id"), cents=Sum("amount_cents"))
    }

    per_event: dict[int, dict] = defaultdict(
        lambda: {
            "by_status": Counter(),
            "by_gender": Counter(),
            "checked_in": 0,
            "excluded": 0,
        }
    )
    for reg in registrations:
        stats = per_event[reg["event_id"]]
        if reg["user_id"] in excluded:
            stats["excluded"] += 1
            continue
        stats["by_status"][reg["status"]] += 1
        if reg["status"] in SEAT_HOLDING_STATUSES:
            stats["by_gender"][genders.get(reg["user_id"]) or "unknown"] += 1
        if reg["status"] == "attended" or reg["checked_in_at"]:
            stats["checked_in"] += 1

    out = []
    for e in event_rows:
        stats = per_event[e["id"]]
        seat_holders = sum(stats["by_status"][s] for s in SEAT_HOLDING_STATUSES)
        capacity = e["max_participants"] or 0
        paid = revenue.get(e["id"], {})
        credit = credits.get(e["id"], {})
        out.append(
            {
                "event_id": e["id"],
                "title": e["title"],
                "event_type": e["event_type"],
                "date": _day(e["date_time"]),
                "starts_at": timezone.localtime(e["date_time"]).isoformat(),
                "canton": e["canton"] or None,
                "fee_eur": _euros(e["registration_fee"]),
                "capacity": capacity,
                "gender_caps": {
                    "M": e["max_participants_m"],
                    "F": e["max_participants_f"],
                    "NB": e["max_participants_nb"],
                },
                "reserved_premium_seats": e["reserved_premium_seats"],
                "registration_mode": e["registration_mode"],
                "is_published": e["is_published"],
                "is_cancelled": e["is_cancelled"],
                "registrations_by_status": dict(stats["by_status"]),
                "seat_holders": seat_holders,
                "seat_holders_by_gender": dict(stats["by_gender"]),
                "checked_in": stats["checked_in"],
                "no_show": stats["by_status"]["no_show"],
                "fill_pct": (
                    round(100 * seat_holders / capacity, 1) if capacity else None
                ),
                "paid_registrations": paid.get("n", 0),
                "paid_revenue_eur": _euros(paid.get("total")),
                "credit_redemptions": credit.get("n", 0),
                "credit_redeemed_eur": _cents_to_euros(credit.get("cents")),
                "excluded_account_registrations": stats["excluded"],
            }
        )
    return {"count": len(out), "truncated": truncated, "events": out}


def event_detail(event_id: int) -> dict:
    alias = _alias()
    excluded = excluded_user_ids(alias)
    event = (
        MeetupEvent.objects.using(alias)
        .filter(id=event_id)
        .order_by()
        .values("id", "date_time")
        .first()
    )
    if not event:
        raise NotFound(f"event {event_id} does not exist")
    day = _local_date(event["date_time"])
    summary = events(day, day)["events"]
    summary = next((e for e in summary if e["event_id"] == event_id), None)

    registrations = list(
        EventRegistration.objects.using(alias)
        .filter(event_id=event_id)
        .exclude(user_id__in=excluded)
        .order_by("registered_at", "id")
        .values(
            "id",
            "user_id",
            "status",
            "registered_at",
            "checked_in_at",
            "payment_confirmed",
        )
    )
    user_ids = [r["user_id"] for r in registrations]
    profiles = {
        row["user_id"]: row
        for row in CrushProfile.objects.using(alias)
        .filter(user_id__in=user_ids)
        .order_by()
        .values("user_id", "gender", "date_of_birth")
    }
    prior = dict(
        EventRegistration.objects.using(alias)
        .filter(
            _ATTENDED, user_id__in=user_ids, event__date_time__lt=event["date_time"]
        )
        .order_by()
        .values("user_id")
        .annotate(n=Count("id"))
        .values_list("user_id", "n")
    )
    paid_users = set(
        PaymentTransaction.objects.using(alias)
        .paid_event_registrations()
        .filter(event_id=event_id)
        .order_by()
        .values_list("user_id", flat=True)
    )
    credit_regs = set(
        CreditRedemption.objects.using(alias)
        .filter(event_registration__event_id=event_id)
        .order_by()
        .values_list("event_registration_id", flat=True)
    )
    rows = []
    for reg in registrations:
        uid = reg["user_id"]
        profile = profiles.get(uid, {})
        prior_count = prior.get(uid, 0)
        rows.append(
            {
                "member": pseudonym(uid),
                "status": reg["status"],
                "registered_day": _day(reg["registered_at"]),
                "checked_in": bool(reg["status"] == "attended" or reg["checked_in_at"]),
                "paid": uid in paid_users or bool(reg["payment_confirmed"]),
                "paid_with_credit": reg["id"] in credit_regs,
                "gender": profile.get("gender"),
                "age_band": age_band(profile.get("date_of_birth"), day),
                "prior_events_attended": prior_count,
                "first_event": prior_count == 0,
            }
        )
    return {"event": summary, "registrations": rows}


def payments(start: date, end: date, grain: str = "month") -> dict:
    alias = _alias()
    excluded = excluded_user_ids(alias)
    low, high = _window(start, end)
    txs = list(
        PaymentTransaction.objects.using(alias)
        .filter(created_at__gte=low, created_at__lt=high)
        .order_by()
        .values("user_id", "created_at", "provider", "status", "purpose", "amount")
    )
    groups: dict[tuple, dict] = defaultdict(
        lambda: {"count": 0, "amount": Decimal("0")}
    )
    excluded_totals = {"count": 0, "amount": Decimal("0")}
    for tx in txs:
        if tx["user_id"] in excluded:
            excluded_totals["count"] += 1
            excluded_totals["amount"] += tx["amount"] or 0
            continue
        key = (
            _bucket(_local_date(tx["created_at"]), grain),
            tx["purpose"],
            tx["provider"],
            tx["status"],
        )
        groups[key]["count"] += 1
        groups[key]["amount"] += tx["amount"] or 0

    paid_revenue: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "amount": Decimal("0")}
    )
    for tx in (
        PaymentTransaction.objects.using(alias)
        .paid_event_registrations()
        .filter(
            Q(paid_at__gte=low, paid_at__lt=high)
            | Q(paid_at__isnull=True, created_at__gte=low, created_at__lt=high)
        )
        .exclude(user_id__in=excluded)
        .order_by()
        .values("paid_at", "created_at", "amount")
    ):
        key = _bucket(_local_date(tx["paid_at"] or tx["created_at"]), grain)
        paid_revenue[key]["count"] += 1
        paid_revenue[key]["amount"] += tx["amount"] or 0

    issued: dict[tuple, dict] = defaultdict(lambda: {"count": 0, "cents": 0})
    for credit in (
        CrushCredit.objects.using(alias)
        .filter(issued_at__gte=low, issued_at__lt=high)
        .exclude(user_id__in=excluded)
        .order_by()
        .values("reason", "status", "amount_cents")
    ):
        issued[(credit["reason"], credit["status"])]["count"] += 1
        issued[(credit["reason"], credit["status"])]["cents"] += (
            credit["amount_cents"] or 0
        )
    redeemed = (
        CreditRedemption.objects.using(alias)
        .filter(redeemed_at__gte=low, redeemed_at__lt=high)
        .exclude(credit__user_id__in=excluded)
        .order_by()
        .aggregate(n=Count("id"), cents=Sum("amount_cents"))
    )
    premium_created = Counter(
        dict(
            PremiumMembership.objects.using(alias)
            .filter(created_at__gte=low, created_at__lt=high)
            .exclude(user_id__in=excluded)
            .order_by()
            .values("status")
            .annotate(n=Count("id"))
            .values_list("status", "n")
        )
    )
    premium_active_now = (
        PremiumMembership.objects.using(alias)
        .filter(status="active")
        .exclude(user_id__in=excluded)
        .order_by()
        .count()
    )
    return {
        "grain": grain,
        "transactions_by_created": [
            {
                "bucket": k[0],
                "purpose": k[1],
                "provider": k[2],
                "status": k[3],
                "count": v["count"],
                "amount_eur": _euros(v["amount"]),
            }
            for k, v in sorted(groups.items())
        ],
        "paid_event_revenue_by_paid_at": [
            {"bucket": k, "count": v["count"], "amount_eur": _euros(v["amount"])}
            for k, v in sorted(paid_revenue.items())
        ],
        "credits_issued": [
            {
                "reason": k[0],
                "current_status": k[1],
                "count": v["count"],
                "amount_eur": _cents_to_euros(v["cents"]),
            }
            for k, v in sorted(issued.items())
        ],
        "credits_redeemed": {
            "count": redeemed["n"] or 0,
            "amount_eur": _cents_to_euros(redeemed["cents"]),
        },
        "premium": {
            "created_by_status": dict(premium_created),
            "active_now": premium_active_now,
        },
        "excluded_accounts": {
            "transactions": excluded_totals["count"],
            "amount_eur": _euros(excluded_totals["amount"]),
        },
    }


def connect(start: date, end: date) -> dict:
    alias = _alias()
    excluded = excluded_user_ids(alias)
    low, high = _window(start, end)
    memberships = list(
        CrushConnectMembership.objects.using(alias)
        .exclude(user_id__in=excluded)
        .order_by()
        .values(
            "created_at",
            "onboarding_started_at",
            "onboarded_at",
            "onboarding_step",
            "paused_at",
            "excluded_by_coach",
        )
    )

    def in_window(value):
        return value is not None and low <= value < high

    stuck_steps = Counter(
        m["onboarding_step"]
        for m in memberships
        if m["onboarding_started_at"] and not m["onboarded_at"]
    )
    sessions = dict(
        ConnectWeekSession.objects.using(alias)
        .filter(started_at__gte=low, started_at__lt=high)
        .exclude(user_id__in=excluded)
        .order_by()
        .values("status")
        .annotate(n=Count("id"))
        .values_list("status", "n")
    )
    requests = list(
        ConnectWeeklyRequest.objects.using(alias)
        .filter(sent_at__gte=low, sent_at__lt=high)
        .exclude(requester_id__in=excluded)
        .exclude(recipient_id__in=excluded)
        .order_by()
        .values("status", "sent_at", "responded_at")
    )
    by_status = Counter(r["status"] for r in requests)
    response_hours = [
        (r["responded_at"] - r["sent_at"]).total_seconds() / 3600
        for r in requests
        if r["responded_at"] and r["sent_at"]
    ]
    sent = len(requests)
    return {
        "memberships": {
            "created_in_window": sum(in_window(m["created_at"]) for m in memberships),
            "onboarding_started_in_window": sum(
                in_window(m["onboarding_started_at"]) for m in memberships
            ),
            "onboarded_in_window": sum(
                in_window(m["onboarded_at"]) for m in memberships
            ),
            "onboarded_total": sum(bool(m["onboarded_at"]) for m in memberships),
            "paused_now": sum(bool(m["paused_at"]) for m in memberships),
            "excluded_by_coach_now": sum(
                bool(m["excluded_by_coach"]) for m in memberships
            ),
            "started_not_finished_by_step": {
                str(k): v
                for k, v in sorted(
                    stuck_steps.items(), key=lambda kv: (kv[0] is None, kv[0])
                )
            },
        },
        "sessions_started_by_status": sessions,
        "requests": {
            "sent": sent,
            "by_status": dict(by_status),
            "responded": len(response_hours),
            "response_rate_pct": (
                round(100 * len(response_hours) / sent, 1) if sent else None
            ),
            "accepted_rate_pct": (
                round(100 * by_status["accepted"] / sent, 1) if sent else None
            ),
            "median_response_hours": (
                round(median(response_hours), 1) if response_hours else None
            ),
        },
    }


def retention(start: date, end: date) -> dict:
    alias = _alias()
    excluded = excluded_user_ids(alias)
    low, high = _window(start, end)
    attended = list(
        EventRegistration.objects.using(alias)
        .filter(_ATTENDED)
        .exclude(user_id__in=excluded)
        .order_by("event__date_time")
        .values("user_id", "event__date_time")
    )
    history: dict[int, list[datetime]] = defaultdict(list)
    for row in attended:
        history[row["user_id"]].append(row["event__date_time"])

    in_window = {
        uid: [d for d in dates if low <= d < high] for uid, dates in history.items()
    }
    in_window = {uid: dates for uid, dates in in_window.items() if dates}
    distribution = Counter(
        "3+" if len(dates) >= 3 else str(len(dates)) for dates in in_window.values()
    )
    gaps = []
    for dates in in_window.values():
        gaps.extend((b - a).days for a, b in zip(dates, dates[1:]))
    first_timers = sum(1 for uid in in_window if history[uid][0] >= low)
    mature_cutoff = timezone.now() - timedelta(days=90)
    mature = [
        uid
        for uid in in_window
        if low <= history[uid][0] < high and history[uid][0] <= mature_cutoff
    ]
    returned = sum(
        1
        for uid in mature
        if len(history[uid]) > 1 and (history[uid][1] - history[uid][0]).days <= 90
    )
    return {
        "attendees": len(in_window),
        "events_attended_in_window": dict(distribution),
        "median_days_between_events": median(gaps) if gaps else None,
        "first_time_attendees": first_timers,
        "returning_attendees": len(in_window) - first_timers,
        "first_timers_returned_within_90d": {
            "eligible": len(mature),
            "returned": returned,
            "pct": round(100 * returned / len(mature), 1) if mature else None,
            "note": "only first-timers whose first event is at least 90 days old are eligible",
        },
    }


def demographics(group_by: list[str], verification_status: str | None = None) -> dict:
    alias = _alias()
    excluded = excluded_user_ids(alias)
    filters = (
        {"verification_status": verification_status} if verification_status else {}
    )
    profiles = _real_member_profiles(alias, excluded, **filters)
    luxid = (
        _luxid_user_ids(alias, [p["user_id"] for p in profiles])
        if "luxid" in group_by
        else set()
    )
    today = timezone.localdate()

    def value(p, dim):
        if dim == "gender":
            return p["gender"] or "unknown"
        if dim == "age_band":
            return age_band(p["date_of_birth"], today) or "unknown"
        if dim == "canton":
            return canton_code(p["location"]) or "unknown"
        if dim == "verification_status":
            return p["verification_status"]
        return p["user_id"] in luxid

    counts = Counter(tuple(value(p, d) for d in group_by) for p in profiles)
    suppress = sum(d in DEMOGRAPHIC_DIMENSIONS for d in group_by) >= 2
    cells, suppressed = [], 0
    for key, n in sorted(counts.items(), key=lambda kv: [str(x) for x in kv[0]]):
        hidden = suppress and n < SUPPRESSION_FLOOR
        suppressed += hidden
        cells.append(
            {
                **dict(zip(group_by, key)),
                "members": None if hidden else n,
                "suppressed": hidden,
            }
        )
    return {
        "group_by": group_by,
        "verification_status": verification_status,
        "total_members": len(profiles),
        "suppressed_cells": suppressed,
        "cells": cells,
    }


def members(
    signup_from: date | None = None,
    signup_to: date | None = None,
    verification_status: str | None = None,
    gender: str | None = None,
    age_band_filter: str | None = None,
    canton: str | None = None,
    luxid: bool | None = None,
    attended: bool | None = None,
    limit: int = DEFAULT_MEMBER_ROWS,
) -> dict:
    alias = _alias()
    excluded = excluded_user_ids(alias)
    filters: dict = {}
    if signup_from or signup_to:
        low, high = _window(
            signup_from or date(2000, 1, 1), signup_to or timezone.localdate()
        )
        filters.update(created_at__gte=low, created_at__lt=high)
    if verification_status:
        filters["verification_status"] = verification_status
    if gender:
        filters["gender"] = gender
    if canton:
        filters["location"] = canton
    profiles = _real_member_profiles(alias, excluded, **filters)
    today = timezone.localdate()
    if age_band_filter:
        profiles = [
            p
            for p in profiles
            if age_band(p["date_of_birth"], today) == age_band_filter
        ]

    user_ids = [p["user_id"] for p in profiles]
    luxid_ids = _luxid_user_ids(alias, user_ids)
    attended_dates: dict[int, list[datetime]] = defaultdict(list)
    for row in (
        EventRegistration.objects.using(alias)
        .filter(_ATTENDED, user_id__in=user_ids)
        .order_by("event__date_time")
        .values("user_id", "event__date_time")
    ):
        attended_dates[row["user_id"]].append(row["event__date_time"])
    paid_counts = dict(
        PaymentTransaction.objects.using(alias)
        .paid_event_registrations()
        .filter(user_id__in=user_ids)
        .order_by()
        .values("user_id")
        .annotate(n=Count("id"))
        .values_list("user_id", "n")
    )
    premium = _flag_user_ids(PremiumMembership, alias, user_ids, status="active")
    connect_ids = _flag_user_ids(
        CrushConnectMembership, alias, user_ids, onboarded_at__isnull=False
    )

    if luxid is not None:
        profiles = [p for p in profiles if (p["user_id"] in luxid_ids) == luxid]
    if attended is not None:
        profiles = [
            p for p in profiles if bool(attended_dates.get(p["user_id"])) == attended
        ]

    total = len(profiles)
    rows = []
    for p in profiles[:limit]:
        uid = p["user_id"]
        dates = attended_dates.get(uid, [])
        rows.append(
            {
                "member": pseudonym(uid),
                "signup_week": _bucket(_local_date(p["created_at"]), "week"),
                "gender": p["gender"] or None,
                "age_band": age_band(p["date_of_birth"], today),
                "canton": canton_code(p["location"]),
                "verification_status": p["verification_status"],
                "verification_method": p["verification_method"] or None,
                "phone_verified": bool(p["phone_verified"]),
                "luxid_linked": uid in luxid_ids,
                "events_attended": len(dates),
                "first_event_day": _day(dates[0]) if dates else None,
                "last_event_day": _day(dates[-1]) if dates else None,
                "paid_event_registrations": paid_counts.get(uid, 0),
                "premium_active": uid in premium,
                "connect_onboarded": uid in connect_ids,
            }
        )
    return {
        "total_matching": total,
        "returned": len(rows),
        "truncated": total > len(rows),
        "members": rows,
    }
