"""Authorization, replay and uncertain external-write tests; no network calls."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "review", Path(__file__).with_name("review-service.py")
)
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        review.DB = Path(self.temp.name) / "reviews.sqlite"
        review.initialize()
        review.CHAT = "-123"
        review.REVIEWERS = {99}
        review.ENABLE_PUBLISH = True
        review.PREVIEW_ONLY = False
        review.POLL_CALLBACKS = True
        self.post = {
            "id": "123",
            "status": "pending_review",
            "buffer_id": "",
            "content": "Advice",
            "media_urls": ["https://cdn.crush.lu/a.png"],
            "media_url": "https://cdn.crush.lu/a.png",
            "platforms": ["instagram", "facebook"],
            "language": "en",
        }
        self.record = {
            "id": "a" * 32,
            "post": self.post,
            "script": {},
            "state": "pending_review",
            "revision": 1,
            "message_id": 456,
            "created": review.time.time(),
        }
        with review.database() as db:
            db.execute(
                "INSERT INTO reviews VALUES (?,?,?)",
                (self.record["id"], "run-1", json.dumps(self.record)),
            )

    def query(self, action="p"):
        return {
            "from": {"id": 99},
            "message": {"chat": {"id": -123}, "message_id": 456},
            "data": f"{action}:{self.record['id']}:1",
        }

    @patch.object(review, "hub")
    def test_unauthorized_and_stale_buttons_never_touch_hub(self, hub):
        query = self.query()
        query["from"]["id"] = 100
        self.assertIn("restricted", review.callback(query))
        query = self.query()
        query["data"] = query["data"][:-1] + "0"
        self.assertIn("already been used", review.callback(query))
        query = self.query()
        query["message"]["message_id"] = 987
        self.assertIn("older review", review.callback(query))
        hub.assert_not_called()

    @patch.object(review, "hub")
    def test_changed_copy_requires_new_review(self, hub):
        hub.return_value = {**self.post, "content": "Edited in Hub"}
        self.assertIn("changed", review.callback(self.query()))
        self.assertEqual(hub.call_count, 1)
        self.assertEqual(review.load(self.record["id"])["state"], "pending_review")

    @patch.object(review.requests, "get")
    @patch.object(review, "hub")
    def test_publish_uses_hub_contract_and_consumes_action_once(self, hub, profiles):
        hub.side_effect = [
            self.post,
            {**self.post, "status": "scheduled", "buffer_id": "ig,fb"},
        ]
        profiles.return_value = Mock(
            json=lambda: {
                "items": [
                    {"id": "ig", "service": "instagram"},
                    {"id": "fb", "service": "facebook"},
                ]
            }
        )
        self.assertIn("Scheduled", review.callback(self.query()))
        self.assertIn("already been used", review.callback(self.query()))
        self.assertEqual(hub.call_count, 2)
        self.assertEqual(hub.call_args.kwargs["json"]["status"], "scheduled")

    @patch.object(review.requests, "get", side_effect=TimeoutError("unknown response"))
    @patch.object(review, "hub")
    def test_uncertain_publish_cannot_be_replayed(self, hub, profiles):
        hub.return_value = self.post
        with self.assertRaises(TimeoutError):
            review.callback(self.query())
        self.assertEqual(review.load(self.record["id"])["state"], "publishing")
        self.assertIn("already been used", review.callback(self.query()))
        self.assertEqual(profiles.call_count, 1)

    @patch.object(review.requests, "post")
    @patch.object(review, "hub")
    def test_regeneration_claims_record_before_webhook_and_never_generates_copy(
        self, hub, request
    ):
        hub.return_value = self.post
        review.callback(self.query("r"))
        self.assertEqual(review.load(self.record["id"])["state"], "regenerating")
        request.assert_called_once()
        self.assertEqual(
            request.call_args.kwargs["json"], {"review_id": self.record["id"]}
        )

    def test_dead_letters_are_durable_deduplicated_and_redacted(self):
        review.TOKEN = "example-review-secret"
        envelope = {
            "workflow": "Coaching",
            "execution": "42",
            "node": "Gemini",
            "message": "Bearer example-review-secret https://example.com/?key=secret",
            "stack": "frame 1",
        }
        first = review.dead_letter(envelope)
        second = review.dead_letter(envelope)
        self.assertEqual(first, second)
        with review.database() as db:
            count, raw = db.execute("SELECT COUNT(*), record FROM errors").fetchone()
        self.assertEqual(count, 1)
        self.assertNotIn("example-review-secret", raw)
        self.assertNotIn("example.com", raw)

    @patch.object(review, "hub")
    @patch.object(review, "telegram", return_value={"message_id": 789})
    @patch.object(review.requests, "post")
    def test_preview_stores_ordered_deck_and_never_writes_hub(
        self, request, telegram, hub
    ):
        import base64

        review.PREVIEW_ONLY = True
        request.return_value = Mock(ok=True, json=lambda: {"ok": True})
        script = {
            "slides": [{"title": f"Card {i}"} for i in range(5)],
            "caption_en": "Advice",
            "caption_fr": "Conseil",
            "posting_date": "2026-10-08",
            "source_idea": {
                "facts": {
                    "source_url": "https://crush.lu/en/events/27/",
                    "checked_at": "2026-10-01T12:00:00+02:00",
                }
            },
        }
        body = {
            "run_key": "preview-1",
            "script": script,
            "images": [
                {"image_base64": base64.b64encode(bytes([i])).decode()}
                for i in range(5)
            ],
        }
        result = review.deliver(body)
        record = review.load(result["review_id"])
        message_text = telegram.call_args.args[1]["text"]
        self.assertIn("https://crush.lu/en/events/27/", message_text)
        self.assertIn("2026-10-08", message_text)
        self.assertEqual(record["script"]["source_idea"], script["source_idea"])
        self.assertEqual(
            [Path(p).read_bytes() for p in record["preview_files"]],
            [bytes([i]) for i in range(5)],
        )
        self.assertEqual(len(json.loads(request.call_args.kwargs["data"]["media"])), 5)
        buttons = telegram.call_args.args[1]["reply_markup"]["inline_keyboard"]
        self.assertEqual(len(buttons), 1)
        query = {
            "from": {"id": 99},
            "message": {"chat": {"id": -123}, "message_id": 789},
            "data": f"p:{record['id']}:1",
        }
        self.assertIn("disabled", review.callback(query))
        query["data"] = f"r:{record['id']}:1"
        review.callback(query)
        self.assertEqual(review.load(record["id"])["state"], "regenerating")
        result = review.deliver(
            {**body, "review_id": record["id"], "run_key": "preview-regen"}
        )
        self.assertEqual(review.load(result["review_id"])["revision"], 2)
        self.assertIn("already been used", review.callback(query))
        hub.assert_not_called()
        with patch.object(review, "POLL_CALLBACKS", False):
            review.notify_review(review.load(record["id"]))
        self.assertEqual(
            telegram.call_args.args[1]["reply_markup"]["inline_keyboard"], []
        )

    @patch.object(review, "hub")
    @patch.object(review, "telegram")
    @patch.object(review.requests, "post")
    def test_visual_comparison_sends_only_album_without_post_captions(
        self, request, telegram, hub
    ):
        import base64

        review.PREVIEW_ONLY = True
        request.return_value = Mock(
            ok=True, json=lambda: {"ok": True, "result": [{"message_id": 999}]}
        )
        script = {
            "slides": [{"title": f"Card {i}"} for i in range(5)],
            "caption_en": "Do not send English post",
            "caption_fr": "Do not send French post",
        }
        body = {
            "run_key": "visual-only",
            "script": script,
            "visual_only": True,
            "comparison_label": "Comparison A",
            "images": [
                {"image_base64": base64.b64encode(bytes([i])).decode()}
                for i in range(5)
            ],
        }
        result = review.deliver(body)
        record = review.load(result["review_id"])
        self.assertEqual(record["message_id"], 999)
        media = json.loads(request.call_args.kwargs["data"]["media"])
        self.assertEqual(media[0]["caption"], "Comparison A")
        self.assertEqual(len(media), 5)
        telegram.assert_not_called()
        hub.assert_not_called()
        review.PREVIEW_ONLY = False
        with self.assertRaisesRegex(ValueError, "requires preview"):
            review.deliver({**body, "run_key": "blocked-visual"})


if __name__ == "__main__":
    unittest.main()
