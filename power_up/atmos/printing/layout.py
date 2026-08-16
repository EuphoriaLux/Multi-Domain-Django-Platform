"""Paper-width-aware ticket layout.

Layout is separated from ESC/POS bytes on purpose. This module produces a list
of `Directive`s — an intermediate representation that the byte encoder turns
into printer commands and the virtual receipt simulator turns into pixels.
One layout, two renderers, and the on-screen preview is therefore guaranteed to
match the paper rather than merely resembling it.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum


class Paper(Enum):
    """Roll width and the resulting column count in the printer's Font A."""

    MM80 = 48
    MM58 = 32

    @property
    def columns(self) -> int:
        return self.value


class Align(str, Enum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


@dataclass(frozen=True)
class Text:
    text: str
    align: Align = Align.LEFT
    bold: bool = False
    double_width: bool = False
    double_height: bool = False
    underline: bool = False


@dataclass(frozen=True)
class Rule:
    """A full-width divider."""

    char: str = "-"


@dataclass(frozen=True)
class Feed:
    lines: int = 1


@dataclass(frozen=True)
class QrCode:
    data: str
    size: int = 6  # printer module size, 1-16


@dataclass(frozen=True)
class Barcode:
    data: str
    height: int = 60


@dataclass(frozen=True)
class Cut:
    partial: bool = True


Directive = Text | Rule | Feed | QrCode | Barcode | Cut


@dataclass(frozen=True)
class TicketLine:
    name: str
    quantity: int = 1
    unit_price: Decimal = Decimal("0.00")
    note: str = ""

    @property
    def line_total(self) -> Decimal:
        return (self.unit_price * self.quantity).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )


@dataclass(frozen=True)
class TicketData:
    """Everything that appears on one drink ticket."""

    venue_name: str
    table_label: str
    ticket_code: str
    placed_at: datetime
    persona: str
    lines: tuple[TicketLine, ...]
    vignette: str = ""
    currency: str = "EUR"
    qr_payload: str = ""
    footer: str = ""
    contains_alcohol: bool = False

    @property
    def total(self) -> Decimal:
        return sum(
            (line.line_total for line in self.lines), Decimal("0.00")
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


_SYMBOLS = {"EUR": "€", "GBP": "£", "USD": "$"}


def money(amount: Decimal, currency: str = "EUR") -> str:
    """Format for the ticket. The euro sign needs CP858 — see `escpos`.

    An unrecognised currency falls back to its code rather than printing a bare
    number: an ambiguous price on a ticket is worse than an ugly one.
    """
    value = Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{_SYMBOLS.get(currency, currency + ' ')}{value:.2f}"


def justify(left: str, right: str, columns: int) -> str:
    """`left` flush left, `right` flush right, at least one space between.

    Truncates the left side rather than the right: a price that silently loses
    a digit is a worse failure than a clipped drink name.
    """
    gap = columns - len(right)
    if gap < 2:
        # Keep the *tail* — losing the cents is exactly the failure this
        # function exists to avoid.
        return right[-columns:].rjust(columns)
    left = left[: gap - 1]
    return f"{left}{' ' * (columns - len(left) - len(right))}{right}"


def wrap(text: str, columns: int, indent: str = "") -> list[str]:
    out: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            out.append("")
            continue
        out.extend(
            textwrap.wrap(
                paragraph,
                # textwrap counts the indent inside `width`; subtracting it here
                # too would narrow every line by the indent twice.
                width=max(8, columns),
                initial_indent=indent,
                subsequent_indent=indent,
                break_long_words=True,
                break_on_hyphens=False,
            )
            or [""]
        )
    return out


def _item_directives(line: TicketLine, columns: int, currency: str) -> list[Directive]:
    price = money(line.line_total, currency)
    prefix = f"{line.quantity:>2}  "
    name_width = columns - len(prefix) - len(price) - 1

    wrapped = textwrap.wrap(line.name, width=max(6, name_width)) or [line.name]
    head = justify(f"{prefix}{wrapped[0]}", price, columns)

    out: list[Directive] = [Text(head)]
    out.extend(Text(" " * len(prefix) + part) for part in wrapped[1:])
    if line.note:
        out.extend(
            Text(part) for part in wrap(line.note, columns, indent=" " * len(prefix))
        )
    return out


def render_ticket(ticket: TicketData, paper: Paper = Paper.MM80) -> list[Directive]:
    """Turn a ticket into an ordered list of printer directives."""
    cols = paper.columns
    out: list[Directive] = []

    out.extend(
        Text(part, Align.CENTER, bold=True, double_height=True)
        for part in wrap(ticket.venue_name.upper(), cols)
    )
    out.append(Text("bar ticket", Align.CENTER))
    out.append(Rule("="))

    out.append(Text(justify(f"TABLE {ticket.table_label}", ticket.ticket_code, cols), bold=True))
    out.append(Text(f"{ticket.placed_at:%a %d %b %H:%M}"))
    out.append(Rule())

    for line in ticket.lines:
        out.extend(_item_directives(line, cols, ticket.currency))

    out.append(Rule())
    out.append(
        Text(justify("TOTAL", money(ticket.total, ticket.currency), cols), bold=True)
    )

    if ticket.contains_alcohol:
        # Atmos cannot verify age and does not try. The check stays with the
        # person handing over the glass, so the ticket reminds them.
        out.append(Rule())
        # Kept under 32 characters so it does not wrap on a 58mm roll.
        out.append(Text("ALCOHOL - CHECK AT TABLE", Align.CENTER, bold=True))

    out.append(Rule("="))
    out.append(Text(ticket.persona.upper(), Align.CENTER, bold=True))
    out.append(Rule("="))

    if ticket.vignette:
        out.append(Feed(1))
        out.extend(Text(part, Align.LEFT) for part in wrap(ticket.vignette, cols))
        out.append(Feed(1))
        out.append(Rule())

    if ticket.qr_payload:
        out.append(QrCode(ticket.qr_payload))
    out.append(Text(ticket.ticket_code, Align.CENTER))

    if ticket.footer:
        out.extend(Text(part, Align.CENTER) for part in wrap(ticket.footer, cols))

    out.append(Feed(3))
    out.append(Cut())
    return out
