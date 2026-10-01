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
