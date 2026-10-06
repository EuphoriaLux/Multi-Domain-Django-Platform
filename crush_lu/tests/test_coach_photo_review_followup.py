"""Regression coverage for moderation escape paths and irreversible notices."""

from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import override

from crush_lu.models import CrushProfile, Notification, ProfilePhotoReviewLog
from crush_lu.models.crush_connect import Interest
from crush_lu.notification_service import NotificationService, NotificationType
from crush_lu.services.photo_review import (
    PhotoReviewError,
    _retract_revision_safely,
    get_photo_review_queue,
    submit_photo_review,
    undo_last_photo_review,
)
from crush_lu.tests.test_coach_photo_review import _make_candidate, _make_coach

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]


@pytest.fixture(autouse=True)
def safe_side_effects(monkeypatch):
    cache.clear()
    monkeypatch.setattr(
        CrushProfile._meta.get_field("photo_1").storage, "delete", lambda name: None
    )
    monkeypatch.setattr("crush_lu.email_helpers.can_send_email", lambda *args: True)
    with patch.object(NotificationService, "_send_email", return_value=True) as email:
        yield email


def review(coach, profile, decision="needs_revision", reason="unclear_face"):
    return submit_photo_review(
        coach, profile.pk, decision, reason, photo_key=profile.photo_1.name
    )


@pytest.mark.parametrize(
    "decision,reason",
    [
        ("flagged_fake", "inappropriate"),
        ("needs_revision", "fake_profile"),
        ("approved", "group_photo"),
        ("skipped", "fake_profile"),
        ("needs_revision", ""),
    ],
)
def test_invalid_combinations_have_no_side_effects(decision, reason):
    coach, profile = _make_coach(), _make_candidate()
    with pytest.raises(PhotoReviewError) as exc:
        review(coach, profile, decision, reason)
    assert exc.value.status == 400
    profile.refresh_from_db()
    assert profile.photo_review_status == "pending"
    assert not ProfilePhotoReviewLog.objects.exists()
    assert not Notification.objects.exists()


def test_secondary_photo_submission_is_refused():
    coach, profile = _make_coach(), _make_candidate()
    client = Client()
    client.force_login(coach.user)
    response = client.post(
        reverse("crush_lu:coach_photo_review_decide"),
        {
            "profile_id": profile.pk,
            "photo_key": profile.photo_1.name,
            "photo_field": "photo_2",
            "decision": "needs_revision",
            "reason": "inappropriate",
        },
        content_type="application/json",
    )
    assert response.status_code == 400
    assert not ProfilePhotoReviewLog.objects.exists()


@pytest.mark.parametrize(
    "language,word", [("en", "Born"), ("de", "Geboren"), ("fr", "Né")]
)
def test_populated_interests_and_dynamic_details_render(language, word):
    coach, profile = _make_coach(), _make_candidate()
    interest = Interest.objects.create(
        slug="round-two-hiking",
        label="Hiking",
        label_de="Wandern",
        label_fr="Randonnée",
        category="outdoors",
    )
    profile.interests_new.add(interest)
    with override(language):
        cards, total = get_photo_review_queue(coach)
    assert total == 1
    assert cards[0]["interests"] == [
        {"en": "Hiking", "de": "Wandern", "fr": "Randonnée"}[language]
    ]
    assert word in cards[0]["dob_label"]
    assert cards[0]["risk_flags"]
    if language != "en":
        assert "No phone number" not in cards[0]["risk_flags"]


@pytest.mark.parametrize("door", [False, True])
@pytest.mark.parametrize(
    "decision,reason",
    [("needs_revision", "inappropriate"), ("flagged_fake", "fake_profile")],
)
def test_negative_moderation_survives_rejection_and_reverification(
    door, decision, reason
):
    from crush_lu.services.profile_verification import (
        claim_profile_verification,
        reject_door_verification,
        transition_unverified_profile,
    )

    coach, profile = _make_coach(), _make_candidate(is_approved=door)
    review(coach, profile, decision, reason)
    profile.refresh_from_db()
    before = (
        profile.photo_review_status,
        profile.photo_review_key,
        profile.photo_reviewed_at,
        profile.photo_reviewed_by_id,
    )
    transition = reject_door_verification if door else transition_unverified_profile
    assert transition(profile, target_status="rejected")
    assert claim_profile_verification(
        profile, method="admin", approved_at=timezone.now(), claim_from=("rejected",)
    )
    profile.refresh_from_db()
    assert (
        profile.photo_review_status,
        profile.photo_review_key,
        profile.photo_reviewed_at,
        profile.photo_reviewed_by_id,
    ) == before
    profile.photo_1 = "new.jpg"
    profile.save(update_fields=["photo_1"])
    # A new upload answers a revision request, but never lifts a fake flag.
    assert profile.photo_review_status == (
        "flagged_fake" if decision == "flagged_fake" else "pending"
    )


