"""Pin the middleware order that only ``azureproject.production`` produces.

The rest of the suite runs against ``azureproject.settings``, so production's
own list — base list plus five Azure-specific insertions — was never exercised
by anything. That gap let a real bug ship invisibly: the three
``redirect_www_middleware`` entries were inserted at hardcoded indexes 2, 3 and
4, which landed them just after CorsMiddleware only because CorsMiddleware
happened to sit at index 1. Adding one middleware ahead of it in settings.py
pushed all three in front of Cors instead, so RedirectWWWToRootDomainMiddleware
short-circuited with a 301 before CorsMiddleware ever ran and those responses
went out without the CORS and security headers.

The import runs in a subprocess on purpose. Importing a second settings module
in-process would configure Azure Monitor and touch logging handlers for every
test that follows.
"""

import json
import os
import subprocess
import sys
import textwrap

import pytest

_DUMP_MIDDLEWARE = textwrap.dedent("""
    import importlib, json, sys
    settings = importlib.import_module("azureproject.production")
    sys.stdout.write("@@MIDDLEWARE@@" + json.dumps(list(settings.MIDDLEWARE)))
    """)

# production.py refuses to import without these; the values are never connected
# to, they only have to parse.
_FAKE_ENV = {
    "SECRET_KEY": "production-middleware-order-test",
    "WEBSITE_HOSTNAME": "middleware-order.test",
    "POSTGRESQLCONNSTR_pythonappConnection": (
        "dbname=test host=localhost user=test password=test"
    ),
}


@pytest.fixture(scope="module")
def production_middleware():
    result = subprocess.run(
        [sys.executable, "-c", _DUMP_MIDDLEWARE],
        # Inherit the real environment: a bare dict would drop PATH and the
        # interpreter would not find its own DLLs on Windows.
        env={**os.environ, **_FAKE_ENV},
        capture_output=True,
        text=True,
        timeout=120,
    )
    if "@@MIDDLEWARE@@" not in result.stdout:
        pytest.fail(
            "could not import azureproject.production\n"
            f"exit={result.returncode}\nstdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return json.loads(result.stdout.split("@@MIDDLEWARE@@", 1)[1])


def _index(middleware, name):
    matches = [i for i, entry in enumerate(middleware) if entry.endswith(name)]
    assert len(matches) == 1, f"expected exactly one {name}, found {matches}"
    return matches[0]


def test_azure_middlewares_stay_between_cors_and_security(production_middleware):
    """Relative order only — absolute indexes are the thing that broke."""
    cors = _index(production_middleware, "CorsMiddleware")
    security = _index(production_middleware, "security.SecurityMiddleware")

    for name in (
        "AzureInternalIPMiddleware",
        "RedirectWWWToRootDomainMiddleware",
        "StagingNoIndexMiddleware",
    ):
        position = _index(production_middleware, name)
        assert cors < position < security, (
            f"{name} must sit inside CorsMiddleware's wrapping and ahead of "
            f"SecurityMiddleware, got cors={cors} {name}={position} "
            f"security={security}"
        )


def test_health_check_still_short_circuits_before_the_canary(production_middleware):
    """The canary must not fire on Azure's liveness pings."""
    health = _index(production_middleware, "HealthCheckMiddleware")
    canary = _index(production_middleware, "RuntimeLoggingCanaryMiddleware")

    assert health == 0
    assert health < canary


def test_conditional_middlewares_follow_their_anchors(production_middleware):
    force_english = _index(production_middleware, "ForceAdminToEnglishMiddleware")
    domain_routing = _index(production_middleware, "DomainURLRoutingMiddleware")
    oauth = _index(production_middleware, "OAuthCallbackProtectionMiddleware")
    auth = _index(production_middleware, "auth.middleware.AuthenticationMiddleware")

    assert force_english == domain_routing + 1
    assert oauth == auth + 1
