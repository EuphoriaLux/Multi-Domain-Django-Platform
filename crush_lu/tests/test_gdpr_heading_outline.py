"""#1117: the GDPR page's heading outline (from #1088).

"Account Deletion" is a section heading (h2) and each deletion option sits
one level below it (h3); no level is skipped, so screen-reader heading
navigation matches the visual hierarchy. Both variants of option 1 (with
and without a Crush.lu profile) are checked.
"""

from html.parser import HTMLParser

from django.core.cache import cache
from django.test import Client, TestCase

from crush_lu.models import CrushProfile
from crush_lu.tests.test_profile_edit_connect_card import _make_member

HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


class _Outline(HTMLParser):
    """Every heading in document order: ``[(level, text), ...]``."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.headings = []
        self._open = None

    def handle_starttag(self, tag, attrs):
        if tag in HEADINGS:
            self._open = [int(tag[1]), ""]

    def handle_data(self, data):
        if self._open is not None:
            self._open[1] += data

    def handle_endtag(self, tag):
        if tag in HEADINGS and self._open is not None:
            self.headings.append((self._open[0], " ".join(self._open[1].split())))
            self._open = None


class GdprHeadingOutlineTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = _make_member("gdpr-outline@example.com")
        self.client = Client(HTTP_HOST="crush.lu")
        self.client.force_login(self.user)

    def _outline(self):
        response = self.client.get("/en/account/gdpr/")
        self.assertEqual(response.status_code, 200)
        parser = _Outline()
        parser.feed(response.content.decode())
        return parser.headings

    def _assert_deletion_outline(self, headings):
        levels = dict((text, level) for level, text in headings)
        self.assertEqual(levels["Account Deletion"], 2)
        self.assertEqual(levels["Option 1: Delete Crush.lu Profile Only"], 3)
        self.assertEqual(levels["Option 2: Delete your entire account"], 3)
        # The options follow their section heading.
        texts = [text for _level, text in headings]
        section = texts.index("Account Deletion")
        self.assertGreater(
            texts.index("Option 1: Delete Crush.lu Profile Only"), section
        )
        # No heading skips a level below the one before it.
        for (prev, _), (level, text) in zip(headings, headings[1:]):
            self.assertLessEqual(level, prev + 1, f"skipped a level at {text!r}")

    def test_outline_with_a_profile(self):
        self._assert_deletion_outline(self._outline())

    def test_outline_without_a_profile(self):
        CrushProfile.objects.filter(user=self.user).delete()
        self._assert_deletion_outline(self._outline())
