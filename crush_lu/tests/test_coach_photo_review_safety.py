"""Moderation refuses stale/unauthorized actions and preserves newer decisions."""

import json
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.db import transaction
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from crush_lu.admin.moderation import ProfilePhotoReviewLogAdmin
from crush_lu.admin.site import crush_admin_site
from crush_lu.models import (
    ConnectCoachPick,
    CrushConnectMembership,
    CrushProfile,
    ProfilePhotoReviewLog,
    UserDataConsent,
)
from crush_lu.services.photo_review import (
    PhotoReviewError,
    get_photo_review_queue,
    submit_photo_review,
    undo_last_photo_review,
)
from crush_lu.tests.test_coach_photo_review import _make_candidate, _make_coach

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]


@pytest.fixture(autouse=True)
def isolate_review_side_effects(monkeypatch):
    cache.clear()
    monkeypatch.setattr(
        CrushProfile._meta.get_field("photo_1").storage, "delete", lambda name: None
    )
    with patch(
        "crush_lu.services.photo_review.notify_profile_revision"
    ) as notification:
        yield notification


def _review(coach, profile, decision="approved", **kwargs):
    return submit_photo_review(
        coach, profile.pk, decision, photo_key=profile.photo_1.name, **kwargs
    )


def _post(client, profile, **overrides):
    payload = {
        "profile_id": profile.pk,
        "photo_key": profile.photo_1.name,
        "decision": "approved",
    }
    payload.update(overrides)
    return client.post(
        reverse("crush_lu:coach_photo_review_decide"),
        data=json.dumps(payload),
        content_type="application/json",
    )


def test_replaced_photo_rejects_stale_decision():
    coach, profile = _make_coach(), _make_candidate(photo_key="old.jpg")
    profile.photo_1 = "replacement.jpg"
    profile.save(update_fields=["photo_1"])
    client = Client()
    client.force_login(coach.user)
    assert _post(client, profile, photo_key="old.jpg").status_code == 409
    profile.refresh_from_db()
    assert not profile.is_photo_review_approved
    assert not ProfilePhotoReviewLog.objects.exists()


@pytest.mark.parametrize(
    "target_state",
    [
        "self",
        "banned",
        "no_consent",
        "inactive_user",
        "inactive_profile",
        "rejected",
        "no_photo",
    ],
)
def test_decision_refuses_ineligible_target(target_state):
    coach, profile = _make_coach(), _make_candidate()
    if target_state == "self":
        profile = CrushProfile.objects.create(
            user=coach.user, photo_1="coach.jpg", is_active=True
        )
    elif target_state == "banned":
        UserDataConsent.objects.filter(user=profile.user).update(crushlu_banned=True)
    elif target_state == "no_consent":
        UserDataConsent.objects.filter(user=profile.user).delete()
    elif target_state == "inactive_user":
        profile.user.is_active = False
        profile.user.save(update_fields=["is_active"])
    elif target_state == "inactive_profile":
        CrushProfile.objects.filter(pk=profile.pk).update(is_active=False)
    elif target_state == "rejected":
        CrushProfile.objects.filter(pk=profile.pk).update(
            verification_status="rejected"
        )
    elif target_state == "no_photo":
        profile.photo_1 = ""
        profile.save(update_fields=["photo_1"])
    client = Client()
    client.force_login(coach.user)
    assert _post(client, profile).status_code in (400, 403)
    profile.refresh_from_db()
    assert profile.photo_review_status == "pending"
    assert not ProfilePhotoReviewLog.objects.exists()


def test_queue_excludes_banned_nonconsenting_and_unchanged_revision():
    coach = _make_coach()
    banned, no_consent = _make_candidate("banned"), _make_candidate("no_consent")
    revision = _make_candidate("revision", photo_review_status="needs_revision")
    UserDataConsent.objects.filter(user=banned.user).update(crushlu_banned=True)
    UserDataConsent.objects.filter(user=no_consent.user).update(
        crushlu_consent_given=False
    )
    cards, total = get_photo_review_queue(coach)
    assert cards == [] and total == 0
    revision.photo_1 = "new.jpg"
    revision.save(update_fields=["photo_1"])
    cards, total = get_photo_review_queue(coach)
    assert [card["id"] for card in cards] == [revision.pk]
    assert total == 1


