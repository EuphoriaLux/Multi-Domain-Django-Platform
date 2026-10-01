"""UX Wave 5 · WP3 trust bugs (R1, R12, Child Safety translation).

R1:  the hub catalogue tile follows ``connect_summary.is_visible`` like the
     hub header and ``catalogue_status.html``.
R12: the footer only promises instant LuxID verification when LuxID is offered.
CS:  DE/FR "Child Safety Standards" is no longer a fuzzy "Community
     standards"/"Change password" leftover.
Literal paths; testserver resolves to crush.lu.
"""

import html
from unittest import mock

import polib
import pytest
from django.conf import settings as dj_settings
from django.core.cache import cache

from crush_lu.tests.test_crush_connect import HUB_URL, _login_eligible, _make_user

pytestmark = pytest.mark.urls("azureproject.urls_crush")

FOOTER_LUXID = "Your privacy matters. Members are verified instantly with LuxID"
LIST_APPS = "allauth.socialaccount.adapter.DefaultSocialAccountAdapter.list_apps"


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


def _body(resp):
    assert resp.status_code == 200
    return html.unescape(resp.content.decode())


def _tile(body):
    start = body.index('href="/en/crush-connect/catalogue/')
    return body[start : body.index("</a>", start)]


@pytest.mark.django_db
def test_hub_tile_says_almost_when_not_visible(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="tilehidden", premium=False, photo_share_consent=True)
    me.crushprofile.photo_1 = ""
    me.crushprofile.save(update_fields=["photo_1"])
    _login_eligible(client, me)

    tile = _tile(_body(client.get(HUB_URL)))

    assert "You're almost in the mix." in tile
    assert "You're not shown to anyone yet." in tile
    assert "anonymous totals only" not in tile


@pytest.mark.django_db
def test_hub_tile_says_in_the_mix_when_visible(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="tilevisible", premium=False)
    _login_eligible(client, me)

    tile = _tile(_body(client.get(HUB_URL)))

    assert "You're in the mix" in tile
    assert "almost" not in tile


@pytest.mark.django_db
def test_footer_promises_luxid_only_when_offered(client):
    with mock.patch(LIST_APPS, return_value=[mock.Mock()]):
        offered = _body(client.get("/en/about/"))
    cache.clear()
    with mock.patch(LIST_APPS, return_value=[]):
        absent = _body(client.get("/en/about/"))

    assert FOOTER_LUXID in offered
    assert FOOTER_LUXID not in absent
    assert "Members are verified in person at an event." in absent


@pytest.mark.django_db
def test_footer_check_never_links_other_apps_to_the_site(client):
    """The footer renders on every page; it must not trigger the adapter's
    ``list_providers`` recovery, which links an unlinked Apple app to the
    current site (a write that could expose another domain's OAuth app)."""
    from allauth.socialaccount.models import SocialApp

    apple = SocialApp.objects.create(
        provider="apple", name="Apple", client_id="apple-id", secret="s"
    )
    assert apple.sites.count() == 0
    assert client.get("/en/about/").status_code == 200
    assert apple.sites.count() == 0


@pytest.mark.parametrize("lang", ["de", "fr"])
def test_child_safety_translations_not_fuzzy_or_wrong(lang):
    po = polib.pofile(
        f"{dj_settings.BASE_DIR}/crush_lu/locale/{lang}/LC_MESSAGES/django.po"
    )
    wrong = ("gemeinschaft", "communauté", "passwort", "mot de passe")
    for msgid in (
        "Child Safety Standards",
        "Child Safety Standards - Crush.lu",
        "Crush.lu Child Safety Standards — our published commitments against child "
        "sexual abuse and exploitation (CSAE), reporting mechanisms, and safety "
        "enforcement.",
    ):
        entry = po.find(msgid)
        assert entry is not None and entry.msgstr
        assert not entry.fuzzy
        assert not any(w in entry.msgstr.lower() for w in wrong)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "lang,expected",
    [("de", "Kinderschutzstandards"), ("fr", "Normes de protection de l'enfance")],
)
def test_child_safety_page_renders_translated_heading(client, lang, expected):
    page = _body(client.get(f"/{lang}/child-safety-standards/"))
    assert expected in page
