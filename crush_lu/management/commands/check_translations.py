"""
Check for missing customer-facing translations in Crush.lu .po files.

Filters out admin-only strings (sourced exclusively from admin/ paths)
to focus on what end users actually see. Fuzzy entries are included by
default since Django treats them as untranslated at runtime.

Usage:
    python manage.py check_translations --summary
    python manage.py check_translations --language de
    python manage.py check_translations --no-fuzzy --summary
    python manage.py check_translations --include-js
"""

import os

import polib
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Check for missing customer-facing translations in .po files"

    def add_arguments(self, parser):
        parser.add_argument(
            "--language",
            choices=["de", "fr"],
            help="Check one language only (default: both)",
        )
        parser.add_argument(
            "--no-fuzzy",
            action="store_true",
            help="Exclude fuzzy entries (included by default since Django ignores them)",
        )
        parser.add_argument(
            "--summary",
            action="store_true",
            help="Show counts only, no detailed listing",
        )
        parser.add_argument(
            "--include-js",
            action="store_true",
            help="Also check djangojs.po files",
        )

    def handle(self, *args, **options):
        languages = [options["language"]] if options["language"] else ["de", "fr"]
        filenames = ["django.po"]
        if options["include_js"]:
            filenames.append("djangojs.po")

        locale_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "locale",
        )

        for lang in languages:
            for filename in filenames:
                po_path = os.path.join(lang, "LC_MESSAGES", filename)
                full_path = os.path.join(locale_dir, po_path)
                if not os.path.exists(full_path):
                    self.stderr.write(self.style.WARNING(f"File not found: {po_path}"))
                    continue
                self._check_file(full_path, lang, filename, options)

    def _is_admin_only(self, entry):
        """Return True if ALL source locations are from admin paths."""
        if not entry.occurrences:
            return False
        return all(
            "\\admin\\" in src or "/admin/" in src
            for src, _line in entry.occurrences
        )

    def _check_file(self, path, lang, filename, options):
        po = polib.pofile(path)

        # Gather customer-facing untranslated entries
        untranslated = [
            e for e in po.untranslated_entries() if not self._is_admin_only(e)
        ]

        fuzzy = []
        if not options["no_fuzzy"]:
            fuzzy = [e for e in po.fuzzy_entries() if not self._is_admin_only(e)]

        # Count totals for context
        total = len([e for e in po if not e.obsolete and not self._is_admin_only(e)])
        admin_count = len([e for e in po if not e.obsolete and self._is_admin_only(e)])

        self.stdout.write("")
        self.stdout.write(
            self.style.HTTP_INFO(f"=== {lang.upper()} / {filename} ===")
        )
        self.stdout.write(f"  Total customer-facing entries: {total}")
        self.stdout.write(f"  Admin-only entries (excluded): {admin_count}")
        self.stdout.write(
            self.style.ERROR(f"  Untranslated: {len(untranslated)}")
            if untranslated
            else f"  Untranslated: 0"
        )
        if not options["no_fuzzy"]:
            self.stdout.write(
                self.style.WARNING(f"  Fuzzy: {len(fuzzy)}")
                if fuzzy
                else f"  Fuzzy: 0"
            )
        translated = total - len(untranslated) - (len(fuzzy) if not options["no_fuzzy"] else 0)
        pct = (translated / total * 100) if total else 100
        self.stdout.write(f"  Coverage: {pct:.1f}%")

        if options["summary"]:
            return

        # Detailed listing grouped by source file
        if untranslated:
            self.stdout.write("")
            self.stdout.write(self.style.ERROR("  Untranslated strings:"))
            self._print_entries(untranslated)

        if fuzzy:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("  Fuzzy strings:"))
            self._print_entries(fuzzy)

    def _print_entries(self, entries):
        """Print entries grouped by their first source file."""
        by_source = {}
        for entry in entries:
            if entry.occurrences:
                src = entry.occurrences[0][0]
            else:
                src = "(no source)"
            by_source.setdefault(src, []).append(entry)

        for src in sorted(by_source):
            self.stdout.write(f"    {src}")
            for entry in by_source[src]:
                msgid = entry.msgid
                if len(msgid) > 80:
                    msgid = msgid[:77] + "..."
                # Replace non-ASCII chars that may break Windows console encoding
                msgid = msgid.encode("ascii", "replace").decode("ascii")
                lines = ", ".join(f"{s}:{l}" for s, l in entry.occurrences[:3])
                if len(entry.occurrences) > 3:
                    lines += f" (+{len(entry.occurrences) - 3} more)"
                self.stdout.write(f'      "{msgid}"')
                self.stdout.write(f"        {lines}")
