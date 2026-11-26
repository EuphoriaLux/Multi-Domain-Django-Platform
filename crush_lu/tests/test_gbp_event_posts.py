"""Google Business Profile event posts (services/google_business_profile.py).

No HTTP library is mocked: the service takes a session object, and these tests
hand it a fake that records each call and replays canned responses.
"""

import json
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from io import StringIO
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from crush_lu.models import GoogleLocalPostSync, MeetupEvent
from crush_lu.services import google_business_profile as gbp

LOCATION = "accounts/1/locations/2"
POST_NAME = f"{LOCATION}/localPosts/abc"
TOKEN = json.dumps(
    {
        "client_id": "id",
        "client_secret": "secret",
        "refresh_token": "refresh",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
)
ENABLED = dict(
    GOOGLE_BUSINESS_PROFILE_ENABLED=True,
    GOOGLE_BUSINESS_PROFILE_LOCATION=LOCATION,
    GOOGLE_BUSINESS_PROFILE_TOKEN_JSON=TOKEN,
)


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.content = json.dumps(self._body).encode()
        self.text = self.content.decode()

    def json(self):
        return self._body


class FakeSession:
    """Answers each (method) from a queue; records every call."""

    def __init__(self, **responses):
        self.responses = {k.upper(): list(v) for k, v in responses.items()}
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        queue = self.responses.get(method, [])
        return queue.pop(0) if queue else FakeResponse(200, {})

    def methods(self):
        return [c[0] for c in self.calls]


def make_event(**overrides):
    start = timezone.now() + timedelta(days=7)
    defaults = {
        "title": "Speed Dating Luxembourg",
        "description": "An evening of short dates.\n\n\n\nBring a smile.",
        "event_type": "speed_dating",
        "location": "Café Konrad",
        "address": "7, rue du Nord\nL-2229 Luxembourg",
        "canton": "Luxembourg",
        "date_time": start,
        "duration_minutes": 120,
        "registration_deadline": start - timedelta(days=1),
        "is_published": True,
    }
    defaults.update(overrides)
    return MeetupEvent.objects.create(**defaults)


class PayloadTests(TestCase):
    def test_schedule_is_luxembourg_wall_clock_time(self):
        # 18:30 UTC on a July evening is 20:30 in Luxembourg (CEST, UTC+2).
        start = datetime(2027, 7, 9, 18, 30, tzinfo=dt_timezone.utc)
        event = make_event(
            date_time=start,
            duration_minutes=150,
            registration_deadline=start - timedelta(days=1),
        )
        schedule = gbp.build_post_payload(event)["event"]["schedule"]
        self.assertEqual(schedule["startDate"], {"year": 2027, "month": 7, "day": 9})
        self.assertEqual(schedule["startTime"]["hours"], 20)
        self.assertEqual(schedule["startTime"]["minutes"], 30)
        self.assertEqual(schedule["endDate"], {"year": 2027, "month": 7, "day": 9})
        self.assertEqual(schedule["endTime"]["hours"], 23)

    def test_english_post_with_tagged_booking_link(self):
        event = make_event()
        payload = gbp.build_post_payload(event)
        self.assertEqual(payload["languageCode"], "en")
        self.assertEqual(payload["topicType"], "EVENT")
        self.assertEqual(payload["callToAction"]["actionType"], "BOOK")
        url = payload["callToAction"]["url"]
        self.assertIn(f"/en/", url)
        self.assertIn("utm_source=google", url)
        self.assertIn(f"utm_content=event-{event.pk}", url)
        self.assertNotIn("media", payload)  # no banner, no photo
        self.assertEqual(
            payload["summary"], "An evening of short dates.\n\nBring a smile."
        )

    def test_title_is_truncated_to_googles_limit(self):
        event = make_event(title="A" * 120)
        title = gbp.build_post_payload(event)["event"]["title"]
        self.assertEqual(len(title), gbp.MAX_TITLE_CHARS)
        self.assertTrue(title.endswith("…"))

    def test_eligibility(self):
        self.assertTrue(gbp.should_post(make_event()))
        self.assertTrue(gbp.must_remove(make_event(is_cancelled=True)))
        self.assertTrue(gbp.must_remove(make_event(is_published=False)))
        self.assertTrue(gbp.must_remove(make_event(is_private_invitation=True)))
        past = timezone.now() - timedelta(days=2)
        started = make_event(date_time=past, registration_deadline=past)
        self.assertFalse(gbp.should_post(started))
        self.assertFalse(gbp.must_remove(started))


class GuardTests(TestCase):
    def test_disabled_by_default(self):
        self.assertFalse(gbp.is_sync_enabled())

    @override_settings(**dict(ENABLED, GOOGLE_BUSINESS_PROFILE_LOCATION=""))
    def test_no_location_refuses_even_when_enabled(self):
        with self.assertLogs("crush_lu.services.google_business_profile", "WARNING"):
            self.assertIn("LOCATION", gbp.disabled_reason())

    @override_settings(**dict(ENABLED, GOOGLE_BUSINESS_PROFILE_TOKEN_JSON=""))
    def test_no_token_refuses(self):
        self.assertIn("TOKEN", gbp.disabled_reason())

    @override_settings(**ENABLED)
    def test_fully_configured(self):
        self.assertEqual(gbp.disabled_reason(), "")


@override_settings(**ENABLED)
class SyncTests(TestCase):
    def test_create_then_unchanged_then_update(self):
        event = make_event()
        session = FakeSession(
            get=[FakeResponse(200, {"localPosts": []})],
            post=[FakeResponse(200, {"name": POST_NAME, "searchUrl": "https://g/x"})],
        )
        self.assertEqual(gbp.sync_event(event, session=session), "created")
        self.assertEqual(session.methods(), ["GET", "POST"])
        sync = GoogleLocalPostSync.objects.get(event=event)
        self.assertEqual(sync.post_name, POST_NAME)
        self.assertEqual(sync.status, GoogleLocalPostSync.Status.SYNCED)

        quiet = FakeSession()
        self.assertEqual(gbp.sync_event(event, session=quiet), "unchanged")
        self.assertEqual(quiet.calls, [])

        event.title = "Speed Dating Luxembourg — new date"
        event.save()
        patch = FakeSession(patch=[FakeResponse(200, {"name": POST_NAME})])
        self.assertEqual(gbp.sync_event(event, session=patch), "updated")
        method, url, kwargs = patch.calls[0]
        self.assertEqual(method, "PATCH")
        self.assertTrue(url.endswith(POST_NAME))
        self.assertEqual(kwargs["params"]["updateMask"], gbp.UPDATE_MASK)

    def test_lost_create_is_adopted_not_duplicated(self):
        event = make_event()
        existing = {
            "name": POST_NAME,
            "callToAction": {"url": gbp.booking_url(event)},
        }
        other = {
            "name": f"{LOCATION}/localPosts/other",
            "callToAction": {"url": "https://crush.lu/x?utm_content=event-99999"},
        }
        session = FakeSession(
            get=[FakeResponse(200, {"localPosts": [other, existing]})],
            patch=[FakeResponse(200, {"name": POST_NAME})],
        )
        self.assertEqual(gbp.sync_event(event, session=session), "adopted")
        self.assertEqual(session.methods(), ["GET", "PATCH"])
        self.assertEqual(
            GoogleLocalPostSync.objects.get(event=event).post_name, POST_NAME
        )

    def test_cancelled_event_post_is_deleted(self):
        event = make_event()
        GoogleLocalPostSync.objects.create(
            event=event, post_name=POST_NAME, status="synced"
        )
        event.is_cancelled = True
        event.save()
        session = FakeSession()
        self.assertEqual(gbp.sync_event(event, session=session), "removed")
        self.assertEqual(session.methods(), ["DELETE"])
        sync = GoogleLocalPostSync.objects.get(event=event)
        self.assertEqual(sync.post_name, "")
        self.assertEqual(sync.status, GoogleLocalPostSync.Status.REMOVED)

    def test_post_already_gone_counts_as_removed(self):
        event = make_event(is_published=False)
        GoogleLocalPostSync.objects.create(event=event, post_name=POST_NAME)
        session = FakeSession(delete=[FakeResponse(404, {"error": {"message": "nf"}})])
        self.assertEqual(gbp.sync_event(event, session=session), "removed")

    def test_rejected_post_is_recorded_and_retried(self):
        event = make_event()
        session = FakeSession(
            get=[FakeResponse(200, {})],
            post=[FakeResponse(400, {"error": {"message": "Invalid summary"}})],
        )
        with self.assertRaises(gbp.GbpError) as ctx:
            gbp.sync_event(event, session=session)
        self.assertFalse(ctx.exception.fatal)
        sync = GoogleLocalPostSync.objects.get(event=event)
        self.assertEqual(sync.status, GoogleLocalPostSync.Status.FAILED)
        self.assertIn("Invalid summary", sync.last_error)
        self.assertEqual(sync.payload_hash, "")

    def test_auth_failure_is_fatal(self):
        event = make_event()
        session = FakeSession(get=[FakeResponse(401, {"error": {"message": "no"}})])
        with self.assertRaises(gbp.GbpError) as ctx:
            gbp.sync_event(event, session=session)
        self.assertTrue(ctx.exception.fatal)

    def test_dry_run_sends_nothing(self):
        event = make_event()
        self.assertEqual(gbp.sync_event(event, session=None, dry_run=True), "created")
        self.assertFalse(GoogleLocalPostSync.objects.filter(event=event).exists())

    def test_sweep_selection(self):
        upcoming = make_event()
        past = timezone.now() - timedelta(days=3)
        old = make_event(date_time=past, registration_deadline=past)
        cancelled_with_post = make_event(is_cancelled=True)
        GoogleLocalPostSync.objects.create(
            event=cancelled_with_post, post_name=POST_NAME
        )
        make_event(is_published=False)
        ids = set(gbp.events_needing_sync().values_list("pk", flat=True))
        self.assertEqual(ids, {upcoming.pk, cancelled_with_post.pk})
        self.assertNotIn(old.pk, ids)


class CommandTests(TestCase):
    def test_disabled_command_sends_nothing(self):
        make_event()
        out = StringIO()
        with mock.patch.object(gbp, "get_session") as get_session:
            call_command("sync_events_to_gbp", stdout=out)
        get_session.assert_not_called()
        self.assertIn("GBP sync disabled", out.getvalue())

    def test_dry_run_works_without_configuration(self):
        make_event()
        out = StringIO()
        call_command("sync_events_to_gbp", "--dry-run", stdout=out)
        self.assertIn("would be created", out.getvalue())

    @override_settings(**ENABLED)
    def test_one_bad_event_does_not_stop_the_sweep(self):
        make_event(title="Bad")
        make_event(title="Good")
        session = FakeSession(
            get=[FakeResponse(200, {}), FakeResponse(200, {})],
            post=[
                FakeResponse(400, {"error": {"message": "rejected"}}),
                FakeResponse(200, {"name": POST_NAME}),
            ],
        )
        out = StringIO()
        with mock.patch.object(gbp, "get_session", return_value=session):
            call_command("sync_events_to_gbp", stdout=out)
        self.assertIn("created=1", out.getvalue())
        self.assertIn("failed=1", out.getvalue())

    @override_settings(**ENABLED)
    def test_fatal_error_fails_the_run(self):
        make_event()
        session = FakeSession(get=[FakeResponse(403, {"error": {"message": "x"}})])
        with mock.patch.object(gbp, "get_session", return_value=session):
            with self.assertRaises(CommandError):
                call_command("sync_events_to_gbp", stdout=StringIO())
