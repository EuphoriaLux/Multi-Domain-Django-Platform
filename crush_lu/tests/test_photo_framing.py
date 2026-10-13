"""Coach-set photo focus points (``ProfilePhotoFraming``).

A focus belongs to a file, not a slot: every surface must look it up for the
file the viewer is actually served (live for the owner and coaches, published
for other members).

Spec: ai-memory-hub/specs/2026-10-13-crush-photo-framing.md
"""

import json
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.template import Context, Template
from django.test import Client, RequestFactory, TestCase
from PIL import Image

from crush_lu.models import CrushProfile, ProfilePhotoFraming
from crush_lu.services.photo_framing import (
    PhotoFramingError,
    clear_focus,
    get_focus,
    move_focus,
    primary_photo_positions,
    set_focus,
    square_crop_box,
)
from crush_lu.tests.test_coach_photo_review import _make_candidate, _make_coach

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]

User = get_user_model()

KEY = "users/1/photos/photo1.jpg"  # _make_candidate's published primary photo
NEW_KEY = "users/1/photos/replacement.jpg"


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


def _viewer(username="viewer"):
    return User.objects.create_user(
        username=username, email=f"{username}@example.com", password="x"
    )


def _frame(profile, key, x, y):
    return ProfilePhotoFraming.objects.create(
        profile=profile, photo_key=key, focus_x=x, focus_y=y
    )


def _held_replacement(profile):
    """Members keep seeing KEY (published) while NEW_KEY waits for review."""
    CrushProfile.objects.filter(pk=profile.pk).update(photo_1=NEW_KEY)
    profile.refresh_from_db()
    return profile


def _render(template, user, profile):
    request = RequestFactory().get("/")
    request.user = user
    return Template("{% load crush_media %}" + template).render(
        Context({"request": request, "profile": profile})
    )


# --- Service -----------------------------------------------------------------


def test_set_focus_stores_clamped_rounded_percentages():
    coach, profile = _make_coach(), _make_candidate()

    assert set_focus(coach, profile, "photo_1", KEY, 140, -5) == (100, 0)
    assert set_focus(coach, profile, "photo_1", KEY, "33.6", 12.4) == (34, 12)

    row = ProfilePhotoFraming.objects.get(profile=profile, photo_key=KEY)
    assert (row.focus_x, row.focus_y, row.set_by) == (34, 12, coach)
    assert get_focus(CrushProfile.objects.get(pk=profile.pk), KEY) == (34, 12)


def test_set_focus_refuses_a_photo_replaced_since_the_page_loaded():
    coach, profile = _make_coach(), _make_candidate()

    with pytest.raises(PhotoFramingError) as refused:
        set_focus(coach, profile, "photo_1", "users/1/photos/older.jpg", 40, 40)

    assert refused.value.status == 409
    assert not ProfilePhotoFraming.objects.exists()


@pytest.mark.parametrize(
    "field, x, y",
    [
        ("photo_9", 40, 40),
        ("photo_1", "left", 40),
        ("photo_1", None, 40),
        ("photo_1", float("nan"), 40),
    ],
)
def test_set_focus_refuses_invalid_input(field, x, y):
    coach, profile = _make_coach(), _make_candidate()

    with pytest.raises(PhotoFramingError) as refused:
        set_focus(coach, profile, field, KEY, x, y)

    assert refused.value.status == 400
    assert not ProfilePhotoFraming.objects.exists()


def test_clear_and_move_focus():
    profile = _make_candidate()
    _frame(profile, KEY, 30, 40)

    move_focus(profile, KEY, NEW_KEY)
    assert get_focus(CrushProfile.objects.get(pk=profile.pk), NEW_KEY) == (30, 40)
    assert get_focus(CrushProfile.objects.get(pk=profile.pk), KEY) is None

    _held_replacement(profile)
    clear_focus(profile, "photo_1", NEW_KEY)
    assert not ProfilePhotoFraming.objects.exists()


def test_a_saved_focus_is_seen_by_the_same_profile_instance():
    coach, profile = _make_coach(), _make_candidate()
    assert get_focus(profile, KEY) is None  # memoised as "no focus"

    set_focus(coach, profile, "photo_1", KEY, 20, 30)

    assert get_focus(profile, KEY) == (20, 30)


def test_the_current_file_is_checked_under_a_lock_inside_the_write(monkeypatch):
    """A member upload must not slip in between the check and the write.
    SQLite ignores row locks, so assert the shape: the check takes the
    profile row lock inside the transaction that writes the framing."""
    from django.db import connection
    from django.db.models.query import QuerySet

    coach, profile = _make_coach(), _make_candidate()
    original = QuerySet.select_for_update
    depths = []

    def spy(queryset, *args, **kwargs):
        depths.append((queryset.model, len(connection.savepoint_ids)))
        return original(queryset, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "select_for_update", spy)
    outside = len(connection.savepoint_ids)
    set_focus(coach, profile, "photo_1", KEY, 10, 10)
    clear_focus(profile, "photo_1", KEY)

    profile_locks = [depth for model, depth in depths if model is CrushProfile]
    assert len(profile_locks) == 2  # one per write
    assert all(depth > outside for depth in profile_locks)


