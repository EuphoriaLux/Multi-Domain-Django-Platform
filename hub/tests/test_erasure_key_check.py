"""ERASURE_DIGEST_KEY must be visible when missing on the production slot."""

import os
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from azureproject import checks

PRODUCTION = {"WEBSITE_HOSTNAME": "crush.lu", "WEBSITE_SLOT_NAME": "Production"}


def _env(**env):
    return patch.dict(os.environ, env, clear=False)


class ErasureKeyCheckTests(SimpleTestCase):
    @override_settings(ERASURE_DIGEST_KEY="")
    def test_missing_key_on_production_warns(self):
        with _env(**PRODUCTION):
            messages = checks.check_erasure_digest_key(None)

        self.assertEqual([m.id for m in messages], ["azureproject.W001"])
        self.assertIn("ERASURE_DIGEST_KEY", messages[0].msg)

    @override_settings(ERASURE_DIGEST_KEY="a-dedicated-key")
    def test_configured_key_is_fine(self):
        with _env(**PRODUCTION):
            self.assertEqual(checks.check_erasure_digest_key(None), [])
            self.assertIsNone(checks.erasure_key_problem())

    @override_settings(ERASURE_DIGEST_KEY="")
    def test_staging_slot_never_warns(self):
        # The setting is slot-sticky on production only: staging has no key and
        # a warning there would be noise on every deploy.
        with _env(WEBSITE_HOSTNAME="crush-staging.azurewebsites.net",
                  WEBSITE_SLOT_NAME="staging"):
            self.assertEqual(checks.check_erasure_digest_key(None), [])

    @override_settings(ERASURE_DIGEST_KEY="")
    def test_local_and_ci_never_warn(self):
        env = {k: v for k, v in os.environ.items() if k != "WEBSITE_HOSTNAME"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(checks.check_erasure_digest_key(None), [])

    @override_settings(ERASURE_DIGEST_KEY="")
    def test_missing_key_on_production_logs_an_error_at_boot(self):
        from django.apps import apps

        with _env(**PRODUCTION), self.assertLogs("azureproject", "ERROR") as logs:
            apps.get_app_config("azureproject").ready()

        self.assertTrue(any("ERASURE_DIGEST_KEY" in line for line in logs.output))

    def test_check_is_registered_for_deploy_checks(self):
        from django.core.checks.registry import registry

        self.assertIn(checks.check_erasure_digest_key, registry.deployment_checks)
