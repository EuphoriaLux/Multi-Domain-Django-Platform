"""Tests for the Event Identity redesign — Phase B (models, migration command,
and the Event Identity form's validation layer).

Spec: docs/superpowers/specs/2026-07-21-crush-event-identity-redesign.md (§8, §13).
The Interest taxonomy (including the O3 additions) is seeded by migrations, so
the slugs asserted below exist in the test DB.
"""

from datetime import timedelta
from io import StringIO

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.utils import timezone

from crush_lu.forms import CrushProfileEventIdentityForm
from crush_lu.models import CrushProfile, Interest


def _make_profile(username, interests=""):
    user = User.objects.create_user(username=username, email=username, password="x")
    return CrushProfile.objects.create(
        user=user,
        date_of_birth=timezone.now().date() - timedelta(days=30 * 365),
        gender="F",
        location="canton-luxembourg",
        interests=interests,
    )


def _run(*args):
    out = StringIO()
    call_command("migrate_interests_to_taxonomy", *args, stdout=out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Migration command: migrate_interests_to_taxonomy
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_dry_run_writes_nothing():
    p = _make_profile("dry@example.com", interests="Yoga, traveling")
    _run()  # no --execute
    p.refresh_from_db()
    assert p.interests_new.count() == 0
    assert p.interests == "Yoga, traveling"  # legacy untouched


@pytest.mark.django_db
def test_execute_populates_interests_new():
    p = _make_profile("exec@example.com", interests="Yoga, traveling")
    _run("--execute")
    assert set(p.interests_new.values_list("slug", flat=True)) == {"yoga", "city-trips"}
    p.refresh_from_db()
    assert p.interests == "Yoga, traveling"  # legacy still untouched


@pytest.mark.django_db
def test_accent_and_case_folding():
    p = _make_profile("fold@example.com", interests="RANDONNÉE, Cinéma")
    _run("--execute")
    assert set(p.interests_new.values_list("slug", flat=True)) == {"hiking", "cinema"}


@pytest.mark.django_db
def test_unmatched_profile_gets_nothing():
    p = _make_profile("nomatch@example.com", interests="oil rig engineer")
    _run("--execute")
    assert p.interests_new.count() == 0
    assert p.interests == "oil rig engineer"


@pytest.mark.django_db
def test_over_8_matches_caps_deterministically_and_retains_overflow():
    # 11 distinct matches; the first 8 by appearance are kept, the rest stay in
    # the legacy field (spec §8.2).
    legacy = "hiking, tennis, surfing, coding, anime, dogs, cars, nightlife, wine, coffee, yoga"
    p = _make_profile("overflow@example.com", interests=legacy)
    _run("--execute")

    slugs = set(p.interests_new.values_list("slug", flat=True))
    assert len(slugs) == 8
    assert slugs == {
        "hiking",
        "ball-racket-sports",  # tennis
        "water-sports",  # surfing
        "tech",  # coding
        "anime-manga",
        "animals-pets",  # dogs
        "cars-motorcycles",  # cars
        "nightlife",
    }
    # Overflow (wine, coffee, yoga) is never deleted from the legacy field.
    p.refresh_from_db()
    assert p.interests == legacy


@pytest.mark.django_db
def test_capped_profile_round_trips_through_the_form():
    legacy = "hiking, tennis, surfing, coding, anime, dogs, cars, nightlife, wine, coffee, yoga"
    p = _make_profile("roundtrip@example.com", interests=legacy)
    _run("--execute")
    ids = list(p.interests_new.values_list("pk", flat=True))
    assert len(ids) == 8

    form = CrushProfileEventIdentityForm(
        data={"interests_new": ids, "ask_me_about": [], "event_vibe": ""},
        instance=p,
    )
    assert form.is_valid(), form.errors
    form.save()
    p.refresh_from_db()
    assert p.interests_new.count() == 8  # no rejection, no silent loss


@pytest.mark.django_db
def test_execute_is_idempotent():
    p = _make_profile("idem@example.com", interests="Yoga, traveling")
    _run("--execute")
    first = set(p.interests_new.values_list("pk", flat=True))
    _run("--execute")  # second run skips already-populated profiles
    p.refresh_from_db()
    assert set(p.interests_new.values_list("pk", flat=True)) == first


# ---------------------------------------------------------------------------
# CrushProfileEventIdentityForm validators
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_form_ask_me_about_subset_is_valid_and_saves():
    p = _make_profile("form1@example.com")
    yoga = Interest.objects.get(slug="yoga")
    city = Interest.objects.get(slug="city-trips")
    form = CrushProfileEventIdentityForm(
        data={
            "interests_new": [yoga.pk, city.pk],
            "ask_me_about": [yoga.pk],
            "event_vibe": "quiet_corner",
        },
        instance=p,
    )
    assert form.is_valid(), form.errors
    form.save()
    p.refresh_from_db()
    assert set(p.interests_new.values_list("slug", flat=True)) == {"yoga", "city-trips"}
    assert p.ask_me_about == [yoga.pk]
    assert p.event_vibe == "quiet_corner"


@pytest.mark.django_db
def test_form_ask_me_about_must_be_subset_of_interests():
    p = _make_profile("form2@example.com")
    yoga = Interest.objects.get(slug="yoga")
    city = Interest.objects.get(slug="city-trips")
    form = CrushProfileEventIdentityForm(
        data={"interests_new": [yoga.pk], "ask_me_about": [city.pk], "event_vibe": ""},
        instance=p,
    )
    assert not form.is_valid()
    assert "ask_me_about" in form.errors


@pytest.mark.django_db
def test_form_rejects_more_than_8_interests():
    p = _make_profile("form3@example.com")
    ids = list(
        Interest.objects.filter(is_active=True).values_list("pk", flat=True)[:9]
    )
    assert len(ids) == 9
    form = CrushProfileEventIdentityForm(
        data={"interests_new": ids, "ask_me_about": [], "event_vibe": ""}, instance=p
    )
    assert not form.is_valid()
    assert "interests_new" in form.errors


@pytest.mark.django_db
def test_form_rejects_more_than_3_ask_me_about():
    p = _make_profile("form4@example.com")
    ids = list(
        Interest.objects.filter(is_active=True).values_list("pk", flat=True)[:4]
    )
    form = CrushProfileEventIdentityForm(
        data={"interests_new": ids, "ask_me_about": ids, "event_vibe": ""}, instance=p
    )
    assert not form.is_valid()
    assert "ask_me_about" in form.errors


@pytest.mark.django_db
def test_form_preserves_a_retired_but_selected_interest():
    """A retired interest the member already picked is offered in the queryset,
    survives a re-save, and remains a valid ask_me_about target (spec §5.2)."""
    p = _make_profile("form5@example.com")
    active = Interest.objects.get(slug="yoga")
    retired = Interest.objects.create(
        slug="retired-x",
        category="games",
        sort_order=900,
        label="RetiredX",
        is_active=False,
    )
    p.interests_new.set([active, retired])

    form = CrushProfileEventIdentityForm(
        data={
            "interests_new": [active.pk, retired.pk],
            "ask_me_about": [retired.pk],
            "event_vibe": "",
        },
        instance=p,
    )
    assert form.is_valid(), form.errors
    form.save()
    p.refresh_from_db()
    assert set(p.interests_new.values_list("slug", flat=True)) == {"yoga", "retired-x"}


@pytest.mark.django_db
def test_form_does_not_offer_retired_interests_to_new_members():
    retired = Interest.objects.create(
        slug="retired-y", category="games", sort_order=901, label="RetiredY", is_active=False
    )
    form = CrushProfileEventIdentityForm()  # no instance → active choices only
    assert retired not in form.fields["interests_new"].queryset
