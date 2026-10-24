"""In-app video calls: relay credentials, who may join, the signalling relay,
the microphone opt-in and the superuser test room.

Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md (D12)
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import requests
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.http import HttpResponse
from django.test import Client, RequestFactory, SimpleTestCase, TestCase
from django.test import override_settings
from django.utils.module_loading import import_string

from azureproject.csp_middleware import PermissionsPolicyMiddleware, allow_microphone
from crush_lu.consumers_video_call import MAX_MESSAGES_PER_MINUTE, VideoCallConsumer
from crush_lu.routing import websocket_urlpatterns
from crush_lu.services import turn_credentials
from crush_lu.services.video_call import (
    VIDEO_CALL_KINDS,
    can_join_call,
    video_call_context,
)

User = get_user_model()

TURN = {
    "CLOUDFLARE_TURN_KEY_ID": "key123",
    "CLOUDFLARE_TURN_KEY_API_TOKEN": "secret-token-value",
}
RELAY = {
    "urls": ["turn:turn.cloudflare.com:3478?transport=udp"],
    "username": "u",
    "credential": "c",
}
CLOUDFLARE_RESPONSE = {
    "iceServers": [
        {"urls": ["stun:stun.cloudflare.com:3478", "stun:stun.cloudflare.com:53"]},
        {
            "urls": [
                "turn:turn.cloudflare.com:3478?transport=udp",
                "turn:turn.cloudflare.com:53?transport=udp",
                "turns:turn.cloudflare.com:443?transport=tcp",
            ],
            "username": "generated-user",
            "credential": "generated-credential",
        },
    ]
}


def _cloudflare_reply(payload=CLOUDFLARE_RESPONSE, status=201):
    reply = MagicMock()
    reply.status_code = status
    reply.json.return_value = payload
    if status >= 400:
        reply.raise_for_status.side_effect = requests.HTTPError(response=reply)
    return reply


class TurnCredentialTests(SimpleTestCase):
    @override_settings(CLOUDFLARE_TURN_KEY_ID="", CLOUDFLARE_TURN_KEY_API_TOKEN="")
    def test_unconfigured_returns_none_without_calling_cloudflare(self):
        with patch.object(turn_credentials.requests, "post") as post:
            self.assertIsNone(turn_credentials.generate_ice_servers())
        post.assert_not_called()

    @override_settings(**TURN, VIDEO_CALL_TURN_TTL_SECONDS=7200)
    def test_mints_credentials_and_drops_port_53(self):
        with patch.object(
            turn_credentials.requests, "post", return_value=_cloudflare_reply()
        ) as post:
            servers = turn_credentials.generate_ice_servers()

        post.assert_called_once_with(
            "https://rtc.live.cloudflare.com/v1/turn/keys/key123"
            "/credentials/generate-ice-servers",
            headers={"Authorization": "Bearer secret-token-value"},
            json={"ttl": 7200},
            timeout=turn_credentials.REQUEST_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            servers,
            [
                {"urls": ["stun:stun.cloudflare.com:3478"]},
                {
                    "urls": [
                        "turn:turn.cloudflare.com:3478?transport=udp",
                        "turns:turn.cloudflare.com:443?transport=tcp",
                    ],
                    "username": "generated-user",
                    "credential": "generated-credential",
                },
            ],
        )

    @override_settings(**TURN)
    def test_failure_returns_none_and_never_logs_the_token(self):
        for failure in (
            {"return_value": _cloudflare_reply(status=401)},
            {"side_effect": requests.Timeout("secret-token-value")},
            {"return_value": _cloudflare_reply(payload={"unexpected": True})},
        ):
            with self.subTest(failure=failure):
                with patch.object(turn_credentials.requests, "post", **failure):
                    with self.assertLogs(turn_credentials.logger, "WARNING") as logs:
                        self.assertIsNone(turn_credentials.generate_ice_servers())
                self.assertNotIn("secret-token-value", "\n".join(logs.output))

    @override_settings(**TURN)
    def test_answer_without_a_relay_server_is_unavailable(self):
        stun_only = {"iceServers": [{"urls": ["stun:stun.cloudflare.com:3478"]}]}
        with patch.object(
            turn_credentials.requests, "post", return_value=_cloudflare_reply(stun_only)
        ):
            with self.assertLogs(turn_credentials.logger, "WARNING"):
                self.assertIsNone(turn_credentials.generate_ice_servers())


class VideoCallContextTests(SimpleTestCase):
    @override_settings(DEBUG=False)
    def test_without_turn_video_is_unavailable_outside_debug(self):
        with patch(
            "crush_lu.services.video_call.generate_ice_servers", return_value=None
        ):
            self.assertEqual(
                video_call_context("staff-test", "room1"),
                {"video_call": {"available": False}},
            )

    @override_settings(DEBUG=True)
    def test_debug_without_turn_connects_directly(self):
        with patch(
            "crush_lu.services.video_call.generate_ice_servers", return_value=None
        ):
            config = video_call_context("staff-test", "room1")["video_call"]["config"]
        self.assertEqual(config["iceServers"], [])
        self.assertEqual(config["iceTransportPolicy"], "all")

    def test_policy_follows_the_setting_and_defaults_to_relay(self):
        for setting, expected in (
            ("relay", "relay"),
            ("all", "all"),
            ("bogus", "relay"),
        ):
            with self.subTest(setting=setting):
                with override_settings(VIDEO_CALL_ICE_TRANSPORT_POLICY=setting):
                    with patch(
                        "crush_lu.services.video_call.generate_ice_servers",
                        return_value=[RELAY],
                    ):
                        call = video_call_context("staff-test", "room1")["video_call"]
                self.assertTrue(call["available"])
                self.assertEqual(
                    call["config"],
                    {
                        "socketPath": "/ws/video-call/staff-test/room1/",
                        "iceServers": [RELAY],
                        "iceTransportPolicy": expected,
                    },
                )

    def test_every_kind_resolves_to_an_authorizer(self):
        for kind, path in VIDEO_CALL_KINDS.items():
            with self.subTest(kind=kind):
                self.assertTrue(callable(import_string(path)))

    def test_socket_route_accepts_only_valid_addresses(self):
        (route,) = [p for p in websocket_urlpatterns if "video-call" in str(p.pattern)]
        match = route.pattern.match("ws/video-call/staff-test/room1/")
        self.assertEqual(match[2], {"kind": "staff-test", "object_id": "room1"})
        for bad in (
            "ws/video-call/Staff/room1/",
            "ws/video-call/staff-test/room_1/",
            "ws/video-call/staff-test/" + "a" * 33 + "/",
        ):
            with self.subTest(path=bad):
                self.assertIsNone(route.pattern.match(bad))


class CanJoinCallTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser("root", "root@example.com", "pw")
        self.member = User.objects.create_user("member", "member@example.com", "pw")

    def test_staff_test_room_admits_superusers_only(self):
        self.assertTrue(can_join_call(self.superuser, "staff-test", "room1"))
        self.assertFalse(can_join_call(self.member, "staff-test", "room1"))

    def test_refuses_inactive_unknown_and_malformed(self):
        inactive = User.objects.create_superuser("gone", "gone@example.com", "pw")
        inactive.is_active = False
        inactive.save()
        self.assertFalse(can_join_call(inactive, "staff-test", "room1"))
        self.assertFalse(can_join_call(self.superuser, "no-such-kind", "room1"))
        self.assertFalse(can_join_call(self.superuser, "staff-test", "room 1"))
        self.assertFalse(can_join_call(self.superuser, "Staff", "room1"))
        self.assertFalse(can_join_call(None, "staff-test", "room1"))


class VideoCallConsumerTests(TestCase):
    """Drives the consumer directly (WebsocketCommunicator pulls in daphne),
    as the quiz consumer tests do."""

    def setUp(self):
        # database_sync_to_async would close the test transaction's connection.
        patcher = patch("channels.db.close_old_connections", lambda *a, **kw: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.superuser = User.objects.create_superuser("root", "root@example.com", "pw")

    def _consumer(self, user, kind="staff-test", object_id="room1", name="chan-a"):
        consumer = VideoCallConsumer()
        consumer.scope = {
            "user": user,
            "url_route": {"kwargs": {"kind": kind, "object_id": object_id}},
        }
        consumer.channel_name = name
        consumer.channel_layer = AsyncMock()
        consumer.accept = AsyncMock()
        consumer.close = AsyncMock()
        consumer.send_json = AsyncMock()
        return consumer

    def _connected(self, **kwargs):
        consumer = self._consumer(self.superuser, **kwargs)
        async_to_sync(consumer.connect)()
        consumer.accept.assert_awaited_once()
        consumer.channel_layer.group_send.reset_mock()
        return consumer

    def test_refuses_anonymous_and_unauthorized_users(self):
        from django.contrib.auth.models import AnonymousUser

        member = User.objects.create_user("member", "member@example.com", "pw")
        for user in (AnonymousUser(), member):
            with self.subTest(user=user):
                consumer = self._consumer(user)
                async_to_sync(consumer.connect)()
                consumer.close.assert_awaited_once()
                consumer.accept.assert_not_awaited()
                consumer.channel_layer.group_add.assert_not_awaited()

    def test_joins_the_call_group(self):
        consumer = self._connected()
        consumer.channel_layer.group_add.assert_awaited_once_with(
            "video_call.staff-test.room1", "chan-a"
        )

    def test_relays_allowlisted_fields_only(self):
        consumer = self._connected()
        description = {"type": "offer", "sdp": "v=0..."}
        async_to_sync(consumer.receive)(
            text_data=json.dumps(
                {"type": "offer", "id": "abc", "description": description, "extra": 1}
            )
        )
        consumer.channel_layer.group_send.assert_awaited_once_with(
            "video_call.staff-test.room1",
            {
                "type": "video.signal",
                "sender": "chan-a",
                "message": {"type": "offer", "id": "abc", "description": description},
            },
        )

    def test_drops_unknown_malformed_and_oversized_messages(self):
        consumer = self._connected()
        for text in (
            json.dumps({"type": "chat", "body": "hi"}),
            json.dumps(["offer"]),
            "not json",
            "",
            json.dumps({"type": "candidate", "candidate": "x" * 40000}),
        ):
            with self.subTest(text=text[:20]):
                async_to_sync(consumer.receive)(text_data=text)
        consumer.channel_layer.group_send.assert_not_awaited()

    def test_rate_limits_each_connection(self):
        consumer = self._connected()
        hello = json.dumps({"type": "hello", "id": "abc"})
        for _ in range(MAX_MESSAGES_PER_MINUTE + 5):
            async_to_sync(consumer.receive)(text_data=hello)
        self.assertEqual(
            consumer.channel_layer.group_send.await_count, MAX_MESSAGES_PER_MINUTE
        )

    def test_never_echoes_to_the_sender(self):
        consumer = self._connected()
        message = {"type": "hello", "id": "abc"}
        async_to_sync(consumer.video_signal)({"sender": "chan-a", "message": message})
        consumer.send_json.assert_not_awaited()
        async_to_sync(consumer.video_signal)({"sender": "chan-b", "message": message})
        consumer.send_json.assert_awaited_once_with(message)

    def test_disconnect_leaves_the_group_without_speaking_for_the_person(self):
        consumer = self._connected()
        async_to_sync(consumer.disconnect)(1006)
        consumer.channel_layer.group_discard.assert_awaited_once_with(
            "video_call.staff-test.room1", "chan-a"
        )
        consumer.channel_layer.group_send.assert_not_awaited()


class MicrophonePermissionTests(SimpleTestCase):
    def _policy(self, response):
        request = RequestFactory().get("/en/")
        middleware = PermissionsPolicyMiddleware(lambda _request: response)
        return middleware(request)["Permissions-Policy"]

    def test_microphone_stays_off_by_default(self):
        policy = self._policy(HttpResponse())
        self.assertIn("microphone=()", policy)
        self.assertIn("camera=(self)", policy)

    def test_a_view_can_open_the_microphone_for_its_own_response(self):
        policy = self._policy(allow_microphone(HttpResponse()))
        self.assertIn("microphone=(self)", policy)
        self.assertNotIn("microphone=()", policy)
        self.assertEqual(
            policy.replace("microphone=(self)", "microphone=()"),
            PermissionsPolicyMiddleware.PERMISSIONS_POLICY,
        )


@override_settings(DEBUG=False)
class VideoCallTestPageTests(TestCase):
    path = "/en/coach/video-call-test/room1/"

    def setUp(self):
        from crush_lu.tests.test_ux_wave3_profile_wizard import _grant_consent

        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")
        self.superuser = User.objects.create_superuser("root", "root@example.com", "pw")
        _grant_consent(self.superuser)

    def test_requires_login(self):
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 302)

    def test_hidden_from_everyone_but_superusers(self):
        from crush_lu.tests.test_ux_wave3_profile_wizard import _grant_consent

        member = User.objects.create_user("member", "member@example.com", "pw")
        _grant_consent(member)
        self.client.force_login(member)
        self.assertEqual(self.client.get(self.path).status_code, 404)

    def test_rejects_a_malformed_room(self):
        self.client.force_login(self.superuser)
        response = self.client.get("/en/coach/video-call-test/room_1/")
        self.assertEqual(response.status_code, 404)

    def test_renders_the_call_with_fresh_credentials(self):
        self.client.force_login(self.superuser)
        with patch(
            "crush_lu.services.video_call.generate_ice_servers", return_value=[RELAY]
        ):
            response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('x-data="videoCall"', html)
        self.assertIn('id="video-call-config"', html)
        self.assertIn("/ws/video-call/staff-test/room1/", html)
        self.assertIn("microphone=(self)", response["Permissions-Policy"])
        self.assertIn("no-store", response["Cache-Control"])

    def test_without_turn_it_says_video_is_unavailable(self):
        self.client.force_login(self.superuser)
        with patch(
            "crush_lu.services.video_call.generate_ice_servers", return_value=None
        ):
            response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('x-data="videoCall"', response.content.decode())
        self.assertContains(response, "Video calls are not available right now.")

    def test_inside_the_app_it_points_to_the_browser(self):
        self.client.force_login(self.superuser)
        with patch(
            "crush_lu.services.video_call.generate_ice_servers", return_value=[RELAY]
        ):
            response = self.client.get(self.path, HTTP_X_CRUSH_CLIENT="ios-app")
        self.assertNotIn('x-data="videoCall"', response.content.decode())
        self.assertContains(response, "Open this page in your browser")
