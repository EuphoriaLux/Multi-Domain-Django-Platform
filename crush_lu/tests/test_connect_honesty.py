"""UX Wave 2 · WP6 — Connect pages tell the member the truth.

6-02: the catalogue page reads visibility from the same source as the hub and
      shows Premium members their coach instead of the Premium upsell.
6-09: the chat header carries the shared Report/Block menu.
6-10: the requests inbox and the shared empty states stop implying things
      that did not happen.

Literal ``/en/...`` paths; testserver resolves to crush.lu (see
``test_connect_chat_flows.py``).
"""

import html
import re
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone

from crush_lu.models import UserBlock, UserReport
from crush_lu.models.crush_connect_cycle import (
    ConnectPairExclusion,
    ConnectTemporaryChat,
)
from crush_lu.services.connect_cycle import send_weekly_request
from crush_lu.tests.test_connect_chat_flows import CHATS_URL, _make_open_chat
from crush_lu.tests.test_connect_week_experience import (
    WEEK_INBOX_URL,
    _make_cycle_user,
    _reviewable_session_with_card,
    _seed_cycle_pool,
)
from crush_lu.tests.test_crush_connect import (
    CATALOGUE_STATUS_URL,
    _get_coach,
    _login_eligible,
    _make_user,
)

pytestmark = pytest.mark.urls("azureproject.urls_crush")


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


def _body(resp):
    assert resp.status_code == 200
    return html.unescape(resp.content.decode())


def _block(body, marker):
    """The HTML fragment starting at ``marker`` up to its closing div."""
    start = body.index(marker)
    return body[start : body.index("</div>", start)]


# --- 6-02 catalogue status ------------------------------------------------


@pytest.mark.django_db
def test_catalogue_header_says_hidden_when_hub_says_hidden(client, settings):
    """Photo consent is on, but without a photo the member is not catalogue
    eligible: the hub says "hidden", so this page must not say "in the mix"."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="nophoto", premium=False, photo_share_consent=True)
    me.crushprofile.photo_1 = ""
    me.crushprofile.save(update_fields=["photo_1"])
    _login_eligible(client, me)

    body = _body(client.get(CATALOGUE_STATUS_URL))

    assert "You're almost in the mix." in body
    assert "You're in the mix." not in body
    assert "your card is visible to eligible members" not in body
    # LuxID member without an event: the blocker is the photo, never the
    # event-verification readiness step (not a visibility gate).
    assert 'data-connect-blocking-step="photo"' in body
    assert "?section=photos" in body


@pytest.mark.django_db
def test_catalogue_header_hidden_without_known_blocker_points_to_hub(client, settings):
    """Every readiness step done but still not eligible (inactive): no
    invented blocker, just the honest status plus the full checklist link."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="stale", premium=False)
    _login_eligible(client, me)
    type(me).objects.filter(pk=me.pk).update(
        last_login=timezone.now() - timedelta(days=365)
    )

    body = _body(client.get(CATALOGUE_STATUS_URL))

    assert "You're almost in the mix." in body
    assert 'data-connect-blocking-step=""' in body
    assert "Your Connect home shows what is still missing." in body
    assert 'href="/en/crush-connect/home/"' in body


@pytest.mark.django_db
def test_catalogue_header_in_the_mix_when_eligible(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="visible", premium=False)
    _login_eligible(client, me)

    body = _body(client.get(CATALOGUE_STATUS_URL))

    assert "You're in the mix." in body
    assert "You're almost in the mix." not in body
    assert "data-connect-your-coach" not in body


