"""
Tests for Event Poll voting system.

Run with: pytest crush_lu/tests/test_event_polls.py -v
"""

import json
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from crush_lu.models.event_polls import EventPoll, EventPollOption, EventPollVote

User = get_user_model()

pytestmark = pytest.mark.urls("azureproject.urls_crush")


class EventPollModelTests(TestCase):
    """Test EventPoll model properties."""

    def setUp(self):
        now = timezone.now()
        self.poll = EventPoll.objects.create(
            title="Best event type?",
            start_date=now - timedelta(hours=1),
            end_date=now + timedelta(days=7),
            is_published=True,
        )

    def test_is_active_when_published_and_in_range(self):
        self.assertTrue(self.poll.is_active)

    def test_is_active_false_when_unpublished(self):
        self.poll.is_published = False
        self.poll.save()
        self.assertFalse(self.poll.is_active)

    def test_is_active_false_when_not_started(self):
        self.poll.start_date = timezone.now() + timedelta(days=1)
        self.poll.save()
        self.assertFalse(self.poll.is_active)

    def test_is_closed_when_past_end_date(self):
        self.poll.end_date = timezone.now() - timedelta(hours=1)
        self.poll.save()
        self.assertTrue(self.poll.is_closed)

    def test_is_closed_false_when_active(self):
        self.assertFalse(self.poll.is_closed)

    def test_str(self):
        self.assertEqual(str(self.poll), "Best event type?")


class EventPollOptionTests(TestCase):
    """Test EventPollOption model."""

    def setUp(self):
        now = timezone.now()
        self.poll = EventPoll.objects.create(
            title="Test poll",
            start_date=now - timedelta(hours=1),
            end_date=now + timedelta(days=7),
            is_published=True,
        )

    def test_option_ordering(self):
        opt_b = EventPollOption.objects.create(poll=self.poll, name="B", sort_order=2)
        opt_a = EventPollOption.objects.create(poll=self.poll, name="A", sort_order=1)
        options = list(self.poll.options.all())
        self.assertEqual(options[0], opt_a)
        self.assertEqual(options[1], opt_b)

    def test_str(self):
        opt = EventPollOption.objects.create(poll=self.poll, name="Speed Dating")
        self.assertEqual(str(opt), "Speed Dating")


class EventPollVoteTests(TestCase):
    """Test EventPollVote model and constraints."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="voter@test.com", email="voter@test.com", password="testpass123"
        )
        now = timezone.now()
        self.poll = EventPoll.objects.create(
            title="Test poll",
            start_date=now - timedelta(hours=1),
            end_date=now + timedelta(days=7),
            is_published=True,
        )
        self.option = EventPollOption.objects.create(poll=self.poll, name="Option 1")

    def test_unique_together_prevents_duplicate(self):
        EventPollVote.objects.create(poll=self.poll, option=self.option, user=self.user)
        from django.db import IntegrityError

        with self.assertRaises(IntegrityError):
            EventPollVote.objects.create(
                poll=self.poll, option=self.option, user=self.user
            )

    def test_str(self):
        vote = EventPollVote.objects.create(
            poll=self.poll, option=self.option, user=self.user
        )
        self.assertIn("voter@test.com", str(vote))


def _create_approved_profile(user):
    """Helper to create an approved CrushProfile."""
    from crush_lu.models import CrushProfile

    profile = CrushProfile.objects.create(
        user=user,
        date_of_birth="1995-01-01",
        gender="M",
        location="Luxembourg",
    )
    profile.is_approved = True
    profile.save()
    return profile


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class EventPollViewTests(TestCase):
    """Test poll views require authentication and approved profile."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser@test.com",
            email="testuser@test.com",
            password="testpass123",
            first_name="Test",
            last_name="User",
        )
        now = timezone.now()
        self.poll = EventPoll.objects.create(
            title="Test poll",
            start_date=now - timedelta(hours=1),
            end_date=now + timedelta(days=7),
            is_published=True,
        )
        self.option1 = EventPollOption.objects.create(poll=self.poll, name="Option 1")
        self.option2 = EventPollOption.objects.create(poll=self.poll, name="Option 2")

    def test_poll_list_requires_login(self):
        response = self.client.get("/en/polls/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)

    def test_poll_detail_requires_login(self):
        response = self.client.get(f"/en/polls/{self.poll.id}/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)

    def test_poll_list_redirects_without_profile(self):
        self.client.login(username="testuser@test.com", password="testpass123")
        response = self.client.get("/en/polls/")
        self.assertEqual(response.status_code, 302)

    def test_poll_vote_rejects_closed_poll(self):
        """Voting on a closed poll returns 400."""
        _create_approved_profile(self.user)
        self.client.login(username="testuser@test.com", password="testpass123")

        # Close the poll
        self.poll.end_date = timezone.now() - timedelta(hours=1)
        self.poll.save()

        response = self.client.post(
            f"/api/polls/{self.poll.id}/vote/",
            data=json.dumps({"option_ids": [self.option1.id]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_single_choice_vote(self):
        """Single choice poll replaces previous vote."""
        _create_approved_profile(self.user)
        self.client.login(username="testuser@test.com", password="testpass123")

        # Vote for option 1
        response = self.client.post(
            f"/api/polls/{self.poll.id}/vote/",
            data=json.dumps({"option_ids": [self.option1.id]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])

        # Vote again for option 2 (should replace)
        response = self.client.post(
            f"/api/polls/{self.poll.id}/vote/",
            data=json.dumps({"option_ids": [self.option2.id]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        # Should have only 1 vote total
        self.assertEqual(
            EventPollVote.objects.filter(user=self.user, poll=self.poll).count(), 1
        )
        vote = EventPollVote.objects.get(user=self.user, poll=self.poll)
        self.assertEqual(vote.option, self.option2)

    def test_multi_choice_vote(self):
        """Multi-choice poll allows multiple selections."""
        self.poll.allow_multiple_choices = True
        self.poll.save()

        _create_approved_profile(self.user)
        self.client.login(username="testuser@test.com", password="testpass123")

        response = self.client.post(
            f"/api/polls/{self.poll.id}/vote/",
            data=json.dumps({"option_ids": [self.option1.id, self.option2.id]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            EventPollVote.objects.filter(user=self.user, poll=self.poll).count(), 2
        )

    def test_single_choice_rejects_multiple(self):
        """Single-choice poll rejects multiple selections."""
        _create_approved_profile(self.user)
        self.client.login(username="testuser@test.com", password="testpass123")

        response = self.client.post(
            f"/api/polls/{self.poll.id}/vote/",
            data=json.dumps({"option_ids": [self.option1.id, self.option2.id]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_results_api(self):
        """Results API returns vote counts."""
        self.client.login(username="testuser@test.com", password="testpass123")
        response = self.client.get(f"/api/polls/{self.poll.id}/results/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("results", data)
        self.assertEqual(len(data["results"]), 2)
