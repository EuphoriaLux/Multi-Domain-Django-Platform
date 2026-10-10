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
signal. Each writes the bell inside the promotion's transaction, so it commits
or rolls back with the seat, and sends email and push only once that commits.

Run with: pytest crush_lu/tests/test_waitlist_promotion_notice.py -v
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.db import transaction
from django.test import Client, TestCase, override_settings
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
        # Recorded for the coach event page.
        self.assertIsNotNone(self.waiting.waitlist_promoted_at)
        self.assertEqual(self.waiting.promotion_notice, "email")

    def test_master_unsubscribe_gets_bell_but_no_email(self):
        self.prefs.unsubscribed_all = True
        self.prefs.save()

        self._commit(self._cancel_holder())

        self.assertEqual(self._waiter_mail(), [])
        self.assertEqual(self._bells().count(), 1)
        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.promotion_notice, "bell")

    def test_push_without_email_is_recorded_as_push_only(self):
        from crush_lu.models import PushSubscription

        self.prefs.unsubscribed_all = True
        self.prefs.save()
        PushSubscription.objects.create(
            user=self.waiter,
            endpoint="https://push.example.test/sub",
            p256dh_key="p256dh",
            auth_key="auth",
        )

        with patch(
            "crush_lu.push_notifications.send_push_notification",
            return_value={"success": 1, "failed": 0, "total": 1},
        ):
            self._commit(self._cancel_holder())

        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.promotion_notice, "push")

    def test_the_email_is_recorded_before_a_push_can_kill_the_worker(self):
        """The callback runs inline: a push that hangs until gunicorn kills
        the worker never returns to the final outcome update."""
        from crush_lu.models import PushSubscription

        class WorkerKilled(BaseException):
            pass

        PushSubscription.objects.create(
            user=self.waiter,
            endpoint="https://push.example.test/sub",
            p256dh_key="p256dh",
            auth_key="auth",
        )

        with patch(
            "crush_lu.push_notifications.send_push_notification",
            side_effect=WorkerKilled,
        ), self.assertRaises(WorkerKilled):
            self._commit(self._cancel_holder())

        self.assertEqual(len(self._waiter_mail()), 1)
        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.promotion_notice, "email")

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

    def test_bell_is_written_before_any_push_or_email_is_attempted(self):
        """A push that hangs until the worker is killed must not take the
        bell, the one record every promoted member gets, down with it."""
        from crush_lu.models import PushSubscription

        PushSubscription.objects.create(
            user=self.waiter,
            endpoint="https://push.example.test/sub",
            p256dh_key="p256dh",
            auth_key="auth",
        )
        bell_existed_at_push = []
        bell_existed_at_email = []

        def push(**kwargs):
            bell_existed_at_push.append(self._bells().exists())
            return {"success": 1, "failed": 0, "total": 1}

        from crush_lu import email_helpers

        real_confirmation = email_helpers.send_event_registration_confirmation

        def confirmation(*args, **kwargs):
            bell_existed_at_email.append(self._bells().exists())
            return real_confirmation(*args, **kwargs)

        with patch(
            "crush_lu.push_notifications.send_push_notification", side_effect=push
        ), patch.object(
            email_helpers, "send_event_registration_confirmation", confirmation
        ):
            self._commit(self._cancel_holder())

        self.assertEqual(bell_existed_at_push, [True])
        self.assertEqual(bell_existed_at_email, [True])
        self.assertEqual(self._bells().count(), 1, "written once, not again")

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
        """The bell fails too, so nothing reached the member: "Not sent"."""
        with patch(
            "crush_lu.notification_service.NotificationService.notify",
            side_effect=RuntimeError("notification stack down"),
        ), patch(
            "crush_lu.notification_service.NotificationService._render_inapp_payload",
            side_effect=RuntimeError("bell renderer down"),
        ):
            self._commit(self._cancel_holder())

        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.status, "confirmed")
        self.assertEqual(self.waiting.promotion_notice, "failed")

    def test_a_notice_crash_after_the_bell_still_reports_the_bell(self):
        with patch(
            "crush_lu.notification_service.NotificationService.notify",
            side_effect=RuntimeError("notification stack down"),
        ):
            self._commit(self._cancel_holder())

        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.status, "confirmed")
        self.assertEqual(self._bells().count(), 1)
        self.assertEqual(self.waiting.promotion_notice, "bell")


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


