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

        from crush_lu.api_admin_hybrid import run_sla_sweep

        if not getattr(settings, "HYBRID_COACH_SYSTEM_ENABLED", False):
            self.stdout.write(
                self.style.WARNING(
                    "HYBRID_COACH_SYSTEM_ENABLED=False — set it in .env to run the sweep."
                )
            )
            return

        # Same code path as the API sweep (new breaches, backed-off retries and
        # stale-lease recovery), so the two can never drift apart.
        result = run_sla_sweep(
            opts["host"], not opts["insecure"], actor="system:sla_tick"
        )
        self.stdout.write(
            self.style.SUCCESS(
                "sla_tick done: "
                + " ".join(f"{k}={v}" for k, v in result.items() if k != "timestamp")
            )
        )
