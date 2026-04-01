"""
Management command to extract key metrics for the Crush.lu business plan.

Outputs: gender split, age distribution, profile completion, location
distribution, and referral data.
"""

import json
from datetime import date

from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from crush_lu.models import CrushProfile, ProfileSubmission
from crush_lu.models.referrals import ReferralAttribution, ReferralCode


class Command(BaseCommand):
    help = "Output business plan metrics (gender, age, completion, referrals)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--json", action="store_true", help="Output as JSON instead of tables"
        )

    def handle(self, *args, **options):
        output_json = options["json"]
        all_profiles = CrushProfile.objects.all()
        approved = all_profiles.filter(is_approved=True)
        not_approved = all_profiles.filter(is_approved=False)

        data = {}

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
        submission_counts = dict(
            ProfileSubmission.objects.values_list("status")
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
        total_attributions = ReferralAttribution.objects.count()
        converted = ReferralAttribution.objects.filter(status="converted").count()
        pending_referrals = ReferralAttribution.objects.filter(status="pending").count()
        conv_rate = (
            f"{converted / total_attributions * 100:.1f}%"
            if total_attributions
            else "N/A"
        )

        top_referrers = list(
            ReferralAttribution.objects.filter(status="converted")
            .values("referrer__user__email")
            .annotate(conversions=Count("id"))
            .order_by("-conversions")[:10]
        )

        total_codes = ReferralCode.objects.count()
        active_codes = ReferralCode.objects.filter(is_active=True).count()

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

            self.stdout.write(self.style.SUCCESS("\n=== DONE ===\n"))

        # ── JSON output ──────────────────────────────────────────────
        if output_json:
            self.stdout.write(json.dumps(data, indent=2, default=str))
