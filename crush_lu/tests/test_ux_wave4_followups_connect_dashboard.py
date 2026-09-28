"""
UX Wave 4 · WP13a "followups-connect-dashboard" — follow-ups from issues
#1080 (Connect Week timeline, review dialog CTA), #1083 (dashboard prompts),
#1078 (My Connections CTAs and copy) and Tom's decision I (prompt timing,
block dialog "Also let a Crush Coach know", "Meeting safely" link).

Literal ``/en/`` paths throughout; the HX-Refresh test for a blocked request
lives with its siblings in ``test_ux_wave3_dashboard.py``.
"""

from datetime import timedelta
from html.parser import HTMLParser

import pytest
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from crush_lu.models import EventConnection, EventRegistration
from crush_lu.models.crush_connect_cycle import (
    ConnectTemporaryChat,
    ConnectWeeklyRequest,
    ConnectWeekSession,
)
from crush_lu.services.connect_cycle import week_timeline_state
from crush_lu.tests.test_connect_chat_flows import _make_open_chat
from crush_lu.tests.test_connect_week_experience import (
    WEEK_REVIEW_URL,
    _answer_all,
    _make_cycle_user,
    _reviewable_session_with_card,
    _seed_cycle_pool,
)
from crush_lu.tests.test_crush_connect import _login_eligible
from crush_lu.tests.test_ux_wave3_dashboard import _make_event, _make_member


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


class _Collector(HTMLParser):
    """Collects (tag, attrs) for every start tag, plus whether it sits
    inside a <dialog>."""

    def __init__(self):
        super().__init__()
        self.tags = []
        self._dialog_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "dialog":
            self._dialog_depth += 1
        self.tags.append((tag, dict(attrs), self._dialog_depth > 0))

    def handle_endtag(self, tag):
        if tag == "dialog" and self._dialog_depth:
            self._dialog_depth -= 1


def _tags(html):
    parser = _Collector()
    parser.feed(html)
    return parser.tags


def _classes(attrs):
    return (attrs.get("class") or "").split()


# ---------------------------------------------------------------------------
# #1080 — week_timeline_state()
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status",
    [
        ConnectWeeklyRequest.Status.DECLINED,
        ConnectWeeklyRequest.Status.EXPIRED,
        ConnectWeeklyRequest.Status.CANCELLED,
    ],
)
def test_ended_request_does_not_revert_the_timeline_to_review(status):
    from crush_lu.services.connect_cycle import send_weekly_request

    me = _make_cycle_user(f"tl_end_{status}")
    target = _make_cycle_user(f"tl_end_target_{status}")
    session, _card = _reviewable_session_with_card(me, target)
    req = send_weekly_request(session, me, target)
    ConnectWeeklyRequest.objects.filter(pk=req.pk).update(status=status)
    req.refresh_from_db()

    # Neither passed in nor looked up: both paths must agree.
    assert week_timeline_state(session, sent_request=req) is None
    assert week_timeline_state(session) is None


def _complete(session):
    ConnectWeekSession.objects.filter(pk=session.pk).update(
        status=ConnectWeekSession.Status.COMPLETED, completed_at=timezone.now()
    )
    session.refresh_from_db()
    return session


@pytest.mark.django_db
def test_completed_session_keeps_the_chat_step_while_the_chat_is_open():
    me, _target, _chat = _make_open_chat()
    session = _complete(ConnectWeekSession.objects.filter(user=me).first())

    state = week_timeline_state(session)

    assert state == {"step": 4, "next_kind": None, "next_at": None}


@pytest.mark.django_db
def test_completed_session_keeps_the_coffee_step_with_a_plan():
    from crush_lu.services.connect_chat import propose_venue

    me, _target, chat = _make_open_chat()
    propose_venue(
        chat,
        me,
        venue_location_id=None,
        custom_venue_name="Café de Paris",
        proposed_date=(timezone.localdate() + timedelta(days=1)).isoformat(),
        proposed_time_slot="Evening",
    )
    session = _complete(ConnectWeekSession.objects.filter(user=me).first())

    assert week_timeline_state(session)["step"] == 5


@pytest.mark.django_db
@pytest.mark.parametrize(
    "chat_status",
    [ConnectTemporaryChat.Status.CLOSED, ConnectTemporaryChat.Status.BLOCKED],
)
def test_completed_session_hides_the_timeline_once_the_chat_ended(chat_status):
    me, _target, chat = _make_open_chat()
    ConnectTemporaryChat.objects.filter(pk=chat.pk).update(status=chat_status)
    session = _complete(ConnectWeekSession.objects.filter(user=me).first())

    assert week_timeline_state(session) is None


@pytest.mark.django_db
def test_hub_shows_the_chat_step_for_a_completed_session_with_an_open_chat(
    client, settings
):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me, _target, _chat = _make_open_chat()
    _complete(ConnectWeekSession.objects.filter(user=me).first())
    _login_eligible(client, me)

    body = client.get("/en/crush-connect/home/").content.decode()

    current = [
        attrs
        for tag, attrs, _in_dialog in _tags(body)
        if tag == "li" and attrs.get("aria-current") == "step"
    ]
    assert len(current) == 1
    assert "connect-week-timeline-step" in _classes(current[0])
    assert "Chat" in body


