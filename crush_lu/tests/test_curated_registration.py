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

        registration = EventRegistration.objects.get(event=self.curated, user=self.user)
        self.assertEqual(registration.status, "applied")

    def test_preferences_are_still_collected(self):
        """Phase 1's snapshot is what the organiser selects on."""
        from crush_lu.models import EventRegistration, EventRegistrationPreference

        self._register(
            self.user, self.curated, preferred_genders=["F"], languages=["en"]
        )
        registration = EventRegistration.objects.get(event=self.curated, user=self.user)
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
        registration = EventRegistration.objects.get(event=self.curated, user=self.user)
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
        registration = EventRegistration.objects.get(event=self.curated, user=self.user)
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
        registration = EventRegistration.objects.get(event=self.curated, user=self.user)
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

        registration = EventRegistration.objects.get(event=self.curated, user=self.user)
        self.assertEqual(registration.status, "cancelled")


class OrganiserSelectionTests(CuratedRegistrationTestBase):
    """Phase 2 selection is a manual status change in Django admin; what
    matters is that the change grants a seat through the normal machinery."""

    def test_selecting_an_applicant_grants_a_seat(self):
        from crush_lu.models import EventRegistration

        self._register(self.user, self.curated)
        registration = EventRegistration.objects.get(event=self.curated, user=self.user)
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

        registration = EventRegistration.objects.get(event=self.curated, user=chosen)
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


class CuratedSurfaceConsistencyTests(CuratedRegistrationTestBase):
    """Surfaces must not describe an applicant as someone holding a seat.

    Adding "applied" to the member's own event lists exposed the status to
    templates whose fall-through branch means "confirmed". An unselected
    applicant shown as admitted — or handed a ticket button that only 403s — is
    worse than not seeing the application at all.
    """

    def test_event_detail_does_not_call_an_applicant_registered(self):
        self._register(self.user, self.curated)
        response = self.client.get(f"/en/events/{self.curated.id}/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("Your application is in!", html)
        self.assertNotIn("You&#x27;re registered for this event!", html)

    def test_event_detail_offers_no_checkout_to_a_paid_applicant(self):
        """The server rejects the click anyway; the point is not to offer a
        payment that is designed to fail."""
        from decimal import Decimal

        self.curated.registration_fee = Decimal("15.00")
        self.curated.save(update_fields=["registration_fee"])
        self._register(self.user, self.curated)

        response = self.client.get(f"/en/events/{self.curated.id}/")
        self.assertNotContains(response, "js-sumup-checkout-detail")

    def test_my_events_shows_applied_not_confirmed(self):
        self._register(self.user, self.curated)
        response = self.client.get("/en/my-events/")
        self.assertEqual(response.status_code, 200)
        entries = [
            e
            for e in response.context["upcoming_registrations"]
            if e["event"].pk == self.curated.pk
        ]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["registration"].status, "applied")
        # Neither a seat nor money owed.
        self.assertFalse(entries[0]["is_waitlist"])
        self.assertFalse(entries[0]["is_pending_payment"])
        # ...but withdrawing is still allowed.
        self.assertTrue(entries[0]["can_cancel"])

    def test_my_events_offers_no_ticket_to_an_applicant(self):
        """event_ticket gates on SEAT_HOLDING_STATUSES, so the link would 403."""
        self._register(self.user, self.curated)
        response = self.client.get("/en/my-events/")
        self.assertNotContains(response, f"/events/{self.curated.id}/ticket/")


