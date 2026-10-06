import struct
from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from hub.buffer_service import BufferServiceError, create_buffer_update
from hub.models import SocialPost
from hub.social_planning import review_fingerprint
from hub.social_video import validate_video
from hub.tests.test_social_carousel import SocialCarouselTests, png


def box(kind, payload):
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def mp4(width=720, duration=15, codec=b"avc1"):
    movie_header = bytes(12) + struct.pack(">II", 1000, duration * 1000)
    handler = bytes(8) + b"vide" + bytes(12)
    sample = bytes(4) + struct.pack(">I", 1) + box(codec, bytes(78))
    media = box(b"hdlr", handler) + box(b"minf", box(b"stbl", box(b"stsd", sample)))
    track = box(b"tkhd", bytes(76) + struct.pack(">II", width << 16, 1280 << 16)) + box(
        b"mdia", media
    )
    raw = (
        box(b"ftyp", b"isom" + bytes(4) + b"isomiso2avc1mp41")
        + box(b"moov", box(b"mvhd", movie_header) + box(b"trak", track))
        + box(b"mdat", bytes(16))
    )
    return SimpleUploadedFile("clip.mp4", raw, "video/mp4")


class SocialVideoTests(SocialCarouselTests):
    def test_mp4_metadata_is_bounded_and_requires_h264_vertical_track(self):
        self.assertEqual(validate_video(mp4()).detected_extension, ".mp4")
        for video in (
            mp4(width=1080),
            mp4(duration=61),
            mp4(codec=b"hvc1"),
            SimpleUploadedFile("a.mp4", b"not a video"),
        ):
            with self.assertRaises(ValidationError):
                validate_video(video)
        corrupt = mp4()
        corrupt.file.truncate(20)
        with self.assertRaises(ValidationError):
            validate_video(corrupt)

    def test_video_intake_is_idempotent_and_never_schedules(self):
        with TemporaryDirectory() as root, self.storage(root), patch(
            "hub.views_social.create_buffer_update"
        ) as buffer:

            def upload():
                return self.client.post(
                    "/hub/social/posts/",
                    {
                        "content": "Video caption",
                        "status": "pending_review",
                        "media_type": "video",
                        "video": mp4(),
                        "generation_key": "video:55",
                    },
                    format="multipart",
                )

            first, second = upload(), upload()
            self.assertEqual(first.status_code, 201, first.data)
            self.assertEqual(second.status_code, 200, second.data)
            self.assertEqual(first.data["post"]["id"], second.data["post"]["id"])
            self.assertEqual(first.data["post"]["media_urls"], [])
            self.assertTrue(first.data["post"]["media_url"].endswith(".mp4"))
            buffer.assert_not_called()

    def test_mixed_media_upload_is_rejected_before_writing(self):
        response = self.client.post(
            "/hub/social/posts/",
            {"media_type": "video", "video": mp4(), "images": [png()]},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(SocialPost.objects.exists())

    def test_video_approval_requires_confirmation_and_current_fingerprint(self):
        post = SocialPost.objects.create(
            user=self.user,
            media_type="video",
            media_url="https://cdn.crush.lu/clip.mp4",
            content="Video",
            status="pending_review",
        )
        with patch("hub.views_social.create_buffer_update") as buffer:
            response = self.client.patch(
                f"/hub/social/posts/{post.pk}/",
                {"status": "scheduled", "review_fingerprint": review_fingerprint(post)},
                format="json",
            )
        self.assertEqual(response.status_code, 400)
        buffer.assert_not_called()
        previous = review_fingerprint(post)
        post.media_type = "image"
        self.assertNotEqual(previous, review_fingerprint(post))

    @patch("hub.buffer_service._graphql")
    def test_buffer_uses_video_asset_and_reel_metadata(self, graphql):
        graphql.return_value = {
            "createPost": {"__typename": "PostActionSuccess", "post": {"id": "result"}}
        }
        with patch("hub.buffer_service._organization_id", return_value="org"):
            create_buffer_update(
                text="Video",
                profile_ids=["ig"],
                media_url="https://cdn.crush.lu/clip.mp4",
                media_type="video",
                profile_platforms={"ig": "instagram"},
                scheduled_at=(timezone.now() + timedelta(days=1)).isoformat(),
            )
        payload = graphql.call_args.args[1]["input"]
        self.assertEqual(
            payload["assets"], [{"video": {"url": "https://cdn.crush.lu/clip.mp4"}}]
        )
        self.assertEqual(payload["metadata"]["instagram"]["type"], "reel")
        graphql.reset_mock()
        with self.assertRaises(BufferServiceError):
            create_buffer_update(
                text="Video",
                profile_ids=["ig", "li"],
                media_url="https://cdn.crush.lu/clip.mp4",
                media_type="video",
                profile_platforms={"ig": "instagram", "li": "linkedin"},
            )
        graphql.assert_not_called()
