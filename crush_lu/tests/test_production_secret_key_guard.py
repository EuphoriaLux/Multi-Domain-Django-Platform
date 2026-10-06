"""Production must refuse to boot without a real SECRET_KEY.

settings.py substitutes the committed key ``test-secret-key-for-pytest`` when
it decides it runs under pytest. That guess once also matched any process with
the substring "pytest" in an argument, or with pytest merely imported (pytest
ships in requirements.txt), and production.py inherited the public key without
re-checking. Each case imports ``azureproject.production`` in a subprocess with
an empty SECRET_KEY and must fail loudly.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_PRODUCTION_ENV = {
    "SECRET_KEY": "",
    "DJANGO_DEBUG": "",
    "WEBSITE_HOSTNAME": "secret-key-guard.test",
    "POSTGRESQLCONNSTR_pythonappConnection": (
        "dbname=test host=localhost user=test password=test"
    ),
    "APPLICATIONINSIGHTS_CONNECTION_STRING": "",
    "REDIS_URL": "",
    "AZURE_REDIS_CONNECTIONSTRING": "",
}


@pytest.mark.parametrize(
    ("argv", "drop_website_hostname"),
    [
        # A "pytest" substring in any argument used to flip IS_TESTING.
        (
            ["-c", "import azureproject.production", "--log-dir=/var/log/pytest-runs"],
            False,
        ),
        # pytest is installed in production; importing it is not a test run.
        (["-c", "import pytest, azureproject.production"], False),
        # settings.py does fall back here; production.py itself must refuse.
        (["-c", "import pytest, azureproject.production"], True),
    ],
    ids=["pytest-in-argv", "pytest-imported", "pytest-imported-off-azure"],
)
def test_production_refuses_placeholder_secret_key(argv, drop_website_hostname):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("POSTGRESQLCONNSTR_")
    }
    env.update(_PRODUCTION_ENV)
    if drop_website_hostname:
        env.pop("WEBSITE_HOSTNAME")

    result = subprocess.run(
        [sys.executable, *argv],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode != 0, result.stdout[-2000:]
    assert "ImproperlyConfigured" in result.stderr, result.stderr[-6000:]
    assert "SECRET_KEY environment variable must be set" in result.stderr
