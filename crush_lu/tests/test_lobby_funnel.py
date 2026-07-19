"""
Tests for the Event Lobby ↔ attendees/connections funnel unification
(decisions 2026-07-18, spec §5.3 amendment):

- the live-overlap gate: the named attendees page and connection-request
  endpoints open only at the scheduled event end;
- the connection window counted from the event end (aligned with the 48h
  lobby recap);
- the branching post-event recap email (participant / LuxID-capable guest /
  everyone else).
"""

from datetime import timedelta

import pytest
from django.core import mail
from django.urls import reverse

from crush_lu.models import EventConnection
from crush_lu.tests.test_event_lobby import (
    _attend,
    _end_event,
    _join,
    _login,
    _make_event,
    _make_member,
)

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]


@pytest.fixture(autouse=True)
def lobby_flags(settings):
    settings.CRUSH_EVENT_LOBBY_ENABLED = True
    settings.CRUSH_CONNECT_LAUNCHED = True
    settings.AZURE_ACCOUNT_NAME = ""


def _attendees_url(event):
    return reverse("crush_lu:event_attendees", kwargs={"event_id": event.pk})


def _request_url(event, user):
    return reverse(
        "crush_lu:request_connection",
        kwargs={"event_id": event.pk, "user_id": user.pk},
    )


def _inline_url(event, user):
    return reverse(
        "crush_lu:request_connection_inline",
        kwargs={"event_id": event.pk, "user_id": user.pk},
    )


class TestLiveOverlapGate:
    """During the live phase the anonymous lobby is the only social surface —
    the named attendees list and connection requests open at the exact end."""

    def test_attendees_page_redirects_while_live(self, client):
        member = _make_member("gate_a")
        event = _make_event()  # started 30 min ago, ends in 90
        _attend(member, event)
        _login(client, member)
        response = client.get(_attendees_url(event))
        assert response.status_code == 302
        assert response.url == reverse(
            "crush_lu:event_detail", kwargs={"event_id": event.pk}
        )

    def test_attendees_page_opens_after_end(self, client):
        member = _make_member("gate_b")
        other = _make_member("gate_b2")
        event = _make_event()
        _attend(member, event)
        _attend(other, event)
        _end_event(event)
        _login(client, member)
        response = client.get(_attendees_url(event))
        assert response.status_code == 200

    def test_request_connection_blocked_while_live(self, client):
        member = _make_member("gate_c")
        other = _make_member("gate_c2")
        event = _make_event()
        _attend(member, event)
        _attend(other, event)
        _login(client, member)
        response = client.post(_request_url(event, other), {"note": "hi"})
        assert response.status_code == 302
        assert response.url == reverse(
            "crush_lu:event_detail", kwargs={"event_id": event.pk}
        )
        assert not EventConnection.objects.filter(event=event).exists()

    def test_request_connection_inline_blocked_while_live(self, client):
        member = _make_member("gate_d")
        other = _make_member("gate_d2")
        event = _make_event()
        _attend(member, event)
        _attend(other, event)
        _login(client, member)
        response = client.post(_inline_url(event, other), {"note": "hi"})
        assert response.status_code == 200  # htmx error partial
        assert b"Connections open once the event ends." in response.content
        assert not EventConnection.objects.filter(event=event).exists()

    def test_request_connection_allowed_after_end(self, client):
        member = _make_member("gate_e")
        other = _make_member("gate_e2")
        event = _make_event()
        _attend(member, event)
        _attend(other, event)
        _end_event(event)
        _login(client, member)
        response = client.post(_request_url(event, other), {"note": "hi"})
        assert response.status_code == 302
        assert response.url == _attendees_url(event)
        assert EventConnection.objects.filter(
            event=event, requester=member, recipient=other
        ).exists()


