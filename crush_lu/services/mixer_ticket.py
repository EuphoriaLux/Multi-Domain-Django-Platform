"""
Crush.lu Social Mixer check-in ticket ("Find your number").

Replaces the speed-dating ticket for ``event_type == "mixer"``. Each guest gets:

1. a **badge strip** — only their event number, printed huge, then a cut, so
   they can wear it. Deliberately no name: the game is that nobody knows who
   is behind a number;
2. their **affinity list** — the numbers of other confirmed guests, each with
   an affinity percentage, best first, and a blank to write the first name
   once they have found the person;
3. three small challenges, a community line and a QR to My Crush.

Numbering
---------
Numbers run 1..N over the event's confirmed/attended/no-show registrations in
registration order (pk). They are computed at print time, not stored, so they
are only stable while the confirmed list is: print once online cancellation
has closed (it closes at ``event.date_time``, see ``_event_cancel_refusal``).
``no_show`` stays in the numbering so a coach marking an absentee mid-event
does not shift everybody else's number; it is excluded from the lists.

Someone who reaches ``attended`` at the door from another status (a waitlist
promotion, a seat-holding ``pending`` row) was never in that list, so slotting
them in by pk would renumber everyone after them. ``checkin_prior_status``
records where a check-in came from. Every waitlist/pending row therefore
holds a reserved number after the last regular one, in pk order, whether or
not it is ever admitted: admitting it, or undoing that admission
(``coach_undo_checkin`` restores the prior status), keeps the same number, so
no badge already printed ever changes. Unused reserved numbers are gaps.

Affinity
--------
Built only from Event Identity fields every member fills in for events
(``interests_new``, ``event_languages``, ``date_of_birth``, ``event_vibe``,
``ask_me_about``) — never Connect traits, zodiac or gender/age preferences:
this is a social evening, not a matching product. The pair score is symmetric,
so A sees B at the same percentage B sees A. Raw scores are mapped to a
35-98 % display by their percentile among all pairs of the room, which spreads
the list without inventing precision.
"""

from __future__ import annotations

import io
import logging
import re
from typing import TYPE_CHECKING

from django.conf import settings

from power_up.atmos.printing.layout import (
    Align,
    Cut,
    Directive,
    Feed,
    Image,
    Paper,
    QrCode,
    Rule,
    Text,
    justify,
    wrap,
)
from power_up.atmos.text import ENCODING, TRANSLIT

if TYPE_CHECKING:
    from crush_lu.models import EventRegistration, MeetupEvent

logger = logging.getLogger(__name__)

# Statuses that hold a number. ``no_show`` keeps its number (see module doc).
NUMBERED_STATUSES = ("confirmed", "attended", "no_show")
# Statuses that can still be admitted at the door: they get a reserved number.
RESERVED_STATUSES = ("waitlist", "pending")
# Statuses that appear in other guests' lists.
LISTED_STATUSES = ("confirmed", "attended")

LIST_SIZE = 20
DISPLAY_MIN = 35
DISPLAY_MAX = 98

WEIGHT_INTERESTS = 0.50
WEIGHT_LANGUAGES = 0.20
WEIGHT_AGE = 0.15
WEIGHT_VIBE = 0.10
WEIGHT_ASK_ME = 0.05

# Symmetric vibe compatibility; pairs not listed score 0.5.
_VIBE_PAIRS = {
    frozenset({"dance_floor", "quiet_corner"}): 0.3,
    frozenset({"dance_floor", "at_the_bar"}): 0.7,
    frozenset({"dragged_along", "at_the_bar"}): 0.7,
    frozenset({"dance_floor", "dragged_along"}): 0.6,
    frozenset({"quiet_corner", "at_the_bar"}): 0.6,
    frozenset({"quiet_corner", "dragged_along"}): 0.6,
}

_ANNIVERSARY_RE = re.compile(r"\b1\s*(an|year|jahr|joer)\b", re.IGNORECASE)

