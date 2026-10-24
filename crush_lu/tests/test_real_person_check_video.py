"""The real-person check in the in-app video call layer (PR 3b, wiring).

The generic layer (``services/video_call.py``, the signalling consumer, the
relay-credentials endpoint and the call partial) is covered by
``test_video_call.py``. This file covers what the real-person check adds to it:

* ``authorize_video_call``, the authorizer behind the ``real-person-check``
  kind: who may join, and every case in which the call must not exist;
* the same rule at the video layer's three doors (``call_role``, the
  relay-credentials endpoint, the signalling consumer);
* the coach check page, which hosts the call and opens the microphone for that
  page only;
* the member's call page, and the two ways to it (the coach's text message and
  the hub card).

Requests use literal ``/en/`` paths (AGENTS.md: ``reverse()`` resolves the
default urlconf, not the one the host selects).

Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md (D12)
"""

import re
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import unquote

from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone, translation
from django.utils.translation import gettext

from crush_lu.consumers_video_call import VideoCallConsumer
from crush_lu.models import (
    ConnectRealPersonCheck,
    CrushCoach,
    CrushProfile,
    EmailPreference,
    ProfilePhotoReviewState,
    PublishedProfilePhoto,
    UserBlock,
    UserDataConsent,
    UserReport,
)
from crush_lu.services.real_person_check import (
    CALL_KIND,
    authorize_video_call,
    claim_check,
    member_call_check,
)
from crush_lu.services.video_call import (
    VIDEO_CALL_KINDS,
    call_role,
    valid_call_address,
)
from crush_lu.tests.real_person_check_helpers import (
    make_coach,
    make_member,
    profile_of,
)
from crush_lu.tests.test_crush_connect import _grant_consent
from crush_lu.tests.test_video_call import RELAY, TURN, _cloudflare_reply

BASE = "/en/coach/real-person-checks/"
SECRET = TURN["CLOUDFLARE_TURN_KEY_API_TOKEN"]

LIVE_FLAGS = {
    "ROOT_URLCONF": "azureproject.urls_crush",
    "CONNECT_REAL_PERSON_CHECK_ENABLED": True,
    "CONNECT_REAL_PERSON_BADGE_VISIBLE": True,
    "CRUSH_CONNECT_LAUNCHED": True,
    "CRUSH_CONNECT_CANDIDATE_OPEN": True,
}
# DEBUG off, like production: video needs the relay settings.
WITH_TURN = {"DEBUG": False, **TURN}
WITHOUT_TURN = {
    "DEBUG": False,
    "CLOUDFLARE_TURN_KEY_ID": "",
    "CLOUDFLARE_TURN_KEY_API_TOKEN": "",
}

PHONE = "+352 621 123 456"


def _call_page(check):
    return f"/en/crush-connect/real-person-call/{check.pk}/"


def _ice_url(check):
    return f"/en/video-call/{CALL_KIND}/{check.pk}/ice-servers/"


class VideoCheckTestCase(TestCase):
    """A coach, a catalogue member and a live check between them, plus a second
    coach and a second member who are in no call."""

    def setUp(self):
        # Settings that every test of this file shares, enabled first.
        for override in (
            override_settings(**LIVE_FLAGS),
            override_settings(**WITH_TURN),
        ):
            override.enable()
            self.addCleanup(override.disable)
        cache.clear()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )
        self.client.defaults["HTTP_HOST"] = "crush.lu"
        self.coach = make_coach("rpcvid_coach")
        self.other_coach = make_coach("rpcvid_other_coach")
        for coach in (self.coach, self.other_coach):
            _grant_consent(coach.user)
        self.member = make_member("rpcvid_member")
        self.other_member = make_member("rpcvid_other_member")
        CrushProfile.objects.filter(user=self.member).update(
            phone_number=PHONE, phone_verified=True
        )
        self.superuser = type(self.member).objects.create_superuser(
            "rpcvid_root", "rpcvid_root@example.com", "pw12345678"
        )
        _grant_consent(self.superuser)
        self.check = claim_check(self.coach, profile_of(self.member).pk)

    def update_check(self, **fields):
        ConnectRealPersonCheck.objects.filter(pk=self.check.pk).update(**fields)

    def expire(self):
        self.update_check(lease_expires_at=timezone.now() - timedelta(minutes=1))

    def role(self, user):
        return authorize_video_call(user, str(self.check.pk))

    def block_publication(self):
        """Photo 1 is waiting for review while an earlier file stays published:
        a video badge is not possible, a conversation still is."""
        ProfilePhotoReviewState.objects.filter(profile__user=self.member).update(
            status="pending"
        )
        PublishedProfilePhoto.objects.create(
            profile=profile_of(self.member),
            photo_field="photo_1",
            photo_key="users/earlier/photos/earlier.jpg",
        )

    def both_refused(self):
        return (self.role(self.coach.user), self.role(self.member))


# ---------------------------------------------------------------------------
# The authorizer
# ---------------------------------------------------------------------------


