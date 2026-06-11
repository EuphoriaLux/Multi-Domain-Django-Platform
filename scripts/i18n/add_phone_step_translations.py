"""One-off: add DE/FR translations for the redesigned onboarding phone step.

Run with: .venv-1/Scripts/python.exe scripts/add_phone_step_translations.py
Adds entries to crush_lu .po catalogs (informal du/tu register) and compiles
the .mo files directly via polib, bypassing makemessages quirks.
"""

import polib

NEW = {
    "This keeps Crush.lu real and spam-free. Your number stays private — we never show it to other members.": {
        "de": "So bleibt Crush.lu echt und spamfrei. Deine Nummer bleibt privat — wir zeigen sie anderen Mitgliedern nie.",
        "fr": "C'est ce qui garde Crush.lu authentique et sans spam. Ton numéro reste privé — nous ne le montrons jamais aux autres membres.",
    },
    "Recommended": {
        "de": "Empfohlen",
        "fr": "Recommandé",
    },
    "One tap verifies your number and your identity — your profile goes live the moment you submit it. No SMS code, no waiting.": {
        "de": "Ein Klick bestätigt deine Nummer und deine Identität — dein Profil geht in dem Moment live, in dem du es einreichst. Kein SMS-Code, kein Warten.",
        "fr": "Un seul clic vérifie ton numéro et ton identité — ton profil est activé dès que tu le soumets. Pas de code SMS, pas d'attente.",
    },
    "Free for Luxembourg residents via POST. Works when LuxID has your mobile number on file.": {
        "de": "Kostenlos für Einwohner Luxemburgs über POST. Funktioniert, wenn LuxID deine Handynummer hinterlegt hat.",
        "fr": "Gratuit pour les résidents du Luxembourg via POST. Fonctionne si LuxID a ton numéro de portable.",
    },
    "or verify by SMS": {
        "de": "oder per SMS bestätigen",
        "fr": "ou vérifie par SMS",
    },
    "Your LuxID is connected, but it didn't include a mobile number we could use. Please verify your number with an SMS code below.": {
        "de": "Dein LuxID ist verbunden, aber es enthielt keine Handynummer, die wir verwenden können. Bitte bestätige deine Nummer unten mit einem SMS-Code.",
        "fr": "Ton LuxID est connecté, mais il ne contenait pas de numéro de portable utilisable. Vérifie ton numéro ci-dessous avec un code SMS.",
    },
    # create_profile.html — restored-draft notice
    "Welcome back — we restored the answers you hadn't finished saving, so you can pick up right where you left off.": {
        "de": "Willkommen zurück — wir haben deine noch nicht gespeicherten Antworten wiederhergestellt. Du kannst genau da weitermachen, wo du aufgehört hast.",
        "fr": "Te revoilà — nous avons restauré les réponses que tu n'avais pas encore enregistrées : tu peux reprendre exactement où tu en étais.",
    },
    # create_profile.html — auto-save pill (passed to profileWizard via data attrs)
    "Saved just now": {
        "de": "Gerade eben gespeichert",
        "fr": "Enregistré à l'instant",
    },
    "Saved %s ago": {
        "de": "Gespeichert vor %s",
        "fr": "Enregistré il y a %s",
    },
    "Saved earlier": {
        "de": "Zuvor gespeichert",
        "fr": "Enregistré plus tôt",
    },
    # create_profile.html — enriched Review step fallbacks
    "No photos yet": {
        "de": "Noch keine Fotos",
        "fr": "Pas encore de photos",
    },
    "No bio yet (optional)": {
        "de": "Noch keine Bio (optional)",
        "fr": "Pas encore de bio (optionnel)",
    },
    "None selected yet": {
        "de": "Noch nichts ausgewählt",
        "fr": "Rien de sélectionné pour l'instant",
    },
}

OCCURRENCE = ("crush_lu/templates/crush_lu/onboarding/phone.html", "")

for lang in ("de", "fr"):
    path = f"crush_lu/locale/{lang}/LC_MESSAGES/django.po"
    po = polib.pofile(path)
    by_msgid = {e.msgid: e for e in po}
    added, updated = 0, 0
    for msgid, translations in NEW.items():
        entry = by_msgid.get(msgid)
        if entry is None:
            po.append(
                polib.POEntry(
                    msgid=msgid,
                    msgstr=translations[lang],
                    occurrences=[OCCURRENCE],
                )
            )
            added += 1
        elif not entry.msgstr or "fuzzy" in entry.flags:
            entry.msgstr = translations[lang]
            if "fuzzy" in entry.flags:
                entry.flags.remove("fuzzy")
            updated += 1
    po.save(path)
    po.save_as_mofile(path.replace(".po", ".mo"))
    print(f"{lang}: {added} added, {updated} updated, catalog size {len(po)}")
