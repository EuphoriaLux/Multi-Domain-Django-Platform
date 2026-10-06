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
import shutil
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
    import dotenv
    # The parent pytest process already loaded .env into the inherited
    # environment. Re-loading it here would restore the keys that
    # _azurite_settings() strips, e.g. a developer's own AZURITE_BLOB_HOST.
    dotenv.load_dotenv = lambda *args, **kwargs: False
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


def _load_setup_azurite(root):
    """Load a copy of scripts/setup_azurite.py placed under ``root``.

    The script reads ``<repo>/.env``, so the copy reads ``root/.env`` instead of
    the developer's own .env, which may set AZURITE_BLOB_HOST.
    """
    script = root / "scripts" / "setup_azurite.py"
    script.parent.mkdir()
    shutil.copy(REPO_ROOT / "scripts" / "setup_azurite.py", script)
    spec = importlib.util.spec_from_file_location("setup_azurite_under_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("env_host", "dotenv_host", "expected_host"),
    [
        ("azurite:10000", None, "azurite:10000"),
        (None, None, "127.0.0.1:10000"),
        # A standalone `python scripts/setup_azurite.py` must read .env too.
        (None, "dotenv-azurite:10000", "dotenv-azurite:10000"),
        # As in settings.py, a real environment variable beats .env.
        ("azurite:10000", "dotenv-azurite:10000", "azurite:10000"),
    ],
)
def test_container_setup_uses_configured_azurite_host(
    tmp_path, env_host, dotenv_host, expected_host
):
    """Containers must be created on the emulator the app's storage talks to."""
    if dotenv_host is not None:
        (tmp_path / ".env").write_text(f"AZURITE_BLOB_HOST={dotenv_host}\n")
    with patch.dict(os.environ):  # load_dotenv writes into os.environ
        os.environ.pop("AZURITE_BLOB_HOST", None)
        if env_host is not None:
            os.environ["AZURITE_BLOB_HOST"] = env_host
        module = _load_setup_azurite(tmp_path)

    with patch.object(
        module.BlobServiceClient, "from_connection_string"
    ) as from_connection_string:
        module.get_blob_service_client()

    connection_string = from_connection_string.call_args.args[0]
    assert f"BlobEndpoint=http://{expected_host}/devstoreaccount1;" in connection_string
