"""Local print bridge: thermal-ticket printing from a Windows, macOS or Linux PC.

The check-in desk (``triggerRawBtPrint`` in
``crush_lu/static/crush_lu/js/alpine/coach.js``) and the Atmos KDS
(``power_up/templates/atmos/kds.html``) send every ticket as raw ESC/POS bytes,
one binary WebSocket message to ``ws://127.0.0.1:40213/``. On Android the
RawBT app listens there. A computer has no RawBT, so the browser logs
``ERR_CONNECTION_REFUSED`` and nothing prints. Run this script on the computer
the thermal printer is installed on and the same page prints there unchanged.
The bytes are the same, so the logo, QR code and paper cut come out exactly as
they do on Android::

    py scripts\\print_bridge.py --list                # Windows: show printers
    py scripts\\print_bridge.py --printer "POS-80C"   # leave it running
    python3 scripts/print_bridge.py                   # macOS/Linux: default queue
    python3 scripts/print_bridge.py --output out.bin  # no printer: append to a file

Standard library only (Python 3.9+), so it runs without the project's
virtualenv. On Windows each job goes to the spooler with datatype RAW, so the
printer's driver must accept RAW jobs (vendor "POS-80" drivers and Windows'
own "Generic / Text Only" driver do). macOS and Linux use ``lp -o raw``.

Only pages from the platform's own hosts may print. A browser lets any website
open a WebSocket to 127.0.0.1, and the Origin header is what tells a crush.lu
tab apart from some other site feeding the printer.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import logging
import shutil
import socketserver
import struct
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger("print_bridge")

# RawBT's port. The pages hardcode it, so the bridge never listens elsewhere.
PORT = 40213
# A check-in ticket is ~20 KB; this only stops a runaway client.
MAX_JOB_BYTES = 4 * 1024 * 1024
MAX_HEADER_LINE = 8192
MAX_HEADER_LINES = 100
SOCKET_TIMEOUT_SECONDS = 30
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONTINUATION, OP_TEXT, OP_BINARY = 0x0, 0x1, 0x2
OP_CLOSE, OP_PING, OP_PONG = 0x8, 0x9, 0xA
CLOSE_PROTOCOL_ERROR = 1002
CLOSE_INTERNAL_ERROR = 1011

# Hosts whose pages print through the bridge: crush.lu (check-in desk) and
# power-up.lu (Atmos KDS) with their aliases from azureproject/domains.py.
# Literal because this script runs without Django;
# crush_lu/tests/test_print_bridge.py fails when domains.py drifts from it.
ALLOWED_HOSTS = frozenset(
    {
        "crush.lu",
        "www.crush.lu",
        "test.crush.lu",
        "power-up.lu",
        "www.power-up.lu",
        "powerup.lu",
        "www.powerup.lu",
        "test.power-up.lu",
        "test.powerup.lu",
    }
)


class PrintError(Exception):
    """The job did not reach the printer. The message is shown to the coach."""


class ProtocolError(Exception):
    """The client broke RFC 6455; the connection is closed with 1002."""


def origin_allowed(origin: str | None, extra: frozenset[str] = frozenset()) -> bool:
    """True if a page from ``origin`` may print.

    Production hosts must be HTTPS. ``localhost``, ``127.0.0.1`` and the
    ``*.localhost`` dev aliases are accepted on any port, since only a page
    served from this machine can carry those origins.
    """
    if not origin:
        return False
    if origin in extra:
        return True
    parts = urlsplit(origin)
    host = (parts.hostname or "").lower()
    if parts.scheme == "https" and host in ALLOWED_HOSTS:
        return True
    return parts.scheme in ("http", "https") and (
        host in ("localhost", "127.0.0.1") or host.endswith(".localhost")
    )


def accept_key(client_key: str) -> str:
    # RFC 6455 fixes SHA-1 here; the accept key proves the server speaks
    # WebSocket, it protects nothing.
    digest = hashlib.sha1(
        (client_key + WEBSOCKET_GUID).encode("ascii"), usedforsecurity=False
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def _unmask(data: bytes, mask: bytes) -> bytes:
    if not data:
        return data
    keystream = (mask * (len(data) // 4 + 1))[: len(data)]
    xored = int.from_bytes(data, "big") ^ int.from_bytes(keystream, "big")
    return xored.to_bytes(len(data), "big")


def encode_frame(opcode: int, payload: bytes = b"") -> bytes:
    """A single unmasked frame, as a server sends it."""
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", 0x80 | opcode, length)
    elif length < 1 << 16:
        header = struct.pack("!BBH", 0x80 | opcode, 126, length)
    else:
        header = struct.pack("!BBQ", 0x80 | opcode, 127, length)
    return header + payload


# --- Printer sinks -----------------------------------------------------------


class FileSink:
    """Append every job to a file: a dry run, or a test fixture."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def describe(self) -> str:
        return f"file {self.path}"

    def send(self, payload: bytes) -> None:
        try:
            with self.path.open("ab") as handle:
                handle.write(payload)
        except OSError as exc:
            raise PrintError(f"cannot write {self.path}: {exc}") from exc


