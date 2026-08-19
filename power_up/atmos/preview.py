"""Render sample tickets with no bar, no printer, and no API key.

    python -m power_up.atmos.preview            # 80mm, four linked orders
    python -m power_up.atmos.preview --paper 58
    python -m power_up.atmos.preview --bytes    # also dump the ESC/POS stream

Runs a short simulated service so the chronicle accumulates and later tickets
reference earlier guests — which is the part of the concept that is hard to
judge from a single ticket.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from decimal import Decimal

from .lore.chronicle import Chronicle, ChronicleEvent, DrinkLine, summarize
from .lore.engine import generate_vignette
from .printing.art import select_ascii_art, select_mission
from .printing.escpos import encode_ticket, render_plain_text
from .printing.layout import Paper, TicketData, TicketLine, render_ticket
from .printing.transport import Tcp9100Transport, Transport, WindowsRawTransport

VENUE = "The Velvet Hour"

# (name, qty, price, note, contains_alcohol) — matches seed_atmos_demo.py's
# MENU dict, so a demo ticket's alcohol banner reflects the same items the
# real seeded venue would serve.
SERVICE = [
    ("The Whispering Gambler", "4", [("Old Fashioned", 2, "8.50", "no ice", True)]),
    ("The Midnight Chemist", "9", [("Smoky Mezcalita", 1, "9.50", "extra lime", True)]),
    (
        "Detective Vance",
        "12",
        [("Rye Sour", 1, "9.00", "", True), ("Bar Nuts", 1, "4.00", "", False)],
    ),
    (
        "The Velvet Silhouette",
        "4",
        [("French 75", 3, "11.00", "one without gin", True)],
    ),
]


def generate_service_tickets(paper: Paper) -> list[tuple[TicketData, list, bytes, str]]:
    chronicle = Chronicle(VENUE, max_events=12)
    started = datetime(2026, 8, 16, 21, 40)
    items_out = []

    for index, (persona, table, items) in enumerate(SERVICE):
        placed = started + timedelta(minutes=index * 7)
        code = f"T{table.zfill(2)}-{index + 1:02d}"

        lines = tuple(
            TicketLine(name, qty, Decimal(price), note)
            for name, qty, price, note, _alcohol in items
        )
        event = ChronicleEvent(
            at=placed,
            table_label=table,
            persona=persona,
            drinks=tuple(DrinkLine(line.name, line.quantity) for line in lines),
            ticket_code=code,
        )

        result = generate_vignette(event, chronicle)
        drink_names = [line.name for line in lines]
        ascii_art = select_ascii_art(drink_names)
        mission = select_mission(f"demo-{table}-{code}")

        ticket = TicketData(
            venue_name=VENUE,
            table_label=table,
            ticket_code=code,
            placed_at=placed,
            persona=persona,
            lines=lines,
            vignette=result.text,
            ascii_art=ascii_art,
            mission=mission,
            qr_payload=f"https://power-up.lu/atmos/t/demo-table-{table}/",
            contains_alcohol=any(alcohol for *_, alcohol in items),
            footer="pay at the table",
        )

        directives = render_ticket(ticket, paper)
        payload = encode_ticket(directives, paper)
        diag = f"[{result.source}, {result.elapsed_ms} ms" + (f", {result.reason}" if result.reason else "") + "]"
        items_out.append((ticket, directives, payload, diag))

    return items_out



def build(paper: Paper, dump_bytes: bool) -> str:
    blocks: list[str] = []
    service_items = generate_service_tickets(paper)
    chronicle = Chronicle(VENUE, max_events=12)

    for index, (ticket, directives, payload, diag) in enumerate(service_items):
        blocks.append(render_plain_text(directives, paper))
        blocks.append(f"  {diag}")
        if dump_bytes:
            blocks.append(f"  [{len(payload)} bytes ESC/POS] {payload[:48]!r}...")
        blocks.append("")

        # Rebuild chronicle event for summary
        event = ChronicleEvent(
            at=ticket.placed_at,
            table_label=ticket.table_label,
            persona=ticket.persona,
            drinks=tuple(DrinkLine(line.name, line.quantity) for line in ticket.lines),
            ticket_code=ticket.ticket_code,
        )
        chronicle.record(event)

    blocks.append("=" * paper.columns)
    blocks.append("CHRONICLE")
    blocks.append("=" * paper.columns)
    blocks.append(summarize(chronicle))
    return "\n".join(blocks)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper", choices=("80", "58"), default="80")
    parser.add_argument("--bytes", action="store_true", dest="dump_bytes")
    parser.add_argument(
        "--printer",
        nargs="?",
        const="POS-80C",
        help="Windows printer name to send raw ESC/POS bytes (default: POS-80C)",
    )
    parser.add_argument(
        "--tcp",
        metavar="HOST[:PORT]",
        help="TCP network printer host:port (default port: 9100)",
    )
    parser.add_argument(
        "--order",
        type=int,
        choices=(1, 2, 3, 4),
        help="Print only a single specific order number (1-4) instead of all 4",
    )
    args = parser.parse_args()

    paper = Paper.MM80 if args.paper == "80" else Paper.MM58
    preview_output = build(paper, args.dump_bytes)
    print(preview_output)

    transport: Transport | None = None
    if args.printer:
        transport = WindowsRawTransport(printer_name=args.printer)
    elif args.tcp:
        parts = args.tcp.split(":")
        host = parts[0]
        port = int(parts[1]) if len(parts) > 1 else 9100
        transport = Tcp9100Transport(host=host, port=port)

    if transport:
        service_items = generate_service_tickets(paper)
        if args.order:
            service_items = [service_items[args.order - 1]]

        print(f"\n--- Sending {len(service_items)} ticket(s) to {transport} ---")
        for ticket, _directives, payload, _diag in service_items:
            print(f"Printing ticket {ticket.ticket_code} ({ticket.persona}) -> {len(payload)} bytes...")
            transport.send(payload)
        print("Done!")


if __name__ == "__main__":
    main()