# Plain-text preview of the big number image (render_plain_text shows every
# Image as the logo placeholder, so the number is repeated in text below it).
SAMPLE_NUMBER = 17
SAMPLE_ROWS = [
    (41, 94),
    (8, 91),
    (52, 89),
    (23, 84),
    (35, 81),
    (14, 78),
    (49, 76),
    (6, 73),
    (30, 71),
    (57, 69),
    (19, 66),
    (2, 64),
    (44, 62),
    (27, 59),
    (60, 57),
    (11, 55),
    (38, 52),
    (3, 49),
    (25, 46),
    (46, 43),
]


def _t(lang: str, fr: str, de: str, en: str) -> str:
    return {"fr": fr, "de": de}.get(lang, en)


def printable(text: str) -> str:
    """Drop what CP858 cannot print (emoji in event titles) instead of '?'."""
    text = (text or "").translate(TRANSLIT)
    kept = []
    for ch in text:
        try:
            ch.encode(ENCODING)
            kept.append(ch)
        except UnicodeEncodeError:
            continue
    return " ".join("".join(kept).split())


# ---------------------------------------------------------------------------
# Numbering and affinity
# ---------------------------------------------------------------------------


def _is_door_arrival(registration) -> bool:
    """Checked in from a status that held no number (waitlist, pending)."""
    return registration.status == "attended" and (
        registration.checkin_prior_status or ""
    ) not in ("", "confirmed")


def event_numbers(event: MeetupEvent) -> dict[int, int]:
    """Registration pk -> event number: regular guests 1..N, then reserved ones."""
    from crush_lu.models import EventRegistration

    rows = EventRegistration.objects.filter(
        event=event, status__in=NUMBERED_STATUSES + RESERVED_STATUSES
    )
    regular, reserved = [], []
    for reg in rows.only("pk", "status", "checkin_prior_status"):
        if reg.status in RESERVED_STATUSES or _is_door_arrival(reg):
            reserved.append(reg.pk)
        else:
            regular.append(reg.pk)
    ordered = sorted(regular) + sorted(reserved)
    return {pk: i for i, pk in enumerate(ordered, 1)}


class _Guest:
    __slots__ = ("pk", "status", "interests", "ask", "langs", "dob", "vibe")

    def __init__(self, registration: EventRegistration):
        user = getattr(registration, "user", None)
        profile = getattr(user, "crushprofile", None) if user else None
        self.pk = registration.pk
        self.status = registration.status
        if profile is None:
            self.interests, self.ask, self.langs = set(), set(), set()
            self.dob, self.vibe = None, ""
            return
        self.interests = {i.pk for i in profile.interests_new.all()}
        self.ask = {str(x) for x in (profile.ask_me_about or [])}
        self.langs = set(profile.event_languages or [])
        self.dob = profile.date_of_birth
        self.vibe = profile.event_vibe or ""


def _interest_score(a: _Guest, b: _Guest) -> float | None:
    if not a.interests or not b.interests:
        return None
    return len(a.interests & b.interests) / min(len(a.interests), len(b.interests))


def _raw_affinity(a: _Guest, b: _Guest, neutral_interest: float) -> float:
    """Symmetric 0..1 score; a missing signal is skipped, not penalised.

    Interests are the exception: a guest with none gets the room's median
    interest score, otherwise their (always-shared) languages and age alone
    would push them to the top of everybody's list.
    """
    parts: list[tuple[float, float]] = []

    interest = _interest_score(a, b)
    parts.append((WEIGHT_INTERESTS, neutral_interest if interest is None else interest))

    if a.langs and b.langs:
        shared = a.langs & b.langs
        parts.append((WEIGHT_LANGUAGES, len(shared) / min(len(a.langs), len(b.langs))))

    if a.dob and b.dob:
        years = abs((a.dob - b.dob).days) / 365.25
        parts.append((WEIGHT_AGE, max(0.0, 1.0 - years / 25.0)))

    if a.vibe and b.vibe:
        if a.vibe == b.vibe:
            vibe = 1.0
        elif "meet_everyone" in (a.vibe, b.vibe):
            vibe = 0.8
        else:
            vibe = _VIBE_PAIRS.get(frozenset({a.vibe, b.vibe}), 0.5)
        parts.append((WEIGHT_VIBE, vibe))

    if a.ask and b.ask:
        parts.append((WEIGHT_ASK_ME, 1.0 if a.ask & b.ask else 0.0))

    total = sum(w for w, _ in parts)
    return sum(w * s for w, s in parts) / total if total else 0.5