def test_undo_retracts_exact_notice_and_sends_correction(safe_side_effects):
    coach, profile = _make_coach(), _make_candidate()
    with TestCase.captureOnCommitCallbacks(execute=True):
        result = review(coach, profile)
    assert Notification.objects.filter(
        dedupe_key=f"photo-review:{result['log_id']}:revision"
    ).exists()
    unrelated = Notification.objects.create(
        user=profile.user, notification_type="profile_revision", title="Other feedback"
    )
    with TestCase.captureOnCommitCallbacks(execute=True):
        undo_last_photo_review(coach, log_id=result["log_id"])
    unrelated.refresh_from_db()
    assert unrelated.title == "Other feedback"
    # Exactly one bell entry for this review: the withdrawal, never the request.
    notices = Notification.objects.filter(user=profile.user).exclude(pk=unrelated.pk)
    assert [notice.title for notice in notices] == ["Photo revision request withdrawn"]
    assert notices.get().dedupe_key == f"photo-review:{result['log_id']}:retracted"
    assert [call.args[1] for call in safe_side_effects.call_args_list] == [
        NotificationType.PROFILE_REVISION,
        NotificationType.PHOTO_REVIEW_RETRACTED,
    ]
    assert (
        ProfilePhotoReviewLog.objects.get(
            pk=result["log_id"]
        ).revision_notification_state
        == "retracted"
    )
    # A replayed correction adds no bell row and sends no second email.
    _retract_revision_safely(result["log_id"], None)
    ProfilePhotoReviewLog.objects.filter(pk=result["log_id"]).update(
        revision_notification_state="retract_pending"
    )
    _retract_revision_safely(result["log_id"], None)
    assert list(
        Notification.objects.filter(user=profile.user).exclude(pk=unrelated.pk)
    ) == list(notices)
    assert safe_side_effects.call_count == 2


def test_undo_before_send_cancels_notification(safe_side_effects):
    coach, profile = _make_coach(), _make_candidate()
    with TestCase.captureOnCommitCallbacks(execute=True):
        result = review(coach, profile)
        undo_last_photo_review(coach, log_id=result["log_id"])
    assert not Notification.objects.exists()
    safe_side_effects.assert_not_called()


def test_photo_replacement_before_callback_suppresses_obsolete_notice(
    safe_side_effects,
):
    coach, profile = _make_coach(), _make_candidate()
    with TestCase.captureOnCommitCallbacks(execute=True):
        review(coach, profile)
        profile.photo_1 = "replacement.jpg"
        profile.save(update_fields=["photo_1"])
    assert not Notification.objects.exists()
    safe_side_effects.assert_not_called()


@pytest.mark.parametrize(
    "language,title",
    [
        ("en", "Photo revision request withdrawn"),
        ("de", "Fotoänderung zurückgenommen"),
        ("fr", "Demande de modification de photo retirée"),
    ],
)
def test_correction_email_uses_member_language(language, title):
    from crush_lu.email_helpers import send_photo_review_retracted_notification

    profile = _make_candidate()
    profile.preferred_language = language
    profile.save(update_fields=["preferred_language"])
    with patch("crush_lu.email_helpers.send_domain_email", return_value=1) as sender:
        assert send_photo_review_retracted_notification(profile.user) == 1
    assert sender.call_args.kwargs["subject"] == title
    assert title in sender.call_args.kwargs["html_message"]
    assert sender.call_args.kwargs["domain"] == "crush.lu"


def test_undo_during_send_is_corrected_after_sender_finishes(safe_side_effects):
    coach, profile = _make_coach(), _make_candidate()

    def delivery(user, kind, context, request):
        if kind == NotificationType.PROFILE_REVISION:
            undo_last_photo_review(coach, log_id=context["photo_review_log_id"])
        return True

    safe_side_effects.side_effect = delivery
    with TestCase.captureOnCommitCallbacks(execute=True):
        result = review(coach, profile)
    assert [call.args[1] for call in safe_side_effects.call_args_list] == [
        NotificationType.PROFILE_REVISION,
        NotificationType.PHOTO_REVIEW_RETRACTED,
    ]
    # The sender's notice became the withdrawal; the correction replaced it.
    notices = Notification.objects.filter(user=profile.user)
    assert [(n.title, n.dedupe_key) for n in notices] == [
        (
            "Photo revision request withdrawn",
            f"photo-review:{result['log_id']}:retracted",
        )
    ]


