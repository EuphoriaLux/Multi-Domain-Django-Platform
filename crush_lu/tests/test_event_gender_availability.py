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
        from django.core.cache import cache

        from crush_lu.models import MeetupEvent

        # event_register is @ratelimit(key="user", rate="5/h", method="POST")
        # and the cache is NOT rolled back between tests, while the user PK
        # sequence is -- on SQLite. Every test's viewer is therefore user 2,
        # they all share one counter, and the sixth POST in this file gets a
        # 429 that surfaces as a missing EventRegistration three lines later.
        #
        # Postgres hides this completely: its sequence keeps climbing through
        # the rollback, so each viewer gets a fresh key and a fresh budget. That
        # is the wrong way round from the usual warning -- here the local
        # Postgres run is the permissive one and CI's SQLite is strict.
        cache.clear()

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

    def test_capacity_remaining_caps_every_pool(self):
        """`pool_full` stays the pool's own cap -- the predicate registration
        re-checks -- while `remaining`/`is_full` answer what a viewer could
        actually claim. Conflating them is how a page offers a seat that a
        total or reserved-premium cap has already spoken for."""
        self._set_caps(4, 4, 4)
        self._register(self._create_user_with_profile("m1@test.com", "M"))

        pools = {
            p["key"]: p
            for p in self.event.get_gender_pool_availability(capacity_remaining=1)
        }
        self.assertEqual(pools["m"]["remaining"], 1)  # 3 by pool cap, 1 overall
        self.assertEqual(pools["f"]["remaining"], 1)  # 4 by pool cap, 1 overall
        self.assertFalse(pools["m"]["pool_full"])
        self.assertFalse(pools["m"]["is_full"])

        exhausted = {
            p["key"]: p
            for p in self.event.get_gender_pool_availability(capacity_remaining=0)
        }
        self.assertTrue(exhausted["m"]["is_full"])
        self.assertFalse(exhausted["m"]["pool_full"])

    def test_capacity_remaining_defaults_to_the_pool_cap(self):
        self._set_caps(2, 2, 2)
        self._register(self._create_user_with_profile("m1@test.com", "M"))
        self._register(self._create_user_with_profile("m2@test.com", "M"))

        pools = {p["key"]: p for p in self.event.get_gender_pool_availability()}
        self.assertTrue(pools["m"]["pool_full"])
        self.assertTrue(pools["m"]["is_full"])
        self.assertEqual(pools["f"]["remaining"], 2)


