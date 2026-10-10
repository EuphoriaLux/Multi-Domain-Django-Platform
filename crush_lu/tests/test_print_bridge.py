"""scripts/print_bridge.py: check-in ticket printing from a PC.

On Android the RawBT app listens on ws://127.0.0.1:40213 and the check-in page
(triggerRawBtPrint in coach.js) streams the ESC/POS bytes to it. A Windows PC
has no RawBT, so the bridge takes its place on the same port. These tests talk
real RFC 6455 to it over loopback, writing to a file sink instead of a spooler.
"""

import base64
import hashlib
import os
import socket
import struct
import sys
import tempfile
import threading
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from azureproject.domains import DOMAINS

ROOT = Path(settings.BASE_DIR)
sys.path.insert(0, str(ROOT / "scripts"))
try:
    import print_bridge
finally:
    sys.path.remove(str(ROOT / "scripts"))

CRUSH_ORIGIN = "https://crush.lu"
# Bytes a 70 KB ticket can contain, past the 16-bit frame-length boundary.
TICKET = bytes(range(256)) * 280


class OriginPolicyTests(SimpleTestCase):
    def test_platform_hosts_over_https_may_print(self):
        for origin in (
            "https://crush.lu",
            "https://www.crush.lu",
            "https://test.crush.lu",
            "https://power-up.lu",
            "https://crush.lu:443",
        ):
            with self.subTest(origin=origin):
                self.assertTrue(print_bridge.origin_allowed(origin))

    def test_local_dev_origins_may_print(self):
        for origin in (
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "http://crush.localhost:8000",
        ):
            with self.subTest(origin=origin):
                self.assertTrue(print_bridge.origin_allowed(origin))

    def test_other_sites_may_not_print(self):
        for origin in (
            None,
            "",
            "null",
            "http://crush.lu",
            "https://crush.lu.evil.example",
            "https://evilcrush.lu",
            "https://example.com",
            "http://localhost.evil.example",
        ):
            with self.subTest(origin=origin):
                self.assertFalse(print_bridge.origin_allowed(origin))

    def test_extra_origin_is_an_exact_match(self):
        extra = frozenset({"https://crush-staging.azurewebsites.net"})
        self.assertTrue(
            print_bridge.origin_allowed(
                "https://crush-staging.azurewebsites.net", extra
            )
        )
        self.assertFalse(
            print_bridge.origin_allowed("https://other.azurewebsites.net", extra)
        )

    def test_allowed_hosts_match_the_printing_platforms(self):
        """The bridge runs without Django, so it keeps its own host list:
        crush.lu (check-in desk) and power-up.lu (Atmos KDS) with aliases."""
        expected = set()
        for domain in ("crush.lu", "power-up.lu"):
            expected.add(domain)
            expected.update(DOMAINS[domain].get("aliases", []))
        self.assertEqual(set(print_bridge.ALLOWED_HOSTS), expected)


class _Client:
    """A minimal browser-side WebSocket: masked frames, raw bytes."""

    KEY = base64.b64encode(b"crush-print-key!").decode()

    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.buffer = b""

    def send_raw(self, data):
        self.sock.sendall(data)

    def handshake(self, origin=CRUSH_ORIGIN, upgrade=True):
        lines = ["GET / HTTP/1.1", "Host: 127.0.0.1:40213"]
        if upgrade:
            lines += [
                "Upgrade: websocket",
                "Connection: Upgrade",
                f"Sec-WebSocket-Key: {self.KEY}",
                "Sec-WebSocket-Version: 13",
            ]
        if origin:
            lines.append(f"Origin: {origin}")
        self.send_raw(("\r\n".join(lines) + "\r\n\r\n").encode())
        while b"\r\n\r\n" not in self.buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            self.buffer += chunk
        head, _, self.buffer = self.buffer.partition(b"\r\n\r\n")
        return head.decode()

    def frame(self, opcode, payload, fin=True, masked=True):
        first = (0x80 if fin else 0) | opcode
        mask_bit = 0x80 if masked else 0
        length = len(payload)
        if length < 126:
            header = struct.pack("!BB", first, mask_bit | length)
        elif length < 1 << 16:
            header = struct.pack("!BBH", first, mask_bit | 126, length)
        else:
            header = struct.pack("!BBQ", first, mask_bit | 127, length)
        if not masked:
            return header + payload
        mask = os.urandom(4)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return header + mask + body

    def read_frame(self):
        while len(self.buffer) < 2:
            chunk = self.sock.recv(4096)
            if not chunk:
                return None
            self.buffer += chunk
        opcode, length = self.buffer[0] & 0x0F, self.buffer[1] & 0x7F
        while len(self.buffer) < 2 + length:
            self.buffer += self.sock.recv(4096)
        payload = self.buffer[2 : 2 + length]
        self.buffer = self.buffer[2 + length :]
        return opcode, payload

    def close(self):
        self.sock.close()


