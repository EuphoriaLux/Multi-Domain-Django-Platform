"""Tests for UserActivityMiddleware cache-gated throttling."""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory
from django.utils import timezone

from crush_lu.middleware import UserActivityMiddleware
from crush_lu.models import UserActivity

User = get_user_model()


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username="activity@example.com",
        email="activity@example.com",
        password="pass12345",
    )


def _make_request(user):
    request = RequestFactory().get("/events/")
    request.user = user
    request.session = {}
    request.META["HTTP_HOST"] = "crush.lu"
    return request


def _run(request):
    mw = UserActivityMiddleware(lambda r: HttpResponse("ok"))
    return mw(request)


def test_first_request_creates_activity_and_primes_cache(user):
    _run(_make_request(user))

    assert UserActivity.objects.filter(user=user).exists()
    assert cache.get(f"user_activity:last_seen:{user.pk}") is not None


def test_second_request_within_throttle_window_skips_db(user):
    _run(_make_request(user))
    activity_before = UserActivity.objects.get(user=user)

    with patch("crush_lu.models.UserActivity.objects") as mock_objects:
        _run(_make_request(user))

    # Throttle must short-circuit before any query runs.
    mock_objects.get_or_create.assert_not_called()
    mock_objects.filter.assert_not_called()

    activity_after = UserActivity.objects.get(user=user)
    assert activity_after.total_visits == activity_before.total_visits


def test_cache_miss_but_db_recent_reprimes_cache_without_writing(user):
    activity = UserActivity.objects.create(
        user=user,
        last_seen=timezone.now() - timedelta(seconds=10),
        total_visits=1,
    )
    cache.delete(f"user_activity:last_seen:{user.pk}")

    _run(_make_request(user))

    # Cache should be reprimed so subsequent requests skip the DB read.
    assert cache.get(f"user_activity:last_seen:{user.pk}") is not None
    # total_visits must not have incremented — we're inside throttle window.
    activity.refresh_from_db()
    assert activity.total_visits == 1


def test_request_after_throttle_window_updates_db_and_primes_cache(user):
    past = timezone.now() - timedelta(seconds=400)
    UserActivity.objects.create(user=user, last_seen=past, total_visits=5)
    cache.delete(f"user_activity:last_seen:{user.pk}")

    _run(_make_request(user))

    activity = UserActivity.objects.get(user=user)
    assert activity.total_visits == 6
    assert (timezone.now() - activity.last_seen).total_seconds() < 5
    assert cache.get(f"user_activity:last_seen:{user.pk}") is not None


def test_cache_backend_failure_does_not_stop_activity_tracking(user):
    """If the cache backend raises, we must still hit the DB throttle
    path rather than silently no-op'ing for the entire outage window."""
    with patch(
        "crush_lu.middleware.cache.get",
        side_effect=ConnectionError("redis down"),
    ), patch(
        "crush_lu.middleware.cache.set",
        side_effect=ConnectionError("redis down"),
    ):
        _run(_make_request(user))

    # DB path still ran, so the UserActivity row exists.
    assert UserActivity.objects.filter(user=user).exists()
