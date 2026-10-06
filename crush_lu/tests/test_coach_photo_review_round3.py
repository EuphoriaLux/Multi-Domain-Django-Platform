"""Queue traversal and reprocessing must preserve safety decisions."""

from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.test import Client, TestCase
from django.urls import reverse
import pytest

from crush_lu.models import CrushProfile, ProfilePhotoReviewLog
from crush_lu.services.photo_review import get_photo_review_queue, submit_photo_review
from crush_lu.tests.test_coach_photo_review import _make_candidate, _make_coach

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]


def test_cursor_reaches_profiles_beyond_unreviewed_initial_batch():
    coach = _make_coach()
    profiles = [
        _make_candidate(username=f"queued_{i}", onboarded=i % 2 == 0) for i in range(35)
    ]
    first, total = get_photo_review_queue(coach, limit=30)
    assert total == 35
    client = Client()
    client.force_login(coach.user)
    response = client.get(
        reverse("crush_lu:coach_photo_review_more"),
        {"cursor": first[-1]["queue_cursor"]},
    )
    assert response.status_code == 200
    second = response.json()["cards"]
    assert len(second) == 5
    assert not ({c["id"] for c in first} & {c["id"] for c in second})
    assert {c["id"] for c in first + second} == {p.pk for p in profiles}
    assert response.json()["total_waiting"] == 35
    assert not ProfilePhotoReviewLog.objects.exists()
    assert not get_photo_review_queue(coach, cursor=second[-1]["queue_cursor"])[0]


def test_queue_refuses_tampered_cursor():
    coach = _make_coach()
    client = Client()
    client.force_login(coach.user)
    response = client.get(
        reverse("crush_lu:coach_photo_review_more"), {"cursor": "forged"}
    )
    assert response.status_code == 400


@pytest.mark.parametrize("decision", ["needs_revision", "flagged_fake"])
@pytest.mark.parametrize("during_processing", [False, True])
def test_reprocessing_does_not_clear_negative_moderation(
    tmp_path, monkeypatch, decision, during_processing
):
    from crush_lu.management.commands.reprocess_photos import Command

    storage = FileSystemStorage(location=tmp_path)
    monkeypatch.setattr(CrushProfile._meta.get_field("photo_1"), "storage", storage)
    monkeypatch.setattr(
        "crush_lu.services.photo_review._notify_revision_safely", lambda *args: None
    )
    coach, profile = _make_coach(), _make_candidate(has_photo=False)
    profile.photo_1.save("original.jpg", ContentFile(b"original" * 1000), save=True)
    old_key = profile.photo_1.name

    def moderate():
        submit_photo_review(
            coach,
            profile.pk,
            decision,
            "fake_profile" if decision == "flagged_fake" else "unclear_face",
            photo_key=old_key,
        )

    calls = []

    def process(*args):
        calls.append(True)
        moderate()
        return ContentFile(b"small", name="processed.jpg")

    monkeypatch.setattr(
        "crush_lu.management.commands.reprocess_photos.process_uploaded_image", process
    )
    if not during_processing:
        moderate()
    stats = {"processed": 0, "skipped": 0, "errors": 0}
    with TestCase.captureOnCommitCallbacks(execute=True):
        Command()._process_photo(profile, "photo_1", profile.photo_1, False, stats)
    profile.refresh_from_db()
    assert profile.photo_1.name == old_key
    assert profile.photo_review_key == old_key
    assert profile.photo_review_status == decision
    assert stats == {"processed": 0, "skipped": 1, "errors": 0}
    assert bool(calls) == during_processing
    assert storage.exists(old_key)
    assert len(list(tmp_path.rglob("*.jpg"))) == 1
