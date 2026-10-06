"""Moderation reach: persisted surfaces, staff decisions and the review pass."""

from datetime import timedelta
from unittest.mock import patch
from urllib.parse import quote

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core import signing
from django.core.cache import cache
from django.utils.dateparse import parse_datetime

from crush_lu.models import (
    CrushConnectMembership,
    CrushProfile,
    ProfilePhotoReviewLog,
    UserReport,
)
from crush_lu.notification_service import NotificationService, NotificationType
from crush_lu.services.photo_review import (
    PhotoReviewError,
    get_photo_review_queue,
    submit_photo_review,
    undo_last_photo_review,
)
from crush_lu.tests.test_coach_photo_review import _make_candidate, _make_coach

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]

User = get_user_model()
REASONS = {"approved": "", "flagged_fake": "fake_profile", "needs_revision": "other"}


@pytest.fixture(autouse=True)
def isolate_side_effects(monkeypatch):
    cache.clear()
    monkeypatch.setattr(
        CrushProfile._meta.get_field("photo_1").storage, "delete", lambda name: None
    )
    with patch("crush_lu.services.photo_review.notify_photo_revision"):
        yield


def _review(coach, profile, decision, notes=""):
    return submit_photo_review(
        coach,
        profile.pk,
        decision,
        REASONS[decision],
        notes,
        photo_key=profile.photo_1.name,
    )


def _staff_request(username="staff_moderator"):
    from crush_lu.tests.test_crush_connect import _admin_request

    staff = User.objects.create_superuser(
        username=username, email=f"{username}@crush.lu", password="staffpass123"
    )
    return _admin_request(staff)


# --- Connect Week: cards persisted before the decision -----------------------


@pytest.mark.parametrize("decision", ["needs_revision", "flagged_fake"])
def test_moderated_member_loses_persisted_connect_week_cards(
    client, settings, decision
):
    from crush_lu.models.crush_connect_cycle import ConnectCycleCard
    from crush_lu.tests.test_connect_week_experience import (
        WEEK_HOME_URL,
        WEEK_REVIEW_URL,
        _make_cycle_user,
        _seed_cycle_pool,
    )
    from crush_lu.tests.test_crush_connect import CONNECT_TEASER_URL, _login_eligible
    from crush_lu.views_media import can_view_profile_photo

    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("cycle_me")
    _seed_cycle_pool(me, n=3)
    _login_eligible(client, me)
    assert client.get(WEEK_HOME_URL).status_code == 200
    card = ConnectCycleCard.objects.filter(session__user=me).first()
    target = card.target_user
    assert can_view_profile_photo(User.objects.get(pk=me.pk), target.crushprofile)

    # The decision lands after today's cards were generated; a fake flag
    # normally also excludes, so this is the exclusion-lifted case.
    CrushProfile.objects.filter(user=me).update(photo_review_status=decision)
    assert not CrushConnectMembership.objects.get(user=me).excluded_by_coach

    def assert_blocked(response):
        assert response.status_code == 302
        location = response["Location"]
        if decision == "flagged_fake":
            assert location == CONNECT_TEASER_URL
        else:
            assert location.startswith("/en/profile/edit/?")
            assert "section=photos" in location
            assert f"next={quote(WEEK_HOME_URL, safe='')}" in location

    assert_blocked(client.get(WEEK_HOME_URL))
    data = {
        f"answer_{gq.question_id}": "yes"
        for gq in target.crush_connect_membership.active_gate_questions
    }
    assert_blocked(client.post(f"/en/crush-connect/week/card/{card.pk}/answer/", data))
    card.refresh_from_db()
    assert not card.is_completed
    # Unblocked, an active week's review bounces to week home instead.
    assert_blocked(client.get(WEEK_REVIEW_URL))
    assert not can_view_profile_photo(User.objects.get(pk=me.pk), target.crushprofile)

    if decision == "needs_revision":
        # Uploading a new photo answers the request (back to pending review).
        profile = CrushProfile.objects.get(user=me)
        profile.photo_1 = "users/1/photos/replacement.jpg"
        profile.save(update_fields=["photo_1"])
    else:
        # A fake flag lifts only by an explicit staff decision.
        CrushProfile.objects.filter(user=me).update(photo_review_status="pending")
    assert CrushProfile.objects.get(user=me).photo_review_status == "pending"
    assert client.get(WEEK_HOME_URL).status_code == 200
    assert can_view_profile_photo(User.objects.get(pk=me.pk), target.crushprofile)


