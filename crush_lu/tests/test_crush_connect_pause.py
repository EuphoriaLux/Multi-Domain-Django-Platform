"""Member-controlled Crush Connect pause / reactivation coverage."""

from datetime import timedelta

import pytest
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

pytestmark = pytest.mark.urls("azureproject.urls_crush")

from crush_lu.models.crush_connect_cycle import (  # noqa: E402
    ConnectChatMessage,
    ConnectCycleCard,
    ConnectTemporaryChat,
    ConnectWeeklyRequest,
    ConnectWeekSession,
)
from crush_lu.services.connect_cycle import get_or_create_todays_cards  # noqa: E402
from crush_lu.services.crush_connect import (  # noqa: E402
    get_eligible_pool,
    is_catalogue_eligible,
)
from crush_lu.services.event_lobby import GATE_PAUSED, participant_gate  # noqa: E402
from crush_lu.tests.test_crush_connect import (  # noqa: E402
    _login_eligible,
    _make_user,
    _set_gate_questions,
)


@pytest.fixture(autouse=True)
def _connect_open(settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    settings.CRUSH_CONNECT_LAUNCHED = True


@pytest.mark.django_db
def test_membership_pause_is_reversible_and_preserves_setup():
    member = _make_user(
        username="pause_setup",
        preferred_genders=["F"],
        preferred_age_min=27,
        preferred_age_max=42,
    )
    membership = member.crush_connect_membership
    onboarded_at = membership.onboarded_at

    membership.pause()
    membership.refresh_from_db()

    assert membership.is_paused is True
    assert membership.is_onboarded is True
    assert membership.is_participating is False
    assert membership.onboarded_at == onboarded_at
    assert membership.preferred_genders == ["F"]
    assert membership.preferred_age_min == 27
    assert membership.preferred_age_max == 42

    membership.reactivate()
    membership.refresh_from_db()

    assert membership.is_paused is False
    assert membership.is_participating is True
    assert membership.onboarded_at == onboarded_at


@pytest.mark.django_db
def test_pause_removes_requester_and_target_from_live_eligibility():
    requester = _make_user(
        username="pause_requester", gender="M", preferred_genders=["F"]
    )
    target = _make_user(username="pause_target", gender="F", preferred_genders=["M"])

    assert target in list(get_eligible_pool(requester))
    assert is_catalogue_eligible(target) is True

    target.crush_connect_membership.pause()
    assert target not in list(get_eligible_pool(requester))
    assert is_catalogue_eligible(target) is False

    target.crush_connect_membership.reactivate()
    requester.crush_connect_membership.pause()
    assert list(get_eligible_pool(requester)) == []


@pytest.mark.django_db
def test_global_user_and_profile_deactivation_are_respected_by_connect():
    requester = _make_user(
        username="deactivation_requester", gender="M", preferred_genders=["F"]
    )
    target = _make_user(
        username="deactivation_target", gender="F", preferred_genders=["M"]
    )

    target.crushprofile.is_active = False
    target.crushprofile.save(update_fields=["is_active"])
    assert target not in list(get_eligible_pool(requester))
    assert is_catalogue_eligible(target) is False

    target.crushprofile.is_active = True
    target.crushprofile.save(update_fields=["is_active"])
    target.is_active = False
    target.save(update_fields=["is_active"])
    assert target not in list(get_eligible_pool(requester))
    assert is_catalogue_eligible(target) is False


@pytest.mark.django_db
def test_persisted_cycle_card_is_hidden_during_pause_and_returns_after_resume():
    viewer = _make_user(
        username="pause_cycle_viewer", gender="M", preferred_genders=["F"]
    )
    target = _make_user(
        username="pause_cycle_target", gender="F", preferred_genders=["M"]
    )
    _set_gate_questions(target)
    session = ConnectWeekSession.objects.create(user=viewer)
    card = ConnectCycleCard.objects.create(
        session=session,
        day_number=1,
        card_index=1,
        target_user=target,
        generated_date=timezone.localdate(),
    )

    assert get_or_create_todays_cards(session) == [card]

    target.crush_connect_membership.pause()
    assert get_or_create_todays_cards(session) == []
    assert ConnectCycleCard.objects.filter(pk=card.pk).exists()

    target.crush_connect_membership.reactivate()
    assert get_or_create_todays_cards(session) == [card]


@pytest.mark.django_db
def test_pause_and_reactivate_endpoints_render_clear_hub_state(client):
    member = _make_user(username="pause_ui")
    membership = member.crush_connect_membership
    onboarded_at = membership.onboarded_at
    _login_eligible(client, member)

    confirmation = client.get(reverse("crush_lu:crush_connect_pause"))
    assert confirmation.status_code == 200
    assert "Pause your Connect profile?" in confirmation.content.decode()
    membership.refresh_from_db()
    assert membership.is_paused is False

    paused = client.post(reverse("crush_lu:crush_connect_pause"), follow=True)
    assert paused.redirect_chain[-1][0] == reverse("crush_lu:crush_connect_hub")
    assert "Your Connect profile is paused" in paused.content.decode()
    assert paused.context["cycle_access"] is False
    membership.refresh_from_db()
    assert membership.is_paused is True
    assert membership.onboarded_at == onboarded_at

    deep_link = client.get(reverse("crush_lu:crush_connect_home"))
    assert deep_link.status_code == 302
    assert deep_link.url == reverse("crush_lu:crush_connect_hub")

    assert client.get(reverse("crush_lu:crush_connect_reactivate")).status_code == 405
    resumed = client.post(reverse("crush_lu:crush_connect_reactivate"), follow=True)
    assert "Crush Connect is active again." in resumed.content.decode()
    membership.refresh_from_db()
    assert membership.is_paused is False
    assert membership.onboarded_at == onboarded_at


@pytest.mark.django_db
def test_pause_keeps_existing_temporary_chat_open(client):
    member = _make_user(username="pause_chat_member")
    partner = _make_user(username="pause_chat_partner", gender="F")
    session = ConnectWeekSession.objects.create(user=member)
    weekly_request = ConnectWeeklyRequest.objects.create(
        session=session,
        requester=member,
        recipient=partner,
        status=ConnectWeeklyRequest.Status.ACCEPTED,
    )
    chat = ConnectTemporaryChat.objects.create(
        request=weekly_request,
        participant_1=member,
        participant_2=partner,
        expires_at=timezone.now() + timedelta(days=7),
    )
    _login_eligible(client, member)

    client.post(reverse("crush_lu:crush_connect_pause"))

    detail = client.get(
        reverse("crush_lu:connect_week_chat_detail", kwargs={"chat_id": chat.pk})
    )
    assert detail.status_code == 200
    sent = client.post(
        reverse("crush_lu:connect_week_chat_send", kwargs={"chat_id": chat.pk}),
        {"message": "I am still here for our existing chat."},
    )
    assert sent.status_code == 302
    assert ConnectChatMessage.objects.filter(chat=chat, sender=member).count() == 1


@pytest.mark.django_db
def test_pause_removes_member_from_event_lobby_gate():
    member = _make_user(username="pause_lobby")
    member.crush_connect_membership.pause()

    allowed, reason = participant_gate(member)

    assert allowed is False
    assert reason == GATE_PAUSED


@pytest.mark.django_db(transaction=True)
def test_pause_drops_cached_match_scores_and_resume_rebuilds_them():
    """The pause promise covers the score cache, not just the flag.

    Coach match surfaces read MatchScore rows directly and filter counterparts
    only on profile verification and activity, so a paused member left in the
    cache stays on those pages indefinitely — nothing is scheduled to clean up.
    Resume has the mirror problem: pairs a counterpart pruned during the pause
    stay missing, and are scored as neutral, until an unrelated edit heals them.

    transaction=True because the rebuild is deferred to on_commit.
    """
    from crush_lu.matching import update_match_scores_for_user
    from crush_lu.models.matching import MatchScore

    member = _make_user(
        username="pause_scores_a", gender="M", preferred_genders=["F"]
    )
    other = _make_user(
        username="pause_scores_b", gender="F", preferred_genders=["M"]
    )
    # The scorer skips any pair whose members have not set traits.
    from crush_lu.models import Trait

    quals = list(
        Trait.objects.filter(trait_type="quality").order_by("pk").values_list("pk", flat=True)
    )
    defs = list(
        Trait.objects.filter(trait_type="defect").order_by("pk").values_list("pk", flat=True)
    )
    for user in (member, other):
        _set_gate_questions(user)
        membership = user.crush_connect_membership
        membership.qualities.set(quals[:3])
        membership.defects.set(defs[:2])
        membership.sought_qualities.set(quals[3:6])

    update_match_scores_for_user(member)
    pair = MatchScore.objects.filter(
        Q(user_a=member) | Q(user_b=member)
    )
    assert pair.exists(), "precondition: the pair should be cached before pausing"

    member.crush_connect_membership.pause()

    assert not pair.exists(), (
        "a paused member must leave the score cache the coach views read"
    )

    member.refresh_from_db()
    member.crush_connect_membership.reactivate()

    assert pair.exists(), "resuming must rebuild the pairs pruned during the pause"
