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
from django.utils.crypto import get_random_string

from crush_lu.models import EventRegistration, MeetupEvent
from crush_lu.models.profiles import UserDataConsent
from crush_lu.services import event_intent

User = get_user_model()
HOST = "crush.lu"


def _login_request(host, user):
    """A login on ``host`` in a fresh browser: the ``user_logged_in`` signal
    with a real host (``Client.force_login`` sends it with none)."""
    from django.contrib.auth.signals import user_logged_in
    from django.contrib.sessions.middleware import SessionMiddleware
    from django.test import RequestFactory

    request = RequestFactory().get("/", HTTP_HOST=host)
    SessionMiddleware(lambda r: None).process_request(request)
    user_logged_in.send(sender=user.__class__, request=request, user=user)
    return request


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
                "password1": get_random_string(20),
                "crushlu_consent": "on",
            },
        )
        user = User.objects.get(email="fresh@example.com")
        self.assertIsNotNone(cache.get(f"event_intent:{user.pk}"))

        other_browser = _login_request(HOST, user)
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

    def test_curated_event_banner_says_application(self):
        self.event.registration_mode = MeetupEvent.REGISTRATION_MODE_CURATED
        self.event.event_type = "speed_dating"
        self.event.save()
        self._member()
        self._set_intent()
        html = self.client.get("/en/events/").content.decode()
        self.assertIn("Continue application", html)
        self.assertNotIn("Continue registration", html)

    def test_login_on_another_platform_leaves_the_intent_for_crush(self):
        user = self._member()
        cache.set(f"event_intent:{user.pk}", {"event_id": self.event.pk, "at": "x"})
        request = _login_request("powerup.lu", user)
        self.assertNotIn(event_intent.SESSION_KEY, request.session)
        self.assertIsNotNone(cache.get(f"event_intent:{user.pk}"))

    # -- Codex review, second round ---------------------------------------------------

    def test_private_event_is_never_remembered(self):
        self.event.is_private_invitation = True
        self.event.save()
        self.client.get(f"/en/signup/?event={self.event.pk}")
        self.assertIsNone(self._session_intent())

    def test_stale_local_intent_does_not_swallow_the_new_one(self):
        user = User.objects.create_user(username="s@example.com", password="x")
        cache.set(
            f"event_intent:{user.pk}",
            {"event_id": self.event.pk, "at": timezone.now().isoformat()},
        )
        from django.contrib.auth.signals import user_logged_in
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.test import RequestFactory

        request = RequestFactory().get("/", HTTP_HOST=HOST)
        SessionMiddleware(lambda r: None).process_request(request)
        request.session[event_intent.SESSION_KEY] = {
            "event_id": self.event.pk,
            "at": (timezone.now() - timedelta(days=30)).isoformat(),
        }
        user_logged_in.send(sender=User, request=request, user=user)
        self.assertTrue(event_intent._fresh(request.session[event_intent.SESSION_KEY]))

    def test_htmx_dismiss_stays_on_the_page(self):
        self._member()
        self._set_intent()
        response = self.client.post(
            "/en/events/continue/dismiss/", HTTP_HX_REQUEST="true"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")
        self.assertIsNone(self._session_intent())

    def test_plain_dismiss_returns_to_a_safe_page_only(self):
        self._member()
        self._set_intent()
        response = self.client.post(
            "/en/events/continue/dismiss/", {"next": "/en/create-profile/"}
        )
        self.assertEqual(response["Location"], "/en/create-profile/")
        self._set_intent()
        response = self.client.post(
            "/en/events/continue/dismiss/", {"next": "https://evil.example/"}
        )
        self.assertNotIn("evil", response["Location"])
