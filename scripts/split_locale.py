#!/usr/bin/env python
"""
Split centralized locale files into per-app locale files.

This script parses the existing locale/de/django.po and locale/fr/django.po files
and splits them into app-specific .po files based on the #: file reference comments.

Usage:
    python scripts/split_locale.py
"""

import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path


# Apps to split translations into
APPS = [
    "crush_lu",
    "vinsdelux",
    "arborist",
    "entreprinder",
    "power_up",
    "delegations",
    "tableau",
]

# Languages to process
LANGUAGES = ["de", "fr"]

# Project root directory
PROJECT_ROOT = Path(__file__).parent.parent


def get_po_header(language: str, app_name: str) -> str:
    """Generate a proper PO file header."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M%z")
    plural_forms = {
        "de": 'nplurals=2; plural=(n != 1);',
        "fr": 'nplurals=2; plural=(n > 1);',
    }

    return f'''# Translations for {app_name} app.
# Copyright (C) 2024-2025 Entreprinder
# This file is distributed under the same license as the project.
#
msgid ""
msgstr ""
"Project-Id-Version: {app_name} 1.0\\n"
"Report-Msgid-Bugs-To: \\n"
"POT-Creation-Date: {now}\\n"
"PO-Revision-Date: {now}\\n"
"Last-Translator: \\n"
"Language-Team: {language.upper()}\\n"
"Language: {language}\\n"
"MIME-Version: 1.0\\n"
"Content-Type: text/plain; charset=UTF-8\\n"
"Content-Transfer-Encoding: 8bit\\n"
"Plural-Forms: {plural_forms.get(language, plural_forms["de"])}\\n"

'''


def parse_po_file(filepath: Path) -> list[dict]:
    """
    Parse a .po file and return a list of translation entries.

    Each entry is a dict with:
    - comments: list of comment lines (including #:, #, #|, etc.)
    - msgid: the source string (can be multiline)
    - msgid_plural: plural form if present
    - msgstr: the translation (can be multiline or list for plurals)
    - locations: list of file:line references from #: comments
    """
    entries = []

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Split into blocks by empty lines (entries are separated by blank lines)
    # But we need to be careful with multiline strings

    lines = content.split("\n")

    current_entry = {
        "comments": [],
        "msgid": "",
        "msgid_plural": "",
        "msgstr": [],
        "locations": [],
        "flags": [],
        "previous": [],
    }

    in_msgid = False
    in_msgid_plural = False
    in_msgstr = False
    msgstr_index = 0

    # Skip the header (first entry with empty msgid)
    header_done = False

    for line in lines:
        line_stripped = line.strip()

        # Skip empty lines between entries
        if not line_stripped:
            if current_entry["msgid"] or current_entry["comments"]:
                # Check if this is the header (empty msgid with msgstr content)
                if not header_done and not current_entry["msgid"]:
                    header_done = True
                    current_entry = {
                        "comments": [],
                        "msgid": "",
                        "msgid_plural": "",
                        "msgstr": [],
                        "locations": [],
                        "flags": [],
                        "previous": [],
                    }
                    in_msgid = False
                    in_msgid_plural = False
                    in_msgstr = False
                    continue

                if current_entry["msgid"]:
                    entries.append(current_entry)
                current_entry = {
                    "comments": [],
                    "msgid": "",
                    "msgid_plural": "",
                    "msgstr": [],
                    "locations": [],
                    "flags": [],
                    "previous": [],
                }
                in_msgid = False
                in_msgid_plural = False
                in_msgstr = False
            continue

        # Location comments
        if line_stripped.startswith("#:"):
            locations = line_stripped[2:].strip().split()
            current_entry["locations"].extend(locations)
            current_entry["comments"].append(line)
            in_msgid = False
            in_msgid_plural = False
            in_msgstr = False
            continue

        # Flag comments (like #, fuzzy)
        if line_stripped.startswith("#,"):
            current_entry["flags"].append(line_stripped[2:].strip())
            current_entry["comments"].append(line)
            continue

        # Previous value comments (like #| msgid "old value")
        if line_stripped.startswith("#|"):
            current_entry["previous"].append(line)
            current_entry["comments"].append(line)
            continue

        # Translator comments
        if line_stripped.startswith("#"):
            current_entry["comments"].append(line)
            continue

        # msgid line
        if line_stripped.startswith("msgid_plural"):
            match = re.match(r'msgid_plural\s+"(.*)"', line_stripped)
            if match:
                current_entry["msgid_plural"] = match.group(1)
            in_msgid_plural = True
            in_msgid = False
            in_msgstr = False
            continue

        if line_stripped.startswith("msgid"):
            match = re.match(r'msgid\s+"(.*)"', line_stripped)
            if match:
                current_entry["msgid"] = match.group(1)
            in_msgid = True
            in_msgid_plural = False
            in_msgstr = False
            continue

        # msgstr line (can be indexed for plurals)
        if line_stripped.startswith("msgstr"):
            match = re.match(r'msgstr(?:\[(\d+)\])?\s+"(.*)"', line_stripped)
            if match:
                idx = int(match.group(1)) if match.group(1) else 0
                value = match.group(2)
                while len(current_entry["msgstr"]) <= idx:
                    current_entry["msgstr"].append("")
                current_entry["msgstr"][idx] = value
                msgstr_index = idx
            in_msgstr = True
            in_msgid = False
            in_msgid_plural = False
            continue

        # Continuation line (starts with ")
        if line_stripped.startswith('"') and line_stripped.endswith('"'):
            value = line_stripped[1:-1]
            if in_msgid:
                current_entry["msgid"] += value
            elif in_msgid_plural:
                current_entry["msgid_plural"] += value
            elif in_msgstr:
                if current_entry["msgstr"]:
                    current_entry["msgstr"][msgstr_index] += value
            continue

    # Don't forget the last entry
    if current_entry["msgid"]:
        entries.append(current_entry)

    return entries


def determine_app(locations: list[str]) -> str:
    """
    Determine which app a translation entry belongs to based on its file locations.

    Returns the app name or 'azureproject' for shared/project-level strings.
    """
    if not locations:
        return "azureproject"

    app_counts = defaultdict(int)

    for loc in locations:
        # Location format: .\app_name\file.py:line or ./app_name/file.py:line
        loc_clean = loc.replace("\\", "/").lstrip("./")

        for app in APPS:
            if loc_clean.startswith(f"{app}/"):
                app_counts[app] += 1
                break
        else:
            # Check for azureproject
            if loc_clean.startswith("azureproject/"):
                app_counts["azureproject"] += 1
            else:
                # Unknown location, assign to azureproject
                app_counts["azureproject"] += 1

    if not app_counts:
        return "azureproject"

    # Return the app with the most references
    return max(app_counts.keys(), key=lambda x: app_counts[x])


def format_entry(entry: dict) -> str:
    """Format a translation entry back to PO file format."""
    lines = []

    # Add comments (locations, flags, etc.)
    for comment in entry["comments"]:
        lines.append(comment)

    # msgid
    msgid = entry["msgid"]
    if "\n" in msgid or len(msgid) > 70:
        # Multiline format
        lines.append('msgid ""')
        for part in split_long_string(msgid):
            lines.append(f'"{part}"')
    else:
        lines.append(f'msgid "{msgid}"')

    # msgid_plural if present
    if entry.get("msgid_plural"):
        msgid_plural = entry["msgid_plural"]
        if "\n" in msgid_plural or len(msgid_plural) > 70:
            lines.append('msgid_plural ""')
            for part in split_long_string(msgid_plural):
                lines.append(f'"{part}"')
        else:
            lines.append(f'msgid_plural "{msgid_plural}"')

    # msgstr
    msgstr_list = entry.get("msgstr", [""])
    if not msgstr_list:
        msgstr_list = [""]

    if entry.get("msgid_plural"):
        # Plural forms
        for i, msgstr in enumerate(msgstr_list):
            if "\n" in msgstr or len(msgstr) > 70:
                lines.append(f'msgstr[{i}] ""')
                for part in split_long_string(msgstr):
                    lines.append(f'"{part}"')
            else:
                lines.append(f'msgstr[{i}] "{msgstr}"')
    else:
        # Singular form
        msgstr = msgstr_list[0] if msgstr_list else ""
        if "\n" in msgstr or len(msgstr) > 70:
            lines.append('msgstr ""')
            for part in split_long_string(msgstr):
                lines.append(f'"{part}"')
        else:
            lines.append(f'msgstr "{msgstr}"')

    return "\n".join(lines)


def split_long_string(s: str, max_len: int = 70) -> list[str]:
    """Split a long string into multiple lines for PO format."""
    if not s:
        return [""]

    # Handle explicit newlines
    if "\\n" in s:
        parts = []
        for part in s.split("\\n"):
            if parts:
                parts[-1] += "\\n"
            parts.append(part)
        return parts

    # Split by length
    if len(s) <= max_len:
        return [s]

    parts = []
    while s:
        if len(s) <= max_len:
            parts.append(s)
            break

        # Find a good split point (space)
        split_idx = s.rfind(" ", 0, max_len)
        if split_idx == -1:
            split_idx = max_len

        parts.append(s[:split_idx])
        s = s[split_idx:].lstrip()

    return parts


def write_po_file(filepath: Path, entries: list[dict], language: str, app_name: str):
    """Write entries to a PO file with proper header."""
    filepath.parent.mkdir(parents=True, exist_ok=True)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(get_po_header(language, app_name))

        for entry in entries:
            f.write(format_entry(entry))
            f.write("\n\n")


def main():
    """Main function to split locale files."""
    print("Starting locale file split...")
    print(f"Project root: {PROJECT_ROOT}")

    for lang in LANGUAGES:
        print(f"\nProcessing language: {lang}")

        source_file = PROJECT_ROOT / "locale" / lang / "LC_MESSAGES" / "django.po"

        if not source_file.exists():
            print(f"  Warning: {source_file} not found, skipping.")
            continue

        print(f"  Parsing {source_file}...")
        entries = parse_po_file(source_file)
        print(f"  Found {len(entries)} translation entries")

        # Group entries by app
        app_entries = defaultdict(list)

        for entry in entries:
            app = determine_app(entry["locations"])
            app_entries[app].append(entry)

        # Report counts
        print(f"  Distribution by app:")
        for app, app_entry_list in sorted(app_entries.items(), key=lambda x: -len(x[1])):
            print(f"    {app}: {len(app_entry_list)} entries")

        # Write app-specific files
        for app, app_entry_list in app_entries.items():
            if app == "azureproject":
                # Keep azureproject strings in the project-level locale
                output_file = PROJECT_ROOT / "locale" / lang / "LC_MESSAGES" / "django.po"
                print(f"  Writing {len(app_entry_list)} entries to {output_file} (project-level)")
            else:
                output_file = PROJECT_ROOT / app / "locale" / lang / "LC_MESSAGES" / "django.po"
                print(f"  Writing {len(app_entry_list)} entries to {output_file}")

            write_po_file(output_file, app_entry_list, lang, app)

    print("\nLocale split complete!")
    print("\nNext steps:")
    print("1. Run: python manage.py compilemessages")
    print("2. Test the translations by visiting the site in each language")


if __name__ == "__main__":
    main()
