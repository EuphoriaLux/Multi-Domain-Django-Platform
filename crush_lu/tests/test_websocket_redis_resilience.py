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


class TestWebsocketConsumerSafeConnect:
    """Connect()-side counterpart to TestWebsocketConsumerSafeDisconnect.

    Reproduces the prod incident: a Redis TCP connection reset mid
    ``group_add`` propagated out of ``CheckinConsumer.connect()`` as an
    unhandled ``ConnectionError``, which Channels surfaced to the ASGI
    server as "Exception in ASGI application" / "connection rejected
    (500 Internal Server Error)" instead of a clean reject the client's
    existing backoff-reconnect loop could recover from.
    """

    def test_safe_group_add_handles_connection_reset(self, caplog):
        consumer = BaseCrushWebsocketConsumer()
        consumer.channel_name = "test_channel_123"
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_add = AsyncMock(
            side_effect=ConnectionResetError("Connection lost")
        )

        with caplog.at_level(logging.WARNING):
            # Should not raise any exception
            result = asyncio.run(consumer._safe_group_add("test_group"))

        assert result is False
        assert (
            "Failed to add group test_group for channel test_channel_123" in caplog.text
        )

    def test_safe_group_add_handles_timeout(self, caplog):
        consumer = BaseCrushWebsocketConsumer()
        consumer.channel_name = "test_channel_timeout"
        consumer.channel_layer = MagicMock()

        async def hanging_add(group, channel):
            await asyncio.sleep(5.0)

        consumer.channel_layer.group_add = AsyncMock(side_effect=hanging_add)

        with caplog.at_level(logging.WARNING):
            # Should time out after 0.1s without hanging the test
            result = asyncio.run(consumer._safe_group_add("test_group", timeout=0.1))

        assert result is False
        assert (
            "Failed to add group test_group for channel test_channel_timeout"
            in caplog.text
        )

    def test_safe_group_add_success(self):
        consumer = BaseCrushWebsocketConsumer()
        consumer.channel_name = "test_channel_ok"
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_add = AsyncMock()

        result = asyncio.run(consumer._safe_group_add("test_group"))

        assert result is True
        consumer.channel_layer.group_add.assert_awaited_once_with(
            "test_group", "test_channel_ok"
        )

    def test_safe_group_adds_all_succeed(self):
        consumer = BaseCrushWebsocketConsumer()
        consumer.channel_name = "ch1"
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_add = AsyncMock()

        result = asyncio.run(consumer._safe_group_adds("g1", None, "g2"))

        assert result is True
        assert consumer.channel_layer.group_add.await_count == 2

    def test_safe_group_adds_empty_returns_true(self):
        """No groups to join is a vacuous success, so callers can uniformly
        gate accept() on the return value without a special-case."""
        consumer = BaseCrushWebsocketConsumer()
        consumer.channel_name = "ch_empty"
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_add = AsyncMock()

        result = asyncio.run(consumer._safe_group_adds(None, None))

        assert result is True
        consumer.channel_layer.group_add.assert_not_awaited()

    def test_safe_group_adds_unwinds_already_joined_groups_on_failure(self):
        """A socket must never end up subscribed to only some of its groups:
        if a later group fails, whatever already joined is discarded and the
        whole call reports failure, so the caller rejects the connection
        cleanly instead of leaving it half-subscribed."""
        consumer = BaseCrushWebsocketConsumer()
        consumer.channel_name = "ch2"
        consumer.channel_layer = MagicMock()

        add_calls = []

        async def mock_add(group, channel):
            add_calls.append(group)
            if group == "g2":
                raise ConnectionResetError("Redis dropped")

        consumer.channel_layer.group_add = AsyncMock(side_effect=mock_add)
        consumer.channel_layer.group_discard = AsyncMock()

        result = asyncio.run(consumer._safe_group_adds("g1", "g2", "g3"))

        assert result is False
        # Stops at the first failure — never attempts g3 on an already-broken connection.
        assert add_calls == ["g1", "g2"]
        consumer.channel_layer.group_discard.assert_awaited_once_with("g1", "ch2")

    def test_checkin_consumer_connect_closes_cleanly_when_redis_down(self, caplog):
        consumer = CheckinConsumer()
        consumer.channel_name = "checkin_ch_new"
        consumer.scope = {
            "user": MagicMock(id=5, is_authenticated=True),
            "url_route": {"kwargs": {"event_id": "38"}},
        }
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_add = AsyncMock(
            side_effect=ConnectionResetError("Connection lost")
        )
        consumer._is_coach = AsyncMock(return_value=True)
        consumer.close = AsyncMock()
        consumer.accept = AsyncMock()

        with caplog.at_level(logging.WARNING):
            # Should not raise — the prod incident was an unhandled
            # ConnectionError propagating out of connect() as a 500.
            asyncio.run(consumer.connect())

        consumer.accept.assert_not_awaited()
        consumer.close.assert_awaited_once()

    def test_checkin_consumer_connect_succeeds_when_redis_healthy(self):
        consumer = CheckinConsumer()
        consumer.channel_name = "checkin_ch_ok"
        consumer.scope = {
            "user": MagicMock(id=5, is_authenticated=True),
            "url_route": {"kwargs": {"event_id": "38"}},
        }
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_add = AsyncMock()
        consumer._is_coach = AsyncMock(return_value=True)
        consumer.close = AsyncMock()
        consumer.accept = AsyncMock()

        asyncio.run(consumer.connect())

        consumer.accept.assert_awaited_once()
        consumer.close.assert_not_awaited()
        assert consumer.checkin_group == "checkin_38"

    def test_cache_hunt_consumer_connect_closes_cleanly_when_redis_down(self):
        consumer = CacheHuntConsumer()
        consumer.channel_name = "cache_ch_new"
        consumer.scope = {
            "user": MagicMock(id=9, is_authenticated=True),
            "url_route": {"kwargs": {"hunt_id": "5"}},
        }
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_add = AsyncMock(
            side_effect=ConnectionResetError("Connection lost")
        )
        consumer._can_join = AsyncMock(return_value=True)
        consumer._cache_is_host = AsyncMock(return_value=False)
        consumer.close = AsyncMock()
        consumer.accept = AsyncMock()

        asyncio.run(consumer.connect())

        consumer.accept.assert_not_awaited()
        consumer.close.assert_awaited_once()

    def test_quiz_consumer_connect_closes_cleanly_when_redis_down(self):
        """A Redis hiccup while joining quiz_group/host_group/table_group
        must reject the connection instead of crashing connect()."""
        consumer = QuizConsumer()
        consumer.channel_name = "quiz_ch_new"
        consumer.scope = {
            "user": MagicMock(id=3, is_authenticated=True),
            "url_route": {"kwargs": {"quiz_id": "10"}},
        }
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_add = AsyncMock(
            side_effect=ConnectionResetError("Connection lost")
        )
        consumer.is_host = AsyncMock(return_value=False)
        consumer._is_staff_viewer = AsyncMock(return_value=False)
        consumer._has_event_registration = AsyncMock(return_value=True)
        consumer.get_user_table_id = AsyncMock(return_value=None)
        consumer.close = AsyncMock()
        consumer.accept = AsyncMock()

        asyncio.run(consumer.connect())

        consumer.accept.assert_not_awaited()
        consumer.close.assert_awaited_once()

    def test_event_lobby_consumer_connect_closes_cleanly_when_redis_down(self):
        consumer = EventLobbyConsumer()
        consumer.channel_name = "lobby_ch_new"
        consumer.scope = {
            "user": MagicMock(pk=2, is_authenticated=True),
            "url_route": {"kwargs": {"event_id": "1"}},
        }
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_add = AsyncMock(
            side_effect=ConnectionResetError("Connection lost")
        )
        consumer._can_join = AsyncMock(return_value=True)
        consumer.close = AsyncMock()
        consumer.accept = AsyncMock()

        asyncio.run(consumer.connect())

        consumer.accept.assert_not_awaited()
        consumer.close.assert_awaited_once()