class MemberCancelViewNoticeTests(_PromotionFixture):
    """`views_events.event_cancel`: the member cancels on their own page."""

    def setUp(self):
        super().setUp()
        from crush_lu.models import UserDataConsent

        # ConsentMiddleware 302s every urls_crush view without this.
        UserDataConsent.objects.filter(user=self.member).update(
            crushlu_consent_given=True
        )

    def test_view_promotes_and_writes_the_bell_at_once_but_emails_on_commit(self):
        client = Client(HTTP_HOST="crush.lu")
        client.force_login(self.member)

        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            response = client.post(f"/en/events/{self.event.id}/cancel/")

        self.assertEqual(response.status_code, 302)
        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.status, "confirmed")
        self.assertEqual(self._bells().count(), 1, "written with the seat")
        self.assertEqual(self.waiting.promotion_notice, "bell")
        self.assertEqual(self._waiter_mail(), [], "no email before commit")

        self._commit(callbacks)

        self.assertEqual(len(self._waiter_mail()), 1)
        self.assertEqual(
            self._bells().count(), 1, "the view and the signal must not both notify"
        )


class CapacityIncreaseNoticeTests(_PromotionFixture):
    """`signals.promote_waitlist_on_capacity_increase`: staff add seats."""

    def test_capacity_increase_writes_the_bell_at_once_but_emails_on_commit(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.event.max_participants = 2
            self.event.save()

        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.status, "confirmed")
        self.assertEqual(self._bells().count(), 1, "written with the seat")
        self.assertEqual(self.waiting.promotion_notice, "bell")
        self.assertEqual(self._waiter_mail(), [], "no email before commit")

        self._commit(callbacks)

        self.assertEqual(len(self._waiter_mail()), 1)
        self.assertEqual(self._bells().count(), 1)
        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.promotion_notice, "email")

    def test_a_rolled_back_capacity_change_takes_its_bells_with_it(self):
        """An admin save wraps the handler in its own transaction. If the
        rest of it fails, the rollback takes the seat, its bell and its email
        with it."""
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            with self.assertRaises(RuntimeError), transaction.atomic():
                self.event.max_participants = 2
                self.event.save()
                raise RuntimeError("the rest of the admin save failed")
        self._commit(callbacks)

        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.status, "waitlist")
        self.assertFalse(self._bells().exists())
        self.assertEqual(self._waiter_mail(), [])

    def test_a_bell_that_fails_to_write_never_costs_the_seat(self):
        """The bell is written inside the promotion's transaction. A database
        error there rolls back to its own savepoint: the seat still commits,
        and the bell is written once the promotion has committed."""
        from crush_lu.notification_service import NotificationService

        real_render = NotificationService._render_inapp_payload

        def unwritable(*args, **kwargs):
            # link_url is NOT NULL: a genuine IntegrityError from the insert.
            return {**real_render(*args, **kwargs), "link_url": None}

        with patch.object(
            NotificationService, "_render_inapp_payload", side_effect=unwritable
        ), self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.event.max_participants = 2
            self.event.save()

        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.status, "confirmed")
        self.assertFalse(self._bells().exists())

        self._commit(callbacks)

        self.assertEqual(self._bells().count(), 1)
        self.assertEqual(len(self._waiter_mail()), 1)
        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.promotion_notice, "email")

    def _second_waiter(self):
        user = self._user("waiter2@example.com")
        return user, self._register(user, "waitlist")

    def _raise_capacity_and_commit(self, seats):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.event.max_participants = seats
            self.event.save()
        self._commit(callbacks)

    def test_batch_within_budget_notifies_every_promoted_member(self):
        second, second_reg = self._second_waiter()

        self._raise_capacity_and_commit(3)

        second_reg.refresh_from_db()
        self.assertEqual(second_reg.status, "confirmed")
        self.assertEqual(len(self._waiter_mail()), 1)
        self.assertEqual(len([m for m in mail.outbox if second.email in m.to]), 1)

    @override_settings(WAITLIST_PROMOTION_NOTICE_BUDGET_SECONDS=0)
    def test_batch_past_budget_still_writes_every_bell_and_logs(self):
        """Every on_commit callback runs inside the admin request, so a large
        capacity jump must stop sending once the budget is spent. Nobody is
        dropped: the bell row needs no network, and staff get an ERROR."""
        from crush_lu.models import Notification

        second, second_reg = self._second_waiter()

        with self.assertLogs("crush_lu.notification_service", "ERROR") as logs:
            self._raise_capacity_and_commit(3)

        self.assertEqual(mail.outbox, [])
        for user in (self.waiter, second):
            self.assertEqual(
                Notification.objects.filter(
                    user=user, notification_type=BELL_TYPE
                ).count(),
                1,
            )
        self.assertIn("budget", logs.output[0])
        self.assertIn(str(second_reg.pk), logs.output[0])
        for registration in (self.waiting, second_reg):
            registration.refresh_from_db()
            self.assertEqual(registration.promotion_notice, "bell")

    def test_every_bell_in_the_batch_exists_before_any_email_goes_out(self):
        """One member's email or push hanging until the worker is killed
        must not cost the members after them their bell."""
        from crush_lu import email_helpers
        from crush_lu.models import Notification

        second, second_reg = self._second_waiter()
        promoted_users = [self.waiter, second]
        bells_at_first_email = []
        real_confirmation = email_helpers.send_event_registration_confirmation

        def confirmation(*args, **kwargs):
            if not bells_at_first_email:
                bells_at_first_email.append(
                    Notification.objects.filter(
                        user__in=promoted_users, notification_type=BELL_TYPE
                    ).count()
                )
            return real_confirmation(*args, **kwargs)

        with patch.object(
            email_helpers, "send_event_registration_confirmation", confirmation
        ):
            self._raise_capacity_and_commit(3)

        self.assertEqual(bells_at_first_email, [2])
        for user in promoted_users:
            self.assertEqual(
                Notification.objects.filter(
                    user=user, notification_type=BELL_TYPE
                ).count(),
                1,
                "the batch's bell is not written a second time",
            )
        for registration in (self.waiting, second_reg):
            registration.refresh_from_db()
            self.assertEqual(registration.promotion_notice, "email")

    def test_the_bell_pass_costs_the_same_queries_for_any_batch_size(self):
        """However many seats a capacity increase opens, writing the bells
        inside its transaction must not grow with the batch."""
        from crush_lu.models import EventRegistration
        from crush_lu.notification_service import write_waitlist_promotion_bells

        now = timezone.now()
        for index in range(4):
            self._register(self._user(f"batch{index}@example.com"), "confirmed")
        EventRegistration.objects.filter(event=self.event).update(
            waitlist_promoted_at=now
        )

        def promoted(limit):
            return list(EventRegistration.objects.filter(event=self.event)[:limit])

        small, large = promoted(2), promoted(6)
        # A savepoint around one preloading read, one insert, one update.
        with self.assertNumQueries(5):
            self.assertEqual(len(write_waitlist_promotion_bells(small)), 2)
        with self.assertNumQueries(5):
            self.assertEqual(len(write_waitlist_promotion_bells(large)), 6)

    def test_a_member_who_gives_the_seat_back_mid_batch_is_not_emailed(self):
        """Every bell goes out first, so a later member can cancel while
        earlier members are still being emailed. Nobody may then be told
        about a seat that is already gone."""
        from crush_lu import email_helpers
        from crush_lu.models import EventRegistration

        second, second_reg = self._second_waiter()
        real_confirmation = email_helpers.send_event_registration_confirmation
        calls = []

        def confirmation(registration, *args, **kwargs):
            calls.append(registration.pk)
            if len(calls) == 1:
                # Promotion runs in waitlist order; while the first member is
                # being emailed, the second gives their new seat back.
                EventRegistration.objects.filter(pk=second_reg.pk).update(
                    status="cancelled", waitlist_promoted_at=None, promotion_notice=""
                )
            return real_confirmation(registration, *args, **kwargs)

        with patch.object(
            email_helpers, "send_event_registration_confirmation", confirmation
        ):
            self._raise_capacity_and_commit(3)

        self.assertEqual(calls, [self.waiting.pk])
        self.assertEqual(len(self._waiter_mail()), 1)
        self.assertEqual([m for m in mail.outbox if second.email in m.to], [])

    def test_a_notice_for_a_seat_already_gone_sends_nothing(self):
        from crush_lu.models import EventRegistration, Notification
        from crush_lu.notification_service import notify_waitlist_promotion

        self.waiting.status = "confirmed"
        self.waiting.waitlist_promoted_at = timezone.now()
        # The row in the database never got (or already lost) this promotion.
        EventRegistration.objects.filter(pk=self.waiting.pk).update(status="waitlist")

        self.assertIsNone(notify_waitlist_promotion(self.waiting))
        self.assertEqual(self._waiter_mail(), [])
        self.assertFalse(
            Notification.objects.filter(
                user=self.waiter, notification_type=BELL_TYPE
            ).exists()
        )

    def test_a_notice_for_an_event_that_no_longer_takes_promotions_sends_nothing(
        self,
    ):
        """In a batch, staff can cancel or unpublish the event, or it can
        start, while earlier members are still being notified; the seat row
        itself stays confirmed."""
        from crush_lu.models import EventRegistration, MeetupEvent
        from crush_lu.notification_service import notify_waitlist_promotion

        EventRegistration.objects.filter(pk=self.waiting.pk).update(
            status="confirmed", waitlist_promoted_at=timezone.now()
        )
        self.waiting.refresh_from_db()
        events = MeetupEvent.objects.filter(pk=self.event.pk)
        original = events.values("is_cancelled", "is_published", "date_time").get()
        for change in (
            {"is_cancelled": True},
            {"is_published": False},
            {"date_time": timezone.now() - timedelta(minutes=1)},
        ):
            with self.subTest(change=sorted(change)):
                events.update(**change)
                with patch(
                    "crush_lu.notification_service.NotificationService.notify"
                ) as notify:
                    self.assertIsNone(notify_waitlist_promotion(self.waiting))
                notify.assert_not_called()
                events.update(**original)

        # The control: the same row, on the event as it was, is announced.
        with patch(
            "crush_lu.notification_service.NotificationService.notify"
        ) as notify:
            notify_waitlist_promotion(self.waiting)
        notify.assert_called_once()

    def test_a_notice_renders_the_event_as_it_is_when_sent(self):
        """Staff can edit the event while earlier members of a batch are
        still being notified; the batch holds the event as it was."""
        from crush_lu.models import EventRegistration, MeetupEvent
        from crush_lu.notification_service import notify_waitlist_promotion

        EventRegistration.objects.filter(pk=self.waiting.pk).update(
            status="confirmed", waitlist_promoted_at=timezone.now()
        )
        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.event.title, "Promotion Notice Mixer")
        MeetupEvent.objects.filter(pk=self.event.pk).update(title="Renamed Mixer")

        notify_waitlist_promotion(self.waiting)

        [sent] = self._waiter_mail()
        self.assertIn("Renamed Mixer", sent.subject)


