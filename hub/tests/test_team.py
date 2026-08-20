"""Tests for the Hub team-directory endpoint backing the CRM's ``/team`` page."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from crush_lu.models import CrushCoach, MeetupEvent

User = get_user_model()


class TeamAPITestCase(TestCase):
    def setUp(self):
        # No password: every test authenticates via ``force_authenticate``, and
        # hashing one per test costs ~2s each under ``manage.py test`` (the MD5
        # hasher in conftest.py only applies to pytest runs).
        self.staff = User.objects.create_user(
            username="team_staff",
            email="staff@crush.lu",
            is_staff=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.staff)

    def _event(self, **overrides):
        now = timezone.now()
        values = {
            "title": "Speed Dating — August",
            "description": "Une soirée de rencontres.",
            "event_type": "speed_dating",
            "location": "Casino 2000, Mondorf-les-Bains",
            "address": "Rue Flammang, Mondorf-les-Bains",
            "date_time": now + timedelta(days=7),
            "registration_deadline": now + timedelta(days=5),
            "is_published": True,
        }
        values.update(overrides)
        return MeetupEvent.objects.create(**values)


class TeamMembersViewTests(TeamAPITestCase):
    def test_non_staff_cannot_access_team_endpoint(self):
        non_staff = User.objects.create_user(username="member")
        self.client.force_authenticate(user=non_staff)

        response = self.client.get("/hub/team")

        self.assertEqual(response.status_code, 403)

    def test_staff_can_list_team_members_with_frontend_contract(self):
        coach_user = User.objects.create_user(
            username="tom@crush.lu",
            email="tom@crush.lu",
            first_name="Tom",
            last_name="Scheuer",
        )
        coach = CrushCoach.objects.create(
            user=coach_user,
            is_active=True,
            specializations="Young professionals, Students",
        )
        event = self._event()
        event.coaches.add(coach)

        response = self.client.get("/hub/team")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["items"]), 1)
        member = payload["items"][0]
        # Contract-exact key set: name, role, initial, gradient, events,
        # presence -- matches Codex_Marketing_CRM/frontend/app/team/page.tsx's
        # `TeamMember` type.
        self.assertEqual(
            set(member.keys()),
            {"name", "role", "initial", "gradient", "events", "presence"},
        )
        self.assertEqual(member["name"], "Tom Scheuer")
        self.assertEqual(member["role"], "Young professionals, Students")
        self.assertEqual(member["initial"], "T")
        self.assertEqual(member["events"], 1)
        self.assertEqual(member["presence"], "—")
        self.assertTrue(member["gradient"].startswith("linear-gradient("))

    def test_role_falls_back_to_coach_when_no_specialization(self):
        coach_user = User.objects.create_user(
            username="wesley@crush.lu", email="wesley@crush.lu", first_name="Wesley"
        )
        CrushCoach.objects.create(user=coach_user, is_active=True, specializations="")

        response = self.client.get("/hub/team")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["role"], "Coach")

    def test_name_falls_back_to_username_without_first_name(self):
        coach_user = User.objects.create_user(
            username="noname@crush.lu", email="noname@crush.lu"
        )
        CrushCoach.objects.create(user=coach_user, is_active=True)

        response = self.client.get("/hub/team")

        item = response.json()["items"][0]
        self.assertEqual(item["name"], "noname@crush.lu")
        self.assertEqual(item["initial"], "N")

    def test_inactive_coach_is_excluded(self):
        coach_user = User.objects.create_user(
            username="away@crush.lu", email="away@crush.lu", first_name="Away"
        )
        CrushCoach.objects.create(user=coach_user, is_active=False)

        response = self.client.get("/hub/team")

        self.assertEqual(response.json()["items"], [])

    def test_events_count_excludes_unassigned_events(self):
        coach_user = User.objects.create_user(
            username="busy@crush.lu", email="busy@crush.lu", first_name="Busy"
        )
        coach = CrushCoach.objects.create(user=coach_user, is_active=True)
        self._event()  # not assigned to this coach

        response = self.client.get("/hub/team")

        self.assertEqual(response.json()["items"][0]["events"], 0)
        self.assertEqual(coach.assigned_events.count(), 0)

    def test_trailing_slash_route_also_works(self):
        coach_user = User.objects.create_user(
            username="slash@crush.lu", email="slash@crush.lu", first_name="Slash"
        )
        CrushCoach.objects.create(user=coach_user, is_active=True)

        response = self.client.get("/hub/team/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["items"]), 1)
