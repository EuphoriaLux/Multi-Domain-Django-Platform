from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.utils import timezone
from rest_framework.test import APIClient

from crush_lu.models import CrushCoach, MeetupEvent
from hub.models import EventCoachAvailability
from hub.tests.test_partners import ThrottleIsolatedTestCase, make_partner


class HubEventsStaffingTests(ThrottleIsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.staff_user = get_user_model().objects.create_user(
            "hub_staff_coach",
            email="coach@crush.lu",
            password="pass",
            is_staff=True,
        )
        self.coach_profile = CrushCoach.objects.create(
            user=self.staff_user,
            is_active=True,
        )

        self.manager_user = get_user_model().objects.create_user(
            "hub_event_manager",
            email="manager@crush.lu",
            password="pass",
            is_staff=True,
        )
        self.manager_user.user_permissions.add(
            Permission.objects.get(codename="change_meetupevent")
        )

        self.client = APIClient()
        self.client.force_authenticate(self.staff_user)

        # Create upcoming event
        self.upcoming_event = MeetupEvent.objects.create(
            title_fr="Speed Dating À Venir",
            title_en="Upcoming Speed Dating",
            event_type="speed_dating",
            location="Urban Bar",
            date_time=timezone.now() + timedelta(days=5),
            registration_deadline=timezone.now() + timedelta(days=4),
            registration_fee="15.50",
            max_participants=20,
            is_published=True,
            is_cancelled=False,
            is_private_invitation=False,
        )

        # Create past event
        self.past_event = MeetupEvent.objects.create(
            title_fr="Soirée Passée",
            title_en="Past Mixer",
            event_type="mixer",
            location="Urban Bar",
            date_time=timezone.now() - timedelta(days=10),
            registration_deadline=timezone.now() - timedelta(days=11),
            registration_fee="10.00",
            max_participants=30,
            is_published=True,
            is_cancelled=False,
            is_private_invitation=False,
        )

    def test_hub_events_returns_all_by_default(self):
        res = self.client.get("/hub/events")
        self.assertEqual(res.status_code, 200)
        items = res.data["items"]
        ids = [item["id"] for item in items]
        self.assertIn(str(self.upcoming_event.pk), ids)
        self.assertIn(str(self.past_event.pk), ids)

        first = next(
            item for item in items if item["id"] == str(self.upcoming_event.pk)
        )
        self.assertEqual(first["title"], "Upcoming Speed Dating")
        self.assertEqual(first["location"], "Urban Bar")
        self.assertEqual(first["coachesNeeded"], 2)
        self.assertTrue("dateTime" in first)

    def test_hub_events_filters_past_when_requested(self):
        res = self.client.get("/hub/events?include_past=false")
        self.assertEqual(res.status_code, 200)
        ids = [item["id"] for item in res.data["items"]]
        self.assertIn(str(self.upcoming_event.pk), ids)
        self.assertNotIn(str(self.past_event.pk), ids)

    def test_declare_availability_and_withdraw(self):
        # 1. Declare availability with valid fields
        res = self.client.post(
            f"/hub/events/{self.upcoming_event.pk}/availability",
            {"role": "Accueil", "note": "Arrive à 19h"},
            format="json",
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["item"]["role"], "Accueil")
        self.assertEqual(res.data["item"]["status"], "available")
        self.assertEqual(res.data["item"]["note"], "Arrive à 19h")

        # Check in availabilities list
        list_res = self.client.get("/hub/events/availabilities")
        self.assertEqual(list_res.status_code, 200)
        match = [
            it
            for it in list_res.data["items"]
            if it["eventId"] == str(self.upcoming_event.pk)
            and it["coachEmail"] == self.staff_user.email
        ]
        self.assertEqual(len(match), 1)

        # 2. Declare availability with null note (should not 500)
        res_null_note = self.client.post(
            f"/hub/events/{self.upcoming_event.pk}/availability",
            {"role": "Accueil", "note": None},
            format="json",
        )
        self.assertEqual(res_null_note.status_code, 200)
        self.assertEqual(res_null_note.data["item"]["note"], "")

        # 3. Reject invalid role
        res_bad_role = self.client.post(
            f"/hub/events/{self.upcoming_event.pk}/availability",
            {"role": "NonExistentRole"},
            format="json",
        )
        self.assertEqual(res_bad_role.status_code, 400)

        # 4. Withdraw availability
        del_res = self.client.delete(
            f"/hub/events/{self.upcoming_event.pk}/availability"
        )
        self.assertEqual(del_res.status_code, 204)
        self.assertFalse(
            EventCoachAvailability.objects.filter(
                event=self.upcoming_event, user=self.staff_user
            ).exists()
        )

    def test_admin_assign_and_decline_coach(self):
        # Create availability
        avail = EventCoachAvailability.objects.create(
            event=self.upcoming_event,
            user=self.staff_user,
            role=EventCoachAvailability.Role.ANIMATION,
            status=EventCoachAvailability.Status.AVAILABLE,
        )

        # Coach without change_meetupevent permission is denied
        self.client.force_authenticate(self.staff_user)
        res_forbidden = self.client.patch(
            f"/hub/events/{self.upcoming_event.pk}/availability/{avail.pk}",
            {"status": "assigned"},
            format="json",
        )
        self.assertEqual(res_forbidden.status_code, 403)

        # Authenticate with event manager having change_meetupevent
        self.client.force_authenticate(self.manager_user)

        # Assign
        patch_res = self.client.patch(
            f"/hub/events/{self.upcoming_event.pk}/availability/{avail.pk}",
            {"status": "assigned"},
            format="json",
        )
        self.assertEqual(patch_res.status_code, 200)
        self.assertEqual(patch_res.data["item"]["status"], "assigned")
        assigned_at = patch_res.data["item"]["assignedAt"]
        self.assertIsNotNone(assigned_at)

        # Idempotent re-assignment preserves assigned_at
        patch_res_again = self.client.patch(
            f"/hub/events/{self.upcoming_event.pk}/availability/{avail.pk}",
            {"status": "assigned"},
            format="json",
        )
        self.assertEqual(patch_res_again.status_code, 200)
        self.assertEqual(patch_res_again.data["item"]["assignedAt"], assigned_at)

        # Coach profile should now be in event.coaches
        self.upcoming_event.refresh_from_db()
        self.assertTrue(
            self.upcoming_event.coaches.filter(pk=self.coach_profile.pk).exists()
        )

        # Decline / Unassign
        decline_res = self.client.patch(
            f"/hub/events/{self.upcoming_event.pk}/availability/{avail.pk}",
            {"status": "declined"},
            format="json",
        )
        self.assertEqual(decline_res.status_code, 200)
        self.assertEqual(decline_res.data["item"]["status"], "declined")
        self.upcoming_event.refresh_from_db()
        self.assertFalse(
            self.upcoming_event.coaches.filter(pk=self.coach_profile.pk).exists()
        )

    def test_assign_availability_validates_synthetic_and_numeric_ids(self):
        self.client.force_authenticate(self.manager_user)

        # Malformed synthetic ID
        res = self.client.patch(
            f"/hub/events/{self.upcoming_event.pk}/availability/coach-invalid",
            {"status": "assigned"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)

        # Mismatched event ID in synthetic ID
        res_mismatch = self.client.patch(
            f"/hub/events/{self.upcoming_event.pk}/availability/coach-9999-{self.coach_profile.pk}",
            {"status": "assigned"},
            format="json",
        )
        self.assertEqual(res_mismatch.status_code, 400)

        # Non-numeric standard ID
        res_non_digit = self.client.patch(
            f"/hub/events/{self.upcoming_event.pk}/availability/nonnumeric",
            {"status": "assigned"},
            format="json",
        )
        self.assertEqual(res_non_digit.status_code, 400)

    def test_assign_rejects_user_without_active_coach_profile(self):
        self.client.force_authenticate(self.manager_user)
        non_coach = get_user_model().objects.create_user("plain_staff", is_staff=True)
        avail = EventCoachAvailability.objects.create(
            event=self.upcoming_event,
            user=non_coach,
            role=EventCoachAvailability.Role.ANIMATION,
            status=EventCoachAvailability.Status.AVAILABLE,
        )

        res = self.client.patch(
            f"/hub/events/{self.upcoming_event.pk}/availability/{avail.pk}",
            {"status": "assigned"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("active CrushCoach profile", res.data["error"])

    def test_availabilities_feed_scopes_out_cancelled_or_unpublished_events(self):
        cancelled_event = MeetupEvent.objects.create(
            title_en="Cancelled Party",
            event_type="mixer",
            location="Urban Bar",
            date_time=timezone.now() + timedelta(days=3),
            registration_deadline=timezone.now(),
            registration_fee="10.00",
            max_participants=20,
            is_published=True,
            is_cancelled=True,
        )
        EventCoachAvailability.objects.create(
            event=cancelled_event,
            user=self.staff_user,
            role=EventCoachAvailability.Role.ANIMATION,
            status=EventCoachAvailability.Status.AVAILABLE,
        )

        res = self.client.get("/hub/events/availabilities")
        self.assertEqual(res.status_code, 200)
        event_ids = [it["eventId"] for it in res.data["items"]]
        self.assertNotIn(str(cancelled_event.pk), event_ids)

    def test_partner_events_fallback_matches_unlinked_by_location_name(self):
        partner = make_partner(name="Urban Bar")
        other_partner = make_partner(name="De Gudde Willen")

        # Explicitly linked event
        linked_event = MeetupEvent.objects.create(
            title_en="Explicitly linked",
            event_type="mixer",
            location="Somewhere Else",
            partner=partner,
            date_time=timezone.now() + timedelta(days=1),
            registration_deadline=timezone.now(),
            registration_fee="10.00",
            max_participants=20,
            is_published=True,
            is_cancelled=False,
        )

        # Explicitly linked to other partner with location="Urban Bar"
        other_event = MeetupEvent.objects.create(
            title_en="Belongs to other partner",
            event_type="mixer",
            location="Urban Bar",
            partner=other_partner,
            date_time=timezone.now() + timedelta(days=2),
            registration_deadline=timezone.now(),
            registration_fee="10.00",
            max_participants=20,
            is_published=True,
            is_cancelled=False,
        )

        # Default query: strict attribution, only linked events returned
        res_default = self.client.get(f"/hub/locations/{partner.pk}/events")
        self.assertEqual(res_default.status_code, 200)
        default_ids = [it["id"] for it in res_default.data["items"]]
        self.assertEqual(default_ids, [str(linked_event.pk)])

        # With include_unlinked=true: also matches unlinked events with matching venue name
        res = self.client.get(
            f"/hub/locations/{partner.pk}/events?include_unlinked=true"
        )
        self.assertEqual(res.status_code, 200)
        event_ids = [it["id"] for it in res.data["items"]]

        # Should include:
        # 1. self.upcoming_event (location="Urban Bar", partner=None) -> matched by name fallback
        # 2. self.past_event (location="Urban Bar", partner=None) -> matched by name fallback
        # 3. linked_event (partner=partner) -> matched by partner_id
        self.assertIn(str(self.upcoming_event.pk), event_ids)
        self.assertIn(str(self.past_event.pk), event_ids)
        self.assertIn(str(linked_event.pk), event_ids)

        # Should NOT include other_event (even though location="Urban Bar", it has partner=other_partner)
        self.assertNotIn(str(other_event.pk), event_ids)

        # Disambiguation: if a second partner shares the exact name, fallback is suppressed
        make_partner(name="Urban Bar")
        res_ambiguous = self.client.get(
            f"/hub/locations/{partner.pk}/events?include_unlinked=true"
        )
        self.assertEqual(res_ambiguous.status_code, 200)
        ambiguous_ids = [it["id"] for it in res_ambiguous.data["items"]]
        # Unlinked events should NOT be returned now due to ambiguous name
        self.assertNotIn(str(self.upcoming_event.pk), ambiguous_ids)
        self.assertIn(str(linked_event.pk), ambiguous_ids)
