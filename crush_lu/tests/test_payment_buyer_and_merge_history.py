"""Who a payment is for, and whose history a merged account's rows are.

Two gaps Codex found in the #1194 export (PR #1267, threads 4287510165 and
4287510173):

* ``PaymentTransaction.buyer`` -- stamped once at checkout creation, so a
  staff-opened checkout stays the member's after its registration is deleted
  (SET_NULL), instead of falling back to the staff account in ``user``.
* ``AccountMerge`` -- the duplicate keeps its memberships, payments and
  Connect/spark/gift/safety/lobby rows, so the keeper's export carries one
  block per merged account, and the keeper still opens and is receipted for
  what the duplicate bought.
"""

import importlib
import json
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from django.apps import apps as global_apps
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.utils import timezone

from crush_lu.email_helpers import _receipt_recipient
from crush_lu.models import (
    AccountMerge,
    CrushProfile,
    EventRegistration,
    MeetupEvent,
    PaymentTransaction,
    PremiumMembership,
)
from crush_lu.services.account_merge import merge_accounts
from crush_lu.views_account import export_user_data
from crush_lu.views_payments import _payment_owner_ids

User = get_user_model()

OTHER_EMAIL = "zelda-counterpart@example.com"
OTHER_NAME = "Zelda"


def _user(email, **extra):
    return User.objects.create_user(email, email, "pw12345678", **extra)


def _profile(user, gender="F"):
    return CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1995, 1, 1),
        gender=gender,
        location="canton-luxembourg",
    )


def _premium(user, **fields):
    from crush_lu.models import CrushCoach

    coach = CrushCoach.objects.create(
        user=_user(f"coach-{user.pk}@example.com", first_name="Cora"), bio="b"
    )
    return PremiumMembership.objects.create(user=user, coach=coach, **fields)


def _event(title="Buyer night"):
    return MeetupEvent.objects.create(
        title=title,
        description="d",
        event_type="mixer",
        date_time=timezone.now() + timedelta(days=7),
        location="Lux",
        address="1 Street",
        max_participants=20,
        registration_deadline=timezone.now() + timedelta(days=5),
        registration_fee=Decimal("25.00"),
        is_published=True,
    )


def _payment(reference, **fields):
    defaults = {
        "transaction_reference": reference,
        "provider": PaymentTransaction.Provider.SUMUP,
        "amount": Decimal("25.00"),
        "currency": "EUR",
        "status": PaymentTransaction.Status.PENDING,
        "purpose": PaymentTransaction.Purpose.EVENT_REGISTRATION,
    }
    defaults.update(fields)
    return PaymentTransaction.objects.create(**defaults)


def _export(user):
    request = RequestFactory().get("/")
    request.user = User.objects.get(pk=user.pk)
    return json.loads(export_user_data(request).content)


