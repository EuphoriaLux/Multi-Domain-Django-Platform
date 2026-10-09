"""Held photo replacements, the daily upload limit and the member-card deck.

A member's new photo reaches other members only once a coach approves it; the
earlier approved photo stays visible meanwhile. Coaches review one card per
member (claimed, so two coaches never get the same member) and the member gets
one notice naming every photo to replace.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import (
    CrushProfile,
    Notification,
    PhotoReviewClaim,
    ProfilePhotoReviewLog,
    ProfilePhotoUpload,
    PublishedProfilePhoto,
)
from crush_lu.notification_service import NotificationService, NotificationType
from crush_lu.services.photo_publication import (
    get_public_photo_key,
    photo_for_viewer,
    photo_upload_refusal,
)
from crush_lu.services.photo_review import (
    PhotoReviewError,
    get_photo_review_queue,
    submit_member_review,
    undo_last_photo_review,
)
from crush_lu.tests.test_coach_photo_review import _make_candidate, _make_coach

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]

User = get_user_model()


@pytest.fixture(autouse=True)
def deleted_blobs(monkeypatch):
    """Record storage deletes instead of touching files."""
    cache.clear()
    deleted = []
    for field in ("photo_1", "photo_2", "photo_3"):
        monkeypatch.setattr(
            CrushProfile._meta.get_field(field).storage,
            "delete",
            lambda name: deleted.append(name),
        )
    return deleted


@pytest.fixture
def notices():
    with patch("crush_lu.services.photo_review.notify_photo_revision") as notify:
        yield notify


def _viewer():
    user = User.objects.create_user(username="viewer", email="viewer@example.com")
    return user


def _decide(coach, profile, *items):
    return submit_member_review(
        coach,
        profile.pk,
        [
            {
                "photo_field": field,
                "photo_key": getattr(profile, field).name,
                "decision": decision,
                "reason": reason,
            }
            for field, decision, reason in items
        ],
    )


def _replace(profile, field, key):
    setattr(profile, field, key)
    profile.save(update_fields=[field])
    profile.refresh_from_db()


# --- Held replacements -------------------------------------------------------


def test_first_upload_is_hidden_from_members_until_approved():
    coach, profile = _make_coach(), _make_candidate()
    PublishedProfilePhoto.objects.filter(profile=profile).delete()
    viewer = _viewer()

    assert photo_for_viewer(viewer, profile, "photo_1") is None
    assert (
        photo_for_viewer(profile.user, profile, "photo_1").name
        == "users/1/photos/photo1.jpg"
    )
    assert photo_for_viewer(viewer, profile, "photo_1", privileged=True)

    _decide(coach, profile, ("photo_1", "approved", "clear_authentic"))
    profile.refresh_from_db()
    assert photo_for_viewer(viewer, profile, "photo_1").name == profile.photo_1.name


def test_replacement_of_approved_photo_is_held_until_approval(deleted_blobs):
    coach, profile = _make_coach(), _make_candidate()
    _decide(coach, profile, ("photo_1", "approved", "clear_authentic"))
    profile.refresh_from_db()
    old_key = profile.photo_1.name

    _replace(profile, "photo_1", "users/1/photos/new.jpg")
    # The approved file is still published, so saving must not delete it.
    assert old_key not in deleted_blobs
    viewer = _viewer()
    assert photo_for_viewer(viewer, profile, "photo_1").name == old_key
    assert (
        photo_for_viewer(profile.user, profile, "photo_1").name
        == "users/1/photos/new.jpg"
    )

    with TestCase.captureOnCommitCallbacks(execute=True):
        _decide(coach, profile, ("photo_1", "approved", "clear_authentic"))
    profile.refresh_from_db()
    assert get_public_photo_key(profile, "photo_1") == "users/1/photos/new.jpg"
    # The superseded file goes once nothing shows it any more.
    assert old_key in deleted_blobs


def test_refused_replacement_keeps_the_earlier_approved_photo(notices):
    coach, profile = _make_coach(), _make_candidate()
    _decide(coach, profile, ("photo_1", "approved", "clear_authentic"))
    profile.refresh_from_db()
    old_key = profile.photo_1.name
    _replace(profile, "photo_1", "users/1/photos/blurry.jpg")

    _decide(coach, profile, ("photo_1", "needs_revision", "blurry_photo"))
    profile.refresh_from_db()
    # Only the refused file is marked; the member is not paused profile-wide.
    assert profile.get_photo_field_review_status("photo_1") == "needs_revision"
    assert profile.photo_review_status != "needs_revision"
    assert get_public_photo_key(profile, "photo_1") == old_key


def test_member_with_a_refused_replacement_stays_listed(notices):
    from crush_lu.services.crush_connect import (
        filter_primary_photo_review_approved,
        is_catalogue_eligible,
    )
    from crush_lu.services.event_lobby import GATE_PHOTO_REVISION, participant_gate

    coach, profile = _make_coach(), _make_candidate()
    _decide(coach, profile, ("photo_1", "approved", "clear_authentic"))
    profile.refresh_from_db()
    _replace(profile, "photo_1", "users/1/photos/blurry.jpg")
    result = _decide(coach, profile, ("photo_1", "needs_revision", "blurry_photo"))
    user = User.objects.get(pk=profile.user_id)

    listed = filter_primary_photo_review_approved(User.objects.filter(pk=user.pk))
    assert listed.exists()
    assert is_catalogue_eligible(user)
    assert participant_gate(user)[1] != GATE_PHOTO_REVISION

    # Undo still works on a decision that never touched the profile status.
    undo_last_photo_review(coach, log_id=result["log_id"])
    profile.refresh_from_db()
    assert profile.get_photo_field_review_status("photo_1") == "pending"


def test_refused_first_photo_still_pauses_the_member(notices):
    from crush_lu.services.crush_connect import filter_primary_photo_review_approved

    coach, profile = _make_coach(), _make_candidate()
    _decide(coach, profile, ("photo_1", "needs_revision", "unclear_face"))
    profile.refresh_from_db()
    assert profile.photo_review_status == "needs_revision"
    assert not filter_primary_photo_review_approved(
        User.objects.filter(pk=profile.user_id)
    ).exists()


def test_refusing_a_published_photo_hides_it_and_undo_restores_it(notices):
    coach, profile = _make_coach(), _make_candidate()
    key = profile.photo_1.name
    assert get_public_photo_key(profile, "photo_1") == key  # grandfathered

    _decide(coach, profile, ("photo_1", "needs_revision", "unclear_face"))
    profile.refresh_from_db()
    assert get_public_photo_key(profile, "photo_1") == ""

    undo_last_photo_review(coach)
    profile.refresh_from_db()
    assert get_public_photo_key(profile, "photo_1") == key


def test_removing_a_photo_also_removes_its_held_approved_file(deleted_blobs):
    coach, profile = _make_coach(), _make_candidate()
    _decide(coach, profile, ("photo_1", "approved", "clear_authentic"))
    profile.refresh_from_db()
    old_key = profile.photo_1.name
    _replace(profile, "photo_1", "users/1/photos/new.jpg")

    profile.photo_1 = None
    with TestCase.captureOnCommitCallbacks(execute=True):
        profile.save(update_fields=["photo_1"])
    assert old_key in deleted_blobs
    assert "users/1/photos/new.jpg" in deleted_blobs
    assert not PublishedProfilePhoto.objects.filter(profile=profile).exists()


def test_fake_flag_hides_every_published_photo(notices):
    coach, profile = _make_coach(), _make_candidate()
    _decide(coach, profile, ("photo_1", "flagged_fake", "fake_profile"))
    profile.refresh_from_db()
    assert get_public_photo_key(profile, "photo_1") == ""


def test_photo_endpoint_serves_the_published_file_to_members(settings):
    settings.AZURE_ACCOUNT_NAME = ""
    coach, profile = _make_coach(), _make_candidate()
    _decide(coach, profile, ("photo_1", "approved", "clear_authentic"))
    profile.refresh_from_db()
    _replace(profile, "photo_1", "users/1/photos/new.jpg")
    opened = []

    def exists(path):
        if "/photos/" in str(path):
            opened.append(str(path))
        return False

    url = f"/en/media/profile/{profile.user_id}/photo_1/"
    with patch("crush_lu.views_media.can_view_profile_photo", return_value=True), patch(
        "crush_lu.views_media.os.path.exists", side_effect=exists
    ):
        for viewer in (_make_candidate("viewer_member").user, profile.user):
            client = Client()
            client.force_login(viewer)
            client.get(url)
    assert opened[0].endswith("users/1/photos/photo1.jpg")
    assert opened[1].endswith("users/1/photos/new.jpg")


def test_publish_existing_migration_mirrors_old_visibility():
    import importlib

    from django.apps import apps

    migration = importlib.import_module(
        "crush_lu.migrations.0272_publish_existing_profile_photos"
    )
    visible = _make_candidate("visible_member")
    revision = _make_candidate("revision_member", photo_review_status="needs_revision")
    secondary = _make_candidate("secondary_member")
    _replace(secondary, "photo_2", "users/9/photos/second.jpg")
    PublishedProfilePhoto.objects.all().delete()

    migration.publish_existing(apps, None)

    published = set(
        PublishedProfilePhoto.objects.values_list("profile_id", "photo_field")
    )
    assert (visible.pk, "photo_1") in published
    assert (revision.pk, "photo_1") not in published
    # Photos 2 and 3 needed an approval of the exact file before.
    assert (secondary.pk, "photo_2") not in published


# --- Upload limit ------------------------------------------------------------


def test_upload_limit_counts_uploads_per_slot(settings):
    settings.PHOTO_UPLOADS_PER_SLOT_PER_DAY = 2
    profile = _make_candidate()
    _replace(profile, "photo_1", "users/1/photos/a.jpg")
    assert photo_upload_refusal(profile, "photo_1") == ""
    _replace(profile, "photo_1", "users/1/photos/b.jpg")
    refusal = photo_upload_refusal(profile, "photo_1")
    assert "too often today" in refusal
    assert photo_upload_refusal(profile, "photo_2") == ""

    ProfilePhotoUpload.objects.update(created_at=timezone.now() - timedelta(hours=25))
    assert photo_upload_refusal(profile, "photo_1") == ""


def test_htmx_upload_shows_the_limit_to_the_member(settings):
    settings.PHOTO_UPLOADS_PER_SLOT_PER_DAY = 1
    from django.core.files.uploadedfile import SimpleUploadedFile

    profile = _make_candidate()
    _replace(profile, "photo_2", "users/1/photos/a.jpg")
    client = Client()
    client.force_login(profile.user)
    with patch("crush_lu.utils.image_processing.process_uploaded_image") as process:
        response = client.post(
            "/api/profile/upload-photo/2/",
            {"photo_2": SimpleUploadedFile("x.jpg", b"x", content_type="image/jpeg")},
        )
    assert response.status_code == 200
    assert "too often today" in response.content.decode()
    process.assert_not_called()
    profile.refresh_from_db()
    assert profile.photo_2.name == "users/1/photos/a.jpg"


# --- Member-card deck --------------------------------------------------------


def test_queue_deals_one_card_per_member_with_every_waiting_photo():
    coach, profile = _make_coach(), _make_candidate()
    _replace(profile, "photo_2", "users/1/photos/second.jpg")

    cards, total = get_photo_review_queue(coach)
    assert total == 1
    assert [card["id"] for card in cards] == [profile.pk]
    assert cards[0]["pending_fields"] == ["photo_1", "photo_2"]
    assert [photo["label"] for photo in cards[0]["photos"]] == ["Main photo", "Photo 2"]


def test_two_coaches_never_get_the_same_member():
    coach_a, coach_b = _make_coach("coach_a"), _make_coach("coach_b")
    first, second = _make_candidate("first_member"), _make_candidate("second_member")

    cards_a, _total = get_photo_review_queue(coach_a, limit=1)
    cards_b, total_b = get_photo_review_queue(coach_b, limit=5)
    assert len(cards_a) == 1
    assert {card["id"] for card in cards_b} == {first.pk, second.pk} - {
        cards_a[0]["id"]
    }
    assert total_b == 1

    # A claim that ran out lets the member be dealt again.
    PhotoReviewClaim.objects.filter(coach=coach_a).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    cards_b, total_b = get_photo_review_queue(coach_b, limit=5)
    assert {card["id"] for card in cards_b} == {first.pk, second.pk}


def test_decision_on_a_member_another_coach_holds_is_refused():
    coach_a, coach_b = _make_coach("coach_a"), _make_coach("coach_b")
    profile = _make_candidate()
    get_photo_review_queue(coach_a)
    with pytest.raises(PhotoReviewError) as refused:
        _decide(coach_b, profile, ("photo_1", "approved", "clear_authentic"))
    assert refused.value.status == 409
    assert not ProfilePhotoReviewLog.objects.exists()


def test_deciding_releases_the_claim():
    coach = _make_coach()
    profile = _make_candidate()
    get_photo_review_queue(coach)
    _decide(coach, profile, ("photo_1", "approved", "clear_authentic"))
    assert not PhotoReviewClaim.objects.filter(profile=profile).exists()


def test_card_sends_one_notice_naming_every_photo(notices):
    coach, profile = _make_coach(), _make_candidate()
    _replace(profile, "photo_2", "users/1/photos/second.jpg")
    _replace(profile, "photo_3", "users/1/photos/third.jpg")
    with TestCase.captureOnCommitCallbacks(execute=True):
        result = _decide(
            coach,
            profile,
            ("photo_1", "needs_revision", "blurry_photo"),
            ("photo_2", "approved", "clear_authentic"),
            ("photo_3", "needs_revision", "group_photo"),
        )
    assert result["decisions"] == {
        "photo_1": "needs_revision",
        "photo_2": "approved",
        "photo_3": "needs_revision",
    }
    notices.assert_called_once()
    kwargs = notices.call_args.kwargs
    assert kwargs["photos"] == [
        {"photo_field": "photo_1", "reason": "blurry_photo"},
        {"photo_field": "photo_3", "reason": "group_photo"},
    ]
    lead = min(
        ProfilePhotoReviewLog.objects.filter(decision="needs_revision").values_list(
            "pk", flat=True
        )
    )
    assert kwargs["photo_review_log_id"] == lead
    assert set(
        ProfilePhotoReviewLog.objects.filter(decision="needs_revision").values_list(
            "revision_notification_state", flat=True
        )
    ) == {"sent"}


def test_undo_reverts_the_whole_card_and_withdraws_the_notice_once():
    coach, profile = _make_coach(), _make_candidate()
    _replace(profile, "photo_2", "users/1/photos/second.jpg")
    with patch.object(NotificationService, "_send_email", return_value=True), patch(
        "crush_lu.email_helpers.can_send_email", return_value=True
    ):
        with TestCase.captureOnCommitCallbacks(execute=True):
            result = _decide(
                coach,
                profile,
                ("photo_1", "needs_revision", "blurry_photo"),
                ("photo_2", "needs_revision", "unclear_face"),
            )
        notice = Notification.objects.get(user=profile.user)
        assert notice.title == "Please replace 2 of your profile photos"
        assert "Main profile photo" not in notice.body
        assert "main profile photo (Blurry or low-quality photo)" in notice.body
        assert "second profile photo (Face unclear or covered)" in notice.body
        assert notice.link_url == "/profile/edit/?section=photos"

        with TestCase.captureOnCommitCallbacks(execute=True):
            undone = undo_last_photo_review(coach, log_id=result["log_id"])
    assert undone["undone_decisions"] == {
        "photo_1": "needs_revision",
        "photo_2": "needs_revision",
    }
    profile.refresh_from_db()
    assert profile.get_photo_field_review_status("photo_1") == "pending"
    assert profile.get_photo_field_review_status("photo_2") == "pending"
    assert set(
        ProfilePhotoReviewLog.objects.values_list(
            "revision_notification_state", flat=True
        )
    ) == {"retracted"}
    assert Notification.objects.filter(user=profile.user).count() == 1
    assert Notification.objects.get(user=profile.user).notification_type == (
        "photo_review_retracted"
    )


def test_fake_flag_cannot_be_combined_with_other_decisions():
    coach, profile = _make_coach(), _make_candidate()
    _replace(profile, "photo_2", "users/1/photos/second.jpg")
    with pytest.raises(PhotoReviewError) as refused:
        _decide(
            coach,
            profile,
            ("photo_1", "flagged_fake", "fake_profile"),
            ("photo_2", "approved", "clear_authentic"),
        )
    assert refused.value.status == 400
    assert not ProfilePhotoReviewLog.objects.exists()


def test_decide_endpoint_accepts_a_member_card(notices):
    coach, profile = _make_coach(), _make_candidate()
    _replace(profile, "photo_2", "users/1/photos/second.jpg")
    client = Client()
    client.force_login(coach.user)
    response = client.post(
        reverse("crush_lu:coach_photo_review_decide"),
        {
            "profile_id": profile.pk,
            "decisions": [
                {
                    "photo_field": "photo_1",
                    "photo_key": profile.photo_1.name,
                    "decision": "approved",
                    "reason": "clear_authentic",
                },
                {
                    "photo_field": "photo_2",
                    "photo_key": profile.photo_2.name,
                    "decision": "needs_revision",
                    "reason": "unclear_face",
                },
            ],
        },
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    assert response.json()["decisions"] == {
        "photo_1": "approved",
        "photo_2": "needs_revision",
    }


def test_revision_email_lists_each_photo_with_its_reason():
    from crush_lu.email_helpers import send_photo_revision_request

    profile = _make_candidate()
    with patch("crush_lu.email_helpers.send_domain_email", return_value=1) as sender:
        assert (
            send_photo_revision_request(
                profile.user,
                photos=[
                    {"photo_field": "photo_1", "reason": "blurry_photo"},
                    {"photo_field": "photo_3", "reason": "group_photo"},
                ],
            )
            == 1
        )
    kwargs = sender.call_args.kwargs
    assert kwargs["subject"] == "Please replace 2 of your profile photos"
    html = kwargs["html_message"]
    assert "Main profile photo: Blurry or low-quality photo" in html
    assert "Third profile photo: Group photo / cannot identify member" in html
    assert "?section=photos" in html


def test_edit_page_marks_the_photo_to_replace(notices):
    coach, profile = _make_coach(), _make_candidate()
    _replace(profile, "photo_2", "users/1/photos/second.jpg")
    _decide(
        coach,
        profile,
        ("photo_1", "approved", "clear_authentic"),
        ("photo_2", "needs_revision", "heavy_filter"),
    )
    _replace(profile, "photo_1", "users/1/photos/newer.jpg")
    client = Client()
    client.force_login(profile.user)
    html = client.get("/en/profile/edit/?section=photos").content.decode()
    assert "Needs replacement: Heavy filters or altered appearance" in html
    assert "Other members keep seeing your previous approved photo" in html


def test_revision_notice_link_resolves():
    from django.urls import resolve

    payload = NotificationService._render_inapp_payload(
        _make_candidate().user,
        NotificationType.PHOTO_REVISION,
        {"photo_review_log_id": 1, "photos": [{"photo_field": "photo_2"}]},
        None,
    )
    path = payload["link_url"].split("?")[0]
    assert resolve(f"/en{path}", "azureproject.urls_crush").url_name == "edit_profile"


# --- Review follow-ups ---------------------------------------------------------


def test_quiz_projects_the_approved_photo_while_a_replacement_is_refused(
    notices, settings
):
    from crush_lu.views_quiz import _photo_url

    coach, profile = _make_coach(), _make_candidate()
    _decide(coach, profile, ("photo_1", "approved", "clear_authentic"))
    profile.refresh_from_db()
    old_key = profile.photo_1.name
    _replace(profile, "photo_1", "users/1/photos/refused.jpg")
    _decide(coach, profile, ("photo_1", "needs_revision", "blurry_photo"))
    profile.refresh_from_db()

    assert _photo_url(profile) == f"/api/quiz/photo/{profile.user_id}/"
    viewer = _make_candidate("quiz_viewer").user
    assert photo_for_viewer(viewer, profile, "photo_1").name == old_key


def test_social_import_keeps_the_published_file(deleted_blobs, monkeypatch):
    from types import SimpleNamespace

    from crush_lu import social_photos

    coach, profile = _make_coach(), _make_candidate()
    _decide(coach, profile, ("photo_1", "approved", "clear_authentic"))
    profile.refresh_from_db()
    old_key = profile.photo_1.name

    monkeypatch.setattr(
        social_photos,
        "get_social_photo_url",
        lambda account: "data:image/jpeg;base64,anBlZw==",
    )
    monkeypatch.setattr(
        social_photos, "process_uploaded_image", lambda raw, name=None: raw
    )
    stored = []

    def fake_save(self, name, content, save=True):
        self.name = f"users/1/photos/{name}"
        stored.append(self.name)

    monkeypatch.setattr(type(profile.photo_1), "save", fake_save)
    account = SimpleNamespace(provider="google", user=profile.user)
    result = social_photos.download_and_save_social_photo(profile.user, account, 1)
    assert result["success"], result

    profile.refresh_from_db()
    assert stored and profile.photo_1.name == stored[0]
    assert old_key not in deleted_blobs
    assert get_public_photo_key(profile, "photo_1") == old_key


def test_failed_save_does_not_use_up_the_upload_limit(monkeypatch):
    from django.db import models as dj_models

    profile = _make_candidate()
    before = ProfilePhotoUpload.objects.count()

    def boom(self, *args, **kwargs):
        raise RuntimeError("database down")

    monkeypatch.setattr(dj_models.Model, "save", boom)
    profile.photo_2 = "users/1/photos/never-saved.jpg"
    with pytest.raises(RuntimeError):
        profile.save(update_fields=["photo_2"])
    monkeypatch.undo()
    assert ProfilePhotoUpload.objects.count() == before


# --- Second review round -------------------------------------------------------


def test_member_with_a_photo_in_review_cannot_see_others_either():
    from crush_lu.services.crush_connect import is_premium_connect_eligible
    from crush_lu.services.event_lobby import GATE_PHOTO_IN_REVIEW, participant_gate
    from crush_lu.services.photo_publication import primary_photo_in_review

    profile = _make_candidate()
    PublishedProfilePhoto.objects.filter(profile=profile).delete()
    user = User.objects.get(pk=profile.user_id)

    assert primary_photo_in_review(profile)
    assert participant_gate(user) == (False, GATE_PHOTO_IN_REVIEW)
    assert not is_premium_connect_eligible(user)

    coach = _make_coach()
    _decide(coach, profile, ("photo_1", "approved", "clear_authentic"))
    profile.refresh_from_db()
    assert not primary_photo_in_review(profile)
    assert participant_gate(User.objects.get(pk=profile.user_id))[1] != (
        GATE_PHOTO_IN_REVIEW
    )


def test_photo_tag_renders_the_fallback_for_an_unapproved_photo():
    from django.template import Context, Template
    from django.test import RequestFactory

    profile = _make_candidate()
    PublishedProfilePhoto.objects.filter(profile=profile).delete()
    template = Template(
        "{% load crush_media %}{% profile_photo profile 'photo_1' %}"
        "|{% if profile|has_public_photo:'photo_1' %}yes{% else %}no{% endif %}"
    )

    def render(user):
        request = RequestFactory().get("/")
        request.user = user
        return template.render(Context({"profile": profile, "request": request}))

    other = render(_make_candidate("other_member").user)
    assert "/media/profile/" not in other
    assert other.endswith("|no")
    assert "/media/profile/" in render(profile.user)


def test_removing_a_photo_keeps_the_approved_file_if_the_save_fails(
    deleted_blobs, monkeypatch
):
    from django.db import models as dj_models

    coach, profile = _make_coach(), _make_candidate()
    _decide(coach, profile, ("photo_1", "approved", "clear_authentic"))
    profile.refresh_from_db()
    old_key = profile.photo_1.name
    _replace(profile, "photo_1", "users/1/photos/new.jpg")

    def boom(self, *args, **kwargs):
        raise RuntimeError("database down")

    monkeypatch.setattr(dj_models.Model, "save", boom)
    profile.photo_1 = None
    with pytest.raises(RuntimeError):
        profile.save(update_fields=["photo_1"])
    monkeypatch.undo()
    assert old_key not in deleted_blobs
    assert PublishedProfilePhoto.objects.filter(photo_key=old_key).exists()


def test_projector_shows_the_approved_photo_to_its_owner_too(settings):
    settings.AZURE_ACCOUNT_NAME = ""
    coach, profile = _make_coach(), _make_candidate()
    _decide(coach, profile, ("photo_1", "approved", "clear_authentic"))
    profile.refresh_from_db()
    _replace(profile, "photo_1", "users/1/photos/new.jpg")
    opened = []

    def exists(path):
        if "/photos/" in str(path):
            opened.append(str(path))
        return False

    with patch("crush_lu.views_quiz._can_view_quiz_photo", return_value=True), patch(
        "crush_lu.views_quiz.os.path.exists", side_effect=exists
    ):
        client = Client()
        client.force_login(profile.user)
        client.get(f"/api/quiz/photo/{profile.user_id}/")
    assert opened and opened[0].endswith("users/1/photos/photo1.jpg")


def test_gallery_keeps_an_approved_secondary_photo_while_its_replacement_waits():
    coach, profile = _make_coach(), _make_candidate()
    _replace(profile, "photo_2", "users/1/photos/second.jpg")
    _decide(coach, profile, ("photo_2", "approved", "clear_authentic"))
    profile.refresh_from_db()
    assert profile.get_coach_reviewed_secondary_photo_fields() == ["photo_2"]

    _replace(profile, "photo_2", "users/1/photos/second-new.jpg")
    assert profile.get_coach_reviewed_secondary_photo_fields() == ["photo_2"]
    assert get_public_photo_key(profile, "photo_2") == "users/1/photos/second.jpg"


# --- Third review round --------------------------------------------------------


def test_lifting_a_fake_flag_sends_the_photo_back_to_review_unpublished():
    from crush_lu.admin.profiles import CrushProfileAdmin
    from crush_lu.admin.site import crush_admin_site
    from crush_lu.tests.test_coach_photo_review_round4 import _staff_request

    coach, profile = _make_coach(), _make_candidate()
    assert get_public_photo_key(profile, "photo_1")  # published before the flag
    _decide(coach, profile, ("photo_1", "flagged_fake", "fake_profile"))

    CrushProfileAdmin(CrushProfile, crush_admin_site).lift_photo_review_moderation(
        _staff_request(), CrushProfile.objects.filter(pk=profile.pk)
    )
    profile.refresh_from_db()
    assert profile.photo_review_status == "pending"
    assert get_public_photo_key(profile, "photo_1") == ""
    cards, _total = get_photo_review_queue(coach)
    assert [card["id"] for card in cards] == [profile.pk]


def test_a_decision_releases_an_expired_claim_of_another_coach():
    coach_a, coach_b = _make_coach("coach_a"), _make_coach("coach_b")
    profile = _make_candidate()
    get_photo_review_queue(coach_a)
    PhotoReviewClaim.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

    _decide(coach_b, profile, ("photo_1", "approved", "clear_authentic"))
    assert not PhotoReviewClaim.objects.filter(profile=profile).exists()
    # Nothing left to deal, for either coach.
    assert get_photo_review_queue(coach_a)[0] == []
