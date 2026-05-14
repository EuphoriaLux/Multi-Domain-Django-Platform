#!/usr/bin/env python3
"""
crush_lu/scripts/lint_design_tokens.py

Enforces the token / class policy described in crush_lu/STYLE.md.

Flagged today:
  - Hardcoded brand-purple / pink / indigo hexes in non-email HTML
    templates. Use {% load crush_brand %}{% brand_colors as brand %}
    and {{ brand.purple }} instead.
  - Deprecated button classes (.btn-primary, .btn-secondary, .btn-success,
    .btn-warning, .btn-info, .btn-outline-*) in non-exempt directories.
    Coach / admin / onboarding / journey / advent templates are exempt
    while their respective surfaces remain out of scope for the visual
    refactor.

Usage:
    # Lint the in-scope template tree:
    python crush_lu/scripts/lint_design_tokens.py
    # Lint specific files (used by pre-commit hooks):
    python crush_lu/scripts/lint_design_tokens.py path/a.html path/b.html

Exit code: 0 = clean, 1 = violations found.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


# Hex strings that drift from the brand palette and should never appear
# in non-email templates. Email templates use literal hex because most
# mail clients strip CSS custom properties.
BRAND_DRIFT_HEX = {
    "#7c3aed": "use brand.purple (#9b59b6) via {% load crush_brand %}",
    "#7C3AED": "use brand.purple (#9b59b6) via {% load crush_brand %}",
    "#4f46e5": "use brand.purple (#9b59b6) via {% load crush_brand %}",
    "#4F46E5": "use brand.purple (#9b59b6) via {% load crush_brand %}",
    "#6366f1": "use brand.purple (#9b59b6) via {% load crush_brand %}",
    "#6366F1": "use brand.purple (#9b59b6) via {% load crush_brand %}",
}

# Legacy button classes. See crush_lu/STYLE.md §2.
DEPRECATED_BUTTON_CLASSES = {
    "btn-primary": ".btn-crush-primary (gradient CTA) or .btn-crush-solid",
    "btn-secondary": ".btn-crush-outline or .btn-crush-solid",
    "btn-outline-primary": ".btn-crush-outline",
    "btn-outline-secondary": ".btn-crush-outline",
    "btn-outline-danger": ".btn-danger",
    "btn-success": ".btn-crush-solid + bg-green-* override",
    "btn-warning": ".btn-crush-solid + bg-yellow-* override",
    "btn-info": ".btn-crush-solid + bg-blue-* override",
}

# Path-component substrings that exempt a file from the deprecated-button
# rule. Coach / admin / onboarding / journey / advent are intentionally
# out of scope for the visual refactor.
BUTTON_EXEMPT_PARTS = (
    "admin",
    "coach_",
    "coach.",
    "coaches",
    "advent",
    "journey",
    "gift",
    "wonderland",
    "pre_screening",
    "create_profile",
    "edit_profile",
    "onboarding",
    "welcome",
)

# Path-component substrings that mark a file as an email template. Email
# templates are exempt from the brand-hex rule.
EMAIL_DIR_PARTS = ("emails", "email")

# Filename prefixes that are exempt from the brand-hex rule. Ghost-story
# SVG decorations use a specific dark-mode purple as a fill color for
# illustration consistency — these are visual assets, not brand tokens.
# Test/scratch templates (test_*.html) are also exempt — they're not
# shipped product surfaces.
HEX_EXEMPT_FILENAME_PREFIXES = (
    "ghost-story-",
    "test_",
)


def _has_part(path: Path, needles: tuple[str, ...]) -> bool:
    parts = [p.lower() for p in path.parts]
    for needle in needles:
        n = needle.lower()
        for part in parts:
            if n in part:
                return True
    return False


def is_email_template(path: Path) -> bool:
    return _has_part(path, EMAIL_DIR_PARTS)


def is_hex_exempt_filename(path: Path) -> bool:
    name = path.name.lower()
    return any(name.startswith(prefix) for prefix in HEX_EXEMPT_FILENAME_PREFIXES)


def is_button_exempt(path: Path) -> bool:
    return _has_part(path, BUTTON_EXEMPT_PARTS)


def scan_file(path: Path) -> list[str]:
    if path.suffix.lower() != ".html":
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []

    violations: list[str] = []
    hex_exempt = is_email_template(path) or is_hex_exempt_filename(path)
    button_exempt = is_button_exempt(path) or is_hex_exempt_filename(path)

    for n, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        # Skip Django template comments and HTML comment shells. Inline
        # comments mid-line still get scanned (good).
        if stripped.startswith(("{#", "<!--")):
            continue

        if not hex_exempt:
            for hex_val, advice in BRAND_DRIFT_HEX.items():
                if hex_val in line:
                    violations.append(
                        f"{path}:{n}: hardcoded {hex_val} -- {advice}"
                    )

        if not button_exempt:
            for cls, advice in DEPRECATED_BUTTON_CLASSES.items():
                # Class must appear as a whole token inside a class=..
                # attribute or similar — not as a substring of another
                # word (e.g. "btn-primary-modal" is fine).
                if re.search(rf"(?<![\w-]){re.escape(cls)}(?![\w-])", line):
                    violations.append(
                        f"{path}:{n}: legacy class .{cls} -- prefer {advice}"
                    )

    return violations


def collect_files(roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if root.is_file():
            files.append(root)
        elif root.is_dir():
            files.extend(sorted(root.rglob("*.html")))
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="crush_lu design-token linter")
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files or directories to scan. Default: crush_lu/templates/crush_lu",
    )
    args = parser.parse_args(argv)

    if args.paths:
        roots = [Path(p) for p in args.paths]
    else:
        roots = [Path("crush_lu/templates/crush_lu")]

    files = collect_files(roots)
    all_violations: list[str] = []
    for path in files:
        all_violations.extend(scan_file(path))

    if all_violations:
        print("crush_lu design-token lint violations:\n")
        for v in all_violations:
            print(f"  {v}")
        print(f"\nTotal: {len(all_violations)} violation(s).")
        print("See crush_lu/STYLE.md for the canonical policy.")
        return 1

    print(f"crush_lu design-token lint: clean ({len(files)} file(s) scanned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
