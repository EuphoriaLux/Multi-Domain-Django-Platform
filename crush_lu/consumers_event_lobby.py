"""
Member-only Event Lobby WebSocket consumer (spec §11.1).

Deliberately distinct from the Coach-only ``CheckinConsumer`` (which forwards
attendee names and profile data and must never be shared with members). This
consumer is a read-only relay in the ``CacheHuntConsumer`` mould:

- authorization on connect: authenticated + current lobby participation +
  live phase (re-derived from server time);
- ``receive_json`` is a no-op — every write goes over HTTP with CSRF and
  rate limiting;
- event-wide messages are sanitized refetch hints with no identity;
- counter changes and mutual reveals arrive only on the per-user group;
- when WebSockets/Redis are unavailable the client's polling fallback against
  ``lobby_state_api`` preserves correctness (broadcasts are hints, never the
  source of roster or quota truth).

# PROTOTYPE-STUB: eligibility loss *during* a connection (block, exclusion,
# event end) currently downgrades to the HTTP layer — every state fetch and
# write re-authorizes, so a stale socket only receives identity-free hints.
# A real implementation also force-disconnects lobby sockets on those events
# (spec §11.1 "disconnect/denial at event end or eligibility loss").
"""

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer


class EventLobbyConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close()
            return

        self.event_id = int(self.scope["url_route"]["kwargs"]["event_id"])
        if not await self._can_join(user.pk):
            await self.close()
            return

        self.lobby_group = f"event_lobby_{self.event_id}"
        self.user_group = f"event_lobby_{self.event_id}_user_{user.pk}"
        await self.channel_layer.group_add(self.lobby_group, self.channel_name)
        await self.channel_layer.group_add(self.user_group, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        for group in (
            getattr(self, "lobby_group", None),
            getattr(self, "user_group", None),
        ):
            if group:
                await self.channel_layer.group_discard(group, self.channel_name)

    async def receive_json(self, content, **kwargs):
        # Read-only by design (§11.1): all writes go through the HTTP
        # endpoints, which carry CSRF, rate limits, and transactions.
        pass

    @database_sync_to_async
    def _can_join(self, user_id):
        from django.contrib.auth.models import User

        from crush_lu.models import MeetupEvent
        from crush_lu.services.event_lobby import (
            PHASE_LIVE,
            event_lobby_phase,
            lobby_feature_enabled,
            viewer_participation,
        )

        if not lobby_feature_enabled():
            return False
        event = MeetupEvent.objects.filter(
            pk=self.event_id, is_published=True, is_cancelled=False
        ).first()
        if event is None:
            return False
        # §7.6: no live socket membership at/after the exact scheduled end.
        if event_lobby_phase(event) != PHASE_LIVE:
            return False
        user = (
            User.objects.select_related("crushprofile", "crush_connect_membership")
            .filter(pk=user_id)
            .first()
        )
        if user is None:
            return False
        return viewer_participation(user, event) is not None

    # ---- server→client relays (payloads sanitized at the broadcast site) ----

    async def lobby_joined(self, event):
        await self.send_json({"type": "joined", "data": event.get("data", {})})

    async def lobby_counter(self, event):
        await self.send_json({"type": "counter", "data": event.get("data", {})})

    async def lobby_mutual(self, event):
        await self.send_json({"type": "mutual", "data": event.get("data", {})})

    async def lobby_phase(self, event):
        await self.send_json({"type": "phase", "data": event.get("data", {})})
