"""Connect Showcase: a coach swipes Crush Connect cards with a guest at an event.

The guest is anonymous and nothing is stored, so the safety lives in who may
appear (the In the Mix catalogue, minus anyone holding a seat at the event and
the coach) and in which photo loads: the published one, even though the page
runs on a coach's phone, whom the photo endpoint otherwise serves the live
upload.

Requests use literal paths on the crush.lu host, so they go through the
host routing (AGENTS.md: ``reverse()`` would resolve the default urlconf).
"""

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from crush_lu.models import (
    CrushCoach,
    CrushProfile,
    EventRegistration,
    MeetupEvent,
    PublishedProfilePhoto,
)
from crush_lu.services.connect_showcase import (
    SHOWCASE_DECK_SIZE,
    draw_showcase_deck,
    get_showcase_pool,
)
from crush_lu.services.photo_publication import get_public_photo_key
from crush_lu.tests.test_coach_photo_review import _make_candidate, _make_coach

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    # Every test's viewer shares one ratelimit counter (AGENTS.md trap), and
    # replacing a photo must not touch storage.
    cache.clear()
    for field in ("photo_1", "photo_2", "photo_3"):
        monkeypatch.setattr(
            CrushProfile._meta.get_field(field).storage, "delete", lambda name: None
        )


@pytest.fixture
def event():
    return MeetupEvent.objects.create(
        title="Autumn mixer",
        description="",
        event_type="mixer",
        location="Atmos",
        date_time=timezone.now() + timedelta(hours=1),
        registration_deadline=timezone.now(),
        duration_minutes=180,
        max_participants=60,
        is_published=True,
    )


@pytest.fixture
def coach():
    return _make_coach("showcase_coach")


def _member(
    username,
    *,
    gender="F",
    born=date(1995, 1, 1),
    languages=(),
    preferred_genders=(),
    preferred_age=(18, 99),
):
    profile = _make_candidate(username, photo_key=f"users/{username}/photo1.jpg")
    profile.gender = gender
    profile.date_of_birth = born
    profile.save(update_fields=["gender", "date_of_birth"])
    membership = profile.user.crush_connect_membership
    membership.languages = list(languages)
    membership.preferred_genders = list(preferred_genders)
    membership.preferred_age_min, membership.preferred_age_max = preferred_age
    membership.save()
    return profile.user


def _years_old(years):
    # Born on 1 January: the birthday has always passed (or is today).
    return date(date.today().year - years, 1, 1)


def _pool(event, coach, **filters):
    return {
        user.username
        for user in get_showcase_pool(event=event, coach_user=coach.user, **filters)
    }


def _deck_url(event, **params):
    from urllib.parse import urlencode

    url = f"/en/coach/events/{event.pk}/connect-showcase/"
    return f"{url}?{urlencode(params, doseq=True)}" if params else url


def _client(user):
    client = Client(HTTP_HOST="crush.lu")
    client.force_login(user)
    return client


# --- Access ------------------------------------------------------------------


def test_non_coach_is_turned_away(event):
    member = _member("plain_member")
    client = _client(member)
    response = client.get(_deck_url(event))
    assert response.status_code == 302
    assert "showcase" not in response.url


def test_filter_screen_shows_no_cards(event, coach):
    _member("anna")
    client = _client(coach.user)
    html = client.get(_deck_url(event)).content.decode()
    assert 'name="show"' in html
    assert "data-showcase-card" not in html
    assert "Anna" not in html


def test_event_page_links_to_the_showcase(event, coach):
    client = _client(coach.user)
    html = client.get(f"/en/coach/events/{event.pk}/").content.decode()
    assert f'href="/en/coach/events/{event.pk}/connect-showcase/"' in html


# --- Filters -----------------------------------------------------------------


def test_filters_narrow_by_gender_age_and_language(event, coach):
    _member("woman_fr_31", gender="F", born=_years_old(31), languages=["fr"])
    _member("woman_de_45", gender="F", born=_years_old(45), languages=["de"])
    _member("man_fr_31", gender="M", born=_years_old(31), languages=["fr", "en"])

    assert _pool(event, coach) == {"woman_fr_31", "woman_de_45", "man_fr_31"}
    assert _pool(event, coach, genders=["F"]) == {"woman_fr_31", "woman_de_45"}
    assert _pool(event, coach, age_min=40, age_max=50) == {"woman_de_45"}
    assert _pool(event, coach, languages=["fr"]) == {"woman_fr_31", "man_fr_31"}
    assert _pool(event, coach, languages=["en", "de"]) == {"woman_de_45", "man_fr_31"}
    assert _pool(event, coach, genders=["F"], languages=["fr"]) == {"woman_fr_31"}


