"""Only the downloading member's Connect question selections are portable."""

import json
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from crush_lu.models import (
    ConnectCycleCard,
    ConnectWeekSession,
    CrushConnectMembership,
    CrushProfile,
    UserDataConsent,
)
from crush_lu.models.crush_connect_questions import (
    ConnectQuestion,
    ConnectQuestionWeek,
    MemberGateQuestion,
)

User = get_user_model()
COLLECTION = "connect_question_selections"


class ExportConnectQuestionSelectionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.now = timezone.now()
        self.user = User.objects.create_user(
            "question-owner", "question-owner@example.com", "test-password"
        )
        self.other = User.objects.create_user(
            "other-question-owner", "other-question-owner@example.com", "test-password"
        )
        UserDataConsent.objects.update_or_create(
            user=self.user, defaults={"crushlu_consent_given": True}
        )
        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 1, 1),
            gender="F",
            location="Luxembourg",
            phone_verified=True,
            is_approved=True,
            welcome_seen_at=self.now,
        )
        self.membership = CrushConnectMembership.objects.create(user=self.user)
        self.other_membership = CrushConnectMembership.objects.create(user=self.other)
        self.client.force_login(self.user)

    def _export(self, lang="en"):
        response = self.client.get(
            f"/{lang}/account/gdpr/export/", HTTP_HOST="crush.lu"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(
            response["Content-Disposition"],
            f'attachment; filename="crush_lu_data_{self.user.pk}.json"',
        )
        return response.json()

    def _question(self, slug="own-question", **fields):
        return ConnectQuestion.objects.create(
            slug=slug,
            text="Do I like coffee?",
            text_en="Do I like coffee?",
            text_de="Mag ich Kaffee?",
            text_fr="Est-ce que j’aime le café ?",
            category=ConnectQuestion.Category.LIFESTYLE,
            **fields,
        )

    def _selection(self, question=None, membership=None, position=1, answer=True):
        return MemberGateQuestion.objects.create(
            membership=membership or self.membership,
            question=question or self._question(),
            position=position,
            owner_answer=answer,
        )

    def test_true_and_false_answers_export_with_only_readable_selection_fields(self):
        yes = self._selection(answer=True)
        no = self._selection(
            question=self._question("second-question"), position=2, answer=False
        )
        self.assertEqual(
            self._export()[COLLECTION],
            [
                {
                    "question": selection.question.text,
                    "owner_answer": selection.owner_answer,
                    "position": selection.position,
                    "created_at": selection.created_at.isoformat(),
                }
                for selection in (yes, no)
            ],
        )

    def test_retired_selected_question_is_preserved(self):
        retired = self._question(is_active=False)
        self._selection(question=retired, answer=False)
        selected = self._export()[COLLECTION]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["question"], retired.text)
        self.assertIs(selected[0]["owner_answer"], False)

    def test_only_own_selections_not_peer_truths_guesses_or_unselected_questions(self):
        own = self._selection(answer=True)
        self._selection(
            question=own.question, membership=self.other_membership, answer=False
        )
        private = self._question("unselected-private")
        private.text = "OTHER-MEMBERS-PRIVATE-QUESTION"
        private.text_en = private.text
        private.save()
        self._selection(
            question=private,
            membership=self.other_membership,
            position=2,
            answer=False,
        )
        guesses = ConnectWeekSession.objects.create(user=self.other)
        ConnectCycleCard.objects.create(
            session=guesses,
            day_number=1,
            card_index=1,
            target_user=self.user,
            generated_date=self.now.date(),
            answers_json={"PEER-SECRET-GUESS": False},
        )
        self.membership.exclusion_reason = "COACH-PRIVATE-NOTE"
        self.membership.save(update_fields=["exclusion_reason"])
        data = self._export()
        selections = data[COLLECTION]
        self.assertEqual(len(selections), 1)
        self.assertIs(selections[0]["owner_answer"], True)
        body = json.dumps(data)
        for excluded in (
            "OTHER-MEMBERS-PRIVATE-QUESTION",
            "PEER-SECRET-GUESS",
            "COACH-PRIVATE-NOTE",
            self.other.email,
            own.question.slug,
        ):
            self.assertNotIn(excluded, body)

    def test_order_uses_member_position_despite_creation_time_or_catalogue_order(self):
        second = self._selection(position=2)
        first = self._selection(question=self._question("first"), position=1)
        third = self._selection(question=self._question("third"), position=3)
        MemberGateQuestion.objects.filter(pk=first.pk).update(
            created_at=self.now + timedelta(days=1)
        )
        MemberGateQuestion.objects.filter(pk=third.pk).update(
            created_at=self.now - timedelta(days=1)
        )
        selected = self._export()[COLLECTION]
        self.assertEqual([row["position"] for row in selected], [1, 2, 3])
        self.assertEqual(
            [row["created_at"] for row in selected],
            [
                (self.now + timedelta(days=1)).isoformat(),
                second.created_at.isoformat(),
                (self.now - timedelta(days=1)).isoformat(),
            ],
        )
        self.assertEqual(selected, self._export()[COLLECTION])

    def test_selected_question_text_follows_request_language(self):
        self._selection()
        for lang, expected in (
            ("en", "Do I like coffee?"),
            ("de", "Mag ich Kaffee?"),
            ("fr", "Est-ce que j’aime le café ?"),
        ):
            with self.subTest(language=lang):
                selected = self._export(lang)[COLLECTION]
                self.assertEqual(selected[0]["question"], expected)
                self.assertIs(selected[0]["owner_answer"], True)

    def test_membership_without_selections_has_an_empty_collection(self):
        self._selection(membership=self.other_membership)
        self.assertEqual(self._export()[COLLECTION], [])

    def test_missing_membership_has_an_empty_collection_and_is_not_created(self):
        self.membership.delete()
        self._selection(membership=self.other_membership)
        self.assertEqual(self._export()[COLLECTION], [])
        self.assertFalse(CrushConnectMembership.objects.filter(user=self.user).exists())

    def test_profileless_account_without_membership_has_an_empty_collection(self):
        self.profile.delete()
        self.membership.delete()
        data = self._export()
        self.assertNotIn("profile", data)
        self.assertEqual(data[COLLECTION], [])

    def test_export_does_not_mutate_membership_question_or_week_state(self):
        self._selection()
        self.membership.paused_at = self.now
        self.membership.excluded_by_coach = True
        self.membership.save(update_fields=["paused_at", "excluded_by_coach"])
        before_memberships = list(
            CrushConnectMembership.objects.order_by("pk").values()
        )
        before_selections = list(MemberGateQuestion.objects.order_by("pk").values())
        before_questions = list(ConnectQuestion.objects.order_by("pk").values())
        weeks = ConnectQuestionWeek.objects.count()
        self.assertEqual(len(self._export()[COLLECTION]), 1)
        self.assertEqual(
            before_memberships,
            list(CrushConnectMembership.objects.order_by("pk").values()),
        )
        self.assertEqual(
            before_selections, list(MemberGateQuestion.objects.order_by("pk").values())
        )
        self.assertEqual(
            before_questions, list(ConnectQuestion.objects.order_by("pk").values())
        )
        self.assertEqual(weeks, ConnectQuestionWeek.objects.count())

    def test_prior_export_sections_are_unchanged_when_selection_is_added(self):
        before = self._export()
        self._selection()
        after = self._export()
        self.assertEqual(before.pop(COLLECTION), [])
        self.assertEqual(len(after.pop(COLLECTION)), 1)
        before.pop("export_date")
        after.pop("export_date")
        self.assertEqual(before, after)

    def test_anonymous_request_still_requires_login(self):
        self._selection()
        self.client.logout()
        response = self.client.get("/en/account/gdpr/export/", HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(COLLECTION, response.content.decode())
