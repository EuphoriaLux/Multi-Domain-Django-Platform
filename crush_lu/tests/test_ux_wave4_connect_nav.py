"""UX Wave 4 · WP10 "connect-nav" — findings 6-04, 6-05 and decision F.

6-04: the Connect subnav has four tabs (Home first), the "← Connect home" and
      page-local back links are gone, and the 44px rule no longer stretches
      every text link in the Connect shell.
6-05: a top-level desktop "Connect" link, gated like the bottom-nav tab.
F:    the member-facing "Connections" (crush_lu:my_connections) is "Matches".
Follow-up (2026-09-28): desktop Connect badge from a cached counts-only
      helper, no detail-mode up-link in the profile editor, Home is the
      current tab on every other hub page, FR subnav says "Chats".

Literal ``/en/...`` paths; testserver resolves to crush.lu.
"""

import html
import re
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

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


# --- Follow-up decisions (2026-09-28) --------------------------------------


class _Links(HTMLParser):
    """Every ``<a>``: attrs, visible text, ``sr-only`` text, subnav membership."""

    def __init__(self):
        super().__init__()
        self.links, self._a, self._spans, self._subnav = [], None, [], False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "nav" and "connect-local-nav" in (attrs.get("class") or ""):
            self._subnav = True
        elif tag == "a":
            self._a = {"attrs": attrs, "text": "", "sr": "", "subnav": self._subnav}
            self._spans = []
        elif tag == "span" and self._a is not None:
            self._spans.append("sr-only" in (attrs.get("class") or ""))

    def handle_endtag(self, tag):
        if tag == "nav":
            self._subnav = False
        elif tag == "a" and self._a is not None:
            self._a["text"] = " ".join(self._a["text"].split())
            self._a["sr"] = " ".join(self._a["sr"].split())
            self.links.append(self._a)
            self._a = None
        elif tag == "span" and self._spans:
            self._spans.pop()

    def handle_data(self, data):
        if self._a is not None:
            self._a["sr" if any(self._spans) else "text"] += data


def _links(body):
    parser = _Links()
    parser.feed(body)
    return parser.links


def _tabs(body):
    return [link for link in _links(body) if link["subnav"]]


def _current_tab(body):
    current = [t for t in _tabs(body) if t["attrs"].get("aria-current") == "page"]
    assert len(current) == 1, [t["text"] for t in current]
    return current[0]["attrs"]["href"]


def _desktop_connect(body):
    links = [
        link
        for link in _links(body)
        if link["attrs"].get("data-nav") == "crush-connect"
    ]
    assert len(links) == 1
    return links[0]


def _connect_activity(me):
    """One live request + one unread chat for ``me``, plus rows both the
    subnav and the badge must ignore (blocked, expired, ineligible, read)."""
    from crush_lu.models import UserBlock
    from crush_lu.models.crush_connect_cycle import (
        ConnectChatMessage,
        ConnectTemporaryChat,
        ConnectWeeklyRequest,
        ConnectWeekSession,
    )

    def request(name, status=ConnectWeeklyRequest.Status.PENDING, **kwargs):
        other = _make_user(username=f"{me.username}_{name}", gender="F", **kwargs)
        return ConnectWeeklyRequest.objects.create(
            session=ConnectWeekSession.objects.create(user=other),
            requester=other,
            recipient=me,
            status=status,
        )

    request("live")
    UserBlock.objects.create(blocker=me, blocked=request("blocked").requester)
    expired = request("expired")
    ConnectWeeklyRequest.objects.filter(pk=expired.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )
    request("noid", has_luxid=False, premium=False)
    for name, read_at in (("unread", None), ("read", timezone.now())):
        accepted = request(name, ConnectWeeklyRequest.Status.ACCEPTED)
        chat = ConnectTemporaryChat.objects.create(
            request=accepted,
            participant_1=accepted.requester,
            participant_2=me,
            expires_at=timezone.now() + timedelta(days=7),
        )
        ConnectChatMessage.objects.create(
            chat=chat, sender=accepted.requester, message="Hi", read_at=read_at
        )