class CapacitySnapshotTests(GenderPoolAvailabilityTestBase):
    """One count behind both halves of "is it full / how many left".

    `is_full_for()` and `spots_remaining_for()` each issue their own COUNT, so
    a caller needing both read the database twice -- and a registration landing
    between those reads let one response contradict itself: `capacity_remaining`
    zero (every pool chip "full") beside `total_full` false (a CTA still
    offering a seat).
    """

    def _fill(self, n, gender="M"):
        for i in range(n):
            self._register(self._create_user_with_profile(f"u{i}@test.com", gender))

    def test_agrees_with_both_helpers_it_replaces(self):
        """Delegation must not shift either predicate: `remaining == 0` and the
        older `count >= cap` are the same test, max() only clamps the full side."""
        self.event.max_participants = 3
        self.event.save()
        for filled in range(0, 5):
            with self.subTest(filled=filled):
                is_full, remaining = self.event.capacity_snapshot()
                self.assertEqual(is_full, self.event.is_full_for())
                self.assertEqual(remaining, self.event.spots_remaining_for())
                if filled < 4:
                    self._register(
                        self._create_user_with_profile(f"f{filled}@test.com", "M")
                    )

    def test_respects_reserved_premium_seats_for_each_audience(self):
        self.event.max_participants = 3
        self.event.reserved_premium_seats = 1
        self.event.save()
        self._fill(2)

        self.assertEqual(self.event.capacity_snapshot(is_premium=False), (True, 0))
        self.assertEqual(self.event.capacity_snapshot(is_premium=True), (False, 1))

    def test_is_a_single_query(self):
        """The whole point: two reads could straddle a concurrent registration."""
        self._fill(1)
        with self.assertNumQueries(1):
            self.event.capacity_snapshot()

    def test_chips_and_cta_cannot_contradict_each_other(self):
        """Capacity zero now implies total_full by construction, so a row of
        "full" chips can never appear beside a CTA promising a seat."""
        self.event.max_participants = 3
        self.event.reserved_premium_seats = 1
        self.event.save()
        self._set_caps(3, 3, 0)
        self._fill(2)
        viewer = self._create_user_with_profile("viewer@test.com", "M")
        self.client.force_login(viewer)

        response = self._get_detail()
        pools = response.context["gender_pool_availability"]
        self.assertTrue(all(p["is_full"] for p in pools))
        self.assertTrue(response.context["event_full_for_user"])
        self.assertContains(response, "Join Waitlist")


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

    def _create_genderless_viewer(self):
        from crush_lu.models import CrushProfile

        viewer = self._create_user("viewer@test.com")
        CrushProfile.objects.create(
            user=viewer,
            date_of_birth=date(1995, 1, 1),
            gender="",
            location="Luxembourg",
        )
        return viewer

    def test_genderless_member_with_every_pool_full_gets_waitlist_cta(self):
        """ "No stored gender" is not "no pool": event_register makes them pick
        one and persists it *before* the pool check. Which pool they land in is
        unknowable here, but when every pool is full every choice waitlists --
        and the total cap still has slack, so nothing else would catch it."""
        self._set_caps(0, 0, 0)
        self.client.force_login(self._create_genderless_viewer())

        response = self._get_detail()
        self.assertFalse(self.event.is_full)
        self.assertIsNone(response.context["user_gender_pool"])
        self.assertTrue(response.context["event_full_for_user"])
        self.assertContains(response, "Join Waitlist")

    def test_genderless_member_with_room_somewhere_still_gets_register_cta(self):
        """Some pool has room, so the gender they pick may well seat them --
        promising a waitlist would be the same error in the other direction."""
        self._set_caps(0, 4, 0)
        self.client.force_login(self._create_genderless_viewer())

        response = self._get_detail()
        self.assertIsNone(response.context["user_gender_pool"])
        self.assertFalse(response.context["event_full_for_user"])
        self.assertContains(response, "Register for This Event")

    def test_seat_holder_is_not_told_to_join_the_waitlist(self):
        """Their own confirmed seat is part of what filled the pool, so the
        personal line would be advice on a page already showing their ticket."""
        self._set_caps(1, 4, 0)
        viewer = self._create_user_with_profile("viewer@test.com", "M")
        self._register(viewer)
        self.client.force_login(viewer)

        response = self._get_detail()
        # The chips still tell the truth about the pool ...
        self.assertContains(response, "Men: full")
        # ... but the viewer is in it, so no waitlist advice.
        self.assertNotContains(response, "All spots for your gender group are taken")

    def test_pool_chips_never_advertise_a_reserved_premium_seat(self):
        """Pool room means nothing if the only seat left is reserved: a general
        member reading "1 spot left for you" would still be waitlisted."""
        self.event.max_participants = 3
        self.event.reserved_premium_seats = 1
        self.event.save()
        self._set_caps(3, 3, 0)
        self._register(self._create_user_with_profile("m1@test.com", "M"))
        self._register(self._create_user_with_profile("f1@test.com", "F"))

        viewer = self._create_user_with_profile("viewer@test.com", "M")
        self.client.force_login(viewer)

        response = self._get_detail()
        # The men's pool cap alone is not reached (1 of 3) ...
        self.assertFalse(self.event.is_gender_pool_full("M"))
        # ... but general seats are gone, so nothing is offered.
        self.assertTrue(response.context["event_full_for_user"])
        self.assertContains(response, "Men: full")
        self.assertNotContains(response, "spots left for you")
        self.assertContains(response, "Join Waitlist")

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


