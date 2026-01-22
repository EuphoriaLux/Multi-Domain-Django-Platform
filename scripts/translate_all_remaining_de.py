"""
Translate ALL remaining untranslated German strings in Crush.lu .po file.
"""

import polib
import sys
import io
import re

# Set UTF-8 encoding for Windows console
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

PO_FILE = r"C:\Users\User\Github-Local\Multi-Domain-Django-Platform\crush_lu\locale\de\LC_MESSAGES\django.po"

def main():
    print(f"Loading {PO_FILE}...\n")
    po = polib.pofile(PO_FILE)

    untranslated = po.untranslated_entries()
    print(f"Found {len(untranslated)} untranslated entries\n")

    translated_count = 0

    for entry in untranslated:
        msgid = entry.msgid.strip()
        translation = None

        # Direct translations
        translations_map = {
            # API strings
            "Missing challenge ID or answer": "Fehlende Challenge-ID oder Antwort",
            "Correct! Well done! 🎉": "Richtig! Gut gemacht! 🎉",
            "Not quite right. Try again! 💪": "Nicht ganz richtig. Versuch es nochmal! 💪",
            "Missing challenge ID or hint number": "Fehlende Challenge-ID oder Hinweisnummer",
            "Missing reward ID or piece index": "Fehlende Belohnungs-ID oder Teilindex",

            # Form help texts
            "Photo Puzzle Image": "Foto-Puzzle-Bild",
            "Slideshow Photo 1": "Slideshow-Foto 1",
            "Slideshow Photo 2": "Slideshow-Foto 2",
            "Slideshow Photo 3": "Slideshow-Foto 3",
            "Slideshow Photo 4": "Slideshow-Foto 4",
            "Slideshow Photo 5": "Slideshow-Foto 5",

            # Themes
            "Wonderland Night (Dark starry sky)": "Wonderland-Nacht (Dunkler Sternenhimmel)",
            "Enchanted Garden (Flowers & butterflies)": "Verzauberter Garten (Blumen & Schmetterlinge)",
            "Art Gallery (Golden frames & vintage)": "Kunstgalerie (Goldene Rahmen & Vintage)",
            "Carnival (Warm lights & mirrors)": "Karneval (Warme Lichter & Spiegel)",
            "Starlit Observatory (Deep space & cosmos)": "Sternwarte (Tiefes Weltall & Kosmos)",
            "Magical Door (Sunrise & celebration)": "Magische Tür (Sonnenaufgang & Feier)",

            # Difficulty
            "Easy": "Einfach",
            "Hard": "Schwer",

            # Challenge types
            "Riddle": "Rätsel",
            "Multiple Choice": "Multiple Choice",
            "Reorder Timeline": "Zeitstrahl ordnen",
            "Open Text": "Freier Text",
            "Slider": "Schieberegler",
            "Rating": "Bewertung",
            "Binary Choice": "Binäre Auswahl",
            "Interactive": "Interaktiv",

            # Reward types
            "Poem": "Gedicht",
            "Photo Reveal": "Foto-Enthüllung",
            "Future Letter": "Brief an die Zukunft",
            "Audio Message": "Audionachricht",
            "Video Message": "Videonachricht",
            "Memory Box": "Erinnerungsbox",
            "Photo Puzzle": "Foto-Puzzle",
            "Photo Slideshow": "Foto-Slideshow",
            "Voice Message": "Sprachnachricht",

            # Phone verification
            "Enter Code": "Code eingeben",
            "Verify": "Verifizieren",
            "Resend Code": "Code erneut senden",

            # View strings
            "Submission not found or not assigned to you.": "Einreichung nicht gefunden oder dir nicht zugewiesen.",

            # Journey descriptions
            "A magical 6-chapter adventure through the Wonderland of You": "Ein magisches 6-Kapitel-Abenteuer durch dein Wunderland",
            "24 doors of surprises waiting to be discovered": "24 Türen voller Überraschungen warten darauf, entdeckt zu werden",
            "Complete the journey to unlock your certificate.": "Schließe die Reise ab, um dein Zertifikat freizuschalten.",
        }

        # Check direct translations
        if msgid in translations_map:
            translation = translations_map[msgid]

        # Handle placeholder strings
        elif "%(points)s points to unlock this piece" in msgid:
            translation = "Nicht genug Punkte! Du brauchst %(points)s Punkte, um dieses Teil freizuschalten."
        elif "%(max_size)s MB" in msgid and "Image file size" in msgid:
            translation = "Bilddateigröße muss kleiner als %(max_size)s MB sein."
        elif "Audio file size must be less than 10 MB" in msgid:
            translation = "Audiodateigröße muss kleiner als 10 MB sein."
        elif "Invalid audio format" in msgid:
            translation = "Ungültiges Audioformat. Bitte verwende MP3-, WAV- oder M4A-Dateien."
        elif "Video file size must be less than 50 MB" in msgid:
            translation = "Videodateigröße muss kleiner als 50 MB sein."
        elif "Invalid video format" in msgid:
            translation = "Ungültiges Videoformat. Bitte verwende MP4- oder MOV-Dateien."

        # Help text patterns
        elif msgid.startswith("e.g., Verified identity"):
            translation = "z.B. Identität per Videoanruf verifiziert. Fotos stimmen überein. Authentisches Profil."
        elif msgid.startswith("e.g., Welcome to Crush.lu"):
            translation = "z.B. Willkommen bei Crush.lu! Dein Profil sieht super aus..."
        elif "This image will be revealed as a puzzle" in msgid:
            translation = "Dieses Bild wird als Puzzle in Kapitel 1 enthüllt. Empfohlen: Quadratisches Bild (1:1), mindestens 800x800px."
        elif "First photo for the Chapter" in msgid and "slideshow" in msgid:
            ch_num = re.search(r'Chapter (\d+)', msgid)
            if ch_num:
                translation = f"Erstes Foto für die Slideshow in Kapitel {ch_num.group(1)}."
        elif "Second photo" in msgid and "slideshow" in msgid:
            translation = "Zweites Foto für die Slideshow."
        elif "Third photo" in msgid and "slideshow" in msgid:
            translation = "Drittes Foto für die Slideshow."
        elif "Fourth photo" in msgid and "slideshow" in msgid:
            translation = "Viertes Foto für die Slideshow."
        elif "Fifth photo" in msgid and "slideshow" in msgid:
            translation = "Fünftes Foto für die Slideshow."
        elif "Record a personal voice message for Chapter" in msgid:
            translation = "Nimm eine persönliche Sprachnachricht für Kapitel 4 auf. Formate: MP3, WAV, M4A (max. 10 MB)."
        elif "Alternatively, record a video message" in msgid:
            translation = "Alternativ kannst du eine Videonachricht aufnehmen. Formate: MP4, MOV (max. 50 MB)."
        elif "A 6-digit code has been sent to" in msgid:
            translation = "Ein 6-stelliger Code wurde gesendet an"

        # JSON examples
        elif 'JSON data for options/choices: {"A": "option1"' in msgid:
            translation = 'JSON-Daten für Optionen/Auswahl: {"A": "Option1", "B":"Option2"}'
        elif 'JSON data for options, choices, etc. ({"A": "option1"' in msgid:
            translation = 'JSON-Daten für Optionen, Auswahl usw. ({"A":"Option1", "B": "Option2"})'

        # Apply translation if found
        if translation:
            entry.msgstr = translation
            translated_count += 1
            print(f"✓ {msgid[:60]}...")
            print(f"  -> {translation[:60]}...\n")
        else:
            print(f"⚠ NEEDS MANUAL: {msgid[:70]}...")

    if translated_count > 0:
        print(f"\n💾 Saving {translated_count} new translations...")
        po.save(PO_FILE)
        print("✅ Saved!\n")
    else:
        print("\n⚠ No changes made.\n")

    # Show stats
    po = polib.pofile(PO_FILE)
    remaining = po.untranslated_entries()
    print(f"Remaining untranslated: {len(remaining)}/{len(po)} ({len(remaining)/len(po)*100:.1f}%)")
    print(f"Translated: {len(po.translated_entries())}/{len(po)} ({len(po.translated_entries())/len(po)*100:.1f}%)")

if __name__ == "__main__":
    main()
