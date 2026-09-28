"""UX Wave 4 · WP11 "partials": findings 3-10, 5-06, 4-15, 5-08, 5-09 and #1082.

- 3-10: a pre-screening auto-save against a locked submission returns a
  read-only "answers locked" section (200) instead of an empty 410.
- 5-06: the dashboard has a real <h1> and no skipped heading levels; so does
  My Connections.
- 4-15: My Events' image link is a hidden duplicate of the title link;
  event_detail availability text uses the AA-passing 700 shades.
- 5-08: received-request cards are no longer live regions; one region per
  page is filled out of band; Accept/Decline use the canonical buttons.
- 5-09: Decline asks through the branded confirm sheet with a "Decline" label.
- #1082: "See entry events" links to ?entry=1, which keeps only
  profile_requirement="completed" events and badges them.

Literal paths with HTTP_HOST=crush.lu (never reverse(); AGENTS.md), and
cache.clear() in every setUp.
"""

from datetime import date, timedelta
from html.parser import HTMLParser

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import Client, TestCase, override_settings
from django.utils import timezone, translation

from crush_lu.models import (
    CrushCoach,
    CrushProfile,
    EventConnection,
    EventRegistration,
    MeetupEvent,
    ProfileSubmission,
    UserDataConsent,
)

User = get_user_model()


class _Tags(HTMLParser):
    """Collects (tag, attrs, text) for every element, in document order."""

    HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__()
        self.elements = []
        self._open_headings = []
        self._in_main = False

    def handle_starttag(self, tag, attrs):
        if tag == "main":
            self._in_main = True
        entry = {"tag": tag, "attrs": dict(attrs), "text": "", "main": self._in_main}
        self.elements.append(entry)
        if tag in self.HEADINGS:
            self._open_headings.append(entry)

    def handle_endtag(self, tag):
        if tag == "main":
            self._in_main = False
        if tag in self.HEADINGS and self._open_headings:
            self._open_headings.pop()

    def handle_data(self, data):
        for heading in self._open_headings:
            heading["text"] += data

    @classmethod
    def parse(cls, html):
        parser = cls()
        parser.feed(html)
        return parser.elements

    @classmethod
    def headings(cls, html):
        """Page-content headings (inside <main>): the shared chrome's drawer
        and prompts are outside this WP."""
        return [
            (int(e["tag"][1]), " ".join(e["text"].split()))
            for e in cls.parse(html)
            if e["tag"] in cls.HEADINGS and e["main"]
        ]


def _outline_skips(levels):
    """Heading levels that jump more than one step deeper than the previous."""
    skips = []
    previous = 0
    for level in levels:
        if level > previous + 1:
            skips.append((previous, level))
        previous = level
    return skips


def _member(username, *, verified=True, gender="F"):
    user = User.objects.create_user(
        username=username, email=username, password="testpass123", first_name="Mem"
    )
    CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1995, 5, 15),
        gender=gender,
        location="canton-luxembourg",
        is_approved=verified,
        is_active=True,
    )
    UserDataConsent.objects.filter(user=user).update(crushlu_consent_given=True)
    EmailAddress.objects.update_or_create(
        user=user, email=user.email, defaults={"verified": True, "primary": True}
    )
    return user


def _event(title="Test event", *, days=10, **kwargs):
    when = timezone.now() + timedelta(days=days)
    defaults = dict(
        title=title,
        description="x",
        event_type="mixer",
        date_time=when,
        location="Luxembourg",
        address="1 Test St",
        max_participants=40,
        duration_minutes=120,
        registration_deadline=when - timedelta(days=2),
        is_published=True,
    )
    defaults.update(kwargs)
    return MeetupEvent.objects.create(**defaults)


# --------------------------------------------------------------------------
# 3-10 — pre-screening locked section
# --------------------------------------------------------------------------


