"""UX Wave 4 · WP8a/WP8b: minified Alpine bundles + Sortable only where used.

Spec: ai-memory-hub/specs (UX Wave 4, finding 8-17).

- Outside DEBUG the Crush.lu shell serves ``js/alpine/core.min.js`` and both
  admin ``base_site.html`` templates ``js/alpine/coach.min.js``: the committed
  output of ``npm run build:js`` (WP8b split the old single file into the
  core/coach/quiz/journey/connect entries). DEBUG keeps the readable ES-module
  sources.
- Every .min.js must exist and match its source: under WhiteNoise's manifest
  storage a missing file 500s every page, and a stale one ships old code.
- Sortable (44 KB) loads only on the timeline-sort challenge, before Alpine.
"""

import json
import re
import shlex
import shutil
import subprocess
import tempfile
from datetime import date
from html.parser import HTMLParser
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
ALPINE_DIR = (
    Path(settings.BASE_DIR) / "crush_lu" / "static" / "crush_lu" / "js" / "alpine"
)
BUNDLES = ["core", "coach", "quiz", "journey", "connect"]
SORTABLE = "vendor/sortable-1.15.2.min.js"
ADMIN_TEMPLATES = [
    Path(settings.BASE_DIR) / "crush_lu" / "templates" / "admin" / "base_site.html",
    Path(settings.BASE_DIR) / "core" / "templates" / "admin" / "base_site.html",
]
ALPINE_DATA_RE = re.compile(r"""Alpine\.data\(\s*["']([A-Za-z0-9_]+)["']""")
STORE_RE = re.compile(r"""Alpine\.store\(\s*["']([A-Za-z0-9_]+)["']""")


def min_src(bundle):
    return f"crush_lu/js/alpine/{bundle}.min.js"


def dev_src(bundle):
    return f"crush_lu/js/alpine/{bundle}.js?v="


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.scripts.append(dict(attrs))


def _scripts(html):
    parser = _Scripts()
    parser.feed(html)
    return [s for s in parser.scripts if s.get("src")]


def _script_srcs(html):
    return [s["src"] for s in _scripts(html)]


def _code(path):
    # Drop comment lines: the helpers document `Alpine.data("myTabs", ...)`
    # as an example, and esbuild strips comments.
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith(("//", "*", "/*"))
    )