class CuratedOutlookTests(CuratedRegistrationTestBase):
    """A curated event never waitlists, so no surface may offer a waitlist."""

    def _fill_to_capacity(self):
        from crush_lu.models import EventRegistration

        for i in range(self.curated.max_participants):
            user = self._create_member(f"seated{i}@example.com")
            EventRegistration.objects.create(
                event=self.curated, user=user, status="confirmed"
            )

    def test_full_curated_event_still_reports_no_waitlist(self):
        from crush_lu.views_events import _registration_outlook

        self._fill_to_capacity()
        self.curated.refresh_from_db()
        self.assertTrue(self.curated.is_full)

        _pools, _user_pool, will_waitlist, reason = _registration_outlook(
            self.curated, self.user.crushprofile
        )
        self.assertFalse(will_waitlist)
        self.assertIsNone(reason)

    def test_full_direct_event_still_reports_a_waitlist(self):
        """The curated bypass must not leak into ordinary events."""
        from crush_lu.models import EventRegistration
        from crush_lu.views_events import _registration_outlook

        for i in range(self.direct.max_participants):
            user = self._create_member(f"directseat{i}@example.com")
            EventRegistration.objects.create(
                event=self.direct, user=user, status="confirmed"
            )
        self.direct.refresh_from_db()

        _pools, _user_pool, will_waitlist, reason = _registration_outlook(
            self.direct, self.user.crushprofile
        )
        self.assertTrue(will_waitlist)
        self.assertEqual(reason, "total")

    def test_applying_to_a_full_curated_event_still_applies(self):
        """Not merely cosmetic: the outlook must agree with what POST does."""
        from crush_lu.models import EventRegistration

        self._fill_to_capacity()
        self._register(self.user, self.curated)

        registration = EventRegistration.objects.get(event=self.curated, user=self.user)
        self.assertEqual(registration.status, "applied")


class CoachApplicationsFilterTests(CuratedRegistrationTestBase):
    def _make_coach(self):
        from crush_lu.models import CrushCoach

        user = self._create_member("filtercoach@example.com")
        CrushCoach.objects.create(user=user, is_active=True)
        return user

    def test_applied_filter_is_reachable(self):
        """The template branch is dead unless the view's allow-list keeps it."""
        self._register(self._create_member("f1@example.com"), self.curated)

        self._login(self._make_coach())
        response = self.client.get(
            f"/en/coach/events/{self.curated.id}/?status=applied"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["status_filter"], "applied")

    def test_signup_total_counts_applications(self):
        """Ten cards under a "Total Signups: 0" heading is not a summary."""
        for i in range(3):
            self._register(self._create_member(f"t{i}@example.com"), self.curated)

        self._login(self._make_coach())
        response = self.client.get(f"/en/coach/events/{self.curated.id}/")
        self.assertEqual(response.context["total_registrations"], 3)
        # Capacity is a different number and still excludes them.
        self.assertEqual(response.context["confirmed_count"], 0)


class CuratedAdminSelectionTests(CuratedRegistrationTestBase):
    """The bulk confirm action is the obvious way to select a group."""

    def _applied_registration(self, username="bulk@example.com"):
        from crush_lu.models import EventRegistration

        user = self._create_member(username)
        self._register(user, self.curated)
        return EventRegistration.objects.get(event=self.curated, user=user)

    def _run_confirm_action(self, queryset):
        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from crush_lu.admin.events import EventRegistrationAdmin
        from crush_lu.models import EventRegistration

        request = RequestFactory().post("/")
        request.user = self.user
        request.session = self.client.session
        request._messages = FallbackStorage(request)

        admin_instance = EventRegistrationAdmin(EventRegistration, AdminSite())
        admin_instance.confirm_registrations(request, queryset)

    def test_free_event_application_is_confirmed(self):
        from crush_lu.models import EventRegistration

        registration = self._applied_registration("free@example.com")
        self._run_confirm_action(EventRegistration.objects.filter(pk=registration.pk))

        registration.refresh_from_db()
        self.assertEqual(registration.status, "confirmed")

    def test_paid_event_application_goes_to_pending_not_confirmed(self):
        """Money-safety: confirming outright would grant a seat, a door ticket
        and check-in eligibility on a paid event before anyone paid."""
        from decimal import Decimal

        from crush_lu.models import EventRegistration

        self.curated.registration_fee = Decimal("15.00")
        self.curated.save(update_fields=["registration_fee"])
        registration = self._applied_registration("paid@example.com")

        self._run_confirm_action(EventRegistration.objects.filter(pk=registration.pk))

        registration.refresh_from_db()
        self.assertEqual(registration.status, "pending")
        self.assertFalse(registration.payment_confirmed)

    def test_a_cancelled_row_on_a_paid_event_still_confirms(self):
        """The paid-event rule is scoped to applications: every pre-existing
        path through this action must keep behaving exactly as before."""
        from decimal import Decimal

        from crush_lu.models import EventRegistration

        self.direct.registration_fee = Decimal("15.00")
        self.direct.save(update_fields=["registration_fee"])
        user = self._create_member("wascancelled@example.com")
        registration = EventRegistration.objects.create(
            event=self.direct, user=user, status="cancelled"
        )

        self._run_confirm_action(EventRegistration.objects.filter(pk=registration.pk))

        registration.refresh_from_db()
        self.assertEqual(registration.status, "confirmed")