def test_week_home_tells_revision_member_why(client, settings):
    from crush_lu.tests.test_connect_week_experience import (
        WEEK_HOME_URL,
        _make_cycle_user,
    )
    from crush_lu.tests.test_crush_connect import _login_eligible

    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("cycle_revision")
    CrushProfile.objects.filter(user=me).update(photo_review_status="needs_revision")
    _login_eligible(client, me)
    response = client.get(WEEK_HOME_URL)
    assert response.status_code == 302
    assert [str(m) for m in get_messages(response.wsgi_request)] == [
        (
            "A coach requested an updated profile photo. Please upload a clear "
            "photo showing your face to continue."
        )
    ]


# --- Event Lobby: an actionable lock page for a revision request -------------


@pytest.mark.parametrize("decision", ["needs_revision", "flagged_fake"])
def test_lobby_lock_page_offers_photo_update_only_for_revision(
    client, settings, decision
):
    from crush_lu.tests.test_event_lobby import (
        _attend,
        _login,
        _make_event,
        _make_member,
    )

    settings.CRUSH_EVENT_LOBBY_ENABLED = True
    settings.CRUSH_CONNECT_LAUNCHED = True
    settings.AZURE_ACCOUNT_NAME = ""
    member = _make_member("lobby_moderated")
    event = _make_event()
    _attend(member, event)
    _login(client, member)
    lobby_url = f"/en/events/{event.pk}/lobby/"
    # Exclusion lifted (or never set): the flag alone decides the gate.
    CrushProfile.objects.filter(user=member).update(photo_review_status=decision)

    response = client.get(lobby_url)

    assert response.status_code == 200
    assert "crush_lu/event_lobby/lobby_locked.html" in [
        t.name for t in response.templates
    ]
    html = response.content.decode()
    photo_link = "/en/profile/edit/?section=photos&amp;next=" + quote(
        lobby_url, safe=""
    )
    if decision == "needs_revision":
        assert photo_link in html
        assert "Update photo" in html
        assert "Finish Crush Connect" not in html
    else:
        # The flag is sticky: never invite a photo swap that cannot clear it.
        assert "section=photos" not in html
        assert "About Crush Connect" in html


@pytest.mark.parametrize(
    "language,heading", [("de", "Prüfung des Profilfotos"), ("fr", "Vérification")]
)
def test_lobby_revision_lock_page_is_translated(client, settings, language, heading):
    from crush_lu.tests.test_event_lobby import (
        _attend,
        _login,
        _make_event,
        _make_member,
    )

    settings.CRUSH_EVENT_LOBBY_ENABLED = True
    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_member("lobby_revision_i18n")
    event = _make_event()
    _attend(member, event)
    _login(client, member)
    CrushProfile.objects.filter(user=member).update(
        photo_review_status="needs_revision"
    )
    html = client.get(f"/{language}/events/{event.pk}/lobby/").content.decode()
    assert heading in html
    assert f"/{language}/profile/edit/?section=photos" in html


# --- Coach match recommendations ---------------------------------------------


