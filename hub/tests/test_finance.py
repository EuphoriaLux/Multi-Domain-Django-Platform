from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from hub.models import Location, PaymentIn, PaymentMethod, PaymentOut, Payroll, Refund

User = get_user_model()

FINANCE_ENDPOINTS = ["payments-in", "payments-out", "payroll", "refunds"]


class FinanceAPITestCase(TestCase):
    def setUp(self):
        # No password: every test authenticates via ``force_authenticate``, and
        # hashing one per test costs ~2s each under ``manage.py test`` (the MD5
        # hasher in conftest.py only applies to pytest runs).
        self.staff = User.objects.create_user(
            username="finance_coach",
            email="finance-coach@crush.lu",
            is_staff=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.staff)


class PaymentInAPITests(FinanceAPITestCase):
    def test_staff_can_list_payments_in_with_frontend_contract(self):
        payment = PaymentIn.objects.create(
            date=date(2026, 5, 12),
            amount=Decimal("1250.50"),
            source="Speed dating — May",
            client_name="Camille Hoffmann",
            status="Paid",
            reference="INV-2026-042",
            payment_method=PaymentMethod.CARD,
            receipt_url="https://receipts.example/inv-2026-042.pdf",
        )

        response = self.client.get("/hub/payments-in")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "items": [
                    {
                        "id": str(payment.pk),
                        "date": "2026-05-12",
                        "amount": 1250.50,
                        "source": "Speed dating — May",
                        "clientName": "Camille Hoffmann",
                        "status": "Paid",
                        "reference": "INV-2026-042",
                        "paymentMethod": "Card",
                        "receiptUrl": "https://receipts.example/inv-2026-042.pdf",
                    }
                ]
            },
        )

    def test_amount_is_a_json_number_not_a_string(self):
        """The SPA sums these values — a string would silently concatenate."""
        PaymentIn.objects.create(
            date=date(2026, 5, 12),
            amount=Decimal("1250.50"),
            source="Sponsorship",
        )

        amount = self.client.get("/hub/payments-in").json()["items"][0]["amount"]

        self.assertNotIsInstance(amount, str)
        self.assertEqual(amount, 1250.50)

    def test_blank_optional_text_stays_an_empty_string(self):
        PaymentIn.objects.create(
            date=date(2026, 5, 12),
            amount=Decimal("40.00"),
            source="Door sales",
        )

        item = self.client.get("/hub/payments-in/").json()["items"][0]

        self.assertEqual(item["clientName"], "")
        self.assertEqual(item["reference"], "")
        self.assertEqual(item["receiptUrl"], "")
        self.assertEqual(item["status"], "Pending")
        self.assertEqual(item["paymentMethod"], "Transfer")

    def test_rows_are_listed_newest_first(self):
        for day in (3, 21, 11):
            PaymentIn.objects.create(
                date=date(2026, 5, day),
                amount=Decimal("10.00"),
                source=f"Day {day}",
            )

        items = self.client.get("/hub/payments-in").json()["items"]

        self.assertEqual(
            [item["date"] for item in items],
            ["2026-05-21", "2026-05-11", "2026-05-03"],
        )

    def test_negative_amounts_are_rejected_by_the_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            PaymentIn.objects.create(
                date=date(2026, 5, 12),
                amount=Decimal("-1.00"),
                source="Impossible",
            )


class PaymentOutAPITests(FinanceAPITestCase):
    def test_staff_can_list_payments_out_with_frontend_contract(self):
        location = Location.objects.create(
            name="Maison du Vin",
            address="12 Rue de la Boucherie, 1247 Luxembourg",
            city="Luxembourg City",
            max_capacity=60,
        )
        payment = PaymentOut.objects.create(
            date=date(2026, 5, 2),
            amount=Decimal("450.00"),
            location=location,
            payee="Maison du Vin",
            description="Venue deposit for the May tasting",
            status="Scheduled",
            payment_method=PaymentMethod.TRANSFER,
            category=PaymentOut.Category.VENUE,
            deposit_status=PaymentOut.DepositStatus.DEPOSIT,
            receipt_url="https://receipts.example/venue-may.pdf",
        )

        response = self.client.get("/hub/payments-out")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "items": [
                    {
                        "id": str(payment.pk),
                        "date": "2026-05-02",
                        "amount": 450.00,
                        "locationId": str(location.pk),
                        "payee": "Maison du Vin",
                        "description": "Venue deposit for the May tasting",
                        "status": "Scheduled",
                        "paymentMethod": "Transfer",
                        "category": "Lieu",
                        "depositStatus": "deposit",
                        "receiptUrl": "https://receipts.example/venue-may.pdf",
                    }
                ]
            },
        )

    def test_null_optional_fields_are_omitted_for_frontend(self):
        PaymentOut.objects.create(
            date=date(2026, 5, 2),
            amount=Decimal("89.90"),
            payee="Buffer",
            category=PaymentOut.Category.TECH,
        )

        item = self.client.get("/hub/payments-out/").json()["items"][0]

        self.assertNotIn("locationId", item)
        self.assertNotIn("depositStatus", item)
        self.assertEqual(item["description"], "")
        self.assertEqual(item["receiptUrl"], "")

    def test_cost_history_survives_deleting_the_venue(self):
        location = Location.objects.create(
            name="Closed venue",
            address="2 Test Street",
            city="Esch-sur-Alzette",
            max_capacity=50,
        )
        PaymentOut.objects.create(
            date=date(2026, 5, 2),
            amount=Decimal("450.00"),
            location=location,
            payee="Closed venue",
            category=PaymentOut.Category.VENUE,
        )

        location.delete()

        items = self.client.get("/hub/payments-out").json()["items"]
        self.assertEqual(len(items), 1)
        self.assertNotIn("locationId", items[0])
        self.assertEqual(items[0]["amount"], 450.00)


