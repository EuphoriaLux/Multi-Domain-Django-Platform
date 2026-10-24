"""Tests for the coach follow-up template (screening tab, Crush numbers).

A coach whose WhatsApp number moved into the coach inbox must never get a
``wa.me`` link (it would send from a personal WhatsApp). They get a button that
sends the approved follow-up template from their Crush number instead, gated
like every business-initiated WhatsApp: the member's opt-in, no master
unsubscribe, a sendable verified number, and one send per 24 hours.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import (
    CallAttempt,
    CrushCoach,
    CrushProfile,
    EmailPreference,
    ProfileSubmission,
    UserDataConsent,
)
from crush_lu.services import coach_whatsapp
from crush_lu.views_coach import _screening_outreach_context
from hub.models import WhatsAppInboundMessage, WhatsAppMessage

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]
User = get_user_model()

COACH_NUMBER_ID = "2000"
TEMPLATE = "crush_coach_followup"
POST = "hub.whatsapp_service.requests.post"

followup_settings = override_settings(
    META_WHATSAPP_ACCESS_TOKEN="test-token",
    META_PHONE_NUMBER_ID="1000",
    META_WABA_ID="waba",
    WHATSAPP_COACH_FOLLOWUP_TEMPLATE=TEMPLATE,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _make_user(username, **extra):
    user = User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="pass12345",
        **extra,
    )
    consent, _ = UserDataConsent.objects.get_or_create(user=user)
    consent.crushlu_consent_given = True
    consent.save(update_fields=["crushlu_consent_given"])
    return user


def _make_coach(username="coach_anna", number_id=COACH_NUMBER_ID):
    return CrushCoach.objects.create(
        user=_make_user(username, first_name="Anna"),
        phone_number="+352691000000",
        whatsapp_phone_number_id=number_id,
    )


def _make_member(opt_in=True, language="en"):
    user = _make_user("member_bea", first_name="Bea", last_name="Muller")
    profile = CrushProfile.objects.create(
        user=user,
        date_of_birth="1995-01-01",
        gender="F",
        location="Luxembourg",
        phone_number="+352621000001",
        phone_verified=True,
        preferred_language=language,
    )
    prefs = EmailPreference.get_or_create_for_user(user)
    prefs.whatsapp_opt_in = opt_in
    prefs.save()
    return profile


def _submission(coach, profile):
    return ProfileSubmission.objects.create(
        profile=profile, coach=coach, status="pending", review_call_completed=False
    )


def _graph_ok():
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 200
    resp.json.return_value = {"messages": [{"id": "wamid.F1"}]}
    return resp


def _post_followup(client, submission, **extra):
    return client.post(
        reverse("crush_lu:coach_whatsapp_followup", args=[submission.pk]),
        HTTP_HX_REQUEST="true",
        **extra,
    )


# --- Screening tab: no wa.me for a Crush number -----------------------------


@followup_settings
def test_crush_number_coach_gets_no_wa_me_link():
    context = _screening_outreach_context(_make_coach(), _make_member())

    assert context["whatsapp_url"] == ""
    assert context["sms_template_encoded"]  # SMS still works from their SIM
    assert context["crush_number_followup"].ready


@followup_settings
def test_coach_without_crush_number_keeps_wa_me_link():
    context = _screening_outreach_context(_make_coach(number_id=""), _make_member())

    assert context["whatsapp_url"].startswith("https://wa.me/352621000001")
    assert "crush_number_followup" not in context


@followup_settings
def test_screening_tab_shows_the_followup_button_not_wa_me():
    coach = _make_coach()
    submission = _submission(coach, _make_member())
    client = Client()
    client.force_login(coach.user)

    resp = client.post(
        reverse("crush_lu:coach_log_sms_sent", args=[submission.pk]),
        HTTP_HX_REQUEST="true",
    )

    html = resp.content.decode()
    assert "wa.me" not in html
    assert reverse("crush_lu:coach_whatsapp_followup", args=[submission.pk]) in html


# --- followup_status gate ----------------------------------------------------


@followup_settings
def test_ready_when_everything_is_in_place():
    status = coach_whatsapp.followup_status(_make_coach(), _make_member())
    assert status.ready
    assert status.reason == ""


@followup_settings
def test_coach_without_crush_number_is_never_ready_and_gets_no_reason():
    status = coach_whatsapp.followup_status(_make_coach(number_id=""), _make_member())
    assert not status.ready
    assert status.reason == ""


@followup_settings
@pytest.mark.parametrize(
    "overrides", [{"WHATSAPP_COACH_FOLLOWUP_TEMPLATE": ""}, {"META_WABA_ID": ""}]
)
def test_off_until_template_and_meta_are_configured(overrides):
    coach, profile = _make_coach(), _make_member()
    with override_settings(**overrides):
        status = coach_whatsapp.followup_status(coach, profile)
    assert not status.ready
    assert status.reason


@followup_settings
def test_no_opt_in_means_not_ready():
    status = coach_whatsapp.followup_status(_make_coach(), _make_member(opt_in=False))
    assert not status.ready
    assert "WhatsApp" in status.reason


@followup_settings
def test_missing_preferences_row_means_not_ready():
    profile = _make_member()
    EmailPreference.objects.filter(user=profile.user).delete()
    assert not coach_whatsapp.followup_status(_make_coach(), profile).ready


@followup_settings
def test_master_unsubscribe_wins_over_opt_in():
    profile = _make_member()
    EmailPreference.objects.filter(user=profile.user).update(unsubscribed_all=True)
    assert not coach_whatsapp.followup_status(_make_coach(), profile).ready


@followup_settings
@pytest.mark.parametrize("field", ["phone_verified", "not_on_whatsapp"])
def test_unsendable_number_is_not_ready(field):
    profile = _make_member()
    # Queryset update: CrushProfile.save() restores phone_verified from the
    # stored row when the number is unchanged.
    CrushProfile.objects.filter(pk=profile.pk).update(
        **{field: field == "not_on_whatsapp"}
    )
    profile.refresh_from_db()
    assert not coach_whatsapp.followup_status(_make_coach(), profile).ready


@followup_settings
def test_one_followup_per_member_per_day():
    coach, profile = _make_coach(), _make_member()
    sent = WhatsAppMessage.objects.create(
        user=coach.user,
        recipient="+352621000001",
        template_name=TEMPLATE,
        business_phone_number_id=COACH_NUMBER_ID,
        status=WhatsAppMessage.Status.SENT,
    )
    assert not coach_whatsapp.followup_status(coach, profile).ready

    WhatsAppMessage.objects.filter(pk=sent.pk).update(
        created_at=timezone.now() - timedelta(hours=25)
    )
    assert coach_whatsapp.followup_status(coach, profile).ready


@followup_settings
def test_a_failed_followup_does_not_start_the_cooldown():
    coach, profile = _make_coach(), _make_member()
    WhatsAppMessage.objects.create(
        user=coach.user,
        recipient="+352621000001",
        template_name=TEMPLATE,
        business_phone_number_id=COACH_NUMBER_ID,
        status=WhatsAppMessage.Status.FAILED,
    )
    assert coach_whatsapp.followup_status(coach, profile).ready


# --- Sending -----------------------------------------------------------------


@followup_settings
def test_send_goes_out_from_the_coach_number_in_the_member_language():
    coach = _make_coach()
    submission = _submission(coach, _make_member(language="fr"))
    client = Client()
    client.force_login(coach.user)

    with patch(POST, return_value=_graph_ok()) as mock_post:
        resp = _post_followup(client, submission)

    assert resp.status_code == 200
    url = mock_post.call_args.args[0]
    assert f"/{COACH_NUMBER_ID}/messages" in url
    template = mock_post.call_args.kwargs["json"]["template"]
    assert template["name"] == TEMPLATE
    assert template["language"] == {"code": "fr"}
    params = template["components"][0]["parameters"]
    assert [p["text"] for p in params] == ["Bea", "Anna"]

    sent = WhatsAppMessage.objects.get()
    assert sent.business_phone_number_id == COACH_NUMBER_ID
    assert sent.recipient == "+352621000001"
    assert sent.user == coach.user
    attempt = CallAttempt.objects.get(submission=submission)
    assert attempt.result == "whatsapp_sent"
    assert attempt.coach == coach
    assert "Sent from your Crush number" in resp.content.decode()


@followup_settings
def test_second_send_within_a_day_is_refused():
    coach = _make_coach()
    submission = _submission(coach, _make_member())
    client = Client()
    client.force_login(coach.user)

    with patch(POST, return_value=_graph_ok()) as mock_post:
        _post_followup(client, submission)
        resp = _post_followup(client, submission)

    assert mock_post.call_count == 1
    assert CallAttempt.objects.filter(submission=submission).count() == 1
    assert "in the last 24 hours" in resp.content.decode()


@followup_settings
def test_member_without_opt_in_gets_nothing_sent():
    coach = _make_coach()
    submission = _submission(coach, _make_member(opt_in=False))
    client = Client()
    client.force_login(coach.user)

    with patch(POST) as mock_post:
        resp = _post_followup(client, submission)

    mock_post.assert_not_called()
    assert not WhatsAppMessage.objects.exists()
    assert not CallAttempt.objects.exists()
    assert "turned on WhatsApp messages" in resp.content.decode()


@followup_settings
def test_meta_rejection_logs_no_attempt():
    coach = _make_coach()
    submission = _submission(coach, _make_member())
    client = Client()
    client.force_login(coach.user)
    rejected = MagicMock(ok=False, status_code=400)
    rejected.json.return_value = {"error": {"code": 132001, "message": "No template"}}

    with patch(POST, return_value=rejected):
        resp = _post_followup(client, submission)

    assert WhatsAppMessage.objects.get().status == WhatsAppMessage.Status.FAILED
    assert not CallAttempt.objects.exists()
    assert "accept the message. Please try again." in resp.content.decode()


@followup_settings
def test_another_coachs_submission_is_a_404():
    owner = _make_coach()
    other = _make_coach(username="coach_ben", number_id="3000")
    submission = _submission(owner, _make_member())
    client = Client()
    client.force_login(other.user)

    with patch(POST) as mock_post:
        resp = _post_followup(client, submission)

    assert resp.status_code == 404
    mock_post.assert_not_called()


@followup_settings
def test_plain_post_redirects_to_the_review_page():
    coach = _make_coach()
    submission = _submission(coach, _make_member())
    client = Client()
    client.force_login(coach.user)

    with patch(POST, return_value=_graph_ok()):
        resp = client.post(
            reverse("crush_lu:coach_whatsapp_followup", args=[submission.pk])
        )

    assert resp.status_code == 302
    assert resp.url == reverse("crush_lu:coach_review_profile", args=[submission.pk])


@followup_settings
def test_get_is_not_allowed():
    coach = _make_coach()
    submission = _submission(coach, _make_member())
    client = Client()
    client.force_login(coach.user)

    resp = client.get(reverse("crush_lu:coach_whatsapp_followup", args=[submission.pk]))
    assert resp.status_code == 405


# --- The reply lands in the coach inbox --------------------------------------


@followup_settings
def test_inbox_labels_the_followup_instead_of_the_template_name():
    coach = _make_coach()
    WhatsAppInboundMessage.objects.create(
        wa_message_id="wamid.IN1",
        from_number="352621000001",
        message_type="text",
        text="Sure, tomorrow at 6?",
        payload={},
        received_at=timezone.now() - timedelta(minutes=5),
        business_phone_number_id=COACH_NUMBER_ID,
    )
    WhatsAppMessage.objects.create(
        user=coach.user,
        recipient="+352621000001",
        template_name=TEMPLATE,
        business_phone_number_id=COACH_NUMBER_ID,
        status=WhatsAppMessage.Status.SENT,
    )

    items = coach_whatsapp.thread(coach, "352621000001")
    conv = coach_whatsapp.conversations_for(coach)[0]

    outbound = [item.text for item in items if item.outbound]
    assert outbound == ["Follow-up message (template)"]
    assert conv.last_is_outbound
    assert conv.last_text == "Follow-up message (template)"
