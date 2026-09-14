"""Current Crush Connect hub tests."""

import pytest

from crush_lu.models import CrushConnectWaitlist
from crush_lu.services.crush_connect import propose_coach_pick
from crush_lu.tests.test_crush_connect import (
    HUB_URL,
    _coach_for,
    _login_eligible,
    _make_user,
)

pytestmark = pytest.mark.urls("azureproject.urls_crush")


@pytest.mark.django_db
@pytest.mark.parametrize("incoming", [False, True])
def test_summary_hides_chat_blocked_outside_chat_without_mutating_it(
    settings, incoming
):
    from crush_lu.services.blocking import apply_block
    from crush_lu.services.connect_summary import get_connect_summary
    from crush_lu.tests.test_connect_chat_flows import _make_open_chat

    settings.CRUSH_CONNECT_LAUNCHED = True
    member, partner, chat = _make_open_chat()
    assert get_connect_summary(member)["chat_count"] == 1
    expiry = chat.expires_at
    apply_block(partner, member) if incoming else apply_block(member, partner)
    assert get_connect_summary(member)["chat_count"] == 0
    chat.refresh_from_db()
    assert chat.status == "active"
    assert chat.expires_at == expiry


@pytest.mark.django_db
@pytest.mark.parametrize("lost", ["paused", "excluded", "account", "profile"])
def test_summary_counts_only_targets_available_on_today(settings, lost):
    from django.utils import timezone
    from crush_lu.models import ConnectCycleCard, ConnectWeekSession
    from crush_lu.services.connect_cycle import get_or_create_todays_cards
    from crush_lu.services.connect_summary import get_connect_summary

    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_user(username="progress_member", premium=False)
    target = _make_user(username="progress_target", premium=False)
    available = _make_user(username="progress_available", premium=False)
    session = ConnectWeekSession.objects.create(user=member)
    for index, user in enumerate([target, available], start=1):
        ConnectCycleCard.objects.create(
            session=session,
            target_user=user,
            day_number=1,
            card_index=index,
            generated_date=timezone.localdate(),
            is_completed=index == 1,
        )
    before = get_connect_summary(member)
    assert (before["daily_total"], before["daily_completed"]) == (2, 1)
    if lost == "paused":
        target.crush_connect_membership.paused_at = timezone.now()
        target.crush_connect_membership.save(update_fields=["paused_at"])
    elif lost == "excluded":
        target.crush_connect_membership.excluded_by_coach = True
        target.crush_connect_membership.save(update_fields=["excluded_by_coach"])
    elif lost == "profile":
        target.crushprofile.is_active = False
        target.crushprofile.save(update_fields=["is_active"])
    else:
        target.is_active = False
        target.save(update_fields=["is_active"])
    summary = get_connect_summary(member)
    assert (summary["daily_total"], summary["daily_completed"]) == (1, 0)
    assert len(get_or_create_todays_cards(session)) == 1


@pytest.mark.django_db
@pytest.mark.parametrize("lost", ["consent", "photo", "active", "phase", "paused"])
def test_summary_hides_pending_requests_when_inbox_becomes_unavailable(settings, lost):
    from django.utils import timezone
    from crush_lu.models import ConnectWeeklyRequest, ConnectWeekSession
    from crush_lu.services.connect_summary import get_connect_summary

    settings.CRUSH_CONNECT_LAUNCHED = True
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = False
    recipient = _make_user(username="inbox_recipient", premium=False)
    sender = _make_user(username="inbox_sender", premium=False)
    invitation = ConnectWeeklyRequest.objects.create(
        session=ConnectWeekSession.objects.create(user=sender),
        requester=sender,
        recipient=recipient,
    )
    assert get_connect_summary(recipient)["pending_requests"] == 1
    if lost == "consent":
        recipient.crush_connect_membership.photo_share_consent = False
        recipient.crush_connect_membership.save(update_fields=["photo_share_consent"])
    elif lost == "photo":
        recipient.crushprofile.photo_1 = ""
        recipient.crushprofile.save(update_fields=["photo_1"])
    elif lost == "active":
        recipient.is_active = False
        recipient.save(update_fields=["is_active"])
    elif lost == "paused":
        recipient.crush_connect_membership.paused_at = timezone.now()
        recipient.crush_connect_membership.save(update_fields=["paused_at"])
    else:
        settings.CRUSH_CONNECT_LAUNCHED = False
    assert get_connect_summary(recipient)["pending_requests"] == 0
    invitation.refresh_from_db()
    assert invitation.status == ConnectWeeklyRequest.Status.PENDING


