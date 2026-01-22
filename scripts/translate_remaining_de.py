"""
Translate remaining untranslated German strings in Crush.lu .po file.
"""

import polib
import sys
import io

# Set UTF-8 encoding for Windows console
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

PO_FILE = r"C:\Users\User\Github-Local\Multi-Domain-Django-Platform\crush_lu\locale\de\LC_MESSAGES\django.po"

# Translation dictionary for remaining strings
# Using informal "du" form for Gen Z/Millennial audience
TRANSLATIONS = {
    # API Journey strings
    "Missing challenge ID or answer": "Fehlende Challenge-ID oder Antwort",
    "Correct! Well done! 🎉": "Richtig! Gut gemacht! 🎉",
    "Not quite right. Try again! 💪": "Nicht ganz richtig. Versuch es nochmal! 💪",
    "Missing challenge ID or hint number": "Fehlende Challenge-ID oder Hinweisnummer",
    "Missing reward ID or piece index": "Fehlende Belohnungs-ID oder Teilindex",
    "Not enough points! You need %(points)s points to unlock this piece.": "Nicht genug Punkte! Du brauchst %(points)s Punkte, um dieses Teil freizuschalten.",

    # Form help texts
    "e.g., Verified identity via video call. Photos match. Genuine profile.": "z.B. Identität per Videoanruf verifiziert. Fotos stimmen überein. Authentisches Profil.",
    "e.g., Welcome to Crush.lu! Your profile looks great...": "z.B. Willkommen bei Crush.lu! Dein Profil sieht super aus...",
    "Photo Puzzle Image": "Foto-Puzzle-Bild",
    "This image will be revealed as a puzzle in Chapter 1. Recommended: Square image (1:1 ratio), minimum 800x800px.": "Dieses Bild wird als Puzzle in Kapitel 1 enthüllt. Empfohlen: Quadratisches Bild (1:1), mindestens 800x800px.",

    # Challenge types and descriptions
    "Voice Message": "Sprachnachricht",
    "Video Message (Alternative)": "Videonachricht (Alternative)",
    "The Wonderland of You": "Dein Wunderland",
    "Custom Journey": "Individuelle Reise",
    "Medium": "Mittel",
    "Word Scramble": "Buchstabensalat",

    # Admin strings
    "User Responses (%(counter)s)": "Benutzerantworten (%(counter)s)",
    "%(counter)s hint used": "%(counter)s Hinweis verwendet",
    "Challenges (%(counter)s)": "Herausforderungen (%(counter)s)",
    "Rewards (%(counter)s)": "Belohnungen (%(counter)s)",
    "Pending Approval (%(counter)s)": "Ausstehende Genehmigung (%(counter)s)",
    "Approved Guests (%(counter)s)": "Genehmigte Gäste (%(counter)s)",
    "Sent Invitations (%(counter)s)": "Gesendete Einladungen (%(counter)s)",
    "Rejected (%(counter)s)": "Abgelehnt (%(counter)s)",

    # Journey content
    "Challenge not found": "Challenge nicht gefunden",
    "No active journey found": "Keine aktive Reise gefunden",
    "You already completed this challenge!": "Du hast diese Challenge bereits gelöst!",
    "An error occurred processing your answer": "Bei der Verarbeitung deiner Antwort ist ein Fehler aufgetreten",
    "Invalid hint number": "Ungültige Hinweisnummer",
    "Hint not available": "Hinweis nicht verfügbar",
    "An error occurred unlocking the hint": "Beim Freischalten des Hinweises ist ein Fehler aufgetreten",
    "An error occurred retrieving progress": "Beim Abrufen des Fortschritts ist ein Fehler aufgetreten",
    "An error occurred saving state": "Beim Speichern des Status ist ein Fehler aufgetreten",
    "Invalid response choice": "Ungültige Antwortauswahl",
    "Response recorded": "Antwort aufgezeichnet",
    "An error occurred recording your response": "Beim Aufzeichnen deiner Antwort ist ein Fehler aufgetreten",
    "Reward not found": "Belohnung nicht gefunden",
    "This piece is already unlocked": "Dieses Teil ist bereits freigeschaltet",
    "Piece unlocked! -%(points)s points": "Teil freigeschaltet! -%(points)s Punkte",
    "An error occurred unlocking the piece": "Beim Freischalten des Teils ist ein Fehler aufgetreten",
    "An error occurred retrieving reward progress": "Beim Abrufen des Belohnungsfortschritts ist ein Fehler aufgetreten",

    # Journey gift
    "{sender_name} has created a journey for you": "{sender_name} hat eine Reise für dich erstellt",
    "has created a journey for you": "hat eine Reise für dich erstellt",

    # Event languages
    "Languages for Events": "Sprachen für Veranstaltungen",
    "Invalid language selection: %(lang)s": "Ungültige Sprachauswahl: %(lang)s",

    # JSON data examples
    'JSON data for options/choices: {"A": "option1", "B":"option2"}': 'JSON-Daten für Optionen/Auswahl: {"A": "Option1", "B":"Option2"}',
    'JSON data for options, choices, etc. ({"A": "option1", "B":"option2"})': 'JSON-Daten für Optionen, Auswahl usw. ({"A":"Option1", "B": "Option2"})',

    # Additional common strings
    "Export selected submissions to CSV": "Ausgewählte Einreichungen als CSV exportieren",
    "An error occurred while deleting your account. Please contact support.": "Beim Löschen deines Kontos ist ein Fehler aufgetreten. Bitte kontaktiere den Support.",
    "An error occurred. Please try again.": "Ein Fehler ist aufgetreten. Bitte versuche es erneut.",
    "Invalid action.": "Ungültige Aktion.",

    # Email templates
    "Your gift has been created! Share the QR code below.": "Dein Geschenk wurde erstellt! Teile den QR-Code unten.",
    "This gift has expired.": "Dieses Geschenk ist abgelaufen.",
    "This gift has already been claimed.": "Dieses Geschenk wurde bereits eingelöst.",

    # Profile & Events
    "Confirm Registration": "Anmeldung bestätigen",
    "⏳ Waitlist": "⏳ Warteliste",
    "Please complete your profile first.": "Bitte vervollständige zuerst dein Profil.",
    "Complete all challenges to unlock this reward.": "Schließe alle Challenges ab, um diese Belohnung freizuschalten.",
}

