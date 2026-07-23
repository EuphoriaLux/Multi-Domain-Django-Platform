"""Phase E: add DE/FR translations for the Event Identity redesign strings.

Appends msgid/msgstr entries to crush_lu/locale/{de,fr}/LC_MESSAGES/django.po
and rebuilds the .mo with polib (repo convention — no gettext on the dev box).

DE uses du-form, FR vous-form, matching the existing catalogs.
Idempotent: skips msgids that already have a translation.
"""

from pathlib import Path

import polib

ROOT = Path(__file__).resolve().parents[2]

# msgid -> (de, fr)
TRANSLATIONS = {
    # --- User-facing: edit card / wizard / surfaces -------------------------
    "Your Event Identity": (
        "Deine Event-Identität",
        "Votre identité événement",
    ),
    "Event Identity": (
        "Event-Identität",
        "Identité événement",
    ),
    "Your Event Identity was updated!": (
        "Deine Event-Identität wurde aktualisiert!",
        "Votre identité événement a été mise à jour !",
    ),
    "This is what people will discover about you at our events — no long bio needed.": (
        "Das werden die Leute bei unseren Events über dich entdecken — ganz ohne lange Bio.",
        "Voici ce que les gens découvriront de vous lors de nos événements — pas besoin de longue bio.",
    ),
    "This is what people will discover about you at our events — no long bio needed. (All optional.)": (
        "Das werden die Leute bei unseren Events über dich entdecken — ganz ohne lange Bio. (Alles optional.)",
        "Voici ce que les gens découvriront de vous lors de nos événements — pas besoin de longue bio. (Tout est facultatif.)",
    ),
    "Vibe, interests & conversation starters — what people discover at events.": (
        "Vibe, Interessen & Gesprächseinstiege — was die Leute bei Events über dich entdecken.",
        "Ambiance, centres d'intérêt et amorces de conversation — ce que les gens découvrent de vous en événement.",
    ),
    "Your vibe": (
        "Dein Vibe",
        "Votre ambiance",
    ),
    "Pick up to 5 qualities and up to 5 things you own up to.": (
        "Wähle bis zu 5 Stärken und bis zu 5 Dinge, zu denen du stehst.",
        "Choisissez jusqu'à 5 qualités et jusqu'à 5 défauts que vous assumez.",
    ),
    "Your interests": (
        "Deine Interessen",
        "Vos centres d'intérêt",
    ),
    "Pick up to 8 — these appear as chips at events.": (
        "Wähle bis zu 8 — sie erscheinen als Chips bei Events.",
        "Choisissez-en jusqu'à 8 — ils apparaîtront sous forme de badges lors des événements.",
    ),
    "Ask me about…": (
        "Frag mich zu…",
        "Parlez-moi de…",
    ),
    "Ask me about": (
        "Frag mich zu",
        "Parlez-moi de",
    ),
    "Highlight up to 3 of your interests as conversation starters.": (
        "Hebe bis zu 3 deiner Interessen als Gesprächseinstiege hervor.",
        "Mettez en avant jusqu'à 3 de vos centres d'intérêt comme amorces de conversation.",
    ),
    "Select some interests above first.": (
        "Wähle zuerst oben einige Interessen aus.",
        "Sélectionnez d'abord quelques centres d'intérêt ci-dessus.",
    ),
    "Nothing selected yet (optional)": (
        "Noch nichts ausgewählt (optional)",
        "Rien de sélectionné pour l'instant (facultatif)",
    ),
    "My event vibe": (
        "Meine Event-Stimmung",
        "Mon ambiance en événement",
    ),
    "Pick the one that sounds most like you at an event.": (
        "Wähle die, die dir bei einem Event am meisten entspricht.",
        "Choisissez celle qui vous ressemble le plus en événement.",
    ),
    "Skip for now": (
        "Vorerst überspringen",
        "Passer pour l'instant",
    ),
    "Legacy bio & interests (pre-2026 redesign)": (
        "Alte Bio & Interessen (vor dem Redesign 2026)",
        "Ancienne bio et intérêts (avant la refonte 2026)",
    ),
    # --- edit_profile IA cards (Phase C page, adjacent headings) ------------
    "Name visibility and age display": (
        "Namens- und Altersanzeige",
        "Affichage du nom et de l'âge",
    ),
    "Phone number & region": (
        "Telefonnummer & Region",
        "Numéro de téléphone et région",
    ),
    # --- Event vibe choices (model) ------------------------------------------
    "First one on the dance floor": (
        "Zuerst auf der Tanzfläche",
        "Premier sur la piste de danse",
    ),
    "Quiet corner conversations": (
        "Gespräche in der ruhigen Ecke",
        "Conversations tranquilles dans un coin",
    ),
    "Here to meet everyone": (
        "Hier, um alle kennenzulernen",
        "Là pour rencontrer tout le monde",
    ),
    "Dragged along by friends": (
        "Von Freunden mitgeschleppt",
        "Traîné par des amis",
    ),
    # --- Form validation errors ----------------------------------------------
    "Invalid conversation-starter selection.": (
        "Ungültige Auswahl der Gesprächseinstiege.",
        "Sélection d'amorces de conversation invalide.",
    ),
    "Pick each conversation starter once.": (
        "Wähle jeden Gesprächseinstieg nur einmal.",
        "Choisissez chaque amorce de conversation une seule fois.",
    ),
    "Pick at most %(max)d conversation starters.": (
        "Wähle höchstens %(max)d Gesprächseinstiege.",
        "Choisissez au maximum %(max)d amorces de conversation.",
    ),
    "Select at least one language you can speak at events.": (
        "Wähle mindestens eine Sprache, die du bei Events sprechen kannst.",
        "Sélectionnez au moins une langue que vous pouvez parler lors des événements.",
    ),
    "“Ask me about” items must be among your selected interests.": (
        "„Frag mich zu“-Einträge müssen unter deinen ausgewählten Interessen sein.",
        "Les éléments « Parlez-moi de » doivent faire partie de vos centres d'intérêt sélectionnés.",
    ),
    # --- Model help texts (admin/coach tooling) -------------------------------
    "Curated event interests (max 8) — replaces free-text interests": (
        "Kuratierte Event-Interessen (max. 8) — ersetzt die Freitext-Interessen",
        "Centres d'intérêt d'événement sélectionnés (max. 8) — remplace les intérêts en texte libre",
    ),
    "Up to 3 interest ids to highlight as conversation starters": (
        "Bis zu 3 Interessen-IDs, die als Gesprächseinstiege hervorgehoben werden",
        "Jusqu'à 3 identifiants d'intérêts à mettre en avant comme amorces de conversation",
    ),
    "Your event vibe — one chip shown on event surfaces": (
        "Deine Event-Stimmung — ein Chip, der auf Event-Seiten angezeigt wird",
        "Votre ambiance en événement — un badge affiché sur les pages d'événement",
    ),
    "DEPRECATED (Event Identity redesign): legacy free-text bio, coach-visible only": (
        "VERALTET (Event-Identity-Redesign): alte Freitext-Bio, nur für Coaches sichtbar",
        "OBSOLÈTE (refonte Event Identity) : ancienne bio en texte libre, visible par les coachs uniquement",
    ),
    "DEPRECATED (Event Identity redesign): legacy free-text interests, coach-visible only": (
        "VERALTET (Event-Identity-Redesign): alte Freitext-Interessen, nur für Coaches sichtbar",
        "OBSOLÈTE (refonte Event Identity) : anciens intérêts en texte libre, visibles par les coachs uniquement",
    ),
}


def main():
    for lang_index, lang in enumerate(("de", "fr")):
        po_path = ROOT / f"crush_lu/locale/{lang}/LC_MESSAGES/django.po"
        mo_path = po_path.with_suffix(".mo")
        po = polib.pofile(str(po_path))
        added, skipped = 0, 0
        for msgid, pair in TRANSLATIONS.items():
            msgstr = pair[lang_index]
            existing = po.find(msgid)
            if existing is not None:
                if existing.msgstr:
                    skipped += 1
                    continue
                existing.msgstr = msgstr
                added += 1
                continue
            entry = polib.POEntry(msgid=msgid, msgstr=msgstr)
            po.append(entry)
            added += 1
        po.save(str(po_path))
        po.save_as_mofile(str(mo_path))
        print(
            f"{lang}: +{added} entries ({skipped} already translated) -> {po_path.name} + django.mo"
        )


if __name__ == "__main__":
    main()
