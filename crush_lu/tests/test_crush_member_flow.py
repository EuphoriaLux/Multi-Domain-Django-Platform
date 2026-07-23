"""
"My Crush!" Phase C — member declaration flow tests.

Spec: docs/superpowers/specs/2026-07-21-crush-my-crush-post-event-flow.md

Covers (Phase C scope):
- declaration creates a flow='crush' lead with a routed coach (§5/§7)
- gender-independent per-event counter, free AND Connect members (O9),
  including the same-requester race (§7/§13)
- directional duplicates: reciprocal declarations create independent leads,
  legacy mutual auto-accept/auto-share branch neutralized (§5/§7)
- one-pair-one-flow redirect enforced in BOTH write endpoints (§9.1, O7),
  with My Crush as fallback and removal pairs getting neither flow
- recipient privacy: no notification, no hint, no inbox row, no badge
  change, response endpoint closed, messaging locked, export suppression
- O10 coach navbar rename ("My Crush" -> "My Dating Profile", both navs)

Run with: pytest crush_lu/tests/test_crush_member_flow.py -v
"""
import json
import threading

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.db import connection as db_connection
from django.test import Client, RequestFactory
from django.urls import reverse

from crush_lu.models import (
    ConfirmedEncounter,
    CrushCoach,
    EventConnection,
    Notification,
)
from crush_lu.tests.test_event_lobby import (
    _attend,
    _end_event,
    _join,
    _login,
    _make_event,
    _make_member,
)

User = get_user_model()

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]


@pytest.fixture(autouse=True)
def lobby_flags(settings):
    """Lobby flag + Connect launch phase on (redirect-path tests need it)."""
    settings.CRUSH_EVENT_LOBBY_ENABLED = True
    settings.CRUSH_CONNECT_LAUNCHED = True
    settings.AZURE_ACCOUNT_NAME = ""


@pytest.fixture(autouse=True)
def _clear_ratelimit_cache():
    cache.clear()
    yield


def _make_coach(username="coach@example.com"):
    user = User.objects.create_user(
        username=username,
        email=username,
        password="coachpass123",
        first_name="Coachy",
    )
    return CrushCoach.objects.create(user=user, bio="Test coach", is_active=True)


def _declare_url(event, user):
    return reverse(
        "crush_lu:request_connection",
        kwargs={"event_id": event.pk, "user_id": user.pk},
    )


def _inline_url(event, user):
    return reverse(
        "crush_lu:request_connection_inline",
        kwargs={"event_id": event.pk, "user_id": user.pk},
    )


def _lobby_url(event):
    return reverse("crush_lu:event_lobby", kwargs={"event_id": event.pk})


def _ended_event(**kwargs):
    """An event whose scheduled end (and lobby recap opening) is 1h ago."""
    return _end_event(_make_event(**kwargs))


def _plain(username, **kwargs):
    """A free (non-Connect) member."""
    kwargs.setdefault("membership", False)
    kwargs.setdefault("luxid", False)
    return _make_member(username, **kwargs)


