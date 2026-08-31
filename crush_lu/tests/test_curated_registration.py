"""Curated speed-dating registration (Phase 2).

A speed-dating event set to ``registration_mode="curated"`` takes sign-ups as
*applications* rather than seats: status ``applied``, which is deliberately
absent from ``SEAT_HOLDING_STATUSES``. That absence is the whole mechanism —
capacity, door tickets, check-in, reminders and the metrics rollups all derive
from that constant, so an application cannot consume a place or mint a ticket.
The organiser composes the group afterwards by moving applicants into a
seat-holding status.

Every event that has not opted in must behave exactly as before. That includes
every mixer, every direct-mode speed-dating event, and — because
``uses_curated_registration`` checks the type as well as the field — any
non-speed-dating event whose mode was flipped to curated by mistake.

Paths are literal because the host middleware swaps the urlconf per domain --
``reverse("crush_lu:event_register")`` builds a ``/crush/...`` path that 404s
under ``HTTP_HOST=crush.lu``.

Run with: pytest crush_lu/tests/test_curated_registration.py -v
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

User = get_user_model()


class CuratedRegistrationTestBase(TestCase):
    """A curated speed-dating event, a direct one, and a mixer as controls."""

    def setUp(self):
        from django.core.cache import cache

        from crush_lu.models import MeetupEvent

        # event_register is @ratelimit(key="user", rate="5/h", method="POST"),
        # and the cache is NOT rolled back between tests while the user PK
        # sequence is — without this the sixth POST in the file 429s.
        cache.clear()

        self.client = Client(HTTP_HOST="crush.lu")
        self.curated = self._make_event(
            "Curated Speed Dating",
            event_type="speed_dating",
            registration_mode="curated",
        )
        self.direct = self._make_event(
            "Direct Speed Dating",
            event_type="speed_dating",
            registration_mode="direct",
        )
        self.mixer = self._make_event("Social Mixer", event_type="mixer")
        self.user = self._create_member("member@example.com")

    def _make_event(self, title, event_type, registration_mode="direct", **extra):
        from crush_lu.models import MeetupEvent

        defaults = dict(
            title=title,
            description="Curated registration test",
            event_type=event_type,
            registration_mode=registration_mode,
            date_time=timezone.now() + timedelta(days=7),
            location="Luxembourg",
            address="123 Test Street",
            max_participants=2,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True,
            profile_requirement="none",
        )
        defaults.update(extra)
        return MeetupEvent.objects.create(**defaults)

    def _create_member(self, username, gender="M"):
        from crush_lu.models import CrushProfile, UserDataConsent

        user = User.objects.create_user(
            username=username,
            email=username,
            password="testpass123",
            first_name=username.split("@")[0],
        )
        # consent_middleware is scoped to urls_crush and 302s every page
        # without this.
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

    def _login(self, user):
        self.client.login(username=user.username, password="testpass123")

    def _register_url(self, event):
        return f"/en/events/{event.id}/register/"

    def _register(self, user, event, **extra):
        self._login(user)
        payload = {"preferred_age_min": "25", "preferred_age_max": "40"}
        payload.update(extra)
        return self.client.post(self._register_url(event), payload)


class CuratedModeGatingTests(CuratedRegistrationTestBase):
    def test_curated_speed_dating_event_uses_curated_registration(self):
        self.assertTrue(self.curated.uses_curated_registration)

    def test_direct_speed_dating_event_does_not(self):
        self.assertFalse(self.direct.uses_curated_registration)

    def test_curated_mode_on_a_mixer_is_ignored(self):
        """The type gate, not just the field, decides.

        Curated only makes sense where a preference snapshot is collected to
        compose the group. A mixer flipped to curated by mistake must keep
        admitting people directly rather than silently swallowing sign-ups
        into a pool nobody is going to select from.
        """
        self.mixer.registration_mode = "curated"
        self.mixer.save(update_fields=["registration_mode"])
        self.assertFalse(self.mixer.uses_curated_registration)

    def test_default_mode_is_direct(self):
        from crush_lu.models import MeetupEvent

        event = MeetupEvent.objects.get(pk=self.direct.pk)
        self.assertEqual(event.registration_mode, "direct")


class CuratedSignupTests(CuratedRegistrationTestBase):
    def test_signup_lands_as_applied(self):
        from crush_lu.models import EventRegistration

        response = self._register(self.user, self.curated)
        self.assertEqual(response.status_code, 302)

        registration = EventRegistration.objects.get(
            event=self.curated, user=self.user
        )
        self.assertEqual(registration.status, "applied")

    def test_preferences_are_still_collected(self):
        """Phase 1's snapshot is what the organiser selects on."""
        from crush_lu.models import EventRegistration, EventRegistrationPreference

        self._register(
            self.user, self.curated, preferred_genders=["F"], languages=["en"]
        )
        registration = EventRegistration.objects.get(
            event=self.curated, user=self.user
        )
        pref = EventRegistrationPreference.objects.get(registration=registration)
        self.assertEqual(pref.preferred_genders, ["F"])
        self.assertEqual(pref.preferred_age_min, 25)
        self.assertEqual(pref.preferred_age_max, 40)

    def test_direct_speed_dating_still_confirms(self):
        from crush_lu.models import EventRegistration

        self._register(self.user, self.direct)
        registration = EventRegistration.objects.get(event=self.direct, user=self.user)
        self.assertEqual(registration.status, "confirmed")

    def test_mixer_still_confirms(self):
        from crush_lu.models import EventRegistration

        self._register(self.user, self.mixer)
        registration = EventRegistration.objects.get(event=self.mixer, user=self.user)
        self.assertEqual(registration.status, "confirmed")

    def test_paid_curated_event_does_not_ask_for_payment_yet(self):
        """Money comes after selection, not at application time.

        On a direct paid event the seat is held as "pending" and the member is
        sent to checkout immediately. A curated applicant has not been given a
        place yet, so asking them to pay would be selling something that may
        not exist.
        """
        from decimal import Decimal

        from crush_lu.models import EventRegistration

        self.curated.registration_fee = Decimal("15.00")
        self.curated.save(update_fields=["registration_fee"])

        self._register(self.user, self.curated)
        registration = EventRegistration.objects.get(
            event=self.curated, user=self.user
        )
        self.assertEqual(registration.status, "applied")
        self.assertFalse(registration.payment_confirmed)