@pytest.mark.django_db
def test_flag_off_redirects_nonstaff_to_teaser(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = False
    member = _make_user(username="member", premium=False)
    _login_eligible(client, member)

    response = client.get(HUB_URL)

    assert response.status_code == 302
    assert "/crush-connect/" in response.url


@pytest.mark.django_db
def test_onboarded_member_sees_catalogue_and_connect_week_without_legacy_tiles(
    client, settings
):
    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_user(username="member", premium=False)
    _login_eligible(client, member)

    response = client.get(HUB_URL)
    body = response.content.decode()

    assert response.status_code == 200
    assert "Your Connect Week" in body
    assert "anonymous totals" in body
    assert "Today's Drop" not in body
    assert "Your Sparks" not in body


@pytest.mark.django_db
def test_unonboarded_eligible_member_gets_preparation_hub(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_user(username="member", premium=False, onboarded=False)
    _login_eligible(client, member)

    response = client.get(HUB_URL)

    assert response.status_code == 200
    assert "Complete your setup" in response.content.decode()


@pytest.mark.django_db
def test_beta_selected_tester_gets_connect_week_tile(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    member = _make_user(username="member", premium=False)
    CrushConnectWaitlist.objects.create(user=member, selected_as_tester=True)
    _login_eligible(client, member)

    response = client.get(HUB_URL)

    assert response.status_code == 200
    assert "Your Connect Week" in response.content.decode()


@pytest.mark.django_db
def test_premium_badge_and_named_coach_are_visible(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_user(username="member")
    _login_eligible(client, member)

    body = client.get(HUB_URL).content.decode()

    assert "Premium member" in body
    assert member.crushprofile.assigned_coach.user.username in body


@pytest.mark.django_db
def test_open_coach_pick_is_linked_from_hub(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_user(username="member", preferred_genders=["F"])
    candidate = _make_user(username="candidate", gender="F", premium=False)
    propose_coach_pick(_coach_for(member), member, candidate)
    _login_eligible(client, member)

    body = client.get(HUB_URL).content.decode()

    assert "Your coach picked a match" in body
    assert "/crush-connect/coach-pick/" in body


@pytest.mark.django_db
def test_loading_hub_does_not_start_discovery(client, settings):
    from crush_lu.models import ConnectCycleCard, ConnectWeekSession

    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_user(username="read_only_hub", premium=False)
    _login_eligible(client, member)
    for _ in range(2):
        response = client.get(HUB_URL)
        assert response.status_code == 200
        assert "Connect navigation" in response.content.decode()
    assert not ConnectWeekSession.objects.filter(user=member).exists()
    assert not ConnectCycleCard.objects.exists()


@pytest.mark.django_db
def test_staff_today_navigation_preserves_preview_without_membership(client, settings):
    from django.contrib.auth import get_user_model
    from crush_lu.services.connect_summary import get_connect_summary

    settings.CRUSH_CONNECT_LAUNCHED = False
    staff = get_user_model().objects.create_user("preview", is_staff=True)
    assert get_connect_summary(staff)["cycle_access"] is True


@pytest.mark.django_db
@pytest.mark.parametrize("missing", ["consent", "photo"])
def test_hub_visibility_follows_current_catalogue_eligibility(
    client, settings, missing
):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    member = _make_user(username="hidden", premium=False)
    if missing == "consent":
        membership = member.crush_connect_membership
        membership.photo_share_consent = False
        membership.save(update_fields=["photo_share_consent"])
    else:
        profile = member.crushprofile
        profile.photo_1 = ""
        profile.save(update_fields=["photo_1"])
    _login_eligible(client, member)
    body = client.get(HUB_URL).content.decode()
    assert "Your Connect profile is currently hidden" in body
    assert "Your Connect profile is visible to eligible members." not in body
    assert "You can receive requests" not in body


@pytest.mark.django_db
def test_summary_advances_existing_week_without_generating_cards(settings):
    from datetime import timedelta
    from django.utils import timezone
    from crush_lu.models import ConnectCycleCard, ConnectWeekSession
    from crush_lu.services.connect_summary import get_connect_summary

    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_user(username="returning", premium=False)
    session = ConnectWeekSession.objects.create(user=member)
    ConnectWeekSession.objects.filter(pk=session.pk).update(
        started_at=timezone.now() - timedelta(days=7)
    )
    summary = get_connect_summary(member)
    assert summary["review_open"] is True
    assert summary["session"].review_expires_at is not None
    assert ConnectWeekSession.objects.filter(user=member).count() == 1
    assert not ConnectCycleCard.objects.exists()
