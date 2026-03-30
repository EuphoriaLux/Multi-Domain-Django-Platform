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

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "azureproject.settings")
django.setup()

from asgiref.wsgi import WsgiToAsgi  # noqa: E402
from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402
from django.conf import settings  # noqa: E402
from django.core.asgi import get_asgi_application  # noqa: E402
from whitenoise import WhiteNoise  # noqa: E402

from crush_lu.routing import websocket_urlpatterns  # noqa: E402

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


class StaticFilesASGI:
    """
    ASGI middleware: intercept static file requests and serve them via
    WhiteNoise (WSGI→ASGI bridge), bypassing Django's ASGIHandler.
    Eliminates the StreamingHttpResponse sync-iterator warning.
    """

    def __init__(self, app):
        self.app = app
        self.static_prefix = settings.STATIC_URL

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path.startswith(self.static_prefix) and path in whitenoise_app.files:
                wsgi_to_asgi = WsgiToAsgi(whitenoise_app)
                await wsgi_to_asgi(scope, receive, send)
                return
        await self.app(scope, receive, send)


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
        await _http_app(scope, receive, send)
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
