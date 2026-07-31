"""
Tests for the coach event management page (``coach_event_detail``) and the
coach preview exemption on the Crush Connect Event Lobby.

Covers:
- The quiz config header link renders for every ``quiz_night`` *and* for any
  event that already has a quiz (a mixer can legitimately run one), labelled
  "Create Quiz" or "Configure Quiz" depending on which mode it lands in.
- The "Quiz Live View" link renders only when the event actually has a quiz.
- An active coach can preview the live event lobby without an attended
  registration (read-only — no participation row is created), while a
  non-coach non-attendee still gets the §5.3 indistinguishable 404.
- The preview really is read-only: no signal ledger, no live socket, inert
  tiles, and no member recap CTA once the 15s poll flips it to the ended
  state (#715). The participant-side counterparts live in
  ``test_event_lobby.py`` (``test_participant_page_is_not_read_only``,
  ``test_participant_keeps_the_recap_cta_in_the_ended_state``).
- Coach-vs-attendance precedence (#715, decided in #718): coach status wins
  only where the member path would dead-end on the lock page; a coach who
  clears the Connect gate gets the full member lobby.
- The manual check-in list filters on name *and* email.
"""

import re
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
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
from crush_lu.tests.test_event_lobby import (
    _end_event,
    _handle_of,
    _join,
    _make_member,
)

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
        """No quiz and not a quiz night — *creating* one stays type-specific,
        so the link would only be clutter."""
        coach = _make_coach()
        event = _make_event(event_type="mixer")
        client.force_login(coach)

        response = client.get(reverse("crush_lu:coach_event_detail", args=[event.pk]))

        assert response.status_code == 200
        quiz_config_url = reverse("crush_lu:coach_quiz_config", args=[event.pk])
        assert quiz_config_url.encode() not in response.content
        assert b"Configure Quiz" not in response.content
        assert b"Create Quiz" not in response.content

    def test_configure_quiz_shown_for_quiz_night(self, client):
        """A quiz night with no QuizEvent yet: the config page's empty state is
        the create form, so the link is live and labelled for creating."""
        coach = _make_coach()
        event = _make_event(event_type="quiz_night")
        client.force_login(coach)

        response = client.get(reverse("crush_lu:coach_event_detail", args=[event.pk]))

        assert response.status_code == 200
        quiz_config_url = reverse("crush_lu:coach_quiz_config", args=[event.pk])
        assert quiz_config_url.encode() in response.content
        assert b"Create Quiz" in response.content

    def test_configure_quiz_shown_for_non_quiz_night_with_quiz(self, client):
        """The bug: ``QuizEvent.event`` is unrestricted and ``quiz_live_view``
        works for any event type, so a coach can legitimately run a quiz at a
        Social Mixer. Gating the config link on ``event_type`` left that coach
        with a working Host Panel and Live View but no route to configuration
        outside Django admin."""
        coach = _make_coach()
        event = _make_event(event_type="mixer")
        QuizEvent.objects.create(event=event, created_by=coach)
        client.force_login(coach)

        response = client.get(reverse("crush_lu:coach_event_detail", args=[event.pk]))

        assert response.status_code == 200
        quiz_config_url = reverse("crush_lu:coach_quiz_config", args=[event.pk])
        assert quiz_config_url.encode() in response.content
        # An existing quiz is configured, never created.
        assert b"Configure Quiz" in response.content
        assert b"Create Quiz" not in response.content

    def test_configure_quiz_link_actually_opens_for_a_non_quiz_night(self, client):
        """Rendering the link is half the claim — ``coach_quiz_config`` must
        also accept a non-``quiz_night`` event, or the fix just moves the dead
        end one click further in."""
        coach = _make_coach()
        event = _make_event(event_type="mixer")
        QuizEvent.objects.create(event=event, created_by=coach)
        client.force_login(coach)

        response = client.get(
            reverse("crush_lu:coach_quiz_config", args=[event.pk]), follow=True
        )

        assert response.status_code == 200
        assert b"Quiz Configuration" in response.content

    def test_configure_quiz_label_for_quiz_night_that_has_a_quiz(self, client):
        """Both gate halves true — the label must follow the quiz, not the type."""
        coach = _make_coach()
        event = _make_event(event_type="quiz_night")
        QuizEvent.objects.create(event=event, created_by=coach)
        client.force_login(coach)

        response = client.get(reverse("crush_lu:coach_event_detail", args=[event.pk]))

        assert response.status_code == 200
        assert b"Configure Quiz" in response.content
        assert b"Create Quiz" not in response.content

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

    def test_quiz_live_link_actually_opens_for_a_coach(self, client):
        """The link is only honest because a signal grants is_staff to every
        active CrushCoach (signals.py), which is what quiz_live_view checks —
        it has no coach branch of its own. Pinned here so revoking that
        implicit staff grant fails loudly instead of shipping a dead link to
        every coach who never checked in as an attendee."""
        coach = _make_coach()
        event = _make_event(event_type="quiz_night", starts_in_minutes=-30)
        QuizEvent.objects.create(event=event, created_by=coach)
        coach.refresh_from_db()
        assert coach.is_staff, "coach lost the implicit staff grant"
        client.force_login(coach)

        response = client.get(reverse("crush_lu:quiz_live", args=[event.pk]))

        assert response.status_code == 200


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
        """Recap requires member participation, which the coach preview cannot
        have, so it degrades to the closed page instead of a 404."""
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

    def test_preview_has_no_member_recap_cta_in_its_ended_state(self, client):
        """#715: the preview polls every 15s and ``lobby_state_api`` answers a
        coach with a non-live phase once the event ends, so ``applyState`` ->
        ``markEnded`` flips the panel to "ended" under a coach who simply left
        the tab open. The member ending asks "Who did you meet?" and links to
        the recap — which for a coach with no participation reloads
        ``event_lobby`` straight into ``lobby_closed.html``, a dead end at
        exactly the moment the coach is trying to work.

        Asserted on the served HTML because ``x-show`` cannot un-hide a block
        that was never rendered: the fix has to remove it, not hide it."""
        coach = _make_coach()
        event = _make_event(starts_in_minutes=-30)
        client.force_login(coach)

        html = client.get(
            reverse("crush_lu:event_lobby", args=[event.pk])
        ).content.decode()

        assert "Who did you meet?" not in html
        assert "Open the recap" not in html
        # ...replaced by a coach ending that leads back to their work surface.
        assert "data-coach-preview-ended" in html
        assert reverse("crush_lu:coach_event_detail", args=[event.pk]) in html

    def test_state_api_flips_the_preview_to_ended_after_the_event(self, client):
        """The server half of the #715 path: this non-live phase is what
        ``applyState`` turns into ``markEnded()``. Pinned so the leak cannot
        come back by the API starting to answer ``live`` forever (which would
        make the template assertion above vacuous)."""
        coach = _make_coach()
        event = _make_event(starts_in_minutes=-30)
        _end_event(event)
        client.force_login(coach)

        payload = client.get(
            reverse("crush_lu:event_lobby_state_api", args=[event.pk])
        ).json()

        assert payload["ok"] is True
        assert payload["state"]["phase"] != "live"

    def test_attended_coach_still_gets_the_preview(self, client):
        """A coach who scanned themselves in at the door is still a coach.
        Attendance must not drop them onto the member path, where failing the
        Connect gate hands them the lock page (or a 404) instead of the
        preview they came for."""
        coach = _make_coach()
        event = _make_event(starts_in_minutes=-30)
        EventRegistration.objects.create(event=event, user=coach, status="attended")
        client.force_login(coach)

        response = client.get(reverse("crush_lu:event_lobby", args=[event.pk]))

        assert response.status_code == 200
        html = response.content.decode()
        assert 'data-read-only="1"' in html
        assert "data-coach-preview-notice" in html
        # Still no participation — attendance alone must not mint one here.
        assert EventLobbyParticipation.objects.filter(user=coach).count() == 0

    def test_eligible_coach_who_attends_gets_the_full_member_lobby(self, client):
        """The precedence question #715 raised, settled in #718 as "keep
        current behaviour": a coach who clears the Connect gate on their own
        attended registration never reaches the coach branch in
        ``event_lobby`` — they fall through to the participant path and get
        the real member lobby, signals included. Coach status beats attendance
        only when the member path has nothing left to offer but the lock page
        (the test above). Pinned here so flipping that precedence has to be a
        deliberate change, not a silent one."""
        coach = _make_member("workingcoach")
        _grant_consent(coach)
        CrushCoach.objects.create(user=coach, is_active=True)
        event = _make_event(starts_in_minutes=-30)
        _join(coach, event)
        client.force_login(coach)

        html = client.get(
            reverse("crush_lu:event_lobby", args=[event.pk])
        ).content.decode()

        assert 'data-read-only="1"' not in html
        assert "data-coach-preview-notice" not in html
        assert "data-coach-preview-ended" not in html
        assert "Your signals for tonight" in html
        assert EventLobbyParticipation.objects.filter(user=coach).count() == 1

    def test_preview_card_hidden_for_unpublished_or_cancelled_events(self, client):
        """_get_lobby_event rejects both, so the card would be a dead link."""
        coach = _make_coach()
        client.force_login(coach)

        unpublished = _make_event()
        MeetupEvent.objects.filter(pk=unpublished.pk).update(is_published=False)
        cancelled = _make_event()
        MeetupEvent.objects.filter(pk=cancelled.pk).update(is_cancelled=True)
        published = _make_event()

        for event, expected in (
            (unpublished, False),
            (cancelled, False),
            (published, True),
        ):
            html = client.get(
                reverse("crush_lu:coach_event_detail", args=[event.pk])
            ).content.decode()
            assert ("Event Lobby Preview" in html) is expected, event.pk

    def test_preview_can_load_roster_photos(self, client, settings, tmp_path):
        """The faces ARE the preview. ``lobby_photo`` authorizes the viewer
        against the roster, so without the coach exemption the grid renders
        three broken images — a hole the page-level tests cannot see, because
        the page itself returns 200 either way."""
        coach = _make_coach()
        event = _make_event(starts_in_minutes=-30)
        member = _make_member("photoone")
        settings.MEDIA_ROOT = str(tmp_path)
        member.crushprofile.photo_1.save(
            "lobby.jpg", ContentFile(b"jpegbytes"), save=True
        )
        _join(member, event)
        client.force_login(coach)

        response = client.get(
            reverse(
                "crush_lu:event_lobby_photo",
                kwargs={"event_id": event.pk, "handle": _handle_of(member, event)},
            )
        )

        assert response.status_code == 200
        assert response["Content-Type"] == "image/jpeg"

    def test_photo_exemption_ends_with_the_live_phase(self, client, settings, tmp_path):
        """Narrower than the page it serves: the coach has no recap preview,
        so the photo route must not stay open into the recap window."""
        coach = _make_coach()
        # Join while live — participations are only ever created live — then
        # rewind the event into the recap window.
        event = _make_event(starts_in_minutes=-30)
        member = _make_member("photorecap")
        settings.MEDIA_ROOT = str(tmp_path)
        member.crushprofile.photo_1.save(
            "lobby.jpg", ContentFile(b"jpegbytes"), save=True
        )
        _join(member, event)
        handle = _handle_of(member, event)
        _end_event(event)
        client.force_login(coach)

        response = client.get(
            reverse(
                "crush_lu:event_lobby_photo",
                kwargs={"event_id": event.pk, "handle": handle},
            )
        )

        assert response.status_code == 404

    def test_non_coach_non_participant_still_denied_photos(
        self, client, settings, tmp_path
    ):
        """§13 unchanged: the exemption is for coaches, not for everyone who
        can guess a handle."""
        outsider = User.objects.create_user(
            username="nosy@example.com", email="nosy@example.com", password="pass12345"
        )
        _grant_consent(outsider)
        event = _make_event(starts_in_minutes=-30)
        member = _make_member("photoguard")
        settings.MEDIA_ROOT = str(tmp_path)
        member.crushprofile.photo_1.save(
            "lobby.jpg", ContentFile(b"jpegbytes"), save=True
        )
        _join(member, event)
        client.force_login(outsider)

        response = client.get(
            reverse(
                "crush_lu:event_lobby_photo",
                kwargs={"event_id": event.pk, "handle": _handle_of(member, event)},
            )
        )

        assert response.status_code == 404


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
