import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from azureproject.settings import (
    CHANNEL_LAYER_HEALTH_CHECK_INTERVAL,
    CHANNEL_LAYER_SOCKET_CONNECT_TIMEOUT,
    CHANNEL_LAYER_SOCKET_TIMEOUT,
    channel_layer_hosts,
)
from crush_lu.consumers import (
    BaseCrushWebsocketConsumer,
    CacheHuntConsumer,
    CheckinConsumer,
    QuizConsumer,
)
from crush_lu.consumers_event_lobby import EventLobbyConsumer


class TestChannelLayerConfiguration:
    def test_channel_layer_hosts_resilience_settings(self):
        url = "redis://redis.cache.windows.net:6380/0"
        hosts = channel_layer_hosts(url)
        assert len(hosts) == 1
        config = hosts[0]
        assert config["address"] == url
        assert config["socket_timeout"] == CHANNEL_LAYER_SOCKET_TIMEOUT
        assert config["socket_connect_timeout"] == CHANNEL_LAYER_SOCKET_CONNECT_TIMEOUT
        assert config["health_check_interval"] == CHANNEL_LAYER_HEALTH_CHECK_INTERVAL
        assert config["socket_keepalive"] is True
        # Connection-level retry must be omitted to avoid retrying destructive receives (BZPOPMIN)
        assert "retry" not in config
        assert "retry_on_timeout" not in config
        assert "retry_on_error" not in config


class TestWebsocketConsumerSafeDisconnect:
    def test_safe_group_discard_handles_connection_reset(self, caplog):
        consumer = BaseCrushWebsocketConsumer()
        consumer.channel_name = "test_channel_123"
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_discard = AsyncMock(
            side_effect=ConnectionResetError("Connection lost")
        )

        with caplog.at_level(logging.WARNING):
            # Should not raise any exception
            asyncio.run(consumer._safe_group_discard("test_group"))

        assert (
            "Failed to discard group test_group for channel test_channel_123"
            in caplog.text
        )

    def test_safe_group_discard_handles_timeout(self, caplog):
        consumer = BaseCrushWebsocketConsumer()
        consumer.channel_name = "test_channel_timeout"
        consumer.channel_layer = MagicMock()

        async def hanging_discard(group, channel):
            await asyncio.sleep(5.0)

        consumer.channel_layer.group_discard = AsyncMock(side_effect=hanging_discard)

        with caplog.at_level(logging.WARNING):
            # Should time out after 0.1s without hanging the test
            asyncio.run(consumer._safe_group_discard("test_group", timeout=0.1))

        assert (
            "Failed to discard group test_group for channel test_channel_timeout"
            in caplog.text
        )

    def test_safe_group_discards_attempts_all_groups_concurrently_despite_failures(
        self,
    ):
        """Discards all active groups concurrently; failure in one does not skip others."""
        consumer = BaseCrushWebsocketConsumer()
        consumer.channel_name = "test_concurrent_ch"
        consumer.channel_layer = MagicMock()

        calls = []

        async def mock_discard(group, channel):
            calls.append(group)
            if group == "group_fail":
                raise ConnectionResetError("Redis dropped")
            return None

        consumer.channel_layer.group_discard = AsyncMock(side_effect=mock_discard)

        asyncio.run(
            consumer._safe_group_discards("group_fail", "group_ok", None, "group_ok_2")
        )

        assert "group_fail" in calls
        assert "group_ok" in calls
        assert "group_ok_2" in calls
        assert len(calls) == 3

    def test_checkin_consumer_disconnect_with_failing_redis(self):
        consumer = CheckinConsumer()
        consumer.channel_name = "checkin_ch_1"
        consumer.checkin_group = "checkin_42"
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_discard = AsyncMock(
            side_effect=ConnectionResetError("Socket closed")
        )

        # Disconnect should complete cleanly
        asyncio.run(consumer.disconnect(1000))
        consumer.channel_layer.group_discard.assert_called_once_with(
            "checkin_42", "checkin_ch_1"
        )

    def test_quiz_consumer_disconnect_attempts_all_groups_concurrently(self):
        """All 4 groups are attempted concurrently during disconnect even if some fail."""
        consumer = QuizConsumer()
        consumer.channel_name = "quiz_ch_1"
        consumer.quiz_group = "quiz_10"
        consumer.display_group = "quiz_10_display"
        consumer.table_group = "quiz_10_table_1"
        consumer.host_group = "quiz_10_host"
        consumer.channel_layer = MagicMock()

        def side_effect(group, channel):
            if group == "quiz_10":
                raise ConnectionResetError("Connection lost")
            return None

        consumer.channel_layer.group_discard = AsyncMock(side_effect=side_effect)

        asyncio.run(consumer.disconnect(1000))
        assert consumer.channel_layer.group_discard.call_count == 4

    def test_quiz_consumer_disconnect_success_discards_all_groups(self):
        """When Redis is healthy, all groups must be discarded cleanly."""
        consumer = QuizConsumer()
        consumer.channel_name = "quiz_ch_1"
        consumer.quiz_group = "quiz_10"
        consumer.display_group = "quiz_10_display"
        consumer.table_group = "quiz_10_table_1"
        consumer.host_group = "quiz_10_host"
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_discard = AsyncMock()

        asyncio.run(consumer.disconnect(1000))
        assert consumer.channel_layer.group_discard.call_count == 4

    def test_cache_hunt_consumer_disconnect_with_failing_redis(self):
        consumer = CacheHuntConsumer()
        consumer.channel_name = "cache_ch_1"
        consumer.cache_group = "cache_5"
        consumer.cache_coach_group = "cache_5_coach"
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_discard = AsyncMock(
            side_effect=ConnectionResetError("Connection lost")
        )

        asyncio.run(consumer.disconnect(1000))
        assert consumer.channel_layer.group_discard.call_count == 2

    def test_event_lobby_consumer_disconnect_with_failing_redis(self):
        consumer = EventLobbyConsumer()
        consumer.channel_name = "lobby_ch_1"
        consumer.lobby_group = "event_lobby_1"
        consumer.user_group = "event_lobby_1_user_2"
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_discard = AsyncMock(
            side_effect=ConnectionResetError("Connection lost")
        )

        asyncio.run(consumer.disconnect(1000))
        assert consumer.channel_layer.group_discard.call_count == 2

    def test_quiz_rotation_does_not_swallow_group_discard_failure(self):
        """An active socket must not join a new table if leaving the old one fails."""
        consumer = QuizConsumer()
        consumer.scope = {
            "user": MagicMock(id=7, is_authenticated=True),
        }
        consumer.channel_name = "quiz_ch_7"
        consumer.quiz_id = 10
        consumer.table_group = "quiz_10_table_1"
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_discard = AsyncMock(
            side_effect=ConnectionResetError("Connection lost")
        )
        consumer.channel_layer.group_add = AsyncMock()

        event = {"data": {"assignments": {"7": {"table_id": 2}}}}
        with pytest.raises(ConnectionResetError):
            asyncio.run(consumer.quiz_rotate(event))

        consumer.channel_layer.group_add.assert_not_awaited()
        assert consumer.table_group == "quiz_10_table_1"
