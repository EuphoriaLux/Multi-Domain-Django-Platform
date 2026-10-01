"""Generated Crush.lu assets are built, not committed (scripts/build_assets.py).

The CSS/JS bundles and compiled ``.mo`` files used to be committed, which made
every Wave of PRs conflict on them. These tests pin the new contract: the files
are ignored by git, the builder produces and verifies them, and CI/deploy run
the builder before anything depends on the output.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import polib
from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR)
sys.path.insert(0, str(ROOT / "scripts"))
try:
    import build_assets
finally:
    sys.path.remove(str(ROOT / "scripts"))

WORKFLOWS = ROOT / ".github" / "workflows"


def _git(*args):
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )


class GeneratedFilesAreNotTrackedTests(SimpleTestCase):
    GENERATED = [
        "crush_lu/static/crush_lu/css/tailwind.css",
        "crush_lu/static/crush_lu/css/journey.css",
        "crush_lu/static/crush_lu/css/marketing.css",
        "crush_lu/static/crush_lu/js/alpine/core.min.js",
        "crush_lu/static/crush_lu/js/alpine/core.min.js.map",
        "crush_lu/locale/de/LC_MESSAGES/django.mo",
        "crush_lu/locale/fr/LC_MESSAGES/djangojs.mo",
    ]
    STAY_COMMITTED = [
        "power_up/static/power_up/css/tailwind.css",
        "core/locale/de/LC_MESSAGES/django.mo",
        "crush_lu/locale/de/LC_MESSAGES/django.po",
    ]

    def setUp(self):
        if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
            self.skipTest("not a git checkout")

    def test_generated_files_are_ignored(self):
        for path in self.GENERATED:
            self.assertEqual(_git("check-ignore", "-q", path).returncode, 0, path)

    def test_generated_files_are_not_tracked(self):
        tracked = set(_git("ls-files").stdout.splitlines())
        for path in self.GENERATED:
            self.assertNotIn(path, tracked, path)

    def test_other_platforms_and_po_files_stay_committed(self):
        tracked = set(_git("ls-files").stdout.splitlines())
        for path in self.STAY_COMMITTED:
            self.assertIn(path, tracked, path)


class BuilderVerificationTests(SimpleTestCase):
    def test_the_built_repository_passes_every_check(self):
        # conftest already refuses to start without these; assert it directly.
        self.assertEqual(build_assets.verify_translations(), [])
        self.assertEqual(build_assets.verify_files(build_assets.CSS_FILES, "CSS"), [])
        self.assertEqual(build_assets.verify_files(build_assets.JS_FILES, "JS"), [])

    def test_bundle_lists_cover_every_alpine_entry_point(self):
        import json

        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        build_js = package["scripts"]["build:js"]
        for bundle in build_assets.ALPINE_BUNDLES:
            self.assertIn(f"alpine/{bundle}.js", build_js)

    def test_missing_or_empty_files_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.css"
            empty.write_text("")
            problems = build_assets.verify_files(
                [empty, Path(tmp) / "absent.css"], "CSS bundle"
            )
        self.assertEqual(len(problems), 2)


class TranslationBuildTests(SimpleTestCase):
    """Run the builder against a throwaway locale tree."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        messages = self.tmp / "fr" / "LC_MESSAGES"
        messages.mkdir(parents=True)
        po = polib.POFile()
        po.metadata = {"Content-Type": "text/plain; charset=UTF-8"}
        po.append(polib.POEntry(msgid="Approve", msgstr="Approuver"))
        po.append(polib.POEntry(msgid="Fuzzy one", msgstr="Flou", flags=["fuzzy"]))
        po.append(
            polib.POEntry(
                msgid="%(n)s vote",
                msgid_plural="%(n)s votes",
                msgstr_plural={0: "%(n)s vote", 1: "%(n)s votes"},
            )
        )
        self.po_path = messages / "django.po"
        po.save(str(self.po_path))
        self.original_locale = build_assets.LOCALE
        self.original_languages = build_assets.LANGUAGES
        self.original_floor = build_assets.MIN_DJANGO_ENTRIES
        build_assets.LOCALE = self.tmp
        build_assets.LANGUAGES = ("fr",)
        build_assets.MIN_DJANGO_ENTRIES = {}  # a tiny catalogue is fine here
        self.addCleanup(
            setattr, build_assets, "MIN_DJANGO_ENTRIES", self.original_floor
        )
        self.addCleanup(setattr, build_assets, "LOCALE", self.original_locale)
        self.addCleanup(setattr, build_assets, "LANGUAGES", self.original_languages)

    def test_build_compiles_translated_entries_only(self):
        build_assets.build_translations()
        compiled = {
            e.msgid: e for e in polib.mofile(str(self.po_path.with_suffix(".mo")))
        }
        self.assertEqual(compiled["Approve"].msgstr, "Approuver")
        self.assertNotIn("Fuzzy one", compiled)  # fuzzy entries are skipped
        self.assertNotIn("Fuzzy one", {e.msgid for e in compiled.values()})
        self.assertEqual(build_assets.verify_translations(), [])

    def test_an_emptied_catalogue_fails_the_entry_floor(self):
        build_assets.MIN_DJANGO_ENTRIES = {"fr": 1000}
        build_assets.build_translations()
        problems = build_assets.verify_translations()
        self.assertEqual(len(problems), 1)
        self.assertIn("only", problems[0])

    def test_a_missing_mo_is_reported(self):
        problems = build_assets.verify_translations()
        self.assertEqual(len(problems), 1)
        self.assertIn("missing", problems[0])

    def test_a_stale_mo_is_reported(self):
        build_assets.build_translations()
        po = polib.pofile(str(self.po_path))
        po.append(polib.POEntry(msgid="Added later", msgstr="Ajouté plus tard"))
        po.save(str(self.po_path))
        problems = build_assets.verify_translations()
        self.assertEqual(len(problems), 1)
        self.assertIn("does not match", problems[0])

    def test_a_malformed_mo_is_reported(self):
        self.po_path.with_suffix(".mo").write_bytes(b"not a catalogue")
        problems = build_assets.verify_translations()
        self.assertEqual(len(problems), 1)
        self.assertIn("unreadable", problems[0])


