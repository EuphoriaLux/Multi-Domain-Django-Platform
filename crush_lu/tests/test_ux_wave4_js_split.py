"""UX Wave 4 · WP8b: the Alpine components ship as per-feature bundles.

Spec: ai-memory-hub/specs (UX Wave 4, finding 8-17, PR 2 of 2).

``crush_lu/static/crush_lu/js/alpine/`` holds ES-module entries (core, coach,
quiz, journey, connect) plus ``shared.js`` helpers; ``npm run build:js``
bundles each entry into a committed ``<entry>.min.js``. base.html loads only
core; a page adds a feature bundle through ``crush_lu/partials/
alpine_bundle.html`` in its ``pre_alpine_js`` block. A missing include fails
silently in the browser (Alpine leaves an unknown x-data inert), so
``TemplateBundleCoverageTests`` statically proves that every x-data component
a template uses is registered by a bundle that template's pages load.
"""

import re
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.template import engines
from django.template.base import Lexer, TokenType
from django.test import SimpleTestCase, TestCase

from crush_lu.models import CrushCoach, UserDataConsent

BASE = Path(settings.BASE_DIR)
ALPINE_DIR = BASE / "crush_lu" / "static" / "crush_lu" / "js" / "alpine"
BUNDLES = ["core", "coach", "quiz", "journey", "connect"]
BUNDLE_PARTIAL = "crush_lu/partials/alpine_bundle.html"
TEMPLATE_ROOTS = [BASE / "crush_lu" / "templates", BASE / "core" / "templates"]
HOST = "crush.lu"

# A registration statement (not the photoUpload deprecation message, which
# quotes `Alpine.data('photoUpload')` inside a string).
ALPINE_DATA_RE = re.compile(r"""^\s*Alpine\.data\(\s*"([A-Za-z0-9_$]+)\"""", re.M)
# The component name at the start of an x-data value; inline object literals
# (`x-data="{ open: false }"`) start with "{" and are not components.
X_DATA_RE = re.compile(r"""x-data\s*=\s*["']\s*([A-Za-z_$][\w$]*)""")

# `{% include <variable> %}` targets, keyed by (template, variable). Every
# template these can render must be covered: a new step or section that is
# not listed here is treated as a standalone fragment (see _available).
DYNAMIC_INCLUDES = {
    ("crush_lu/crush_connect/onboarding.html", "step_template"): (
        "crush_lu/crush_connect/onboarding_steps/_step*.html"
    ),
    ("crush_lu/crush_connect/profile_edit.html", "cfg.template"): (
        "crush_lu/crush_connect/onboarding_steps/_step*.html"
    ),
    ("crush_lu/edit_profile.html", "section_template"): (
        "crush_lu/partials/edit_*.html"
    ),
    ("crush_lu/components/event_guide_ghost.html", "illustration"): (
        "crush_lu/includes/ghost-story-*.html"
    ),
}


def _code_lines(path):
    """Source without comment lines (the helpers document an example
    `Alpine.data("myTabs", ...)` that is not a registration)."""
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith(("//", "*", "/*"))
    )


@lru_cache(maxsize=None)
def registrations():
    """{component name: bundle} from the readable bundle sources."""
    owner = {}
    for bundle in BUNDLES:
        for name in ALPINE_DATA_RE.findall(_code_lines(ALPINE_DIR / f"{bundle}.js")):
            owner.setdefault(name, []).append(bundle)
    return owner


def _literal(bit):
    if len(bit) >= 2 and bit[0] == bit[-1] and bit[0] in "'\"":
        return bit[1:-1]
    return None


def _engine():
    return engines["django"].engine