def test_without_a_prefetch_a_profile_reads_its_framings_once(
    django_assert_num_queries,
):
    profile = _make_candidate()
    for slot, key in enumerate(("a.jpg", "b.jpg", "c.jpg")):
        _frame(profile, key, slot * 10, slot * 10)
    fresh = CrushProfile.objects.get(pk=profile.pk)

    with django_assert_num_queries(1):
        assert [
            get_focus(fresh, key) for key in ("a.jpg", "b.jpg", "c.jpg", "d.jpg")
        ] == [
            (0, 0),
            (10, 10),
            (20, 20),
            None,
        ]


@pytest.mark.parametrize(
    "size, focus, box",
    [
        ((300, 400), None, (0, 25, 300, 325)),  # object-face default
        ((300, 400), (50, 0), (0, 0, 300, 300)),
        ((300, 400), (50, 100), (0, 100, 300, 400)),
        ((400, 300), None, (50, 0, 350, 300)),
        ((400, 300), (0, 50), (0, 0, 300, 300)),
        ((400, 300), (100, 50), (100, 0, 400, 300)),
    ],
)
def test_square_crop_box_uses_object_position_arithmetic(size, focus, box):
    assert square_crop_box(*size, focus) == box


# --- Rendering: the focus of the file the viewer is served -------------------


def test_photos_without_a_focus_render_no_inline_position():
    profile = _make_candidate()

    html = _render("{% profile_photo profile 'photo_1' %}", profile.user, profile)
    style = _render("{% profile_photo_style profile 'photo_1' %}", _viewer(), profile)

    assert "object-face" in html
    assert "object-position" not in html
    assert style == ""


def test_each_viewer_gets_the_focus_of_the_file_they_are_served():
    profile = _held_replacement(_make_candidate())
    _frame(profile, KEY, 10, 20)
    _frame(profile, NEW_KEY, 80, 90)
    coach = _make_coach()
    member = _viewer()
    component = "{% profile_photo profile 'photo_1' %}"
    tag = "{% profile_photo_style profile 'photo_1' %}"

    # Owner and coaches see the live upload; other members the published one.
    assert "object-position: 80% 90%" in _render(component, profile.user, profile)
    assert "object-position: 80% 90%" in _render(component, coach.user, profile)
    assert "object-position: 10% 20%" in _render(component, member, profile)
    assert "object-position: 10% 20%" in _render(
        "{% profile_photo profile 'photo_1' public_view=True %}", coach.user, profile
    )
    assert _render(tag, coach.user, profile) == 'style="object-position: 80% 90%"'
    assert _render(tag, member, profile) == 'style="object-position: 10% 20%"'


def test_the_owner_sees_the_focus_of_their_saved_photo_in_the_editor():
    from django.template.loader import render_to_string

    profile = _make_candidate()
    _frame(profile, KEY, 40, 30)
    request = RequestFactory().get("/")
    request.user = profile.user

    position = _render(
        "{% profile_photo_position profile 'photo_1' %}", profile.user, profile
    )
    card = render_to_string(
        "crush_lu/partials/photo_card.html",
        {"slot": 1, "photo": profile.photo_1, "is_main": True},
        request=request,
    )

    assert position == "40% 30%"
    assert 'style="object-position: 40% 30%"' in card


def test_lobby_lists_in_one_request_share_one_focus_batch():
    from crush_lu.services import event_lobby

    viewer = _viewer()
    calls = []

    def positions(_viewer, user_ids):
        calls.append(set(user_ids))
        return {user_id: "10% 20%" for user_id in user_ids if user_id == 1}

    with patch(
        "crush_lu.services.photo_framing.primary_photo_positions", side_effect=positions
    ):
        roster = event_lobby._photo_focus(viewer, [1, 2, 3])
        mutuals = event_lobby._photo_focus(viewer, [1, 3])
        later = event_lobby._photo_focus(viewer, [3, 4])

    assert calls == [{1, 2, 3}, {4}]
    assert roster == {1: "10% 20%"} and mutuals == {1: "10% 20%"} and later == {}


def test_batched_positions_follow_the_photo_route_rule():
    framed = _held_replacement(_make_candidate())
    _frame(framed, KEY, 10, 20)
    _frame(framed, NEW_KEY, 80, 90)
    plain = _make_candidate(username="candidate_2", photo_key="users/2/photos/p.jpg")
    ids = [framed.user_id, plain.user_id]

    assert primary_photo_positions(_viewer(), ids) == {framed.user_id: "10% 20%"}
    assert primary_photo_positions(_make_coach().user, ids) == {
        framed.user_id: "80% 90%"
    }


