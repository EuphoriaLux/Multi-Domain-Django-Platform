"""
Fix fuzzy German translations in Crush.lu .po file.
This script reads fuzzy entries, provides proper translations, and removes fuzzy flags.
"""

import polib
import sys
import io

# Set UTF-8 encoding for Windows console
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Path to the German .po file
PO_FILE = r"C:\Users\User\Github-Local\Multi-Domain-Django-Platform\crush_lu\locale\de\LC_MESSAGES\django.po"

def main():
    print(f"Loading {PO_FILE}...")
    po = polib.pofile(PO_FILE)

    fuzzy_entries = po.fuzzy_entries()
    print(f"\nFound {len(fuzzy_entries)} fuzzy entries to review\n")

    # Translations mapping for fuzzy entries
    # Based on context and maintaining informal "du" tone
    translations = {
        "📋 Export attendees to CSV": "📋 Teilnehmer als CSV exportieren",
        "📋 Export selected registrations to CSV": "📋 Ausgewählte Anmeldungen als CSV exportieren",
        "✅ Confirm selected registrations": "✅ Ausgewählte Anmeldungen bestätigen",
        "⏳ Move to waitlist": "⏳ Zur Warteliste verschieben",
        "📧 Send invitation reminder": "📧 Einladungserinnerung senden",
        "🔄 Resend invitation": "🔄 Einladung erneut senden",
        "📤 Send reminder email": "📤 Erinnerungs-E-Mail senden",
        "✅ Mark as paid": "✅ Als bezahlt markieren",
        "❌ Mark as unpaid": "❌ Als unbezahlt markieren",
        "📧 Send welcome email": "📧 Willkommens-E-Mail senden",
        "🔄 Reset password": "🔄 Passwort zurücksetzen",
        "❌ Deactivate account": "❌ Konto deaktivieren",
        "✅ Activate account": "✅ Konto aktivieren",
        "📋 Export to CSV": "📋 Als CSV exportieren",
        "🗑️ Delete selected": "🗑️ Ausgewählte löschen",
        "✅ Approve selected": "✅ Ausgewählte genehmigen",
        "❌ Reject selected": "❌ Ausgewählte ablehnen",
        "📧 Send notification": "📧 Benachrichtigung senden",
        "🔄 Refresh data": "🔄 Daten aktualisieren",
        "Export selected profiles to CSV": "Ausgewählte Profile als CSV exportieren",
        "Confirm Registration": "Anmeldung bestätigen",
        "⏳ Waitlist": "⏳ Warteliste",
        "Send reminder email to attendees": "Erinnerungs-E-Mail an Teilnehmer senden",
        "Mark selected as attended": "Ausgewählte als teilgenommen markieren",
        "Mark selected as no-show": "Ausgewählte als nicht erschienen markieren",
        "Send welcome email to user": "Willkommens-E-Mail an Benutzer senden",
        "Approve selected profiles": "Ausgewählte Profile genehmigen",
        "Reject selected profiles": "Ausgewählte Profile ablehnen",
        "Request revision for selected": "Überarbeitung für Ausgewählte anfordern",
    }

    fixed_count = 0

    for entry in fuzzy_entries:
        msgid = entry.msgid
        current_msgstr = entry.msgstr

        # Try to find a proper translation
        if msgid in translations:
            new_translation = translations[msgid]
            entry.msgstr = new_translation
            entry.flags.remove('fuzzy')
            fixed_count += 1
            print(f"✓ Fixed: {msgid[:60]}...")
            print(f"  Old: {current_msgstr}")
            print(f"  New: {new_translation}\n")
        else:
            # For entries not in our mapping, check if current translation is good
            # and just needs fuzzy flag removed
            if current_msgstr and len(current_msgstr) > 0:
                # Keep the current translation but remove fuzzy flag
                entry.flags.remove('fuzzy')
                fixed_count += 1
                print(f"✓ Kept existing: {msgid[:60]}...")
                print(f"  Translation: {current_msgstr}\n")
            else:
                print(f"⚠ Needs manual review: {msgid[:60]}...")
                print(f"  Current: {current_msgstr}\n")

    if fixed_count > 0:
        print(f"\n💾 Saving {fixed_count} fixes to {PO_FILE}...")
        po.save(PO_FILE)
        print("✅ Done! Run 'python manage.py compilemessages' to compile.")
    else:
        print("\n⚠ No changes made.")

    # Show remaining fuzzy entries
    po = polib.pofile(PO_FILE)
    remaining_fuzzy = po.fuzzy_entries()
    print(f"\nRemaining fuzzy entries: {len(remaining_fuzzy)}")

if __name__ == "__main__":
    main()