class _Parsed:
    def __init__(self, source):
        self.extends = None
        self.dynamic_extends = False
        self.includes = []
        self.dynamic_includes = []
        self.bundles = set()
        self.x_data = set()
        in_comment = False
        for token in Lexer(source).tokenize():
            if token.token_type == TokenType.BLOCK:
                bits = token.split_contents()
                if bits[0] in ("comment", "verbatim"):
                    in_comment = True
                elif bits[0] in ("endcomment", "endverbatim"):
                    in_comment = False
                elif in_comment:
                    continue
                elif bits[0] == "extends":
                    self.extends = _literal(bits[1])
                    self.dynamic_extends = self.extends is None
                elif bits[0] == "include":
                    target = _literal(bits[1])
                    if target is None:
                        self.dynamic_includes.append(bits[1])
                    elif target == BUNDLE_PARTIAL:
                        kwargs = dict(b.split("=", 1) for b in bits[2:] if "=" in b)
                        self.bundles.add(_literal(kwargs["bundle"]))
                    else:
                        self.includes.append(target)
            elif token.token_type == TokenType.TEXT and not in_comment:
                self.x_data.update(X_DATA_RE.findall(token.contents))


def _template_files():
    """{template name: file} for every template the loaders actually serve
    from the Crush.lu roots (a shadowed copy never renders)."""
    files = {}
    for root in TEMPLATE_ROOTS:
        for path in sorted(root.rglob("*.html")):
            name = path.relative_to(root).as_posix()
            if name in files:
                continue
            origin = Path(_engine().get_template(name).origin.name)
            if origin.resolve() == path.resolve():
                files[name] = path
    return files


class _Graph:
    def __init__(self):
        self.files = _template_files()
        self.parsed = {}
        self.includers = {}
        for name in self.files:
            parsed = self.parse(name)
            targets = list(parsed.includes)
            for var in parsed.dynamic_includes:
                pattern = DYNAMIC_INCLUDES.get((name, var))
                if pattern:
                    targets += [n for n in self.files if Path(n).match(pattern)]
            for target in targets:
                self.includers.setdefault(target, set()).add(name)
        self._chain = {}
        self._avail = {}

    def parse(self, name):
        if name not in self.parsed:
            if name in self.files:
                source = self.files[name].read_text(encoding="utf-8")
            else:  # e.g. Django's own admin templates
                source = _engine().get_template(name).source
            self.parsed[name] = _Parsed(source)
        return self.parsed[name]

    def chain(self, name):
        """Bundles loaded by the template itself or its extends-ancestors."""
        if name not in self._chain:
            parsed = self.parse(name)
            bundles = set(parsed.bundles)
            if parsed.extends:
                bundles |= self.chain(parsed.extends)
            self._chain[name] = frozenset(bundles)
        return self._chain[name]

    def is_fragment(self, name):
        parsed = self.parse(name)
        return not parsed.extends and not parsed.dynamic_extends

    def available(self, name, _stack=()):
        """Bundles guaranteed loaded wherever `name` renders."""
        if name in self._avail:
            return self._avail[name]
        if name in _stack:
            return None
        hosts = [
            self.available(i, _stack + (name,))
            for i in sorted(self.includers.get(name, ()))
        ]
        hosts = [h for h in hosts if h is not None]
        if hosts:
            inherited = frozenset.intersection(*hosts)
        elif self.is_fragment(name) and not name.startswith("admin/"):
            # A standalone HTMX fragment swaps into a crush.lu page, which
            # always has core. Anything more must come from a real includer.
            inherited = frozenset({"core"})
        else:
            inherited = frozenset()
        self._avail[name] = self.chain(name) | inherited
        return self._avail[name]


class BundleRegistryTests(SimpleTestCase):
    def test_every_component_is_registered_by_exactly_one_bundle(self):
        owner = registrations()
        self.assertGreater(len(owner), 100)
        duplicated = {n: b for n, b in owner.items() if len(b) != 1}
        self.assertEqual(duplicated, {})

    def test_feature_components_left_the_core_bundle(self):
        owner = {n: b[0] for n, b in registrations().items()}
        for name, bundle in {
            "coachCheckin": "coach",
            "todaysFocus": "coach",
            "campaignComposer": "coach",
            "quizQuestionForm": "quiz",
            "timelineSort": "journey",
            "giftCreateForm": "journey",
            "connectOnboarding": "connect",
            "heightSlider": "connect",
            "navbar": "core",
            "coachPushPreferences": "core",  # account settings, not a coach page
        }.items():
            self.assertEqual(owner.get(name), bundle, name)

    def test_feature_bundles_import_helpers_instead_of_redefining_them(self):
        shared = (ALPINE_DIR / "shared.js").read_text(encoding="utf-8")
        for helper in ("mixin", "makeModal", "makeTabs", "makeConfirm", "notifyError"):
            self.assertIn(f"export function {helper}(", shared)
            for bundle in BUNDLES:
                source = _code_lines(ALPINE_DIR / f"{bundle}.js")
                self.assertNotIn(f"function {helper}(", source, bundle)
        coach = (ALPINE_DIR / "coach.js").read_text(encoding="utf-8")
        self.assertIn('import { makeModal, mixin } from "./shared.js";', coach)


