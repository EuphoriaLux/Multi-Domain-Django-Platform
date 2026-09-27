"""
UX Wave 3 · WP7 — event list findings 4-07, 4-08, 1-05, 1-13, 4-17.

Literal paths + HTTP_HOST='crush.lu' per AGENTS.md (reverse() resolves
against the wrong urlconf under the test client's default host).
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, Client
from django.utils import timezone
from django.utils.translation import activate, deactivate

from crush_lu.models import CrushProfile, MeetupEvent, EventRegistration
from crush_lu.models.profiles import UserDataConsent
from crush_lu.templatetags.crush_price import price
from crush_lu.views_events import _light_eligibility

User = get_user_model()


def _make_event(**overrides):
    defaults = dict(
        title="Test Speed Dating",
        description="A test event",
        event_type="speed_dating",
        date_time=timezone.now() + timedelta(days=7),
        registration_deadline=timezone.now() + timedelta(days=6),
        location="Luxembourg City",
        max_participants=20,
        min_age=18,
        max_age=99,
        registration_fee=Decimal("25.00"),
        is_published=True,
    )
    defaults.update(overrides)
    return MeetupEvent.objects.create(**defaults)


def _grant_consent(user):
    # consent_middleware is scoped to urls_crush and 302s without this.
    UserDataConsent.objects.update_or_create(
        user=user,
        defaults={"crushlu_consent_given": True},
    )


class HomeUpcomingEventsTests(TestCase):
    """Finding 1-05: home() must not show already-live events to anonymous
    visitors, and must exclude seeded QA events."""

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")

    def test_live_event_excluded_from_anonymous_home(self):
        # Started 10 minutes ago, still within its duration -> "live".
        _make_event(
            title="Live Right Now",
            date_time=timezone.now() - timedelta(minutes=10),
            duration_minutes=60,
        )
        _make_event(title="Future Mixer", event_type="mixer")

        response = self.client.get("/en/")

        titles = [e.title for e in response.context["upcoming_events"]]
        self.assertNotIn("Live Right Now", titles)
        self.assertIn("Future Mixer", titles)

    def test_debug_seed_event_excluded_from_anonymous_home(self):
        # Real seed_crush_cache.py title: the "[DEBUG]" tag is preceded by an
        # emoji, so a naive `istartswith("[DEBUG]")` check would miss it.
        _make_event(title="🧭 [DEBUG] Luxembourg City Crush Cache")
        _make_event(title="Real Public Event")

        response = self.client.get("/en/")

        titles = [e.title for e in response.context["upcoming_events"]]
        self.assertNotIn("🧭 [DEBUG] Luxembourg City Crush Cache", titles)
        self.assertIn("Real Public Event", titles)

    def test_debug_seed_event_excluded_from_anonymous_home_on_locale_path(self):
        # Round-2 finding: modeltranslation rewrites a bare `title__icontains`
        # exclude to `title_de__icontains` on /de/, and seed_crush_cache.py
        # only ever populates the English column -- so title_de is NULL and
        # the naive filter stopped excluding the debug event on that path.
        _make_event(title="🧭 [DEBUG] Luxembourg City Crush Cache")
        _make_event(title="Real Public Event")

        response = self.client.get("/de/")

        titles = [e.title for e in response.context["upcoming_events"]]
        self.assertNotIn("🧭 [DEBUG] Luxembourg City Crush Cache", titles)
        self.assertIn("Real Public Event", titles)


class EventCardAgesAndSpotsTests(TestCase):
    """Findings 1-05 / 4-07: hide the placeholder ages row and only surface
    seat counts once they are low, on the shared event_card partial."""

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")

    def test_placeholder_ages_hidden_on_card(self):
        _make_event(title="Ages Placeholder Event", min_age=18, max_age=99)
        response = self.client.get("/en/events/")
        self.assertNotContains(response, "Ages 18-99")

    def test_real_age_range_still_shown_on_card(self):
        _make_event(title="Narrow Age Event", min_age=25, max_age=40)
        response = self.client.get("/en/events/")
        self.assertContains(response, "25-40")

    def test_high_seat_count_not_shown(self):
        _make_event(title="Plenty Of Room", max_participants=300)
        response = self.client.get("/en/events/")
        self.assertNotContains(response, "spots left")

    def test_low_seat_count_shown_with_urgency_copy(self):
        _make_event(title="Almost Full", max_participants=5)
        # 5 - 0 confirmed = 5 remaining, under the <=10 threshold.
        response = self.client.get("/en/events/")
        self.assertContains(response, "Only 5 spots left")

    def test_upcoming_card_date_keeps_the_year(self):
        # Codex review finding: compacting the date to "j M" dropped the
        # year, so an event scheduled across a year boundary read as an
        # ambiguous "Mon, 5 Jan" with no way to tell it isn't this year.
        _make_event(
            title="New Year Mixer",
            event_type="mixer",
            date_time=timezone.make_aware(timezone.datetime(2027, 1, 5, 19, 0)),
        )
        response = self.client.get("/en/events/")
        self.assertContains(response, "5 Jan 2027")


class EventListRegistrationStatusTests(TestCase):
    """Finding 4-08: the list view builds a per-user registration-status map
    without N+1 queries, and the card surfaces a status chip + CTA label."""

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")
        self.user = User.objects.create_user(
            username="member@example.com",
            email="member@example.com",
            password="testpass123",
        )
        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 5, 15),
            gender="M",
            location="Luxembourg",
            verification_status="verified",
        )
        self.event = _make_event(title="My Confirmed Event")
        _grant_consent(self.user)

    def test_confirmed_registration_shows_chip_and_view_ticket_cta(self):
        EventRegistration.objects.create(
            event=self.event, user=self.user, status="confirmed"
        )
        self.client.login(username="member@example.com", password="testpass123")

        response = self.client.get("/en/events/")

        self.assertEqual(
            response.context["event_registration_status"][self.event.id], "confirmed"
        )
        self.assertContains(response, "Confirmed")
        self.assertContains(response, "View ticket")

    def test_confirmed_registration_view_ticket_links_to_ticket_page(self):
        # Finding WP7-5: the "View ticket" CTA must actually land on the
        # ticket page, not on event_detail (which only surfaces the ticket
        # link indirectly, further down its own page).
        EventRegistration.objects.create(
            event=self.event, user=self.user, status="confirmed"
        )
        self.client.login(username="member@example.com", password="testpass123")

        response = self.client.get("/en/events/")

        self.assertContains(response, f"/en/events/{self.event.id}/ticket/")

    def test_pending_payment_shows_complete_payment_cta(self):
        EventRegistration.objects.create(
            event=self.event, user=self.user, status="pending"
        )
        self.client.login(username="member@example.com", password="testpass123")

        response = self.client.get("/en/events/")

        self.assertContains(response, "Complete payment")

    def test_attended_registration_keeps_ticket_badge_and_cta(self):
        # Codex review finding: a member checked in at the door (status
        # flips to "attended") stays on upcoming_event_list until the event
        # ends, and event_ticket already accepts "attended" — so the card
        # must keep the success chip + "View ticket" CTA, not fall back to
        # "View Details".
        EventRegistration.objects.create(
            event=self.event, user=self.user, status="attended"
        )
        self.client.login(username="member@example.com", password="testpass123")

        response = self.client.get("/en/events/")

        self.assertEqual(
            response.context["event_registration_status"][self.event.id], "attended"
        )
        self.assertContains(response, "Attended")
        self.assertContains(response, "View ticket")
        self.assertContains(response, f"/en/events/{self.event.id}/ticket/")

    def test_attended_registration_suppresses_eligibility_warning(self):
        # Codex review finding: a coach can reject a confirmed attendee at
        # the door without cancelling the EventRegistration, so an event
        # requiring a profile the member no longer satisfies must not show
        # a contradictory "Verified members only" chip next to "Attended".
        # Uses a fresh unverified user rather than self.user (whose profile
        # already satisfies "approved") so _light_eligibility() genuinely
        # returns False here.
        gated_event = _make_event(
            title="Gated Attended Event", profile_requirement="approved"
        )
        unverified_user = User.objects.create_user(
            username="unverified@example.com",
            email="unverified@example.com",
            password="testpass123",
        )
        _grant_consent(unverified_user)
        EventRegistration.objects.create(
            event=gated_event, user=unverified_user, status="attended"
        )
        self.client.login(username="unverified@example.com", password="testpass123")

        response = self.client.get("/en/events/")

        self.assertFalse(response.context["event_eligibility"][gated_event.id])
        self.assertContains(response, "Attended")
        self.assertNotContains(response, "Verified members only")

    def test_no_registration_query_count_scales_flat_with_event_count(self):
        # Regression guard for the N+1 the finding calls out: adding more
        # events must not add more registration-status queries on the real
        # event_list() view. This exercises /en/events/ itself (not a
        # hand-reconstructed queryset) so it actually fails on origin/main,
        # where event_list() builds no registration-status map at all and a
        # per-event N+1 would go undetected by a synthetic query.
        EventRegistration.objects.create(
            event=self.event, user=self.user, status="confirmed"
        )
        self.client.login(username="member@example.com", password="testpass123")

        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        # Warm up first: the request path lazily creates several singleton
        # rows on first sight of this host/user (Site, CrushSiteConfig,
        # DailyUserActivity, UserActivity, ...). Those one-time INSERTs
        # would otherwise pad the "baseline" call and be silently absent
        # from the second, masking a real per-event N+1 in the diff.
        self.client.get("/en/events/")

        with CaptureQueriesContext(connection) as baseline:
            self.client.get("/en/events/")
        baseline_count = len(baseline.captured_queries)

        for i in range(5):
            _make_event(title=f"Extra Event {i}")

        with CaptureQueriesContext(connection) as with_more_events:
            self.client.get("/en/events/")

        self.assertEqual(
            len(with_more_events.captured_queries),
            baseline_count,
            "query count grew with more events — a registration-status "
            "(or eligibility) lookup is running per event instead of once",
        )


class EventCardEligibilityChipLabelTests(TestCase):
    """Finding WP7-1: the ineligibility chip must name the ACTUAL gate
    (from PROFILE_REQUIREMENT_CHOICES) instead of a fixed "Verified members
    only" string that was wrong for 4 of the 5 profile_requirement values."""

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")
        self.user = User.objects.create_user(
            username="incomplete@example.com",
            email="incomplete@example.com",
            password="testpass123",
        )
        _grant_consent(self.user)

    def test_completed_requirement_shows_its_own_label_not_verified_only(self):
        # profile_requirement="completed" (the model default) is about a
        # finished, phone-verified profile — NOT about verification — so an
        # incomplete-profile member must not be told "Verified members only".
        _make_event(title="Completed Profile Event", profile_requirement="completed")
        self.client.login(username="incomplete@example.com", password="testpass123")

        response = self.client.get("/en/events/")

        self.assertContains(response, "Participation-ready Crush profile")
        self.assertNotContains(response, "Verified members only")

    def test_approved_requirement_still_shows_verified_only(self):
        # The one case where "Verified members only" is actually correct
        # copy (it is PROFILE_REQUIREMENT_CHOICES' own label for "approved").
        _make_event(title="Approved Only Event", profile_requirement="approved")
        self.client.login(username="incomplete@example.com", password="testpass123")

        response = self.client.get("/en/events/")

        self.assertContains(response, "Verified members only")


    def test_private_approved_event_names_profile_gate_not_verification(self):
        event = _make_event(
            title="Private Approved Event",
            is_private_invitation=True,
            profile_requirement="approved",
        )
        event.invited_users.add(self.user)
        self.client.login(username="incomplete@example.com", password="testpass123")

        response = self.client.get("/en/events/")

        self.assertContains(response, "This event requires a Crush profile.")
        self.assertNotContains(response, "Verified members only")

    def test_private_none_event_does_not_claim_no_profile_required(self):
        event = _make_event(
            title="Private Open Event",
            is_private_invitation=True,
            profile_requirement="none",
        )
        event.invited_users.add(self.user)
        self.client.login(username="incomplete@example.com", password="testpass123")

        response = self.client.get("/en/events/")

        self.assertContains(response, "This event requires a Crush profile.")
        self.assertNotContains(response, "No profile required")