class RegistrationPageAgreesWithTheCtaTests(GenderPoolAvailabilityTestBase):
    """The screen the CTA sends them to must not contradict it.

    Pressing "Join Waitlist" used to land on a page headed "Confirm
    Registration" with no warning, because event_register.html branched on
    `event.is_full` -- the raw total, blind to both a full gender pool and a
    reserved-premium block.
    """

    def _register_url(self):
        return f"/en/events/{self.event.id}/register/"

    def test_pool_full_member_sees_the_waitlist_warning_and_button(self):
        self._set_caps(1, 4, 0)
        self._register(self._create_user_with_profile("m1@test.com", "M"))
        viewer = self._create_user_with_profile("viewer@test.com", "M")
        self.client.force_login(viewer)

        response = self.client.get(self._register_url())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["registration_will_waitlist"])
        self.assertEqual(response.context["registration_waitlist_reason"], "pool")
        self.assertContains(response, "Join Waitlist")
        self.assertNotContains(response, "Confirm Registration")
        # "Event is Full" would be the same misinformation one screen later:
        # the event has seats, this member's pool does not.
        self.assertContains(response, "Your gender group is full")
        self.assertNotContains(response, "Event is Full")

    def test_totally_full_event_still_says_event_is_full(self):
        self.event.max_participants = 1
        self.event.save()
        self._set_caps(4, 4, 4)
        self._register(self._create_user_with_profile("f1@test.com", "F"))
        viewer = self._create_user_with_profile("viewer@test.com", "M")
        self.client.force_login(viewer)

        response = self.client.get(self._register_url())
        self.assertEqual(response.context["registration_waitlist_reason"], "total")
        self.assertContains(response, "Event is Full")
        self.assertContains(response, "Join Waitlist")

    def test_seatable_member_sees_confirm_registration(self):
        self._set_caps(4, 4, 2)
        viewer = self._create_user_with_profile("viewer@test.com", "M")
        self.client.force_login(viewer)

        response = self.client.get(self._register_url())
        self.assertFalse(response.context["registration_will_waitlist"])
        self.assertIsNone(response.context["registration_waitlist_reason"])
        self.assertContains(response, "Confirm Registration")
        self.assertNotContains(response, "Join Waitlist")

    def test_uncapped_event_is_unaffected(self):
        viewer = self._create_user_with_profile("viewer@test.com", "M")
        self.client.force_login(viewer)

        response = self.client.get(self._register_url())
        self.assertFalse(response.context["registration_will_waitlist"])
        self.assertContains(response, "Confirm Registration")


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


