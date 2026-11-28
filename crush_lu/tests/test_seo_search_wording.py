"""Search-result wording and crawl rules for the public crush.lu pages.

Covers the on-site actions from the 2026-11 Google search review:

1. Home page titles and descriptions carry the words people search in each
   language (not a literal translation of the English).
2. /login/ answers ``noindex`` and robots.txt lets crawlers read it.
3. Event pages are titled by format, canton and date, in the page language.
4. The sitemap lists every event in EN, DE and FR with hreflang + x-default.
5. The home page links both format guides; the speed dating guide is titled
   for the "how does it work" search.
6. /membership/ and /themes/ describe themselves.

Literal paths + HTTP_HOST='crush.lu' per AGENTS.md (reverse() resolves against
the wrong urlconf under the test client's default host).
"""

import json
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal
from html.parser import HTMLParser
from unittest import mock
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import polib
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone, translation

from crush_lu.models import MeetupEvent
from crush_lu.models.event_polls import EventPoll
from crush_lu.services.event_seo import (
    _format_phrase,
    event_search_description,
    event_search_headline,
    event_search_title,
)

HOST = "crush.lu"
LUX = ZoneInfo("Europe/Luxembourg")
# A fixed "today" for everything that prints a date, so the assertions do not
# change when the calendar does (the title adds the year across a year end).
FROZEN_NOW = datetime(2026, 11, 28, 12, 0, tzinfo=dt_timezone.utc)
SITEMAP_NS = {
    "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "xhtml": "http://www.w3.org/1999/xhtml",
}

CREATIVE_TITLE = "A winemaker, a glass and you"


