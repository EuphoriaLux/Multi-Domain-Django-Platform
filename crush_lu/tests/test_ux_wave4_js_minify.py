"""UX Wave 4 · WP8a: minified alpine-components bundle + Sortable only where used.

Spec: ai-memory-hub/specs (UX Wave 4, finding 8-17).

- Outside DEBUG the Crush.lu shell (and both admin ``base_site.html``
  templates) serve ``alpine-components.min.js``, the committed output of
  ``npm run build:js``. DEBUG keeps the readable source.
- The .min.js must exist and match the source: under WhiteNoise's manifest
  storage a missing file 500s every page, and a stale one ships old code.
- Sortable (44 KB) loads only on the timeline-sort challenge, before Alpine.
"""

import re
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.template import engines
from django.test import RequestFactory, TestCase, override_settings
from django.urls import set_urlconf

from crush_lu.models import (
    ChapterProgress,
    CrushProfile,
    JourneyChallenge,
    JourneyChapter,
    JourneyConfiguration,
    JourneyProgress,
    SpecialUserExperience,
    UserDataConsent,
)

HOST = "crush.lu"
JS_DIR = Path(settings.BASE_DIR) / "crush_lu" / "static" / "crush_lu" / "js"
SOURCE = JS_DIR / "alpine-components.js"
MINIFIED = JS_DIR / "alpine-components.min.js"
MIN_SRC = "crush_lu/js/alpine-components.min.js"
SORTABLE = "vendor/sortable-1.15.2.min.js"
ADMIN_TEMPLATES = [
    Path(settings.BASE_DIR) / "crush_lu" / "templates" / "admin" / "base_site.html",
    Path(settings.BASE_DIR) / "core" / "templates" / "admin" / "base_site.html",
]
ALPINE_DATA_RE = re.compile(r"""Alpine\.data\(\s*["']([A-Za-z0-9_]+)["']""")


def _script_srcs(html):
    return re.findall(r"<script[^>]*\ssrc=\"([^\"]+)\"", html)