def test_queue_handles_optional_empty_location():
    coach, profile = _make_coach(), _make_candidate()
    CrushProfile.objects.filter(pk=profile.pk).update(location="")
    cards, _ = get_photo_review_queue(coach)
    assert cards[0]["location"] == ""


def test_queue_has_bounded_verification_queries_and_excludes_coach_total(
    django_assert_max_num_queries,
):
    coach = _make_coach()
    CrushProfile.objects.create(user=coach.user, photo_1="coach.jpg")
    for number in range(30):
        _make_candidate(f"queued_{number}", has_luxid=number % 2 == 0)
    with django_assert_max_num_queries(3):
        cards, total = get_photo_review_queue(coach, limit=30)
    assert len(cards) == total == 30
    assert sum(card["is_luxid_verified"] for card in cards) == 15
    assert not any(card["has_attended_event"] for card in cards)


@pytest.mark.parametrize("decision", ["approved", "needs_revision", "flagged_fake"])
def test_unrelated_stale_save_preserves_review(decision):
    coach, profile = _make_coach(), _make_candidate()
    stale = CrushProfile.objects.get(pk=profile.pk)
    _review(coach, profile, decision)
    stale.bio = "An unrelated update"
    stale.save()
    profile.refresh_from_db()
    assert profile.photo_review_status == decision
    assert profile.photo_reviewed_by_id == coach.pk
    assert profile.photo_review_key == profile.photo_1.name


def test_second_coach_cannot_override_reviewed_photo():
    coach_a, coach_b = _make_coach("coach_a"), _make_coach("coach_b")
    profile = _make_candidate()
    _review(coach_a, profile)
    with pytest.raises(PhotoReviewError):
        _review(coach_b, profile, "flagged_fake")
    assert ProfilePhotoReviewLog.objects.count() == 1


def test_review_committing_between_save_read_and_write_is_preserved(monkeypatch):
    from django.db.models.query import QuerySet

    coach, profile = _make_coach(), _make_candidate()
    stale = CrushProfile.objects.get(pk=profile.pk)
    original_update = QuerySet._update
    decision_committed = False

    def interleaved_update(queryset, values, *args, **kwargs):
        nonlocal decision_committed
        if queryset.model is CrushProfile and not decision_committed:
            decision_committed = True
            _review(coach, profile, "needs_revision")
        return original_update(queryset, values, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "_update", interleaved_update)
    stale.bio = "An unrelated update racing a moderation decision"
    stale.save()
    profile.refresh_from_db()
    assert decision_committed
    assert profile.photo_review_status == "needs_revision"
    assert profile.photo_reviewed_by_id == coach.pk
    assert profile.photo_review_key == profile.photo_1.name


@pytest.mark.parametrize(
    "language,word", [("de", "unangemessene"), ("fr", "inappropriée")]
)
def test_revision_default_feedback_uses_member_language(
    isolate_review_side_effects, language, word
):
    from django.test import TestCase

    coach, profile = _make_coach(), _make_candidate()
    profile.preferred_language = language
    profile.save(update_fields=["preferred_language"])
    with TestCase.captureOnCommitCallbacks(execute=True):
        _review(coach, profile, "needs_revision", reason="inappropriate")
    assert word in isolate_review_side_effects.call_args.kwargs["feedback"]


@pytest.mark.parametrize("door", [True, False])
def test_identity_rejection_invalidates_old_photo_review(door):
    from crush_lu.services.profile_verification import (
        reject_door_verification,
        transition_unverified_profile,
    )

    coach, profile = _make_coach(), _make_candidate(is_approved=door)
    _review(coach, profile)
    transition = reject_door_verification if door else transition_unverified_profile
    assert transition(profile, target_status="rejected")
    profile.refresh_from_db()
    assert profile.photo_review_status == "pending"
    assert not profile.photo_review_key
    assert not profile.is_photo_review_approved
    assert ProfilePhotoReviewLog.objects.count() == 1


