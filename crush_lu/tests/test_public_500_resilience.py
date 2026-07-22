"""
Regression tests for the 2026-07-22 public-page 500 incident.

Symptom: while a worker's asgiref sync-executor was wedged, authenticated
crush.lu pages — and, because ``custom_404`` renders ``crush_lu/base.html``
with the request context processors, even missing routes like
``/favicon.ico`` — returned 500 instead of degrading gracefully. ``/crush-admin``
kept working, which is what pointed at the crush_lu public render path.

Two defensive layers are covered here:

1. ``crush_user_context`` must not propagate a transient backend fault; it
   falls back to safe navbar defaults so the page still renders.
2. ``custom_404`` must never let a failed branded-404 render become a 500 —
   a not-found stays a not-found.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http import Http404
from django.test import RequestFactory, TestCase

from crush_lu.context_processors import _SAFE_NAV_DEFAULTS, crush_user_context
from crush_lu.views_seo import custom_404


class CrushUserContextResilienceTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        User = get_user_model()
        self.user = User.objects.create_user(
            username="coach@example.com",
            email="coach@example.com",
            password="pass123",
            first_name="Coach",
        )

    def _authenticated_request(self):
        request = self.factory.get("/en/")
        request.user = self.user
        return request

    def test_backend_failure_falls_back_to_safe_defaults(self):
        """A transient fault in the authenticated block must not raise; the
        navbar-critical keys fall back to safe defaults instead."""
        request = self._authenticated_request()

        # blocked_user_ids runs near the top of the authenticated block, so
        # failing it exercises the outer guard. Mimic the real failure mode.
        with patch(
            "crush_lu.services.blocking.blocked_user_ids",
            side_effect=RuntimeError("CurrentThreadExecutor already quit or is broken"),
        ):
            context = crush_user_context(request)  # must NOT raise

        # Every key the block would have set past the failure point falls back
        # to its safe default (email_verified is resolved earlier, in its own
        # try/except, so it keeps whatever real value it computed).
        for key in (
            "connection_count",
            "pending_requests_count",
            "actionable_sparks_count",
            "connect_pending_sparks_count",
            "profile_completion_step",
            "profile_step_label",
            "upcoming_events",
            "upcoming_events_count",
            # base.html branches on these instead of dereferencing the
            # crushprofile/crushcoach reverse relations, so they must be safe
            # too or the nav render would re-trigger the DB and 500.
            "nav_has_profile",
            "nav_is_active_coach",
        ):
            self.assertEqual(
                context[key], _SAFE_NAV_DEFAULTS[key], msg=f"default missing for {key}"
            )
        # Anonymous-safe base keys and the early email flag are still present.
        self.assertIn("crush_cache_enabled", context)
        self.assertIn("email_verified", context)

    def test_healthy_authenticated_context_is_unchanged(self):
        """The guard is transparent on the happy path (no profile → step 0)."""
        context = crush_user_context(self._authenticated_request())
        self.assertEqual(context["connection_count"], 0)
        self.assertEqual(context["profile_completion_step"], 0)
        self.assertIn("upcoming_events", context)
        # Template-safe nav flags are always resolved for authenticated users
        # (this user has neither a profile nor a coach record).
        self.assertFalse(context["nav_has_profile"])
        self.assertFalse(context["nav_is_active_coach"])

    def test_anonymous_user_skips_the_block(self):
        request = self.factory.get("/en/")
        request.user = AnonymousUser()
        context = crush_user_context(request)
        # The DB-heavy block only runs for authenticated users.
        self.assertNotIn("connection_count", context)


class Custom404ResilienceTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_failed_branded_render_falls_back_to_404_not_500(self):
        """If the branded 404 render blows up (e.g. a context processor throws),
        custom_404 returns a plain 404 — never a 500."""
        request = self.factory.get("/favicon.ico")
        request.user = AnonymousUser()

        with patch(
            "crush_lu.views_seo.render",
            side_effect=RuntimeError("context processor blew up mid-render"),
        ):
            response = custom_404(request, Http404())

        self.assertEqual(response.status_code, 404)
        self.assertIn(b"404", response.content)
