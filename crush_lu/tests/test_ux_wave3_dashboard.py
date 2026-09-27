"""
UX review Wave 3, WP9 (dashboard): findings 5-02, 5-03, 5-04, 5-07, 5-12.

  5-02 — a received connection request only ever showed as grey header text.
         The dashboard now surfaces up to 3 as actionable cards in "Needs
         action", and the header count links to my_connections#received.
  5-03 — the stats tiles repeated the next-event card and the header; the
         Apple Wallet row showed a disabled "Soon" pill instead of hiding.
  5-07 — the next-event card's Cancel button sat right beside View Ticket,
         both under the 44px touch target and using ad-hoc classes.
  5-12 — the Premium CTA lost its side padding when the DE/FR label wraps.

Every test here fails on origin/main (pre-Wave-3 dashboard.html/views.py).
"""

import re
from datetime import date, timedelta

from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from crush_lu.models import (
    CrushProfile,
    EventConnection,
    EventRegistration,
    MeetupEvent,
)
from crush_lu.models.profiles import UserDataConsent


def _make_member(username, *, verified=True):
    user = User.objects.create_user(
        username=username,
        email=username,
        password="testpass123",
        first_name="Mem",
    )
    CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1995, 5, 15),
        gender="M",
        location="canton-luxembourg",
        is_approved=verified,
        is_active=True,
    )
    consent, _ = UserDataConsent.objects.get_or_create(user=user)
    consent.crushlu_consent_given = True
    consent.save()
    EmailAddress.objects.update_or_create(
        user=user, email=user.email, defaults={"verified": True, "primary": True}
    )
    return user


def _make_event(title="Test event", *, days_from_now=10):
    when = timezone.now() + timedelta(days=days_from_now)
    return MeetupEvent.objects.create(
        title=title,
        description="x",
        event_type="mixer",
        date_time=when,
        location="Luxembourg",
        address="1 Test St",
        max_participants=20,
        duration_minutes=120,
        registration_deadline=when - timedelta(days=2),
        is_published=True,
    )


class DashboardConnectionRequestsTests(TestCase):
    """5-02: received connection requests get an actionable dashboard card."""

    def setUp(self):
        self.user = _make_member("member5-02@example.com")
        self.client.login(username="member5-02@example.com", password="testpass123")
        self.cache_clear()

    def cache_clear(self):
        from django.core.cache import cache

        cache.clear()

    def _get(self):
        return self.client.get("/en/dashboard/", HTTP_HOST="crush.lu")

    def test_received_request_renders_as_an_actionable_card(self):
        requester = _make_member("requester@example.com")
        event = _make_event()
        connection = EventConnection.objects.create(
            requester=requester, recipient=self.user, event=event, status="pending"
        )

        response = self._get()

        self.assertIn(connection, response.context["pending_connection_requests"])
        self.assertContains(response, "wants to connect")
        self.assertContains(
            response,
            f"/en/connections/{connection.id}/accept/",
        )

    def test_header_count_links_to_my_connections_received_anchor(self):
        requester = _make_member("requester2@example.com")
        event = _make_event()
        EventConnection.objects.create(
            requester=requester, recipient=self.user, event=event, status="pending"
        )

        response = self._get()

        self.assertContains(
            response,
            'href="/en/connections/#received"',
        )

    def test_no_pending_requests_means_no_header_subtitle_link(self):
        response = self._get()
        self.assertNotContains(response, "#received")

    def test_crush_flow_leads_never_appear_as_a_dashboard_card(self):
        """Crush leads are private -- never shown to the recipient directly."""
        requester = _make_member("crusher@example.com")
        event = _make_event()
        EventConnection.objects.create(
            requester=requester,
            recipient=self.user,
            event=event,
            status="pending",
            flow=EventConnection.FLOW_CRUSH,
        )

        response = self._get()
        self.assertEqual(list(response.context["pending_connection_requests"]), [])


