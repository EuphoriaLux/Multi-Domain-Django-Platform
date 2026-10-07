"""
Run one tick of the hybrid-coach SLA sweep from the command line.

Used during local development to drive Phase 3 without waiting for the
Azure Function timer or standing up a worker. Mirrors what
``api_admin_hybrid.sla_sweep`` does, minus the Bearer auth.

Typical flow:
    1. Ensure HYBRID_COACH_SYSTEM_ENABLED=True in .env (or export inline).
    2. Opt a coach in: `hybrid_features_enabled=True`, `working_mode='hybrid'`,
       add ≥1 availability window via /coach/settings/.
    3. Backdate a submission's SLA to force a breach:
           ProfileSubmission.objects.filter(pk=...).update(
               sla_deadline=timezone.now() - timedelta(minutes=1)
           )
    4. `python manage.py sla_tick`
    5. Check /profile-submitted/ for the fallback banner and console for the
       email.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run the hybrid-coach SLA sweep once (dev-only; production uses Azure Functions)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--host",
            default="localhost:8000",
            help="Host used for building booking URLs in the email (default: localhost:8000)",
        )
        parser.add_argument(
            "--insecure",
            action="store_true",
            help="Use http:// instead of https:// in generated links (for local dev).",
        )

    def handle(self, *args, **opts):
        from django.conf import settings
        from django.db import transaction
        from django.utils import timezone

        from crush_lu.api_admin_hybrid import (
            mark_fallback_offered,
            mark_fallback_sent,
            revert_fallback_offer,
        )
        from crush_lu.models import ProfileSubmission
        from crush_lu.tasks import (
            SLA_EMAIL_AMBIGUOUS,
            SLA_EMAIL_FAILED,
            SLA_EMAIL_SENT,
            SLA_EMAIL_STALE,
            deliver_sla_fallback_email,
        )

        if not getattr(settings, "HYBRID_COACH_SYSTEM_ENABLED", False):
            self.stdout.write(
                self.style.WARNING(
                    "HYBRID_COACH_SYSTEM_ENABLED=False — set it in .env to run the sweep."
                )
            )
            return

        now = timezone.now()
        candidates = (
            ProfileSubmission.objects.filter(
                status="pending",
                sla_deadline__lte=now,
                sla_deadline__isnull=False,
                fallback_offered_at__isnull=True,
                booking_token__isnull=True,
                coach__hybrid_features_enabled=True,
                is_paused=False,
            )
            .select_related("coach__user", "profile__user")
        )

        # Claim under the lock, then send after the commit: a slow or failing
        # mail backend must not hold row locks, and an offer whose email did not
        # go out is undone so the next tick retries it.
        claimed = []
        failed = 0
        with transaction.atomic():
            locked_ids = list(
                ProfileSubmission.objects.filter(pk__in=candidates.values("pk"))
                .select_for_update(skip_locked=True)
                .values_list("pk", flat=True)
            )
            submissions = ProfileSubmission.objects.filter(
                pk__in=locked_ids
            ).select_related("coach__user", "profile__user")
            for sub in submissions:
                try:
                    with transaction.atomic():
                        mark_fallback_offered(
                            sub, now, actor="system:sla_tick", reason="sla_breach"
                        )
                    claimed.append((sub, sub.booking_token))
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    self.stderr.write(f"  FAILED submission #{sub.pk}: {e}")

        processed = 0
        for sub, token in claimed:
            outcome = deliver_sla_fallback_email(
                sub.pk, opts["host"], not opts["insecure"]
            )
            if outcome == SLA_EMAIL_AMBIGUOUS:
                self.stderr.write(
                    f"  submission #{sub.pk}: send outcome unknown; token kept, "
                    "the lease retry reuses it"
                )
                continue
            if outcome == SLA_EMAIL_STALE:
                revert_fallback_offer(sub.pk, token, reason="no_longer_bookable")
                continue
            if outcome == SLA_EMAIL_FAILED:
                revert_fallback_offer(sub.pk, token, reason="email_not_sent")
                failed += 1
                self.stderr.write(
                    f"  email not sent for submission #{sub.pk}; offer undone"
                )
                continue
            mark_fallback_sent(sub.pk)
            processed += 1
            note = "" if outcome == SLA_EMAIL_SENT else " (no email: skipped)"
            self.stdout.write(
                self.style.SUCCESS(
                    f"  offered submission #{sub.pk} "
                    f"(user={sub.profile.user.email}, "
                    f"coach={sub.coach.user.email}){note}"
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"sla_tick done: processed={processed} failed={failed}"
            )
        )
