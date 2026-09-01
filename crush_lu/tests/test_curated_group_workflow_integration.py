"""Cross-layer guarantees for fair curated groups and deferred payment.

These tests intentionally cross the projector, persisted group lifecycle,
checkout claim, capture remedy and translated email boundaries.  Unit tests
cover each component in isolation; this file pins the customer promise that
connects them: no certified five-date group means no payable seat, and a rare
capture after certification is lost is returned in full.
"""

from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
import json
from unittest.mock import Mock, patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages import ERROR
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from requests import HTTPError, Timeout

from crush_lu.models.credits import CreditRedemption, CrushCredit
from crush_lu.models.events import (
    CuratedEventGroup,
    CuratedEventGroupMembership,
    CuratedGroupNotification,
    EventRegistration,
    EventRegistrationPreference,
    MeetupEvent,
)
from crush_lu.models.payments import (
    EventCheckoutCreationClaim,
    PaymentTransaction,
)
from crush_lu.models.profiles import CrushProfile, UserDataConsent
from crush_lu.services.account_merge import merge_accounts
from crush_lu.services.curated_group_workflow import (
    approve_current_generation,
    generate_group_projection,
    lock_current_generation,
    repair_degraded_event_groups,
    start_curated_rounds,
)
from crush_lu.services.sumup import SumUpConfigurationError, SumUpError
from crush_lu.views_payments import _apply_paid_checkout

