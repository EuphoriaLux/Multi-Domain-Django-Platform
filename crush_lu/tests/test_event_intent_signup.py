"""Audit A11: keep the event a visitor picked through signup and onboarding.

``/signup/?event=<id>`` remembers an open event by ID (services/event_intent.py);
a successful email signup copies it to the cache so the verification link can
be opened in another browser; logging in there restores it; a banner on every
page links back to the event until the member registers, dismisses it, the
event closes (said once) or a week passes.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.utils import timezone

from crush_lu.models import EventRegistration, MeetupEvent
from crush_lu.models.profiles import UserDataConsent
from crush_lu.services import event_intent

User = get_user_model()
HOST = "crush.lu"


class EventIntentSignupTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST=HOST)
        self.event = MeetupEvent.objects.create(
            title="Autumn Mixer",
            description="Meet people",
            event_type="mixer",
            date_time=timezone.now() + timedelta(days=7),
            location="Luxembourg",
            address="1 Test Street",
            max_participants=20,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True,
            profile_requirement="none",
        )

    def _session_intent(self):
        return self.client.session.get(event_intent.SESSION_KEY)

    def _member(self):
        user = User.objects.create_user(
            username="new@example.com", email="new@example.com", password="pw-12345!"
        )
        UserDataConsent.objects.update_or_create(
            user=user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        self.client.force_login(user)
        return user

    def _set_intent(self, at=None):
        session = self.client.session
        session[event_intent.SESSION_KEY] = {
            "event_id": self.event.pk,
            "at": (at or timezone.now()).isoformat(),
        }
        session.save()

    # -- capture ----------------------------------------------------------------

    def test_event_page_signup_links_carry_the_event(self):
        html = self.client.get(f"/en/events/{self.event.pk}/").content.decode()
        self.assertIn(f"/en/signup/?event={self.event.pk}", html)

    def test_signup_page_remembers_an_open_event(self):
        self.client.get(f"/en/signup/?event={self.event.pk}")
        self.assertEqual(self._session_intent()["event_id"], self.event.pk)

    def test_signup_page_ignores_garbage_unpublished_and_closed_events(self):
        for value in ("abc", "999999", "../../admin"):
            self.client.get(f"/en/signup/?event={value}")
            self.assertIsNone(self._session_intent())
        self.event.is_published = False
        self.event.save()
        self.client.get(f"/en/signup/?event={self.event.pk}")
        self.assertIsNone(self._session_intent())
        self.event.is_published = True
        self.event.registration_deadline = timezone.now() - timedelta(hours=1)
        self.event.save()
        self.client.get(f"/en/signup/?event={self.event.pk}")
        self.assertIsNone(self._session_intent())

    # -- across browsers ----------------------------------------------------------

    def test_signup_post_keeps_the_event_for_another_browser(self):
        self.client.get(f"/en/signup/?event={self.event.pk}")
        self.client.post(
            "/en/signup/",
            {
                "first_name": "New",
                "last_name": "Member",
                "email": "fresh@example.com",
                "password1": "a-Strong-pass-9876",
                "crushlu_consent": "on",
            },
        )
        user = User.objects.get(email="fresh@example.com")
        self.assertIsNotNone(cache.get(f"event_intent:{user.pk}"))

        other_browser = Client(HTTP_HOST=HOST)
        other_browser.force_login(user)
        self.assertEqual(
            other_browser.session[event_intent.SESSION_KEY]["event_id"], self.event.pk
        )
        self.assertIsNone(cache.get(f"event_intent:{user.pk}"))

    # -- banner -------------------------------------------------------------------

    def test_banner_links_back_to_the_event(self):
        self._member()
        self._set_intent()
        html = self.client.get("/en/events/").content.decode()
        self.assertIn('data-testid="event-intent-banner"', html)
        self.assertIn(f'href="/en/events/{self.event.pk}/"', html)
        self.assertIn("Continue registration", html)

    def test_banner_hidden_on_the_event_page_itself(self):
        self._member()
        self._set_intent()
        html = self.client.get(f"/en/events/{self.event.pk}/").content.decode()
        self.assertNotIn('data-testid="event-intent-banner"', html)

    def test_registering_clears_the_intent(self):
        user = self._member()
        self._set_intent()
        EventRegistration.objects.create(event=self.event, user=user, status="pending")
        html = self.client.get("/en/events/").content.decode()
        self.assertNotIn('data-testid="event-intent-banner"', html)
        self.assertIsNone(self._session_intent())

    def test_closed_event_is_explained_once(self):
        self._member()
        self._set_intent()
        self.event.registration_deadline = timezone.now() - timedelta(hours=1)
        self.event.save()
        html = self.client.get("/en/events/").content.decode()
        self.assertIn("has closed", html)
        self.assertIsNone(self._session_intent())
        html = self.client.get("/en/events/").content.decode()
        self.assertNotIn('data-testid="event-intent-banner"', html)

    def test_stale_intent_is_dropped(self):
        self._member()
        self._set_intent(at=timezone.now() - timedelta(days=8))
        html = self.client.get("/en/events/").content.decode()
        self.assertNotIn('data-testid="event-intent-banner"', html)
        self.assertIsNone(self._session_intent())

    def test_dismiss_clears_the_intent(self):
        self._member()
        self._set_intent()
        self.client.post("/en/events/continue/dismiss/")
        self.assertIsNone(self._session_intent())

    def test_no_query_without_an_intent(self):
        """Runs in the context processor on every authenticated page."""
        from types import SimpleNamespace

        request = SimpleNamespace(session={}, user=None)
        with self.assertNumQueries(0):
            self.assertIsNone(event_intent.current(request))
