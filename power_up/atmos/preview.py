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
from .printing.escpos import encode_ticket, render_plain_text
from .printing.layout import Paper, TicketData, TicketLine, render_ticket

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


def build(paper: Paper, dump_bytes: bool) -> str:
    chronicle = Chronicle(VENUE, max_events=12)
    started = datetime(2026, 8, 16, 21, 40)
    blocks: list[str] = []

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

        # No provider configured, so this exercises the deterministic fallback —
        # deliberately, because that is what prints when the uplink is having
        # a bad night and it is the path least likely to be reviewed otherwise.
        result = generate_vignette(event, chronicle)

        ticket = TicketData(
            venue_name=VENUE,
            table_label=table,
            ticket_code=code,
            placed_at=placed,
            persona=persona,
            lines=lines,
            vignette=result.text,
            # `/atmos/o/<code>` was never implemented (order_status is
            # guest-cookie-scoped, per spec §8.1, so a bare public code
            # couldn't resolve one anyway) — the live app points tickets at
            # the table's own scan URL instead (views.py's order_status()).
            # This demo token isn't real (preview.py has no DB access), but
            # the URL shape now matches the live app's, not a 404.
            qr_payload=f"https://power-up.lu/atmos/t/demo-table-{table}/",
            contains_alcohol=any(alcohol for *_, alcohol in items),
            footer="pay at the table",
        )

        directives = render_ticket(ticket, paper)
        blocks.append(render_plain_text(directives, paper))
        blocks.append(
            f"  [{result.source}, {result.elapsed_ms} ms"
            + (f", {result.reason}" if result.reason else "")
            + "]"
        )

        if dump_bytes:
            payload = encode_ticket(directives, paper)
            blocks.append(f"  [{len(payload)} bytes ESC/POS] {payload[:48]!r}...")

        blocks.append("")

    blocks.append("=" * paper.columns)
    blocks.append("CHRONICLE")
    blocks.append("=" * paper.columns)
    blocks.append(summarize(chronicle))
    return "\n".join(blocks)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper", choices=("80", "58"), default="80")
    parser.add_argument("--bytes", action="store_true", dest="dump_bytes")
    args = parser.parse_args()

    paper = Paper.MM80 if args.paper == "80" else Paper.MM58
    print(build(paper, args.dump_bytes))


if __name__ == "__main__":
    main()
