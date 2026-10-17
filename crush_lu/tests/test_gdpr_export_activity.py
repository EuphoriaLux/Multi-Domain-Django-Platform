"""#1194: the export covers the member's own activity, never the other person.

Connect, sparks and gifts sent, payments and Premium, referrals, safety tools
and the event lobby. The rule under test: the member's own content, dates and
status are exported; the other person's identity, received secrets, staff
notes and credentials are not.
"""

import json
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.utils import timezone

from crush_lu.models.profiles import CrushProfile
from crush_lu.views_account import export_user_data

User = get_user_model()

OTHER_EMAIL = "zelda-counterpart@example.com"
OTHER_NAME = "Zelda"


class ExportMemberActivityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            "act@example.com", "act@example.com", "pw12345678", first_name="Ann"
        )
        self.other = User.objects.create_user(
            OTHER_EMAIL, OTHER_EMAIL, "pw12345678", first_name=OTHER_NAME
        )
        for user, gender in ((self.user, "F"), (self.other, "M")):
            CrushProfile.objects.create(
                user=user,
                date_of_birth=date(1995, 1, 1),
                gender=gender,
                location="canton-luxembourg",
            )

    def _export(self):
        request = RequestFactory().get("/")
        request.user = User.objects.get(pk=self.user.pk)
        return json.loads(export_user_data(request).content)

    def _assert_other_member_absent(self, data):
        dump = json.dumps(data, ensure_ascii=False)
        self.assertNotIn(OTHER_EMAIL, dump)
        self.assertNotIn(OTHER_NAME, dump)

    def _event(self):
        from crush_lu.models import MeetupEvent

        return MeetupEvent.objects.create(
            title="Lobby night",
            description="d",
            event_type="mixer",
            date_time=timezone.now() - timedelta(hours=2),
            location="Lux",
            address="1 Street",
            max_participants=20,
            registration_deadline=timezone.now() - timedelta(days=1),
            is_published=True,
        )

    def test_empty_member_gets_every_section_without_errors(self):
        data = self._export()
        self.assertIsNone(data["crush_connect"]["membership"])
        for key in ("requests", "cards", "chats", "coffee_dates"):
            self.assertEqual(data["crush_connect"][key], [])
        for key in (
            "sparks_sent",
            "journey_gifts_sent",
            "payments",
            "premium_memberships",
        ):
            self.assertEqual(data[key], [])
        self.assertIsNone(data["crush_connect"]["waitlist"])
        self.assertEqual(data["crush_connect"]["sessions"], [])
        self.assertEqual(data["crush_connect"]["cycle_feedback"], [])
        self.assertEqual(data["referrals"]["your_codes"], [])
        self.assertEqual(data["referrals"]["you_were_referred"], [])
        self.assertEqual(data["event_lobby"]["removal_requests_you_made"], [])
        self.assertEqual(data["event_lobby"]["lobbies_joined"], [])
        self.assertEqual(data["crush_connect"]["coach_picks"], [])
        self.assertEqual(data["safety"]["blocks_made"], [])
        self.assertEqual(data["event_lobby"]["meet_signals_sent"], [])

    def test_connect_membership_requests_cards_and_own_chat_text(self):
        from crush_lu.models import CrushConnectMembership
        from crush_lu.models.crush_connect_cycle import (
            ConnectChatMessage,
            ConnectCoffeeDate,
            ConnectCycleCard,
            ConnectTemporaryChat,
            ConnectWeeklyRequest,
            ConnectWeekSession,
        )

        CrushConnectMembership.objects.create(
            user=self.user,
            onboarded_at=timezone.now(),
            smoking="never",
            excluded_by_coach=True,
            exclusion_reason="staff-only reason",
        )
        mine = ConnectWeekSession.objects.create(user=self.user)
        theirs = ConnectWeekSession.objects.create(user=self.other)
        sent = ConnectWeeklyRequest.objects.create(
            session=mine,
            requester=self.user,
            recipient=self.other,
            message="my own hello",
            expires_at=timezone.now() + timedelta(days=1),
        )
        ConnectWeeklyRequest.objects.create(
            session=theirs,
            requester=self.other,
            recipient=self.user,
            message="their private words",
            expires_at=timezone.now() + timedelta(days=1),
        )
        ConnectCycleCard.objects.create(
            session=mine,
            day_number=1,
            card_index=1,
            target_user=self.other,
            generated_date=timezone.localdate(),
            answers_json={"guesses": {"7": True}, "gate_align": 3},
            is_completed=True,
            completed_at=timezone.now(),
        )
        chat = ConnectTemporaryChat.objects.create(
            request=sent,
            participant_1=self.user,
            participant_2=self.other,
            expires_at=timezone.now() + timedelta(days=1),
        )
        ConnectChatMessage.objects.create(chat=chat, sender=self.user, message="mine")
        ConnectChatMessage.objects.create(
            chat=chat, sender=self.other, message="their chat text"
        )
        ConnectCoffeeDate.objects.create(
            chat=chat,
            proposer=self.other,
            custom_venue_name="Café Test",
            proposed_date=timezone.localdate() + timedelta(days=2),
            proposed_time_slot="18:30",
        )
        from crush_lu.models import CrushConnectWaitlist
        from crush_lu.models.crush_connect_cycle import ConnectCycleFeedback

        CrushConnectWaitlist.objects.create(
            user=self.user,
            notification_preference=True,
            payment_confirmed=True,
            payment_date=timezone.now(),
            confirmed_by=self.other,
        )
        ConnectCycleFeedback.objects.create(
            session=mine, user=self.user, comment="my week feedback"
        )
        from crush_lu.models import ConnectCoachPick, CrushCoach

        coach = CrushCoach.objects.create(
            user=User.objects.create_user(
                "pickcoach@example.com", "pickcoach@example.com", "x"
            ),
            bio="b",
        )
        ConnectCoachPick.objects.create(
            coach=coach,
            member=self.user,
            candidate=self.other,
            note="coach note about the candidate",
            status="accepted",
            responded_at=timezone.now(),
        )
        # The member as someone else's candidate stays out.
        ConnectCoachPick.objects.create(
            coach=coach, member=self.other, candidate=self.user, status="pending"
        )

        data = self._export()
        connect = data["crush_connect"]
        self.assertEqual(len(connect["coach_picks"]), 1)
        self.assertIsNotNone(connect["coach_picks"][0]["responded_at"])
        self.assertNotIn("coach note about the candidate", json.dumps(connect))
        self.assertNotIn("pickcoach@example.com", json.dumps(connect))
        self.assertIsNotNone(connect["cards"][0]["generated_at"])
        self.assertTrue(connect["waitlist"]["payment_confirmed"])
        self.assertNotIn("confirmed_by", connect["waitlist"])
        self.assertEqual(connect["cycle_feedback"][0]["comment"], "my week feedback")
        # Only the member's own week; the highlighted member never appears.
        self.assertEqual(len(connect["sessions"]), 1)
        self.assertIsNotNone(connect["sessions"][0]["started_at"])
        self.assertNotIn("compatibility_highlight_user", json.dumps(connect))
        self.assertIsNotNone(connect["cycle_feedback"][0]["week_started_at"])
        membership = connect["membership"]
        self.assertEqual(membership["smoking"], "never")
        for hidden in ("excluded_by_coach", "exclusion_reason", "excluded_by"):
            self.assertNotIn(hidden, membership)
        directions = {r["direction"]: r for r in connect["requests"]}
        self.assertEqual(directions["sent"]["your_message"], "my own hello")
        self.assertIsNone(directions["received"]["your_message"])
        self.assertEqual(connect["cards"][0]["completed"], True)
        self.assertNotIn("guesses", connect["cards"][0])
        self.assertNotIn("gate_align", json.dumps(connect))
        self.assertEqual(
            [m["text"] for m in connect["chats"][0]["messages_you_sent"]], ["mine"]
        )
        self.assertEqual(connect["coffee_dates"][0]["venue"], "Café Test")
        self.assertFalse(connect["coffee_dates"][0]["proposed_by_you"])
        self.assertIsNone(connect["coffee_dates"][0]["venue_address"])
        dump = json.dumps(data, ensure_ascii=False)
        for secret in ("their private words", "their chat text", "staff-only reason"):
            self.assertNotIn(secret, dump)
        self._assert_other_member_absent(data)

    def test_curated_coffee_venue_exports_the_venue_address(self):
        from hub.models import Location

        from crush_lu.models.crush_connect_cycle import (
            ConnectCoffeeDate,
            ConnectTemporaryChat,
            ConnectWeeklyRequest,
            ConnectWeekSession,
        )

        venue = Location.objects.create(
            name="Café de Paris",
            address="Place d'Armes 1",
            city="Luxembourg",
            max_capacity=50,
            partnership_stage="Active",
            notes="internal partner note",
        )
        request = ConnectWeeklyRequest.objects.create(
            session=ConnectWeekSession.objects.create(user=self.user),
            requester=self.user,
            recipient=self.other,
            expires_at=timezone.now() + timedelta(days=1),
        )
        chat = ConnectTemporaryChat.objects.create(
            request=request,
            participant_1=self.user,
            participant_2=self.other,
            expires_at=timezone.now() + timedelta(days=1),
        )
        ConnectCoffeeDate.objects.create(
            chat=chat,
            proposer=self.user,
            venue_location=venue,
            proposed_date=timezone.localdate() + timedelta(days=2),
            proposed_time_slot="18:30",
        )

        coffee = self._export()["crush_connect"]["coffee_dates"][0]
        self.assertEqual(coffee["venue"], "Café de Paris")
        self.assertEqual(coffee["venue_address"], "Place d'Armes 1")
        self.assertTrue(coffee["proposed_by_you"])
        self.assertNotIn("internal partner note", json.dumps(coffee))

    def test_membership_registry_covers_every_field(self):
        from crush_lu.models import CrushConnectMembership
        from crush_lu.views_account import ACCOUNT_EXPORT_CONNECT_MEMBERSHIP_EXCLUDED

        CrushConnectMembership.objects.create(user=self.user)
        exported = set(self._export()["crush_connect"]["membership"])
        names = [f.name for f in CrushConnectMembership._meta.concrete_fields]
        names += [f.name for f in CrushConnectMembership._meta.many_to_many]
        missing = sorted(
            n
            for n in names
            if n not in exported and n not in ACCOUNT_EXPORT_CONNECT_MEMBERSHIP_EXCLUDED
        )
        self.assertEqual(missing, [])
        real = {f.name for f in CrushConnectMembership._meta.get_fields()}
        for name, reason in ACCOUNT_EXPORT_CONNECT_MEMBERSHIP_EXCLUDED.items():
            self.assertIn(name, real)
            self.assertTrue(reason)

    def test_sparks_and_gifts_sent_keep_own_text_only(self):
        from crush_lu.models import CrushSpark, JourneyGift

        event = self._event()
        CrushSpark.objects.create(
            event=event,
            sender=self.user,
            recipient=self.other,
            sender_description="red scarf",
            sender_message="my note",
            coach_notes="coach-only note",
            is_sender_revealed=True,
            revealed_at=timezone.now(),
            coach_assigned_at=timezone.now(),
            journey_created_at=timezone.now(),
        )
        CrushSpark.objects.create(
            event=event,
            sender=self.other,
            recipient=self.user,
            sender_description="received secret",
            sender_message="from them",
        )
        JourneyGift.objects.create(
            sender=self.user,
            recipient_name="Recipient Person",
            recipient_email="recipient@example.com",
            sender_message="for you",
            date_first_met=date(2024, 1, 1),
            location_first_met="Lux",
        )

        data = self._export()
        self.assertEqual(len(data["sparks_sent"]), 1)
        spark = data["sparks_sent"][0]
        self.assertEqual(spark["your_description"], "red scarf")
        self.assertEqual(spark["your_message"], "my note")
        self.assertTrue(spark["revealed_to_recipient"])
        self.assertIsNotNone(spark["revealed_at"])
        self.assertIsNotNone(spark["coach_assigned_at"])
        self.assertIsNotNone(spark["journey_created_at"])
        gift = data["journey_gifts_sent"][0]
        self.assertEqual(gift["your_message"], "for you")
        self.assertNotIn("gift_code", gift)
        dump = json.dumps(data, ensure_ascii=False)
        for secret in (
            "coach-only note",
            "received secret",
            "from them",
            "Recipient Person",
            "recipient@example.com",
        ):
            self.assertNotIn(secret, dump)
        self._assert_other_member_absent(data)

    def test_payments_and_premium_without_provider_internals(self):
        from crush_lu.models import CrushCoach, PaymentTransaction, PremiumMembership

        coach = CrushCoach.objects.create(
            user=User.objects.create_user(
                "coach@example.com", "coach@example.com", "x", first_name="Cora"
            ),
            bio="b",
        )
        PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EXPORT-1",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_PROVIDER_SECRET",
            amount=Decimal("10.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.DONATION,
            user=self.user,
            raw_response={"token": "provider-secret"},
        )
        PremiumMembership.objects.create(
            user=self.user, coach=coach, status="pending", onboarding_fee="10.00"
        )

        data = self._export()
        payment = data["payments"][0]
        self.assertEqual(payment["reference"], "CRUSH-EXPORT-1")
        self.assertEqual(payment["amount"], "10.00")
        self.assertEqual(data["premium_memberships"][0]["coach"], "Cora")
        self.assertEqual(data["premium_memberships"][0]["onboarding_fee"], "10.00")
        self.assertNotIn("coach@example.com", json.dumps(data))
        dump = json.dumps(data)
        self.assertNotIn("CHK_PROVIDER_SECRET", dump)
        self.assertNotIn("provider-secret", dump)

    def test_staff_opened_checkout_belongs_to_the_member_not_the_staff(self):
        """Ownership follows _payment_owner_ids: the linked registration wins."""
        from crush_lu.models import EventRegistration, PaymentTransaction

        staff = User.objects.create_user(
            "staff@example.com", "staff@example.com", "x", is_staff=True
        )
        registration = EventRegistration.objects.create(
            event=self._event(), user=self.user, status="pending"
        )
        PaymentTransaction.objects.create(
            transaction_reference="CRUSH-STAFF-OPENED",
            provider=PaymentTransaction.Provider.SUMUP,
            amount=Decimal("25.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=staff,
            event_registration=registration,
        )

        payments = self._export()["payments"]
        self.assertEqual([p["reference"] for p in payments], ["CRUSH-STAFF-OPENED"])
        # Not stamped yet (pending), so the title comes from the registration.
        self.assertEqual(payments[0]["event"], "Lobby night")

        request = RequestFactory().get("/")
        request.user = staff
        staff_export = json.loads(export_user_data(request).content)
        self.assertEqual(staff_export["payments"], [])

    def test_referrals_both_ways_without_the_other_person(self):
        from crush_lu.models import ReferralAttribution, ReferralCode

        their_code = ReferralCode.objects.create(referrer=self.other.crushprofile)
        ReferralAttribution.objects.create(
            referral_code=their_code,
            referrer=self.other.crushprofile,
            referred_user=self.user,
            ip_address="1.2.3.4",
            user_agent="UA",
            landing_path="/join",
            session_key="sk",
        )
        my_code = ReferralCode.objects.create(referrer=self.user.crushprofile)
        ReferralAttribution.objects.create(
            referral_code=my_code,
            referrer=self.user.crushprofile,
            referred_user=self.other,
            ip_address="9.9.9.9",
            user_agent="THEIR-UA",
            landing_path="/their-landing",
            session_key="sk2",
        )

        # A merged duplicate account can leave a second inbound attribution.
        ReferralAttribution.objects.create(
            referral_code=their_code,
            referrer=self.other.crushprofile,
            referred_user=self.user,
            landing_path="/second",
            session_key="sk3",
        )

        data = self._export()
        arrivals = data["referrals"]["you_were_referred"]
        self.assertEqual([a["landing_path"] for a in arrivals], ["/join", "/second"])
        self.assertEqual(
            [c["code"] for c in data["referrals"]["your_codes"]], [my_code.code]
        )
        self.assertNotIn(their_code.code, json.dumps(data["referrals"]))
        self.assertIs(
            data["referrals"]["people_you_referred"][0]["reward_applied"], False
        )
        self.assertEqual(arrivals[0]["ip_address"], "1.2.3.4")
        self.assertEqual(len(data["referrals"]["people_you_referred"]), 1)
        dump = json.dumps(data)
        for secret in ("9.9.9.9", "THEIR-UA", "/their-landing"):
            self.assertNotIn(secret, dump)
        self._assert_other_member_absent(data)

    def test_safety_tools_used_by_the_member_only(self):
        from crush_lu.models import UserBlock, UserReport
        from crush_lu.models.crush_connect_cycle import ConnectReport

        UserBlock.objects.create(blocker=self.user, blocked=self.other)
        UserBlock.objects.create(blocker=self.other, blocked=self.user)
        UserReport.objects.create(
            reporter=self.user,
            reported_user=self.other,
            reason="spam",
            details="my report text",
            resolution_notes="staff resolution",
            status="resolved",
            handled_at=timezone.now(),
        )
        UserReport.objects.create(
            reporter=self.other,
            reported_user=self.user,
            reason="spam",
            details="report about the member",
        )
        ConnectReport.objects.create(
            reporter=self.user,
            reported_user=self.other,
            reason="other",
            details="connect report text",
            admin_notes="admin note",
            reviewed_at=timezone.now(),
        )

        safety = self._export()["safety"]
        self.assertEqual(len(safety["blocks_made"]), 1)
        self.assertIsNotNone(safety["reports_made"][0]["handled_at"])
        self.assertIsNotNone(safety["connect_reports_made"][0]["reviewed_at"])
        self.assertEqual(safety["reports_made"][0]["details"], "my report text")
        self.assertEqual(
            safety["connect_reports_made"][0]["details"], "connect report text"
        )
        dump = json.dumps(safety)
        for secret in ("staff resolution", "report about the member", "admin note"):
            self.assertNotIn(secret, dump)
        self._assert_other_member_absent(safety)

    def test_lobby_exports_own_signals_and_active_encounters_only(self):
        from crush_lu.models.event_lobby import (
            ConfirmedEncounter,
            EventMeetingConfirmation,
            EventMeetSignal,
        )

        event = self._event()
        third = User.objects.create_user("third@example.com", "third@example.com", "x")
        EventMeetSignal.objects.create(
            event=event,
            sender=self.user,
            recipient=self.other,
            mutual_revealed_at=timezone.now(),
        )
        EventMeetSignal.objects.create(event=event, sender=third, recipient=self.user)
        EventMeetingConfirmation.objects.create(
            event=event,
            confirmer=self.user,
            other_user=self.other,
        )

        def encounter(a, b, status):
            low, high = sorted((a, b), key=lambda u: u.pk)
            return ConfirmedEncounter.objects.create(
                user_low=low,
                user_high=high,
                created_from_event=event,
                status=status,
            )

        encounter(self.user, self.other, "active")
        under_review = encounter(self.user, third, "removal_pending")
        from crush_lu.models.event_lobby import ConfirmedEncounterRemovalRequest

        ConfirmedEncounterRemovalRequest.objects.create(
            encounter=under_review,
            requested_by=self.user,
            reason="privacy",
            details="my removal reason",
            resolution_notes="reviewer-only note",
        )
        # Someone else asked to remove their encounter with the member (one
        # request per encounter, so it needs its own encounter).
        fourth = User.objects.create_user(
            "fourth@example.com", "fourth@example.com", "x"
        )
        ConfirmedEncounterRemovalRequest.objects.create(
            encounter=encounter(self.user, fourth, "removal_pending"),
            requested_by=fourth,
            reason="safety",
            details="their removal text",
        )

        from crush_lu.models import EventRegistration
        from crush_lu.models.event_lobby import EventLobbyParticipation

        EventLobbyParticipation.objects.create(
            event=event,
            user=self.user,
            event_registration=EventRegistration.objects.create(
                event=event, user=self.user, status="confirmed"
            ),
        )

        lobby = self._export()["event_lobby"]
        self.assertEqual([j["event"] for j in lobby["lobbies_joined"]], ["Lobby night"])
        self.assertNotIn("handle", lobby["lobbies_joined"][0])
        self.assertEqual(len(lobby["meet_signals_sent"]), 1)
        self.assertTrue(lobby["meet_signals_sent"][0]["mutual"])
        self.assertEqual(len(lobby["meetings_you_confirmed"]), 1)
        self.assertEqual(len(lobby["confirmed_encounters"]), 1)
        # created_from_event is internal audit provenance: date only.
        self.assertEqual(list(lobby["confirmed_encounters"][0]), ["created_at"])
        # The member's own removal request is exported; the other person's and
        # the reviewer's note are not.
        requests = lobby["removal_requests_you_made"]
        self.assertEqual([r["details"] for r in requests], ["my removal reason"])
        for secret in ("their removal text", "reviewer-only note"):
            self.assertNotIn(secret, json.dumps(lobby))
        self.assertNotIn("third@example.com", json.dumps(lobby))
        self._assert_other_member_absent(lobby)
