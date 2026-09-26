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

from crush_lu.models.event_polls import (
    EventPoll,
    EventPollOption,
    EventPollSuggestion,
    EventPollVote,
)

User = get_user_model()

pytestmark = pytest.mark.urls("azureproject.urls_crush")


class EventPollModelTests(TestCase):
    """Test EventPoll model properties."""

    def setUp(self):
        now = timezone.now()
        self.poll = EventPoll.objects.create(
            title='Best event type?',
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
        self.assertEqual(str(self.poll), 'Best event type?')


class EventPollOptionTests(TestCase):
    """Test EventPollOption model."""

    def setUp(self):
        now = timezone.now()
        self.poll = EventPoll.objects.create(
            title='Test poll',
            start_date=now - timedelta(hours=1),
            end_date=now + timedelta(days=7),
            is_published=True,
        )

    def test_option_ordering(self):
        opt_b = EventPollOption.objects.create(poll=self.poll, name='B', sort_order=2)
        opt_a = EventPollOption.objects.create(poll=self.poll, name='A', sort_order=1)
        options = list(self.poll.options.all())
        self.assertEqual(options[0], opt_a)
        self.assertEqual(options[1], opt_b)

    def test_str(self):
        opt = EventPollOption.objects.create(poll=self.poll, name='Speed Dating')
        self.assertEqual(str(opt), 'Speed Dating')


class EventPollVoteTests(TestCase):
    """Test EventPollVote model and constraints."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='voter@test.com', email='voter@test.com', password='testpass123'
        )
        now = timezone.now()
        self.poll = EventPoll.objects.create(
            title='Test poll',
            start_date=now - timedelta(hours=1),
            end_date=now + timedelta(days=7),
            is_published=True,
        )
        self.option = EventPollOption.objects.create(poll=self.poll, name='Option 1')

    def test_unique_together_prevents_duplicate(self):
        EventPollVote.objects.create(poll=self.poll, option=self.option, user=self.user)
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            EventPollVote.objects.create(poll=self.poll, option=self.option, user=self.user)

    def test_str(self):
        vote = EventPollVote.objects.create(poll=self.poll, option=self.option, user=self.user)
        self.assertIn('voter@test.com', str(vote))


def _create_approved_profile(user):
    """Helper to create an approved CrushProfile."""
    from crush_lu.models import CrushProfile
    profile = CrushProfile.objects.create(
        user=user,
        date_of_birth='1995-01-01',
        gender='M',
        location='Luxembourg',
    )
    profile.is_approved = True
    profile.save()
    return profile


@override_settings(ROOT_URLCONF='azureproject.urls_crush')
class EventPollViewTests(TestCase):
    """Test poll views require authentication and approved profile."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser@test.com',
            email='testuser@test.com',
            password='testpass123',
            first_name='Test',
            last_name='User',
        )
        now = timezone.now()
        self.poll = EventPoll.objects.create(
            title='Test poll',
            start_date=now - timedelta(hours=1),
            end_date=now + timedelta(days=7),
            is_published=True,
        )
        self.option1 = EventPollOption.objects.create(poll=self.poll, name='Option 1')
        self.option2 = EventPollOption.objects.create(poll=self.poll, name='Option 2')

    def test_poll_list_requires_login(self):
        response = self.client.get('/en/polls/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)

    def test_poll_detail_requires_login(self):
        response = self.client.get(f'/en/polls/{self.poll.id}/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)

    def test_poll_list_redirects_without_profile(self):
        self.client.login(username='testuser@test.com', password='testpass123')
        response = self.client.get('/en/polls/')
        self.assertEqual(response.status_code, 302)

    def test_poll_vote_rejects_closed_poll(self):
        """Voting on a closed poll returns 400."""
        _create_approved_profile(self.user)
        self.client.login(username='testuser@test.com', password='testpass123')

        # Close the poll
        self.poll.end_date = timezone.now() - timedelta(hours=1)
        self.poll.save()

        response = self.client.post(
            f'/api/polls/{self.poll.id}/vote/',
            data=json.dumps({'option_ids': [self.option1.id]}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_single_choice_vote(self):
        """Single choice poll replaces previous vote."""
        _create_approved_profile(self.user)
        self.client.login(username='testuser@test.com', password='testpass123')

        # Vote for option 1
        response = self.client.post(
            f'/api/polls/{self.poll.id}/vote/',
            data=json.dumps({'option_ids': [self.option1.id]}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])

        # Vote again for option 2 (should replace)
        response = self.client.post(
            f'/api/polls/{self.poll.id}/vote/',
            data=json.dumps({'option_ids': [self.option2.id]}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        # Should have only 1 vote total
        self.assertEqual(EventPollVote.objects.filter(user=self.user, poll=self.poll).count(), 1)
        vote = EventPollVote.objects.get(user=self.user, poll=self.poll)
        self.assertEqual(vote.option, self.option2)

    def test_multi_choice_vote(self):
        """Multi-choice poll allows multiple selections."""
        self.poll.allow_multiple_choices = True
        self.poll.save()

        _create_approved_profile(self.user)
        self.client.login(username='testuser@test.com', password='testpass123')

        response = self.client.post(
            f'/api/polls/{self.poll.id}/vote/',
            data=json.dumps({'option_ids': [self.option1.id, self.option2.id]}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(EventPollVote.objects.filter(user=self.user, poll=self.poll).count(), 2)

    def test_single_choice_rejects_multiple(self):
        """Single-choice poll rejects multiple selections."""
        _create_approved_profile(self.user)
        self.client.login(username='testuser@test.com', password='testpass123')

        response = self.client.post(
            f'/api/polls/{self.poll.id}/vote/',
            data=json.dumps({'option_ids': [self.option1.id, self.option2.id]}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_results_api(self):
        """Results API returns vote counts."""
        self.client.login(username='testuser@test.com', password='testpass123')
        response = self.client.get(f'/api/polls/{self.poll.id}/results/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('results', data)
        self.assertEqual(len(data['results']), 2)


@override_settings(ROOT_URLCONF='azureproject.urls_crush')
class PublicThemePollTests(TestCase):
    """A public poll: anyone views it, any account votes, voters suggest."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()  # every test's viewer shares one ratelimit counter
        self.user = User.objects.create_user(
            username='fan@test.com', email='fan@test.com', password='testpass123'
        )
        # A crush.lu signup records consent; without it the consent middleware
        # bounces every page view (the JSON vote API is exempt).
        from crush_lu.models import UserDataConsent

        UserDataConsent.objects.filter(user=self.user).update(
            crushlu_consent_given=True
        )
        now = timezone.now()
        self.poll = EventPoll.objects.create(
            title_en='Which theme night?',
            title_de='Welcher Themenabend?',
            title_fr='Quelle soirée ?',
            start_date=now - timedelta(hours=1),
            end_date=now + timedelta(days=30),
            is_published=True,
            is_public=True,
            allow_multiple_choices=True,
            show_results_before_close=True,
        )
        self.option = EventPollOption.objects.create(
            poll=self.poll,
            name_en='Harry Potter Night',
            name_de='Harry-Potter-Abend',
            name_fr='Soirée Harry Potter',
        )

    def _login(self):
        self.client.login(username='fan@test.com', password='testpass123')

    def _members_poll(self):
        poll = EventPoll.objects.create(
            title='Members only',
            start_date=timezone.now() - timedelta(hours=1),
            end_date=timezone.now() + timedelta(days=7),
            is_published=True,
        )
        EventPollOption.objects.create(poll=poll, name='A')
        return poll

    def _vote(self, poll):
        return self.client.post(
            f'/api/polls/{poll.id}/vote/',
            data=json.dumps({'option_ids': [poll.options.first().id]}),
            content_type='application/json',
        )

    def test_anonymous_sees_public_poll_and_login_cta(self):
        response = self.client.get(f'/en/polls/{self.poll.id}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Harry Potter Night')
        self.assertContains(response, 'Log in to vote')
        self.assertNotContains(response, 'Send my idea')

    def test_theme_board_shows_newest_public_poll(self):
        response = self.client.get('/en/themes/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Which theme night?')
        self.assertIn('no-cache', response.headers['Cache-Control'])

    def test_theme_board_is_translated(self):
        response = self.client.get('/de/themes/')
        self.assertContains(response, 'Harry-Potter-Abend')

    def test_theme_board_empty_state_without_public_poll(self):
        self.poll.is_public = False
        self.poll.save()
        response = self.client.get('/en/themes/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Which theme night?')

    def test_poll_list_shows_public_polls_to_anonymous(self):
        response = self.client.get('/en/polls/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Which theme night?')

    def test_members_only_poll_still_requires_login(self):
        private = self._members_poll()
        response = self.client.get(f'/en/polls/{private.id}/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)

    def test_anonymous_vote_redirects_to_login(self):
        response = self._vote(self.poll)
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)
        self.assertFalse(EventPollVote.objects.exists())

    def test_account_without_profile_votes_on_public_poll(self):
        self._login()
        response = self._vote(self.poll)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(EventPollVote.objects.filter(user=self.user).count(), 1)
        page = self.client.get('/en/themes/')
        self.assertContains(page, 'Your vote has been recorded')

    def test_account_without_profile_refused_on_members_poll(self):
        private = self._members_poll()
        self._login()
        self.assertEqual(self._vote(private).status_code, 403)
        self.assertEqual(self.client.get(f'/en/polls/{private.id}/').status_code, 302)

    def test_suggestion_is_stored_pending_and_not_shown(self):
        self._login()
        self.assertContains(self.client.get('/en/themes/'), 'Send my idea')
        response = self.client.post(
            f'/en/polls/{self.poll.id}/suggest/',
            {'text': '  Tango   evening ', 'return_to': 'themes'},
        )
        self.assertRedirects(response, '/en/themes/', fetch_redirect_response=False)
        suggestion = EventPollSuggestion.objects.get()
        self.assertEqual(suggestion.text, 'Tango evening')
        self.assertEqual(suggestion.status, 'pending')
        self.assertEqual(suggestion.user, self.user)
        self.assertNotContains(self.client.get('/en/themes/'), 'Tango evening')

    def test_suggestion_ignores_user_supplied_next(self):
        self._login()
        self.assertContains(
            self.client.get('/en/themes/'), 'name="return_to" value="themes"'
        )
        response = self.client.post(
            f'/en/polls/{self.poll.id}/suggest/',
            {'text': 'Salsa night', 'next': 'https://evil.example/'},
        )
        self.assertEqual(response.url, f'/en/polls/{self.poll.id}/')

    def test_suggestion_honeypot_drops_silently(self):
        self._login()
        self.client.post(
            f'/en/polls/{self.poll.id}/suggest/',
            {'text': 'Buy pills', 'website': 'http://spam'},
        )
        self.assertFalse(EventPollSuggestion.objects.exists())

    def test_suggestion_rate_limited(self):
        self._login()
        for i in range(5):
            self.client.post(
                f'/en/polls/{self.poll.id}/suggest/', {'text': f'Idea {i}'}
            )
        response = self.client.post(
            f'/en/polls/{self.poll.id}/suggest/', {'text': 'One too many'}
        )
        self.assertEqual(response.status_code, 429)

    def test_suggestion_refused_on_members_poll(self):
        private = self._members_poll()
        self._login()
        response = self.client.post(
            f'/en/polls/{private.id}/suggest/', {'text': 'Salsa night'}
        )
        self.assertEqual(response.status_code, 404)

    def _vote_with_gender(self, gender):
        return self.client.post(
            f'/api/polls/{self.poll.id}/vote/',
            data=json.dumps({'option_ids': [self.option.id], 'gender': gender}),
            content_type='application/json',
        )

    def test_profileless_voter_is_asked_and_answer_is_stored(self):
        self._login()
        self.assertContains(self.client.get('/en/themes/'), 'name="voter_gender"')
        self.assertEqual(self._vote_with_gender('F').status_code, 200)
        self.assertEqual(EventPollVote.objects.get().voter_gender, 'F')

    def test_profile_gender_wins_over_ballot_answer(self):
        from crush_lu.models import CrushProfile

        CrushProfile.objects.create(
            user=self.user, date_of_birth='1990-01-01', gender='M', location='Luxembourg'
        )
        self._login()
        self.assertNotContains(self.client.get('/en/themes/'), 'name="voter_gender"')
        self._vote_with_gender('F')
        self.assertEqual(EventPollVote.objects.get().voter_gender, 'M')

    def test_later_votes_keep_the_first_gender(self):
        other = EventPollOption.objects.create(poll=self.poll, name_en='Karaoke')
        self._login()
        self._vote_with_gender('F')
        self.client.post(
            f'/api/polls/{self.poll.id}/vote/',
            data=json.dumps({'option_ids': [other.id], 'gender': 'M'}),
            content_type='application/json',
        )
        self.assertEqual(
            set(EventPollVote.objects.values_list('voter_gender', flat=True)), {'F'}
        )

    def test_unknown_gender_answer_is_ignored(self):
        self._login()
        self._vote_with_gender('X')
        self.assertEqual(EventPollVote.objects.get().voter_gender, '')

    def _add_votes(self, option, gender, count):
        start = EventPollVote.objects.count()
        for i in range(count):
            voter = User.objects.create_user(
                username=f'{gender}{start + i}@test.com', password='x'
            )
            EventPollVote.objects.create(
                poll=self.poll, option=option, user=voter, voter_gender=gender
            )

    def test_gender_split_needs_five_women_and_five_men(self):
        other = EventPollOption.objects.create(poll=self.poll, name_en='Karaoke')
        self._add_votes(self.option, 'F', 5)
        self._add_votes(self.option, 'M', 4)
        self._add_votes(other, 'M', 5)
        self.assertNotContains(self.client.get('/en/themes/'), 'Women ')

        self._add_votes(self.option, 'M', 1)
        response = self.client.get('/en/themes/')
        # 5 of 5 women and 5 of 10 men picked it; Karaoke has no women.
        self.assertContains(response, 'Women 100% · Men 50%', count=1)
        self.assertContains(self.client.get('/fr/themes/'), 'Femmes 100% · Hommes 50%')

    def test_members_poll_never_shows_gender_split(self):
        self.poll.is_public = False
        self.poll.save()
        self._add_votes(self.option, 'F', 5)
        self._add_votes(self.option, 'M', 5)
        _create_approved_profile(self.user)
        self._login()
        response = self.client.get(f'/en/polls/{self.poll.id}/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Women 100%')

        self.poll.is_public = True
        self.poll.save()
        self.assertContains(
            self.client.get(f'/en/polls/{self.poll.id}/'), 'Women 100% · Men 100%'
        )

    def test_suggestion_status_is_read_only_in_admin(self):
        from crush_lu.admin import crush_admin_site
        from crush_lu.admin.event_polls import EventPollSuggestionAdmin

        model_admin = EventPollSuggestionAdmin(EventPollSuggestion, crush_admin_site)
        self.assertIn('status', model_admin.readonly_fields)

    def test_vote_gender_is_read_only_in_admin(self):
        from crush_lu.admin import crush_admin_site
        from crush_lu.admin.event_polls import EventPollVoteAdmin

        model_admin = EventPollVoteAdmin(EventPollVote, crush_admin_site)
        self.assertIn('voter_gender', model_admin.readonly_fields)

    def test_admin_approve_creates_option(self):
        from unittest import mock

        from django.test import RequestFactory

        from crush_lu.admin import crush_admin_site
        from crush_lu.admin.event_polls import EventPollSuggestionAdmin

        suggestion = EventPollSuggestion.objects.create(
            poll=self.poll, user=self.user, text='Salsa-Abend', language='de'
        )
        model_admin = EventPollSuggestionAdmin(EventPollSuggestion, crush_admin_site)
        with mock.patch.object(model_admin, 'message_user'):
            model_admin.approve_suggestions(
                RequestFactory().post('/'), EventPollSuggestion.objects.all()
            )
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, 'approved')
        self.assertEqual(suggestion.promoted_to.poll, self.poll)
        self.assertEqual(suggestion.promoted_to.name_de, 'Salsa-Abend')
        self.assertEqual(suggestion.promoted_to.name_en, 'Salsa-Abend')
        self.assertEqual(self.poll.options.count(), 2)


class SeedThemePollCommandTests(TestCase):
    def test_seed_is_idempotent_and_unpublished(self):
        from io import StringIO

        from django.core.management import call_command

        call_command('seed_theme_poll', stdout=StringIO())
        call_command('seed_theme_poll', stdout=StringIO())
        poll = EventPoll.objects.get()
        self.assertTrue(poll.is_public)
        self.assertFalse(poll.is_published)
        self.assertEqual(poll.options.count(), 15)
        self.assertEqual(
            poll.options.get(name_en='Silver Fox Night').name_fr, 'Soirée Silver Fox'
        )
