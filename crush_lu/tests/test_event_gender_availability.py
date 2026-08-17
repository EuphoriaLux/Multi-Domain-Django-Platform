"""Per-gender seat availability on the public event page (#866).

Gender-balanced events cap each pool separately (`max_participants_m` / `_f` /
`_nb`), but the event page used to advertise only the *total* remaining. A
member whose own pool was full read "spots available", registered, and was
waitlisted with no explanation -- which is exactly the complaint that opened
the issue.

These tests pin the surface, not just the model helper: the CTA wording and the
per-pool chips are what the member actually reads, and a gate test that only
checks `is_gender_pool_full()` would have passed throughout the incident.

Paths are literal because the host middleware swaps the urlconf per domain --
`reverse("crush_lu:event_detail")` builds a `/crush/...` path that 404s under
`HTTP_HOST=crush.lu`.

Run with: pytest crush_lu/tests/test_event_gender_availability.py -v
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

User = get_user_model()


class GenderPoolAvailabilityTestBase(TestCase):
    """Shared event/user fixtures for a gender-capped mixer."""

    def setUp(self):
        from crush_lu.models import MeetupEvent

        self.client = Client(HTTP_HOST="crush.lu")
        self.event = MeetupEvent.objects.create(
            title="Gender Balanced Speed Dating",
            description="Testing per-pool availability",
            event_type="mixer",
            date_time=timezone.now() + timedelta(days=7),
            location="Luxembourg",
            address="123 Test Street",
            max_participants=10,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True,
            profile_requirement="none",
        )

    def _set_caps(self, m, f, nb):
        self.event.max_participants_m = m
        self.event.max_participants_f = f
        self.event.max_participants_nb = nb
        self.event.save()

    def _create_user(self, username):
        user = User.objects.create_user(
            username=username,
            email=username,
            password="testpass123",
            first_name=username.split("@")[0],
        )
        # consent_middleware is scoped to urls_crush and 302s every page without
        # this, so a logged-in viewer never reaches the event detail template.
        self._grant_consent(user)
        return user

    def _grant_consent(self, user):
        from crush_lu.models import UserDataConsent

        UserDataConsent.objects.update_or_create(
            user=user, defaults={"crushlu_consent_given": True}
        )

    def _create_user_with_profile(self, username, gender):
        from crush_lu.models import CrushProfile

        user = self._create_user(username)
        CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 1, 1),
            gender=gender,
            location="Luxembourg",
        )
        return user

    def _register(self, user, status="confirmed"):
        from crush_lu.models import EventRegistration

        return EventRegistration.objects.create(
            event=self.event, user=user, status=status
        )

    def _detail_url(self):
        return f"/en/events/{self.event.id}/"

    def _get_detail(self):
        response = self.client.get(self._detail_url())
        self.assertEqual(response.status_code, 200)
        return response


class GenderPoolAvailabilityModelTests(GenderPoolAvailabilityTestBase):
    """MeetupEvent.get_gender_pool_availability()."""

    def test_returns_empty_without_caps(self):
        """No caps set -> no pools, so callers keep the total-only display."""
        self.assertEqual(self.event.get_gender_pool_availability(), [])

    def test_counts_each_pool_separately(self):
        self._set_caps(4, 4, 2)
        self._register(self._create_user_with_profile("m1@test.com", "M"))
        self._register(self._create_user_with_profile("m2@test.com", "M"))
        self._register(self._create_user_with_profile("f1@test.com", "F"))

        pools = {p["key"]: p for p in self.event.get_gender_pool_availability()}
        self.assertEqual(pools["m"]["confirmed"], 2)
        self.assertEqual(pools["m"]["remaining"], 2)
        self.assertFalse(pools["m"]["is_full"])
        self.assertEqual(pools["f"]["confirmed"], 1)
        self.assertEqual(pools["f"]["remaining"], 3)
        self.assertEqual(pools["nb"]["confirmed"], 0)
        self.assertEqual(pools["nb"]["remaining"], 2)

    def test_nb_pool_absorbs_other_and_prefer_not_to_say(self):
        """POOL_TO_CODES folds NB/O/P into one pool -- the display must too."""
        self._set_caps(2, 2, 3)
        self._register(self._create_user_with_profile("nb1@test.com", "NB"))
        self._register(self._create_user_with_profile("o1@test.com", "O"))
        self._register(self._create_user_with_profile("p1@test.com", "P"))

        pools = {p["key"]: p for p in self.event.get_gender_pool_availability()}
        self.assertEqual(pools["nb"]["confirmed"], 3)
        self.assertTrue(pools["nb"]["is_full"])

    def test_only_seat_holding_statuses_count(self):
        """Waitlisted and cancelled seats are not held, so they do not fill a
        pool -- and a pending (paid, unconfirmed) seat does."""
        self._set_caps(2, 2, 0)
        self._register(self._create_user_with_profile("m1@test.com", "M"), "pending")
        self._register(self._create_user_with_profile("m2@test.com", "M"), "waitlist")
        self._register(self._create_user_with_profile("m3@test.com", "M"), "cancelled")

        pools = {p["key"]: p for p in self.event.get_gender_pool_availability()}
        self.assertEqual(pools["m"]["confirmed"], 1)

    def test_agrees_with_get_confirmed_count_for_gender(self):
        """The two counters decide what is *shown* and who is *waitlisted*.
        A divergence would recreate the mismatch this feature exists to fix."""
        self._set_caps(3, 3, 1)
        for i, gender in enumerate(["M", "M", "F", "NB", "O"]):
            self._register(self._create_user_with_profile(f"u{i}@test.com", gender))

        pools = {p["key"]: p for p in self.event.get_gender_pool_availability()}
        self.assertEqual(
            pools["m"]["confirmed"], self.event.get_confirmed_count_for_gender("M")
        )
        self.assertEqual(
            pools["f"]["confirmed"], self.event.get_confirmed_count_for_gender("F")
        )
        self.assertEqual(
            pools["nb"]["confirmed"], self.event.get_confirmed_count_for_gender("NB")
        )
        self.assertTrue(self.event.is_gender_pool_full("NB"))
        self.assertTrue(pools["nb"]["is_full"])

    def test_seat_without_profile_belongs_to_no_pool(self):
        """Documented blind spot, shared with get_confirmed_count_for_gender:
        a seat held by a user with no CrushProfile counts in no pool."""
        self._set_caps(1, 1, 1)
        self._register(self._create_user("noprofile@test.com"))

        pools = self.event.get_gender_pool_availability()
        self.assertEqual(sum(p["confirmed"] for p in pools), 0)

    def test_single_query(self):
        """All three pools come from one grouped query, not three COUNTs."""
        self._set_caps(2, 2, 2)
        self._register(self._create_user_with_profile("m1@test.com", "M"))
        with self.assertNumQueries(1):
            self.event.get_gender_pool_availability()


class GenderPoolAvailabilityPageTests(GenderPoolAvailabilityTestBase):
    """What the member actually reads on /events/<id>/."""

    def test_uncapped_event_keeps_total_only_display(self):
        response = self._get_detail()
        self.assertContains(response, "Spots remaining: 10")
        self.assertEqual(response.context["gender_pool_availability"], [])
        self.assertIsNone(response.context["user_gender_pool"])

    def test_capped_event_shows_per_pool_counts_to_anonymous_visitors(self):
        """Gender is unknown for an anonymous visitor, so they get every pool
        and no personal line."""
        self._set_caps(4, 4, 2)
        self._register(self._create_user_with_profile("m1@test.com", "M"))

        response = self._get_detail()
        self.assertNotContains(response, "Spots remaining:")
        self.assertContains(response, "Men: 3 spots left")
        self.assertContains(response, "Women: 4 spots left")
        self.assertContains(response, "Other genders: 2 spots left")
        self.assertIsNone(response.context["user_gender_pool"])
        self.assertNotContains(response, "spots left for you")

    def test_full_pool_is_labelled_full_not_counted_down(self):
        self._set_caps(1, 4, 0)
        self._register(self._create_user_with_profile("m1@test.com", "M"))

        response = self._get_detail()
        self.assertContains(response, "Men: full")
        self.assertContains(response, "Other genders: full")
        self.assertContains(response, "Women: 4 spots left")

    def test_member_sees_their_own_pool_state(self):
        self._set_caps(4, 4, 2)
        self._register(self._create_user_with_profile("m1@test.com", "M"))
        viewer = self._create_user_with_profile("viewer@test.com", "M")
        self.client.force_login(viewer)

        response = self._get_detail()
        self.assertContains(response, "3 spots left for you.")
        self.assertEqual(response.context["user_gender_pool"]["key"], "m")
        self.assertFalse(response.context["user_pool_full"])

    def test_member_with_full_pool_is_told_so_and_gets_waitlist_cta(self):
        """The incident: total capacity still had room, the member's pool did
        not, and the page said "Register"."""
        self._set_caps(1, 4, 0)
        self._register(self._create_user_with_profile("m1@test.com", "M"))
        viewer = self._create_user_with_profile("viewer@test.com", "M")
        self.client.force_login(viewer)

        response = self._get_detail()
        # The event is nowhere near its total cap ...
        self.assertFalse(self.event.is_full)
        # ... but this member cannot take one of those seats.
        self.assertTrue(response.context["user_pool_full"])
        self.assertTrue(response.context["event_full_for_user"])
        self.assertContains(
            response,
            "All spots for your gender group are taken",
        )
        self.assertContains(response, "Join Waitlist")
        self.assertNotContains(response, "Register for This Event")

    def test_member_with_room_in_their_pool_still_gets_register_cta(self):
        self._set_caps(4, 1, 0)
        self._register(self._create_user_with_profile("f1@test.com", "F"))
        viewer = self._create_user_with_profile("viewer@test.com", "M")
        self.client.force_login(viewer)

        response = self._get_detail()
        self.assertFalse(response.context["event_full_for_user"])
        self.assertContains(response, "Register for This Event")
        self.assertNotContains(response, "Join Waitlist")

    def test_member_without_profile_gender_is_not_pool_waitlisted(self):
        """event_register only pool-waitlists a member whose gender resolves to
        a pool, so the CTA must not claim a waitlist for anyone else."""
        from crush_lu.models import CrushProfile

        self._set_caps(0, 0, 0)
        viewer = self._create_user("viewer@test.com")
        CrushProfile.objects.create(
            user=viewer,
            date_of_birth=date(1995, 1, 1),
            gender="",
            location="Luxembourg",
        )
        self.client.force_login(viewer)

        response = self._get_detail()
        self.assertIsNone(response.context["user_gender_pool"])
        self.assertFalse(response.context["user_pool_full"])
        self.assertFalse(response.context["event_full_for_user"])

    def test_cta_matches_what_registration_actually_does(self):
        """The whole point of the fix: the button and event_register must agree
        on who gets a seat. Registering as a pool-full member must land on the
        waitlist the CTA promised."""
        self._set_caps(1, 4, 0)
        self._register(self._create_user_with_profile("m1@test.com", "M"))
        viewer = self._create_user_with_profile("viewer@test.com", "M")
        self.client.force_login(viewer)

        response = self._get_detail()
        self.assertContains(response, "Join Waitlist")

        self.client.post(
            f"/en/events/{self.event.id}/register/",
            {"dietary_restrictions": "", "special_requests": ""},
        )
        from crush_lu.models import EventRegistration

        registration = EventRegistration.objects.get(event=self.event, user=viewer)
        self.assertEqual(registration.status, "waitlist")


class GenderPoolPremiumReservedSeatTests(GenderPoolAvailabilityTestBase):
    """A reserved premium seat does not survive a full gender pool."""

    def _make_premium(self, user):
        from crush_lu.models import CrushCoach, CrushProfile

        coach_user = User.objects.create_user(
            username=f"coach_{user.username}",
            email=f"coach_{user.username}",
            password="testpass123",
        )
        coach = CrushCoach.objects.create(user=coach_user, is_active=True)
        CrushProfile.objects.filter(user=user).update(assigned_coach=coach)

    def test_reserved_seat_banner_hidden_when_the_pool_is_full(self):
        """Premium buys a seat past `reserved_premium_seats`, not past a pool
        cap -- event_register waitlists them anyway, so promising a held seat
        would be the same lie in a different place."""
        self.event.max_participants = 2
        self.event.reserved_premium_seats = 1
        self.event.save()
        self._set_caps(1, 1, 0)
        self._register(self._create_user_with_profile("m1@test.com", "M"))

        viewer = self._create_user_with_profile("viewer@test.com", "M")
        self._make_premium(viewer)
        self.client.force_login(viewer)

        response = self._get_detail()
        # Publicly full (1/1 general seats), so the banner would normally show.
        self.assertTrue(self.event.is_full_for(is_premium=False))
        self.assertTrue(response.context["user_pool_full"])
        self.assertFalse(response.context["premium_reserved_seat_available"])
        self.assertNotContains(response, "A seat is reserved for you")

    def test_reserved_seat_banner_still_shows_when_the_pool_has_room(self):
        self.event.max_participants = 2
        self.event.reserved_premium_seats = 1
        self.event.save()
        self._set_caps(2, 2, 0)
        self._register(self._create_user_with_profile("f1@test.com", "F"))

        viewer = self._create_user_with_profile("viewer@test.com", "M")
        self._make_premium(viewer)
        self.client.force_login(viewer)

        response = self._get_detail()
        self.assertTrue(response.context["premium_reserved_seat_available"])
        self.assertContains(response, "A seat is reserved for you")
