"""
Tests for the changelog / patch notes feature.

Covers:
- Unit tests for the commit classifier and secret scrubber
- The P1 guarantees the generator must hold on re-run:
  * ``is_published=True`` survives regeneration
  * Curator-edited notes (``auto_generated=False``) are not deleted
- ``changelog_list`` / ``changelog_detail`` view behaviour
  (HTMX fragment, filter, 404 on draft, pagination)

Run with: pytest crush_lu/tests/test_changelog.py -v
"""
from datetime import date
from unittest.mock import patch

from django.test import Client, TestCase

from crush_lu.management.commands.generate_patch_notes import (
    classify_commit,
    scrub,
)
from crush_lu.models import PatchNote, PatchNoteCategory, PatchRelease


class ClassifyCommitTests(TestCase):
    """Conventional-commit prefix → category/bucket mapping."""

    def test_feat_maps_to_feature(self):
        category, bucket, human = classify_commit("feat(quiz): add team mode")
        self.assertEqual(category, PatchNoteCategory.FEATURE)
        self.assertEqual(bucket, "Quiz Night")
        self.assertEqual(human, "add team mode")

    def test_fix_maps_to_fix(self):
        category, _bucket, _human = classify_commit("fix(coach): clear stale cache")
        self.assertEqual(category, PatchNoteCategory.FIX)

    def test_refactor_maps_to_improvement(self):
        category, _bucket, _human = classify_commit("refactor(profile): extract helper")
        self.assertEqual(category, PatchNoteCategory.IMPROVEMENT)

    def test_security_maps_to_under_hood(self):
        category, _bucket, _human = classify_commit("security: rotate keys")
        self.assertEqual(category, PatchNoteCategory.UNDER_HOOD)

    def test_non_conventional_defaults_to_improvement(self):
        category, bucket, human = classify_commit("Random uncategorised work")
        self.assertEqual(category, PatchNoteCategory.IMPROVEMENT)
        self.assertEqual(bucket, "Everything else")
        self.assertEqual(human, "Random uncategorised work")


class ScrubTests(TestCase):
    """Red-line filters must never leak PII / internal URLs."""

    def test_strips_emails(self):
        out = scrub("contact dev@example.com for help")
        self.assertNotIn("@", out)
        self.assertNotIn("example.com", out)

    def test_strips_azure_hosts(self):
        out = scrub("deployed to https://crush-staging.azurewebsites.net/ok")
        self.assertNotIn("azurewebsites", out)

    def test_strips_co_authored_by(self):
        out = scrub("feat: thing\nCo-authored-by: Alice <alice@x.com>")
        self.assertNotIn("Co-authored", out)
        self.assertNotIn("alice", out.lower())

    def test_strips_claude_session_link(self):
        out = scrub("foo https://claude.ai/code/session_01xxx bar")
        self.assertNotIn("claude.ai", out)

    def test_collapses_whitespace(self):
        out = scrub("alpha   beta\n\n  gamma")
        self.assertEqual(out, "alpha beta gamma")


class WriteReleaseIdempotencyTests(TestCase):
    """P1 guarantees: regeneration must not destroy editorial state."""

    COMMITS = [
        ("sha1", "2026-01-10", "feat(quiz): add team mode", ["crush_lu/quiz.py"]),
        ("sha2", "2026-01-09", "fix(coach): clear cache", ["crush_lu/coach.py"]),
    ]

    META = {
        "slug": "v1-0-launch",
        "version": "v1.0",
        "title": "Launch",
        "released_on": date(2026, 1, 15),
        "hero_summary": "",
    }

    def _run_write(self):
        from crush_lu.management.commands.generate_patch_notes import Command
        cmd = Command()
        cmd.stdout = type("S", (), {"write": lambda self, *_a, **_k: None})()
        cmd._write_release(self.META, self.COMMITS)

    def test_first_run_creates_draft(self):
        self._run_write()
        release = PatchRelease.objects.get(slug="v1-0-launch")
        self.assertFalse(release.is_published)
        self.assertTrue(release.notes.exists())
        self.assertTrue(release.notes.filter(auto_generated=True).exists())

    def test_rerun_preserves_is_published(self):
        """P1 #1: editor publishes, generator re-runs, publish flag survives."""
        self._run_write()
        PatchRelease.objects.filter(slug="v1-0-launch").update(is_published=True)

        self._run_write()

        release = PatchRelease.objects.get(slug="v1-0-launch")
        self.assertTrue(
            release.is_published,
            "Regenerating must not silently unpublish a live release.",
        )

    def test_rerun_preserves_curator_notes(self):
        """P1 #2: curator-edited notes survive regeneration."""
        self._run_write()
        release = PatchRelease.objects.get(slug="v1-0-launch")
        # Simulate a curator polishing one note.
        curated = release.notes.first()
        curated.title = "Hand-polished headline"
        curated.auto_generated = False
        curated.save()
        curated_id = curated.id

        self._run_write()

        # The curator's note must still exist with their title.
        surviving = PatchNote.objects.filter(pk=curated_id).first()
        self.assertIsNotNone(surviving, "Curator note was deleted on regen.")
        self.assertEqual(surviving.title, "Hand-polished headline")
        self.assertFalse(surviving.auto_generated)

    def test_rerun_does_not_duplicate_curated_buckets(self):
        """P1 #3: re-running must not add an auto note alongside a curated one."""
        self._run_write()
        release = PatchRelease.objects.get(slug="v1-0-launch")
        # Curator keeps one bucket's note as-is (no title change): its
        # (category, title) key should survive and block auto-regen for that key.
        curated = release.notes.first()
        bucket_title = curated.title
        bucket_category = curated.category
        curated.auto_generated = False
        curated.save()

        notes_before = release.notes.count()
        self._run_write()
        notes_after = PatchRelease.objects.get(slug="v1-0-launch").notes.count()

        self.assertEqual(
            notes_before,
            notes_after,
            "Regenerating duplicated notes for a bucket the curator already kept.",
        )
        same_bucket = PatchNote.objects.filter(
            release=release, category=bucket_category, title=bucket_title,
        )
        self.assertEqual(
            same_bucket.count(),
            1,
            "Bucket should hold the curator's note only — not curator + auto duplicate.",
        )
        self.assertFalse(same_bucket.get().auto_generated)