def test_undo_refuses_newer_decision_even_by_same_coach():
    coach, profile = _make_coach(), _make_candidate()
    result = _review(coach, profile)
    # Another moderation path can supersede the review without changing the file.
    CrushProfile.objects.filter(pk=profile.pk).update(
        photo_review_status="needs_revision", photo_reviewed_at=timezone.now()
    )
    with pytest.raises(PhotoReviewError):
        undo_last_photo_review(coach, log_id=result["log_id"])
    profile.refresh_from_db()
    assert profile.photo_review_status == "needs_revision"


def test_undo_refuses_replaced_photo():
    coach, profile = _make_coach(), _make_candidate()
    _review(coach, profile)
    profile.refresh_from_db()
    profile.photo_1 = "new.jpg"
    profile.save(update_fields=["photo_1"])
    with pytest.raises(PhotoReviewError):
        undo_last_photo_review(coach)


def test_undo_retains_audit_and_dismisses_its_report():
    coach, profile = _make_coach(), _make_candidate()
    result = _review(coach, profile, "flagged_fake")
    undo_last_photo_review(coach, log_id=result["log_id"])
    log = ProfilePhotoReviewLog.objects.get(pk=result["log_id"])
    assert log.undone_at is not None
    assert log.report.status == "dismissed"
    assert not CrushConnectMembership.objects.get(user=profile.user).excluded_by_coach
    with pytest.raises(PhotoReviewError):
        undo_last_photo_review(coach, log_id=log.pk)


def test_undo_preserves_independent_exclusion():
    coach, profile = _make_coach(), _make_candidate()
    CrushConnectMembership.objects.filter(user=profile.user).update(
        excluded_by_coach=True, exclusion_reason="Independent safety decision"
    )
    _review(coach, profile, "flagged_fake")
    undo_last_photo_review(coach)
    membership = CrushConnectMembership.objects.get(user=profile.user)
    assert membership.excluded_by_coach
    assert membership.exclusion_reason == "Independent safety decision"


def _make_pick(coach, profile, status="proposed"):
    member = _make_candidate("pick_member")
    member.assigned_coach = coach
    member.save(update_fields=["assigned_coach"])
    return ConnectCoachPick.objects.create(
        coach=coach,
        member=member.user,
        candidate=profile.user,
        status=status,
        responded_at=timezone.now() if status == "accepted" else None,
    )


@pytest.mark.parametrize("status", ["proposed", "accepted"])
def test_undo_restores_only_its_withdrawn_pick(status):
    coach, profile = _make_coach(), _make_candidate()
    pick = _make_pick(coach, profile, status)
    previous_response = pick.responded_at
    result = _review(coach, profile, "flagged_fake")
    pick.refresh_from_db()
    assert pick.status == "withdrawn"
    undo_last_photo_review(coach, log_id=result["log_id"])
    pick.refresh_from_db()
    assert pick.status == status
    assert pick.responded_at == previous_response


@pytest.mark.parametrize("later_action", ["withdraw", "block", "ban", "new_pick"])
def test_undo_does_not_revive_picks_superseded_by_safety_or_new_proposal(later_action):
    from crush_lu.models import UserBlock

    coach, profile = _make_coach(), _make_candidate()
    pick = _make_pick(coach, profile)
    _review(coach, profile, "flagged_fake")
    if later_action == "withdraw":
        ConnectCoachPick.objects.filter(pk=pick.pk).update(responded_at=timezone.now())
    elif later_action == "block":
        UserBlock.objects.create(blocker=pick.member, blocked=profile.user)
    elif later_action == "ban":
        UserDataConsent.objects.filter(user=pick.member).update(crushlu_banned=True)
    else:
        candidate = _make_candidate("replacement_pick")
        ConnectCoachPick.objects.create(
            coach=coach, member=pick.member, candidate=candidate.user
        )
    undo_last_photo_review(coach)
    pick.refresh_from_db()
    assert pick.status == "withdrawn"


def test_undo_refuses_exclusion_superseded_by_other_coach():
    coach, other = _make_coach(), _make_coach("other")
    profile = _make_candidate()
    _review(coach, profile, "flagged_fake")
    CrushConnectMembership.objects.filter(user=profile.user).update(
        excluded_by=other, exclusion_reason="Later safety decision"
    )
    with pytest.raises(PhotoReviewError):
        undo_last_photo_review(coach)
    assert CrushConnectMembership.objects.get(user=profile.user).excluded_by_coach


