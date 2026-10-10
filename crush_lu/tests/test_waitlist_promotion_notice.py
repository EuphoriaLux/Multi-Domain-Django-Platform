"""Waitlist promotion notice: every automatic promotion tells the member.

Incident 2026-10-10, event 27 (free mixer): a member was promoted from the
waitlist when someone else cancelled, and the only notice was an email gated
on the "Event Reminders" toggle, which that member had switched off. They held
a seat and were never told.

Decision (Tom, 2026-10-10): a promotion notice is transactional. The email
ignores the reminders toggle but honours the ban and the master unsubscribe; a
bell row is always written; push follows the device's event-reminders switch.

Covers the three automatic paths: the member cancel view, the cancellation
signal (admin, shell, payment reconciliation) and the capacity-increase
signal. Each must notify only once the promotion has committed.

Run with: pytest crush_lu/tests/test_waitlist_promotion_notice.py -v
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

User = get_user_model()

BELL_TYPE = "event_waitlist_promoted"


class _PromotionFixture(TestCase):
    """A one-seat event, its seat-holder, and a waitlisted member who has
    switched off event-reminder emails (the incident's shape)."""

    fee = Decimal("0.00")

    def setUp(self):
        from crush_lu.models import EmailPreference, MeetupEvent

        # Every test's viewer is the same user pk on SQLite and they share one
        # @ratelimit counter (AGENTS.md traps).
        cache.clear()
        self.event = MeetupEvent.objects.create(
            title="Promotion Notice Mixer",
            description="Testing the waitlist promotion notice",
            event_type="mixer",
            date_time=timezone.now() + timedelta(days=7),
            location="Luxembourg",
            address="123 Test Street",
            max_participants=1,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True,
            registration_fee=self.fee,
        )
        self.member = self._user("holder@example.com")
        self.holder = self._register(self.member, "confirmed")
        self.waiter = self._user("waiter@example.com")
        self.waiting = self._register(self.waiter, "waitlist")

        self.prefs = EmailPreference.get_or_create_for_user(self.waiter)
        self.prefs.email_event_reminders = False
        self.prefs.save()
        mail.outbox = []

    def _user(self, email):
        from crush_lu.models import CrushProfile

        user = User.objects.create_user(
            username=email, email=email, password="testpass123"
        )
        CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 1, 1),
            gender="F",
            location="Luxembourg",
        )
        return user

    def _register(self, user, status):
        from crush_lu.models import EventRegistration

        return EventRegistration.objects.create(
            event=self.event, user=user, status=status
        )

    def _waiter_mail(self):
        # The seat-holder gets their own cancellation email; ignore it.
        return [m for m in mail.outbox if self.waiter.email in m.to]

    def _bells(self):
        from crush_lu.models import Notification

        return Notification.objects.filter(
            user=self.waiter, notification_type=BELL_TYPE
        )

    def _cancel_holder(self):
        """Cancel the held seat as the admin or a coach would, capturing the
        on_commit hooks WITHOUT running them so a test can look before and
        after the commit."""
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.holder.status = "cancelled"
            self.holder.save()
        return callbacks

    def _commit(self, callbacks):
        """Run captured hooks as a real commit would, including any hook
        they queue in turn."""
        with self.captureOnCommitCallbacks(execute=True):
            for callback in callbacks:
                callback()


class CancellationSignalNoticeTests(_PromotionFixture):
    """`signals.promote_waitlist_on_cancellation`: a cancellation made anywhere
    but the member's own cancel page."""

    def test_reminders_opt_out_still_gets_email_and_bell_after_commit(self):
        callbacks = self._cancel_holder()

        self.assertEqual(self._waiter_mail(), [], "nothing before commit")
        self.assertFalse(self._bells().exists(), "nothing before commit")

        self._commit(callbacks)

        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.status, "confirmed")
        sent = self._waiter_mail()
        self.assertEqual(len(sent), 1)
        self.assertIn(self.event.title, sent[0].subject)
        bell = self._bells().get()
        self.assertEqual(bell.link_url, f"/events/{self.event.id}/")
        self.assertEqual(bell.metadata, {"registration_id": self.waiting.pk})
        self.assertIn("you're in", bell.title)

    def test_master_unsubscribe_gets_bell_but_no_email(self):
        self.prefs.unsubscribed_all = True
        self.prefs.save()

        self._commit(self._cancel_holder())

        self.assertEqual(self._waiter_mail(), [])
        self.assertEqual(self._bells().count(), 1)

    def test_banned_member_gets_no_email(self):
        from crush_lu.models.profiles import UserDataConsent

        UserDataConsent.objects.update_or_create(
            user=self.waiter, defaults={"crushlu_banned": True}
        )

        self._commit(self._cancel_holder())

        self.assertEqual(self._waiter_mail(), [])

    def test_web_push_goes_to_a_device_with_event_reminders_on(self):
        from crush_lu.models import PushSubscription

        PushSubscription.objects.create(
            user=self.waiter,
            endpoint="https://push.example.test/sub",
            p256dh_key="p256dh",
            auth_key="auth",
        )

        with patch(
            "crush_lu.push_notifications.send_push_notification",
            return_value={"success": 1, "failed": 0, "total": 1},
        ) as mocked:
            self._commit(self._cancel_holder())

        mocked.assert_called_once()
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["user"], self.waiter)
        self.assertEqual(kwargs["preference_key"], "event_reminders")
        self.assertEqual(kwargs["tag"], f"waitlist-promoted-{self.waiting.pk}")
        self.assertEqual(kwargs["url"], f"/events/{self.event.id}/")

    def test_web_push_skips_a_device_that_muted_event_reminders(self):
        from crush_lu.models import PushSubscription

        PushSubscription.objects.create(
            user=self.waiter,
            endpoint="https://push.example.test/sub",
            p256dh_key="p256dh",
            auth_key="auth",
            notify_event_reminders=False,
        )

        with patch("crush_lu.push_notifications.send_push_notification") as mocked:
            self._commit(self._cancel_holder())

        mocked.assert_not_called()
        # The other two channels do not depend on the device switch.
        self.assertEqual(len(self._waiter_mail()), 1)
        self.assertEqual(self._bells().count(), 1)

    def test_bell_renders_in_the_members_language(self):
        profile = self.waiter.crushprofile
        profile.preferred_language = "de"
        profile.save(update_fields=["preferred_language"])

        self._commit(self._cancel_holder())

        self.assertEqual(
            self._bells().get().title,
            "Ein Platz ist frei geworden: Du bist bei Promotion Notice Mixer dabei",
        )

    def test_notice_failure_never_undoes_the_promotion(self):
        with patch(
            "crush_lu.notification_service.NotificationService.notify",
            side_effect=RuntimeError("notification stack down"),
        ):
            self._commit(self._cancel_holder())

        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.status, "confirmed")


