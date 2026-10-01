"""Regenerate the crush_lu djangojs.po catalogs (en/de/fr) from the JS sources.

Usage (from the repo root, venv active):

    python scripts/i18n/makemessages_js.py            # en de fr
    python scripts/i18n/makemessages_js.py de fr      # only these languages
    npm run i18n:js                                   # same, via npm

Why a wrapper: `makemessages -d djangojs` run at the repo root walks
node_modules and every minified bundle, so each string appears twice (source +
`alpine/*.min.js`) and vendor code pollutes the catalog. This runs it from
crush_lu/ (where locale/ lives, so LOCALE_PATHS does not matter) and ignores
`*.min.js` and `vendor/*`. It needs GNU gettext (xgettext, msguniq, msgmerge)
on PATH, which the Windows dev box and CI images have; the Claude web sandbox
does not.

After editing the .po, build the .mo with polib (never hand-merge a .mo):

    python -c "import polib; polib.pofile('crush_lu/locale/de/LC_MESSAGES/djangojs.po').save_as_mofile('crush_lu/locale/de/LC_MESSAGES/djangojs.mo')"
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_DIR = ROOT / "crush_lu"
DEFAULT_LANGUAGES = ["en", "de", "fr"]
IGNORE_PATTERNS = ["*.min.js", "vendor/*", "node_modules/*"]


def build_command(languages=None, python=None):
    """The manage.py invocation (run with cwd=crush_lu/)."""
    cmd = [
        python or sys.executable,
        str(ROOT / "manage.py"),
        "makemessages",
        "-d",
        "djangojs",
    ]
    for lang in languages or DEFAULT_LANGUAGES:
        cmd += ["-l", lang]
    for pattern in IGNORE_PATTERNS:
        cmd += ["--ignore", pattern]
    return cmd


def main(argv):
    if not shutil.which("xgettext"):
        print(
            "xgettext not found: install GNU gettext to run makemessages.",
            file=sys.stderr,
        )
        return 2
    return subprocess.call(build_command(argv or None), cwd=APP_DIR)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