class CupsSink:
    """macOS / Linux: hand the bytes to CUPS untouched (``lp -o raw``)."""

    def __init__(self, printer: str | None = None) -> None:
        if not shutil.which("lp"):
            raise PrintError("`lp` not found: install CUPS, or use --output FILE")
        self.printer = printer

    def describe(self) -> str:
        return f"CUPS queue {self.printer or '(system default)'}"

    def send(self, payload: bytes) -> None:
        command = ["lp", "-o", "raw", "-t", "Crush ticket"]
        if self.printer:
            command += ["-d", self.printer]
        try:
            result = subprocess.run(
                command, input=payload, capture_output=True, timeout=30, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PrintError(f"lp failed: {exc}") from exc
        if result.returncode != 0:
            detail = result.stderr.decode(errors="replace").strip()
            raise PrintError(f"lp failed: {detail}")


# Windows: winspool through ctypes, so no pywin32 is needed.
PRINTER_ENUM_LOCAL = 0x2
PRINTER_ENUM_CONNECTIONS = 0x4
ERROR_INVALID_DATATYPE = 1804


def _winspool():
    import ctypes
    from ctypes import wintypes

    dll = ctypes.WinDLL("winspool.drv", use_last_error=True)
    handle_p = ctypes.POINTER(wintypes.HANDLE)
    dword_p = ctypes.POINTER(wintypes.DWORD)
    signatures = {
        "OpenPrinterW": (
            wintypes.BOOL,
            [wintypes.LPWSTR, handle_p, ctypes.c_void_p],
        ),
        "ClosePrinter": (wintypes.BOOL, [wintypes.HANDLE]),
        "StartDocPrinterW": (
            wintypes.DWORD,
            [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p],
        ),
        "EndDocPrinter": (wintypes.BOOL, [wintypes.HANDLE]),
        "StartPagePrinter": (wintypes.BOOL, [wintypes.HANDLE]),
        "EndPagePrinter": (wintypes.BOOL, [wintypes.HANDLE]),
        "WritePrinter": (
            wintypes.BOOL,
            [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, dword_p],
        ),
        "GetDefaultPrinterW": (wintypes.BOOL, [wintypes.LPWSTR, dword_p]),
        "EnumPrintersW": (
            wintypes.BOOL,
            [
                wintypes.DWORD,
                wintypes.LPWSTR,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
                dword_p,
                dword_p,
            ],
        ),
    }
    for name, (restype, argtypes) in signatures.items():
        function = getattr(dll, name)
        function.restype = restype
        function.argtypes = argtypes
    return ctypes, wintypes, dll


def _windows_error(ctypes, action: str) -> PrintError:
    code = ctypes.get_last_error()
    return PrintError(f"{action}: {ctypes.FormatError(code)} (error {code})")


def windows_default_printer() -> str | None:
    ctypes, wintypes, dll = _winspool()
    size = wintypes.DWORD(0)
    dll.GetDefaultPrinterW(None, ctypes.byref(size))
    if not size.value:
        return None
    buffer = ctypes.create_unicode_buffer(size.value)
    if not dll.GetDefaultPrinterW(buffer, ctypes.byref(size)):
        return None
    return buffer.value


def windows_printers() -> list[str]:
    ctypes, wintypes, dll = _winspool()

    class PRINTER_INFO_4W(ctypes.Structure):
        _fields_ = (
            ("pPrinterName", wintypes.LPWSTR),
            ("pServerName", wintypes.LPWSTR),
            ("Attributes", wintypes.DWORD),
        )

    flags = PRINTER_ENUM_LOCAL | PRINTER_ENUM_CONNECTIONS
    needed, returned = wintypes.DWORD(0), wintypes.DWORD(0)
    dll.EnumPrintersW(
        flags, None, 4, None, 0, ctypes.byref(needed), ctypes.byref(returned)
    )
    if not needed.value:
        return []
    buffer = (ctypes.c_byte * needed.value)()
    if not dll.EnumPrintersW(
        flags,
        None,
        4,
        buffer,
        needed.value,
        ctypes.byref(needed),
        ctypes.byref(returned),
    ):
        raise _windows_error(ctypes, "cannot list printers")
    infos = ctypes.cast(buffer, ctypes.POINTER(PRINTER_INFO_4W))
    return [infos[i].pPrinterName for i in range(returned.value)]


class WindowsSink:
    """Windows: one RAW spooler job per ticket, bytes passed through as-is."""

    def __init__(self, printer: str | None = None) -> None:
        self.printer = printer or windows_default_printer()
        if not self.printer:
            raise PrintError(
                "no default printer: pass --printer NAME (see --list for names)"
            )
        # Fail at startup, not at the first check-in.
        ctypes, wintypes, dll = _winspool()
        handle = wintypes.HANDLE()
        if not dll.OpenPrinterW(self.printer, ctypes.byref(handle), None):
            raise _windows_error(ctypes, f"cannot open printer {self.printer!r}")
        dll.ClosePrinter(handle)

    def describe(self) -> str:
        return f"Windows printer {self.printer!r}"

    def send(self, payload: bytes) -> None:
        ctypes, wintypes, dll = _winspool()

        class DOC_INFO_1W(ctypes.Structure):
            _fields_ = (
                ("pDocName", wintypes.LPWSTR),
                ("pOutputFile", wintypes.LPWSTR),
                ("pDatatype", wintypes.LPWSTR),
            )

        handle = wintypes.HANDLE()
        if not dll.OpenPrinterW(self.printer, ctypes.byref(handle), None):
            raise _windows_error(ctypes, f"cannot open printer {self.printer!r}")
        try:
            doc = DOC_INFO_1W("Crush ticket", None, "RAW")
            if not dll.StartDocPrinterW(handle, 1, ctypes.byref(doc)):
                if ctypes.get_last_error() == ERROR_INVALID_DATATYPE:
                    raise PrintError(
                        f"the driver of {self.printer!r} refuses RAW jobs. "
                        "Install the printer with its vendor's POS driver or "
                        "Windows' 'Generic / Text Only' driver."
                    )
                raise _windows_error(ctypes, "cannot start the print job")
            # Each End* call runs whenever its Start* succeeded, but its
            # result only counts when nothing failed before it:
            # EndDocPrinter is where the spooler commits the job, so a
            # False there means no ticket.
            try:
                if not dll.StartPagePrinter(handle):
                    raise _windows_error(ctypes, "cannot start the page")
                try:
                    written = wintypes.DWORD(0)
                    ok = dll.WritePrinter(
                        handle, payload, len(payload), ctypes.byref(written)
                    )
                    if not ok:
                        raise _windows_error(ctypes, "cannot write to the printer")
                    if written.value != len(payload):
                        raise PrintError(
                            f"printer took {written.value} of {len(payload)} bytes"
                        )
                except BaseException:
                    dll.EndPagePrinter(handle)
                    raise
                if not dll.EndPagePrinter(handle):
                    raise _windows_error(ctypes, "cannot finish the page")
            except BaseException:
                dll.EndDocPrinter(handle)
                raise
            if not dll.EndDocPrinter(handle):
                raise _windows_error(ctypes, "cannot finish the print job")
        finally:
            dll.ClosePrinter(handle)


def make_sink(printer: str | None, output: str | None):
    if output:
        return FileSink(output)
    if sys.platform == "win32":
        return WindowsSink(printer)
    return CupsSink(printer)


def list_printers() -> list[str]:
    if sys.platform == "win32":
        return windows_printers()
    if not shutil.which("lpstat"):
        raise PrintError("`lpstat` not found: install CUPS")
    result = subprocess.run(
        ["lpstat", "-e"], capture_output=True, text=True, check=False
    )
    return result.stdout.split()


# --- WebSocket server (RFC 6455, just what the print pages use) -------------


class BridgeHandler(socketserver.StreamRequestHandler):
    timeout = SOCKET_TIMEOUT_SECONDS

    def handle(self) -> None:
        try:
            headers = self._read_request_head()
        except (ProtocolError, OSError, UnicodeDecodeError):
            return
        if headers is None:
            return
        if headers.get("upgrade", "").lower() != "websocket":
            self._respond(
                "200 OK", f"Crush print bridge: printing to {self.server.sink_name}\n"
            )
            return
        origin = headers.get("origin")
        if not origin_allowed(origin, self.server.extra_origins):
            logger.warning("Refused a print connection from origin %r", origin)
            self._respond("403 Forbidden", "origin not allowed\n")
            return
        key = headers.get("sec-websocket-key")
        if not key or headers.get("sec-websocket-version") != "13":
            self._respond("400 Bad Request", "not a WebSocket handshake\n")
            return
        self.wfile.write(
            (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept_key(key)}\r\n\r\n"
            ).encode("ascii")
        )
        try:
            self._serve_messages(origin)
        except ProtocolError as exc:
            logger.warning("Dropped a connection from %s: %s", origin, exc)
            self._close(CLOSE_PROTOCOL_ERROR)
        except PrintError as exc:
            logger.error("PRINT FAILED: %s", exc)
            self._close(CLOSE_INTERNAL_ERROR)
        except (OSError, ConnectionError):
            pass  # the tab closed or the socket timed out

    def _read_request_head(self) -> dict[str, str] | None:
        request_line = self.rfile.readline(MAX_HEADER_LINE)
        if not request_line.startswith(b"GET "):
            return None
        headers = {}
        for _ in range(MAX_HEADER_LINES):
            line = self.rfile.readline(MAX_HEADER_LINE)
            if line in (b"\r\n", b"\n", b""):
                return headers
            name, _, value = line.decode("latin-1").partition(":")
            headers[name.strip().lower()] = value.strip()
        raise ProtocolError("too many header lines")

    def _respond(self, status: str, body: str) -> None:
        payload = body.encode("utf-8")
        self.wfile.write(
            (
                f"HTTP/1.1 {status}\r\n"
                "Content-Type: text/plain; charset=utf-8\r\n"
                f"Content-Length: {len(payload)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            + payload
        )

    def _close(self, code: int) -> None:
        try:
            self.wfile.write(encode_frame(OP_CLOSE, struct.pack("!H", code)))
        except OSError:
            pass

    def _read_exact(self, count: int) -> bytes:
        data = self.rfile.read(count)
        if len(data) < count:
            raise ConnectionError("client went away mid-frame")
        return data

    def _read_frame(self) -> tuple[bool, int, bytes]:
        first, second = self._read_exact(2)
        length = second & 0x7F
        if length == 126:
            (length,) = struct.unpack("!H", self._read_exact(2))
        elif length == 127:
            (length,) = struct.unpack("!Q", self._read_exact(8))
        if not second & 0x80:
            raise ProtocolError("client frames must be masked")
        if length > MAX_JOB_BYTES:
            raise ProtocolError(f"frame of {length} bytes exceeds the job limit")
        mask = self._read_exact(4)
        return bool(first & 0x80), first & 0x0F, _unmask(self._read_exact(length), mask)

    def _serve_messages(self, origin: str) -> None:
        message = bytearray()
        message_opcode = None
        while True:
            fin, opcode, payload = self._read_frame()
            if opcode == OP_CLOSE:
                self.wfile.write(encode_frame(OP_CLOSE, payload[:2]))
                return
            if opcode == OP_PING:
                self.wfile.write(encode_frame(OP_PONG, payload))
                continue
            if opcode == OP_PONG:
                continue
            if opcode in (OP_TEXT, OP_BINARY):
                if message_opcode is not None:
                    raise ProtocolError("new message before the last one ended")
                message_opcode = opcode
            elif opcode != OP_CONTINUATION or message_opcode is None:
                raise ProtocolError(f"unexpected opcode {opcode:#x}")
            message += payload
            if len(message) > MAX_JOB_BYTES:
                raise ProtocolError("job exceeds the size limit")
            if not fin:
                continue
            if message_opcode == OP_BINARY:
                self.server.print_job(bytes(message), origin)
            else:
                logger.warning("Ignored a text message from %s", origin)
            message = bytearray()
            message_opcode = None


class BridgeServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """Loopback only: a printer endpoint is never reachable from the LAN."""

    daemon_threads = True

    def __init__(self, sink, port: int = PORT, extra_origins=()) -> None:
        super().__init__(("127.0.0.1", port), BridgeHandler)
        self.sink = sink
        self.sink_name = sink.describe()
        self.extra_origins = frozenset(extra_origins)
        # Two tabs printing at once must not interleave their tickets.
        self._print_lock = threading.Lock()

    def print_job(self, payload: bytes, origin: str) -> None:
        with self._print_lock:
            self.sink.send(payload)
        logger.info("Printed %d bytes from %s", len(payload), origin)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Print Crush.lu check-in tickets (and Atmos KDS tickets) on a "
            "thermal printer attached to this computer. Leave it running "
            "while the check-in page is open."
        )
    )
    parser.add_argument(
        "--printer", help="printer or queue name (default: the system default)"
    )
    parser.add_argument(
        "--output", help="append each job to this file instead of printing"
    )
    parser.add_argument(
        "--list", action="store_true", help="list installed printers and exit"
    )
    parser.add_argument(
        "--allow-origin",
        action="append",
        default=[],
        metavar="ORIGIN",
        help="also accept jobs from this exact origin, e.g. a staging slot "
        "(https://<app>.azurewebsites.net); repeatable",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")

    try:
        if args.list:
            for name in list_printers():
                print(name)
            return 0
        sink = make_sink(args.printer, args.output)
    except PrintError as exc:
        logger.error("%s", exc)
        return 1

    try:
        server = BridgeServer(sink, PORT, args.allow_origin)
    except OSError as exc:
        logger.error(
            "Cannot listen on 127.0.0.1:%d (%s). Is another print bridge "
            "already running?",
            PORT,
            exc,
        )
        return 1

    logger.info(
        "Print bridge ready on ws://127.0.0.1:%d/ and printing to %s. Keep this "
        "window open during check-in; Ctrl+C stops it.",
        PORT,
        sink.describe(),
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
