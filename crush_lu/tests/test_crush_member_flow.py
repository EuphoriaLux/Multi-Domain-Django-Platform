"""
"My Crush!" Phase C — member declaration flow tests.

Spec: ai-memory-hub/specs/2026-07-21-crush-my-crush-post-event-flow.md

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
    UserDataConsent,
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


def _grant_consent(user):
    """Grant Crush.lu consent without logging in.

    The signal creates a ``UserDataConsent`` row with ``crushlu_consent_given``
    still False, and the declaration endpoints are not exempt from
    ``CrushConsentMiddleware`` — so a bare ``force_login`` is redirected to the
    consent page and writes nothing. ``_login`` does this for the ordinary
    tests; a test that builds its own ``Client`` has to do it itself.
    """
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )


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


def _declare(event, requester, target, note=""):
    """Create a "My Crush!" lead the way the retired endpoint did."""
    from crush_lu.models import EventRegistration
    from crush_lu.services.crush_leads import declare_crush

    return declare_crush(
        requester=requester,
        recipient=target,
        event=event,
        requester_registration=EventRegistration.objects.get(
            event=event, user=requester
        ),
        note=note,
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


# The declaration endpoint, its per-event limit and the one-pair-one-flow
# redirect were retired when post-event swiping replaced "My Crush!"
# (decision 2026-11-20); see test_event_swipes.py. Existing crush leads keep
# their coach workflow, which the classes below still cover — their leads are
# created through the ``declare_crush`` service directly.


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
            rows = data.get("connections", [])
            assert len(rows) == 1
            assert rows[0]["status"] == "with your coach"
            # The target never shared their email with the crusher.
            assert rows[0]["connected_with"] is None

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
        assert b"<span>Connect</span>" in response.content
        assert b'aria-label="Crush Connect"' in response.content
        assert b"<span>My Crush</span>" not in response.content


class TestCoachLeadPrivacy:
    """The `requester_note` is promised to the routed Crush Coach alone, and
    Phase C is what first puts member-written notes on those coach surfaces."""

    def _routed_lead(self, coach, note="I could not stop smiling."):
        """A declared crush lead deterministically routed to ``coach``."""
        crusher = _plain("clp_a", gender="M")
        target = _plain("clp_b", gender="F")
        crusher.crushprofile.assigned_coach = coach
        crusher.crushprofile.save(update_fields=["assigned_coach"])
        event = _ended_event()
        _attend(crusher, event)
        _attend(target, event)
        lead = _declare(event, crusher, target, note=note)
        assert lead.assigned_coach == coach
        return lead

    def test_non_routed_coach_gets_404_on_review_get(self, client):
        """Authorization applies to GET, not just POST — and the 404 must not
        disclose that the lead exists."""
        routed = _make_coach("routed2@example.com")
        other = _make_coach("other2@example.com")
        lead = self._routed_lead(routed)
        _login(client, other.user)

        response = client.get(
            reverse(
                "crush_lu:coach_connection_review",
                kwargs={"connection_id": lead.pk},
            )
        )

        assert response.status_code == 404

    def test_routed_coach_still_sees_the_lead_and_its_note(self, client):
        routed = _make_coach("routed3@example.com")
        lead = self._routed_lead(routed)
        _login(client, routed.user)

        review = client.get(
            reverse(
                "crush_lu:coach_connection_review",
                kwargs={"connection_id": lead.pk},
            )
        )

        assert review.status_code == 200
        assert b"I could not stop smiling." in review.content

    def test_pending_lead_reaches_the_routed_coach_inbox(self, client):
        """The member is promised a call within 48h, so a `pending` lead has
        to appear in the coach action queue with its `call_by` deadline."""
        routed = _make_coach("routed4@example.com")
        lead = self._routed_lead(routed)
        assert lead.status == "pending"
        _login(client, routed.user)

        response = client.get(reverse("crush_lu:coach_action_queue"))

        assert response.status_code == 200
        assert response.context["counts"]["crush_lead"] == 1
        entries = [i for i in response.context["items"] if i["kind"] == "crush_lead"]
        assert len(entries) == 1
        assert entries[0]["deadline"] == lead.call_by
        assert entries[0]["url_kwargs"] == {"connection_id": lead.pk}

    def test_other_coach_inbox_stays_empty(self, client):
        routed = _make_coach("routed5@example.com")
        other = _make_coach("other5@example.com")
        self._routed_lead(routed)
        _login(client, other.user)

        response = client.get(reverse("crush_lu:coach_action_queue"))

        assert response.status_code == 200
        assert response.context["counts"]["crush_lead"] == 0

    def test_lead_is_queued_once_not_twice(self, client):
        """The legacy connection branch excludes crush rows, so a lead that
        reaches `coach_reviewing` must not appear under both kinds."""
        routed = _make_coach("routed6@example.com")
        lead = self._routed_lead(routed)
        lead.status = "coach_reviewing"
        lead.save(update_fields=["status"])
        _login(client, routed.user)

        response = client.get(reverse("crush_lu:coach_action_queue"))

        items = response.context["items"]
        assert [i["kind"] for i in items].count("crush_lead") == 1
        assert [i["kind"] for i in items].count("connection") == 0

    def test_note_stays_coach_only_for_both_members_after_shared(self, client):
        """The promise was "never your crush" — it outlives the introduction,
        so the note stays redacted on the member detail page at `shared`."""
        routed = _make_coach("routed7@example.com")
        lead = self._routed_lead(routed)
        lead.status = "shared"
        lead.save(update_fields=["status"])
        url = reverse("crush_lu:connection_detail", kwargs={"connection_id": lead.pk})

        for member in (lead.requester, lead.recipient):
            member_client = Client()
            _login(member_client, member)
            response = member_client.get(url)
            assert response.status_code == 200
            assert b"I could not stop smiling." not in response.content

    def test_unclaimed_pool_lead_withholds_the_note_on_both_surfaces(self, client):
        """Triage-before-claim: an unrouted lead stays listed and openable so
        any coach can claim it, but its note opens only once owned — the list
        and the detail page must agree."""
        triager = _make_coach("triager@example.com")
        crusher = _plain("clp_pool_a", gender="M")
        target = _plain("clp_pool_b", gender="F")
        event = _ended_event()
        _attend(crusher, event)
        _attend(target, event)
        lead = EventConnection.objects.create(
            requester=crusher,
            recipient=target,
            event=event,
            flow=EventConnection.FLOW_CRUSH,
            requester_note="Pool lead note.",
        )
        assert lead.assigned_coach is None
        _login(client, triager.user)

        review = client.get(
            reverse(
                "crush_lu:coach_connection_review",
                kwargs={"connection_id": lead.pk},
            )
        )

        # Listed and openable so it can be claimed...
        assert review.status_code == 200
        # ...but the note itself stays shut.
        assert b"Pool lead note." not in review.content
        assert b"Claim this lead to read the note." in review.content

    def test_claiming_coach_then_reads_the_pool_lead_note(self, client):
        """The withholding is claim-gated, not permanent."""
        claimer = _make_coach("claimer@example.com")
        crusher = _plain("clp_clm_a", gender="M")
        target = _plain("clp_clm_b", gender="F")
        event = _ended_event()
        _attend(crusher, event)
        _attend(target, event)
        lead = EventConnection.objects.create(
            requester=crusher,
            recipient=target,
            event=event,
            flow=EventConnection.FLOW_CRUSH,
            requester_note="Pool lead note.",
        )
        lead.assigned_coach = claimer
        lead.save(update_fields=["assigned_coach"])
        _login(client, claimer.user)

        review = client.get(
            reverse(
                "crush_lu:coach_connection_review",
                kwargs={"connection_id": lead.pk},
            )
        )

        assert review.status_code == 200
        assert b"Pool lead note." in review.content


class TestCodexRound2:
    """Second Codex round on this PR."""

    # --- P1: `shared` must not open the note to every coach ---

    def test_shared_crush_note_stays_with_the_routed_coach(self, client):
        """The note was written under "only your Crush Coach will read this".
        Completing the introduction changes who may see the pair, not who may
        read the note."""
        routed = _make_coach("r2_routed@example.com")
        stranger = _make_coach("r2_stranger@example.com")
        crusher = _plain("r2_ca", gender="M")
        target = _plain("r2_cb", gender="F")
        event = _ended_event()
        _attend(crusher, event)
        _attend(target, event)
        lead = EventConnection.objects.create(
            requester=crusher,
            recipient=target,
            event=event,
            flow=EventConnection.FLOW_CRUSH,
            requester_note="Shared-state note.",
            assigned_coach=routed,
            status="shared",
        )
        _login(client, stranger.user)

        review = client.get(
            reverse(
                "crush_lu:coach_connection_review",
                kwargs={"connection_id": lead.pk},
            )
        )

        # The completed introduction is still visible as a row...
        assert review.status_code == 200
        # ...but the note is not.
        assert b"Shared-state note." not in review.content

    # --- P1: member overview discloses the pair ---

    def test_member_overview_hides_the_pair_from_an_unrelated_coach(self, client):
        routed = _make_coach("r2_mo_routed@example.com")
        stranger = _make_coach("r2_mo_stranger@example.com")
        crusher = _plain("r2_moa", gender="M")
        target = _plain("r2_mob", gender="F")
        event = _ended_event()
        _attend(crusher, event)
        _attend(target, event)
        EventConnection.objects.create(
            requester=crusher,
            recipient=target,
            event=event,
            flow=EventConnection.FLOW_CRUSH,
            assigned_coach=routed,
        )
        _login(client, stranger.user)

        response = client.get(
            reverse("crush_lu:coach_member_overview", kwargs={"user_id": target.pk})
        )

        assert response.status_code == 200
        assert crusher.username.encode() not in response.content

    # --- P1: reciprocal leads must stay independent ---

    def test_approve_does_not_hijack_the_other_coachs_lead(self, client):
        coach_a = _make_coach("r2_ind_a@example.com")
        coach_b = _make_coach("r2_ind_b@example.com")
        user_a = _plain("r2_ia", gender="M")
        user_b = _plain("r2_ib", gender="F")
        event = _ended_event()
        _attend(user_a, event)
        _attend(user_b, event)
        lead_ab = EventConnection.objects.create(
            requester=user_a,
            recipient=user_b,
            event=event,
            flow=EventConnection.FLOW_CRUSH,
            assigned_coach=coach_a,
            status="accepted",
        )
        lead_ba = EventConnection.objects.create(
            requester=user_b,
            recipient=user_a,
            event=event,
            flow=EventConnection.FLOW_CRUSH,
            assigned_coach=coach_b,
            status="accepted",
        )
        _login(client, coach_a.user)

        client.post(
            reverse(
                "crush_lu:coach_connection_review",
                kwargs={"connection_id": lead_ab.pk},
            ),
            {"action": "approve"},
        )

        lead_ab.refresh_from_db()
        lead_ba.refresh_from_db()
        assert lead_ab.status == "coach_approved"
        assert lead_ba.status == "accepted"
        assert lead_ba.assigned_coach == coach_b
        assert lead_ba.coach_approved_at is None

    # --- P1: recipient must not enumerate hidden crushes ---

    def test_recipient_polling_a_hidden_crush_gets_404(self, client):
        """A 286 here is a tell: an unrelated id 404s, so walking the ids
        would mark every hidden admirer. No rate limit on this GET."""
        crusher = _plain("r2_pa", gender="M")
        target = _plain("r2_pb", gender="F")
        event = _ended_event()
        _attend(crusher, event)
        _attend(target, event)
        lead = EventConnection.objects.create(
            requester=crusher,
            recipient=target,
            event=event,
            flow=EventConnection.FLOW_CRUSH,
        )
        _login(client, target)

        response = client.get(
            reverse(
                "crush_lu:connection_messages",
                kwargs={"connection_id": lead.pk},
            )
        )

        assert response.status_code == 404

    def test_requester_polling_their_own_crush_still_gets_286(self, client):
        """The requester's own poll must still stop cleanly."""
        crusher = _plain("r2_qa", gender="M")
        target = _plain("r2_qb", gender="F")
        event = _ended_event()
        _attend(crusher, event)
        _attend(target, event)
        lead = EventConnection.objects.create(
            requester=crusher,
            recipient=target,
            event=event,
            flow=EventConnection.FLOW_CRUSH,
        )
        _login(client, crusher)

        response = client.get(
            reverse(
                "crush_lu:connection_messages",
                kwargs={"connection_id": lead.pk},
            )
        )

        assert response.status_code == 286

    # --- P1: the export must not fingerprint a hidden row ---

    def test_export_omits_the_connections_key_entirely_for_a_hidden_only_row(
        self, client
    ):
        """An empty array where a no-connection member gets no key at all is
        itself the disclosure."""
        crusher = _plain("r2_ea", gender="M")
        target = _plain("r2_eb", gender="F")
        event = _ended_event()
        _attend(crusher, event)
        _attend(target, event)
        EventConnection.objects.create(
            requester=crusher,
            recipient=target,
            event=event,
            flow=EventConnection.FLOW_CRUSH,
        )
        _login(client, target)

        response = client.get(reverse("crush_lu:export_user_data"))
        payload = json.loads(response.content)

        assert "connections" not in payload

    def test_export_still_lists_a_visible_connection(self, client):
        requester = _plain("r2_va", gender="M")
        target = _plain("r2_vb", gender="F")
        event = _ended_event()
        _attend(requester, event)
        _attend(target, event)
        EventConnection.objects.create(
            requester=requester,
            recipient=target,
            event=event,
            flow=EventConnection.FLOW_LEGACY,
            status="accepted",
        )
        _login(client, target)

        payload = json.loads(client.get(reverse("crush_lu:export_user_data")).content)

        assert len(payload["connections"]) == 1

    # --- P2: mutual totals must exclude the forward crush row ---

    def test_an_unshared_crush_is_not_a_mutual_match_on_my_events(self, client):
        """`annotate_is_visible_mutual` screens only the reverse subquery, so
        a legacy reverse would otherwise flip the forward crush row."""
        user_a = _plain("r2_ma", gender="M")
        user_b = _plain("r2_mb", gender="F")
        event = _ended_event()
        _attend(user_a, event)
        _attend(user_b, event)
        EventConnection.objects.create(
            requester=user_a,
            recipient=user_b,
            event=event,
            flow=EventConnection.FLOW_CRUSH,
        )
        EventConnection.objects.create(
            requester=user_b,
            recipient=user_a,
            event=event,
            flow=EventConnection.FLOW_LEGACY,
            status="accepted",
        )
        _login(client, user_a)

        response = client.get(reverse("crush_lu:my_events"))

        assert response.status_code == 200
        cards = {
            c["event"].pk: c["mutual_matches"]
            for c in response.context["past_registrations"]
        }
        assert cards[event.pk] == 0

    # --- P2: the attendees hint no longer promises an instant match ---

    def test_the_interest_hint_does_not_promise_an_instant_match(self, client):
        viewer = _plain("r2_ha", gender="M")
        admirer = _plain("r2_hb", gender="F")
        event = _ended_event()
        _attend(viewer, event)
        _attend(admirer, event)
        EventConnection.objects.create(
            requester=admirer,
            recipient=viewer,
            event=event,
            flow=EventConnection.FLOW_LEGACY,
            status="pending",
        )
        _login(client, viewer)

        response = client.get(
            reverse("crush_lu:event_attendees", kwargs={"event_id": event.pk})
        )

        assert b"match instantly" not in response.content

    # --- P2: HTMX GET needs HX-Redirect too ---

    def test_an_htmx_get_into_a_recap_pair_uses_hx_redirect(self, client):
        """A plain 302 is followed by the XHR, and HTMX swaps the whole lobby
        document into the card instead of navigating."""
        user_a = _make_member("r2_xa", gender="M")
        user_b = _make_member("r2_xb", gender="F")
        event = _make_event()
        _join(user_a, event)
        _join(user_b, event)
        _end_event(event)
        _login(client, user_a)

        response = client.get(_inline_url(event, user_b), HTTP_HX_REQUEST="true")

        assert "HX-Redirect" in response
        assert response.status_code != 302