@pytest.mark.django_db
@pytest.mark.parametrize("paused", [False, True])
def test_badge_count_uses_the_subnav_definitions(client, settings, paused):
    from crush_lu.services.connect_summary import (
        connect_nav_badge_count,
        get_connect_summary,
    )

    me = _member(client, settings, f"badge_def_{paused}")
    _connect_activity(me)
    if paused:
        me.crush_connect_membership.pause()

    summary = get_connect_summary(me)

    assert (summary["pending_requests"], summary["unread_chats"]) == (
        (0, 1) if paused else (1, 1)
    )
    expected = summary["pending_requests"] + summary["unread_chats"]
    assert connect_nav_badge_count(me) == expected


@pytest.mark.django_db
def test_badge_count_skips_hidden_encounters_and_coach_pairs(client, settings):
    from crush_lu.models import ConfirmedEncounter, CrushCoach
    from crush_lu.models.crush_connect_cycle import (
        ConnectWeeklyRequest,
        ConnectWeekSession,
    )
    from crush_lu.services.connect_summary import (
        connect_nav_badge_count,
        get_connect_summary,
    )

    me = _member(client, settings, "badge_pairs")
    _connect_activity(me)
    my_coach = CrushCoach.objects.create(user=me, bio="", specializations="")
    for name in ("hidden", "client"):
        other = _make_user(username=f"badge_pairs_{name}", gender="F")
        ConnectWeeklyRequest.objects.create(
            session=ConnectWeekSession.objects.create(user=other),
            requester=other,
            recipient=me,
        )
    ConfirmedEncounter.objects.create(
        user_low=me,
        user_high=get_user_model().objects.get(username="badge_pairs_hidden"),
        status="removal_pending",
    )
    client_user = get_user_model().objects.get(username="badge_pairs_client")
    client_user.crushprofile.assigned_coach = my_coach
    client_user.crushprofile.save(update_fields=["assigned_coach"])

    summary = get_connect_summary(me)

    assert (summary["pending_requests"], summary["unread_chats"]) == (1, 1)
    assert connect_nav_badge_count(me) == 2


@pytest.mark.django_db
def test_badge_count_is_two_counts_then_cached_per_user(client, settings):
    from crush_lu.services.connect_summary import connect_nav_badge_count

    me = _member(client, settings, "badge_q")
    other = _make_user(username="badge_q_other")
    _connect_activity(me)
    assert me.crush_connect_membership  # the nav gate has already loaded it

    with CaptureQueriesContext(connection) as miss:
        assert connect_nav_badge_count(me) == 2
    assert len(miss) == 2
    assert all("COUNT(" in q["sql"].upper() for q in miss.captured_queries)

    with CaptureQueriesContext(connection) as hit:
        assert connect_nav_badge_count(me) == 2
    assert len(hit) == 0
    assert other.crush_connect_membership
    assert connect_nav_badge_count(other) == 0  # the key is per user


@pytest.mark.django_db
def test_desktop_connect_link_shows_badge_without_the_full_summary(
    client, settings, monkeypatch
):
    from crush_lu.services import connect_summary

    me = _member(client, settings, "badge_page")
    _connect_activity(me)

    def boom(*args, **kwargs):
        raise AssertionError("the header must not build the full Connect summary")

    monkeypatch.setattr(connect_summary, "get_connect_summary", boom)
    monkeypatch.setattr(connect_summary, "sync_session_state", boom)

    link = _desktop_connect(_body(client.get("/en/dashboard/")))

    assert link["text"] == "Connect 2"
    assert link["sr"] == "2 items need your attention"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("lang", "one", "many"),
    [
        ("en", "1 item needs your attention", "2 items need your attention"),
        (
            "de",
            "1 Eintrag braucht deine Aufmerksamkeit",
            "2 Einträge brauchen deine Aufmerksamkeit",
        ),
        (
            "fr",
            "1 élément requiert votre attention",
            "2 éléments requièrent votre attention",
        ),
    ],
)
def test_badge_label_is_pluralized_and_translated(client, settings, lang, one, many):
    from crush_lu.models.crush_connect_cycle import ConnectChatMessage

    me = _member(client, settings, f"badge_{lang}")
    _connect_activity(me)
    ConnectChatMessage.objects.update(read_at=timezone.now())

    assert _desktop_connect(_body(client.get(f"/{lang}/dashboard/")))["sr"] == one
    ConnectChatMessage.objects.filter(sender__username__endswith="_unread").update(
        read_at=None
    )
    cache.clear()
    assert _desktop_connect(_body(client.get(f"/{lang}/dashboard/")))["sr"] == many


