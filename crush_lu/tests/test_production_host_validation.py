"""Exercise the production middleware and real OTel wrapper in isolation.

Production settings patch Django's host validator and configure telemetry.
Keep those effects in a subprocess, never in pytest's shared Django process.
Only external readiness/Sites dependencies and the terminal view are stubbed.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

_PROBE = textwrap.dedent("""
    import asyncio
    from http import HTTPStatus
    import json
    from types import SimpleNamespace
    from unittest.mock import patch

    import django
    django.setup()

    from django.conf import settings
    from django.core.exceptions import DisallowedHost
    from django.http import JsonResponse
    from django.test import Client, RequestFactory, override_settings
    from opentelemetry.instrumentation.django import DjangoInstrumentor

    from azureproject.domains import DOMAINS

    # The real instrumentation prepends itself to production's actual list.
    # No exporter is configured, so telemetry cannot leave the test process.
    DjangoInstrumentor().instrument()
    client = Client()
    client.handler._get_response = lambda request: JsonResponse({
        "host": request.get_host(), "urlconf": request.urlconf,
    })

    results = {"allowed_hosts": settings.ALLOWED_HOSTS, "responses": {}}
    def request(name, host, path="/en/", forwarded=None, secure=True):
        headers = {"HTTP_HOST": host}
        if forwarded is not None:
            headers["HTTP_X_FORWARDED_HOST"] = forwarded
        response = client.get(path, secure=secure, **headers)
        results["responses"][name] = {
            "status": response.status_code,
            "location": response.get("Location"),
            "robots": response.get("X-Robots-Tag"),
        }
        if response.get("Content-Type", "").startswith("application/json"):
            results["responses"][name]["payload"] = response.json()

    with override_settings(
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    ), patch(
        "azureproject.middleware.SafeCurrentSiteMiddleware._get_site",
        return_value=SimpleNamespace(id=1, domain="crush.lu", name="Crush.lu"),
    ), patch(
        "azureproject.middleware.RuntimeLoggingCanaryMiddleware._emit",
    ), patch(
        "azureproject.readiness.run_readiness_checks", return_value=(True, {"test": "ok"}),
    ) as readiness, patch(
        "azureproject.readiness.build_info", return_value={},
    ), patch(
        "django.db.backends.base.base.BaseDatabaseWrapper.connect",
        side_effect=AssertionError("host validation must not connect to a database"),
    ):
        for host in ("test.attacker.invalid", "test-attacker.invalid", "attacker.invalid",
                     "other-tenant.azurewebsites.net", "169.254.attacker.invalid",
                     "169.254.999.2", "crush.lu,attacker.invalid", "crush.lu@attacker.invalid"):
            request("host:" + host, host)
            request("forwarded:" + host, "crush.lu", forwarded=host)
            request("redirect:" + host, "www.crush.lu", forwarded=host)
            request("hidden:" + host, host, forwarded="crush.lu")
            request("probe:" + host, host, path="/healthz/")
        request("empty-forwarded", "crush.lu", forwarded="")
        request("unknown-readiness", "test.attacker.invalid", path="/readyz/")
        results["readiness_before_valid_probes"] = readiness.call_count

        for host in DOMAINS:
            request("configured:" + host, host)
        for host in ("test.crush.lu", "test-portal.powerup.lu", "extra-staging.example.com"):
            request("staging:" + host, host)
        request("azure", "a2-app.azurewebsites.net")
        request("extra-azure", "a2-app-staging.azurewebsites.net")
        request("www", "www.crush.lu", path="/en/?source=host-test")
        request("forwarded-staging", "a2-app.azurewebsites.net", forwarded="test.crush.lu")
        request("forwarded-portal", "a2-app.azurewebsites.net", forwarded="test-portal.powerup.lu")
        request("forwarded-www", "a2-app.azurewebsites.net", forwarded="www.crush.lu")
        request("case-and-port", "CRUSH.LU.:443")
        request("http", "crush.lu", secure=False)

        for host in ("169.254.1.2:8000", "localhost", "127.0.0.1", "a2-app.azurewebsites.net"):
            for path in ("/healthz", "/healthz/", "/readyz", "/readyz/"):
                request("valid-probe:" + host + path, host, path=path, secure=False)
        request("internal-page", "169.254.1.2:8000")
        request("internal-forwarded-page", "crush.lu", forwarded="169.254.1.2:8000")
        request("internal-transport-page", "169.254.1.2:8000", forwarded="crush.lu")
        request("internal-transport-www", "169.254.1.2:8000", forwarded="www.crush.lu")
        request("internal-forwarded-probe", "169.254.1.2:8000", path="/healthz/", forwarded="crush.lu")
        request("internal-bad-forwarded-probe", "169.254.1.2:8000", path="/healthz/", forwarded="test.attacker.invalid")

    results["middleware"] = list(settings.MIDDLEWARE)
    # Directly reproduce the original allowlist bypass, independently of routing.
    with override_settings(ALLOWED_HOSTS=["crush.lu"]):
        results["get_host"] = {}
        for host in ("test.attacker.invalid", "test-attacker.invalid", "169.254.attacker.invalid", "crush.lu"):
            try:
                RequestFactory().get("/", HTTP_HOST=host).get_host()
                results["get_host"][host] = "accepted"
            except DisallowedHost:
                results["get_host"][host] = "rejected"

    # Production serves static assets directly through ASGI, outside Django.
    from azureproject.asgi import StaticFilesASGI, whitenoise_app
    async def unused_app(scope, receive, send):
        raise AssertionError("the static fixture should never fall through")
    async def static_request(host, forwarded=None):
        headers = [(b"host", host.encode())]
        if forwarded is not None:
            headers.append((b"x-forwarded-host", forwarded.encode()))
        scope = {
            "type": "http", "method": "GET", "path": "/static/host-test.css",
            "query_string": b"", "headers": headers, "scheme": "https",
            "server": ("a2-app.azurewebsites.net", 443),
        }
        messages = []
        async def send(message):
            messages.append(message)
        await StaticFilesASGI(unused_app)(scope, None, send)
        return messages[0]["status"]
    static_file = SimpleNamespace(get_response=lambda *args: SimpleNamespace(
        status=HTTPStatus.OK, headers=[("Content-Type", "text/css")], file=None,
    ))
    with patch.object(whitenoise_app, "autorefresh", False), patch.object(
        whitenoise_app, "files", {"/static/host-test.css": static_file},
    ):
        results["static"] = {
            "configured": asyncio.run(static_request("crush.lu")),
            "unknown": asyncio.run(static_request("test.attacker.invalid")),
            "unknown-forwarded": asyncio.run(static_request("crush.lu", "test-attacker.invalid")),
            "hidden-host": asyncio.run(static_request("attacker.invalid", "crush.lu")),
            "internal": asyncio.run(static_request("169.254.1.2")),
            "internal-transport": asyncio.run(static_request("169.254.1.2", "crush.lu")),
            "redirect-only": asyncio.run(static_request("moonlightdating.lu")),
        }

    # Run the actual ASGI protocol router with real OriginValidator, session/auth
    # middleware and URLRouter; replace only the final consumer with a sentinel.
    import azureproject.asgi as asgi
    from channels.auth import AuthMiddlewareStack, get_user
    from channels.routing import URLRouter
    from channels.security.websocket import AllowedHostsOriginValidator
    from django.urls import re_path

    reached = []
    async def consumer(scope, receive, send):
        reached.append({
            "anonymous": scope["user"].is_anonymous,
            "session": "session" in scope,
        })
        await send({"type": "websocket.accept"})

    socket_app = AllowedHostsOriginValidator(AuthMiddlewareStack(URLRouter([
        re_path(r"^.*$", consumer),
    ])))
    async def websocket_request(host, forwarded=None, origin="https://crush.lu",
                                path="/ws/host-test/", extra_headers=()):
        headers = [(b"host", host.encode())]
        if origin is not None:
            headers.append((b"origin", origin.encode()))
        if forwarded is not None:
            headers.append((b"x-forwarded-host", forwarded.encode()))
        headers.extend(extra_headers)
        scope = {
            "type": "websocket", "path": path, "query_string": b"",
            "headers": headers, "scheme": "wss",
            "server": ("a2-app.azurewebsites.net", 443),
        }
        messages = []
        events = iter([{"type": "websocket.connect"},
                       {"type": "websocket.disconnect", "code": 1000}])
        async def receive():
            return next(events)
        async def send(message):
            messages.append(message)
        before = len(reached)
        auth_before = auth.call_count
        await asgi.application(scope, receive, send)
        assert scope["type"] == "websocket" and scope["scheme"] == "wss"
        assert "method" not in scope  # the handshake view must not mutate ASGI scope
        return {"messages": messages, "reached": reached[before:],
                "auth_calls": auth.call_count - auth_before}

    with override_settings(CHANNEL_LAYERS={
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
    }), patch.object(asgi, "_websocket_app", socket_app), patch(
        "channels.auth.get_user", wraps=get_user,
    ) as auth, patch(
        "django.db.backends.base.base.BaseDatabaseWrapper.connect",
        side_effect=AssertionError("anonymous handshake must not connect to database"),
    ):
        results["websocket"] = {}
        for host in ("attacker.invalid", "test.attacker.invalid", "test-attacker.invalid",
                     "169.254.attacker.invalid", "other-tenant.azurewebsites.net"):
            results["websocket"]["host:" + host] = asyncio.run(websocket_request(host))
            results["websocket"]["hidden:" + host] = asyncio.run(websocket_request(host, "crush.lu"))
            results["websocket"]["forwarded:" + host] = asyncio.run(websocket_request("crush.lu", host))
        for host in ("crush.lu", "test.crush.lu", "a2-app.azurewebsites.net", "CRUSH.LU.:443"):
            results["websocket"]["valid:" + host] = asyncio.run(websocket_request(host))
        results["websocket"]["valid:internal-transport"] = asyncio.run(websocket_request("169.254.1.2", "crush.lu"))
        results["websocket"]["valid:forwarded-staging"] = asyncio.run(websocket_request("a2-app.azurewebsites.net", "test.crush.lu"))
        results["websocket"]["empty-forwarded"] = asyncio.run(websocket_request("crush.lu", ""))
        results["websocket"]["duplicate-host"] = asyncio.run(websocket_request("crush.lu", extra_headers=[(b"host", b"attacker.invalid")]))
        results["websocket"]["duplicate-forwarded"] = asyncio.run(websocket_request("crush.lu", "crush.lu", extra_headers=[(b"x-forwarded-host", b"attacker.invalid")]))
        results["websocket"]["internal"] = asyncio.run(websocket_request("169.254.1.2"))
        results["websocket"]["internal-forwarded"] = asyncio.run(websocket_request("crush.lu", "169.254.1.2"))
        for path in ("/healthz", "/healthz/", "/readyz", "/readyz/"):
            results["websocket"]["internal-probe:" + path] = asyncio.run(websocket_request("169.254.1.2", path=path))
        results["websocket"]["bad-origin"] = asyncio.run(websocket_request("crush.lu", origin="https://attacker.invalid"))
        results["websocket"]["missing-origin"] = asyncio.run(websocket_request("crush.lu", origin=None))
    print("@@HOST_RESULTS@@" + json.dumps(results))