class CuratedOverbookingTests(CuratedRegistrationTestBase):
    """The applicant pool is meant to outnumber the seats, which makes
    over-selecting an ordinary slip rather than an edge case."""

    def _applications(self, count):
        from crush_lu.models import EventRegistration

        for i in range(count):
            self._register(self._create_member(f"over{i}@example.com"), self.curated)
        return EventRegistration.objects.filter(event=self.curated, status="applied")

    def _run_confirm_action(self, queryset):
        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from crush_lu.admin.events import EventRegistrationAdmin
        from crush_lu.models import EventRegistration

        request = RequestFactory().post("/")
        request.user = self.user
        request.session = self.client.session
        request._messages = FallbackStorage(request)

        admin_instance = EventRegistrationAdmin(EventRegistration, AdminSite())
        admin_instance.confirm_registrations(request, queryset)

    def test_selecting_more_applications_than_places_changes_nothing(self):
        from crush_lu.models import EventRegistration

        # max_participants is 2.
        applications = self._applications(3)
        self._run_confirm_action(applications)

        self.assertEqual(
            EventRegistration.objects.filter(
                event=self.curated, status="applied"
            ).count(),
            3,
        )
        self.assertEqual(self.curated.get_confirmed_count(), 0)

    def test_selecting_exactly_the_remaining_places_succeeds(self):
        from crush_lu.models import EventRegistration

        applications = self._applications(2)
        self._run_confirm_action(applications)

        self.assertEqual(
            EventRegistration.objects.filter(
                event=self.curated, status="confirmed"
            ).count(),
            2,
        )

    def test_capacity_already_taken_is_counted(self):
        """Remaining places, not total places — a second selection round must
        not re-spend seats the first one already awarded."""
        from crush_lu.models import EventRegistration

        seated = self._create_member("seated@example.com")
        EventRegistration.objects.create(
            event=self.curated, user=seated, status="confirmed"
        )
        applications = self._applications(2)  # only 1 place left
        self._run_confirm_action(applications)

        self.assertEqual(
            EventRegistration.objects.filter(
                event=self.curated, status="applied"
            ).count(),
            2,
        )


class RegistrationModeChangeTests(CuratedRegistrationTestBase):
    """An event cannot change what signing up MEANS once people have signed up.

    Guarded on effective behaviour rather than the registration_mode field
    alone: event_type is the other half of the switch, so turning a curated
    speed-dating event into a mixer would otherwise slip past untouched while
    making every later sign-up direct.
    """

    def _form(self, event, **changes):
        from crush_lu.admin.events import MeetupEventAdminForm

        data = {
            "event_type": event.event_type,
            "registration_mode": event.registration_mode,
        }
        data.update(changes)
        return MeetupEventAdminForm(data=data, instance=event)

    def _blocked(self, form):
        """The objection is a non-field error raised from clean()."""
        form.is_valid()
        return any(
            "switches what signing up means" in str(e) for e in form.non_field_errors()
        )

    def test_mode_change_is_refused_once_someone_has_signed_up(self):
        self._register(self.user, self.curated)
        self.assertTrue(
            self._blocked(self._form(self.curated, registration_mode="direct"))
        )

    def test_event_type_change_is_refused_too(self):
        """The hole a mode-only guard leaves: the mode stays "curated" and is
        simply ignored once the type is no longer speed dating."""
        self._register(self.user, self.curated)
        self.assertTrue(self._blocked(self._form(self.curated, event_type="mixer")))

    def test_turning_a_mixer_into_curated_speed_dating_is_refused(self):
        """The inverse: an ignored "curated" mode becoming an active one."""
        self.mixer.registration_mode = "curated"
        self.mixer.save(update_fields=["registration_mode"])
        self._register(self.user, self.mixer)  # admitted directly, mode ignored

        self.assertTrue(
            self._blocked(self._form(self.mixer, event_type="speed_dating"))
        )

    def test_change_is_allowed_with_no_live_signups(self):
        """A cancelled sign-up does not lock the event."""
        from crush_lu.models import EventRegistration

        EventRegistration.objects.create(
            event=self.curated, user=self.user, status="cancelled"
        )
        self.assertFalse(
            self._blocked(self._form(self.curated, registration_mode="direct"))
        )

    def test_an_unrelated_edit_is_never_blocked(self):
        """Editing any other field on an event with sign-ups must still work —
        the guard fires on a behaviour change, not on every save."""
        self._register(self.user, self.curated)
        self.assertFalse(self._blocked(self._form(self.curated)))

    def test_a_type_change_that_does_not_switch_behaviour_is_allowed(self):
        """direct speed dating -> mixer is both non-curated: nothing changes
        about what a sign-up means, so there is nothing to protect."""
        self._register(self.user, self.direct)
        self.assertFalse(self._blocked(self._form(self.direct, event_type="mixer")))