def main():
    print(f"Loading {PO_FILE}...\n")
    po = polib.pofile(PO_FILE)

    untranslated = po.untranslated_entries()
    print(f"Found {len(untranslated)} untranslated entries\n")

    translated_count = 0

    for entry in untranslated:
        msgid = entry.msgid

        if msgid in TRANSLATIONS:
            entry.msgstr = TRANSLATIONS[msgid]
            translated_count += 1
            print(f"✓ Translated: {msgid[:60]}...")
            print(f"  -> {TRANSLATIONS[msgid][:60]}...\n")
        else:
            # Try to provide a reasonable default for common patterns
            if "Photo" in msgid and "chapter" in msgid.lower():
                entry.msgstr = msgid.replace("Photo", "Foto").replace("Chapter", "Kapitel")
                translated_count += 1
                print(f"✓ Auto-translated: {msgid[:60]}...")
                print(f"  -> {entry.msgstr[:60]}...\n")
            else:
                print(f"⚠ Needs manual translation: {msgid[:60]}...")
                if entry.occurrences:
                    print(f"  Location: {entry.occurrences[0][0]}\n")

    if translated_count > 0:
        print(f"\n💾 Saving {translated_count} new translations...")
        po.save(PO_FILE)
        print("✅ Done! Run 'python manage.py compilemessages' to compile.")
    else:
        print("\n⚠ No translations added.")

    # Show remaining untranslated
    po = polib.pofile(PO_FILE)
    remaining = po.untranslated_entries()
    print(f"\nRemaining untranslated: {len(remaining)}")

if __name__ == "__main__":
    main()
