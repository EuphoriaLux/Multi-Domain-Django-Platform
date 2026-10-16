"""One-off: DE/FR for the coach "Mark paid (cash)" door action.

Run with: .venv/Scripts/python.exe scripts/i18n/add_cash_payment_translations.py
Appends missing entries to the crush_lu django.po catalogs as text (polib's
save() re-wraps the whole catalogue). Coach-facing, so the wording avoids a
direct address in DE, where the coach panel mixes "Sie" and "du"; FR "vous".
The .mo files are built by scripts/build_assets.py.
"""

import polib

NEW = {
    "This event is cancelled.": {
        "de": "Dieses Event ist abgesagt.",
        "fr": "Cet événement est annulé.",
    },
    "This event is free; there is nothing to pay.": {
        "de": "Dieses Event ist kostenlos, es gibt nichts zu bezahlen.",
        "fr": "Cet événement est gratuit, il n'y a rien à payer.",
    },
    "Payment can only be recorded for someone holding a seat.": {
        "de": "Eine Zahlung kann nur für jemanden mit reserviertem Platz erfasst werden.",
        "fr": "Un paiement ne peut être enregistré que pour une personne ayant une place.",
    },
    "This member still has an open card checkout that could not be closed. Recording cash now could charge them twice; ask staff to check it.": {
        "de": "Dieses Mitglied hat noch eine offene Kartenzahlung, die nicht geschlossen werden konnte. Eine Barzahlung jetzt könnte doppelt kassieren; bitte das Team prüfen lassen.",
        "fr": "Ce membre a encore un paiement par carte ouvert qui n'a pas pu être fermé. Enregistrer les espèces maintenant pourrait le faire payer deux fois ; demandez à l'équipe de vérifier.",
    },
    "This curated seat no longer belongs to a complete group. Ask staff to repair the group first.": {
        "de": "Dieser kuratierte Platz gehört zu keiner vollständigen Gruppe mehr. Bitte zuerst die Gruppe vom Team reparieren lassen.",
        "fr": "Cette place sélectionnée n'appartient plus à un groupe complet. Demandez d'abord à l'équipe de réparer le groupe.",
    },
    "Cash payment not recorded: %(reason)s": {
        "de": "Barzahlung nicht erfasst: %(reason)s",
        "fr": "Paiement en espèces non enregistré : %(reason)s",
    },
    "Cash payment recorded for %(name)s.": {
        "de": "Barzahlung für %(name)s erfasst.",
        "fr": "Paiement en espèces enregistré pour %(name)s.",
    },
    "Unpaid · €%(amount)s": {
        "de": "Unbezahlt · %(amount)s €",
        "fr": "Non payé · %(amount)s €",
    },
    "Mark paid (cash)": {
        "de": "Als bezahlt markieren (bar)",
        "fr": "Marquer payé (espèces)",
    },
}


def _quote(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


for lang in ("de", "fr"):
    path = f"crush_lu/locale/{lang}/LC_MESSAGES/django.po"
    po = polib.pofile(path)
    blocks = []
    for msgid, translations in NEW.items():
        if po.find(msgid) is not None:
            continue
        flag = "#, python-format\n" if "%(" in msgid else ""
        blocks.append(
            f"{flag}msgid {_quote(msgid)}\nmsgstr {_quote(translations[lang])}\n"
        )
    if blocks:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n" + "\n".join(blocks))
    print(f"{lang}: {len(blocks)} added")