class TestCrushDeclaration:
    """§5/§7: declarations create flow='crush' coach leads — privately."""

    def test_declaration_creates_crush_lead_with_routed_coach(self, client):
        coach = _make_coach()
        crusher = _plain("dec_a", gender="M")
        target = _plain("dec_b", gender="F")
        event = _ended_event()
        _attend(crusher, event)
        _attend(target, event)
        _login(client, crusher)

        response = client.post(_declare_url(event, target), {"note": "great chat"})

        assert response.status_code == 302
        assert response.url == reverse(
            "crush_lu:event_attendees", kwargs={"event_id": event.pk}
        )
        lead = EventConnection.objects.get(
            requester=crusher, recipient=target, event=event
        )
        assert lead.flow == EventConnection.FLOW_CRUSH
        assert lead.status == "pending"
        assert lead.requester_note == "great chat"
        # Routed via the Phase B tiers (no assigned/event coach -> active pool).
        assert lead.assigned_coach == coach
        assert lead.call_by is not None

    def test_declaration_never_notifies_recipient(self, client):
        _make_coach()
        crusher = _plain("dec_c", gender="M")
        target = _plain("dec_d", gender="F")
        event = _ended_event()
        _attend(crusher, event)
        _attend(target, event)
        _login(client, crusher)

        client.post(_declare_url(event, target), {"note": ""})

        assert not Notification.objects.filter(user=target).exists()
        assert not Notification.objects.filter(user=crusher).exists()
        assert len(mail.outbox) == 0

    def test_reciprocal_declarations_create_independent_leads(self, client):
        """A declares A->B; B's independent B->A succeeds silently. The first
        lead is never touched (§5: no auto-accept, no notification)."""
        coach = _make_coach()
        user_a = _plain("rec_a", gender="M")
        user_b = _plain("rec_b", gender="F")
        event = _ended_event()
        _attend(user_a, event)
        _attend(user_b, event)

        _login(client, user_a)
        client.post(_declare_url(event, user_b), {"note": ""})
        lead_ab = EventConnection.objects.get(requester=user_a, recipient=user_b)

        _login(client, user_b)
        response = client.post(_declare_url(event, user_a), {"note": ""})

        assert response.status_code == 302
        lead_ba = EventConnection.objects.get(requester=user_b, recipient=user_a)
        assert lead_ba.flow == EventConnection.FLOW_CRUSH
        assert lead_ba.status == "pending"
        # Independence: the reverse lead is byte-for-byte untouched.
        lead_ab.refresh_from_db()
        assert lead_ab.status == "pending"
        assert lead_ab.assigned_coach == coach
        for lead in (lead_ab, lead_ba):
            assert lead.requester_consents_to_share is False
            assert lead.recipient_consents_to_share is False
            assert lead.shared_at is None
        assert not Notification.objects.exists()

    def test_reciprocal_same_gender_never_auto_shares(self, client):
        """The legacy same-gender auto-share branch is dead for crush rows."""
        _make_coach()
        user_a = _plain("sg_a", gender="F")
        user_b = _plain("sg_b", gender="F")
        event = _ended_event()
        _attend(user_a, event)
        _attend(user_b, event)

        _login(client, user_a)
        client.post(_declare_url(event, user_b), {"note": ""})
        _login(client, user_b)
        client.post(_declare_url(event, user_a), {"note": ""})

        leads = EventConnection.objects.filter(event=event)
        assert leads.count() == 2
        for lead in leads:
            assert lead.status == "pending"
            assert lead.shared_at is None
            assert lead.requester_consents_to_share is False
        assert not Notification.objects.exists()

    def test_same_direction_duplicate_rejected(self, client):
        _make_coach()
        crusher = _plain("dup_a", gender="M")
        target = _plain("dup_b", gender="F")
        event = _ended_event()
        _attend(crusher, event)
        _attend(target, event)
        _login(client, crusher)

        client.post(_declare_url(event, target), {"note": ""})
        response = client.post(_declare_url(event, target), {"note": ""})

        assert response.status_code == 302
        assert (
            EventConnection.objects.filter(
                requester=crusher, recipient=target, event=event
            ).count()
            == 1
        )

    def test_reverse_declaration_response_byte_identical(self, client):
        """B's first declaration response is identical whether or not A has
        already privately declared — the reverse row's existence never leaks."""
        _make_coach()
        # Scenario 1: no prior row.
        a1 = _plain("bi_a1", gender="M")
        b1 = _plain("bi_b1", gender="F")
        event1 = _ended_event()
        _attend(a1, event1)
        _attend(b1, event1)
        _login(client, b1)
        r1 = client.post(_inline_url(event1, a1), {"note": ""})

        # Scenario 2: A already declared (direct ORM row, as the endpoint would).
        a2 = _plain("bi_a2", gender="M")
        b2 = _plain("bi_b2", gender="F")
        event2 = _ended_event()
        _attend(a2, event2)
        _attend(b2, event2)
        EventConnection.objects.create(
            requester=a2,
            recipient=b2,
            event=event2,
            flow=EventConnection.FLOW_CRUSH,
        )
        _login(client, b2)
        r2 = client.post(_inline_url(event2, a2), {"note": ""})

        assert r1.status_code == r2.status_code == 200
        # Normalize only the per-user element id — everything else must be
        # byte-identical whether or not the reverse row exists.
        normalized1 = r1.content.replace(
            b"connection-actions-" + str(a1.pk).encode(), b"connection-actions-UID"
        )
        normalized2 = r2.content.replace(
            b"connection-actions-" + str(a2.pk).encode(), b"connection-actions-UID"
        )
        assert normalized1 == normalized2