class AdminWithdrawalTests(_PromotionFixture):
    def test_moving_a_promoted_seat_back_to_the_waitlist_clears_its_notice(self):
        """Otherwise a later restore shows coaches the old "emailed" result
        for a seat grant nobody announced."""
        from django.test import RequestFactory

        from crush_lu.admin.site import crush_admin_site
        from crush_lu.models import EventRegistration

        self._commit(self._cancel_holder())
        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.promotion_notice, "email")

        with patch("crush_lu.admin.events.django_messages"):
            crush_admin_site._registry[EventRegistration].move_to_waitlist(
                RequestFactory().post("/"),
                EventRegistration.objects.filter(pk=self.waiting.pk),
            )

        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.status, "waitlist")
        self.assertIsNone(self.waiting.waitlist_promoted_at)
        self.assertEqual(self.waiting.promotion_notice, "")

    def test_any_save_that_gives_up_the_seat_clears_its_notice(self):
        """The admin change form, list_editable and the member's own cancel
        save the model rather than run a bulk action, with or without
        update_fields. Waitlisted and cancelled rows hold no seat."""
        from crush_lu.models import EventRegistration

        for status in ("waitlist", "cancelled"):
            for update_fields in (None, ["status"]):
                with self.subTest(status=status, update_fields=update_fields):
                    EventRegistration.objects.filter(pk=self.waiting.pk).update(
                        status="confirmed",
                        cancelled_at=None,
                        waitlist_promoted_at=timezone.now(),
                        promotion_notice=EventRegistration.PromotionNotice.EMAIL,
                    )
                    self.waiting.refresh_from_db()

                    self.waiting.status = status
                    self.waiting._waitlist_promotion_handled = True
                    self.waiting.save(update_fields=update_fields)

                    self.waiting.refresh_from_db()
                    self.assertIsNone(self.waiting.waitlist_promoted_at)
                    self.assertEqual(self.waiting.promotion_notice, "")

    def test_admin_confirm_restoring_a_cancelled_promotion_clears_its_notice(self):
        """The bulk Confirm action restores cancelled rows with update(); the
        seat it grants was never announced, so no old outcome may show."""
        from django.test import RequestFactory

        from crush_lu.admin.site import crush_admin_site
        from crush_lu.models import EventRegistration

        self._commit(self._cancel_holder())
        # A cancellation that bypassed save() and so kept the old outcome.
        EventRegistration.objects.filter(pk=self.waiting.pk).update(
            status="cancelled", cancelled_at=timezone.now()
        )
        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.promotion_notice, "email")

        request = RequestFactory().post("/")
        request.user = User.objects.create_superuser(
            username="staff@example.com", email="staff@example.com", password="x"
        )
        with patch("crush_lu.admin.events.django_messages"):
            crush_admin_site._registry[EventRegistration].confirm_registrations(
                request, EventRegistration.objects.filter(pk=self.waiting.pk)
            )

        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.status, "confirmed")
        self.assertIsNone(self.waiting.waitlist_promoted_at)
        self.assertEqual(self.waiting.promotion_notice, "")

    def test_admin_confirm_keeps_the_outcome_of_a_seat_already_held(self):
        """Confirming a row that already holds its seat restores nothing, so
        a promoted member only the bell reached must stay flagged."""
        from django.test import RequestFactory

        from crush_lu.admin.site import crush_admin_site
        from crush_lu.models import EventRegistration

        self.prefs.unsubscribed_all = True
        self.prefs.save()
        self._commit(self._cancel_holder())
        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.status, "confirmed")
        promoted_at = self.waiting.waitlist_promoted_at

        request = RequestFactory().post("/")
        request.user = User.objects.create_superuser(
            username="staff2@example.com", email="staff2@example.com", password="x"
        )
        with patch("crush_lu.admin.events.django_messages"):
            crush_admin_site._registry[EventRegistration].confirm_registrations(
                request, EventRegistration.objects.filter(pk=self.waiting.pk)
            )

        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.waitlist_promoted_at, promoted_at)
        self.assertEqual(self.waiting.promotion_notice, "bell")


