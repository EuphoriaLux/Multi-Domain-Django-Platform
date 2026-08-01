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


def test_no_csrf_token_read_from_cookie():
    """JS must take the CSRF token from the hidden input, never document.cookie.

    ``CSRF_COOKIE_HTTPONLY = True`` (settings.py / production.py), so the
    ``csrftoken`` cookie is invisible to JavaScript by design — ``base.html``
    renders ``<input name="csrfmiddlewaretoken">`` for HTMX and everything else
    to read. A fetch() that scrapes ``document.cookie`` therefore sends an empty
    X-CSRFToken and Django answers 403 with an HTML error page, which the caller
    then fails to parse as JSON ("Unexpected token 'C'").

    All four SumUp payment entry points shipped with this bug and every one of
    them 403'd in the browser while the 14 view tests stayed green — Django's
    test client sets ``enforce_csrf_checks=False``, so the suite never exercised
    the path that was broken.
    """
    offenders = []
    for template in sorted(TEMPLATES_DIR.rglob("*.html")):
        for lineno, line in enumerate(
            template.read_text(encoding="utf-8").splitlines(), start=1
        ):
            # Match the cookie NAME as a quoted literal -- getCookie('csrftoken')
            # or cookie.substring(0, 10) === ('csrftoken='). Prose mentioning the
            # cookie in a comment does not quote it, so this stays comment-safe.
            if "'csrftoken" in line or '"csrftoken' in line:
                offenders.append(f"{template.relative_to(TEMPLATES_DIR)}:{lineno}")
    assert not offenders, (
        "CSRF token read from document.cookie, but CSRF_COOKIE_HTTPONLY=True "
        "makes that cookie unreadable — use "
        "document.querySelector('input[name=\"csrfmiddlewaretoken\"]').value "
        "instead: " + ", ".join(offenders)
    )