class TestCrushLimit:
    """O9: 1 crush per event per member, gender-independent, all tiers."""

    def test_second_crush_blocked_for_free_member(self, client):
        _make_coach()
        crusher = _plain("lim_a", gender="M")
        target1 = _plain("lim_b", gender="F")
        target2 = _plain("lim_c", gender="F")
        event = _ended_event()
        for user in (crusher, target1, target2):
            _attend(user, event)
        _login(client, crusher)

        client.post(_declare_url(event, target1), {"note": ""})
        response = client.post(_declare_url(event, target2), {"note": ""})

        assert response.status_code == 302
        assert EventConnection.objects.filter(requester=crusher, event=event).count() == 1

    def test_second_crush_blocked_for_connect_member(self, client):
        """No unlimited tier — Connect members get exactly 1 crush too (O9)."""
        _make_coach()
        crusher = _make_member("lim_d", gender="M")  # full Connect member
        target1 = _plain("lim_e", gender="F")
        target2 = _plain("lim_f", gender="F")
        event = _ended_event()
        for user in (crusher, target1, target2):
            _attend(user, event)
        _login(client, crusher)

        r1 = client.post(_inline_url(event, target1), {"note": ""})
        assert r1.status_code == 200
        r2 = client.post(_inline_url(event, target2), {"note": ""})

        assert b"already declared your crush for this event" in r2.content
        assert EventConnection.objects.filter(requester=crusher, event=event).count() == 1

    def test_same_gender_crush_counts_against_limit(self, client):
        """The legacy cross-gender counter is NOT reused: a same-gender crush
        consumes the one per-event slot exactly like a cross-gender one."""
        _make_coach()
        crusher = _plain("lim_g", gender="F")
        target1 = _plain("lim_h", gender="F")  # same gender
        target2 = _plain("lim_i", gender="M")
        event = _ended_event()
        for user in (crusher, target1, target2):
            _attend(user, event)
        _login(client, crusher)

        client.post(_declare_url(event, target1), {"note": ""})
        client.post(_declare_url(event, target2), {"note": ""})

        assert EventConnection.objects.filter(requester=crusher, event=event).count() == 1

    def test_limit_is_per_event(self, client):
        _make_coach()
        crusher = _plain("lim_j", gender="M")
        target = _plain("lim_k", gender="F")
        event1 = _ended_event()
        event2 = _ended_event()
        for event in (event1, event2):
            _attend(crusher, event)
            _attend(target, event)
        _login(client, crusher)

        client.post(_declare_url(event1, target), {"note": ""})
        response = client.post(_declare_url(event2, target), {"note": ""})

        assert response.status_code == 302
        assert EventConnection.objects.filter(requester=crusher).count() == 2

    @pytest.mark.skipif(
        not db_connection.features.has_select_for_update,
        reason="row locking requires a backend with SELECT ... FOR UPDATE",
    )
    @pytest.mark.django_db(transaction=True)
    def test_concurrent_declarations_never_exceed_limit(self):
        """§7/§13: two simultaneous same-requester declarations to different
        recipients — the per-(requester, event) lock serializes the count."""
        _make_coach()
        crusher = _plain("race_a", gender="M")
        target1 = _plain("race_b", gender="F")
        target2 = _plain("race_c", gender="F")
        event = _ended_event()
        for user in (crusher, target1, target2):
            _attend(user, event)

        barrier = threading.Barrier(2)
        results = []

        def post(target):
            client = Client()
            client.force_login(crusher)
            barrier.wait(timeout=10)
            results.append(client.post(_declare_url(event, target), {"note": ""}))

        threads = [
            threading.Thread(target=post, args=(target1,)),
            threading.Thread(target=post, args=(target2,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert len(results) == 2
        assert (
            EventConnection.objects.filter(requester=crusher, event=event).count() == 1
        )


class TestOnePairOneFlow:
    """§9.1/O7: recap-eligible Connect pairs redirect to the recap — enforced
    in BOTH write endpoints, not just the button."""

    def _eligible_pair(self):
        user_a = _make_member("pf_a", gender="M")
        user_b = _make_member("pf_b", gender="F")
        event = _make_event()  # still live: participation requires pre-end join
        _join(user_a, event)
        _join(user_b, event)
        _end_event(event)  # recap phase now open
        return user_a, user_b, event

    def test_direct_post_full_endpoint_redirects_eligible_pair(self, client):
        user_a, user_b, event = self._eligible_pair()
        _login(client, user_a)

        response = client.post(_declare_url(event, user_b), {"note": "hi"})

        assert response.status_code == 302
        assert response.url == _lobby_url(event)
        assert not EventConnection.objects.filter(event=event).exists()

    def test_direct_post_inline_endpoint_redirects_eligible_pair(self, client):
        user_a, user_b, event = self._eligible_pair()
        _login(client, user_a)

        response = client.post(_inline_url(event, user_b), {"note": "hi"})

        assert response.status_code == 200
        assert response["HX-Redirect"] == _lobby_url(event)
        assert not EventConnection.objects.filter(event=event).exists()

    def test_attendees_page_shows_recap_cta_not_crush_button(self, client):
        user_a, user_b, event = self._eligible_pair()
        _login(client, user_a)

        response = client.get(
            reverse("crush_lu:event_attendees", kwargs={"event_id": event.pk})
        )

        assert response.status_code == 200
        assert b"Find them in your event recap" in response.content
        # No My Crush! button for this pair (one pair, one flow).
        assert f"/events/{event.pk}/connect-inline/{user_b.pk}/".encode() not in response.content

    def test_fallback_when_target_lost_eligibility(self, client):
        """Participation rows outlive eligibility: a target who dropped out of
        the read-time roster gets My Crush, not a dead-end recap redirect."""
        user_a, user_b, event = self._eligible_pair()
        membership = user_b.crush_connect_membership
        membership.excluded_by_coach = True
        membership.save(update_fields=["excluded_by_coach"])
        _login(client, user_a)

        response = client.post(_declare_url(event, user_b), {"note": ""})

        assert response.status_code == 302
        assert response.url != _lobby_url(event)
        assert EventConnection.objects.filter(
            requester=user_a, recipient=user_b, event=event
        ).exists()

    def test_fallback_when_recap_closed_but_connection_window_open(self, client):
        """connection_window_hours can exceed the fixed 48h recap: the pair is
        still eligible but the recap is closed — My Crush applies."""
        user_a = _make_member("pf_c", gender="M")
        user_b = _make_member("pf_d", gender="F")
        event = _make_event()
        _join(user_a, event)
        _join(user_b, event)
        _end_event(event, hours_ago=49)  # recap closed
        event.connection_window_hours = 168  # crush window still open
        event.save(update_fields=["connection_window_hours"])
        _login(client, user_a)

        response = client.post(_declare_url(event, user_b), {"note": ""})

        assert response.status_code == 302
        assert response.url != _lobby_url(event)
        assert EventConnection.objects.filter(
            requester=user_a, recipient=user_b, event=event
        ).exists()

    def test_fallback_when_lobby_flag_off(self, client, settings):
        settings.CRUSH_EVENT_LOBBY_ENABLED = False
        user_a = _make_member("pf_e", gender="M")
        user_b = _make_member("pf_f", gender="F")
        event = _ended_event()
        _attend(user_a, event)
        _attend(user_b, event)
        _login(client, user_a)

        response = client.post(_declare_url(event, user_b), {"note": ""})

        assert response.status_code == 302
        assert response.url != _lobby_url(event)
        assert EventConnection.objects.filter(
            requester=user_a, recipient=user_b, event=event
        ).exists()

    def test_removal_pair_gets_neither_flow(self, client):
        """A pair hidden by a pending/approved encounter removal: the target
        is absent from the attendee list and a direct POST is rejected
        without a recap redirect and without creating a row (§9.1)."""
        user_a, user_b, event = self._eligible_pair()
        low, high = ConfirmedEncounter.canonical_pair(user_a, user_b)
        ConfirmedEncounter.objects.create(
            user_low=low,
            user_high=high,
            status="removal_pending",
        )
        _login(client, user_a)

        # Absent from the attendee list (block semantics).
        response = client.get(
            reverse("crush_lu:event_attendees", kwargs={"event_id": event.pk})
        )
        assert user_b.crushprofile.display_name.encode() not in response.content

        # Direct POST rejected — no row, no recap redirect, reason not disclosed.
        response = client.post(_declare_url(event, user_b), {"note": ""})
        assert response.status_code == 302
        assert response.url != _lobby_url(event)
        assert not EventConnection.objects.filter(event=event).exists()

        response = client.post(_inline_url(event, user_b), {"note": ""})
        assert "HX-Redirect" not in response
        assert not EventConnection.objects.filter(event=event).exists()


class TestRecipientPrivacy:
    """§5/§13: a crush is private until the coach-facilitated introduction."""

    def _declared(self):
        _make_coach()
        crusher = _plain("pr_a", gender="M")
        target = _plain("pr_b", gender="F")
        event = _ended_event()
        _attend(crusher, event)
        _attend(target, event)
        lead = EventConnection.objects.create(
            requester=crusher,
            recipient=target,
            event=event,
            flow=EventConnection.FLOW_CRUSH,
        )
        return crusher, target, event, lead

    def test_attendee_hint_suppressed_for_crush_recipient(self, client):
        _crusher, target, event, _lead = self._declared()
        _login(client, target)

        response = client.get(
            reverse("crush_lu:event_attendees", kwargs={"event_id": event.pk})
        )

        assert response.status_code == 200
        assert response.context["incoming_pending_count"] == 0
        assert b"Someone here wants to connect with you" not in response.content
        # No Accept/Decline card either.
        assert b"respond_connection" not in response.content

    def test_recipient_inbox_shows_nothing(self, client):
        crusher, target, _event, _lead = self._declared()
        _login(client, target)

        response = client.get(reverse("crush_lu:my_connections"))

        assert response.status_code == 200
        assert b"Requests You've Received" not in response.content
        assert crusher.crushprofile.display_name.encode() not in response.content

    def test_recipient_connection_detail_is_404(self, client):
        _crusher, target, _event, lead = self._declared()
        _login(client, target)

        response = client.get(
            reverse("crush_lu:connection_detail", kwargs={"connection_id": lead.pk})
        )

        assert response.status_code == 404

    def test_badge_counters_unchanged_in_every_pre_shared_status(self):
        """pending -> coach_reviewing -> coach_approved -> recipient consent
        recorded: the recipient's context-processor counters never move."""
        from crush_lu.context_processors import crush_user_context

        _crusher, target, _event, lead = self._declared()
        factory = RequestFactory()

        def counters():
            request = factory.get("/")
            request.user = target
            context = crush_user_context(request)
            return context["pending_requests_count"], context["connection_count"]

        assert counters() == (0, 0)
        lead.status = "coach_reviewing"
        lead.save(update_fields=["status"])
        assert counters() == (0, 0)
        lead.status = "coach_approved"
        lead.recipient_consents_to_share = True  # recorded by the coach
        lead.save(update_fields=["status", "recipient_consents_to_share"])
        assert counters() == (0, 0)

    def test_respond_connection_closed_for_crush_rows(self, client):
        """Direct POSTs to accept/decline a crush lead no-op neutrally."""
        _crusher, target, _event, lead = self._declared()
        _login(client, target)

        for action in ("accept", "decline"):
            response = client.post(
                reverse(
                    "crush_lu:respond_connection",
                    kwargs={"connection_id": lead.pk, "action": action},
                )
            )
            assert response.status_code == 302
            lead.refresh_from_db()
            assert lead.status == "pending"
            assert lead.requester_consents_to_share is False
            assert lead.recipient_consents_to_share is False
        assert not Notification.objects.exists()

    def test_messaging_locked_for_crush_leads(self, client):
        crusher, _target, _event, lead = self._declared()
        _login(client, crusher)

        # Message creation rejected server-side.
        response = client.post(
            reverse("crush_lu:connection_detail", kwargs={"connection_id": lead.pk}),
            {"message": "hello?"},
        )
        assert response.status_code == 302
        assert lead.messages.count() == 0

        # Polling endpoint stops (byte-identical to a dead connection).
        response = client.get(
            reverse("crush_lu:connection_messages", kwargs={"connection_id": lead.pk})
        )
        assert response.status_code == 286
        assert response.content == b""

    def test_requester_sees_neutral_state_even_after_decline(self, client):
        """The crusher's detail page renders the neutral "with your coach"
        state regardless of the actual lead status."""
        crusher, _target, _event, lead = self._declared()
        _login(client, crusher)
        url = reverse("crush_lu:connection_detail", kwargs={"connection_id": lead.pk})

        response = client.get(url)
        assert response.status_code == 200
        assert b"with your coach" in response.content
        assert b"Coach Facilitation" not in response.content

        lead.status = "declined"  # coach-recorded silent decline
        lead.save(update_fields=["status"])
        response = client.get(url)
        assert response.status_code == 200
        assert b"with your coach" in response.content
        assert b"Declined" not in response.content
        assert b"Coach Facilitation" not in response.content

    def test_connection_actions_byte_identical_no_connection(self, client):
        """The recipient's connection_actions endpoint yields the
        no-connection representation for a pre-shared incoming crush."""
        crusher, target, event, _lead = self._declared()
        _login(client, target)
        response = client.get(
            reverse(
                "crush_lu:connection_actions",
                kwargs={"event_id": event.pk, "user_id": crusher.pk},
            )
        )
        assert response.status_code == 200
        assert b"Accept" not in response.content
        assert b"Decline" not in response.content

    def test_export_suppresses_incoming_and_normalizes_outgoing(self):
        """GDPR export: the recipient's export has no trace of the row; the
        crusher's export keeps their own declaration with a neutral status."""
        from crush_lu.views_account import export_user_data

        crusher, target, _event, lead = self._declared()
        factory = RequestFactory()

        request = factory.get("/")
        request.user = target
        data = json.loads(export_user_data(request).content)
        for row in data.get("connections", []):
            assert row["connected_with"] != crusher.email

        # Even a declined or in-progress lead exports neutrally for the crusher.
        for status in ("declined", "coach_reviewing"):
            lead.status = status
            lead.save(update_fields=["status"])
            request = factory.get("/")
            request.user = crusher
            data = json.loads(export_user_data(request).content)
            rows = [
                r for r in data.get("connections", [])
                if r["connected_with"] == target.email
            ]
            assert len(rows) == 1
            assert rows[0]["status"] == "with your coach"

    def test_my_events_mutual_count_hides_reciprocal_crush(self, client):
        """After reciprocal declarations, both members' my_events mutual-match
        counts stay 0 until `shared` (no private-reciprocal confirmation)."""
        _make_coach()
        user_a = _plain("me_a", gender="M")
        user_b = _plain("me_b", gender="F")
        event = _ended_event()
        _attend(user_a, event)
        _attend(user_b, event)
        for requester, recipient in ((user_a, user_b), (user_b, user_a)):
            EventConnection.objects.create(
                requester=requester,
                recipient=recipient,
                event=event,
                flow=EventConnection.FLOW_CRUSH,
            )

        for member in (user_a, user_b):
            _login(client, member)
            response = client.get(reverse("crush_lu:my_events"))
            assert response.status_code == 200
            entries = {
                entry["event"].pk: entry
                for entry in response.context["past_registrations"]
            }
            assert entries[event.pk]["mutual_matches"] == 0

    def test_recap_email_excludes_crush_rows(self):
        """A recipient whose only received rows are crush leads gets no
        'waiting to hear back' section; reciprocal crushes produce no
        'two-way interest' line."""
        from crush_lu.email_helpers import send_event_recap
        from crush_lu.models import EventRegistration

        _make_coach()
        crusher = _plain("re_a", gender="M")
        target = _plain("re_b", gender="F")
        event = _ended_event()
        _attend(crusher, event)
        _attend(target, event)
        for requester, recipient in ((crusher, target), (target, crusher)):
            EventConnection.objects.create(
                requester=requester,
                recipient=recipient,
                event=event,
                flow=EventConnection.FLOW_CRUSH,
            )

        registration = EventRegistration.objects.get(event=event, user=target)
        sent = send_event_recap(registration)
        assert sent == 1
        body = mail.outbox[-1].body
        assert "waiting to hear back" not in body
        assert "two-way interest" not in body


class TestCoachNavRename:
    """O10: coach navbar dropdown renamed in BOTH nav locations; the member
    feature keeps the "My Crush!" name."""

    def test_my_dating_profile_in_desktop_and_mobile_nav(self, client):
        coach = _make_coach("navcoach@example.com")
        _login(client, coach.user)

        response = client.get(reverse("crush_lu:my_connections"))

        assert response.status_code == 200
        assert response.content.count(b"My Dating Profile") >= 2
        assert b"<span>My Crush</span>" not in response.content
