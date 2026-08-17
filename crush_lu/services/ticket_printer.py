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
    # Double-width characters take 2 columns each on thermal printers, so
    # a 48-col roll fits at most 24 double-width characters per line.
    dw_cols = max(16, cols // 2)
    out.append(
        Text(
            justify(candidate_display.upper()[: dw_cols - len(table_label) - 1], table_label.upper(), dw_cols),
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


def _build_room_stats_directives(
    registration: EventRegistration | None = None,
    event: MeetupEvent | None = None,
    cols: int = 48,
) -> list[Directive]:
    """Builds collective group statistics for the event attendees."""
    out: list[Directive] = []

    if not (registration and getattr(registration, "pk", None)):
        return out

    try:
        from collections import Counter
        from crush_lu.models import EventRegistration

        event_id = getattr(event, "id", None) or getattr(
            registration, "event_id", None
        )
        if not event_id:
            return out

        regs = list(
            EventRegistration.objects.filter(
                event_id=event_id,
                status__in=["confirmed", "attended"],
            )
            .select_related("user__crushprofile")
            .prefetch_related(
                "user__crushprofile__interests_new",
                "user__crushprofile__defects",
            )
        )
        total = len(regs)
        if total < 2:
            return out

        all_interests = []
        all_defects = []
        all_langs = []
        first_step_counts: Counter[str] = Counter()

        for r in regs:
            prof = getattr(getattr(r, "user", None), "crushprofile", None)
            if not prof:
                continue
            all_interests.extend([i.label for i in prof.interests_new.all()])
            all_defects.extend([d.label for d in prof.defects.all()])
            all_langs.extend(prof.event_languages or [])
            if prof.first_step_preference:
                first_step_counts[prof.first_step_preference] += 1

        top_interests = Counter(all_interests).most_common(3)
        top_defects = Counter(all_defects).most_common(2)

        out.append(Text("ROOM DATA // STATS DE LA SOIREE", Align.CENTER, bold=True))
        out.append(Rule("-"))

        if top_interests:
            out.append(Text("TOP PASSIONS DANS LA SALLE :", bold=True))
            for name, count in top_interests:
                pct = int((count / total) * 100)
                out.append(Text(justify(f"• {name}", f"{pct}% ({count}p)", cols)))

        if first_step_counts:
            they_init = first_step_counts.get("they_initiate", 0)
            they_pct = int((they_init / total) * 100)
            if they_pct > 0:
                out.append(Text(f"• {they_pct}% attendent le premier pas"))

        if top_defects:
            def_name, def_cnt = top_defects[0]
            def_pct = int((def_cnt / total) * 100)
            out.append(Text(f"• {def_pct}% avouent : '{def_name}'"))

        if all_langs:
            lang_counts = Counter(all_langs)
            lu_pct = int((lang_counts.get("lu", 0) / total) * 100)
            fr_pct = int((lang_counts.get("fr", 0) / total) * 100)
            de_pct = int((lang_counts.get("de", 0) / total) * 100)
            en_pct = int((lang_counts.get("en", 0) / total) * 100)
            out.append(
                Text(
                    f"• Langues: LU {lu_pct}% | FR {fr_pct}% | DE {de_pct}% | EN {en_pct}%"
                )
            )

        out.append(Rule("="))
    except Exception:
        return []

    return out


def _build_mission_directives(
    custom_mission: str | None = None,
    cols: int = 48,
) -> list[Directive]:
    """Builds fallback Room Mystery Radar secret icebreaker mission."""
    out: list[Directive] = []
    out.append(Text("MYSTERY RADAR // SECRET MISSION", Align.CENTER, bold=True))
    out.append(Rule("-"))

    mission = custom_mission or random.choice(SECRET_MISSIONS)
    for part in wrap(mission, cols):
        out.append(Text(part, Align.CENTER))

    out.append(Rule("="))
    return out


def _build_mystery_radar_directives(
    registration: EventRegistration | None = None,
    event: MeetupEvent | None = None,
    cols: int = 48,
) -> list[Directive]:
    """Builds interactive Mystery Radar clues from actual event attendees.

    Displays anonymous badges like 'Pos (#6)' with empty checkboxes [   ]
    so candidates can guess and write the person's name during dates.
    """
    clues: list[tuple[str, str]] = []

    if registration and getattr(registration, "pk", None):
        try:
            from crush_lu.models import EventRegistration

            event_id = getattr(event, "id", None) or getattr(
                registration, "event_id", None
            )
            if event_id:
                other_regs = list(
                    EventRegistration.objects.filter(
                        event_id=event_id,
                        status__in=["confirmed", "attended"],
                    )
                    .exclude(id=registration.id)
                    .select_related("user", "user__crushprofile")
                    .prefetch_related(
                        "user__crushprofile__interests_new",
                        "user__crushprofile__qualities",
                        "user__crushprofile__defects",
                    )
                )

                my_user = getattr(registration, "user", None)
                my_profile = (
                    getattr(my_user, "crushprofile", None) if my_user else None
                )
                my_interests = set()
                my_gender = ""
                my_pref_genders = []
                if my_profile:
                    my_interests = set(
                        i.label for i in my_profile.interests_new.all()
                    )
                    my_gender = getattr(my_profile, "gender", "") or ""
                    my_pref_genders = getattr(my_profile, "preferred_genders", []) or []

                # Target dating genders: Men get clues about Women, Women about Men
                target_genders = set()
                if my_pref_genders:
                    target_genders = set(my_pref_genders)
                elif my_gender == "M":
                    target_genders = {"F"}
                elif my_gender == "F":
                    target_genders = {"M"}

                dating_pool = []
                general_pool = []
                for other in other_regs:
                    op = getattr(
                        getattr(other, "user", None), "crushprofile", None
                    )
                    other_gender = getattr(op, "gender", "") if op else ""
                    if target_genders and other_gender in target_genders:
                        dating_pool.append(other)
                    else:
                        general_pool.append(other)

                # Prioritize dating candidates; fallback to general if pool is small
                candidate_pool = (
                    dating_pool
                    if len(dating_pool) >= 2
                    else (dating_pool + general_pool)
                )

                for other in candidate_pool:
                    op = getattr(
                        getattr(other, "user", None), "crushprofile", None
                    )
                    if not op:
                        continue
                    # Badge format e.g. "Pos (#6)" or "Alex (#9)"
                    badge_name = (
                        getattr(op, "display_name", "")
                        or getattr(other.user, "first_name", "")
                        or ""
                    )
                    badge = f"{badge_name} (#{other.id})".strip()

                    other_interests = [i.label for i in op.interests_new.all()]
                    common = my_interests.intersection(other_interests)

                    if common:
                        clues.append(
                            (
                                f'Partage ta passion "{list(common)[0]}"',
                                badge,
                            )
                        )
                    elif op.event_vibe:
                        clues.append(
                            (f'Vibe: "{op.get_event_vibe_display()}"', badge)
                        )
                    elif other_interests:
                        clues.append(
                            (f'Adore "{other_interests[0]}"', badge)
                        )
                    elif op.defects.exists():
                        clues.append(
                            (
                                f'Macke: "{op.defects.first().label}"',
                                badge,
                            )
                        )
                    elif op.qualities.exists():
                        clues.append(
                            (
                                f'Atout: "{op.qualities.first().label}"',
                                badge,
                            )
                        )

                # Keep up to 4 unique clues
                random.shuffle(clues)
                clues = clues[:4]
        except Exception:
            clues = []

    if not clues:
        return _build_mission_directives(cols=cols)

    out: list[Directive] = []
    out.append(Text("MYSTERY RADAR // INDICES & MISSIONS", Align.CENTER, bold=True))
    out.append(Rule("-"))
    out.append(
        Text("Devine qui correspond a chaque indice et", Align.CENTER)
    )
    out.append(
        Text("ecris son prenom dans la case [   ] :", Align.CENTER)
    )
    out.append(Feed(1))

    for clue_text, badge in clues:
        prefix = "[   ] "
        full_clue = f"{prefix}{clue_text}"
        # If line fits with right-justified badge, justify directly
        if len(full_clue) + len(badge) + 2 <= cols:
            out.append(Text(justify(full_clue, badge, cols)))
        else:
            for part in wrap(full_clue, cols):
                out.append(Text(part))
            out.append(Text(justify("", badge, cols)))

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
    event: MeetupEvent | None = None,
    table_number: int | None = None,
    seat_label: str = "",
    qr_url: str = "",
    paper: Paper = Paper.MM80,
    coach_authenticated: bool = False,
) -> list[Directive]:
    """Builds the full sequence of directives for a check-in ticket."""
    cols = paper.columns

    event_title = "Speed Dating Event"
    now_local = timezone.localtime(timezone.now()) if timezone.is_aware(timezone.now()) else timezone.now()
    date_str = now_local.strftime("%a %d %b %H:%M")
    attendee_name = "Guest"
    candidate_num = ""

    if registration:
        reg_user = getattr(registration, "user", None)
        if reg_user:
            profile = getattr(reg_user, "crushprofile", None)
            if profile and getattr(profile, "display_name", None):
                attendee_name = profile.display_name
            elif reg_user.first_name:
                attendee_name = reg_user.first_name
            elif coach_authenticated and getattr(reg_user, "username", None):
                attendee_name = reg_user.username.split("@")[0]
            else:
                attendee_name = "Attendee"
        candidate_num = f"(#{registration.id})"

    if event:
        event_title = getattr(event, "title", event_title)
        event_dt = getattr(event, "date_time", None)
        if event_dt:
            local_dt = (
                timezone.localtime(event_dt)
                if timezone.is_aware(event_dt)
                else event_dt
            )
            date_str = local_dt.strftime("%a %d %b %H:%M")

    table_display = "WELCOME"
    if table_number:
        table_display = f"TABLE {table_number}"
        if seat_label:
            table_display += f" ({seat_label})"

    if not qr_url:
        base_domain = getattr(
            settings, "CRUSH_LU_CANONICAL_DOMAIN", "https://crush.lu"
        ).rstrip("/")
        event_id = getattr(event, "id", None) or (
            getattr(registration, "event_id", None) if registration else None
        )
        if event_id:
            qr_url = f"{base_domain}/events/{event_id}/lobby/"
        else:
            qr_url = f"{base_domain}/my-crush/"

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
    directives.extend(
        _build_room_stats_directives(
            registration=registration, event=event, cols=cols
        )
    )
    directives.extend(_build_coach_rules_directives(cols=cols))
    directives.extend(
        _build_mystery_radar_directives(
            registration=registration, event=event, cols=cols
        )
    )
    directives.extend(_build_qr_footer_directives(qr_url=qr_url, cols=cols))

    return directives


def build_checkin_ticket_bytes(
    registration: EventRegistration | None = None,
    event: MeetupEvent | None = None,
    table_number: int | None = None,
    seat_label: str = "",
    qr_url: str = "",
    paper: Paper = Paper.MM80,
    coach_authenticated: bool = False,
) -> bytes:
    """Builds the raw ESC/POS byte sequence for a ticket."""
    directives = build_checkin_ticket_directives(
        registration=registration,
        event=event,
        table_number=table_number,
        seat_label=seat_label,
        qr_url=qr_url,
        paper=paper,
        coach_authenticated=coach_authenticated,
    )
    return encode_ticket(directives, paper=paper)


def build_checkin_ticket_base64(
    registration: EventRegistration | None = None,
    event: MeetupEvent | None = None,
    table_number: int | None = None,
    seat_label: str = "",
    qr_url: str = "",
    paper: Paper = Paper.MM80,
    coach_authenticated: bool = False,
) -> str:
    """Builds the base64-encoded ESC/POS byte payload for RawBT transmission."""
    raw_bytes = build_checkin_ticket_bytes(
        registration=registration,
        event=event,
        table_number=table_number,
        seat_label=seat_label,
        qr_url=qr_url,
        paper=paper,
        coach_authenticated=coach_authenticated,
    )
    return base64.b64encode(raw_bytes).decode("ascii")


def preview_checkin_ticket_text(
    registration: EventRegistration | None = None,
    event: MeetupEvent | None = None,
    table_number: int | None = None,
    seat_label: str = "",
    qr_url: str = "",
    paper: Paper = Paper.MM80,
    coach_authenticated: bool = False,
) -> str:
    """Generates a plain-text monospace preview of the ticket."""
    directives = build_checkin_ticket_directives(
        registration=registration,
        event=event,
        table_number=table_number,
        seat_label=seat_label,
        qr_url=qr_url,
        paper=paper,
        coach_authenticated=coach_authenticated,
    )
    return render_plain_text(directives, paper=paper)
