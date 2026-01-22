"""
Verify German translations in Crush.lu .po file.
Shows statistics and checks for common issues.
"""

import polib
import sys
import io

# Set UTF-8 encoding for Windows console
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

PO_FILE = r"C:\Users\User\Github-Local\Multi-Domain-Django-Platform\crush_lu\locale\de\LC_MESSAGES\django.po"

def main():
    print(f"Loading {PO_FILE}...\n")
    po = polib.pofile(PO_FILE)

    # Overall statistics
    total = len(po)
    translated = len(po.translated_entries())
    untranslated = len(po.untranslated_entries())
    fuzzy = len(po.fuzzy_entries())
    obsolete = len(po.obsolete_entries())

    print("=" * 60)
    print("TRANSLATION STATISTICS")
    print("=" * 60)
    print(f"Total entries:        {total:>6}")
    print(f"Translated:           {translated:>6} ({translated/total*100:.1f}%)")
    print(f"Untranslated:         {untranslated:>6} ({untranslated/total*100:.1f}%)")
    print(f"Fuzzy (need review):  {fuzzy:>6} ({fuzzy/total*100:.1f}%)")
    print(f"Obsolete:             {obsolete:>6}")
    print("=" * 60)

    # Check for common issues
    issues = []

    # Check for placeholder mismatches
    for entry in po.translated_entries():
        msgid = entry.msgid
        msgstr = entry.msgstr

        # Check for %(...)s placeholders
        import re
        msgid_placeholders = set(re.findall(r'%\([^)]+\)[sd]', msgid))
        msgstr_placeholders = set(re.findall(r'%\([^)]+\)[sd]', msgstr))

        if msgid_placeholders != msgstr_placeholders:
            issues.append({
                'type': 'Placeholder mismatch',
                'msgid': msgid[:60],
                'expected': msgid_placeholders,
                'found': msgstr_placeholders
            })

        # Check for {var} placeholders
        msgid_braces = set(re.findall(r'\{[^}]+\}', msgid))
        msgstr_braces = set(re.findall(r'\{[^}]+\}', msgstr))

        if msgid_braces != msgstr_braces:
            issues.append({
                'type': 'Brace placeholder mismatch',
                'msgid': msgid[:60],
                'expected': msgid_braces,
                'found': msgstr_braces
            })

    if issues:
        print(f"\n⚠️  FOUND {len(issues)} POTENTIAL ISSUES:\n")
        for i, issue in enumerate(issues[:10], 1):  # Show first 10
            print(f"{i}. {issue['type']}")
            print(f"   String: {issue['msgid']}...")
            print(f"   Expected: {issue['expected']}")
            print(f"   Found:    {issue['found']}\n")

        if len(issues) > 10:
            print(f"   ... and {len(issues) - 10} more issues\n")
    else:
        print("\n✅ No placeholder mismatches found!\n")

    # Show sample translations
    print("=" * 60)
    print("SAMPLE TRANSLATIONS (First 10)")
    print("=" * 60)
    for i, entry in enumerate(po.translated_entries()[:10], 1):
        print(f"{i}. EN: {entry.msgid[:50]}...")
        print(f"   DE: {entry.msgstr[:50]}...\n")

    # Check for untranslated strings
    if untranslated > 0:
        print("=" * 60)
        print(f"UNTRANSLATED STRINGS ({min(untranslated, 10)} of {untranslated})")
        print("=" * 60)
        for i, entry in enumerate(po.untranslated_entries()[:10], 1):
            print(f"{i}. {entry.msgid[:60]}...")
            if entry.occurrences:
                print(f"   Location: {entry.occurrences[0][0]}:{entry.occurrences[0][1]}\n")

    # Check for fuzzy entries
    if fuzzy > 0:
        print("=" * 60)
        print(f"FUZZY ENTRIES ({min(fuzzy, 10)} of {fuzzy})")
        print("=" * 60)
        for i, entry in enumerate(po.fuzzy_entries()[:10], 1):
            print(f"{i}. {entry.msgid[:60]}...")
            print(f"   Current: {entry.msgstr[:60]}...")
            if entry.occurrences:
                print(f"   Location: {entry.occurrences[0][0]}:{entry.occurrences[0][1]}\n")

    print("=" * 60)
    if untranslated == 0 and fuzzy == 0 and len(issues) == 0:
        print("✅ ALL TRANSLATIONS COMPLETE AND VALID!")
    elif untranslated == 0 and fuzzy == 0:
        print("✅ ALL STRINGS TRANSLATED (some placeholder issues to review)")
    else:
        print("⚠️  Translation work needed")
    print("=" * 60)

if __name__ == "__main__":
    main()