@pytest.mark.parametrize("undo_during_send", [False, True])
def test_undelivered_revision_is_never_retracted(safe_side_effects, undo_during_send):
    coach, profile = _make_coach(), _make_candidate()

    def failing_delivery(**kwargs):
        if undo_during_send:
            undo_last_photo_review(coach, log_id=kwargs["photo_review_log_id"])
        # notify() raises only before its dedupe claim: nothing was delivered.
        raise RuntimeError("Rendering failed before the dedupe claim")

    with (
        patch(
            "crush_lu.services.photo_review.notify_profile_revision",
            side_effect=failing_delivery,
        ),
        TestCase.captureOnCommitCallbacks(execute=True),
    ):
        result = review(coach, profile)
    log = ProfilePhotoReviewLog.objects.get(pk=result["log_id"])
    if not undo_during_send:
        assert log.revision_notification_state == "failed"
        with TestCase.captureOnCommitCallbacks(execute=True):
            undo_last_photo_review(coach, log_id=log.pk)
        log.refresh_from_db()
    assert log.undone_at is not None
    assert log.revision_notification_state == "cancelled"
    assert not Notification.objects.filter(
        dedupe_key=f"photo-review:{log.pk}:retracted"
    ).exists()
    safe_side_effects.assert_not_called()


@pytest.mark.parametrize("decision", ["needs_revision", "flagged_fake"])
def test_generation_pools_exclude_moderated_candidates(settings, decision):
    from crush_lu.services.crush_connect import get_eligible_pool
    from crush_lu.services.connect_cycle import get_cycle_eligible_pool
    from crush_lu.tests.test_crush_connect import _make_user, _set_gate_questions

    settings.CRUSH_CONNECT_LAUNCHED = True
    settings.CRUSH_CONNECT_CYCLE_ENABLED = True
    requester = _make_user(username="requester", gender="M")
    candidate = _make_user(username="candidate", gender="F", premium=False)
    _set_gate_questions(candidate)
    # Establish the positive path so exclusion cannot pass vacuously.
    assert candidate in get_cycle_eligible_pool(requester)
    assert candidate in get_eligible_pool(requester)
    CrushProfile.objects.filter(user=candidate).update(photo_review_status=decision)
    assert candidate not in get_eligible_pool(requester)
    assert candidate not in get_cycle_eligible_pool(requester)
    CrushProfile.objects.filter(user=candidate).update(photo_review_status="pending")
    requester.crushprofile.photo_review_status = decision
    requester.crushprofile.save(update_fields=["photo_review_status"])
    assert not get_cycle_eligible_pool(requester).exists()
    assert not get_eligible_pool(requester).exists()


@pytest.mark.parametrize("decision", ["needs_revision", "flagged_fake"])
def test_lobby_and_encounter_proxies_deny_moderated_photos(
    settings, tmp_path, monkeypatch, decision
):
    from django.core.files.base import ContentFile
    from django.core.files.storage import FileSystemStorage
    from crush_lu.tests.test_event_lobby import (
        _make_member,
        _make_event,
        _join,
        _handle_of,
        _login,
    )
    from crush_lu.tests.test_event_lobby_recap import _make_encounter

    settings.CRUSH_EVENT_LOBBY_ENABLED = True
    settings.CRUSH_CONNECT_LAUNCHED = True
    # This fixture stores real bytes locally; don't take the Azure SAS branch
    # simply because the CI environment supplies a test account name.
    settings.AZURE_ACCOUNT_NAME = ""
    monkeypatch.setattr(
        CrushProfile._meta.get_field("photo_1"),
        "storage",
        FileSystemStorage(location=tmp_path),
    )
    alice, ben = _make_member("alice"), _make_member("ben", gender="M")
    profile = ben.crushprofile
    profile.photo_1.save("ben.jpg", ContentFile(b"photo"), save=True)
    event = _make_event()
    _join(alice, event)
    _join(ben, event)
    _make_encounter(alice, ben)
    lobby_url = reverse(
        "crush_lu:event_lobby_photo",
        kwargs={"event_id": event.pk, "handle": _handle_of(ben, event)},
    )
    person_url = reverse("crush_lu:event_lobby_person_photo", args=[ben.pk])
    client = Client()
    _login(client, alice)
    assert client.get(lobby_url).status_code == 200
    assert client.get(person_url).status_code == 200
    CrushProfile.objects.filter(user=ben).update(photo_review_status=decision)
    assert client.get(lobby_url).status_code == 404
    assert client.get(person_url).status_code == 404
    from crush_lu.services.event_lobby import participant_gate, eligible_participations

    ben.crushprofile.refresh_from_db()
    assert not participant_gate(ben)[0]
    assert not eligible_participations(event).filter(user=ben).exists()
    own_photo_url = reverse("crush_lu:serve_profile_photo", args=[ben.pk, "photo_1"])
    _login(client, ben)
    assert client.get(lobby_url).status_code == 404
    response = client.get(own_photo_url)
    assert response.status_code == 200
    assert response.content == b"photo"
    coach = _make_coach()
    client.force_login(coach.user)
    assert client.get(lobby_url).status_code == 404
    response = client.get(own_photo_url)
    assert response.status_code == 200
    assert response.content == b"photo"
