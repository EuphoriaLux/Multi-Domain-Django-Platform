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
from crush_lu.consumers_video_call import (
    CLOSE_REPLACED,
    MAX_MESSAGES_PER_MINUTE,
    VideoCallConsumer,
)
from crush_lu.routing import websocket_urlpatterns
from crush_lu.services import turn_credentials
from crush_lu.services.video_call import (
    VIDEO_CALL_KINDS,
    call_role,
    ice_servers_payload,
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


def _deny_call(user, object_id):
    return None


def _bad_role_call(user, object_id):
    return "Not A Role!"


TEST_KINDS = {
    **VIDEO_CALL_KINDS,
    "deny-test": "crush_lu.tests.test_video_call._deny_call",
    "bad-role-test": "crush_lu.tests.test_video_call._bad_role_call",
}


class VideoCallContextTests(SimpleTestCase):
    @override_settings(DEBUG=False, CLOUDFLARE_TURN_KEY_ID="")
    def test_without_turn_video_is_unavailable_outside_debug(self):
        self.assertEqual(
            video_call_context("staff-test", "room1"),
            {"video_call": {"available": False}},
        )

    @override_settings(DEBUG=False, **TURN)
    def test_the_page_holds_the_socket_path_but_no_credentials(self):
        with patch("crush_lu.services.video_call.generate_ice_servers") as mint:
            call = video_call_context("staff-test", "room1")["video_call"]
        mint.assert_not_called()
        self.assertEqual(
            call,
            {
                "available": True,
                "kind": "staff-test",
                "object_id": "room1",
                "config": {"socketPath": "/ws/video-call/staff-test/room1/"},
            },
        )

    @override_settings(DEBUG=True)
    def test_debug_without_turn_connects_directly(self):
        with patch(
            "crush_lu.services.video_call.generate_ice_servers", return_value=None
        ):
            self.assertEqual(
                ice_servers_payload(), {"iceServers": [], "iceTransportPolicy": "all"}
            )

    @override_settings(DEBUG=False)
    def test_without_relay_credentials_there_is_no_payload(self):
        with patch(
            "crush_lu.services.video_call.generate_ice_servers", return_value=None
        ):
            self.assertIsNone(ice_servers_payload())

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
                        payload = ice_servers_payload()
                self.assertEqual(
                    payload, {"iceServers": [RELAY], "iceTransportPolicy": expected}
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


@patch.dict("crush_lu.services.video_call.VIDEO_CALL_KINDS", TEST_KINDS)
class CallRoleTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser("root", "root@example.com", "pw")
        self.member = User.objects.create_user("member", "member@example.com", "pw")

    def test_staff_test_room_gives_each_superuser_a_role(self):
        self.assertEqual(
            call_role(self.superuser, "staff-test", "room1"),
            f"user-{self.superuser.pk}",
        )
        self.assertIsNone(call_role(self.member, "staff-test", "room1"))

    def test_an_authorizer_refusal_or_bad_role_refuses(self):
        self.assertIsNone(call_role(self.superuser, "deny-test", "room1"))
        self.assertIsNone(call_role(self.superuser, "bad-role-test", "room1"))

    def test_refuses_inactive_unknown_and_malformed(self):
        inactive = User.objects.create_superuser("gone", "gone@example.com", "pw")
        inactive.is_active = False
        inactive.save()
        self.assertIsNone(call_role(inactive, "staff-test", "room1"))
        self.assertIsNone(call_role(self.superuser, "no-such-kind", "room1"))
        self.assertIsNone(call_role(self.superuser, "staff-test", "room 1"))
        self.assertIsNone(call_role(self.superuser, "Staff", "room1"))
        self.assertIsNone(call_role(None, "staff-test", "room1"))


@patch.dict("crush_lu.services.video_call.VIDEO_CALL_KINDS", TEST_KINDS)
class VideoCallConsumerTests(TestCase):
    """Drives the consumer directly (WebsocketCommunicator pulls in daphne),
    as the quiz consumer tests do."""

    def setUp(self):
        cache.clear()
        # database_sync_to_async would close the test transaction's connection.
        patcher = patch("channels.db.close_old_connections", lambda *a, **kw: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.superuser = User.objects.create_superuser("root", "root@example.com", "pw")
        self.slot = f"video_call.staff-test.room1.user-{self.superuser.pk}"

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

    def _connected(self, name="chan-a"):
        consumer = self._consumer(self.superuser, name=name)
        async_to_sync(consumer.connect)()
        consumer.accept.assert_awaited_once()
        consumer.channel_layer.group_send.reset_mock()
        return consumer

    def test_refuses_anonymous_unauthorized_and_refused_users(self):
        from django.contrib.auth.models import AnonymousUser

        member = User.objects.create_user("member", "member@example.com", "pw")
        for user, kind in (
            (AnonymousUser(), "staff-test"),
            (member, "staff-test"),
            (self.superuser, "deny-test"),
        ):
            with self.subTest(user=user, kind=kind):
                consumer = self._consumer(user, kind=kind)
                async_to_sync(consumer.connect)()
                consumer.close.assert_awaited_once()
                consumer.accept.assert_not_awaited()
                consumer.channel_layer.group_add.assert_not_awaited()

    def test_joins_the_call_group_and_takes_its_role_slot(self):
        consumer = self._connected()
        consumer.channel_layer.group_add.assert_awaited_once_with(
            "video_call.staff-test.room1", "chan-a"
        )
        self.assertEqual(cache.get(self.slot), "chan-a")
        consumer.channel_layer.send.assert_not_awaited()

    def test_a_newer_socket_for_the_same_role_replaces_the_older(self):
        older = self._connected("chan-a")
        newer = self._connected("chan-b")
        self.assertEqual(cache.get(self.slot), "chan-b")
        newer.channel_layer.send.assert_awaited_once_with(
            "chan-a", {"type": "video.replaced"}
        )
        async_to_sync(older.video_replaced)({"type": "video.replaced"})
        older.close.assert_awaited_once_with(code=CLOSE_REPLACED)
        # The replaced socket's disconnect must not free the newer one's slot.
        async_to_sync(older.disconnect)(CLOSE_REPLACED)
        self.assertEqual(cache.get(self.slot), "chan-b")
        async_to_sync(newer.disconnect)(1000)
        self.assertIsNone(cache.get(self.slot))

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
            # Over MAX_MESSAGE_CHARS (characters; SDP and ICE are ASCII).
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

    def test_disconnect_leaves_without_speaking_for_the_person(self):
        consumer = self._connected()
        async_to_sync(consumer.disconnect)(1006)
        consumer.channel_layer.group_discard.assert_awaited_once_with(
            "video_call.staff-test.room1", "chan-a"
        )
        consumer.channel_layer.group_send.assert_not_awaited()
        self.assertIsNone(cache.get(self.slot))


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


def _logged_in_superuser(test):
    from crush_lu.tests.test_ux_wave3_profile_wizard import _grant_consent

    cache.clear()
    test.client = Client(HTTP_HOST="crush.lu")
    test.superuser = User.objects.create_superuser("root", "root@example.com", "pw")
    _grant_consent(test.superuser)


@override_settings(DEBUG=False, **TURN)
class VideoCallTestPageTests(TestCase):
    path = "/en/coach/video-call-test/room1/"

    def setUp(self):
        _logged_in_superuser(self)

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

    def test_renders_the_call_without_credentials(self):
        self.client.force_login(self.superuser)
        with patch("crush_lu.services.video_call.generate_ice_servers") as mint:
            response = self.client.get(self.path)
        mint.assert_not_called()
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('x-data="videoCall"', html)
        self.assertIn("/ws/video-call/staff-test/room1/", html)
        self.assertIn(
            'data-ice-url="/en/video-call/staff-test/room1/ice-servers/"', html
        )
        self.assertIn("microphone=(self)", response["Permissions-Policy"])

    @override_settings(CLOUDFLARE_TURN_KEY_ID="")
    def test_without_turn_it_says_video_is_unavailable(self):
        self.client.force_login(self.superuser)
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('x-data="videoCall"', response.content.decode())
        self.assertContains(response, "Video calls are not available right now.")

    def test_inside_the_app_it_points_to_the_browser(self):
        self.client.force_login(self.superuser)
        response = self.client.get(self.path, HTTP_X_CRUSH_CLIENT="ios-app")
        self.assertNotIn('x-data="videoCall"', response.content.decode())
        self.assertContains(response, "Open this page in your browser")


@override_settings(DEBUG=False, **TURN)
class IceServersEndpointTests(TestCase):
    path = "/en/video-call/staff-test/room1/ice-servers/"

    def setUp(self):
        _logged_in_superuser(self)

    def test_requires_login_and_post(self):
        self.assertEqual(self.client.post(self.path).status_code, 302)
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(self.path).status_code, 405)

    def test_refuses_people_who_are_not_in_the_call(self):
        from crush_lu.tests.test_ux_wave3_profile_wizard import _grant_consent

        member = User.objects.create_user("member", "member@example.com", "pw")
        _grant_consent(member)
        self.client.force_login(member)
        with patch("crush_lu.services.video_call.generate_ice_servers") as mint:
            self.assertEqual(self.client.post(self.path).status_code, 404)
        mint.assert_not_called()

    def test_mints_fresh_credentials_for_the_call(self):
        self.client.force_login(self.superuser)
        with patch(
            "crush_lu.services.video_call.generate_ice_servers", return_value=[RELAY]
        ):
            response = self.client.post(self.path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(), {"iceServers": [RELAY], "iceTransportPolicy": "relay"}
        )
        self.assertIn("no-store", response["Cache-Control"])

    def test_reports_the_relay_unavailable(self):
        self.client.force_login(self.superuser)
        with patch(
            "crush_lu.services.video_call.generate_ice_servers", return_value=None
        ):
            response = self.client.post(self.path)
        self.assertEqual(response.status_code, 503)