class BridgeServerTests(SimpleTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.output = Path(tmp.name) / "tickets.bin"
        self.server = print_bridge.BridgeServer(
            print_bridge.FileSink(self.output), port=0
        )
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def _client(self):
        client = _Client(self.port)
        self.addCleanup(client.close)
        return client

    def _printed(self):
        return self.output.read_bytes() if self.output.exists() else b""

    def _close_and_wait(self, client):
        client.send_raw(client.frame(print_bridge.OP_CLOSE, struct.pack("!H", 1000)))
        return client.read_frame()

    def test_handshake_answers_with_the_rfc_6455_accept_key(self):
        head = self._client().handshake()
        expected = base64.b64encode(
            hashlib.sha1(
                (_Client.KEY + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
            ).digest()
        ).decode()
        self.assertTrue(head.startswith("HTTP/1.1 101"), head)
        self.assertIn(f"Sec-WebSocket-Accept: {expected}", head)

    def test_binary_message_prints_the_exact_bytes(self):
        client = self._client()
        client.handshake()
        client.send_raw(client.frame(print_bridge.OP_BINARY, TICKET))
        self.assertEqual(
            self._close_and_wait(client), (print_bridge.OP_CLOSE, b"\x03\xe8")
        )
        self.assertEqual(self._printed(), TICKET)

    def test_fragmented_message_is_one_job(self):
        client = self._client()
        client.handshake()
        client.send_raw(client.frame(print_bridge.OP_BINARY, b"\x1b@HEL", fin=False))
        client.send_raw(client.frame(print_bridge.OP_PING, b"hi"))
        self.assertEqual(client.read_frame(), (print_bridge.OP_PONG, b"hi"))
        client.send_raw(client.frame(print_bridge.OP_CONTINUATION, b"LO\n"))
        self._close_and_wait(client)
        self.assertEqual(self._printed(), b"\x1b@HELLO\n")

    def test_two_tickets_on_one_connection_print_in_order(self):
        client = self._client()
        client.handshake()
        client.send_raw(client.frame(print_bridge.OP_BINARY, b"first\n"))
        client.send_raw(client.frame(print_bridge.OP_BINARY, b"second\n"))
        self._close_and_wait(client)
        self.assertEqual(self._printed(), b"first\nsecond\n")

    def test_foreign_origin_is_refused_and_prints_nothing(self):
        client = self._client()
        head = client.handshake(origin="https://example.com")
        self.assertTrue(head.startswith("HTTP/1.1 403"), head)
        self.assertEqual(self._printed(), b"")

    def test_missing_origin_is_refused(self):
        head = self._client().handshake(origin=None)
        self.assertTrue(head.startswith("HTTP/1.1 403"), head)

    def test_unmasked_frame_closes_with_protocol_error(self):
        client = self._client()
        client.handshake()
        client.send_raw(client.frame(print_bridge.OP_BINARY, b"x", masked=False))
        self.assertEqual(client.read_frame(), (print_bridge.OP_CLOSE, b"\x03\xea"))
        self.assertEqual(self._printed(), b"")

    def test_text_message_is_not_printed(self):
        client = self._client()
        client.handshake()
        client.send_raw(client.frame(print_bridge.OP_TEXT, b"hello"))
        self._close_and_wait(client)
        self.assertEqual(self._printed(), b"")

    def test_print_failure_closes_with_1011(self):
        """coach.js shows the coach a notice on close code 1011."""

        class FailingSink:
            def send(self, payload):
                raise print_bridge.PrintError("paper out")

        self.server.sink = FailingSink()
        client = self._client()
        client.handshake()
        client.send_raw(client.frame(print_bridge.OP_BINARY, b"ticket"))
        self.assertEqual(client.read_frame(), (print_bridge.OP_CLOSE, b"\x03\xf3"))

    def test_plain_get_shows_a_status_page(self):
        head = self._client().handshake(upgrade=False)
        self.assertTrue(head.startswith("HTTP/1.1 200"), head)

    def test_server_listens_on_loopback_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")


class PrintPagesFallBackOnlyOnAndroidTests(SimpleTestCase):
    """The intent: URL opens RawBT and fails on any other OS ("Failed to
    launch 'intent:…'" on Windows). Both print pages must keep it Android-only
    so a PC reports the missing bridge instead."""

    PAGES = (
        ROOT / "crush_lu" / "static" / "crush_lu" / "js" / "alpine" / "coach.js",
        ROOT / "power_up" / "templates" / "atmos" / "kds.html",
    )

    def test_intent_fallback_is_guarded_by_an_android_check(self):
        for page in self.PAGES:
            with self.subTest(page=page.name):
                source = page.read_text(encoding="utf-8")
                fallback = source.index("var fireIntentFallback = function () {")
                guard = source.index("if (!isAndroid) {", fallback)
                intent = source.index('"intent:base64,"', fallback)
                self.assertLess(guard, intent)
                self.assertIn("var isAndroid = /Android/i.test(", source[:fallback])