class TestConnectionWindowFromEnd:
    """The window counts from the scheduled end and defaults to 48h — the
    same span as the lobby recap, so both surfaces close together."""

    def test_default_window_is_48h_from_end(self):
        event = _make_event()
        assert event.connection_window_hours == 48
        assert event.connection_window_deadline == event.end_time + timedelta(hours=48)

    def test_connections_open_only_between_end_and_deadline(self):
        live = _make_event()
        assert live.connections_open is False  # still live

        open_event = _make_event()
        _end_event(open_event, hours_ago=1)
        assert open_event.connections_open is True

        closed = _make_event()
        _end_event(closed, hours_ago=49)
        assert closed.connections_open is False

    def test_request_after_window_redirects_to_teaser(self, client):
        member = _make_member("win_a")
        other = _make_member("win_a2")
        event = _make_event()
        _attend(member, event)
        _attend(other, event)
        _end_event(event, hours_ago=49)
        _login(client, member)
        response = client.post(_request_url(event, other), {"note": "hi"})
        assert response.status_code == 302
        assert response.url == reverse("crush_lu:crush_connect_teaser")
        assert not EventConnection.objects.filter(event=event).exists()


class TestRecapEmailBranching:
    """send_event_recap: lobby participants get the recap CTA, LuxID-capable
    non-members get the Finish-Crush-Connect nudge, everyone else neither.
    All variants keep the classic attendees link."""

    def _send(self, registration):
        from crush_lu.email_helpers import send_event_recap

        sent = send_event_recap(registration)
        assert sent
        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        # send_domain_email packs the html straight into body (subtype html).
        assert message.content_subtype == "html"
        return message.body

    def test_participant_gets_recap_cta(self):
        from crush_lu.models import EventRegistration

        member = _make_member("mail_a")
        event = _make_event()
        _join(member, event)
        _end_event(event)
        registration = EventRegistration.objects.get(event=event, user=member)
        html = self._send(registration)
        assert f"/events/{event.pk}/lobby/" in html
        assert "Confirm who you met" in html
        assert "Finish Crush Connect" not in html

    def test_luxid_guest_gets_connect_nudge(self):
        guest = _make_member("mail_b", membership=False)
        event = _make_event()
        registration = _attend(guest, event)
        _end_event(event)
        html = self._send(registration)
        assert "Finish Crush Connect" in html
        assert f"/events/{event.pk}/lobby/" not in html

    def test_plain_guest_gets_neither(self):
        guest = _make_member("mail_c", membership=False, luxid=False)
        event = _make_event()
        registration = _attend(guest, event)
        _end_event(event)
        html = self._send(registration)
        assert "Finish Crush Connect" not in html
        assert f"/events/{event.pk}/lobby/" not in html

    def test_excluded_member_gets_neither(self):
        """Coach exclusion renders as if no lobby state existed — no nudge."""
        member = _make_member("mail_d", excluded=True)
        event = _make_event()
        registration = _attend(member, event)
        _end_event(event)
        html = self._send(registration)
        assert "Finish Crush Connect" not in html
        assert f"/events/{event.pk}/lobby/" not in html

    def test_recap_sends_from_crush_domain(self):
        """Codex P2 (2026-07-19): request-less batch sends must not fall back
        to the PowerUp sender config."""
        from azureproject.email_utils import get_domain_email_config

        guest = _make_member("mail_f", membership=False, luxid=False)
        event = _make_event()
        registration = _attend(guest, event)
        _end_event(event)
        self._send(registration)
        expected = get_domain_email_config(domain="crush.lu")["DEFAULT_FROM_EMAIL"]
        assert mail.outbox[0].from_email == expected

    def test_feature_off_gets_neither(self, settings):
        settings.CRUSH_EVENT_LOBBY_ENABLED = False
        member = _make_member("mail_e")
        event = _make_event()
        from crush_lu.models import EventRegistration

        _join(member, event)  # no-op participation while flag off
        _end_event(event)
        registration = EventRegistration.objects.get(event=event, user=member)
        html = self._send(registration)
        assert "Finish Crush Connect" not in html
        assert f"/events/{event.pk}/lobby/" not in html


