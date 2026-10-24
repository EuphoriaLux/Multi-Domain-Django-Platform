"""Two-person video call set-up relay (WebRTC signalling).

The two browsers exchange their offer, answer and ICE candidates through this
consumer; the media itself never reaches the server. It relays and forgets:
nothing is stored, and no payload is ever logged, because SDP and ICE
candidates carry IP addresses.

- authorization on connect only, through the call kind's authorizer
  (``crush_lu/services/video_call.py``); an ongoing call keeps its media
  path, so a lost right ends at the next reconnect;
- only allowlisted message types and fields are relayed, size-capped and
  rate-limited per connection;
- a sender never receives its own message. Two tabs of the same person both
  receive the other side's messages, a known v1 gap the client tolerates by
  pairing with one peer id at a time.

Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md (D12)
"""

import asyncio
import json
import logging
import time

from channels.db import database_sync_to_async

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
# An SDP with many candidates runs to ~10 KB; this leaves headroom.
MAX_MESSAGE_CHARS = 32 * 1024
MAX_MESSAGES_PER_MINUTE = 240


class VideoCallConsumer(BaseCrushWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close()
            return

        kwargs = self.scope["url_route"]["kwargs"]
        self.kind = kwargs["kind"]
        self.object_id = kwargs["object_id"]
        if not await self._can_join(user):
            await self.close()
            return

        from crush_lu.services.video_call import call_group_name

        self.call_group = call_group_name(self.kind, self.object_id)
        if not await self._safe_group_adds(self.call_group):
            await self.close()
            return
        self._window_started = time.monotonic()
        self._window_count = 0
        await self.accept()

    async def disconnect(self, close_code):
        # No "bye" on the person's behalf: a dropped socket (a worker recycle,
        # a network switch) leaves the media flowing and reconnects, and only
        # the page knows when its person really hung up.
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
    def _can_join(self, user):
        from crush_lu.services.video_call import can_join_call

        return can_join_call(user, self.kind, self.object_id)
