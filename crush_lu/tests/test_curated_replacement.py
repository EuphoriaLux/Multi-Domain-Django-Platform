"""Coach-chosen replacements on a curated evening, and the door admit.

After the selection is closed nobody is moved automatically. A coach may seat
one ``not_selected`` member ("Invite as replacement"), or admit one who walked
in ("Admit and check in" on the door page). Both go through
``seat_not_selected_member``, the one door through the curated seat guard for
a single person.

Spec: ai-memory-hub/specs/2026-10-29-crush-curated-auto-selection.md (D4)
Run with: pytest crush_lu/tests/test_curated_replacement.py -v
"""

import inspect
from datetime import date, timedelta
from decimal import Decimal

from django.contrib import admin as django_admin
from django.contrib.auth import get_user_model
from django.contrib import messages as django_messages
from django.contrib.messages import get_messages
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from crush_lu.models import CrushCoach
from crush_lu.models.events import (
    CuratedEventGroup,
    CuratedEventGroupMembership,
    CuratedGroupNotification,
    EventRegistration,
    EventRegistrationPreference,
    MeetupEvent,
)
from crush_lu.models.profiles import CrushProfile, UserDataConsent
from crush_lu.services import curated_group_workflow
from crush_lu.services.curated_group_workflow import (
    approve_current_generation,
    close_curated_selection,
    generate_group_projection,
    registration_has_certified_payable_group,
    seat_not_selected_member,
)

User = get_user_model()


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class ReplacementFixture(TestCase):
    def setUp(self):
        cache.clear()
        self.client.defaults["HTTP_HOST"] = "crush.lu"

    def _event(self, **overrides):
        values = {
            "title": "Replacement evening",
            "description": "Testing coach-chosen replacements",
            "event_type": "speed_dating",
            "registration_mode": "curated",
            "date_time": timezone.now() + timedelta(days=3),
            "registration_deadline": timezone.now() - timedelta(hours=1),
            "location": "Luxembourg",
            "address": "Test venue",
            "max_participants": 12,
            "group_size": 6,
            "planned_groups": 1,
            "registration_fee": Decimal("0.00"),
            "profile_requirement": "none",
            "is_published": True,
        }
        values.update(overrides)
        return MeetupEvent.objects.create(**values)

    def _applicant(self, event, index):
        email = f"repl-{event.pk}-{index}@example.com"
        user = User.objects.create_user(
            username=email, email=email, password="testpass123", first_name=f"M{index}"
        )
        UserDataConsent.objects.update_or_create(
            user=user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 1, 1),
            gender="NB",
            location="Luxembourg",
            event_languages=["en"],
            preferred_language="en",
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
        return registration

    def _closed_selection(self, *, fee="0.00", **overrides):
        """One invited group of six and two members marked Not selected."""
        event = self._event(registration_fee=Decimal(fee), **overrides)
        registrations = [self._applicant(event, i) for i in range(8)]
        generate_group_projection(event, deterministic_seed="repl")
        approved = approve_current_generation(event)
        invited_ids = list(approved.applied_registration_ids)
        EventRegistration.objects.filter(pk__in=invited_ids).update(
            status="pending" if Decimal(fee) else "confirmed"
        )
        close_curated_selection(event)
        outside = [r for r in registrations if r.pk not in invited_ids]
        mail.outbox = []
        return event, invited_ids, outside

    def _cancel(self, registration_id):
        """An invited member cancels: the group degrades and waits for a coach
        (the on-commit remedy never runs inside a TestCase)."""
        registration = EventRegistration.objects.get(pk=registration_id)
        registration.status = "cancelled"
        registration.save()

    def _status(self, pk):
        return EventRegistration.objects.get(pk=pk).status

    def _active_group_ids(self, registration_id):
        return list(
            CuratedEventGroupMembership.objects.filter(
                registration_id=registration_id,
                released_at__isnull=True,
            )
            .exclude(group__status=CuratedEventGroup.STATUS_CANCELLED)
            .values_list("group_id", flat=True)
        )