class DashboardStatsTilesTests(TestCase):
    """5-03: the stats row repeated facts shown elsewhere for a brand-new member."""

    def setUp(self):
        self.user = _make_member("stats@example.com")
        self.client.login(username="stats@example.com", password="testpass123")
        from django.core.cache import cache

        cache.clear()

    def _get(self):
        return self.client.get("/en/dashboard/", HTTP_HOST="crush.lu")

    def test_stats_hidden_for_a_member_with_nothing_attended_or_connected(self):
        response = self._get()
        self.assertFalse(response.context["show_stats_tiles"])
        self.assertNotContains(response, "Attended")

    def test_stats_shown_once_the_member_has_attended_an_event(self):
        event = _make_event(days_from_now=-5)
        EventRegistration.objects.create(user=self.user, event=event, status="attended")

        response = self._get()
        self.assertTrue(response.context["show_stats_tiles"])
        self.assertContains(response, "Attended")

    def test_apple_wallet_soon_pill_is_hidden_when_not_configured(self):
        """apple_wallet_enabled is False with no WALLET_APPLE_* settings.

        The page's own JS comments legitimately say "Apple Wallet" (the iOS
        bridge for the *enabled* case), so this checks the disabled pill's
        actual markup, not a bare substring.
        """
        response = self._get()
        self.assertFalse(response.context["apple_wallet_enabled"])
        # Neither the working link (enabled) nor the disabled "Soon" pill
        # (the old always-rendered fallback) should be present at all.
        self.assertNotContains(response, "wallet/apple_wallet_badge.svg")
        self.assertNotContains(response, '<button type="button" disabled')


class DashboardNextEventButtonsTests(TestCase):
    """5-07: 44px canonical buttons, Cancel separated from View Ticket."""

    def setUp(self):
        self.user = _make_member("buttons@example.com")
        self.client.login(username="buttons@example.com", password="testpass123")
        from django.core.cache import cache

        cache.clear()

    def _get(self):
        return self.client.get("/en/dashboard/", HTTP_HOST="crush.lu")

    def test_view_ticket_and_details_use_canonical_44px_buttons(self):
        event = _make_event(days_from_now=7)
        EventRegistration.objects.create(
            user=self.user, event=event, status="confirmed"
        )

        response = self._get()
        html = response.content.decode()
        self.assertIn(
            "btn-crush-solid btn-sm inline-flex items-center justify-center gap-1.5 min-h-[44px]",
            html,
        )
        self.assertIn(
            "btn-crush-outline btn-sm inline-flex items-center justify-center gap-1.5 min-h-[44px]",
            html,
        )

    def test_cancel_uses_danger_variant_not_a_hand_rolled_outline(self):
        event = _make_event(days_from_now=7)
        EventRegistration.objects.create(
            user=self.user, event=event, status="confirmed"
        )

        response = self._get()
        html = response.content.decode()
        self.assertIn("btn-danger btn-sm", html)
        # The old hand-rolled red outline classes must be gone.
        self.assertNotIn("border-red-300", html)

    def test_cancel_no_longer_shares_a_row_with_view_ticket(self):
        """Spatial separation: Cancel's wrapping div comes after the button
        row's closing tag, not inside the same flex row."""
        event = _make_event(days_from_now=7)
        EventRegistration.objects.create(
            user=self.user, event=event, status="confirmed"
        )

        html = self._get().content.decode()
        details_index = html.index("Details</a>")
        cancel_index = html.index("Cancel</a>")
        # Between Details and Cancel, the action row's </div> must close
        # before Cancel's own wrapper opens. The old markup had both links in
        # one flex row, with no tags between them.
        between = html[details_index:cancel_index]
        row_close = between.find("</div>")
        cancel_wrapper = between.rfind('<div class="mt-2.5">')
        self.assertNotEqual(row_close, -1)
        self.assertNotEqual(cancel_wrapper, -1)
        self.assertLess(row_close, cancel_wrapper)
        # No other link (i.e. Cancel) sits inside the action row.
        self.assertNotIn("<a ", between[:row_close])