class TemplateBundleCoverageTests(SimpleTestCase):
    def test_every_x_data_component_is_loaded_where_the_template_renders(self):
        graph = _Graph()
        owner = {n: b[0] for n, b in registrations().items()}
        checked = 0
        missing = []
        for name in sorted(graph.files):
            used = {c for c in graph.parse(name).x_data if c in owner}
            if not used:
                continue
            checked += 1
            available = graph.available(name)
            for component in sorted(used):
                if owner[component] not in available:
                    missing.append(
                        f"{name}: x-data={component!r} needs the "
                        f"{owner[component]!r} bundle, page loads {sorted(available)}"
                    )
        self.assertEqual(missing, [])
        self.assertGreater(checked, 80)

    def test_the_checker_catches_a_missing_include(self):
        """Guard the guard: drop the coach include from one page and the
        coverage check must report it."""
        graph = _Graph()
        name = "crush_lu/coach_event_checkin.html"
        self.assertIn("coach", graph.available(name))
        graph.parse(name).bundles.discard("coach")
        graph._chain.clear()
        graph._avail.clear()
        self.assertNotIn("coach", graph.available(name))

    def test_dynamic_includes_are_all_mapped(self):
        graph = _Graph()
        unmapped = []
        for name in sorted(graph.files):
            for var in graph.parse(name).dynamic_includes:
                # Icon / form-widget includes render SVG or <input> partials
                # with no Alpine; anything else must be mapped above.
                if (name, var) not in DYNAMIC_INCLUDES and var not in (
                    "perk.icon",
                    "cat.ghost_include",
                    "zodiac.template",
                    "option.template_name",
                ):
                    unmapped.append((name, var))
        self.assertEqual(unmapped, [])

    def test_bundle_partial_names_real_bundles(self):
        graph = _Graph()
        used = set()
        for name in graph.files:
            used |= graph.parse(name).bundles
        self.assertEqual(used, set(BUNDLES))


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.scripts.append(dict(attrs))


def _script_srcs(html):
    parser = _Scripts()
    parser.feed(html)
    return [s.get("src", "") for s in parser.scripts if s.get("src")]


def _bundles_on(html):
    srcs = _script_srcs(html)
    found = []
    for bundle in BUNDLES:
        if any(f"crush_lu/js/alpine/{bundle}.min.js" in s for s in srcs):
            found.append(bundle)
    return found


def _alpine_index(srcs):
    return next(i for i, s in enumerate(srcs) if "alpinejs-csp" in s)


class PageBundleTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_home_ships_only_the_core_bundle(self):
        response = self.client.get("/en/", HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_bundles_on(response.content.decode()), ["core"])

    def test_coach_dashboard_adds_the_coach_bundle_before_alpine(self):
        user = get_user_model().objects.create_user(
            username="coach@example.com",
            email="coach@example.com",
            password="testpass123",
            first_name="Coach",
        )
        CrushCoach.objects.create(user=user, is_active=True)
        UserDataConsent.objects.update_or_create(
            user=user, defaults={"crushlu_consent_given": True}
        )
        self.client.force_login(user)
        response = self.client.get("/en/coach/dashboard/", HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertTemplateUsed(response, "crush_lu/coach_dashboard.html")
        self.assertEqual(_bundles_on(html), ["core", "coach"])
        srcs = _script_srcs(html)
        coach = next(i for i, s in enumerate(srcs) if "alpine/coach.min.js" in s)
        self.assertLess(coach, _alpine_index(srcs))
