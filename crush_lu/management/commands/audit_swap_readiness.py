"""
audit_swap_readiness — Production migration and pre-slot-swap database health audit.

Audits the database state before promoting staging to production, specifically
validating the Crush Credit schema (migrations 0225–0231), historical registrations,
payment transactions, credit ledger integrity, and attendees on upcoming events.
Handles pre-migration and post-migration database states safely.

Usage:
    # Run a full readiness audit
    python manage.py audit_swap_readiness

    # Include detailed per-user tables for upcoming events and historical policies
    python manage.py audit_swap_readiness --detailed

    # Output JSON format for automated CI / ops tooling
    python manage.py audit_swap_readiness --json
"""

import json

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Count, Q, Sum
from django.utils import timezone


class Command(BaseCommand):
    help = (
        "Audit database integrity, migration status, and policy boundaries "
        "before an Azure App Service slot swap."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--detailed",
            action="store_true",
            help="Print detailed user lists for upcoming events, cancellations, and credits.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Output machine-readable JSON summary.",
        )

    def _get_existing_columns(self, table_name):
        with connection.cursor() as cursor:
            tables = connection.introspection.table_names(cursor)
            if table_name not in tables:
                return set()
            description = connection.introspection.get_table_description(
                cursor, table_name
            )
            return {col.name for col in description}

    def handle(self, *args, **options):
        detailed = options["detailed"]
        as_json = options["json"]

        from crush_lu.models.events import EventRegistration, MeetupEvent
        from crush_lu.models.payments import PaymentTransaction
        from crush_lu.utils.formatting import format_cents

        report = {
            "timestamp": timezone.now().isoformat(),
            "database_vendor": connection.vendor,
            "database_name": connection.settings_dict.get("NAME"),
            "migrations": {},
            "event_registrations": {},
            "payment_transactions": {},
            "crush_credits": {},
            "upcoming_events": [],
            "anomalies": [],
        }

        # -------------------------------------------------------------
        # 1. Migration & Schema Status
        # -------------------------------------------------------------
        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        unapplied_migrations = [
            f"{app}.{name}" for app, name in executor.migration_plan(targets)
        ]
        applied_crush_migrations = [
            name
            for app, name in executor.loader.applied_migrations
            if app == "crush_lu" and name.startswith("02")
        ]

        report["migrations"] = {
            "unapplied_count": len(unapplied_migrations),
            "unapplied_list": unapplied_migrations,
            "recent_crush_applied": sorted(applied_crush_migrations)[-10:],
        }

        # Check column existence in DB
        reg_columns = self._get_existing_columns(EventRegistration._meta.db_table)
        tx_columns = self._get_existing_columns(PaymentTransaction._meta.db_table)
        evt_columns = self._get_existing_columns(MeetupEvent._meta.db_table)

        has_cancelled_at = "cancelled_at" in reg_columns
        has_resale_beneficiary = "resale_beneficiary_id" in reg_columns
        has_paid_at = "paid_at" in tx_columns
        has_tx_event = "event_id" in tx_columns
        has_cancellation_cycle = "cancellation_cycle" in evt_columns

        # -------------------------------------------------------------
        # 2. Event Registrations & Cancellation Policy Status
        # -------------------------------------------------------------
        now = timezone.now()
        total_regs = EventRegistration.objects.count()
        status_counts = dict(
            EventRegistration.objects.values_list("status")
            .annotate(cnt=Count("id"))
            .order_by("-cnt")
        )

        cancelled_regs = EventRegistration.objects.filter(status="cancelled")
        cancelled_total = cancelled_regs.count()

        cancelled_with_timestamp = (
            cancelled_regs.filter(cancelled_at__isnull=False).count()
            if has_cancelled_at
            else "N/A (migration pending)"
        )
        cancelled_with_beneficiary = (
            EventRegistration.objects.filter(resale_beneficiary__isnull=False).count()
            if has_resale_beneficiary
            else "N/A (migration pending)"
        )

        report["event_registrations"] = {
            "total": total_regs,
            "by_status": status_counts,
            "cancelled_total": cancelled_total,
            "cancelled_with_timestamp": cancelled_with_timestamp,
            "resale_beneficiary_assigned": cancelled_with_beneficiary,
            "schema": {
                "has_cancelled_at_column": has_cancelled_at,
                "has_resale_beneficiary_column": has_resale_beneficiary,
            },
        }

        if has_cancelled_at and isinstance(cancelled_with_timestamp, int):
            if cancelled_total - cancelled_with_timestamp > 0:
                report["anomalies"].append(
                    f"{cancelled_total - cancelled_with_timestamp} cancelled registration(s) have NULL cancelled_at."
                )

        # -------------------------------------------------------------
        # 3. Payment Transactions Audit
        # -------------------------------------------------------------
        total_tx = PaymentTransaction.objects.count()
        tx_by_provider = dict(
            PaymentTransaction.objects.values_list("provider")
            .annotate(cnt=Count("id"))
            .order_by("-cnt")
        )
        tx_by_status = dict(
            PaymentTransaction.objects.values_list("status")
            .annotate(cnt=Count("id"))
            .order_by("-cnt")
        )

        tx_by_purpose = dict(
            PaymentTransaction.objects.values_list("purpose")
            .annotate(cnt=Count("id"))
            .order_by("-cnt")
        )

        successful_filter = Q(status="paid") | Q(status="PAID")
        if "manual" in tx_by_provider or "MANUAL" in tx_by_provider:
            successful_filter |= Q(provider="manual") | Q(provider="MANUAL")
        if "credit" in tx_by_provider or "CREDIT" in tx_by_provider:
            successful_filter |= Q(provider="credit") | Q(provider="CREDIT")

        successful_tx = PaymentTransaction.objects.filter(successful_filter)
        successful_total = successful_tx.count()

        event_tx = successful_tx.filter(purpose="event_registration")
        event_tx_total = event_tx.count()

        successful_with_paid_at = (
            successful_tx.filter(paid_at__isnull=False).count()
            if has_paid_at
            else "N/A (migration pending)"
        )
        event_tx_with_event = (
            event_tx.filter(event__isnull=False).count()
            if has_tx_event
            else "N/A (migration pending)"
        )

        report["payment_transactions"] = {
            "total": total_tx,
            "by_provider": tx_by_provider,
            "by_status": tx_by_status,
            "by_purpose": tx_by_purpose,
            "successful_total": successful_total,
            "successful_with_paid_at": successful_with_paid_at,
            "event_purpose_total": event_tx_total,
            "event_purpose_with_event": event_tx_with_event,
            "schema": {
                "has_paid_at_column": has_paid_at,
                "has_event_id_column": has_tx_event,
            },
        }

        if has_paid_at and isinstance(successful_with_paid_at, int):
            if successful_total - successful_with_paid_at > 0:
                report["anomalies"].append(
                    f"{successful_total - successful_with_paid_at} successful transaction(s) missing paid_at timestamp."
                )
        if has_tx_event and isinstance(event_tx_with_event, int):
            if event_tx_total - event_tx_with_event > 0:
                report["anomalies"].append(
                    f"{event_tx_total - event_tx_with_event} event_registration transaction(s) missing event link."
                )

        # -------------------------------------------------------------
        # 4. Crush Credit Ledger State
        # -------------------------------------------------------------
        credit_tables = {"crush_lu_crushcredit", "crush_lu_creditredemption"}
        existing_tables = set(connection.introspection.table_names())

        if credit_tables.issubset(existing_tables):
            from crush_lu.models.credits import CreditRedemption, CrushCredit

            total_credits_issued = CrushCredit.objects.count()
            total_credit_cents = (
                CrushCredit.objects.aggregate(total=Sum("amount_cents"))["total"] or 0
            )
            total_redemptions = CreditRedemption.objects.count()
            total_redeemed_cents = (
                CreditRedemption.objects.aggregate(total=Sum("amount_cents"))["total"]
                or 0
            )
            users_with_credit = CrushCredit.objects.values("user").distinct().count()

            credit_by_reason = dict(
                CrushCredit.objects.values_list("reason")
                .annotate(cnt=Count("id"))
                .order_by("-cnt")
            )
            credit_by_status = dict(
                CrushCredit.objects.values_list("status")
                .annotate(cnt=Count("id"))
                .order_by("-cnt")
            )

            report["crush_credits"] = {
                "table_status": "INSTALLED",
                "total_issued_count": total_credits_issued,
                "total_issued_cents": total_credit_cents,
                "total_issued_formatted": format_cents(total_credit_cents),
                "total_redemptions_count": total_redemptions,
                "total_redeemed_cents": total_redeemed_cents,
                "total_redeemed_formatted": format_cents(total_redeemed_cents),
                "net_active_liability_cents": total_credit_cents - total_redeemed_cents,
                "net_active_liability_formatted": format_cents(
                    total_credit_cents - total_redeemed_cents
                ),
                "unique_credit_holders": users_with_credit,
                "by_reason": credit_by_reason,
                "by_status": credit_by_status,
            }
        else:
            report["crush_credits"] = {
                "table_status": "PENDING_MIGRATION (0225_crush_credit_ledger)",
                "total_issued_count": 0,
                "total_issued_cents": 0,
                "total_issued_formatted": "€0.00",
                "total_redemptions_count": 0,
                "total_redeemed_cents": 0,
                "total_redeemed_formatted": "€0.00",
                "net_active_liability_cents": 0,
                "net_active_liability_formatted": "€0.00",
                "unique_credit_holders": 0,
                "by_reason": {},
                "by_status": {},
            }

        # -------------------------------------------------------------
        # 5. Upcoming Published Events & Attendee Policy Exposure
        # -------------------------------------------------------------
        evt_fields = ["id", "title", "date_time", "registration_fee"]
        if has_cancellation_cycle:
            evt_fields.append("cancellation_cycle")

        upcoming_events_data = list(
            MeetupEvent.objects.filter(
                date_time__gte=now,
                is_published=True,
                is_cancelled=False,
            )
            .values(*evt_fields)
            .order_by("date_time")
        )

        for evt in upcoming_events_data:
            evt_id = evt["id"]
            reg_qs = EventRegistration.objects.filter(event_id=evt_id)
            confirmed_count = reg_qs.filter(status="confirmed").count()
            waitlist_count = reg_qs.filter(status="waitlisted").count()
            registered_unpaid = reg_qs.filter(status="registered").count()

            evt_data = {
                "id": evt_id,
                "title": evt.get("title", f"Event #{evt_id}"),
                "date_time": (
                    evt["date_time"].isoformat() if evt.get("date_time") else "N/A"
                ),
                "fee": (
                    f"€{evt.get('registration_fee')}"
                    if evt.get("registration_fee")
                    else "Free"
                ),
                "confirmed": confirmed_count,
                "registered_unpaid": registered_unpaid,
                "waitlist": waitlist_count,
                "cancellation_cycle": evt.get("cancellation_cycle", "N/A"),
            }
            if detailed:
                attendees = []
                reg_fields = [
                    "id",
                    "user_id",
                    "user__email",
                    "status",
                    "payment_confirmed",
                ]
                if has_cancelled_at:
                    reg_fields.append("cancelled_at")
                for reg in reg_qs.values(*reg_fields).order_by("id"):
                    attendees.append(
                        {
                            "registration_id": reg["id"],
                            "user_id": reg["user_id"],
                            "email": reg.get("user__email") or "N/A",
                            "status": reg["status"],
                            "payment_confirmed": reg["payment_confirmed"],
                            "cancelled_at": (
                                reg["cancelled_at"].isoformat()
                                if reg.get("cancelled_at")
                                else None
                            ),
                        }
                    )
                evt_data["attendees"] = attendees
            report["upcoming_events"].append(evt_data)

        # -------------------------------------------------------------
        # Output Formatting
        # -------------------------------------------------------------
        if as_json:
            self.stdout.write(json.dumps(report, indent=2, default=str))
            return

        self._print_human_report(report, detailed)

    def _print_human_report(self, report, detailed):
        self.stdout.write(self.style.SUCCESS("=" * 72))
        self.stdout.write(
            self.style.SUCCESS("  CRUSH.LU PRE-SLOT-SWAP & MIGRATION READINESS AUDIT")
        )
        self.stdout.write(self.style.SUCCESS("=" * 72))
        self.stdout.write(f"Timestamp: {report['timestamp']}")
        self.stdout.write(
            f"Database:  {report['database_vendor']} ({report['database_name']})\n"
        )

        # Migrations
        m = report["migrations"]
        if m["unapplied_count"] == 0:
            self.stdout.write(
                self.style.SUCCESS("✅ Migrations: ALL APPLIED (0 unapplied)")
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"⚠️ Migrations: {m['unapplied_count']} unapplied migration(s)"
                )
            )
            for mig in m["unapplied_list"]:
                self.stdout.write(f"   - {mig}")
        if m["recent_crush_applied"]:
            self.stdout.write(
                "   Recent applied: " + ", ".join(m["recent_crush_applied"][-4:]) + "\n"
            )

        # Registrations & Policy
        r = report["event_registrations"]
        self.stdout.write(
            self.style.MIGRATE_HEADING("📋 Event Registrations & Cancellation State:")
        )
        self.stdout.write(f"   Total registrations: {r['total']}")
        self.stdout.write(f"   By Status: {r['by_status']}")
        self.stdout.write(
            f"   Cancelled: {r['cancelled_total']} (with timestamp: {r['cancelled_with_timestamp']})"
        )
        self.stdout.write(
            f"   Resale Beneficiaries assigned: {r['resale_beneficiary_assigned']}\n"
        )

        # Transactions
        t = report["payment_transactions"]
        self.stdout.write(
            self.style.MIGRATE_HEADING("💳 Payment Transactions & Revenue Attribution:")
        )
        self.stdout.write(f"   Total transactions: {t['total']}")
        self.stdout.write(f"   By Provider: {t['by_provider']}")
        self.stdout.write(f"   By Status: {t['by_status']}")
        self.stdout.write(f"   By Purpose: {t['by_purpose']}")
        self.stdout.write(
            f"   Successful: {t['successful_total']} "
            f"(with paid_at: {t['successful_with_paid_at']}) | "
            f"Event Registrations: {t['event_purpose_total']} (with event FK: {t['event_purpose_with_event']})\n"
        )

        # Crush Credit Ledger
        c = report["crush_credits"]
        self.stdout.write(self.style.MIGRATE_HEADING("🪙 Crush Credit Ledger:"))
        self.stdout.write(f"   Table Status: {c['table_status']}")
        self.stdout.write(
            f"   Total Issued: {c['total_issued_count']} ({c['total_issued_formatted']})"
        )
        self.stdout.write(
            f"   Total Redeemed: {c['total_redemptions_count']} ({c['total_redeemed_formatted']})"
        )
        self.stdout.write(f"   Active Liability: {c['net_active_liability_formatted']}")
        self.stdout.write(f"   Unique Credit Holders: {c['unique_credit_holders']}")
        if c["by_reason"]:
            self.stdout.write(f"   By Reason: {c['by_reason']}")
        if c["by_status"]:
            self.stdout.write(f"   By Status: {c['by_status']}")
        self.stdout.write("")

        # Upcoming Events
        evts = report["upcoming_events"]
        self.stdout.write(
            self.style.MIGRATE_HEADING(f"📅 Upcoming Published Events ({len(evts)}):")
        )
        if not evts:
            self.stdout.write("   (No upcoming published events found)")
        for evt in evts:
            self.stdout.write(
                f"   • Event #{evt['id']} \"{evt['title']}\" ({evt['date_time'][:10]} | {evt['fee']}) — "
                f"Confirmed: {evt['confirmed']}, Unpaid: {evt['registered_unpaid']}, Waitlist: {evt['waitlist']}"
            )
            if detailed and "attendees" in evt:
                for att in evt["attendees"]:
                    paid_str = "PAID" if att["payment_confirmed"] else "UNPAID"
                    self.stdout.write(
                        f"       - Reg #{att['registration_id']} | User #{att['user_id']} ({att['email']}) "
                        f"| Status: {att['status']} [{paid_str}]"
                    )
        self.stdout.write("")

        # Anomalies
        if report["anomalies"]:
            self.stdout.write(self.style.ERROR("🚨 ANOMALIES / WARNINGS DETECTED:"))
            for a in report["anomalies"]:
                self.stdout.write(self.style.ERROR(f"   ❌ {a}"))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "✨ Zero anomalies detected. Database schema & ledgers are consistent!"
                )
            )

        self.stdout.write(self.style.SUCCESS("=" * 72))