User = get_user_model()


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class CuratedGroupWorkflowIntegrationTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "crush.lu"

    def make_event(self, *, deadline=None, fee="15.00", **overrides):
        values = {
            "title": "Elastic evening",
            "description": "A fair parallel speed-dating evening",
            "event_type": "speed_dating",
            "registration_mode": "curated",
            "date_time": timezone.now() + timedelta(days=7),
            "registration_deadline": deadline or timezone.now() - timedelta(hours=1),
            "location": "Luxembourg",
            "address": "Test venue",
            "max_participants": 12,
            "group_size": 6,
            "planned_groups": 1,
            "registration_fee": Decimal(fee),
            "profile_requirement": "none",
            "is_published": True,
        }
        values.update(overrides)
        return MeetupEvent.objects.create(**values)

    def make_applicants(self, event, count=6, *, preferred_language="en"):
        registrations = []
        first_index = EventRegistration.objects.filter(event=event).count()
        for index in range(first_index, first_index + count):
            user = User.objects.create_user(
                username=f"elastic-{event.pk}-{index}@example.com",
                email=f"elastic-{event.pk}-{index}@example.com",
                password="testpass123",
                first_name=f"Member {index}",
            )
            UserDataConsent.objects.update_or_create(
                user=user,
                defaults={
                    "powerup_consent_given": True,
                    "crushlu_consent_given": True,
                },
            )
            CrushProfile.objects.create(
                user=user,
                date_of_birth=date(1995, 1, 1),
                gender="NB",
                location="Luxembourg",
                event_languages=["en"],
                preferred_language=preferred_language,
            )
            registration = EventRegistration.objects.create(
                event=event,
                user=user,
                status="applied",
            )
            EventRegistrationPreference.objects.create(
                registration=registration,
                preferred_genders=[],
                preferred_age_min=18,
                preferred_age_max=99,
                languages=[],
            )
            registrations.append(registration)
        return registrations

    def certify_one_group(
        self,
        *,
        preferred_language="en",
        applicant_count=6,
        event_overrides=None,
    ):
        event = self.make_event(**(event_overrides or {}))
        registrations = self.make_applicants(
            event,
            applicant_count,
            preferred_language=preferred_language,
        )
        stored = generate_group_projection(event, deterministic_seed="integration")
        approved = approve_current_generation(event)
        EventRegistration.objects.filter(
            pk__in=approved.applied_registration_ids
        ).update(status="pending")
        for registration in registrations:
            registration.refresh_from_db()
        group = CuratedEventGroup.objects.get(pk=stored.group_ids[0])
        return event, registrations, group

    def mark_group_paid(self, registrations, *, status="confirmed"):
        registration_ids = [registration.pk for registration in registrations]
        EventRegistration.objects.filter(pk__in=registration_ids).update(
            status=status,
            payment_confirmed=True,
            payment_date=timezone.now(),
        )
        payments = {}
        for registration in registrations:
            registration.refresh_from_db()
            payments[registration.pk] = PaymentTransaction.objects.create(
                transaction_reference=f"CURATED-PAID-{registration.pk}",
                provider=PaymentTransaction.Provider.SUMUP,
                sumup_checkout_id=f"CHK-CURATED-{registration.pk}",
                amount=Decimal("15.00"),
                currency="EUR",
                status=PaymentTransaction.Status.PAID,
                purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
                user=registration.user,
                event_registration=registration,
            )
        return payments

    def degrade_group_without_replacement(self, group, registrations):
        """Leave fewer than five candidates without firing the signal callback."""

        EventRegistration.objects.filter(
            pk__in=[registration.pk for registration in registrations[:2]]
        ).update(status="cancelled")
        with transaction.atomic():
            locked = CuratedEventGroup.objects.select_for_update().get(pk=group.pk)
            locked._mark_degraded_locked(
                reason=CuratedEventGroup.DEGRADATION_REASON_ORGANISER
            )
        group.refresh_from_db()
        self.assertEqual(group.status, CuratedEventGroup.STATUS_DEGRADED)

    def checkout_url(self, registration):
        return reverse(
            "crush_lu:sumup_create_event_checkout",
            kwargs={"registration_id": registration.pk},
        )

    def admin_request(self):
        staff_index = User.objects.count()
        staff = User.objects.create_superuser(
            username=f"curated-coach-{staff_index}@example.com",
            email=f"curated-coach-{staff_index}@example.com",
            password="testpass123",
        )
        request = RequestFactory().post("/crush-admin/")
        request.user = staff
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    def test_projection_generated_after_deadline_can_be_approved(self):
        event = self.make_event()
        self.make_applicants(event)

        stored = generate_group_projection(event, deterministic_seed="after-deadline")
        group = CuratedEventGroup.objects.get(pk=stored.group_ids[0])
        self.assertEqual(stored.status, CuratedEventGroup.STATUS_DRAFT)
        self.assertEqual(group.status, CuratedEventGroup.STATUS_DRAFT)

        approved = approve_current_generation(event)

        group.refresh_from_db()
        self.assertEqual(approved.generation, stored.generation)
        self.assertEqual(group.status, CuratedEventGroup.STATUS_PROVISIONAL)
        self.assertEqual(len(approved.applied_registration_ids), 6)
        self.assertTrue(group.schedule_digest)

    def test_projection_cannot_be_generated_before_deadline(self):
        deadline = timezone.now() + timedelta(hours=2)
        event = self.make_event(deadline=deadline)
        self.make_applicants(event)

        with self.assertRaisesMessage(
            ValidationError,
            "Close the application window before selecting and inviting groups",
        ):
            generate_group_projection(event, deterministic_seed="before-deadline")

        self.assertFalse(CuratedEventGroup.objects.filter(event=event).exists())

    def test_new_applicant_after_draft_forces_regeneration(self):
        event = self.make_event()
        self.make_applicants(event)
        stored = generate_group_projection(event, deterministic_seed="stale-pool")
        self.make_applicants(event, 1)

        with self.assertRaisesMessage(
            ValidationError,
            "Regenerate before selecting anyone",
        ):
            approve_current_generation(event)

        self.assertTrue(
            CuratedEventGroup.objects.filter(
                pk__in=stored.group_ids,
                status=CuratedEventGroup.STATUS_DRAFT,
            ).exists()
        )
        self.assertFalse(
            CuratedEventGroup.objects.filter(
                event=event,
                status=CuratedEventGroup.STATUS_PROVISIONAL,
            ).exists()
        )

    def test_preference_change_after_draft_forces_regeneration(self):
        event = self.make_event()
        registrations = self.make_applicants(event)
        stored = generate_group_projection(event, deterministic_seed="stale-preference")
        preference = registrations[0].preference
        preference.languages = ["fr"]
        preference.save(update_fields=["languages", "updated_at"])

        with self.assertRaisesMessage(
            ValidationError,
            "Regenerate before selecting anyone",
        ):
            approve_current_generation(event)

        self.assertTrue(
            CuratedEventGroup.objects.filter(
                pk__in=stored.group_ids,
                status=CuratedEventGroup.STATUS_DRAFT,
            ).exists()
        )
        self.assertFalse(
            CuratedEventGroup.objects.filter(
                event=event,
                status=CuratedEventGroup.STATUS_PROVISIONAL,
            ).exists()
        )

    def test_generation_refuses_open_gender_pool_above_event_cap(self):
        from crush_lu.admin.events import MeetupEventAdmin
        from crush_lu.services.event_grouping import project_event_groups

        event = self.make_event(
            max_participants_m=4,
            max_participants_f=4,
            max_participants_nb=4,
        )
        registrations = self.make_applicants(event)
        CrushProfile.objects.filter(
            user_id__in=[registration.user_id for registration in registrations]
        ).update(gender="M")

        projection = project_event_groups(
            event,
            deterministic_seed="gender-cap-projection",
        )
        self.assertEqual(projection.viable_groups, ())
        self.assertEqual(
            set(projection.unassigned_registration_ids),
            {registration.pk for registration in registrations},
        )

        with self.assertRaisesMessage(ValidationError, "gender pool cap"):
            generate_group_projection(
                event,
                deterministic_seed="gender-cap-generation",
            )

        request = self.admin_request()
        MeetupEventAdmin(MeetupEvent, AdminSite()).generate_curated_groups(
            request,
            MeetupEvent.objects.filter(pk=event.pk),
        )
        self.assertFalse(CuratedEventGroup.objects.filter(event=event).exists())
        errors = [message for message in request._messages if message.level == ERROR]
        self.assertEqual(len(errors), 1)
        self.assertIn("gender pool cap", str(errors[0]))

    def test_approval_refuses_draft_made_uninvitable_by_lower_gender_cap(self):
        event = self.make_event()
        registrations = self.make_applicants(event)
        CrushProfile.objects.filter(
            user_id__in=[registration.user_id for registration in registrations]
        ).update(gender="M")
        stored = generate_group_projection(
            event,
            deterministic_seed="cap-introduced-after-draft",
        )
        MeetupEvent.objects.filter(pk=event.pk).update(
            max_participants_m=4,
            max_participants_f=4,
            max_participants_nb=4,
        )

        with self.assertRaisesMessage(
            ValidationError,
            "Regenerate before selecting anyone",
        ):
            approve_current_generation(event)

        self.assertEqual(
            CuratedEventGroup.objects.filter(
                pk__in=stored.group_ids,
                status=CuratedEventGroup.STATUS_DRAFT,
            ).count(),
            len(stored.group_ids),
        )
        self.assertFalse(
            CuratedEventGroup.objects.filter(
                event=event,
                status=CuratedEventGroup.STATUS_PROVISIONAL,
            ).exists()
        )

    def test_admin_invite_requires_explicit_approval_and_selects_exact_roster(self):
        from crush_lu.admin.events import MeetupEventAdmin

        event = self.make_event()
        registrations = self.make_applicants(event, 7)
        stored = generate_group_projection(event, deterministic_seed="admin-invite")
        group = CuratedEventGroup.objects.get(pk=stored.group_ids[0])
        selected_ids = set(
            group.memberships.filter(released_at__isnull=True).values_list(
                "registration_id", flat=True
            )
        )
        unselected_ids = {
            registration.pk for registration in registrations
        } - selected_ids
        event_admin = MeetupEventAdmin(MeetupEvent, AdminSite())
        request = self.admin_request()
        event_queryset = MeetupEvent.objects.filter(pk=event.pk)

        with patch(
            "crush_lu.email_helpers.send_event_payment_pending_notification",
            return_value=1,
        ) as notify:
            with self.captureOnCommitCallbacks(execute=True):
                event_admin.invite_approved_curated_groups(
                    request,
                    event_queryset,
                )

            group.refresh_from_db()
            self.assertEqual(group.status, CuratedEventGroup.STATUS_DRAFT)
            self.assertFalse(
                EventRegistration.objects.filter(event=event)
                .exclude(status="applied")
                .exists()
            )
            notify.assert_not_called()
            self.assertEqual(mail.outbox, [])

            event_admin.approve_curated_groups(request, event_queryset)

            group.refresh_from_db()
            self.assertEqual(group.status, CuratedEventGroup.STATUS_PROVISIONAL)
            self.assertFalse(
                EventRegistration.objects.filter(event=event)
                .exclude(status="applied")
                .exists()
            )
            notify.assert_not_called()

            with self.captureOnCommitCallbacks(execute=True):
                event_admin.invite_approved_curated_groups(
                    request,
                    event_queryset,
                )

        self.assertEqual(
            set(
                EventRegistration.objects.filter(
                    event=event,
                    status="pending",
                ).values_list("pk", flat=True)
            ),
            selected_ids,
        )
        self.assertEqual(
            set(
                EventRegistration.objects.filter(
                    event=event,
                    status="applied",
                ).values_list("pk", flat=True)
            ),
            unselected_ids,
        )
        self.assertEqual(notify.call_count, len(selected_ids))
        self.assertEqual(
            {invocation.args[0].pk for invocation in notify.call_args_list},
            selected_ids,
        )

    def test_selection_notification_partial_failure_retries_only_failed_recipient(self):
        from crush_lu.services.curated_group_notifications import (
            deliver_curated_group_notifications,
            enqueue_selection_notifications,
        )

        _event, registrations, group = self.certify_one_group()
        selected_ids = sorted(
            group.memberships.filter(released_at__isnull=True).values_list(
                "registration_id", flat=True
            )
        )
        failed_id = selected_ids[0]
        self.assertEqual(
            enqueue_selection_notifications(
                {registration_id: group.generation for registration_id in selected_ids}
            ),
            len(selected_ids),
        )
        deliveries = []

        def deliver_with_one_retry(registration):
            deliveries.append(registration.pk)
            return registration.pk != failed_id or deliveries.count(failed_id) > 1

        with patch(
            "crush_lu.email_helpers.send_event_payment_pending_notification",
            side_effect=deliver_with_one_retry,
        ):
            first = deliver_curated_group_notifications(
                registration_ids=selected_ids,
                kinds=[CuratedGroupNotification.Kind.SELECTION],
                limit=50,
            )
            self.assertEqual(first.attempted, len(selected_ids))
            self.assertEqual(first.sent, len(selected_ids) - 1)
            self.assertEqual(first.failed, 1)
            self.assertEqual(first.remaining, 1)

            failed_notice = CuratedGroupNotification.objects.get(
                registration_id=failed_id,
                kind=CuratedGroupNotification.Kind.SELECTION,
            )
            self.assertEqual(
                failed_notice.status,
                CuratedGroupNotification.Status.PENDING,
            )
            self.assertEqual(failed_notice.attempt_count, 1)
            self.assertIn("returned false", failed_notice.last_error)
            self.assertEqual(
                CuratedGroupNotification.objects.filter(
                    registration_id__in=selected_ids,
                    kind=CuratedGroupNotification.Kind.SELECTION,
                    status=CuratedGroupNotification.Status.SENT,
                    attempt_count=1,
                ).count(),
                len(selected_ids) - 1,
            )

            second = deliver_curated_group_notifications(
                registration_ids=selected_ids,
                kinds=[CuratedGroupNotification.Kind.SELECTION],
                limit=50,
            )

        self.assertEqual(second.attempted, 1)
        self.assertEqual(second.sent, 1)
        self.assertEqual(second.failed, 0)
        self.assertEqual(second.remaining, 0)
        self.assertEqual(Counter(deliveries)[failed_id], 2)
        for registration_id in selected_ids[1:]:
            self.assertEqual(Counter(deliveries)[registration_id], 1)
        failed_notice.refresh_from_db()
        self.assertEqual(failed_notice.status, CuratedGroupNotification.Status.SENT)
        self.assertEqual(failed_notice.attempt_count, 2)

    def test_selection_notification_drain_leaves_exact_bounded_remainder(self):
        from crush_lu.services.curated_group_notifications import (
            deliver_curated_group_notifications,
            enqueue_selection_notifications,
        )

        _event, _registrations, group = self.certify_one_group()
        selected_ids = set(
            group.memberships.filter(released_at__isnull=True).values_list(
                "registration_id", flat=True
            )
        )
        enqueue_selection_notifications(
            {registration_id: group.generation for registration_id in selected_ids}
        )

        with patch(
            "crush_lu.email_helpers.send_event_payment_pending_notification",
            return_value=1,
        ) as send:
            batch = deliver_curated_group_notifications(
                registration_ids=selected_ids,
                kinds=[CuratedGroupNotification.Kind.SELECTION],
                limit=2,
            )

        sent_ids = set(
            CuratedGroupNotification.objects.filter(
                registration_id__in=selected_ids,
                kind=CuratedGroupNotification.Kind.SELECTION,
                status=CuratedGroupNotification.Status.SENT,
            ).values_list("registration_id", flat=True)
        )
        pending_ids = set(
            CuratedGroupNotification.objects.filter(
                registration_id__in=selected_ids,
                kind=CuratedGroupNotification.Kind.SELECTION,
                status=CuratedGroupNotification.Status.PENDING,
            ).values_list("registration_id", flat=True)
        )
        self.assertEqual(batch.attempted, 2)
        self.assertEqual(batch.sent, 2)
        self.assertEqual(batch.remaining, len(selected_ids) - 2)
        self.assertEqual(len(sent_ids), 2)
        self.assertEqual(pending_ids, selected_ids - sent_ids)
        self.assertEqual(
            {invocation.args[0].pk for invocation in send.call_args_list},
            sent_ids,
        )

    def test_degraded_group_reprojects_without_displacing_pinned_members(self):
        event, registrations, degraded = self.certify_one_group(applicant_count=7)
        selected_ids = set(
            CuratedEventGroupMembership.objects.filter(
                group=degraded,
                released_at__isnull=True,
            ).values_list("registration_id", flat=True)
        )
        departing = next(reg for reg in registrations if reg.pk in selected_ids)
        departing_checkout = PaymentTransaction.objects.create(
            transaction_reference="CURATED-DROPPED-REPROJECT",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK-CURATED-DROPPED-REPROJECT",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=departing.user,
            event_registration=departing,
        )
        departing.status = "cancelled"
        departing.save(update_fields=["status"])
        degraded.refresh_from_db()
        self.assertEqual(degraded.status, CuratedEventGroup.STATUS_DEGRADED)

        with self.assertRaisesMessage(
            ValidationError, "must use the audited repair action"
        ):
            generate_group_projection(event, deterministic_seed="unsafe-bypass")

        with patch(
            "crush_lu.services.sumup.SumUpClient.ensure_checkout_not_payable",
            return_value=True,
        ) as ensure_not_payable:
            remedy = repair_degraded_event_groups(event)

        degraded.refresh_from_db()
        departing_checkout.refresh_from_db()
        replacement_group = CuratedEventGroup.objects.get(
            event=event,
            generation=remedy.replacement_generation,
        )
        pinned_ids = selected_ids - {departing.pk}
        replacement_ids = set(
            replacement_group.memberships.filter(released_at__isnull=True).values_list(
                "registration_id", flat=True
            )
        )
        ensure_not_payable.assert_called_once_with(departing_checkout.sumup_checkout_id)
        self.assertEqual(remedy.action, "reprojected")
        self.assertEqual(departing_checkout.status, PaymentTransaction.Status.CANCELLED)
        self.assertEqual(replacement_group.status, CuratedEventGroup.STATUS_PROVISIONAL)
        self.assertTrue(pinned_ids <= replacement_ids)
        self.assertEqual(degraded.status, CuratedEventGroup.STATUS_CANCELLED)
        self.assertFalse(degraded.memberships.filter(released_at__isnull=True).exists())

    def test_reprojection_reconciles_lease_when_member_returns_during_provider_call(
        self,
    ):
        event, registrations, degraded = self.certify_one_group()
        departing = registrations[0]
        transaction_reference = "CURATED-RETURNING-LEASE"
        old_checkout_id = "CHK-CURATED-RETURNING-LEASE"
        claim = EventCheckoutCreationClaim.objects.create(
            registration=departing,
            registration_id_snapshot=departing.pk,
            event_id_snapshot=event.pk,
            transaction_reference=transaction_reference,
            payment_method="card",
            provider_checkout_id=old_checkout_id,
        )
        old_payment = PaymentTransaction.objects.create(
            transaction_reference=transaction_reference,
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id=old_checkout_id,
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=departing.user,
            event_registration=departing,
        )
        departing.status = "cancelled"
        departing.save(update_fields=["status"])
        degraded.refresh_from_db()
        self.assertEqual(degraded.status, CuratedEventGroup.STATUS_DEGRADED)

        def member_returns_while_sumup_is_unlocked(_checkout_id):
            EventRegistration.objects.filter(pk=departing.pk).update(
                status="pending",
                cancelled_at=None,
            )
            return True

        with patch(
            "crush_lu.services.sumup.SumUpClient.ensure_checkout_not_payable",
            side_effect=member_returns_while_sumup_is_unlocked,
        ) as ensure_not_payable:
            remedy = repair_degraded_event_groups(event)

        ensure_not_payable.assert_called_once_with(old_checkout_id)
        self.assertEqual(remedy.action, "reprojected")
        replacement_group = CuratedEventGroup.objects.get(
            event=event,
            generation=remedy.replacement_generation,
        )
        self.assertTrue(
            replacement_group.memberships.filter(
                registration=departing,
                released_at__isnull=True,
            ).exists()
        )
        old_payment.refresh_from_db()
        self.assertEqual(old_payment.status, PaymentTransaction.Status.CANCELLED)
        self.assertFalse(
            EventCheckoutCreationClaim.objects.filter(pk=claim.pk).exists()
        )
        self.assertFalse(
            EventCheckoutCreationClaim.objects.filter(
                state=EventCheckoutCreationClaim.State.RETIRING
            ).exists()
        )

        self.client.force_login(departing.user)
        with patch(
            "crush_lu.views_payments.SumUpClient.create_checkout",
            return_value={"id": "CHK-CURATED-AFTER-RETURN", "status": "PENDING"},
        ):
            retried = self.client.post(self.checkout_url(departing))

        self.assertEqual(retried.status_code, 200)
        self.assertEqual(retried.json()["checkout_id"], "CHK-CURATED-AFTER-RETURN")

    def test_ambiguous_dropped_member_claim_blocks_reprojection(self):
        event, registrations, degraded = self.certify_one_group(applicant_count=7)
        selected_ids = set(
            degraded.memberships.filter(released_at__isnull=True).values_list(
                "registration_id", flat=True
            )
        )
        departing = next(reg for reg in registrations if reg.pk in selected_ids)
        claim = EventCheckoutCreationClaim.objects.create(
            registration=departing,
            registration_id_snapshot=departing.pk,
            event_id_snapshot=event.pk,
            transaction_reference="CURATED-DROPPED-AMBIGUOUS",
            payment_method="card",
        )
        departing.status = "cancelled"
        departing.save(update_fields=["status"])

        with self.assertRaisesMessage(
            ValidationError, "ambiguous checkout claims without a provider ID"
        ):
            repair_degraded_event_groups(event)

        degraded.refresh_from_db()
        claim.refresh_from_db()
        self.assertEqual(degraded.status, CuratedEventGroup.STATUS_DEGRADED)
        self.assertEqual(claim.state, EventCheckoutCreationClaim.State.ACTIVE)
        self.assertTrue(
            degraded.memberships.filter(
                registration=departing, released_at__isnull=True
            ).exists()
        )

    def test_account_merge_refuses_released_cancelled_group_history(self):
        event = self.make_event()
        registrations = self.make_applicants(event)
        stored = generate_group_projection(event, deterministic_seed="merge-history")
        cancelled = CuratedEventGroup.objects.get(pk=stored.group_ids[0])
        duplicate = registrations[0]
        keeper = registrations[1]
        duplicate_membership = cancelled.memberships.get(registration=duplicate)
        duplicate_participant_ids = list(
            cancelled.pairing_participants.filter(registration=duplicate).values_list(
                "pk", flat=True
            )
        )
        cancelled.cancel(reason="Retain the projection as audit history")
        duplicate_membership.refresh_from_db()
        self.assertIsNotNone(duplicate_membership.released_at)
        self.assertEqual(cancelled.status, CuratedEventGroup.STATUS_CANCELLED)

        with self.assertRaisesMessage(ValueError, "curated-group history"):
            merge_accounts(keeper.user, duplicate.user)

        duplicate.user.refresh_from_db()
        self.assertTrue(duplicate.user.is_active)
        self.assertTrue(EventRegistration.objects.filter(pk=duplicate.pk).exists())
        self.assertTrue(EventRegistration.objects.filter(pk=keeper.pk).exists())
        self.assertTrue(
            cancelled.pairing_participants.filter(
                pk__in=duplicate_participant_ids
            ).exists()
        )

    def test_checkout_is_rejected_without_a_certified_group(self):
        event = self.make_event()
        registration = self.make_applicants(event, 1)[0]
        # Simulate an old/manual seat-holding row that predates the selection
        # backstop.  Checkout must still fail closed at its own boundary.
        EventRegistration.objects.filter(pk=registration.pk).update(status="pending")
        registration.refresh_from_db()
        self.client.force_login(registration.user)

        with patch(
            "crush_lu.views_payments.SumUpClient.create_checkout"
        ) as create_checkout:
            response = self.client.post(self.checkout_url(registration))

        self.assertEqual(response.status_code, 400)
        create_checkout.assert_not_called()
        self.assertFalse(PaymentTransaction.objects.exists())
        self.assertFalse(EventCheckoutCreationClaim.objects.exists())

    def test_provider_checkout_runs_after_database_atomic_scope_closes(self):
        _event, registrations, group = self.certify_one_group()
        registration = next(
            registration
            for registration in registrations
            if group.memberships.filter(registration=registration).exists()
        )
        self.client.force_login(registration.user)
        connection = transaction.get_connection()
        # TestCase itself owns wrapper atomics.  The provider boundary must be
        # back at that baseline, with no view-owned atomic block still open.
        baseline_atomic_depth = len(connection.atomic_blocks)
        atomic_depths = []

        def provider_create(**_kwargs):
            atomic_depths.append(len(connection.atomic_blocks))
            return {"id": "CHK-CURATED-UNLOCKED", "status": "PENDING"}

        with patch(
            "crush_lu.views_payments.SumUpClient.create_checkout",
            side_effect=provider_create,
        ):
            response = self.client.post(self.checkout_url(registration))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(atomic_depths, [baseline_atomic_depth])
        self.assertTrue(
            PaymentTransaction.objects.filter(
                sumup_checkout_id="CHK-CURATED-UNLOCKED",
                status=PaymentTransaction.Status.PENDING,
            ).exists()
        )
        self.assertFalse(EventCheckoutCreationClaim.objects.exists())

    def test_definitive_sumup_rejection_releases_claim_for_retry(self):
        _event, registrations, group = self.certify_one_group()
        registration = next(
            registration
            for registration in registrations
            if group.memberships.filter(registration=registration).exists()
        )
        self.client.force_login(registration.user)
        rejected_response = Mock(status_code=400)
        rejected = SumUpError("SumUp rejected checkout creation")
        rejected.__cause__ = HTTPError(response=rejected_response)

        with patch(
            "crush_lu.views_payments.SumUpClient.create_checkout",
            side_effect=[rejected, {"id": "CHK-AFTER-REJECTION", "status": "PENDING"}],
        ) as create_checkout:
            failed = self.client.post(self.checkout_url(registration))
            self.assertEqual(failed.status_code, 500)
            self.assertFalse(EventCheckoutCreationClaim.objects.exists())

            retried = self.client.post(self.checkout_url(registration))

        self.assertEqual(retried.status_code, 200)
        self.assertEqual(retried.json()["checkout_id"], "CHK-AFTER-REJECTION")
        self.assertEqual(create_checkout.call_count, 2)
        self.assertFalse(EventCheckoutCreationClaim.objects.exists())

    def test_local_sumup_configuration_failure_releases_claim_for_retry(self):
        _event, registrations, group = self.certify_one_group()
        registration = next(
            registration
            for registration in registrations
            if group.memberships.filter(registration=registration).exists()
        )
        self.client.force_login(registration.user)

        with patch(
            "crush_lu.views_payments.SumUpClient.create_checkout",
            side_effect=[
                SumUpConfigurationError("SUMUP_API_KEY is not configured"),
                {"id": "CHK-AFTER-CONFIG-FIX", "status": "PENDING"},
            ],
        ) as create_checkout:
            failed = self.client.post(self.checkout_url(registration))
            self.assertEqual(failed.status_code, 500)
            self.assertFalse(EventCheckoutCreationClaim.objects.exists())

            retried = self.client.post(self.checkout_url(registration))

        self.assertEqual(retried.status_code, 200)
        self.assertEqual(retried.json()["checkout_id"], "CHK-AFTER-CONFIG-FIX")
        self.assertEqual(create_checkout.call_count, 2)

    def test_sumup_rate_limit_rejection_releases_claim_for_retry(self):
        _event, registrations, group = self.certify_one_group()
        registration = next(
            registration
            for registration in registrations
            if group.memberships.filter(registration=registration).exists()
        )
        self.client.force_login(registration.user)
        rate_limited = SumUpError("SumUp rate limit rejected checkout creation")
        rate_limited.__cause__ = HTTPError(response=Mock(status_code=429))

        with patch(
            "crush_lu.views_payments.SumUpClient.create_checkout",
            side_effect=[
                rate_limited,
                {"id": "CHK-AFTER-RATE-LIMIT", "status": "PENDING"},
            ],
        ) as create_checkout:
            failed = self.client.post(self.checkout_url(registration))
            self.assertEqual(failed.status_code, 500)
            self.assertFalse(EventCheckoutCreationClaim.objects.exists())

            retried = self.client.post(self.checkout_url(registration))

        self.assertEqual(retried.status_code, 200)
        self.assertEqual(retried.json()["checkout_id"], "CHK-AFTER-RATE-LIMIT")
        self.assertEqual(create_checkout.call_count, 2)

    def test_ambiguous_sumup_timeout_retains_claim_and_blocks_retry(self):
        _event, registrations, group = self.certify_one_group()
        registration = next(
            registration
            for registration in registrations
            if group.memberships.filter(registration=registration).exists()
        )
        self.client.force_login(registration.user)
        ambiguous = SumUpError("SumUp checkout outcome is unknown")
        ambiguous.__cause__ = Timeout("response timed out")

        with patch(
            "crush_lu.views_payments.SumUpClient.create_checkout",
            side_effect=ambiguous,
        ) as create_checkout:
            failed = self.client.post(self.checkout_url(registration))
            blocked = self.client.post(self.checkout_url(registration))

        self.assertEqual(failed.status_code, 500)
        self.assertEqual(blocked.status_code, 409)
        create_checkout.assert_called_once()
        claim = EventCheckoutCreationClaim.objects.get(registration=registration)
        self.assertEqual(claim.state, EventCheckoutCreationClaim.State.ACTIVE)
        self.assertEqual(claim.provider_checkout_id, "")

    def test_sumup_conflict_retains_claim_as_possible_prior_creation(self):
        _event, registrations, group = self.certify_one_group()
        registration = next(
            registration
            for registration in registrations
            if group.memberships.filter(registration=registration).exists()
        )
        self.client.force_login(registration.user)
        conflict = SumUpError("checkout reference may already exist")
        conflict.__cause__ = HTTPError(response=Mock(status_code=409))

        with patch(
            "crush_lu.views_payments.SumUpClient.create_checkout",
            side_effect=conflict,
        ) as create_checkout:
            failed = self.client.post(self.checkout_url(registration))
            blocked = self.client.post(self.checkout_url(registration))

        self.assertEqual(failed.status_code, 500)
        self.assertEqual(blocked.status_code, 409)
        create_checkout.assert_called_once()
        self.assertTrue(
            EventCheckoutCreationClaim.objects.filter(
                registration=registration,
                state=EventCheckoutCreationClaim.State.ACTIVE,
                provider_checkout_id="",
            ).exists()
        )

    def test_manual_payment_refuses_stale_attended_to_pending_admin_post(self):
        from crush_lu.admin.events import (
            EventRegistrationAdmin,
            EventRegistrationAdminForm,
        )

        _event, registrations, group = self.certify_one_group()
        registration = next(
            registration
            for registration in registrations
            if group.memberships.filter(registration=registration).exists()
        )
        EventRegistration.objects.filter(pk=registration.pk).update(status="attended")
        current = EventRegistration.objects.select_related("event", "user").get(
            pk=registration.pk
        )
        form = EventRegistrationAdminForm(
            data={
                "event": current.event_id,
                "user": current.user_id,
                # The browser posts the stale value it originally displayed;
                # the form initial is the now-current attended row.
                "status": "pending",
                "payment_confirmed": "on",
                "dietary_restrictions": "",
                "bringing_guest": "",
                "guest_name": "",
                "accessibility_needs": "",
                "special_requests": "",
            },
            instance=current,
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        self.assertEqual(form.initial["status"], "attended")
        self.assertIn("status", form.changed_data)
        self.assertIn("payment_confirmed", form.changed_data)
        posted = form.save(commit=False)
        request = self.admin_request()

        with patch(
            "crush_lu.services.sumup.SumUpClient.deactivate_checkout"
        ) as deactivate:
            EventRegistrationAdmin(EventRegistration, AdminSite()).save_model(
                request,
                posted,
                form,
                change=True,
            )

        registration.refresh_from_db()
        self.assertEqual(registration.status, "attended")
        self.assertFalse(registration.payment_confirmed)
        self.assertIsNone(registration.payment_date)
        deactivate.assert_not_called()
        self.assertFalse(
            PaymentTransaction.objects.filter(
                event_registration=registration,
                provider=PaymentTransaction.Provider.MANUAL,
                status=PaymentTransaction.Status.PAID,
            ).exists()
        )
        errors = [message for message in request._messages if message.level == ERROR]
        self.assertEqual(len(errors), 1)
        self.assertIn("record payment separately", str(errors[0]))

    def test_manual_uncheck_refuses_card_and_credit_payment_ledgers(self):
        from crush_lu.admin.events import EventRegistrationAdmin
        from crush_lu.services.credits import issue_credit

        for provider in (
            PaymentTransaction.Provider.SUMUP,
            PaymentTransaction.Provider.CREDIT,
        ):
            with self.subTest(provider=provider):
                event = self.make_event()
                registration = self.make_applicants(event, 1)[0]
                confirmed_at = timezone.now()
                EventRegistration.objects.filter(pk=registration.pk).update(
                    status="confirmed",
                    payment_confirmed=True,
                    payment_date=confirmed_at,
                )
                registration.refresh_from_db()
                payment = PaymentTransaction.objects.create(
                    transaction_reference=(
                        f"CRUSH-{provider.upper()}-UNCHECK-{registration.pk}"
                    ),
                    provider=provider,
                    sumup_checkout_id=(
                        f"CHK-UNCHECK-{registration.pk}"
                        if provider == PaymentTransaction.Provider.SUMUP
                        else ""
                    ),
                    amount=Decimal("15.00"),
                    currency="EUR",
                    status=PaymentTransaction.Status.PAID,
                    purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
                    user=registration.user,
                    event_registration=registration,
                    paid_at=registration.payment_date,
                )
                redemption_ids = []
                if provider == PaymentTransaction.Provider.CREDIT:
                    funding = issue_credit(
                        registration.user,
                        1500,
                        CrushCredit.Reason.GOODWILL,
                    )
                    redemption = CreditRedemption.objects.create(
                        credit=funding,
                        event_registration=registration,
                        amount_cents=1500,
                    )
                    redemption_ids.append(redemption.pk)

                posted = EventRegistration.objects.select_related("event", "user").get(
                    pk=registration.pk
                )
                posted.payment_confirmed = False
                form = type(
                    "PaymentUncheckForm",
                    (),
                    {
                        "initial": {
                            "status": "confirmed",
                            "payment_confirmed": True,
                        },
                        "changed_data": ["payment_confirmed"],
                    },
                )()
                request = self.admin_request()

                EventRegistrationAdmin(EventRegistration, AdminSite()).save_model(
                    request,
                    posted,
                    form,
                    change=True,
                )

                registration.refresh_from_db()
                payment.refresh_from_db()
                self.assertEqual(registration.status, "confirmed")
                self.assertTrue(registration.payment_confirmed)
                self.assertEqual(registration.payment_date, confirmed_at)
                self.assertEqual(payment.status, PaymentTransaction.Status.PAID)
                self.assertEqual(payment.provider, provider)
                self.assertEqual(
                    list(
                        CreditRedemption.objects.filter(
                            pk__in=redemption_ids,
                            event_registration=registration,
                            amount_cents=1500,
                        ).values_list("pk", flat=True)
                    ),
                    redemption_ids,
                )
                self.assertFalse(
                    PaymentTransaction.objects.filter(
                        event_registration=registration,
                        provider=PaymentTransaction.Provider.MANUAL,
                    ).exists()
                )
                errors = [
                    message for message in request._messages if message.level == ERROR
                ]
                self.assertEqual(len(errors), 1)
                self.assertIn("explicit refund/credit-void workflow", str(errors[0]))

    def test_active_checkout_claim_returns_conflict_without_provider_call(self):
        event, registrations, group = self.certify_one_group()
        registration = next(
            registration
            for registration in registrations
            if group.memberships.filter(registration=registration).exists()
        )
        EventCheckoutCreationClaim.objects.create(
            registration=registration,
            registration_id_snapshot=registration.pk,
            event_id_snapshot=event.pk,
            transaction_reference="CRUSH-EVT-ACTIVE-CLAIM",
            payment_method="card",
        )
        self.client.force_login(registration.user)

        with patch(
            "crush_lu.views_payments.SumUpClient.create_checkout"
        ) as create_checkout:
            response = self.client.post(self.checkout_url(registration))

        self.assertEqual(response.status_code, 409)
        self.assertIn("already being prepared", response.json()["error"])
        create_checkout.assert_not_called()
        self.assertEqual(EventCheckoutCreationClaim.objects.count(), 1)

    def test_stale_blank_checkout_claim_cleanup_is_retained_active(self):
        event = self.make_event()
        registration = self.make_applicants(event, 1)[0]
        claim = EventCheckoutCreationClaim.objects.create(
            registration=registration,
            registration_id_snapshot=registration.pk,
            event_id_snapshot=event.pk,
            transaction_reference="CRUSH-EVT-STALE-CLAIM",
            payment_method="card",
            claimed_at=timezone.now() - timedelta(minutes=30),
        )
        dry_output = StringIO()

        call_command(
            "cleanup_event_checkout_claims",
            minutes=10,
            limit=20,
            stdout=dry_output,
        )

        self.assertIn("Stale event checkout claims [DRY-RUN]: 1", dry_output.getvalue())
        self.assertTrue(EventCheckoutCreationClaim.objects.filter(pk=claim.pk).exists())

        apply_output = StringIO()
        error_output = StringIO()
        with patch(
            "crush_lu.management.commands.cleanup_event_checkout_claims."
            "SumUpClient.deactivate_checkout"
        ) as deactivate:
            call_command(
                "cleanup_event_checkout_claims",
                apply=True,
                minutes=10,
                limit=20,
                stdout=apply_output,
                stderr=error_output,
            )

        self.assertIn("Retired 0 stale claim(s); retained 1", apply_output.getvalue())
        self.assertIn("provider absence", error_output.getvalue())
        deactivate.assert_not_called()
        claim.refresh_from_db()
        self.assertEqual(claim.state, EventCheckoutCreationClaim.State.ACTIVE)

    def test_stale_known_checkout_claim_is_deactivated_then_retired(self):
        event = self.make_event()
        registration = self.make_applicants(event, 1)[0]
        claim = EventCheckoutCreationClaim.objects.create(
            registration=registration,
            registration_id_snapshot=registration.pk,
            event_id_snapshot=event.pk,
            transaction_reference="CRUSH-EVT-STALE-KNOWN",
            payment_method="card",
            provider_checkout_id="CHK-STALE-KNOWN",
            claimed_at=timezone.now() - timedelta(minutes=30),
        )
        apply_output = StringIO()

        with patch(
            "crush_lu.management.commands.cleanup_event_checkout_claims."
            "SumUpClient.deactivate_checkout",
            return_value=True,
        ) as deactivate:
            call_command(
                "cleanup_event_checkout_claims",
                apply=True,
                minutes=10,
                limit=20,
                stdout=apply_output,
            )

        deactivate.assert_called_once_with("CHK-STALE-KNOWN")
        self.assertIn("Retired 1 stale claim(s); retained 0", apply_output.getvalue())
        self.assertFalse(
            EventCheckoutCreationClaim.objects.filter(pk=claim.pk).exists()
        )

    def test_cleanup_retires_legacy_orphan_pending_event_checkout(self):
        event = self.make_event()
        registration = self.make_applicants(event, 1)[0]
        payment = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-LEGACY-ORPHAN-CLEANUP",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK-LEGACY-ORPHAN-CLEANUP",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=registration.user,
            event_registration=registration,
        )
        PaymentTransaction.objects.filter(pk=payment.pk).update(
            event_registration=None,
            created_at=timezone.now() - timedelta(minutes=30),
        )
        dry_output = StringIO()

        call_command(
            "cleanup_event_checkout_claims",
            minutes=10,
            limit=20,
            stdout=dry_output,
        )

        self.assertIn(
            "Legacy orphan PENDING event payments [DRY-RUN]: 1",
            dry_output.getvalue(),
        )
        apply_output = StringIO()
        with patch(
            "crush_lu.management.commands.cleanup_event_checkout_claims."
            "SumUpClient.ensure_checkout_not_payable",
            return_value=True,
        ) as ensure_not_payable:
            call_command(
                "cleanup_event_checkout_claims",
                apply=True,
                minutes=10,
                limit=20,
                stdout=apply_output,
            )

        ensure_not_payable.assert_called_once_with("CHK-LEGACY-ORPHAN-CLEANUP")
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentTransaction.Status.CANCELLED)
        self.assertIn("Legacy orphan", payment.failure_reason)
        self.assertIn("Retired 1 legacy orphan payment(s)", apply_output.getvalue())

    def test_sumup_checkout_retirement_accepts_404_and_terminal_failed(self):
        from crush_lu.services.sumup import SumUpClient

        client = SumUpClient(api_key="test-api-key")
        not_found = Mock(status_code=404)
        not_found.raise_for_status.side_effect = HTTPError(response=not_found)

        with (
            patch(
                "crush_lu.services.sumup.requests.delete",
                return_value=not_found,
            ) as delete,
            patch.object(client, "get_checkout") as get_checkout,
        ):
            self.assertTrue(client.ensure_checkout_not_payable("CHK-ALREADY-GONE"))

        delete.assert_called_once()
        get_checkout.assert_not_called()

        with (
            patch.object(
                client,
                "deactivate_checkout",
                return_value=False,
            ) as deactivate,
            patch.object(
                client,
                "get_checkout",
                return_value={"id": "CHK-FAILED", "status": "FAILED"},
            ) as get_checkout,
        ):
            self.assertTrue(client.ensure_checkout_not_payable("CHK-FAILED"))

        deactivate.assert_called_once_with("CHK-FAILED")
        get_checkout.assert_called_once_with("CHK-FAILED")

    def test_live_checkout_state_protects_registration_and_event_deletion(self):
        event = self.make_event()
        registration = self.make_applicants(event, 1)[0]
        payment = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-PROTECTED-DELETE",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK-PROTECTED-DELETE",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=registration.user,
            event_registration=registration,
        )
        claim = EventCheckoutCreationClaim.objects.create(
            registration=registration,
            registration_id_snapshot=registration.pk,
            event_id_snapshot=event.pk,
            transaction_reference=payment.transaction_reference,
            payment_method="card",
            provider_checkout_id=payment.sumup_checkout_id,
        )

        with self.assertRaises(ProtectedError) as registration_error:
            with transaction.atomic():
                registration.delete()

        self.assertIn(payment, registration_error.exception.protected_objects)
        self.assertIn(claim, registration_error.exception.protected_objects)
        self.assertTrue(EventRegistration.objects.filter(pk=registration.pk).exists())

        with self.assertRaises(ProtectedError) as event_error:
            with transaction.atomic():
                event.delete()

        self.assertIn(payment, event_error.exception.protected_objects)
        self.assertIn(claim, event_error.exception.protected_objects)
        self.assertTrue(MeetupEvent.objects.filter(pk=event.pk).exists())
        self.assertTrue(EventRegistration.objects.filter(pk=registration.pk).exists())
        self.assertTrue(PaymentTransaction.objects.filter(pk=payment.pk).exists())
        self.assertTrue(EventCheckoutCreationClaim.objects.filter(pk=claim.pk).exists())

    def test_deletion_in_progress_middleware_allows_only_retry_routes(self):
        from crush_lu.consent_middleware import CrushConsentMiddleware

        event = self.make_event()
        user = self.make_applicants(event, 1)[0].user
        consent = UserDataConsent.objects.get(user=user)
        consent.crushlu_consent_given = False
        consent.crushlu_banned = True
        consent.crushlu_ban_reason = "deletion_in_progress"
        consent.save(
            update_fields=[
                "crushlu_consent_given",
                "crushlu_banned",
                "crushlu_ban_reason",
            ]
        )
        # Match a real authenticated request, whose User is loaded after the
        # consent row. The creation signal may have cached the original
        # reverse OneToOne value on this test fixture's in-memory User.
        user = User.objects.get(pk=user.pk)
        middleware = CrushConsentMiddleware(
            lambda request: HttpResponse(f"allowed:{request.path}")
        )

        for path in (
            "/account/delete/",
            "/account/delete-profile/",
            "/account/gdpr/",
            "/fr/account/delete-profile/",
        ):
            with self.subTest(path=path):
                request = RequestFactory().get(path)
                request.user = user
                request.urlconf = "azureproject.urls_crush"

                response = middleware(request)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content.decode(), f"allowed:{path}")

        for path in (
            "/events/",
            "/account/settings/",
            "/account/delete-profile/extra/",
        ):
            with self.subTest(path=path):
                request = RequestFactory().get(path)
                request.user = user
                request.urlconf = "azureproject.urls_crush"

                response = middleware(request)

                self.assertEqual(response.status_code, 302)
                self.assertIn("/account/banned/", response["Location"])

    def test_account_deletion_view_resumes_after_profile_was_erased(self):
        event = self.make_event()
        registration = self.make_applicants(event, 1)[0]
        user = registration.user
        self.client.force_login(user)
        deletion_url = reverse("crush_lu:delete_crushlu_profile")

        with patch(
            "crush_lu.storage.delete_user_storage",
            side_effect=[RuntimeError("storage unavailable"), (True, 0)],
        ) as delete_storage:
            first_response = self.client.post(
                deletion_url,
                {"confirm_email": user.email},
            )

            self.assertRedirects(
                first_response,
                deletion_url,
                fetch_redirect_response=False,
            )
            self.assertFalse(CrushProfile.objects.filter(user=user).exists())
            self.assertTrue(
                EventRegistration.objects.filter(pk=registration.pk).exists()
            )
            consent = UserDataConsent.objects.get(user=user)
            self.assertTrue(consent.crushlu_banned)
            self.assertEqual(consent.crushlu_ban_reason, "deletion_in_progress")

            retry_page = self.client.get(deletion_url)
            self.assertEqual(retry_page.status_code, 200)

            retry_response = self.client.post(
                deletion_url,
                {"confirm_email": user.email},
            )

        self.assertRedirects(
            retry_response,
            reverse("crush_lu:account_settings"),
            fetch_redirect_response=False,
        )
        self.assertEqual(delete_storage.call_count, 2)
        self.assertFalse(EventRegistration.objects.filter(pk=registration.pk).exists())
        consent.refresh_from_db()
        self.assertTrue(consent.crushlu_banned)
        self.assertEqual(consent.crushlu_ban_reason, "user_deletion")

    def test_profileless_account_cannot_start_unstaged_deletion(self):
        event = self.make_event()
        user = self.make_applicants(event, 1)[0].user
        CrushProfile.objects.filter(user=user).delete()
        self.client.force_login(user)

        with patch("crush_lu.views_account.delete_crushlu_profile_only") as delete:
            response = self.client.post(
                reverse("crush_lu:delete_crushlu_profile"),
                {"confirm_email": user.email},
            )

        self.assertRedirects(
            response,
            reverse("crush_lu:account_settings"),
            fetch_redirect_response=False,
        )
        delete.assert_not_called()
        consent = UserDataConsent.objects.get(user=user)
        self.assertFalse(consent.crushlu_banned)

    @patch("crush_lu.storage.delete_user_storage", return_value=(True, 0))
    def test_account_deletion_aborts_before_erasure_for_blank_checkout_claim(
        self, delete_storage
    ):
        from crush_lu.views_account import delete_crushlu_profile_only

        event = self.make_event()
        registration = self.make_applicants(event, 1)[0]
        user = registration.user
        claim = EventCheckoutCreationClaim.objects.create(
            registration=registration,
            registration_id_snapshot=registration.pk,
            event_id_snapshot=event.pk,
            transaction_reference="CRUSH-EVT-DELETE-AMBIGUOUS",
            payment_method="card",
        )

        with patch(
            "crush_lu.services.sumup.SumUpClient.deactivate_checkout"
        ) as deactivate:
            with self.assertRaisesMessage(
                RuntimeError, "provider outcome is not yet known"
            ):
                delete_crushlu_profile_only(user)

        deactivate.assert_not_called()
        delete_storage.assert_not_called()
        self.assertTrue(CrushProfile.objects.filter(user=user).exists())
        self.assertTrue(EventRegistration.objects.filter(pk=registration.pk).exists())
        claim.refresh_from_db()
        self.assertEqual(claim.state, EventCheckoutCreationClaim.State.ACTIVE)
        consent = UserDataConsent.objects.get(user=user)
        self.assertTrue(consent.crushlu_banned)
        self.assertEqual(consent.crushlu_ban_reason, "deletion_in_progress")

    @patch("crush_lu.storage.delete_user_storage", return_value=(True, 0))
    def test_account_deletion_deactivates_known_checkout_before_erasure(
        self, delete_storage
    ):
        from crush_lu.views_account import delete_crushlu_profile_only

        event = self.make_event()
        registration = self.make_applicants(event, 1)[0]
        user = registration.user
        payment = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-DELETE-KNOWN",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK-DELETE-KNOWN",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=user,
            event_registration=registration,
        )
        claim = EventCheckoutCreationClaim.objects.create(
            registration=registration,
            registration_id_snapshot=registration.pk,
            event_id_snapshot=event.pk,
            transaction_reference=payment.transaction_reference,
            payment_method="card",
            provider_checkout_id=payment.sumup_checkout_id,
        )

        with patch(
            "crush_lu.services.sumup.SumUpClient.deactivate_checkout",
            return_value=True,
        ) as deactivate:
            delete_crushlu_profile_only(user)

        deactivate.assert_called_once_with("CHK-DELETE-KNOWN")
        delete_storage.assert_called_once_with(user.pk)
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentTransaction.Status.CANCELLED)
        self.assertIn("deactivated", payment.failure_reason)
        self.assertIsNone(payment.event_registration_id)
        claim.refresh_from_db()
        self.assertEqual(claim.state, EventCheckoutCreationClaim.State.RETIRED)
        self.assertIsNone(claim.registration_id)
        self.assertEqual(claim.provider_checkout_id, "CHK-DELETE-KNOWN")
        self.assertFalse(EventRegistration.objects.filter(pk=registration.pk).exists())
        self.assertFalse(CrushProfile.objects.filter(user=user).exists())

    @patch("crush_lu.storage.delete_user_storage", return_value=(True, 0))
    def test_account_deletion_retry_accepts_preexisting_retired_claim(
        self, delete_storage
    ):
        from crush_lu.views_account import delete_crushlu_profile_only

        event = self.make_event()
        registration = self.make_applicants(event, 1)[0]
        user = registration.user
        claim = EventCheckoutCreationClaim.objects.create(
            registration=registration,
            registration_id_snapshot=registration.pk,
            event_id_snapshot=event.pk,
            transaction_reference="CRUSH-EVT-DELETE-RETRY",
            payment_method="card",
            state=EventCheckoutCreationClaim.State.RETIRED,
        )

        with patch(
            "crush_lu.services.sumup.SumUpClient.ensure_checkout_not_payable"
        ) as ensure_not_payable:
            delete_crushlu_profile_only(user)

        ensure_not_payable.assert_not_called()
        delete_storage.assert_called_once_with(user.pk)
        claim.refresh_from_db()
        self.assertEqual(claim.state, EventCheckoutCreationClaim.State.RETIRED)
        self.assertIsNone(claim.registration_id)
        self.assertFalse(EventRegistration.objects.filter(pk=registration.pk).exists())
        self.assertFalse(CrushProfile.objects.filter(user=user).exists())
        consent = UserDataConsent.objects.get(user=user)
        self.assertTrue(consent.crushlu_banned)
        self.assertEqual(consent.crushlu_ban_reason, "user_deletion")

    @patch("crush_lu.storage.delete_user_storage", return_value=(True, 0))
    def test_account_deletion_retires_legacy_orphan_pending_checkout(
        self, delete_storage
    ):
        from crush_lu.views_account import delete_crushlu_profile_only

        event = self.make_event()
        registration = self.make_applicants(event, 1)[0]
        user = registration.user
        payment = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-DELETE-LEGACY-ORPHAN",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK-DELETE-LEGACY-ORPHAN",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=user,
            event_registration=registration,
        )
        # Reproduce production data created before registration/event deletion
        # was protected by this PR.
        PaymentTransaction.objects.filter(pk=payment.pk).update(event_registration=None)

        with patch(
            "crush_lu.services.sumup.SumUpClient.ensure_checkout_not_payable",
            return_value=True,
        ) as ensure_not_payable:
            delete_crushlu_profile_only(user)

        ensure_not_payable.assert_called_once_with("CHK-DELETE-LEGACY-ORPHAN")
        delete_storage.assert_called_once_with(user.pk)
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentTransaction.Status.CANCELLED)
        self.assertIn("deactivated", payment.failure_reason)
        self.assertFalse(CrushProfile.objects.filter(user=user).exists())

    def test_legacy_orphan_capture_records_full_refundable_value(self):
        event = self.make_event()
        registration = self.make_applicants(event, 1)[0]
        payment = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-CAPTURE-LEGACY-ORPHAN",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK-CAPTURE-LEGACY-ORPHAN",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=registration.user,
            event_registration=registration,
        )
        PaymentTransaction.objects.filter(pk=payment.pk).update(event_registration=None)
        payment.refresh_from_db()

        _apply_paid_checkout(
            payment, {"id": payment.sumup_checkout_id, "status": "PAID"}
        )

        payment.refresh_from_db()
        credit = CrushCredit.objects.get(source_payment=payment)
        self.assertEqual(payment.status, PaymentTransaction.Status.PAID)
        self.assertIn("refundable Crush Credit", payment.failure_reason)
        self.assertEqual(credit.amount_cents, 1500)
        self.assertTrue(credit.cash_refund_eligible)

    def test_curated_price_drift_capture_returns_full_value_and_no_seat(self):
        event, registrations, group = self.certify_one_group()
        registration = next(
            registration
            for registration in registrations
            if group.memberships.filter(registration=registration).exists()
        )
        payment = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-CURATED-PRICE-DRIFT",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK-CURATED-PRICE-DRIFT",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=registration.user,
            event_registration=registration,
        )
        # Bypass model validation to prove the callback remains safe even for
        # a legacy/scripted event edit.
        MeetupEvent.objects.filter(pk=event.pk).update(
            registration_fee=Decimal("20.00")
        )

        with patch(
            "crush_lu.services.curated_group_notifications."
            "deliver_curated_group_notifications"
        ) as deliver:
            with self.captureOnCommitCallbacks(execute=True):
                _apply_paid_checkout(payment, {"status": "PAID"})

        registration.refresh_from_db()
        payment.refresh_from_db()
        credit = CrushCredit.objects.get(source_payment=payment)
        notice = CuratedGroupNotification.objects.get(source_payment=payment)
        self.assertEqual(payment.status, PaymentTransaction.Status.PAID)
        self.assertEqual(registration.status, "applied")
        self.assertFalse(registration.payment_confirmed)
        self.assertEqual(credit.amount_cents, 1500)
        self.assertTrue(credit.cash_refund_eligible)
        self.assertEqual(notice.status, CuratedGroupNotification.Status.PENDING)
        deliver.assert_called_once()

    def test_degraded_group_retires_pending_checkout_before_roster_release(self):
        event, registrations, group = self.certify_one_group()
        selected = registrations[2]
        payment = PaymentTransaction.objects.create(
            transaction_reference="CURATED-PENDING-RETIRE",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK-CURATED-PENDING-RETIRE",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=selected.user,
            event_registration=selected,
        )
        self.degrade_group_without_replacement(group, registrations)
        provider_atomic_depths = []

        def provider_retirement(_checkout_id):
            provider_atomic_depths.append(
                sum(
                    not getattr(block, "_from_testcase", False)
                    for block in transaction.get_connection().atomic_blocks
                )
            )
            return True

        with (
            patch(
                "crush_lu.services.sumup.SumUpClient.ensure_checkout_not_payable",
                side_effect=provider_retirement,
            ) as ensure_not_payable,
            patch(
                "crush_lu.services.curated_group_notifications."
                "deliver_curated_group_notifications"
            ) as deliver,
        ):
            with self.captureOnCommitCallbacks(execute=True):
                remedy = repair_degraded_event_groups(event)

        ensure_not_payable.assert_called_once_with(payment.sumup_checkout_id)
        self.assertEqual(provider_atomic_depths, [0])
        self.assertEqual(remedy.action, "compensated")
        group.refresh_from_db()
        payment.refresh_from_db()
        selected.refresh_from_db()
        self.assertEqual(group.status, CuratedEventGroup.STATUS_CANCELLED)
        self.assertEqual(payment.status, PaymentTransaction.Status.CANCELLED)
        self.assertEqual(selected.status, "applied")
        self.assertFalse(selected.payment_confirmed)
        self.assertTrue(
            CuratedGroupNotification.objects.filter(
                registration=selected,
                kind=CuratedGroupNotification.Kind.WITHDRAWAL,
                status=CuratedGroupNotification.Status.PENDING,
            ).exists()
        )
        deliver.assert_called_once()

    def test_degraded_group_stays_frozen_when_checkout_cannot_be_retired(self):
        event, registrations, group = self.certify_one_group()
        selected = registrations[2]
        payment = PaymentTransaction.objects.create(
            transaction_reference="CURATED-PENDING-BLOCKED",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK-CURATED-PENDING-BLOCKED",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=selected.user,
            event_registration=selected,
        )
        self.degrade_group_without_replacement(group, registrations)

        with patch(
            "crush_lu.services.sumup.SumUpClient.ensure_checkout_not_payable",
            return_value=False,
        ):
            with self.assertRaisesMessage(
                ValidationError, "provider checkouts not proven closed"
            ):
                repair_degraded_event_groups(event)

        group.refresh_from_db()
        payment.refresh_from_db()
        selected.refresh_from_db()
        self.assertEqual(group.status, CuratedEventGroup.STATUS_DEGRADED)
        self.assertEqual(payment.status, PaymentTransaction.Status.PENDING)
        self.assertEqual(selected.status, "pending")
        self.assertTrue(
            group.memberships.filter(
                registration=selected, released_at__isnull=True
            ).exists()
        )
        self.assertFalse(CuratedGroupNotification.objects.filter(event=event).exists())

    def test_capture_racing_retirement_is_compensated_instead_of_blocked(self):
        event, registrations, group = self.certify_one_group()
        selected = registrations[2]
        payment = PaymentTransaction.objects.create(
            transaction_reference="CURATED-PENDING-RACE",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK-CURATED-PENDING-RACE",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=selected.user,
            event_registration=selected,
        )
        self.degrade_group_without_replacement(group, registrations)

        def captured_before_revalidation(_checkout_id):
            PaymentTransaction.objects.filter(pk=payment.pk).update(
                status=PaymentTransaction.Status.PAID,
                paid_at=timezone.now(),
            )
            EventRegistration.objects.filter(pk=selected.pk).update(
                status="confirmed",
                payment_confirmed=True,
                payment_date=timezone.now(),
            )
            return False

        with (
            patch(
                "crush_lu.services.sumup.SumUpClient.ensure_checkout_not_payable",
                side_effect=captured_before_revalidation,
            ),
            patch(
                "crush_lu.services.curated_group_notifications."
                "deliver_curated_group_notifications"
            ),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                remedy = repair_degraded_event_groups(event)

        payment.refresh_from_db()
        group.refresh_from_db()
        self.assertEqual(payment.status, PaymentTransaction.Status.PAID)
        self.assertEqual(group.status, CuratedEventGroup.STATUS_CANCELLED)
        self.assertEqual(remedy.compensated_registration_ids, (selected.pk,))
        credit = CrushCredit.objects.get(source_payment=payment)
        self.assertEqual(credit.amount_cents, 1500)
        self.assertTrue(credit.cash_refund_eligible)

    def test_ambiguous_providerless_claim_blocks_roster_release(self):
        event, registrations, group = self.certify_one_group()
        selected = registrations[2]
        claim = EventCheckoutCreationClaim.objects.create(
            registration=selected,
            registration_id_snapshot=selected.pk,
            event_id_snapshot=event.pk,
            transaction_reference="CURATED-AMBIGUOUS-CLAIM",
            payment_method="card",
        )
        self.degrade_group_without_replacement(group, registrations)

        with patch(
            "crush_lu.services.sumup.SumUpClient.ensure_checkout_not_payable"
        ) as ensure_not_payable:
            with self.assertRaisesMessage(
                ValidationError, "ambiguous checkout claims without a provider ID"
            ):
                repair_degraded_event_groups(event)

        ensure_not_payable.assert_not_called()
        group.refresh_from_db()
        claim.refresh_from_db()
        selected.refresh_from_db()
        self.assertEqual(group.status, CuratedEventGroup.STATUS_DEGRADED)
        self.assertEqual(claim.state, EventCheckoutCreationClaim.State.ACTIVE)
        self.assertEqual(selected.status, "pending")

    def test_failed_repair_does_not_steal_an_existing_retiring_claim_lease(self):
        event, registrations, group = self.certify_one_group()
        selected = registrations[2]
        claim = EventCheckoutCreationClaim.objects.create(
            registration=selected,
            registration_id_snapshot=selected.pk,
            event_id_snapshot=event.pk,
            transaction_reference="CURATED-OTHER-RETIREMENT-LEASE",
            payment_method="card",
            provider_checkout_id="CHK-CURATED-OTHER-LEASE",
            state=EventCheckoutCreationClaim.State.RETIRING,
        )
        self.degrade_group_without_replacement(group, registrations)

        with patch(
            "crush_lu.services.sumup.SumUpClient.ensure_checkout_not_payable",
            return_value=False,
        ):
            with self.assertRaisesMessage(
                ValidationError, "provider checkouts not proven closed"
            ):
                repair_degraded_event_groups(event)

        claim.refresh_from_db()
        group.refresh_from_db()
        self.assertEqual(claim.state, EventCheckoutCreationClaim.State.RETIRING)
        self.assertEqual(group.status, CuratedEventGroup.STATUS_DEGRADED)

    def test_late_canceller_is_not_paid_group_remedy_but_innocent_payers_are(self):
        event, registrations, group = self.certify_one_group(
            event_overrides={"date_time": timezone.now() + timedelta(hours=24)}
        )
        payments = self.mark_group_paid(registrations)
        departing = registrations[0]

        # Exercise the real post-commit signal chain while suppressing only
        # delivery.  The generic late-cancellation callback runs first, then
        # the degraded-group callback decides that five people cannot form a
        # viable group and compensates only the innocent payers.
        with (
            patch("crush_lu.views_payments._send_member_cancellation_safely"),
            patch(
                "crush_lu.services.curated_group_notifications."
                "deliver_curated_group_notifications"
            ),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                departing.status = "cancelled"
                departing.save(update_fields=["status"])
        group.refresh_from_db()
        self.assertEqual(group.status, CuratedEventGroup.STATUS_CANCELLED)

        innocent_ids = {registration.pk for registration in registrations[1:]}
        self.assertFalse(
            CrushCredit.objects.filter(
                source_payment=payments[departing.pk],
                reason=CrushCredit.Reason.CURATED_GROUP_UNAVAILABLE,
            ).exists()
        )
        innocent_credits = CrushCredit.objects.filter(
            source_payment_id__in=[payments[pk].pk for pk in innocent_ids],
            reason=CrushCredit.Reason.CURATED_GROUP_UNAVAILABLE,
        )
        self.assertEqual(innocent_credits.count(), 5)
        self.assertEqual(
            sum(credit.amount_cents for credit in innocent_credits),
            5 * 1500,
        )
        self.assertTrue(all(credit.cash_refund_eligible for credit in innocent_credits))

    def test_post_start_locked_erasure_is_audit_only_and_issues_no_credit(self):
        event, registrations, group = self.certify_one_group()
        payments = self.mark_group_paid(registrations, status="attended")
        MeetupEvent.objects.filter(pk=event.pk).update(
            date_time=timezone.now() - timedelta(minutes=10)
        )
        event.refresh_from_db()
        actor = self.admin_request().user

        locked_ids = lock_current_generation(event, actor=actor)

        group.refresh_from_db()
        event.refresh_from_db()
        self.assertEqual(locked_ids, (group.pk,))
        self.assertEqual(group.status, CuratedEventGroup.STATUS_LOCKED)
        self.assertIsNone(event.curated_rounds_started_at)

        before_start = timezone.now()
        started_ids = start_curated_rounds(event, actor=actor)
        after_start = timezone.now()
        event.refresh_from_db()
        self.assertEqual(started_ids, (group.pk,))
        self.assertLess(event.date_time, event.curated_rounds_started_at)
        self.assertGreaterEqual(event.curated_rounds_started_at, before_start)
        self.assertLessEqual(event.curated_rounds_started_at, after_start)
        self.assertEqual(event.curated_rounds_started_by_id, actor.pk)
        erased = registrations[0]

        with self.captureOnCommitCallbacks(execute=True):
            erased.delete()

        group.refresh_from_db()
        remedy = repair_degraded_event_groups(event)
        self.assertEqual(group.status, CuratedEventGroup.STATUS_DEGRADED)
        self.assertEqual(
            group.audit_data["degradation"]["from_status"],
            CuratedEventGroup.STATUS_LOCKED,
        )
        self.assertEqual(remedy.action, "post_start_audit_only")
        self.assertEqual(remedy.compensated_registration_ids, ())
        self.assertEqual(remedy.credit_ids, ())
        self.assertFalse(
            CrushCredit.objects.filter(
                source_payment_id__in=[payment.pk for payment in payments.values()],
                reason=CrushCredit.Reason.CURATED_GROUP_UNAVAILABLE,
            ).exists()
        )

    def test_locked_prestart_erasure_compensates_even_after_scheduled_start(self):
        event, registrations, group = self.certify_one_group()
        payments = self.mark_group_paid(registrations, status="attended")
        MeetupEvent.objects.filter(pk=event.pk).update(
            date_time=timezone.now() - timedelta(minutes=10)
        )
        event.refresh_from_db()

        lock_current_generation(event)
        group.refresh_from_db()
        event.refresh_from_db()
        self.assertEqual(group.status, CuratedEventGroup.STATUS_LOCKED)
        self.assertIsNone(event.curated_rounds_started_at)
        departing = registrations[0]
        departing_id = departing.pk
        departing_payment = payments[departing_id]

        with patch(
            "crush_lu.services.curated_group_notifications."
            "deliver_curated_group_notifications"
        ):
            with self.captureOnCommitCallbacks(execute=True):
                departing.delete()

        group.refresh_from_db()
        self.assertEqual(group.status, CuratedEventGroup.STATUS_CANCELLED)
        self.assertFalse(
            CrushCredit.objects.filter(
                source_payment=departing_payment,
                reason=CrushCredit.Reason.CURATED_GROUP_UNAVAILABLE,
            ).exists()
        )
        innocent_ids = {registration.pk for registration in registrations[1:]}
        credits = CrushCredit.objects.filter(
            source_payment_id__in=[payments[pk].pk for pk in innocent_ids],
            reason=CrushCredit.Reason.CURATED_GROUP_UNAVAILABLE,
        )
        self.assertEqual(credits.count(), 5)
        self.assertTrue(all(credit.cash_refund_eligible for credit in credits))

    def test_lock_refuses_an_unresolved_degraded_group(self):
        event, registrations, group = self.certify_one_group()
        EventRegistration.objects.filter(
            pk__in=[registration.pk for registration in registrations]
        ).update(status="attended")
        group.degrade_for_reprojection(
            reason=CuratedEventGroup.DEGRADATION_REASON_INTEGRITY,
        )

        with self.assertRaisesMessage(
            ValidationError,
            "Resolve every degraded group before locking",
        ):
            lock_current_generation(event)

        group.refresh_from_db()
        event.refresh_from_db()
        self.assertEqual(group.status, CuratedEventGroup.STATUS_DEGRADED)
        self.assertIsNone(event.curated_rounds_started_at)
        self.assertFalse(
            CuratedEventGroup.objects.filter(
                event=event,
                status=CuratedEventGroup.STATUS_LOCKED,
            ).exists()
        )

    def test_no_show_late_capture_records_payment_without_group_remedy(self):
        _event, registrations, group = self.certify_one_group()
        payer = registrations[0]
        payment = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-NO-SHOW-LATE-CAPTURE",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK-NO-SHOW-LATE-CAPTURE",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=payer.user,
            event_registration=payer,
        )
        payer.status = "no_show"
        payer.save(update_fields=["status"])
        group.refresh_from_db()
        self.assertEqual(group.status, CuratedEventGroup.STATUS_DEGRADED)

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            _apply_paid_checkout(
                payment,
                {"id": payment.sumup_checkout_id, "status": "PAID"},
            )

        payment.refresh_from_db()
        payer.refresh_from_db()
        self.assertEqual(callbacks, [])
        self.assertEqual(payment.status, PaymentTransaction.Status.PAID)
        self.assertEqual(payer.status, "no_show")
        self.assertTrue(payer.payment_confirmed)
        self.assertFalse(
            CrushCredit.objects.filter(
                source_payment=payment,
                reason=CrushCredit.Reason.CURATED_GROUP_UNAVAILABLE,
            ).exists()
        )

    def test_attended_post_start_degraded_late_capture_is_accepted(self):
        event, registrations, group = self.certify_one_group()
        payer = registrations[0]
        payment = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-ATTENDED-LATE-CAPTURE",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK-ATTENDED-LATE-CAPTURE",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=payer.user,
            event_registration=payer,
        )
        EventRegistration.objects.filter(
            pk__in=[registration.pk for registration in registrations]
        ).update(status="attended")
        MeetupEvent.objects.filter(pk=event.pk).update(
            date_time=timezone.now() - timedelta(minutes=10)
        )
        lock_current_generation(event)
        start_curated_rounds(event)
        group.refresh_from_db()
        group.degrade_for_reprojection(
            reason=CuratedEventGroup.DEGRADATION_REASON_INTEGRITY,
        )
        group.refresh_from_db()
        self.assertEqual(group.status, CuratedEventGroup.STATUS_DEGRADED)
        self.assertEqual(
            group.audit_data["degradation"]["from_status"],
            CuratedEventGroup.STATUS_LOCKED,
        )

        with patch(
            "crush_lu.views_payments._send_registration_confirmation_safely"
        ) as send_confirmation:
            with self.captureOnCommitCallbacks(execute=True):
                _apply_paid_checkout(
                    payment,
                    {"id": payment.sumup_checkout_id, "status": "PAID"},
                )

        payment.refresh_from_db()
        payer.refresh_from_db()
        self.assertEqual(payment.status, PaymentTransaction.Status.PAID)
        self.assertEqual(payer.status, "attended")
        self.assertTrue(payer.payment_confirmed)
        self.assertFalse(
            CrushCredit.objects.filter(
                source_payment=payment,
                reason=CrushCredit.Reason.CURATED_GROUP_UNAVAILABLE,
            ).exists()
        )
        send_confirmation.assert_called_once()

    def test_post_start_provisional_no_show_compensates_only_remaining_payers(self):
        event, registrations, group = self.certify_one_group()
        payments = self.mark_group_paid(registrations)
        MeetupEvent.objects.filter(pk=event.pk).update(
            date_time=timezone.now() - timedelta(minutes=10)
        )
        no_show = registrations[0]

        with patch(
            "crush_lu.services.curated_group_notifications."
            "deliver_curated_group_notifications"
        ):
            with self.captureOnCommitCallbacks(execute=True):
                no_show.status = "no_show"
                no_show.save(update_fields=["status"])

        group.refresh_from_db()
        innocent_ids = {registration.pk for registration in registrations[1:]}
        self.assertEqual(group.status, CuratedEventGroup.STATUS_CANCELLED)
        self.assertFalse(
            CrushCredit.objects.filter(
                source_payment=payments[no_show.pk],
                reason=CrushCredit.Reason.CURATED_GROUP_UNAVAILABLE,
            ).exists()
        )
        credits = CrushCredit.objects.filter(
            source_payment_id__in=[payments[pk].pk for pk in innocent_ids],
            reason=CrushCredit.Reason.CURATED_GROUP_UNAVAILABLE,
        )
        self.assertEqual(credits.count(), 5)
        self.assertEqual(sum(credit.amount_cents for credit in credits), 5 * 1500)
        self.assertTrue(all(credit.cash_refund_eligible for credit in credits))

    def test_gdpr_export_contains_only_members_own_schedule_coordinates(self):
        _event, registrations, group = self.certify_one_group()
        member = next(
            registration
            for registration in registrations
            if group.memberships.filter(registration=registration).exists()
        )
        partners = [
            registration for registration in registrations if registration != member
        ]
        expected_schedule = [
            {
                "round": participant.round_number,
                "table": participant.pairing.table_number,
                "seat": participant.seat,
            }
            for participant in member.curated_pairing_participations.select_related(
                "pairing"
            ).order_by("round_number", "pairing__table_number", "pk")
        ]
        self.client.force_login(member.user)

        response = self.client.get(reverse("crush_lu:export_user_data"))

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        registration = next(
            entry
            for entry in payload["event_registrations"]
            if entry["event"] == "Elastic evening"
        )
        history = registration["curated_group_history"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["schedule"], expected_schedule)
        self.assertEqual(len(expected_schedule), 5)
        self.assertTrue(
            all(set(item) == {"round", "table", "seat"} for item in expected_schedule)
        )
        exported_group_data = json.dumps(history)
        for partner in partners:
            self.assertNotIn(partner.user.email, exported_group_data)
            self.assertNotIn(partner.user.first_name, exported_group_data)

    def test_stale_group_capture_remedy_mail_failure_is_retryable(self):
        from crush_lu.services.curated_group_notifications import (
            deliver_curated_group_notifications,
        )

        _event, registrations, group = self.certify_one_group()
        selected = [
            registration
            for registration in registrations
            if group.memberships.filter(registration=registration).exists()
        ]
        payer, departing = selected[:2]
        transaction_row = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-STALE-GROUP-RETRY",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK-STALE-GROUP-RETRY",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=payer.user,
            event_registration=payer,
        )
        departing.status = "cancelled"
        departing.save(update_fields=["status"])
        group.refresh_from_db()
        self.assertEqual(group.status, CuratedEventGroup.STATUS_DEGRADED)

        with patch(
            "crush_lu.email_helpers.send_curated_group_payment_remedy",
            return_value=0,
        ) as initial_delivery:
            with self.captureOnCommitCallbacks(execute=True):
                _apply_paid_checkout(
                    transaction_row,
                    {"id": transaction_row.sumup_checkout_id, "status": "PAID"},
                )

        credit = CrushCredit.objects.get(source_payment=transaction_row)
        notice = CuratedGroupNotification.objects.get(
            registration=payer,
            source_payment=transaction_row,
            kind=CuratedGroupNotification.Kind.REMEDY,
        )
        self.assertEqual(initial_delivery.call_count, 1)
        self.assertEqual(notice.status, CuratedGroupNotification.Status.PENDING)
        self.assertEqual(notice.attempt_count, 1)
        self.assertIn("returned false", notice.last_error)

        with patch(
            "crush_lu.email_helpers.send_curated_group_payment_remedy",
            return_value=1,
        ) as retry_delivery:
            retry = deliver_curated_group_notifications(
                registration_ids=[payer.pk],
                kinds=[CuratedGroupNotification.Kind.REMEDY],
            )

        self.assertEqual(retry.attempted, 1)
        self.assertEqual(retry.sent, 1)
        self.assertEqual(retry.failed, 0)
        self.assertEqual(retry.remaining, 0)
        retry_delivery.assert_called_once()
        self.assertEqual(retry_delivery.call_args.args[0].pk, payer.pk)
        self.assertEqual(
            [row.pk for row in retry_delivery.call_args.args[1]],
            [credit.pk],
        )
        notice.refresh_from_db()
        self.assertEqual(notice.status, CuratedGroupNotification.Status.SENT)
        self.assertEqual(notice.attempt_count, 2)

        with patch(
            "crush_lu.email_helpers.send_curated_group_payment_remedy"
        ) as duplicate_delivery:
            empty = deliver_curated_group_notifications(
                registration_ids=[payer.pk],
                kinds=[CuratedGroupNotification.Kind.REMEDY],
            )
        self.assertEqual(empty.attempted, 0)
        self.assertEqual(empty.remaining, 0)
        duplicate_delivery.assert_not_called()
        self.assertEqual(
            CrushCredit.objects.filter(source_payment=transaction_row).count(),
            1,
        )

    def test_stale_group_capture_returns_full_value_and_emails_in_french(self):
        _event, registrations, group = self.certify_one_group(preferred_language="fr")
        selected = [
            registration
            for registration in registrations
            if group.memberships.filter(registration=registration).exists()
        ]
        payer, departing = selected[:2]
        transaction_row = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-STALE-GROUP-CAPTURE",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK-STALE-GROUP-CAPTURE",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=payer.user,
            event_registration=payer,
        )
        departing.status = "cancelled"
        departing.save(update_fields=["status"])
        group.refresh_from_db()
        self.assertEqual(group.status, CuratedEventGroup.STATUS_DEGRADED)
        mail.outbox = []

        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            _apply_paid_checkout(
                transaction_row,
                {"id": transaction_row.sumup_checkout_id, "status": "PAID"},
            )
        self.assertEqual(mail.outbox, [])
        self.assertEqual(len(callbacks), 1)
        callbacks[0]()

        transaction_row.refresh_from_db()
        payer.refresh_from_db()
        credit = CrushCredit.objects.get(source_payment=transaction_row)
        self.assertEqual(transaction_row.status, PaymentTransaction.Status.PAID)
        self.assertEqual(payer.status, "applied")
        self.assertFalse(payer.payment_confirmed)
        self.assertEqual(credit.amount_cents, 1500)
        self.assertEqual(
            credit.reason,
            CrushCredit.Reason.CURATED_GROUP_UNAVAILABLE,
        )
        self.assertTrue(credit.cash_refund_eligible)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].subject,
            "Votre paiement pour Elastic evening a été restitué",
        )
        self.assertIn("Votre candidature reste dans le pool", mail.outbox[0].body)
        self.assertIn("https://crush.lu/fr/events/", mail.outbox[0].body)

    def test_stale_group_capture_returns_localized_remedy_in_german(self):
        _event, registrations, group = self.certify_one_group(preferred_language="de")
        selected = [
            registration
            for registration in registrations
            if group.memberships.filter(registration=registration).exists()
        ]
        payer, departing = selected[:2]
        transaction_row = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-STALE-GROUP-CAPTURE-DE",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK-STALE-GROUP-CAPTURE-DE",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=payer.user,
            event_registration=payer,
        )
        departing.status = "cancelled"
        departing.save(update_fields=["status"])
        group.refresh_from_db()
        self.assertEqual(group.status, CuratedEventGroup.STATUS_DEGRADED)
        mail.outbox = []

        with self.captureOnCommitCallbacks(execute=True):
            _apply_paid_checkout(
                transaction_row,
                {"id": transaction_row.sumup_checkout_id, "status": "PAID"},
            )

        notice = CuratedGroupNotification.objects.get(
            registration=payer,
            source_payment=transaction_row,
            kind=CuratedGroupNotification.Kind.REMEDY,
        )
        self.assertEqual(notice.status, CuratedGroupNotification.Status.SENT)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].subject,
            "Deine Zahlung für Elastic evening wurde zurückerstattet",
        )
        self.assertIn("Deine Bewerbung bleibt im Pool", mail.outbox[0].body)
        self.assertIn("https://crush.lu/de/events/", mail.outbox[0].body)
