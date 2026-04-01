"""
Management command to extract key metrics for the Crush.lu business plan.

Outputs: gender split, age distribution, profile completion, location
distribution, and referral data.

Supports --since / --until date filters and --monthly breakdown.
"""

import json
from datetime import date, datetime, timedelta

from django.core.management.base import BaseCommand
from django.db.models import Avg, Count, F, FloatField, ExpressionWrapper, Exists, OuterRef, Q
from django.utils import timezone

from crush_lu.models import CrushProfile, ProfileSubmission
from crush_lu.models.connections import EventConnection, ConnectionMessage
from crush_lu.models.crush_spark import CrushSpark
from crush_lu.models.events import MeetupEvent, EventRegistration
from crush_lu.models.journey import JourneyProgress, ChapterProgress
from crush_lu.models.profiles import CallAttempt, CrushCoach, UserActivity, PWADeviceInstallation
from crush_lu.models.referrals import ReferralAttribution, ReferralCode


def _add_months(d, months):
    """Add months to a date, returning the 1st of the resulting month."""
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    return date(year, month, 1)


def _fmt_timedelta(td):
    """Format a timedelta as 'Xd Yh' or 'Yh Zm'."""
    if td is None:
        return "N/A"
    total_seconds = int(td.total_seconds())
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    if days > 0:
        return f"{days}d {hours}h"
    return f"{hours}h {minutes}m"