class AuthorizerTests(VideoCheckTestCase):
    def test_the_checks_coach_and_member_get_their_roles(self):
        self.assertEqual(self.role(self.coach.user), "coach")
        self.assertEqual(self.role(self.member), "member")

    def test_everyone_else_is_refused(self):
        for who, user in (
            ("another coach", self.other_coach.user),
            ("another member", self.other_member),
            ("a superuser", self.superuser),
            ("an anonymous visitor", AnonymousUser()),
            ("nobody", None),
        ):
            with self.subTest(who):
                self.assertIsNone(self.role(user))

    def test_an_inactive_user_is_refused(self):
        type(self.member).objects.filter(pk=self.member.pk).update(is_active=False)
        self.member.refresh_from_db()
        self.assertIsNone(self.role(self.member))
        # The coach-side guard stops the pair, so the coach is out as well.
        self.assertIsNone(self.role(self.coach.user))

    def test_an_inactive_coach_account_is_refused(self):
        CrushCoach.objects.filter(pk=self.coach.pk).update(is_active=False)
        self.assertEqual(self.both_refused(), (None, None))

    def test_the_tooling_flag_off_refuses_everyone(self):
        with override_settings(CONNECT_REAL_PERSON_CHECK_ENABLED=False):
            self.assertEqual(self.both_refused(), (None, None))
        # The badge flag alone opens nothing.
        with override_settings(
            CONNECT_REAL_PERSON_CHECK_ENABLED=False,
            CONNECT_REAL_PERSON_BADGE_VISIBLE=True,
        ):
            self.assertEqual(self.both_refused(), (None, None))

    def test_a_closed_check_refuses_everyone_for_each_terminal_status(self):
        terminal = [
            status
            for status, _label in ConnectRealPersonCheck.STATUS_CHOICES
            if status != ConnectRealPersonCheck.STATUS_OPEN
        ]
        self.assertEqual(len(terminal), 6)
        for status in terminal:
            with self.subTest(status):
                # The lease is still in the future: the status alone closes it.
                self.update_check(
                    status=status,
                    medium="voice" if status == "passed" else "",
                )
                self.assertEqual(self.both_refused(), (None, None))

    def test_a_lapsed_lease_refuses_everyone(self):
        self.expire()
        self.assertEqual(self.both_refused(), (None, None))
        self.update_check(lease_expires_at=None)
        self.assertEqual(self.both_refused(), (None, None))

    def test_a_lease_that_is_still_running_is_enough(self):
        self.update_check(lease_expires_at=timezone.now() + timedelta(seconds=30))
        self.assertEqual(self.both_refused(), ("coach", "member"))

    def test_a_check_whose_coach_was_removed_is_refused(self):
        self.update_check(coach=None)
        self.assertEqual(self.both_refused(), (None, None))

    def test_a_coach_cannot_call_themself(self):
        # A self-check cannot be claimed, but a stale row must still be refused.
        user = make_member("rpcvid_selfie")
        coach = CrushCoach.objects.create(user=user, bio="coach", is_active=True)
        own_check = ConnectRealPersonCheck.objects.create(
            profile=profile_of(user),
            coach=coach,
            lease_expires_at=timezone.now() + timedelta(hours=1),
        )
        self.assertIsNone(authorize_video_call(user, str(own_check.pk)))

    def test_every_guard_that_rules_out_the_pair_closes_the_call(self):
        def block_by_coach():
            UserBlock.objects.create(blocker=self.coach.user, blocked=self.member)

        def block_by_member():
            UserBlock.objects.create(blocker=self.member, blocked=self.coach.user)

        def banned():
            UserDataConsent.objects.filter(user=self.member).update(crushlu_banned=True)

        def no_consent():
            UserDataConsent.objects.filter(user=self.member).update(
                crushlu_consent_given=False
            )

        def fake_report_pending():
            UserReport.objects.create(
                reporter=self.other_member,
                reported_user=self.member,
                reason="fake_profile",
                status="open",
            )

        def left_the_cohort():
            membership = self.member.crush_connect_membership
            membership.paused_at = timezone.now()
            membership.save(update_fields=["paused_at"])

        def profile_inactive():
            CrushProfile.objects.filter(user=self.member).update(is_active=False)

        def already_verified():
            from crush_lu.models import ConnectRealPersonBadge

            profile = profile_of(self.member)
            passed = ConnectRealPersonCheck.objects.create(
                profile=profile,
                coach=self.other_coach,
                status="passed",
                medium="voice",
            )
            ConnectRealPersonBadge.objects.create(
                profile=profile,
                source_check=passed,
                medium="voice",
                profile_approved_at=profile.approved_at,
            )

        for name, apply in (
            ("blocked by the coach", block_by_coach),
            ("blocked by the member", block_by_member),
            ("banned", banned),
            ("no Crush.lu consent", no_consent),
            ("fake report pending", fake_report_pending),
            ("left the cohort", left_the_cohort),
            ("profile inactive", profile_inactive),
            ("already has a badge", already_verified),
        ):
            with self.subTest(name):
                # Each case starts from a fresh, working call.
                self.reset_guards()
                self.assertEqual(self.both_refused(), ("coach", "member"))
                apply()
                self.assertEqual(self.both_refused(), (None, None))

    def reset_guards(self):
        """Undo whatever a previous guard case changed to the pair."""
        UserBlock.objects.all().delete()
        UserReport.objects.all().delete()
        UserDataConsent.objects.filter(user=self.member).update(
            crushlu_banned=False, crushlu_consent_given=True
        )
        CrushProfile.objects.filter(user=self.member).update(is_active=True)
        membership = self.member.crush_connect_membership
        membership.paused_at = None
        membership.save(update_fields=["paused_at"])
        from crush_lu.models import ConnectRealPersonBadge

        ConnectRealPersonBadge.objects.all().delete()
        ConnectRealPersonCheck.objects.exclude(pk=self.check.pk).delete()

    def test_a_photo_that_is_not_public_yet_does_not_stop_the_call(self):
        """The photo rules decide what may be recorded, not whether to talk."""
        self.block_publication()
        self.assertEqual(self.both_refused(), ("coach", "member"))

    def test_a_malformed_or_unknown_address_is_refused_without_raising(self):
        other_check_pk = self.check.pk + 1000
        for object_id in (
            "",
            "abc",
            "0",
            "-1",
            "+1",
            "1.5",
            " 7",
            f"0{self.check.pk}",  # the same check, not in its canonical form
            f"{self.check.pk}\n",  # "$" would match before the newline
            f"{self.check.pk}\r\n",
            f"\n{self.check.pk}",
            f"{self.check.pk}a",
            "9" * 30,
            "9" * 19,
            "٧",  # an Arabic-Indic digit seven
            "²",  # superscript two
            str(other_check_pk),  # no such check
            None,
            self.check.pk,  # an int, not the str the video layer passes
            b"1",
        ):
            with self.subTest(object_id=object_id):
                self.assertIsNone(authorize_video_call(self.coach.user, object_id))

    def test_it_writes_nothing_and_logs_nothing(self):
        with self.assertNoLogs():
            with CaptureQueriesContext(connection) as queries:
                for user in (self.coach.user, self.member, self.other_member):
                    self.role(user)
                with override_settings(CONNECT_REAL_PERSON_CHECK_ENABLED=False):
                    self.role(self.coach.user)
        statements = [q["sql"].lstrip().upper() for q in queries]
        self.assertTrue(statements)
        self.assertFalse(
            [s for s in statements if s.startswith(("INSERT", "UPDATE", "DELETE"))]
        )

    def test_a_call_cannot_be_opened_by_reading_the_check_in_another_form(self):
        """ "007" and "7" would be two rooms for one check; only one form works."""
        self.assertIsNone(authorize_video_call(self.coach.user, f"00{self.check.pk}"))
        self.assertEqual(
            authorize_video_call(self.coach.user, str(self.check.pk)), "coach"
        )


