"""Azurite (local blob emulator) configuration that the test harness never runs.

``settings.IS_TESTING`` forces ``AZURITE_MODE`` off in-process, so the Azurite
branch of ``azureproject/settings.py`` is exercised here in a subprocess (same
pattern as test_production_middleware_order.py). That branch once indexed an
undefined ``SECURE_CSP`` and raised NameError on every import, which broke
runserver and every manage.py command for anyone with ``USE_AZURITE=true``.
"""

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings

REPO_ROOT = Path(__file__).resolve().parents[2]

_DUMP_AZURITE = textwrap.dedent("""
    import json, sys
    import azureproject.settings as s
    sys.stdout.write("@@AZURITE@@" + json.dumps([
        s.MEDIA_URL,
        s.SECURE_CSP_REPORT_ONLY["img-src"],
        s.SECURE_CSP_REPORT_ONLY["media-src"],
    ]))
    """)


def _azurite_settings(**overrides):
    env = {
        key: value
        for key, value in os.environ.items()
        if key != "AZURITE_BLOB_HOST" and not key.startswith("POSTGRESQLCONNSTR_")
    }
    env.update({"USE_AZURITE": "true", "SECRET_KEY": "azurite-settings-test"})
    env.update(overrides)
    result = subprocess.run(
        [sys.executable, "-c", _DUMP_AZURITE],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-6000:]
    assert "@@AZURITE@@" in result.stdout, result.stderr[-6000:]
    return json.loads(result.stdout.split("@@AZURITE@@", 1)[1])


def test_custom_azurite_host_reaches_media_url_and_csp():
    media_url, img_src, media_src = _azurite_settings(AZURITE_BLOB_HOST="azurite:10000")

    assert media_url.startswith("http://azurite:10000/")
    assert img_src.count("http://azurite:10000") == 1
    assert media_src.count("http://azurite:10000") == 1


def test_default_azurite_host_is_not_duplicated_in_csp():
    media_url, img_src, media_src = _azurite_settings()

    assert media_url.startswith("http://127.0.0.1:10000/")
    assert img_src.count("http://127.0.0.1:10000") == 1
    assert media_src.count("http://127.0.0.1:10000") == 1


def test_test_harness_never_runs_azurite():
    assert settings.AZURITE_MODE is False


def _load_setup_azurite():
    """Load scripts/setup_azurite.py fresh, as setup_local_dev imports it."""
    spec = importlib.util.spec_from_file_location(
        "setup_azurite_under_test", REPO_ROOT / "scripts" / "setup_azurite.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("blob_host", "expected_endpoint"),
    [
        ("azurite:10000", "BlobEndpoint=http://azurite:10000/devstoreaccount1;"),
        (None, "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"),
    ],
)
def test_container_setup_uses_configured_azurite_host(
    monkeypatch, blob_host, expected_endpoint
):
    """Containers must be created on the emulator the app's storage talks to."""
    if blob_host is None:
        monkeypatch.delenv("AZURITE_BLOB_HOST", raising=False)
    else:
        monkeypatch.setenv("AZURITE_BLOB_HOST", blob_host)
    module = _load_setup_azurite()

    with patch.object(
        module.BlobServiceClient, "from_connection_string"
    ) as from_connection_string:
        module.get_blob_service_client()

    connection_string = from_connection_string.call_args.args[0]
    assert expected_endpoint in connection_string
