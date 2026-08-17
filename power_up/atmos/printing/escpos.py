"""ESC/POS byte-stream generation, plus a plain-text renderer for previews.

**Codepage, and why it gets its own paragraph.** Thermal printers boot in
CP437, which has no euro sign. A Luxembourg bar printing `EUR` prices from a
default configuration gets a different glyph — usually `Õ` — on every single
ticket, and it is invariably discovered on install night. `encode_ticket`
therefore emits `ESC t 19` (CP858, identical to CP437 plus `€` at 0xD5) before
any text, and every string is encoded as CP858 thereafter.
"""

from __future__ import annotations

from ..text import ENCODING, encode as encode_text
from .layout import (
    Align,
    Barcode,
    Cut,
    Directive,
    Feed,
    Image,
    Paper,
    QrCode,
    Rule,
    Text,
)

ESC = b"\x1b"
GS = b"\x1d"

INIT = ESC + b"@"
CODEPAGE_CP858 = ESC + b"t" + bytes([19])

_ALIGN = {Align.LEFT: 0, Align.CENTER: 1, Align.RIGHT: 2}

__all__ = [
    "CODEPAGE_CP858",
    "ENCODING",
    "INIT",
    "encode_directive",
    "encode_text",
    "encode_ticket",
    "render_plain_text",
]


def _size_byte(double_width: bool, double_height: bool) -> int:
    """`GS ! n`: bits 4-6 are width multiplier-1, bits 0-2 height multiplier-1."""
    return (0x10 if double_width else 0) | (0x01 if double_height else 0)


def _qr_commands(data: str, size: int = 6) -> bytes:
    """The `GS ( k` family: select model, set size and ECC, store, print."""
    payload = encode_text(data)
    store_len = len(payload) + 3

    return b"".join(
        [
            GS + b"(k" + bytes([4, 0, 49, 65, 50, 0]),            # model 2
            GS + b"(k" + bytes([3, 0, 49, 67, max(1, min(16, size))]),
            GS + b"(k" + bytes([3, 0, 49, 69, 49]),               # ECC level M
            GS
            + b"(k"
            + bytes([store_len & 0xFF, (store_len >> 8) & 0xFF, 49, 80, 48])
            + payload,
            GS + b"(k" + bytes([3, 0, 49, 81, 48]),               # print
        ]
    )


MAX_BARCODE_DATA = 250


class BarcodeTooLong(ValueError):
    """Raised rather than letting `bytes([len])` overflow in the print path."""


def _barcode_commands(data: str, height: int = 60) -> bytes:
    """CODE128 via `GS k 73`. The `{B` prefix selects code set B.

    Code set B covers ASCII 32-126 only, and the length is a single byte, so
    both are enforced here instead of surfacing as a `ValueError` from
    `bytes()` or as unreadable bars on paper.
    """
    ascii_only = "".join(ch if 32 <= ord(ch) <= 126 else "?" for ch in data)
    if len(ascii_only) > MAX_BARCODE_DATA:
        raise BarcodeTooLong(
            f"CODE128 payload is {len(ascii_only)} chars, max {MAX_BARCODE_DATA}"
        )

    payload = b"{B" + ascii_only.encode("ascii")
    return b"".join(
        [
            GS + b"h" + bytes([max(1, min(255, height))]),
            GS + b"w" + bytes([2]),
            GS + b"H" + bytes([2]),  # human-readable text below the bars
            GS + b"k" + bytes([73, len(payload)]) + payload,
        ]
    )


