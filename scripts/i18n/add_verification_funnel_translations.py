"""One-off: DE/FR for the verification-funnel copy correction.

Run with: .venv/Scripts/python.exe scripts/i18n/add_verification_funnel_translations.py

Several emails still described the old review-and-approve funnel as the path
every member takes. It isn't: a fresh submit creates no ProfileSubmission at
all, and LuxID / event-verified members never get one (views_profile.py:742,
views.py:797, seed_debug_profiles.py:381). The coach review, the screening call
and the approve/reject mails ride the *paid Premium* path only.

So these strings changed:
  - profile_submission_confirmation: "While you wait:" / "We'll be in touch
    soon!" -- nobody is going to be in touch in the general funnel, and the
    same email already says verification involves no waiting.
  - profile_incomplete_final: "goes to our review team", "pick a coach and
    submit" (the latter also contradicted the 24h reminder's step 3).
  - external_guest_invitation: steps 3 and 4 promised review-then-approve
    before you may register, plus "human-reviewed profiles" in the footer.
  - profile_recontact: claimed the screening call is what unlocks event
    registration. It never was.

Register is DE du, FR vous -- see add_email_wording_translations.py for the
measurement. Compiles .mo via polib (no gettext on the dev box).
"""

import polib

NEW = {
    # --- profile_submission_confirmation -----------------------------------
    "Good next steps:": {
        "de": "Gute nächste Schritte:",
        "fr": "Prochaines étapes :",
    },
    "See you at an event!": {
        "de": "Wir sehen uns auf einem Event!",
        "fr": "À bientôt lors d'un événement !",
    },
    # --- profile_incomplete_final ------------------------------------------
    (
        "You're literally one step away! Just add a photo or two and you're "
        "ready to get verified."
    ): {
        "de": (
            "Du bist buchstäblich einen Schritt entfernt! Füge ein bis zwei "
            "Fotos hinzu und du kannst dich verifizieren lassen."
        ),
        "fr": (
            "Vous n'êtes qu'à une étape ! Ajoutez une ou deux photos et vous "
            "êtes prêt à vous faire vérifier."
        ),
    },
    (
        "You've done the hard part! Add your preferences and submit — then get "
        "verified at an event or with LuxID."
    ): {
        "de": (
            "Das Schwerste hast du geschafft! Ergänze deine Präferenzen und "
            "reiche ein — danach lässt du dich auf einem Event oder mit LuxID "
            "verifizieren."
        ),
        "fr": (
            "Vous avez fait le plus dur ! Ajoutez vos préférences et soumettez "
            "votre profil — ensuite, faites-vous vérifier lors d'un événement "
            "ou avec LuxID."
        ),
    },
    # --- external_guest_invitation -----------------------------------------
    (
        "3. Complete your profile — verify instantly with LuxID, or in person "
        "when you arrive"
    ): {
        "de": (
            "3. Vervollständige dein Profil — sofort mit LuxID verifizieren "
            "oder persönlich bei deiner Ankunft"
        ),
        "fr": (
            "3. Complétez votre profil — vérification instantanée avec LuxID, "
            "ou en personne à votre arrivée"
        ),
    },
    "4. Register for the event": {
        "de": "4. Melde dich für das Event an",
        "fr": "4. Inscrivez-vous à l'événement",
    },
    (
        "Crush.lu is Luxembourg's privacy-first dating platform focused on "
        "real connections through in-person events. We prioritize quality over "
        "quantity, with every member verified and exclusive events."
    ): {
        "de": (
            "Crush.lu ist Luxemburgs datenschutzorientierte Dating-Plattform "
            "für echte Begegnungen bei Events vor Ort. Wir setzen auf Qualität "
            "statt Quantität — mit verifizierten Mitgliedern und exklusiven "
            "Events."
        ),
        "fr": (
            "Crush.lu est la plateforme de rencontres du Luxembourg axée sur "
            "la confidentialité et les rencontres réelles lors d'événements en "
            "personne. Nous privilégions la qualité à la quantité, avec des "
            "membres tous vérifiés et des événements exclusifs."
        ),
    },
    # --- profile_recontact (paid Premium coach-review path) ----------------
    (
        "You can browse and register for events any time — the call is about "
        "your Crush Coach getting to know you, so they can pick better matches "
        "for you."
    ): {
        "de": (
            "Du kannst dich jederzeit für Events anmelden — bei dem Gespräch "
            "geht es darum, dass dein Crush Coach dich kennenlernt und dir "
            "bessere Matches aussuchen kann."
        ),
        "fr": (
            "Vous pouvez parcourir et réserver des événements à tout moment — "
            "l'appel sert à ce que votre Crush Coach apprenne à vous connaître "
            "et vous propose de meilleurs profils."
        ),
    },
}

OCCURRENCE = (
    "crush_lu/templates/crush_lu/emails/profile_submission_confirmation.html",
    "",
)

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
        elif entry.msgstr != translations[lang] or "fuzzy" in entry.flags:
            # This script owns these msgids, so it overwrites rather than skips.
            entry.msgstr = translations[lang]
            if "fuzzy" in entry.flags:
                entry.flags.remove("fuzzy")
            updated += 1
    po.save(path)
    po.save_as_mofile(path.replace(".po", ".mo"))
    print(f"{lang}: {added} added, {updated} updated, catalog size {len(po)}")
