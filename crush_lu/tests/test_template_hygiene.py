"""Template hygiene guards.

Django's ``{# ... #}`` comment syntax is single-line only: the lexer never
matches an opener whose ``#}`` sits on a later line, so the whole block is
emitted as visible page text (this leaked internal beta notes on the Connect
catalogue page and teaser, found in the 2026-07-10 staging dry-run). Multi-line
notes must use ``{% comment %} ... {% endcomment %}``.
"""

from pathlib import Path

import crush_lu

TEMPLATES_DIR = Path(crush_lu.__file__).resolve().parent / "templates"


def test_no_multiline_template_comments():
    offenders = []
    for template in sorted(TEMPLATES_DIR.rglob("*.html")):
        for lineno, line in enumerate(
            template.read_text(encoding="utf-8").splitlines(), start=1
        ):
            # Every {# opened on a line must close on that same line, else
            # Django renders the "comment" as literal text.
            tail = line
            while "{#" in tail:
                tail = tail.split("{#", 1)[1]
                if "#}" not in tail:
                    offenders.append(f"{template.relative_to(TEMPLATES_DIR)}:{lineno}")
                    break
                tail = tail.split("#}", 1)[1]
    assert not offenders, (
        "Multi-line {# ... #} renders as visible text — use "
        "{% comment %}...{% endcomment %} instead: " + ", ".join(offenders)
    )