def _image_commands(
    source: str | bytes,
    width: int = 192,
    invert: bool = False,
) -> bytes:
    """The `GS v 0` command: encode a 1-bit monochrome raster image."""
    import io
    from PIL import Image as PILImage

    if isinstance(source, bytes):
        img = PILImage.open(io.BytesIO(source))
    else:
        img = PILImage.open(source)

    img = img.convert("L")
    target_w = max(8, (width // 8) * 8)
    target_h = int(img.height * (target_w / img.width))
    img = img.resize((target_w, target_h), PILImage.Resampling.LANCZOS)

    # Convert to 1-bit monochrome: 0 in PIL is black, 255 is white
    mono = img.point(lambda p: 0 if p < 180 else 255, mode="1")
    if invert:
        mono = mono.point(lambda p: 255 if p == 0 else 0, mode="1")

    width_dots, height_dots = mono.size
    width_bytes = width_dots // 8
    xl = width_bytes & 0xFF
    xh = (width_bytes >> 8) & 0xFF
    yl = height_dots & 0xFF
    yh = (height_dots >> 8) & 0xFF

    raw_data = bytearray()
    pixels = mono.load()
    for y in range(height_dots):
        for x_byte in range(width_bytes):
            byte_val = 0
            for bit in range(8):
                x = x_byte * 8 + bit
                # In ESC/POS bit 1 is black, bit 0 is white
                if pixels[x, y] == 0:
                    byte_val |= 1 << (7 - bit)
            raw_data.append(byte_val)

    return GS + b"v0\x00" + bytes([xl, xh, yl, yh]) + bytes(raw_data)


def encode_directive(directive: Directive, paper: Paper) -> bytes:
    cols = paper.columns

    if isinstance(directive, Rule):
        return encode_text(directive.char * cols) + b"\n"

    if isinstance(directive, Feed):
        return ESC + b"d" + bytes([max(0, min(255, directive.lines))])

    if isinstance(directive, Cut):
        # GS V B n = feed to cutting position, partial cut. GS V 0 = full cut.
        if directive.partial:
            return GS + b"VB" + bytes([0])
        return GS + b"V" + bytes([0])

    if isinstance(directive, QrCode):
        return (
            ESC + b"a" + bytes([1])
            + _qr_commands(directive.data, directive.size)
            + ESC + b"a" + bytes([0])
        )

    if isinstance(directive, Barcode):
        return (
            ESC + b"a" + bytes([1])
            + _barcode_commands(directive.data, directive.height)
            + ESC + b"a" + bytes([0])
        )

    if isinstance(directive, Image):
        return (
            ESC + b"a" + bytes([_ALIGN[directive.align]])
            + _image_commands(directive.source, directive.width, directive.invert)
            + ESC + b"a" + bytes([0])
        )

    if isinstance(directive, Text):
        size = _size_byte(directive.double_width, directive.double_height)
        out = [
            ESC + b"a" + bytes([_ALIGN[directive.align]]),
            ESC + b"E" + bytes([1 if directive.bold else 0]),
            ESC + b"-" + bytes([1 if directive.underline else 0]),
            GS + b"!" + bytes([size]),
            encode_text(directive.text),
            b"\n",
            GS + b"!" + bytes([0]),
            ESC + b"-" + bytes([0]),
            ESC + b"E" + bytes([0]),
            ESC + b"a" + bytes([0]),
        ]
        return b"".join(out)

    raise TypeError(f"unknown directive: {directive!r}")


def encode_ticket(directives: list[Directive], paper: Paper = Paper.MM80) -> bytes:
    """Serialize a laid-out ticket into a raw ESC/POS byte stream."""
    parts = [INIT, CODEPAGE_CP858]
    parts.extend(encode_directive(d, paper) for d in directives)
    return b"".join(parts)


def _placeholder(label: str, columns: int) -> str:
    """Preview stand-in for a graphic, elided so it never exceeds the paper."""
    if len(label) > columns:
        label = label[: max(4, columns - 4)] + "...]"
    return label.center(columns)


def render_plain_text(directives: list[Directive], paper: Paper = Paper.MM80) -> str:
    """Monospace preview of the same directives — tests, logs, and the simulator.

    Shares the layout pass with `encode_ticket`, so what you see here is what
    the paper gets, modulo double-height glyphs and the QR bitmap.
    """
    cols = paper.columns
    out: list[str] = []

    for directive in directives:
        if isinstance(directive, Rule):
            out.append(directive.char * cols)
        elif isinstance(directive, Feed):
            out.extend("" for _ in range(directive.lines))
        elif isinstance(directive, Cut):
            out.append(">8".rjust(cols, "-"))
        elif isinstance(directive, QrCode):
            out.append(_placeholder(f"[QR {directive.data}]", cols))
        elif isinstance(directive, Barcode):
            out.append(_placeholder(f"[BARCODE {directive.data}]", cols))
        elif isinstance(directive, Image):
            out.append(_placeholder("[IMAGE: CRUSH GHOST LOGO]", cols))
        elif isinstance(directive, Text):
            text = directive.text
            if directive.align is Align.CENTER:
                text = text.center(cols)
            elif directive.align is Align.RIGHT:
                text = text.rjust(cols)
            out.append(text.rstrip())
        else:
            raise TypeError(f"unknown directive: {directive!r}")

    return "\n".join(out)
