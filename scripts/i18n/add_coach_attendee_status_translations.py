"""One-off: DE/FR for the coach event page status tiles and attendee list.

Run with: python scripts/i18n/add_coach_attendee_status_translations.py

2026-11-22: the coach event page (/coach/events/<id>/) now shows one tile per
registration status (checked in, not checked in, pending payment, waitlist,
cancelled, ...) and a compact list that says how each seat was paid and what
happened to its online checkout. Compiles .mo via polib.
"""

import polib

NEW = {
    "Not checked in": {"de": "Nicht eingecheckt", "fr": "Pas enregistré"},
    "Waitlist #%(n)s": {"de": "Warteliste #%(n)s", "fr": "Liste d'attente n°%(n)s"},
    "Paid online": {"de": "Online bezahlt", "fr": "Payé en ligne"},
    "Paid cash": {"de": "Bar bezahlt", "fr": "Payé en espèces"},
    "Paid with credit": {"de": "Mit Guthaben bezahlt", "fr": "Payé avec crédit"},
    "Refunded": {"de": "Erstattet", "fr": "Remboursé"},
    "No online checkout started": {
        "de": "Kein Online-Checkout gestartet",
        "fr": "Aucun paiement en ligne commencé",
    },
    "Checkout open since %(time)s": {
        "de": "Checkout offen seit %(time)s",
        "fr": "Paiement ouvert depuis %(time)s",
    },
    "Checkout abandoned %(time)s": {
        "de": "Checkout abgebrochen %(time)s",
        "fr": "Paiement abandonné %(time)s",
    },
    "Reminder sent %(time)s": {
        "de": "Erinnerung gesendet %(time)s",
        "fr": "Rappel envoyé %(time)s",
    },
    "Seat released: checkout not paid": {
        "de": "Platz freigegeben: Checkout nicht bezahlt",
        "fr": "Place libérée : paiement non effectué",
    },
    "seat, never scanned": {"de": "Platz, nie gescannt", "fr": "place, jamais scanné"},
    "expected at the door": {"de": "am Eingang erwartet", "fr": "attendus à l'entrée"},
    "active registrations": {"de": "aktive Anmeldungen", "fr": "inscriptions actives"},
    "scanned at the door": {"de": "am Eingang gescannt", "fr": "scannés à l'entrée"},
    "seat held, not paid": {
        "de": "Platz reserviert, nicht bezahlt",
        "fr": "place réservée, non payée",
    },
    "seat held": {"de": "Platz reserviert", "fr": "place réservée"},
    "no seat yet": {"de": "noch kein Platz", "fr": "pas encore de place"},
    "awaiting selection": {"de": "wartet auf Auswahl", "fr": "en attente de sélection"},
    "no place given": {"de": "kein Platz vergeben", "fr": "pas de place attribuée"},
    "recorded as no-show": {
        "de": "als No-Show erfasst",
        "fr": "enregistré comme absent",
    },
    "not counted above": {"de": "oben nicht gezählt", "fr": "non comptées ci-dessus"},
    "Free seats": {"de": "Freie Plätze", "fr": "Places libres"},
    "%(held)s of %(max)s held": {
        "de": "%(held)s von %(max)s belegt",
        "fr": "%(held)s sur %(max)s occupées",
    },
    "%(n)s waiting": {"de": "%(n)s wartend", "fr": "%(n)s en attente"},
    "Layout": {"de": "Ansicht", "fr": "Affichage"},
    "List": {"de": "Liste", "fr": "Liste"},
    "Nobody in this group.": {
        "de": "Niemand in dieser Gruppe.",
        "fr": "Personne dans ce groupe.",
    },
    "Never checked in": {"de": "Nie eingecheckt", "fr": "Jamais enregistré"},
    "Cancelled %(time)s": {"de": "Storniert %(time)s", "fr": "Annulé %(time)s"},
    "In at %(time)s": {"de": "Da seit %(time)s", "fr": "Arrivé à %(time)s"},
}


def _quote(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


# Appends raw entries instead of po.save(): polib re-wraps the whole catalog,
# which turns a 30-string change into a 7,000-line diff.
for lang in ("de", "fr"):
    path = f"crush_lu/locale/{lang}/LC_MESSAGES/django.po"
    existing = {e.msgid for e in polib.pofile(path)}
    chunks = []
    for msgid, translations in NEW.items():
        if msgid in existing:
            continue
        flag = "#, python-format\n" if "%(" in msgid else ""
        chunks.append(
            "#: crush_lu/views_coach.py\n"
            f"{flag}msgid {_quote(msgid)}\nmsgstr {_quote(translations[lang])}\n"
        )
    if chunks:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n" + "\n".join(chunks))
    polib.pofile(path).save_as_mofile(path.replace(".po", ".mo"))
    print(f"{lang}: {len(chunks)} added")
