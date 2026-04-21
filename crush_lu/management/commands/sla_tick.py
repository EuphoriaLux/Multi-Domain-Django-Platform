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
        import uuid
        from datetime import timedelta

        from django.conf import settings
        from django.db import transaction
        from django.utils import timezone

        from crush_lu.models import ProfileSubmission
        from crush_lu.tasks import send_sla_fallback_email_task

        if not getattr(settings, "HYBRID_COACH_SYSTEM_ENABLED", False):
            self.stdout.write(
                self.style.WARNING(
                    "HYBRID_COACH_SYSTEM_ENABLED=False — set it in .env to run the sweep."
                )
            )
            return

        now = timezone.now()
        fallback_ttl = timedelta(days=30)
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

        processed = 0
        failed = 0
        with transaction.atomic():
            locked_ids = list(
                ProfileSubmission.objects.filter(pk__in=candidates.values("pk"))
                .select_for_update(skip_locked=True)
                .values_list("pk", flat=True)
            )
            submissions = (
                ProfileSubmission.objects.filter(pk__in=locked_ids)
                .select_related("coach__user", "profile__user")
            )
            for sub in submissions:
                try:
                    sub.fallback_offered_at = now
                    sub.booking_token = uuid.uuid4()
                    sub.booking_token_expires_at = now + fallback_ttl
                    sub.log_system_action(
                        "fallback_offered",
                        actor="system:sla_tick",
                        reason="sla_breach",
                    )
                    sub.save(
                        update_fields=[
                            "fallback_offered_at",
                            "booking_token",
                            "booking_token_expires_at",
                            "system_actions",
                        ]
                    )
                    send_sla_fallback_email_task.enqueue(
                        submission_id=sub.pk,
                        host=opts["host"],
                        is_secure=not opts["insecure"],
                    )
                    processed += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  offered submission #{sub.pk} "
                            f"(user={sub.profile.user.email}, coach={sub.coach.user.email}, "
                            f"token={sub.booking_token})"
                        )
                    )
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    self.stderr.write(f"  FAILED submission #{sub.pk}: {e}")

        self.stdout.write(
            self.style.SUCCESS(
                f"sla_tick done: processed={processed} failed={failed}"
            )
        )
