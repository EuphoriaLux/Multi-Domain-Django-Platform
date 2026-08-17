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
    ],
    "en": [
        "3 minutes fly faster than the tram on the Kirchberg.",
        "Talking about your 'Ex' in Round 1 costs -50 aura points.",
        "Eye contact: Warm smile, avoid staring like in a crime drama.",
        "Emergency question for awkward silence: 'What is your most useless talent?'",
        "Ask about travel & obsessions, not about tax brackets.",
        "A smile is half the charm. Active listening is the rest.",
        "Nobody judges your music taste (unless it's fairground techno).",
        "Complimenting shoes or a smile works 10x better than cars.",
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

RECEIPT_ITEMS: dict[str, list[tuple[str, str]]] = {
    "fr": [
        ("1x Espoir & Optimisme", "EUR 0.00"),
        ("1x Charme 1ère impression", "100%"),
        ("1x Assurance Smalltalk", "INCLUS"),
        ("1x Brise-glace luxembourgeois", "GRATUIT"),
    ],
    "de": [
        ("1x Hoffnung & Optimismus", "EUR 0.00"),
        ("1x Charme beim 1. Eindruck", "100%"),
        ("1x Smalltalk-Versicherung", "INKLUSIVE"),
        ("1x Luxemburger Eisbrecher", "GRATIS"),
    ],
    "en": [
        ("1x Hope & Optimism", "EUR 0.00"),
        ("1x First Impression Charm", "100%"),
        ("1x Smalltalk Insurance", "INCLUDED"),
        ("1x Luxembourgish Icebreaker", "FREE"),
    ],
}


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
            if lang in ("fr", "de", "en"):
                return lang
    if event and getattr(event, "languages", None):
        for el in event.languages:
            if el in ("fr", "de", "en"):
                return el
    return "en"


def _build_header_directives(
    event_title: str,
    date_str: str,
    attendee_name: str,
    table_label: str,
    candidate_num: str = "",
    cols: int = 48,
    lang: str = "fr",
) -> list[Directive]:
    """Builds top banner and candidate table assignment."""
    out: list[Directive] = []

    sub = {
        "fr": "SPEED DATING // PASS ENREGISTREMENT",
        "de": "SPEED DATING // CHECK-IN PASS",
        "en": "SPEED DATING // CHECK-IN PASS",
    }.get(lang, "SPEED DATING // CHECK-IN PASS")

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
    """Builds the viral humorous dating receipt breakdown."""
    out: list[Directive] = []
    hdr = {
        "fr": "REÇU DATING // RÉCAPITULATIF",
        "de": "DATING RECEIPT // SUMMARY",
        "en": "DATING RECEIPT // SUMMARY",
    }.get(lang, "DATING RECEIPT // SUMMARY")
    tot = {
        "fr": ("TOTAL", "INESTIMABLE"),
        "de": ("TOTAL", "PRICELESS"),
        "en": ("TOTAL", "PRICELESS"),
    }.get(lang, ("TOTAL", "PRICELESS"))

    out.append(Text(hdr, Align.CENTER, bold=True))
    out.append(Rule("-"))

    items = RECEIPT_ITEMS.get(lang, RECEIPT_ITEMS["en"])
    for left, right in items:
        out.append(Text(justify(left, right, cols)))

    out.append(Rule("-"))
    out.append(Text(justify(tot[0], tot[1], cols), bold=True))
    out.append(Rule("="))
    return out


def _build_coach_rules_directives(
    custom_rules: list[str] | None = None,
    cols: int = 48,
    lang: str = "fr",
) -> list[Directive]:
    """Builds Coach survival tips."""
    out: list[Directive] = []
    hdr = {
        "fr": "GUIDE DE SURVIE DU CRUSH COACH",
        "de": "CRUSH COACH // SURVIVAL GUIDE",
        "en": "CRUSH COACH // SURVIVAL GUIDE",
    }.get(lang, "CRUSH COACH // SURVIVAL GUIDE")

    out.append(Text(hdr, Align.CENTER, bold=True))
    out.append(Rule("-"))

    pool = COACH_SURVIVAL_RULES.get(lang, COACH_SURVIVAL_RULES["en"])
    rules = custom_rules or random.sample(pool, min(2, len(pool)))
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

        out.append(Text(hdr, Align.CENTER, bold=True))
        out.append(Rule("-"))

        # 1. Âge & Démographie
        if ages:
            avg_age = int(round(sum(ages) / len(ages)))
            min_age, max_age = min(ages), max(ages)
            lbl_age = {
                "fr": f"• Âge moyen: {avg_age} ans",
                "de": f"• Durchschnittsalter: {avg_age} J.",
                "en": f"• Average age: {avg_age} yrs",
            }.get(lang, f"• Âge moyen: {avg_age} ans")
            span_age = f"({min_age}-{max_age} ans)" if lang == "fr" else (f"({min_age}-{max_age} J.)" if lang == "de" else f"({min_age}-{max_age} yrs)")
            out.append(Text(justify(lbl_age, span_age, cols)))

        # 2. Top Passions & Hobbies
        if top_interests:
            hdr_passions = {
                "fr": "TOP PASSIONS DU GROUPE :",
                "de": "TOP HOBBYS IM RAUM :",
                "en": "TOP GROUP PASSIONS :",
            }.get(lang, "TOP PASSIONS DU GROUPE :")
            out.append(Text(hdr_passions, bold=True))
            for name, count in top_interests:
                pct = int((count / total) * 100)
                out.append(Text(justify(f"  - {name}", f"{pct}% ({count}p)", cols)))

        # 3. Dynamique de drague (1er pas)
        if first_step_counts:
            they_init = first_step_counts.get("they_initiate", 0)
            they_pct = int((they_init / total) * 100)
            i_init = first_step_counts.get("i_initiate", 0)
            i_pct = int((i_init / total) * 100)
            if they_pct > 0 or i_pct > 0:
                line_step = {
                    "fr": f"• 1er pas: {they_pct}% attendent | {i_pct}% foncent",
                    "de": f"• 1. Schritt: {they_pct}% warten | {i_pct}% starten",
                    "en": f"• 1st step: {they_pct}% wait | {i_pct}% initiate",
                }.get(lang, f"• 1er pas: {they_pct}% attendent | {i_pct}% foncent")
                out.append(Text(line_step))

        # 4. Ambiance / Vibes
        if top_vibes:
            vibe_str = " | ".join(f"{v[0]} ({int(v[1]/total*100)}%)" for v in top_vibes)
            out.append(Text(f"• Vibes: {vibe_str}"[:cols]))

        # 5. Petits défauts partagés (Autodérision)
        if top_defects:
            lbl_def = {
                "fr": "• Défauts avoués: ",
                "de": "• Offene Macken: ",
                "en": "• Admitted quirks: ",
            }.get(lang, "• Défauts avoués: ")
            def_items = [f"'{d[0]}' ({int(d[1]/total*100)}%)" for d in top_defects]
            def_str = lbl_def + " & ".join(def_items)
            for part in wrap(def_str, cols):
                out.append(Text(part))

        # 6. Répartition des langues
        if all_langs:
            lang_counts = Counter(all_langs)
            lu_pct = int((lang_counts.get("lu", 0) / total) * 100)
            fr_pct = int((lang_counts.get("fr", 0) / total) * 100)
            de_pct = int((lang_counts.get("de", 0) / total) * 100)
            en_pct = int((lang_counts.get("en", 0) / total) * 100)
            lbl_l = {
                "fr": "• Langues:",
                "de": "• Sprachen:",
                "en": "• Languages:",
            }.get(lang, "• Langues:")
            out.append(
                Text(
                    f"{lbl_l} LU {lu_pct}% | FR {fr_pct}% | DE {de_pct}% | EN {en_pct}%"
                )
            )

        out.append(Rule("="))
    except Exception:
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
) -> list[Directive]:
    """Builds interactive Mystery Radar clues from actual event attendees.

    Displays anonymous badge numbers like '( #6 )' with empty checkboxes [   ]
    WITHOUT attendee names, so candidates discover and write the name during dates.
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
                    # Anonymous badge number only: e.g. "(#6)"
                    badge = f"(#{other.id})"

                    other_interests = [i.label for i in op.interests_new.all()]
                    common = my_interests.intersection(other_interests)

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
                    elif op.defects.exists():
                        txt = {
                            "fr": f'Défaut: "{op.defects.first().label}"',
                            "de": f'Macke: "{op.defects.first().label}"',
                            "en": f'Quirk: "{op.defects.first().label}"',
                        }.get(lang, f'Quirk: "{op.defects.first().label}"')
                        clues.append((txt, badge))
                    elif op.qualities.exists():
                        txt = {
                            "fr": f'Atout: "{op.qualities.first().label}"',
                            "de": f'Stärke: "{op.qualities.first().label}"',
                            "en": f'Strength: "{op.qualities.first().label}"',
                        }.get(lang, f'Strength: "{op.qualities.first().label}"')
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
                        clues.append(("Mystery Match", badge))

                # Scale clues dynamically to match event size (e.g. 7 candidates for 7 tables)
                max_clues = len(candidate_pool)
                if event and getattr(event, "max_participants", None):
                    max_clues = max(max_clues, min(7, event.max_participants // 2))

                random.shuffle(clues)
                clues = clues[:max(max_clues, 7)]
        except Exception:
            clues = []

    if not clues:
        return _build_mission_directives(cols=cols, lang=lang)

    out: list[Directive] = []
    hdr = {
        "fr": f"MYSTERY RADAR // LES {len(clues)} CANDIDAT(E)S",
        "de": f"MYSTERY RADAR // DIE {len(clues)} KANDIDATEN",
        "en": f"MYSTERY RADAR // THE {len(clues)} CANDIDATES",
    }.get(lang, f"MYSTERY RADAR // THE {len(clues)} CANDIDATES")

    sub1 = {
        "fr": "Devine qui correspond à chaque indice et",
        "de": "Finde heraus, wer zu jedem Hinweis passt und",
        "en": "Guess who matches each clue and",
    }.get(lang, "Guess who matches each clue and")

    sub2 = {
        "fr": "écris son prénom dans la case [   ] :",
        "de": "schreibe den Namen in das Feld [   ] :",
        "en": "write their name in the box [   ] :",
    }.get(lang, "write their name in the box [   ] :")

    out.append(Text(hdr, Align.CENTER, bold=True))
    out.append(Rule("-"))
    out.append(Text(sub1, Align.CENTER))
    out.append(Text(sub2, Align.CENTER))
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
    lang: str = "fr",
) -> list[Directive]:
    """Builds QR code and social media footer."""
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
                lang=lang,
            )
        )
        directives.extend(_build_receipt_directives(cols=cols, lang=lang))
        directives.extend(
            _build_room_stats_directives(
                registration=registration, event=event, cols=cols, lang=lang
            )
        )
        directives.extend(
            _build_coach_rules_directives(cols=cols, lang=lang)
        )
        directives.extend(
            _build_mystery_radar_directives(
                registration=registration, event=event, cols=cols, lang=lang
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
