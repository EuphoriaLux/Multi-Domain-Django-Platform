"""
Tests for the coach event management page (``coach_event_detail``) and the
coach preview exemption on the Crush Connect Event Lobby.

Covers:
- The "Configure Quiz" header link renders only for ``quiz_night`` events.
- The "Quiz Live View" link renders only when the event actually has a quiz.
- An active coach can preview the live event lobby without an attended
  registration (read-only — no participation row is created), while a
  non-coach non-attendee still gets the §5.3 indistinguishable 404.
- The preview really is read-only: no signal ledger, no live socket, inert
  tiles. The participant-side counterpart lives in ``test_event_lobby.py``
  (``test_participant_page_is_not_read_only``).
- The manual check-in list filters on name *and* email.
"""

import re
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import (
    CrushCoach,
    EventLobbyParticipation,
    EventRegistration,
    MeetupEvent,
    QuizEvent,
    UserDataConsent,
)

# Reused rather than re-derived: building a member who clears every §5.1 gate
# knob is exactly what these helpers already encode.
from crush_lu.tests.test_event_lobby import _join, _make_member

User = get_user_model()

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]


@pytest.fixture(autouse=True)
def _clear_ratelimit_cache():
    cache.clear()
    yield


def _grant_consent(user):
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )


def _make_coach(username="coach@example.com"):
    user = User.objects.create_user(
        username=username, email=username, password="pass12345", first_name="Cam"
    )
    _grant_consent(user)
    CrushCoach.objects.create(user=user, is_active=True)
    return user


def _make_event(event_type="mixer", starts_in_minutes=60, duration=120):
    now = timezone.now()
    return MeetupEvent.objects.create(
        title="Coach Test Event",
        description="x",
        event_type=event_type,
        location="Lux",
        address="1 Test St",
        canton="Luxembourg",
        date_time=now + timedelta(minutes=starts_in_minutes),
        duration_minutes=duration,
        max_participants=30,
        registration_deadline=now + timedelta(minutes=30),
        is_published=True,
    )


class TestQuizLinksVisibility:
    def test_configure_quiz_hidden_for_non_quiz_event(self, client):
        coach = _make_coach()
        event = _make_event(event_type="mixer")
        client.force_login(coach)

        response = client.get(reverse("crush_lu:coach_event_detail", args=[event.pk]))

        assert response.status_code == 200
        quiz_config_url = reverse("crush_lu:coach_quiz_config", args=[event.pk])
        assert quiz_config_url.encode() not in response.content
        assert b"Configure Quiz" not in response.content

    def test_configure_quiz_shown_for_quiz_night(self, client):
        coach = _make_coach()
        event = _make_event(event_type="quiz_night")
        client.force_login(coach)

        response = client.get(reverse("crush_lu:coach_event_detail", args=[event.pk]))

        assert response.status_code == 200
        quiz_config_url = reverse("crush_lu:coach_quiz_config", args=[event.pk])
        assert quiz_config_url.encode() in response.content
        assert b"Configure Quiz" in response.content

    def test_quiz_live_view_hidden_without_quiz(self, client):
        """A quiz_night event without a QuizEvent row must not render links
        that would 404 (quiz_live 404s without one)."""
        coach = _make_coach()
        event = _make_event(event_type="quiz_night")
        client.force_login(coach)

        response = client.get(reverse("crush_lu:coach_event_detail", args=[event.pk]))

        assert response.status_code == 200
        assert (
            reverse("crush_lu:quiz_live", args=[event.pk]).encode()
            not in response.content
        )
        assert (
            reverse("crush_lu:quiz_coach", args=[event.pk]).encode()
            not in response.content
        )

    def test_quiz_live_view_shown_with_quiz(self, client):
        coach = _make_coach()
        event = _make_event(event_type="quiz_night")
        QuizEvent.objects.create(event=event, created_by=coach)
        client.force_login(coach)

        response = client.get(reverse("crush_lu:coach_event_detail", args=[event.pk]))

        assert response.status_code == 200
        assert (
            reverse("crush_lu:quiz_live", args=[event.pk]).encode() in response.content
        )


@pytest.fixture
def lobby_flags(settings):
    """Rollout flag + Connect launch phase on; force local photo serving."""
    settings.CRUSH_EVENT_LOBBY_ENABLED = True
    settings.CRUSH_CONNECT_LAUNCHED = True
    settings.AZURE_ACCOUNT_NAME = ""


