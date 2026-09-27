"""UX Wave 3 WP6 — event detail (findings 4-03, 4-12, 4-14, 4-18).

Paths are literal (`/en/events/<id>/`) because `reverse("crush_lu:...")`
resolves against the default urlconf, not the one `HTTP_HOST=crush.lu`
selects (see AGENTS.md).

Run with: pytest crush_lu/tests/test_ux_wave3_event_detail.py
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.test import Client, TestCase
from django.utils import timezone

from crush_lu.models import CrushProfile, MeetupEvent, UserDataConsent

User = get_user_model()


class EventDetailWave3TestBase(TestCase):
    """Shared fixtures: an upcoming event and a helper to fetch its page."""

    def setUp(self):
        # SQLite doesn't roll back the cache between tests and every test's
        # viewer is user 2 sharing one @ratelimit counter — see
        # AGENTS.md "SQLite rolls back PK sequences but not the cache".
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")

    def _make_event(self, **kwargs):
        defaults = dict(
            title="Wine Tasting & Speed Dating – Moselle Edition",
            description=(
                "Join us for a wonderful evening of wine tasting paired with "
                "speed dating. We'll explore some of the finest wines the "
                "Moselle valley has to offer, guided by a local sommelier, "
                "while you meet new people in a relaxed, low-pressure "
                "setting. Snacks are provided, and the format leaves plenty "
                "of time for longer conversations between rounds."
            ),
            event_type="mixer",
            date_time=timezone.now() + timedelta(days=7),
            location="Luxembourg City",
            address="1 Rue de la Gare, Luxembourg",
            max_participants=20,
            registration_deadline=timezone.now() + timedelta(days=5),
            registration_fee=0,
            is_published=True,
            profile_requirement="none",
        )
        defaults.update(kwargs)
        return MeetupEvent.objects.create(**defaults)

    def _create_user(self, username):
        user = User.objects.create_user(
            username=username,
            email=username,
            password="testpass123",
            first_name=username.split("@")[0],
        )
        UserDataConsent.objects.update_or_create(
            user=user, defaults={"crushlu_consent_given": True}
        )
        return user

    def _detail_url(self, event):
        return f"/en/events/{event.id}/"

    def _get_detail(self, event):
        response = self.client.get(self._detail_url(event))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()


class NoImageHeroTitleTests(EventDetailWave3TestBase):
    """#4-12: the no-image hero shows the full title, not a clamped one."""

    def test_hero_h2_no_longer_line_clamped(self):
        event = self._make_event()
        html = self._get_detail(event)
        self.assertNotIn("line-clamp-2", html)

    def test_full_title_renders_as_an_h1(self):
        event = self._make_event()
        html = self._get_detail(event)
        self.assertIn('<h1 class="text-white font-bold', html)
        self.assertIn(event.title, html)


class FactStripAndDescriptionTests(EventDetailWave3TestBase):
    """#4-03: a fact strip precedes the (collapsible) description."""

    def test_fact_strip_appears_before_description(self):
        event = self._make_event(registration_fee=15)
        html = self._get_detail(event)
        facts_index = html.index("Ages 18")  # part of the quick-facts strip
        description_index = html.index("eventDescriptionToggle")
        self.assertLess(
            facts_index,
            description_index,
            "the quick-facts strip must render before the description block",
        )
        self.assertIn("€15", html)

    def test_long_description_is_collapsible(self):
        event = self._make_event()
        html = self._get_detail(event)
        self.assertIn("eventDescriptionToggle", html)
        self.assertIn(':class="descriptionClass"', html)
        self.assertIn("Read more", html)

    def test_short_description_has_no_read_more_toggle(self):
        event = self._make_event(description="A short blurb.")
        html = self._get_detail(event)
        self.assertNotIn("Read more", html)

    def test_cta_panel_and_sticky_bar_markers_present(self):
        event = self._make_event()
        html = self._get_detail(event)
        self.assertIn('id="event-cta-panel"', html)
        self.assertIn('id="event-sticky-cta"', html)
        self.assertIn("eventStickyCta", html)

    def test_read_more_toggle_has_aria_expanded_and_controls(self):
        """WP6-2: the toggle must expose its expand/collapse state and the
        region it controls, not just visually swap the label."""
        event = self._make_event()
        html = self._get_detail(event)
        self.assertIn('id="event-description-text"', html)
        self.assertIn('aria-controls="event-description-text"', html)
        self.assertIn('x-bind:aria-expanded="expandedAria"', html)