class WaitlistCtaSurvivesAValidationErrorTests(GenderPoolAvailabilityTestBase):
    """The HTMX re-render must not undo the corrected button.

    An invalid submit swaps `#registration-form-container` for
    `_event_registration_form.html`, which branched on `event.is_full` long
    after every other surface had stopped. A pool-full member who forgot a
    required field therefore watched "Join Waitlist" turn into "Confirm
    Registration" -- the same false promise as #866, now on the very screen
    where they are about to act on it.
    """

    def setUp(self):
        super().setUp()
        # The one validation failure this fixture can actually reach: the form
        # only keeps `bringing_guest`/`guest_name` when the event allows a plus
        # one, and clean() errors when the box is ticked with no name.
        self.event.allow_plus_ones = True
        self.event.save()

    def _post_invalid_htmx(self):
        return self.client.post(
            f"/en/events/{self.event.id}/register/",
            {"bringing_guest": "on", "guest_name": ""},
            HTTP_HX_REQUEST="true",
        )

    def test_pool_full_member_keeps_the_waitlist_button(self):
        self._set_caps(1, 4, 0)
        self._register(self._create_user_with_profile("m1@test.com", "M"))
        viewer = self._create_user_with_profile("viewer@test.com", "M")
        self.client.force_login(viewer)

        response = self._post_invalid_htmx()
        self.assertEqual(response.status_code, 200)
        # It really is the invalid-form partial, not a success render ...
        self.assertContains(response, "Please provide your guest&#x27;s name.")
        # ... and the button still tells the truth about what will happen.
        self.assertTrue(response.context["registration_will_waitlist"])
        self.assertContains(response, "Join Waitlist")
        self.assertNotContains(response, "Confirm Registration")

    def test_the_swap_carries_the_warning_banner_too(self):
        """The banner used to sit outside the swapped container, so it kept
        describing the capacity at page load while the button was recomputed --
        a cancellation in between left the two contradicting each other."""
        self._set_caps(1, 4, 0)
        self._register(self._create_user_with_profile("m1@test.com", "M"))
        viewer = self._create_user_with_profile("viewer@test.com", "M")
        self.client.force_login(viewer)

        response = self._post_invalid_htmx()
        # Same render produced both, so they cannot disagree.
        self.assertContains(response, "Your gender group is full")
        self.assertContains(response, "Join Waitlist")

    def test_the_swap_carries_the_reason_not_just_the_flag(self):
        """A totally full event must not be relabelled "your gender group" on
        the way through the partial -- the reason has to survive the swap."""
        self.event.max_participants = 1
        self.event.save()
        self._set_caps(4, 4, 4)
        self._register(self._create_user_with_profile("f1@test.com", "F"))
        viewer = self._create_user_with_profile("viewer@test.com", "M")
        self.client.force_login(viewer)

        response = self._post_invalid_htmx()
        self.assertEqual(response.context["registration_waitlist_reason"], "total")
        self.assertContains(response, "Event is Full")
        self.assertNotContains(response, "Your gender group is full")

    def test_a_seatable_member_gets_no_banner_in_the_swap(self):
        """The banner must be cleared by the swap, not merely added by it."""
        self._set_caps(4, 4, 0)
        viewer = self._create_user_with_profile("viewer@test.com", "M")
        self.client.force_login(viewer)

        response = self._post_invalid_htmx()
        self.assertNotContains(response, "You will be added to the waitlist")

    def _create_genderless_viewer(self):
        from crush_lu.models import CrushProfile

        viewer = self._create_user("viewer@test.com")
        CrushProfile.objects.create(
            user=viewer,
            date_of_birth=date(1995, 1, 1),
            gender="",
            location="Luxembourg",
        )
        return viewer

    def _post_invalid_htmx_choosing(self, gender):
        return self.client.post(
            f"/en/events/{self.event.id}/register/",
            {"bringing_guest": "on", "guest_name": "", "gender": gender},
            HTTP_HX_REQUEST="true",
        )

    def test_the_gender_just_chosen_is_used_even_though_nothing_saved_it(self):
        """The write lives on the valid branch, under the lock -- so after a
        validation error the profile is still genderless while the resubmit will
        pool-check the choice. Reading the profile alone fell back to "is every
        pool full?", answered no, and promised a seat the retry refuses."""
        self._set_caps(1, 4, 0)  # men's pool full, women's wide open
        self._register(self._create_user_with_profile("m1@test.com", "M"))
        viewer = self._create_genderless_viewer()
        self.client.force_login(viewer)

        response = self._post_invalid_htmx_choosing("M")
        # Nothing persisted it -- the profile is still genderless ...
        viewer.crushprofile.refresh_from_db()
        self.assertEqual(viewer.crushprofile.gender, "")
        # ... and not every pool is full, so the old fallback said "register".
        self.assertFalse(self.event.is_full)
        self.assertTrue(response.context["registration_will_waitlist"])
        self.assertEqual(response.context["registration_waitlist_reason"], "pool")
        self.assertContains(response, "Join Waitlist")
        self.assertContains(response, "Your gender group is full")

    def test_choosing_a_pool_with_room_still_offers_the_seat(self):
        """The other direction: the override must not blanket-waitlist."""
        self._set_caps(1, 4, 0)
        self._register(self._create_user_with_profile("m1@test.com", "M"))
        viewer = self._create_genderless_viewer()
        self.client.force_login(viewer)

        response = self._post_invalid_htmx_choosing("F")
        self.assertFalse(response.context["registration_will_waitlist"])
        self.assertContains(response, "Confirm Registration")
        self.assertNotContains(response, "Join Waitlist")

    def test_the_cta_matches_what_the_corrected_resubmit_does(self):
        """End to end: fix the guest name, resubmit, and the seat outcome must
        be the one the partial promised."""
        self._set_caps(1, 4, 0)
        self._register(self._create_user_with_profile("m1@test.com", "M"))
        viewer = self._create_genderless_viewer()
        self.client.force_login(viewer)

        self.assertContains(self._post_invalid_htmx_choosing("M"), "Join Waitlist")
        self.client.post(
            f"/en/events/{self.event.id}/register/",
            {"bringing_guest": "on", "guest_name": "Guest", "gender": "M"},
        )
        from crush_lu.models import EventRegistration

        registration = EventRegistration.objects.get(event=self.event, user=viewer)
        self.assertEqual(registration.status, "waitlist")

    def test_seatable_member_keeps_the_register_button(self):
        """The other direction, so the fix cannot be "always say waitlist"."""
        self._set_caps(4, 4, 0)
        viewer = self._create_user_with_profile("viewer@test.com", "M")
        self.client.force_login(viewer)

        response = self._post_invalid_htmx()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please provide your guest&#x27;s name.")
        self.assertFalse(response.context["registration_will_waitlist"])
        self.assertContains(response, "Confirm Registration")
        self.assertNotContains(response, "Join Waitlist")

    def test_reserved_premium_block_also_survives_the_re_render(self):
        """`event.is_full` was blind to this too: the event is not full, the
        pool is not full, and the viewer still cannot have a seat."""
        self.event.max_participants = 2
        self.event.reserved_premium_seats = 1
        self.event.save()
        self._set_caps(3, 3, 0)
        self._register(self._create_user_with_profile("m1@test.com", "M"))
        viewer = self._create_user_with_profile("viewer@test.com", "M")
        self.client.force_login(viewer)

        response = self._post_invalid_htmx()
        self.assertFalse(self.event.is_full)
        self.assertFalse(self.event.is_gender_pool_full("M"))
        self.assertTrue(response.context["registration_will_waitlist"])
        self.assertContains(response, "Join Waitlist")


