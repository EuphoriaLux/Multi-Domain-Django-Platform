"""Privacy-safe member surfaces for elastic curated speed-dating groups.

Curated sign-ups are applications, not seat claims. The public event page may
show coarse overall interest and configured group options, but never the
organiser-only gender/preference pool or an exact shortage. Direct events keep
their established capacity, gender-pool and waitlist behaviour.

Paths are literal because the Crush host middleware replaces the active
urlconf; ``reverse()`` would build a path for the default host instead.
"""

import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.utils import timezone

from crush_lu.models import (
    CrushProfile,
    EventRegistration,
    MeetupEvent,
    UserDataConsent,
)
from crush_lu.models.events import (
    CuratedEventGroup,
    EventRegistrationPreference,
)
from crush_lu.services.curated_group_workflow import (
    approve_current_generation,
    generate_group_projection,
    lock_current_generation,
)

User = get_user_model()


class CuratedGroupSurfaceTests(TestCase):
    def setUp(self):
        # The event registration endpoints are user-rate-limited in a cache
        # that outlives SQLite TestCase rollbacks. Clear it even though these
        # tests render only GETs, so their shared user IDs cannot inherit debt.
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")
        self.viewer = self._member("viewer@example.com", gender="F")
        self.client.login(username=self.viewer.username, password="testpass123")

        self.curated = self._event(
            "Elastic Curated Night",
            registration_mode="curated",
            registration_fee=Decimal("15.00"),
            group_size=6,
            planned_groups=2,
            max_participants_m=9,
            max_participants_f=9,
            max_participants_nb=0,
        )
        self.direct = self._event(
            "Direct Night",
            registration_mode="direct",
            max_participants_m=9,
            max_participants_f=9,
            max_participants_nb=0,
        )

    def _event(self, title, **overrides):
        values = {
            "title": title,
            "description": "Group surface test",
            "event_type": "speed_dating",
            "date_time": timezone.now() + timedelta(days=7),
            "location": "Luxembourg",
            "address": "123 Test Street",
            "max_participants": 18,
            "registration_deadline": timezone.now() + timedelta(days=5),
            "is_published": True,
            "profile_requirement": "none",
        }
        values.update(overrides)
        return MeetupEvent.objects.create(**values)

    def _member(self, email, gender="M"):
        user = User.objects.create_user(
            username=email,
            email=email,
            password="testpass123",
            first_name=email.split("@", 1)[0],
        )
        UserDataConsent.objects.update_or_create(
            user=user, defaults={"crushlu_consent_given": True}
        )
        CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 1, 1),
            gender=gender,
            location="Luxembourg",
            event_languages=["en"],
        )
        return user

    def _applications(self, count):
        for index in range(count):
            user = self._member(
                f"applicant-{index}@example.com",
                gender="F" if index == count - 1 else "M",
            )
            EventRegistration.objects.create(
                event=self.curated,
                user=user,
                status="applied",
            )

    def _detail(self, event, language="en"):
        return self.client.get(f"/{language}/events/{event.pk}/")

    def test_curated_page_uses_coarse_group_outlook_and_application_copy(self):
        self._applications(6)

        response = self._detail(self.curated)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["gender_pool_availability"], [])
        outlook = response.context["curated_group_outlook"]
        self.assertEqual(
            set(outlook),
            {
                "interest_state",
                "group_size",
                "planned_groups",
                "max_groups",
                "parallel_groups_possible",
                "viewer_match",
                "several_first_timers",
                "mostly_verified",
                "group",
            },
        )
        self.assertEqual(outlook["interest_state"], "exploring")
        self.assertEqual(outlook["group_size"], 6)
        self.assertEqual(outlook["planned_groups"], 2)
        self.assertNotIn("by_pool", outlook)

        self.assertContains(response, "Flexible groups")
        self.assertContains(response, "Apply for This Event")
        self.assertContains(response, "Applying does not reserve a place.")
        self.assertContains(
            response,
            "If selected for a viable provisional group, you will be invited to pay.",
        )
        self.assertContains(
            response,
            "Groups are finalized before the first round and stay together for the whole evening.",
        )
        self.assertContains(
            response,
            "Every finalized group is scheduled to give each participant at least five mutually compatible mini-dates, with seven as the goal.",
        )
        self.assertNotContains(response, "Register for This Event")
        self.assertNotContains(response, "Spots remaining:")
        self.assertNotContains(response, "Men:")
        self.assertNotContains(response, "Women:")

    def test_organiser_only_pool_and_exact_shortage_cannot_reach_template(self):
        aggregate = {
            "applications": 1234567,
            "group_size": 6,
            "max_groups": 3,
            "planned_groups": 2,
            "groups_unlocked": 1,
            "next_group_at": 2345678,
            "first_timers": 3456789,
            "certified": 4567890,
            "by_pool": {"men": 5678901, "women": 6789012},
        }

        with patch.object(MeetupEvent, "get_application_pool", return_value=aggregate):
            response = self._detail(self.curated)

        outlook = response.context["curated_group_outlook"]
        self.assertEqual(outlook["interest_state"], "exploring")
        self.assertEqual(outlook["max_groups"], 3)
        for private_value in (
            "1234567",
            "2345678",
            "3456789",
            "4567890",
            "5678901",
            "6789012",
        ):
            self.assertNotContains(response, private_value)
        self.assertNotIn("applications", outlook)
        self.assertNotIn("next_group_at", outlook)
        self.assertNotIn("by_pool", outlook)

    def test_direct_mode_keeps_capacity_pool_and_registration_copy(self):
        response = self._detail(self.direct)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["curated_group_outlook"])
        self.assertTrue(response.context["gender_pool_availability"])
        self.assertContains(response, "Capacity")
        self.assertContains(response, "Max participants: 18")
        self.assertContains(response, "Register for This Event")
        self.assertNotContains(response, "Flexible groups")
        self.assertNotContains(response, "Apply for This Event")

        structured_data = json.loads(response.context["event_jsonld"])
        self.assertEqual(structured_data["remainingAttendeeCapacity"], 18)
        self.assertEqual(
            structured_data["offers"]["availability"],
            "https://schema.org/InStock",
        )

    def test_incomplete_curated_configuration_has_safe_fallback(self):
        event = self._event(
            "Configuration Pending",
            registration_mode="curated",
            group_size=None,
            planned_groups=None,
        )

        response = self._detail(event)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["curated_group_outlook"]["group_size"])
        # The match bucket is sized in groups: no group size, no sentence, not
        # even the completion hint.
        self.assertIsNone(response.context["curated_group_outlook"]["viewer_match"])
        self.assertNotContains(response, "data-curated-viewer-match")
        self.assertContains(
            response,
            "The group size and number of groups will be set after applications are reviewed.",
        )
        self.assertNotContains(
            response,
            "at least five mutually compatible mini-dates",
        )
        self.assertNotContains(response, "producing a viable round schedule")
        self.assertNotContains(response, "selected for a viable provisional group")
        self.assertNotContains(response, "None")

    def test_paid_applicant_sees_selection_then_payment_and_can_withdraw(self):
        EventRegistration.objects.create(
            event=self.curated,
            user=self.viewer,
            status="applied",
        )

        response = self._detail(self.curated)

        self.assertContains(response, "Your application is in!")
        self.assertContains(
            response,
            "will invite you to pay only if you are selected for a viable provisional group",
        )
        self.assertContains(response, "Selection is not guaranteed.")
        self.assertContains(response, "Withdraw Application")
        self.assertNotContains(response, "Cancel Registration")
        self.assertNotContains(response, "js-sumup-checkout-detail")

    def test_curated_structured_data_does_not_publish_stale_seats(self):
        for index in range(self.curated.max_participants):
            user = self._member(f"selected-{index}@example.com")
            registration = EventRegistration.objects.create(
                event=self.curated,
                user=user,
                status="applied",
            )
            # Reproduce legacy/stale database state without asking the current
            # model lifecycle to grant an uncertified curated seat.
            EventRegistration.objects.filter(pk=registration.pk).update(
                status="confirmed"
            )

        response = self._detail(self.curated)
        structured_data = json.loads(response.context["event_jsonld"])

        self.assertNotIn("remainingAttendeeCapacity", structured_data)
        self.assertEqual(
            structured_data["offers"]["availability"],
            "https://schema.org/InStock",
        )

    def test_member_contract_is_localized_in_formal_fr_and_informal_de(self):
        expectations = {
            "de": (
                "Flexible Gruppen",
                "Deine Bewerbung reserviert keinen Platz.",
                "Für diese Veranstaltung bewerben",
                "mindestens fünf gegenseitig passende Mini-Dates pro Person",
            ),
            "fr": (
                "Groupes flexibles",
                "Votre candidature ne réserve pas de place.",
                "Postuler à cet événement",
                "au moins cinq mini-rencontres mutuellement compatibles",
            ),
        }

        for language, phrases in expectations.items():
            with self.subTest(language=language):
                response = self._detail(self.curated, language=language)
                self.assertEqual(response.status_code, 200)
                for phrase in phrases:
                    self.assertContains(response, phrase)

    def test_event_card_never_prints_a_seat_count_for_a_curated_event(self):
        """Applications hold no seat, so "18 spots left" would sit frozen at
        the venue ceiling however large the pool grew. The direct card is
        untouched."""
        response = self.client.get("/en/events/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Curated groups · applications open")
        self.assertContains(response, "18 spots left", count=1)

    def test_event_card_says_applications_closed_after_the_deadline(self):
        MeetupEvent.objects.filter(pk=self.curated.pk).update(
            registration_deadline=timezone.now() - timedelta(hours=1)
        )

        response = self.client.get("/en/events/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Curated groups · applications closed")
        self.assertNotContains(response, "Curated groups · applications open")