class MinifiedBundleFileTests(TestCase):
    def test_min_file_is_committed_and_found_by_finders(self):
        found = finders.find(MIN_SRC)
        self.assertIsNotNone(found, "alpine-components.min.js missing from static")
        size = Path(found).stat().st_size
        self.assertGreater(size, 0)
        self.assertLess(size, SOURCE.stat().st_size // 2)

    def test_min_file_registers_the_same_components_as_the_source(self):
        """Cheap staleness guard that needs no Node: every named component
        (and store) in the source must be in the minified build and vice
        versa. esbuild keeps these string literals verbatim."""
        # Drop comment lines: the source documents `Alpine.data("myTabs", ...)`
        # as an example, and esbuild strips comments.
        source = "\n".join(
            line
            for line in SOURCE.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith(("//", "*", "/*"))
        )
        minified = MINIFIED.read_text(encoding="utf-8")
        source_names = sorted(ALPINE_DATA_RE.findall(source))
        self.assertGreater(len(source_names), 50)
        self.assertEqual(source_names, sorted(ALPINE_DATA_RE.findall(minified)))
        store_re = re.compile(r"""Alpine\.store\(\s*["']([A-Za-z0-9_]+)["']""")
        self.assertEqual(
            sorted(set(store_re.findall(source))),
            sorted(set(store_re.findall(minified))),
        )

    def test_min_file_is_byte_identical_to_a_fresh_build(self):
        """Exact check when esbuild is installed (CI's JavaScript Lint job runs
        the same comparison with the pinned version from package.json)."""
        local = Path(settings.BASE_DIR) / "node_modules" / ".bin" / "esbuild"
        esbuild = str(local) if local.exists() else shutil.which("esbuild")
        if not esbuild:
            self.skipTest("esbuild not installed (npm ci)")
        pinned = re.search(
            r'"esbuild":\s*"([^"]+)"',
            (Path(settings.BASE_DIR) / "package.json").read_text(encoding="utf-8"),
        ).group(1)
        version = subprocess.run(
            [esbuild, "--version"], capture_output=True, text=True, check=True
        ).stdout.strip()
        if version != pinned:
            self.skipTest(f"esbuild {version} is not the pinned {pinned}")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.min.js"
            subprocess.run(
                [
                    esbuild,
                    str(SOURCE),
                    "--minify",
                    "--log-level=warning",
                    f"--outfile={out}",
                ],
                check=True,
            )
            self.assertEqual(
                out.read_bytes(),
                MINIFIED.read_bytes(),
                "alpine-components.min.js is stale: run `npm run build:js`",
            )


class ShellServesMinifiedBundleTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_non_debug_shell_serves_min_and_no_sortable(self):
        html = self.client.get("/en/", HTTP_HOST=HOST).content.decode()
        srcs = _script_srcs(html)
        self.assertTrue(any(MIN_SRC in s for s in srcs), srcs)
        self.assertFalse(any("alpine-components.js" in s for s in srcs), srcs)
        self.assertFalse(any(SORTABLE in s for s in srcs), srcs)

    @override_settings(DEBUG=True)
    def test_debug_shell_keeps_the_readable_source(self):
        # The test client's REMOTE_ADDR (127.0.0.1) is in INTERNAL_IPS, so the
        # debug context processor exposes `debug`, as on a dev machine.
        html = self.client.get("/en/", HTTP_HOST=HOST).content.decode()
        srcs = _script_srcs(html)
        self.assertTrue(
            any("crush_lu/js/alpine-components.js?v=" in s for s in srcs), srcs
        )
        self.assertFalse(any("alpine-components.min.js" in s for s in srcs), srcs)


class AdminBaseSiteTests(TestCase):
    def _render(self, path, debug):
        request = RequestFactory().get("/crush-admin/", HTTP_HOST=HOST)
        request.user = get_user_model()(is_staff=True, is_active=True)
        set_urlconf("azureproject.urls_crush")
        try:
            template = engines["django"].from_string(path.read_text("utf-8"))
            return template.render({"debug": debug}, request)
        finally:
            set_urlconf(None)

    def test_both_admin_base_sites_switch_on_debug(self):
        for path in ADMIN_TEMPLATES:
            prod = _script_srcs(self._render(path, debug=False))
            dev = _script_srcs(self._render(path, debug=True))
            self.assertTrue(any(MIN_SRC in s for s in prod), (path, prod))
            self.assertFalse(any("alpine-components.js" in s for s in prod), path)
            self.assertTrue(any("alpine-components.js" in s for s in dev), path)
            self.assertFalse(any("alpine-components.min" in s for s in dev), path)


class TimelineSortLoadsSortableTests(TestCase):
    def setUp(self):
        cache.clear()
        user = get_user_model().objects.create_user(
            username="timeline@example.com",
            email="timeline@example.com",
            password="testpass123",
            first_name="Tim",
            last_name="Line",
        )
        CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 5, 15),
            gender="M",
            location="Luxembourg",
            is_approved=True,
        )
        UserDataConsent.objects.update_or_create(
            user=user, defaults={"crushlu_consent_given": True}
        )
        experience = SpecialUserExperience.objects.create(
            first_name="Tim", last_name="Line", linked_user=user, is_active=True
        )
        journey = JourneyConfiguration.objects.create(
            special_experience=experience,
            journey_type="wonderland",
            journey_name="Tim's Journey",
            total_chapters=1,
            is_active=True,
        )
        chapter = JourneyChapter.objects.create(
            journey=journey,
            chapter_number=1,
            title="Chapter",
            theme="Mystery",
            story_introduction="Once upon a time",
            completion_message="Well done",
        )
        self.challenge = JourneyChallenge.objects.create(
            chapter=chapter,
            challenge_order=1,
            challenge_type="timeline_sort",
            question="Put these in order",
            options={"events": ["First", "Second", "Third"]},
            correct_answer="0,1,2",
            points_awarded=100,
        )
        progress = JourneyProgress.objects.create(
            user=user, journey=journey, current_chapter=1
        )
        ChapterProgress.objects.create(journey_progress=progress, chapter=chapter)
        self.client.force_login(user)

    def test_timeline_page_loads_sortable_before_alpine(self):
        response = self.client.get(
            f"/en/journey/chapter/1/challenge/{self.challenge.id}/", HTTP_HOST=HOST
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response, "crush_lu/journey/challenges/timeline_sort.html"
        )
        html = response.content.decode()
        self.assertIn('x-data="timelineSort"', html)
        srcs = _script_srcs(html)
        sortable = [i for i, s in enumerate(srcs) if SORTABLE in s]
        self.assertEqual(len(sortable), 1, srcs)
        components = [i for i, s in enumerate(srcs) if MIN_SRC in s]
        alpine = [i for i, s in enumerate(srcs) if "alpinejs-csp" in s]
        self.assertEqual(len(components), 1, srcs)
        self.assertEqual(len(alpine), 1, srcs)
        # Deferred scripts run in document order: Sortable must exist when
        # timelineSort.init() calls initSortable(), or dragging silently no-ops.
        self.assertLess(sortable[0], alpine[0])
        tag = re.search(r"<script[^>]*sortable-1\.15\.2\.min\.js[^>]*>", html)
        self.assertIn("defer", tag.group(0))
        self.assertIn("nonce=", tag.group(0))
