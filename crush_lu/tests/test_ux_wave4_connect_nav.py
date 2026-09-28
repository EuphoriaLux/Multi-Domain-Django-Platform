"""UX Wave 4 · WP10 "connect-nav" — findings 6-04, 6-05 and decision F.

6-04: the Connect subnav has four tabs (Home first), the "← Connect home" and
      page-local back links are gone, and the 44px rule no longer stretches
      every text link in the Connect shell.
6-05: a top-level desktop "Connect" link, gated like the bottom-nav tab.
F:    the member-facing "Connections" (crush_lu:my_connections) is "Matches".

Literal ``/en/...`` paths; testserver resolves to crush.lu.
"""

import html
import re
from pathlib import Path

import pytest
from django.core.cache import cache

from crush_lu.tests.test_crush_connect import (
    CATALOGUE_STATUS_URL,
    _login_eligible,
    _make_user,
)

pytestmark = pytest.mark.urls("azureproject.urls_crush")

HUB_URL = "/en/crush-connect/home/"
PAUSE_URL = "/en/crush-connect/pause/"
PROFILE_EDIT_URL = "/en/crush-connect/profile/"
CSS = Path(__file__).resolve().parents[1] / "static/crush_lu/css/connect-mobile.css"


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


def _body(resp):
    assert resp.status_code == 200, resp.status_code
    return html.unescape(resp.content.decode())


def _subnav_links(body):
    start = body.index('class="connect-local-nav')
    nav = body[start : body.index("</nav>", start)]
    return re.findall(r"<a ([^>]*)>(.*?)</a>", nav, flags=re.S)


def _member(client, settings, username, **kwargs):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username=username, **kwargs)
    _login_eligible(client, me)
    return me


# --- 6-04 subnav ---------------------------------------------------------


@pytest.mark.django_db
def test_subnav_has_home_first_and_marks_it_current_on_the_hub(client, settings):
    _member(client, settings, "nav_hub")

    links = _subnav_links(_body(client.get(HUB_URL)))

    assert len(links) == 4
    attrs, label = links[0]
    assert 'href="/en/crush-connect/home/"' in attrs
    assert 'aria-current="page"' in attrs
    assert label.strip() == "Home"
    assert not any('aria-current="page"' in a for a, _ in links[1:])


@pytest.mark.django_db
def test_subnav_home_is_not_current_on_other_connect_pages(client, settings):
    _member(client, settings, "nav_cat")

    links = _subnav_links(_body(client.get(CATALOGUE_STATUS_URL)))

    assert len(links) == 4
    assert 'aria-current="page"' not in links[0][0]
    assert 'aria-current="page"' in links[1][0]


@pytest.mark.django_db
def test_connect_home_link_is_gone(client, settings):
    _member(client, settings, "nav_nohome")

    body = _body(client.get(CATALOGUE_STATUS_URL))

    assert "Connect home" not in body


@pytest.mark.django_db
def test_subnav_home_uses_its_own_translation(client, settings):
    _member(client, settings, "nav_de")

    de = _subnav_links(_body(client.get("/de/crush-connect/home/")))
    fr = _subnav_links(_body(client.get("/fr/crush-connect/home/")))

    assert de[0][1].strip() == "Start"
    assert fr[0][1].strip() == "Accueil"


@pytest.mark.django_db
def test_pause_page_has_no_local_back_link(client, settings):
    _member(client, settings, "nav_pause")

    body = _body(client.get(PAUSE_URL))

    assert "Back to Crush Connect" not in body
    # The subnav Home tab and the "Keep Connect active" button remain.
    assert "Keep Connect active" in body
    assert 'href="/en/crush-connect/home/"' in body


@pytest.mark.django_db
def test_profile_edit_index_header_has_no_back_link(client, settings):
    _member(client, settings, "nav_profile")

    body = _body(client.get(PROFILE_EDIT_URL))

    h1 = re.search(r"<h1[^>]*>\s*Your Connect profile\s*</h1>", body)
    assert h1
    header = body[body.rindex("<header", 0, h1.start()) : h1.start()]
    assert "<a " not in header


def test_44px_rule_is_scoped_to_controls():
    css = re.sub(r"/\*.*?\*/", "", CSS.read_text(encoding="utf-8"), flags=re.S)

    assert not re.search(r"\.connect-shell a\s*[,{]", css)
    rule = re.search(r"([^{}]*)\{\s*min-height:\s*44px;\s*\}", css).group(1)
    selectors = {s.strip() for s in rule.split(",")}
    assert ".connect-local-nav a" in selectors
    assert '.connect-shell [class*="btn-"]' in selectors
    assert "repeat(3, 1fr)" not in css  # the old 3-tab grid squeezed a 4th tab


# --- 6-05 desktop Connect link --------------------------------------------


@pytest.mark.django_db
def test_desktop_nav_has_connect_link_when_connect_nav_visible(client, settings):
    _member(client, settings, "nav_desk")

    body = _body(client.get("/en/dashboard/"))

    link = re.search(
        r'<a class="nav-link" href="([^"]+)" data-nav="crush-connect"', body
    )
    assert link and link.group(1) == "/en/crush-connect/home/"


@pytest.mark.django_db
def test_desktop_connect_link_hidden_when_not_onboarded(client, settings):
    _member(client, settings, "nav_desk_no", onboarded=False)

    body = _body(client.get("/en/dashboard/"))

    assert 'data-nav="crush-connect"' not in body


# --- Decision F: Connections -> Matches -----------------------------------


@pytest.mark.django_db
def test_member_connections_are_labelled_matches(client, settings):
    _member(client, settings, "nav_matches")

    body = _body(client.get("/en/connections/"))

    assert "<title>My Matches" in body or "My Matches |" in body
    assert "My Matches 💕" in body
    nav = body[body.index('class="bottom-nav') :]
    assert "<span>Matches</span>" in nav
    assert "<span>Connections</span>" not in body


@pytest.mark.django_db
def test_matches_label_is_translated(client, settings):
    _member(client, settings, "nav_matches_fr")

    fr = _body(client.get("/fr/connections/"))
    de = _body(client.get("/de/connections/"))

    assert "<span>Affinités</span>" in fr
    assert "Mes affinités 💕" in fr
    assert "<span>Matches</span>" in de
    assert "Meine Matches 💕" in de
