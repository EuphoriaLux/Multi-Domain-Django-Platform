"""
Crush.lu Check-In Ticket Printer Service.

Builds 80mm ESC/POS thermal receipt payloads for Speed Dating & Mixer events.
Generates a tangible, viral physical slip at check-in featuring:
- Event & Candidate identity (Table assignment, Candidate Badge #)
- Humorous "Dating Receipt" itemization
- Crush Coach Survival Rules (quirky tips & icebreakers)
- Room Mystery Radar (Secret Mission)
- Deep-link QR code to post-event MyCrush portal
"""

from __future__ import annotations

import base64
import random
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.utils import timezone

from power_up.atmos.printing.escpos import encode_ticket, render_plain_text
from power_up.atmos.printing.layout import (
    Align,
    Cut,
    Directive,
    Feed,
    Paper,
    QrCode,
    Rule,
    Text,
    justify,
    wrap,
)

if TYPE_CHECKING:
    from crush_lu.models import MeetupEvent, EventRegistration


COACH_SURVIVAL_RULES = [
    "3 Minuten vergehen schneller als die Tram auf dem Kirchberg.",
    "Das Thema 'Ex-Partner' in Runde 1 kostet -50 Aura-Punkte.",
    "Augenkontakt halten: Charmant lächeln, nicht wie ein Serienmörder anstarren.",
    "Notfall-Frage bei Stille: 'Was ist dein absolut nutzlosestes Talent?'",
    "Frag nach Leidenschaften & Reisezielen, nicht nach der Steuerklasse.",
    "Lächeln ist die halbe Miete. Die andere Hälfte ist aktives Zuhören.",
    "Niemand verurteilt dich für deinen Musikgeschmack (außer es ist Kirmes-Techno).",
    "Komplimente über Schuhe oder Lächeln funktionieren 10x besser als über Autos.",
]

SECRET_MISSIONS = [
    "Finde heraus, wer heimlich Trash-TV schaut – ohne direkt danach zu fragen!",
    "Finde die Person, die Pizza mit Ananas verteidigt!",
    "Finde heraus, wer schon mal Fallschirmspringen oder Bungee-Jumping gemacht hat!",
    "Finde die Person, die 4 oder mehr Sprachen spricht!",
    "Finde heraus, wer den besten Restaurant-Geheimtipp in Luxemburg hat!",
    "Finde die Person, die schon mal ihren Schlüssel im Auto eingesperrt hat!",
]


def _build_header_directives(
    event_title: str,
    date_str: str,
    attendee_name: str,
    table_label: str,
    candidate_num: str = "",
    cols: int = 48,
) -> list[Directive]:
    """Builds top banner and candidate table assignment."""
    out: list[Directive] = []

    out.append(Text("CRUSH.LU", Align.CENTER, bold=True, double_height=True))
    out.append(Text("SPEED DATING // CHECK-IN PASS", Align.CENTER, bold=True))
    out.append(Rule("="))

    out.append(Text(justify("EVENT:", event_title[: cols - 8], cols)))
    out.append(Text(justify("DATE:", date_str, cols)))
    out.append(Rule("-"))

    candidate_display = f"{attendee_name} {candidate_num}".strip()
    out.append(
        Text(
            justify(candidate_display.upper(), table_label.upper(), cols),
            bold=True,
            double_width=True,
        )
    )
    out.append(Rule("="))
    return out


def _build_receipt_directives(cols: int = 48) -> list[Directive]:
    """Builds the viral humorous dating receipt breakdown."""
    out: list[Directive] = []
    out.append(Text("DATING RECEIPT // SUMMARY", Align.CENTER, bold=True))
    out.append(Rule("-"))

    items = [
        ("1x Hope & Optimism", "EUR 0.00"),
        ("1x First Impression Charm", "100%"),
        ("1x Smalltalk Insurance", "INCLUDED"),
        ("1x Luxembourgish Icebreaker", "FREE"),
    ]
    for left, right in items:
        out.append(Text(justify(left, right, cols)))

    out.append(Rule("-"))
    out.append(Text(justify("TOTAL", "PRICELESS", cols), bold=True))
    out.append(Rule("="))
    return out


def _build_coach_rules_directives(
    custom_rules: list[str] | None = None,
    cols: int = 48,
) -> list[Directive]:
    """Builds Coach survival tips."""
    out: list[Directive] = []
    out.append(Text("CRUSH COACH // SURVIVAL GUIDE", Align.CENTER, bold=True))
    out.append(Rule("-"))

    rules = custom_rules or random.sample(
        COACH_SURVIVAL_RULES, min(3, len(COACH_SURVIVAL_RULES))
    )
    for i, rule in enumerate(rules, 1):
        for part in wrap(f"{i}. {rule}", cols):
            out.append(Text(part))
        out.append(Feed(1))

    out.append(Rule("="))
    return out


