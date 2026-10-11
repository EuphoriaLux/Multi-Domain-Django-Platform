"""Public trust copy follows the offered verification routes (#1166)."""

import json
from html.parser import HTMLParser
from unittest import mock

import polib
import pytest
from allauth.socialaccount.models import SocialApp
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.utils import translation
from django.utils.safestring import mark_safe

from crush_lu.models import CrushCoach
from crush_lu.models.profiles import UserDataConsent
from crush_lu.tests.test_crush_connect import _login_eligible, _make_user

HOST = "crush.lu"
LIST_APPS = "allauth.socialaccount.adapter.DefaultSocialAccountAdapter.list_apps"
PAGES = ("", "how-it-works/", "crush-connect/")
PERSONAL_VERIFICATION = "Come to an event and a coach verifies you in person."
CONNECT_VERIFICATION = "Attend an in-person event — real people only."
META = {
    "": (
        "Crush.lu is Luxembourg's dating platform. Meet singles at speed dating "
        "events, social mixers, and themed meetups. Members are verified in person "
        "or via LuxID for authentic connections. Join free.",
        "Crush.lu is Luxembourg's dating platform. Meet singles at speed dating "
        "events, social mixers, and themed meetups. Members are verified in person "
        "at an event for authentic connections. Join free.",
    ),
    "how-it-works/": (
        "Learn how Crush.lu works in 4 simple steps: Create your profile, get "
        "verified at an event or via LuxID, browse events, and meet real people. "
        "Luxembourg's privacy-first dating platform.",
        "Learn how Crush.lu works in 4 simple steps: Create your profile, get "
        "verified at an event, browse events, and meet real people. Luxembourg's "
        "privacy-first dating platform.",
    ),
}
HOW_OG = (
    "Discover how Crush.lu helps you meet singles in Luxembourg. Create your "
    "profile, get verified at an event or via LuxID, join events, and make "
    "authentic connections. No endless swiping!",
    "Discover how Crush.lu helps you meet singles in Luxembourg. Create your "
    "profile, get verified at an event, join events, and make authentic "
    "connections. No endless swiping!",
)
JSON_VERIFICATION = {
    "": (
        "Create your profile, then get verified — come to an event and a coach "
        "verifies you in person, or connect your LuxID in seconds. Then join "
        "speed dating nights and social meetups to meet real people.",
        "Create your profile, then get verified — come to an event and a coach "
        "verifies you in person. Then join speed dating nights and social meetups "
        "to meet real people.",
    ),
    "how-it-works/": (
        "Come to an event and a coach verifies you in person, or connect your "
        "LuxID in seconds — no review queue, no waiting.",
        PERSONAL_VERIFICATION,
    ),
}


class PublicPage(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.metadata = {}
        self.schemas = []
        self.body = []
        self.headings = []
        self._script = None
        self._in_body = False
        self._heading = None
        self._skip = False
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            self.metadata[attrs.get("name", attrs.get("property"))] = attrs.get(
                "content", ""
            )
        elif tag == "body":
            self._in_body = True
        elif tag == "script":
            self._script = [] if attrs.get("type") == "application/ld+json" else None
            self._skip = True
        elif tag == "style":
            self._skip = True
        elif tag in ("h2", "h5"):
            self._heading = []

    def handle_endtag(self, tag):
        if tag == "script":
            if self._script is not None:
                self.schemas.append(json.loads("".join(self._script)))
            self._script = None
            self._skip = False
        elif tag == "style":
            self._skip = False
        elif tag in ("h2", "h5") and self._heading is not None:
            self.headings.append("".join(self._heading).strip())
            self._heading = None

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)
        if self._in_body and not self._skip:
            self.body.append(data)
        if self._heading is not None:
            self._heading.append(data)

    def verification_answer(self, page):
        if page == "":
            faq = next(s for s in self.schemas if s.get("@type") == "FAQPage")
            return faq["mainEntity"][0]["acceptedAnswer"]["text"]
        how_to = next(s for s in self.schemas if s.get("@type") == "HowTo")
        return how_to["step"][1]["text"]


def render_page(client, page, lang="en", host=HOST):
    response = client.get(f"/{lang}/{page}", HTTP_HOST=host)
    assert response.status_code == 200
    return PublicPage(response.content.decode())


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    Site.objects.clear_cache()


@pytest.mark.django_db
@pytest.mark.parametrize("lang", ["en", "de", "fr"])
@pytest.mark.parametrize("page", PAGES)
@pytest.mark.parametrize("available", [False, True])
def test_public_claims_body_metadata_and_json_follow_luxid(
    client, page, lang, available
):
    apps = [mock.Mock(settings={})] if available else []
    with mock.patch(LIST_APPS, return_value=apps) as list_apps:
        parsed = render_page(client, page, lang)
    # Body, head and footer share one read-only lookup on this request.
    luxid_calls = [
        c for c in list_apps.call_args_list if c.kwargs.get("provider") == "luxid"
    ]
    assert len(luxid_calls) == 1
    assert bool(parsed.schemas)
    body = " ".join(" ".join(parsed.body).split())
    assert ("LuxID" in body) is available
    if not available:
        assert "LuxID" not in json.dumps(parsed.schemas)
        assert all("LuxID" not in text for text in parsed.metadata.values())
    with translation.override(lang):
        if page in META:
            assert parsed.metadata["description"] == translation.gettext(
                META[page][not available]
            )
        if page == "how-it-works/":
            assert parsed.metadata["og:description"] == translation.gettext(
                HOW_OG[not available]
            )
            count, label = (2, "Ways to Verify") if available else (1, "Way to Verify")
            assert f"{count} {translation.gettext(label)}" in body
        if page in JSON_VERIFICATION:
            expected = translation.gettext(JSON_VERIFICATION[page][not available])
            assert parsed.verification_answer(page) == expected
            if lang != "en":
                assert expected != JSON_VERIFICATION[page][not available]
        if not available:
            fallback = (
                CONNECT_VERIFICATION
                if page == "crush-connect/"
                else PERSONAL_VERIFICATION
            )
            assert translation.gettext(fallback) in body


