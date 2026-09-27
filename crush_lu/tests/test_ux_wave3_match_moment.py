"""
UX Wave 3 — WP10 "match moment" tests.

Covers findings 5-09/5-10/5-13/5-14/5-15 from the Wave 3 review:
- 5-09: the accept response is a celebratory card with a mini-timeline.
- 5-10: consent step's "not now" option and per-channel (email) opt-in.
- 5-13: retired Sparks pages redirect members with no in-flight spark.
- 5-15: chat compose — first-message placeholder removal, inline error on
  a failed send instead of the whole page being spliced into the thread.

Uses literal paths with HTTP_HOST='crush.lu' (never reverse("crush_lu:...")
in tests — the host-routed urlconf trap documented in AGENTS.md) and
cache.clear() in setUp (SQLite PK-reuse leaks across tests).
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from crush_lu.models import (
    CrushProfile,
    CrushSpark,
    EventConnection,
    EventRegistration,
    MeetupEvent,
    UserDataConsent,
)

User = get_user_model()


def _give_crushlu_consent(user):
    """The consent middleware gates every crush_lu page behind
    UserDataConsent.crushlu_consent_given — a signal creates the row on user
    creation, so tests only need to flip the flag (same pattern as
    ConnectionMessagesEndpointTests in test_connections.py)."""
    UserDataConsent.objects.filter(user=user).update(crushlu_consent_given=True)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class ConsentStepTests(TestCase):
    """Finding 5-10: 'not now' and email opt-in at the consent step."""

    def setUp(self):
        cache.clear()
        self.requester = User.objects.create_user(
            username="consent-req@example.com",
            email="consent-req@example.com",
            password="testpass123",
        )
        self.recipient = User.objects.create_user(
            username="consent-rec@example.com",
            email="consent-rec@example.com",
            password="testpass123",
        )
        for user, gender in [(self.requester, "M"), (self.recipient, "F")]:
            CrushProfile.objects.create(
                user=user,
                date_of_birth=date(1995, 5, 15),
                gender=gender,
                location="Luxembourg",
                is_approved=True,
            )
            _give_crushlu_consent(user)
        self.event = MeetupEvent.objects.create(
            title="Consent Test Event",
            description="desc",
            event_type="mixer",
            date_time=timezone.now() - timedelta(days=1),
            location="Luxembourg",
            address="123 Test Street",
            max_participants=20,
            registration_deadline=timezone.now() - timedelta(days=3),
            is_published=True,
        )
        self.connection = EventConnection.objects.create(
            event=self.event,
            requester=self.requester,
            recipient=self.recipient,
            status="coach_approved",
        )
        self.client = Client()
        self.client.login(username="consent-req@example.com", password="testpass123")

    def _detail_url(self):
        return f"/en/connections/{self.connection.id}/"

    def test_not_now_declines_without_notifying_or_sharing(self):
        response = self.client.post(
            self._detail_url(),
            {"consent": "not_now"},
            HTTP_HOST="crush.lu",
        )
        self.assertEqual(response.status_code, 302)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.status, "declined")
        # Neither side's consent flags were flipped true.
        self.assertFalse(self.connection.requester_consents_to_share)
        self.assertFalse(self.connection.recipient_consents_to_share)

    def test_consent_without_email_checkbox_does_not_share_email(self):
        """The consent form's email checkbox is unchecked by default — an
        omitted `share_email` field (an unchecked HTML checkbox is absent
        from POST data entirely) must record that choice, not silently
        share the email as the old always-on behaviour did."""
        response = self.client.post(
            self._detail_url(),
            {"consent": "yes"},
            HTTP_HOST="crush.lu",
        )
        self.assertEqual(response.status_code, 302)
        self.connection.refresh_from_db()
        self.assertTrue(self.connection.requester_consents_to_share)
        self.assertFalse(self.connection.requester_shares_email)

    def test_consent_with_email_checkbox_shares_email(self):
        response = self.client.post(
            self._detail_url(),
            {"consent": "yes", "share_email": "on"},
            HTTP_HOST="crush.lu",
        )
        self.assertEqual(response.status_code, 302)
        self.connection.refresh_from_db()
        self.assertTrue(self.connection.requester_shares_email)

    def test_shared_connection_hides_email_when_other_side_opted_out(self):
        # Recipient consents without sharing email; requester consents with it.
        self.connection.recipient_consents_to_share = True
        self.connection.recipient_shares_email = False
        self.connection.requester_consents_to_share = True
        self.connection.requester_shares_email = True
        self.connection.status = "shared"
        self.connection.save()

        response = self.client.get(self._detail_url(), HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 200)
        # The requester is viewing: the recipient (the "other side") opted
        # out of email, so it must not appear on the page at all.
        self.assertNotContains(response, self.recipient.email)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class AcceptCelebratoryCardTests(TestCase):
    """Finding 5-09: the accept response is a celebratory card (mobile-
    stacked, "It's mutual!" header, mini-timeline) instead of the old
    cramped single-row green alert."""

    def setUp(self):
        cache.clear()
        self.requester = User.objects.create_user(
            username="accept-req@example.com",
            email="accept-req@example.com",
            password="testpass123",
        )
        self.recipient = User.objects.create_user(
            username="accept-rec@example.com",
            email="accept-rec@example.com",
            password="testpass123",
        )
        for user, gender in [(self.requester, "M"), (self.recipient, "F")]:
            CrushProfile.objects.create(
                user=user,
                date_of_birth=date(1995, 5, 15),
                gender=gender,
                location="Luxembourg",
                is_approved=True,
            )
            _give_crushlu_consent(user)
        self.event = MeetupEvent.objects.create(
            title="Accept Test Event",
            description="desc",
            event_type="mixer",
            date_time=timezone.now() - timedelta(days=1),
            location="Luxembourg",
            address="123 Test Street",
            max_participants=20,
            registration_deadline=timezone.now() - timedelta(days=3),
            is_published=True,
        )
        EventRegistration.objects.create(
            event=self.event, user=self.recipient, status="attended"
        )
        # Different genders: the accept goes through coach review (not the
        # same-gender auto-share branch), which is the path finding 5-09's
        # mini-timeline is for.
        self.connection = EventConnection.objects.create(
            event=self.event,
            requester=self.requester,
            recipient=self.recipient,
            status="pending",
        )
        self.client = Client()
        self.client.login(username="accept-rec@example.com", password="testpass123")

    def test_accept_renders_celebratory_card(self):
        response = self.client.post(
            f"/en/connections/{self.connection.id}/accept/",
            {},
            HTTP_HOST="crush.lu",
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("It's mutual!", body)
        self.assertIn("See next steps", body)
        # The old copy never explained *when* — the mini-timeline does.
        self.assertIn("Coach reviews", body)
        self.assertIn("You both consent", body)
        self.assertIn("Contacts shared", body)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.status, "accepted")


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class SparkListRetirementTests(TestCase):
    """Finding 5-13 (product answer): /sparks/, /sparks/received/ and
    spark_detail permanently redirect to the Crush Connect hub, for every
    member unconditionally — including one with an in-flight spark, per
    the brief's explicit "REMOVE the member Sparks pages" instruction
    (the orphaning mitigation is that coach-side spark tooling stays; see
    coach_spark_list / coach_spark_assign, untouched by this WP)."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="spark-user@example.com",
            email="spark-user@example.com",
            password="testpass123",
        )
        CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 5, 15),
            gender="M",
            location="Luxembourg",
            is_approved=True,
        )
        _give_crushlu_consent(self.user)
        self.event = MeetupEvent.objects.create(
            title="Spark Test Event",
            description="desc",
            event_type="mixer",
            date_time=timezone.now() - timedelta(days=1),
            location="Luxembourg",
            address="123 Test Street",
            max_participants=20,
            registration_deadline=timezone.now() - timedelta(days=3),
            is_published=True,
        )
        self.client = Client()
        self.client.login(username="spark-user@example.com", password="testpass123")

    def test_sparks_list_redirects_permanently_to_connect_hub(self):
        response = self.client.get("/en/sparks/", HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 301)
        self.assertIn("/crush-connect/home/", response.url)

    def test_sparks_received_redirects_permanently_to_connect_hub(self):
        response = self.client.get("/en/sparks/received/", HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 301)
        self.assertIn("/crush-connect/home/", response.url)

    def test_spark_detail_redirects_permanently_to_connect_hub(self):
        spark = CrushSpark.objects.create(
            event=self.event,
            sender=self.user,
            sender_description="the person in the red dress",
        )
        response = self.client.get(f"/en/sparks/{spark.id}/", HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 301)
        self.assertIn("/crush-connect/home/", response.url)

    def test_in_flight_spark_still_redirects_no_exception(self):
        """The brief's product answer is unconditional: even a member with
        an in-flight spark (the case #433's guard was protecting) now gets
        redirected — the member has no page for it any more, by design.
        The coach keeps coach_spark_list/coach_spark_assign (coach-only) to
        see and resolve it, and spark_create_journey stays reachable by
        direct URL."""
        CrushSpark.objects.create(
            event=self.event,
            sender=self.user,
            sender_description="the person in the red dress",
        )
        response = self.client.get("/en/sparks/", HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 301)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class ChatComposeTests(TestCase):
    """Finding 5-15: first-message placeholder removal and an inline error
    (instead of the whole redirected page landing in the thread) on a
    failed send."""

    def setUp(self):
        cache.clear()
        self.sender = User.objects.create_user(
            username="chat-sender@example.com",
            email="chat-sender@example.com",
            password="testpass123",
        )
        self.other = User.objects.create_user(
            username="chat-other@example.com",
            email="chat-other@example.com",
            password="testpass123",
        )
        for user, gender in [(self.sender, "M"), (self.other, "F")]:
            CrushProfile.objects.create(
                user=user,
                date_of_birth=date(1995, 5, 15),
                gender=gender,
                location="Luxembourg",
                is_approved=True,
            )
            _give_crushlu_consent(user)
        self.event = MeetupEvent.objects.create(
            title="Chat Test Event",
            description="desc",
            event_type="mixer",
            date_time=timezone.now() - timedelta(days=1),
            location="Luxembourg",
            address="123 Test Street",
            max_participants=20,
            registration_deadline=timezone.now() - timedelta(days=3),
            is_published=True,
        )
        self.connection = EventConnection.objects.create(
            event=self.event,
            requester=self.sender,
            recipient=self.other,
            status="accepted",
            responded_at=timezone.now(),
        )
        self.client = Client()
        self.client.login(username="chat-sender@example.com", password="testpass123")

    def _detail_url(self):
        return f"/en/connections/{self.connection.id}/"

    def test_first_message_response_removes_empty_placeholder_oob(self):
        response = self.client.post(
            self._detail_url(),
            {"message": "Hello there!"},
            HTTP_HOST="crush.lu",
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('hx-swap-oob="delete"', body)
        self.assertIn('id="chat-empty"', body)
        self.assertEqual(response.headers.get("HX-Trigger"), "connection-message-sent")

    def test_second_message_does_not_repeat_oob_delete(self):
        from crush_lu.models import ConnectionMessage

        ConnectionMessage.objects.create(
            connection=self.connection, sender=self.sender, message="First"
        )
        response = self.client.post(
            self._detail_url(),
            {"message": "Second message"},
            HTTP_HOST="crush.lu",
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("chat-empty", response.content.decode())

    def test_failed_send_returns_inline_retarget_not_a_redirect(self):
        """Before this fix, an invalid HTMX send fell through to a plain
        redirect, so htmx's `beforeend` swap spliced the entire redirected
        page into the message thread instead of showing nothing useful."""
        response = self.client.post(
            self._detail_url(),
            {"message": ""},
            HTTP_HOST="crush.lu",
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("HX-Retarget"), "#chat-compose-error")
        self.assertEqual(response.headers.get("HX-Reswap"), "innerHTML")
        # No full page (e.g. the site nav) leaked into the response.
        self.assertNotIn("<html", response.content.decode().lower())

    def test_failed_send_does_not_create_a_message(self):
        from crush_lu.models import ConnectionMessage

        self.client.post(
            self._detail_url(),
            {"message": "x" * 501},
            HTTP_HOST="crush.lu",
            HTTP_HX_REQUEST="true",
        )
        self.assertFalse(
            ConnectionMessage.objects.filter(connection=self.connection).exists()
        )


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class MyConnectionsEmptyStateTests(TestCase):
    """Finding 5-14: the empty state explains the mechanic and keeps the
    primary CTA available (no longer a 55vh-tall block pushing it down)."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="lonely@example.com",
            email="lonely@example.com",
            password="testpass123",
        )
        # is_approved deliberately left False: CrushProfile.save() forces
        # verification_status to "verified" whenever is_approved is True
        # (legacy-field sync), which would defeat the unverified-member case.
        CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 5, 15),
            gender="M",
            location="Luxembourg",
            verification_status="pending",
        )
        _give_crushlu_consent(self.user)
        self.client = Client()
        self.client.login(username="lonely@example.com", password="testpass123")

    def test_empty_state_explains_the_three_steps(self):
        response = self.client.get("/en/connections/", HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Meet at an event", body)
        self.assertIn("Both say yes", body)
        self.assertIn("Your coach introduces you", body)

    def test_unverified_member_does_not_see_connect_cta(self):
        response = self.client.get("/en/connections/", HTTP_HOST="crush.lu")
        self.assertNotContains(response, "Try Crush Connect")

    def test_verified_member_sees_connect_cta(self):
        self.user.crushprofile.verification_status = "verified"
        self.user.crushprofile.save()
        response = self.client.get("/en/connections/", HTTP_HOST="crush.lu")
        self.assertContains(response, "Try Crush Connect")
