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

    async def disconnect(self, close_code):
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
