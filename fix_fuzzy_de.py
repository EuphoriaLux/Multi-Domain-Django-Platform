#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Fix fuzzy entries in German translation file for Crush.lu
"""

import re

# Define fixes for user-facing fuzzy entries
fixes = [
    # Fix 1: "Sign Out" translation
    {
        'line_match': 'msgid "Sign Out"',
        'old_msgstr': 'msgstr "Anmelden"',
        'new_msgstr': 'msgstr "Abmelden"',
        'remove_fuzzy': True,
        'context': '.\templates\account\logout_crush.html'
    },
    # Fix 2: "Are you sure you want to sign out?"
    {
        'line_match': 'msgid "Are you sure you want to sign out?"',
        'old_msgstr': 'msgstr "Möchtest du diese Verbindung wirklich ablehnen?"',
        'new_msgstr': 'msgstr "Möchten Sie sich wirklich abmelden?"',
        'remove_fuzzy': True,
        'context': '.\templates\account\logout_crush.html'
    },
    # Fix 3: "Sign In" in membership page
    {
        'line_match': 'msgid "Sign In"',
        'old_msgstr': 'msgstr "Anmelden"',
        'new_msgstr': 'msgstr "Anmelden"',  # This one is correct, just remove fuzzy
        'remove_fuzzy': True,
        'context': '.\templates\crush_lu\membership.html'
    },
    # Fix 4: "log in" (lowercase)
    {
        'line_match': 'msgid "log in"',
        'old_msgstr': 'msgstr "Anmelden"',
        'new_msgstr': 'msgstr "anmelden"',  # Use lowercase to match
        'remove_fuzzy': True,
        'context': '.\templates\crush_lu\email_unsubscribe.html'
    },
    # Fix 5: Profile approved notification
    {
        'line_match': 'Your Crush.lu profile has been approved! You can now register for events.',
        'old_msgstr': '',  # Empty or wrong
        'new_msgstr': 'msgstr "Ihr Crush.lu-Profil wurde genehmigt! Sie können sich jetzt für Events registrieren."',
        'remove_fuzzy': True,
        'context': 'push_notifications.py'
    },
    # Fix 6: "We're bringing back authentic connections" - just needs fuzzy removed
    {
        'line_match': "We're bringing back authentic connections in Luxembourg's dating scene.",
        'old_msgstr': 'msgstr "Wir bringen authentische Verbindungen zurück in Luxemburgs Dating-Szene."',
        'new_msgstr': 'msgstr "Wir bringen authentische Verbindungen zurück in Luxemburgs Dating-Szene."',
        'remove_fuzzy': True,
        'context': '.\templates\crush_lu\about.html'
    },
    # Fix 7: Record video message
    {
        'line_match': 'msgid "Record a video message for Chapter 4. Formats: MP4, MOV (max 50MB)."',
        'old_msgstr': '',
        'new_msgstr': 'msgstr "Nehmen Sie eine Videonachricht für Kapitel 4 auf. Formate: MP4, MOV (max. 50 MB)."',
        'remove_fuzzy': True,
        'context': '.\forms.py'
    },
    # Fix 8: Video message file for Chapter 4
    {
        'line_match': 'msgid "Video message file for Chapter 4 (MP4, MOV - max 50MB)"',
        'old_msgstr': 'msgstr "Videonachricht-Datei (MP4, MOV - max. 50 MB)"',
        'new_msgstr': 'msgstr "Videonachricht-Datei für Kapitel 4 (MP4, MOV - max. 50 MB)"',
        'remove_fuzzy': True,
        'context': '.\models\journey_gift.py'
    },
]

def fix_fuzzy_entries():
    """Process the .po file and fix fuzzy entries."""

    po_file = r'C:\Users\User\Github-Local\Multi-Domain-Django-Platform\crush_lu\locale\de\LC_MESSAGES\django.po'

    with open(po_file, 'r', encoding='utf-8') as f:
        content = f.read()

    original_content = content

    # Process each fix
    for fix_num, fix in enumerate(fixes, 1):
        print(f"Processing fix {fix_num}/{len(fixes)}: {fix['line_match'][:60]}...")

        # Simple approach: find the section and replace
        # We'll look for the context + msgid pattern

        # For each fix, we'll find the fuzzy block and replace it
        # This is a simplified approach - for production use a proper .po parser

        if fix['remove_fuzzy']:
            # Pattern: find the msgid and associated fuzzy marker
            # We need to be careful with multiline strings

            # Search for the pattern in content
            if fix['line_match'] in content:
                # Find the position
                pos = content.find(fix['line_match'])
                if pos != -1:
                    # Look backwards for #, fuzzy
                    section_start = max(0, pos - 500)
                    section = content[section_start:pos + len(fix['line_match']) + 200]

                    # Check if there's a fuzzy marker
                    if '#, fuzzy' in section:
                        print(f"  Found fuzzy marker for: {fix['line_match'][:40]}")

    print("\nManual processing required. Generating sed commands...")
    return False

if __name__ == '__main__':
    fix_fuzzy_entries()
