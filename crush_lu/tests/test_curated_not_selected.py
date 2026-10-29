"""Closing a curated selection: everyone without a place is "Not selected".

Until now an applicant left out of the invited groups stayed "applied" and was
only told their application "stays in the pool", so on event 26 seventy people
kept hoping until the evening had passed. Closing the selection is the final
word: every open application without a place becomes ``not_selected``, which
holds no seat and is not a cancellation, and the member gets one notice (email,
bell, push).

Spec: ai-memory-hub/specs/2026-10-29-crush-curated-auto-selection.md
Run with: pytest crush_lu/tests/test_curated_not_selected.py -v
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from crush_lu.models import EmailPreference
from crush_lu.models.events import (
    CuratedEventGroupMembership,
    CuratedGroupNotification,
    EventRegistration,
    EventRegistrationPreference,
    MeetupEvent,
)
from crush_lu.models.notifications import Notification
from crush_lu.models.profiles import CrushProfile, UserDataConsent
from crush_lu.services.curated_group_insights import (
    NEXT_CHECK_IN_AND_LOCK,
    NEXT_CLOSE_SELECTION,
    coach_group_panel,
)
from crush_lu.services.curated_group_notifications import (
    deliver_curated_group_notifications,
    enqueue_reserve_notifications,
)
from crush_lu.services.curated_group_workflow import (
    approve_current_generation,
    close_curated_selection,
    generate_group_projection,
)

User = get_user_model()
BELL_TYPE = "event_curated_not_selected"


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class CloseCuratedSelectionTests(TestCase):
    def setUp(self):
        # Every test's viewer is the same user pk on SQLite and they share one
        # @ratelimit counter (AGENTS.md traps).
        cache.clear()
        self.client.defaults["HTTP_HOST"] = "crush.lu"

    def _event(self, **overrides):
        values = {
            "title": "Closing evening",
            "description": "Testing the closed selection",
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

    def _applicant(self, event, index, *, language="en"):
        user = User.objects.create_user(
            username=f"close-{event.pk}-{index}@example.com",
            email=f"close-{event.pk}-{index}@example.com",
            password="testpass123",
            first_name=f"Member{index}",
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
            preferred_language=language,
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

    def _invited(self, *, languages=None, leave_in_group_uninvited=0, **overrides):
        """Generate, approve and seat one group of six the way Invite does.

        Eight applicants, so two are left outside the group.
        """
        event = self._event(**overrides)
        languages = languages or ["en"] * 8
        registrations = [
            self._applicant(event, i, language=lang) for i, lang in enumerate(languages)
        ]
        generate_group_projection(event, deterministic_seed="close")
        approved = approve_current_generation(event)
        invited_ids = list(approved.applied_registration_ids)
        EventRegistration.objects.filter(
            pk__in=invited_ids[leave_in_group_uninvited:]
        ).update(status="confirmed")
        outside = [r for r in registrations if r.pk not in invited_ids]
        mail.outbox = []
        return event, invited_ids, outside

    def _status(self, pk):
        return EventRegistration.objects.get(pk=pk).status

    # -- refusals -----------------------------------------------------------

    def test_refused_before_the_application_deadline(self):
        event = self._event(registration_deadline=timezone.now() + timedelta(hours=2))
        self._applicant(event, 0)
        with self.assertRaisesMessage(ValidationError, "application window"):
            close_curated_selection(event)

    def test_refused_while_nobody_holds_a_seat(self):
        event = self._event()
        applicants = [self._applicant(event, i) for i in range(7)]
        generate_group_projection(event, deterministic_seed="close")
        approve_current_generation(event)
        with self.assertRaisesMessage(ValidationError, "nobody holds a seat"):
            close_curated_selection(event)
        self.assertEqual({self._status(r.pk) for r in applicants}, {"applied"})

    def test_refused_on_a_cancelled_event(self):
        event, _, _ = self._invited()
        MeetupEvent.objects.filter(pk=event.pk).update(is_cancelled=True)
        with self.assertRaisesMessage(ValidationError, "cancelled"):
            close_curated_selection(event)

    # -- the close itself ---------------------------------------------------

    def test_marks_only_applicants_without_a_place(self):
        event, invited_ids, outside = self._invited()
        self.assertEqual(len(outside), 2)

        closed = close_curated_selection(event)

        self.assertEqual(set(closed.not_selected_ids), {r.pk for r in outside})
        self.assertEqual({self._status(r.pk) for r in outside}, {"not_selected"})
        self.assertEqual({self._status(pk) for pk in invited_ids}, {"confirmed"})
        self.assertEqual(closed.placed_uninvited_ids, ())
        self.assertEqual(len(mail.outbox), len(outside))
        self.assertIn("Your application for Closing evening", mail.outbox[0].subject)
        self.assertEqual(
            Notification.objects.filter(notification_type=BELL_TYPE).count(),
            len(outside),
        )
        self.assertEqual(closed.delivery.sent, len(outside))

    def test_member_placed_in_a_group_without_a_seat_is_left_alone(self):
        event, invited_ids, outside = self._invited(leave_in_group_uninvited=1)
        placed = invited_ids[0]

        closed = close_curated_selection(event)

        self.assertEqual(closed.placed_uninvited_ids, (placed,))
        self.assertEqual(self._status(placed), "applied")
        self.assertTrue(
            CuratedEventGroupMembership.objects.filter(
                registration_id=placed, released_at__isnull=True
            ).exists()
        )

    def test_closing_twice_tells_nobody_twice(self):
        event, _, outside = self._invited()
        close_curated_selection(event)
        mail.outbox = []

        again = close_curated_selection(event)

        self.assertEqual(again.not_selected_ids, ())
        self.assertEqual(mail.outbox, [])
        self.assertEqual(
            CuratedGroupNotification.objects.filter(
                event=event, kind=CuratedGroupNotification.Kind.NOT_SELECTED
            ).count(),
            len(outside),
        )

    def test_pending_reserve_notice_becomes_stale(self):
        event, invited_ids, outside = self._invited()
        generation = (
            CuratedEventGroupMembership.objects.filter(registration_id=invited_ids[0])
            .values_list("group__generation", flat=True)
            .first()
        )
        reserve_ids = enqueue_reserve_notifications(
            event.pk, generation=generation, exclude_registration_ids=invited_ids
        )
        self.assertTrue(reserve_ids)

        close_curated_selection(event)
        deliver_curated_group_notifications(notice_ids=reserve_ids, drain=True)

        self.assertEqual(
            set(
                CuratedGroupNotification.objects.filter(pk__in=reserve_ids).values_list(
                    "status", flat=True
                )
            ),
            {CuratedGroupNotification.Status.CANCELLED},
        )

    def test_master_unsubscribe_gets_the_bell_but_no_email(self):
        event, _, outside = self._invited()
        prefs = EmailPreference.get_or_create_for_user(outside[0].user)
        prefs.unsubscribed_all = True
        prefs.save()

        close_curated_selection(event)

        self.assertNotIn(outside[0].user.email, [m.to[0] for m in mail.outbox])
        self.assertTrue(
            Notification.objects.filter(
                user=outside[0].user, notification_type=BELL_TYPE
            ).exists()
        )
        notice = CuratedGroupNotification.objects.get(
            registration_id=outside[0].pk,
            kind=CuratedGroupNotification.Kind.NOT_SELECTED,
        )
        self.assertEqual(notice.status, CuratedGroupNotification.Status.SENT)
        self.assertIn("Email not allowed", notice.last_error)

    def test_email_is_in_each_members_language(self):
        event, _, outside = self._invited(languages=["fr", "de"] * 4)

        close_curated_selection(event)

        subjects = {message.to[0]: message.subject for message in mail.outbox}
        expected = {"fr": "Votre candidature pour", "de": "Deine Bewerbung für"}
        for registration in outside:
            language = registration.user.crushprofile.preferred_language
            self.assertIn(expected[language], subjects[registration.user.email])

    # -- the guard and the surfaces -----------------------------------------

    def test_not_selected_is_refused_off_curated_events(self):
        event = self._event(registration_mode="direct", group_size=None)
        registration = self._applicant(event, 0)
        EventRegistration.objects.filter(pk=registration.pk).update(status="confirmed")
        registration.refresh_from_db()
        registration.status = "not_selected"
        with self.assertRaises(ValidationError) as caught:
            registration.full_clean()
        self.assertIn("Not selected", str(caught.exception))

    def test_event_page_and_my_events_tell_the_member(self):
        event, _, outside = self._invited()
        close_curated_selection(event)
        self.client.force_login(outside[0].user)

        page = self.client.get(f"/en/events/{event.pk}/")
        mine = self.client.get("/en/my-events/")

        self.assertContains(page, "No place this time")
        self.assertNotContains(page, "Withdraw Application")
        self.assertNotContains(page, "Cancel Registration")
        self.assertNotContains(page, "reviewing group options")
        self.assertNotContains(page, f"/events/{event.pk}/ticket/")
        self.assertContains(mine, "Not selected")
        self.assertNotContains(mine, f"/events/{event.pk}/ticket/")

    def test_cancelling_a_not_selected_application_is_refused(self):
        event, _, outside = self._invited()
        close_curated_selection(event)
        self.client.force_login(outside[0].user)
        mail.outbox = []

        response = self.client.post(f"/en/events/{event.pk}/cancel/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._status(outside[0].pk), "not_selected")
        self.assertEqual(mail.outbox, [])

    def test_coach_panel_names_the_next_step_and_lists_the_not_selected(self):
        event, _, outside = self._invited()
        self.assertEqual(coach_group_panel(event)["next_action"], NEXT_CLOSE_SELECTION)

        close_curated_selection(event)

        after = coach_group_panel(event)
        self.assertEqual(after["next_action"], NEXT_CHECK_IN_AND_LOCK)
        self.assertEqual(len(after["left_out"]["not_selected"]), len(outside))
