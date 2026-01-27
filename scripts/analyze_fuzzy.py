#!/usr/bin/env python
"""Extract fuzzy entries from German .po file for review."""

import re

def extract_fuzzy_entries(filepath):
    """Extract all fuzzy entries with context."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Split by entry (entries start with #:)
    entries = re.split(r'\n(?=#:)', content)

    fuzzy_entries = []
    for entry in entries:
        if '#, fuzzy' in entry:
            # Extract key parts
            msgid_match = re.search(r'msgid "(.*?)"', entry, re.DOTALL)
            msgstr_match = re.search(r'msgstr "(.*?)"', entry, re.DOTALL)
            location_match = re.search(r'#: (.*?)\n', entry)

            if msgid_match and msgstr_match:
                msgid = msgid_match.group(1)
                msgstr = msgstr_match.group(1)
                location = location_match.group(1) if location_match else 'unknown'

                # Skip if msgid is empty or very long (likely multiline)
                if msgid and len(msgid) < 200 and not msgid.startswith('\\n'):
                    fuzzy_entries.append({
                        'location': location,
                        'msgid': msgid,
                        'msgstr': msgstr,
                        'full_entry': entry
                    })

    return fuzzy_entries

# Extract fuzzy entries
filepath = r'C:\Users\User\Github-Local\Multi-Domain-Django-Platform\crush_lu\locale\de\LC_MESSAGES\django.po'
fuzzy = extract_fuzzy_entries(filepath)

# Categorize by type
user_facing = []
admin = []
forms = []

for entry in fuzzy:
    loc = entry['location'].lower()
    msgid = entry['msgid'].lower()

    if 'admin' in loc or 'admin' in msgid:
        admin.append(entry)
    elif 'form' in loc or 'label' in msgid or 'help_text' in msgid:
        forms.append(entry)
    else:
        user_facing.append(entry)

# Print user-facing entries first
print(f"=== USER-FACING FUZZY ENTRIES ({len(user_facing)}) ===\n")
for i, entry in enumerate(user_facing[:30], 1):
    print(f"{i}. Location: {entry['location']}")
    print(f"   EN: {entry['msgid'][:100]}")
    print(f"   DE: {entry['msgstr'][:100]}")
    print()

print(f"\n=== FORM/LABEL ENTRIES ({len(forms)}) ===")
for i, entry in enumerate(forms[:20], 1):
    print(f"{i}. {entry['msgid'][:80]} -> {entry['msgstr'][:80]}")

print(f"\n=== ADMIN ENTRIES ({len(admin)}) ===")
print(f"Total: {len(admin)} (skipping detailed output)")

print(f"\n=== TOTAL FUZZY: {len(fuzzy)} ===")
