import asyncio
import os
from http import HTTPStatus
from io import BytesIO
from types import SimpleNamespace
from wsgiref.headers import Headers

from django.conf import settings

from azureproject.asgi import StaticFilesASGI, whitenoise_app


class _CompressedMissingStaticFile:
    def __init__(self):
        self.calls = []

    def get_response(self, method, request_headers):
        self.calls.append(dict(request_headers))
        if request_headers.get("HTTP_ACCEPT_ENCODING"):
            raise FileNotFoundError(2, "No such file", "/tmp/app.js.gz")
        return SimpleNamespace(
            status=HTTPStatus.OK,
            headers=[("Content-Type", "application/javascript")],
            file=BytesIO(b"console.log('ok');"),
        )


def test_static_asgi_retries_uncompressed_when_precompressed_file_missing():
    static_file = _CompressedMissingStaticFile()
    sent = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "GET",
        "headers": [(b"accept-encoding", b"gzip, br")],
    }

    asyncio.run(StaticFilesASGI._serve_static(scope, static_file, send))

    assert static_file.calls[0]["HTTP_ACCEPT_ENCODING"] == "gzip, br"
    assert "HTTP_ACCEPT_ENCODING" not in static_file.calls[1]
    assert sent[0]["status"] == 200
    assert b"".join(message.get("body", b"") for message in sent[1:]) == b"console.log('ok');"


def _cache_control(url):
    """Return the Cache-Control WhiteNoise would set for ``url``.

    ``path`` is derived from the real STATIC_ROOT rather than fabricated. The
    regex form of ``immutable_file_test`` only inspects ``url`` today, but a
    callable form -- or a future WhiteNoise -- may consult ``path`` too, and a
    bogus value would silently mask that.
    """
    headers = Headers([])
    path = os.path.join(settings.STATIC_ROOT, url[len(settings.STATIC_URL):])
    whitenoise_app.add_cache_headers(headers, path, url)
    return headers["Cache-Control"]


def test_hashed_static_files_are_served_immutable():
    """
    Regression guard for #227, which replaced WhiteNoiseMiddleware with the base
    WhiteNoise class. The base class stubs immutable_file_test out to always-False,
    which silently downgraded every ManifestStaticFilesStorage-hashed asset from
    immutable to max-age=60 -- so browsers revalidated all of them every minute.
    """
    cache_control = _cache_control("/static/crush_lu/css/tailwind.360bb230e31e.css")

    assert "immutable" in cache_control
    assert "max-age=60" not in cache_control


def test_unhashed_static_files_are_not_served_immutable():
    """
    The workbox bundles are loaded by importScripts under their plain names, so
    they carry no content hash and must stay revalidating -- caching them forever
    would pin clients to a stale service-worker runtime.
    """
    cache_control = _cache_control("/static/crush_lu/workbox/workbox-sw.js")

    assert "immutable" not in cache_control
