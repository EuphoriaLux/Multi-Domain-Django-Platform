"""Photo refusal codes stay auditable and emails follow the recipient locale."""

from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase
from django.utils.translation import override

from crush_lu.models import Notification, ProfilePhotoReviewLog
from crush_lu.notification_service import NotificationService, NotificationType
from crush_lu.services.photo_review import PhotoReviewError, submit_photo_review
from crush_lu.tests.test_coach_photo_review import _make_candidate, _make_coach

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]

REASONS = [
    (
        "blurry_photo",
        {"en": "sharp, clear", "de": "scharfes, klares", "fr": "nette et claire"},
    ),
    (
        "poor_lighting",
        {"en": "well-lit", "de": "gut beleuchtetes", "fr": "bien éclairée"},
    ),
    (
        "heavy_filter",
        {"en": "without filters", "de": "ohne Filter", "fr": "sans filtres"},
    ),
    ("not_person", {"en": "landscape", "de": "Landschaft", "fr": "paysage"}),
    (
        "cropped_face",
        {"en": "whole face", "de": "ganzes Gesicht", "fr": "visage en entier"},
    ),
    ("other", {"en": "suitable photo", "de": "geeignetes", "fr": "adaptée"}),
]


@pytest.mark.parametrize("language", ["en", "de", "fr"])
@pytest.mark.parametrize("reason,fragments", REASONS)
def test_refusal_email_and_bell_use_member_language_with_internal_notes(
    monkeypatch, language, reason, fragments
):
    cache.clear()
    coach, profile = _make_coach(), _make_candidate()
    profile.preferred_language = language
    profile.save(update_fields=["preferred_language"])
    request = RequestFactory().get("/fr/coach/photo-review/", HTTP_HOST="crush.lu")
    request.LANGUAGE_CODE = "de" if language == "fr" else "fr"
    notes = "INTERNAL_ONLY: a coach note in a different language"
    monkeypatch.setattr("crush_lu.email_helpers.can_send_email", lambda *args: True)

    with (
        override(request.LANGUAGE_CODE),
        patch("crush_lu.email_helpers.send_domain_email", return_value=1) as sender,
        TestCase.captureOnCommitCallbacks(execute=True),
    ):
        submit_photo_review(
            coach,
            profile.pk,
            "needs_revision",
            reason=reason,
            notes=notes,
            request=request,
            photo_key=profile.photo_1.name,
        )

    assert sender.call_count == 1
    email = sender.call_args.kwargs
    assert fragments[language] in email["html_message"]
    assert fragments[language] in email["message"]
    assert notes not in email["html_message"]
    assert f"/{language}/profile/edit/" in email["html_message"]
    log = ProfilePhotoReviewLog.objects.get(profile=profile)
    assert log.reason == reason
    assert log.notes == notes
    assert log.revision_notification_state == "sent"
    notification = Notification.objects.get(
        dedupe_key=f"photo-review:{log.pk}:revision"
    )
    assert fragments[language] in notification.body
    assert notes not in notification.body


def test_email_retranslates_reason_at_send_time():
    profile = _make_candidate()
    profile.preferred_language = "de"
    profile.save(update_fields=["preferred_language"])
    with (
        override("fr"),
        patch("crush_lu.email_helpers.send_domain_email", return_value=1) as sender,
    ):
        assert NotificationService._send_email(
            profile.user,
            NotificationType.PHOTO_REVISION,
            {
                "feedback": "Stale English feedback",
                "photo_review_reason": "blurry_photo",
            },
            None,
        )
    html = sender.call_args.kwargs["html_message"]
    assert "scharfes, klares" in html
    assert "Stale English feedback" not in html


@pytest.mark.parametrize("reason,fragments", REASONS[:-1])
def test_photo_quality_reasons_cannot_exclude_as_fake(reason, fragments):
    coach, profile = _make_coach(), _make_candidate()
    with pytest.raises(PhotoReviewError):
        submit_photo_review(
            coach,
            profile.pk,
            "flagged_fake",
            reason=reason,
            photo_key=profile.photo_1.name,
        )
    assert not ProfilePhotoReviewLog.objects.filter(profile=profile).exists()


def test_french_deck_exposes_the_new_reason_options():
    cache.clear()
    client = Client()
    client.force_login(_make_coach().user)
    response = client.get("/fr/coach/photo-review/", HTTP_HOST="crush.lu")
    assert response.status_code == 200
    html = response.content.decode()
    for reason, fragments in REASONS:
        assert f'value="{reason}"' in html
    assert "Photo floue ou de mauvaise qualité" in html
    assert "Les notes du coach restent internes" in html
