import json, polib
with open("_fr_translations.json", "r", encoding="utf-8") as f:
    translations = json.load(f)
po = polib.pofile("crush_lu/locale/fr/LC_MESSAGES/django.po")
updated = 0
not_found = []
for msgid, msgstr in translations.items():
    entry = po.find(msgid)
    if entry:
        entry.msgstr = msgstr
        updated += 1
    else:
        not_found.append(msgid[:80])
po.save()
print(f"Updated {updated} entries in FR .po file")
if not_found:
    print(f"WARNING: {len(not_found)} not found:")
    for nf in not_found:
        print(f"  - {nf}")
import os
os.remove("_fr_translations.json")
os.remove("add_fr_translations.py")
print("Cleanup done")