class ApplicationsHoldNoSeatTests(CuratedRegistrationTestBase):
    """The core invariant: applications must not consume capacity."""

    def test_applications_may_outnumber_the_places(self):
        from crush_lu.models import EventRegistration

        # max_participants is 2; send three applicants.
        for i in range(3):
            user = self._create_member(f"applicant{i}@example.com")
            self._register(user, self.curated)

        registrations = EventRegistration.objects.filter(event=self.curated)
        self.assertEqual(registrations.count(), 3)
        self.assertEqual(
            set(registrations.values_list("status", flat=True)), {"applied"}
        )

    def test_applications_do_not_count_toward_capacity(self):
        for i in range(3):
            user = self._create_member(f"capacity{i}@example.com")
            self._register(user, self.curated)

        self.curated.refresh_from_db()
        self.assertEqual(self.curated.get_confirmed_count(), 0)
        self.assertEqual(self.curated.get_applied_count(), 3)
        self.assertFalse(self.curated.is_full)

    def test_nobody_is_waitlisted_on_a_curated_event(self):
        """There is no queue to be behind while nobody has been admitted."""
        from crush_lu.models import EventRegistration

        for i in range(3):
            user = self._create_member(f"nowait{i}@example.com")
            self._register(user, self.curated)

        self.assertFalse(
            EventRegistration.objects.filter(
                event=self.curated, status="waitlist"
            ).exists()
        )

    def test_applied_is_absent_from_seat_holding_statuses(self):
        """Pinned directly: every capacity, ticket and check-in path keys off
        this constant, so the exclusion is the mechanism, not a side effect."""
        from crush_lu.models.events import SEAT_HOLDING_STATUSES

        self.assertNotIn("applied", SEAT_HOLDING_STATUSES)

    def test_annotated_counts_agree_with_the_methods(self):
        """with_registration_counts() is preferred over the method in loops, and
        get_confirmed_count() returns the annotation when present — so the two
        disagreeing would make one event report different capacities depending
        on how it was fetched."""
        from crush_lu.models import MeetupEvent

        for i in range(3):
            user = self._create_member(f"annotated{i}@example.com")
            self._register(user, self.curated)

        annotated = MeetupEvent.objects.with_registration_counts().get(
            pk=self.curated.pk
        )
        self.assertEqual(annotated.confirmed_count_annotated, 0)
        self.assertEqual(annotated.applied_count_annotated, 3)
        self.assertEqual(annotated.get_confirmed_count(), 0)
        self.assertEqual(annotated.get_applied_count(), 3)


