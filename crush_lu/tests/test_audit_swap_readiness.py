import io
import json
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from crush_lu.models.credits import CrushCredit
from crush_lu.models.events import EventRegistration, MeetupEvent
from crush_lu.services.credits import issue_credit

User = get_user_model()


class TestAuditSwapReadinessCommand(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="audit_test@crush.lu",
            email="audit_test@crush.lu",
            password="testpassword",
        )
        now = timezone.now()
        self.event = MeetupEvent.objects.create(
            title="Audit Test Event",
            date_time=now + timezone.timedelta(days=7),
            registration_deadline=now + timezone.timedelta(days=6),
            registration_fee=Decimal("25.00"),
            is_published=True,
            is_cancelled=False,
        )
        self.reg = EventRegistration.objects.create(
            user=self.user,
            event=self.event,
            status="confirmed",
            payment_confirmed=True,
        )

    def test_audit_swap_readiness_human_output(self):
        out = io.StringIO()
        call_command("audit_swap_readiness", stdout=out)
        output = out.getvalue()
        self.assertIn("CRUSH.LU PRE-SLOT-SWAP & MIGRATION READINESS AUDIT", output)
        self.assertIn("Event Registrations & Cancellation State:", output)
        self.assertIn("Payment Transactions & Revenue Attribution:", output)
        self.assertIn("Crush Credit Ledger:", output)

    def test_audit_swap_readiness_detailed_output(self):
        out = io.StringIO()
        call_command("audit_swap_readiness", "--detailed", stdout=out)
        output = out.getvalue()
        self.assertIn("Audit Test Event", output)
        self.assertIn("audit_test@crush.lu", output)

    def test_audit_swap_readiness_json_output(self):
        out = io.StringIO()
        call_command("audit_swap_readiness", "--json", stdout=out)
        output = out.getvalue()
        data = json.loads(output)
        self.assertIn("migrations", data)
        self.assertIn("event_registrations", data)
        self.assertIn("payment_transactions", data)
        self.assertIn("crush_credits", data)
        self.assertIn("upcoming_events", data)
        self.assertEqual(len(data["upcoming_events"]), 1)
        self.assertEqual(data["upcoming_events"][0]["title"], "Audit Test Event")

    def test_audit_swap_readiness_with_credits(self):
        issue_credit(
            self.user,
            2500,
            CrushCredit.Reason.GOODWILL,
        )
        out = io.StringIO()
        call_command("audit_swap_readiness", "--json", stdout=out)
        data = json.loads(out.getvalue())
        self.assertEqual(data["crush_credits"]["total_issued_count"], 1)
        self.assertEqual(data["crush_credits"]["total_issued_cents"], 2500)
        self.assertEqual(data["crush_credits"]["unique_credit_holders"], 1)
