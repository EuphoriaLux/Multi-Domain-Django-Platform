"""
Tests for the changelog ingest endpoint (POST /api/admin/changelog/ingest/).

Covers:
- Bearer-token auth (missing / wrong / correct) and method guard
- A valid payload creating a *published* PatchRelease + PatchNote
- Idempotency: re-posting the same merge SHA adds no duplicate note
- Validation 400s (bad category, oversized title, invalid slug, bad JSON)
- Server-side scrubbing of secrets/PII even though the caller is "trusted"

The endpoint lives in the crush domain's URLconf, so these tests override
ROOT_URLCONF the same way the rest of the crush_lu suite does.

Run with: pytest crush_lu/tests/test_api_admin_changelog.py -v
"""
import json

from django.test import Client, TestCase, override_settings

from crush_lu.models import PatchNote, PatchRelease

INGEST_URL = "/api/admin/changelog/ingest/"
API_KEY = "test-admin-key"

CRUSH_URLS = {"ROOT_URLCONF": "azureproject.urls_crush", "ADMIN_API_KEY": API_KEY}


def _valid_payload(sha="abc123", slug="catchup-2026-06"):
    return {
        "release": {
            "version": "v1.8",
            "slug": slug,
            "title": "Crush Connect, reimagined",
            "hero_summary": "A warmer, privacy-first way to connect.",
            "released_on": "2026-06-18",
        },
        "notes": [
            {
                "category": "improvement",
                "title": "Crush Connect",
                "body": "You're in the mix — clearer, warmer wording across Connect.",
                "related_commits": [sha],
                "order": 0,
            }
        ],
    }


@override_settings(**CRUSH_URLS)
class ChangelogIngestAuthTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_missing_token_is_401(self):
        resp = self.client.post(
            INGEST_URL, data=json.dumps(_valid_payload()),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(PatchRelease.objects.count(), 0)

    def test_wrong_token_is_401(self):
        resp = self.client.post(
            INGEST_URL, data=json.dumps(_valid_payload()),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer not-the-key",
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(PatchRelease.objects.count(), 0)

    def test_get_is_405(self):
        resp = self.client.get(INGEST_URL, HTTP_AUTHORIZATION=f"Bearer {API_KEY}")
        self.assertEqual(resp.status_code, 405)


@override_settings(**CRUSH_URLS)
class ChangelogIngestWriteTests(TestCase):
    def setUp(self):
        self.client = Client()

    def _post(self, payload):
        return self.client.post(
            INGEST_URL, data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {API_KEY}",
        )

    def test_valid_payload_creates_published_release_and_note(self):
        resp = self._post(_valid_payload())
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertTrue(body["created"])
        self.assertEqual(body["notes_added"], 1)

        release = PatchRelease.objects.get(slug="catchup-2026-06")
        self.assertTrue(release.is_published)
        self.assertEqual(release.version, "v1.8")
        self.assertEqual(release.notes.count(), 1)
        note = release.notes.get()
        self.assertEqual(note.title, "Crush Connect")
        self.assertTrue(note.auto_generated)

    def test_published_release_is_visible_on_changelog_list(self):
        self._post(_valid_payload())
        resp = self.client.get("/changelog/", follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Crush Connect, reimagined")

    def test_same_merge_sha_is_idempotent(self):
        self.assertEqual(self._post(_valid_payload(sha="dup-sha")).status_code, 201)
        second = self._post(_valid_payload(sha="dup-sha"))
        self.assertEqual(second.status_code, 201)
        self.assertEqual(second.json()["notes_added"], 0)
        self.assertEqual(PatchRelease.objects.get(slug="catchup-2026-06").notes.count(), 1)

    def test_multiple_notes_sharing_merge_sha_are_all_kept(self):
        # A single PR (one merge SHA) may yield several user-facing notes; the
        # within-request dedupe must not drop notes 2..N just because they share
        # the SHA. A re-delivery of the same payload must then add nothing.
        payload = _valid_payload(sha="multi-sha")
        payload["notes"].append({
            "category": "feature",
            "title": "Quiz Night",
            "body": "A second user-facing item from the same merge.",
            "related_commits": ["multi-sha"],
            "order": 1,
        })
        first = self._post(payload)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json()["notes_added"], 2)
        self.assertEqual(PatchRelease.objects.get(slug="catchup-2026-06").notes.count(), 2)

        second = self._post(payload)
        self.assertEqual(second.json()["notes_added"], 0)
        self.assertEqual(PatchRelease.objects.get(slug="catchup-2026-06").notes.count(), 2)

    def test_release_fields_update_on_repost(self):
        self._post(_valid_payload())
        payload = _valid_payload(sha="another-sha")
        payload["release"]["title"] = "Crush Connect, even better"
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 201)
        release = PatchRelease.objects.get(slug="catchup-2026-06")
        self.assertEqual(release.title, "Crush Connect, even better")
        self.assertEqual(release.notes.count(), 2)  # different SHA → appended


@override_settings(**CRUSH_URLS)
class ChangelogIngestValidationTests(TestCase):
    def setUp(self):
        self.client = Client()

    def _post(self, payload_or_raw, raw=False):
        data = payload_or_raw if raw else json.dumps(payload_or_raw)
        return self.client.post(
            INGEST_URL, data=data, content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {API_KEY}",
        )

    def test_invalid_json_is_400(self):
        resp = self._post("{not json", raw=True)
        self.assertEqual(resp.status_code, 400)

    def test_invalid_category_is_400(self):
        payload = _valid_payload()
        payload["notes"][0]["category"] = "marketing"
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(PatchRelease.objects.count(), 0)

    def test_oversized_note_title_is_400(self):
        payload = _valid_payload()
        payload["notes"][0]["title"] = "x" * 200
        self.assertEqual(self._post(payload).status_code, 400)

    def test_invalid_slug_is_400(self):
        payload = _valid_payload(slug="not a slug!")
        self.assertEqual(self._post(payload).status_code, 400)

    def test_missing_release_is_400(self):
        self.assertEqual(self._post({"notes": []}).status_code, 400)


@override_settings(**CRUSH_URLS)
class ChangelogIngestScrubTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_secrets_are_scrubbed_server_side(self):
        payload = _valid_payload()
        payload["notes"][0]["body"] = (
            "Shipped! ping me at dev@crush.lu or see "
            "https://claude.ai/code/session_0123 Co-Authored-By: Claude"
        )
        resp = self.client.post(
            INGEST_URL, data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {API_KEY}",
        )
        self.assertEqual(resp.status_code, 201)
        body = PatchRelease.objects.get(slug="catchup-2026-06").notes.get().body
        self.assertNotIn("dev@crush.lu", body)
        self.assertNotIn("claude.ai/code", body)
        self.assertNotIn("Co-Authored-By", body)
        self.assertIn("Shipped!", body)