class TestClaimCannotTakeOverALead:
    """A `shared` crush lead is openable by any coach, and `claim` is exempt
    from the ownership check — so without a guard, clicking Claim hands over
    the note the routed coach was promised sole sight of."""

    def _shared_lead(self):
        routed = _make_coach("clm_routed@example.com")
        crusher = _plain("clm_a", gender="M")
        target = _plain("clm_b", gender="F")
        event = _ended_event()
        _attend(crusher, event)
        _attend(target, event)
        lead = EventConnection.objects.create(
            requester=crusher,
            recipient=target,
            event=event,
            flow=EventConnection.FLOW_CRUSH,
            requester_note="Takeover note.",
            assigned_coach=routed,
            status="shared",
        )
        return routed, lead

    def _url(self, lead):
        return reverse(
            "crush_lu:coach_connection_review", kwargs={"connection_id": lead.pk}
        )

    def test_claim_is_refused_on_an_assigned_lead(self, client):
        routed, lead = self._shared_lead()
        thief = _make_coach("clm_thief@example.com")
        _login(client, thief.user)

        client.post(self._url(lead), {"action": "claim"})

        lead.refresh_from_db()
        assert lead.assigned_coach == routed

    def test_a_refused_claim_does_not_expose_the_note(self, client):
        routed, lead = self._shared_lead()
        thief = _make_coach("clm_thief2@example.com")
        _login(client, thief.user)

        client.post(self._url(lead), {"action": "claim"})
        response = client.get(self._url(lead))

        assert b"Takeover note." not in response.content

    def test_an_unassigned_lead_can_still_be_claimed(self, client):
        """The guard must not break normal pool triage."""
        taker = _make_coach("clm_taker@example.com")
        crusher = _plain("clm_ua", gender="M")
        target = _plain("clm_ub", gender="F")
        event = _ended_event()
        _attend(crusher, event)
        _attend(target, event)
        lead = EventConnection.objects.create(
            requester=crusher,
            recipient=target,
            event=event,
            flow=EventConnection.FLOW_CRUSH,
        )
        _login(client, taker.user)

        client.post(self._url(lead), {"action": "claim"})

        lead.refresh_from_db()
        assert lead.assigned_coach == taker

    def test_a_legacy_connection_is_protected_too(self, client):
        """Pre-existing takeover, same guard — a coach should not silently
        reassign another coach's connection."""
        routed = _make_coach("clm_lroute@example.com")
        thief = _make_coach("clm_lthief@example.com")
        requester = _plain("clm_la", gender="M")
        target = _plain("clm_lb", gender="F")
        event = _ended_event()
        _attend(requester, event)
        _attend(target, event)
        legacy = EventConnection.objects.create(
            requester=requester,
            recipient=target,
            event=event,
            flow=EventConnection.FLOW_LEGACY,
            status="accepted",
            assigned_coach=routed,
        )
        _login(client, thief.user)

        client.post(self._url(legacy), {"action": "claim"})

        legacy.refresh_from_db()
        assert legacy.assigned_coach == routed