class PaidEventNoticeTests(_PromotionFixture):
    """A paid event admits the promoted member as "pending": the notice must
    ask for payment, not claim a confirmed seat."""

    fee = Decimal("15.00")

    def test_pending_seat_gets_the_payment_ask_despite_reminders_opt_out(self):
        self._commit(self._cancel_holder())

        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.status, "pending")
        sent = self._waiter_mail()
        self.assertEqual(len(sent), 1)
        self.assertIn("Payment required", sent[0].subject)
        bell = self._bells().get()
        self.assertEqual(bell.title, "A spot opened up for Promotion Notice Mixer")
        self.assertIn("Complete payment", bell.body)

    def test_master_unsubscribe_now_suppresses_the_payment_ask(self):
        """The payment-pending email used to check no preference at all. As
        a promotion notice it now honours the master unsubscribe."""
        self.prefs.unsubscribed_all = True
        self.prefs.save()

        self._commit(self._cancel_holder())

        self.assertEqual(self._waiter_mail(), [])
        self.assertEqual(self._bells().count(), 1)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class MemberCancelViewNoticeTests(_PromotionFixture):
    """`views_events.event_cancel`: the member cancels on their own page."""

    def setUp(self):
        super().setUp()
        from crush_lu.models import UserDataConsent

        # ConsentMiddleware 302s every urls_crush view without this.
        UserDataConsent.objects.filter(user=self.member).update(
            crushlu_consent_given=True
        )

    def test_view_promotes_at_once_but_notifies_only_on_commit(self):
        client = Client(HTTP_HOST="crush.lu")
        client.force_login(self.member)
        url = reverse("crush_lu:event_cancel", kwargs={"event_id": self.event.id})

        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            response = client.post(url)

        self.assertEqual(response.status_code, 302)
        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.status, "confirmed")
        self.assertEqual(self._waiter_mail(), [], "nothing before commit")
        self.assertFalse(self._bells().exists(), "nothing before commit")

        self._commit(callbacks)

        self.assertEqual(len(self._waiter_mail()), 1)
        self.assertEqual(
            self._bells().count(), 1, "the view and the signal must not both notify"
        )


class CapacityIncreaseNoticeTests(_PromotionFixture):
    """`signals.promote_waitlist_on_capacity_increase`: staff add seats."""

    def test_capacity_increase_notifies_only_on_commit(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.event.max_participants = 2
            self.event.save()

        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.status, "confirmed")
        self.assertEqual(self._waiter_mail(), [], "nothing before commit")
        self.assertFalse(self._bells().exists(), "nothing before commit")

        self._commit(callbacks)

        self.assertEqual(len(self._waiter_mail()), 1)
        self.assertEqual(self._bells().count(), 1)


class SeatGrantedEmailGateTests(_PromotionFixture):
    """The transactional gate itself, and that it stays scoped to promotions."""

    def test_ignores_the_event_reminders_toggle(self):
        from crush_lu.email_helpers import can_send_email

        self.assertTrue(can_send_email(self.waiter, "event_seat_granted"))
        self.assertFalse(can_send_email(self.waiter, "event_reminders"))

    def test_honours_the_master_unsubscribe(self):
        from crush_lu.email_helpers import can_send_email

        self.prefs.unsubscribed_all = True
        self.prefs.save()

        self.assertFalse(can_send_email(self.waiter, "event_seat_granted"))

    def test_honours_the_ban(self):
        from crush_lu.email_helpers import can_send_email
        from crush_lu.models.profiles import UserDataConsent

        UserDataConsent.objects.update_or_create(
            user=self.waiter, defaults={"crushlu_banned": True}
        )

        self.assertFalse(can_send_email(self.waiter, "event_seat_granted"))

    def test_ordinary_registration_confirmation_still_follows_reminders(self):
        """Only promotions are transactional. A member registering themselves
        has just seen the result on screen, so their opt-out still applies."""
        from crush_lu.email_helpers import send_event_registration_confirmation

        self.assertEqual(send_event_registration_confirmation(self.holder), 1)
        self.waiting.status = "confirmed"
        self.assertEqual(send_event_registration_confirmation(self.waiting), 0)
