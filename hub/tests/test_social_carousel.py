"""Ordered deck upload, regeneration safety and all-channel Buffer preflight."""

import io
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from hub.buffer_service import BufferServiceError, create_buffer_update
from hub.models import SocialPost


def png(index=0, size=(1080, 1080)):
    stream = io.BytesIO()
    Image.new("RGB", size, (index * 20, 40, 70)).save(stream, "PNG")
    return SimpleUploadedFile(f"slide-{index}.png", stream.getvalue(), "image/png")


class SocialCarouselTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="carousel-staff", email="staff@example.com", is_staff=True
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def storage(self, root):
        return override_settings(
            MEDIA_ROOT=root,
            MEDIA_URL="/media/",
            BACKEND_BASE_URL="https://crush.lu",
            STORAGES={
                "crush_media": {
                    "BACKEND": "django.core.files.storage.FileSystemStorage"
                }
            },
        )

    def test_upload_preserves_five_image_order_and_cover(self):
        with TemporaryDirectory() as root, self.storage(root):
            response = self.client.post(
                "/hub/social/posts/",
                {
                    "content": "A practical tip",
                    "status": "pending_review",
                    "images": [png(i) for i in range(5)],
                },
                format="multipart",
            )
            self.assertEqual(response.status_code, 201, response.data)
            post = response.data["post"]
            self.assertEqual(len(post["media_urls"]), 5)
            self.assertEqual(post["media_url"], post["media_urls"][0])
            from pathlib import Path

            for i, url in enumerate(post["media_urls"]):
                with Image.open(Path(root) / url.split("/media/")[1]) as image:
                    self.assertEqual(image.getpixel((0, 0)), (i * 20, 40, 70))

    def test_invalid_fifth_slide_writes_nothing(self):
        with TemporaryDirectory() as root, self.storage(root):
            response = self.client.post(
                "/hub/social/posts/",
                {"images": [png(i) for i in range(4)] + [png(4, (1024, 1024))]},
                format="multipart",
            )
            self.assertEqual(response.status_code, 400)
            self.assertFalse(SocialPost.objects.exists())
            from pathlib import Path

            self.assertEqual(list(Path(root).rglob("*.png")), [])

    def test_pending_deck_can_be_replaced_but_scheduled_deck_cannot(self):
        post = SocialPost.objects.create(
            user=self.user, status="pending_review", content="Copy"
        )
        with TemporaryDirectory() as root, self.storage(root):
            response = self.client.patch(
                f"/hub/social/posts/{post.pk}/",
                {"images": [png(i) for i in range(5)]},
                format="multipart",
            )
            self.assertEqual(response.status_code, 200, response.data)
            post.refresh_from_db()
            original = list(post.media_urls)
            post.status = "scheduled"
            post.buffer_id = "existing"
            post.save()
            response = self.client.patch(
                f"/hub/social/posts/{post.pk}/",
                {"images": [png(i) for i in range(5)]},
                format="multipart",
            )
            self.assertEqual(response.status_code, 409)
            post.refresh_from_db()
            self.assertEqual(post.media_urls, original)

    def test_partial_external_delivery_also_blocks_regeneration(self):
        post = SocialPost.objects.create(
            user=self.user, status="failed", buffer_id="partial"
        )
        response = self.client.patch(
            f"/hub/social/posts/{post.pk}/", {"images": [png()]}, format="multipart"
        )
        self.assertEqual(response.status_code, 409)

    @patch("hub.views_social.create_buffer_update")
    def test_stale_fingerprint_blocks_publication_under_the_post_lock(self, buffer):
        from hub.views_social import _social_review_fingerprint

        post = SocialPost.objects.create(
            user=self.user, content="Reviewed copy", status="pending_review"
        )
        approved = _social_review_fingerprint(post)
        post.content = "Copy edited after the review"
        post.save()
        response = self.client.patch(
            f"/hub/social/posts/{post.pk}/",
            {"status": "scheduled", "review_fingerprint": approved},
            format="json",
        )
        self.assertEqual(response.status_code, 409)
        buffer.assert_not_called()
        post.refresh_from_db()
        self.assertEqual(post.status, "pending_review")

    def test_deck_urls_respect_the_cover_database_column_length(self):
        response = self.client.post(
            "/hub/social/posts/",
            {"media_urls": ["https://cdn.crush.lu/" + "x" * 300 + ".png"]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_serializer_rejects_string_deck_and_inconsistent_cover(self):
        for payload in [
            {"media_urls": "https://cdn.crush.lu/a.png"},
            {
                "media_urls": ["https://cdn.crush.lu/a.png"],
                "media_url": "https://cdn.crush.lu/b.png",
            },
        ]:
            response = self.client.post("/hub/social/posts/", payload, format="json")
            self.assertEqual(response.status_code, 400)

    @patch("hub.buffer_service._graphql")
    def test_buffer_receives_five_ordered_assets_per_channel(self, graphql):
        graphql.return_value = {
            "createPost": {"__typename": "PostActionSuccess", "post": {"id": "ok"}}
        }
        urls = [f"https://cdn.crush.lu/slide-{i}.png" for i in range(5)]
        create_buffer_update(
            text="Advice",
            profile_ids=["ig", "fb"],
            media_urls=urls,
            profile_platforms={"ig": "instagram", "fb": "facebook"},
        )
        self.assertEqual(graphql.call_count, 2)
        for call in graphql.call_args_list:
            self.assertEqual(
                call.args[1]["input"]["assets"],
                [{"image": {"url": url}} for url in urls],
            )

    @patch("hub.buffer_service._graphql")
    def test_invalid_last_asset_fails_before_any_publication(self, graphql):
        with self.assertRaises(BufferServiceError):
            create_buffer_update(
                text="Advice",
                profile_ids=["fb", "ig"],
                media_urls=["https://cdn.crush.lu/ok.png", "http://localhost/bad.png"],
            )
        graphql.assert_not_called()