@pytest.mark.django_db
@pytest.mark.parametrize("page", ["", "how-it-works/"])
@pytest.mark.parametrize("available", [False, True])
def test_metadata_and_json_preserve_translated_quotes_and_apostrophes(
    client, page, available
):
    sources = [JSON_VERIFICATION[page][not available], META[page][not available]]
    if page == "how-it-works/":
        sources.append(HOW_OG[not available])
    unusual_translation = 'Une "rencontre" \\ en personne — O\'Brien <script>'
    original = translation._trans.gettext
    apps = [mock.Mock(settings={})] if available else []

    def translated(message):
        # Django gettext preserves the SafeString type of template literals.
        return (
            mark_safe(unusual_translation) if message in sources else original(message)
        )

    with mock.patch(LIST_APPS, return_value=apps), mock.patch.object(
        translation._trans, "gettext", side_effect=translated
    ):
        parsed = render_page(client, page, "fr")
    assert parsed.verification_answer(page) == unusual_translation
    assert parsed.metadata["description"] == unusual_translation
    if page == "how-it-works/":
        assert parsed.metadata["og:description"] == unusual_translation


@pytest.mark.django_db
def test_public_gate_is_request_and_site_specific_without_signup_writes(
    client, settings
):
    settings.SITE_ID = None
    settings.SOCIALACCOUNT_PROVIDERS = {}
    main_site, _ = Site.objects.get_or_create(domain=HOST, defaults={"name": "Crush"})
    other_site, _ = Site.objects.get_or_create(
        domain="test.crush.lu", defaults={"name": "Test Crush"}
    )
    luxid = SocialApp.objects.create(provider="luxid", name="LuxID", client_id="test")
    luxid.sites.add(main_site)
    apple = SocialApp.objects.create(
        provider="apple", name="Unlinked Apple", client_id="apple"
    )
    for host, offered in ((HOST, True), (other_site.domain, False), (HOST, True)):
        parsed = render_page(client, "how-it-works/", host=host)
        assert ("LuxID" in parsed.metadata["description"]) is offered
    assert list(luxid.sites.values_list("pk", flat=True)) == [main_site.pk]
    assert not apple.sites.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "lang,expected",
    [("en", "Reward Levels"), ("de", "Prämienstufen"), ("fr", "Niveaux de récompense")],
)
def test_rewards_headings_distinguish_points_from_paid_membership(
    client, lang, expected
):
    membership = render_page(client, "membership/", lang)
    assert expected in membership.headings
    coach = get_user_model().objects.create_user(
        username="reward-coach", email="reward-coach@example.com"
    )
    UserDataConsent.objects.filter(user=coach).update(crushlu_consent_given=True)
    CrushCoach.objects.create(user=coach, is_active=True)
    client.force_login(coach)
    dashboard = render_page(client, "coach/dashboard/", lang)
    assert expected in dashboard.headings


@pytest.mark.parametrize("lang", ["de", "fr"])
def test_new_public_copy_catalogue_entries_are_complete(lang):
    po = polib.pofile(
        django_settings.BASE_DIR
        / "crush_lu"
        / "locale"
        / lang
        / "LC_MESSAGES"
        / "django.po"
    )
    messages = [PERSONAL_VERIFICATION, CONNECT_VERIFICATION, "Reward Levels", HOW_OG[1]]
    messages.extend(pair[1] for pair in META.values())
    messages.extend(pair[1] for pair in JSON_VERIFICATION.values())
    for source in messages:
        entry = po.find(source)
        assert entry is not None and entry.msgstr and not entry.fuzzy, source


@pytest.mark.django_db
@pytest.mark.parametrize("page", PAGES)
def test_hidden_luxid_is_not_promised_on_public_pages(client, page):
    with mock.patch(LIST_APPS, return_value=[mock.Mock(settings={"hidden": True})]):
        parsed = render_page(client, page, "fr")
    assert "LuxID" not in " ".join(parsed.body)
    assert "LuxID" not in json.dumps(parsed.schemas)
    assert all("LuxID" not in text for text in parsed.metadata.values())


@pytest.mark.django_db
@pytest.mark.parametrize("lang", ["en", "de", "fr"])
@pytest.mark.parametrize("available", [False, True])
def test_unverified_member_gets_only_available_connect_instructions(
    client, lang, available
):
    member = _make_user(
        username="unverified-trust", premium=False, has_luxid=False, onboarded=False
    )
    _login_eligible(client, member)
    apps = [mock.Mock(settings={})] if available else []
    with mock.patch(LIST_APPS, return_value=apps), mock.patch(
        "crush_lu.luxid.get_luxid_connect_url",
        return_value="/accounts/luxid/login/?process=connect",
    ):
        parsed = render_page(client, "crush-connect/", lang)
    body = " ".join(" ".join(parsed.body).split())
    with translation.override(lang):
        if available:
            assert translation.gettext("Connect LuxID") in body
            assert (
                translation.gettext(
                    "Crush Connect requires verified identity to keep matching safe and authentic. You can verify in two easy ways:"
                )
                in body
            )
        else:
            assert "LuxID" not in body
            assert (
                translation.gettext(
                    "Crush Connect requires verified identity to keep matching safe and authentic. Attend an event to get verified in person."
                )
                in body
            )
        assert translation.gettext("Attend an Event") in body