class SeatNotSelectedMemberTests(ReplacementFixture):
    def test_replacement_takes_the_freed_place_and_is_told(self):
        event, invited_ids, outside = self._closed_selection()
        self._cancel(invited_ids[0])
        newcomer = outside[0]

        with self.captureOnCommitCallbacks(execute=True):
            seated = seat_not_selected_member(newcomer.pk)

        self.assertEqual(seated.status, "confirmed")
        self.assertEqual(seated.minimum_dates, 5)
        self.assertEqual(self._status(newcomer.pk), "confirmed")
        # Everyone who held a seat still does, in the same rebuilt group.
        remaining = invited_ids[1:]
        self.assertEqual({self._status(pk) for pk in remaining}, {"confirmed"})
        group_ids = set(self._active_group_ids(newcomer.pk))
        self.assertEqual(len(group_ids), 1)
        for pk in remaining:
            self.assertEqual(set(self._active_group_ids(pk)), group_ids)
        group = CuratedEventGroup.objects.get(pk=group_ids.pop())
        self.assertEqual(group.status, CuratedEventGroup.STATUS_PROVISIONAL)
        self.assertEqual(group.generation, seated.generation)
        newcomer.refresh_from_db()
        self.assertTrue(registration_has_certified_payable_group(newcomer))
        # The ordinary selection notice, once.
        notice = CuratedGroupNotification.objects.get(
            registration_id=newcomer.pk,
            kind=CuratedGroupNotification.Kind.SELECTION,
        )
        self.assertEqual(notice.status, CuratedGroupNotification.Status.SENT)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [newcomer.user.email])
        # The other Not selected member is untouched.
        self.assertEqual(self._status(outside[1].pk), "not_selected")

    def test_paid_event_seats_the_replacement_as_pending(self):
        event, invited_ids, outside = self._closed_selection(fee="15.50")
        self._cancel(invited_ids[0])

        seated = seat_not_selected_member(outside[0].pk)

        self.assertEqual(seated.status, "pending")
        self.assertEqual(self._status(outside[0].pk), "pending")
        self.assertEqual({self._status(pk) for pk in invited_ids[1:]}, {"pending"})

    def test_full_group_refuses_and_changes_nothing(self):
        event, invited_ids, outside = self._closed_selection()
        before = {pk: self._active_group_ids(pk) for pk in invited_ids}

        with self.assertRaises(ValidationError):
            seat_not_selected_member(outside[0].pk)

        self.assertEqual(self._status(outside[0].pk), "not_selected")
        self.assertEqual(self._active_group_ids(outside[0].pk), [])
        self.assertEqual({pk: self._active_group_ids(pk) for pk in invited_ids}, before)
        self.assertFalse(
            CuratedGroupNotification.objects.filter(
                registration_id=outside[0].pk,
                kind=CuratedGroupNotification.Kind.SELECTION,
            ).exists()
        )

    def test_only_a_not_selected_member_qualifies(self):
        event, invited_ids, outside = self._closed_selection()
        self._cancel(invited_ids[0])
        with self.assertRaisesMessage(ValidationError, "Not selected"):
            seat_not_selected_member(invited_ids[0])
        with self.assertRaisesMessage(ValidationError, "Not selected"):
            seat_not_selected_member(invited_ids[1])

    def test_refused_on_a_cancelled_event_and_after_round_one(self):
        event, invited_ids, outside = self._closed_selection()
        self._cancel(invited_ids[0])
        MeetupEvent.objects.filter(pk=event.pk).update(is_cancelled=True)
        with self.assertRaisesMessage(ValidationError, "cancelled"):
            seat_not_selected_member(outside[0].pk)
        MeetupEvent.objects.filter(pk=event.pk).update(
            is_cancelled=False, curated_rounds_started_at=timezone.now()
        )
        with self.assertRaisesMessage(ValidationError, "round one"):
            seat_not_selected_member(outside[0].pk)
        self.assertEqual(self._status(outside[0].pk), "not_selected")

    def test_refused_once_the_groups_are_locked(self):
        event, invited_ids, outside = self._closed_selection()
        CuratedEventGroup.objects.filter(event=event).exclude(
            status=CuratedEventGroup.STATUS_CANCELLED
        ).update(status=CuratedEventGroup.STATUS_LOCKED, locked_at=timezone.now())
        with self.assertRaisesMessage(ValidationError, "locked"):
            seat_not_selected_member(outside[0].pk)
        self.assertEqual(self._status(outside[0].pk), "not_selected")

    def test_invite_respects_capacity_but_the_door_does_not(self):
        event, invited_ids, outside = self._closed_selection()
        self._cancel(invited_ids[0])
        # Five seats are held; a capacity of five leaves no free place.
        MeetupEvent.objects.filter(pk=event.pk).update(max_participants=5)

        with self.assertRaisesMessage(ValidationError, "full"):
            seat_not_selected_member(outside[0].pk)
        self.assertEqual(self._status(outside[0].pk), "not_selected")

        seated = seat_not_selected_member(outside[0].pk, at_the_door=True)
        self.assertEqual(seated.status, "confirmed")

    def test_door_seat_sends_no_selection_notice(self):
        event, invited_ids, outside = self._closed_selection()
        self._cancel(invited_ids[0])

        with self.captureOnCommitCallbacks(execute=True):
            seat_not_selected_member(outside[0].pk, at_the_door=True)

        self.assertEqual(mail.outbox, [])
        self.assertFalse(
            CuratedGroupNotification.objects.filter(
                registration_id=outside[0].pk,
                kind=CuratedGroupNotification.Kind.SELECTION,
            ).exists()
        )

    def test_open_checkout_of_a_departed_member_sends_the_coach_to_the_repair(self):
        from crush_lu.models.payments import PaymentTransaction

        event, invited_ids, outside = self._closed_selection(fee="15.50")
        leaver = invited_ids[0]
        payment = PaymentTransaction.objects.create(
            transaction_reference=f"CRUSH-EVT-{leaver}-open",
            provider=PaymentTransaction.Provider.MOLLIE,
            mollie_payment_id="tr_open",
            amount=Decimal("15.50"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=EventRegistration.objects.get(pk=leaver).user,
            event_registration_id=leaver,
        )
        self._cancel(leaver)

        with self.assertRaisesMessage(ValidationError, "Keep the held group"):
            seat_not_selected_member(outside[0].pk)

        # Nothing moved, and the checkout is still the audited repair's.
        self.assertEqual(self._status(outside[0].pk), "not_selected")
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentTransaction.Status.PENDING)

        # Once that checkout is closed, the replacement goes through.
        payment.status = PaymentTransaction.Status.CANCELLED
        payment.save(update_fields=["status"])
        self.assertEqual(seat_not_selected_member(outside[0].pk).status, "pending")

    def test_uninvited_placed_applicant_leaves_the_group_and_is_reported(self):
        event = self._event()
        registrations = [self._applicant(event, i) for i in range(8)]
        generate_group_projection(event, deterministic_seed="repl")
        approved = approve_current_generation(event)
        invited_ids = list(approved.applied_registration_ids)
        # One member of the approved group was never invited.
        uninvited, seated_ids = invited_ids[0], invited_ids[1:]
        EventRegistration.objects.filter(pk__in=seated_ids).update(status="confirmed")
        close_curated_selection(event)
        outside = [r for r in registrations if r.pk not in invited_ids]

        seated = seat_not_selected_member(outside[0].pk)

        self.assertEqual(seated.released_uninvited_ids, (uninvited,))
        self.assertEqual(self._status(uninvited), "applied")
        self.assertEqual(self._active_group_ids(uninvited), [])
        self.assertEqual(len(self._active_group_ids(outside[0].pk)), 1)

    def test_new_seat_has_a_working_ticket_and_scans_at_the_door(self):
        from crush_lu.views_ticket import _generate_checkin_token

        event, invited_ids, outside = self._closed_selection(
            date_time=timezone.now() + timedelta(hours=1)
        )
        self._cancel(invited_ids[0])
        newcomer = outside[0]
        with self.captureOnCommitCallbacks(execute=True):
            seat_not_selected_member(newcomer.pk)

        # The member's own ticket page (event 26's failure was a 404 here).
        self.client.force_login(newcomer.user)
        ticket = self.client.get(f"/en/events/{event.pk}/ticket/")
        self.assertEqual(ticket.status_code, 200)

        # ...and a coach's scan admits them.
        coach_user = User.objects.create_user(
            username="scan-coach@example.com",
            email="scan-coach@example.com",
            password="pw",
        )
        CrushCoach.objects.create(user=coach_user, is_active=True)
        UserDataConsent.objects.update_or_create(
            user=coach_user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        self.client.force_login(coach_user)
        newcomer.refresh_from_db()
        token = newcomer.checkin_token or _generate_checkin_token(newcomer)
        scan = self.client.post(f"/api/events/checkin/{newcomer.pk}/{token}/")
        self.assertEqual(scan.status_code, 200, scan.content)
        self.assertEqual(self._status(newcomer.pk), "attended")

    def test_wallet_tickets_are_refreshed_after_the_seat_commits(self):
        from unittest import mock

        event, invited_ids, outside = self._closed_selection()
        self._cancel(invited_ids[0])
        EventRegistration.objects.filter(pk=outside[0].pk).update(
            apple_wallet_ticket_serial="ticket-serial-1"
        )

        with (
            mock.patch(
                "crush_lu.wallet.passkit_service.refresh_ticket_serials"
            ) as refresh,
            mock.patch(
                "crush_lu.services.registration_confirmation.notify_google_indexing"
            ) as indexing,
            self.captureOnCommitCallbacks(execute=True),
        ):
            seat_not_selected_member(outside[0].pk)

        refresh.assert_called_once()
        self.assertEqual(refresh.call_args.args[0], ["ticket-serial-1"])
        indexing.assert_called_once_with([event.pk])

    def test_locks_through_the_shared_helper_and_no_payment_row(self):
        """SQLite ignores row locks, so the order is asserted on the source:
        event, registrations and groups come from ``_lock_event_state`` before
        anything is written, and no payment row is locked."""
        src = inspect.getsource(curated_group_workflow.seat_not_selected_member)
        lock = src.index("_lock_event_state(event_id)")
        self.assertLess(lock, src.index(".update("))
        self.assertLess(lock, src.index("_generate_group_projection("))
        # Payments are read for the open-checkout refusal, never locked: the
        # documented order puts payment locks before the event lock.
        self.assertNotIn("select_for_update", src)


class InviteAsReplacementAdminTests(ReplacementFixture):
    def _run(self, queryset):
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from crush_lu.admin.events import EventRegistrationAdmin

        request = RequestFactory().post("/")
        request.user = User.objects.create_superuser(
            username="staff@example.com", email="staff@example.com", password="pw"
        )
        request.session = {}
        request._messages = FallbackStorage(request)
        model_admin = EventRegistrationAdmin(
            EventRegistration, django_admin.AdminSite()
        )
        with self.captureOnCommitCallbacks(execute=True):
            model_admin.invite_as_replacement(request, queryset)
        return [
            ("success" if m.level == django_messages.SUCCESS else "error", str(m))
            for m in get_messages(request)
        ]

    def test_action_is_offered_on_registrations(self):
        from crush_lu.admin.events import EventRegistrationAdmin

        self.assertIn("invite_as_replacement", EventRegistrationAdmin.actions)

    def test_action_seats_the_member_and_says_so(self):
        event, invited_ids, outside = self._closed_selection()
        self._cancel(invited_ids[0])

        messages = self._run(EventRegistration.objects.filter(pk=outside[0].pk))

        self.assertEqual(self._status(outside[0].pk), "confirmed")
        self.assertEqual([level for level, _text in messages], ["success"])
        self.assertIn("at least 5 dates", messages[0][1])
        self.assertIn("was told", messages[0][1])
        self.assertEqual(len(mail.outbox), 1)

    def test_second_pick_is_judged_against_the_roster_the_first_left(self):
        event, invited_ids, outside = self._closed_selection()
        self._cancel(invited_ids[0])

        messages = self._run(
            EventRegistration.objects.filter(pk__in=[r.pk for r in outside])
        )

        # One place was free: the first member takes it, the second is refused
        # and stays Not selected.
        first, second = sorted(outside, key=lambda r: r.pk)
        self.assertEqual(self._status(first.pk), "confirmed")
        self.assertEqual(self._status(second.pk), "not_selected")
        self.assertEqual([level for level, _text in messages], ["success", "error"])


class DoorAdmitTests(ReplacementFixture):
    def setUp(self):
        super().setUp()
        coach_user = User.objects.create_user(
            username="door-coach@example.com",
            email="door-coach@example.com",
            password="pw",
        )
        CrushCoach.objects.create(user=coach_user, is_active=True)
        UserDataConsent.objects.update_or_create(
            user=coach_user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        self.client.force_login(coach_user)

    def _tonight(self, **overrides):
        return self._closed_selection(
            date_time=timezone.now() + timedelta(hours=1), **overrides
        )

    def _admit(self, event, registration):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                f"/api/events/{event.pk}/promote/{registration.pk}/"
            )

    def test_walk_in_is_seated_grouped_and_checked_in(self):
        event, invited_ids, outside = self._tonight()
        self._cancel(invited_ids[0])

        response = self._admit(event, outside[0])

        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertTrue(data["promoted"])
        self.assertIn("admitted", data["message"])
        row = EventRegistration.objects.get(pk=outside[0].pk)
        self.assertEqual(row.status, "attended")
        self.assertIsNotNone(row.checked_in_at)
        # An undo puts them back on the seat they were given, not outside a
        # roster that now contains them.
        self.assertEqual(row.checkin_prior_status, "confirmed")
        self.assertEqual(len(self._active_group_ids(row.pk)), 1)
        self.assertEqual({self._status(pk) for pk in invited_ids[1:]}, {"confirmed"})
        # At the door nobody is emailed a selection notice.
        self.assertEqual(mail.outbox, [])

    def test_toast_says_so_when_the_walk_in_lowers_the_guarantee(self):
        event, invited_ids, outside = self._tonight()
        # Two members left: with the walk-in, five people share four dates.
        self._cancel(invited_ids[0])
        self._cancel(invited_ids[1])

        response = self._admit(event, outside[0])

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("at least 4 dates", response.json()["message"])
        self.assertEqual(self._status(outside[0].pk), "attended")

    def test_waitlisted_row_on_a_curated_evening_is_refused_not_a_500(self):
        event, invited_ids, outside = self._tonight()
        EventRegistration.objects.filter(pk=outside[1].pk).update(status="waitlist")

        response = self._admit(event, outside[1])

        self.assertEqual(response.status_code, 409)
        self.assertIn("bulk Confirm", response.json()["error"])
        row = EventRegistration.objects.get(pk=outside[1].pk)
        self.assertEqual(row.status, "waitlist")
        self.assertIsNone(row.checked_in_at)

    def test_walk_in_who_fits_nobody_is_refused_and_stays_outside(self):
        event, invited_ids, outside = self._tonight()
        # Nobody left, so the group of six is full.

        response = self._admit(event, outside[0])

        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json()["success"])
        self.assertEqual(self._status(outside[0].pk), "not_selected")

    def test_walk_in_is_refused_outside_the_checkin_window(self):
        event, invited_ids, outside = self._closed_selection()  # in three days
        self._cancel(invited_ids[0])

        response = self._admit(event, outside[0])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._status(outside[0].pk), "not_selected")

    def test_walk_in_is_refused_on_a_cancelled_event(self):
        event, invited_ids, outside = self._tonight()
        self._cancel(invited_ids[0])
        MeetupEvent.objects.filter(pk=event.pk).update(is_cancelled=True)

        response = self._admit(event, outside[0])

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self._status(outside[0].pk), "not_selected")

    def test_other_statuses_are_still_refused(self):
        event, invited_ids, outside = self._tonight()

        response = self._admit(event, EventRegistration.objects.get(pk=invited_ids[0]))

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self._status(invited_ids[0]), "confirmed")

    def test_door_page_lists_the_not_selected_members(self):
        event, invited_ids, outside = self._tonight()

        response = self.client.get(f"/en/coach/events/{event.pk}/checkin/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {r.pk for r in response.context["not_selected_walk_ups"]},
            {r.pk for r in outside},
        )
        html = response.content.decode()
        self.assertIn('data-testid="not-selected-card"', html)
        for registration in outside:
            self.assertIn(
                f'data-promote-url="/api/events/{event.pk}/promote/{registration.pk}/"',
                html,
            )

    def test_door_page_of_an_ordinary_event_has_no_such_card(self):
        event = self._event(
            event_type="mixer",
            registration_mode="direct",
            group_size=None,
            planned_groups=None,
            date_time=timezone.now() + timedelta(hours=1),
        )

        response = self.client.get(f"/en/coach/events/{event.pk}/checkin/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('data-testid="not-selected-card"', response.content.decode())
