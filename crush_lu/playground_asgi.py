"""Coach playground: the 7-day crush.lu recheck for WebSocket connections.

Spec: ai-memory-hub/specs/2026-11-13-crush-coach-playground.md

WebSockets go through Channels' ``AuthMiddlewareStack`` and never reach
Django's middleware, so ``PlaygroundSSOFreshnessMiddleware`` cannot see them.
This ASGI middleware sits inside the auth stack and applies the same rule to
a staging coach account linked to crush.lu:

* sign-in older than 7 days (or none in this session) at connect time: the
  connection continues as an anonymous user, so coach-only consumers refuse
  it;
* fresh at connect time: the connection is closed (code 4401) when the
  7-day window runs out, even if it stays open past it.

Everyone else, and every slot where the staging sign-in is not configured,
passes straight through.
"""

import asyncio
import time

from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser

#: Close code sent when a linked coach's crush.lu sign-in runs out.
CLOSE_SIGN_IN_EXPIRED = 4401
_EXPIRED = 0.0


def deadline_for(scope):
    """When this connection's crush.lu sign-in runs out (epoch seconds).

    ``None``: the rule does not apply (not configured, not a linked coach).
    ``0.0``: already expired. Synchronous: touches the session and database.
    """
    from crush_lu.services import playground_sso as sso

    if not sso.consumer_enabled():
        return None
    user = scope.get("user")
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    if not sso.is_linked_coach(user):
        return None
    session = scope.get("session")
    signed_in_at = session.get(sso.SIGNED_IN_AT_SESSION_KEY) if session else None
    if not sso.timestamp_fresh(signed_in_at):
        return _EXPIRED
    return float(signed_in_at) + sso.FRESHNESS_SECONDS


class PlaygroundFreshnessASGIMiddleware:
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "websocket":
            return await self.inner(scope, receive, send)
        deadline = await database_sync_to_async(deadline_for)(scope)
        if deadline is None:
            return await self.inner(scope, receive, send)
        if deadline <= time.time():
            return await self.inner(dict(scope, user=AnonymousUser()), receive, send)

        async def expire():
            await send({"type": "websocket.close", "code": CLOSE_SIGN_IN_EXPIRED})
            return {"type": "websocket.disconnect", "code": CLOSE_SIGN_IN_EXPIRED}

        async def guarded_receive():
            remaining = deadline - time.time()
            if remaining <= 0:
                return await expire()
            try:
                return await asyncio.wait_for(receive(), timeout=remaining)
            except asyncio.TimeoutError:
                return await expire()

        return await self.inner(scope, guarded_receive, send)
