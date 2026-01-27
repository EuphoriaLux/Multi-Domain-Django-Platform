#!/usr/bin/env python
"""Batch fix fuzzy entries in German .po file."""

import re

filepath = r'C:\Users\User\Github-Local\Multi-Domain-Django-Platform\crush_lu\locale\de\LC_MESSAGES\django.po'

# Read the file
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# Find all fuzzy entries and their line numbers
fuzzy_pattern = re.compile(
    r'(#: .*?\n)'  # Location
    r'(#, fuzzy.*?\n)'  # Fuzzy flag
    r'((?:#\| .*?\n)*)'  # Old msgid lines
    r'(msgid .*?\n(?:\".*?\"\n)*)'  # msgid
    r'(msgstr .*?\n(?:\".*?\"\n)*)',  # msgstr
    re.MULTILINE
)

matches = list(fuzzy_pattern.finditer(content))
print(f"Found {len(matches)} fuzzy entries")

# Simple fixes - just remove fuzzy flag when translation looks correct
simple_fixes = []
for match in matches:
    location = match.group(1)
    fuzzy_line = match.group(2)
    old_msgid = match.group(3)
    msgid = match.group(4)
    msgstr = match.group(5)

    # Extract actual text
    msgid_text = re.findall(r'"(.*?)"', msgid)
    msgstr_text = re.findall(r'"(.*?)"', msgstr)

    full_msgid = ''.join(msgid_text)
    full_msgstr = ''.join(msgstr_text)

    # Check if it needs simple fix (just spacing issues)
    if full_msgstr and not full_msgstr.startswith('\\n'):
        # Count replacements needed
        needs_fix = False

        # Check for common spacing issues in old_msgid
        if old_msgid:
            # Space missing before opening paren or after word
            if 'characters)' in old_msgid and 'characters)' not in msgid:
                needs_fix = True
            if 'M4A(' in old_msgid and 'M4A (' in full_msgid:
                needs_fix = True

        if needs_fix or len(old_msgid.strip()) < 50:  # Simple old comment
            simple_fixes.append({
                'full_match': match.group(0),
                'location': location.strip(),
                'msgid': full_msgid[:100],
                'msgstr': full_msgstr[:100],
            })

print(f"\nSimple fixes (just remove fuzzy): {len(simple_fixes)}")
for i, fix in enumerate(simple_fixes[:20], 1):
    print(f"\n{i}. {fix['location']}")
    print(f"   EN: {fix['msgid']}")
    print(f"   DE: {fix['msgstr']}")
