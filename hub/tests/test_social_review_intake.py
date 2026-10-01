"""Automatic intake remains review-only; approval sends the reviewed deck/time."""

import json
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import requests
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from hub.buffer_service import BufferDeliveryUnknown, _create_channel_post
from hub.models import SocialPost
from hub.social_planning import posting_proposal, review_fingerprint
from hub.tests import test_social_carousel as carousel
from hub.tests.test_social_carousel import png
from tempfile import TemporaryDirectory

PROFILES = [
    {"id": "ig", "service": "instagram", "timezone": "Europe/Luxembourg"},
    {"id": "fb", "service": "facebook", "timezone": "Europe/Luxembourg"},
]


class PostingProposalTests(SimpleTestCase):
    def test_common_buffer_slot_and_reserved_times(self):
        now = datetime(2026, 10, 5, 9, tzinfo=ZoneInfo("Europe/Luxembourg"))
        profiles = [
            {**p, "posting_schedule": [{"day": "mon", "times": ["17:00", "19:00"]}]}
            for p in PROFILES
        ]
        first = posting_proposal(now=now, profiles=profiles, occupied=[])
        self.assertEqual(first["scheduled_for"], "2026-10-05T17:00:00+02:00")
        second = posting_proposal(
            now=now,
            profiles=profiles,
            occupied=[datetime.fromisoformat(first["scheduled_for"])],
        )
        self.assertEqual(second["scheduled_for"], "2026-10-05T19:00:00+02:00")

    def test_review_window_and_winter_timezone(self):
        now = datetime(2026, 10, 26, 17, tzinfo=ZoneInfo("Europe/Luxembourg"))
        proposal = posting_proposal(now=now, occupied=[])
        self.assertEqual(proposal["scheduled_for"], "2026-10-27T18:30:00+01:00")

    def test_expired_external_event_cannot_receive_new_time(self):
        post = SocialPost(source_metadata={"event_date": "2026-10-05"})
        proposal = posting_proposal(
            post,
            now=datetime(2026, 10, 5, 9, tzinfo=ZoneInfo("Europe/Luxembourg")),
            occupied=[],
        )
        self.assertIsNone(proposal["scheduled_for"])

    def test_fingerprint_covers_time_provenance_and_equivalent_instants(self):
        post = SocialPost(
            content="Copy",
            source_metadata={"title": "Source"},
            scheduled_for=timezone.now(),
        )
        original = review_fingerprint(post)
        post.scheduled_for = post.scheduled_for.astimezone(
            ZoneInfo("Europe/Luxembourg")
        )
        self.assertEqual(review_fingerprint(post), original)
        post.scheduled_for += timedelta(minutes=1)
        self.assertNotEqual(review_fingerprint(post), original)
        post.scheduled_for -= timedelta(minutes=1)
        post.source_metadata["title"] = "Different source"
        self.assertNotEqual(review_fingerprint(post), original)

    @patch("hub.buffer_service.requests.post", side_effect=requests.Timeout)
    @patch("hub.buffer_service.settings")
    def test_lost_mutation_response_requires_reconciliation(self, settings, request):
        settings.BUFFER_API_KEY = "test-only"
        settings.BUFFER_TIMEOUT_SECONDS = 2
        with self.assertRaises(BufferDeliveryUnknown):
            _create_channel_post(
                channel_id="fb", text="Advice", scheduled_at=None, media_url=None
            )