@pytest.mark.usefixtures("lobby_flags")
class TestCoachLobbyPreview:
    def test_coach_previews_live_lobby_without_attending(self, client):
        """The coach running the event never checks in as a member — the
        attendance hard wall must not 404 them out of the preview."""
        coach = _make_coach()
        event = _make_event(starts_in_minutes=-30)  # live phase
        client.force_login(coach)

        response = client.get(reverse("crush_lu:event_lobby", args=[event.pk]))

        assert response.status_code == 200
        assert response["Cache-Control"] == "private, no-store"
        assert b"lobby-grid" in response.content
        # Read-only: previewing never creates a participation row.
        assert EventLobbyParticipation.objects.count() == 0

    def test_coach_preview_after_event_end_shows_closed_not_404(self, client):
        """Recap requires a frozen participation the coach cannot have, so
        the preview degrades to the closed page instead of a 404."""
        coach = _make_coach()
        event = _make_event(starts_in_minutes=-180, duration=120)  # recap phase
        client.force_login(coach)

        response = client.get(reverse("crush_lu:event_lobby", args=[event.pk]))

        assert response.status_code == 200

    def test_non_coach_non_attendee_still_gets_404(self, client):
        """§5.3 unchanged for ordinary members: no attended registration and
        no coach account -> response indistinguishable from "no lobby"."""
        outsider = User.objects.create_user(
            username="outsider@example.com",
            email="outsider@example.com",
            password="pass12345",
        )
        _grant_consent(outsider)
        event = _make_event(starts_in_minutes=-30)
        client.force_login(outsider)

        assert (
            client.get(reverse("crush_lu:event_lobby", args=[event.pk])).status_code
            == 404
        )

    def test_inactive_coach_gets_404(self, client):
        """A deactivated coach account must not keep lobby access."""
        coach = _make_coach()
        CrushCoach.objects.filter(user=coach).update(is_active=False)
        event = _make_event(starts_in_minutes=-30)
        client.force_login(coach)

        assert (
            client.get(reverse("crush_lu:event_lobby", args=[event.pk])).status_code
            == 404
        )

    def test_coach_can_poll_state_api_read_only(self, client):
        """Without this the preview page's own polling would 403 and the
        client JS would wipe the server-rendered roster (revokeAccess)."""
        coach = _make_coach()
        event = _make_event(starts_in_minutes=-30)
        client.force_login(coach)

        response = client.get(
            reverse("crush_lu:event_lobby_state_api", args=[event.pk])
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is True
        assert payload["state"]["phase"] == "live"

    def test_preview_hides_the_signal_ledger_and_says_it_is_a_preview(self, client):
        """``lobby_state`` honestly reports three unspent signals for anyone,
        so without the preview switch the coach gets a full "Your signals for
        tonight" UI whose every action 403s — and one 403 makes
        event-lobby.js revokeAccess() the whole page."""
        coach = _make_coach()
        event = _make_event(starts_in_minutes=-30)
        client.force_login(coach)

        html = client.get(
            reverse("crush_lu:event_lobby", args=[event.pk])
        ).content.decode()

        assert 'data-read-only="1"' in html
        assert "data-coach-preview-notice" in html
        assert "Your signals for tonight" not in html
        assert "I&#x27;d like to meet" not in html

    def test_preview_opens_no_websocket(self, client):
        """``EventLobbyConsumer._can_join`` requires a participation, so a
        socket for the coach would only be opened to be closed — five bounded
        retries of pure noise. The 15s poll carries the preview instead."""
        coach = _make_coach()
        event = _make_event(starts_in_minutes=-30)
        client.force_login(coach)

        html = client.get(
            reverse("crush_lu:event_lobby", args=[event.pk])
        ).content.decode()

        assert 'data-ws-path=""' in html
        assert f"/ws/event-lobby/{event.pk}/" not in html

    def test_preview_roster_tiles_are_inert(self, client):
        """A tile the coach can press is a tile that 403s. The tile must ship
        disabled from the server; ``buildTile`` re-disables it on every poll."""
        coach = _make_coach()
        event = _make_event(starts_in_minutes=-30)
        member = _make_member("rosterone")
        _join(member, event)
        client.force_login(coach)

        html = client.get(
            reverse("crush_lu:event_lobby", args=[event.pk])
        ).content.decode()

        tiles = re.findall(r"<button[^>]*data-handle=[^>]*>", html)
        assert tiles, "roster tile did not render — the rest asserts nothing"
        assert all("disabled" in tile for tile in tiles)


class TestCheckinAttendeeSearch:
    def test_search_box_filters_on_name_and_email(self, client):
        """At the door a guest gives the name on their ID or the email they
        booked with — neither of which need match ``display_name``."""
        coach = _make_coach()
        event = _make_event()
        attendee = User.objects.create_user(
            username="dana@example.com",
            email="dana@example.com",
            password="pass12345",
            first_name="Dana",
            last_name="Vasseur",
        )
        _grant_consent(attendee)
        EventRegistration.objects.create(event=event, user=attendee, status="confirmed")
        client.force_login(coach)

        html = client.get(
            reverse("crush_lu:coach_event_checkin", args=[event.pk])
        ).content.decode()

        assert 'id="attendee-search"' in html
        assert "data-attendee-search=" in html
        # All three needles live in the one haystack attribute.
        haystack = html.split('data-attendee-search="')[1].split('"')[0]
        assert "Dana" in haystack
        assert "Vasseur" in haystack
        assert "dana@example.com" in haystack

    def test_search_box_absent_without_registrations(self, client):
        """No rows to filter — the control would be dead furniture."""
        coach = _make_coach()
        event = _make_event()
        client.force_login(coach)

        html = client.get(
            reverse("crush_lu:coach_event_checkin", args=[event.pk])
        ).content.decode()

        assert 'id="attendee-search"' not in html
