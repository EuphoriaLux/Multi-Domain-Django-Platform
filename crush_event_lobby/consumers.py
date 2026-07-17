import asyncio

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer


class EventLobbyConsumer(AsyncJsonWebsocketConsumer):
    """Read-only, identity-free refresh channel for Event Lobby members."""

    async def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            await self.close()
            return

        self.event_id = int(self.scope["url_route"]["kwargs"]["event_id"])
        if not await self._can_join(user.pk, self.event_id):
            await self.close()
            return

        self.event_group = f"event_lobby_{self.event_id}"
        self.private_group = f"event_lobby_{self.event_id}_user_{user.pk}"
        await self.channel_layer.group_add(self.event_group, self.channel_name)
        await self.channel_layer.group_add(self.private_group, self.channel_name)
        await self.accept()
        seconds_until_end = await self._seconds_until_end(self.event_id)
        self.phase_task = asyncio.create_task(
            self._close_at_event_end(seconds_until_end)
        )

    async def disconnect(self, close_code):
        phase_task = getattr(self, "phase_task", None)
        if phase_task and phase_task is not asyncio.current_task():
            phase_task.cancel()
        if hasattr(self, "event_group"):
            await self.channel_layer.group_discard(
                self.event_group,
                self.channel_name,
            )
        if hasattr(self, "private_group"):
            await self.channel_layer.group_discard(
                self.private_group,
                self.channel_name,
            )

    async def receive_json(self, content):
        # All mutations remain authenticated HTTP POSTs with CSRF protection.
        return None

    async def lobby_refresh(self, event):
        reason = event.get("reason")
        if reason not in {
            "participant_joined",
            "incoming_signal",
            "mutual_revealed",
            "phase_changed",
        }:
            return
        await self.send_json(
            {
                "type": "event_lobby.refresh",
                "reason": reason,
            }
        )

    async def _close_at_event_end(self, seconds_until_end):
        try:
            await asyncio.sleep(max(0, seconds_until_end))
            await self.send_json(
                {"type": "event_lobby.refresh", "reason": "phase_changed"}
            )
            await self.close(code=1000)
        except asyncio.CancelledError:
            return

    @database_sync_to_async
    def _can_join(self, user_id, event_id):
        from crush_event_lobby.models import EventLobbyParticipation
        from crush_event_lobby.services import _active_member_reason, is_live

        try:
            participation = EventLobbyParticipation.objects.select_related(
                "user",
                "user__crushprofile",
                "user__crush_connect_membership",
                "user__event_lobby_consent",
                "event",
            ).get(event_id=event_id, user_id=user_id)
        except EventLobbyParticipation.DoesNotExist:
            return False
        return (
            is_live(participation.event)
            and _active_member_reason(participation.user) is None
        )

    @database_sync_to_async
    def _seconds_until_end(self, event_id):
        from django.utils import timezone

        from crush_event_lobby.services import event_end_at
        from crush_lu.models import MeetupEvent

        event = MeetupEvent.objects.get(pk=event_id)
        return max(0, (event_end_at(event) - timezone.now()).total_seconds())