class HubReviewIntakeTests(TestCase):
    setUp = carousel.SocialCarouselTests.setUp
    storage = carousel.SocialCarouselTests.storage

    def post_for_review(self):
        return SocialPost.objects.create(
            user=self.user,
            content="Reviewed caption",
            status="pending_review",
            platforms=["instagram", "facebook"],
            media_urls=[f"https://cdn.crush.lu/{i}.png" for i in range(5)],
            media_url="https://cdn.crush.lu/0.png",
            scheduled_for=timezone.now() + timedelta(days=2),
        )

    def approval(self, post):
        return {
            "status": "scheduled",
            "approval_mode": "hub",
            "review_fingerprint": review_fingerprint(post),
            "buffer_profile_ids": ["ig", "fb"],
            "buffer_profile_platforms": {"ig": "facebook", "fb": "instagram"},
        }

    @patch("hub.views_social.create_buffer_update")
    def test_retried_upload_returns_original_deck_without_dispatch(self, buffer):
        with TemporaryDirectory() as root, self.storage(root):
            data = {
                "generation_key": "n8n:carousel:123",
                "status": "pending_review",
                "content": "Original caption",
                "platforms": json.dumps(["instagram", "facebook"]),
                "source_metadata": json.dumps(
                    {
                        "source_url": "https://crush.lu/en/events/27/",
                        "title": "Local source",
                    }
                ),
                "images": [png(i) for i in range(5)],
            }
            first = self.client.post("/hub/social/posts/", data, format="multipart")
            self.assertEqual(first.status_code, 201, first.data)
            data.update(
                content="Retried changed caption", images=[png(4 - i) for i in range(5)]
            )
            second = self.client.post("/hub/social/posts/", data, format="multipart")
            self.assertEqual(second.status_code, 200, second.data)
            self.assertTrue(second.data["cached"])
            self.assertEqual(first.data["post"]["id"], second.data["post"]["id"])
            self.assertEqual(second.data["post"]["content"], "Original caption")
            self.assertEqual(
                first.data["post"]["media_urls"], second.data["post"]["media_urls"]
            )
            self.assertEqual(SocialPost.objects.count(), 1)
            self.assertIsNotNone(first.data["post"]["scheduled_for"])
        buffer.assert_not_called()

    @patch("hub.views_social.create_buffer_update")
    def test_fingerprint_rejects_changed_time_or_provenance(self, buffer):
        post = self.post_for_review()
        for change in (
            {"scheduled_for": (timezone.now() + timedelta(days=9)).isoformat()},
            {"source_metadata": {"title": "Swapped after review"}},
        ):
            response = self.client.patch(
                f"/hub/social/posts/{post.pk}/",
                {**self.approval(post), **change},
                format="json",
            )
            self.assertEqual(response.status_code, 409, change)
        buffer.assert_not_called()

    def test_uncertain_event_delivery_reserves_its_platforms(self):
        from hub.views_social import _event_post_dispatched_platforms

        post = SocialPost(
            status="failed",
            platforms=["instagram", "facebook"],
            buffer_delivery_uncertain=True,
        )
        self.assertEqual(
            _event_post_dispatched_platforms(post), {"instagram", "facebook"}
        )
        post.buffer_delivery_uncertain = False
        self.assertEqual(_event_post_dispatched_platforms(post), set())

    @patch("hub.views_social.list_buffer_profiles", return_value=PROFILES)
    @patch(
        "hub.views_social.create_buffer_update",
        return_value={"buffer_id": "ig-post,fb-post"},
    )
    def test_one_click_sends_exact_reviewed_deck_and_time_once(self, buffer, profiles):
        post = self.post_for_review()
        response = self.client.patch(
            f"/hub/social/posts/{post.pk}/", self.approval(post), format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            buffer.call_args.kwargs["scheduled_at"], post.scheduled_for.isoformat()
        )
        self.assertEqual(buffer.call_args.kwargs["media_urls"], post.media_urls)
        self.assertEqual(
            buffer.call_args.kwargs["profile_platforms"],
            {"ig": "instagram", "fb": "facebook"},
        )
        repeated = self.approval(post)
        repeated.pop("buffer_profile_platforms")
        again = self.client.patch(
            f"/hub/social/posts/{post.pk}/", repeated, format="json"
        )
        self.assertEqual(again.status_code, 200, again.data)
        self.assertEqual(buffer.call_count, 1)

    @patch(
        "hub.views_social.list_buffer_profiles",
        return_value=[{**p, "is_disconnected": True} for p in PROFILES],
    )
    @patch("hub.views_social.create_buffer_update")
    def test_disconnected_account_blocks_approval_without_changing_post(
        self, buffer, profiles
    ):
        post = self.post_for_review()
        response = self.client.patch(
            f"/hub/social/posts/{post.pk}/", self.approval(post), format="json"
        )
        self.assertEqual(response.status_code, 409)
        post.refresh_from_db()
        self.assertEqual(post.status, "pending_review")
        buffer.assert_not_called()

    @patch("hub.views_social.list_buffer_profiles", return_value=PROFILES)
    @patch(
        "hub.views_social.create_buffer_update",
        side_effect=BufferDeliveryUnknown("Timeout"),
    )
    def test_uncertain_buffer_response_blocks_repeat_and_media_edits(
        self, buffer, profiles
    ):
        post = self.post_for_review()
        first = self.client.patch(
            f"/hub/social/posts/{post.pk}/", self.approval(post), format="json"
        )
        self.assertEqual(first.status_code, 502)
        post.refresh_from_db()
        self.assertTrue(post.buffer_delivery_uncertain)
        again = self.client.patch(
            f"/hub/social/posts/{post.pk}/", self.approval(post), format="json"
        )
        self.assertEqual(again.status_code, 409)
        edit = self.client.patch(
            f"/hub/social/posts/{post.pk}/", {"content": "Different"}, format="json"
        )
        self.assertEqual(edit.status_code, 409)
        self.assertEqual(buffer.call_count, 1)

    @patch("hub.views_social.list_buffer_profiles", return_value=PROFILES)
    @patch(
        "hub.views_social.create_buffer_update",
        side_effect=BufferDeliveryUnknown("Timeout"),
    )
    def test_reconciled_uncertain_delivery_can_be_reviewed_again(
        self, buffer, profiles
    ):
        post = self.post_for_review()
        self.client.patch(
            f"/hub/social/posts/{post.pk}/", self.approval(post), format="json"
        )
        post.refresh_from_db()
        self.assertTrue(post.buffer_delivery_uncertain)
        url = f"/hub/social/posts/{post.pk}/"
        bad = self.client.patch(url, {"reconcile": "delivered"}, format="json")
        self.assertEqual(bad.status_code, 400)
        ok = self.client.patch(url, {"reconcile": "not_delivered"}, format="json")
        self.assertEqual(ok.status_code, 200)
        post.refresh_from_db()
        self.assertFalse(post.buffer_delivery_uncertain)
        self.assertEqual(post.status, "draft")
        self.assertIn("Staff confirmed", post.status_history[-1]["note"])
        again = self.client.patch(url, {"reconcile": "not_delivered"}, format="json")
        self.assertEqual(again.status_code, 409)

    def test_partial_failure_with_unknown_channel_stays_uncertain(self):
        from hub.buffer_service import BufferPartialFailure
        from hub.views_social import _event_post_dispatched_platforms

        post = self.post_for_review()
        post.buffer_profile_ids = ["ig", "fb"]
        post.save()
        failure = BufferPartialFailure(["p1"], ["ig"], uncertain=True)
        with (
            patch("hub.views_social.list_buffer_profiles", return_value=PROFILES),
            patch("hub.views_social.create_buffer_update", side_effect=failure),
        ):
            response = self.client.patch(
                f"/hub/social/posts/{post.pk}/", self.approval(post), format="json"
            )
        self.assertEqual(response.status_code, 502)
        post.refresh_from_db()
        self.assertTrue(post.buffer_delivery_uncertain)
        self.assertEqual(
            _event_post_dispatched_platforms(post), {"instagram", "facebook"}
        )

    def test_buffer_partial_failure_flags_unknown_channel(self):
        from hub.buffer_service import BufferPartialFailure, create_buffer_update

        calls = iter(["p1", BufferDeliveryUnknown("Timeout")])

        def post_channel(**kwargs):
            result = next(calls)
            if isinstance(result, Exception):
                raise result
            return result

        with patch("hub.buffer_service._create_channel_post", side_effect=post_channel):
            with self.assertRaises(BufferPartialFailure) as raised:
                create_buffer_update(
                    text="x",
                    profile_ids=["ig", "fb"],
                    profile_platforms={"ig": "facebook", "fb": "facebook"},
                    media_url="https://cdn.crush.lu/a.png",
                )
        self.assertTrue(raised.exception.uncertain)
        self.assertEqual(raised.exception.created_profile_ids, ["ig"])

    def test_future_drafts_reserve_their_proposed_slot(self):
        from hub.social_planning import reserved_times

        slot = timezone.now() + timedelta(days=3)
        SocialPost.objects.create(user=self.user, content="Draft", scheduled_for=slot)
        self.assertIn(slot, reserved_times())

    @patch("hub.buffer_service.requests.post")
    def test_graphql_execution_error_after_mutation_is_uncertain(self, post):
        from hub.buffer_service import BufferServiceError, _graphql

        query = "mutation CreatePost($input: CreatePostInput!) { createPost { x } }"
        post.return_value.raise_for_status.return_value = None
        post.return_value.json.return_value = {
            "errors": [{"message": "resolver failed", "path": ["createPost", "post"]}]
        }
        with self.settings(BUFFER_API_KEY="k"):
            with self.assertRaises(BufferDeliveryUnknown):
                _graphql(query)
            post.return_value.json.return_value = {
                "errors": [{"message": "invalid input"}]
            }
            with self.assertRaises(BufferServiceError) as raised:
                _graphql(query)
        self.assertNotIsInstance(raised.exception, BufferDeliveryUnknown)

    def test_reconcile_keeps_known_deliveries_of_a_partial_failure(self):
        post = self.post_for_review()
        post.status = "failed"
        post.buffer_delivery_uncertain = True
        post.buffer_id = "abc"
        post.dispatched_platforms = ["instagram"]
        post.save()
        response = self.client.patch(
            f"/hub/social/posts/{post.pk}/",
            {"reconcile": "not_delivered"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        post.refresh_from_db()
        self.assertFalse(post.buffer_delivery_uncertain)
        self.assertEqual(post.status, "failed")
        self.assertEqual(post.buffer_id, "abc")
        self.assertEqual(post.dispatched_platforms, ["instagram"])
        from hub.views_social import _event_post_dispatched_platforms

        self.assertEqual(_event_post_dispatched_platforms(post), {"instagram"})

    def test_reconcile_requires_an_uncertain_delivery(self):
        post = self.post_for_review()
        response = self.client.patch(
            f"/hub/social/posts/{post.pk}/",
            {"reconcile": "not_delivered"},
            format="json",
        )
        self.assertEqual(response.status_code, 409)

    def test_intake_rejects_out_of_range_posting_date(self):
        for value in ("9999-12-31", "2001-01-01"):
            response = self.client.post(
                "/hub/social/posts/",
                {"content": "x", "source_metadata": {"posting_date": value}},
                format="json",
            )
            self.assertEqual(response.status_code, 400, value)

    def test_automation_intake_rechecks_a_taken_proposed_slot(self):
        taken = timezone.now() + timedelta(days=3)
        SocialPost.objects.create(
            user=self.user, content="Held", status="pending_review", scheduled_for=taken
        )
        response = self.client.post(
            "/hub/social/posts/",
            {
                "content": "Auto",
                "generation_key": "run-1",
                "scheduled_for": taken.isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        got = datetime.fromisoformat(response.json()["post"]["scheduled_for"])
        self.assertGreaterEqual(abs(got - taken), timedelta(hours=1))

    @patch("hub.views_social.create_buffer_update")
    def test_automation_posts_always_require_the_review_fingerprint(self, buffer):
        post = self.post_for_review()
        post.generation_key = "run-fp"
        post.save()
        response = self.client.patch(
            f"/hub/social/posts/{post.pk}/",
            {
                "status": "scheduled",
                "buffer_profile_ids": ["ig", "fb"],
                "buffer_profile_platforms": {"ig": "instagram", "fb": "facebook"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        buffer.assert_not_called()

    def test_automation_intake_rejects_a_schedule_beyond_the_window(self):
        response = self.client.post(
            "/hub/social/posts/",
            {
                "content": "x",
                "generation_key": "run-far",
                "scheduled_for": "9999-12-31T18:30:00Z",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_orphaned_uncertain_post_is_never_retryable(self):
        from crush_lu.models import MeetupEvent

        now = timezone.now()
        event = MeetupEvent.objects.create(
            title="Soirée",
            description="Une soirée.",
            event_type="speed_dating",
            location="Luxembourg",
            address="Rue 1",
            date_time=now + timedelta(days=7),
            registration_deadline=now + timedelta(days=5),
            is_published=True,
        )
        post = SocialPost.objects.create(
            user=self.user,
            content="Uncertain",
            status="failed",
            source_event=event,
            buffer_delivery_uncertain=True,
            scheduled_for=now + timedelta(days=2),
        )
        event.delete()
        post.refresh_from_db()
        self.assertTrue(post.source_metadata["source_event_deleted"])
        url = f"/hub/social/posts/{post.pk}/"
        ok = self.client.patch(url, {"reconcile": "not_delivered"}, format="json")
        self.assertEqual(ok.status_code, 200)
        post.refresh_from_db()
        self.assertEqual(post.status, "failed")
        retry = self.client.patch(
            url,
            {"status": "scheduled", "buffer_profile_ids": ["ig"]},
            format="json",
        )
        self.assertEqual(retry.status_code, 409)
        echoed = self.client.patch(
            url, {"source_metadata": post.source_metadata}, format="json"
        )
        self.assertEqual(echoed.status_code, 200)

    def test_deleting_an_event_keeps_posts_with_uncertain_delivery(self):
        from crush_lu.models import MeetupEvent

        now = timezone.now()
        event = MeetupEvent.objects.create(
            title="Soirée",
            description="Une soirée.",
            event_type="speed_dating",
            location="Luxembourg",
            address="Rue 1",
            date_time=now + timedelta(days=7),
            registration_deadline=now + timedelta(days=5),
            is_published=True,
        )
        uncertain = SocialPost.objects.create(
            user=self.user,
            content="Uncertain",
            status="failed",
            source_event=event,
            buffer_delivery_uncertain=True,
        )
        plain = SocialPost.objects.create(
            user=self.user, content="Plain", status="draft", source_event=event
        )
        event.delete()
        self.assertTrue(SocialPost.objects.filter(pk=uncertain.pk).exists())
        self.assertFalse(SocialPost.objects.filter(pk=plain.pk).exists())

    @patch("hub.views_social.create_buffer_update")
    def test_expired_source_or_stale_time_blocks_approval(self, buffer):
        post = self.post_for_review()
        post.source_metadata = {
            "event_date": (timezone.now() - timedelta(days=1)).date().isoformat()
        }
        post.save()
        response = self.client.patch(
            f"/hub/social/posts/{post.pk}/", self.approval(post), format="json"
        )
        self.assertEqual(response.status_code, 400)
        buffer.assert_not_called()

    @patch("hub.views_social.list_buffer_profiles", return_value=PROFILES)
    def test_planning_contract_is_staff_only_and_read_only(self, profiles):
        response = self.client.get("/hub/social/planning-slot/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["intake_version"], 1)
        self.assertTrue(response.data["review_in_hub"])
        self.assertFalse(SocialPost.objects.exists())
        self.user.is_staff = False
        self.user.save()
        self.assertEqual(self.client.get("/hub/social/planning-slot/").status_code, 403)

    def test_bad_provenance_rejected_without_writing(self):
        for metadata in [
            {"source_url": "http://example.com"},
            {"event_date": "bad"},
            {"secret": "no"},
        ]:
            response = self.client.post(
                "/hub/social/posts/", {"source_metadata": metadata}, format="json"
            )
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(SocialPost.objects.exists())

    @patch("hub.views_social.create_buffer_update")
    def test_editing_returns_a_new_review_and_stale_edits_cannot_overwrite_it(
        self, buffer
    ):
        post = self.post_for_review()
        original = review_fingerprint(post)
        response = self.client.patch(
            f"/hub/social/posts/{post.pk}/",
            {
                "content": "Edited caption",
                "status": "pending_review",
                "edit_fingerprint": original,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotEqual(response.data["post"]["review_fingerprint"], original)
        stale = self.client.patch(
            f"/hub/social/posts/{post.pk}/",
            {"content": "Stale overwrite", "edit_fingerprint": original},
            format="json",
        )
        self.assertEqual(stale.status_code, 409)
        post.refresh_from_db()
        self.assertEqual(post.content, "Edited caption")
        buffer.assert_not_called()
