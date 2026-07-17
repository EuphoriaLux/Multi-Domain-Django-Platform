from datetime import date, timedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from allauth.socialaccount.models import SocialAccount

from crush_lu.models import CrushProfile, CrushConnectMembership, EventRegistration, MeetupEvent, CrushCoach
from crush_lu.models.moderation import UserBlock
from crush_connect_lobby.models import (
    EventLobbyParticipation,
    EventMeetSignal,
    EventMeetingConfirmation,
    ConfirmedEncounter,
    ConfirmedEncounterRemovalRequest,
)
from crush_connect_lobby import services

User = get_user_model()


class LobbyTestCase(TestCase):
    def setUp(self):
        # Ensure global launch flag is True for tests
        self.old_launched = getattr(settings, "CRUSH_CONNECT_LAUNCHED", False)
        settings.CRUSH_CONNECT_LAUNCHED = True

        # Create a coach
        self.coach_user = User.objects.create_user(username="coach_u", password="123")
        self.coach = CrushCoach.objects.create(
            user=self.coach_user,
            phone_number="+3521111",
            is_active=True
        )

        # Create an event
        self.event = MeetupEvent.objects.create(
            title="Lobby Party",
            description="Mixer party",
            event_type="mixer",
            date_time=timezone.now() - timedelta(minutes=30),  # started 30 mins ago
            duration_minutes=120,
            location="City Center",
            address="1 main st",
            max_participants=20,
            registration_deadline=timezone.now() - timedelta(days=1),
            is_published=True,
        )

    def tearDown(self):
        settings.CRUSH_CONNECT_LAUNCHED = self.old_launched

    def _make_member(self, username, onboarded=True, lobby_consent=True, has_photo=True):
        user = User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="password123",
            first_name=username.title()
        )
        
        # Create standard profile
        profile = CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 5, 10),
            gender="M",
            is_approved=True,
            verification_status="verified",
        )
        
        if has_photo:
            profile.photo_1 = SimpleUploadedFile("face.jpg", b"imagebytes", content_type="image/jpeg")
            profile.save()

        # Connect LuxID
        SocialAccount.objects.create(user=user, provider="luxid", uid=username)

        # Create Connect membership
        membership = CrushConnectMembership.objects.create(
            user=user,
            onboarded_at=timezone.now() if onboarded else None,
            lobby_consent_given=lobby_consent,
            photo_share_consent=True,
        )

        return user

    def _check_in(self, user, event, status="attended"):
        reg = EventRegistration.objects.create(
            event=event,
            user=user,
            status=status,
            payment_confirmed=True
        )
        return reg

    def test_eligibility_conditions(self):
        # 1. Fully eligible user
        u1 = self._make_member("member1")
        self._check_in(u1, self.event)
        self.assertTrue(services.check_eligibility(u1, self.event))

        # 2. Not checked in
        u2 = self._make_member("member2")
        self.assertFalse(services.check_eligibility(u2, self.event))

        # 3. Checked in but registration status is only 'confirmed' (not 'attended')
        u3 = self._make_member("member3")
        self._check_in(u3, self.event, status="confirmed")
        self.assertFalse(services.check_eligibility(u3, self.event))

        # 4. No consent given
        u4 = self._make_member("member4", lobby_consent=False)
        self._check_in(u4, self.event)
        self.assertFalse(services.check_eligibility(u4, self.event))

        # 5. Not onboarded
        u5 = self._make_member("member5", onboarded=False)
        self._check_in(u5, self.event)
        self.assertFalse(services.check_eligibility(u5, self.event))

        # 6. No primary photo
        u6 = self._make_member("member6", has_photo=False)
        self._check_in(u6, self.event)
        self.assertFalse(services.check_eligibility(u6, self.event))

    def test_evaluate_and_join_lobby(self):
        user = self._make_member("joiner")
        self._check_in(user, self.event)
        
        part = services.evaluate_and_join_lobby(user, self.event)
        self.assertIsNotNone(part)
        self.assertEqual(part.user, user)
        self.assertEqual(part.event, self.event)

        # Joining again is idempotent
        part2 = services.evaluate_and_join_lobby(user, self.event)
        self.assertEqual(part.id, part2.id)

    def test_phase_transition(self):
        # Live phase
        self.assertEqual(services.get_lobby_phase(self.event), "live")

        # End of event (3 hours later)
        self.event.date_time = timezone.now() - timedelta(hours=3)
        self.event.save()
        self.assertEqual(services.get_lobby_phase(self.event), "recap")

        # Expired recap (50 hours later)
        self.event.date_time = timezone.now() - timedelta(hours=52)
        self.event.save()
        self.assertEqual(services.get_lobby_phase(self.event), "closed")

    def test_signal_quota_and_mutual_reveal(self):
        u1 = self._make_member("u1")
        u2 = self._make_member("u2")
        u3 = self._make_member("u3")
        u4 = self._make_member("u4")
        u5 = self._make_member("u5")

        self._check_in(u1, self.event)
        self._check_in(u2, self.event)
        self._check_in(u3, self.event)
        self._check_in(u4, self.event)
        self._check_in(u5, self.event)

        services.evaluate_and_join_lobby(u1, self.event)
        services.evaluate_and_join_lobby(u2, self.event)
        services.evaluate_and_join_lobby(u3, self.event)
        services.evaluate_and_join_lobby(u4, self.event)
        services.evaluate_and_join_lobby(u5, self.event)

        h2 = services.generate_opaque_handle(u2, self.event)
        h3 = services.generate_opaque_handle(u3, self.event)
        h4 = services.generate_opaque_handle(u4, self.event)
        h5 = services.generate_opaque_handle(u5, self.event)

        # u1 signals u2
        res1 = services.send_meet_signal(u1, h2, self.event)
        self.assertEqual(res1["status"], "sent")

        # u1 signals u3
        res2 = services.send_meet_signal(u1, h3, self.event)
        self.assertEqual(res2["status"], "sent")

        # u1 signals u4
        res3 = services.send_meet_signal(u1, h4, self.event)
        self.assertEqual(res3["status"], "sent")

        # u1 tries to signal u5 but has reached quota
        with self.assertRaises(ValidationError):
            services.send_meet_signal(u1, h5, self.event)

        # u2 signals u1 -> mutual reveal
        h1 = services.generate_opaque_handle(u1, self.event)
        res_mutual = services.send_meet_signal(u2, h1, self.event)
        self.assertEqual(res_mutual["status"], "mutual")
        self.assertEqual(res_mutual["first_name"], u1.first_name)

    def test_anonymous_pre_reveal_payload(self):
        u1 = self._make_member("u1")
        u2 = self._make_member("u2")
        self._check_in(u1, self.event)
        self._check_in(u2, self.event)
        services.evaluate_and_join_lobby(u1, self.event)
        services.evaluate_and_join_lobby(u2, self.event)

        roster = services.list_participants(u1, self.event)
        self.assertEqual(len(roster), 1)
        # Verify first_name is withheld
        self.assertFalse(roster[0]["is_revealed"])
        self.assertNil = roster[0]["first_name"] is None

        # After mutual reveal, name is visible
        h1 = services.generate_opaque_handle(u1, self.event)
        services.send_meet_signal(u1, services.generate_opaque_handle(u2, self.event), self.event)
        services.send_meet_signal(u2, h1, self.event)

        roster_mutual = services.list_participants(u1, self.event)
        self.assertTrue(roster_mutual[0]["is_revealed"])
        self.assertEqual(roster_mutual[0]["first_name"], u2.first_name)

    def test_recap_and_confirmed_encounter(self):
        u1 = self._make_member("u1")
        u2 = self._make_member("u2")
        self._check_in(u1, self.event)
        self._check_in(u2, self.event)
        services.evaluate_and_join_lobby(u1, self.event)
        services.evaluate_and_join_lobby(u2, self.event)

        # Fast forward time to recap phase
        self.event.date_time = timezone.now() - timedelta(hours=3)
        self.event.save()

        h1 = services.generate_opaque_handle(u1, self.event)
        h2 = services.generate_opaque_handle(u2, self.event)

        # u1 confirms u2
        res1 = services.confirm_meeting(u1, h2, self.event)
        self.assertEqual(res1["status"], "confirmed")

        # u2 confirms u1 -> ConfirmedEncounter created
        res2 = services.confirm_meeting(u2, h1, self.event)
        self.assertEqual(res2["status"], "encounter_created")
        self.assertEqual(res2["first_name"], u1.first_name)

        # Check people I've met
        met_u1 = services.get_people_ive_met(u1)
        self.assertEqual(len(met_u1), 1)
        self.assertEqual(met_u1[0]["first_name"], u2.first_name)

    def test_blocking_removes_immediately(self):
        u1 = self._make_member("u1")
        u2 = self._make_member("u2")
        self._check_in(u1, self.event)
        self._check_in(u2, self.event)
        services.evaluate_and_join_lobby(u1, self.event)
        services.evaluate_and_join_lobby(u2, self.event)

        # Both can see each other initially
        self.assertEqual(len(services.list_participants(u1, self.event)), 1)

        # u1 blocks u2
        UserBlock.objects.create(blocker=u1, blocked=u2, reason="other")

        # Symmetrically hidden
        self.assertEqual(len(services.list_participants(u1, self.event)), 0)
        self.assertEqual(len(services.list_participants(u2, self.event)), 0)

    def test_moderated_removal(self):
        u1 = self._make_member("u1")
        u2 = self._make_member("u2")
        self._check_in(u1, self.event)
        self._check_in(u2, self.event)
        services.evaluate_and_join_lobby(u1, self.event)
        services.evaluate_and_join_lobby(u2, self.event)

        # Create encounter
        self.event.date_time = timezone.now() - timedelta(hours=3)
        self.event.save()
        h1 = services.generate_opaque_handle(u1, self.event)
        h2 = services.generate_opaque_handle(u2, self.event)
        services.confirm_meeting(u1, h2, self.event)
        services.confirm_meeting(u2, h1, self.event)

        encounters = services.get_people_ive_met(u1)
        self.assertEqual(len(encounters), 1)
        encounter_id = encounters[0]["encounter_id"]

        # Request removal
        req = services.request_encounter_removal(u1, encounter_id, reason="privacy")
        self.assertEqual(req.status, "pending")

        # Hides immediately
        self.assertEqual(len(services.get_people_ive_met(u1)), 0)
        self.assertEqual(len(services.get_people_ive_met(u2)), 0)

        # Review removal - Coach approves
        services.review_encounter_removal(self.coach, req.id, approved=True, resolution_notes="Done")
        encounter = ConfirmedEncounter.objects.get(id=encounter_id)
        self.assertEqual(encounter.status, "removed")