def test_language_filter_falls_back_to_event_languages_like_the_card(event, coach):
    user = _member("event_only_langs")
    user.crushprofile.event_languages = ["lu"]
    user.crushprofile.save(update_fields=["event_languages"])

    assert _pool(event, coach, languages=["lu"]) == {"event_only_langs"}
    assert _pool(event, coach, languages=["fr"]) == set()


def test_guest_filter_applies_each_candidates_own_preferences(event, coach):
    _member("wants_women", preferred_genders=["F"])
    _member("open_to_all")
    _member("wants_30_to_40", preferred_age=(30, 40))

    assert _pool(event, coach, guest_gender="M") == {"open_to_all", "wants_30_to_40"}
    assert _pool(event, coach, guest_gender="F") == {
        "wants_women",
        "open_to_all",
        "wants_30_to_40",
    }
    assert _pool(event, coach, guest_age=25) == {"wants_women", "open_to_all"}
    assert _pool(event, coach, guest_gender="M", guest_age=35) == {
        "open_to_all",
        "wants_30_to_40",
    }


# --- Who never appears -------------------------------------------------------


def test_members_outside_the_catalogue_never_appear(event, coach):
    _member("listed")
    paused = _member("paused")
    paused.crush_connect_membership.paused_at = timezone.now()
    paused.crush_connect_membership.save(update_fields=["paused_at"])
    excluded = _member("excluded")
    excluded.crush_connect_membership.excluded_by_coach = True
    excluded.crush_connect_membership.save(update_fields=["excluded_by_coach"])
    no_consent = _member("no_consent")
    no_consent.crush_connect_membership.photo_share_consent = False
    no_consent.crush_connect_membership.save(update_fields=["photo_share_consent"])
    dormant = _member("dormant")
    dormant.last_login = timezone.now() - timedelta(days=45)
    dormant.save(update_fields=["last_login"])

    assert _pool(event, coach) == {"listed"}


def test_seat_holders_at_this_event_and_the_coach_never_appear(event, coach):
    _member("elsewhere")
    for username, status in (
        ("confirmed_here", "confirmed"),
        ("attended_here", "attended"),
        ("pending_here", "pending"),
        ("waitlisted_here", "waitlist"),
    ):
        EventRegistration.objects.create(
            user=_member(username), event=event, status=status
        )
    coach_member = _member("coach_member")
    other_coach = CrushCoach.objects.create(user=coach_member, is_active=True)

    assert _pool(event, other_coach) == {"elsewhere", "waitlisted_here"}
    # The member who coaches is a candidate for every other coach's guests.
    assert "coach_member" in _pool(event, coach)


# --- The deck ----------------------------------------------------------------


def test_deck_is_a_capped_random_sample(event, coach):
    for number in range(SHOWCASE_DECK_SIZE + 3):
        _member(f"member_{number}")
    pool = get_showcase_pool(event=event, coach_user=coach.user)

    deck = draw_showcase_deck(pool)

    assert len(pool) == SHOWCASE_DECK_SIZE + 3
    assert len(deck) == SHOWCASE_DECK_SIZE
    assert len({user.pk for user in deck}) == SHOWCASE_DECK_SIZE


def test_deck_renders_the_member_safe_card_only(event, coach):
    user = _member("anna", born=_years_old(31))
    profile = user.crushprofile
    user.last_name = "Surnameson"
    user.save(update_fields=["last_name"])
    client = _client(coach.user)

    response = client.get(_deck_url(event, show=1, genders=["F"], age_min=25))

    html = response.content.decode()
    assert response.status_code == 200
    assert html.count("data-showcase-card") == 1
    assert 'data-name="Anna"' in html
    assert profile.age_range in html
    assert "Surnameson" not in html
    assert user.email not in html
    # "Change filters" keeps the filters but drops the deck.
    assert 'href="?genders=F&amp;age_min=25"' in html


def _add_published_photo(profile, field):
    key = f"users/{profile.user.username}/{field}.jpg"
    setattr(profile, field, key)
    profile.save(update_fields=[field])
    PublishedProfilePhoto.objects.create(
        profile=profile, photo_field=field, photo_key=key
    )


