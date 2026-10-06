"""Coach warnings are private, read-only and independent of event eligibility."""

from datetime import date
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext

from crush_lu.models import (
    CrushCoach,
    CrushProfile,
    EventRegistration,
    UserBlock,
    UserDataConsent,
)
from crush_lu.services.event_conflicts import event_conflict_pairs
from crush_lu.services.event_grouping import EventApplicant, build_compatibility_graph
from crush_lu.tests.test_coach_event_detail import _make_coach, _make_event

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]
User = get_user_model()


@pytest.fixture
def pool(client):
    coach_user = _make_coach()
    coach = coach_user.crushcoach
    event = _make_event()
    event.coaches.add(coach)
    members = []
    for name in ("Alice", "Benoit", "Carla"):
        user = User.objects.create_user(
            username=f"{name.lower()}@example.com", first_name=name
        )
        CrushProfile.objects.create(user=user)
        EventRegistration.objects.create(event=event, user=user, status="applied")
        members.append(user)
    client.force_login(coach_user)
    return event, coach, members


def _detail(client, event, query=""):
    return client.get(f"/en/coach/events/{event.pk}/{query}", HTTP_HOST="crush.lu")


def _list(client):
    return client.get("/en/coach/events/", HTTP_HOST="crush.lu")


@pytest.mark.parametrize("reverse", [False, True])
def test_one_way_block_and_mutual_count_once(pool, client, reverse):
    event, coach, (alice, benoit, carla) = pool
    first, second = (benoit, alice) if reverse else (alice, benoit)
    UserBlock.objects.create(blocker=first, blocked=second, reason="harassment")
    expected = {event.pk: [(alice.pk, benoit.pk)]}
    assert event_conflict_pairs([event], coach) == expected
    UserBlock.objects.create(blocker=second, blocked=first, reason="fake")
    assert event_conflict_pairs([event], coach) == expected
    UserBlock.objects.create(blocker=carla, blocked=alice)
    response = _list(client)
    assert response.status_code == 200
    assert response.context["upcoming_events"][0].coach_conflict_count == 2
    assert b"2 potential conflicts" in response.content
    response = _detail(client, event, "?status=waitlist")
    assert response.context["event_conflicts"] == [
        ("Alice", "Benoit"),
        ("Alice", "Carla"),
    ]
    assert b"data-event-conflicts" in response.content
    section = response.content.split(b"data-event-conflicts>")[1].split(b"</section>")[
        0
    ]
    assert b"harassment" not in section and b"fake" not in section
    assert b"example.com" not in section


@pytest.mark.parametrize(
    "status, included",
    [
        ("applied", True),
        ("pending", True),
        ("confirmed", True),
        ("waitlist", True),
        ("attended", True),
        ("cancelled", False),
        ("no_show", False),
    ],
)
def test_participation_scope_on_either_endpoint(pool, status, included):
    event, coach, (alice, benoit, _) = pool
    UserBlock.objects.create(blocker=alice, blocked=benoit)
    for user, other in ((alice, benoit), (benoit, alice)):
        EventRegistration.objects.filter(event=event, user=other).update(
            status="applied"
        )
        EventRegistration.objects.filter(event=event, user=user).update(status=status)
        assert bool(event_conflict_pairs([event], coach)) is included


def test_only_pairs_registered_on_same_event_and_not_guests(pool):
    event, coach, (alice, benoit, _) = pool
    other = _make_event()
    other.coaches.add(coach)
    EventRegistration.objects.filter(event=event, user=benoit).delete()
    EventRegistration.objects.create(event=other, user=benoit, status="confirmed")
    UserBlock.objects.create(blocker=alice, blocked=benoit)
    assert event_conflict_pairs([event, other], coach) == {}
    # A guest name is not a registered User and must not be resolved by name.
    EventRegistration.objects.filter(event=event, user=alice).update(
        bringing_guest=True, guest_name="Benoit"
    )
    assert event_conflict_pairs([event], coach) == {}


def test_automatic_staff_grant_does_not_disclose_other_events(pool, client):
    event, _, (alice, benoit, _) = pool
    UserBlock.objects.create(blocker=alice, blocked=benoit)
    unassigned = _make_coach("unassigned@example.com")
    unassigned.refresh_from_db()
    assert unassigned.is_staff
    other = _make_event()
    other.coaches.add(unassigned.crushcoach)
    client.force_login(unassigned)
    response = _detail(client, event)
    assert response.status_code == 200  # Preserve existing roster access.
    assert response.context["event_conflicts"] == []
    assert b"data-event-conflicts" not in response.content
    response = _list(client)
    assert b"data-event-conflict-warning" not in response.content
    assert all(e.coach_conflict_count == 0 for e in response.context["upcoming_events"])


def test_assignment_removed_and_superuser_exception(pool, client):
    event, coach, (alice, benoit, _) = pool
    UserBlock.objects.create(blocker=alice, blocked=benoit)
    event.coaches.remove(coach)
    assert event_conflict_pairs([event], coach) == {}
    User.objects.filter(pk=coach.user_id).update(is_superuser=True)
    client.force_login(User.objects.get(pk=coach.user_id))
    assert _detail(client, event).context["event_conflicts"] == [("Alice", "Benoit")]


@pytest.mark.parametrize("viewer", ["member", "staff", "anonymous", "inactive"])
def test_non_coaches_cannot_obtain_warning(pool, client, viewer):
    event, coach, (alice, benoit, _) = pool
    UserBlock.objects.create(blocker=alice, blocked=benoit)
    client.logout()
    if viewer in ("member", "staff"):
        if viewer == "staff":
            alice.is_staff = True
            alice.save(update_fields=["is_staff"])
        client.force_login(alice)
    elif viewer == "inactive":
        coach.is_active = False
        coach.save(update_fields=["is_active"])
        client.force_login(coach.user)
    for response in (_detail(client, event), _list(client)):
        assert response.status_code == 302
        assert b"data-event-conflict" not in response.content