def _match_setup(client):
    from crush_lu.models import MatchScore, ProfileSubmission, Trait

    coach = _make_coach()
    member = _make_candidate("matched_member")
    counterpart = _make_candidate(
        "matched_counterpart", photo_key="users/2/photos/counterpart.jpg"
    )
    ProfileSubmission.objects.create(profile=member, coach=coach, status="approved")
    member.user.crush_connect_membership.sought_qualities.set(
        Trait.objects.filter(trait_type="quality")[:1]
    )
    user_a, user_b = sorted([member.user, counterpart.user], key=lambda u: u.pk)
    MatchScore.objects.create(user_a=user_a, user_b=user_b, score_final=0.9)
    client.force_login(coach.user)

    def visible():
        pairs = client.get("/en/coach/match-pairs/").context["pairs"]
        matches = client.get(f"/en/coach/member/{member.user_id}/matches/").context[
            "matches"
        ]
        dashboard = client.get("/en/coach/dashboard/").context["match_pairs_count"]
        return len(pairs), [match["user"].pk for match in matches], dashboard

    return coach, member, counterpart, visible


@pytest.mark.parametrize("decision", ["needs_revision", "flagged_fake"])
def test_coach_match_views_hide_moderated_counterpart(client, decision):
    coach, _member, counterpart, visible = _match_setup(client)
    assert visible() == (1, [counterpart.user_id], 1)

    result = _review(coach, counterpart, decision)
    assert visible() == (0, [], 0)

    # Display-time rule: Undo heals the cached scores without a rebuild.
    undo_last_photo_review(coach, log_id=result["log_id"])
    assert visible() == (1, [counterpart.user_id], 1)


@pytest.mark.parametrize("decision", ["needs_revision", "flagged_fake"])
def test_coach_match_views_hide_moderated_member(client, decision):
    _coach, member, counterpart, visible = _match_setup(client)
    assert visible() == (1, [counterpart.user_id], 1)
    _review(_make_coach("other_reviewer"), member, decision)
    assert visible() == (0, [], 0)


# --- Undo must not override a staff decision on the flag's report ------------


@pytest.mark.parametrize(
    "staff_action",
    ["mark_reviewing", "mark_dismissed", "exclude_reported_users", "edit_notes"],
)
def test_undo_refuses_after_staff_handled_flag_report(staff_action):
    from crush_lu.admin.moderation import UserReportAdmin
    from crush_lu.admin.site import crush_admin_site

    coach, profile = _make_coach(), _make_candidate()
    result = _review(coach, profile, "flagged_fake")
    log = ProfilePhotoReviewLog.objects.get(pk=result["log_id"])
    reports = UserReport.objects.filter(pk=log.report_id)
    if staff_action == "edit_notes":
        report = reports.get()
        report.resolution_notes = "Staff confirmed: stock photo."
        report.save()
    else:
        admin = UserReportAdmin(UserReport, crush_admin_site)
        getattr(admin, staff_action)(_staff_request(), reports)
    fields = ("status", "handled_by_id", "handled_at", "resolution_notes")
    staff_state = reports.values(*fields).get()

    with pytest.raises(PhotoReviewError) as exc:
        undo_last_photo_review(coach, log_id=log.pk)

    assert exc.value.status == 409
    profile.refresh_from_db()
    log.refresh_from_db()
    assert profile.photo_review_status == "flagged_fake"
    assert log.undone_at is None
    assert CrushConnectMembership.objects.get(user=profile.user).excluded_by_coach
    assert reports.values(*fields).get() == staff_state


def test_undo_without_report_still_lifts_flag():
    coach, profile = _make_coach(), _make_candidate()
    result = _review(coach, profile, "flagged_fake")
    UserReport.objects.filter(
        pk=ProfilePhotoReviewLog.objects.get(pk=result["log_id"]).report_id
    ).delete()
    undo_last_photo_review(coach, log_id=result["log_id"])
    profile.refresh_from_db()
    assert profile.photo_review_status == "pending"
    assert not CrushConnectMembership.objects.get(user=profile.user).excluded_by_coach


# --- Review pass: priority is snapshotted at pass start ----------------------