class ProductsPremiumCtaPaddingTests(TestCase):
    """5-12: the Premium CTA keeps its padding when the DE/FR label wraps."""

    def setUp(self):
        self.user = _make_member("premium@example.com")
        self.client.login(username="premium@example.com", password="testpass123")
        from django.core.cache import cache

        cache.clear()

    def _get(self, lang="en"):
        # Literal path, not reverse(): urls_crush sits behind i18n_patterns,
        # so the URL always carries a language prefix (AGENTS.md).
        return self.client.get(f"/{lang}/dashboard/", HTTP_HOST="crush.lu")

    def test_premium_cta_has_side_padding_and_wrap_safe_classes(self):
        response = self._get()
        html = response.content.decode()
        self.assertIn(
            'class="inline-flex items-center justify-center gap-1.5 w-full px-4 py-2.5 '
            "rounded-xl bg-white text-crush-purple text-sm font-bold text-center "
            'leading-snug hover:bg-purple-50 transition-colors"',
            html,
        )

    def test_premium_cta_label_translates_in_french(self):
        response = self._get(lang="fr")
        self.assertContains(response, "Passez à Premium")

    def test_premium_cta_padding_survives_in_french_too(self):
        response = self._get(lang="fr")
        self.assertContains(response, "px-4 py-2.5")


class ProductsConnectStripDedupeTests(TestCase):
    """5-03: 'Join the Mix' must not repeat between the strip and the product
    card once the strip is rendered on the same page."""

    def setUp(self):
        self.user = _make_member("dedupe@example.com")
        self.client.login(username="dedupe@example.com", password="testpass123")
        from django.core.cache import cache

        cache.clear()

    def _get(self):
        return self.client.get("/en/dashboard/", HTTP_HOST="crush.lu")

    @override_settings(CRUSH_CONNECT_LAUNCHED=True)
    def test_join_the_mix_cta_appears_exactly_once(self):
        # Verified with LuxID linked (Connect-identity-verified), no attended
        # event, not premium, not onboarded into Connect. In that state the
        # strip and the LuxID product card both used to render "Join the Mix".
        # (With in-person verification the strip says "Start my Connect Week"
        # instead, so it has to be the LuxID path.)
        from allauth.socialaccount.models import SocialAccount

        profile = self.user.crushprofile
        profile.verification_status = "verified"
        profile.save(update_fields=["verification_status"])
        SocialAccount.objects.create(
            user=self.user, provider="luxid", uid=f"lux-{self.user.pk}"
        )
        profile.refresh_from_db()
        self.assertTrue(profile.is_connect_identity_verified)

        response = self._get()
        html = response.content.decode()
        # The strip pads its link text with template whitespace.
        self.assertEqual(len(re.findall(r">\s*Join the Mix\s*<", html)), 1)
        self.assertContains(response, "See Crush Connect above")


class MyConnectionsReceivedAnchorTests(TestCase):
    """The dashboard's #received links need somewhere to land."""

    def setUp(self):
        self.user = _make_member("anchor@example.com")
        self.client.login(username="anchor@example.com", password="testpass123")
        from django.core.cache import cache

        cache.clear()

    def test_received_requests_section_has_the_anchor_id(self):
        requester = _make_member("anchor-requester@example.com")
        event = _make_event()
        EventConnection.objects.create(
            requester=requester, recipient=self.user, event=event, status="pending"
        )

        response = self.client.get("/en/connections/", HTTP_HOST="crush.lu")
        self.assertContains(response, 'id="received"')


