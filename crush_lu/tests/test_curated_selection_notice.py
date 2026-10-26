"""Curated selection notice: every selected member is told they have a place.

Gap found 2026-10-25 while preparing event 26 (free curated speed dating): on
a free event the Invite step selects members straight into "confirmed", and
the only notice was the registration confirmation email, gated on the "Event
Reminders" toggle. A selected member who had switched it off held a place in
a group and was never told. The outbox also retried that refused email
forever.

Selection hands the member a seat, like a waitlist promotion, so it now
follows the same rules: the email ignores the reminders toggle but honours the
ban and the master unsubscribe; a bell row is always written; push follows the
device's event-reminders switch. The bell row is claimed with the outbox
notice's dedupe key, so a retried email never repeats the bell or the push.

Run with: pytest crush_lu/tests/test_curated_selection_notice.py -v
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from crush_lu.models.events import (
    CuratedEventGroup,
    CuratedGroupNotification,
    EventRegistration,
    EventRegistrationPreference,
    MeetupEvent,
)
from crush_lu.models.profiles import CrushProfile, UserDataConsent
from crush_lu.services.curated_group_notifications import (
    deliver_curated_group_notifications,
    enqueue_selection_notifications,
)
from crush_lu.services.curated_group_workflow import (
    approve_current_generation,
    generate_group_projection,
)

User = get_user_model()

BELL_TYPE = "event_curated_selected"


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class CuratedSelectionNoticeTests(TestCase):
    def setUp(self):
        # Every test's viewer is the same user pk on SQLite and they share one
        # @ratelimit counter (AGENTS.md traps).
        cache.clear()

    def _select(self, *, fee="0.00", preferred_language="en"):
        """Certify one curated group of six and seat its members the way
        Invite does: "confirmed" on a free event, "pending" on a paid one."""
        event = MeetupEvent.objects.create(
            title="Selection Notice Evening",
            description="Testing the curated selection notice",
            event_type="speed_dating",
            registration_mode="curated",
            date_time=timezone.now() + timedelta(days=3),
            registration_deadline=timezone.now() - timedelta(hours=1),
            location="Luxembourg",
            address="Test venue",
            max_participants=12,
            group_size=6,
            planned_groups=1,
            registration_fee=Decimal(fee),
            profile_requirement="none",
            is_published=True,
        )
        for index in range(6):
            user = User.objects.create_user(
                username=f"selected-{index}@example.com",
                email=f"selected-{index}@example.com",
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
                event=event, user=user, status="applied"
            )
            EventRegistrationPreference.objects.create(
                registration=registration,
                preferred_genders=[],
                preferred_age_min=18,
                preferred_age_max=99,
                languages=[],
            )
        stored = generate_group_projection(event, deterministic_seed="selection")
        approved = approve_current_generation(event)
        seat_status = "confirmed" if Decimal(fee) == 0 else "pending"
        EventRegistration.objects.filter(
            pk__in=approved.applied_registration_ids
        ).update(status=seat_status)
        group = CuratedEventGroup.objects.get(pk=stored.group_ids[0])
        selected = list(
            EventRegistration.objects.filter(pk__in=approved.applied_registration_ids)
            .select_related("user")
            .order_by("pk")
        )
        enqueue_selection_notifications(
            {registration.pk: group.generation for registration in selected}
        )
        mail.outbox = []
        return event, selected

    def _deliver(self, selected):
        return deliver_curated_group_notifications(
            registration_ids=[registration.pk for registration in selected],
            kinds=[CuratedGroupNotification.Kind.SELECTION],
            limit=50,
        )

    def _mail_to(self, user):
        return [message for message in mail.outbox if user.email in message.to]

    def _bells(self, user):
        from crush_lu.models import Notification

        return Notification.objects.filter(user=user, notification_type=BELL_TYPE)

    def _notice(self, registration):
        return CuratedGroupNotification.objects.get(
            registration_id=registration.pk,
            kind=CuratedGroupNotification.Kind.SELECTION,
        )

    def _subscribe(self, user, *, event_reminders=True):
        from crush_lu.models import PushSubscription

        return PushSubscription.objects.create(
            user=user,
            endpoint=f"https://push.example.test/{user.pk}",
            p256dh_key="p256dh",
            auth_key="auth",
            notify_event_reminders=event_reminders,
        )

    def test_free_event_selection_reaches_a_member_with_event_reminders_off(self):
        from crush_lu.models import EmailPreference

        event, selected = self._select()
        member = selected[0]
        prefs = EmailPreference.get_or_create_for_user(member.user)
        prefs.email_event_reminders = False
        prefs.save()

        batch = self._deliver(selected)

        self.assertEqual(batch.sent, len(selected))
        self.assertEqual(batch.remaining, 0)
        messages = self._mail_to(member.user)
        self.assertEqual(len(messages), 1)
        self.assertIn("Event Registration Confirmed", messages[0].subject)
        self.assertIn(event.title, messages[0].subject)
        self.assertEqual(self._bells(member.user).count(), 1)
        self.assertEqual(
            self._notice(member).status, CuratedGroupNotification.Status.SENT
        )

    def test_every_selected_member_gets_one_bell_row(self):
        event, selected = self._select()

        self._deliver(selected)

        for registration in selected:
            bells = self._bells(registration.user)
            self.assertEqual(bells.count(), 1)
            bell = bells.get()
            self.assertIn("Good news: you have a place at", bell.title)
            self.assertIn(event.title, bell.title)
            self.assertIn("your place is confirmed", bell.body)
            self.assertEqual(bell.link_url, f"/events/{event.id}/")
            self.assertEqual(bell.metadata["registration_id"], registration.pk)

    def test_bell_is_in_the_members_language(self):
        _event, selected = self._select(preferred_language="fr")

        self._deliver(selected)

        bell = self._bells(selected[0].user).get()
        self.assertIn("Bonne nouvelle", bell.title)
        self.assertIn("votre place est confirmée", bell.body)

    def test_master_unsubscribe_closes_the_notice_with_the_bell_only(self):
        from crush_lu.models import EmailPreference

        _event, selected = self._select()
        member = selected[0]
        prefs = EmailPreference.get_or_create_for_user(member.user)
        prefs.unsubscribed_all = True
        prefs.save()

        first = self._deliver(selected)
        second = self._deliver(selected)

        self.assertEqual(self._mail_to(member.user), [])
        self.assertEqual(self._bells(member.user).count(), 1)
        notice = self._notice(member)
        self.assertEqual(notice.status, CuratedGroupNotification.Status.SENT)
        self.assertIn("Email not allowed", notice.last_error)
        # A refused email is not retried forever.
        self.assertEqual(notice.attempt_count, 1)
        self.assertEqual(first.remaining, 0)
        self.assertEqual(second.attempted, 0)

    def test_banned_member_gets_no_email(self):
        _event, selected = self._select()
        member = selected[0]
        UserDataConsent.objects.update_or_create(
            user=member.user, defaults={"crushlu_banned": True}
        )

        self._deliver(selected)

        self.assertEqual(self._mail_to(member.user), [])
        self.assertEqual(
            self._notice(member).status, CuratedGroupNotification.Status.SENT
        )

    def test_a_retried_email_repeats_neither_the_bell_nor_the_push(self):
        _event, selected = self._select()
        member = selected[0]
        self._subscribe(member.user)
        attempts = []

        def fail_first_attempt(registration, **kwargs):
            attempts.append(registration.pk)
            return 0 if registration.pk == member.pk and len(attempts) == 1 else 1

        with patch(
            "crush_lu.email_helpers.send_event_registration_confirmation",
            side_effect=fail_first_attempt,
        ) as send, patch(
            "crush_lu.push_notifications.send_push_notification",
            return_value={"success": 1, "failed": 0, "total": 1},
        ) as push:
            first = deliver_curated_group_notifications(
                registration_ids=[member.pk],
                kinds=[CuratedGroupNotification.Kind.SELECTION],
            )
            second = deliver_curated_group_notifications(
                registration_ids=[member.pk],
                kinds=[CuratedGroupNotification.Kind.SELECTION],
            )

        self.assertEqual((first.sent, first.failed), (0, 1))
        self.assertEqual((second.sent, second.failed), (1, 0))
        self.assertEqual(send.call_count, 2)
        # The email ignores the reminders toggle: it is sent as a seat notice.
        self.assertEqual(send.call_args.kwargs["email_type"], "event_seat_granted")
        push.assert_called_once()
        self.assertEqual(self._bells(member.user).count(), 1)
        notice = self._notice(member)
        self.assertEqual(notice.status, CuratedGroupNotification.Status.SENT)
        self.assertEqual(notice.attempt_count, 2)

    def test_web_push_goes_to_a_device_with_event_reminders_on(self):
        event, selected = self._select()
        listening, muted = selected[0], selected[1]
        self._subscribe(listening.user)
        self._subscribe(muted.user, event_reminders=False)

        with patch(
            "crush_lu.push_notifications.send_push_notification",
            return_value={"success": 1, "failed": 0, "total": 1},
        ) as push:
            self._deliver(selected)

        push.assert_called_once()
        kwargs = push.call_args.kwargs
        self.assertEqual(kwargs["user"], listening.user)
        self.assertEqual(kwargs["preference_key"], "event_reminders")
        self.assertEqual(kwargs["tag"], f"curated-selected-{listening.pk}")
        self.assertEqual(kwargs["url"], f"/events/{event.id}/")
        # The muted device still gets the bell and the email.
        self.assertEqual(self._bells(muted.user).count(), 1)
        self.assertEqual(len(self._mail_to(muted.user)), 1)

    def test_paid_event_bell_asks_for_cash_while_online_payments_are_paused(self):
        _event, selected = self._select(fee="15.00")
        member = selected[0]
        self.assertEqual(member.status, "pending")

        with patch(
            "crush_lu.services.event_payments.online_payments_paused",
            return_value=True,
        ), patch(
            "crush_lu.email_helpers.send_event_payment_pending_notification",
            return_value=1,
        ) as send:
            self._deliver([member])

        send.assert_called_once()
        self.assertEqual(send.call_args.args[0].pk, member.pk)
        bell = self._bells(member.user).get()
        self.assertIn("your place is reserved", bell.body)
        self.assertIn("cash at the door", bell.body)

    def test_paid_event_email_honours_the_master_unsubscribe(self):
        """The payment-due email used to check no preference at all."""
        from crush_lu.models import EmailPreference

        _event, selected = self._select(fee="15.00")
        member = selected[0]
        prefs = EmailPreference.get_or_create_for_user(member.user)
        prefs.unsubscribed_all = True
        prefs.save()

        with patch(
            "crush_lu.email_helpers.send_event_payment_pending_notification",
            return_value=1,
        ) as send:
            self._deliver([member])

        send.assert_not_called()
        self.assertEqual(self._bells(member.user).count(), 1)


class NotifySendEmailFlagTests(TestCase):
    def test_send_email_false_skips_the_email_channel(self):
        from crush_lu.notification_service import (
            NotificationService,
            NotificationType,
        )

        user = User.objects.create_user(
            username="flag@example.com",
            email="flag@example.com",
            password="testpass123",
        )
        event = MeetupEvent.objects.create(
            title="Flag Evening",
            description="Testing send_email=False",
            event_type="speed_dating",
            date_time=timezone.now() + timedelta(days=3),
            registration_deadline=timezone.now() + timedelta(days=2),
            location="Luxembourg",
            address="Test venue",
            max_participants=12,
            is_published=True,
        )
        registration = EventRegistration.objects.create(
            event=event, user=user, status="confirmed"
        )
        mail.outbox = []

        with patch(
            "crush_lu.notification_service.NotificationService._send_email"
        ) as send:
            result = NotificationService.notify(
                user=user,
                notification_type=NotificationType.EVENT_CURATED_SELECTED,
                context={"registration": registration, "event": event},
                send_email=False,
            )

        send.assert_not_called()
        self.assertFalse(result.email_attempted)
        self.assertTrue(result.inapp_created)
