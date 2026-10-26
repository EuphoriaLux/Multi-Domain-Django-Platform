"""SessionRenewalMiddleware slides a logged-in session's expiry once a day.

Before it, a session's expiry was fixed at login (SESSION_SAVE_EVERY_REQUEST
is off), so members were logged out exactly 14 days after signing in however
often they came back. Evidence: ai-memory-hub/memories/crush-session-expiry.md
"""

from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from azureproject.middleware import SessionRenewalMiddleware

User = get_user_model()

STAMP_KEY = SessionRenewalMiddleware.STAMP_KEY
PAGE = "/en/events/"
T0 = datetime(2026, 10, 5, 9, 0, tzinfo=dt_timezone.utc)
COOKIE_AGE = timedelta(seconds=settings.SESSION_COOKIE_AGE)


def _at(moment):
    return patch("django.utils.timezone.now", return_value=moment)


class SessionRenewalTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="renewal@example.com",
            email="renewal@example.com",
            password="pass12345",
        )

    def _get(self, path=PAGE):
        return self.client.get(path, HTTP_HOST="crush.lu")

    def _session_cookie(self, response):
        return response.cookies.get(settings.SESSION_COOKIE_NAME)

    def _stored_session(self):
        return Session.objects.get(session_key=self.client.session.session_key)

    def _log_in_and_visit(self):
        """Log in at T0; the first request stamps the session."""
        with _at(T0):
            self.client.force_login(self.user)
            self._get()
        self.assertEqual(self._stored_session().expire_date, T0 + COOKIE_AGE)

    def test_request_after_interval_slides_expiry_and_resends_cookie(self):
        self._log_in_and_visit()
        session_key = self.client.session.session_key
        later = T0 + timedelta(hours=25)

        with _at(later):
            response = self._get()

        cookie = self._session_cookie(response)
        self.assertIsNotNone(cookie, "the renewed session must re-send its cookie")
        self.assertEqual(cookie.value, session_key)
        self.assertEqual(cookie["max-age"], settings.SESSION_COOKIE_AGE)
        stored = self._stored_session()
        self.assertEqual(stored.expire_date, later + COOKIE_AGE)
        self.assertEqual(stored.get_decoded()[STAMP_KEY], int(later.timestamp()))

    def test_daily_visits_outlive_the_login_window(self):
        """The bug itself: a member who visits daily is still logged in on day 20."""
        self._log_in_and_visit()

        for day in range(1, 21):
            with _at(T0 + timedelta(days=day, minutes=5)):
                self._get()

        with _at(T0 + timedelta(days=20, hours=1)):
            self.assertEqual(
                str(self.client.session.get("_auth_user_id")), str(self.user.pk)
            )

    def test_request_inside_interval_does_not_write_session(self):
        self._log_in_and_visit()

        with _at(T0 + timedelta(hours=1)):
            response = self._get()

        self.assertIsNone(self._session_cookie(response))
        stored = self._stored_session()
        self.assertEqual(stored.expire_date, T0 + COOKIE_AGE)
        self.assertEqual(stored.get_decoded()[STAMP_KEY], int(T0.timestamp()))

    def test_anonymous_session_is_not_renewed(self):
        with _at(T0):
            session = self.client.session
            session["django_language"] = "fr"
            session.save()
        key = session.session_key

        with _at(T0 + timedelta(hours=25)):
            response = self._get()

        self.assertIsNone(self._session_cookie(response))
        stored = Session.objects.get(session_key=key)
        self.assertEqual(stored.expire_date, T0 + COOKIE_AGE)
        self.assertNotIn(STAMP_KEY, stored.get_decoded())

    def test_anonymous_request_without_session_creates_none(self):
        with _at(T0):
            response = self._get()

        self.assertIsNone(self._session_cookie(response))
        self.assertFalse(Session.objects.exists())

    def test_logout_is_not_renewed(self):
        self._log_in_and_visit()
        key = self.client.session.session_key

        with _at(T0 + timedelta(hours=25)):
            response = self.client.post("/en/logout/", HTTP_HOST="crush.lu")

        self.assertFalse(Session.objects.filter(session_key=key).exists())
        self.assertFalse(Session.objects.exists())
        cookie = self._session_cookie(response)
        self.assertIsNotNone(cookie, "logout must delete the session cookie")
        self.assertEqual(cookie.value, "")

    def test_health_probe_and_static_paths_are_skipped(self):
        self._log_in_and_visit()

        with _at(T0 + timedelta(hours=25)):
            for path in ("/healthz/", "/static/crush_lu/missing.css"):
                response = self._get(path)
                self.assertIsNone(self._session_cookie(response), path)

        self.assertEqual(self._stored_session().expire_date, T0 + COOKIE_AGE)


class SessionRenewalUnitTests(TestCase):
    """Cases the test client cannot reach: a user without a stored session."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="renewal-unit@example.com",
            email="renewal-unit@example.com",
            password="pass12345",
        )

    def _run(self, request, view=None):
        view = view or (lambda r: HttpResponse("ok"))
        chain = SessionMiddleware(SessionRenewalMiddleware(view))
        return chain(request)

    def test_authenticated_request_without_session_creates_none(self):
        """A JWT-authenticated hub call has a user but no session to renew."""
        request = RequestFactory().get("/api/hub/contacts/", HTTP_HOST="crush.lu")
        request.user = self.user

        with _at(T0):
            response = self._run(request)

        self.assertNotIn(settings.SESSION_COOKIE_NAME, response.cookies)
        self.assertFalse(Session.objects.exists())

    def test_session_saved_anyway_records_the_renewal(self):
        """A write the view causes anyway resets the clock without a second one."""
        with _at(T0):
            self.client.force_login(self.user)
        key = self.client.session.session_key
        later = T0 + timedelta(hours=2)

        def view(request):
            request.session["wizard_step"] = 2
            return HttpResponse("ok")

        request = RequestFactory().get("/en/events/", HTTP_HOST="crush.lu")
        request.COOKIES[settings.SESSION_COOKIE_NAME] = key
        request.user = self.user
        with _at(later):
            response = self._run(request, view)

        self.assertIn(settings.SESSION_COOKIE_NAME, response.cookies)
        stored = Session.objects.get(session_key=key).get_decoded()
        self.assertEqual(stored[STAMP_KEY], int(later.timestamp()))
        self.assertEqual(stored["wizard_step"], 2)