class WaitlistReasonNamesTheRightCauseTests(GenderPoolAvailabilityTestBase):
    """ "Your gender group is full" must not be said when it is not true.

    `user_pool.is_full` folds the pool's own cap together with a total or
    reserved-premium block, so a viewer stopped by overall capacity read that
    their gender group was full while its cap sat untouched. The reason now
    travels from `_registration_outlook` into the component, which is the same
    value the registration page and the post-POST flash use -- so all three
    name one cause.
    """

    GENDER_LINE = "All spots for your gender group are taken"
    TOTAL_LINE = "The event is fully booked"

    def test_total_capacity_is_not_blamed_on_the_gender_pool(self):
        """Two general seats gone, a third reserved for premium: the men's cap
        is 3 and only one man holds a seat."""
        self.event.max_participants = 3
        self.event.reserved_premium_seats = 1
        self.event.save()
        self._set_caps(3, 3, 0)
        self._register(self._create_user_with_profile("m1@test.com", "M"))
        self._register(self._create_user_with_profile("f1@test.com", "F"))

        viewer = self._create_user_with_profile("viewer@test.com", "M")
        self.client.force_login(viewer)

        response = self._get_detail()
        self.assertFalse(self.event.is_gender_pool_full("M"))
        self.assertEqual(response.context["registration_waitlist_reason"], "total")
        self.assertContains(response, self.TOTAL_LINE)
        self.assertNotContains(response, self.GENDER_LINE)

    def test_a_full_pool_is_still_named_as_a_full_pool(self):
        self._set_caps(1, 4, 0)
        self._register(self._create_user_with_profile("m1@test.com", "M"))
        viewer = self._create_user_with_profile("viewer@test.com", "M")
        self.client.force_login(viewer)

        response = self._get_detail()
        self.assertEqual(response.context["registration_waitlist_reason"], "pool")
        self.assertContains(response, self.GENDER_LINE)
        self.assertNotContains(response, self.TOTAL_LINE)

    def test_total_wins_the_tie_exactly_as_the_other_surfaces_decide(self):
        """Both full: `_registration_outlook` returns "total", event_register's
        flash says "Event is full", and the chips must agree rather than pick
        the narrower cause on their own."""
        self.event.max_participants = 1
        self.event.save()
        self._set_caps(1, 4, 0)
        self._register(self._create_user_with_profile("m1@test.com", "M"))
        viewer = self._create_user_with_profile("viewer@test.com", "M")
        self.client.force_login(viewer)

        response = self._get_detail()
        self.assertTrue(self.event.is_gender_pool_full("M"))
        self.assertTrue(self.event.is_full)
        self.assertEqual(response.context["registration_waitlist_reason"], "total")
        self.assertContains(response, self.TOTAL_LINE)
        self.assertNotContains(response, self.GENDER_LINE)

    def test_a_seatable_member_gets_neither_line(self):
        self._set_caps(4, 4, 0)
        viewer = self._create_user_with_profile("viewer@test.com", "M")
        self.client.force_login(viewer)

        response = self._get_detail()
        self.assertIsNone(response.context["registration_waitlist_reason"])
        self.assertContains(response, "spots left for you.")
        self.assertNotContains(response, self.TOTAL_LINE)
        self.assertNotContains(response, self.GENDER_LINE)
