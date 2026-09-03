"""One-off: EN/DE/FR catalog entries for the coach "Groups" panel.

Run with: .venv/Scripts/python.exe scripts/i18n/add_curated_insights_translations.py
Check only (no writes): add --check

Adds the strings introduced by the read-only curated-group insight panel on
the coach event page (``components/coach_curated_groups.html``,
``services/curated_group_insights.py`` and the "Groups" filter tab).

Register follows the catalogs: DE du, FR vous. The admin action names inside
the quotation marks stay in English in every language on purpose: the coach
panel (Django admin) shows them untranslated, and a translated name would not
be found in its actions dropdown.

There is no ``makemessages`` on this machine, so every msgid below is typed by
hand. ``--check`` extracts every ``trans``/``blocktrans`` body from the partial
and every ``_()``/``ngettext()`` literal from the module and refuses to run
when the two lists disagree: a mistyped msgid would otherwise silently fall
back to English on the DE/FR page.

Compiles the DE/FR .mo directly via polib -- gettext is not installed here and
a hand-rolled .mo 500s every DE/FR request. EN gets the msgids with an empty
msgstr, matching how every other EN entry is recorded. Plural entries carry
``msgid_plural`` and a two-form ``msgstr_plural`` (both catalogs declare
``nplurals=2``).
"""

import ast
import re
import sys

import polib

PANEL = ("templates/crush_lu/components/coach_curated_groups.html", "")
MODULE = ("services/curated_group_insights.py", "")
TAB = ("templates/crush_lu/coach_event_detail.html", "")