class TestEntryPointRendering:
    """The new entry-point surfaces actually render the CTA / promo blocks
    (template regressions here would otherwise only surface in the browser)."""

    def test_event_detail_renders_live_cta_for_member(self, client):
        member = _make_member("render_a")
        event = _make_event()
        _attend(member, event)
        _login(client, member)
        response = client.get(
            reverse("crush_lu:event_detail", kwargs={"event_id": event.pk})
        )
        assert response.status_code == 200
        assert b"Event Lobby is live" in response.content
        assert f"/events/{event.pk}/lobby/".encode() in response.content

    def test_event_detail_renders_finish_connect_for_luxid_guest(self, client):
        guest = _make_member("render_b", membership=False)
        event = _make_event()
        _attend(guest, event)
        _login(client, guest)
        response = client.get(
            reverse("crush_lu:event_detail", kwargs={"event_id": event.pk})
        )
        assert response.status_code == 200
        assert b"Finish Crush Connect to join the Event Lobby" in response.content

    def test_event_detail_renders_static_promo_for_anonymous(self, client):
        event = _make_event()
        response = client.get(
            reverse("crush_lu:event_detail", kwargs={"event_id": event.pk})
        )
        assert response.status_code == 200
        assert b"members-only lobby at every event" in response.content

    def test_event_detail_hides_everything_when_flag_off(self, settings, client):
        settings.CRUSH_EVENT_LOBBY_ENABLED = False
        member = _make_member("render_c")
        event = _make_event()
        _attend(member, event)
        _login(client, member)
        response = client.get(
            reverse("crush_lu:event_detail", kwargs={"event_id": event.pk})
        )
        assert response.status_code == 200
        assert b"Event Lobby is live" not in response.content
        assert b"members-only lobby" not in response.content

    def test_ticket_page_renders_live_cta_after_checkin(self, client):
        member = _make_member("render_d")
        event = _make_event()
        _attend(member, event)
        _login(client, member)
        response = client.get(
            reverse("crush_lu:event_ticket", kwargs={"event_id": event.pk})
        )
        assert response.status_code == 200
        assert f"/events/{event.pk}/lobby/".encode() in response.content


class TestAttendeesPageClosesWithWindow:
    """Codex P1 (2026-07-19): the named roster must disappear once the
    connection window closes — both post-event surfaces close together."""

    def test_attendees_page_redirects_after_window(self, client):
        member = _make_member("close_a")
        other = _make_member("close_a2")
        event = _make_event()
        _attend(member, event)
        _attend(other, event)
        _end_event(event, hours_ago=49)  # 48h window from end has closed
        _login(client, member)
        response = client.get(_attendees_url(event))
        assert response.status_code == 302
        assert response.url == reverse("crush_lu:my_connections")


class TestWeeklyKpiMutualReveals:
    """Codex P2 (2026-07-19): a reveal stamps both directional signal rows —
    the KPI must count each pair once."""

    def test_mutual_reveal_counted_once_per_pair(self):
        from datetime import timedelta as td

        from django.utils import timezone

        from crush_lu.models import EventMeetSignal
        from crush_lu.services.weekly_kpis import compute_weekly_snapshot

        a = _make_member("kpi_a")
        b = _make_member("kpi_b")
        event = _make_event()
        _join(a, event)
        _join(b, event)
        now = timezone.now()
        EventMeetSignal.objects.create(
            event=event, sender=a, recipient=b, mutual_revealed_at=now
        )
        EventMeetSignal.objects.create(
            event=event, sender=b, recipient=a, mutual_revealed_at=now
        )
        today = timezone.localdate()
        week_start = today - td(days=today.weekday())
        snapshot = compute_weekly_snapshot(week_start)
        assert snapshot["event_lobby"]["meet_signals"] == 2
        assert snapshot["event_lobby"]["mutual_reveals"] == 1
