#!/usr/bin/env python
"""Show untranslated and fuzzy entries"""

import polib
from pathlib import Path

po_file = Path("crush_lu/locale/fr/LC_MESSAGES/django.po")
po = polib.pofile(str(po_file))

print(f"=== FUZZY ENTRIES ({len(po.fuzzy_entries())}) ===\n")
for i, entry in enumerate(po.fuzzy_entries()[:30], 1):  # Show first 30
    try:
        print(f"{i}. msgid: {entry.msgid[:80]}")
        print(f"   msgstr: {entry.msgstr[:80]}")
    except (UnicodeEncodeError, UnicodeDecodeError):
        print(f"{i}. (contains special characters)")
    print()

print(f"\n=== UNTRANSLATED ENTRIES ({len(po.untranslated_entries())}) ===\n")
for i, entry in enumerate(po.untranslated_entries()[:50], 1):  # Show first 50
    try:
        print(f"{i}. {entry.msgid[:100]}")
    except (UnicodeEncodeError, UnicodeDecodeError):
        print(f"{i}. (contains special characters)")
