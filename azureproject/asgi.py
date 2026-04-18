"""
ASGI config for azureproject project.

Supports both HTTP and WebSocket protocols via Django Channels.
HTTP requests are handled by the standard Django ASGI application.
WebSocket connections are routed to crush_lu consumers.
Static files are served at the ASGI level via WhiteNoise to avoid
Django's StreamingHttpResponse sync-iterator warning under ASGI.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/asgi/
"""

import logging
import os
import sys

import django

# Mirror wsgi.py: pick production.py on Azure (WEBSITE_HOSTNAME is set by
# App Service), settings.py otherwise (local dev, pytest). Must match
# wsgi.py exactly — divergence here silently loads the wrong settings
# module at runtime (e.g. SEC-04 headers missing from production traffic).
_settings_module = (
    "azureproject.production"
    if "WEBSITE_HOSTNAME" in os.environ
    else "azureproject.settings"
)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", _settings_module)
django.setup()

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402
from django.conf import settings  # noqa: E402
from django.core.asgi import get_asgi_application  # noqa: E402
from whitenoise import WhiteNoise  # noqa: E402

from crush_lu.routing import websocket_urlpatterns  # noqa: E402

logger = logging.getLogger("azureproject.asgi")

django_asgi_app = get_asgi_application()


def _null_wsgi_app(environ, start_response):
    """No-op WSGI app — static-only requests never reach this."""
    start_response("404 Not Found", [("Content-Type", "text/plain")])
    return [b""]


whitenoise_app = WhiteNoise(
    _null_wsgi_app,
    root=settings.STATIC_ROOT,
    prefix=settings.STATIC_URL,
)

# In DEBUG mode, also serve files from each app's static/ directory
# so developers don't need to run collectstatic after every change.
if settings.DEBUG:
    import importlib

    for app_label in settings.INSTALLED_APPS:
        try:
            mod = importlib.import_module(app_label)
        except ImportError:
            continue
        app_dir = os.path.dirname(mod.__file__)
        static_dir = os.path.join(app_dir, "static")
        if os.path.isdir(static_dir):
            whitenoise_app.add_files(static_dir, prefix=settings.STATIC_URL)


_STATIC_FILE_CHUNK_SIZE = 65536


class StaticFilesASGI:
    """
    ASGI middleware: intercept static file requests and serve them directly
    via WhiteNoise's StaticFile objects, bypassing both Django's ASGIHandler
    and the asgiref WsgiToAsgi bridge.

    This avoids the CurrentThreadExecutor race condition that causes
    "RuntimeError: CurrentThreadExecutor already quit or is broken"
    under concurrent static file requests.
    """

    def __init__(self, app):
        self.app = app
        self.static_prefix = settings.STATIC_URL

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path.startswith(self.static_prefix):
                static_file = (
                    whitenoise_app.find_file(path)
                    if whitenoise_app.autorefresh
                    else whitenoise_app.files.get(path)
                )
                if static_file is not None:
                    await self._serve_static(scope, static_file, send)
                    return
        await self.app(scope, receive, send)

    @staticmethod
    async def _serve_static(scope, static_file, send):
        """Serve a WhiteNoise StaticFile/Redirect directly via ASGI."""
        method = scope.get("method", "GET")

        # Convert ASGI headers to the HTTP_* environ dict WhiteNoise expects.
        request_headers = {}
        for raw_name, raw_value in scope.get("headers", []):
            name = raw_name.decode("latin-1").upper().replace("-", "_")
            request_headers[f"HTTP_{name}"] = raw_value.decode("latin-1")

        response = static_file.get_response(method, request_headers)
        file_handle = response.file
        try:
            await send(
                {
                    "type": "http.response.start",
                    "status": response.status.value,
                    "headers": [
                        (k.encode("latin-1"), v.encode("latin-1"))
                        for k, v in response.headers
                    ],
                }
            )

            if file_handle is None:
                await send(
                    {"type": "http.response.body", "body": b"", "more_body": False}
                )
            else:
                while True:
                    chunk = file_handle.read(_STATIC_FILE_CHUNK_SIZE)
                    if not chunk:
                        await send(
                            {
                                "type": "http.response.body",
                                "body": b"",
                                "more_body": False,
                            }
                        )
                        break
                    await send(
                        {
                            "type": "http.response.body",
                            "body": chunk,
                            "more_body": True,
                        }
                    )
        finally:
            if file_handle is not None:
                file_handle.close()


_http_app = StaticFilesASGI(django_asgi_app)
_websocket_app = AllowedHostsOriginValidator(
    AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
)


async def application(scope, receive, send):
    """
    Lightweight protocol router that bypasses channels' ProtocolTypeRouter
    for HTTP requests, avoiding CurrentThreadExecutor conflicts with
    channels-redis under uvicorn workers.
    """
    if scope["type"] == "http":
        try:
            await _http_app(scope, receive, send)
        except Exception:
            # Log to Python logging — OpenTelemetry forwards this to App Insights.
            # These ASGI transport errors (e.g. CurrentThreadExecutor broken) occur
            # before Django's request pipeline, so OpenTelemetry's Django
            # instrumentation never sees them without this explicit catch.
            path = scope.get("path", "unknown")
            logger.exception("ASGI transport error on %s", path)

            # Record an OpenTelemetry exception span so the error also appears
            # in the App Insights 'exceptions' table with full stack trace.
            try:
                from opentelemetry import trace

                tracer = trace.get_tracer(__name__)
                with tracer.start_as_current_span("asgi-transport-error") as span:
                    span.set_attribute("http.target", path)
                    span.record_exception(sys.exc_info()[1])
                    span.set_status(
                        trace.StatusCode.ERROR,
                        str(sys.exc_info()[1]),
                    )
            except Exception:
                pass  # Don't let telemetry failures mask the original error

            raise
    elif scope["type"] == "websocket":
        await _websocket_app(scope, receive, send)
    elif scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    else:
        raise ValueError(f"Unknown ASGI scope type: {scope['type']}")