class CuratedGenderPoolSelectionTests(CuratedRegistrationTestBase):
    """Total capacity is not the only cap the selection has to respect."""

    def _pooled_event(self):
        event = self._make_event(
            "Pooled Speed Dating",
            event_type="speed_dating",
            registration_mode="curated",
            max_participants=4,
            max_participants_m=1,
            max_participants_f=1,
            max_participants_nb=2,
        )
        return event

    def _run_confirm_action(self, queryset):
        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from crush_lu.admin.events import EventRegistrationAdmin
        from crush_lu.models import EventRegistration

        request = RequestFactory().post("/")
        request.user = self.user
        request.session = self.client.session
        request._messages = FallbackStorage(request)

        admin_instance = EventRegistrationAdmin(EventRegistration, AdminSite())
        admin_instance.confirm_registrations(request, queryset)

    def test_a_full_gender_pool_blocks_selection_despite_free_seats(self):
        """Two men for one male place, on an event with four seats free: the
        total test passes and the pool test is the only thing standing in the
        way."""
        from crush_lu.models import EventRegistration

        event = self._pooled_event()
        for i in range(2):
            user = self._create_member(f"male{i}@example.com", gender="M")
            EventRegistration.objects.create(event=event, user=user, status="applied")

        applications = EventRegistration.objects.filter(event=event, status="applied")
        self._run_confirm_action(applications)

        self.assertEqual(
            EventRegistration.objects.filter(event=event, status="applied").count(),
            2,
        )
        self.assertEqual(event.get_confirmed_count(), 0)

    def test_selection_within_every_pool_succeeds(self):
        from crush_lu.models import EventRegistration

        event = self._pooled_event()
        for gender, name in (("M", "poolm"), ("F", "poolf")):
            user = self._create_member(f"{name}@example.com", gender=gender)
            EventRegistration.objects.create(event=event, user=user, status="applied")

        applications = EventRegistration.objects.filter(event=event, status="applied")
        self._run_confirm_action(applications)

        self.assertEqual(
            EventRegistration.objects.filter(event=event, status="confirmed").count(),
            2,
        )

    def test_seats_already_taken_in_a_pool_are_counted(self):
        from crush_lu.models import EventRegistration

        event = self._pooled_event()
        seated = self._create_member("seatedmale@example.com", gender="M")
        EventRegistration.objects.create(event=event, user=seated, status="confirmed")
        applicant = self._create_member("secondmale@example.com", gender="M")
        EventRegistration.objects.create(event=event, user=applicant, status="applied")

        self._run_confirm_action(
            EventRegistration.objects.filter(event=event, status="applied")
        )

        self.assertEqual(
            EventRegistration.objects.filter(event=event, status="applied").count(),
            1,
        )


