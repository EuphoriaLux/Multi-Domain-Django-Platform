"""
Compute, persist, and email the weekly Crush.lu KPI digest.

Defaults to the most recently *completed* ISO week (Mon..Sun). Run from a dev
shell, or let the Azure Function timer drive it via the
``/api/admin/weekly-kpis/`` endpoint on Monday mornings.

    python manage.py send_weekly_kpis                       # last full week, email it
    python manage.py send_weekly_kpis --week-start 2026-06-08
    python manage.py send_weekly_kpis --no-email            # compute + persist only

Recipients come from ``settings.WEEKLY_KPI_RECIPIENTS`` (env-driven). With no
recipients configured the command still computes and persists the snapshot, and
just warns that nothing was emailed.
"""
import json
from datetime import datetime, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags

from crush_lu.services.weekly_kpis import (
    last_completed_week_start,
    snapshot_with_deltas,
    upsert_snapshot,
)


# Ordered (section title, [(metric_key, label)]) so the email reads as a funnel.
# Keys ending in "_pct" render as percentages with "pts" deltas.
EMAIL_SECTIONS = [
    (
        "Acquisition funnel",
        [
            ("new_signups", "New sign-ups"),
            ("new_profiles", "New profiles started"),
            ("phone_verifications", "Phone verifications"),
            ("profiles_submitted", "Profiles submitted"),
            ("profiles_verified", "Profiles verified ★"),
            ("signup_to_verified_pct", "Sign-up → verified"),
            ("submitted_to_verified_pct", "Submitted → verified"),
            ("cumulative_total_users", "Total users (cumulative)"),
            ("cumulative_verified_members", "Verified members (cumulative)"),
        ],
    ),
    (
        "Engagement & retention",
        [
            ("wau", "Weekly active users"),
            ("new_active", "New active"),
            ("returning_active", "Returning active"),
            ("pwa_active", "PWA active"),
            ("dormant", "Dormant (quiet ≥1 week)"),
        ],
    ),
    (
        "Revenue & premium",
        [
            ("new_premium", "New premium members"),
            ("total_active_premium", "Active premium (total)"),
            ("new_connect_optins", "New Connect opt-ins"),
            ("total_connect_onboarded", "Connect onboarded (total)"),
            ("waitlist_new", "New waitlist joiners"),
            ("waitlist_total", "Waitlist size (total)"),
            ("paid_event_registrations", "Paid event registrations"),
        ],
    ),
    (
        "Matching & events",
        [
            ("events_held", "Events held"),
            ("registrations", "Event registrations"),
            ("attended", "Attended"),
            ("no_show", "No-shows"),
            ("avg_fill_rate_pct", "Avg fill rate"),
            ("connections_requested", "Connections requested"),
            ("connections_shared", "Contacts shared"),
            ("referrals_converted", "Referrals converted"),
        ],
    ),
]

# Map section title → the metrics dict key it draws from.
_SECTION_KEYS = {
    "Acquisition funnel": "acquisition",
    "Engagement & retention": "engagement",
    "Revenue & premium": "revenue",
    "Matching & events": "matching_events",
}


def _format_value(key, value):
    if key.endswith("_pct"):
        return f"{value}%"
    return str(value)


def _format_delta(key, delta):
    """Human delta string with arrow, or '' when there's no prior week."""
    if delta is None:
        return ""
    unit = " pts" if key.endswith("_pct") else ""
    if delta > 0:
        return f"▲ +{delta}{unit}"
    if delta < 0:
        return f"▼ {delta}{unit}"
    return "–"