class DashboardReviewRoundTwoTests(TestCase):
    """Second Codex review round on #1042."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.user = _make_member("round2@example.com")
        self.client.login(username="round2@example.com", password="testpass123")

    def test_stats_shown_when_more_bookings_than_the_next_event_card(self):
        EventRegistration.objects.create(
            user=self.user,
            event=_make_event("First", days_from_now=5),
            status="confirmed",
        )
        EventRegistration.objects.create(
            user=self.user,
            event=_make_event("Second", days_from_now=9),
            status="confirmed",
        )
        response = self.client.get("/en/dashboard/", HTTP_HOST="crush.lu")
        self.assertTrue(response.context["show_stats_tiles"])

    def test_single_booking_still_hides_the_duplicated_stats_row(self):
        EventRegistration.objects.create(
            user=self.user,
            event=_make_event("Only", days_from_now=5),
            status="confirmed",
        )
        response = self.client.get("/en/dashboard/", HTTP_HOST="crush.lu")
        self.assertFalse(response.context["show_stats_tiles"])

    def _pending_request(self):
        requester = _make_member("round2-requester@example.com")
        event = _make_event("Past", days_from_now=-3)
        EventRegistration.objects.create(user=self.user, event=event, status="attended")
        EventRegistration.objects.create(user=requester, event=event, status="attended")
        return EventConnection.objects.create(
            requester=requester, recipient=self.user, event=event, status="pending"
        )

    def _respond(self, connection, action, current_url):
        return self.client.post(
            f"/en/connections/{connection.id}/{action}/",
            HTTP_HOST="crush.lu",
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET=f"connection-{connection.id}",
            HTTP_HX_CURRENT_URL=current_url,
        )

    def test_inline_accept_on_the_dashboard_refreshes_the_page(self):
        connection = self._pending_request()
        response = self._respond(connection, "accept", "https://crush.lu/en/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Refresh"], "true")
        connection.refresh_from_db()
        self.assertNotEqual(connection.status, "pending")

    def test_inline_decline_on_the_dashboard_refreshes_the_page(self):
        connection = self._pending_request()
        response = self._respond(
            connection, "decline", "https://crush.lu/en/dashboard/"
        )
        self.assertEqual(response["HX-Refresh"], "true")

    def test_my_connections_keeps_the_in_place_swap(self):
        connection = self._pending_request()
        response = self._respond(
            connection, "accept", "https://crush.lu/en/connections/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("HX-Refresh", response)


class DashboardAlreadyProcessedRequestTests(DashboardReviewRoundTwoTests):
    def test_request_handled_elsewhere_still_refreshes_the_dashboard(self):
        connection = self._pending_request()
        connection.status = "declined"
        connection.save(update_fields=["status"])
        response = self._respond(connection, "accept", "https://crush.lu/en/dashboard/")
        self.assertEqual(response["HX-Refresh"], "true")


class DashboardHiddenEncounterTests(DashboardReviewRoundTwoTests):
    """Codex (P1) on #1042: a safety-removed encounter pair must stay
    invisible on the dashboard and must not be acceptable from it."""

    def _hide_pair(self, other, status):
        from crush_lu.models import ConfirmedEncounter

        low, high = ConfirmedEncounter.canonical_pair(self.user, other)
        ConfirmedEncounter.objects.create(user_low=low, user_high=high, status=status)

    def _assert_hidden_on_dashboard(self, status):
        connection = self._pending_request()
        self._hide_pair(connection.requester, status)
        response = self.client.get("/en/dashboard/", HTTP_HOST="crush.lu")
        self.assertNotIn(connection, response.context["pending_connection_requests"])

    def test_removal_pending_request_is_not_shown_on_the_dashboard(self):
        self._assert_hidden_on_dashboard("removal_pending")

    def test_removed_encounter_request_is_not_shown_on_the_dashboard(self):
        self._assert_hidden_on_dashboard("removed")

    def test_removed_encounter_request_cannot_be_accepted(self):
        connection = self._pending_request()
        self._hide_pair(connection.requester, "removed")
        self._respond(connection, "accept", "https://crush.lu/en/dashboard/")
        connection.refresh_from_db()
        self.assertEqual(connection.status, "pending")


class DashboardHiddenEncounterBadgeCountTests(DashboardReviewRoundTwoTests):
    """Follow-up on #1042: the context processor that feeds the header badge,
    the "#received" link and "View all requests" must exclude a
    safety-removed encounter's pending request the same way the dashboard's
    own query and my_connections already do (see DashboardHiddenEncounterTests
    above) — otherwise the nav count still advertises a request the page
    itself hides."""

    def _hide_pair(self, other, status):
        from crush_lu.models import ConfirmedEncounter

        low, high = ConfirmedEncounter.canonical_pair(self.user, other)
        ConfirmedEncounter.objects.create(user_low=low, user_high=high, status=status)

    def test_removal_pending_request_does_not_count_toward_the_badge(self):
        connection = self._pending_request()
        self._hide_pair(connection.requester, "removal_pending")
        response = self.client.get("/en/dashboard/", HTTP_HOST="crush.lu")
        self.assertEqual(response.context["pending_requests_count"], 0)

    def test_removed_encounter_request_does_not_count_toward_the_badge(self):
        connection = self._pending_request()
        self._hide_pair(connection.requester, "removed")
        response = self.client.get("/en/dashboard/", HTTP_HOST="crush.lu")
        self.assertEqual(response.context["pending_requests_count"], 0)

    def test_visible_pending_request_still_counts(self):
        self._pending_request()
        response = self.client.get("/en/dashboard/", HTTP_HOST="crush.lu")
        self.assertEqual(response.context["pending_requests_count"], 1)