def test_revision_notifies_after_commit_and_uses_reason(isolate_review_side_effects):
    coach, profile = _make_coach(), _make_candidate()
    with transaction.atomic():
        _review(coach, profile, "needs_revision", reason="inappropriate")
        isolate_review_side_effects.assert_not_called()
    # pytest's outer transaction has not committed: explicitly execute new hooks.
    from django.test import TestCase

    with TestCase.captureOnCommitCallbacks(execute=True):
        profile.photo_1 = "replacement.jpg"
        profile.save(update_fields=["photo_1"])
        _review(coach, profile, "needs_revision", reason="inappropriate")
    isolate_review_side_effects.assert_called_once()
    assert "inappropriate" in isolate_review_side_effects.call_args.kwargs["feedback"]


def test_revision_rollback_sends_no_notification(isolate_review_side_effects):
    coach, profile = _make_coach(), _make_candidate()
    with pytest.raises(RuntimeError), transaction.atomic():
        _review(coach, profile, "needs_revision")
        raise RuntimeError("Rollback the decision")
    isolate_review_side_effects.assert_not_called()
    profile.refresh_from_db()
    assert profile.photo_review_status == "pending"


@pytest.mark.parametrize(
    "payload",
    [
        {"notes": "x" * 256},
        {"notes": []},
        {"decision": None},
        {"reason": "unknown"},
        {"photo_key": ""},
    ],
)
def test_invalid_payload_is_safe_400(payload):
    coach, profile = _make_coach(), _make_candidate()
    client = Client()
    client.force_login(coach.user)
    assert _post(client, profile, **payload).status_code == 400
    assert not ProfilePhotoReviewLog.objects.exists()


def test_errors_never_expose_exception_details():
    coach, profile = _make_coach(), _make_candidate()
    client = Client()
    client.force_login(coach.user)
    with patch(
        "crush_lu.views_coach_photos.submit_photo_review",
        side_effect=RuntimeError("SECRET database internals"),
    ):
        response = _post(client, profile)
    assert response.status_code == 500
    assert "SECRET" not in response.content.decode()
    with patch(
        "crush_lu.views_coach_photos.undo_last_photo_review",
        side_effect=RuntimeError("SECRET database internals"),
    ):
        response = client.post(reverse("crush_lu:coach_photo_review_undo"))
    assert response.status_code == 500
    assert "SECRET" not in response.content.decode()


def test_deck_escapes_member_script_markup():
    coach, profile = _make_coach(), _make_candidate()
    profile.user.first_name = "</script><script src=//x.y>"
    profile.user.save(update_fields=["first_name"])
    client = Client()
    client.force_login(coach.user)
    html = client.get(reverse("crush_lu:coach_photo_review_deck")).content.decode()
    assert "<script src=//x.y>" not in html
    assert "\\u003C/script\\u003E" in html
    assert 'x-data="photoSwipeDeck"' in html
    assert "coach.min.js" in html


def test_audit_admin_cannot_mutate_records():
    coach = _make_coach()
    request = RequestFactory().get("/")
    request.user = coach.user
    admin = ProfilePhotoReviewLogAdmin(ProfilePhotoReviewLog, crush_admin_site)
    assert not admin.has_add_permission(request)
    assert not admin.has_change_permission(request)
    assert not admin.has_delete_permission(request)


def test_chat_keeps_conversation_but_hides_moderated_photo():
    from crush_lu.tests.test_connect_chat_flows import _make_open_chat
    from crush_lu.views_connect_chat import (
        _may_view_partner_photo,
        _participants_available,
    )
    from crush_lu.views_media import can_view_profile_photo

    me, partner, chat = _make_open_chat()
    CrushProfile.objects.filter(user=partner).update(
        photo_review_status="needs_revision"
    )
    partner.crushprofile.refresh_from_db()
    assert _participants_available(chat)
    assert not _may_view_partner_photo(me, partner)
    assert not can_view_profile_photo(me, partner.crushprofile)
