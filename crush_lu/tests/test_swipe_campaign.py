"""
December 2026 past-event swipe campaign (decision 2026-11-20).

Covers: the campaign window opening old events, campaign-only deck rules
(opt-out, break, decided-elsewhere, already connected, newest shared event),
the hub page and its visibility switch, the Crush Connect chat for matches of
two Connect members, the message dispatcher, the dispatch endpoint, and the
command that closes the old "My Crush!" leads.
"""

from datetime import datetime, timedelta
from io import StringIO

import pytest
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import (
    EmailPreference,
    EventConnection,
    EventSwipe,
    PastEventDeckOptOut,
    SwipeCampaign,
    SwipeCampaignMessage,
    UserDataConsent,
)
from crush_lu.models.crush_connect_cycle import ConnectTemporaryChat
from crush_lu.services import event_swipes
from crush_lu.services import swipe_campaign_messages as dispatcher
from crush_lu.tests.test_event_lobby import (
    _attend,
    _end_event,
    _login,
    _make_event,
    _make_member,
)

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]


@pytest.fixture(autouse=True)
def _flags(settings):
    settings.AZURE_ACCOUNT_NAME = ""
    cache.clear()
    yield


def _member(username, gender="F", connect=False, **kwargs):
    kwargs.setdefault("membership", connect)
    kwargs.setdefault("luxid", connect)
    user = _make_member(username, gender=gender, **kwargs)
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    return user


def _old_event(days_ago=30):
    return _end_event(_make_event(), hours_ago=24 * days_ago)


def _campaign(active=True, **times):
    now = timezone.now()
    defaults = {
        "starts_at": now - timedelta(hours=1),
        "ends_at": now + timedelta(days=10),
    }
    defaults.update(times)
    return SwipeCampaign.objects.create(name="Test", is_active=active, **defaults)


def _deck_users(user, event):
    return {card["user"] for card in event_swipes.swipe_deck(user, event)}


@pytest.fixture
def old_room():
    """An event that ended 30 days ago: two women, two men."""
    event = _old_event()
    anna, bea = _member("anna", "F"), _member("bea", "F")
    carl, dan = _member("carl", "M"), _member("dan", "M")
    for user in (anna, bea, carl, dan):
        _attend(user, event)
    return {"event": event, "anna": anna, "bea": bea, "carl": carl, "dan": dan}


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------


class TestWindow:
    def test_old_event_is_closed_without_a_campaign(self, old_room):
        assert event_swipes.swipe_mode(old_room["event"]) == (None, None)
        with pytest.raises(event_swipes.SwipeNotAllowed) as exc:
            event_swipes.record_swipe(
                old_room["anna"], old_room["event"], old_room["carl"], liked=True
            )
        assert exc.value.code == "closed"

    def test_running_campaign_opens_old_events(self, old_room):
        campaign = _campaign()
        assert event_swipes.swipe_mode(old_room["event"]) == (
            event_swipes.MODE_CAMPAIGN,
            campaign,
        )
        assert event_swipes.swipe_deadline(old_room["event"]) == campaign.ends_at

    def test_switched_off_or_finished_campaign_keeps_them_closed(self, old_room):
        _campaign(active=False)
        _campaign(
            starts_at=timezone.now() - timedelta(days=5),
            ends_at=timezone.now() - timedelta(days=1),
        )
        assert not event_swipes.swipe_window_open(old_room["event"])

    def test_events_after_the_campaign_start_keep_their_own_window(self):
        _campaign(starts_at=timezone.now() - timedelta(days=3))
        fresh = _end_event(_make_event())
        assert event_swipes.swipe_mode(fresh) == (event_swipes.MODE_EVENT, None)

    def test_deck_page_opens_during_the_campaign(self, client, old_room):
        _login(client, old_room["anna"])
        url = reverse(
            "crush_lu:event_attendees", kwargs={"event_id": old_room["event"].pk}
        )
        assert client.get(url).status_code == 302
        _campaign()
        response = client.get(url)
        assert response.status_code == 200
        assert {c["user"] for c in response.context["deck"]} == {
            old_room["carl"],
            old_room["dan"],
        }


# ---------------------------------------------------------------------------
# Campaign deck rules
# ---------------------------------------------------------------------------