# msgid -> {"occ", "de", "fr"} or, for plurals, {"plural", "occ", "de": (one,
# many), "fr": (one, many)}.
NEW = {
    # -- headings, banner -------------------------------------------------
    "Groups": {"occ": TAB, "de": "Gruppen", "fr": "Groupes"},
    "Curated groups": {
        "occ": PANEL,
        "de": "Kuratierte Gruppen",
        "fr": "Groupes sur sélection",
    },
    "generation %(generation)s": {
        "occ": PANEL,
        "de": "Generation %(generation)s",
        "fr": "génération %(generation)s",
    },
    "Generation %(generation)s is degraded and needs repair": {
        "occ": PANEL,
        "de": "Generation %(generation)s ist beeinträchtigt und braucht eine Reparatur",
        "fr": "La génération %(generation)s est dégradée et doit être réparée",
    },
    "Round one has started": {
        "occ": PANEL,
        "de": "Runde eins hat begonnen",
        "fr": "La première ronde a commencé",
    },
    "Generation %(generation)s is locked: final evening roster": {
        "occ": PANEL,
        "de": "Generation %(generation)s ist gesperrt: endgültige Besetzung des Abends",
        "fr": (
            "La génération %(generation)s est verrouillée : composition "
            "définitive de la soirée"
        ),
    },
    "Generation %(generation)s is provisional: selected and payable": {
        "occ": PANEL,
        "de": "Generation %(generation)s ist vorläufig: ausgewählt und zahlbar",
        "fr": "La génération %(generation)s est provisoire : sélectionnée et payable",
    },
    "Generation %(generation)s is a draft": {
        "occ": PANEL,
        "de": "Generation %(generation)s ist ein Entwurf",
        "fr": "La génération %(generation)s est un brouillon",
    },
    "No groups generated yet": {
        "occ": PANEL,
        "de": "Noch keine Gruppen erstellt",
        "fr": "Aucun groupe généré pour l’instant",
    },
    "Applications closed %(deadline)s": {
        "occ": PANEL,
        "de": "Bewerbungen geschlossen seit %(deadline)s",
        "fr": "Candidatures closes depuis le %(deadline)s",
    },
    "Applications close %(deadline)s": {
        "occ": PANEL,
        "de": "Bewerbungen schließen am %(deadline)s",
        "fr": "Les candidatures ferment le %(deadline)s",
    },
    "Round one was already marked as started.": {
        "occ": PANEL,
        "de": "Runde eins wurde bereits als gestartet markiert.",
        "fr": "La première ronde a déjà été marquée comme commencée.",
    },
    "Next step:": {"occ": PANEL, "de": "Nächster Schritt:", "fr": "Prochaine étape :"},
    "Open the coach panel": {
        "occ": PANEL,
        "de": "Coach-Panel öffnen",
        "fr": "Ouvrir le panel coach",
    },
    # -- preflight ----------------------------------------------------------
    "What the projector would do with the pool right now": {
        "occ": PANEL,
        "de": "Was der Projektor mit dem aktuellen Pool machen würde",
        "fr": "Ce que le projecteur ferait du pool en ce moment",
    },
    "Draft is stale": {
        "occ": PANEL,
        "de": "Entwurf veraltet",
        "fr": "Brouillon obsolète",
    },
    "The projector refused this pool: %(error)s": {
        "occ": PANEL,
        "de": "Der Projektor hat diesen Pool abgelehnt: %(error)s",
        "fr": "Le projecteur a refusé ce pool : %(error)s",
    },
    "Applications": {"occ": PANEL, "de": "Bewerbungen", "fr": "Candidatures"},
    "Viable groups": {
        "occ": PANEL,
        "de": "Tragfähige Gruppen",
        "fr": "Groupes viables",
    },
    "Would be left out": {
        "occ": PANEL,
        "de": "Blieben ohne Platz",
        "fr": "Resteraient sans place",
    },
    # -- group cards --------------------------------------------------------
    "Group %(number)s": {
        "occ": PANEL,
        "de": "Gruppe %(number)s",
        "fr": "Groupe %(number)s",
    },
    "Provisional": {"occ": PANEL, "de": "Vorläufig", "fr": "Provisoire"},
    "Degraded": {"occ": PANEL, "de": "Beeinträchtigt", "fr": "Dégradé"},
    "was locked": {"occ": PANEL, "de": "war gesperrt", "fr": "était verrouillé"},
    "was provisional": {"occ": PANEL, "de": "war vorläufig", "fr": "était provisoire"},
    "%(counter)s round": {
        "plural": "%(counter)s rounds",
        "occ": PANEL,
        "de": ("%(counter)s Runde", "%(counter)s Runden"),
        "fr": ("%(counter)s ronde", "%(counter)s rondes"),
    },
    "everyone meets the %(target)s-date target": {
        "occ": PANEL,
        "de": "alle erreichen das Ziel von %(target)s Dates",
        "fr": "tout le monde atteint l’objectif de %(target)s rencontres",
    },
    "%(meeting)s of %(members)s meet the %(target)s-date target": {
        "occ": PANEL,
        "de": "%(meeting)s von %(members)s erreichen das Ziel von %(target)s Dates",
        "fr": "%(meeting)s sur %(members)s atteignent l’objectif de %(target)s rencontres",
    },
    "at least %(minimum)s dates each": {
        "occ": PANEL,
        "de": "mindestens %(minimum)s Dates pro Person",
        "fr": "au moins %(minimum)s rencontres par personne",
    },
    "survives one drop-out": {
        "occ": PANEL,
        "de": "übersteht einen Ausfall",
        "fr": "résiste à un désistement",
    },
    "does not survive a drop-out": {
        "occ": PANEL,
        "de": "übersteht keinen Ausfall",
        "fr": "ne résiste pas à un désistement",
    },
    "%(counter)s mini-date": {
        "plural": "%(counter)s mini-dates",
        "occ": PANEL,
        "de": ("%(counter)s Mini-Date", "%(counter)s Mini-Dates"),
        "fr": ("%(counter)s mini-rencontre", "%(counter)s mini-rencontres"),
    },
    "Released": {"occ": PANEL, "de": "Freigegeben", "fr": "Libéré·e"},
    "Why this group": {
        "occ": PANEL,
        "de": "Warum diese Gruppe",
        "fr": "Pourquoi ce groupe",
    },
    "Show pairing schedule": {
        "occ": PANEL,
        "de": "Tischplan anzeigen",
        "fr": "Afficher le plan des tables",
    },
    "Round": {"occ": PANEL, "de": "Runde", "fr": "Ronde"},
    "Table %(table)s": {"occ": PANEL, "de": "Tisch %(table)s", "fr": "Table %(table)s"},
    "Sitting out": {"occ": PANEL, "de": "Setzt aus", "fr": "En pause"},
    # -- left out -----------------------------------------------------------
    "Left out": {"occ": PANEL, "de": "Ohne Platz", "fr": "Sans place"},
    "Nobody is left out.": {
        "occ": PANEL,
        "de": "Niemand bleibt ohne Platz.",
        "fr": "Personne ne reste sans place.",
    },
    "Cannot be placed": {
        "occ": PANEL,
        "de": "Kann nicht platziert werden",
        "fr": "Ne peut pas être placé·e",
    },
    "Eligible but not placed": {
        "occ": PANEL,
        "de": "Geeignet, aber nicht platziert",
        "fr": "Éligible mais sans place",
    },
    # -- next-action sentences (module) -------------------------------------
    "Wait for the application deadline, then run “Generate fair curated groups”.": {
        "occ": MODULE,
        "de": (
            "Warte auf die Bewerbungsfrist und führe dann „Generate fair curated "
            "groups“ aus."
        ),
        "fr": (
            "Attendez la date limite de candidature, puis lancez « Generate fair "
            "curated groups »."
        ),
    },
    "Run “Generate fair curated groups”.": {
        "occ": MODULE,
        "de": "Führe „Generate fair curated groups“ aus.",
        "fr": "Lancez « Generate fair curated groups ».",
    },
    (
        "Run “Generate fair curated groups” again: the pool or the deadline "
        "changed since this draft, so approval would be refused."
    ): {
        "occ": MODULE,
        "de": (
            "Führe „Generate fair curated groups“ erneut aus: Der Pool oder die "
            "Frist hat sich seit diesem Entwurf geändert, die Freigabe würde "
            "abgelehnt."
        ),
        "fr": (
            "Relancez « Generate fair curated groups » : le pool ou la date "
            "limite a changé depuis ce brouillon, l’approbation serait refusée."
        ),
    },
    "Review the draft, then run “Approve all current fair groups”.": {
        "occ": MODULE,
        "de": "Prüfe den Entwurf und führe dann „Approve all current fair groups“ aus.",
        "fr": "Vérifiez le brouillon, puis lancez « Approve all current fair groups ».",
    },
    "Run “Invite the approved generation to pay”.": {
        "occ": MODULE,
        "de": "Führe „Invite the approved generation to pay“ aus.",
        "fr": "Lancez « Invite the approved generation to pay ».",
    },
    "Check every member in at the door, then run “Lock checked-in curated groups”.": {
        "occ": MODULE,
        "de": (
            "Checke alle Mitglieder am Eingang ein und führe dann „Lock checked-in "
            "curated groups“ aus."
        ),
        "fr": (
            "Enregistrez chaque membre à l’entrée, puis lancez « Lock checked-in "
            "curated groups »."
        ),
    },
    "Run “Mark curated round one as started” as the first round begins.": {
        "occ": MODULE,
        "de": (
            "Führe „Mark curated round one as started“ aus, sobald die erste Runde "
            "beginnt."
        ),
        "fr": (
            "Lancez « Mark curated round one as started » au début de la première "
            "ronde."
        ),
    },
    "Round one has been marked as started. Nothing left to do here.": {
        "occ": MODULE,
        "de": "Runde eins wurde als gestartet markiert. Hier gibt es nichts mehr zu tun.",
        "fr": "La première ronde a été marquée comme commencée. Plus rien à faire ici.",
    },
    "Run “Reproject or compensate degraded groups”.": {
        "occ": MODULE,
        "de": "Führe „Reproject or compensate degraded groups“ aus.",
        "fr": "Lancez « Reproject or compensate degraded groups ».",
    },
    "The event is cancelled; no group action applies.": {
        "occ": MODULE,
        "de": "Die Veranstaltung ist abgesagt; keine Gruppenaktion nötig.",
        "fr": "L’événement est annulé ; aucune action de groupe ne s’applique.",
    },
    # -- ineligibility reasons (module) -------------------------------------
    "no event preferences on the application": {
        "occ": MODULE,
        "de": "keine Event-Präferenzen in der Bewerbung",
        "fr": "aucune préférence d’événement dans la candidature",
    },
    "no gender on the profile": {
        "occ": MODULE,
        "de": "kein Geschlecht im Profil",
        "fr": "aucun genre dans le profil",
    },
    "no date of birth on the profile": {
        "occ": MODULE,
        "de": "kein Geburtsdatum im Profil",
        "fr": "aucune date de naissance dans le profil",
    },
    "outside the event's age range": {
        "occ": MODULE,
        "de": "außerhalb der Altersspanne der Veranstaltung",
        "fr": "hors de la tranche d’âge de l’événement",
    },
    "incomplete application": {
        "occ": MODULE,
        "de": "unvollständige Bewerbung",
        "fr": "candidature incomplète",
    },
    # -- "why this group" (module) ------------------------------------------
    (
        "Its members form a compatibility track of their own: no other applicant "
        "was mutually compatible with anyone in it."
    ): {
        "occ": MODULE,
        "de": (
            "Ihre Mitglieder bilden einen eigenen Kompatibilitätsstrang: Keine "
            "andere Person aus dem Pool passte gegenseitig zu jemandem darin."
        ),
        "fr": (
            "Ses membres forment leur propre bloc de compatibilité : aucun autre "
            "candidat n’était mutuellement compatible avec l’un d’eux."
        ),
    },
    (
        "Drawn from a compatibility track of %(track)d mutually compatible "
        "applicants; group %(ordinal)d within that track."
    ): {
        "occ": MODULE,
        "de": (
            "Aus einem Kompatibilitätsstrang von %(track)d gegenseitig passenden "
            "Personen gebildet; Gruppe %(ordinal)d in diesem Strang."
        ),
        "fr": (
            "Issu d’un bloc de compatibilité de %(track)d candidats mutuellement "
            "compatibles ; groupe %(ordinal)d de ce bloc."
        ),
    },
    "Prioritised because its members had few alternative groups in the pool.": {
        "occ": MODULE,
        "de": "Bevorzugt, weil ihre Mitglieder im Pool nur wenige andere Gruppen hatten.",
        "fr": (
            "Priorisé parce que ses membres avaient peu d’autres groupes possibles "
            "dans le pool."
        ),
    },
    "%(count)d member already held a seat when this projection ran.": {
        "plural": "%(count)d members already held a seat when this projection ran.",
        "occ": MODULE,
        "de": (
            "%(count)d Mitglied hatte bei dieser Berechnung bereits einen Platz.",
            "%(count)d Mitglieder hatten bei dieser Berechnung bereits einen Platz.",
        ),
        "fr": (
            "%(count)d membre avait déjà une place lors de ce calcul.",
            "%(count)d membres avaient déjà une place lors de ce calcul.",
        ),
    },
}