# --- Coach page and endpoint -------------------------------------------------


def _page(profile):
    return f"/en/coach/member/{profile.user_id}/photos/"


def _post(client, profile, payload):
    return client.post(
        _page(profile) + "focus/",
        data=json.dumps(payload),
        content_type="application/json",
    )


def test_only_active_coaches_reach_the_framing_page():
    profile = _make_candidate()
    client = Client()

    assert client.get(_page(profile)).status_code == 302
    client.force_login(_viewer())
    assert client.get(_page(profile)).status_code == 302
    response = _post(
        client, profile, {"photo_field": "photo_1", "photo_key": KEY, "focus_x": 1}
    )
    assert response.status_code == 302
    assert not ProfilePhotoFraming.objects.exists()


def test_coach_frames_resets_and_is_refused_a_stale_photo():
    coach, profile = _make_coach(), _make_candidate()
    client = Client()
    client.force_login(coach.user)

    page = client.get(_page(profile))
    assert page.status_code == 200
    assert f'data-photo-key="{KEY}"' in page.content.decode()
    assert 'data-focus-y="25"' in page.content.decode()  # object-face default

    saved = _post(
        client,
        profile,
        {"photo_field": "photo_1", "photo_key": KEY, "focus_x": 30, "focus_y": 15},
    )
    assert saved.status_code == 200
    assert saved.json() == {
        "success": True,
        "focus_x": 30,
        "focus_y": 15,
        "has_focus": True,
    }
    assert 'data-focus-x="30"' in client.get(_page(profile)).content.decode()

    stale = _post(
        client,
        profile,
        {"photo_field": "photo_1", "photo_key": "old.jpg", "focus_x": 1, "focus_y": 1},
    )
    assert stale.status_code == 409

    reset = _post(
        client, profile, {"photo_field": "photo_1", "photo_key": KEY, "reset": True}
    )
    assert reset.json()["has_focus"] is False
    assert not ProfilePhotoFraming.objects.exists()


@pytest.mark.parametrize("body", ["not json", "[]", '{"photo_field": 1}'])
def test_framing_endpoint_rejects_malformed_payloads(body):
    coach, profile = _make_coach(), _make_candidate()
    client = Client()
    client.force_login(coach.user)

    response = client.post(
        _page(profile) + "focus/", data=body, content_type="application/json"
    )

    assert response.status_code == 400


# --- Server-side consumers ---------------------------------------------------


def _two_band_photo():
    """300x400 portrait: red top quarter, blue below."""
    img = Image.new("RGB", (300, 400), (0, 0, 255))
    img.paste((255, 0, 0), (0, 0, 300, 100))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.parametrize(
    "focus, top_is_red", [(None, True), ((50, 0), True), ((50, 100), False)]
)
def test_wallet_thumbnail_crops_around_the_focus(focus, top_is_red):
    from crush_lu.wallet.apple_pass import generate_pass_thumbnail

    photo = MagicMock()
    photo.read.return_value = _two_band_photo()
    profile = MagicMock(pk=7, show_photo_on_wallet=True, photo_1=photo)

    with patch("crush_lu.services.photo_framing.get_focus", return_value=focus):
        thumbs = generate_pass_thumbnail(profile)

    with Image.open(BytesIO(thumbs["thumbnail@2x.png"])) as thumb:
        red, _green, blue, *_alpha = thumb.convert("RGBA").getpixel((90, 2))
    assert (red > blue) is top_is_red


def test_reprocessing_carries_the_focus_to_the_new_file(tmp_path, monkeypatch):
    from crush_lu.management.commands.reprocess_photos import Command

    storage = FileSystemStorage(location=tmp_path)
    monkeypatch.setattr(CrushProfile._meta.get_field("photo_1"), "storage", storage)
    monkeypatch.setattr(
        "crush_lu.management.commands.reprocess_photos.process_uploaded_image",
        lambda *args: ContentFile(b"small", name="processed.jpg"),
    )
    profile = _make_candidate(has_photo=False)
    profile.photo_1.save("original.jpg", ContentFile(b"original" * 1000), save=True)
    old_key = profile.photo_1.name
    _frame(profile, old_key, 35, 15)

    stats = {"processed": 0, "skipped": 0, "errors": 0}
    with TestCase.captureOnCommitCallbacks(execute=True):
        Command()._process_photo(profile, "photo_1", profile.photo_1, False, stats)

    profile.refresh_from_db()
    assert stats["processed"] == 1
    assert profile.photo_1.name != old_key
    assert get_focus(profile, profile.photo_1.name) == (35, 15)
    assert get_focus(profile, old_key) is None
