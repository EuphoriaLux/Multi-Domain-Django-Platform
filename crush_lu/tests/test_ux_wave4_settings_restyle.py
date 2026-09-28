"""Account drill-down restyle (UX Wave 4 · WP9c, finding 8-07).

The surviving settings surface (/profile/edit/?section=account[&sub=...])
follows STYLE.md: one gradient CTA per page, true-white resting cards, and
section titles as h2 under the page h1. The linter gained the matching
one-gradient-CTA and hand-rolled-button rules.
"""

import importlib.util
import tempfile
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from crush_lu.models import CrushCoach, CrushProfile
from crush_lu.models.profiles import UserDataConsent

User = get_user_model()

HOST = "crush.lu"
ACCOUNT = "/en/profile/edit/?section=account"
SUBS = ("", "&sub=settings", "&sub=notifications", "&sub=danger")
REPO_ROOT = Path(__file__).resolve().parents[2]


class _Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.headings = []
        self.primaries = []
        self.classes = []

    def handle_starttag(self, tag, attrs):
        classes = (dict(attrs).get("class") or "").split()
        self.classes.append((tag, classes))
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.headings.append(int(tag[1]))
        if "btn-crush-primary" in classes:
            self.primaries.append(classes)


def _make_user(email, **profile):
    user = User.objects.create_user(username=email, email=email, password="x")
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    EmailAddress.objects.update_or_create(
        user=user, email=email, defaults={"verified": True, "primary": True}
    )
    if profile:
        defaults = dict(
            user=user,
            date_of_birth=date(1995, 1, 1),
            gender="M",
            location="Luxembourg",
            is_approved=True,
            verification_status="verified",
        )
        defaults.update(profile)
        CrushProfile.objects.create(**defaults)
    return user


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class AccountRestyleTests(TestCase):
    def setUp(self):
        cache.clear()
        coach = _make_user(
            "coach@example.com", phone_number="+352621123456", phone_verified=True
        )
        CrushCoach.objects.create(user=coach, is_active=True)
        self.users = {
            "approved_coach": coach,
            "incomplete": _make_user(
                "incomplete@example.com",
                is_approved=False,
                verification_status="incomplete",
            ),
            "no_profile": _make_user("noprofile@example.com"),
        }

    def _pages(self):
        for label, user in self.users.items():
            self.client.force_login(user)
            for sub in SUBS:
                # The HTMX response is the partial alone, without the shell.
                response = self.client.get(
                    ACCOUNT + sub, HTTP_HOST=HOST, HTTP_HX_REQUEST="true"
                )
                self.assertEqual(response.status_code, 200, (label, sub))
                page = _Page()
                page.feed(response.content.decode())
                yield (label, sub), page

    def test_at_most_one_gradient_cta_per_page(self):
        counts = {key: len(page.primaries) for key, page in self._pages()}
        too_many = {key: n for key, n in counts.items() if n > 1}
        self.assertEqual(too_many, {})
        # The create-profile states still get their one CTA.
        self.assertEqual(counts[("incomplete", "&sub=settings")], 1)
        self.assertEqual(counts[("no_profile", "&sub=settings")], 1)

    def test_gradient_cta_drops_ad_hoc_padding(self):
        for key, page in self._pages():
            for classes in page.primaries:
                self.assertNotIn("rounded-lg", classes, key)
                self.assertNotIn("px-4", classes, key)

    def test_one_h1_then_h2_section_titles_in_order(self):
        bad = {}
        for key, page in self._pages():
            levels = page.headings
            ok = (
                levels[:1] == [1]
                and levels.count(1) == 1
                and all(b - a <= 1 for a, b in zip(levels, levels[1:]))
                and not {5, 6} & set(levels)
            )
            if key[1] and 2 not in levels:
                ok = False
            if not ok:
                bad[key] = levels
        self.assertEqual(bad, {})

    def test_h1_is_the_shared_page_header_visible_on_desktop(self):
        # components/page_header.html: shown on desktop, sr-only on mobile only
        # (where the top bar carries the title). A plain sr-only h1 hides the
        # page title from desktop users.
        h1s = {
            key: [classes for tag, classes in page.classes if tag == "h1"]
            for key, page in self._pages()
        }
        bad = {
            key: found
            for key, found in h1s.items()
            if len(found) != 1
            or "sr-only" in found[0]
            or "max-lg:sr-only" not in found[0]
        }
        self.assertEqual(bad, {})
        self.assertIn("text-red-600", h1s[("approved_coach", "&sub=danger")][0])

    def test_cards_use_the_surface_token_not_lavender_bg_white(self):
        offenders = []
        for key, page in self._pages():
            for tag, classes in page.classes:
                if "absolute" in classes:
                    continue  # floating panels (the language dropdown)
                if "shadow-lg" in classes or (
                    "bg-white" in classes and "rounded-xl" in classes
                ):
                    offenders.append((key, tag, " ".join(classes)))
        self.assertEqual(offenders, [])

    def test_coach_push_enable_is_solid_not_gradient(self):
        self.client.force_login(self.users["approved_coach"])
        page = _Page()
        page.feed(
            self.client.get(
                ACCOUNT + "&sub=notifications", HTTP_HOST=HOST, HTTP_HX_REQUEST="true"
            ).content.decode()
        )
        coach_btns = [c for _, c in page.classes if "enable-coach-push-btn" in c]
        self.assertEqual(len(coach_btns), 1)
        self.assertIn("btn-crush-solid", coach_btns[0])
        self.assertIn("btn-sm", coach_btns[0])
        self.assertEqual(len(page.primaries), 1)  # the member push enable


