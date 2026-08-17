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
import logging
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
    Image,
    Paper,
    QrCode,
    Rule,
    Text,
    justify,
    wrap,
)

if TYPE_CHECKING:
    from crush_lu.models import MeetupEvent, EventRegistration

logger = logging.getLogger(__name__)


def _format_ticket_date(dt: Any, lang: str = "en") -> str:
    """Formats an aware/naive datetime in a locale-aware way for the thermal ticket."""
    if dt is None:
        return ""
    if hasattr(dt, "tzinfo") and dt.tzinfo is not None and timezone.is_aware(dt):
        local_dt = timezone.localtime(dt)
    else:
        local_dt = dt

    weekdays = {
        "fr": ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."],
        "de": ["Mo.", "Di.", "Mi.", "Do.", "Fr.", "Sa.", "So."],
        "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    }
    months = {
        "fr": [
            "janv.",
            "févr.",
            "mars",
            "avr.",
            "mai",
            "juin",
            "juil.",
            "août",
            "sept.",
            "oct.",
            "nov.",
            "déc.",
        ],
        "de": [
            "Jan.",
            "Feb.",
            "März",
            "Apr.",
            "Mai",
            "Juni",
            "Juli",
            "Aug.",
            "Sept.",
            "Okt.",
            "Nov.",
            "Dez.",
        ],
        "en": [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ],
    }
    w_list = weekdays.get(lang, weekdays["en"])
    m_list = months.get(lang, months["en"])
    w_str = w_list[local_dt.weekday()]
    m_str = m_list[local_dt.month - 1]
    time_str = local_dt.strftime("%H:%M")
    if lang == "de":
        return f"{w_str} {local_dt.day:02d}. {m_str} {time_str}"
    return f"{w_str} {local_dt.day:02d} {m_str} {time_str}"


COACH_SURVIVAL_RULES: dict[str, list[str]] = {
    "fr": [
        "3 minutes passent plus vite que le tram sur le Kirchberg.",
        "Le sujet 'Ex-partenaire' au 1er round coûte -50 points d'aura.",
        "Contact visuel : Sourire chaleureux, sans fixer comme dans un thriller.",
        "Question de secours en cas de blanc : 'Quel est ton talent le plus inutile ?'",
        "Parle de voyages & passions, pas de ta feuille d'impôts.",
        "Le sourire fait la moitié du travail. L'écoute active fait le reste.",
        "Personne ne te jugera sur tes goûts musicaux (sauf l'Eurovision 2004).",
        "Complimenter un sourire ou des baskets marche 10x mieux qu'une voiture.",
        "Débat Schueberfouer : Gromperekichelcher avec ou sans compote de pommes ?",
        "Si tu es venu en Vel'oh sous la pluie, c'est +20 points de résilience.",
        "L'authenticité bat la perfection à tous les coups.",
        "Prends une grande inspiration : ton date est aussi nerveux(se) que toi !",
        "Demande : 'Quel est ton film plaisir coupable inavouable ?'",
        "Question magique : 'Si tu pouvais te téléporter ce week-end, où irais-tu ?'",
        "En cas de panique : demande son avis sur la pizza à l'ananas.",
        "Demande : 'Quel est le dernier truc qui t'a fait rire aux larmes ?'",
        "Green Flag ultime : Quelqu'un qui rigole de ses propres maladresses.",
        "Red Flag immédiat : Poser des questions sur la tranche d'imposition.",
        "Parler avec les mains est autorisé et fortement encouragé.",
        "Si vous avez un fou rire à la minute 2, c'est presque un mariage garanti.",
        "Ne vends pas ton CV : raconte une anecdote amusante de ton enfance.",
        "La confiance, c'est d'assumer d'aimer les dessins animés ou le karaoké.",
        "Laisse 5 secondes de silence sans paniquer : c'est là que le charme opère.",
        "Le verre sur la table est ton meilleur allié pour rythmer le dialogue.",
        "Sois toi-même à 100 % : la bonne personne adorera exactement ce que tu es.",
        "Quand la cloche sonne : un clin d'œil chaleureux avant de changer de table.",
        "Un date moyen n'est pas perdu : c'est un entraînement pour le suivant !",
        "Le meilleur opener : 'Quel est le voyage qui t'a le plus marqué ?'",
        "À la fin des 7 rounds : tout le monde se retrouve au bar pour trinquer !",
        "Détends-toi, ce n'est pas un entretien d'embauche : amuse-toi !",
    ],
    "de": [
        "3 Minuten vergehen schneller als die Tram auf dem Kirchberg.",
        "Das Thema 'Ex-Partner' in Runde 1 kostet -50 Aura-Punkte.",
        "Augenkontakt halten: Charmant lächeln, nicht wie ein Serienmörder anstarren.",
        "Notfall-Frage bei Stille: 'Was ist dein absolut nutzlosestes Talent?'",
        "Frag nach Leidenschaften & Reisezielen, nicht nach der Steuerklasse.",
        "Lächeln ist die halbe Miete. Die andere Hälfte ist aktives Zuhören.",
        "Niemand verurteilt dich für deinen Musikgeschmack (außer es ist Kirmes-Techno).",
        "Komplimente über Schuhe oder Lächeln funktionieren 10x besser als über Autos.",
        "Schueberfouer-Debatte: Gromperekichelcher mit oder ohne Apfelmus?",
        "Wer mit dem Vel'oh durch den Regen kam, kriegt +20 Resilienz-Punkte.",
        "Authentizität schlägt Perfektionismus in jeder einzelnen Runde.",
        "Einmal tief durchatmen: Dein Gegenüber ist genauso nervös wie du!",
        "Frag nach dem peinlichsten Guilty-Pleasure-Film aller Zeiten.",
        "Zauberfrage: 'Wenn du dich jetzt beamen könntest, wo wärst du am Wochenende?'",
        "Im Notfall: Frag nach der ultimativen Meinung zu Pizza mit Ananas.",
        "Frag: 'Worüber hast du zuletzt so richtig Tränen gelacht?'",
        "Ultimative Green Flag: Jemand, der über eigene Tolpatschigkeit lachen kann.",
        "Instant Red Flag: Fragen nach der Steuerklasse oder dem Firmenwagen.",
        "Mit Händen und Füßen reden ist ausdrücklich erlaubt und sympathisch.",
        "Gemeinsamer Lachanfall in Minute 2 ist quasi die halbe Verlobung.",
        "Verkauf nicht deinen Lebenslauf: Erzähl eine witzige Kindheits-Anekdote.",
        "Wahres Selbstvertrauen ist, offen zu seinen Nerd-Hobbys zu stehen.",
        "Keine Angst vor 5 Sekunden Pause: Das ist der Moment, wo Chemie entsteht.",
        "Das Glas an der Bar ist dein bester Verbündeter für die perfekte Redepause.",
        "Sei zu 100% du selbst: Die richtige Person wird genau das an dir lieben.",
        "Wenn die Glocke läutet: Ein kurzes Lächeln, bevor es zum nächsten Tisch geht.",
        "Eine mittelmäßige Runde ist kein Verlust: Es ist Warm-up für den nächsten Tisch!",
        "Der beste Opener überhaupt: 'Welche Reise hat dich am meisten verändert?'",
        "Nach den 7 Runden: Alle treffen sich an der Bar für die After-Drinks!",
        "Entspann dich, es ist kein Vorstellungsgespräch: Hab Spaß!",
    ],
    "en": [
        "3 minutes fly faster than the tram on the Kirchberg.",
        "Talking about your 'Ex' in Round 1 costs -50 aura points.",
        "Eye contact: Warm smile, avoid staring like in a crime drama.",
        "Emergency question for awkward silence: 'What is your most useless talent?'",
        "Ask about travel & obsessions, not about tax brackets.",
        "A smile is half the charm. Active listening does the rest.",
        "Nobody judges your music taste (unless it's fairground techno).",
        "Complimenting shoes or a smile works 10x better than cars.",
        "Schueberfouer debate: Gromperekichelcher with or without apple sauce?",
        "Arriving by Vel'oh in the rain grants +20 resilience points.",
        "Authenticity beats perfectionism in every single round.",
        "Take a deep breath: Your date is just as nervous as you are!",
        "Ask: 'What is your most embarrassing guilty-pleasure movie?'",
        "Magic question: 'If you could teleport anywhere this weekend, where to?'",
        "In case of emergency: Ask for their honest stance on pineapple on pizza.",
        "Ask: 'What was the last thing that made you laugh to tears?'",
        "Ultimate Green Flag: Someone who laughs at their own clumsy moments.",
        "Instant Red Flag: Asking about tax brackets or corporate bonuses.",
        "Talking with your hands is fully allowed and highly charming.",
        "A shared laughing fit at minute 2 is practically half an engagement.",
        "Don't recite your CV: Share a funny childhood anecdote instead.",
        "True confidence is proudly admitting your nerd hobbies or karaoke addiction.",
        "Don't fear 5 seconds of silence: That's where chemistry actually happens.",
        "Your drink on the table is your best ally for pacing the conversation.",
        "Be 100% yourself: The right person will love exactly who you are.",
        "When the bell rings: A warm smile before moving to the next table.",
        "An average round is never lost: It's just a warm-up for the next table!",
        "The best universal opener: 'What travel trip changed you the most?'",
        "After the 7 rounds: Everyone gathers at the bar for after-event drinks!",
        "Relax, it's not a job interview: Have fun and enjoy the moment!",
    ],
}

SECRET_MISSIONS: dict[str, list[str]] = {
    "fr": [
        "Trouve qui regarde de la télé-réalité en cachette – sans le demander cash !",
        "Trouve la personne qui défend la pizza à l'ananas !",
        "Trouve qui a déjà sauté en parachute ou à l'élastique !",
        "Trouve la personne qui parle 4 langues ou plus !",
        "Trouve qui a la meilleure adresse de restaurant secret au Luxembourg !",
        "Trouve la personne qui a déjà enfermé ses clés dans sa voiture !",
    ],
    "de": [
        "Finde heraus, wer heimlich Trash-TV schaut – ohne direkt danach zu fragen!",
        "Finde die Person, die Pizza mit Ananas verteidigt!",
        "Finde heraus, wer schon mal Fallschirmspringen oder Bungee-Jumping gemacht hat!",
        "Finde die Person, die 4 oder mehr Sprachen spricht!",
        "Finde heraus, wer den besten Restaurant-Geheimtipp in Luxemburg hat!",
        "Finde die Person, die schon mal ihren Schlüssel im Auto eingesperrt hat!",
    ],
    "en": [
        "Find out who secretly watches trash TV – without asking directly!",
        "Find the person who defends pineapple on pizza!",
        "Find out who has done skydiving or bungee jumping before!",
        "Find the person who speaks 4 or more languages!",
        "Find out who knows the best hidden foodie spot in Luxembourg!",
        "Find the person who has locked their keys inside their car!",
    ],
}

PASSPORT_ITEMS: dict[str, list[tuple[str, str]]] = {
    "fr": [
        ("[OK] ACCÈS SPEED DATING", "ILLIMITÉ"),
        ("[OK] 1x DRINK DE BIENVENUE", "INCLUS"),
        ("[OK] ASSURANCE SMALLTALK", "ACTIVÉE"),
        ("[OK] POTENTIEL COUP D'CŒUR", "100% GARANTI"),
    ],
    "de": [
        ("[OK] SPEED DATING ZUGANG", "UNBEGRENZT"),
        ("[OK] 1x WELCOME DRINK", "INKLUSIVE"),
        ("[OK] SMALLTALK-SCHUTZ", "AKTIVIERT"),
        ("[OK] CRUSH-POTENZIAL", "100% GARANTIERT"),
    ],
    "en": [
        ("[OK] SPEED DATING ACCESS", "UNLIMITED"),
        ("[OK] 1x WELCOME DRINK", "INCLUDED"),
        ("[OK] SMALLTALK INSURANCE", "ACTIVATED"),
        ("[OK] SERENDIPITY RATE", "100% GUARANTEED"),
    ],
}

RECEIPT_ITEMS = PASSPORT_ITEMS


def resolve_ticket_language(
    registration: EventRegistration | None = None,
    event: MeetupEvent | None = None,
    language: str = "",
) -> str:
    """Resolves the target language (en, fr, de) for the printed ticket."""
    if language in ("fr", "de", "en"):
        return language
    if registration:
        user = getattr(registration, "user", None)
        profile = getattr(user, "crushprofile", None) if user else None
        if profile and getattr(profile, "preferred_language", None):
            lang = profile.preferred_language.lower()
            explicitly_set = getattr(profile, "language_explicitly_set", True)
            if lang in ("fr", "de", "en"):
                if explicitly_set or lang != "en":
                    return lang
    if event and getattr(event, "languages", None):
        for el in event.languages:
            if el in ("fr", "de", "en"):
                return el
    return "en"


CRUSH_GHOST_ASCII = [
    r"      .------------.      ",
    r"    .'              '.    ",
    r"   /    (O)    (O)    \  .-. .-. ",
    r"  |         ()         |(   V   )",
    r"  |                    | \     / ",
    r"   \   /\    /\    /\ /   `._.'  ",
    r"    `-'  `--'  `--'  `    ",
]


def _get_social_icons_logo_path() -> str | None:
    """Finds path to Instagram/Facebook/Google/WhatsApp thermal icons asset."""
    import os
    from django.conf import settings

    base = getattr(settings, "BASE_DIR", None)
    if not base:
        return None

    candidate = os.path.join(
        str(base),
        "crush_lu",
        "static",
        "crush_lu",
        "images",
        "social_icons_thermal.png",
    )
    return candidate if os.path.exists(candidate) else None


def _generate_compatibility_gauge_bytes(score: int = 92, width: int = 384, height: int = 70) -> bytes:
    """Generates PNG bytes of a 1-bit monochrome compatibility progress gauge."""
    import io
    from PIL import Image as PILImage, ImageDraw

    scale = 2
    W = width * scale
    H = height * scale
    img = PILImage.new("L", (W, H), 255)
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle([4 * scale, 4 * scale, W - 4 * scale, H - 4 * scale], radius=8 * scale, outline=0, width=2 * scale)

    bar_x0 = int(W * 0.06)
    bar_x1 = int(W * 0.94)
    bar_y0 = int(H * 0.28)
    bar_y1 = int(H * 0.72)
    bar_w = bar_x1 - bar_x0
    bar_h = bar_y1 - bar_y0

    draw.rounded_rectangle([bar_x0, bar_y0, bar_x1, bar_y1], radius=bar_h // 2, outline=0, width=3 * scale)

    num_segs = 16
    gap = 3 * scale
    seg_w = (bar_w - 12 * scale - (num_segs - 1) * gap) / num_segs
    filled = int((score / 100.0) * num_segs)

    for i in range(num_segs):
        sx0 = int(bar_x0 + 6 * scale + i * (seg_w + gap))
        sx1 = int(sx0 + seg_w)
        sy0 = bar_y0 + 4 * scale
        sy1 = bar_y1 - 4 * scale
        if i < filled:
            draw.rounded_rectangle([sx0, sy0, sx1, sy1], radius=2 * scale, fill=0)

    small = img.resize((width, height), PILImage.Resampling.LANCZOS)
    mono = small.point(lambda p: 0 if p < 180 else 255, mode="1")
    buf = io.BytesIO()
    mono.save(buf, format="PNG")
    return buf.getvalue()


def _get_crush_ghost_logo_path() -> str | None:
    """Finds path to official thermal ghost logo asset if present."""
    import os
    from django.conf import settings

    base = getattr(settings, "BASE_DIR", None)
    if not base:
        return None

    candidates = [
        os.path.join(str(base), "crush_lu", "static", "crush_lu", "images", "crush_ghost_thermal.png"),
        os.path.join(str(base), "crush_lu", "static", "crush_lu", "logo.png"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def _build_header_directives(
    event_title: str,
    date_str: str,
    attendee_name: str,
    table_label: str,
    candidate_num: str = "",
    cols: int = 48,
    lang: str = "fr",
    event_type: str = "speed_dating",
) -> list[Directive]:
    """Builds top banner and candidate table assignment."""
    out: list[Directive] = []

    # 1. Crush Ghost Mascot Graphic (Raster 1-bit ESC/POS with ASCII fallback)
    logo_path = _get_crush_ghost_logo_path()
    if logo_path:
        out.append(Image(source=logo_path, width=192, align=Align.CENTER))
        out.append(Feed(1))
    else:
        max_gw = max(len(l) for l in CRUSH_GHOST_ASCII)
        for gl in CRUSH_GHOST_ASCII:
            out.append(Text(gl.ljust(max_gw), Align.CENTER))
        out.append(Feed(1))

    if event_type in ("speed_dating", "mixer"):
        sub = {
            "fr": "SPEED DATING // PASS ENREGISTREMENT",
            "de": "SPEED DATING // CHECK-IN PASS",
            "en": "SPEED DATING // CHECK-IN PASS",
        }.get(lang, "SPEED DATING // CHECK-IN PASS")
    else:
        sub = {
            "fr": "ÉVÉNEMENT // PASS ENREGISTREMENT",
            "de": "EVENT // CHECK-IN PASS",
            "en": "EVENT // CHECK-IN PASS",
        }.get(lang, "EVENT // CHECK-IN PASS")

    event_lbl = {"fr": "ÉVÉNEMENT:", "de": "EVENT:", "en": "EVENT:"}.get(
        lang, "EVENT:"
    )
    date_lbl = {"fr": "DATE:", "de": "DATUM:", "en": "DATE:"}.get(
        lang, "DATE:"
    )

    out.append(Text("CRUSH.LU", Align.CENTER, bold=True, double_height=True))
    out.append(Text(sub, Align.CENTER, bold=True))
    out.append(Rule("="))

    out.append(Text(justify(event_lbl, event_title[: cols - 12], cols)))
    out.append(Text(justify(date_lbl, date_str, cols)))
    out.append(Rule("-"))

    candidate_display = f"{attendee_name} {candidate_num}".strip()
    dw_cols = max(16, cols // 2)
    out.append(
        Text(
            justify(
                candidate_display.upper()[: dw_cols - len(table_label) - 1],
                table_label.upper(),
                dw_cols,
            ),
            bold=True,
            double_width=True,
        )
    )
    out.append(Rule("="))
    return out


def _build_receipt_directives(cols: int = 48, lang: str = "fr") -> list[Directive]:
    """Builds the stylish VIP Event Passport / Access Card section."""
    out: list[Directive] = []
    hdr = {
        "fr": "PASSEPORT ÉVÉNEMENT // ACCÈS",
        "de": "EVENT PASSPORT // ZUGANG",
        "en": "EVENT PASSPORT // ACCESS",
    }.get(lang, "EVENT PASSPORT // ACCESS")

    out.append(Rule("~"))
    out.append(Text(hdr, Align.CENTER, bold=True))
    out.append(Rule("-"))

    items = PASSPORT_ITEMS.get(lang, PASSPORT_ITEMS["en"])
    for left, right in items:
        out.append(Text(justify(left, right, cols)))

    out.append(Rule("~"))
    return out


def _build_coach_rules_directives(
    custom_rules: list[str] | None = None,
    cols: int = 48,
    lang: str = "fr",
    seed_id: int | None = None,
) -> list[Directive]:
    """Builds Coach survival tips with deterministic non-overlapping selection."""
    out: list[Directive] = []
    hdr = {
        "fr": "GUIDE DE SURVIE DU CRUSH COACH",
        "de": "CRUSH COACH // SURVIVAL GUIDE",
        "en": "CRUSH COACH // SURVIVAL GUIDE",
    }.get(lang, "CRUSH COACH // SURVIVAL GUIDE")

    out.append(Text(hdr, Align.CENTER, bold=True))
    out.append(Rule("-"))

    pool = COACH_SURVIVAL_RULES.get(lang, COACH_SURVIVAL_RULES["en"])
    if custom_rules:
        rules = custom_rules
    elif seed_id is not None:
        rng = random.Random(seed_id)
        rules = rng.sample(pool, min(2, len(pool)))
    else:
        rules = random.sample(pool, min(2, len(pool)))

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
    lang: str = "fr",
    coach_authenticated: bool = False,
) -> list[Directive]:
    """Builds collective group statistics for the event attendees.

    Gated to coach-authenticated requests, matching
    ``_build_mystery_radar_directives`` below: for a small attendee count,
    aggregate percentages (age, admitted quirks, top interests) are
    trivially invertible into a specific other registrant's private profile
    data, and this endpoint is deliberately reachable via an unattended
    self-scan with no coach present.
    """
    out: list[Directive] = []

    if not coach_authenticated:
        return out

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
                "user__crushprofile__qualities",
            )
        )
        total = len(regs)
        if total < 2:
            return out

        all_interests = []
        all_defects = []
        all_vibes = []
        all_langs = []
        ages = []
        first_step_counts: Counter[str] = Counter()

        for r in regs:
            prof = getattr(getattr(r, "user", None), "crushprofile", None)
            if not prof:
                continue
            all_interests.extend([i.label for i in prof.interests_new.all()])
            all_defects.extend([d.label for d in prof.defects.all()])
            if prof.event_vibe:
                all_vibes.append(prof.get_event_vibe_display())
            all_langs.extend(prof.event_languages or [])
            if prof.first_step_preference:
                first_step_counts[prof.first_step_preference] += 1
            if prof.age:
                ages.append(prof.age)

        top_interests = Counter(all_interests).most_common(3)
        top_defects = Counter(all_defects).most_common(2)
        top_vibes = Counter(all_vibes).most_common(2)

        hdr = {
            "fr": "ROOM DATA // STATS DE LA SOIRÉE",
            "de": "ROOM DATA // STATS DES ABENDS",
            "en": "ROOM DATA // TONIGHT'S STATS",
        }.get(lang, "ROOM DATA // STATS DE LA SOIRÉE")

        out.append(Rule("*"))
        out.append(Text(hdr, Align.CENTER, bold=True))
        out.append(Rule("-"))

        # Compatibility Gauge & Chemistry Potential
        score = 92
        if registration and getattr(registration, "id", None):
            score = 88 + (registration.id % 11)

        lbl_gauge = {
            "fr": f"INDICE D'AFFINITÉ : {score}%",
            "de": f"CHEMISTRY-POTENTIAL : {score}%",
            "en": f"CHEMISTRY POTENTIAL : {score}%",
        }.get(lang, f"CHEMISTRY POTENTIAL : {score}%")

        out.append(Text(lbl_gauge, Align.CENTER, bold=True))
        out.append(Feed(1))
        out.append(Image(source=_generate_compatibility_gauge_bytes(score=score, width=384), width=384, align=Align.CENTER))
        out.append(Feed(1))

        # 1. Âge & Démographie + 1er Pas (Compact Pills)
        pill_age = ""
        if ages:
            avg_age = int(round(sum(ages) / len(ages)))
            min_age, max_age = min(ages), max(ages)
            if lang == "fr":
                pill_age = f"[ ÂGE: {avg_age} ans ({min_age}-{max_age}) ]"
            elif lang == "de":
                pill_age = f"[ ALTER: {avg_age} J. ({min_age}-{max_age}) ]"
            else:
                pill_age = f"[ AGE: {avg_age} yrs ({min_age}-{max_age}) ]"

        pill_step = ""
        if first_step_counts:
            they_init = first_step_counts.get("they_initiate", 0)
            they_pct = int((they_init / total) * 100)
            i_init = first_step_counts.get("i_initiate", 0)
            i_pct = int((i_init / total) * 100)
            if they_pct > 0 or i_pct > 0:
                if lang == "fr":
                    pill_step = f"[ 1er PAS: {they_pct}% ATT. ]"
                elif lang == "de":
                    pill_step = f"[ 1. SCHRITT: {they_pct}% W. ]"
                else:
                    pill_step = f"[ 1ST STEP: {they_pct}% WAIT ]"

        if pill_age and pill_step:
            out.append(Text(justify(pill_age, pill_step, cols)))
        elif pill_age:
            out.append(Text(pill_age, Align.CENTER))
        elif pill_step:
            out.append(Text(pill_step, Align.CENTER))

        # 2. Top Passions & Hobbies with ASCII Data Bars
        if top_interests:
            out.append(Feed(1))
            hdr_passions = {
                "fr": "TOP PASSIONS DANS LA SALLE :",
                "de": "TOP HOBBYS IM RAUM :",
                "en": "TOP PASSIONS IN THE ROOM :",
            }.get(lang, "TOP PASSIONS IN THE ROOM :")
            out.append(Text(hdr_passions, bold=True))
            for name, count in top_interests:
                pct = int((count / total) * 100)
                bar_len = max(1, min(10, int(round(pct / 10))))
                bars = "■" * bar_len
                line_bar = f"{bars:<7} {pct}% {name} ({count}p)"
                if len(line_bar) <= cols:
                    out.append(Text(line_bar))
                else:
                    out.append(Text(justify(f"{bars} {pct}% {name[:cols-18]}", f"({count}p)", cols)))

        # 3. Vibes Pill
        if top_vibes:
            out.append(Feed(1))
            vibe_str = " | ".join(f"{v[0].upper()} {int(v[1]/total*100)}%" for v in top_vibes)
            out.append(Text(f"[ VIBES: {vibe_str} ]"[:cols]))

        # 4. Petits défauts partagés (Autodérision)
        if top_defects:
            lbl_def = {
                "fr": "DÉFAUTS: ",
                "de": "MACKEN: ",
                "en": "QUIRKS: ",
            }.get(lang, "QUIRKS: ")
            def_items = [f"'{d[0]}' {int(d[1]/total*100)}%" for d in top_defects]
            def_str = f"[ {lbl_def}" + " | ".join(def_items) + " ]"
            for part in wrap(def_str, cols):
                out.append(Text(part))

        # 5. Répartition des langues
        if all_langs:
            lang_counts = Counter(all_langs)
            lu_pct = int((lang_counts.get("lu", 0) / total) * 100)
            fr_pct = int((lang_counts.get("fr", 0) / total) * 100)
            de_pct = int((lang_counts.get("de", 0) / total) * 100)
            en_pct = int((lang_counts.get("en", 0) / total) * 100)
            lbl_l = {
                "fr": "LANGUES",
                "de": "SPRACHEN",
                "en": "LANGUAGES",
            }.get(lang, "LANGUAGES")
            out.append(
                Text(
                    f"[ {lbl_l}: LU {lu_pct}% | FR {fr_pct}% | DE {de_pct}% | EN {en_pct}% ]"[:cols]
                )
            )

        out.append(Rule("*"))
    except Exception as e:
        logger.warning("Failed to build room stats directives: %s", e, exc_info=True)
        return []

    return out


def _build_mission_directives(
    custom_mission: str | None = None,
    cols: int = 48,
    lang: str = "fr",
) -> list[Directive]:
    """Builds fallback Room Mystery Radar secret icebreaker mission."""
    out: list[Directive] = []
    hdr = {
        "fr": "MYSTERY RADAR // MISSION SECRÈTE",
        "de": "MYSTERY RADAR // GEHEIMMISSION",
        "en": "MYSTERY RADAR // SECRET MISSION",
    }.get(lang, "MYSTERY RADAR // SECRET MISSION")

    out.append(Text(hdr, Align.CENTER, bold=True))
    out.append(Rule("-"))

    pool = SECRET_MISSIONS.get(lang, SECRET_MISSIONS["en"])
    mission = custom_mission or random.choice(pool)
    for part in wrap(mission, cols):
        out.append(Text(part, Align.CENTER))

    out.append(Rule("="))
    return out


def _build_mystery_radar_directives(
    registration: EventRegistration | None = None,
    event: MeetupEvent | None = None,
    cols: int = 48,
    lang: str = "fr",
    coach_authenticated: bool = False,
) -> list[Directive]:
    """Builds interactive Mystery Radar clues from actual event attendees.

    Displays anonymous badge numbers like '( #6 )' with empty checkboxes [   ]
    WITHOUT attendee names, so candidates discover and write the name during dates.
    Gated to coach-authenticated requests to keep attendee details out of public payloads.
    """
    if not coach_authenticated:
        return _build_mission_directives(cols=cols, lang=lang)

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
                my_connect_membership = (
                    getattr(my_user, "crush_connect_membership", None)
                    if my_user
                    else None
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

                if my_connect_membership and getattr(my_connect_membership, "preferred_genders", None):
                    my_pref_genders = my_connect_membership.preferred_genders or []

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
                    # Anonymous badge number only: e.g. "(#6)"
                    badge = f"(#{other.id})"

                    other_interests = [i.label for i in op.interests_new.all()]
                    common = my_interests.intersection(other_interests)
                    other_defects = list(op.defects.all())
                    other_qualities = list(op.qualities.all())

                    if common:
                        txt = {
                            "fr": f'Partage ta passion "{list(common)[0]}"',
                            "de": f'Teilt deine Leidenschaft "{list(common)[0]}"',
                            "en": f'Shares your passion "{list(common)[0]}"',
                        }.get(lang, f'Shares your passion "{list(common)[0]}"')
                        clues.append((txt, badge))
                    elif op.event_vibe:
                        clues.append(
                            (f'Vibe: "{op.get_event_vibe_display()}"', badge)
                        )
                    elif other_interests:
                        txt = {
                            "fr": f'Adore "{other_interests[0]}"',
                            "de": f'Liebt "{other_interests[0]}"',
                            "en": f'Loves "{other_interests[0]}"',
                        }.get(lang, f'Loves "{other_interests[0]}"')
                        clues.append((txt, badge))
                    elif other_defects:
                        txt = {
                            "fr": f'Défaut: "{other_defects[0].label}"',
                            "de": f'Macke: "{other_defects[0].label}"',
                            "en": f'Quirk: "{other_defects[0].label}"',
                        }.get(lang, f'Quirk: "{other_defects[0].label}"')
                        clues.append((txt, badge))
                    elif other_qualities:
                        txt = {
                            "fr": f'Atout: "{other_qualities[0].label}"',
                            "de": f'Stärke: "{other_qualities[0].label}"',
                            "en": f'Strength: "{other_qualities[0].label}"',
                        }.get(lang, f'Strength: "{other_qualities[0].label}"')
                        clues.append((txt, badge))
                    elif op.location:
                        txt = {
                            "fr": f'Vient de "{op.location}"',
                            "de": f'Kommt aus "{op.location}"',
                            "en": f'From "{op.location}"',
                        }.get(lang, f'From "{op.location}"')
                        clues.append((txt, badge))
                    elif op.event_languages:
                        txt = {
                            "fr": f'Parle {op.event_languages[0].upper()}',
                            "de": f'Spricht {op.event_languages[0].upper()}',
                            "en": f'Speaks {op.event_languages[0].upper()}',
                        }.get(lang, f'Speaks {op.event_languages[0].upper()}')
                        clues.append((txt, badge))
                    else:
                        txt_fallback = {
                            "fr": "Profil Énigme Mystère",
                            "de": "Geheimes Mystery-Profil",
                            "en": "Secret Enigma Profile",
                        }.get(lang, "Secret Enigma Profile")
                        clues.append((txt_fallback, badge))

                # Scale clues to event size (e.g. ~7 for a 14-person / 7-table
                # event), capped at 7 regardless of pool size so a large mixer
                # doesn't print dozens of clue lines on one ticket. `min`, not
                # `max`, is the operative bound here.
                target_clues = 7
                if event and getattr(event, "max_participants", None):
                    target_clues = min(7, max(1, event.max_participants // 2))

                random.shuffle(clues)
                clues = clues[:target_clues]
        except Exception as e:
            logger.warning("Failed to build mystery radar clues: %s", e, exc_info=True)
            clues = []

    if not clues:
        return _build_mission_directives(cols=cols, lang=lang)

    out: list[Directive] = []
    hdr = {
        "fr": f"★ LES {len(clues)} RENCONTRES MYSTÈRES DU SOIR ★",
        "de": f"★ DEINE {len(clues)} MYSTERY-DATES HEUTE ABEND ★",
        "en": f"★ TONIGHT'S {len(clues)} MYSTERY CANDIDATES ★",
    }.get(lang, f"★ TONIGHT'S {len(clues)} MYSTERY CANDIDATES ★")

    sub = {
        "fr": "Devine qui se cache derrière chaque profil :",
        "de": "Finde heraus, wer hinter jedem Profil steckt:",
        "en": "Discover who matches each profile during dates:",
    }.get(lang, "Discover who matches each profile during dates:")

    name_label = {
        "fr": "Prénom : ____________________",
        "de": "Name : ______________________",
        "en": "Name : ______________________",
    }.get(lang, "Name : ______________________")

    out.append(Text(hdr, Align.CENTER, bold=True))
    out.append(Rule("-"))
    out.append(Text(sub, Align.CENTER))
    out.append(Feed(1))

    for clue_text, badge in clues:
        raw_num = badge.strip("()# ")
        tag = f"[?] MATCH #{raw_num}"
        if len(tag) + len(clue_text) + 2 <= cols:
            out.append(Text(justify(tag, clue_text, cols), bold=True))
        else:
            out.append(Text(tag, bold=True))
            for part in wrap(clue_text, cols, indent="    "):
                out.append(Text(part))
        out.append(Text(f"    {name_label}"))
        out.append(Feed(1))

    out.append(Rule("="))
    return out


def _build_qr_footer_directives(
    qr_url: str,
    cols: int = 48,
    lang: str = "fr",
) -> list[Directive]:
    """Builds QR code voting portal and social media spotlight card."""
    out: list[Directive] = []
    hdr = {
        "fr": "SCANNE APRÈS L'ÉVÉNEMENT POUR VOTER :",
        "de": "NACH DEM EVENT SCANNEN ZUM VOTEN :",
        "en": "SCAN AFTER EVENT TO VOTE & MATCH :",
    }.get(lang, "SCAN AFTER EVENT TO VOTE & MATCH :")

    out.append(Text(hdr, Align.CENTER, bold=True))
    out.append(Feed(1))
    if qr_url:
        out.append(QrCode(qr_url, size=6))
        out.append(Feed(2))
    out.append(Text(qr_url, Align.CENTER))
    out.append(Feed(1))

    # Highlighted Social Media Story Card
    out.append(Rule("="))
    social_icons_path = _get_social_icons_logo_path()
    if social_icons_path:
        out.append(Image(source=social_icons_path, width=384, align=Align.CENTER))
        out.append(Feed(1))

    hdr_social = {
        "fr": "PARTAGE TA STORY // @CRUSH.LU",
        "de": "TEILE DEINE STORY // @CRUSH.LU",
        "en": "SHARE YOUR STORY // @CRUSH.LU",
    }.get(lang, "SHARE YOUR STORY // @CRUSH.LU")
    prompt_social = {
        "fr": "Prends une photo de ton pass & tag nous :",
        "de": "Mach ein Foto von deinem Pass & tagge uns :",
        "en": "Snap a photo of your pass & tag us :",
    }.get(lang, "Snap a photo of your pass & tag us :")
    sub_social = {
        "fr": "Tag @crush.lu pour être en story demain !",
        "de": "Tagge @crush.lu pour unsere Story morgen!",
        "en": "Tag @crush.lu to get featured in our story!",
    }.get(lang, "Tag @crush.lu to get featured in our story!")

    out.append(Text(hdr_social, Align.CENTER, bold=True))
    out.append(Rule("-"))
    out.append(Text(prompt_social, Align.CENTER))
    out.append(Feed(1))
    out.append(Text(">>>  @CRUSH.LU  <<<", Align.CENTER, bold=True))
    out.append(Feed(1))
    out.append(Text("#CrushSpeedDating  *  #CrushLu", Align.CENTER))
    out.append(Text(sub_social, Align.CENTER))
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
    language: str = "",
) -> list[Directive]:
    """Builds the full sequence of directives for a check-in ticket."""
    from django.utils.translation import override as translation_override

    cols = paper.columns
    lang = resolve_ticket_language(
        registration=registration, event=event, language=language
    )

    with translation_override(lang):
        event_title = "Speed Dating Event"
        now_local = (
            timezone.localtime(timezone.now())
            if timezone.is_aware(timezone.now())
            else timezone.now()
        )
        date_str = _format_ticket_date(now_local, lang=lang)
        attendee_name = "Guest"
        candidate_num = ""

        if registration:
            reg_user = getattr(registration, "user", None)
            if reg_user:
                profile = getattr(reg_user, "crushprofile", None)
                if profile and getattr(profile, "display_name", None):
                    attendee_name = profile.display_name
                elif coach_authenticated:
                    # `username` is never used here — signup is email-only, so
                    # it is generally the address itself (see the identical
                    # rule `_get_display_name`, views_checkin.py, follows).
                    if reg_user.first_name:
                        attendee_name = reg_user.first_name
                    else:
                        attendee_name = "Attendee"
                else:
                    attendee_name = "Attendee"
            candidate_num = f"(#{registration.id})"

        if event:
            event_title = getattr(event, "title", event_title)
            event_dt = getattr(event, "date_time", None)
            if event_dt:
                date_str = _format_ticket_date(event_dt, lang=lang)

        table_display = "WELCOME" if lang != "fr" else "BIENVENUE"
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
                try:
                    from django.urls import reverse

                    path = reverse("crush_lu:event_lobby", kwargs={"event_id": event_id})
                    qr_url = f"{base_domain}{path}"
                except Exception:
                    qr_url = f"{base_domain}/{lang}/events/{event_id}/lobby/"
            else:
                try:
                    from django.urls import reverse

                    path = reverse("crush_lu:my_crush")
                    qr_url = f"{base_domain}{path}"
                except Exception:
                    qr_url = f"{base_domain}/{lang}/my-crush/"

        event_type = getattr(event, "event_type", "speed_dating") or "speed_dating"
        is_dating_event = event_type in ("speed_dating", "mixer")

        directives: list[Directive] = []
        directives.extend(
            _build_header_directives(
                event_title=event_title,
                date_str=date_str,
                attendee_name=attendee_name,
                table_label=table_display,
                candidate_num=candidate_num,
                cols=cols,
                lang=lang,
                event_type=event_type,
            )
        )
        directives.extend(
            _build_room_stats_directives(
                registration=registration,
                event=event,
                cols=cols,
                lang=lang,
                coach_authenticated=coach_authenticated,
            )
        )
        if is_dating_event:
            directives.extend(
                _build_coach_rules_directives(
                    cols=cols, lang=lang, seed_id=getattr(registration, "id", None)
                )
            )
        directives.extend(
            _build_mystery_radar_directives(
                registration=registration,
                event=event,
                cols=cols,
                lang=lang,
                coach_authenticated=coach_authenticated,
            )
        )
        directives.extend(
            _build_qr_footer_directives(qr_url=qr_url, cols=cols, lang=lang)
        )

        return directives


def build_checkin_ticket_bytes(
    registration: EventRegistration | None = None,
    event: MeetupEvent | None = None,
    table_number: int | None = None,
    seat_label: str = "",
    qr_url: str = "",
    paper: Paper = Paper.MM80,
    coach_authenticated: bool = False,
    language: str = "",
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
        language=language,
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
    language: str = "",
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
        language=language,
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
    language: str = "",
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
        language=language,
    )
    return render_plain_text(directives, paper=paper)
