"""One-off: resolve the crush_lu .po merge conflict (ours = branch, theirs = origin/main).

Keeps our catalog as the base (it contains the branch's new strings),
appends entries that only exist on main, and fills any msgstr that is
empty on our side but translated on main. Recompiles the .mo via polib.

Run from the repo root DURING the merge (index stages 2/3 present):
    .venv-1/Scripts/python.exe scripts/i18n/merge_po_conflict.py
"""

import subprocess
import tempfile
import os

import polib

for lang in ("de", "fr"):
    path = f"crush_lu/locale/{lang}/LC_MESSAGES/django.po"

    ours_text = subprocess.run(
        ["git", "show", f":2:{path}"], capture_output=True, check=True
    ).stdout
    theirs_text = subprocess.run(
        ["git", "show", f":3:{path}"], capture_output=True, check=True
    ).stdout

    def load(raw):
        with tempfile.NamedTemporaryFile(suffix=".po", delete=False) as f:
            f.write(raw)
            tmp = f.name
        try:
            return polib.pofile(tmp)
        finally:
            os.unlink(tmp)

    ours = load(ours_text)
    theirs = load(theirs_text)

    by_key = {(e.msgid, e.msgctxt): e for e in ours}
    added, filled = 0, 0
    for entry in theirs:
        mine = by_key.get((entry.msgid, entry.msgctxt))
        if mine is None:
            ours.append(entry)
            added += 1
        elif not mine.msgstr and entry.msgstr:
            mine.msgstr = entry.msgstr
            if "fuzzy" in entry.flags and "fuzzy" not in mine.flags:
                mine.flags.append("fuzzy")
            filled += 1

    ours.save(path)
    ours.save_as_mofile(path.replace(".po", ".mo"))
    print(f"{lang}: {added} added from main, {filled} filled, total {len(ours)}")
