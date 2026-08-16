import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import redis.exceptions
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
        assert config["retry_on_timeout"] is True
        assert redis.exceptions.ConnectionError in config["retry_on_error"]
        assert redis.exceptions.TimeoutError in config["retry_on_error"]
        assert ConnectionResetError in config["retry_on_error"]
        assert config["retry"] is not None


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

    def test_safe_group_discard_handles_redis_connection_error(self, caplog):
        consumer = BaseCrushWebsocketConsumer()
        consumer.channel_name = "test_channel_456"
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_discard = AsyncMock(
            side_effect=redis.exceptions.ConnectionError("Error writing to socket")
        )

        with caplog.at_level(logging.WARNING):
            asyncio.run(consumer._safe_group_discard("test_group_456"))

        assert (
            "Failed to discard group test_group_456 for channel test_channel_456"
            in caplog.text
        )

    def test_checkin_consumer_disconnect_with_failing_redis(self):
        consumer = CheckinConsumer()
        consumer.channel_name = "checkin_ch_1"
        consumer.checkin_group = "checkin_42"
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_discard = AsyncMock(
            side_effect=redis.exceptions.ConnectionError("Socket closed")
        )

        # Disconnect should complete cleanly
        asyncio.run(consumer.disconnect(1000))
        consumer.channel_layer.group_discard.assert_called_once_with(
            "checkin_42", "checkin_ch_1"
        )

    def test_quiz_consumer_disconnect_with_failing_redis(self):
        consumer = QuizConsumer()
        consumer.channel_name = "quiz_ch_1"
        consumer.quiz_group = "quiz_10"
        consumer.display_group = "quiz_10_display"
        consumer.table_group = "quiz_10_table_1"
        consumer.host_group = "quiz_10_host"
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_discard = AsyncMock(
            side_effect=ConnectionResetError("Connection lost")
        )

        # Disconnect should attempt all groups despite errors on each
        asyncio.run(consumer.disconnect(1000))
        assert consumer.channel_layer.group_discard.call_count == 4

    def test_cache_hunt_consumer_disconnect_with_failing_redis(self):
        consumer = CacheHuntConsumer()
        consumer.channel_name = "cache_ch_1"
        consumer.cache_group = "cache_5"
        consumer.cache_coach_group = "cache_5_coach"
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_discard = AsyncMock(
            side_effect=redis.exceptions.ConnectionError("Connection lost")
        )

        # Disconnect should attempt both groups
        asyncio.run(consumer.disconnect(1000))
        assert consumer.channel_layer.group_discard.call_count == 2

    def test_event_lobby_consumer_disconnect_with_failing_redis(self):
        consumer = EventLobbyConsumer()
        consumer.channel_name = "lobby_ch_1"
        consumer.lobby_group = "event_lobby_1"
        consumer.user_group = "event_lobby_1_user_2"
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_discard = AsyncMock(
            side_effect=redis.exceptions.ConnectionError("Connection lost")
        )

        # Disconnect should attempt both groups
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
