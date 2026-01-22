"""
Final batch of German translations for Crush.lu.
"""

import polib
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

PO_FILE = r"C:\Users\User\Github-Local\Multi-Domain-Django-Platform\crush_lu\locale\de\LC_MESSAGES\django.po"

FINAL_TRANSLATIONS = {
    # Challenge types
    "Memory Matching Game": "Memory-Spiel",
    "Photo Jigsaw Puzzle": "Foto-Puzzle",
    "Star Catcher Mini-Game": "Sternenfänger-Minispiel",

    # Reward types
    "Photo Reveal (Jigsaw)": "Foto-Enthüllung (Puzzle)",
    "Poem/Letter": "Gedicht/Brief",

    # Slideshow labels
    "First slideshow photo": "Erstes Slideshow-Foto",
    "Second slideshow photo": "Zweites Slideshow-Foto",
    "Third slideshow photo": "Drittes Slideshow-Foto",
    "Fourth slideshow photo": "Viertes Slideshow-Foto",
    "Fifth slideshow photo": "Fünftes Slideshow-Foto",

    # Media file labels
    "Voice message audio file (MP3, WAV, M4A - max 10MB)": "Sprachnachricht-Audiodatei (MP3, WAV, M4A - max. 10 MB)",
    "Video message file (MP4, MOV - max 50MB)": "Videonachricht-Datei (MP4, MOV - max. 50 MB)",

    # Admin help texts
    "Direct link to user (bypasses name matching). Used for giftedjourneys.": "Direkter Link zum Benutzer (umgeht Namensabgleich). Wird für Geschenk-Reisen verwendet.",
    "Lëtzebuergesch": "Lëtzebuergesch",
    "Languages the user can speak at in-person events": "Sprachen, die der Benutzer bei persönlichen Veranstaltungen sprechen kann",
    "Select the appropriate review outcome": "Wähle das passende Prüfungsergebnis",
    "not visible to user": "nicht sichtbar für Benutzer",
    "Genuine": "Authentisch",
    "Suspicious": "Verdächtig",
    "Templates:": "Vorlagen:",
    "Look for red flags or suspicious content": "Achte auf Warnzeichen oder verdächtige Inhalte",

    # Phone verification
    "Enter your number and click the Verify button to receive an SMS code": "Gib deine Nummer ein und klicke auf Verifizieren, um einen SMS-Code zu erhalten",
    "We'll send a verification code via SMS to confirm your phone number.": "Wir senden dir einen Verifizierungscode per SMS zur Bestätigung deiner Telefonnummer.",
    "Select your country and enter your phone number": "Wähle dein Land und gib deine Telefonnummer ein",

    # Challenge feedback
    "Hi": "Hi",
    "Not quite right! Try a different answer.": "Nicht ganz richtig! Versuch eine andere Antwort.",
    "Write at least 10 characters to submit your response": "Schreibe mindestens 10 Zeichen, um deine Antwort abzusenden",
    "Not quite right. Try again!": "Nicht ganz richtig. Versuch es nochmal!",
    "Enter your answer to the riddle above": "Gib deine Antwort auf das Rätsel oben ein",
    "Perfect!": "Perfekt!",
    "Not quite right. Try rearranging the events!": "Nicht ganz richtig. Versuch die Ereignisse neu anzuordnen!",
    "Sortable timeline events": "Sortierbare Zeitleisten-Ereignisse",
    "Great choice!": "Tolle Wahl!",

    # Error messages
    "Network error. Please check your connection and try again.": "Netzwerkfehler. Bitte überprüfe deine Verbindung und versuche es erneut.",
    "Security token missing. Please refresh the page.": "Sicherheitstoken fehlt. Bitte aktualisiere die Seite.",

    # Journey gift creation
    "Next: Add Media": "Weiter: Medien hinzufügen",
    "You can skip media and create a basic gift": "Du kannst Medien überspringen und ein einfaches Geschenk erstellen",
    "Add photos and voice/video messages to make the journey extraspecial. You can skip this step if you want a text-only gift.": "Füge Fotos und Sprach-/Videonachrichten hinzu, um die Reise noch spezieller zu machen. Du kannst diesen Schritt überspringen, wenn du nur Text möchtest.",
    "This photo will be revealed piece by piece as a puzzle. Best with asquare image (1:1 ratio).": "Dieses Foto wird Stück für Stück als Puzzle enthüllt. Am besten mit einem quadratischen Bild (1:1-Verhältnis).",
    "Click or drag to upload photo": "Klicke oder ziehe ein Foto zum Hochladen",
    "Add up to 5 photos for a beautiful slideshow reveal. Add memories ofyour time together!": "Füge bis zu 5 Fotos für eine schöne Slideshow hinzu. Teile Erinnerungen an eure gemeinsame Zeit!",
    "Record a heartfelt voice or video message. This will be the emotionalfinal chapter.": "Nimm eine herzliche Sprach- oder Videonachricht auf. Das wird das emotionale letzte Kapitel.",
    "MP3, WAV, M4A (max 10MB)": "MP3, WAV, M4A (max. 10 MB)",
    "MP4, MOV (max 50MB)": "MP4, MOV (max. 50 MB)",

    # Journey completion
    "Congratulations on completing your journey! View your personalizedcompletion certificate.": "Herzlichen Glückwunsch zum Abschluss deiner Reise! Sieh dir dein personalisiertes Abschlusszertifikat an.",

    # Photo puzzle interface
    "Photo puzzle - click pieces to reveal": "Foto-Puzzle - klicke auf Teile zum Enthüllen",
    "locked, click to unlock for 50 points": "gesperrt, klicke zum Freischalten für 50 Punkte",
    "unlocked": "freigeschaltet",
    "Unlock this piece for 50 points?": "Dieses Teil für 50 Punkte freischalten?",
    "Piece unlocked! -50 points": "Teil freigeschaltet! -50 Punkte",
    "Not enough points! You need": "Nicht genug Punkte! Du brauchst",

    # Reward viewing
    "Press play to watch something special...": "Drücke Play, um etwas Besonderes anzusehen...",
    "Read the message below": "Lies die Nachricht unten",
}

