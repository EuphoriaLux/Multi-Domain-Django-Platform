"""Optional WhatsApp channel for the Connect Week request notification.

Off until ``WHATSAPP_CONNECT_REQUEST_TEMPLATE`` names an approved Utility
template. When on, it must respect the member's explicit WhatsApp opt-in, the
master unsubscribe, and a sendable verified number — and a crash in the paid
side channel must never block the request itself.
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from crush_lu.models import EmailPreference
from crush_lu.models.crush_connect_cycle import ConnectWeeklyRequest
from crush_lu.services.connect_cycle import send_weekly_request
from crush_lu.tests.test_connect_week_experience import (
    _make_cycle_user,
    _reviewable_session_with_card,
)

User = get_user_model()

SEND = "hub.whatsapp_service.send_whatsapp_template"


def _pair(settings, *, template="crush_connect_request", opt_in=True):
    settings.CRUSH_CONNECT_LAUNCHED = True
    settings.ROOT_URLCONF = "azureproject.urls_crush"
    settings.WHATSAPP_CONNECT_REQUEST_TEMPLATE = template
    settings.META_WHATSAPP_ACCESS_TOKEN = "token"
    settings.META_PHONE_NUMBER_ID = "123"
    settings.META_WABA_ID = "456"
    User.objects.create_superuser(username="wa_system", email="wa@test.lu", password="x")
    sender = _make_cycle_user("wa_sender")
    recipient = _make_cycle_user("wa_recipient", gender="F")
    profile = recipient.crushprofile
    profile.phone_number = "+352621000000"
    profile.phone_verified = True
    profile.not_on_whatsapp = False
    profile.save(update_fields=["phone_number", "phone_verified", "not_on_whatsapp"])
    prefs = EmailPreference.get_or_create_for_user(recipient)
    prefs.whatsapp_opt_in = opt_in
    prefs.save()
    session, _ = _reviewable_session_with_card(sender, recipient)
    return sender, recipient, session


@pytest.mark.django_db
def test_channel_is_off_without_a_template_name(settings):
    sender, recipient, session = _pair(settings, template="")
    with patch(SEND) as mocked:
        send_weekly_request(session, sender, recipient)
    mocked.assert_not_called()


@pytest.mark.django_db
def test_opted_in_member_gets_the_template_in_their_language(settings):
    sender, recipient, session = _pair(settings)
    recipient.crushprofile.preferred_language = "fr"
    recipient.crushprofile.save(update_fields=["preferred_language"])

    with patch(SEND) as mocked:
        send_weekly_request(session, sender, recipient)

    mocked.assert_called_once()
    kwargs = mocked.call_args.kwargs
    assert kwargs["recipient"] == "+352621000000"
    assert kwargs["template_name"] == "crush_connect_request"
    assert kwargs["language"] == "fr"
    assert kwargs["parameters"] == {
        "1": recipient.first_name,
        "2": sender.crushprofile.display_name,
    }
    assert kwargs["sender"].is_superuser


@pytest.mark.django_db
def test_no_opt_in_means_no_send(settings):
    sender, recipient, session = _pair(settings, opt_in=False)
    with patch(SEND) as mocked:
        send_weekly_request(session, sender, recipient)
    mocked.assert_not_called()


@pytest.mark.django_db
def test_master_unsubscribe_wins_over_opt_in(settings):
    sender, recipient, session = _pair(settings)
    prefs = EmailPreference.get_or_create_for_user(recipient)
    prefs.unsubscribed_all = True
    prefs.save()
    with patch(SEND) as mocked:
        send_weekly_request(session, sender, recipient)
    mocked.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["phone_verified", "not_on_whatsapp"])
def test_unsendable_number_is_skipped(settings, field):
    sender, recipient, session = _pair(settings)
    # Queryset update on purpose: CrushProfile.save() restores phone_verified
    # from the stored row when the number is unchanged, so an instance flip
    # would be silently reverted.
    type(recipient.crushprofile).objects.filter(user=recipient).update(
        **{field: field == "not_on_whatsapp"}  # unverified / flagged
    )
    with patch(SEND) as mocked:
        send_weekly_request(session, sender, recipient)
    mocked.assert_not_called()


@pytest.mark.django_db
def test_whatsapp_crash_never_blocks_the_request(settings):
    sender, recipient, session = _pair(settings)
    with patch(SEND, side_effect=RuntimeError("meta down")):
        weekly_request = send_weekly_request(session, sender, recipient)
    assert ConnectWeeklyRequest.objects.filter(
        pk=weekly_request.pk, status=ConnectWeeklyRequest.Status.PENDING
    ).exists()