class VideoLayerWiringTests(VideoCheckTestCase):
    def test_the_kind_is_registered_next_to_the_staff_test_room(self):
        self.assertEqual(
            VIDEO_CALL_KINDS["real-person-check"],
            "crush_lu.services.real_person_check.authorize_video_call",
        )
        self.assertIn("staff-test", VIDEO_CALL_KINDS)
        self.assertEqual(CALL_KIND, "real-person-check")

    def test_call_role_gives_the_roles_for_the_kind(self):
        pk = str(self.check.pk)
        self.assertEqual(call_role(self.coach.user, CALL_KIND, pk), "coach")
        self.assertEqual(call_role(self.member, CALL_KIND, pk), "member")
        for user in (self.other_coach.user, self.other_member, self.superuser):
            self.assertIsNone(call_role(user, CALL_KIND, pk))
        self.assertIsNone(call_role(AnonymousUser(), CALL_KIND, pk))

    def test_the_layer_does_not_take_an_id_plus_a_newline_for_the_id(self):
        """``$`` matches before a trailing newline: with ``.match`` the layer
        let "7\n" through as one more name for check 7."""
        pk = str(self.check.pk)
        self.assertTrue(valid_call_address(CALL_KIND, pk))
        for object_id in (f"{pk}\n", f"\n{pk}", f"{pk}\n\n"):
            with self.subTest(object_id=object_id):
                self.assertFalse(valid_call_address(CALL_KIND, object_id))
                self.assertIsNone(call_role(self.coach.user, CALL_KIND, object_id))
        self.assertFalse(valid_call_address(f"{CALL_KIND}\n", pk))
        self.assertFalse(valid_call_address("staff-test", "room1\n"))

    def test_call_role_still_refuses_what_the_layer_refuses_first(self):
        self.assertIsNone(call_role(self.coach.user, CALL_KIND, "not_valid"))
        self.assertIsNone(call_role(self.coach.user, "real-person-chec", "1"))
        self.coach.user.is_active = False
        self.assertIsNone(call_role(self.coach.user, CALL_KIND, str(self.check.pk)))

    def test_member_call_check_names_the_members_live_check(self):
        self.assertEqual(member_call_check(self.member), self.check)
        # Not the coach, not someone else.
        self.assertIsNone(member_call_check(self.coach.user))
        self.assertIsNone(member_call_check(self.other_member))
        self.expire()
        self.assertIsNone(member_call_check(self.member))

    def test_member_call_check_needs_the_flag_and_video(self):
        with override_settings(CONNECT_REAL_PERSON_CHECK_ENABLED=False):
            with self.assertNumQueries(0):
                self.assertIsNone(member_call_check(self.member))
        with override_settings(**WITHOUT_TURN):
            with self.assertNumQueries(0):
                self.assertIsNone(member_call_check(self.member))
        # DEBUG lets two local browsers connect without a relay.
        with override_settings(DEBUG=True, **{k: "" for k in TURN}):
            self.assertEqual(member_call_check(self.member), self.check)


# ---------------------------------------------------------------------------
# The relay-credentials endpoint
# ---------------------------------------------------------------------------


class IceServersEndpointTests(VideoCheckTestCase):
    def post(self, user=None):
        self.client.logout()
        if user is not None:
            self.client.force_login(user)
        return self.client.post(_ice_url(self.check))

    def test_the_coach_and_the_member_get_fresh_credentials(self):
        for user in (self.coach.user, self.member):
            with self.subTest(user=user.username):
                with patch(
                    "crush_lu.services.video_call.generate_ice_servers",
                    return_value=[RELAY],
                ) as mint:
                    response = self.post(user)
                mint.assert_called_once()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.json(),
                    {"iceServers": [RELAY], "iceTransportPolicy": "relay"},
                )
                self.assertIn("no-store", response["Cache-Control"])

    def test_the_cloudflare_token_never_reaches_the_response(self):
        with patch(
            "crush_lu.services.turn_credentials.requests.post",
            return_value=_cloudflare_reply(),
        ) as post:
            response = self.post(self.member)
        post.assert_called_once()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(SECRET, response.content.decode())
        self.assertNotIn(SECRET, str(response.headers))

    def test_everyone_else_gets_a_404_and_no_credentials_are_minted(self):
        for who, user in (
            ("another coach", self.other_coach.user),
            ("another member", self.other_member),
            ("a superuser", self.superuser),
        ):
            with self.subTest(who):
                with patch("crush_lu.services.video_call.generate_ice_servers") as mint:
                    response = self.post(user)
                self.assertEqual(response.status_code, 404)
                mint.assert_not_called()

    def test_an_anonymous_visitor_is_sent_to_login(self):
        self.assertEqual(self.post().status_code, 302)

    def test_the_address_of_the_check_plus_a_newline_gets_no_credentials(self):
        """The URL converter takes "7%0A" (its pattern is any non-slash text)."""
        for user in (self.coach.user, self.member):
            for suffix in ("%0A", "%0D%0A"):
                with self.subTest(user=user.username, suffix=suffix):
                    self.client.force_login(user)
                    with patch(
                        "crush_lu.services.video_call.generate_ice_servers"
                    ) as mint:
                        response = self.client.post(
                            f"/en/video-call/{CALL_KIND}/{self.check.pk}{suffix}"
                            "/ice-servers/"
                        )
                    self.assertEqual(response.status_code, 404)
                    mint.assert_not_called()

    def test_a_closed_lapsed_or_switched_off_check_gets_a_404(self):
        with patch("crush_lu.services.video_call.generate_ice_servers") as mint:
            with override_settings(CONNECT_REAL_PERSON_CHECK_ENABLED=False):
                self.assertEqual(self.post(self.member).status_code, 404)
            self.expire()
            self.assertEqual(self.post(self.member).status_code, 404)
            self.assertEqual(self.post(self.coach.user).status_code, 404)
            self.update_check(
                status="released",
                lease_expires_at=timezone.now() + timedelta(hours=1),
            )
            self.assertEqual(self.post(self.member).status_code, 404)
        mint.assert_not_called()