class CuratedMemberInsightTests(TestCase):
    """Coarse, viewer-only insights on the member event page.

    Everything a member sees about other applicants is a bucket or a boolean
    above a documented threshold; everything about their group is a size, a
    round count and their own table. Other members' names, emails, genders and
    ages never reach the page.
    """

    COACH_PANEL_MARKERS = (
        "data-curated-groups-panel",
        "Why this group",
        "Show pairing schedule",
        "Would be left out",
        "Cannot be placed",
        "Eligible but not placed",
        "Next step:",
    )

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")
        self.viewer = self._member("viewer@example.com", gender="F")
        self.client.login(username=self.viewer.username, password="testpass123")
        self.event = self._event(
            "Curated Night",
            registration_mode="curated",
            registration_fee=Decimal("15.00"),
            group_size=6,
            planned_groups=1,
            max_participants=12,
        )

    def _event(self, title, **overrides):
        values = {
            "title": title,
            "description": "Member insight test",
            "event_type": "speed_dating",
            "date_time": timezone.now() + timedelta(days=7),
            "location": "Luxembourg",
            "address": "123 Test Street",
            "max_participants": 12,
            "registration_deadline": timezone.now() + timedelta(days=5),
            "is_published": True,
            "profile_requirement": "none",
        }
        values.update(overrides)
        return MeetupEvent.objects.create(**values)

    def _member(
        self, email, *, gender="M", date_of_birth=date(1995, 1, 1), verified=False
    ):
        user = User.objects.create_user(
            username=email,
            email=email,
            password="testpass123",
            first_name=email.split("@", 1)[0].replace("-", " ").title(),
        )
        UserDataConsent.objects.update_or_create(
            user=user, defaults={"crushlu_consent_given": True}
        )
        CrushProfile.objects.create(
            user=user,
            date_of_birth=date_of_birth,
            gender=gender,
            location="Luxembourg",
            event_languages=["en"],
            verification_status="verified" if verified else "incomplete",
        )
        return user

    def _apply(self, user, event=None, *, with_preference=True):
        registration = EventRegistration.objects.create(
            event=event or self.event, user=user, status="applied"
        )
        if with_preference:
            EventRegistrationPreference.objects.create(
                registration=registration,
                preferred_genders=[],
                preferred_age_min=18,
                preferred_age_max=99,
                languages=[],
            )
        return registration

    def _applicants(self, count, *, verified=False, event=None):
        event = event or self.event
        first = EventRegistration.objects.filter(event=event).count()
        return [
            self._apply(
                self._member(
                    f"applicant-{event.pk}-{index}@example.com", verified=verified
                ),
                event,
            )
            for index in range(first, first + count)
        ]

    def _detail(self, event=None, language="en"):
        event = event or self.event
        response = self.client.get(f"/{language}/events/{event.pk}/")
        self.assertEqual(response.status_code, 200)
        return response

    # -- before selection -----------------------------------------------------

    def test_anonymous_viewer_gets_the_card_unchanged(self):
        self._applicants(12, verified=True)
        self.client.logout()

        response = self._detail()
        outlook = response.context["curated_group_outlook"]

        self.assertIsNone(outlook["viewer_match"])
        self.assertFalse(outlook["several_first_timers"])
        self.assertFalse(outlook["mostly_verified"])
        self.assertIsNone(outlook["group"])
        self.assertNotContains(response, "data-curated-viewer-match")
        self.assertNotContains(response, "data-curated-social-proof")
        self.assertNotContains(response, "match your preferences")
        self.assertContains(response, "Flexible groups")

    def test_match_sentence_per_bucket_never_prints_the_count(self):
        expectations = (
            (
                2,
                "few",
                "Few of the current applicants match your preferences; widening them helps.",
            ),
            (
                3,
                "half",
                "About half an evening's worth of the current applicants match your preferences.",
            ),
            (
                6,
                "evening",
                "Enough of the current applicants match your preferences to fill an evening.",
            ),
            (
                12,
                "multiple",
                "Enough of the current applicants match your preferences for more than one group.",
            ),
        )
        for target, bucket, sentence in expectations:
            with self.subTest(bucket=bucket):
                have = EventRegistration.objects.filter(event=self.event).count()
                self._applicants(target - have)

                response = self._detail()

                self.assertEqual(
                    response.context["curated_group_outlook"]["viewer_match"], bucket
                )
                self.assertContains(response, f'data-curated-viewer-match="{bucket}"')
                self.assertContains(response, sentence)
                for other in expectations:
                    if other[1] != bucket:
                        self.assertNotContains(response, other[2])

    def test_incomplete_applicants_do_not_count_as_matches(self):
        for index in range(6):
            self._apply(
                self._member(f"blank-{index}@example.com"), with_preference=False
            )

        self.assertEqual(
            self._detail().context["curated_group_outlook"]["viewer_match"], "few"
        )

    def test_applied_viewer_still_sees_the_match_sentence(self):
        self._applicants(6)
        self._apply(self.viewer)

        response = self._detail()

        self.assertEqual(
            response.context["curated_group_outlook"]["viewer_match"], "evening"
        )
        self.assertContains(response, "Your application is in!")
        self.assertContains(response, "to fill an evening")

    def test_viewer_without_birthday_gets_a_completion_hint_not_a_bucket(self):
        CrushProfile.objects.filter(user=self.viewer).update(date_of_birth=None)
        self._applicants(6)

        response = self._detail()

        self.assertEqual(
            response.context["curated_group_outlook"]["viewer_match"],
            "profile_incomplete",
        )
        self.assertContains(
            response, "Add your gender and date of birth to your profile"
        )
        self.assertNotContains(response, "widening them helps")

    def test_first_timer_signal_needs_three(self):
        self._applicants(2)
        response = self._detail()
        self.assertFalse(
            response.context["curated_group_outlook"]["several_first_timers"]
        )
        self.assertNotContains(response, "Several first-timers have applied.")

        self._applicants(1)
        response = self._detail()
        self.assertTrue(
            response.context["curated_group_outlook"]["several_first_timers"]
        )
        self.assertContains(response, "Several first-timers have applied.")

    def test_verified_signal_needs_five_applicants_and_sixty_percent(self):
        # 4 verified out of 4: too few applications to say anything.
        self._applicants(4, verified=True)
        response = self._detail()
        self.assertFalse(response.context["curated_group_outlook"]["mostly_verified"])
        self.assertNotContains(response, "Most applicants are verified.")

        # 4 of 6: 66 %, above the line.
        self._applicants(2)
        response = self._detail()
        self.assertTrue(response.context["curated_group_outlook"]["mostly_verified"])
        self.assertContains(response, "Most applicants are verified.")

        # 4 of 8: 50 %, below it again.
        self._applicants(2)
        response = self._detail()
        self.assertFalse(response.context["curated_group_outlook"]["mostly_verified"])
        self.assertNotContains(response, "Most applicants are verified.")

    def test_pool_counts_never_reach_the_page_even_with_signals_on(self):
        aggregate = {
            "applications": 1234567,
            "group_size": 6,
            "max_groups": 2,
            "planned_groups": 1,
            "groups_unlocked": 1,
            "next_group_at": 2345678,
            "first_timers": 3456789,
            "certified": 4567890,
            "by_pool": {"men": 5678901, "women": 6789012},
        }
        with patch.object(MeetupEvent, "get_application_pool", return_value=aggregate):
            response = self._detail()

        outlook = response.context["curated_group_outlook"]
        self.assertTrue(outlook["several_first_timers"])
        self.assertTrue(outlook["mostly_verified"])
        for private_value in (
            "1234567",
            "2345678",
            "3456789",
            "4567890",
            "5678901",
            "6789012",
        ):
            self.assertNotContains(response, private_value)

    # -- after selection --------------------------------------------------------

    def _select_viewer(self):
        event = self._event(
            "Selected Night",
            registration_mode="curated",
            registration_fee=Decimal("15.00"),
            group_size=6,
            planned_groups=1,
            max_participants=12,
            registration_deadline=timezone.now() - timedelta(hours=1),
        )
        mine = self._apply(self.viewer, event)
        others = self._applicants(5, event=event)
        stored = generate_group_projection(event, deterministic_seed="member-insights")
        approved = approve_current_generation(event)
        EventRegistration.objects.filter(
            pk__in=approved.applied_registration_ids
        ).update(status="pending")
        group = CuratedEventGroup.objects.get(pk=stored.group_ids[0])
        return event, mine, others, group

    def _lock(self, event):
        EventRegistration.objects.filter(event=event).update(status="attended")
        MeetupEvent.objects.filter(pk=event.pk).update(
            date_time=timezone.now() - timedelta(minutes=10)
        )
        event.refresh_from_db()
        lock_current_generation(event)

    def test_selected_member_sees_a_provisional_group_summary_without_names(self):
        event, mine, others, group = self._select_viewer()

        response = self._detail(event)
        outlook = response.context["curated_group_outlook"]

        self.assertIsNone(outlook["viewer_match"])
        self.assertEqual(
            outlook["group"],
            {
                "status": "provisional",
                "size": 6,
                "rounds": 5,
                "min_dates": 5,
                "tables": None,
            },
        )
        self.assertContains(response, 'data-curated-member-group="provisional"')
        self.assertContains(response, "Your group is provisional.")
        self.assertContains(
            response,
            "You are in a group of 6 people; 5 rounds are planned; everyone gets at least 5 mini-dates.",
        )
        self.assertContains(response, "Your tables appear here once it is final.")
        self.assertNotContains(response, "data-curated-own-tables")
        self.assertNotContains(response, "match your preferences")
        self.assertContains(response, "js-sumup-checkout-detail")

    def test_locked_member_sees_only_their_own_tables(self):
        event, mine, others, group = self._select_viewer()
        self._lock(event)

        response = self._detail(event)
        outlook = response.context["curated_group_outlook"]

        own = list(
            mine.curated_pairing_participations.select_related("pairing").order_by(
                "round_number", "pairing__table_number", "pk"
            )
        )
        self.assertEqual(len(own), 5)
        self.assertEqual(outlook["group"]["status"], "locked")
        self.assertEqual(
            outlook["group"]["tables"],
            [
                {
                    "round": participant.round_number,
                    "table": participant.pairing.table_number,
                    "seat": participant.seat.upper(),
                }
                for participant in own
            ],
        )
        self.assertContains(response, 'data-curated-member-group="locked"')
        self.assertContains(response, "Your group is final.")
        self.assertContains(response, "data-curated-own-tables")
        for participant in own:
            self.assertContains(
                response,
                f"Round {participant.round_number}: table "
                f"{participant.pairing.table_number}, seat {participant.seat.upper()}",
            )
        self.assertNotContains(response, ": break")

    def test_member_page_never_names_other_members(self):
        event, mine, others, group = self._select_viewer()
        self._lock(event)

        for status in ("attended", "confirmed", "pending"):
            with self.subTest(status=status):
                EventRegistration.objects.filter(pk=mine.pk).update(status=status)
                response = self._detail(event)
                self.assertContains(response, "data-curated-member-group")
                for other in others:
                    self.assertNotContains(response, other.user.first_name)
                    self.assertNotContains(response, other.user.email)
                for word in (
                    "Male",
                    "Female",
                    "Non-binary",
                    "&#9792;",
                    "&#9794;",
                    "\u2640",
                    "\u2642",
                ):
                    self.assertNotContains(response, word)
                for marker in self.COACH_PANEL_MARKERS:
                    self.assertNotContains(response, marker)

    def test_released_member_gets_no_group_block(self):
        event, mine, others, group = self._select_viewer()
        group.memberships.filter(registration=mine).update(released_at=timezone.now())

        response = self._detail(event)

        self.assertIsNone(response.context["curated_group_outlook"]["group"])
        self.assertNotContains(response, "data-curated-member-group")

    def test_member_insights_are_localized(self):
        self._applicants(6, verified=True)
        expectations = {
            "de": (
                "Genug der aktuellen Bewerber passen zu deinen Vorlieben, um einen Abend zu f\u00fcllen.",
                "Mehrere Neulinge haben sich beworben.",
                "Die meisten Bewerber sind verifiziert.",
            ),
            "fr": (
                "Assez de candidats actuels correspondent \u00e0 vos pr\u00e9f\u00e9rences pour remplir une soir\u00e9e.",
                "Plusieurs personnes participent pour la premi\u00e8re fois.",
                "La plupart des candidats sont v\u00e9rifi\u00e9s.",
            ),
        }
        for language, phrases in expectations.items():
            with self.subTest(language=language):
                response = self._detail(language=language)
                for phrase in phrases:
                    self.assertContains(response, phrase)

        event, mine, others, group = self._select_viewer()
        group_expectations = {
            "de": "Du bist in einer Gruppe von 6 Personen; 5 Runden sind geplant; alle bekommen mindestens 5 Mini-Dates.",
            "fr": "Vous faites partie d\u2019un groupe de 6 personnes ; 5 rondes sont pr\u00e9vues ; chacun a au moins 5 mini-rencontres.",
        }
        for language, phrase in group_expectations.items():
            with self.subTest(language=language, stage="selected"):
                self.assertContains(self._detail(event, language=language), phrase)