class ConcurrentSelectionTests(CuratedRegistrationTestBase):
    """Two admins selecting the same application must not grant a free seat.

    The failure this pins: both admins read the row as "applied"; the first
    moves it to "pending" (payment owed); the second then finds it is no
    longer in the paid set and — deriving its confirm list from the stale
    pre-lock selection — confirms it outright. A paid seat, granted for free,
    through the obvious bulk action.
    """

    def _run_confirm_action(self, queryset):
        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from crush_lu.admin.events import EventRegistrationAdmin
        from crush_lu.models import EventRegistration

        request = RequestFactory().post("/")
        request.user = self.user
        request.session = self.client.session
        request._messages = FallbackStorage(request)

        admin_instance = EventRegistrationAdmin(EventRegistration, AdminSite())
        admin_instance.confirm_registrations(request, queryset)

    def test_a_row_another_admin_already_moved_is_left_alone(self):
        """Simulates the other admin committing between our unlocked read and
        our locked re-read, by flipping the row exactly at that boundary."""
        from decimal import Decimal
        from unittest.mock import patch

        from crush_lu.models import EventRegistration

        self.curated.registration_fee = Decimal("15.00")
        self.curated.save(update_fields=["registration_fee"])

        applicant = self._create_member("raced@example.com")
        self._register(applicant, self.curated)
        registration = EventRegistration.objects.get(event=self.curated, user=applicant)
        self.assertEqual(registration.status, "applied")

        real_select_for_update = EventRegistration.objects.select_for_update

        def _flip_then_lock(*args, **kwargs):
            # The competing admin's write lands here — after our unlocked read
            # saw "applied", before our locked re-read runs.
            EventRegistration.objects.filter(pk=registration.pk).update(
                status="pending"
            )
            return real_select_for_update(*args, **kwargs)

        with patch.object(
            EventRegistration.objects, "select_for_update", _flip_then_lock
        ):
            self._run_confirm_action(
                EventRegistration.objects.filter(pk=registration.pk)
            )

        registration.refresh_from_db()
        # Left as the other admin set it. Confirmed here would mean a paid seat
        # handed over with payment_confirmed still False.
        self.assertEqual(registration.status, "pending")
        self.assertFalse(registration.payment_confirmed)


class LockedQueryShapeTests(CuratedRegistrationTestBase):
    """The locked read must not join the nullable side of an outer join.

    PostgreSQL rejects ``FOR UPDATE`` against the nullable side of a LEFT OUTER
    JOIN with ``NotSupportedError``, and ``user__crushprofile`` is a *reverse*
    one-to-one, so ``select_related`` on it produces exactly that join. The
    action would abort before updating anyone.

    SQLite ignores ``select_for_update()`` altogether, so no ordinary test can
    see this failure — the assertion is therefore on the SQL the ORM builds
    rather than on the database's reaction to it. ``_promote_from_waitlist``
    carries the same warning for the same reason.
    """

    def _run_confirm_action(self, queryset):
        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from crush_lu.admin.events import EventRegistrationAdmin
        from crush_lu.models import EventRegistration

        request = RequestFactory().post("/")
        request.user = self.user
        request.session = self.client.session
        request._messages = FallbackStorage(request)

        admin_instance = EventRegistrationAdmin(EventRegistration, AdminSite())
        admin_instance.confirm_registrations(request, queryset)

    def test_no_locked_query_selects_across_an_outer_join(self):
        from unittest.mock import patch

        from django.db.models import QuerySet

        from crush_lu.models import EventRegistration

        applicant = self._create_member("shape@example.com")
        self._register(applicant, self.curated)
        registration = EventRegistration.objects.get(event=self.curated, user=applicant)
        self.assertEqual(registration.status, "applied")

        locked_sql = []
        real_fetch_all = QuerySet._fetch_all

        def _record(self):
            if getattr(self.query, "select_for_update", False):
                locked_sql.append(str(self.query))
            return real_fetch_all(self)

        with patch.object(QuerySet, "_fetch_all", _record):
            self._run_confirm_action(
                EventRegistration.objects.filter(pk=registration.pk)
            )

        self.assertTrue(
            locked_sql, "the action took no lock at all — the guard is gone"
        )
        for sql in locked_sql:
            self.assertNotIn(
                "LEFT OUTER JOIN",
                sql,
                "FOR UPDATE across a nullable outer join raises "
                "NotSupportedError on PostgreSQL:\n%s" % sql,
            )


