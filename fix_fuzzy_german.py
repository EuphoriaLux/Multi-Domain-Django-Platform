#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Fix fuzzy entries in German translation file for Crush.lu
Works by processing the file line by line and removing fuzzy markers
"""

def process_po_file():
    po_file = r'C:\Users\User\Github-Local\Multi-Domain-Django-Platform\crush_lu\locale\de\LC_MESSAGES\django.po'

    with open(po_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Track changes
    changes_made = []
    new_lines = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # Check if this is a fuzzy marker
        if line.strip() == '#, fuzzy':
            # Look ahead to see what msgid this applies to
            msgid_line = ''
            msgstr_line = ''

            for j in range(i+1, min(i+20, len(lines))):
                if lines[j].startswith('msgid '):
                    msgid_line = lines[j].strip()
                if lines[j].startswith('msgstr '):
                    msgstr_line = lines[j].strip()
                    break

            # Decide whether to skip this fuzzy marker or fix the translation
            should_fix = False
            new_msgstr = None

            # Fix 1: Sign Out
            if 'msgid "Sign Out"' in msgid_line and 'msgstr "Anmelden"' in msgstr_line:
                should_fix = True
                new_msgstr = 'msgstr "Abmelden"\n'
                changes_made.append(f'Line {i+1}: Fixed "Sign Out" translation to "Abmelden"')

            # Fix 2: Are you sure you want to sign out?
            elif 'Are you sure you want to sign out?' in msgid_line:
                should_fix = True
                # Find the msgstr line and replace it
                for j in range(i+1, min(i+20, len(lines))):
                    if lines[j].startswith('msgstr '):
                        lines[j] = 'msgstr "Möchten Sie sich wirklich abmelden?"\n'
                        break
                changes_made.append(f'Line {i+1}: Fixed "sign out confirmation" translation')

            # Fix 3: Profile approved message
            elif 'profile has been approved! You can now register for events' in msgid_line:
                should_fix = True
                # This is a multiline string, handle carefully
                for j in range(i+1, min(i+30, len(lines))):
                    if lines[j].startswith('msgstr '):
                        # Check if it's empty or incomplete
                        if 'msgstr ""' in lines[j]:
                            lines[j] = 'msgstr "Ihr Crush.lu-Profil wurde genehmigt! Sie können sich jetzt für Events registrieren."\n'
                        break
                changes_made.append(f'Line {i+1}: Fixed "profile approved" translation')

            # Fix 4: log in (lowercase)
            elif 'msgid "log in"' in msgid_line and 'msgstr "Anmelden"' in msgstr_line:
                should_fix = True
                new_msgstr = 'msgstr "anmelden"\n'
                changes_made.append(f'Line {i+1}: Fixed "log in" translation to lowercase')

            # Fix 5: Sign In - translation is correct, just remove fuzzy
            elif 'msgid "Sign In"' in msgid_line and 'msgstr "Anmelden"' in msgstr_line:
                should_fix = True  # Just remove fuzzy, translation is correct
                changes_made.append(f'Line {i+1}: Removed fuzzy from correct "Sign In" translation')

            # Fix 6: Video message for Chapter 4
            elif 'Record a video message for Chapter 4' in msgid_line:
                should_fix = True
                for j in range(i+1, min(i+30, len(lines))):
                    if lines[j].startswith('msgstr '):
                        if 'msgstr ""' in lines[j]:
                            lines[j] = 'msgstr "Nehmen Sie eine Videonachricht für Kapitel 4 auf. Formate: MP4, MOV (max. 50 MB)."\n'
                        break
                changes_made.append(f'Line {i+1}: Fixed "video message" translation')

            # Fix 7: Video message file for Chapter 4
            elif 'Video message file for Chapter 4' in msgid_line:
                should_fix = True
                for j in range(i+1, min(i+30, len(lines))):
                    if lines[j].startswith('msgstr '):
                        lines[j] = 'msgstr "Videonachricht-Datei für Kapitel 4 (MP4, MOV - max. 50 MB)"\n'
                        break
                changes_made.append(f'Line {i+1}: Fixed "video message file" translation')

            # Fix 8: Authentic connections - translation is correct
            elif "authentic connections in Luxembourg's dating scene" in msgid_line:
                should_fix = True
                changes_made.append(f'Line {i+1}: Removed fuzzy from correct "authentic connections" translation')

            # Fix 9: Gifted journeys
            elif 'Used for gifted journeys' in msgid_line:
                should_fix = True
                for j in range(i+1, min(i+30, len(lines))):
                    if lines[j].startswith('msgstr '):
                        # The translation seems cut off, fix it
                        lines[j] = 'msgstr "Direkter Link zum Benutzer (umgeht Namensabgleich). Wird für Geschenk-Reisen verwendet."\n'
                        break
                changes_made.append(f'Line {i+1}: Fixed "gifted journeys" translation')

            if should_fix:
                # Skip the fuzzy line (don't add it to new_lines)
                i += 1
                continue  # Don't add this line

        # Add the line to output
        new_lines.append(line)
        i += 1

    # Write the modified content
    with open(po_file, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)

    # Print summary
    print(f"\nFixed {len(changes_made)} fuzzy entries:\n")
    for change in changes_made:
        print(f"  {change}")

    print(f"\nFile updated: {po_file}")
    print("\nNext steps:")
    print("  1. Review changes: git diff crush_lu/locale/de/LC_MESSAGES/django.po")
    print("  2. Compile: python manage.py compilemessages")
    print("  3. Test the translations")

if __name__ == '__main__':
    process_po_file()
