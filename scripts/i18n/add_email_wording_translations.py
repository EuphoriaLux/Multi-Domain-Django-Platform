"""One-off: DE/FR for the cancellation-refund and support-contact email rewording.

Run with: .venv/Scripts/python.exe scripts/i18n/add_email_wording_translations.py

Two wording fixes changed msgids, which would otherwise drop those lines back to
English for DE/FR readers:

  1. event_cancellation no longer promises an unconditional refund "within 5-7
     business days" (the Terms grant a full refund only >48h before the event),
     and its subject says "Registration Cancelled" rather than "Event
     Cancellation Confirmed", which read as though the event was called off.
  2. Seven templates invited a reply to an email sent from noreply@crush.lu.
     They now point at support@crush.lu, matching base_email's footer.

Register is DE du, FR vous, measured against the catalogs rather than sampled:
DE is 88% du-only (1426 vs 195), FR is 90% vous-only overall and 218-vs-24
vous within the email templates specifically. A handful of FR email strings do
use tu ("Nous espérons te voir à un événement futur !") -- those are the
minority and should be migrated, not copied. The address itself is kept
outside the translatable string so it cannot be mangled in translation.

Compiles .mo directly via polib -- gettext is not installed on this machine and
a hand-rolled .mo 500s every DE/FR request.
"""

import polib

NEW = {
    # --- event_cancellation: subject + refund line -------------------------
    "Registration Cancelled - {title}": {
        "de": "Anmeldung storniert - {title}",
        "fr": "Inscription annulée - {title}",
    },
    (
        "If you paid a registration fee, any refund is handled under our "
        "cancellation policy and we'll be in touch about it."
    ): {
        "de": (
            "Falls du eine Teilnahmegebühr bezahlt hast, richtet sich eine "
            "mögliche Rückerstattung nach unseren Stornierungsbedingungen — "
            "wir melden uns dazu bei dir."
        ),
        "fr": (
            "Si vous avez payé des frais d'inscription, tout remboursement "
            "éventuel suit notre politique d'annulation — nous vous "
            "recontacterons à ce sujet."
        ),
    },
    "Read the cancellation policy": {
        "de": "Stornierungsbedingungen lesen",
        "fr": "Lire la politique d'annulation",
    },
    # --- support contact, replacing "reply to this email" ------------------
    "Email us at": {
        "de": "Schreib uns an",
        "fr": "Écrivez-nous à",
    },
    "Questions? Email us at": {
        "de": "Fragen? Schreib uns an",
        "fr": "Des questions ? Écrivez-nous à",
    },
    "Questions? We'll help you out — email us at": {
        "de": "Fragen? Wir helfen dir gerne weiter — schreib uns an",
        "fr": "Des questions ? Nous vous aidons volontiers — écrivez-nous à",
    },
    "Have questions or need help? Email us at": {
        "de": "Fragen oder brauchst du Hilfe? Schreib uns an",
        "fr": "Des questions ou besoin d'aide ? Écrivez-nous à",
    },
    "If you have any questions, email us at": {
        "de": "Bei Fragen schreib uns an",
        "fr": "Si vous avez des questions, écrivez-nous à",
    },
    "Our team will get back to you — email us at": {
        "de": "Unser Team meldet sich bei dir — schreib uns an",
        "fr": "Notre équipe vous répondra — écrivez-nous à",
    },
}

OCCURRENCE = ("crush_lu/templates/crush_lu/emails/event_cancellation.html", "")

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