@pytest.mark.django_db
def test_premium_member_sees_their_coach_not_the_upsell(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    coach = _get_coach()
    coach.user.first_name = "Coachy"
    coach.user.last_name = "Test"
    coach.user.save(update_fields=["first_name", "last_name"])
    me = _make_user(username="premiummember", premium=True)
    _login_eligible(client, me)

    body = _body(client.get(CATALOGUE_STATUS_URL))

    card = body[body.index("data-connect-your-coach") :]
    assert "Coachy Test" in card
    assert "See your Coach's Pick" in card
    assert "data-connect-premium-no-coach" not in body
    assert 'href="/en/crush-connect/coach-pick/"' in body
    assert "Premium is in closed beta." not in body
    assert "Want your own Coach Pick?" not in body


@pytest.mark.django_db
def test_non_premium_member_keeps_the_premium_upsell(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_user(username="freemember", premium=False)
    _login_eligible(client, me)

    body = _body(client.get(CATALOGUE_STATUS_URL))

    assert "Premium is in closed beta." in body
    assert "data-connect-your-coach" not in body


@pytest.mark.django_db
def test_catalogue_footer_links_to_pause(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="footer", premium=False)
    _login_eligible(client, me)

    body = _body(client.get(CATALOGUE_STATUS_URL))

    assert 'href="/en/crush-connect/pause/"' in body
    assert "anytime from settings" not in body


@pytest.mark.django_db
def test_inactive_coach_is_not_named_on_catalogue_or_hub(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    coach = _get_coach()
    coach.user.first_name = "Gonecoach"
    coach.user.save(update_fields=["first_name"])
    coach.is_active = False
    coach.save(update_fields=["is_active"])
    me = _make_user(username="orphanpremium", premium=True)
    _login_eligible(client, me)

    body = _body(client.get(CATALOGUE_STATUS_URL))
    hub = _body(client.get("/en/crush-connect/home/"))

    # Premium is still acknowledged (no upsell), but nothing claims a coach.
    assert "data-connect-your-coach" not in body
    assert "data-connect-premium-no-coach" in body
    assert "No coach is assigned to you right now." in body
    assert "See your Coach's Pick" not in body
    assert 'href="/en/crush-connect/coach-pick/"' not in body
    assert "Want your own Coach Pick?" not in body
    assert "Premium is in closed beta." not in body
    assert "Gonecoach" not in body
    assert "Gonecoach" not in hub


@pytest.mark.django_db
def test_premium_member_without_assigned_coach_gets_no_coach_card(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="nocoachpremium", premium=True)
    type(me.crushprofile).objects.filter(pk=me.crushprofile.pk).update(
        assigned_coach=None
    )
    _login_eligible(client, me)

    body = _body(client.get(CATALOGUE_STATUS_URL))

    assert "data-connect-premium-no-coach" in body
    assert "data-connect-your-coach" not in body
    assert "See your Coach's Pick" not in body
    assert "Want your own Coach Pick?" not in body


@pytest.mark.django_db
def test_identity_chip_only_when_identity_verified(client, settings):
    """Staff bypass the candidate gate, so an unverified staff member can
    reach the page: the green "verified" chip must not claim otherwise."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="staffnoid", premium=False, has_luxid=False)
    type(me).objects.filter(pk=me.pk).update(is_staff=True)
    _login_eligible(client, me)

    body = _body(client.get(CATALOGUE_STATUS_URL))

    assert "data-identity-chip" not in body
    assert "Identity verified" not in body


@pytest.mark.django_db
def test_hidden_non_premium_page_has_a_single_primary_button(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="twoprimary", premium=False, photo_share_consent=True)
    me.crushprofile.photo_1 = ""
    me.crushprofile.save(update_fields=["photo_1"])
    _login_eligible(client, me)

    body = _body(client.get(CATALOGUE_STATUS_URL))

    assert "Discover Premium" in body
    assert len(re.findall(r'class="btn-crush-primary\b', body)) == 1


# --- 6-09 chat safety -----------------------------------------------------


@pytest.mark.django_db
def test_chat_header_has_report_and_block_menu(client):
    me, target, chat = _make_open_chat()
    _login_eligible(client, me)

    body = _body(client.get(f"/en/crush-connect/week/chats/{chat.pk}/"))

    start = body.index('<header class="flex items-center gap-4')
    header = body[start : body.index("</header>", start)]
    assert 'aria-label="Safety options"' in header
    assert "w-11 h-11" in header  # 44px tap target
    assert f'action="/en/members/{target.pk}/report/"' in header
    assert 'name="source" value="connect_chat"' in header
    assert f'name="source_id" value="{chat.pk}"' in header
    # Plain block keeps the chat-aware endpoint (closes chat + pair exclusion).
    assert f'action="/en/crush-connect/week/chats/{chat.pk}/block/"' in header
    # The old tiny bottom-of-page panel is gone: one entry point only.
    assert body.count("Safety options") == 2  # aria-label + title


@pytest.mark.django_db
def test_report_from_chat_records_connect_chat_source(client):
    me, target, chat = _make_open_chat()
    _login_eligible(client, me)

    resp = client.post(
        f"/en/members/{target.pk}/report/",
        {"reason": "harassment", "source": "connect_chat", "source_id": chat.pk},
    )

    assert resp.status_code == 302
    report = UserReport.objects.get(reporter=me, reported_user=target)
    assert report.source == "connect_chat"
    assert report.source_id == chat.pk


@pytest.mark.django_db
def test_report_and_block_from_chat_uses_the_chat_block(client):
    """ "Also block them" from the chat menu must match the chat's own block:
    pair excluded from re-matching and the chat closed, not just a UserBlock."""
    me, target, chat = _make_open_chat()
    _login_eligible(client, me)

    resp = client.post(
        f"/en/members/{target.pk}/report/",
        {
            "reason": "harassment",
            "source": "connect_chat",
            "source_id": chat.pk,
            "also_block": "1",
        },
    )

    assert resp.status_code == 302
    assert UserBlock.objects.filter(blocker=me, blocked=target).exists()
    assert ConnectPairExclusion.objects.count() == 1
    chat.refresh_from_db()
    assert chat.status == ConnectTemporaryChat.Status.BLOCKED


@pytest.mark.django_db
def test_report_and_block_with_a_foreign_chat_id_falls_back_to_plain_block(client):
    """A tampered source_id naming someone else's chat never touches it."""
    _a, _b, foreign_chat = _make_open_chat()
    me, target, _chat = _make_open_chat()
    _login_eligible(client, me)

    client.post(
        f"/en/members/{target.pk}/report/",
        {
            "reason": "harassment",
            "source": "connect_chat",
            "source_id": foreign_chat.pk,
            "also_block": "1",
        },
    )

    assert UserBlock.objects.filter(blocker=me, blocked=target).exists()
    assert ConnectPairExclusion.objects.count() == 0
    foreign_chat.refresh_from_db()
    assert foreign_chat.status != ConnectTemporaryChat.Status.BLOCKED


# --- 6-10 requests + empty states -----------------------------------------


@pytest.mark.django_db
def test_inbox_without_requests_does_not_claim_someone_chose_you(client, settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("lonely")
    _login_eligible(client, me)

    body = _body(client.get(WEEK_INBOX_URL))

    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", body, re.S).group(1).strip()
    assert h1 == "Requests"
    assert "Someone chose you" not in body
    assert "No requests right now." in body


@pytest.mark.django_db
def test_inbox_request_uses_a_system_label_not_a_fake_quote(client, settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    sender = _make_cycle_user("sender")
    recipient = _make_cycle_user("recipient", gender="F")
    session, _card = _reviewable_session_with_card(sender, recipient)
    send_weekly_request(session, sender, recipient)
    _login_eligible(client, recipient)

    body = _body(client.get(WEEK_INBOX_URL))

    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", body, re.S).group(1).strip()
    assert h1 == "Someone chose you"
    assert "Sent you a Connect request" in body
    assert "I would like to get to know you." not in body


@pytest.mark.django_db
def test_empty_chats_point_to_today_not_to_events_when_event_verified(client):
    me = _make_cycle_user("nochats")
    _login_eligible(client, me)

    body = _body(client.get(CHATS_URL))

    actions = _block(body, 'data-empty-actions="chats"')
    assert 'href="/en/crush-connect/week/"' in actions
    assert "Open Today" in actions
    assert "Find an event" not in actions


@pytest.mark.django_db
def test_empty_requests_offer_find_an_event_only_when_event_missing(client, settings):
    """A LuxID-only member reaches the inbox but has no event verification:
    the event CTA is the step that actually unlocks Connect Week for them."""
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_user(username="luxidonly", premium=False)
    _login_eligible(client, me)

    body = _body(client.get(WEEK_INBOX_URL))

    actions = _block(body, 'data-empty-actions="requests"')
    assert "Find an event" in actions


@pytest.mark.django_db
def test_empty_requests_for_event_verified_member_open_today(client, settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("reqtoday")
    _login_eligible(client, me)

    body = _body(client.get(WEEK_INBOX_URL))

    actions = _block(body, 'data-empty-actions="requests"')
    assert "Find an event" not in actions
    assert "Open Today" in actions
    assert 'href="/en/crush-connect/week/"' in actions
    assert "data-week-status" not in actions  # no cards today


@pytest.mark.django_db
def test_empty_requests_in_review_window_show_status_and_review(client, settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("reqreview")
    (target,) = _seed_cycle_pool(me, n=1)
    _reviewable_session_with_card(me, target)
    _login_eligible(client, me)

    body = _body(client.get(WEEK_INBOX_URL))

    actions = _block(body, 'data-empty-actions="requests"')
    assert "Find an event" not in actions
    assert "Review your week" in actions
    assert "Open Today" not in actions
    assert "data-week-status" in actions
    assert "Today: 1 of 1 cards answered." in actions
