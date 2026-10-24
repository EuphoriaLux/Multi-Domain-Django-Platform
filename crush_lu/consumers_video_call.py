"""Two-person video call set-up relay (WebRTC signalling).

The two browsers exchange their offer, answer and ICE candidates through this
consumer; the media itself never reaches the server. It relays and forgets:
nothing is stored, and no payload is ever logged, because SDP and ICE
candidates carry IP addresses.

- authorization on connect only, through the call kind's authorizer
  (``crush_lu/services/video_call.py``), which returns the person's role; an
  ongoing call keeps its media path, so a lost right ends at the next
  reconnect;
- one live socket per role: a newer socket (a reload, a second tab) takes
  the role's slot in the cache and the older one is closed with
  ``CLOSE_REPLACED``, which tells its page not to reconnect;
- only allowlisted message types and fields are relayed, size-capped and
  rate-limited per connection, and a sender never receives its own message.

Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md (D12)
"""

import asyncio
import json
import logging
import time

from channels.db import database_sync_to_async
from django.core.cache import cache

from crush_lu.consumers import BaseCrushWebsocketConsumer

logger = logging.getLogger(__name__)

# Message type -> the fields relayed with it. Anything else is dropped.
RELAYED_FIELDS = {
    "hello": ("id",),
    "offer": ("id", "description"),
    "answer": ("id", "description"),
    "candidate": ("id", "candidate"),
    "bye": ("id",),
}
# Characters, which for SDP and ICE (ASCII) equal bytes. An SDP with many
# candidates runs to ~10 KB; this leaves headroom.
MAX_MESSAGE_CHARS = 32 * 1024
MAX_MESSAGES_PER_MINUTE = 240
# Close code telling a page that a newer socket took its role: do not reconnect.
CLOSE_REPLACED = 4000
# Longer than any call; a slot outliving a crashed worker only means the next
# socket for that role sends a harmless "replaced" to a channel nobody reads.
ROLE_SLOT_SECONDS = 6 * 60 * 60


class VideoCallConsumer(BaseCrushWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close()
            return

        kwargs = self.scope["url_route"]["kwargs"]
        self.kind = kwargs["kind"]
        self.object_id = kwargs["object_id"]
        role = await self._role(user)
        if not role:
            await self.close()
            return

        from crush_lu.services.video_call import call_group_name

        self.call_group = call_group_name(self.kind, self.object_id)
        if not await self._safe_group_adds(self.call_group):
            await self.close()
            return
        self.role_slot = f"{self.call_group}.{role}"
        await self._take_role_slot()
        self._window_started = time.monotonic()
        self._window_count = 0
        await self.accept()

    async def disconnect(self, close_code):
        # No "bye" on the person's behalf: a dropped socket (a worker recycle,
        # a network switch) leaves the media flowing and reconnects, and only
        # the page knows when its person really hung up.
        slot = getattr(self, "role_slot", None)
        if slot and await cache.aget(slot) == self.channel_name:
            await cache.adelete(slot)
        await self._safe_group_discards(getattr(self, "call_group", None))

    async def receive(self, text_data=None, bytes_data=None, **kwargs):
        if not text_data or len(text_data) > MAX_MESSAGE_CHARS:
            return
        if not self._within_rate_limit():
            return
        try:
            content = json.loads(text_data)
        except ValueError:
            return
        await self.receive_json(content)

    async def receive_json(self, content, **kwargs):
        if not isinstance(content, dict):
            return
        fields = RELAYED_FIELDS.get(content.get("type"))
        if fields is None:
            return
        message = {"type": content["type"]}
        for field in fields:
            if field in content:
                message[field] = content[field]
        await self._relay(message)

    async def video_signal(self, event):
        if event.get("sender") == self.channel_name:
            return
        await self.send_json(event["message"])

    async def video_replaced(self, event):
        await self.close(code=CLOSE_REPLACED)

    async def _take_role_slot(self):
        previous = await cache.aget(self.role_slot)
        await cache.aset(self.role_slot, self.channel_name, ROLE_SLOT_SECONDS)
        if not previous or previous == self.channel_name:
            return
        try:
            await asyncio.wait_for(
                self.channel_layer.send(previous, {"type": "video.replaced"}),
                timeout=self.GROUP_OP_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.warning(
                "Video call could not close a replaced socket in %s: %s",
                self.call_group,
                type(exc).__name__,
            )

    def _within_rate_limit(self):
        now = time.monotonic()
        if now - self._window_started >= 60:
            self._window_started = now
            self._window_count = 0
        self._window_count += 1
        return self._window_count <= MAX_MESSAGES_PER_MINUTE

    async def _relay(self, message):
        if not getattr(self, "channel_layer", None):
            return
        try:
            await asyncio.wait_for(
                self.channel_layer.group_send(
                    self.call_group,
                    {
                        "type": "video.signal",
                        "sender": self.channel_name,
                        "message": message,
                    },
                ),
                timeout=self.GROUP_OP_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            # The class only: the message holds the other side's addresses.
            logger.warning(
                "Video call relay failed for %s: %s",
                self.call_group,
                type(exc).__name__,
            )

    @database_sync_to_async
    def _role(self, user):
        from crush_lu.services.video_call import call_role

        return call_role(user, self.kind, self.object_id)
