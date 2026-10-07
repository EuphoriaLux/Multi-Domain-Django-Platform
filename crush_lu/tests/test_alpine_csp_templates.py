"""Guard: crush.lu templates must stay within what the Alpine CSP build runs.

The shipped bundle is ``@alpinejs/csp``: it cannot evaluate inline object
literals or call arguments in ``x-data`` and cannot run assignments in event
handlers. Such expressions fail silently (a console warning, state unchanged).
Move the behaviour into an ``Alpine.data`` component and read initial values
from ``data-*`` attributes. See crush_lu/STYLE.md section 7.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

TEMPLATE_ROOT = Path(__file__).resolve().parent.parent / "templates"

XDATA = re.compile(r"""\bx-data\s*=\s*(?P<q>["'])(?P<expr>.*?)(?P=q)""", re.S)
HANDLER = re.compile(
    r"""(?<![\w-])(?:@|x-on:)[\w.:-]+\s*=\s*(?P<q>["'])(?P<expr>.*?)(?P=q)""", re.S
)
ASSIGNMENT = re.compile(r"(?<![=!<>])=(?!=)")
DJANGO_TAGS = re.compile(r"\{%.*?%\}|\{\{.*?\}\}")


def _templates():
    return sorted(TEMPLATE_ROOT.rglob("*.html"))


class AlpineCspTemplateTests(SimpleTestCase):
    def test_x_data_is_a_bare_component_name(self):
        offenders = []
        for path in _templates():
            text = path.read_text(encoding="utf-8")
            for m in XDATA.finditer(text):
                expr = m.group("expr").strip()
                if not expr:
                    continue
                stripped = DJANGO_TAGS.sub("", expr).strip()
                if re.fullmatch(r"[A-Za-z_$][\w$]*(\(\s*\))?", stripped):
                    continue
                offenders.append(f'{path.relative_to(TEMPLATE_ROOT)}: x-data="{expr}"')
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_event_handlers_have_no_assignments(self):
        offenders = []
        for path in _templates():
            text = path.read_text(encoding="utf-8")
            for m in HANDLER.finditer(text):
                expr = DJANGO_TAGS.sub("", m.group("expr"))
                expr = re.sub(r"'[^']*'|\"[^\"]*\"", "''", expr)
                if ASSIGNMENT.search(expr):
                    offenders.append(
                        f'{path.relative_to(TEMPLATE_ROOT)}: handler "{m.group("expr")}"'
                    )
        self.assertEqual(offenders, [], "\n".join(offenders))
