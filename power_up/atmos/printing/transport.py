"""Getting bytes to a printer.

**The deployment constraint this module makes concrete:** a raw TCP socket to
port 9100 only reaches a printer on the same network. An app hosted on Azure
cannot open that socket to a printer behind a bar's router. So either the
server runs on-premise, or a small print bridge runs on the bar's LAN and pulls
jobs from the cloud. `Tcp9100Transport` is the on-premise half; the bridge
speaks the same `send()` interface and is the reason this is an interface at all.

Every transport is fire-and-verify-nothing by design: ESC/POS over 9100 has no
acknowledgement. A successful `send()` means the bytes left the socket, not
that anything was printed. Paper-out looks exactly like success.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Protocol


class TransportError(Exception):
    """The bytes did not leave. Callers should surface this on the KDS."""


class Transport(Protocol):
    def send(self, payload: bytes) -> None: ...


class Tcp9100Transport:
    """Raw JetDirect socket — the standard for LAN thermal printers."""

    def __init__(self, host: str, port: int = 9100, timeout: float = 3.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    def send(self, payload: bytes) -> None:
        try:
            with socket.create_connection(
                (self.host, self.port), timeout=self.timeout
            ) as sock:
                sock.settimeout(self.timeout)
                sock.sendall(payload)
        except OSError as exc:
            raise TransportError(
                f"printer {self.host}:{self.port} unreachable: {exc}"
            ) from exc

    def __repr__(self) -> str:
        return f"Tcp9100Transport({self.host}:{self.port})"


class FileTransport:
    """Append to a file. Feeds `lp -o raw`, a named pipe, or a test fixture."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def send(self, payload: bytes) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("ab") as handle:
                handle.write(payload)
        except OSError as exc:
            raise TransportError(f"cannot write {self.path}: {exc}") from exc

    def __repr__(self) -> str:
        return f"FileTransport({self.path})"


class NullTransport:
    """Discards, but remembers. The default when no printer is configured.

    A venue without a printer still gets the full flow — the KDS shows the
    virtual receipt and staff read the vignette off the screen.
    """

    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def send(self, payload: bytes) -> None:
        self.sent.append(payload)

    def __repr__(self) -> str:
        return f"NullTransport({len(self.sent)} jobs)"


class WindowsRawTransport:
    """Sends raw ESC/POS byte streams to a local Windows printer queue.

    Uses `winspool.drv` directly via `ctypes` without external dependencies.
    Suitable for USB thermal receipt printers (e.g. POS-80C / M817) installed
    in Windows.
    """

    def __init__(
        self, printer_name: str = "POS-80C", doc_name: str = "Atmos Ticket"
    ) -> None:
        self.printer_name = printer_name
        self.doc_name = doc_name

    def send(self, payload: bytes) -> None:
        import sys

        if sys.platform != "win32":
            raise TransportError(
                f"WindowsRawTransport requires Windows, current platform is {sys.platform}"
            )

        import ctypes
        from ctypes import wintypes

        class DOC_INFO_1(ctypes.Structure):
            _fields_ = [
                ("pDocName", wintypes.LPCWSTR),
                ("pOutputFile", wintypes.LPCWSTR),
                ("pDataType", wintypes.LPCWSTR),
            ]

        winspool = ctypes.WinDLL("winspool.drv", use_last_error=True)
        handle = wintypes.HANDLE()

        if not winspool.OpenPrinterW(self.printer_name, ctypes.byref(handle), None):
            err = ctypes.get_last_error()
            raise TransportError(
                f"cannot open Windows printer '{self.printer_name}': error code {err}"
            )

        try:
            doc_info = DOC_INFO_1(self.doc_name, None, "RAW")
            job_id = winspool.StartDocPrinterW(handle, 1, ctypes.byref(doc_info))
            if job_id <= 0:
                err = ctypes.get_last_error()
                raise TransportError(
                    f"StartDocPrinter failed for '{self.printer_name}': error code {err}"
                )

            try:
                if not winspool.StartPagePrinter(handle):
                    err = ctypes.get_last_error()
                    raise TransportError(
                        f"StartPagePrinter failed for '{self.printer_name}': error code {err}"
                    )
                try:
                    written = wintypes.DWORD()
                    success = winspool.WritePrinter(
                        handle, payload, len(payload), ctypes.byref(written)
                    )
                    if not success or written.value != len(payload):
                        err = ctypes.get_last_error()
                        raise TransportError(
                            f"WritePrinter failed on '{self.printer_name}': wrote {written.value}/{len(payload)} bytes: error code {err}"
                        )
                finally:
                    winspool.EndPagePrinter(handle)
            finally:
                winspool.EndDocPrinter(handle)
        finally:
            winspool.ClosePrinter(handle)

    def __repr__(self) -> str:
        return f"WindowsRawTransport({self.printer_name!r})"