class EventTypeFilterLabelTests(TestCase):
    """Codex review finding: the event-type filter chips must be
    translated, not the model's raw EVENT_TYPE_CHOICES strings."""

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")
        _make_event(title="A Speed Dating Night", event_type="speed_dating")
        _make_event(title="A Social Mixer", event_type="mixer")

    def test_german_page_shows_translated_filter_labels(self):
        # Meta/SEO copy elsewhere on the page mentions "Social Mixer" in
        # English on purpose, so this checks the filter chip's own
        # translated label is present rather than asserting the raw
        # English string is absent from the whole page.
        response = self.client.get("/de/events/")
        self.assertContains(response, "Speed-Dating")
        self.assertContains(response, "Mixer-Abend")
        self.assertContains(response, 'data-filter-type="mixer"')

    def test_french_page_shows_translated_filter_labels(self):
        response = self.client.get("/fr/events/")
        self.assertContains(response, "Rencontre conviviale")
        self.assertContains(response, 'data-filter-type="mixer"')


class PastTabHidesEventTypeFilterBarTests(TestCase):
    """Codex review finding: the event-type filter bar only affects
    upcoming cards (eventTypeFilterCard), so it must not stay visible
    (and silently do nothing) on the past-events tab."""

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")
        _make_event(title="An Upcoming Mixer", event_type="mixer")
        _make_event(title="An Upcoming Speed Date", event_type="speed_dating")

    def test_filter_bar_is_scoped_to_the_upcoming_panel(self):
        response = self.client.get("/en/events/")
        content = response.content.decode()
        filter_bar_start = content.index('aria-label="Filter by event type"')
        upcoming_panel_start = content.index('id="events-panel-upcoming"')
        past_panel_start = content.index('id="events-panel-past"')
        # The filter bar must render before the past-events panel opens
        # (i.e. inside/around the upcoming panel, x-show-gated on isUpcoming)
        # rather than sitting outside both tab panels where it stays visible
        # regardless of which tab is active.
        self.assertLess(filter_bar_start, past_panel_start)
        self.assertIn('x-show="isUpcoming"', content[:past_panel_start])
        self.assertGreater(filter_bar_start, 0)
        self.assertGreaterEqual(upcoming_panel_start, 0)


