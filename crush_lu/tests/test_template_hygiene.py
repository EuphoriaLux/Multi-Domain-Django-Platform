"""Template hygiene guards.

Django's ``{# ... #}`` comment syntax is single-line only: the lexer never
matches an opener whose ``#}`` sits on a later line, so the whole block is
emitted as visible page text (this leaked internal beta notes on the Connect
catalogue page and teaser, found in the 2026-07-10 staging dry-run). Multi-line
notes must use ``{% comment %} ... {% endcomment %}``.
"""

import re
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


#: Templates that already carry CSP-unsafe Alpine expressions. They predate
#: this guard and are exempt wholesale rather than fixed here — touching them
#: is its own change. Do not add to this list: a new entry means a component
#: that silently does nothing in the browser.
CSP_ALPINE_DEBT = {
    "admin/crush_lu/email_template_manager.html",
    "crush_lu/account_settings.html",
    "crush_lu/changelog/list.html",
    "crush_lu/event_ticket.html",
    "crush_lu/partials/edit_account_notifications.html",
}

#: The CSP build's evaluator resolves a bare property or method name and
#: nothing else — no operators, no calls, no object literals.
_BARE_NAME = re.compile(r"^[A-Za-z_$][\w$]*$")


def test_no_csp_unsafe_alpine_expressions():
    """`alpinejs-csp-3.13.3.min.js` evaluates no expressions.

    `x-data="{ open: false }"`, `@click="open = !open"` and `x-show="!open"`
    parse fine and then do nothing at runtime, so the component is inert with
    no console error and no failing view test — the disclosure just never
    opens. Expose getters and methods from an `Alpine.data` component and bind
    their bare names instead (see `makeModal` in alpine-components.js).
    """
    offenders = []
    for template in sorted(TEMPLATES_DIR.rglob("*.html")):
        rel = str(template.relative_to(TEMPLATES_DIR)).replace("\\", "/")
        if rel in CSP_ALPINE_DEBT:
            continue
        for lineno, line in enumerate(
            template.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for match in re.finditer(r'(x-data|x-show|@click)="([^"]*)"', line):
                expression = match.group(2).strip()
                if expression and not _BARE_NAME.match(expression):
                    offenders.append(f"{rel}:{lineno} {match.group(1)}={expression!r}")
    assert not offenders, (
        "CSP-unsafe Alpine expression — the CSP build evaluates bare property "
        "and method names only, so these are inert at runtime: " + ", ".join(offenders)
    )