# ---------------------------------------------------------------------------
# The signalling consumer
# ---------------------------------------------------------------------------


class ConsumerTests(VideoCheckTestCase):
    """Drives the consumer directly, like ``test_video_call`` (the channels
    communicator pulls in daphne)."""

    def setUp(self):
        super().setUp()
        # database_sync_to_async would close the test transaction's connection.
        patcher = patch("channels.db.close_old_connections", lambda *a, **kw: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.group = f"video_call.{CALL_KIND}.{self.check.pk}"

    def consumer(self, user, name="chan-a", object_id=None):
        consumer = VideoCallConsumer()
        consumer.scope = {
            "user": user,
            "url_route": {
                "kwargs": {
                    "kind": CALL_KIND,
                    "object_id": object_id or str(self.check.pk),
                }
            },
        }
        consumer.channel_name = name
        consumer.channel_layer = AsyncMock()
        consumer.accept = AsyncMock()
        consumer.close = AsyncMock()
        consumer.send_json = AsyncMock()
        return consumer

    def test_the_coach_and_the_member_connect_each_to_their_own_slot(self):
        for user, role, name in (
            (self.coach.user, "coach", "chan-a"),
            (self.member, "member", "chan-b"),
        ):
            with self.subTest(role):
                consumer = self.consumer(user, name=name)
                async_to_sync(consumer.connect)()
                consumer.accept.assert_awaited_once()
                consumer.close.assert_not_awaited()
                consumer.channel_layer.group_add.assert_awaited_once_with(
                    self.group, name
                )
                self.assertEqual(cache.get(f"{self.group}.{role}"), name)

    def test_an_outsider_is_closed_out(self):
        for who, user in (
            ("another coach", self.other_coach.user),
            ("another member", self.other_member),
            ("a superuser", self.superuser),
            ("an anonymous visitor", AnonymousUser()),
        ):
            with self.subTest(who):
                consumer = self.consumer(user)
                async_to_sync(consumer.connect)()
                consumer.close.assert_awaited_once()
                consumer.accept.assert_not_awaited()
                consumer.channel_layer.group_add.assert_not_awaited()

    def test_a_call_that_may_not_exist_any_more_is_closed(self):
        self.expire()
        for user in (self.coach.user, self.member):
            consumer = self.consumer(user)
            async_to_sync(consumer.connect)()
            consumer.close.assert_awaited_once()
            consumer.accept.assert_not_awaited()

    def test_a_malformed_address_is_closed(self):
        consumer = self.consumer(self.coach.user, object_id=f"0{self.check.pk}")
        async_to_sync(consumer.connect)()
        consumer.close.assert_awaited_once()
        consumer.accept.assert_not_awaited()


# ---------------------------------------------------------------------------
# The coach check page
# ---------------------------------------------------------------------------


class CoachPageTests(VideoCheckTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.coach.user)
        self.url = f"{BASE}{self.check.pk}/"

    def page(self, **extra):
        return self.client.get(self.url, **extra)

    def test_it_hosts_the_call_and_opens_the_microphone_for_this_page_only(self):
        response = self.page()
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn('x-data="videoCall"', html)
        self.assertIn(f"/ws/video-call/{CALL_KIND}/{self.check.pk}/", html)
        self.assertIn(f'data-ice-url="{_ice_url(self.check)}"', html)
        self.assertEqual(response.context["video_call"]["kind"], "real-person-check")
        policy = response["Permissions-Policy"]
        self.assertIn("microphone=(self)", policy)
        self.assertNotIn("microphone=()", policy)
        # Every other page keeps the microphone off.
        queue_policy = self.client.get(BASE)["Permissions-Policy"]
        self.assertIn("microphone=()", queue_policy)
        self.assertNotIn("microphone=(self)", queue_policy)

    def test_both_bundles_are_loaded_and_the_token_is_not_in_the_page(self):
        html = self.page().content.decode()
        self.assertIn("alpine/coach.min.js", html)
        self.assertIn("alpine/connect.min.js", html)
        self.assertNotIn(SECRET, html)

    def test_the_call_sits_outside_the_block_the_contact_log_swaps(self):
        """Logging an attempt swaps #rpc-outreach: a call inside it would be
        torn down mid-conversation."""
        html = self.page().content.decode()
        outreach_start = html.index('id="rpc-outreach"')
        call_start = html.index('x-data="videoCall"')
        decision_start = html.index('id="rpc-decision-heading"')
        self.assertLess(outreach_start, call_start)
        self.assertLess(call_start, decision_start)
        response = self.client.post(
            f"{self.url}log/",
            {"result": "sms_sent"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("videoCall", response.content.decode())
        self.assertNotIn("microphone=(self)", response["Permissions-Policy"])

    def test_without_the_relay_nothing_new_shows(self):
        with override_settings(**WITHOUT_TURN):
            response = self.page()
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("videoCall", html)
        self.assertNotIn("Video call in Crush.lu", html)
        self.assertNotIn("real-person-call", html)
        self.assertNotIn("video_call", response.context)
        self.assertIn("Reach out", html)
        self.assertIn('id="rpc-decision-heading"', html)
        self.assertIn("microphone=()", response["Permissions-Policy"])

    def test_a_closed_or_lapsed_check_hosts_no_call(self):
        self.expire()
        response = self.page()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("videoCall", response.content.decode())
        self.assertIn("microphone=()", response["Permissions-Policy"])
        self.update_check(status="released")
        self.assertNotIn("videoCall", self.page().content.decode())

    def test_a_guard_that_switches_the_contact_links_off_hosts_no_call(self):
        UserBlock.objects.create(blocker=self.member, blocked=self.coach.user)
        response = self.page()
        html = response.content.decode()
        self.assertTrue(response.context["links_blocked"])
        self.assertNotIn("videoCall", html)
        self.assertIn("microphone=()", response["Permissions-Policy"])

    def test_a_main_photo_that_is_not_public_still_hosts_the_call(self):
        """Only a voice pass is possible then, but the conversation is not."""
        self.block_publication()
        response = self.page()
        self.assertFalse(response.context["video_available"])
        self.assertIn('x-data="videoCall"', response.content.decode())
        self.assertIn("microphone=(self)", response["Permissions-Policy"])

    def test_a_refused_decision_shows_the_page_with_its_call_again(self):
        response = self.client.post(
            f"{self.url}decide/",
            {"action": "pass_voice", "checklist": "{}", "confirm": "yes"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('x-data="videoCall"', response.content.decode())
        self.assertIn("microphone=(self)", response["Permissions-Policy"])

    def test_the_call_section_is_translated_in_the_coach_register(self):
        for code, heading, tone in (
            ("de", "Videoanruf in Crush.lu", "in deiner Nachricht"),
            ("fr", "Appel vidéo dans Crush.lu", "de ton message"),
        ):
            with self.subTest(code):
                html = self.client.get(
                    f"/{code}/coach/real-person-checks/{self.check.pk}/"
                ).content.decode()
                self.assertIn(heading, html)
                self.assertIn(tone, html)
                self.assertNotIn("joins from the link", html)

    def test_the_app_webview_is_pointed_to_the_browser(self):
        # In the browser the coach is told to press the button...
        self.assertIn(
            "Press Start video when they are on the call page",
            self.page().content.decode(),
        )
        # ...inside the app there is no button, so no instruction for it.
        for header in ("ios-app", "android-app"):
            with self.subTest(header):
                html = self.page(HTTP_X_CRUSH_CLIENT=header).content.decode()
                self.assertNotIn('x-data="videoCall"', html)
                self.assertIn("Open this page in your browser", html)
                self.assertNotIn("Start video", html)
                self.assertNotIn("joins from the link", html)

    def test_the_flag_off_is_still_a_404(self):
        with override_settings(CONNECT_REAL_PERSON_CHECK_ENABLED=False):
            self.assertEqual(self.page().status_code, 404)


# ---------------------------------------------------------------------------
# The coach's text message
# ---------------------------------------------------------------------------


class OutreachTextTests(VideoCheckTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.coach.user)
        self.url = f"{BASE}{self.check.pk}/"

    def sms(self, **extra):
        response = self.client.get(self.url, **extra)
        return unquote(response.context["sms_href"].split("?body=", 1)[1]), response

    def language(self, code):
        CrushProfile.objects.filter(user=self.member).update(preferred_language=code)

    def test_the_text_ends_with_the_call_page_in_the_members_language(self):
        for code, opening, join in (
            ("en", "Hi Rpcvid_Member", "join from your browser here: "),
            ("de", "Hallo Rpcvid_Member", "tritt hier im Browser bei: "),
            (
                "fr",
                "Bonjour Rpcvid_Member",
                "rejoignez l'appel depuis votre navigateur ici : ",
            ),
        ):
            with self.subTest(code):
                self.language(code)
                text, _response = self.sms()
                self.assertTrue(text.startswith(opening), text)
                self.assertTrue(
                    text.endswith(
                        f"{join}http://crush.lu/{code}/crush-connect/"
                        f"real-person-call/{self.check.pk}/"
                    ),
                    text,
                )

    def test_the_coachs_own_page_language_does_not_pick_the_address_language(self):
        self.language("de")
        response = self.client.get(f"/fr{self.url[3:]}")
        text = unquote(response.context["sms_href"].split("?body=", 1)[1])
        self.assertTrue(text.startswith("Hallo "), text)
        self.assertIn(f"/de/crush-connect/real-person-call/{self.check.pk}/", text)

    def test_whatsapp_carries_the_same_text_for_a_member_who_opted_in(self):
        EmailPreference.objects.update_or_create(
            user=self.member, defaults={"whatsapp_opt_in": True}
        )
        text, response = self.sms()
        whatsapp = unquote(response.context["whatsapp_url"])
        self.assertIn(text, whatsapp)
        self.assertIn(f"/en/crush-connect/real-person-call/{self.check.pk}/", whatsapp)

    def test_without_the_relay_the_text_is_exactly_what_it_was(self):
        with override_settings(**WITHOUT_TURN):
            text, _response = self.sms()
        self.assertEqual(
            text,
            "Hi Rpcvid_Member, this is Rpcvid_Coach from Crush.lu. To confirm "
            "every Crush Connect member is a real person, could we do a quick "
            "5-minute video call? It's optional and adds a 'verified by a coach' "
            "badge to your card.",
        )

    def test_while_only_a_voice_pass_is_possible_the_text_still_offers_the_call(self):
        self.block_publication()
        for code, expected in (
            ("en", "quick 5-minute call? It's optional and adds a badge"),
            ("de", "kurzen Anruf (5 Minuten)"),
            ("fr", "rapide appel de 5 minutes"),
        ):
            with self.subTest(code):
                self.language(code)
                text, response = self.sms()
                self.assertFalse(response.context["video_available"])
                self.assertIn(expected, text)
                self.assertNotIn("verified by a coach", text)
                self.assertTrue(
                    text.endswith(
                        f"/{code}/crush-connect/real-person-call/{self.check.pk}/"
                    ),
                    text,
                )

    def test_a_check_that_is_not_a_live_claim_carries_no_address(self):
        self.expire()
        response = self.client.get(self.url)
        self.assertEqual(response.context["sms_href"], "")
        self.assertNotIn("real-person-call", response.content.decode())

    def test_german_and_french_texts_are_translated_not_english(self):
        for code, forbidden in (("de", "this is"), ("fr", "this is")):
            with self.subTest(code):
                self.language(code)
                text, _response = self.sms()
                self.assertNotIn(forbidden, text)
                self.assertNotIn("%(", text)


# ---------------------------------------------------------------------------
# The member's call page
# ---------------------------------------------------------------------------


class MemberPageTests(VideoCheckTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.member)
        self.url = _call_page(self.check)

    def test_the_member_sees_their_coach_the_explainer_and_the_call(self):
        response = self.client.get(self.url)
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("A short video call with Rpcvid_Coach", html)
        self.assertIn("It is optional. You can say no", html)
        self.assertIn("Nothing is recorded or saved.", html)
        # The coach reaches out first and then joins the same call: the page
        # does not say they are on it already.
        self.assertIn("When your coach has been in touch and you are ready", html)
        self.assertIn("Your coach joins the same call.", html)
        self.assertNotIn("from the same page", html)
        self.assertIn('x-data="videoCall"', html)
        self.assertIn(f'data-ice-url="{_ice_url(self.check)}"', html)
        self.assertIn('href="/en/crush-connect/home/"', html)
        self.assertIn("alpine/connect.min.js", html)
        self.assertIn("noindex", html)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("microphone=(self)", response["Permissions-Policy"])
        self.assertNotIn(SECRET, html)

    def test_it_promises_the_badge_only_once_members_can_see_one(self):
        shown = "your Connect card gets a badge that tells other members"
        waiting = "as soon as badges are shown to other members"
        html = self.client.get(self.url).content.decode()
        self.assertIn(shown, html)
        self.assertNotIn(waiting, html)
        with override_settings(CONNECT_REAL_PERSON_BADGE_VISIBLE=False):
            html = self.client.get(self.url).content.decode()
            self.assertIn(waiting, html)
            self.assertNotIn(shown, html)
            for code, text in (
                ("de", "sobald Abzeichen für andere Mitglieder sichtbar sind"),
                ("fr", "dès que les badges sont visibles pour les autres membres"),
            ):
                with self.subTest(code):
                    html = self.client.get(
                        self.url.replace("/en/", f"/{code}/")
                    ).content.decode()
                    self.assertIn(text, html)

    def test_it_is_translated(self):
        for code, title in (
            ("de", "Ein kurzer Videoanruf mit Rpcvid_Coach"),
            ("fr", "Un court appel vidéo avec Rpcvid_Coach"),
        ):
            with self.subTest(code):
                html = self.client.get(
                    self.url.replace("/en/", f"/{code}/")
                ).content.decode()
                self.assertIn(title, html)
                self.assertIn(
                    "Video starten" if code == "de" else "Démarrer la vidéo", html
                )
                self.assertIn(
                    (
                        "Wenn sich dein Coach gemeldet hat"
                        if code == "de"
                        else "Une fois que votre coach vous a contacté"
                    ),
                    html,
                )
                self.assertNotIn("Start video", html)
                self.assertNotIn("When your coach has been in touch", html)

    def test_every_label_of_the_call_screen_is_translated(self):
        # The labels sit on the videoCall element; the base template has
        # data-label-* attributes of its own.
        call_tag = re.compile(r'<div x-data="videoCall".*?>', re.S)
        label = re.compile(r'data-label-([a-z-]+)="([^"]*)"')

        def labels(html):
            return dict(label.findall(call_tag.search(html).group(0)))

        english = labels(self.client.get(self.url).content.decode())
        # Ten status lines, the four mute / camera button labels and the two
        # microphone meter labels.
        self.assertEqual(len(english), 16)
        for code in ("de", "fr"):
            html = self.client.get(
                self.url.replace("/en/", f"/{code}/")
            ).content.decode()
            translated = labels(html)
            self.assertEqual(translated.keys(), english.keys())
            for key, text in translated.items():
                with self.subTest(code=code, label=key):
                    self.assertNotEqual(text, english[key])
            for text in ("Start video", "Switch camera", "Hang up"):
                with self.subTest(code=code, button=text):
                    self.assertNotIn(f">{text}<", html)

    def test_the_call_screen_shows_both_sound_meters(self):
        html = self.client.get(self.url).content.decode()
        block = re.search(
            r"<div ([^>]*\bdata-meters\b[^>]*)>(.*?)</p>\s*</div>", html, re.S
        )
        self.assertIsNotNone(block, "sound meter block missing")
        attrs, inner = block.group(1), block.group(2)
        # Hidden until a call runs and the meters really measure (x-cloak keeps
        # it hidden before Alpine starts), and hidden from screen readers with
        # its hint, since the hint describes the bars.
        self.assertIn('x-show="showMeters"', attrs)
        self.assertIn("x-cloak", attrs)
        self.assertIn('aria-hidden="true"', attrs)
        self.assertIn('data-meter="local"', inner)
        self.assertIn(':style="localMeterStyle"', inner)
        self.assertIn('data-meter="remote"', inner)
        self.assertIn(':style="remoteMeterStyle"', inner)
        self.assertIn(">Their sound<", inner)
        self.assertIn("turn up your device", inner)
        for code, sound, hint in (
            ("de", ">Ton der anderen Person<", "dreh die Lautstärke deines Geräts auf"),
            (
                "fr",
                ">Son de l'autre personne<",
                "augmentez le volume de votre appareil",
            ),
        ):
            with self.subTest(code):
                html = self.client.get(
                    self.url.replace("/en/", f"/{code}/")
                ).content.decode()
                self.assertIn(sound, html)
                self.assertIn(hint, html)
                self.assertNotIn(">Their sound<", html)

    def test_the_app_notice_is_translated(self):
        for code, text in (
            ("de", "Videoanrufe funktionieren im Browser deines Handys"),
            ("fr", "Les appels vidéo fonctionnent dans le navigateur"),
        ):
            with self.subTest(code):
                html = self.client.get(
                    self.url.replace("/en/", f"/{code}/"),
                    HTTP_X_CRUSH_CLIENT="android-app",
                ).content.decode()
                self.assertIn(text, html)

    def test_a_coach_without_a_first_name_is_called_your_coach(self):
        type(self.member).objects.filter(pk=self.coach.user.pk).update(first_name="")
        html = self.client.get(self.url).content.decode()
        self.assertIn("A short video call with your coach", html)

    def test_an_anonymous_visitor_is_sent_to_login(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_nobody_but_the_member_gets_the_page(self):
        for who, user in (
            ("the coach", self.coach.user),
            ("another coach", self.other_coach.user),
            ("another member", self.other_member),
            ("a superuser", self.superuser),
        ):
            with self.subTest(who):
                _grant_consent(user)
                self.client.force_login(user)
                self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_with_the_tooling_flag_off_the_page_does_not_exist(self):
        """Even for the member: the feature is dark, so there is no page to
        say a call is not open."""
        with override_settings(CONNECT_REAL_PERSON_CHECK_ENABLED=False):
            self.assertEqual(self.client.get(self.url).status_code, 404)
            self.expire()
            self.assertEqual(self.client.get(self.url).status_code, 404)

    def assertNotOpenPage(self, response):
        """The member's "not open right now" page: no call, no microphone."""
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("data-real-person-call-ended", html)
        self.assertIn("This video call is not open right now", html)
        self.assertIn("Joining was optional, so nothing changes for you", html)
        self.assertIn('href="/en/crush-connect/home/"', html)
        self.assertNotIn("videoCall", html)
        self.assertNotIn("video-call-config", html)
        self.assertNotIn("alpine/connect.min.js", html)
        self.assertNotIn(SECRET, html)
        self.assertIn("noindex", html)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("microphone=()", response["Permissions-Policy"])
        self.assertNotIn("microphone=(self)", response["Permissions-Policy"])

    def test_a_call_that_cannot_be_joined_tells_the_member_instead_of_a_404(self):
        """The member's own link, from the text message or a reload, must not
        end in "Page not found" once the coach decided, released, the claim
        lapsed or a safeguard closed the call. The page names no reason."""
        UserBlock.objects.create(blocker=self.member, blocked=self.coach.user)
        self.assertNotOpenPage(self.client.get(self.url))
        UserBlock.objects.all().delete()
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertIn('x-data="videoCall"', self.client.get(self.url).content.decode())
        self.expire()
        self.assertNotOpenPage(self.client.get(self.url))
        for status in ("declined", "passed", "released"):
            with self.subTest(status):
                self.update_check(
                    status=status,
                    medium="voice" if status == "passed" else "",
                    lease_expires_at=timezone.now() + timedelta(hours=1),
                )
                self.assertNotOpenPage(self.client.get(self.url))

    def test_a_coach_who_was_removed_leaves_the_member_the_same_page(self):
        self.update_check(coach=None)
        self.assertNotOpenPage(self.client.get(self.url))

    def test_the_not_open_page_is_translated(self):
        self.expire()
        for code, heading, body in (
            (
                "de",
                "Dieser Videoanruf ist gerade nicht geöffnet",
                "für dich ändert sich also nichts",
            ),
            (
                "fr",
                "Cet appel vidéo n'est pas ouvert pour le moment",
                "rien ne change donc pour vous",
            ),
        ):
            with self.subTest(code):
                html = self.client.get(
                    self.url.replace("/en/", f"/{code}/")
                ).content.decode()
                self.assertIn(heading, html)
                self.assertIn(body, html)
                self.assertNotIn("This video call is not open", html)

    def test_nobody_else_learns_that_a_closed_check_exists(self):
        """The "not open" page is for the check's member alone; for everyone
        else a closed or lapsed check is as absent as an unknown one."""
        self.expire()
        for who, user in (
            ("the coach", self.coach.user),
            ("another coach", self.other_coach.user),
            ("another member", self.other_member),
            ("a superuser", self.superuser),
        ):
            with self.subTest(who):
                _grant_consent(user)
                self.client.force_login(user)
                self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_an_unknown_check_gets_a_404(self):
        for check_id in ("999999", "9" * 30):
            with self.subTest(check_id):
                self.assertEqual(
                    self.client.get(
                        f"/en/crush-connect/real-person-call/{check_id}/"
                    ).status_code,
                    404,
                )

    def test_without_the_relay_the_coach_phones_instead(self):
        with override_settings(**WITHOUT_TURN):
            response = self.client.get(self.url)
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("your coach will phone you instead", html)
        self.assertNotIn("videoCall", html)
        self.assertIn("microphone=()", response["Permissions-Policy"])

    def test_inside_the_app_the_page_points_to_the_browser(self):
        for header in ("ios-app", "android-app"):
            with self.subTest(header):
                response = self.client.get(self.url, HTTP_X_CRUSH_CLIENT=header)
                html = response.content.decode()
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('x-data="videoCall"', html)
                self.assertIn("Open this page in your browser", html)
                self.assertIn(self.url, html)
                # No instruction for a button the app does not show.
                self.assertNotIn("Start video", html)
                self.assertNotIn("When your coach has been in touch", html)


# ---------------------------------------------------------------------------
# The hub card
# ---------------------------------------------------------------------------

HUB = "/en/crush-connect/home/"
CARD = "data-real-person-call-card"
NOTE = "data-real-person-check-note"


class HubCardTests(VideoCheckTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.member)

    def hub(self, user=None, language="en"):
        if user is not None:
            _grant_consent(user)
            self.client.force_login(user)
        return self.client.get(HUB.replace("/en/", f"/{language}/"))

    def test_the_member_with_a_live_check_gets_the_card_and_no_note(self):
        response = self.hub()
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn(CARD, html)
        # A claim is not a conversation: nothing may suggest the coach is
        # already waiting (they may not have written yet).
        self.assertIn("A short video call with your coach", html)
        self.assertIn("Rpcvid_Coach would like to meet you on video", html)
        self.assertIn("Once they have been in touch, you can join here", html)
        self.assertNotIn("is ready", html)
        self.assertIn(f'href="{_call_page(self.check)}"', html)
        # One message, not two: the note promised "a coach may text you".
        self.assertNotIn(NOTE, html)

    def test_the_note_is_back_when_there_is_no_live_check(self):
        for label, change in (
            ("released", {"status": "released"}),
            ("lapsed", {"lease_expires_at": timezone.now() - timedelta(minutes=1)}),
        ):
            with self.subTest(label):
                self.update_check(**change)
                html = self.hub().content.decode()
                self.assertNotIn(CARD, html)
                self.assertIn(NOTE, html)
                self.update_check(
                    status="open",
                    lease_expires_at=timezone.now() + timedelta(hours=1),
                )

    def test_nobody_but_the_member_gets_the_card(self):
        for who, user in (
            ("the coach", self.coach.user),
            ("another member", self.other_member),
        ):
            with self.subTest(who):
                self.assertNotIn(CARD, self.hub(user).content.decode())

    def test_without_the_relay_the_card_stays_away_and_the_note_stays(self):
        with override_settings(**WITHOUT_TURN):
            html = self.hub().content.decode()
        self.assertNotIn(CARD, html)
        self.assertIn(NOTE, html)

    def test_the_card_does_not_wait_for_the_badge_flag(self):
        with override_settings(CONNECT_REAL_PERSON_BADGE_VISIBLE=False):
            html = self.hub().content.decode()
        self.assertIn(CARD, html)
        self.assertNotIn(NOTE, html)

    def test_with_the_tooling_flag_off_neither_shows(self):
        with override_settings(CONNECT_REAL_PERSON_CHECK_ENABLED=False):
            html = self.hub().content.decode()
        self.assertNotIn(CARD, html)
        self.assertNotIn(NOTE, html)

    def test_a_guard_that_closes_the_call_removes_the_card(self):
        UserBlock.objects.create(blocker=self.coach.user, blocked=self.member)
        self.assertNotIn(CARD, self.hub().content.decode())

    def test_the_card_is_translated(self):
        for code, heading, wait, button in (
            (
                "de",
                "Ein kurzer Videoanruf mit deinem Coach",
                "Sobald sich dein Coach gemeldet hat",
                "Beitreten",
            ),
            (
                "fr",
                "Un court appel vidéo avec votre coach",
                "Dès que votre coach vous aura contacté",
                "Rejoindre",
            ),
        ):
            with self.subTest(code):
                html = self.hub(language=code).content.decode()
                self.assertIn(heading, html)
                self.assertIn(wait, html)
                self.assertIn(f'href="/{code}/crush-connect/real-person-call/', html)
                self.assertIn(button, html)
                self.assertNotIn("Once they have been in touch", html)

    def test_the_card_without_a_coach_first_name_is_translated_too(self):
        type(self.member).objects.filter(pk=self.coach.user.pk).update(first_name="")
        for code, text in (
            ("en", "A Crush coach would like to meet you on video"),
            ("de", "Sobald sich der Coach gemeldet hat"),
            ("fr", "Dès que le coach vous aura contacté"),
        ):
            with self.subTest(code):
                html = self.hub(language=code).content.decode()
                self.assertIn(CARD, html)
                self.assertIn(text, html)

    def test_the_card_costs_a_lookup_and_the_guards_only_while_a_check_is_live(self):
        def cost():
            cache.clear()
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(self.hub().status_code, 200)
            return len(queries)

        with override_settings(CONNECT_REAL_PERSON_CHECK_ENABLED=False):
            cost()
            off = cost()
        self.update_check(status="released")
        no_check = cost()
        self.update_check(status="open")
        live = cost()
        # Flag on and no live check: the note's cohort lookup (up to 3, as in
        # test_real_person_check_member_ui) plus the one lookup for the card.
        self.assertLessEqual(no_check, off + 4)
        # A live check: the guards of the coach-side rule run once; the card
        # stands in for the note, which then asks nothing.
        self.assertLessEqual(live, no_check + 8)


class SlotStickySettingsTests(SimpleTestCase):
    """A slot swap must never carry one slot's video-call settings to the other.

    ``infra/resources.bicep`` states which app settings stay with their slot.
    The two Cloudflare TURN credentials are per-slot secrets; the transport
    policy is the privacy setting (``relay`` keeps the coach's and the
    member's IP addresses from each other), so a staging ``all`` must not
    reach production.
    """

    NAMES = (
        "CLOUDFLARE_TURN_KEY_ID",
        "CLOUDFLARE_TURN_KEY_API_TOKEN",
        "VIDEO_CALL_ICE_TRANSPORT_POLICY",
    )

    def pinned_names(self):
        text = (Path(settings.BASE_DIR) / "infra" / "resources.bicep").read_text(
            encoding="utf-8"
        )
        start = text.index("resource slotConfigNames ")
        block = text[text.index("appSettingNames: [", start) :]
        block = block[: block.index("]")]
        lines = (line.split("//")[0].strip() for line in block.splitlines())
        return {line.strip("'") for line in lines if line.startswith("'")}

    def test_the_video_call_settings_are_pinned_to_their_slot(self):
        pinned = self.pinned_names()
        self.assertIn("CONNECT_REAL_PERSON_CHECK_ENABLED", pinned)  # the parser works
        for name in self.NAMES:
            with self.subTest(name):
                self.assertIn(name, pinned)

    def test_the_pinned_names_are_the_settings_the_code_reads(self):
        for name in self.NAMES:
            with self.subTest(name):
                self.assertTrue(hasattr(settings, name))


class UnrenderedStringTests(SimpleTestCase):
    """Strings of the call screen that no page above renders in this state."""

    def test_they_have_german_and_french(self):
        for msgid in (
            "Video calls are not available right now. Use a phone call instead.",
            "Connected",
        ):
            for code in ("de", "fr"):
                with self.subTest(code=code, msgid=msgid):
                    with translation.override(code):
                        self.assertNotEqual(gettext(msgid), msgid)