class ChangelogViewTests(TestCase):
    """Public /changelog/ list + detail view behaviour."""

    def setUp(self):
        self.client = Client()
        self.published = PatchRelease.objects.create(
            slug="v1-0-launch",
            version="v1.0",
            title="Launch day",
            hero_summary="We went live.",
            released_on=date(2026, 1, 15),
            is_published=True,
        )
        PatchNote.objects.create(
            release=self.published,
            category=PatchNoteCategory.FEATURE,
            title="New quiz mode",
            body="Team quiz goes live.",
        )
        self.draft = PatchRelease.objects.create(
            slug="v1-1-draft",
            version="v1.1",
            title="Draft",
            released_on=date(2026, 2, 1),
            is_published=False,
        )

    # Use literal /en/ URLs: testserver routes via DomainURLRoutingMiddleware
    # to azureproject.urls_crush (DEV_DEFAULT='crush.lu'), and that urlconf
    # mounts crush_lu under i18n_patterns. reverse() falls back to ROOT_URLCONF
    # at test-collection time and would emit /crush/changelog/ instead.
    LIST_URL = "/en/changelog/"
    DETAIL_URL = "/en/changelog/{slug}/"

    def test_list_renders_published(self):
        resp = self.client.get(self.LIST_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Launch day")
        self.assertNotContains(resp, "v1-1-draft")

    def test_list_htmx_returns_fragment_only(self):
        resp = self.client.get(self.LIST_URL, HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="changelog-timeline"')
        # The base template's site chrome (nav header) must not appear in
        # an HTMX fragment response.
        self.assertNotContains(resp, "<!DOCTYPE html>")

    def test_list_filters_by_category(self):
        resp = self.client.get(self.LIST_URL + "?category=fix")
        self.assertEqual(resp.status_code, 200)
        # The only note is a FEATURE, so filtering by FIX drops the release.
        self.assertNotContains(resp, "Launch day")

    def test_list_ignores_bogus_category(self):
        resp = self.client.get(self.LIST_URL + "?category=../../etc/passwd")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Launch day")

    def test_detail_404s_for_draft(self):
        resp = self.client.get(self.DETAIL_URL.format(slug="v1-1-draft"))
        self.assertEqual(resp.status_code, 404)

    def test_detail_renders_published(self):
        resp = self.client.get(self.DETAIL_URL.format(slug="v1-0-launch"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Launch day")
        self.assertContains(resp, "New quiz mode")


class GeneratePatchNotesDryRunTests(TestCase):
    """--dry-run must not commit any rows."""

    @patch("crush_lu.management.commands.generate_patch_notes.git_log")
    def test_dry_run_writes_no_rows(self, mock_git_log):
        from django.core.management import call_command
        mock_git_log.return_value = [
            ("sha1", "2026-01-10", "feat(quiz): add thing", ["crush_lu/quiz.py"]),
        ]
        # --milestones-only would need a milestone window; --since picks up
        # the default monthly catch-up path instead.
        call_command(
            "generate_patch_notes",
            "--since=2026-01-01",
            "--until=2026-01-31",
            "--dry-run",
        )
        self.assertEqual(PatchRelease.objects.count(), 0)
        self.assertEqual(PatchNote.objects.count(), 0)