class ReturningPaidApplicantTests(CuratedRegistrationTestBase):
    """A member who already paid must not be asked to pay a second time.

    A late cancellation reuses the same registration row on re-application, and
    that row keeps ``payment_confirmed``. Classifying by the event fee alone
    would move them to Pending Payment: the UI shows payment due while checkout
    rejects them as already paid. ``_admitted_status`` exists to draw this line.
    """

    def _run_confirm_action(self, queryset):
        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from crush_lu.admin.events import EventRegistrationAdmin
        from crush_lu.models import EventRegistration

        request = RequestFactory().post("/")
        request.user = self.user
        request.session = self.client.session
        request._messages = FallbackStorage(request)

        admin_instance = EventRegistrationAdmin(EventRegistration, AdminSite())
        admin_instance.confirm_registrations(request, queryset)

    def _paid_curated_event(self):
        from decimal import Decimal

        self.curated.registration_fee = Decimal("15.00")
        self.curated.save(update_fields=["registration_fee"])

    def test_an_already_paid_applicant_is_confirmed_not_charged_again(self):
        from crush_lu.models import EventRegistration

        self._paid_curated_event()
        applicant = self._create_member("paidalready@example.com")
        self._register(applicant, self.curated)
        registration = EventRegistration.objects.get(event=self.curated, user=applicant)
        self.assertEqual(registration.status, "applied")
        # The money we still hold from the cancelled cycle.
        EventRegistration.objects.filter(pk=registration.pk).update(
            payment_confirmed=True
        )

        self._run_confirm_action(EventRegistration.objects.filter(pk=registration.pk))

        registration.refresh_from_db()
        self.assertEqual(registration.status, "confirmed")
        self.assertTrue(registration.payment_confirmed)

    def test_an_unpaid_applicant_on_the_same_event_still_goes_to_pending(self):
        """The rule must stay narrow: only an already-paid row skips payment."""
        from crush_lu.models import EventRegistration

        self._paid_curated_event()
        applicant = self._create_member("notpaid@example.com")
        self._register(applicant, self.curated)
        registration = EventRegistration.objects.get(event=self.curated, user=applicant)

        self._run_confirm_action(EventRegistration.objects.filter(pk=registration.pk))

        registration.refresh_from_db()
        self.assertEqual(registration.status, "pending")
        self.assertFalse(registration.payment_confirmed)


class ReservedSeatBannerTests(CuratedRegistrationTestBase):
    """Premium buys priority past the reserved block, not a place in a group
    the organiser composes by hand.

    ``event_full_for_user`` is False on a curated event by design — an
    application is never refused for capacity — so the reserved-seat banner
    would fire as soon as the organiser confirmed enough people to fill the
    public block, promising "A seat is reserved for you" to someone whose
    submit only creates an ``applied`` row the organiser may still turn down.
    """

    def _make_premium(self, user):
        from crush_lu.models import CrushCoach, CrushProfile, PremiumMembership

        coach_user = User.objects.create_user(
            username=f"coach_{user.username}",
            email=f"coach_{user.username}",
            password="testpass123",
        )
        coach = CrushCoach.objects.create(user=coach_user, is_active=True)
        # Both halves, as PremiumMembership.confirm() writes them.
        CrushProfile.objects.filter(user=user).update(assigned_coach=coach)
        PremiumMembership.objects.create(
            user=user, coach=coach, status="active", payment_confirmed=True
        )

    def _fill_the_public_block(self, event):
        """One confirmed seat against a public capacity of one."""
        from crush_lu.models import EventRegistration

        event.max_participants = 2
        event.reserved_premium_seats = 1
        event.save(update_fields=["max_participants", "reserved_premium_seats"])
        EventRegistration.objects.create(
            event=event,
            user=self._create_member(f"seated{event.pk}@example.com", gender="F"),
            status="confirmed",
        )

    def _premium_viewer(self, username):
        viewer = self._create_member(username)
        self._make_premium(viewer)
        self._login(viewer)
        return viewer

    def test_no_reserved_seat_promise_on_a_curated_event(self):
        self._fill_the_public_block(self.curated)
        self._premium_viewer("premcurated@example.com")

        response = self.client.get(f"/en/events/{self.curated.id}/")

        # The precondition the banner keys off is genuinely met...
        self.assertTrue(self.curated.is_full_for(is_premium=False))
        self.assertFalse(response.context["event_full_for_user"])
        # ...and the banner is still suppressed.
        self.assertFalse(response.context["premium_reserved_seat_available"])
        self.assertNotContains(response, "A seat is reserved for you")

    def test_a_direct_event_still_promises_the_reserved_seat(self):
        """The suppression must be scoped to curated mode and nothing else."""
        self._fill_the_public_block(self.direct)
        self._premium_viewer("premdirect@example.com")

        response = self.client.get(f"/en/events/{self.direct.id}/")

        self.assertTrue(response.context["premium_reserved_seat_available"])
        self.assertContains(response, "A seat is reserved for you")


