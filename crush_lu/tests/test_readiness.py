"""
Tests for the /healthz/ (static liveness) and /readyz/ (deep readiness)
endpoints served by HealthCheckMiddleware.

/readyz/ is the slot-swap warm-up gate (WEBSITE_SWAP_WARMUP_PING_PATH) —
it must return 200 only when DB, migrations, cache, and storage all check
out, and 503 with per-check detail otherwise.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.apps import apps as django_apps
from django.conf import settings
from django.test import Client, TestCase, override_settings

from azureproject import readiness

# The storage check must probe the *configured* backend in production, but
# tests must not depend on the dev box's Azurite/Azure state — pin the
# default backend to in-memory storage.
HERMETIC_STORAGES = {
    **settings.STORAGES,
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
}


def _failing_check():
    raise ConnectionError("probe failure")


def _write_stamp(directory, commit):
    (Path(directory) / "build_info.json").write_text(
        json.dumps({"commit": commit, "built_at": "2026-07-06T00:00:00Z"}),
        encoding="utf-8",
    )


class HealthzTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_healthz_returns_static_ok(self):
        for path in ("/healthz/", "/healthz"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, b"OK")
            self.assertEqual(response["Content-Type"], "text/plain")


@override_settings(STORAGES=HERMETIC_STORAGES)
class ReadyzTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_readyz_all_checks_pass(self):
        for path in ("/readyz/", "/readyz"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            payload = json.loads(response.content)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(
                payload["checks"],
                {
                    "database": "ok",
                    "migrations": "ok",
                    "cache": "ok",
                    "storage": "ok",
                },
            )

    def test_readyz_returns_503_when_a_check_fails(self):
        patched = tuple(
            (name, _failing_check if name == "database" else check)
            for name, check in readiness.CHECKS
        )
        with patch.object(readiness, "CHECKS", patched):
            response = self.client.get("/readyz/")
        self.assertEqual(response.status_code, 503)
        payload = json.loads(response.content)
        self.assertEqual(payload["status"], "fail")
        self.assertEqual(payload["checks"]["database"], "fail: ConnectionError")
        # The other checks still run and report individually.
        self.assertEqual(payload["checks"]["migrations"], "ok")

    def test_readyz_reports_unapplied_migrations(self):
        def unapplied():
            raise RuntimeError("3 unapplied migration(s)")

        patched = tuple(
            (name, unapplied if name == "migrations" else check)
            for name, check in readiness.CHECKS
        )
        with patch.object(readiness, "CHECKS", patched):
            response = self.client.get("/readyz/")
        self.assertEqual(response.status_code, 503)
        payload = json.loads(response.content)
        self.assertEqual(
            payload["checks"]["migrations"], "fail: 3 unapplied migration(s)"
        )

    def test_readyz_never_raises_even_if_every_check_fails(self):
        patched = tuple((name, _failing_check) for name, _ in readiness.CHECKS)
        with patch.object(readiness, "CHECKS", patched):
            response = self.client.get("/readyz/")
        self.assertEqual(response.status_code, 503)
        payload = json.loads(response.content)
        self.assertEqual(payload["status"], "fail")
        for value in payload["checks"].values():
            self.assertTrue(value.startswith("fail:"))

    def _boot_in(self, directory, commit=None):
        """Simulate a process booting from ``directory`` (with a build stamp
        for ``commit`` when given), restoring the real boot stamp afterwards."""
        self.addCleanup(setattr, readiness, "_boot_build", readiness._boot_build)
        if commit is not None:
            _write_stamp(directory, commit)
        with override_settings(BASE_DIR=Path(directory)):
            django_apps.get_app_config("azureproject").ready()

    def test_readyz_includes_build_stamp_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._boot_in(tmp, commit="abc123")
            with override_settings(BASE_DIR=Path(tmp)):
                response = self.client.get("/readyz/")
        payload = json.loads(response.content)
        self.assertEqual(payload["build"]["commit"], "abc123")

    def test_readyz_omits_build_stamp_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._boot_in(tmp)
            with override_settings(BASE_DIR=Path(tmp)):
                response = self.client.get("/readyz/")
        payload = json.loads(response.content)
        self.assertNotIn("build", payload)

    def test_readyz_reports_the_build_the_process_booted_with(self):
        # A zip deploy rewrites build_info.json under the still-running old
        # process (shared /home/site/wwwroot). Reporting the file as it is now
        # let the old process claim the new commit, so the deploy gate passed
        # and the smoke test ran against old code whose stylesheet the
        # `--clean` deploy had just deleted.
        with tempfile.TemporaryDirectory() as tmp:
            self._boot_in(tmp, commit="old-build")
            _write_stamp(tmp, "new-build")
            with override_settings(BASE_DIR=Path(tmp)):
                response = self.client.get("/readyz/")
        payload = json.loads(response.content)
        self.assertEqual(payload["build"]["commit"], "old-build")

    def test_storage_check_fails_when_container_missing(self):
        # Azure-style backend: exists() on a blob returns False for a missing
        # container too, so the check must probe the container client itself.
        azure_like = MagicMock()
        azure_like.client.exists.return_value = False
        with patch.object(readiness, "default_storage", azure_like):
            with self.assertRaisesMessage(RuntimeError, "storage container missing"):
                readiness._check_storage()

    def test_storage_check_passes_when_container_exists(self):
        azure_like = MagicMock()
        azure_like.client.exists.return_value = True
        with patch.object(readiness, "default_storage", azure_like):
            readiness._check_storage()
        azure_like.client.exists.assert_called_once_with()
        azure_like.exists.assert_not_called()