def main():
    print(f"Loading {PO_FILE}...\n")
    po = polib.pofile(PO_FILE)

    untranslated = po.untranslated_entries()
    print(f"Found {len(untranslated)} untranslated entries\n")

    translated_count = 0

    for entry in untranslated:
        msgid = entry.msgid.strip()

        if msgid in FINAL_TRANSLATIONS:
            entry.msgstr = FINAL_TRANSLATIONS[msgid]
            translated_count += 1
            print(f"✓ {msgid[:65]}")
            print(f"  → {FINAL_TRANSLATIONS[msgid][:65]}\n")
        else:
            print(f"⚠ Still missing: {msgid[:70]}")

    if translated_count > 0:
        print(f"\n💾 Saving {translated_count} new translations...")
        po.save(PO_FILE)
        print("✅ Saved!\n")
    else:
        print("\n⚠ No changes made.\n")

    # Final stats
    po = polib.pofile(PO_FILE)
    remaining = po.untranslated_entries()
    total = len(po)
    translated = len(po.translated_entries())

    print("=" * 70)
    print(f"FINAL STATISTICS")
    print("=" * 70)
    print(f"Total entries:     {total}")
    print(f"Translated:        {translated} ({translated/total*100:.1f}%)")
    print(f"Untranslated:      {len(remaining)} ({len(remaining)/total*100:.1f}%)")
    print(f"Fuzzy:             {len(po.fuzzy_entries())}")
    print("=" * 70)

    if len(remaining) > 0:
        print(f"\nRemaining {len(remaining)} untranslated strings:")
        for i, entry in enumerate(remaining[:20], 1):
            print(f"{i}. {entry.msgid[:70]}")

if __name__ == "__main__":
    main()