class TestCampaignDeck:
    def test_mutual_like_is_a_match_tagged_with_the_campaign(self, old_room):
        campaign = _campaign()
        event = old_room["event"]
        event_swipes.record_swipe(old_room["anna"], event, old_room["carl"], True)
        result = event_swipes.record_swipe(
            old_room["carl"], event, old_room["anna"], True
        )
        assert result["match"]
        connection = result["connection"]
        assert connection.flow == EventConnection.FLOW_SWIPE
        assert connection.status == "shared"
        entry = connection.system_actions[-1]
        assert entry["details"]["campaign"] == campaign.pk

    def test_opted_out_members_are_hidden_and_cannot_swipe(self, client, old_room):
        _campaign()
        PastEventDeckOptOut.objects.create(user=old_room["carl"])
        assert _deck_users(old_room["anna"], old_room["event"]) == {old_room["dan"]}
        with pytest.raises(event_swipes.SwipeNotAllowed) as exc:
            event_swipes.record_swipe(
                old_room["carl"], old_room["event"], old_room["anna"], True
            )
        assert exc.value.code == "hidden"
        _login(client, old_room["carl"])
        response = client.get(
            reverse(
                "crush_lu:event_attendees", kwargs={"event_id": old_room["event"].pk}
            )
        )
        assert response.url == reverse("crush_lu:past_event_swipes")

    def test_opt_out_does_not_touch_the_normal_window(self):
        _campaign(starts_at=timezone.now() - timedelta(days=3))
        fresh = _end_event(_make_event())
        anna, carl = _member("anna2", "F"), _member("carl2", "M")
        _attend(anna, fresh)
        _attend(carl, fresh)
        PastEventDeckOptOut.objects.create(user=carl)
        assert _deck_users(anna, fresh) == {carl}

    def test_members_on_a_break_are_hidden(self, old_room):
        _campaign()
        profile = old_room["carl"].crushprofile
        profile.on_break_at = timezone.now()
        profile.save(update_fields=["on_break_at"])
        assert _deck_users(old_room["anna"], old_room["event"]) == {old_room["dan"]}

    def test_a_person_met_twice_shows_only_at_the_newest_event(self, old_room):
        _campaign()
        newer = _old_event(days_ago=10)
        _attend(old_room["anna"], newer)
        _attend(old_room["carl"], newer)
        assert _deck_users(old_room["anna"], old_room["event"]) == {old_room["dan"]}
        assert _deck_users(old_room["anna"], newer) == {old_room["carl"]}

    def test_someone_decided_on_at_another_event_is_not_shown_again(self, old_room):
        _campaign()
        older = _old_event(days_ago=60)
        _attend(old_room["anna"], older)
        _attend(old_room["dan"], older)
        EventSwipe.objects.create(
            event=older, swiper=old_room["anna"], target=old_room["dan"], liked=False
        )
        assert _deck_users(old_room["anna"], old_room["event"]) == {old_room["carl"]}

    def test_already_connected_people_are_not_shown(self, old_room):
        _campaign()
        other = _old_event(days_ago=90)
        EventConnection.objects.create(
            requester=old_room["anna"],
            recipient=old_room["carl"],
            event=other,
            status="shared",
        )
        assert _deck_users(old_room["anna"], old_room["event"]) == {old_room["dan"]}

    def test_a_double_submit_still_answers_already_swiped(self, old_room):
        _campaign()
        event = old_room["event"]
        event_swipes.record_swipe(old_room["anna"], event, old_room["carl"], False)
        again = event_swipes.record_swipe(
            old_room["anna"], event, old_room["carl"], False
        )
        assert again["already_swiped"]


# ---------------------------------------------------------------------------
# Hub page
# ---------------------------------------------------------------------------


class TestHub:
    def test_lists_past_events_with_profiles_left(self, client, old_room):
        _campaign()
        _login(client, old_room["anna"])
        response = client.get(reverse("crush_lu:past_event_swipes"))
        assert response.status_code == 200
        rows = response.context["rows"]
        assert [r["event"] for r in rows] == [old_room["event"]]
        assert rows[0]["remaining"] == 2

    def test_without_a_campaign_it_says_so(self, client, old_room):
        _login(client, old_room["anna"])
        response = client.get(reverse("crush_lu:past_event_swipes"))
        assert response.context["campaign"] is None
        assert response.context["rows"] == []

    def test_visibility_switch(self, client, old_room):
        _login(client, old_room["anna"])
        url = reverse("crush_lu:past_event_swipes_visibility")
        client.post(url, {"visible": "0"})
        assert PastEventDeckOptOut.objects.filter(user=old_room["anna"]).exists()
        client.post(url, {"visible": "1"})
        assert not PastEventDeckOptOut.objects.filter(user=old_room["anna"]).exists()

    def test_dashboard_banner_helper(self, old_room):
        from crush_lu.views import _swipe_campaign_banner

        assert _swipe_campaign_banner(old_room["anna"]) is None
        campaign = _campaign()
        assert _swipe_campaign_banner(old_room["anna"]) == campaign
        PastEventDeckOptOut.objects.create(user=old_room["anna"])
        assert _swipe_campaign_banner(old_room["anna"]) is None


# ---------------------------------------------------------------------------
# Crush Connect chat
# ---------------------------------------------------------------------------


