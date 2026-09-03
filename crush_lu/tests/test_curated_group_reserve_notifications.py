"""Reserve notices for applicants left out of an invited curated generation.

Before this notice existed, someone who applied and was not placed in a group
kept reading "Your application is in!" for ever. The notice says only that
they stay in the pool: no reason, no headcount, and no claim that the decision
is final, because a later reprojection can still place them.
"""

import re
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from crush_lu.models.events import (
    CuratedEventGroup,
    CuratedEventGroupMembership,
    CuratedGroupNotification,
    EventRegistration,
    MeetupEvent,
)
from crush_lu.models.profiles import CrushProfile
from crush_lu.services.curated_group_notifications import (
    deliver_curated_group_notifications,
    enqueue_reserve_notifications,
)

User = get_user_model()


@override_settings(
    ROOT_URLCONF="azureproject.urls_crush",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class CuratedGroupReserveNotificationTests(TestCase):
    def setUp(self):
        self.event = MeetupEvent.objects.create(
            title="Elastic evening",
            description="A fair parallel speed-dating evening",
            event_type="speed_dating",
            registration_mode="curated",
            date_time=timezone.now() + timedelta(days=7),
            registration_deadline=timezone.now() - timedelta(hours=1),
            location="Luxembourg",
            address="Test venue",
            max_participants=12,
            group_size=6,
            planned_groups=1,
            registration_fee=Decimal("15.00"),
            profile_requirement="none",
            is_published=True,
        )

    def make_registration(self, suffix, *, language="en", status="applied"):
        user = User.objects.create_user(
            username=f"reserve-{suffix}@example.com",
            email=f"reserve-{suffix}@example.com",
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
        )
        if status != "applied":
            EventRegistration.objects.filter(pk=registration.pk).update(status=status)
            registration.refresh_from_db()
        return registration

    def make_provisional_group(self, registrations, *, generation=1):
        # The audited lifecycle only lets a group be born as a draft and child
        # rows freeze once it is provisional, so build the roster first and
        # flip the status underneath the model. These tests only need the
        # membership rows to *look* selected, not a certified schedule.
        group = CuratedEventGroup.objects.create(
            event=self.event,
            generation=generation,
            group_number=1,
            seed=f"reserve-test-{generation}",
            policy_version="reciprocal-graph-v1",
        )
        for position, registration in enumerate(registrations, start=1):
            CuratedEventGroupMembership.objects.create(
                event=self.event,
                group=group,
                registration=registration,
                position=position,
            )
        CuratedEventGroup.objects.filter(pk=group.pk).update(
            status=CuratedEventGroup.STATUS_PROVISIONAL,
            provisional_at=timezone.now(),
        )
        group.refresh_from_db()
        return group

    def test_enqueue_targets_only_open_applications_outside_the_generation(self):
        selected = self.make_registration("selected")
        left_out = self.make_registration("left-out")
        invited_now = self.make_registration("invited-now")
        withdrawn = self.make_registration("withdrawn", status="cancelled")
        self.make_provisional_group([selected])

        notice_ids = enqueue_reserve_notifications(
            self.event.pk,
            generation=1,
            exclude_registration_ids=[invited_now.pk],
        )

        notices = CuratedGroupNotification.objects.filter(pk__in=notice_ids)
        self.assertEqual(
            list(notices.values_list("registration_id", flat=True)),
            [left_out.pk],
        )
        notice = notices.get()
        self.assertEqual(notice.kind, CuratedGroupNotification.Kind.RESERVE)
        cycle = left_out.registered_at.strftime("%Y%m%d%H%M%S%f")
        self.assertEqual(
            notice.dedupe_key, f"reserve:{self.event.pk}:{left_out.pk}:{cycle}"
        )
        self.assertEqual(notice.payload, {"generation": 1, "application_cycle": cycle})
        self.assertFalse(
            CuratedGroupNotification.objects.filter(
                registration_id__in=[selected.pk, invited_now.pk, withdrawn.pk]
            ).exists()
        )

    def test_enqueue_is_idempotent_across_generations(self):
        left_out = self.make_registration("twice")

        first = enqueue_reserve_notifications(self.event.pk, generation=1)
        second = enqueue_reserve_notifications(self.event.pk, generation=2)

        self.assertEqual(first, second)
        self.assertEqual(
            CuratedGroupNotification.objects.filter(registration=left_out).count(), 1
        )

    def test_enqueue_does_not_return_a_notice_already_sent(self):
        left_out = self.make_registration("sent-before")
        first = enqueue_reserve_notifications(self.event.pk, generation=1)
        CuratedGroupNotification.objects.filter(pk__in=first).update(
            status=CuratedGroupNotification.Status.SENT
        )

        again = enqueue_reserve_notifications(self.event.pk, generation=2)

        self.assertEqual(again, [])
        self.assertEqual(
            CuratedGroupNotification.objects.filter(registration=left_out).count(), 1
        )

    def test_delivery_sends_a_reason_free_pool_notice(self):
        left_out = self.make_registration("english")
        notice_ids = enqueue_reserve_notifications(self.event.pk, generation=1)

        result = deliver_curated_group_notifications(
            notice_ids=notice_ids,
            kinds=[CuratedGroupNotification.Kind.RESERVE],
        )

        self.assertEqual((result.attempted, result.sent, result.failed), (1, 1, 0))
        notice = CuratedGroupNotification.objects.get(pk=notice_ids[0])
        self.assertEqual(notice.status, CuratedGroupNotification.Status.SENT)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [left_out.user.email])
        self.assertEqual(
            message.subject, "Your application for Elastic evening stays in the pool"
        )
        self.assertIn("No payment was taken", message.body)
        self.assertIn("we will contact you before any payment", message.body)
        # No reason is ever given: no shortage, no headcount, no demographics.
        self.assertIsNone(
            re.search(
                r"\b(gender|men|women|not enough|applicants|too few)\b",
                message.body.lower(),
            ),
            message.body,
        )

    def test_delivery_is_localized_for_french_and_german_members(self):
        self.make_registration("french", language="fr")
        self.make_registration("german", language="de")
        notice_ids = enqueue_reserve_notifications(self.event.pk, generation=1)

        deliver_curated_group_notifications(
            notice_ids=notice_ids,
            kinds=[CuratedGroupNotification.Kind.RESERVE],
        )

        subjects = sorted(message.subject for message in mail.outbox)
        self.assertEqual(
            subjects,
            [
                "Deine Bewerbung für Elastic evening bleibt im Pool",
                "Votre candidature pour Elastic evening reste dans le pool",
            ],
        )

    def test_delivery_is_stale_once_the_applicant_holds_a_current_place(self):
        placed_later = self.make_registration("placed-later")
        notice_ids = enqueue_reserve_notifications(self.event.pk, generation=1)
        self.make_provisional_group([placed_later], generation=2)

        result = deliver_curated_group_notifications(
            notice_ids=notice_ids,
            kinds=[CuratedGroupNotification.Kind.RESERVE],
        )

        self.assertEqual((result.sent, result.cancelled), (0, 1))
        self.assertEqual(mail.outbox, [])
        notice = CuratedGroupNotification.objects.get(pk=notice_ids[0])
        self.assertEqual(notice.status, CuratedGroupNotification.Status.CANCELLED)

    def test_reapplying_after_a_withdrawal_earns_a_fresh_notice(self):
        """The registration row is reused on reapply; the decision is new."""
        member = self.make_registration("again")
        first = enqueue_reserve_notifications(self.event.pk, generation=1)
        CuratedGroupNotification.objects.filter(pk__in=first).update(
            status=CuratedGroupNotification.Status.SENT
        )
        # Withdraw, then apply again: event_register resets registered_at on
        # the reused row.
        EventRegistration.objects.filter(pk=member.pk).update(
            status="cancelled",
        )
        EventRegistration.objects.filter(pk=member.pk).update(
            status="applied",
            registered_at=timezone.now() + timedelta(seconds=1),
        )

        second = enqueue_reserve_notifications(self.event.pk, generation=2)

        self.assertEqual(len(second), 1)
        self.assertNotEqual(second, first)
        self.assertEqual(
            CuratedGroupNotification.objects.filter(registration=member).count(), 2
        )
        result = deliver_curated_group_notifications(
            notice_ids=second,
            kinds=[CuratedGroupNotification.Kind.RESERVE],
        )
        self.assertEqual((result.sent, result.cancelled), (1, 0))

    def test_unsent_notice_from_an_earlier_application_is_stale(self):
        member = self.make_registration("earlier-cycle")
        notice_ids = enqueue_reserve_notifications(self.event.pk, generation=1)
        EventRegistration.objects.filter(pk=member.pk).update(
            registered_at=timezone.now() + timedelta(seconds=1),
        )

        result = deliver_curated_group_notifications(
            notice_ids=notice_ids,
            kinds=[CuratedGroupNotification.Kind.RESERVE],
        )

        self.assertEqual((result.sent, result.cancelled), (0, 1))
        self.assertEqual(mail.outbox, [])

    def test_delivery_is_stale_once_the_event_is_cancelled(self):
        self.make_registration("event-cancelled")
        notice_ids = enqueue_reserve_notifications(self.event.pk, generation=1)
        MeetupEvent.objects.filter(pk=self.event.pk).update(is_cancelled=True)

        result = deliver_curated_group_notifications(
            notice_ids=notice_ids,
            kinds=[CuratedGroupNotification.Kind.RESERVE],
        )

        self.assertEqual((result.sent, result.cancelled), (0, 1))
        self.assertEqual(mail.outbox, [])

    def test_delivery_is_stale_once_the_event_has_started(self):
        self.make_registration("event-started")
        notice_ids = enqueue_reserve_notifications(self.event.pk, generation=1)
        MeetupEvent.objects.filter(pk=self.event.pk).update(
            date_time=timezone.now() - timedelta(minutes=5)
        )

        result = deliver_curated_group_notifications(
            notice_ids=notice_ids,
            kinds=[CuratedGroupNotification.Kind.RESERVE],
        )

        self.assertEqual((result.sent, result.cancelled), (0, 1))
        self.assertEqual(mail.outbox, [])

    def test_delivery_is_stale_once_the_application_is_withdrawn(self):
        withdrawn_later = self.make_registration("withdrawn-later")
        notice_ids = enqueue_reserve_notifications(self.event.pk, generation=1)
        EventRegistration.objects.filter(pk=withdrawn_later.pk).update(
            status="cancelled"
        )

        result = deliver_curated_group_notifications(
            notice_ids=notice_ids,
            kinds=[CuratedGroupNotification.Kind.RESERVE],
        )

        self.assertEqual((result.sent, result.cancelled), (0, 1))
        self.assertEqual(mail.outbox, [])