def test_fresh_reads_add_remove_and_cancel_without_notifications_or_mutations(
    pool, client
):
    event, coach, (alice, benoit, _) = pool
    before = list(EventRegistration.objects.filter(event=event).values())
    assert b"data-event-conflict" not in _detail(client, event).content
    block = UserBlock.objects.create(blocker=alice, blocked=benoit)
    with patch("crush_lu.services.blocking.apply_block") as apply_block:
        with CaptureQueriesContext(connection) as queries:
            assert event_conflict_pairs([event], coach)
        assert all(q["sql"].lstrip().upper().startswith("SELECT") for q in queries)
        assert _detail(client, event).context["event_conflicts"]
        assert b"1 potential conflict" in _list(client).content
        apply_block.assert_not_called()
    assert list(EventRegistration.objects.filter(event=event).values()) == before
    assert UserBlock.objects.filter(pk=block.pk).exists()
    assert len(mail.outbox) == 0
    block.delete()
    assert _detail(client, event).context["event_conflicts"] == []
    assert b"data-event-conflict-warning" not in _list(client).content
    UserBlock.objects.create(blocker=alice, blocked=benoit)
    event.is_cancelled = True
    event.save(update_fields=["is_cancelled"])
    assert _detail(client, event).context["event_conflicts"] == []


def test_query_count_constant_and_does_not_select_reasons(
    pool, django_assert_num_queries
):
    event, coach, (alice, benoit, _) = pool
    UserBlock.objects.create(blocker=alice, blocked=benoit)
    events = [event]
    for _ in range(12):
        other = _make_event()
        other.coaches.add(coach)
        for user in (alice, benoit):
            EventRegistration.objects.create(event=other, user=user, status="confirmed")
        events.append(other)
    with django_assert_num_queries(2), CaptureQueriesContext(connection) as queries:
        pairs = event_conflict_pairs(events, coach)
    assert len(pairs) == 13
    block_sql = queries.captured_queries[-1]["sql"]
    assert '"reason"' not in block_sql
    assert '"created_at"' not in block_sql
    with django_assert_num_queries(2):
        assert event_conflict_pairs([event], coach)
    uncached_coach = CrushCoach.objects.get(pk=coach.pk)
    with django_assert_num_queries(2):
        assert event_conflict_pairs([event], uncached_coach, viewer=coach.user)


def test_display_convention_and_no_email_fallback(pool, client):
    event, _, (alice, benoit, _) = pool
    alice.last_name = "Example"
    alice.save(update_fields=["last_name"])
    CrushProfile.objects.filter(user=alice).update(show_full_name=True)
    benoit.first_name = ""
    benoit.save(update_fields=["first_name"])
    CrushProfile.objects.filter(user=benoit).update(show_full_name=True)
    UserBlock.objects.create(blocker=benoit, blocked=alice)
    registration = EventRegistration.objects.get(event=event, user=benoit)
    assert _detail(client, event).context["event_conflicts"] == [
        ("Alice Example", f"Participant {registration.pk}")
    ]


def test_block_does_not_change_grouping_graph(pool):
    _, _, (alice, benoit, _) = pool
    applicants = [
        EventApplicant(
            user_id=user.pk,
            registration_id=index,
            gender="NB",
            age=30,
            preferred_genders=(),
            preferred_age_min=18,
            preferred_age_max=99,
            languages=("en",),
            status="applied",
        )
        for index, user in enumerate((alice, benoit), 1)
    ]
    before = build_compatibility_graph(applicants)
    assert before.edges
    UserBlock.objects.create(blocker=alice, blocked=benoit)
    assert build_compatibility_graph(applicants) == before


def test_member_event_pages_never_disclose_conflicts(pool, client):
    event, _, (alice, benoit, _) = pool
    UserBlock.objects.create(blocker=alice, blocked=benoit)
    UserDataConsent.objects.update_or_create(
        user=alice, defaults={"crushlu_consent_given": True}
    )
    client.force_login(alice)
    for path in ("/en/events/", f"/en/events/{event.pk}/"):
        response = client.get(path, HTTP_HOST="crush.lu")
        assert response.status_code == 200
        assert b"data-event-conflict" not in response.content
        assert b"potential conflict" not in response.content
        assert "event_conflicts" not in response.context


@pytest.mark.parametrize(
    "mode, status", [("direct", "confirmed"), ("curated", "applied")]
)
def test_registration_still_succeeds_with_blocked_participant(
    pool, client, mode, status
):
    event, coach, (alice, benoit, _) = pool
    cache.clear()
    event.event_type = "speed_dating"
    event.registration_mode = mode
    event.profile_requirement = "none"
    event.save(update_fields=["event_type", "registration_mode", "profile_requirement"])
    EventRegistration.objects.filter(event=event, user=benoit).delete()
    CrushProfile.objects.filter(user=benoit).update(
        date_of_birth=date(1995, 1, 1), gender="F", event_languages=["en"]
    )
    UserDataConsent.objects.update_or_create(
        user=benoit, defaults={"crushlu_consent_given": True}
    )
    UserBlock.objects.create(blocker=alice, blocked=benoit)
    client.force_login(benoit)
    response = client.post(
        f"/en/events/{event.pk}/register/",
        {"preferred_age_min": "25", "preferred_age_max": "40"},
        HTTP_HOST="crush.lu",
    )
    assert response.status_code == 302
    assert EventRegistration.objects.get(event=event, user=benoit).status == status
    assert event_conflict_pairs([event], coach) == {event.pk: [(alice.pk, benoit.pk)]}
