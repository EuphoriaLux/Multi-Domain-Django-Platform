"""
real_person_check_cohort: count the members the real-person check covers.

Prints aggregate counts only, never names, emails, phones or ids, so it is
safe to run on production before the feature flags are switched on.

Usage::

    python manage.py real_person_check_cohort

Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from crush_lu.models import EventRegistration
from crush_lu.services.crush_connect import CONNECT_INACTIVITY_WINDOW_DAYS
from crush_lu.services.real_person_check import real_person_check_cohort


class Command(BaseCommand):
    help = "Count Connect members without coach-attested attendance (no PII)."

    def handle(self, *args, **options):
        cohort = real_person_check_cohort()
        cutoff = timezone.now() - timedelta(days=CONNECT_INACTIVITY_WINDOW_DAYS)
        self_scanned = Exists(
            EventRegistration.objects.filter(user_id=OuterRef("pk"), status="attended")
        )
        rows = [
            ("cohort.total", cohort),
            ("cohort.last_login_within_30d", cohort.filter(last_login__gte=cutoff)),
            (
                "cohort.luxid_native",
                cohort.filter(_has_luxid_native=True),
            ),
            (
                "cohort.luxid_oidc_only",
                cohort.filter(_has_luxid_native=False, _has_luxid_oidc=True),
            ),
            ("cohort.self_scanned_attendance_only", cohort.filter(self_scanned)),
            (
                "cohort.phone_verified",
                cohort.filter(crushprofile__phone_verified=True),
            ),
            (
                "cohort.staff_or_superuser",
                cohort.filter(Q(is_staff=True) | Q(is_superuser=True)),
            ),
        ]
        for label, qs in rows:
            self.stdout.write(f"{label} {qs.count()}")