class StickyCtaToastOffsetTests(EventDetailWave3TestBase):
    """WP6-1: the sticky CTA bar must not permanently obscure toasts.

    Behaviour itself (offsetting #toast-container while the bar is visible,
    and never doing so when its own height is 0 on desktop) is exercised in
    the browser by test_ux_wave3_event_detail_playwright.py; this asserts
    the JS actually wires up the watcher it depends on.
    """

    def test_alpine_component_watches_visible_and_offsets_toast_container(self):
        js_path = finders.find("crush_lu/js/alpine-components.js")
        self.assertIsNotNone(
            js_path, "alpine-components.js not found via staticfiles finders"
        )
        with open(js_path, encoding="utf-8") as fh:
            js = fh.read()
        start = js.index('Alpine.data("eventStickyCta"')
        end = js.index("Alpine.data(", start + 1)
        component_src = js[start:end]
        self.assertIn('this.$watch("visible"', component_src)
        self.assertIn('getElementById("toast-container")', component_src)
        self.assertIn('removeProperty("bottom")', component_src)
        # Must guard offsetHeight == 0 (bar is md:hidden, so IntersectionObserver
        # can flip `visible` true on desktop while the bar itself is display:none).
        self.assertIn("barHeight > 0", component_src)


class VerificationDeadEndLinksTests(EventDetailWave3TestBase):
    """#4-14: dead-end verification boxes now offer a next step."""

    def _profile(self, user, **kwargs):
        defaults = dict(
            date_of_birth=date(1995, 1, 1),
            gender="F",
            location="Luxembourg",
            is_approved=False,
            verification_status="pending",
        )
        defaults.update(kwargs)
        return CrushProfile.objects.create(user=user, **defaults)

    def test_approved_gate_not_yet_verified_offers_entry_events_link(self):
        event = self._make_event(profile_requirement="approved")
        user = self._create_user("pending1@test.com")
        self._profile(user)
        self.client.force_login(user)

        html = self._get_detail(event)
        self.assertIn("Get verified at an entry event or with LuxID first.", html)
        self.assertIn("See entry events", html)
        self.assertIn('href="/en/events/"', html)

    def test_coach_required_offers_entry_events_link(self):
        event = self._make_event(profile_requirement="coach_assigned")
        user = self._create_user("nocoach@test.com")
        self._profile(user, is_approved=True, verification_status="verified")
        self.client.force_login(user)

        html = self._get_detail(event)
        self.assertIn("Coach required", html)
        self.assertIn("See entry events", html)

    def test_rejected_profile_offers_contact_support_link(self):
        event = self._make_event(profile_requirement="coach_assigned")
        user = self._create_user("rejected@test.com")
        from crush_lu.models.profiles import CrushCoach

        coach_user = self._create_user("coachx@test.com")
        coach = CrushCoach.objects.create(user=coach_user)
        self._profile(
            user,
            is_approved=False,
            verification_status="rejected",
            assigned_coach=coach,
        )
        self.client.force_login(user)

        html = self._get_detail(event)
        self.assertIn("Please contact support.", html)
        self.assertIn('href="mailto:support@crush.lu"', html)
        self.assertIn("Contact support", html)


class ShareButtonFallbackTests(EventDetailWave3TestBase):
    """#4-18: Share stays visible and falls back to copy-link."""

    def test_share_button_is_not_unconditionally_hidden(self):
        event = self._make_event()
        html = self._get_detail(event)
        self.assertNotIn("shareBtn.style.display = 'none';", html)

    def test_share_falls_back_to_clipboard_with_toast(self):
        event = self._make_event()
        html = self._get_detail(event)
        self.assertIn("navigator.clipboard.writeText", html)
        self.assertIn("Link copied", html)