class _Head(HTMLParser):
    """Collects <title>, <meta> content, <link> tags, hrefs and JSON-LD."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta = {}
        self.links = []
        self.hrefs = []
        self.schemas = []
        self._in_title = False
        self._schema = None
        self.feed(html)
        self.title = " ".join(self.title.split())

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = attrs.get("name") or attrs.get("property")
            if key and key not in self.meta:
                self.meta[key] = attrs.get("content", "")
        elif tag == "link":
            self.links.append(attrs)
        elif tag == "a":
            self.hrefs.append(attrs.get("href"))
        elif tag == "script" and attrs.get("type") == "application/ld+json":
            self._schema = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._schema is not None:
            self.schemas.append(json.loads("".join(self._schema)))
            self._schema = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._schema is not None:
            self._schema.append(data)


def _event(**overrides):
    defaults = {
        "title": CREATIVE_TITLE,
        "description": (
            "There are evenings when conversations come all by themselves. "
            "This is one of them."
        ),
        "event_type": "wine_tasting",
        "date_time": datetime(2026, 12, 5, 19, 0, tzinfo=LUX),
        "registration_deadline": datetime(2026, 12, 4, 19, 0, tzinfo=LUX),
        # Venue and town are what an anonymous visitor must never be shown.
        "location": "Secret Cellar",
        "address_town": "Schengen",
        "canton": "Remich",
        "max_participants": 20,
        "registration_fee": Decimal("25.00"),
        "is_published": True,
    }
    defaults.update(overrides)
    return MeetupEvent.objects.create(**defaults)


def _frozen():
    return mock.patch("django.utils.timezone.now", return_value=FROZEN_NOW)


class EventSearchWordingTests(TestCase):
    """services/event_seo.py: format, canton and date, per language."""

    def setUp(self):
        cache.clear()
        self.event = _event()

    def _title(self, lang, event=None, now=FROZEN_NOW):
        with translation.override(lang):
            return event_search_title(event or self.event, now=now)

    def test_title_names_format_canton_and_date_in_each_language(self):
        self.assertEqual(
            self._title("en"), "Wine tasting for singles in Remich, 5 December"
        )
        self.assertEqual(
            self._title("de"), "Weinprobe für Singles in Remich, 5. Dezember"
        )
        # French month names are lower case in running text.
        self.assertEqual(
            self._title("fr"),
            "Dégustation de vin pour célibataires à Remich, 5 décembre",
        )

    def test_title_adds_the_year_once_the_event_is_not_this_year(self):
        next_year = datetime(2027, 1, 10, 12, 0, tzinfo=dt_timezone.utc)
        self.assertEqual(
            self._title("en", now=next_year),
            "Wine tasting for singles in Remich, 5 December 2026",
        )
        self.assertEqual(
            self._title("de", now=next_year),
            "Weinprobe für Singles in Remich, 5. Dezember 2026",
        )

    def test_date_is_the_luxembourg_calendar_day(self):
        # 23:30 UTC on the 4th is already the 5th in Luxembourg.
        late = _event(date_time=datetime(2026, 12, 4, 23, 30, tzinfo=dt_timezone.utc))
        self.assertTrue(self._title("en", event=late).endswith(", 5 December"))

    def test_luxembourg_canton_and_blank_canton_read_as_the_country(self):
        for canton in ("Luxembourg", ""):
            event = _event(event_type="speed_dating", canton=canton)
            with self.subTest(canton=canton):
                self.assertEqual(
                    self._title("en", event=event),
                    "Speed dating in Luxembourg, 5 December",
                )
                self.assertEqual(
                    self._title("de", event=event),
                    "Speed Dating in Luxemburg, 5. Dezember",
                )
                self.assertEqual(
                    self._title("fr", event=event),
                    "Speed dating au Luxembourg, 5 décembre",
                )

    def test_canton_name_is_translated(self):
        event = _event(event_type="quiz_night", canton="Clervaux")
        self.assertEqual(
            self._title("de", event=event),
            "Quiz Night für Singles in Clerf, 5. Dezember",
        )

    def test_every_event_type_has_its_own_wording(self):
        """A new event type must get search wording, not the generic fallback."""
        with translation.override("en"):
            fallback = _format_phrase(MeetupEvent(event_type="not-a-type"))
            self.assertEqual(fallback, "Singles event")
            for value, _label in MeetupEvent.EVENT_TYPE_CHOICES:
                with self.subTest(event_type=value):
                    phrase = _format_phrase(MeetupEvent(event_type=value))
                    self.assertNotEqual(phrase, fallback)

    def test_description_leads_with_the_facts_in_each_language(self):
        teaser = (
            "There are evenings when conversations come all by themselves. "
            "This is one of them."
        )
        expected = {
            "en": (
                "Wine tasting for singles in Remich (Luxembourg) on Saturday "
                "5 December 2026 at 7 p.m. "
            ),
            "de": (
                "Weinprobe für Singles in Remich (Luxemburg) am Samstag, "
                "5. Dezember 2026 um 19:00 Uhr. "
            ),
            "fr": (
                "Dégustation de vin pour célibataires à Remich (Luxembourg) le "
                "samedi 5 décembre 2026 à 19:00. "
            ),
        }
        for lang, facts in expected.items():
            with self.subTest(lang=lang), translation.override(lang):
                self.assertEqual(event_search_description(self.event), facts + teaser)

    def test_teaser_is_plain_text_without_the_repeated_event_name(self):
        event = _event(
            description=(
                f"{CREATIVE_TITLE}\n\n<p>Meet   a winemaker</p>\n"
                + " ".join(f"word{i}" for i in range(40))
            )
        )
        with translation.override("en"):
            description = event_search_description(event)
        teaser = description.split(" at 7 p.m. ", 1)[1]
        self.assertTrue(teaser.startswith("Meet a winemaker word0"), teaser)
        self.assertNotIn("<p>", description)
        self.assertNotIn(CREATIVE_TITLE, description)
        self.assertTrue(teaser.endswith("…"))
        self.assertEqual(len(teaser.rstrip("…").split()), 20)

    def test_empty_description_leaves_only_the_facts(self):
        event = _event(description="")
        with translation.override("en"):
            self.assertTrue(event_search_description(event).endswith("at 7 p.m."))

    def test_wording_never_names_the_venue_or_the_town(self):
        for lang in ("en", "de", "fr"):
            with translation.override(lang):
                text = " ".join(
                    [
                        event_search_title(self.event, now=FROZEN_NOW),
                        event_search_headline(self.event),
                        event_search_description(self.event),
                    ]
                )
            self.assertNotIn("Secret Cellar", text)
            self.assertNotIn("Schengen", text)


class EventPageHeadTests(TestCase):
    """The event page serves that wording; the creative name stays elsewhere."""

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST=HOST)
        with _frozen():
            self.event = _event(
                title_en=CREATIVE_TITLE,
                title_fr="Un vigneron, un verre et vous",
                description_fr=(
                    "Un vigneron, un verre et vous Il y a des soirées où les "
                    "conversations viennent toutes seules."
                ),
            )

    def _head(self, lang):
        with _frozen():
            response = self.client.get(f"/{lang}/events/{self.event.pk}/")
        self.assertEqual(response.status_code, 200)
        return _Head(response.content.decode())

    def test_english_page(self):
        head = self._head("en")
        self.assertEqual(
            head.title, "Wine tasting for singles in Remich, 5 December | Crush.lu"
        )
        self.assertTrue(
            head.meta["description"].startswith(
                "Wine tasting for singles in Remich (Luxembourg) on Saturday "
                "5 December 2026 at 7 p.m. There are evenings"
            ),
            head.meta["description"],
        )
        # The creative name leads the share title and stays the schema name.
        share_title = (
            f"{CREATIVE_TITLE} – Wine tasting for singles in Remich | Crush.lu"
        )
        self.assertEqual(head.meta["og:title"], share_title)
        self.assertEqual(head.meta["twitter:title"], share_title)
        self.assertEqual(head.meta["og:description"], head.meta["description"])
        self.assertEqual(head.meta["twitter:description"], head.meta["description"])
        event_schema = next(s for s in head.schemas if s.get("@type") == "FoodEvent")
        self.assertEqual(event_schema["name"], CREATIVE_TITLE)

    def test_french_page_has_no_english_filler(self):
        head = self._head("fr")
        self.assertEqual(
            head.title,
            "Dégustation de vin pour célibataires à Remich, 5 décembre | Crush.lu",
        )
        description = head.meta["description"]
        self.assertTrue(
            description.startswith(
                "Dégustation de vin pour célibataires à Remich (Luxembourg) le "
                "samedi 5 décembre 2026 à 19:00. Il y a des soirées"
            ),
            description,
        )
        for english in ("event on", "Join us on", "Wine Tasting"):
            self.assertNotIn(english, description)
            self.assertNotIn(english, head.meta["og:description"])
        self.assertEqual(
            head.meta["og:title"],
            "Un vigneron, un verre et vous – Dégustation de vin pour "
            "célibataires à Remich | Crush.lu",
        )

    def test_german_page(self):
        head = self._head("de")
        self.assertEqual(
            head.title, "Weinprobe für Singles in Remich, 5. Dezember | Crush.lu"
        )
        self.assertTrue(
            head.meta["description"].startswith(
                "Weinprobe für Singles in Remich (Luxemburg) am Samstag, "
                "5. Dezember 2026 um 19:00 Uhr."
            ),
            head.meta["description"],
        )

    def test_anonymous_head_never_names_the_venue_or_the_town(self):
        for lang in ("en", "de", "fr"):
            head = self._head(lang)
            text = " ".join([head.title, *head.meta.values()])
            self.assertNotIn("Secret Cellar", text, lang)
            self.assertNotIn("Schengen", text, lang)


class LoginCrawlRulesTests(TestCase):
    """/login/ is crawlable and answers noindex; private areas stay blocked."""

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST=HOST)

    def test_robots_txt_no_longer_hides_the_login_page(self):
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        rules = [
            line.strip()
            for line in response.content.decode().splitlines()
            if line.startswith(("Disallow:", "Allow:"))
        ]
        self.assertFalse([rule for rule in rules if "login" in rule], rules)
        # Everything else that was blocked still is.
        for path in (
            "/signup/",
            "/en/signup/",
            "/de/signup/",
            "/fr/signup/",
            "/data-deletion/",
            "/accounts/",
            "/dashboard/",
            "/profile/",
            "/api/",
            "/crush-admin/",
        ):
            self.assertIn(f"Disallow: {path}", rules)
        self.assertIn(
            "Sitemap: https://crush.lu/sitemap.xml", response.content.decode()
        )

    def test_login_page_answers_noindex_in_header_and_meta(self):
        for lang in ("en", "de", "fr"):
            with self.subTest(lang=lang):
                response = self.client.get(f"/{lang}/login/")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
                head = _Head(response.content.decode())
                self.assertEqual(head.meta["robots"], "noindex, nofollow")

    def test_public_pages_stay_indexable(self):
        response = self.client.get("/en/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("X-Robots-Tag", response)
        self.assertEqual(
            _Head(response.content.decode()).meta["robots"], "index, follow"
        )


# conftest pins SITE_ID to the "localhost" row; production leaves it unset and
# the sitemap takes its domain from the request host.
@override_settings(SITE_ID=None)
class SitemapTests(TestCase):
    """Every event in three languages, with hreflang alternates + x-default."""

    def setUp(self):
        cache.clear()
        Site.objects.clear_cache()
        self.addCleanup(Site.objects.clear_cache)
        Site.objects.get_or_create(domain=HOST, defaults={"name": "Crush.lu"})
        self.client = Client(HTTP_HOST=HOST)
        now = timezone.now()
        self.event = _event(
            date_time=now + timedelta(days=7),
            registration_deadline=now + timedelta(days=6),
        )

    def _urls(self):
        response = self.client.get("/sitemap.xml")
        self.assertEqual(response.status_code, 200)
        root = ElementTree.fromstring(response.content)
        urls = {}
        for url in root.findall("sm:url", SITEMAP_NS):
            loc = url.find("sm:loc", SITEMAP_NS).text
            urls[loc] = {
                link.get("hreflang"): link.get("href")
                for link in url.findall("xhtml:link", SITEMAP_NS)
            }
        return urls

    def test_event_is_listed_in_every_language_with_alternates(self):
        urls = self._urls()
        expected = {
            lang: f"https://crush.lu/{lang}/events/{self.event.pk}/"
            for lang in ("en", "de", "fr")
        }
        alternates = dict(expected, **{"x-default": expected["en"]})
        for lang, loc in expected.items():
            with self.subTest(lang=lang):
                self.assertIn(loc, urls)
                self.assertEqual(urls[loc], alternates)

    def test_static_pages_carry_x_default_too(self):
        urls = self._urls()
        self.assertEqual(
            urls["https://crush.lu/fr/"],
            {
                "en": "https://crush.lu/en/",
                "de": "https://crush.lu/de/",
                "fr": "https://crush.lu/fr/",
                "x-default": "https://crush.lu/en/",
            },
        )
        self.assertEqual(
            urls["https://crush.lu/de/speed-dating/"]["x-default"],
            "https://crush.lu/en/speed-dating/",
        )

    def test_sitemap_and_page_agree_on_x_default(self):
        """Two x-default targets for one page are conflicting signals."""
        loc = f"https://crush.lu/de/events/{self.event.pk}/"
        response = self.client.get(f"/de/events/{self.event.pk}/")
        self.assertEqual(response.status_code, 200)
        page = {
            link.get("hreflang"): link.get("href")
            for link in _Head(response.content.decode()).links
            if link.get("rel") == "alternate" and link.get("hreflang")
        }
        self.assertEqual(self._urls()[loc], page)

    def test_past_private_and_unpublished_events_stay_out(self):
        now = timezone.now()
        hidden = [
            _event(
                date_time=now - timedelta(days=30),
                registration_deadline=now - timedelta(days=31),
            ),
            _event(
                date_time=now + timedelta(days=7),
                registration_deadline=now + timedelta(days=6),
                is_private_invitation=True,
            ),
            _event(
                date_time=now + timedelta(days=7),
                registration_deadline=now + timedelta(days=6),
                is_published=False,
            ),
        ]
        urls = self._urls()
        for event in hidden:
            for lang in ("en", "de", "fr"):
                self.assertNotIn(f"https://crush.lu/{lang}/events/{event.pk}/", urls)


class PublicPageWordingTests(TestCase):
    """Home, events list, guides, pricing and theme board metadata."""

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST=HOST)

    def _head(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, path)
        return _Head(response.content.decode())

    def test_home_title_and_description_use_the_searched_words(self):
        # Keywords, not whole strings: the wording itself may be tuned in the
        # .po files without touching this test.
        expected = {
            "en": (
                ["Dating App", "Luxembourg", "Speed Dating"],
                ["dating app", "quiz"],
            ),
            "fr": (
                ["Site de rencontre au Luxembourg", "gratuit"],
                ["site de rencontre", "gratuit", "quiz", "célibataires"],
            ),
            "de": (
                ["Partnersuche", "Dating", "Luxemburg", "kostenlos"],
                ["partnersuche", "kostenlos", "quiz", "single"],
            ),
        }
        for lang, (title_words, description_words) in expected.items():
            head = self._head(f"/{lang}/")
            with self.subTest(lang=lang):
                for word in title_words:
                    self.assertIn(word, head.title)
                self.assertTrue(head.title.endswith("| Crush.lu"), head.title)
                description = head.meta["description"].lower()
                for word in description_words:
                    self.assertIn(word, description)

    def test_home_wording_differs_per_language(self):
        titles = {lang: self._head(f"/{lang}/").title for lang in ("en", "de", "fr")}
        self.assertEqual(len(set(titles.values())), 3, titles)

    def test_home_links_both_format_guides(self):
        for lang in ("en", "de", "fr"):
            head = self._head(f"/{lang}/")
            with self.subTest(lang=lang):
                self.assertIn(f"/{lang}/speed-dating/", head.hrefs)
                self.assertIn(f"/{lang}/quiz-night/", head.hrefs)

    def test_events_list_title_carries_the_current_year(self):
        year = str(timezone.localdate().year)
        for lang in ("en", "de", "fr"):
            title = self._head(f"/{lang}/events/").title
            with self.subTest(lang=lang):
                self.assertIn("Speed Dating", title)
                self.assertIn(year, title)
                self.assertNotIn("current_year", title)

    def test_events_list_title_moves_with_the_calendar(self):
        # {% now %} reads the clock through ``datetime.now`` in defaulttags.
        with mock.patch("django.template.defaulttags.datetime") as clock:
            clock.now.return_value = datetime(2027, 1, 2, 12, 0, tzinfo=LUX)
            title = self._head("/fr/events/").title
        self.assertEqual(
            title,
            "Speed Dating Luxembourg - Soirées Célibataires & Rencontres 2027 "
            "| Crush.lu",
        )

    def test_speed_dating_guide_is_titled_for_how_it_works(self):
        expected = {
            "en": "How It Works",
            "de": "So funktioniert",
            "fr": "comment ça marche",
        }
        for lang, phrase in expected.items():
            head = self._head(f"/{lang}/speed-dating/")
            with self.subTest(lang=lang):
                self.assertIn("Speed", head.title)
                self.assertIn(phrase, head.title)
                self.assertIn("coach", head.meta["description"].lower())

    def test_theme_board_describes_itself_with_and_without_a_ballot(self):
        default = self._head("/en/support/").meta["description"]
        empty = self._head("/en/themes/").meta["description"]
        self.assertIn("theme nights", empty)
        self.assertNotEqual(empty, default)

        now = timezone.now()
        EventPoll.objects.create(
            title="Which theme next?",
            start_date=now - timedelta(hours=1),
            end_date=now + timedelta(days=7),
            is_published=True,
            is_public=True,
        )
        with_ballot = self._head("/en/themes/")
        self.assertEqual(with_ballot.title, "Which theme next? | Crush.lu")
        self.assertEqual(with_ballot.meta["description"], empty)
        self.assertIn("Themenabende", self._head("/de/themes/").meta["description"])


class SearchWordingCatalogueTests(TestCase):
    """Every new string has a German and a French entry that is not fuzzy."""

    MESSAGES = (
        (None, "Dating App Luxembourg – Speed Dating & Singles Events | Crush.lu"),
        (
            None,
            (
                "Crush.lu is Luxembourg's dating app for meeting singles in real "
                "life: speed dating, quiz nights and social mixers. Verified "
                "members, no endless swiping. Join free."
            ),
        ),
        (
            None,
            (
                "Speed Dating Luxembourg - Singles Events & Meetups "
                "%(current_year)s | Crush.lu"
            ),
        ),
        (None, "Speed Dating in Luxembourg: How It Works, Step by Step | Crush.lu"),
        (None, "Crush.lu Pricing: Free Account, Events & Premium"),
        ("event search format", "Speed dating"),
        ("event search format", "Social mixer for singles"),
        ("event search format", "Activity meetup for singles"),
        ("event search format", "Themed night for singles"),
        ("event search format", "Quiz night for singles"),
        ("event search format", "Scavenger hunt for singles"),
        ("event search format", "Wine tasting for singles"),
        ("event search format", "Singles event"),
        ("event search place", "in Luxembourg"),
        ("event search place", "in %(place)s"),
        ("event search place", "in %(place)s (Luxembourg)"),
        ("event search text", "%(format)s %(place)s"),
        ("event search text", "%(headline)s, %(date)s"),
        ("event search text", "%(format)s %(place)s on %(date)s at %(time)s."),
        ("event search date format", "j F"),
        ("event search date format", "j F Y"),
        ("event search date format", "l j F Y"),
    )

    def test_entries_exist_and_keep_their_placeholders(self):
        for lang in ("de", "fr"):
            po = polib.pofile(
                str(
                    settings.BASE_DIR
                    / "crush_lu"
                    / "locale"
                    / lang
                    / "LC_MESSAGES"
                    / "django.po"
                )
            )
            for context, msgid in self.MESSAGES:
                with self.subTest(lang=lang, msgid=msgid):
                    entry = (
                        po.find(msgid, msgctxt=context) if context else po.find(msgid)
                    )
                    self.assertIsNotNone(entry)
                    self.assertTrue(entry.msgstr)
                    self.assertFalse(entry.fuzzy)
                    # A dropped %(name)s would raise KeyError at render time.
                    for placeholder in ("format", "place", "headline", "date", "time"):
                        token = f"%({placeholder})s"
                        self.assertEqual(token in msgid, token in entry.msgstr)
