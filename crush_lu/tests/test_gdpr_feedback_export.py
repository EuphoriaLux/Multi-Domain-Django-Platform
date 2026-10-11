"""Member-owned feedback and poll responses in the real data-export endpoint."""

import json
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from crush_lu.models import CrushProfile, EventFeedback, MeetupEvent, UserDataConsent
from crush_lu.models.event_polls import (
    EventPoll,
    EventPollOption,
    EventPollSuggestion,
    EventPollVote,
)

User = get_user_model()


class ExportFeedbackAndPollResponseTests(TestCase):
    def setUp(self):
        cache.clear()
        self.now = timezone.now()
        self.user = User.objects.create_user(
            "responses@example.com", "responses@example.com", "test-password"
        )
        self.other = User.objects.create_user(
            "other-responses@example.com",
            "other-responses@example.com",
            "test-password",
        )
        UserDataConsent.objects.update_or_create(
            user=self.user, defaults={"crushlu_consent_given": True}
        )
        CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 1, 1),
            gender="F",
            location="Luxembourg",
            phone_verified=True,
            is_approved=True,
            welcome_seen_at=self.now,
        )
        self.events = [
            MeetupEvent.objects.create(
                title=f"Member event {index}",
                description="PRIVATE-EVENT-DESCRIPTION",
                event_type="mixer",
                location="PRIVATE-EVENT-LOCATION",
                date_time=self.now + timedelta(days=index),
                registration_deadline=self.now,
                is_published=True,
            )
            for index in (1, 2)
        ]
        self.polls = [
            EventPoll.objects.create(
                title=f"Member poll {index}",
                description="PRIVATE-POLL-DESCRIPTION",
                start_date=self.now,
                end_date=self.now + timedelta(days=7),
                is_published=True,
            )
            for index in (1, 2)
        ]
        self.options = [
            EventPollOption.objects.create(
                poll=poll,
                name=f"Selected option {index}",
                description="PRIVATE-OPTION-DESCRIPTION",
            )
            for index, poll in enumerate(self.polls, start=1)
        ]
        self.client.force_login(self.user)

    def _export(self):
        response = self.client.get("/en/account/gdpr/export/", HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(
            response["Content-Disposition"],
            f'attachment; filename="crush_lu_data_{self.user.pk}.json"',
        )
        return response.json()

    def _feedback(self, user=None, event=None, **fields):
        return EventFeedback.objects.create(
            user=user or self.user,
            event=event or self.events[0],
            nps_score=8,
            **fields,
        )

    def _vote(self, user=None, index=0, **fields):
        return EventPollVote.objects.create(
            user=user or self.user,
            poll=self.polls[index],
            option=self.options[index],
            **fields,
        )

    def _suggestion(self, user=None, index=0, **fields):
        return EventPollSuggestion.objects.create(
            user=user or self.user,
            poll=self.polls[index],
            text="A member-authored suggestion",
            **fields,
        )

    def test_exports_own_feedback_with_minimal_event_context(self):
        feedback = self._feedback(
            would_recommend=False,
            what_worked="The conversations were fun!",
            what_to_improve="More time for introductions.",
        )
        self.assertEqual(
            self._export()["event_feedback"],
            [
                {
                    "event": self.events[0].title,
                    "event_date": self.events[0].date_time.isoformat(),
                    "nps_score": 8,
                    "would_recommend": False,
                    "what_worked": feedback.what_worked,
                    "what_to_improve": feedback.what_to_improve,
                    "created_at": feedback.created_at.isoformat(),
                }
            ],
        )

    def test_poll_votes_export_the_selected_option_not_all_poll_options(self):
        vote = self._vote(voter_gender="F")
        EventPollOption.objects.create(
            poll=self.polls[0], name="UNSELECTED-OPTION", description="PRIVATE-OPTION"
        )
        self.assertEqual(
            self._export()["poll_votes"],
            [
                {
                    "poll": self.polls[0].title,
                    "option": self.options[0].name,
                    "voter_gender": "F",
                    "voted_at": vote.voted_at.isoformat(),
                }
            ],
        )

    def test_multiple_choices_in_one_poll_export_each_own_selected_option(self):
        EventPoll.objects.filter(pk=self.polls[0].pk).update(
            allow_multiple_choices=True
        )
        self._vote(voter_gender="F")
        second_option = EventPollOption.objects.create(
            poll=self.polls[0], name="Another selected option"
        )
        EventPollVote.objects.create(
            user=self.user, poll=self.polls[0], option=second_option, voter_gender="F"
        )
        unselected = EventPollOption.objects.create(
            poll=self.polls[0], name="UNSELECTED-MULTICHOICE-OPTION"
        )
        EventPollVote.objects.create(
            user=self.other, poll=self.polls[0], option=unselected, voter_gender="M"
        )

        votes = self._export()["poll_votes"]

        self.assertEqual(
            [vote["option"] for vote in votes],
            [self.options[0].name, second_option.name],
        )
        self.assertEqual([vote["poll"] for vote in votes], [self.polls[0].title] * 2)
        self.assertEqual([vote["voter_gender"] for vote in votes], ["F", "F"])
        self.assertNotIn(unselected.name, json.dumps(votes))

    def test_suggestions_export_authored_content_without_staff_review_data(self):
        promoted = EventPollOption.objects.create(
            poll=self.polls[0], name="STAFF-PROMOTED-OPTION"
        )
        suggestion = self._suggestion(
            language="fr",
            status=EventPollSuggestion.Status.APPROVED,
            promoted_to=promoted,
        )
        self.assertEqual(
            self._export()["poll_suggestions"],
            [
                {
                    "poll": self.polls[0].title,
                    "text": suggestion.text,
                    "language": "fr",
                    "created_at": suggestion.created_at.isoformat(),
                }
            ],
        )

    def test_empty_optional_responses_are_null_and_nullable_promotion_is_safe(self):
        self._feedback()
        self._vote()
        self._suggestion()
        data = self._export()
        self.assertIsNone(data["event_feedback"][0]["what_worked"])
        self.assertIsNone(data["event_feedback"][0]["what_to_improve"])
        self.assertIsNone(data["poll_votes"][0]["voter_gender"])
        self.assertIsNone(data["poll_suggestions"][0]["language"])
        self.assertNotIn("promoted_to", data["poll_suggestions"][0])

    def test_other_members_content_and_unrelated_private_poll_metadata_are_absent(self):
        self._feedback(what_worked="OWN-FEEDBACK")
        self._vote()
        self._suggestion()
        self._feedback(user=self.other, what_worked="OTHER-FEEDBACK")
        self._vote(user=self.other, voter_gender="M")
        self._suggestion(user=self.other, language="de")
        private_poll = EventPoll.objects.create(
            title="OTHER-PRIVATE-POLL",
            description="OTHER-PRIVATE-DESCRIPTION",
            start_date=self.now,
            end_date=self.now + timedelta(days=7),
        )
        private_option = EventPollOption.objects.create(
            poll=private_poll, name="OTHER-PRIVATE-OPTION"
        )
        EventPollVote.objects.create(
            user=self.other,
            poll=private_poll,
            option=private_option,
        )
        EventPollSuggestion.objects.create(
            user=self.other,
            poll=private_poll,
            text="OTHER-PRIVATE-SUGGESTION",
        )
        data = self._export()
        for key in ("event_feedback", "poll_votes", "poll_suggestions"):
            self.assertEqual(len(data[key]), 1)
            for entry in data[key]:
                self.assertNotIn("user", entry)
                self.assertNotIn("user_id", entry)
        body = json.dumps(data)
        for excluded in (
            "OTHER-FEEDBACK",
            "OTHER-PRIVATE-POLL",
            "OTHER-PRIVATE-OPTION",
            "OTHER-PRIVATE-SUGGESTION",
            "OTHER-PRIVATE-DESCRIPTION",
            "PRIVATE-EVENT-DESCRIPTION",
            "PRIVATE-EVENT-LOCATION",
            "PRIVATE-POLL-DESCRIPTION",
            "PRIVATE-OPTION-DESCRIPTION",
            self.other.email,
        ):
            self.assertNotIn(excluded, body)

    def test_empty_lists_are_returned_even_when_only_other_members_have_responses(self):
        self._feedback(user=self.other)
        self._vote(user=self.other)
        self._suggestion(user=self.other)
        data = self._export()
        for key in ("event_feedback", "poll_votes", "poll_suggestions"):
            self.assertEqual(data[key], [])

    def test_multiple_events_and_polls_are_ordered_by_response_time_then_primary_key(
        self,
    ):
        feedback = [self._feedback(event=event) for event in self.events]
        votes = [self._vote(index=index) for index in (0, 1)]
        suggestions = [self._suggestion(index=index) for index in (0, 1)]
        for model, rows, timestamp in (
            (EventFeedback, feedback, "created_at"),
            (EventPollVote, votes, "voted_at"),
            (EventPollSuggestion, suggestions, "created_at"),
        ):
            model.objects.filter(pk=rows[0].pk).update(**{timestamp: self.now})
            model.objects.filter(pk=rows[1].pk).update(
                **{timestamp: self.now - timedelta(days=1)}
            )
        data = self._export()
        self.assertEqual(
            [row["event"] for row in data["event_feedback"]],
            [event.title for event in reversed(self.events)],
        )
        for key in ("poll_votes", "poll_suggestions"):
            self.assertEqual(
                [row["poll"] for row in data[key]],
                [poll.title for poll in reversed(self.polls)],
            )
        for model, timestamp in (
            (EventFeedback, "created_at"),
            (EventPollVote, "voted_at"),
            (EventPollSuggestion, "created_at"),
        ):
            model.objects.filter(user=self.user).update(**{timestamp: self.now})
        tied = self._export()
        self.assertEqual(
            [row["event"] for row in tied["event_feedback"]],
            [event.title for event in self.events],
        )
        for key in ("poll_votes", "poll_suggestions"):
            self.assertEqual(
                [row["poll"] for row in tied[key]],
                [poll.title for poll in self.polls],
            )
