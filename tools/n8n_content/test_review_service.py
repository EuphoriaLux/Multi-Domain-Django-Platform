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


if __name__ == "__main__":
    unittest.main()
