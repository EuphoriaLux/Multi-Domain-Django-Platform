"""
One-shot helper: apply DE + FR translations for the onboarding-redesign
Phase 1 strings into `crush_lu/locale/{de,fr}/LC_MESSAGES/django.po`.

Run with:  python crush_lu/_apply_translations.py

Then delete this file. It is not part of the shipped code — it exists only so
the translator can be reviewed in a diff.
"""
import re
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent

# Phase-1 strings only. Keep this list tight so we don't accidentally overwrite
# good translations elsewhere in the catalog.
DE = {
    # Journey stepper
    "Welcome": "Willkommen",
    "Verify number": "Nummer bestätigen",
    "Select your Coach": "Wähle deinen Coach",
    "Build profile": "Profil erstellen",
    "Meet your Coach": "Lerne deinen Coach kennen",
    "Under review": "In Prüfung",
    "Screening call": "Screening-Anruf",
    "Chapter 1": "Kapitel 1",
    "Chapter 2": "Kapitel 2",
    "Chapter 3": "Kapitel 3",
    "Get set up": "Einrichten",
    "You & your coach": "Du & dein Coach",
    "Review & match": "Prüfung & Match",
    "Onboarding progress": "Onboarding-Fortschritt",
    "min": "Min.",
    "Instant": "Sofort",
    "(completed)": "(abgeschlossen)",
    "(current)": "(aktuell)",
    "(locked)": "(gesperrt)",
    # Welcome page
    "Welcome to Crush.lu": "Willkommen bei Crush.lu",
    "One quick question to help us guide you, and a preview of the road ahead.":
        "Eine kurze Frage, damit wir dich richtig begleiten können – plus ein Blick auf den Weg.",
    "Hi %(name)s,<br>welcome to Crush.lu.": "Hi %(name)s,<br>willkommen bei Crush.lu.",
    "Before we start, one quick question to help us guide you.":
        "Bevor es losgeht: eine kurze Frage, damit wir dich richtig begleiten können.",
    "What brings you here?": "Was führt dich zu uns?",
    "The road ahead · ≈ 15 min to get started":
        "Dein Weg · ca. 15 Min. bis zum Start",
    "What makes us different": "Was uns besonders macht",
    "Events first": "Events zuerst",
    "Mixers, hikes, speed dating — real rooms.":
        "Mixer, Wanderungen, Speed-Dating – echte Räume.",
    "Coach-supported": "Coach-begleitet",
    "A human reviews every profile. Within 48h.":
        "Ein Mensch prüft jedes Profil. Innerhalb von 48 Std.",
    "Privacy-first": "Datenschutz zuerst",
    "No public browsing. No swiping feed. Ever.":
        "Kein öffentliches Stöbern. Kein Swipe-Feed. Niemals.",
    "Start building my profile": "Mit meinem Profil starten",
    "I'll do this later": "Ich mache das später",
    # Intent probe options
    "I want to meet people at real events":
        "Ich will Menschen bei echten Events treffen",
    "I'm curious but still exploring": "Ich bin neugierig, schaue mich noch um",
    "I want online dating, events are a bonus":
        "Ich will Online-Dating, Events sind ein Bonus",
    "A friend recommended it": "Ein Freund hat es mir empfohlen",
    # Intent probe responses
    "Perfect — that's exactly what we're built for. Let's get you to your first event.":
        "Perfekt – genau dafür sind wir da. Wir bringen dich zu deinem ersten Event.",
    "Great, we'll walk you through it. You can decide step by step.":
        "Super, wir begleiten dich. Du entscheidest Schritt für Schritt.",
    "Honest heads-up: our online features require attending at least one event first. Still want to continue?":
        "Ehrlicher Hinweis: Unsere Online-Funktionen setzen voraus, dass du zuerst an mindestens einem Event teilnimmst. Willst du trotzdem weitermachen?",
    "Love that. Who referred you?": "Schön. Wer hat dich empfohlen?",
    "Learn more": "Mehr erfahren",
    "Yes, I'm in": "Ja, ich bin dabei",
    "Referral code or name": "Empfehlungscode oder Name",
}