class MinifiedBundleFileTests(TestCase):
    def test_min_files_are_committed_and_found_by_finders(self):
        for bundle in BUNDLES:
            found = finders.find(min_src(bundle))
            self.assertIsNotNone(found, f"{bundle}.min.js missing from static")
            size = Path(found).stat().st_size
            self.assertGreater(size, 0)
            self.assertLess(size, (ALPINE_DIR / f"{bundle}.js").stat().st_size // 2)

    def test_min_files_register_the_same_components_as_the_sources(self):
        """Cheap staleness guard that needs no Node: every named component
        (and store) in each source must be in its minified build and vice
        versa. esbuild keeps these string literals verbatim."""
        total = 0
        for bundle in BUNDLES:
            source = _code(ALPINE_DIR / f"{bundle}.js")
            minified = (ALPINE_DIR / f"{bundle}.min.js").read_text(encoding="utf-8")
            source_names = sorted(ALPINE_DATA_RE.findall(source))
            self.assertTrue(source_names, bundle)
            total += len(source_names)
            self.assertEqual(
                source_names, sorted(ALPINE_DATA_RE.findall(minified)), bundle
            )
            self.assertEqual(
                sorted(set(STORE_RE.findall(source))),
                sorted(set(STORE_RE.findall(minified))),
                bundle,
            )
        self.assertGreater(total, 100)

    def test_min_files_are_byte_identical_to_a_fresh_build(self):
        """Exact check when esbuild is installed (CI's JavaScript Lint job runs
        the same comparison with the pinned version from package.json). Runs
        the `build:js` script's own arguments, redirected to a temp dir."""
        # shutil.which honours PATHEXT: on Windows it returns esbuild.cmd, not
        # npm's extensionless sh shim, which subprocess cannot execute.
        local_bin = Path(settings.BASE_DIR) / "node_modules" / ".bin"
        esbuild = shutil.which("esbuild", path=str(local_bin)) or shutil.which(
            "esbuild"
        )
        if not esbuild:
            self.skipTest("esbuild not installed (npm ci)")
        package = json.loads(
            (Path(settings.BASE_DIR) / "package.json").read_text(encoding="utf-8")
        )
        pinned = package["devDependencies"]["esbuild"]
        try:
            version = subprocess.run(
                [esbuild, "--version"], capture_output=True, text=True, check=True
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            self.skipTest(f"esbuild at {esbuild} is not runnable: {exc}")
        if version != pinned:
            self.skipTest(f"esbuild {version} is not the pinned {pinned}")
        args = shlex.split(package["scripts"]["build:js"])
        self.assertEqual(args[0], "esbuild")
        entries = [a for a in args[1:] if not a.startswith("--")]
        self.assertEqual(
            entries, [f"crush_lu/static/crush_lu/js/alpine/{b}.js" for b in BUNDLES]
        )
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                [esbuild]
                + [
                    f"--outdir={tmp}" if a.startswith("--outdir=") else a
                    for a in args[1:]
                ],
                cwd=settings.BASE_DIR,
                check=True,
            )
            for bundle in BUNDLES:
                self.assertEqual(
                    (Path(tmp) / f"{bundle}.min.js").read_bytes(),
                    (ALPINE_DIR / f"{bundle}.min.js").read_bytes(),
                    f"{bundle}.min.js is stale: run `npm run build:js`",
                )
            self.assertEqual(
                sorted(p.name for p in Path(tmp).iterdir()),
                sorted(
                    p.name
                    for p in [
                        *ALPINE_DIR.glob("*.min.js"),
                        *ALPINE_DIR.glob("*.min.js.map"),
                    ]
                ),
            )


class ShellServesMinifiedBundleTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_non_debug_shell_serves_core_min_and_no_sortable(self):
        html = self.client.get("/en/", HTTP_HOST=HOST).content.decode()
        srcs = _script_srcs(html)
        self.assertTrue(any(min_src("core") in s for s in srcs), srcs)
        self.assertFalse(any("alpine/core.js" in s for s in srcs), srcs)
        self.assertFalse(any("alpine-components" in s for s in srcs), srcs)
        for bundle in BUNDLES[1:]:
            self.assertFalse(any(f"alpine/{bundle}." in s for s in srcs), srcs)
        self.assertFalse(any(SORTABLE in s for s in srcs), srcs)
        core = next(s for s in _scripts(html) if min_src("core") in s["src"])
        self.assertIn("defer", core)
        self.assertIn("nonce", core)
        self.assertNotIn("type", core)
        # Build notes are template comments, not bytes shipped on every page.
        self.assertNotIn("npm run build:js", html)

    @override_settings(DEBUG=True)
    def test_debug_shell_keeps_the_readable_source(self):
        # `assets_dev` follows settings.DEBUG alone (WP11b), not INTERNAL_IPS.
        html = self.client.get("/en/", HTTP_HOST=HOST).content.decode()
        srcs = _script_srcs(html)
        self.assertTrue(any(dev_src("core") in s for s in srcs), srcs)
        self.assertFalse(any(".min.js" in s and "alpine/" in s for s in srcs), srcs)
        # The source imports ./shared.js, so it must load as a module (which
        # is deferred and keeps document order with the defer scripts).
        core = next(s for s in _scripts(html) if dev_src("core") in s["src"])
        self.assertEqual(core.get("type"), "module")
        self.assertIn("nonce", core)
        srcs = _script_srcs(html)
        self.assertLess(
            next(i for i, s in enumerate(srcs) if dev_src("core") in s),
            next(i for i, s in enumerate(srcs) if "alpinejs-csp" in s),
        )


class AdminBaseSiteTests(TestCase):
    def _render(self, path, debug):
        request = RequestFactory().get("/crush-admin/", HTTP_HOST=HOST)
        request.user = get_user_model().objects.get_or_create(
            username="coach@example.com", defaults={"is_staff": True}
        )[0]
        set_urlconf("azureproject.urls_crush")
        try:
            template = engines["django"].from_string(path.read_text("utf-8"))
            return template.render({"assets_dev": debug}, request)
        finally:
            set_urlconf(None)

    def test_both_admin_base_sites_load_the_coach_bundle_and_switch_on_debug(self):
        for path in ADMIN_TEMPLATES:
            prod = _script_srcs(self._render(path, debug=False))
            dev = _script_srcs(self._render(path, debug=True))
            self.assertTrue(any(min_src("coach") in s for s in prod), (path, prod))
            self.assertFalse(any("alpine/coach.js" in s for s in prod), path)
            self.assertTrue(any(dev_src("coach") in s for s in dev), (path, dev))
            self.assertFalse(any("alpine/coach.min" in s for s in dev), path)
            # Coach Panel pages use no core component (see test_ux_wave4_js_split).
            self.assertFalse(any("alpine/core" in s for s in prod + dev), path)
            for srcs in (prod, dev):
                coach = next(i for i, s in enumerate(srcs) if "alpine/coach" in s)
                alpine = next(i for i, s in enumerate(srcs) if "@alpinejs/csp" in s)
                self.assertLess(coach, alpine, path)


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
        # Template comments must not leak into the page as visible text.
        self.assertNotIn("initSortable", html)
        srcs = _script_srcs(html)
        sortable = [i for i, s in enumerate(srcs) if SORTABLE in s]
        self.assertEqual(len(sortable), 1, srcs)
        core = [i for i, s in enumerate(srcs) if min_src("core") in s]
        journey = [i for i, s in enumerate(srcs) if min_src("journey") in s]
        alpine = [i for i, s in enumerate(srcs) if "alpinejs-csp" in s]
        self.assertEqual(len(core), 1, srcs)
        self.assertEqual(len(journey), 1, srcs)
        self.assertEqual(len(alpine), 1, srcs)
        # Deferred scripts run in document order: Sortable must exist when
        # timelineSort.init() calls initSortable(), or dragging silently no-ops,
        # and the journey bundle must register timelineSort before Alpine starts.
        self.assertLess(sortable[0], alpine[0])
        self.assertLess(journey[0], alpine[0])
        tag = next(t for t in _scripts(html) if SORTABLE in t["src"])
        self.assertIn("defer", tag)
        self.assertIn("nonce", tag)
