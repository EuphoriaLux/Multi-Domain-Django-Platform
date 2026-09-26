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

from datetime import date, timedelta

from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
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


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
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
        return self.client.get(reverse("crush_lu:dashboard"), HTTP_HOST="crush.lu")

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
            reverse("crush_lu:respond_connection", args=[connection.id, "accept"]),
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
            f'href="{reverse("crush_lu:my_connections")}#received"',
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


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class DashboardStatsTilesTests(TestCase):
    """5-03: the stats row repeated facts shown elsewhere for a brand-new member."""

    def setUp(self):
        self.user = _make_member("stats@example.com")
        self.client.login(username="stats@example.com", password="testpass123")
        from django.core.cache import cache

        cache.clear()

    def _get(self):
        return self.client.get(reverse("crush_lu:dashboard"), HTTP_HOST="crush.lu")

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


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class DashboardNextEventButtonsTests(TestCase):
    """5-07: 44px canonical buttons, Cancel separated from View Ticket."""

    def setUp(self):
        self.user = _make_member("buttons@example.com")
        self.client.login(username="buttons@example.com", password="testpass123")
        from django.core.cache import cache

        cache.clear()

    def _get(self):
        return self.client.get(reverse("crush_lu:dashboard"), HTTP_HOST="crush.lu")

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
        ticket_row_end = html.index("Details</a>")
        cancel_index = html.index("Cancel</a>")
        self.assertGreater(cancel_index, ticket_row_end)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
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


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class ProductsConnectStripDedupeTests(TestCase):
    """5-03: 'Join the Mix' must not repeat between the strip and the product
    card once the strip is rendered on the same page."""

    def setUp(self):
        self.user = _make_member("dedupe@example.com")
        self.client.login(username="dedupe@example.com", password="testpass123")
        from django.core.cache import cache

        cache.clear()

    def _get(self):
        return self.client.get(reverse("crush_lu:dashboard"), HTTP_HOST="crush.lu")

    def test_join_the_mix_cta_appears_at_most_once(self):
        # Verified, not premium, not onboarded into Connect, not excluded --
        # exactly the state where both the strip and the LuxID product card
        # used to render their own "Join the Mix" button.
        response = self._get()
        html = response.content.decode()
        self.assertLessEqual(html.count(">Join the Mix<"), 1)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
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

        response = self.client.get(
            reverse("crush_lu:my_connections"), HTTP_HOST="crush.lu"
        )
        self.assertContains(response, 'id="received"')
