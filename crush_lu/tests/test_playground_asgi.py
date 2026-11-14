"""Coach playground: the 7-day crush.lu recheck on WebSocket connections.

Spec: ai-memory-hub/specs/2026-11-13-crush-coach-playground.md
"""

import asyncio
import time
from unittest import mock

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser, User
from django.contrib.sessions.backends.db import SessionStore

from crush_lu import playground_asgi
from crush_lu.models import CrushCoach, PlaygroundAccount
from crush_lu.services import playground_sso as sso

pytestmark = pytest.mark.django_db


@pytest.fixture
def staging(monkeypatch, settings):
    monkeypatch.setenv("DJANGO_ENV", "staging")
    settings.DEBUG = False
    settings.COACH_PLAYGROUND_SSO_SECRET = "s" * 48
    settings.COACH_PLAYGROUND_SSO_ISSUER = "https://crush.lu"
    return settings


def _coach(linked=True):
    user = User.objects.create_user(username="wscoach", email="wscoach@crush.lu")
    CrushCoach.objects.create(user=user, is_active=True)
    if linked:
        PlaygroundAccount.objects.create(
            user=user, kind="coach", production_account_id=99
        )
    return user


def _scope(user, signed_in_ago=None):
    session = SessionStore()
    if signed_in_ago is not None:
        session[sso.SIGNED_IN_AT_SESSION_KEY] = int(time.time()) - signed_in_ago
    return {"type": "websocket", "user": user, "session": session}


class TestDeadline:
    def test_fresh_linked_coach_gets_a_deadline(self, staging):
        deadline = playground_asgi.deadline_for(_scope(_coach(), signed_in_ago=60))
        assert deadline == pytest.approx(
            time.time() - 60 + sso.FRESHNESS_SECONDS, abs=5
        )

    def test_stale_or_missing_sign_in_is_expired(self, staging):
        coach = _coach()
        stale = sso.FRESHNESS_SECONDS + 60
        assert playground_asgi.deadline_for(_scope(coach, signed_in_ago=stale)) == 0.0
        assert playground_asgi.deadline_for(_scope(coach)) == 0.0

    def test_unlinked_and_anonymous_are_left_alone(self, staging):
        assert playground_asgi.deadline_for(_scope(_coach(linked=False))) is None
        assert playground_asgi.deadline_for(_scope(AnonymousUser())) is None

    def test_nothing_applies_without_sso(self, monkeypatch, settings):
        monkeypatch.setenv("DJANGO_ENV", "staging")
        settings.COACH_PLAYGROUND_SSO_SECRET = ""
        assert playground_asgi.deadline_for(_scope(_coach())) is None


def _run(scope, deadline, *, inner_waits=False):
    """Drive the middleware with a stub inner app; return what it saw/sent."""
    seen, sent = {}, []

    async def inner(scope, receive, send):
        seen["user"] = scope.get("user")
        if inner_waits:
            seen["message"] = await receive()

    async def receive():
        await asyncio.Event().wait()  # the client never sends anything

    async def send(message):
        sent.append(message)

    middleware = playground_asgi.PlaygroundFreshnessASGIMiddleware(inner)
    with mock.patch.object(playground_asgi, "deadline_for", return_value=deadline):
        async_to_sync(middleware)(scope, receive, send)
    return seen, sent


class TestMiddleware:
    USER = object()

    def test_rule_does_not_apply(self):
        seen, sent = _run({"type": "websocket", "user": self.USER}, None)
        assert seen["user"] is self.USER
        assert sent == []

    def test_expired_sign_in_connects_as_anonymous(self):
        seen, _ = _run({"type": "websocket", "user": self.USER}, 0.0)
        assert isinstance(seen["user"], AnonymousUser)

    def test_open_connection_is_closed_when_the_window_runs_out(self):
        seen, sent = _run(
            {"type": "websocket", "user": self.USER},
            time.time() + 0.2,
            inner_waits=True,
        )
        assert seen["user"] is self.USER
        assert seen["message"] == {
            "type": "websocket.disconnect",
            "code": playground_asgi.CLOSE_SIGN_IN_EXPIRED,
        }
        assert sent == [
            {"type": "websocket.close", "code": playground_asgi.CLOSE_SIGN_IN_EXPIRED}
        ]

    def test_http_passes_through(self):
        with mock.patch.object(playground_asgi, "deadline_for") as deadline:
            seen, _ = _run({"type": "http", "user": self.USER}, None)
        deadline.assert_not_called()
        assert seen["user"] is self.USER