class TestConnectChat:
    def _match(self, a, b, event):
        event_swipes.record_swipe(a, event, b, True)
        return event_swipes.record_swipe(b, event, a, True)["connection"]

    def test_two_connect_members_chat_in_connect(self, client):
        _campaign()
        event = _old_event()
        eva = _member("eva", "F", connect=True)
        finn = _member("finn", "M", connect=True)
        _attend(eva, event)
        _attend(finn, event)

        _login(client, eva)
        event_swipes.record_swipe(finn, event, eva, True)
        response = client.post(
            reverse(
                "crush_lu:event_swipe",
                kwargs={"event_id": event.pk, "user_id": finn.pk},
            ),
            {"decision": "like"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        data = response.json()
        assert data["match"]
        chat = ConnectTemporaryChat.objects.get(event_connection__event=event)
        assert {chat.participant_1, chat.participant_2} == {eva, finn}
        assert chat.request is None
        assert data["chat_url"] == reverse(
            "crush_lu:connect_week_chat_detail", kwargs={"chat_id": chat.pk}
        )

        detail = client.get(
            reverse(
                "crush_lu:connection_detail",
                kwargs={"connection_id": chat.event_connection_id},
            )
        )
        assert detail.status_code == 302
        assert detail.url == reverse(
            "crush_lu:connect_week_chat_detail", kwargs={"chat_id": chat.pk}
        )

    def test_connect_chat_page_works_for_a_match_chat(self, client):
        event = _end_event(_make_event())
        eva = _member("eva3", "F", connect=True)
        finn = _member("finn3", "M", connect=True)
        _attend(eva, event)
        _attend(finn, event)
        connection = self._match(eva, finn, event)
        _login(client, eva)
        response = client.get(
            reverse(
                "crush_lu:connect_week_chat_detail",
                kwargs={"chat_id": connection.connect_chat.pk},
            )
        )
        assert response.status_code == 200

    def test_non_members_keep_the_normal_chat_and_see_an_invite(self, client, old_room):
        _campaign()
        connection = self._match(old_room["anna"], old_room["carl"], old_room["event"])
        assert not ConnectTemporaryChat.objects.filter(
            event_connection=connection
        ).exists()
        _login(client, old_room["anna"])
        response = client.get(
            reverse(
                "crush_lu:connection_detail", kwargs={"connection_id": connection.pk}
            )
        )
        assert response.status_code == 200
        assert response.context["connect_invite"] is True
        assert b"data-connect-invite" in response.content

    def test_one_member_only_is_not_enough(self, old_room):
        _campaign()
        event = old_room["event"]
        eva = _member("eva2", "F", connect=True)
        _attend(eva, event)
        connection = self._match(eva, old_room["carl"], event)
        assert not ConnectTemporaryChat.objects.filter(
            event_connection=connection
        ).exists()


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class TestDispatcher:
    def test_start_mails_every_attendee_once(self, old_room):
        _campaign()
        result = dispatcher.dispatch()
        assert result["sent"] == 4
        assert len(mail.outbox) == 4
        assert {m.to[0] for m in mail.outbox} == {
            f"{name}@example.com" for name in ("anna", "bea", "carl", "dan")
        }
        # No push devices: logged as skipped, never retried.
        assert (
            SwipeCampaignMessage.objects.filter(
                step="start", channel="push", status="skipped"
            ).count()
            == 4
        )

        again = dispatcher.dispatch()
        assert again["sent"] == 0
        assert len(mail.outbox) == 4

    def test_unsubscribed_and_opted_out_members_get_nothing(self, old_room):
        _campaign()
        prefs = EmailPreference.get_or_create_for_user(old_room["anna"])
        prefs.unsubscribed_all = True
        prefs.save()
        PastEventDeckOptOut.objects.create(user=old_room["bea"])
        dispatcher.dispatch()
        recipients = {m.to[0] for m in mail.outbox}
        assert "anna@example.com" not in recipients
        assert "bea@example.com" not in recipients
        assert len(recipients) == 2

    def test_limit_leaves_the_rest_for_the_next_call(self, old_room):
        _campaign()
        first = dispatcher.dispatch(limit=3)
        assert first["remaining"] > 0
        dispatcher.dispatch()
        assert len(mail.outbox) == 4

    def test_last_call_only_reaches_members_with_profiles_left(self, old_room):
        now = timezone.now()
        campaign = _campaign(last_call_at=now - timedelta(minutes=5))
        for step, channel in (("start", "email"), ("start", "push")):
            for user in (
                old_room["anna"],
                old_room["bea"],
                old_room["carl"],
                old_room["dan"],
            ):
                SwipeCampaignMessage.objects.create(
                    campaign=campaign,
                    user=user,
                    step=step,
                    channel=channel,
                    status="sent",
                )
        event = old_room["event"]
        for target in (old_room["carl"], old_room["dan"]):
            event_swipes.record_swipe(old_room["anna"], event, target, False)
        dispatcher.dispatch()
        assert "anna@example.com" not in {m.to[0] for m in mail.outbox}
        assert len(mail.outbox) == 3

    def test_dry_run_sends_nothing(self, old_room):
        _campaign()
        result = dispatcher.dispatch(dry_run=True)
        assert result["would_send"] == {"start/email": 4, "start/push": 4}
        assert mail.outbox == []
        assert not SwipeCampaignMessage.objects.exists()

    def test_nothing_happens_without_a_running_campaign(self, old_room):
        assert dispatcher.dispatch()["campaign"] is None
        assert mail.outbox == []

    def test_dispatch_endpoint_needs_the_admin_key(self, old_room):
        _campaign()
        url = reverse("api_admin_swipe_campaign_dispatch")
        with override_settings(ADMIN_API_KEY="test-admin-key"):
            assert Client().post(url).status_code == 401
            response = Client().post(url, HTTP_AUTHORIZATION="Bearer test-admin-key")
        assert response.status_code == 202
        assert response.json()["sent"] == 4


# ---------------------------------------------------------------------------
# Old "My Crush!" leads
# ---------------------------------------------------------------------------


class TestCloseOpenCrushLeads:
    def _lead(self, requester, recipient, event, status="pending"):
        return EventConnection.objects.create(
            requester=requester,
            recipient=recipient,
            event=event,
            flow=EventConnection.FLOW_CRUSH,
            status=status,
        )

    def test_dry_run_changes_nothing(self, old_room):
        lead = self._lead(old_room["anna"], old_room["carl"], old_room["event"])
        out = StringIO()
        call_command("close_open_crush_leads", stdout=out)
        lead.refresh_from_db()
        assert lead.status == "pending"
        assert "1 open lead(s)" in out.getvalue()

    def test_apply_closes_open_leads_only(self, client, old_room):
        event = old_room["event"]
        open_lead = self._lead(old_room["anna"], old_room["carl"], event)
        reviewing = self._lead(
            old_room["bea"], old_room["dan"], event, "coach_reviewing"
        )
        shared = self._lead(old_room["anna"], old_room["dan"], event, "shared")
        call_command("close_open_crush_leads", "--apply", stdout=StringIO())
        for lead in (open_lead, reviewing, shared):
            lead.refresh_from_db()
        assert open_lead.status == "declined"
        assert reviewing.status == "declined"
        assert shared.status == "shared"
        assert open_lead.system_actions[-1]["type"] == "crush_lead_retired"

        _login(client, old_room["anna"])
        response = client.get(reverse("crush_lu:my_connections"))
        sent_ids = {c.pk for c in response.context["sent_requests"]}
        assert open_lead.pk not in sent_ids


# ---------------------------------------------------------------------------
# Translations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lang,expected",
    [
        ("de", ["Swipe deine vergangenen Events", "Noch 2 Profile", "Sichtbarkeit"]),
        ("fr", ["Swipez vos soirées passées", "Encore 2 profils", "Visibilité"]),
    ],
)
def test_hub_page_is_translated(client, old_room, lang, expected):
    _campaign()
    _login(client, old_room["anna"])
    body = client.get(f"/{lang}/events/swipe-past/").content.decode()
    for text in expected:
        assert text in body


@pytest.mark.parametrize(
    "lang,expected",
    [
        ("de", ["Erinnerst du dich an", "Jetzt swipen", "Hier ausblenden"]),
        ("fr", ["Vous vous souvenez de", "Commencer à swiper", "Masquez-vous ici"]),
    ],
)
def test_campaign_email_is_translated(old_room, lang, expected):
    profile = old_room["anna"].crushprofile
    profile.preferred_language = lang
    profile.save(update_fields=["preferred_language"])
    campaign = _campaign()
    assert dispatcher.send_campaign_email(old_room["anna"], campaign, "start")
    message = mail.outbox[-1]
    html = message.alternatives[0][0]
    for text in expected:
        assert text in message.subject + html


@pytest.mark.parametrize(
    "lang,expected",
    [("de", "bis 13. dezember"), ("fr", "13 décembre"), ("en", "December 13")],
)
def test_campaign_end_date_uses_the_locale_day_month_format(old_room, lang, expected):
    profile = old_room["anna"].crushprofile
    profile.preferred_language = lang
    profile.save(update_fields=["preferred_language"])
    end = timezone.make_aware(datetime(2026, 12, 13, 23, 59))
    campaign = _campaign(ends_at=end)
    assert dispatcher.send_campaign_email(old_room["anna"], campaign, "start")
    html = mail.outbox[-1].alternatives[0][0]
    # FR month names are capitalised by the site catalog ("Décembre").
    assert expected.lower() in html.lower()