def affinity_list(
    registration: EventRegistration, event: MeetupEvent
) -> tuple[int | None, list[tuple[int, int]], int]:
    """Return (own number, [(number, percent)] best first, other guests count)."""
    from crush_lu.models import EventRegistration

    numbers = event_numbers(event)
    regs = list(
        EventRegistration.objects.filter(event=event, status__in=NUMBERED_STATUSES)
        .select_related("user__crushprofile")
        .prefetch_related("user__crushprofile__interests_new")
        .order_by("pk")
    )
    guests = [_Guest(r) for r in regs]
    me = next((g for g in guests if g.pk == registration.pk), None)
    if me is None:
        me = _Guest(registration)
    others = [
        g
        for g in guests
        if g.pk != registration.pk and g.pk in numbers and g.status in LISTED_STATUSES
    ]

    # The percentile scale is fixed on the regular guests (no_show included,
    # door arrivals excluded), so marking an absentee or admitting someone at
    # the door mid-print never changes a percentage already on paper, and both
    # tickets of a pair always show the same figure.
    cohort = [g for g, r in zip(guests, regs) if not _is_door_arrival(r)]

    interest_scores = sorted(
        s
        for i, a in enumerate(cohort)
        for b in cohort[i + 1 :]
        if (s := _interest_score(a, b)) is not None
    )
    neutral = interest_scores[len(interest_scores) // 2] if interest_scores else 0.25

    room = sorted(
        _raw_affinity(a, b, neutral)
        for i, a in enumerate(cohort)
        for b in cohort[i + 1 :]
    )

    def display(raw: float) -> int:
        if not room:
            return DISPLAY_MIN
        below = sum(1 for r in room if r < raw - 1e-9)
        equal = sum(1 for r in room if abs(r - raw) <= 1e-9)
        frac = (below + equal / 2) / len(room)
        return DISPLAY_MIN + round((DISPLAY_MAX - DISPLAY_MIN) * frac)

    scored = []
    for other in others:
        raw = _raw_affinity(me, other, neutral)
        scored.append((-raw, numbers[other.pk], display(raw)))
    scored.sort()
    rows = [(number, pct) for _, number, pct in scored]
    return numbers.get(registration.pk), rows, len(others)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def _big_number_png(number: int | None) -> bytes | None:
    """Render the number as a 1-bit-friendly PNG, far larger than GS ! allows."""
    try:
        from PIL import Image as PILImage, ImageDraw, ImageFont

        label = f"{number}" if number is not None else "?"
        font = ImageFont.load_default(size=230)
        probe = ImageDraw.Draw(PILImage.new("L", (1, 1)))
        left, top, right, bottom = probe.textbbox(
            (0, 0), label, font=font, stroke_width=6
        )
        width = 432
        height = (bottom - top) + 24
        img = PILImage.new("L", (width, height), 255)
        draw = ImageDraw.Draw(img)
        x = (width - (right - left)) // 2 - left
        draw.text(
            (x, 12 - top), label, font=font, fill=0, stroke_width=6, stroke_fill=0
        )
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        logger.warning("Could not render mixer number image", exc_info=True)
        return None


def _bar(pct: int) -> str:
    filled = max(1, min(10, round(pct / 10)))
    return "█" * filled + "░" * (10 - filled)


def _attendee_name(registration, coach_authenticated: bool, lang: str) -> str:
    user = getattr(registration, "user", None) if registration else None
    profile = getattr(user, "crushprofile", None) if user else None
    if profile and getattr(profile, "display_name", None):
        return profile.display_name
    # Never `username`: signup is email-only, so it is usually the address.
    if user and coach_authenticated and user.first_name:
        return user.first_name
    return _t(lang, "Invité(e)", "Gast", "Guest")


def _member_count() -> int:
    try:
        from crush_lu.models import CrushProfile

        return CrushProfile.objects.filter(is_active=True).count()
    except Exception:
        return 0


def _fmt_int(n: int, lang: str) -> str:
    s = f"{n:,}"
    return s.replace(",", "." if lang == "de" else (" " if lang == "fr" else ","))


def _is_anniversary(event) -> bool:
    titles = [
        getattr(event, f, "") or ""
        for f in ("title", "title_en", "title_fr", "title_de")
    ]
    return any(_ANNIVERSARY_RE.search(t) for t in titles)


def _qr_url(event, lang: str) -> str:
    base = getattr(settings, "CRUSH_LU_CANONICAL_DOMAIN", "https://crush.lu").rstrip(
        "/"
    )
    try:
        from django.urls import reverse

        # Explicit urlconf: the default one builds /crush/... paths (AGENTS.md).
        path = reverse(
            "crush_lu:event_attendees",
            kwargs={"event_id": event.id},
            urlconf="azureproject.urls_crush",
        )
        return f"{base}{path}"
    except Exception:
        event_id = getattr(event, "id", None)
        if event_id:
            return f"{base}/{lang}/events/{event_id}/attendees/"
        return f"{base}/{lang}/events/"


def build_mixer_ticket_directives(
    registration: EventRegistration | None,
    event: MeetupEvent | None,
    paper: Paper = Paper.MM80,
    coach_authenticated: bool = False,
    lang: str = "fr",
    date_str: str = "",
    logo_path: str | None = None,
) -> list[Directive]:
    """Full directive list for one guest's mixer ticket (caller sets the language)."""
    cols = paper.columns

    if registration is not None and getattr(registration, "pk", None) and event:
        number, rows, others_count = affinity_list(registration, event)
    else:
        # Coach test print: realistic sample, no member data.
        number, rows, others_count = SAMPLE_NUMBER, SAMPLE_ROWS, 59

    name = (
        _attendee_name(registration, coach_authenticated, lang)
        if registration is not None
        else "Alex"
    )
    num_label = f"#{number}" if number is not None else "#?"
    out: list[Directive] = []

    # 1. Badge strip — number only, no name.
    out.append(Text("CRUSH.LU", Align.CENTER, bold=True))
    out.append(
        Text(
            _t(lang, "TROUVE-MOI !", "FINDE MICH!", "FIND ME!"), Align.CENTER, bold=True
        )
    )
    png = _big_number_png(number)
    if png:
        out.append(Image(source=png, width=432, align=Align.CENTER))
    else:
        out.append(
            Text(
                num_label,
                Align.CENTER,
                bold=True,
                double_width=True,
                double_height=True,
            )
        )
    out.append(Feed(2))
    out.append(Cut(partial=True))

    # 2. Personal ticket.
    if logo_path:
        out.append(Image(source=logo_path, width=192, align=Align.CENTER))
        out.append(Feed(1))
    out.append(Text("CRUSH.LU", Align.CENTER, bold=True, double_height=True))
    out.append(
        Text(
            _t(
                lang,
                "SOCIAL MIXER // TROUVE TON NUMÉRO",
                "SOCIAL MIXER // FINDE DEINE NUMMER",
                "SOCIAL MIXER // FIND YOUR NUMBER",
            ),
            Align.CENTER,
            bold=True,
        )
    )
    out.append(Rule("="))
    title = printable(getattr(event, "title", "") if event else "") or "Social Mixer"
    for part in wrap(title, cols):
        out.append(Text(part, Align.CENTER))
    location = printable(getattr(event, "location", "") if event else "")
    when = " - ".join(p for p in (date_str, location) if p)
    if when:
        for part in wrap(when, cols):
            out.append(Text(part, Align.CENTER))
    out.append(Rule("-"))
    out.append(
        Text(
            justify(
                (printable(name) or _t(lang, "Invité(e)", "Gast", "Guest")).upper()[
                    : cols - 14
                ],
                _t(
                    lang,
                    f"N° {number or '?'}",
                    f"Nr. {number or '?'}",
                    f"No. {number or '?'}",
                ),
                cols,
            ),
            bold=True,
        )
    )
    out.append(Rule("="))

    out.append(
        Text(
            _t(lang, "TES AFFINITÉS", "DEINE AFFINITÄTEN", "YOUR AFFINITIES"),
            Align.CENTER,
            bold=True,
            double_height=True,
        )
    )
    for line in (
        _t(
            lang,
            "Pas de noms. Pas de photos. Que des chiffres.",
            "Keine Namen. Keine Fotos. Nur Zahlen.",
            "No names. No photos. Just numbers.",
        ),
        _t(
            lang,
            "À toi de trouver qui se cache derrière !",
            "Finde heraus, wer dahintersteckt!",
            "Find out who is behind each one!",
        ),
    ):
        out.append(Text(line, Align.CENTER))
    out.append(Rule("-"))

    if rows:
        out.append(
            Text(
                _t(
                    lang,
                    "  N°    AFFINITÉ            PRÉNOM",
                    "  NR.   AFFINITÄT           VORNAME",
                    "  NO.   AFFINITY            FIRST NAME",
                ),
                bold=True,
            )
        )
        out.append(Rule("-"))
        for n, pct in rows[:LIST_SIZE]:
            out.append(Text(f"  #{n:<3}  {_bar(pct)}  {pct:>3}%    ____________"))
        out.append(Rule("-"))
        rest = others_count - min(len(rows), LIST_SIZE)
        if rest > 0:
            out.append(
                Text(
                    _t(
                        lang,
                        f"+ {rest} autres numéros à découvrir",
                        f"+ {rest} weitere Nummern im Raum",
                        f"+ {rest} more numbers in the room",
                    ),
                    Align.CENTER,
                )
            )
        out.append(
            Text(
                _t(
                    lang,
                    "Introuvable ? Peut-être absent(e) ce soir.",
                    "Unauffindbar? Vielleicht heute nicht da.",
                    "Can't find someone? They may not be here.",
                ),
                Align.CENTER,
            )
        )
    else:
        out.append(
            Text(
                _t(
                    lang,
                    "Ta liste arrive avec les invités !",
                    "Deine Liste folgt mit den Gästen!",
                    "Your list fills up as guests arrive!",
                ),
                Align.CENTER,
            )
        )
    out.append(Rule("="))

    # 3. Challenges.
    top = f"#{rows[0][0]}" if rows else ""
    out.append(
        Text(
            _t(
                lang,
                "3 DÉFIS DU SOIR",
                "3 CHALLENGES DES ABENDS",
                "TONIGHT'S 3 CHALLENGES",
            ),
            Align.CENTER,
            bold=True,
        )
    )
    out.append(Feed(1))
    challenges = [
        _t(
            lang,
            (
                f"Trouve ton n°1 ({top}) et demande-lui son talent le plus inutile"
                if top
                else "Demande à quelqu'un son talent le plus inutile"
            ),
            (
                f"Finde deine Nr. 1 ({top}) und frag nach ihrem nutzlosesten Talent"
                if top
                else "Frag jemanden nach seinem nutzlosesten Talent"
            ),
            (
                f"Find your no. 1 ({top}) and ask for their most useless talent"
                if top
                else "Ask someone for their most useless talent"
            ),
        ),
        _t(
            lang,
            "Fais deviner ton numéro sans le dire",
            "Lass deine Nummer erraten, ohne sie zu sagen",
            "Get someone to guess your number without saying it",
        ),
        _t(
            lang,
            "Complète 10 prénoms avant minuit",
            "Fülle 10 Vornamen vor Mitternacht aus",
            "Fill in 10 first names before midnight",
        ),
    ]
    for challenge in challenges:
        for i, part in enumerate(wrap(challenge, cols - 6)):
            out.append(Text(("  [ ] " if i == 0 else "      ") + part))
        out.append(Feed(1))
    out.append(Rule("="))

    # 4. Community / anniversary.
    members = _fmt_int(_member_count(), lang)
    if event is not None and _is_anniversary(event):
        for line in (
            _t(
                lang,
                "IL Y A 1 AN, CRUSH.LU",
                "VOR 1 JAHR WAR CRUSH.LU",
                "ONE YEAR AGO, CRUSH.LU",
            ),
            _t(
                lang,
                "N'ÉTAIT QU'UN NOM DE DOMAINE.",
                "NUR EIN DOMAINNAME.",
                "WAS JUST A DOMAIN NAME.",
            ),
            _t(
                lang,
                f"AUJOURD'HUI : {members} MEMBRES.",
                f"HEUTE: {members} MITGLIEDER.",
                f"TODAY: {members} MEMBERS.",
            ),
            _t(
                lang,
                "MERCI D'ÊTRE LÀ !",
                "DANKE, DASS DU DA BIST!",
                "THANK YOU FOR BEING HERE!",
            ),
        ):
            out.append(Text(line, Align.CENTER, bold=True))
    else:
        out.append(
            Text(
                _t(
                    lang,
                    f"{members} MEMBRES. MERCI D'ÊTRE LÀ !",
                    f"{members} MITGLIEDER. DANKE!",
                    f"{members} MEMBERS. THANK YOU!",
                ),
                Align.CENTER,
                bold=True,
            )
        )
    out.append(Rule("-"))

    out.append(
        Text(
            _t(
                lang,
                "APRÈS LA SOIRÉE, SCANNE ICI :",
                "NACH DEM ABEND HIER SCANNEN:",
                "AFTER THE EVENING, SCAN HERE:",
            ),
            Align.CENTER,
            bold=True,
        )
    )
    out.append(
        Text(
            _t(
                lang,
                "dis-nous qui t'a tapé dans l'œil",
                "sag uns, wer dir gefallen hat",
                "tell us who caught your eye",
            ),
            Align.CENTER,
        )
    )
    out.append(Feed(1))
    qr = _qr_url(event, lang)
    out.append(QrCode(qr, size=6))
    out.append(Feed(1))
    out.append(Text(qr, Align.CENTER))
    out.append(Rule("="))

    if event is not None and _is_anniversary(event):
        for line in ("     )", "    ( )", "    |_|", "  (_____)", " (_______)"):
            out.append(Text(line.ljust(10), Align.CENTER))
        out.append(
            Text(
                _t(
                    lang,
                    "SOUFFLE LA PREMIÈRE BOUGIE !",
                    "PUSTE DIE ERSTE KERZE AUS!",
                    "BLOW OUT THE FIRST CANDLE!",
                ),
                Align.CENTER,
                bold=True,
            )
        )
    out.append(
        Text(
            _t(
                lang,
                "PARTAGE TA STORY @CRUSH.LU",
                "TEILE DEINE STORY @CRUSH.LU",
                "SHARE YOUR STORY @CRUSH.LU",
            ),
            Align.CENTER,
            bold=True,
        )
    )
    out.append(Text("#CrushLu", Align.CENTER))
    out.append(Rule("="))
    out.append(Feed(3))
    out.append(Cut(partial=True))
    return out


__all__ = [
    "LIST_SIZE",
    "affinity_list",
    "build_mixer_ticket_directives",
    "event_numbers",
]
