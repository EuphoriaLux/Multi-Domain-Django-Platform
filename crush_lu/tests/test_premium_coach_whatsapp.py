"""Premium members message the coach they chose on WhatsApp.

Covers who gets the link (active Premium + active assigned coach only), where
it points (the coach's Crush number, else the main Crush.lu number, never a
personal number), the Connect hub and Coach's Pick rendering, and the
"Premium member" badge in the coach WhatsApp inbox.
"""

from urllib.parse import unquote

import pytest
from django.utils import timezone

from crush_lu.models import CrushSiteConfig, PremiumMembership
from crush_lu.services import coach_whatsapp
from crush_lu.services.premium_coach_contact import premium_coach_whatsapp
from crush_lu.tests.test_crush_connect import (
    COACH_PICK_URL,
    HUB_URL,
    _coach_for,
    _login_eligible,
    _make_user,
)
from hub.models import WhatsAppInboundMessage

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]

COACH_NUMBER_ID = "2000"
MAIN_NUMBER = "352621163882"


def _give_crush_number(coach, phone="+352 661 000 111"):
    coach.phone_number = phone
    coach.whatsapp_phone_number_id = COACH_NUMBER_ID
    coach.save(update_fields=["phone_number", "whatsapp_phone_number_id"])


def _set_main_number(number=MAIN_NUMBER, enabled=True):
    config = CrushSiteConfig.get_config()
    config.whatsapp_number = number
    config.whatsapp_enabled = enabled
    config.save(update_fields=["whatsapp_number", "whatsapp_enabled"])


def test_coach_with_crush_number_gets_direct_link():
    member = _make_user(username="member")
    coach = _coach_for(member)
    _give_crush_number(coach)
    _set_main_number()

    link = premium_coach_whatsapp(member)

    assert link is not None
    assert link.url.startswith("https://wa.me/352661000111?text=")
    assert not link.via_team
    assert link.coach_id == coach.pk
    assert "Member" in unquote(link.url)


def test_coach_without_crush_number_falls_back_to_main_number():
    member = _make_user(username="member")
    coach = _coach_for(member)
    # A personal number alone (no Crush number) must never be offered.
    coach.phone_number = "+352 691 999 999"
    coach.whatsapp_phone_number_id = ""
    coach.save(update_fields=["phone_number", "whatsapp_phone_number_id"])
    _set_main_number()

    link = premium_coach_whatsapp(member)

    assert link is not None
    assert link.url.startswith(f"https://wa.me/{MAIN_NUMBER}?text=")
    assert "352691999999" not in link.url
    assert link.via_team
    # The team needs to know whose coach to pass it to.
    assert coach.user.first_name in unquote(link.url)


def test_no_link_when_main_number_off_and_no_crush_number():
    member = _make_user(username="member")
    _set_main_number(enabled=False)

    assert premium_coach_whatsapp(member) is None


def test_no_link_without_active_premium():
    # Assigned coach without a membership (attendance auto-assign, backfill)
    # is not Premium.
    member = _make_user(username="member")
    _give_crush_number(_coach_for(member))
    PremiumMembership.objects.filter(user=member).update(
        status="cancelled", ended_at=timezone.now()
    )

    assert premium_coach_whatsapp(member) is None


def test_no_link_when_coach_inactive():
    member = _make_user(username="member")
    coach = _coach_for(member)
    _give_crush_number(coach)
    coach.is_active = False
    coach.save(update_fields=["is_active"])
    _set_main_number()

    assert premium_coach_whatsapp(member) is None


def test_hub_and_coach_pick_show_button(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_user(username="member")
    _give_crush_number(_coach_for(member))
    _login_eligible(client, member)

    for url in (HUB_URL, COACH_PICK_URL):
        body = client.get(url).content.decode()
        assert "https://wa.me/352661000111" in body, url
        assert "on WhatsApp" in body, url


def test_hub_hides_button_for_free_member(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    _set_main_number()
    member = _make_user(username="member", premium=False)
    _login_eligible(client, member)

    body = client.get(HUB_URL).content.decode()

    assert "Goes to the Crush.lu team" not in body
    assert "Your coach answers from Crush.lu" not in body


def test_inbox_marks_premium_members(settings):
    member = _make_user(username="member")
    coach = _coach_for(member)
    _give_crush_number(coach)
    member.crushprofile.phone_number = "+352621000001"
    member.crushprofile.save(update_fields=["phone_number"])
    free = _make_user(username="free", premium=False)
    free.crushprofile.phone_number = "+352621000002"
    free.crushprofile.save(update_fields=["phone_number"])
    for frm, wamid in (("352621000001", "wamid.1"), ("352621000002", "wamid.2")):
        WhatsAppInboundMessage.objects.create(
            wa_message_id=wamid,
            from_number=frm,
            business_phone_number_id=COACH_NUMBER_ID,
            message_type="text",
            text="Hi",
            payload={},
            received_at=timezone.now(),
        )

    by_number = {c.from_number: c for c in coach_whatsapp.conversations_for(coach)}

    assert by_number["352621000001"].premium is True
    assert by_number["352621000002"].premium is False


def test_button_is_translated(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_user(username="member")
    _give_crush_number(_coach_for(member))
    _login_eligible(client, member)

    body = client.get(HUB_URL.replace("/en/", "/de/", 1)).content.decode()

    assert "auf WhatsApp schreiben" in body
