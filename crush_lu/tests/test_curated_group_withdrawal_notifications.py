from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from crush_lu.models.credits import CrushCredit
from crush_lu.models.events import (
    CuratedEventGroup,
    CuratedEventGroupMembership,
    CuratedGroupNotification,
    EventRegistration,
    MeetupEvent,
)
from crush_lu.models.payments import PaymentTransaction
from crush_lu.models.profiles import CrushProfile
from crush_lu.services.curated_group_notifications import (
    deliver_curated_group_notifications,
    enqueue_withdrawal_notification,
)

User = get_user_model()


@override_settings(
    ROOT_URLCONF="azureproject.urls_crush",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class CuratedGroupWithdrawalNotificationTests(TestCase):
    def setUp(self):
        self.event = MeetupEvent.objects.create(
            title="Elastic evening",
            description="A fair parallel speed-dating evening",
            event_type="speed_dating",
            registration_mode="curated",
            date_time=timezone.now() + timedelta(days=7),
            registration_deadline=timezone.now() + timedelta(days=5),
            location="Luxembourg",
            address="Test venue",
            max_participants=12,
            group_size=6,
            planned_groups=1,
            registration_fee=Decimal("15.00"),
            profile_requirement="none",
            is_published=True,
        )

    def make_registration(
        self,
        suffix,
        *,
        language="en",
        status="applied",
        payment_confirmed=False,
    ):
        user = User.objects.create_user(
            username=f"withdrawal-{suffix}@example.com",
            email=f"withdrawal-{suffix}@example.com",
            password="testpass123",
            first_name="Alex",
        )
        CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 1, 1),
            gender="NB",
            location="Luxembourg",
            preferred_language=language,
        )
        registration = EventRegistration.objects.create(
            event=self.event,
            user=user,
            status="applied",
            payment_confirmed=False,
        )
        if status != "applied" or payment_confirmed:
            EventRegistration.objects.filter(pk=registration.pk).update(
                status=status,
                payment_confirmed=payment_confirmed,
            )
            registration.refresh_from_db()
        return registration

    def test_enqueue_is_idempotent_per_event_registration_and_generation(self):
        registration = self.make_registration("dedupe")

        first = enqueue_withdrawal_notification(registration, 4)
        duplicate = enqueue_withdrawal_notification(registration, 4)
        later_generation = enqueue_withdrawal_notification(registration, 5)

        self.assertEqual(first.pk, duplicate.pk)
        self.assertNotEqual(first.pk, later_generation.pk)
        self.assertEqual(CuratedGroupNotification.objects.count(), 2)
        self.assertEqual(
            first.dedupe_key,
            f"withdrawal:{self.event.pk}:{registration.pk}:4",
        )
        self.assertEqual(first.payload, {"generation": 4})
        self.assertEqual(first.kind, CuratedGroupNotification.Kind.WITHDRAWAL)

    def test_delivery_sends_no_payment_and_reconsideration_notice(self):
        registration = self.make_registration("english")
        notice = enqueue_withdrawal_notification(registration, 3)

        result = deliver_curated_group_notifications(
            registration_ids=[registration.pk],
            kinds=[CuratedGroupNotification.Kind.WITHDRAWAL],
        )

        self.assertEqual(result.attempted, 1)
        self.assertEqual(result.sent, 1)
        self.assertEqual(result.failed, 0)
        self.assertEqual(result.cancelled, 0)
        notice.refresh_from_db()
        self.assertEqual(notice.status, CuratedGroupNotification.Status.SENT)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].subject,
            "Your application for Elastic evening is being reconsidered",
        )
        self.assertIn("No payment was taken for this place.", mail.outbox[0].body)
        self.assertIn("We will reconsider your application", mail.outbox[0].body)

    def test_delivery_cancels_notices_that_are_no_longer_unpaid_and_applied(self):
        selected = self.make_registration("selected", status="pending")
        paid = self.make_registration("paid", payment_confirmed=True)
        notices = [
            enqueue_withdrawal_notification(selected, 2),
            enqueue_withdrawal_notification(paid, 2),
        ]

        with patch(
            "crush_lu.email_helpers.send_curated_group_withdrawal_notice"
        ) as send_notice:
            result = deliver_curated_group_notifications(
                registration_ids=[selected.pk, paid.pk],
                kinds=[CuratedGroupNotification.Kind.WITHDRAWAL],
            )

        self.assertEqual(result.attempted, 2)
        self.assertEqual(result.sent, 0)
        self.assertEqual(result.cancelled, 2)
        send_notice.assert_not_called()
        self.assertEqual(
            set(
                CuratedGroupNotification.objects.filter(
                    pk__in=[notice.pk for notice in notices]
                ).values_list("status", flat=True)
            ),
            {CuratedGroupNotification.Status.CANCELLED},
        )

    def test_delivery_cancels_old_notice_after_newer_certified_selection(self):
        registration = self.make_registration("reselected")
        notice = enqueue_withdrawal_notification(registration, 2)
        replacement = CuratedEventGroup.objects.create(
            event=self.event,
            generation=3,
            group_number=1,
        )
        CuratedEventGroupMembership.objects.create(
            event=self.event,
            group=replacement,
            registration=registration,
            position=1,
        )
        CuratedEventGroup.objects.filter(pk=replacement.pk).update(
            status=CuratedEventGroup.STATUS_PROVISIONAL,
            provisional_at=timezone.now(),
        )

        with patch(
            "crush_lu.email_helpers.send_curated_group_withdrawal_notice"
        ) as send_notice:
            result = deliver_curated_group_notifications(
                notice_ids=[notice.pk],
                kinds=[CuratedGroupNotification.Kind.WITHDRAWAL],
            )

        notice.refresh_from_db()
        self.assertEqual(result.cancelled, 1)
        self.assertEqual(notice.status, CuratedGroupNotification.Status.CANCELLED)
        send_notice.assert_not_called()

    def test_delivery_cancels_old_notice_after_late_paid_capture_remedy(self):
        registration = self.make_registration("late-paid")
        withdrawal = enqueue_withdrawal_notification(registration, 2)
        payment = PaymentTransaction.objects.create(
            transaction_reference="WITHDRAWAL-LATE-CAPTURE",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK-WITHDRAWAL-LATE-CAPTURE",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            paid_at=timezone.now(),
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=registration.user,
            event_registration=registration,
        )
        CuratedGroupNotification.objects.create(
            event=self.event,
            registration=registration,
            source_payment=payment,
            event_id_snapshot=self.event.pk,
            registration_id_snapshot=registration.pk,
            kind=CuratedGroupNotification.Kind.REMEDY,
            dedupe_key=f"remedy:{registration.pk}:payment:{payment.pk}",
            payload={"credit_ids": []},
        )

        with patch(
            "crush_lu.email_helpers.send_curated_group_withdrawal_notice"
        ) as send_notice:
            result = deliver_curated_group_notifications(
                notice_ids=[withdrawal.pk],
                kinds=[CuratedGroupNotification.Kind.WITHDRAWAL],
            )

        withdrawal.refresh_from_db()
        self.assertEqual(result.cancelled, 1)
        self.assertEqual(withdrawal.status, CuratedGroupNotification.Status.CANCELLED)
        send_notice.assert_not_called()

    def test_older_generation_remedy_does_not_suppress_later_withdrawal(self):
        registration = self.make_registration("older-remedy")
        old_moment = timezone.now() - timedelta(days=1)
        old_payment = PaymentTransaction.objects.create(
            transaction_reference="WITHDRAWAL-OLDER-REMEDY",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK-WITHDRAWAL-OLDER-REMEDY",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.REFUNDED,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            paid_at=old_moment,
            user=registration.user,
            event_registration=registration,
        )
        CrushCredit.objects.create(
            user=registration.user,
            amount_cents=1500,
            currency="EUR",
            issued_at=old_moment,
            expires_at=old_moment + timedelta(days=180),
            reason=CrushCredit.Reason.CURATED_GROUP_UNAVAILABLE,
            source_registration=registration,
            source_payment=old_payment,
        )
        old_remedy = CuratedGroupNotification.objects.create(
            event=self.event,
            registration=registration,
            source_payment=old_payment,
            event_id_snapshot=self.event.pk,
            registration_id_snapshot=registration.pk,
            kind=CuratedGroupNotification.Kind.REMEDY,
            dedupe_key=f"remedy:{registration.pk}:payment:{old_payment.pk}",
            payload={"credit_ids": []},
            status=CuratedGroupNotification.Status.SENT,
            sent_at=old_moment,
        )
        CuratedGroupNotification.objects.filter(pk=old_remedy.pk).update(
            created_at=old_moment
        )
        withdrawal = enqueue_withdrawal_notification(registration, 3)

        with patch(
            "crush_lu.email_helpers.send_curated_group_withdrawal_notice",
            return_value=1,
        ) as send_notice:
            result = deliver_curated_group_notifications(
                notice_ids=[withdrawal.pk],
                kinds=[CuratedGroupNotification.Kind.WITHDRAWAL],
            )

        withdrawal.refresh_from_db()
        self.assertEqual(result.sent, 1)
        self.assertEqual(withdrawal.status, CuratedGroupNotification.Status.SENT)
        send_notice.assert_called_once_with(registration)

    @override_settings(CURATED_NOTIFICATION_ADMIN_BATCH_SIZE=3)
    def test_snapshot_drain_attempts_every_recipient_without_failure_starvation(self):
        registrations = [
            self.make_registration(f"drain-{index}") for index in range(12)
        ]
        notices = [
            enqueue_withdrawal_notification(registration, 4)
            for registration in registrations
        ]

        with patch(
            "crush_lu.email_helpers.send_curated_group_withdrawal_notice",
            side_effect=lambda registration: registration.pk != registrations[0].pk,
        ) as send_notice:
            result = deliver_curated_group_notifications(
                notice_ids=[notice.pk for notice in notices],
                kinds=[CuratedGroupNotification.Kind.WITHDRAWAL],
                drain=True,
            )

        self.assertEqual(result.attempted, 12)
        self.assertEqual(result.sent, 11)
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.remaining, 1)
        self.assertEqual(send_notice.call_count, 12)
        statuses = dict(
            CuratedGroupNotification.objects.filter(
                pk__in=[notice.pk for notice in notices]
            ).values_list("registration_id", "status")
        )
        self.assertEqual(
            statuses[registrations[0].pk], CuratedGroupNotification.Status.PENDING
        )
        self.assertEqual(
            set(statuses[registration.pk] for registration in registrations[1:]),
            {CuratedGroupNotification.Status.SENT},
        )

    def test_bounded_retry_prioritizes_a_never_attempted_recipient(self):
        first = self.make_registration("retried-first")
        later = self.make_registration("never-attempted")
        retried_notice = enqueue_withdrawal_notification(first, 4)
        fresh_notice = enqueue_withdrawal_notification(later, 4)
        CuratedGroupNotification.objects.filter(pk=retried_notice.pk).update(
            attempt_count=1
        )

        with patch(
            "crush_lu.email_helpers.send_curated_group_withdrawal_notice",
            return_value=1,
        ) as send_notice:
            result = deliver_curated_group_notifications(
                notice_ids=[retried_notice.pk, fresh_notice.pk],
                kinds=[CuratedGroupNotification.Kind.WITHDRAWAL],
                limit=1,
            )

        retried_notice.refresh_from_db()
        fresh_notice.refresh_from_db()
        self.assertEqual(result.attempted, 1)
        self.assertEqual(fresh_notice.status, CuratedGroupNotification.Status.SENT)
        self.assertEqual(retried_notice.status, CuratedGroupNotification.Status.PENDING)
        send_notice.assert_called_once_with(later)

    def test_delivery_uses_formal_french_and_informal_german(self):
        cases = (
            (
                "fr",
                "Votre candidature pour Elastic evening est de nouveau à l’étude",
                "Aucun paiement n’a été prélevé pour cette place.",
                "Nous vous contacterons avant qu’un paiement ne soit nécessaire.",
            ),
            (
                "de",
                "Deine Bewerbung für Elastic evening wird erneut berücksichtigt",
                "Für diesen Platz wurde keine Zahlung eingezogen.",
                "Wir kontaktieren dich, bevor eine Zahlung erforderlich wird.",
            ),
        )
        for language, subject, payment_copy, contact_copy in cases:
            with self.subTest(language=language):
                registration = self.make_registration(language, language=language)
                enqueue_withdrawal_notification(registration, 7)
                mail.outbox = []

                result = deliver_curated_group_notifications(
                    registration_ids=[registration.pk],
                    kinds=[CuratedGroupNotification.Kind.WITHDRAWAL],
                )

                self.assertEqual(result.sent, 1)
                self.assertEqual(mail.outbox[0].subject, subject)
                self.assertIn(payment_copy, mail.outbox[0].body)
                self.assertIn(contact_copy, mail.outbox[0].body)