class PromotionDeadlineTests(_PromotionFixture):
    """The notice runs inside the request that freed the seat, so its email
    and push share one wall-clock deadline; the bell is never subject to it."""

    def _subscribe(self):
        from crush_lu.models import PushSubscription

        PushSubscription.objects.create(
            user=self.waiter,
            endpoint="https://push.example.test/sub",
            p256dh_key="p256dh",
            auth_key="auth",
        )

    @override_settings(WAITLIST_PROMOTION_NOTICE_BUDGET_SECONDS=0)
    def test_a_spent_deadline_skips_email_and_push_but_not_the_bell(self):
        self._subscribe()

        with patch("crush_lu.push_notifications.send_push_notification") as push:
            self._commit(self._cancel_holder())

        push.assert_not_called()
        self.assertEqual(self._waiter_mail(), [])
        self.assertEqual(self._bells().count(), 1)
        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.promotion_notice, "bell")

    def test_email_goes_out_before_push(self):
        """The email is the transactional channel; slow push providers must
        not spend the deadline it needs."""
        from crush_lu import email_helpers

        self._subscribe()
        order = []
        real_confirmation = email_helpers.send_event_registration_confirmation

        def confirmation(*args, **kwargs):
            order.append("email")
            return real_confirmation(*args, **kwargs)

        def push(**kwargs):
            order.append("push")
            return {"success": 1, "failed": 0, "total": 1}

        with patch.object(
            email_helpers, "send_event_registration_confirmation", confirmation
        ), patch(
            "crush_lu.push_notifications.send_push_notification", side_effect=push
        ):
            self._commit(self._cancel_holder())

        self.assertEqual(order, ["email", "push"])

    def test_push_is_skipped_once_a_slow_email_spends_the_deadline(self):
        from crush_lu import email_helpers
        from crush_lu import notification_service

        self._subscribe()
        real_confirmation = email_helpers.send_event_registration_confirmation
        clock = {"now": 1000.0}

        def slow_confirmation(*args, **kwargs):
            clock["now"] += 31  # past the 30s default budget
            return real_confirmation(*args, **kwargs)

        with patch.object(
            notification_service.time, "monotonic", side_effect=lambda: clock["now"]
        ), patch.object(
            email_helpers, "send_event_registration_confirmation", slow_confirmation
        ), patch(
            "crush_lu.push_notifications.send_push_notification"
        ) as push:
            self._commit(self._cancel_holder())

        push.assert_not_called()
        self.assertEqual(len(self._waiter_mail()), 1)
        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.promotion_notice, "email")


