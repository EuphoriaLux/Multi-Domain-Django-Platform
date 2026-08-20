"""Tests for the read-only hub event-cancellation reporting endpoints.

There is no ``EventCancellation`` model — every assertion here builds
``crush_lu`` rows directly via the ORM (the ``hub/tests/test_social.py``
pattern) and reads the state back through the two hub endpoints, exactly as
they reconstruct it live. See ``hub/views_events.py`` for the three sources.
"""

import itertools
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from crush_lu.admin.credits import CashRefundQueueFilter
from crush_lu.models import EventRegistration, MeetupEvent
from crush_lu.models.credits import CreditRedemption, CrushCredit
from crush_lu.models.payments import PaymentTransaction

User = get_user_model()


class EventCancellationFixture(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="coach_test",
            email="coach@crush.lu",
            password="password123",
            is_staff=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.staff)
        self._member_counter = itertools.count()

    # -- builders -----------------------------------------------------

    def _event(self, **overrides):
        now = timezone.now()
        values = dict(
            title="Speed Dating Luxembourg",
            description="A cancelled evening",
            event_type="speed_dating",
            location="Luxembourg City",
            address="10 Grand Rue",
            date_time=now + timedelta(days=3),
            registration_deadline=now + timedelta(days=2),
            registration_fee=Decimal("15.50"),
            is_published=True,
        )
        values.update(overrides)
        return MeetupEvent.objects.create(**values)

    def _member(self, email):
        return User.objects.create_user(
            username=email, email=email, password="password123"
        )

    def _registration(self, event, user, *, status="cancelled", payment_confirmed=True):
        return EventRegistration.objects.create(
            event=event,
            user=user,
            status=status,
            payment_confirmed=payment_confirmed,
        )

    def _payment(self, registration, *, amount=Decimal("15.50")):
        return PaymentTransaction.objects.create(
            transaction_reference=f"CRUSH-EVT-{registration.pk}-test",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id=f"CHK{registration.pk}",
            amount=amount,
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=registration.user,
            event_registration=registration,
        )

    def _credit(
        self,
        user,
        *,
        registration=None,
        payment=None,
        amount_cents=2000,
        cash_refund_eligible=False,
        status=CrushCredit.Status.ACTIVE,
        issued_at=None,
        expires_at=None,
    ):
        return CrushCredit.objects.create(
            user=user,
            amount_cents=amount_cents,
            reason=CrushCredit.Reason.EVENT_CANCELLED,
            source_registration=registration,
            source_payment=payment,
            cash_refund_eligible=cash_refund_eligible,
            status=status,
            issued_at=issued_at or timezone.now(),
            expires_at=expires_at,
        )

    def _cancelled_event_with_credit(self, *, cash_refund_eligible=True):
        """One cancelled event, one self-cancelled paid seat, one credit."""
        event = self._event(
            is_cancelled=True, organiser_cancellation_started_at=timezone.now()
        )
        member = self._member(f"member{next(self._member_counter)}@crush.lu")
        registration = self._registration(event, member)
        payment = self._payment(registration)
        credit = self._credit(
            member,
            registration=registration,
            payment=payment,
            amount_cents=2000,
            cash_refund_eligible=cash_refund_eligible,
        )
        return event, registration, credit


class PermissionTests(EventCancellationFixture):
    def test_non_staff_cannot_list_cancellations(self):
        non_staff = User.objects.create_user(username="member", password="password123")
        self.client.force_authenticate(user=non_staff)
        response = self.client.get("/hub/events/cancelled")
        self.assertEqual(response.status_code, 403)

    def test_non_staff_cannot_read_cancellation_detail(self):
        event = self._event(is_cancelled=True)
        non_staff = User.objects.create_user(username="member2", password="password123")
        self.client.force_authenticate(user=non_staff)
        response = self.client.get(f"/hub/events/{event.pk}/cancellation")
        self.assertEqual(response.status_code, 403)

    def test_anonymous_cannot_list_cancellations(self):
        self.client.force_authenticate(user=None)
        response = self.client.get("/hub/events/cancelled")
        self.assertIn(response.status_code, (401, 403))