@override_settings(PRE_SCREENING_ENABLED=True)
class PreScreeningLockedSectionTests(TestCase):
    def setUp(self):
        cache.clear()
        coach_user = _member("ps-coach@example.com")
        coach = CrushCoach.objects.create(user=coach_user, is_active=True)
        self.user = _member("ps-user@example.com", verified=False)
        self.submission = ProfileSubmission.objects.create(
            profile=self.user.crushprofile, coach=coach, status="pending"
        )
        self.client = Client(HTTP_HOST="crush.lu")
        self.client.force_login(self.user)

    def _save(self):
        return self.client.post(
            "/en/pre-screening/section/logistics/",
            {"source": "friend"},
            HTTP_HX_REQUEST="true",
        )

    def _assert_locked(self, response):
        # 200 so htmx swaps it: htmx 2 swaps nothing on a 4xx.
        self.assertEqual(response.status_code, 200)
        elements = _Tags.parse(response.content.decode())
        sections = [e for e in elements if e["tag"] == "section"]
        self.assertEqual(len(sections), 1)
        self.assertEqual(
            sections[0]["attrs"].get("id"), "prescreening-section-logistics"
        )
        # Read-only: nothing left to auto-save.
        self.assertEqual([e for e in elements if e["tag"] in ("form", "input")], [])
        self.assertContains(response, "Answers locked")

    def test_call_completed_returns_locked_partial(self):
        self.submission.review_call_completed = True
        self.submission.save()

        response = self._save()

        self._assert_locked(response)
        self.assertContains(
            response, "Your Coach has already completed your screening call."
        )
        self.submission.refresh_from_db()
        self.assertNotIn("source", self.submission.pre_screening_responses or {})

    def test_reviewed_submission_returns_locked_partial(self):
        self.submission.status = "approved"
        self.submission.save()

        response = self._save()

        self._assert_locked(response)
        self.assertContains(
            response, "Pre-screening is no longer available for this submission."
        )

    def test_editable_submission_still_saves(self):
        response = self._save()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<form", html=False)
        self.assertNotContains(response, "Answers locked")


# --------------------------------------------------------------------------
# 5-06 / 5-08 / 5-09 — dashboard, My Connections, received card
# --------------------------------------------------------------------------


class _ConnectionFixtureMixin:
    def _pending_request(self):
        requester = _member("requester@example.com", gender="M")
        event = _event("Past night", days=-2)
        for user in (requester, self.user):
            EventRegistration.objects.create(event=event, user=user, status="attended")
        return EventConnection.objects.create(
            requester=requester, recipient=self.user, event=event, status="pending"
        )


class DashboardHeadingOutlineTests(_ConnectionFixtureMixin, TestCase):
    def setUp(self):
        cache.clear()
        self.user = _member("dash-heading@example.com")
        self.client = Client(HTTP_HOST="crush.lu")
        self.client.force_login(self.user)

    def test_greeting_is_the_single_h1_and_outline_has_no_skips(self):
        self._pending_request()
        html = self.client.get("/en/dashboard/").content.decode()

        headings = _Tags.headings(html)
        levels = [level for level, _ in headings]
        self.assertEqual(levels.count(1), 1, headings)
        self.assertEqual(levels[0], 1, headings)
        self.assertEqual(_outline_skips(levels), [], headings)
        self.assertNotIn(5, levels, headings)
        self.assertNotIn(6, levels, headings)
        # The received-request card sits directly under the h1 here.
        self.assertIn((2, "Mem wants to connect"), headings)

    def test_one_status_region_not_one_per_card(self):
        self._pending_request()
        html = self.client.get("/en/dashboard/").content.decode()
        live = [e for e in _Tags.parse(html) if e["attrs"].get("aria-live")]
        region = [e for e in live if e["attrs"].get("id") == "connection-status-live"]
        self.assertEqual(len(region), 1)
        card_live = [
            e
            for e in live
            if (e["attrs"].get("id") or "").startswith("connection-")
            and e["attrs"].get("id") != "connection-status-live"
        ]
        self.assertEqual(card_live, [])