def test_every_photo_loads_as_members_see_it(event, coach):
    """All photos show, each asking the endpoint for the published file.

    The deck is rendered for a coach, and the endpoint serves coaches the
    live upload. ``?view=public`` makes it decide when the image loads, so a
    replacement uploaded after the deck was drawn still cannot reach the guest.
    """
    profile = _member("anna").crushprofile
    _add_published_photo(profile, "photo_2")
    _add_published_photo(profile, "photo_3")
    # photo_3 is replaced: members keep seeing the earlier published file.
    profile.photo_3 = "users/anna/photo_3_new.jpg"
    profile.save(update_fields=["photo_3"])
    client = _client(coach.user)

    html = client.get(_deck_url(event, show=1)).content.decode()

    photo_url = f"/en/media/profile/{profile.user_id}/"
    for field in ("photo_1", "photo_2", "photo_3"):
        assert f'"{photo_url}{field}/?view=public"' in html
        assert f'"{photo_url}{field}/"' not in html


def test_member_with_a_held_replacement_appears_with_the_published_photo(event, coach):
    replaced = _member("replaced_photo").crushprofile
    replaced.photo_1 = "users/replaced_photo/new.jpg"
    replaced.save(update_fields=["photo_1"])
    replaced.refresh_from_db()

    assert _pool(event, coach) == {"replaced_photo"}
    assert (
        get_public_photo_key(replaced, "photo_1") == "users/replaced_photo/photo1.jpg"
    )


def test_public_view_serves_a_coach_the_published_file(event, coach, settings):
    settings.AZURE_ACCOUNT_NAME = ""
    profile = _member("anna").crushprofile
    profile.photo_1 = "users/anna/new.jpg"
    profile.save(update_fields=["photo_1"])
    opened = []

    def exists(path):
        # ``photo.path`` is native, so use "/" on Windows too.
        opened.append(Path(path).as_posix())
        return False

    url = f"/en/media/profile/{profile.user_id}/photo_1/"
    client = _client(coach.user)
    with patch("crush_lu.views_media.os.path.exists", side_effect=exists):
        client.get(f"{url}?view=public")
        client.get(url)

    assert opened[0].endswith("users/anna/photo1.jpg")
    assert opened[1].endswith("users/anna/new.jpg")


def test_other_cards_keep_viewer_decided_photo_urls(event, coach):
    from django.template.loader import render_to_string

    profile = _member("anna").crushprofile
    _add_published_photo(profile, "photo_2")

    def gallery(**extra):
        return render_to_string(
            "crush_lu/crush_connect/_reviewed_secondary_photos.html",
            {"profile": profile, **extra},
        )

    assert "/photo_2/" in gallery()
    assert "/photo_2/?view=public" not in gallery()
    assert "/photo_2/?view=public" in gallery(public_view=True)


def test_deck_queries_do_not_grow_with_the_deck(event, coach):
    def deck_queries():
        cache.clear()
        client = _client(coach.user)
        with CaptureQueriesContext(connection) as queries:
            assert client.get(_deck_url(event, show=1)).status_code == 200
        return len(queries)

    for number in range(2):
        _member(f"small_{number}")
    deck_queries()  # warm per-process caches (site, translations)
    small = deck_queries()
    for number in range(4):
        _member(f"large_{number}")

    assert deck_queries() == small


def test_empty_pool_offers_to_change_filters(event, coach):
    _member("anna", gender="F")
    client = _client(coach.user)

    html = client.get(_deck_url(event, show=1, genders=["M"])).content.decode()

    assert "data-showcase-card" not in html
    assert "No Crush Connect member matches these filters." in html


def test_invalid_filters_return_to_the_form(event, coach):
    _member("anna")
    client = _client(coach.user)

    html = client.get(_deck_url(event, show=1, age_min=12)).content.decode()

    assert "data-showcase-card" not in html
    assert 'name="show"' in html
    assert 'role="alert"' in html


def test_crossed_age_range_is_swapped(event, coach):
    _member("anna", born=_years_old(31))
    client = _client(coach.user)

    html = client.get(_deck_url(event, show=1, age_min=40, age_max=25)).content.decode()

    assert html.count("data-showcase-card") == 1


def test_swipes_are_never_posted(event, coach):
    _member("anna")
    client = _client(coach.user)

    response = client.post(_deck_url(event), {"show": 1})

    assert response.status_code == 405