class ListEndpointTests(EventCancellationFixture):
    def test_only_cancelled_events_are_listed(self):
        cancelled, _reg, _credit = self._cancelled_event_with_credit()
        self._event(is_cancelled=False, title="Still live")

        response = self.client.get("/hub/events/cancelled")

        self.assertEqual(response.status_code, 200)
        ids = [item["id"] for item in response.data["items"]]
        self.assertEqual(ids, [str(cancelled.pk)])

    def test_envelope_is_items_list(self):
        self._cancelled_event_with_credit()
        response = self.client.get("/hub/events/cancelled")
        self.assertEqual(set(response.data.keys()), {"items"})
        self.assertIsInstance(response.data["items"], list)

    def test_ordered_by_cancellation_cycle_start_descending(self):
        now = timezone.now()
        older = self._event(
            title="Older cancellation",
            is_cancelled=True,
            organiser_cancellation_started_at=now - timedelta(days=5),
        )
        newer = self._event(
            title="Newer cancellation",
            is_cancelled=True,
            organiser_cancellation_started_at=now - timedelta(hours=1),
        )
        # A legacy row cancelled before this field existed: NULL must sort
        # last on SQLite (tests) exactly as it does on Postgres (prod).
        # ``MeetupEvent``'s own ``post_save`` signal backfills this field the
        # instant ``is_cancelled=True`` hits ``.save()`` (crush_lu/signals.py),
        # so a genuinely-NULL row can only be produced the way it would exist
        # in the database: a bulk ``.update()`` that bypasses signals, exactly
        # like ``cancel_events`` itself uses.
        legacy = self._event(title="Legacy cancellation", is_cancelled=True)
        MeetupEvent.objects.filter(pk=legacy.pk).update(
            organiser_cancellation_started_at=None
        )

        response = self.client.get("/hub/events/cancelled")

        ids = [item["id"] for item in response.data["items"]]
        self.assertEqual(ids, [str(newer.pk), str(older.pk), str(legacy.pk)])

    def test_affected_registrations_counts_self_cancelled_seats_only(self):
        event, _reg, _credit = self._cancelled_event_with_credit()
        # A still-confirmed seat at the same cancelled event must NOT count:
        # cancel_events never touches individual registration status.
        confirmed_member = self._member("still-confirmed@crush.lu")
        self._registration(event, confirmed_member, status="confirmed")
        second_cancelled = self._member("second-cancelled@crush.lu")
        self._registration(event, second_cancelled, status="cancelled")

        response = self.client.get("/hub/events/cancelled")

        row = response.data["items"][0]
        self.assertEqual(row["affectedRegistrations"], 2)

    def test_issued_and_open_credit_totals(self):
        event, registration, credit = self._cancelled_event_with_credit(
            cash_refund_eligible=True
        )
        # A second registration at the same event whose credit was already
        # spent: still counted in "issued", excluded from "open".
        other_member = self._member("spent@crush.lu")
        other_registration = self._registration(event, other_member)
        other_payment = self._payment(other_registration)
        spent_credit = self._credit(
            other_member,
            registration=other_registration,
            payment=other_payment,
            amount_cents=2000,
            cash_refund_eligible=True,
        )
        CreditRedemption.objects.create(
            credit=spent_credit, event_registration=other_registration, amount_cents=500
        )

        response = self.client.get("/hub/events/cancelled")

        row = response.data["items"][0]
        self.assertEqual(row["issuedCreditsCount"], 2)
        self.assertEqual(row["issuedCreditsTotalCents"], 4000)
        # Only the untouched credit remains open.
        self.assertEqual(row["openCashRefundTotalCents"], 2000)

    def test_credit_issued_before_current_cycle_is_excluded(self):
        event = self._event(
            is_cancelled=True, organiser_cancellation_started_at=timezone.now()
        )
        member = self._member("stale-cycle@crush.lu")
        registration = self._registration(event, member)
        payment = self._payment(registration)
        # Issued before the CURRENT cycle started (an earlier
        # cancel/restore/re-cancel round) — must not count now.
        self._credit(
            member,
            registration=registration,
            payment=payment,
            cash_refund_eligible=True,
            issued_at=timezone.now() - timedelta(days=10),
        )

        response = self.client.get("/hub/events/cancelled")

        row = response.data["items"][0]
        self.assertEqual(row["issuedCreditsCount"], 0)
        self.assertEqual(row["openCashRefundTotalCents"], 0)

    def test_no_n_plus_one_across_event_count(self):
        for index in range(2):
            self._cancelled_event_with_credit()
        with CaptureQueriesContext(connection) as small:
            self.client.get("/hub/events/cancelled")

        for index in range(3):
            self._cancelled_event_with_credit()
        with CaptureQueriesContext(connection) as large:
            self.client.get("/hub/events/cancelled")

        self.assertEqual(len(small.captured_queries), len(large.captured_queries))