class MyConnectionsReceivedCardTests(_ConnectionFixtureMixin, TestCase):
    def setUp(self):
        cache.clear()
        self.user = _member("myconn@example.com")
        self.client = Client(HTTP_HOST="crush.lu")
        self.client.force_login(self.user)
        self.connection = self._pending_request()

    def _page(self):
        response = self.client.get("/en/connections/")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_heading_outline_has_no_skips(self):
        headings = _Tags.headings(self._page())
        levels = [level for level, _ in headings]
        self.assertEqual(levels[0], 1, headings)
        self.assertEqual(_outline_skips(levels), [], headings)
        self.assertIn((3, "Mem wants to connect"), headings)

    def test_card_is_not_a_live_region_and_page_has_one(self):
        elements = _Tags.parse(self._page())
        card = next(
            e
            for e in elements
            if e["attrs"].get("id") == f"connection-{self.connection.id}"
        )
        self.assertNotIn("aria-live", card["attrs"])
        self.assertNotIn("role", card["attrs"])
        regions = [
            e for e in elements if e["attrs"].get("id") == "connection-status-live"
        ]
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0]["attrs"].get("role"), "status")
        self.assertEqual(regions[0]["attrs"].get("aria-live"), "polite")

    def test_accept_and_decline_use_canonical_buttons_and_confirm_sheet(self):
        elements = _Tags.parse(self._page())
        accept = next(
            e
            for e in elements
            if e["tag"] == "button"
            and e["attrs"].get("hx-post", "").endswith(f"/{self.connection.id}/accept/")
        )
        decline = next(
            e
            for e in elements
            if e["tag"] == "button"
            and e["attrs"]
            .get("hx-post", "")
            .endswith(f"/{self.connection.id}/decline/")
        )
        accept_classes = accept["attrs"]["class"].split()
        decline_classes = decline["attrs"]["class"].split()
        self.assertIn("btn-crush-solid", accept_classes)
        self.assertIn("btn-sm", accept_classes)
        self.assertNotIn("bg-green-500", accept_classes)
        self.assertIn("btn-crush-outline", decline_classes)
        self.assertIn("btn-sm", decline_classes)
        # confirm-sheet.js turns hx-confirm into the branded sheet; the label
        # names the action instead of a generic "Confirm".
        self.assertTrue(decline["attrs"].get("hx-confirm"))
        self.assertEqual(decline["attrs"].get("data-confirm-label"), "Decline")

    def _respond(self, action):
        return self.client.post(
            f"/en/connections/{self.connection.id}/{action}/",
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET=f"connection-{self.connection.id}",
            HTTP_HX_CURRENT_URL="http://crush.lu/en/connections/",
        )

    def _oob_region(self, response):
        self.assertEqual(response.status_code, 200)
        regions = [
            e
            for e in _Tags.parse(response.content.decode())
            if e["attrs"].get("id") == "connection-status-live"
        ]
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0]["attrs"].get("hx-swap-oob"), "innerHTML")

    def test_accept_fills_the_status_region_out_of_band(self):
        response = self._respond("accept")
        self._oob_region(response)
        self.assertContains(response, "It's mutual!", count=2)

    def test_decline_fills_the_status_region_out_of_band(self):
        response = self._respond("decline")
        self._oob_region(response)
        self.assertContains(response, "Connection request declined", count=2)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class AttendeeDeclineConfirmSheetTests(TestCase):
    def test_attendee_decline_names_the_action_on_the_sheet(self):
        with translation.override("en"):
            html = render_to_string(
                "crush_lu/_attendee_connection_actions.html",
                {
                    "attendee": {
                        "user": {"id": 7},
                        "connection_status": "received",
                        "connection_id": 3,
                    },
                    "event": {"id": 1},
                },
            )
        decline = next(
            e
            for e in _Tags.parse(html)
            if e["tag"] == "button"
            and e["attrs"].get("hx-post", "").endswith("/3/decline/")
        )
        self.assertTrue(decline["attrs"].get("hx-confirm"))
        self.assertEqual(decline["attrs"].get("data-confirm-label"), "Decline")


# --------------------------------------------------------------------------
# 4-15 — My Events image link, event_detail availability contrast
# --------------------------------------------------------------------------


class MyEventsImageLinkTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = _member("myevents@example.com")
        self.client = Client(HTTP_HOST="crush.lu")
        self.client.force_login(self.user)

    def test_image_link_is_hidden_duplicate_of_title_link(self):
        upcoming = _event("Upcoming night", days=5)
        past = _event("Past night", days=-5)
        EventRegistration.objects.create(
            event=upcoming, user=self.user, status="confirmed"
        )
        EventRegistration.objects.create(event=past, user=self.user, status="attended")

        response = self.client.get("/en/my-events/")
        self.assertEqual(response.status_code, 200)
        elements = _Tags.parse(response.content.decode())
        for event in (upcoming, past):
            links = [
                e
                for e in elements
                if e["tag"] == "a"
                and e["attrs"].get("href") == f"/en/events/{event.id}/"
            ]
            hidden = [e for e in links if e["attrs"].get("aria-hidden") == "true"]
            self.assertEqual(len(hidden), 1, event.title)
            self.assertEqual(hidden[0]["attrs"].get("tabindex"), "-1")
            # The title link stays the one focusable, named link.
            self.assertTrue(
                any(e["attrs"].get("aria-hidden") is None for e in links), event.title
            )


class EventDetailAvailabilityContrastTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")

    def test_plenty_of_spots_uses_emerald_700(self):
        event = _event(max_participants=40, profile_requirement="none")
        html = self.client.get(f"/en/events/{event.id}/").content.decode()
        self.assertIn("text-emerald-700 dark:text-emerald-400", html)
        self.assertNotIn("text-emerald-600", html)

    def test_filling_up_uses_amber_700(self):
        event = _event(max_participants=15, profile_requirement="none")
        html = self.client.get(f"/en/events/{event.id}/").content.decode()
        self.assertIn("text-sm font-semibold text-amber-700 dark:text-amber-400", html)


# --------------------------------------------------------------------------
# #1082 — entry events
# --------------------------------------------------------------------------


class EntryEventsFilterTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")
        self.entry = _event("Entry Mixer", profile_requirement="completed")
        self.verified_only = _event("Members Gala", profile_requirement="approved")
        self.open_event = _event("Open Picnic", profile_requirement="none")

    def _upcoming_ids(self, response):
        return [e.id for e in response.context["upcoming_event_list"]]

    def test_entry_filter_keeps_only_completed_events_with_a_badge(self):
        response = self.client.get("/en/events/?entry=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._upcoming_ids(response), [self.entry.id])
        self.assertContains(response, "Entry event")
        # Canonical status_badge primitive, not a hand-rolled badge.
        self.assertContains(response, 'class="badge badge-success ')
        self.assertNotContains(response, "bg-emerald-100 text-emerald-800")
        self.assertContains(response, "Showing entry events only")
        self.assertContains(response, 'href="/en/events/"')

    def test_unfiltered_list_is_unchanged(self):
        response = self.client.get("/en/events/")
        self.assertEqual(
            sorted(self._upcoming_ids(response)),
            sorted([self.entry.id, self.verified_only.id, self.open_event.id]),
        )
        self.assertNotContains(response, "Entry event")
        self.assertNotContains(response, "Showing entry events only")

    def test_entry_filter_copy_is_translated(self):
        de = self.client.get("/de/events/?entry=1")
        self.assertContains(de, "Einstiegs-Event")
        self.assertContains(de, "Alle Events anzeigen")
        fr = self.client.get("/fr/events/?entry=1")
        self.assertContains(fr, "Événement d'entrée")
        self.assertContains(fr, "Afficher tous les événements")

    def _entry_links(self, html):
        return [
            e
            for e in _Tags.parse(html)
            if e["tag"] == "a" and e["attrs"].get("href") == "/en/events/?entry=1"
        ]

    def _pending_member(self, email, **kwargs):
        user = User.objects.create_user(username=email, email=email, password="x")
        UserDataConsent.objects.filter(user=user).update(crushlu_consent_given=True)
        defaults = dict(
            date_of_birth=date(1995, 1, 1),
            gender="F",
            location="Luxembourg",
            is_approved=False,
            verification_status="pending",
        )
        defaults.update(kwargs)
        CrushProfile.objects.create(user=user, **defaults)
        return user

    def test_verified_only_box_links_to_entry_events(self):
        self.client.force_login(self._pending_member("pending-entry@example.com"))
        html = self.client.get(f"/en/events/{self.verified_only.id}/").content.decode()
        self.assertIn("See entry events", html)
        self.assertEqual(len(self._entry_links(html)), 1)

    def test_coach_required_box_links_to_entry_events(self):
        event = _event("Coach Circle", profile_requirement="coach_assigned")
        self.client.force_login(
            self._pending_member(
                "nocoach-entry@example.com",
                is_approved=True,
                verification_status="verified",
            )
        )
        html = self.client.get(f"/en/events/{event.id}/").content.decode()
        self.assertIn("Coach required", html)
        self.assertEqual(len(self._entry_links(html)), 1)