class Command(BaseCommand):
    help = "Output business plan metrics (gender, age, completion, referrals)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--json", action="store_true", help="Output as JSON instead of tables"
        )
        parser.add_argument(
            "--since", type=str, help="Start date (YYYY-MM-DD), filter by created_at"
        )
        parser.add_argument(
            "--until", type=str, help="End date (YYYY-MM-DD, exclusive), filter by created_at"
        )
        parser.add_argument(
            "--monthly", action="store_true", help="Show monthly breakdown from --since to --until"
        )

    def handle(self, *args, **options):
        output_json = options["json"]
        since = datetime.strptime(options["since"], "%Y-%m-%d").date() if options["since"] else None
        until = datetime.strptime(options["until"], "%Y-%m-%d").date() if options["until"] else None

        if options["monthly"]:
            if not since:
                since = date(2026, 1, 1)
            if not until:
                until = _add_months(date.today(), 1)
            self._monthly_breakdown(since, until, output_json)
            return

        all_profiles = CrushProfile.objects.all()
        if since:
            all_profiles = all_profiles.filter(created_at__date__gte=since)
        if until:
            all_profiles = all_profiles.filter(created_at__date__lt=until)
        approved = all_profiles.filter(is_approved=True)
        not_approved = all_profiles.filter(is_approved=False)

        data = {}

        if not output_json and (since or until):
            label = "Filter: "
            if since:
                label += f"from {since} "
            if until:
                label += f"to {until} (exclusive)"
            self.stdout.write(self.style.WARNING(label))

        # ── 1. Gender Split ──────────────────────────────────────────
        gender_labels = dict(CrushProfile.GENDER_CHOICES)
        gender_all = dict(
            all_profiles.values_list("gender").annotate(c=Count("id")).values_list("gender", "c")
        )
        gender_approved = dict(
            approved.values_list("gender").annotate(c=Count("id")).values_list("gender", "c")
        )
        gender_not_approved = dict(
            not_approved.values_list("gender").annotate(c=Count("id")).values_list("gender", "c")
        )

        gender_rows = []
        for code, label in gender_labels.items():
            total = gender_all.get(code, 0)
            appr = gender_approved.get(code, 0)
            pend = gender_not_approved.get(code, 0)
            gender_rows.append(
                {"gender": code, "label": str(label), "total": total, "approved": appr, "not_approved": pend}
            )
        # Add row for blank/null gender
        blank_total = all_profiles.filter(Q(gender="") | Q(gender__isnull=True)).count()
        if blank_total:
            blank_appr = approved.filter(Q(gender="") | Q(gender__isnull=True)).count()
            gender_rows.append(
                {"gender": "", "label": "(not set)", "total": blank_total, "approved": blank_appr, "not_approved": blank_total - blank_appr}
            )

        grand_total = sum(r["total"] for r in gender_rows)
        data["gender_split"] = {"rows": gender_rows, "total": grand_total}

        if not output_json:
            self.stdout.write(self.style.SUCCESS("\n=== 1. GENDER SPLIT ==="))
            self.stdout.write(f"{'Gender':<20} {'Total':>6} {'Approved':>9} {'Not Appr.':>10} {'% of Total':>11}")
            self.stdout.write("-" * 58)
            for r in gender_rows:
                pct = f"{r['total'] / grand_total * 100:.1f}%" if grand_total else "0%"
                self.stdout.write(
                    f"{r['label']:<20} {r['total']:>6} {r['approved']:>9} {r['not_approved']:>10} {pct:>11}"
                )
            self.stdout.write("-" * 58)
            self.stdout.write(f"{'TOTAL':<20} {grand_total:>6}")

        # ── 2. Age Distribution ──────────────────────────────────────
        today = date.today()
        brackets = [
            ("18-24", 18, 24),
            ("25-34", 25, 34),
            ("35-44", 35, 44),
            ("45-54", 45, 54),
            ("55+", 55, 999),
        ]

        profiles_with_dob = list(
            all_profiles.exclude(date_of_birth__isnull=True).values_list(
                "date_of_birth", "gender", "is_approved"
            )
        )

        age_rows = []
        for label, min_age, max_age in brackets:
            age_rows.append({
                "bracket": label, "total": 0,
                "male": 0, "female": 0, "other": 0,
                "approved": 0, "not_approved": 0,
            })

        no_dob_count = all_profiles.filter(date_of_birth__isnull=True).count()
        under_18 = 0

        for dob, gender, is_appr in profiles_with_dob:
            age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
            if age < 18:
                under_18 += 1
                continue
            for i, (label, min_age, max_age) in enumerate(brackets):
                if min_age <= age <= max_age:
                    age_rows[i]["total"] += 1
                    if gender == "M":
                        age_rows[i]["male"] += 1
                    elif gender == "F":
                        age_rows[i]["female"] += 1
                    else:
                        age_rows[i]["other"] += 1
                    if is_appr:
                        age_rows[i]["approved"] += 1
                    else:
                        age_rows[i]["not_approved"] += 1
                    break

        age_total = sum(r["total"] for r in age_rows)
        data["age_distribution"] = {
            "rows": age_rows,
            "total_with_dob": age_total,
            "no_dob": no_dob_count,
            "under_18": under_18,
        }

        if not output_json:
            self.stdout.write(self.style.SUCCESS("\n=== 2. AGE DISTRIBUTION ==="))
            self.stdout.write(
                f"{'Bracket':<10} {'Total':>6} {'Male':>6} {'Female':>7} {'Other':>6} {'Appr.':>6} {'Not A.':>7} {'%':>7}"
            )
            self.stdout.write("-" * 58)
            for r in age_rows:
                pct = f"{r['total'] / age_total * 100:.1f}%" if age_total else "0%"
                self.stdout.write(
                    f"{r['bracket']:<10} {r['total']:>6} {r['male']:>6} {r['female']:>7} "
                    f"{r['other']:>6} {r['approved']:>6} {r['not_approved']:>7} {pct:>7}"
                )
            self.stdout.write("-" * 58)
            self.stdout.write(f"{'TOTAL':<10} {age_total:>6}")
            if no_dob_count:
                self.stdout.write(f"  (no DOB: {no_dob_count})")
            if under_18:
                self.stdout.write(f"  (under 18: {under_18})")

        # ── 3. Profile Completion ────────────────────────────────────
        total_profiles = all_profiles.count()

        # Completion status funnel
        status_counts = dict(
            all_profiles.values_list("completion_status")
            .annotate(c=Count("id"))
            .values_list("completion_status", "c")
        )

        # "Complete" profile: has photo + bio + preferences + submitted
        complete_count = all_profiles.filter(
            completion_status="submitted",
        ).exclude(photo_1="").exclude(photo_1__isnull=True).exclude(
            bio=""
        ).exclude(bio__isnull=True).count()

        has_photo = all_profiles.exclude(photo_1="").exclude(photo_1__isnull=True).count()
        has_bio = all_profiles.exclude(bio="").exclude(bio__isnull=True).count()
        has_prefs = all_profiles.exclude(preferred_genders=[]).exclude(preferred_genders__isnull=True).count()

        # Review pipeline
        sub_qs = ProfileSubmission.objects.filter(profile__in=all_profiles)
        submission_counts = dict(
            sub_qs.values_list("status")
            .annotate(c=Count("id"))
            .values_list("status", "c")
        )
        never_submitted = all_profiles.filter(
            profilesubmission__isnull=True
        ).count()

        data["profile_completion"] = {
            "total_profiles": total_profiles,
            "completion_funnel": status_counts,
            "complete_profiles": complete_count,
            "has_photo": has_photo,
            "has_bio": has_bio,
            "has_preferences": has_prefs,
            "approved_count": approved.count(),
            "review_pipeline": submission_counts,
            "never_submitted": never_submitted,
        }

        if not output_json:
            self.stdout.write(self.style.SUCCESS("\n=== 3. PROFILE COMPLETION ==="))
            self.stdout.write(f"\nTotal profiles: {total_profiles}")
            self.stdout.write(f"\n  Completion funnel:")
            funnel_order = ["not_started", "step1", "step2", "step3", "step4", "submitted"]
            for step in funnel_order:
                c = status_counts.get(step, 0)
                pct = f"{c / total_profiles * 100:.1f}%" if total_profiles else "0%"
                self.stdout.write(f"    {step:<15} {c:>5}  ({pct})")

            self.stdout.write(f"\n  Profile fields filled:")
            self.stdout.write(f"    Has photo:       {has_photo:>5}  ({has_photo / total_profiles * 100:.1f}%)" if total_profiles else "")
            self.stdout.write(f"    Has bio:         {has_bio:>5}  ({has_bio / total_profiles * 100:.1f}%)" if total_profiles else "")
            self.stdout.write(f"    Has preferences: {has_prefs:>5}  ({has_prefs / total_profiles * 100:.1f}%)" if total_profiles else "")
            self.stdout.write(f"    COMPLETE*:       {complete_count:>5}  ({complete_count / total_profiles * 100:.1f}%)" if total_profiles else "")
            self.stdout.write(f"    * Complete = submitted + photo + bio")

            self.stdout.write(f"\n  Review pipeline (ProfileSubmission):")
            for status in ["pending", "approved", "rejected", "revision", "recontact_coach"]:
                c = submission_counts.get(status, 0)
                self.stdout.write(f"    {status:<18} {c:>5}")
            self.stdout.write(f"    {'never submitted':<18} {never_submitted:>5}")

        # ── 4. Location Distribution ─────────────────────────────────
        locations = list(
            all_profiles.exclude(location="")
            .exclude(location__isnull=True)
            .values("location")
            .annotate(count=Count("id"))
            .order_by("-count")[:15]
        )
        no_location = all_profiles.filter(Q(location="") | Q(location__isnull=True)).count()
        distinct_locations = (
            all_profiles.exclude(location="")
            .exclude(location__isnull=True)
            .values("location")
            .distinct()
            .count()
        )

        data["location_distribution"] = {
            "top_15": locations,
            "distinct_count": distinct_locations,
            "no_location_set": no_location,
            "note": "Nationality is not tracked. This shows location (city/region in Luxembourg) as a proxy.",
        }

        if not output_json:
            self.stdout.write(self.style.SUCCESS("\n=== 4. LOCATION DISTRIBUTION (proxy for nationality) ==="))
            self.stdout.write(
                "NOTE: Nationality is not tracked. This shows location (city/region).\n"
            )
            self.stdout.write(f"{'Location':<30} {'Count':>6}")
            self.stdout.write("-" * 38)
            for loc in locations:
                self.stdout.write(f"{loc['location']:<30} {loc['count']:>6}")
            self.stdout.write("-" * 38)
            self.stdout.write(f"Distinct locations: {distinct_locations}")
            if no_location:
                self.stdout.write(f"No location set: {no_location}")

        # ── 5. Referral Data ─────────────────────────────────────────
        ref_qs = ReferralAttribution.objects.all()
        code_qs = ReferralCode.objects.all()
        if since:
            ref_qs = ref_qs.filter(created_at__date__gte=since)
            code_qs = code_qs.filter(created_at__date__gte=since)
        if until:
            ref_qs = ref_qs.filter(created_at__date__lt=until)
            code_qs = code_qs.filter(created_at__date__lt=until)
        total_attributions = ref_qs.count()
        converted = ref_qs.filter(status="converted").count()
        pending_referrals = ref_qs.filter(status="pending").count()
        conv_rate = (
            f"{converted / total_attributions * 100:.1f}%"
            if total_attributions
            else "N/A"
        )

        top_referrers = list(
            ref_qs.filter(status="converted")
            .values("referrer__user__email")
            .annotate(conversions=Count("id"))
            .order_by("-conversions")[:10]
        )

        total_codes = code_qs.count()
        active_codes = code_qs.filter(is_active=True).count()

        data["referral_data"] = {
            "total_clicks": total_attributions,
            "converted": converted,
            "pending": pending_referrals,
            "conversion_rate": conv_rate,
            "total_codes": total_codes,
            "active_codes": active_codes,
            "top_referrers": top_referrers,
            "note": "UTM/campaign tracking is not implemented. Only referral code data is available. Use Google Analytics for full traffic source breakdown.",
        }

        if not output_json:
            self.stdout.write(self.style.SUCCESS("\n=== 5. REFERRAL DATA (proxy for traffic sources) ==="))
            self.stdout.write(
                "NOTE: UTM tracking is not implemented. Only referral code data available.\n"
                "For traffic sources, use Google Analytics.\n"
            )
            self.stdout.write(f"  Referral codes created: {total_codes} (active: {active_codes})")
            self.stdout.write(f"  Total referral clicks:  {total_attributions}")
            self.stdout.write(f"  Converted:              {converted}")
            self.stdout.write(f"  Pending:                {pending_referrals}")
            self.stdout.write(f"  Conversion rate:        {conv_rate}")

            if top_referrers:
                self.stdout.write(f"\n  Top referrers (by conversions):")
                for ref in top_referrers:
                    self.stdout.write(
                        f"    {ref['referrer__user__email']:<35} {ref['conversions']:>3} conversions"
                    )

        # ── 6. Event Activity ────────────────────────────────────────
        events_qs = MeetupEvent.objects.filter(is_published=True)
        if since:
            events_qs = events_qs.filter(date_time__date__gte=since)
        if until:
            events_qs = events_qs.filter(date_time__date__lt=until)

        total_events = events_qs.count()
        cancelled_events = events_qs.filter(is_cancelled=True).count()
        active_events = events_qs.filter(is_cancelled=False)
        by_type = dict(
            active_events.values_list("event_type").annotate(c=Count("id")).values_list("event_type", "c")
        )

        reg_qs = EventRegistration.objects.filter(event__in=active_events)
        total_registrations = reg_qs.count()
        reg_by_status = dict(
            reg_qs.values_list("status").annotate(c=Count("id")).values_list("status", "c")
        )
        confirmed_attended = reg_by_status.get("confirmed", 0) + reg_by_status.get("attended", 0)
        attended = reg_by_status.get("attended", 0)
        no_show = reg_by_status.get("no_show", 0)
        paid_reg = reg_qs.filter(payment_confirmed=True).count()

        # Avg fill rate
        fill_data = active_events.filter(max_participants__gt=0).annotate(
            confirmed_count=Count(
                "eventregistration",
                filter=Q(eventregistration__status__in=["confirmed", "attended"]),
            )
        ).annotate(
            fill_pct=ExpressionWrapper(
                F("confirmed_count") * 100.0 / F("max_participants"),
                output_field=FloatField(),
            )
        )
        avg_fill = fill_data.aggregate(avg=Avg("fill_pct"))["avg"]
        avg_fill_str = f"{avg_fill:.1f}%" if avg_fill is not None else "N/A"

        data["event_activity"] = {
            "total_events": total_events,
            "cancelled": cancelled_events,
            "by_type": by_type,
            "total_registrations": total_registrations,
            "confirmed_attended": confirmed_attended,
            "attended": attended,
            "no_show": no_show,
            "paid_registrations": paid_reg,
            "avg_fill_rate": avg_fill_str,
        }

        if not output_json:
            self.stdout.write(self.style.SUCCESS("\n=== 6. EVENT ACTIVITY ==="))
            self.stdout.write(f"  Events published:      {total_events}  (cancelled: {cancelled_events})")
            if by_type:
                self.stdout.write(f"  By type:")
                for etype, cnt in sorted(by_type.items(), key=lambda x: -x[1]):
                    self.stdout.write(f"    {etype:<20} {cnt:>5}")
            self.stdout.write(f"\n  Total registrations:   {total_registrations}")
            self.stdout.write(f"    Confirmed+Attended:  {confirmed_attended:>5}")
            self.stdout.write(f"    Attended:            {attended:>5}")
            self.stdout.write(f"    No-show:             {no_show:>5}")
            self.stdout.write(f"    Paid:                {paid_reg:>5}")
            self.stdout.write(f"  Avg fill rate:         {avg_fill_str}")

        # ── 7. Connections & Matching ────────────────────────────────
        conn_qs = EventConnection.objects.all()
        if since:
            conn_qs = conn_qs.filter(requested_at__date__gte=since)
        if until:
            conn_qs = conn_qs.filter(requested_at__date__lt=until)

        total_connections = conn_qs.count()
        conn_by_status = dict(
            conn_qs.values_list("status").annotate(c=Count("id")).values_list("status", "c")
        )

        # Mutual connections using the custom manager
        mutual_count = conn_qs.annotate(
            is_mutual_annotated=Exists(
                EventConnection.objects.filter(
                    requester=OuterRef("recipient"),
                    recipient=OuterRef("requester"),
                    event=OuterRef("event"),
                )
            )
        ).filter(is_mutual_annotated=True).count() // 2

        shared_count = conn_by_status.get("shared", 0)

        # Messages
        msg_qs = ConnectionMessage.objects.filter(connection__in=conn_qs)
        total_messages = msg_qs.count()
        coach_messages = msg_qs.filter(is_coach_message=True).count()

        data["connections"] = {
            "total_requests": total_connections,
            "by_status": conn_by_status,
            "mutual_matches": mutual_count,
            "shared_contacts": shared_count,
            "total_messages": total_messages,
            "coach_messages": coach_messages,
        }

        if not output_json:
            self.stdout.write(self.style.SUCCESS("\n=== 7. CONNECTIONS & MATCHING ==="))
            self.stdout.write(f"  Total connection requests: {total_connections}")
            for status_key in ["pending", "accepted", "declined", "coach_reviewing", "coach_approved", "shared"]:
                c = conn_by_status.get(status_key, 0)
                self.stdout.write(f"    {status_key:<20} {c:>5}")
            self.stdout.write(f"  Mutual matches:            {mutual_count}")
            self.stdout.write(f"\n  Messages sent:             {total_messages}  (coach: {coach_messages})")

        # ── 8. Crush Sparks ──────────────────────────────────────────
        spark_qs = CrushSpark.objects.all()
        if since:
            spark_qs = spark_qs.filter(created_at__date__gte=since)
        if until:
            spark_qs = spark_qs.filter(created_at__date__lt=until)

        total_sparks = spark_qs.count()
        spark_by_status = dict(
            spark_qs.values_list("status").annotate(c=Count("id")).values_list("status", "c")
        )
        spark_delivered = spark_by_status.get("delivered", 0) + spark_by_status.get("completed", 0)
        spark_completed = spark_by_status.get("completed", 0)
        spark_revealed = spark_qs.filter(is_sender_revealed=True).count()
        spark_cancelled = spark_by_status.get("cancelled", 0) + spark_by_status.get("expired", 0)
        spark_completion_rate = f"{spark_completed / spark_delivered * 100:.1f}%" if spark_delivered else "N/A"

        data["sparks"] = {
            "total": total_sparks,
            "by_status": spark_by_status,
            "delivered": spark_delivered,
            "completed": spark_completed,
            "sender_revealed": spark_revealed,
            "cancelled_expired": spark_cancelled,
            "delivery_completion_rate": spark_completion_rate,
        }

        if not output_json:
            self.stdout.write(self.style.SUCCESS("\n=== 8. CRUSH SPARKS ==="))
            self.stdout.write(f"  Total sparks created:  {total_sparks}")
            for s_status in ["requested", "pending_review", "coach_approved", "coach_assigned", "journey_created", "delivered", "completed", "cancelled", "expired"]:
                c = spark_by_status.get(s_status, 0)
                if c:
                    self.stdout.write(f"    {s_status:<20} {c:>5}")
            self.stdout.write(f"  Sender revealed:       {spark_revealed}")
            self.stdout.write(f"  Delivery->Completion:  {spark_completion_rate}")

        # ── 9. User Engagement & Retention ───────────────────────────
        ua_qs = UserActivity.objects.all()
        total_tracked = ua_qs.count()
        now = timezone.now()
        active_7d = ua_qs.filter(last_seen__gte=now - timedelta(days=7)).count()
        active_30d = ua_qs.filter(last_seen__gte=now - timedelta(days=30)).count()
        pwa_users = ua_qs.filter(is_pwa_user=True).count()
        avg_visits = ua_qs.aggregate(avg=Avg("total_visits"))["avg"] or 0
        reminders_sent = ua_qs.filter(reminders_sent_count__gt=0).count()

        pwa_by_os = list(
            PWADeviceInstallation.objects.values("os_type").annotate(c=Count("id")).order_by("-c")
        )
        pwa_by_form = list(
            PWADeviceInstallation.objects.values("form_factor").annotate(c=Count("id")).order_by("-c")
        )
        total_pwa_installs = PWADeviceInstallation.objects.count()

        data["user_engagement"] = {
            "note": "Retention metrics are snapshots (not date-filtered).",
            "total_tracked": total_tracked,
            "active_7d": active_7d,
            "active_30d": active_30d,
            "pwa_users": pwa_users,
            "avg_visits_per_user": round(avg_visits, 1),
            "users_sent_reminders": reminders_sent,
            "pwa_installations": total_pwa_installs,
            "pwa_by_os": pwa_by_os,
            "pwa_by_form_factor": pwa_by_form,
        }

        if not output_json:
            self.stdout.write(self.style.SUCCESS("\n=== 9. USER ENGAGEMENT & RETENTION ==="))
            self.stdout.write("  NOTE: Retention metrics are snapshots (not date-filtered).\n")
            self.stdout.write(f"  Users tracked:         {total_tracked}")
            pct_7 = f"({active_7d / total_tracked * 100:.1f}%)" if total_tracked else ""
            pct_30 = f"({active_30d / total_tracked * 100:.1f}%)" if total_tracked else ""
            pct_pwa = f"({pwa_users / total_tracked * 100:.1f}%)" if total_tracked else ""
            self.stdout.write(f"  Active last 7 days:    {active_7d:>5}  {pct_7}")
            self.stdout.write(f"  Active last 30 days:   {active_30d:>5}  {pct_30}")
            self.stdout.write(f"  PWA users:             {pwa_users:>5}  {pct_pwa}")
            self.stdout.write(f"  Avg visits/user:       {avg_visits:.1f}")
            self.stdout.write(f"  Users sent reminders:  {reminders_sent}")
            self.stdout.write(f"\n  PWA Installations:     {total_pwa_installs}")
            if pwa_by_os:
                self.stdout.write(f"    By OS:")
                for row in pwa_by_os:
                    self.stdout.write(f"      {row['os_type']:<15} {row['c']:>5}")
            if pwa_by_form:
                self.stdout.write(f"    By form factor:")
                for row in pwa_by_form:
                    self.stdout.write(f"      {row['form_factor']:<15} {row['c']:>5}")

        # ── 10. Coach Operations ─────────────────────────────────────
        active_coaches = CrushCoach.objects.filter(is_active=True).count()
        total_coaches = CrushCoach.objects.count()

        coach_sub_qs = ProfileSubmission.objects.all()
        if since:
            coach_sub_qs = coach_sub_qs.filter(submitted_at__date__gte=since)
        if until:
            coach_sub_qs = coach_sub_qs.filter(submitted_at__date__lt=until)

        total_submitted = coach_sub_qs.count()
        reviewed = coach_sub_qs.exclude(reviewed_at__isnull=True)
        total_reviewed = reviewed.count()

        avg_review_td = reviewed.annotate(
            review_duration=F("reviewed_at") - F("submitted_at")
        ).aggregate(avg=Avg("review_duration"))["avg"]

        calls_with_review = coach_sub_qs.filter(review_call_completed=True).count()

        call_qs = CallAttempt.objects.all()
        if since:
            call_qs = call_qs.filter(attempt_date__date__gte=since)
        if until:
            call_qs = call_qs.filter(attempt_date__date__lt=until)

        total_calls = call_qs.count()
        call_results = dict(
            call_qs.values_list("result").annotate(c=Count("id")).values_list("result", "c")
        )
        success_calls = call_results.get("success", 0)
        call_success_rate = f"{success_calls / total_calls * 100:.1f}%" if total_calls else "N/A"

        data["coach_operations"] = {
            "active_coaches": active_coaches,
            "total_coaches": total_coaches,
            "submissions_in_period": total_submitted,
            "reviewed": total_reviewed,
            "avg_review_time": str(avg_review_td) if avg_review_td else None,
            "screening_calls_done": calls_with_review,
            "total_call_attempts": total_calls,
            "call_results": call_results,
            "call_success_rate": call_success_rate,
        }

        if not output_json:
            self.stdout.write(self.style.SUCCESS("\n=== 10. COACH OPERATIONS ==="))
            self.stdout.write(f"  Active coaches:        {active_coaches} / {total_coaches}")
            self.stdout.write(f"\n  Reviews (in period):")
            self.stdout.write(f"    Submitted:           {total_submitted:>5}")
            self.stdout.write(f"    Reviewed:            {total_reviewed:>5}")
            self.stdout.write(f"    Avg review time:     {_fmt_timedelta(avg_review_td)}")
            self.stdout.write(f"    Screening calls:     {calls_with_review:>5}")
            self.stdout.write(f"\n  Call attempts:         {total_calls}")
            for result_key in ["success", "failed", "sms_sent", "event_invite_sms"]:
                c = call_results.get(result_key, 0)
                if c:
                    self.stdout.write(f"    {result_key:<20} {c:>5}")
            self.stdout.write(f"  Call success rate:     {call_success_rate}")

        # ── 11. Membership Tiers ─────────────────────────────────────
        tier_counts = dict(
            all_profiles.values_list("membership_tier").annotate(c=Count("id")).values_list("membership_tier", "c")
        )
        tier_approved = dict(
            approved.values_list("membership_tier").annotate(c=Count("id")).values_list("membership_tier", "c")
        )
        phone_verified = all_profiles.filter(phone_verified=True).count()

        data["membership_tiers"] = {
            "by_tier": tier_counts,
            "approved_by_tier": tier_approved,
            "phone_verified": phone_verified,
        }

        if not output_json:
            self.stdout.write(self.style.SUCCESS("\n=== 11. MEMBERSHIP TIERS ==="))
            self.stdout.write(f"{'Tier':<15} {'Total':>6} {'Approved':>9} {'% of Total':>11}")
            self.stdout.write("-" * 43)
            profile_total = all_profiles.count() or 1
            for tier in ["basic", "bronze", "silver", "gold"]:
                t = tier_counts.get(tier, 0)
                a = tier_approved.get(tier, 0)
                pct = f"{t / profile_total * 100:.1f}%"
                self.stdout.write(f"  {tier:<13} {t:>6} {a:>9} {pct:>11}")
            self.stdout.write("-" * 43)
            pv_pct = f"({phone_verified / profile_total * 100:.1f}%)" if profile_total else ""
            self.stdout.write(f"  Phone verified: {phone_verified}  {pv_pct}")

        # ── 12. Journey Engagement ───────────────────────────────────
        jp_qs = JourneyProgress.objects.all()
        if since:
            jp_qs = jp_qs.filter(started_at__date__gte=since)
        if until:
            jp_qs = jp_qs.filter(started_at__date__lt=until)

        journeys_started = jp_qs.count()
        journeys_completed = jp_qs.filter(is_completed=True).count()
        journey_completion_rate = f"{journeys_completed / journeys_started * 100:.1f}%" if journeys_started else "N/A"

        completed_jp = jp_qs.filter(is_completed=True)
        avg_points = completed_jp.aggregate(avg=Avg("total_points"))["avg"] or 0
        avg_time_s = completed_jp.aggregate(avg=Avg("total_time_seconds"))["avg"]
        avg_time_str = _fmt_timedelta(timedelta(seconds=int(avg_time_s))) if avg_time_s else "N/A"

        final_yes = jp_qs.filter(final_response="yes").count()
        final_thinking = jp_qs.filter(final_response="thinking").count()

        # Chapter drop-off funnel
        chapter_funnel = list(
            ChapterProgress.objects.filter(journey_progress__in=jp_qs, is_completed=True)
            .values("chapter__chapter_number")
            .annotate(c=Count("id"))
            .order_by("chapter__chapter_number")
        )

        data["journey_engagement"] = {
            "started": journeys_started,
            "completed": journeys_completed,
            "completion_rate": journey_completion_rate,
            "avg_points": round(avg_points, 1),
            "avg_time": avg_time_str,
            "final_response_yes": final_yes,
            "final_response_thinking": final_thinking,
            "chapter_funnel": chapter_funnel,
        }

        if not output_json:
            self.stdout.write(self.style.SUCCESS("\n=== 12. JOURNEY ENGAGEMENT ==="))
            self.stdout.write(f"  Journeys started:      {journeys_started}")
            self.stdout.write(f"  Journeys completed:    {journeys_completed}  ({journey_completion_rate})")
            self.stdout.write(f"  Avg points (completed):{avg_points:.0f}")
            self.stdout.write(f"  Avg time (completed):  {avg_time_str}")
            if final_yes or final_thinking:
                self.stdout.write(f"\n  Final response:")
                self.stdout.write(f"    Yes, let's go:       {final_yes:>5}")
                self.stdout.write(f"    Need to think:       {final_thinking:>5}")
            if chapter_funnel:
                self.stdout.write(f"\n  Chapter completion funnel:")
                for ch in chapter_funnel:
                    self.stdout.write(f"    Ch {ch['chapter__chapter_number']:>2}:  {ch['c']:>5} completed")

        if not output_json:
            self.stdout.write(self.style.SUCCESS("\n=== DONE ===\n"))

        # ── JSON output ──────────────────────────────────────────────
        if output_json:
            if since or until:
                data["_filter"] = {"since": str(since) if since else None, "until": str(until) if until else None}
            self.stdout.write(json.dumps(data, indent=2, default=str))

    # ── Monthly breakdown ────────────────────────────────────────────
    def _monthly_breakdown(self, since, until, output_json):
        """Show key metrics broken down by month."""
        months = []
        cursor = since.replace(day=1)
        while cursor < until:
            next_month = _add_months(cursor, 1)
            months.append((cursor, next_month))
            cursor = next_month

        monthly_data = []
        for month_start, month_end in months:
            profiles = CrushProfile.objects.filter(
                created_at__date__gte=month_start,
                created_at__date__lt=month_end,
            )
            total = profiles.count()
            approved = profiles.filter(is_approved=True).count()
            male = profiles.filter(gender="M").count()
            female = profiles.filter(gender="F").count()
            no_gender = profiles.filter(Q(gender="") | Q(gender__isnull=True)).count()
            submitted = profiles.filter(completion_status="submitted").count()
            has_photo = profiles.exclude(photo_1="").exclude(photo_1__isnull=True).count()
            has_bio = profiles.exclude(bio="").exclude(bio__isnull=True).count()

            ref_clicks = ReferralAttribution.objects.filter(
                created_at__date__gte=month_start,
                created_at__date__lt=month_end,
            ).count()
            ref_converted = ReferralAttribution.objects.filter(
                created_at__date__gte=month_start,
                created_at__date__lt=month_end,
                status="converted",
            ).count()
            codes_created = ReferralCode.objects.filter(
                created_at__date__gte=month_start,
                created_at__date__lt=month_end,
            ).count()

            # ── Engagement metrics ──
            # Events
            month_events = MeetupEvent.objects.filter(
                is_published=True, is_cancelled=False,
                date_time__date__gte=month_start, date_time__date__lt=month_end,
            )
            events_count = month_events.count()
            attended_count = EventRegistration.objects.filter(
                event__in=month_events, status="attended",
            ).count()
            paid_count = EventRegistration.objects.filter(
                event__in=month_events, payment_confirmed=True,
            ).count()

            # Connections
            month_conn = EventConnection.objects.filter(
                requested_at__date__gte=month_start, requested_at__date__lt=month_end,
            )
            conn_count = month_conn.count()
            mutual_count = month_conn.annotate(
                is_mutual_annotated=Exists(
                    EventConnection.objects.filter(
                        requester=OuterRef("recipient"),
                        recipient=OuterRef("requester"),
                        event=OuterRef("event"),
                    )
                )
            ).filter(is_mutual_annotated=True).count() // 2
            shared_count = month_conn.filter(status="shared").count()

            # Sparks
            sparks_count = CrushSpark.objects.filter(
                created_at__date__gte=month_start, created_at__date__lt=month_end,
            ).count()
            sparks_done = CrushSpark.objects.filter(
                created_at__date__gte=month_start, created_at__date__lt=month_end,
                status="completed",
            ).count()

            # Active users (snapshot at month end, capped at now)
            month_end_dt = timezone.make_aware(datetime.combine(month_end, datetime.min.time()))
            capped_end = min(month_end_dt, timezone.now())
            active_7d = UserActivity.objects.filter(
                last_seen__gte=capped_end - timedelta(days=7),
                last_seen__lt=capped_end,
            ).count()

            # Coach
            reviewed_count = ProfileSubmission.objects.filter(
                reviewed_at__date__gte=month_start, reviewed_at__date__lt=month_end,
            ).count()
            calls_count = CallAttempt.objects.filter(
                attempt_date__date__gte=month_start, attempt_date__date__lt=month_end,
            ).count()

            # Journeys
            journeys_started = JourneyProgress.objects.filter(
                started_at__date__gte=month_start, started_at__date__lt=month_end,
            ).count()
            journeys_done = JourneyProgress.objects.filter(
                completed_at__date__gte=month_start, completed_at__date__lt=month_end,
            ).count()

            row = {
                "month": month_start.strftime("%Y-%m"),
                "new_profiles": total,
                "approved": approved,
                "male": male,
                "female": female,
                "no_gender": no_gender,
                "submitted": submitted,
                "has_photo": has_photo,
                "has_bio": has_bio,
                "referral_clicks": ref_clicks,
                "referral_converted": ref_converted,
                "referral_codes_created": codes_created,
                # Engagement
                "events": events_count,
                "attended": attended_count,
                "paid_reg": paid_count,
                "connections": conn_count,
                "mutual": mutual_count,
                "shared": shared_count,
                "sparks": sparks_count,
                "sparks_done": sparks_done,
                "active_7d": active_7d,
                "reviewed": reviewed_count,
                "calls": calls_count,
                "journeys": journeys_started,
                "j_done": journeys_done,
            }
            monthly_data.append(row)

        # Cumulative totals
        cumulative = CrushProfile.objects.filter(created_at__date__lt=since).count()
        for row in monthly_data:
            cumulative += row["new_profiles"]
            row["cumulative_total"] = cumulative

        if output_json:
            self.stdout.write(json.dumps({"monthly": monthly_data, "period": {"since": str(since), "until": str(until)}}, indent=2, default=str))
            return

        # ── Table A: Profile & Acquisition ───────────────────────────
        self.stdout.write(self.style.SUCCESS(f"\n=== MONTHLY BREAKDOWN - PROFILES ({since} -> {until}) ===\n"))
        self.stdout.write(
            f"{'Month':<10} {'New':>5} {'Appr':>5} {'M':>4} {'F':>4} {'N/A':>4} "
            f"{'Subm':>5} {'Photo':>6} {'Bio':>5} "
            f"{'Ref>':>5} {'Conv':>5} {'Codes':>6} {'Cumul':>7}"
        )
        self.stdout.write("-" * 90)

        for r in monthly_data:
            self.stdout.write(
                f"{r['month']:<10} {r['new_profiles']:>5} {r['approved']:>5} "
                f"{r['male']:>4} {r['female']:>4} {r['no_gender']:>4} "
                f"{r['submitted']:>5} {r['has_photo']:>6} {r['has_bio']:>5} "
                f"{r['referral_clicks']:>5} {r['referral_converted']:>5} "
                f"{r['referral_codes_created']:>6} {r['cumulative_total']:>7}"
            )

        self.stdout.write("-" * 90)
        prof_keys = ["new_profiles", "approved", "male", "female", "no_gender", "submitted", "has_photo", "has_bio", "referral_clicks", "referral_converted", "referral_codes_created"]
        totals = {k: sum(r[k] for r in monthly_data) for k in prof_keys}
        self.stdout.write(
            f"{'TOTAL':<10} {totals['new_profiles']:>5} {totals['approved']:>5} "
            f"{totals['male']:>4} {totals['female']:>4} {totals['no_gender']:>4} "
            f"{totals['submitted']:>5} {totals['has_photo']:>6} {totals['has_bio']:>5} "
            f"{totals['referral_clicks']:>5} {totals['referral_converted']:>5} "
            f"{totals['referral_codes_created']:>6}"
        )

        # ── Table B: Engagement ──────────────────────────────────────
        self.stdout.write(self.style.SUCCESS(f"\n=== MONTHLY BREAKDOWN - ENGAGEMENT ({since} -> {until}) ===\n"))
        self.stdout.write(
            f"{'Month':<10} {'Evts':>5} {'Attnd':>6} {'Paid':>5} "
            f"{'Conn':>5} {'Mutu':>5} {'Shrd':>5} "
            f"{'Sprk':>5} {'S.Dn':>5} "
            f"{'A7d':>5} {'Revw':>5} {'Call':>5} "
            f"{'Jrny':>5} {'J.Dn':>5}"
        )
        self.stdout.write("-" * 90)

        for r in monthly_data:
            self.stdout.write(
                f"{r['month']:<10} {r['events']:>5} {r['attended']:>6} {r['paid_reg']:>5} "
                f"{r['connections']:>5} {r['mutual']:>5} {r['shared']:>5} "
                f"{r['sparks']:>5} {r['sparks_done']:>5} "
                f"{r['active_7d']:>5} {r['reviewed']:>5} {r['calls']:>5} "
                f"{r['journeys']:>5} {r['j_done']:>5}"
            )

        self.stdout.write("-" * 90)
        eng_keys = ["events", "attended", "paid_reg", "connections", "mutual", "shared", "sparks", "sparks_done", "reviewed", "calls", "journeys", "j_done"]
        eng_totals = {k: sum(r[k] for r in monthly_data) for k in eng_keys}
        self.stdout.write(
            f"{'TOTAL':<10} {eng_totals['events']:>5} {eng_totals['attended']:>6} {eng_totals['paid_reg']:>5} "
            f"{eng_totals['connections']:>5} {eng_totals['mutual']:>5} {eng_totals['shared']:>5} "
            f"{eng_totals['sparks']:>5} {eng_totals['sparks_done']:>5} "
            f"{'':>5} {eng_totals['reviewed']:>5} {eng_totals['calls']:>5} "
            f"{eng_totals['journeys']:>5} {eng_totals['j_done']:>5}"
        )
        self.stdout.write(self.style.SUCCESS("\n=== DONE ===\n"))
