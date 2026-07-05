import asyncio
from http import HTTPStatus
from io import BytesIO
from types import SimpleNamespace

from azureproject.asgi import StaticFilesASGI


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