FR = {
    # Journey stepper
    "Welcome": "Bienvenue",
    "Verify number": "Vérifier le numéro",
    "Select your Coach": "Choisir ton coach",
    "Build profile": "Créer ton profil",
    "Meet your Coach": "Rencontre ton coach",
    "Under review": "En cours de relecture",
    "Screening call": "Appel de présélection",
    "Chapter 1": "Chapitre 1",
    "Chapter 2": "Chapitre 2",
    "Chapter 3": "Chapitre 3",
    "Get set up": "Mise en place",
    "You & your coach": "Toi & ton coach",
    "Review & match": "Relecture & match",
    "Onboarding progress": "Progression de l'inscription",
    "min": "min",
    "Instant": "Instantané",
    "(completed)": "(terminé)",
    "(current)": "(actuel)",
    "(locked)": "(verrouillé)",
    # Welcome page
    "Welcome to Crush.lu": "Bienvenue sur Crush.lu",
    "One quick question to help us guide you, and a preview of the road ahead.":
        "Une petite question pour bien t'orienter, et un aperçu de la suite.",
    "Hi %(name)s,<br>welcome to Crush.lu.":
        "Salut %(name)s,<br>bienvenue sur Crush.lu.",
    "Before we start, one quick question to help us guide you.":
        "Avant de commencer, une petite question pour bien t'orienter.",
    "What brings you here?": "Qu'est-ce qui t'amène ?",
    "The road ahead · ≈ 15 min to get started":
        "La suite · ≈ 15 min pour démarrer",
    "What makes us different": "Ce qui nous distingue",
    "Events first": "Les événements d'abord",
    "Mixers, hikes, speed dating — real rooms.":
        "Mixers, rando, speed dating — de vraies rencontres.",
    "Coach-supported": "Soutenu·e par un coach",
    "A human reviews every profile. Within 48h.":
        "Un humain relit chaque profil. Sous 48 h.",
    "Privacy-first": "Confidentialité d'abord",
    "No public browsing. No swiping feed. Ever.":
        "Pas de parcours public. Pas de feed à swiper. Jamais.",
    "Start building my profile": "Créer mon profil",
    "I'll do this later": "Je ferai ça plus tard",
    # Intent probe options
    "I want to meet people at real events":
        "Je veux rencontrer des gens à de vrais événements",
    "I'm curious but still exploring": "Je suis curieux·se, je découvre encore",
    "I want online dating, events are a bonus":
        "Je veux du dating en ligne, les événements en plus",
    "A friend recommended it": "Un·e ami·e me l'a recommandé",
    # Intent probe responses
    "Perfect — that's exactly what we're built for. Let's get you to your first event.":
        "Parfait — c'est exactement pour ça qu'on est là. On t'emmène à ton premier événement.",
    "Great, we'll walk you through it. You can decide step by step.":
        "Super, on t'accompagne. Tu décides étape par étape.",
    "Honest heads-up: our online features require attending at least one event first. Still want to continue?":
        "Franchement : nos fonctions en ligne exigent de venir à au moins un événement d'abord. Tu veux quand même continuer ?",
    "Love that. Who referred you?": "Cool. Qui te l'a recommandé ?",
    "Learn more": "En savoir plus",
    "Yes, I'm in": "Oui, je suis partant·e",
    "Referral code or name": "Code ou nom de parrainage",
}


def apply(po_path: pathlib.Path, translations: dict[str, str]) -> int:
    """
    Apply translations to a .po file. For each msgid in `translations`:
      - Remove `#, fuzzy` flag line(s) preceding its entry
      - Remove `#| msgid "..."` old-source comment line(s)
      - Replace the msgstr with the provided translation

    Returns count of msgids actually written.
    """
    text = po_path.read_text(encoding="utf-8")
    lines = text.split("\n")
    out = []
    applied = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r'msgid "(.*)"$', line)
        if m is not None:
            # Collect the full msgid, which may be multi-line (msgid "" + string continuations).
            msgid_first = m.group(1)
            msgid_lines = [line]
            k = i + 1
            msgid_parts = [msgid_first]
            while k < len(lines) and lines[k].startswith('"') and lines[k].endswith('"'):
                msgid_lines.append(lines[k])
                msgid_parts.append(lines[k][1:-1])  # strip surrounding quotes
                k += 1
            src_joined = "".join(msgid_parts)
            if src_joined in translations:
                new_msgstr = translations[src_joined].replace("\\", "\\\\").replace('"', '\\"')
                # Strip any preceding fuzzy / hint comment lines we wrote to `out`.
                while out and (out[-1].startswith("#, fuzzy")
                               or out[-1].startswith("#| msgid")
                               or out[-1].startswith("#| msgstr")
                               or out[-1].startswith('#| "')):
                    out.pop()
                # Emit the msgid lines as-is.
                for ml in msgid_lines:
                    out.append(ml)
                i = k
                # Expect msgstr next — replace it (and strip any continuation lines).
                if i < len(lines) and lines[i].startswith("msgstr"):
                    out.append(f'msgstr "{new_msgstr}"')
                    i += 1
                    while i < len(lines) and lines[i].startswith('"'):
                        i += 1
                    applied += 1
                    continue
        out.append(line)
        i += 1

    po_path.write_text("\n".join(out), encoding="utf-8")
    return applied


def main():
    for lang, table in [("de", DE), ("fr", FR)]:
        po = ROOT / "locale" / lang / "LC_MESSAGES" / "django.po"
        n = apply(po, table)
        print(f"{lang}: applied {n}/{len(table)} translations to {po}")


if __name__ == "__main__":
    main()