class BellLinkTests(_PromotionFixture):
    def test_bell_link_resolves_to_the_event_page_on_crush_lu(self):
        """The bell stores the unprefixed path like every other bell type;
        LocaleMiddleware redirects it to a language-prefixed event page rather
        than 404ing under i18n_patterns(prefix_default_language=True)."""
        self._commit(self._cancel_holder())

        link = self._bells().get().link_url
        response = Client(HTTP_HOST="crush.lu").get(link)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"/en/events/{self.event.id}/")


class PromotionNoticeRecordTests(_PromotionFixture):
    def test_a_late_result_cannot_overwrite_a_newer_promotion(self):
        """A reused row can be promoted again while an earlier promotion's
        notice is still sending; that late result must not land on the new
        cycle and hide (or fake) a failed notice."""
        from crush_lu.models import EventRegistration
        from crush_lu.notification_service import _record_promotion_notice

        stale = EventRegistration.objects.get(pk=self.waiting.pk)
        stale.waitlist_promoted_at = timezone.now() - timedelta(hours=1)
        EventRegistration.objects.filter(pk=self.waiting.pk).update(
            status="confirmed", waitlist_promoted_at=timezone.now()
        )

        _record_promotion_notice(stale, EventRegistration.PromotionNotice.EMAIL)

        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.promotion_notice, "")


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