@pytest.mark.django_db
def test_desktop_connect_link_has_no_badge_when_nothing_is_waiting(client, settings):
    _member(client, settings, "badge_zero")

    link = _desktop_connect(_body(client.get("/en/dashboard/")))

    assert (link["text"], link["sr"]) == ("Connect", "")


@pytest.mark.django_db
def test_profile_edit_detail_has_no_up_link(client, settings):
    _member(client, settings, "nav_detail")

    body = _body(client.get(PROFILE_EDIT_URL + "?section=questions"))

    assert 'name="section" value="questions"' in body
    assert "Back to your profile" not in body


@pytest.mark.django_db
@pytest.mark.parametrize(
    "url",
    [
        HUB_URL,
        PAUSE_URL,
        PROFILE_EDIT_URL,
        PROFILE_EDIT_URL + "?section=questions",
        "/en/crush-connect/coach-pick/",
    ],
)
def test_home_tab_is_current_on_other_hub_pages(client, settings, url):
    from crush_lu.services.crush_connect import propose_coach_pick

    me = _member(client, settings, "nav_home_current")
    candidate = _make_user(username="nav_home_pick", gender="F", premium=False)
    propose_coach_pick(me.crushprofile.assigned_coach, me, candidate)

    assert _current_tab(_body(client.get(url))) == HUB_URL


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("url", "index"),
    [
        (CATALOGUE_STATUS_URL, 1),
        ("/en/crush-connect/week/inbox/", 2),
        ("/en/crush-connect/week/chats/", 3),
    ],
)
def test_home_tab_yields_to_the_owning_tab(client, settings, url, index):
    _member(client, settings, "nav_owned")

    body = _body(client.get(url))

    assert _current_tab(body) == _tabs(body)[index]["attrs"]["href"] != HUB_URL


@pytest.mark.django_db
def test_fr_subnav_chats_tab_says_chats(client, settings):
    from django.utils import translation

    _member(client, settings, "nav_fr_chats")

    tabs = _tabs(_body(client.get("/fr/crush-connect/home/")))

    assert tabs[3]["attrs"]["href"] == "/fr/crush-connect/week/chats/"
    assert tabs[3]["text"] == "Chats"
    with translation.override("fr"):
        # Other "Chats" labels keep their established French wording.
        assert translation.gettext("Chats") == "Conversations"


# --- PR #1091 review fixes ------------------------------------------------


@pytest.mark.django_db
def test_chat_back_link_keeps_a_44px_tap_target(client, settings):
    """The icon-only "All chats" link lost its 44px floor when the rule was
    scoped to controls; it now sizes itself (w-11 h-11 = 44px)."""
    from crush_lu.models.crush_connect_cycle import ConnectTemporaryChat

    me = _member(client, settings, "nav_chat_back")
    _connect_activity(me)
    chat = ConnectTemporaryChat.objects.filter(participant_2=me).first()

    body = _body(client.get(f"/en/crush-connect/week/chats/{chat.pk}/"))

    back = [a for a in _links(body) if a["attrs"].get("aria-label") == "All chats"]
    assert len(back) == 1
    assert {"w-11", "h-11"} <= set(back[0]["attrs"]["class"].split())


@pytest.mark.django_db
def test_desktop_connect_link_does_not_depend_on_profile_approval(client, settings):
    """Gated by crush_connect_nav_visible alone, like the bottom-nav tab: an
    onboarded member whose profile is no longer approved keeps the link."""
    me = _member(client, settings, "nav_unapproved")
    me.crushprofile.is_approved = False
    me.crushprofile.save(update_fields=["is_approved"])

    body = _body(client.get("/en/events/"))

    assert _desktop_connect(body)["attrs"]["href"] == "/en/crush-connect/home/"