class PayrollAPITests(FinanceAPITestCase):
    def test_staff_can_list_payroll_with_frontend_contract(self):
        entry = Payroll.objects.create(
            date=date(2026, 5, 28),
            amount=Decimal("2400.00"),
            gross_salary=Decimal("3000.00"),
            employer_charges=Decimal("380.25"),
            employee_name="Wesley",
            category=Payroll.Category.SALARY,
            description="May salary",
            status="Paid",
            payment_method=PaymentMethod.TRANSFER,
            receipt_url="https://receipts.example/payroll-may.pdf",
        )

        response = self.client.get("/hub/payroll")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "items": [
                    {
                        "id": str(entry.pk),
                        "date": "2026-05-28",
                        "amount": 2400.00,
                        "grossSalary": 3000.00,
                        "employerCharges": 380.25,
                        "employeeName": "Wesley",
                        "category": "Salary",
                        "description": "May salary",
                        "status": "Paid",
                        "paymentMethod": "Transfer",
                        "receiptUrl": "https://receipts.example/payroll-may.pdf",
                    }
                ]
            },
        )

    def test_all_money_fields_are_json_numbers(self):
        Payroll.objects.create(
            date=date(2026, 5, 28),
            amount=Decimal("2400.00"),
            gross_salary=Decimal("3000.00"),
            employer_charges=Decimal("380.25"),
            employee_name="Wesley",
        )

        item = self.client.get("/hub/payroll").json()["items"][0]

        for field in ("amount", "grossSalary", "employerCharges"):
            self.assertNotIsInstance(item[field], str, msg=field)


class RefundAPITests(FinanceAPITestCase):
    def test_staff_can_list_refunds_with_frontend_contract(self):
        refund = Refund.objects.create(
            date=date(2026, 5, 9),
            amount=Decimal("35.00"),
            participant_name="Alex Muller",
            event_name="Speed dating — May",
            reason="Cancelled more than 48h before the event",
            status="Paid",
        )

        response = self.client.get("/hub/refunds")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "items": [
                    {
                        "id": str(refund.pk),
                        "date": "2026-05-09",
                        "amount": 35.00,
                        "participantName": "Alex Muller",
                        "eventName": "Speed dating — May",
                        "reason": "Cancelled more than 48h before the event",
                        "status": "Paid",
                    }
                ]
            },
        )

    def test_refunds_expose_no_payment_method_or_receipt(self):
        """The SPA's RefundItem has neither field — don't widen the contract."""
        Refund.objects.create(
            date=date(2026, 5, 9),
            amount=Decimal("35.00"),
            participant_name="Alex Muller",
        )

        item = self.client.get("/hub/refunds").json()["items"][0]

        self.assertNotIn("paymentMethod", item)
        self.assertNotIn("receiptUrl", item)


class FinanceAccessTests(FinanceAPITestCase):
    def test_finance_endpoints_are_staff_only(self):
        member = User.objects.create_user(username="finance_member")
        self.client.force_authenticate(user=member)

        for endpoint in FINANCE_ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                response = self.client.get(f"/hub/{endpoint}")
                self.assertEqual(response.status_code, 403)

    def test_finance_endpoints_reject_anonymous_users(self):
        self.client.force_authenticate(user=None)

        for endpoint in FINANCE_ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                response = self.client.get(f"/hub/{endpoint}")
                self.assertEqual(response.status_code, 401)

    def test_finance_endpoints_are_read_only(self):
        for endpoint in FINANCE_ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                response = self.client.post(f"/hub/{endpoint}", {}, format="json")
                self.assertEqual(response.status_code, 405)