class DetailEndpointTests(EventCancellationFixture):
    def test_missing_event_is_404(self):
        response = self.client.get("/hub/events/999999/cancellation")
        self.assertEqual(response.status_code, 404)

    def test_restored_event_is_404_not_a_leak_of_every_past_cycle(self):
        """A restored (no-longer-cancelled) event must 404, not silently

        drop cycle-scoping and surface every EVENT_CANCELLED credit the
        event's self-cancelled seats ever accumulated across past cycles.
        """
        event, registration, credit = self._cancelled_event_with_credit()
        # Sanity: the endpoint serves the event while it's still cancelled.
        self.assertEqual(
            self.client.get(f"/hub/events/{event.pk}/cancellation").status_code, 200
        )

        # Restore: this is exactly what the admin's restore path does —
        # ``is_cancelled=False`` and ``organiser_cancellation_started_at``
        # reset to ``None`` (crush_lu/admin/events.py).
        event.is_cancelled = False
        event.organiser_cancellation_started_at = None
        event.save(update_fields=["is_cancelled", "organiser_cancellation_started_at"])

        response = self.client.get(f"/hub/events/{event.pk}/cancellation")
        self.assertEqual(response.status_code, 404)

    def test_envelope_has_event_and_items(self):
        event, registration, credit = self._cancelled_event_with_credit()
        response = self.client.get(f"/hub/events/{event.pk}/cancellation")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data.keys()), {"event", "items"})
        self.assertEqual(response.data["event"]["id"], str(event.pk))
        self.assertIsInstance(response.data["items"], list)

    def test_only_status_cancelled_registrations_are_listed(self):
        event = self._event(is_cancelled=True)
        cancelled_member = self._member("cancelled@crush.lu")
        cancelled_reg = self._registration(event, cancelled_member, status="cancelled")
        confirmed_member = self._member("confirmed@crush.lu")
        self._registration(event, confirmed_member, status="confirmed")

        response = self.client.get(f"/hub/events/{event.pk}/cancellation")

        ids = [item["id"] for item in response.data["items"]]
        self.assertEqual(ids, [str(cancelled_reg.pk)])

    def test_registration_carries_its_linked_credit(self):
        event, registration, credit = self._cancelled_event_with_credit(
            cash_refund_eligible=True
        )
        response = self.client.get(f"/hub/events/{event.pk}/cancellation")

        row = response.data["items"][0]
        self.assertEqual(row["userEmail"], registration.user.email)
        self.assertEqual(row["status"], "cancelled")
        self.assertIsNotNone(row["credit"])
        self.assertEqual(row["credit"]["id"], str(credit.pk))
        self.assertEqual(row["credit"]["amountCents"], credit.amount_cents)
        self.assertEqual(row["credit"]["status"], CrushCredit.Status.ACTIVE)
        self.assertTrue(row["credit"]["cashRefundEligible"])
        self.assertTrue(row["openCashRefund"])

    def test_registration_without_credit_reports_null(self):
        event = self._event(is_cancelled=True)
        member = self._member("uncredited@crush.lu")
        self._registration(event, member, status="cancelled", payment_confirmed=False)

        response = self.client.get(f"/hub/events/{event.pk}/cancellation")

        row = response.data["items"][0]
        self.assertIsNone(row["credit"])
        self.assertFalse(row["openCashRefund"])

    def test_credit_from_an_earlier_cycle_is_not_attributed(self):
        event = self._event(
            is_cancelled=True, organiser_cancellation_started_at=timezone.now()
        )
        member = self._member("stale@crush.lu")
        registration = self._registration(event, member)
        payment = self._payment(registration)
        self._credit(
            member,
            registration=registration,
            payment=payment,
            cash_refund_eligible=True,
            issued_at=timezone.now() - timedelta(days=30),
        )

        response = self.client.get(f"/hub/events/{event.pk}/cancellation")

        row = response.data["items"][0]
        self.assertIsNone(row["credit"])
        self.assertFalse(row["openCashRefund"])

    def test_representative_credit_pick_is_deterministic_on_issued_at_ties(self):
        """Two EVENT_CANCELLED credits on one registration with the exact

        same ``issued_at`` (a payment-return credit plus a same-instant
        bonus credit, both minted in the same atomic block — see
        ``crush_lu/services/credits.py::_issue_cancelled_event_remedy``)
        must resolve to the same representative credit every time, not
        drift between requests depending on unspecified DB row order.
        """
        event = self._event(
            is_cancelled=True, organiser_cancellation_started_at=timezone.now()
        )
        member = self._member("tie@crush.lu")
        registration = self._registration(event, member)
        payment = self._payment(registration)
        same_instant = timezone.now()
        # Neither credit is cash-refund-open, so the "open one wins" branch
        # is not in play — this exercises the "most recently issued" tie
        # purely on the ``-pk`` fallback.
        first = self._credit(
            member,
            registration=registration,
            payment=payment,
            cash_refund_eligible=False,
            issued_at=same_instant,
        )
        second = self._credit(
            member,
            registration=registration,
            payment=payment,
            cash_refund_eligible=False,
            issued_at=same_instant,
        )
        winner_pk = str(max(first.pk, second.pk))

        for _ in range(3):
            response = self.client.get(f"/hub/events/{event.pk}/cancellation")
            row = response.data["items"][0]
            self.assertEqual(row["credit"]["id"], winner_pk)

    def test_no_n_plus_one_across_registration_count(self):
        small_event = self._event(
            title="Small",
            is_cancelled=True,
            organiser_cancellation_started_at=timezone.now(),
        )
        for index in range(2):
            member = self._member(f"small-{index}@crush.lu")
            registration = self._registration(small_event, member)
            payment = self._payment(registration)
            self._credit(
                member,
                registration=registration,
                payment=payment,
                cash_refund_eligible=True,
            )
        with CaptureQueriesContext(connection) as small:
            self.client.get(f"/hub/events/{small_event.pk}/cancellation")

        large_event = self._event(
            title="Large",
            is_cancelled=True,
            organiser_cancellation_started_at=timezone.now(),
        )
        for index in range(6):
            member = self._member(f"large-{index}@crush.lu")
            registration = self._registration(large_event, member)
            payment = self._payment(registration)
            self._credit(
                member,
                registration=registration,
                payment=payment,
                cash_refund_eligible=True,
            )
        with CaptureQueriesContext(connection) as large:
            self.client.get(f"/hub/events/{large_event.pk}/cancellation")

        self.assertEqual(len(small.captured_queries), len(large.captured_queries))