def test_queue_priority_rise_mid_pass_does_not_skip_a_card():
    coach = _make_coach()
    low_1 = _make_candidate("low_1", onboarded=False)
    low_2 = _make_candidate("low_2", onboarded=False)
    high = _make_candidate("high")
    first, _ = get_photo_review_queue(coach, limit=2)
    assert [card["id"] for card in first] == [high.pk, low_2.pk]

    # low_1 starts and finishes Connect onboarding after the pass started.
    as_of = parse_datetime(
        signing.loads(first[-1]["queue_cursor"], salt="coach-photo-queue")["as_of"]
    )
    CrushConnectMembership.objects.create(user=low_1.user, onboarded_at=as_of)
    CrushConnectMembership.objects.filter(user=low_1.user).update(
        created_at=as_of + timedelta(microseconds=1),
        onboarded_at=as_of + timedelta(microseconds=1),
    )

    second, total = get_photo_review_queue(
        coach, limit=2, cursor=first[-1]["queue_cursor"]
    )
    assert [card["id"] for card in second] == [low_1.pk]
    assert total == 3
    # A new pass ranks the member at the new priority.
    fresh, _ = get_photo_review_queue(coach, limit=3)
    assert [card["id"] for card in fresh] == [high.pk, low_1.pk, low_2.pk]


@pytest.mark.parametrize("as_of", [None, "not-a-date", 12])
def test_queue_refuses_cursor_without_valid_snapshot(as_of):
    coach, profile = _make_coach(), _make_candidate()
    position = {"priority": 3, "id": profile.pk}
    if as_of is not None:
        position["as_of"] = as_of
    cursor = signing.dumps(position, salt="coach-photo-queue")
    with pytest.raises(PhotoReviewError) as exc:
        get_photo_review_queue(coach, cursor=cursor)
    assert exc.value.status == 400


# --- Staff lift of a moderated photo ------------------------------------------


def test_admin_lift_returns_moderated_photos_to_coach_review():
    from django.contrib.admin.models import CHANGE, LogEntry

    from crush_lu.admin.profiles import CrushProfileAdmin
    from crush_lu.admin.site import crush_admin_site

    coach = _make_coach()
    flagged = _make_candidate("flagged_member", photo_key="users/1/photos/f.jpg")
    replaced = _make_candidate("flagged_replaced", photo_key="users/2/photos/a.jpg")
    revision = _make_candidate("revision_member", photo_key="users/3/photos/r.jpg")
    approved = _make_candidate("approved_member", photo_key="users/4/photos/ok.jpg")
    pending = _make_candidate("pending_member", photo_key="users/5/photos/p.jpg")
    _review(coach, flagged, "flagged_fake", notes="Stock image")
    _review(coach, replaced, "flagged_fake")
    _review(coach, revision, "needs_revision")
    _review(coach, approved, "approved")
    # A new upload after the flag keeps the flag (with no reviewed key).
    replaced.refresh_from_db()
    replaced.photo_1 = "users/2/photos/b.jpg"
    replaced.save(update_fields=["photo_1"])
    replaced.refresh_from_db()
    assert (replaced.photo_review_status, replaced.photo_review_key) == (
        "flagged_fake",
        "",
    )
    review_fields = (
        "photo_review_status",
        "photo_review_key",
        "photo_reviewed_at",
        "photo_reviewed_by_id",
        "photo_review_notes",
    )
    untouched = {
        row["pk"]: row
        for row in CrushProfile.objects.filter(pk__in=[approved.pk, pending.pk]).values(
            "pk", *review_fields
        )
    }

    request = _staff_request()
    CrushProfileAdmin(CrushProfile, crush_admin_site).lift_photo_review_moderation(
        request,
        CrushProfile.objects.filter(
            pk__in=[flagged.pk, replaced.pk, revision.pk, approved.pk, pending.pk]
        ),
    )

    for profile, old_status in (
        (flagged, "flagged_fake"),
        (replaced, "flagged_fake"),
        (revision, "needs_revision"),
    ):
        profile.refresh_from_db()
        assert profile.photo_review_status == "pending"
        assert profile.photo_review_key == ""
        assert profile.photo_reviewed_at is None
        assert profile.photo_reviewed_by_id is None
        assert profile.photo_review_notes == ""
        entry = LogEntry.objects.get(
            object_id=str(profile.pk), action_flag=CHANGE, user=request.user
        )
        assert entry.change_message == (
            f"Lifted photo review status '{old_status}' -> 'pending'"
        )
    assert {
        row["pk"]: row
        for row in CrushProfile.objects.filter(pk__in=[approved.pk, pending.pk]).values(
            "pk", *review_fields
        )
    } == untouched
    cards, _ = get_photo_review_queue(_make_coach("second_coach"))
    assert {card["id"] for card in cards} == {
        flagged.pk,
        replaced.pk,
        revision.pk,
        pending.pk,
    }
    # The coach exclusion is a separate lever: kept, and staff are told so.
    assert CrushConnectMembership.objects.get(user=flagged.user).excluded_by_coach
    notices = [str(message) for message in get_messages(request)]
    assert any("3 profile(s)" in notice for notice in notices)
    assert any("2 of them are still excluded" in notice for notice in notices)