def _build_mission_directives(
    custom_mission: str | None = None,
    cols: int = 48,
) -> list[Directive]:
    """Builds the Room Mystery Radar secret icebreaker mission."""
    out: list[Directive] = []
    out.append(Text("MYSTERY RADAR // SECRET MISSION", Align.CENTER, bold=True))
    out.append(Rule("-"))

    mission = custom_mission or random.choice(SECRET_MISSIONS)
    for part in wrap(mission, cols):
        out.append(Text(part, Align.CENTER))

    out.append(Rule("="))
    return out


def _build_qr_footer_directives(
    qr_url: str,
    cols: int = 48,
) -> list[Directive]:
    """Builds QR code and social media footer."""
    out: list[Directive] = []
    out.append(Text("SCAN AFTER EVENT TO VOTE & MATCH:", Align.CENTER, bold=True))
    out.append(Feed(1))
    if qr_url:
        out.append(QrCode(qr_url, size=6))
    out.append(Text(qr_url, Align.CENTER))
    out.append(Feed(1))
    out.append(Text("Tag your story: @crush.lu #CrushSpeedDating", Align.CENTER))
    out.append(Rule("="))
    out.append(Feed(3))
    out.append(Cut(partial=True))
    return out


def build_checkin_ticket_directives(
    registration: EventRegistration | None = None,
    event: Event | None = None,
    table_number: int | None = None,
    seat_label: str = "",
    qr_url: str = "",
    paper: Paper = Paper.MM80,
) -> list[Directive]:
    """Builds the full sequence of directives for a check-in ticket."""
    cols = paper.columns

    event_title = "Speed Dating Event"
    date_str = timezone.now().strftime("%a %d %b %H:%M")
    attendee_name = "Guest"
    candidate_num = ""

    if registration:
        reg_user = getattr(registration, "user", None)
        if reg_user:
            profile = getattr(reg_user, "crushprofile", None)
            if profile and profile.display_name:
                attendee_name = profile.display_name
            elif reg_user.first_name:
                attendee_name = reg_user.first_name
            else:
                attendee_name = reg_user.username
        candidate_num = f"(#{registration.id})"

    if event:
        event_title = getattr(event, "title", event_title)
        event_dt = getattr(event, "date_time", None)
        if event_dt:
            date_str = event_dt.strftime("%a %d %b %H:%M")

    table_display = "WELCOME"
    if table_number:
        table_display = f"TABLE {table_number}"
        if seat_label:
            table_display += f" ({seat_label})"

    if not qr_url:
        base_domain = getattr(settings, "CRUSH_LU_CANONICAL_DOMAIN", "https://crush.lu")
        qr_url = f"{base_domain}/m/event-lobby/"

    directives: list[Directive] = []
    directives.extend(
        _build_header_directives(
            event_title=event_title,
            date_str=date_str,
            attendee_name=attendee_name,
            table_label=table_display,
            candidate_num=candidate_num,
            cols=cols,
        )
    )
    directives.extend(_build_receipt_directives(cols=cols))
    directives.extend(_build_coach_rules_directives(cols=cols))
    directives.extend(_build_mission_directives(cols=cols))
    directives.extend(_build_qr_footer_directives(qr_url=qr_url, cols=cols))

    return directives


def build_checkin_ticket_bytes(
    registration: EventRegistration | None = None,
    event: Event | None = None,
    table_number: int | None = None,
    seat_label: str = "",
    qr_url: str = "",
    paper: Paper = Paper.MM80,
) -> bytes:
    """Builds the raw ESC/POS byte sequence for a ticket."""
    directives = build_checkin_ticket_directives(
        registration=registration,
        event=event,
        table_number=table_number,
        seat_label=seat_label,
        qr_url=qr_url,
        paper=paper,
    )
    return encode_ticket(directives, paper=paper)


def build_checkin_ticket_base64(
    registration: EventRegistration | None = None,
    event: Event | None = None,
    table_number: int | None = None,
    seat_label: str = "",
    qr_url: str = "",
    paper: Paper = Paper.MM80,
) -> str:
    """Builds base64-encoded ESC/POS bytes suitable for JSON API transmission."""
    payload_bytes = build_checkin_ticket_bytes(
        registration=registration,
        event=event,
        table_number=table_number,
        seat_label=seat_label,
        qr_url=qr_url,
        paper=paper,
    )
    return base64.b64encode(payload_bytes).decode("ascii")


def preview_checkin_ticket_text(
    registration: EventRegistration | None = None,
    event: Event | None = None,
    table_number: int | None = None,
    seat_label: str = "",
    qr_url: str = "",
    paper: Paper = Paper.MM80,
) -> str:
    """Renders plain text monospace preview of the check-in ticket."""
    directives = build_checkin_ticket_directives(
        registration=registration,
        event=event,
        table_number=table_number,
        seat_label=seat_label,
        qr_url=qr_url,
        paper=paper,
    )
    return render_plain_text(directives, paper=paper)