class PaymentBuyerTests(TestCase):
    def setUp(self):
        cache.clear()
        self.member = _user("member@example.com")
        self.staff = _user("staff@example.com", is_staff=True)

    def test_staff_opened_checkout_stamps_the_member_not_the_opener(self):
        registration = EventRegistration.objects.create(
            event=_event(), user=self.member, status="pending"
        )
        tx = _payment("BUY-STAFF", user=self.staff, event_registration=registration)

        tx.refresh_from_db()
        self.assertEqual(tx.buyer_id, self.member.pk)
        self.assertEqual(tx.user_id, self.staff.pk)

    def test_premium_checkout_stamps_the_membership_owner(self):
        membership = _premium(self.member, status="pending")
        tx = _payment(
            "BUY-PREM",
            purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
            user=self.staff,
            premium_membership=membership,
        )

        self.assertEqual(tx.buyer_id, self.member.pk)

    def test_donation_and_member_stamped_providers_stamp_user(self):
        donation = _payment(
            "BUY-DON", purpose=PaymentTransaction.Purpose.DONATION, user=self.member
        )
        manual = _payment(
            "BUY-MAN", provider=PaymentTransaction.Provider.MANUAL, user=self.member
        )

        self.assertEqual(donation.buyer_id, self.member.pk)
        self.assertEqual(manual.buyer_id, self.member.pk)

    def test_unlinked_sumup_seat_row_proves_nobody_and_falls_back(self):
        """Its registration may be gone and ``user`` may be staff: no guess."""
        tx = _payment("BUY-ORPHAN", user=self.staff)

        self.assertIsNone(tx.buyer_id)
        self.assertEqual(tx.owner_id(), self.staff.pk)

    def test_buyer_survives_the_registration_being_deleted(self):
        """Codex 4287510165: the staff opener must not inherit the payment."""
        registration = EventRegistration.objects.create(
            event=_event(), user=self.member, status="pending"
        )
        tx = _payment(
            "BUY-SURVIVES",
            status=PaymentTransaction.Status.FAILED,
            user=self.staff,
            event_registration=registration,
        )

        registration.delete()
        tx = PaymentTransaction.objects.get(pk=tx.pk)
        self.assertIsNone(tx.event_registration_id)

        self.assertEqual(_payment_owner_ids(tx), {self.member.pk})
        self.assertEqual(_receipt_recipient(tx), self.member)
        self.assertEqual(
            [p["reference"] for p in _export(self.member)["payments"]],
            ["BUY-SURVIVES"],
        )
        self.assertEqual(_export(self.staff)["payments"], [])

        from crush_lu.admin import crush_admin_site

        admin = crush_admin_site._registry[PaymentTransaction]
        self.assertEqual(admin.get_buyer(tx), self.member.email)

    def test_unstamped_row_falls_back_then_heals_on_its_next_save(self):
        """A row the old code wrote mid-swap: links, then ``user``; stamped once
        it is saved again (the capture, usually)."""
        registration = EventRegistration.objects.create(
            event=_event(), user=self.member, status="pending"
        )
        tx = _payment("BUY-HEAL", user=self.staff, event_registration=registration)
        PaymentTransaction.objects.filter(pk=tx.pk).update(buyer=None)
        tx = PaymentTransaction.objects.get(pk=tx.pk)

        self.assertEqual(tx.owner_id(), self.member.pk)
        self.assertEqual(list(PaymentTransaction.objects.bought_by(self.member)), [tx])
        self.assertFalse(PaymentTransaction.objects.bought_by(self.staff).exists())

        tx.status = PaymentTransaction.Status.PAID
        tx.save(update_fields=["status"])
        tx.refresh_from_db()
        self.assertEqual(tx.buyer_id, self.member.pk)

    def test_backfill_applies_the_same_evidence_rule(self):
        migration = importlib.import_module(
            "crush_lu.migrations.0279_backfill_payment_buyer"
        )
        other = _user("other@example.com")
        registration = EventRegistration.objects.create(
            event=_event(), user=self.member, status="pending"
        )
        membership = _premium(other, status="pending")
        seat = _payment("BF-SEAT", user=self.staff, event_registration=registration)
        premium = _payment(
            "BF-PREM",
            purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
            user=self.staff,
            premium_membership=membership,
        )
        donation = _payment(
            "BF-DON", purpose=PaymentTransaction.Purpose.DONATION, user=self.member
        )
        credit = _payment(
            "BF-CREDIT", provider=PaymentTransaction.Provider.CREDIT, user=other
        )
        orphan = _payment("BF-ORPHAN", user=self.staff)
        PaymentTransaction.objects.update(buyer=None)

        migration.backfill_buyer(global_apps, None)
        migration.backfill_buyer(global_apps, None)  # idempotent

        buyers = dict(PaymentTransaction.objects.values_list("pk", "buyer_id"))
        self.assertEqual(buyers[seat.pk], self.member.pk)
        self.assertEqual(buyers[premium.pk], other.pk)
        self.assertEqual(buyers[donation.pk], self.member.pk)
        self.assertEqual(buyers[credit.pk], other.pk)
        self.assertIsNone(buyers[orphan.pk])

    def test_nothing_outside_the_model_writes_or_reads_buyer_directly(self):
        """``buyer`` is stamped by ``save`` alone and read through ``owner_id``
        / ``bought_by``, which also cover the rows it does not hold yet."""
        root = Path(__file__).resolve().parents[1]
        pattern = re.compile(r"\bbuyer(_id)?\s*=[^=]")
        offenders = []
        for path in root.rglob("*.py"):
            parts = set(path.relative_to(root).parts)
            if parts & {"tests", "migrations"}:
                continue
            if path.relative_to(root).as_posix() == "models/payments.py":
                continue
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(root)}:{number}: {line}")
        self.assertEqual(offenders, [])


class MergeHistoryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.keeper = _user("keeper@example.com", first_name="Kim")
        self.duplicate = _user("dup@privaterelay.appleid.com", first_name="Kim")
        self.admin = _user("admin@example.com", is_staff=True, is_superuser=True)
        self.other = _user(OTHER_EMAIL, first_name=OTHER_NAME)
        for user in (self.keeper, self.duplicate):
            _profile(user)
        _profile(self.other, gender="M")

    def _assert_other_member_absent(self, data):
        dump = json.dumps(data, ensure_ascii=False)
        self.assertNotIn(OTHER_EMAIL, dump)
        self.assertNotIn(OTHER_NAME, dump)

    def test_merge_records_the_alias(self):
        merge_accounts(self.keeper, self.duplicate, admin_user=self.admin)

        merge = AccountMerge.objects.get(duplicate=self.duplicate)
        self.assertEqual(merge.keeper, self.keeper)
        self.assertEqual(merge.merged_by, self.admin)
        self.assertEqual(
            AccountMerge.surviving_user_id(self.duplicate.pk), self.keeper.pk
        )

    def test_a_duplicate_merges_once_and_never_into_a_merged_account(self):
        merge_accounts(self.keeper, self.duplicate)
        third = _user("third@example.com")

        with self.assertRaisesMessage(ValueError, "already merged"):
            merge_accounts(third, self.duplicate)
        with self.assertRaisesMessage(ValueError, "Merge into that account"):
            merge_accounts(self.duplicate, third)
        self.assertEqual(AccountMerge.objects.count(), 1)
        third.refresh_from_db()
        self.assertTrue(third.is_active)

    def test_keeper_export_carries_the_duplicates_history_not_the_counterpart(
        self,
    ):
        """Codex 4287510173: what the merge leaves behind is still exported."""
        from crush_lu.models import UserBlock
        from crush_lu.models.crush_connect_cycle import (
            ConnectWeeklyRequest,
            ConnectWeekSession,
        )

        session = ConnectWeekSession.objects.create(user=self.duplicate)
        ConnectWeeklyRequest.objects.create(
            session=session,
            requester=self.duplicate,
            recipient=self.other,
            message="sent from my other account",
            expires_at=timezone.now() + timedelta(days=1),
        )
        UserBlock.objects.create(blocker=self.duplicate, blocked=self.other)
        membership = _premium(self.duplicate, status="active", payment_confirmed=True)
        _payment(
            "DUP-PREM",
            purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
            status=PaymentTransaction.Status.PAID,
            user=self.duplicate,
            premium_membership=membership,
        )

        merge_accounts(self.keeper, self.duplicate, admin_user=self.admin)
        data = _export(self.keeper)

        self.assertEqual(data["payments"], [])
        self.assertEqual(data["premium_memberships"], [])
        [block] = data["merged_accounts"]
        self.assertEqual(block["account"]["email"], "dup@privaterelay.appleid.com")
        self.assertIsNotNone(block["merged_at"])
        self.assertNotIn("profile", block)  # deleted by the merge
        self.assertNotIn("export_date", block)
        self.assertEqual([p["reference"] for p in block["payments"]], ["DUP-PREM"])
        self.assertEqual(len(block["premium_memberships"]), 1)
        [request] = block["crush_connect"]["requests"]
        self.assertEqual(request["direction"], "sent")
        self.assertEqual(request["your_message"], "sent from my other account")
        self.assertEqual(len(block["safety"]["blocks_made"]), 1)
        self._assert_other_member_absent(data)
        self.assertNotIn(self.admin.email, json.dumps(data))

    def test_moved_registration_payment_is_listed_once_under_its_buyer(self):
        """The registration moves to the keeper; ``buyer`` stays on the
        duplicate, so the payment shows in the duplicate's block only, and the
        keeper can still open its checkout."""
        registration = EventRegistration.objects.create(
            event=_event(), user=self.duplicate, status="pending"
        )
        tx = _payment("DUP-SEAT", user=self.duplicate, event_registration=registration)

        merge_accounts(self.keeper, self.duplicate)
        registration.refresh_from_db()
        tx.refresh_from_db()
        self.assertEqual(registration.user, self.keeper)
        self.assertEqual(tx.buyer_id, self.duplicate.pk)

        data = _export(self.keeper)
        references = [p["reference"] for p in data["payments"]] + [
            p["reference"]
            for block in data["merged_accounts"]
            for p in block["payments"]
        ]
        self.assertEqual(references, ["DUP-SEAT"])
        self.assertEqual(
            data["merged_accounts"][0]["payments"][0]["event"], "Buyer night"
        )
        self.assertIn(self.keeper.pk, _payment_owner_ids(tx))

    def test_keeper_is_receipted_and_opens_the_duplicates_payment(self):
        membership = _premium(self.duplicate, status="active", payment_confirmed=True)
        tx = _payment(
            "DUP-RECEIPT",
            purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
            status=PaymentTransaction.Status.PAID,
            user=self.duplicate,
            premium_membership=membership,
        )

        merge_accounts(self.keeper, self.duplicate)
        tx = PaymentTransaction.objects.get(pk=tx.pk)

        self.assertEqual(_receipt_recipient(tx), self.keeper)
        self.assertEqual(_payment_owner_ids(tx), {self.duplicate.pk, self.keeper.pk})

    def test_chained_merges_reach_the_last_keeper(self):
        """A into B, then B into C: C's export holds both, oldest first."""
        first = self.duplicate
        middle = self.keeper
        last = _user("last@example.com")
        _profile(last)
        UserBlockModel = global_apps.get_model("crush_lu", "UserBlock")
        UserBlockModel.objects.create(blocker=first, blocked=self.other)

        merge_accounts(middle, first)
        merge_accounts(last, middle)

        self.assertEqual(AccountMerge.surviving_user_id(first.pk), last.pk)
        blocks = _export(last)["merged_accounts"]
        self.assertEqual(
            [block["account"]["email"] for block in blocks],
            [first.email, middle.email],
        )
        self.assertEqual(len(blocks[0]["safety"]["blocks_made"]), 1)
        self.assertEqual(
            _export(middle)["merged_accounts"][0]["account"]["email"], first.email
        )

    def test_unmerged_export_has_no_merged_accounts_section(self):
        self.assertNotIn("merged_accounts", _export(self.keeper))