# --- Coach deck: skipped picks are surfaced after Undo -----------------------


@pytest.mark.parametrize(
    "language,text",
    [
        ("en", "Some Coach's Picks could not be restored"),
        ("de", "konnten nicht wiederhergestellt werden"),
        ("fr", "n’ont pas pu être rétablis"),
    ],
)
def test_deck_carries_translated_picks_not_restored_notice(client, language, text):
    coach = _make_coach()
    client.force_login(coach.user)
    html = client.get(f"/{language}/coach/photo-review/").content.decode()
    attribute = html.split('data-picks-not-restored="', 1)[1].split('"', 1)[0]
    assert text in attribute
    assert 'x-text="noticeMessage"' in html


def test_photo_revision_email_asks_only_for_a_new_photo():
    from crush_lu.email_helpers import send_photo_revision_request

    profile = _make_candidate()
    with patch("crush_lu.email_helpers.send_domain_email", return_value=1) as sender:
        # Routed through the service with no request, as the post-commit hook may.
        assert NotificationService._send_email(
            profile.user,
            NotificationType.PHOTO_REVISION,
            {"feedback": "Smile, please", "photo_review_log_id": 7},
            None,
        )
    html = sender.call_args.kwargs["html_message"]
    assert sender.call_args.kwargs["subject"] == "Please replace your profile photo"
    assert "Smile, please" in html
    assert "Upload a new main photo from your profile" in html
    assert "before approval" not in html
    assert "Resubmit" not in html
    assert send_photo_revision_request(profile.user) == 1


@pytest.mark.parametrize(
    "language,subject,line",
    [
        ("en", "Please replace your profile photo", "The rest of your profile"),
        ("de", "Bitte ersetze dein Profilfoto", "Der Rest deines Profils"),
        ("fr", "Veuillez remplacer votre photo de profil", "Le reste de votre profil"),
    ],
)
def test_photo_revision_email_uses_member_language(language, subject, line):
    from crush_lu.email_helpers import send_photo_revision_request

    profile = _make_candidate()
    profile.preferred_language = language
    profile.save(update_fields=["preferred_language"])
    with patch("crush_lu.email_helpers.send_domain_email", return_value=1) as sender:
        assert send_photo_revision_request(profile.user, None, "x") == 1
    assert sender.call_args.kwargs["subject"] == subject
    assert line in sender.call_args.kwargs["html_message"]
    assert sender.call_args.kwargs["domain"] == "crush.lu"


def test_photo_revision_web_push_is_photo_specific():
    profile = _make_candidate()
    with patch(
        "crush_lu.push_notifications.send_push_notification", return_value={}
    ) as push:
        NotificationService._send_push(
            profile.user,
            NotificationType.PHOTO_REVISION,
            {"feedback": "Smile, please", "photo_review_log_id": 7},
        )
    kwargs = push.call_args.kwargs
    assert kwargs["title"] == "Please replace your profile photo"
    assert kwargs["body"].endswith("Smile, please")
    assert kwargs["tag"] == "photo-review-7"