def build_email_sections(payload):
    """Turn a snapshot+deltas payload into render-ready sections.

    Returns a list of ``{"title", "rows": [{"label", "value", "delta"}]}`` so the
    template needs no arithmetic or None-handling of its own.
    """
    metrics = payload["metrics"]
    deltas = payload["deltas"]
    sections = []
    for title, fields in EMAIL_SECTIONS:
        group_key = _SECTION_KEYS[title]
        group_metrics = metrics.get(group_key, {})
        group_deltas = deltas.get(group_key, {})
        rows = [
            {
                "label": label,
                "value": _format_value(key, group_metrics.get(key, 0)),
                "delta": _format_delta(key, group_deltas.get(key)),
            }
            for key, label in fields
        ]
        sections.append({"title": title, "rows": rows})
    return sections


class Command(BaseCommand):
    help = "Compute, persist, and email the weekly KPI digest for Crush.lu"

    def add_arguments(self, parser):
        parser.add_argument(
            "--week-start",
            type=str,
            help="Monday (YYYY-MM-DD) that begins the week. Defaults to last full week.",
        )
        parser.add_argument(
            "--no-email",
            action="store_true",
            help="Compute and persist the snapshot without sending the email.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Print the full metrics + deltas payload as JSON (implies --no-email).",
        )

    def handle(self, *args, **options):
        if options["week_start"]:
            try:
                week_start = datetime.strptime(options["week_start"], "%Y-%m-%d").date()
            except ValueError as exc:
                raise CommandError(f"Invalid --week-start: {exc}") from exc
        else:
            week_start = last_completed_week_start(timezone.localdate())

        if week_start.weekday() != 0:
            # Normalise to that week's Monday so the unique key stays consistent.
            week_start = week_start - timedelta(days=week_start.weekday())

        _, created = upsert_snapshot(week_start)
        self.stdout.write(
            self.style.SUCCESS(
                f"{'Created' if created else 'Updated'} snapshot for week of {week_start}"
            )
        )

        payload = snapshot_with_deltas(week_start)

        if options["json"]:
            serialisable = {
                "week_start": payload["week_start"].isoformat(),
                "week_end": payload["week_end"].isoformat(),
                "previous_week_start": (
                    payload["previous_week_start"].isoformat()
                    if payload["previous_week_start"]
                    else None
                ),
                "metrics": payload["metrics"],
                "deltas": payload["deltas"],
            }
            self.stdout.write(json.dumps(serialisable, indent=2))
            return

        if options["no_email"]:
            self.stdout.write("--no-email set; skipping email.")
            self._print_summary(payload)
            return

        recipients = [
            r.strip()
            for r in getattr(settings, "WEEKLY_KPI_RECIPIENTS", []) or []
            if r and r.strip()
        ]
        if not recipients:
            self.stdout.write(
                self.style.WARNING(
                    "WEEKLY_KPI_RECIPIENTS is empty; snapshot saved but no email sent."
                )
            )
            self._print_summary(payload)
            return

        self._send_email(recipients, payload)
        self.stdout.write(
            self.style.SUCCESS(f"Weekly KPI email sent to {len(recipients)} recipient(s).")
        )

    def _send_email(self, recipients, payload):
        from azureproject.email_utils import send_domain_email

        context = {
            "site_name": "Crush.lu",
            "sections": build_email_sections(payload),
            **payload,
        }
        html_message = render_to_string("crush_lu/email/weekly_kpis.html", context)
        plain_message = strip_tags(html_message)
        subject = (
            f"Crush.lu weekly KPIs — week of {payload['week_start']:%d %b %Y}"
        )
        send_domain_email(
            subject=subject,
            message=plain_message,
            recipient_list=recipients,
            domain="crush.lu",
            html_message=html_message,
            fail_silently=False,
        )

    def _print_summary(self, payload):
        """Echo the headline numbers to the console (dev convenience)."""
        m = payload["metrics"]
        self.stdout.write(
            f"  Signups: {m['acquisition']['new_signups']}  "
            f"Verified: {m['acquisition']['profiles_verified']}  "
            f"WAU: {m['engagement']['wau']}  "
            f"New premium: {m['revenue']['new_premium']}  "
            f"Events: {m['matching_events']['events_held']}"
        )