class LightEligibilityTests(TestCase):
    """Finding 4-08: the "Verified members only" chip logic."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="u@example.com", email="u@example.com", password="x"
        )

    def test_no_requirement_is_always_eligible(self):
        event = _make_event(profile_requirement="none")
        self.assertTrue(_light_eligibility(event, None))

    def test_approved_requirement_needs_verified_profile(self):
        event = _make_event(profile_requirement="approved")
        profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 5, 15),
            gender="M",
            location="Luxembourg",
            verification_status="pending",
        )
        self.assertFalse(_light_eligibility(event, profile))
        profile.verification_status = "verified"
        profile.save(update_fields=["verification_status"])
        self.assertTrue(_light_eligibility(event, profile))

    def test_approved_requirement_no_profile_is_ineligible(self):
        event = _make_event(profile_requirement="approved")
        self.assertFalse(_light_eligibility(event, None))

    def test_private_invitation_eligible_with_any_profile(self):
        # Gated by invitation, not profile_requirement -- event_register
        # never checks verification_status for a private-invitation event,
        # only that a CrushProfile exists at all.
        event = _make_event(is_private_invitation=True, profile_requirement="approved")
        profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 5, 15),
            gender="M",
            location="Luxembourg",
            verification_status="pending",
        )
        self.assertTrue(_light_eligibility(event, profile))

    def test_private_invitation_no_profile_is_ineligible(self):
        # Round-2 finding: event_register's private-invitation branch
        # redirects every invitee without a CrushProfile to create_profile,
        # so the display chip must flag that instead of unconditionally
        # clearing it (it previously returned True regardless of profile).
        event = _make_event(is_private_invitation=True, profile_requirement="approved")
        self.assertFalse(_light_eligibility(event, None))


class EventListPageStructureTests(TestCase):
    """Finding 1-13: H1, anonymous prospect banner, tab a11y."""

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")

    def test_anonymous_visitor_sees_h1_and_prospect_banner(self):
        response = self.client.get("/en/events/")
        self.assertContains(response, "<h1")
        self.assertContains(
            response,
            "Create a free account to register for events.",
        )
        self.assertContains(response, 'role="tablist"')
        self.assertContains(response, 'role="tab"')

    def test_anonymous_banner_does_not_claim_universal_verification(self):
        # Codex review finding: the banner must not claim every event
        # requires a verified profile — profile_requirement="none" events
        # need no profile at all, so that would be an invented rule.
        response = self.client.get("/en/events/")
        self.assertNotContains(response, "get verified to register")
        self.assertNotContains(response, "verified profile")

    def test_authenticated_visitor_does_not_see_prospect_banner(self):
        user = User.objects.create_user(
            username="member2@example.com",
            email="member2@example.com",
            password="testpass123",
        )
        _grant_consent(user)
        self.client.login(username="member2@example.com", password="testpass123")
        response = self.client.get("/en/events/")
        self.assertNotContains(
            response,
            "Create a free account to register for events.",
        )


class PriceFilterTests(TestCase):
    """Finding 4-17: one locale-aware price filter."""

    def tearDown(self):
        deactivate()

    def test_english_whole_euro_prefix(self):
        activate("en")
        self.assertEqual(price(Decimal("25.00")), "€25")

    def test_german_whole_euro_suffix(self):
        activate("de")
        self.assertEqual(price(Decimal("25.00")), "25 €")

    def test_french_cents_use_comma(self):
        activate("fr")
        self.assertEqual(price(Decimal("25.50")), "25,50 €")

    def test_event_card_uses_price_filter_not_hardcoded_prefix(self):
        cache.clear()
        client = Client(HTTP_HOST="crush.lu")
        _make_event(title="Priced Event", registration_fee=Decimal("30.00"))
        response = client.get("/en/events/")
        self.assertContains(response, "€30")