@pytest.mark.django_db
def test_review_dialog_confirm_is_the_solid_variant(client, settings):
    """#1080: several review cards each carry a confirm dialog, so the
    gradient CTA would repeat; the confirm is btn-crush-solid again."""
    from crush_lu.services.connect_cycle import (
        get_or_create_active_session,
        get_or_create_todays_cards,
    )
    from crush_lu.services.connect_cycle import CYCLE_LENGTH_DAYS

    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("rv_solid")
    _seed_cycle_pool(me, n=3)
    session = get_or_create_active_session(me)
    for card in get_or_create_todays_cards(session):
        _answer_all(card)
    ConnectWeekSession.objects.filter(pk=session.pk).update(
        started_at=timezone.now() - timedelta(days=CYCLE_LENGTH_DAYS)
    )
    _login_eligible(client, me)

    resp = client.get(WEEK_REVIEW_URL)
    assert resp.status_code == 200
    submits = [
        _classes(attrs)
        for tag, attrs, in_dialog in _tags(resp.content.decode())
        if tag == "button" and in_dialog and attrs.get("type") == "submit"
    ]
    assert submits, "expected at least one review confirm dialog"
    for classes in submits:
        assert "btn-crush-solid" in classes
        assert "btn-crush-primary" not in classes


# ---------------------------------------------------------------------------
# Decision I — block dialog checkbox + "Meeting safely" link on the chat
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_chat_block_form_offers_the_optional_coach_checkbox(client):
    me, _target, chat = _make_open_chat()
    _login_eligible(client, me)

    body = client.get(f"/en/crush-connect/week/chats/{chat.pk}/").content.decode()
    tags = _tags(body)

    block_forms = [
        attrs
        for tag, attrs, _d in tags
        if tag == "form"
        and attrs.get("action") == f"/en/crush-connect/week/chats/{chat.pk}/block/"
    ]
    assert len(block_forms) == 1
    form = block_forms[0]
    assert form["data-confirm-option"] == "escalate_to_coach"
    assert form["data-confirm-option-label"] == "Also let a Crush Coach know"
    hidden = [
        attrs
        for tag, attrs, _d in tags
        if tag == "input" and attrs.get("name") == "escalate_to_coach"
    ]
    # A hidden input the sheet fills; empty (= unticked) by default.
    assert len(hidden) == 1
    assert hidden[0]["type"] == "hidden"
    assert hidden[0]["value"] == ""
    # The sheet renders the checkbox, unticked and hidden until asked for.
    options = [attrs for tag, attrs, _d in tags if "data-confirm-option" in attrs]
    sheet_option = [a for a in options if a is not form]
    assert any("hidden" in a for a in sheet_option)
    boxes = [
        attrs
        for tag, attrs, _d in tags
        if tag == "input" and "data-confirm-option-input" in attrs
    ]
    assert len(boxes) == 1
    assert "checked" not in boxes[0]


@pytest.mark.django_db
def test_ticked_coach_box_files_a_connect_report(client):
    from crush_lu.models.crush_connect_cycle import ConnectReport

    me, target, chat = _make_open_chat()
    _login_eligible(client, me)

    client.post(
        f"/en/crush-connect/week/chats/{chat.pk}/block/",
        {"escalate_to_coach": "1"},
    )

    assert ConnectReport.objects.filter(reporter=me, reported_user=target).count() == 1


@pytest.mark.django_db
def test_unticked_coach_box_blocks_without_a_report(client):
    from crush_lu.models.crush_connect_cycle import ConnectReport

    me, _target, chat = _make_open_chat()
    _login_eligible(client, me)

    client.post(
        f"/en/crush-connect/week/chats/{chat.pk}/block/", {"escalate_to_coach": ""}
    )

    chat.refresh_from_db()
    assert chat.status == ConnectTemporaryChat.Status.BLOCKED
    assert not ConnectReport.objects.exists()


@pytest.mark.django_db
def test_chat_links_meeting_safely_to_the_support_safety_anchor(client):
    me, _target, chat = _make_open_chat()
    _login_eligible(client, me)

    body = client.get(f"/en/crush-connect/week/chats/{chat.pk}/").content.decode()

    links = [attrs for tag, attrs, _d in _tags(body) if "data-meeting-safely" in attrs]
    assert len(links) == 1
    assert links[0]["href"] == "/en/support/#safety"
    assert "Meeting safely" in body


# ---------------------------------------------------------------------------
# #1078 — My Connections
# ---------------------------------------------------------------------------


class MyConnectionsFollowupTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = _make_member("wp13a-conn@example.com")
        self.client.login(username="wp13a-conn@example.com", password="testpass123")

    def test_empty_state_ctas_carry_no_inert_padding_and_new_step_three(self):
        from unittest import mock

        # The Connect CTA is gated on the hub's own access check; open it.
        with mock.patch(
            "crush_lu.views_crush_connect._hub_access_blocker", return_value=None
        ):
            response = self.client.get("/en/connections/", HTTP_HOST="crush.lu")
        body = response.content.decode()
        ctas = [
            _classes(attrs)
            for tag, attrs, _d in _tags(body)
            if tag == "a"
            and "gap-2" in _classes(attrs)
            and (
                (
                    "btn-crush-primary" in _classes(attrs)
                    and attrs["href"] == "/en/events/"
                )
                or (
                    "btn-crush-outline" in _classes(attrs)
                    and attrs["href"].startswith("/en/crush-connect")
                )
            )
        ]
        self.assertEqual(len(ctas), 2)
        for classes in ctas:
            self.assertNotIn("px-6", classes)
            self.assertNotIn("py-3", classes)
        self.assertContains(response, "You're connected, directly or via your coach")
        self.assertNotContains(response, "Your coach introduces you")

    def test_step_three_is_translated(self):
        de = self.client.get("/de/connections/", HTTP_HOST="crush.lu")
        self.assertContains(de, "Ihr seid verbunden, direkt oder über deinen Coach")
        fr = self.client.get("/fr/connections/", HTTP_HOST="crush.lu")
        self.assertContains(
            fr, "Vous êtes mis en relation, directement ou via votre coach"
        )

    def test_accepted_card_next_step_is_the_outline_variant(self):
        requester = _make_member("wp13a-req@example.com")
        event = _make_event("Past", days_from_now=-3)
        for user in (self.user, requester):
            EventRegistration.objects.create(user=user, event=event, status="attended")
        connection = EventConnection.objects.create(
            requester=requester, recipient=self.user, event=event, status="pending"
        )
        response = self.client.post(
            f"/en/connections/{connection.id}/accept/",
            HTTP_HOST="crush.lu",
            HTTP_HX_REQUEST="true",
            HTTP_HX_CURRENT_URL="https://crush.lu/en/connections/",
        )
        links = [
            _classes(attrs)
            for tag, attrs, _d in _tags(response.content.decode())
            if tag == "a" and attrs.get("href") == f"/en/connections/{connection.id}/"
        ]
        self.assertEqual(len(links), 1)
        self.assertIn("btn-crush-outline", links[0])
        self.assertNotIn("btn-crush-primary", links[0])


# ---------------------------------------------------------------------------
# #1083 + decision I — install card / push prompt gating
# ---------------------------------------------------------------------------


class PromptGatingTests(TestCase):
    def setUp(self):
        cache.clear()

    def _login(self, username, *, approved):
        user = _make_member(username, verified=approved)
        self.client.login(username=username, password="testpass123")
        return user

    def _xdata(self, path):
        body = self.client.get(path, HTTP_HOST="crush.lu").content.decode()
        return [attrs.get("x-data") for tag, attrs, _d in _tags(body)]

    def test_unapproved_member_without_booking_gets_neither_prompt(self):
        self._login("wp13a-new@example.com", approved=False)
        xdata = self._xdata("/en/events/")
        self.assertNotIn("pwaInstallBanner", xdata)
        self.assertNotIn("pushActivationPrompt", xdata)

    def test_approved_member_gets_install_but_not_push(self):
        self._login("wp13a-approved@example.com", approved=True)
        xdata = self._xdata("/en/events/")
        self.assertIn("pwaInstallBanner", xdata)
        self.assertNotIn("pushActivationPrompt", xdata)

    def test_first_confirmed_booking_unlocks_both_prompts(self):
        user = self._login("wp13a-booked@example.com", approved=False)
        EventRegistration.objects.create(
            user=user, event=_make_event(), status="confirmed"
        )
        xdata = self._xdata("/en/events/")
        self.assertIn("pwaInstallBanner", xdata)
        self.assertIn("pushActivationPrompt", xdata)

    def test_a_waitlist_or_pending_payment_booking_is_not_a_success(self):
        user = self._login("wp13a-wait@example.com", approved=False)
        EventRegistration.objects.create(
            user=user, event=_make_event("W"), status="waitlist"
        )
        EventRegistration.objects.create(
            user=user, event=_make_event("P"), status="pending"
        )
        xdata = self._xdata("/en/events/")
        self.assertNotIn("pushActivationPrompt", xdata)
        self.assertNotIn("pwaInstallBanner", xdata)

    def test_dashboard_install_card_replaces_the_global_banner(self):
        self._login("wp13a-dash@example.com", approved=True)
        xdata = self._xdata("/en/dashboard/")
        self.assertIn("pwaInstallButton", xdata)
        self.assertNotIn("pwaInstallBanner", xdata)

    def test_dashboard_has_no_install_card_before_approval_or_booking(self):
        self._login("wp13a-dash-new@example.com", approved=False)
        xdata = self._xdata("/en/dashboard/")
        self.assertNotIn("pwaInstallButton", xdata)
        self.assertNotIn("pwaInstallBanner", xdata)

    def test_anonymous_visitor_gets_no_install_banner(self):
        xdata = self._xdata("/en/events/")
        self.assertNotIn("pwaInstallBanner", xdata)
