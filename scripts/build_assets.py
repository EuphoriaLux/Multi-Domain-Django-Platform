#!/usr/bin/env python
"""Build the generated Crush.lu assets that are no longer committed.

    python scripts/build_assets.py            # build everything, then verify
    python scripts/build_assets.py --check    # verify only (CI, deploy, pytest)
    python scripts/build_assets.py --no-npm   # translations only (no Node needed)
    python scripts/build_assets.py --no-mo    # CSS + JS only

What it builds, and from what:

* ``crush_lu/static/crush_lu/css/{tailwind,journey,marketing}.css``
  from ``tailwind-src/crush_lu`` via ``npm run build:css`` (needs ``npm ci``).
* ``crush_lu/static/crush_lu/js/alpine/*.min.js`` (+ ``.map``)
  from the sibling sources via ``npm run build:js``.
* ``crush_lu/locale/{en,de,fr}/LC_MESSAGES/{django,djangojs}.mo``
  from the committed ``.po`` files with ``polib`` (``gettext`` is not installed
  here, and a malformed ``.mo`` 500s every DE/FR request in production).

The ``.po`` files stay committed. Other platforms' CSS and the other apps'
``.mo`` files stay committed too: they rarely change, so they never conflict.

Exit status is non-zero when anything is missing or inconsistent, so a deploy
step can run ``--check`` and refuse to ship without the files.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "crush_lu" / "static" / "crush_lu"
LOCALE = ROOT / "crush_lu" / "locale"
LANGUAGES = ("en", "de", "fr")
ALPINE_BUNDLES = ("core", "coach", "quiz", "journey", "connect")
CSS_FILES = tuple(
    STATIC / "css" / f"{name}.css" for name in ("tailwind", "journey", "marketing")
)
JS_FILES = tuple(
    STATIC / "js" / "alpine" / f"{bundle}.min.js{suffix}"
    for bundle in ALPINE_BUNDLES
    for suffix in ("", ".map")
)
# Sanity floor: an accidentally emptied catalogue must fail the deploy rather
# than ship untranslated pages. DE and FR have thousands of entries; EN is the
# source language and carries only a few dozen overrides, so it has no floor.
MIN_DJANGO_ENTRIES = {"de": 1000, "fr": 1000}


def _po_mo_pairs():
    """(po, mo) for every crush_lu catalogue that exists in the repo."""
    for lang in LANGUAGES:
        for domain in ("django", "djangojs"):
            po = LOCALE / lang / "LC_MESSAGES" / f"{domain}.po"
            if po.exists():
                yield po, po.with_suffix(".mo")


def build_node_assets() -> None:
    npm = shutil.which("npm")
    if not npm:
        sys.exit("npm not found: install Node 22 and run `npm ci` first.")
    if not (ROOT / "node_modules").is_dir():
        sys.exit("node_modules is missing: run `npm ci` first.")
    for script in ("build:css", "build:js"):
        print(f"-> npm run {script}")
        subprocess.run([npm, "run", script], cwd=ROOT, check=True)


def build_translations() -> None:
    import polib

    for po_path, mo_path in _po_mo_pairs():
        print(f"-> {_rel(mo_path)}")
        # save_as_mofile skips fuzzy and obsolete entries, like msgfmt.
        polib.pofile(str(po_path)).save_as_mofile(str(mo_path))


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def verify_files(paths, label: str) -> list[str]:
    problems = []
    for path in paths:
        if not path.is_file() or path.stat().st_size == 0:
            problems.append(f"{label} missing or empty: {_rel(path)}")
    return problems


def verify_translations() -> list[str]:
    """Every .mo loads, and holds exactly the translated entries of its .po."""
    import polib

    problems = []
    for po_path, mo_path in _po_mo_pairs():
        rel = _rel(mo_path)
        if not mo_path.is_file() or mo_path.stat().st_size == 0:
            problems.append(f".mo missing or empty: {rel}")
            continue
        try:
            compiled = {
                (e.msgctxt, e.msgid): (e.msgstr, tuple(sorted(e.msgstr_plural.items())))
                for e in polib.mofile(str(mo_path))
                if e.msgid
            }
        except Exception as exc:  # a malformed .mo is the production hazard
            problems.append(f".mo unreadable ({exc}): {rel}")
            continue
        source = polib.pofile(str(po_path))
        expected = {
            (e.msgctxt, e.msgid): (e.msgstr, tuple(sorted(e.msgstr_plural.items())))
            for e in source.translated_entries()
            if e.msgid
        }
        if compiled != expected:
            problems.append(f".mo does not match its .po (rebuild it): {rel}")
        floor = MIN_DJANGO_ENTRIES.get(mo_path.parent.parent.name, 0)
        if mo_path.stem == "django" and len(compiled) < floor:
            problems.append(f".mo has only {len(compiled)} entries: {rel}")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--check", action="store_true", help="verify only, build nothing"
    )
    parser.add_argument("--no-npm", action="store_true", help="skip the CSS/JS build")
    parser.add_argument("--no-mo", action="store_true", help="skip the translations")
    args = parser.parse_args(argv)

    if not args.check:
        if not args.no_npm:
            build_node_assets()
        if not args.no_mo:
            build_translations()

    problems = []
    if not args.no_npm:
        problems += verify_files(CSS_FILES, "CSS bundle")
        problems += verify_files(JS_FILES, "JS bundle")
    if not args.no_mo:
        problems += verify_translations()

    if problems:
        print("\nGenerated assets are not ready:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print("\nRun: python scripts/build_assets.py", file=sys.stderr)
        return 1
    print("Generated assets OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