class CashRefundOpenFlagEquivalenceTests(EventCancellationFixture):
    """Both endpoints' open-cash-refund flag must be provably the same
    predicate ``CashRefundQueueFilter._open`` uses for the staff queue —
    never a hand-copied condition that can silently drift from it.

    Every expectation below is recomputed live from ``_open()`` itself, not
    hardcoded, so a future change to ``_open()``'s conditions makes this test
    fail rather than pass on stale assumptions.
    """

    def _open_ids(self):
        return set(
            CashRefundQueueFilter._open(
                None,
                CrushCredit.objects.filter(reason=CrushCredit.Reason.EVENT_CANCELLED),
            ).values_list("id", flat=True)
        )

    def test_boundary_credits_agree_with_the_staff_queue_predicate(self):
        event = self._event(
            is_cancelled=True, organiser_cancellation_started_at=timezone.now()
        )

        # 1. Textbook open: eligible, active, unspent, unexpired.
        member_open = self._member("open@crush.lu")
        reg_open = self._registration(event, member_open)
        pay_open = self._payment(reg_open)
        credit_open = self._credit(
            member_open,
            registration=reg_open,
            payment=pay_open,
            cash_refund_eligible=True,
        )

        # 2. Not eligible at all (e.g. a bonus/tranche credit).
        member_ineligible = self._member("ineligible@crush.lu")
        reg_ineligible = self._registration(event, member_ineligible)
        pay_ineligible = self._payment(reg_ineligible)
        credit_ineligible = self._credit(
            member_ineligible,
            registration=reg_ineligible,
            payment=pay_ineligible,
            cash_refund_eligible=False,
        )

        # 3. Voided.
        member_void = self._member("void@crush.lu")
        reg_void = self._registration(event, member_void)
        pay_void = self._payment(reg_void)
        credit_void = self._credit(
            member_void,
            registration=reg_void,
            payment=pay_void,
            cash_refund_eligible=True,
            status=CrushCredit.Status.VOID,
        )

        # 4. Expired.
        member_expired = self._member("expired@crush.lu")
        reg_expired = self._registration(event, member_expired)
        pay_expired = self._payment(reg_expired)
        credit_expired = self._credit(
            member_expired,
            registration=reg_expired,
            payment=pay_expired,
            cash_refund_eligible=True,
            issued_at=timezone.now() - timedelta(days=400),
        )
        credit_expired.expires_at = timezone.now() - timedelta(days=1)
        credit_expired.save(update_fields=["expires_at"])

        # 5. Partly spent — the load-bearing case per
        # ``CashRefundQueueFilter._open``'s own docstring.
        member_spent = self._member("spent-boundary@crush.lu")
        reg_spent = self._registration(event, member_spent)
        pay_spent = self._payment(reg_spent)
        credit_spent = self._credit(
            member_spent,
            registration=reg_spent,
            payment=pay_spent,
            cash_refund_eligible=True,
        )
        CreditRedemption.objects.create(
            credit=credit_spent, event_registration=reg_spent, amount_cents=100
        )

        expected_open_ids = self._open_ids()
        self.assertEqual(expected_open_ids, {credit_open.pk})

        list_response = self.client.get("/hub/events/cancelled")
        list_row = list_response.data["items"][0]
        self.assertEqual(list_row["openCashRefundTotalCents"], credit_open.amount_cents)

        detail_response = self.client.get(f"/hub/events/{event.pk}/cancellation")
        rows_by_registration = {
            item["id"]: item for item in detail_response.data["items"]
        }

        expectations = {
            reg_open.pk: (credit_open.pk in expected_open_ids, True),
            reg_ineligible.pk: (credit_ineligible.pk in expected_open_ids, False),
            reg_void.pk: (credit_void.pk in expected_open_ids, False),
            reg_expired.pk: (credit_expired.pk in expected_open_ids, False),
            reg_spent.pk: (credit_spent.pk in expected_open_ids, False),
        }
        for pk, (expected_flag, _label) in expectations.items():
            row = rows_by_registration[str(pk)]
            self.assertEqual(
                row["openCashRefund"],
                expected_flag,
                msg=f"registration {pk} diverged from CashRefundQueueFilter._open()",
            )
