"""populate_event_attendees refuses to run off staging.

Marking a verified member as attended can event-verify them and assign them a
coach (``assign_coach_on_first_attendance`` in ``crush_lu/signals.py``), so the
seeding command must never touch a production database.
"""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from crush_lu.models import EventRegistration

pytestmark = pytest.mark.django_db


def test_refuses_without_django_env_and_debug(
    monkeypatch, settings, sample_event, test_user_with_profile
):
    monkeypatch.delenv("DJANGO_ENV", raising=False)
    settings.DEBUG = False

    with pytest.raises(CommandError, match="only runs on staging"):
        call_command("populate_event_attendees", event_id=sample_event.id, count=5)

    assert not EventRegistration.objects.filter(event=sample_event).exists()


def test_refuses_on_production(
    monkeypatch, settings, sample_event, test_user_with_profile
):
    monkeypatch.setenv("DJANGO_ENV", "production")
    settings.DEBUG = False

    with pytest.raises(CommandError):
        call_command("populate_event_attendees", event_id=sample_event.id, count=5)

    assert not EventRegistration.objects.filter(event=sample_event).exists()


def test_runs_on_staging(monkeypatch, settings, sample_event, test_user_with_profile):
    monkeypatch.setenv("DJANGO_ENV", "staging")
    settings.DEBUG = False
    user, _profile = test_user_with_profile

    call_command("populate_event_attendees", event_id=sample_event.id, count=5)

    registration = EventRegistration.objects.get(event=sample_event, user=user)
    assert registration.status == "attended"