class AppliedStatusIsRefusedOffCuratedEventsTests(CuratedRegistrationTestBase):
    """The admin status dropdown offers "Applied" on every registration.

    ``applied`` sits outside ``SEAT_HOLDING_STATUSES``, so choosing it releases
    the seat, voids the door ticket and drops the member from reminders and the
    wallet pass — silently, with no email. On a curated event that is the
    mechanism. On a direct-mode event, which is every mixer and every speed
    dating running the ordinary flow, it is one misclick away from a member
    losing a place they paid for.
    """

    def _registration(self, event, status="confirmed", username=None):
        from crush_lu.models import EventRegistration

        return EventRegistration.objects.create(
            event=event,
            user=self._create_member(username or f"holder{event.pk}@example.com"),
            status=status,
        )

    def _admin_form(self, registration, status):
        """The form the admin change page builds, over its own fieldset."""
        from django.forms.models import modelform_factory

        from crush_lu.models import EventRegistration

        form_class = modelform_factory(
            EventRegistration, fields=["event", "user", "status"]
        )
        return form_class(
            data={
                "event": registration.event_id,
                "user": registration.user_id,
                "status": status,
            },
            instance=registration,
        )

    def _status_errors(self, registration):
        """Field errors from model validation, as a dict — never an exception.

        Asserting on the "status" key rather than on ValidationError being
        raised at all keeps these tests honest: an unrelated required field
        would otherwise make a broken guard look like a passing test.
        """
        from django.core.exceptions import ValidationError

        try:
            registration.full_clean()
        except ValidationError as exc:
            return exc.error_dict if hasattr(exc, "error_dict") else {}
        return {}

    def test_applied_is_refused_on_a_direct_speed_dating_event(self):
        registration = self._registration(self.direct)
        form = self._admin_form(registration, "applied")

        self.assertFalse(form.is_valid())
        self.assertIn("status", form.errors)

    def test_applied_is_refused_on_a_mixer(self):
        """`uses_curated_registration` checks the type as well as the mode, so a
        mixer flipped to curated is still direct — and still guarded."""
        self.mixer.registration_mode = "curated"
        self.mixer.save(update_fields=["registration_mode"])
        registration = self._registration(self.mixer)

        form = self._admin_form(registration, "applied")

        self.assertFalse(form.is_valid())
        self.assertIn("status", form.errors)

    def test_applied_is_accepted_on_a_curated_event(self):
        """The guard must not obstruct the workflow it exists to protect."""
        registration = self._registration(self.curated)

        form = self._admin_form(registration, "applied")

        self.assertTrue(form.is_valid(), form.errors.as_text())

    def test_an_ordinary_status_change_on_a_direct_event_still_works(self):
        """Scoped to `applied`: every status an organiser sets today is
        untouched on every event that has not opted in."""
        registration = self._registration(self.direct)

        for status in ("pending", "confirmed", "waitlist", "cancelled", "attended"):
            with self.subTest(status=status):
                form = self._admin_form(registration, status)
                self.assertTrue(form.is_valid(), form.errors.as_text())

    def test_the_guard_lives_on_the_model_so_the_inline_is_covered_too(self):
        """MeetupEventAdmin's inline edits status through its own form."""
        registration = self._registration(self.direct)
        registration.status = "applied"

        self.assertIn("status", self._status_errors(registration))

    def test_a_curated_application_passes_model_validation(self):
        registration = self._registration(self.curated, status="applied")

        self.assertNotIn("status", self._status_errors(registration))