class WorkflowsBuildTheAssetsTests(SimpleTestCase):
    def _read(self, name):
        return (WORKFLOWS / name).read_text(encoding="utf-8")

    def test_ci_builds_before_tests_and_axe(self):
        ci = self._read("test-and-validate.yml")
        # Tests, axe, and the JS lint job (esbuild only, no translations).
        self.assertGreaterEqual(ci.count("python scripts/build_assets.py"), 2)
        tests = ci.index("Run tests (parallel")
        self.assertLess(ci.index("python scripts/build_assets.py"), tests)

    def test_deploy_builds_ships_and_verifies_the_mo_files(self):
        deploy = self._read("deploy-azure-app-service-optimized.yml")
        self.assertIn("crush_lu/locale/*/LC_MESSAGES/*.mo", deploy)
        self.assertGreaterEqual(deploy.count("scripts/build_assets.py --check"), 2)
        # The restore-and-verify gate must run before the package is built and
        # therefore before anything reaches the staging slot.
        verify = deploy.index("Verify generated assets and compiled translations")
        self.assertLess(verify, deploy.index("Build deployment package locally"))
        self.assertLess(verify, deploy.index("Deploy to Staging Slot"))

    def test_smoke_test_covers_de_fr_and_the_stylesheet(self):
        smoke = (ROOT / "scripts" / "smoke.py").read_text(encoding="utf-8")
        self.assertIn("check_generated_assets", smoke)
        self.assertIn('"de", "fr"', smoke)
        self.assertIn("tailwind", smoke)