class ApplicantSurfacesTests(CuratedRegistrationTestBase):
    def test_applicant_gets_no_door_ticket(self):
        """Ticket validity derives from SEAT_HOLDING_STATUSES."""
        from crush_lu.models import EventRegistration
        from crush_lu.models.events import SEAT_HOLDING_STATUSES

        self._register(self.user, self.curated)
        registration = EventRegistration.objects.get(
            event=self.curated, user=self.user
        )
        self.assertNotIn(registration.status, SEAT_HOLDING_STATUSES)

    def test_applying_twice_is_refused(self):
        """The already-registered guard excludes only cancelled rows, so an
        application blocks a second one."""
        from crush_lu.models import EventRegistration

        self._register(self.user, self.curated)
        self._register(self.user, self.curated)

        self.assertEqual(
            EventRegistration.objects.filter(
                event=self.curated, user=self.user
            ).count(),
            1,
        )

    def test_checkout_is_refused_for_an_application(self):
        """Money-safety invariant, pinned server-side rather than in the UI.

        create_sumup_event_checkout allow-lists ("pending", "confirmed"), so an
        applicant cannot open a checkout even by posting directly — hiding the
        button is presentation, this is the actual guard. Selling a place the
        organiser has not awarded is the failure being prevented.
        """
        from decimal import Decimal

        from crush_lu.models import EventRegistration

        self.curated.registration_fee = Decimal("15.00")
        self.curated.save(update_fields=["registration_fee"])
        self._register(self.user, self.curated)
        registration = EventRegistration.objects.get(
            event=self.curated, user=self.user
        )
        self.assertEqual(registration.status, "applied")

        # A valid payment_method is required, otherwise the view rejects on
        # that instead and the test would pass without ever reaching the
        # status guard it exists to pin.
        response = self.client.post(
            f"/payments/sumup/create-event-checkout/{registration.pk}/",
            data='{"payment_method": "card"}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("current state", response.json()["error"])

    def test_applicant_can_withdraw(self):
        from crush_lu.models import EventRegistration

        self._register(self.user, self.curated)
        response = self.client.post(f"/en/events/{self.curated.id}/cancel/")
        self.assertIn(response.status_code, (200, 302))

        registration = EventRegistration.objects.get(
            event=self.curated, user=self.user
        )
        self.assertEqual(registration.status, "cancelled")


class OrganiserSelectionTests(CuratedRegistrationTestBase):
    """Phase 2 selection is a manual status change in Django admin; what
    matters is that the change grants a seat through the normal machinery."""

    def test_selecting_an_applicant_grants_a_seat(self):
        from crush_lu.models import EventRegistration

        self._register(self.user, self.curated)
        registration = EventRegistration.objects.get(
            event=self.curated, user=self.user
        )
        self.assertEqual(self.curated.get_confirmed_count(), 0)

        registration.status = "confirmed"
        registration.save(update_fields=["status"])

        self.curated.refresh_from_db()
        self.assertEqual(self.curated.get_confirmed_count(), 1)
        self.assertEqual(self.curated.get_applied_count(), 0)

    def test_unselected_applicants_leave_capacity_free(self):
        from crush_lu.models import EventRegistration

        chosen = self._create_member("chosen@example.com")
        passed_over = self._create_member("passedover@example.com")
        self._register(chosen, self.curated)
        self._register(passed_over, self.curated)

        registration = EventRegistration.objects.get(
            event=self.curated, user=chosen
        )
        registration.status = "confirmed"
        registration.save(update_fields=["status"])

        self.curated.refresh_from_db()
        self.assertEqual(self.curated.get_confirmed_count(), 1)
        self.assertEqual(self.curated.get_applied_count(), 1)
        self.assertFalse(self.curated.is_full)


class CoachApplicantVisibilityTests(CuratedRegistrationTestBase):
    """The organiser has to be able to see the pool they are selecting from.

    An "applied" registration matches none of the coach page's existing
    buckets (confirmed / waitlist / pending+no_show), so without its own bucket
    the applicants — and the preference chips that inform the choice — would be
    invisible on the one page built to show them.
    """

    def _make_coach(self):
        from crush_lu.models import CrushCoach

        user = self._create_member("coach@example.com")
        CrushCoach.objects.create(user=user, is_active=True)
        return user

    def test_applicants_appear_in_their_own_bucket(self):
        applicant = self._create_member("applicant@example.com")
        self._register(applicant, self.curated)

        coach = self._make_coach()
        self._login(coach)
        response = self.client.get(f"/en/coach/events/{self.curated.id}/")
        self.assertEqual(response.status_code, 200)

        applied = response.context["applied_registrations"]
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0].user_id, applicant.pk)
        self.assertEqual(response.context["applied_count"], 1)

    def test_applicants_stay_out_of_the_confirmed_bucket(self):
        """They hold no seat, so they must not inflate confirmed_count or eat
        spots_remaining — the numbers the organiser plans capacity against."""
        applicant = self._create_member("notconfirmed@example.com")
        self._register(applicant, self.curated)

        coach = self._make_coach()
        self._login(coach)
        response = self.client.get(f"/en/coach/events/{self.curated.id}/")

        self.assertEqual(list(response.context["confirmed_registrations"]), [])
        self.assertEqual(list(response.context["waitlist_registrations"]), [])
        self.assertEqual(list(response.context["other_registrations"]), [])
        self.assertEqual(response.context["confirmed_count"], 0)
        self.assertEqual(
            response.context["spots_remaining"], self.curated.max_participants
        )
