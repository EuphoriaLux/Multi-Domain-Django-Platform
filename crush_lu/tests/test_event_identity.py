"""Tests for the Event Identity redesign — Phase B (models, migration command,
and the Event Identity form's validation layer).

Spec: docs/superpowers/specs/2026-07-21-crush-event-identity-redesign.md (§8, §13).
The Interest taxonomy (including the O3 additions) is seeded by migrations, so
the slugs asserted below exist in the test DB.
"""

from datetime import timedelta
from importlib import import_module
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
def test_matches_are_ordered_by_position_within_a_token():
    """Within one token (no split delimiter) matches follow text position, not
    keyword-rule order, so the >8 cap keeps the earliest interests."""
    from crush_lu.models import Interest
    from crush_lu.management.commands.migrate_interests_to_taxonomy import match_slugs

    sort_key = {
        i.slug: (i.category, i.sort_order)
        for i in Interest.objects.filter(is_active=True)
    }
    # "yoga" precedes "hiking" in the text even though the hiking rule is first
    # in _RAW_RULES; position ordering must yield yoga before hiking.
    assert match_slugs("yoga and hiking", sort_key) == ["yoga", "hiking"]
    assert match_slugs("hiking then yoga", sort_key) == ["hiking", "yoga"]


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


@pytest.mark.django_db
def test_repopulate_never_removes_member_added_interests():
    """--repopulate merges inferred interests without wiping selections a member
    made through the UI that have no legacy-text match."""
    from crush_lu.models import Interest

    p = _make_profile("repop@example.com", interests="yoga")
    manual = Interest.objects.get(slug="chess")  # no "chess" in the legacy text
    p.interests_new.add(manual)

    _run("--repopulate", "--execute")

    slugs = set(p.interests_new.values_list("slug", flat=True))
    assert "chess" in slugs  # member's manual selection preserved
    assert "yoga" in slugs  # inferred from legacy text, added alongside


@pytest.mark.django_db
def test_repopulate_adds_nothing_to_an_over_cap_profile():
    """A profile already at/over the 8-cap (reachable via the admin's unvalidated
    filter_horizontal) must get nothing added — `room` clamps at 0 rather than
    taking a negative slice."""
    from crush_lu.models import Interest

    p = _make_profile("overcap@example.com", interests="yoga")  # would infer 'yoga'
    nine = list(Interest.objects.exclude(slug="yoga")[:9])
    p.interests_new.set(nine)
    assert p.interests_new.count() == 9

    _run("--repopulate", "--execute")

    p.refresh_from_db()
    assert p.interests_new.count() == 9  # nothing added
    assert "yoga" not in set(p.interests_new.values_list("slug", flat=True))


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
def test_admin_form_enforces_the_8_interest_cap():
    """The admin's filter_horizontal widget must not let staff save 9+
    interests_new (a state the member form later rejects)."""
    from django.core.exceptions import ValidationError

    from crush_lu.admin.profiles import CrushProfileAdminForm

    active = Interest.objects.filter(is_active=True)
    nine = list(active[:9])
    eight = list(active[:8])

    form = CrushProfileAdminForm()
    form.cleaned_data = {"interests_new": nine}
    with pytest.raises(ValidationError):
        form.clean_interests_new()

    form.cleaned_data = {"interests_new": eight}
    assert form.clean_interests_new() == eight  # 8 is allowed


@pytest.mark.django_db
def test_admin_form_rejects_bad_ask_me_about_values():
    """``ask_me_about`` is a raw JSON field in the admin — staff must not be
    able to persist a value the member form would then reject."""
    from django.core.exceptions import ValidationError

    from crush_lu.admin.profiles import CrushProfileAdminForm

    form = CrushProfileAdminForm()
    for bad in ([1, 2, 3, 4], [1, 1], {"a": 1}, ["not-an-id"]):
        form.cleaned_data = {"ask_me_about": bad}
        with pytest.raises(ValidationError):
            form.clean_ask_me_about()

    form.cleaned_data = {"ask_me_about": [7, 9]}
    assert form.clean_ask_me_about() == [7, 9]  # two distinct ids are fine


@pytest.mark.django_db
def test_admin_form_requires_ask_me_about_to_be_a_selected_interest():
    from crush_lu.admin.profiles import CrushProfileAdminForm

    yoga = Interest.objects.get(slug="yoga")
    city = Interest.objects.get(slug="city-trips")

    form = CrushProfileAdminForm()
    form._errors = {}
    form.cleaned_data = {"interests_new": [yoga], "ask_me_about": [city.pk]}
    form.clean()
    assert "ask_me_about" in form._errors  # not among the selected interests

    form = CrushProfileAdminForm()
    form._errors = {}
    form.cleaned_data = {"interests_new": [yoga, city], "ask_me_about": [city.pk]}
    form.clean()
    assert not form._errors


@pytest.mark.django_db
def test_form_does_not_offer_retired_interests_to_new_members():
    retired = Interest.objects.create(
        slug="retired-y", category="games", sort_order=901, label="RetiredY", is_active=False
    )
    form = CrushProfileEventIdentityForm()  # no instance → active choices only
    assert retired not in form.fields["interests_new"].queryset


# ---------------------------------------------------------------------------
# Migration 0198: the ConnectInterest → Interest permission rename
# ---------------------------------------------------------------------------
_NEW_CODENAMES = {"add_interest", "change_interest", "delete_interest", "view_interest"}


def _seed_old_permissions(content_type):
    """Put a group on the four pre-rename codenames, as an upgraded deployment
    has them just before ``create_permissions`` mints the new ones."""
    from django.contrib.auth.models import Group, Permission

    Permission.objects.filter(content_type=content_type).delete()
    perms = [
        Permission.objects.create(
            content_type=content_type,
            codename=f"{action}_connectinterest",
            name=f"Can {action}",
        )
        for action in ("add", "change", "delete", "view")
    ]
    group = Group.objects.create(name="interest-editors")
    group.permissions.set(perms)
    return group


@pytest.mark.django_db
def test_permission_rename_carries_group_grants():
    """The normal path: 0194's injected ``RenameContentType`` already moved the
    row to ``crush_lu/interest``, but the codenames are still the old ones."""
    from django.apps import apps as global_apps
    from django.contrib.contenttypes.models import ContentType

    migration = import_module("crush_lu.migrations.0198_rename_interest_permissions")
    ct = ContentType.objects.get_for_model(Interest)
    group = _seed_old_permissions(ct)

    migration.forwards(global_apps, None)

    assert set(group.permissions.values_list("codename", flat=True)) == _NEW_CODENAMES


@pytest.mark.django_db
def test_permission_rename_falls_back_to_the_old_content_type_row():
    """``RenameContentType`` bails out when a router excludes contenttypes and
    reverts itself on ``IntegrityError`` — the row then still says
    ``connectinterest``, and the grants must travel anyway."""
    from django.apps import apps as global_apps
    from django.contrib.contenttypes.models import ContentType

    migration = import_module("crush_lu.migrations.0198_rename_interest_permissions")
    try:
        ContentType.objects.filter(app_label="crush_lu", model="interest").delete()
        ContentType.objects.clear_cache()
        ct = ContentType.objects.create(app_label="crush_lu", model="connectinterest")
        group = _seed_old_permissions(ct)

        migration.forwards(global_apps, None)

        codenames = set(group.permissions.values_list("codename", flat=True))
        assert codenames == _NEW_CODENAMES
    finally:
        ContentType.objects.clear_cache()