def _load_linter():
    spec = importlib.util.spec_from_file_location(
        "lint_design_tokens", REPO_ROOT / "crush_lu/scripts/lint_design_tokens.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LinterButtonRuleTests(SimpleTestCase):
    def setUp(self):
        self.lint = _load_linter()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "crush_lu"
        self.dir.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, body):
        path = self.dir / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_flags_a_second_gradient_cta(self):
        path = self._write(
            "page.html",
            '<a class="btn-crush-primary">A</a>\n'
            '<button class="btn-crush-primary btn-sm">B</button>\n',
        )
        violations = self.lint.scan_file(path)
        self.assertEqual(len(violations), 1)
        self.assertIn(":2: 2 .btn-crush-primary (lines 1, 2)", violations[0])

    def test_one_gradient_cta_and_comments_pass(self):
        path = self._write(
            "page.html",
            '{# <a class="btn-crush-primary">old</a> #}\n'
            '<a class="btn-crush-primary">A</a>\n'
            '<a class="btn-crush-solid btn-sm">B</a>\n',
        )
        self.assertEqual(self.lint.scan_file(path), [])

    def test_flags_hand_rolled_filled_buttons_only(self):
        path = self._write(
            "page.html",
            '<button class="bg-purple-600 text-white px-6 py-2">Save</button>\n'
            '<div class="bg-red-600 text-white">banner</div>\n'
            '<a class="bg-green-500 text-white">ok</a>\n',
        )
        violations = self.lint.scan_file(path)
        self.assertEqual(len(violations), 1)
        self.assertIn(":1: hand-rolled", violations[0])

    def test_exempt_dirs_and_directory_scans_skip_the_rule(self):
        body = '<a class="btn-crush-primary">A</a><a class="btn-crush-primary">B</a>'
        coach = self.dir / "coach_page.html"
        coach.write_text(body, encoding="utf-8")
        self.assertEqual(self.lint.scan_file(coach), [])
        page = self._write("page.html", body)
        self.assertEqual(self.lint.scan_file(page, check_buttons=False), [])
        self.assertEqual(self.lint.main([str(self.dir)]), 0)
        self.assertEqual(self.lint.main([str(page)]), 1)

    def test_account_partials_pass(self):
        partials = REPO_ROOT / "crush_lu/templates/crush_lu/partials"
        names = (
            "edit_account.html",
            "edit_account_settings.html",
            "edit_account_notifications.html",
            "edit_account_danger.html",
            "_push_native_notice.html",
            "language_switcher.html",
        )
        violations = []
        for name in names:
            violations += self.lint.scan_file(partials / name)
        self.assertEqual(violations, [])
