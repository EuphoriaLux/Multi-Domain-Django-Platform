"""A member who is not verified yet can still replace their photos.

Coaches review a pending member's photos like anyone's, and the
replace-your-photo email, the in-app notice and the Connect/lobby photo gates
all link to ``/profile/edit/?section=photos``. That page used to serve only
verified members, so a pending member was bounced to the status page with no
way to change the photo. Literal paths throughout: ``reverse()`` resolves
against the default urlconf, not the crush.lu one (see AGENTS.md).
"""

import io
from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from crush_lu.models import CrushProfile, ProfilePhotoUpload, ProfileSubmission
from crush_lu.models.profiles import UserDataConsent

pytestmark = pytest.mark.urls("azureproject.urls_crush")

User = get_user_model()

PHOTOS_URL = "/en/profile/edit/?section=photos"


def setup_function(_function):
    cache.clear()


def _jpeg(name="photo.jpg", color="red"):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (400, 400), color=color).save(buf, format="JPEG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/jpeg")


@pytest.fixture
def pending_member(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    user = User.objects.create_user(
        username="pending@example.com",
        email="pending@example.com",
        password="pass-pass-pass",
        first_name="Pia",
    )
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    profile = CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1995, 5, 15),
        gender="F",
        location="canton-luxembourg",
        phone_number="+352600000000",
        phone_verified=True,
        welcome_seen_at=timezone.now(),
        coach_intro_seen_at=timezone.now(),
        event_languages=["en"],
        verification_status="pending",
    )
    profile.photo_1 = _jpeg("first.jpg")
    profile.save()
    return user


@pytest.mark.django_db
def test_pending_member_reaches_the_photo_editor(client, pending_member):
    client.force_login(pending_member)

    response = client.get(PHOTOS_URL)

    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="editPhotosForm"' in content
    assert "/api/profile/upload-photo/1/" in content


@pytest.mark.django_db
def test_pending_member_can_replace_a_photo_from_the_editor(client, pending_member):
    client.force_login(pending_member)
    before = pending_member.crushprofile.photo_1.name

    response = client.post(
        "/api/profile/upload-photo/1/", {"photo_1": _jpeg("second.jpg", "blue")}
    )

    assert response.status_code == 200
    profile = CrushProfile.objects.get(user=pending_member)
    assert profile.photo_1.name != before
    assert profile.verification_status == "pending"


@pytest.mark.django_db
def test_photo_editor_keeps_next_for_a_pending_member(client, pending_member):
    client.force_login(pending_member)

    response = client.get(f"{PHOTOS_URL}&next=/en/crush-connect/")

    assert response.status_code == 200
    assert 'name="next" value="/en/crush-connect/"' in response.content.decode()


@pytest.mark.django_db
def test_other_sections_still_send_a_pending_member_to_the_status_page(
    client, pending_member
):
    client.force_login(pending_member)

    for section in ("", "?section=privacy", "?section=event_identity"):
        response = client.get(f"/en/profile/edit/{section}")
        assert response.status_code == 302
        assert response["Location"] == "/en/profile-submitted/"


@pytest.mark.django_db
def test_rejected_legacy_submission_still_wins_over_the_photo_editor(
    client, pending_member
):
    ProfileSubmission.objects.create(
        profile=pending_member.crushprofile, status="rejected"
    )
    client.force_login(pending_member)

    response = client.get(PHOTOS_URL)

    assert response.status_code == 302
    assert response["Location"] == "/en/profile/rejected/"


@pytest.mark.django_db
def test_rejected_member_does_not_reach_the_photo_editor(client, pending_member):
    CrushProfile.objects.filter(user=pending_member).update(
        verification_status="rejected"
    )
    client.force_login(pending_member)

    response = client.get(PHOTOS_URL)

    assert response.status_code == 302
    assert response["Location"] == "/en/profile/rejected/"


@pytest.mark.django_db
def test_wizard_draft_stores_the_photo_endpoint_not_the_storage_url(
    client, pending_member
):
    """On Azure the storage URL is a SAS link that expires within the hour.
    The wizard restores the draft URL as the preview on the next visit, so a
    stored SAS link showed a broken image to a member who came back later."""
    client.force_login(pending_member)

    response = client.post(
        "/api/profile/draft/upload-photo/",
        {"photo_number": "1", "photo": _jpeg("second.jpg", "blue")},
    )

    assert response.status_code == 200
    expected = f"/media/profile/{pending_member.pk}/photo_1/"
    assert response.json()["photo_url"].endswith(expected)
    profile = CrushProfile.objects.get(user=pending_member)
    assert profile.draft_data["step3"]["photo_1_url"].endswith(expected)
    assert profile.photo_1.name not in profile.draft_data["step3"]["photo_1_url"]
    assert ProfilePhotoUpload.objects.filter(profile=profile).count() == 2