def _template_msgids(path):
    text = open(path, encoding="utf-8").read()
    found = set()
    for match in re.finditer(r"{%\s*trans\s+(['\"])(.*?)\1\s*%}", text):
        found.add((match.group(2), None))
    for match in re.finditer(
        r"{%\s*blocktrans\b[^%]*%}(.*?){%\s*endblocktrans\s*%}", text, re.S
    ):
        body = match.group(1)
        singular, plural = (
            body.split("{% plural %}") if "{% plural %}" in body else (body, None)
        )

        def convert(fragment):
            return re.sub(r"{{\s*(\w+)\s*}}", r"%(\1)s", fragment)

        found.add((convert(singular), convert(plural) if plural else None))
    return found


def _module_msgids(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    found = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id == "_" and isinstance(node.args[0], ast.Constant):
            found.add((node.args[0].value, None))
        elif node.func.id == "ngettext":
            found.add((node.args[0].value, node.args[1].value))
    return found


def check():
    """Refuse to run when the sources and this table disagree."""

    existing = {
        entry.msgid
        for entry in polib.pofile("crush_lu/locale/en/LC_MESSAGES/django.po")
    }
    extracted = _template_msgids("crush_lu/" + PANEL[0]) | _module_msgids(
        "crush_lu/" + MODULE[0]
    )
    extracted_new = {pair for pair in extracted if pair[0] not in existing}
    declared = {(msgid, spec.get("plural")) for msgid, spec in NEW.items()}
    # The "Groups" tab lives in coach_event_detail.html, which carries dozens
    # of untranslated organiser strings this script does not own.
    declared_from_sources = declared - {("Groups", None)}
    missing = extracted_new - declared_from_sources
    orphans = (
        declared_from_sources
        - extracted_new
        - {pair for pair in declared_from_sources if pair[0] in existing}
    )
    problems = []
    if missing:
        problems.append(f"in sources but not in NEW: {sorted(missing)}")
    if orphans:
        problems.append(f"in NEW but not in sources: {sorted(orphans)}")
    if problems:
        sys.exit("\n".join(problems))
    print(f"check: {len(declared)} entries match the sources")


def main():
    for lang in ("en", "de", "fr"):
        path = f"crush_lu/locale/{lang}/LC_MESSAGES/django.po"
        existing = {entry.msgid for entry in polib.pofile(path)}
        # Append as text rather than ``po.save()``: polib re-wraps every long
        # entry it touches, which turns a sixty-string change into a
        # several-hundred-line diff. gettext accepts entries in any order.
        with open(path, "rb") as handle:
            raw = handle.read()
        newline = "\r\n" if b"\r\n" in raw[:2000] else "\n"
        chunks = []
        for msgid, spec in NEW.items():
            if msgid in existing:
                continue
            plural = spec.get("plural")
            if plural:
                forms = ("", "") if lang == "en" else spec[lang]
                entry = polib.POEntry(
                    msgid=msgid,
                    msgid_plural=plural,
                    msgstr_plural={0: forms[0], 1: forms[1]},
                    occurrences=[spec["occ"]],
                )
            else:
                entry = polib.POEntry(
                    msgid=msgid,
                    msgstr="" if lang == "en" else spec[lang],
                    occurrences=[spec["occ"]],
                )
            chunks.append(str(entry).replace("\n", newline))
        if chunks:
            if not raw.endswith(newline.encode()):
                raw += newline.encode()
            raw += newline.encode().join(chunk.encode("utf-8") for chunk in chunks)
            with open(path, "wb") as handle:
                handle.write(raw)
        po = polib.pofile(path)
        if lang != "en":
            mo_path = path.replace(".po", ".mo")
            po.save_as_mofile(mo_path)
            mo = polib.mofile(mo_path)
            assert mo.metadata.get("Content-Type"), "compiled .mo lost its metadata"
            assert len(mo) == len(po.translated_entries()), (
                f"{lang}: mo has {len(mo)} entries, po has "
                f"{len(po.translated_entries())} translated"
            )
        print(f"{lang}: {len(chunks)} added, catalog size {len(po)}")


if __name__ == "__main__":
    check()
    if "--check" not in sys.argv:
        main()