""")


@pytest.fixture(scope="module")
def production_hosts():
    # Clear inherited connection strings; never initialize a real service here.
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("POSTGRESQLCONNSTR_")
    }
    env.update(
        {
            "DJANGO_SETTINGS_MODULE": "azureproject.production",
            "SECRET_KEY": "isolated-production-host-test",
            "WEBSITE_HOSTNAME": "a2-app.azurewebsites.net",
            "CUSTOM_DOMAINS": "crush.lu,extra-staging.example.com",
            "ALLOWED_HOSTS_ENV": "a2-app-staging.azurewebsites.net",
            "POSTGRESQLCONNSTR_pythonappConnection": "dbname=test host=localhost user=test password=test",
            "APPLICATIONINSIGHTS_CONNECTION_STRING": "",
            "REDIS_URL": "",
            "AZURE_REDIS_CONNECTIONSTRING": "",
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr[-6000:]
    assert "@@HOST_RESULTS@@" in result.stdout, result.stderr
    return json.loads(result.stdout.split("@@HOST_RESULTS@@", 1)[1])


def test_unknown_host_headers_cannot_reach_routes_redirects_or_probes(production_hosts):
    rejected_prefixes = ("host:", "forwarded:", "redirect:", "hidden:", "probe:")
    for name, response in production_hosts["responses"].items():
        if name.startswith(rejected_prefixes) or name in {
            "empty-forwarded",
            "unknown-readiness",
            "internal-page",
            "internal-forwarded-page",
            "internal-bad-forwarded-probe",
        }:
            assert response["status"] == 400, (name, response)
            assert response["location"] is None, (name, response)
    assert production_hosts["readiness_before_valid_probes"] == 0


def test_configured_sites_and_staging_hosts_keep_their_routes(production_hosts):
    responses = production_hosts["responses"]
    for name, response in responses.items():
        if name.startswith(("configured:", "staging:", "valid-probe:")):
            assert response["status"] == 200, (name, response)
    assert (
        responses["staging:test.crush.lu"]["payload"]["urlconf"]
        == "azureproject.urls_crush"
    )
    assert (
        responses["staging:test-portal.powerup.lu"]["payload"]["urlconf"]
        == "azureproject.urls_portal"
    )
    for name in ("azure", "extra-azure", "www", "http", "forwarded-www"):
        assert responses[name]["status"] == 301, (name, responses[name])
    assert responses["www"]["location"] == "https://crush.lu/en/?source=host-test"
    assert responses["forwarded-www"]["location"] == "https://crush.lu/en/"
    assert responses["case-and-port"]["status"] == 200
    assert responses["case-and-port"]["payload"]["urlconf"] == "azureproject.urls_crush"
    assert responses["internal-forwarded-probe"]["status"] == 200
    assert responses["internal-transport-page"]["status"] == 200
    assert responses["internal-transport-page"]["payload"] == {
        "host": "crush.lu",
        "urlconf": "azureproject.urls_crush",
    }
    assert responses["internal-transport-www"]["status"] == 301
    assert responses["internal-transport-www"]["location"] == "https://crush.lu/en/"


def test_forwarded_host_drives_redirects_routing_and_staging_headers(production_hosts):
    for name, urlconf in (
        ("forwarded-staging", "azureproject.urls_crush"),
        ("forwarded-portal", "azureproject.urls_portal"),
    ):
        response = production_hosts["responses"][name]
        assert response["status"] == 200, response
        assert response["payload"]["urlconf"] == urlconf
        assert response["robots"] == "noindex, nofollow"


def test_host_validation_runs_with_production_middleware_and_real_otel(
    production_hosts,
):
    middleware = production_hosts["middleware"]
    assert (
        "opentelemetry.instrumentation.django.middleware.otel_middleware._DjangoMiddleware"
        in middleware
    )
    assert middleware.index(
        "azureproject.middleware.HealthCheckMiddleware"
    ) < middleware.index(
        "azureproject.redirect_www_middleware.RedirectWWWToRootDomainMiddleware"
    )
    assert ".azurewebsites.net" not in production_hosts["allowed_hosts"]
    assert production_hosts["get_host"] == {
        "test.attacker.invalid": "rejected",
        "test-attacker.invalid": "rejected",
        "169.254.attacker.invalid": "rejected",
        "crush.lu": "accepted",
    }


def test_asgi_static_shortcut_enforces_the_same_host_boundary(production_hosts):
    assert production_hosts["static"] == {
        "configured": 200,
        "unknown": 400,
        "unknown-forwarded": 400,
        "hidden-host": 400,
        "internal": 400,
        "internal-transport": 200,
        # A redirect-only host is in ALLOWED_HOSTS so it clears the boundary,
        # but this shortcut runs ahead of RedirectWWWToRootDomainMiddleware —
        # without its own check it would serve the asset with a 200 and the
        # parked domain would never redirect on /static/ paths.
        "redirect-only": 301,
    }


def test_websocket_host_boundary_rejects_before_auth_and_consumer(production_hosts):
    for name, result in production_hosts["websocket"].items():
        if name.startswith("valid:") or name in {"bad-origin", "missing-origin"}:
            continue
        assert result == {
            "messages": [{"type": "websocket.close", "code": 1008}],
            "reached": [],
            "auth_calls": 0,
        }, (name, result)


def test_valid_websocket_handshakes_keep_origin_and_auth_validation(production_hosts):
    for name, result in production_hosts["websocket"].items():
        if name.startswith("valid:"):
            assert result == {
                "messages": [{"type": "websocket.accept"}],
                "reached": [{"anonymous": True, "session": True}],
                "auth_calls": 1,
            }, (name, result)
        elif name in {"bad-origin", "missing-origin"}:
            assert result["messages"][0]["type"] == "websocket.close"
            assert result["reached"] == []
            assert result["auth_calls"] == 0
