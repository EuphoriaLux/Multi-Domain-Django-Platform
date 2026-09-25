"""
Namesake privacy for special journeys (UX review finding 7-02).

A SpecialUserExperience used to be granted to anyone whose first and last
name matched it, so a namesake could open someone else's private journey,
get their VIP session on login and even an auto-approved profile. Access now
requires ``linked_user``; ``link_special_experiences`` links the legacy rows.
"""

import json
from datetime import date
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib import messages as django_messages
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, TestCase, override_settings

from crush_lu.models import (
    AdventCalendar,
    AdventDoor,
    AdventProgress,
    ChapterProgress,
    CrushProfile,
    JourneyChallenge,
    JourneyChapter,
    JourneyConfiguration,
    JourneyProgress,
    JourneyReward,
    QRCodeToken,
    SpecialUserExperience,
    UserDataConsent,
)

User = get_user_model()
HOST = "crush.lu"


def _make_user(email, first="Lena", last="Schmit", **extra):
    user = User.objects.create_user(
        username=email,
        email=email,
        password="testpass123",
        first_name=first,
        last_name=last,
        **extra,
    )
    # consent_middleware 302s every crush page without this.
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    return user


def _messages(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


class NamesakeFixtureMixin:
    """The owner has a linked experience; the namesake shares their name."""

    def setUp(self):
        cache.clear()
        self.owner = _make_user("owner@example.com")
        self.namesake = _make_user("namesake@example.com")
        self.experience = SpecialUserExperience.objects.create(
            first_name="Lena",
            last_name="Schmit",
            linked_user=self.owner,
            is_active=True,
            auto_approve_profile=True,
        )


class JourneyViewPrivacyTests(NamesakeFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.wonderland = JourneyConfiguration.objects.create(
            special_experience=self.experience,
            journey_type="wonderland",
            journey_name="The Wonderland of You",
            is_active=True,
        )

    def test_namesake_selector_redirects_home_without_journey(self):
        self.client.force_login(self.namesake)

        response = self.client.get("/en/journey/select/", HTTP_HOST=HOST)

        self.assertEqual(response.status_code, 302)
        self.assertNotIn("journey", response.url)
        self.assertIn("No special journey found for your account.", _messages(response))

    def test_namesake_wonderland_redirects_home_and_creates_no_progress(self):
        self.client.force_login(self.namesake)

        response = self.client.get("/en/journey/wonderland/", HTTP_HOST=HOST)

        self.assertEqual(response.status_code, 302)
        self.assertNotIn("journey", response.url)
        self.assertIn("No special journey found for your account.", _messages(response))
        self.assertFalse(JourneyProgress.objects.filter(user=self.namesake).exists())

    def test_name_only_experience_grants_nobody_access(self):
        """A legacy, unlinked experience no longer opens by name match."""
        self.experience.linked_user = None
        self.experience.save()
        self.client.force_login(self.namesake)

        response = self.client.get("/en/journey/wonderland/", HTTP_HOST=HOST)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(JourneyProgress.objects.filter(user=self.namesake).exists())

    def test_linked_user_single_journey_redirects_into_it(self):
        self.client.force_login(self.owner)

        response = self.client.get("/en/journey/select/", HTTP_HOST=HOST)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith("/journey/wonderland/"))

    def test_linked_user_sees_selector_and_wonderland(self):
        JourneyConfiguration.objects.create(
            special_experience=self.experience,
            journey_type="advent_calendar",
            journey_name="Advent",
            is_active=True,
        )
        self.client.force_login(self.owner)

        selector = self.client.get("/en/journey/select/", HTTP_HOST=HOST)
        self.assertEqual(selector.status_code, 200)
        self.assertEqual(selector.context["special_experience"], self.experience)
        self.assertEqual(len(selector.context["journeys"]), 2)

        wonderland = self.client.get("/en/journey/wonderland/", HTTP_HOST=HOST)
        self.assertEqual(wonderland.status_code, 200)
        self.assertTemplateUsed(wonderland, "crush_lu/journey/journey_map.html")
        self.assertTrue(
            JourneyProgress.objects.filter(
                user=self.owner, journey=self.wonderland
            ).exists()
        )


class StaleJourneyProgressTests(NamesakeFixtureMixin, TestCase):
    """A namesake who opened the owner's journey through the old name match
    still has a JourneyProgress row on it; that row must not keep granting
    the chapters, rewards, certificate or journey API."""

    def setUp(self):
        super().setUp()
        self.journey = JourneyConfiguration.objects.create(
            special_experience=self.experience,
            journey_type="wonderland",
            journey_name="Owner Journey",
            is_active=True,
        )
        self.chapter = JourneyChapter.objects.create(
            journey=self.journey,
            chapter_number=1,
            title="OWNER-PRIVATE-CHAPTER",
            theme="Mystery",
            story_introduction="OWNER-PRIVATE-STORY",
            completion_message="Done",
        )
        self.challenge = JourneyChallenge.objects.create(
            chapter=self.chapter,
            challenge_order=1,
            challenge_type="riddle",
            question="OWNER-PRIVATE-QUESTION",
            correct_answer="yes",
            hint_1="OWNER-PRIVATE-HINT",
        )
        self.reward = JourneyReward.objects.create(
            chapter=self.chapter,
            reward_type="poem",
            title="OWNER-PRIVATE-REWARD",
            message="OWNER-PRIVATE-POEM",
        )
        # Left behind by the old name match: completed, with the chapter done.
        self.stale = JourneyProgress.objects.create(
            user=self.namesake,
            journey=self.journey,
            is_completed=True,
            total_points=500,
        )
        ChapterProgress.objects.create(
            journey_progress=self.stale, chapter=self.chapter, is_completed=True
        )

    def _post_json(self, path, payload):
        return self.client.post(
            path,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_HOST=HOST,
        )

    def test_stale_progress_opens_no_journey_page(self):
        self.client.force_login(self.namesake)

        for path in (
            "/en/journey/chapter/1/",
            f"/en/journey/chapter/1/challenge/{self.challenge.pk}/",
            f"/en/journey/reward/{self.reward.pk}/",
            "/en/journey/certificate/",
        ):
            with self.subTest(path=path):
                response = self.client.get(path, HTTP_HOST=HOST)

                self.assertEqual(response.status_code, 302)
                self.assertNotIn(b"OWNER-PRIVATE", response.content)

    def test_stale_progress_is_denied_by_the_journey_api(self):
        self.client.force_login(self.namesake)

        responses = {
            "progress": self.client.get("/en/api/journey/progress/", HTTP_HOST=HOST),
            "reward-progress": self.client.get(
                f"/api/journey/reward-progress/{self.reward.pk}/", HTTP_HOST=HOST
            ),
            "save-state": self._post_json(
                "/en/api/journey/save-state/", {"time_increment": 30}
            ),
            "unlock-hint": self._post_json(
                "/en/api/journey/unlock-hint/",
                {"challenge_id": self.challenge.pk, "hint_number": 1},
            ),
            "submit-challenge": self._post_json(
                "/en/api/journey/submit-challenge/",
                {"challenge_id": self.challenge.pk, "answer": "yes"},
            ),
            "unlock-puzzle-piece": self._post_json(
                "/api/journey/unlock-puzzle-piece/",
                {"reward_id": self.reward.pk, "piece_index": 0},
            ),
        }
        with self.captureOnCommitCallbacks(execute=True):
            responses["final-response"] = self._post_json(
                "/en/api/journey/final-response/", {"response": "yes"}
            )

        for name, response in responses.items():
            with self.subTest(endpoint=name):
                self.assertEqual(response.status_code, 404)
                self.assertFalse(response.json()["success"])
                self.assertNotIn(b"OWNER-PRIVATE", response.content)
                self.assertNotIn(b"Owner Journey", response.content)
        self.assertEqual(len(mail.outbox), 0)
        self.stale.refresh_from_db()
        self.assertEqual(self.stale.total_time_seconds, 0)
        self.assertEqual(self.stale.total_points, 500)
        self.assertEqual(self.stale.final_response, "")

    def test_linked_owner_still_plays_the_journey(self):
        JourneyProgress.objects.create(user=self.owner, journey=self.journey)
        self.client.force_login(self.owner)

        chapter = self.client.get("/en/journey/chapter/1/", HTTP_HOST=HOST)
        progress = self.client.get("/en/api/journey/progress/", HTTP_HOST=HOST)

        self.assertEqual(chapter.status_code, 200)
        self.assertContains(chapter, "OWNER-PRIVATE-STORY")
        self.assertEqual(progress.status_code, 200)
        self.assertEqual(progress.json()["data"]["journey_name"], "Owner Journey")

    def test_owner_with_an_older_stale_row_gets_their_own_journey(self):
        """``.first()`` must not pick a stale row on someone else's journey."""
        other_owner = _make_user("other@example.com", first="Tom", last="Other")
        other_exp = SpecialUserExperience.objects.create(
            first_name="Tom", last_name="Other", linked_user=other_owner
        )
        other_journey = JourneyConfiguration.objects.create(
            special_experience=other_exp,
            journey_type="wonderland",
            journey_name="Other Journey",
        )
        # Lower pk than the owner's own row, so an unscoped .first() finds it.
        JourneyProgress.objects.create(user=self.owner, journey=other_journey)
        own = JourneyProgress.objects.create(user=self.owner, journey=self.journey)
        self.client.force_login(self.owner)

        progress = self.client.get("/en/api/journey/progress/", HTTP_HOST=HOST)

        self.assertEqual(progress.json()["data"]["journey_name"], "Owner Journey")
        self.assertEqual(list(JourneyProgress.accessible_to(self.owner)), [own])

    def test_inactive_experience_closes_its_progress(self):
        JourneyProgress.objects.create(user=self.owner, journey=self.journey)
        self.experience.is_active = False
        self.experience.save()
        self.client.force_login(self.owner)

        response = self.client.get("/en/journey/chapter/1/", HTTP_HOST=HOST)

        self.assertEqual(response.status_code, 302)
        self.assertNotIn(b"OWNER-PRIVATE", response.content)


class AdventViewPrivacyTests(NamesakeFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        journey = JourneyConfiguration.objects.create(
            special_experience=self.experience,
            journey_type="advent_calendar",
            journey_name="Advent",
            is_active=True,
        )
        year = date.today().year
        self.calendar = AdventCalendar.objects.create(
            journey=journey,
            year=year,
            start_date=date(year, 12, 1),
            end_date=date(year, 12, 24),
        )
        AdventDoor.objects.create(calendar=self.calendar, door_number=1)

    def test_namesake_gets_no_advent_calendar(self):
        self.client.force_login(self.namesake)

        response = self.client.get("/en/advent/", HTTP_HOST=HOST)

        self.assertEqual(response.status_code, 302)
        self.assertNotIn("advent", response.url)
        self.assertIn(
            "No special experience found for your account.", _messages(response)
        )

    def test_namesake_gets_no_door_scanner_or_api(self):
        self.client.force_login(self.namesake)

        door = self.client.get("/en/advent/door/1/", HTTP_HOST=HOST)
        self.assertEqual(door.status_code, 302)
        self.assertIn("No special experience found.", _messages(door))

        scanner = self.client.get("/en/advent/qr-scanner/", HTTP_HOST=HOST)
        self.assertEqual(scanner.status_code, 302)
        self.assertNotIn("advent", scanner.url)

        status = self.client.get("/en/api/advent/status/", HTTP_HOST=HOST)
        self.assertEqual(status.status_code, 404)
        self.assertFalse(status.json()["success"])

        opened = self.client.post(
            "/en/api/advent/open-door/",
            data='{"door_number": 1}',
            content_type="application/json",
            HTTP_HOST=HOST,
        )
        self.assertEqual(opened.status_code, 404)

    def test_namesake_cannot_redeem_a_stale_token_on_the_owners_calendar(self):
        """A QR token issued to a namesake under the old name match must not
        redeem on, or create progress for, the owner's calendar."""
        door = AdventDoor.objects.get(calendar=self.calendar, door_number=1)
        stale = QRCodeToken.objects.create(door=door, user=self.namesake)
        self.client.force_login(self.namesake)

        response = self.client.get(f"/en/advent/qr/{stale.token}/", HTTP_HOST=HOST)

        self.assertEqual(response.status_code, 302)
        self.assertIn("This QR code is not for you.", _messages(response))
        stale.refresh_from_db()
        self.assertFalse(stale.is_used)
        self.assertFalse(AdventProgress.objects.filter(calendar=self.calendar).exists())

    def test_linked_user_redeems_their_own_token(self):
        door = AdventDoor.objects.get(calendar=self.calendar, door_number=1)
        token = QRCodeToken.objects.create(door=door, user=self.owner)
        self.client.force_login(self.owner)

        response = self.client.get(f"/en/advent/qr/{token.token}/", HTTP_HOST=HOST)

        self.assertEqual(response.status_code, 302)
        self.assertNotIn("This QR code is not for you.", _messages(response))
        token.refresh_from_db()
        self.assertTrue(token.is_used)
        progress = AdventProgress.objects.get(user=self.owner, calendar=self.calendar)
        self.assertEqual(progress.qr_scans, [1])

    def test_linked_user_advent_view_renders(self):
        self.client.force_login(self.owner)

        # AdventCalendar's date helpers import pytz, which is not installed
        # (pre-existing); stub them so this test covers the access check only.
        with (
            patch.object(AdventCalendar, "is_december", return_value=False),
            patch.object(AdventCalendar, "get_current_day", return_value=None),
            patch.object(AdventCalendar, "get_available_doors", return_value=[]),
        ):
            response = self.client.get("/en/advent/", HTTP_HOST=HOST)
            status = self.client.get("/en/api/advent/status/", HTTP_HOST=HOST)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "crush_lu/advent/calendar_locked.html")
        self.assertEqual(response.context["calendar"], self.calendar)
        self.assertEqual(status.status_code, 200)
        self.assertTrue(status.json()["success"])


class ContextProcessorPrivacyTests(NamesakeFixtureMixin, TestCase):
    def _context_for(self, user):
        from crush_lu.context_processors import crush_user_context

        request = RequestFactory().get("/en/", HTTP_HOST=HOST)
        request.user = user
        return crush_user_context(request)

    def test_namesake_context_has_no_special_journey(self):
        context = self._context_for(self.namesake)

        self.assertFalse(context.get("has_special_journey"))
        self.assertNotIn("special_experience", context)

    def test_name_only_experience_not_exposed(self):
        self.experience.linked_user = None
        self.experience.save()

        context = self._context_for(self.namesake)

        self.assertFalse(context.get("has_special_journey"))

    def test_linked_user_context_has_special_journey(self):
        context = self._context_for(self.owner)

        self.assertTrue(context["has_special_journey"])
        self.assertEqual(context["special_experience"], self.experience)

    def test_journey_progress_ignores_a_stale_row_on_someone_elses_journey(self):
        other_owner = _make_user("other@example.com", first="Tom", last="Other")
        other_journey = JourneyConfiguration.objects.create(
            special_experience=SpecialUserExperience.objects.create(
                first_name="Tom", last_name="Other", linked_user=other_owner
            ),
            journey_type="wonderland",
        )
        JourneyProgress.objects.create(user=self.owner, journey=other_journey)

        context = self._context_for(self.owner)

        self.assertTrue(context["has_special_journey"])
        self.assertFalse(context["journey_started"])
        self.assertNotIn("journey_progress", context)


class LoginSignalPrivacyTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = _make_user("member@example.com")
        self.profile = CrushProfile.objects.create(user=self.user)
        # Legacy, unlinked experience carrying the member's name.
        self.experience = SpecialUserExperience.objects.create(
            first_name="lena",
            last_name="SCHMIT",
            is_active=True,
            auto_approve_profile=True,
        )

    def _login(self, user):
        from crush_lu.signals import check_special_user_experience

        request = RequestFactory().get("/en/accounts/login/", HTTP_HOST=HOST)
        request.session = SessionStore()
        check_special_user_experience(sender=User, request=request, user=user)
        return request

    def test_namesake_login_does_not_activate_or_auto_approve(self):
        request = self._login(self.user)

        self.assertNotIn("special_experience_active", request.session)
        self.assertNotIn("special_experience_id", request.session)
        self.experience.refresh_from_db()
        self.assertEqual(self.experience.trigger_count, 0)
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.is_approved)
        self.assertNotEqual(self.profile.verification_status, "verified")

    def test_linked_user_login_activates_experience(self):
        self.experience.linked_user = self.user
        self.experience.save()

        request = self._login(self.user)

        self.assertTrue(request.session["special_experience_active"])
        self.assertEqual(request.session["special_experience_id"], self.experience.pk)
        self.experience.refresh_from_db()
        self.assertEqual(self.experience.trigger_count, 1)
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.is_approved)


class SpecialWelcomeStaleSessionTests(NamesakeFixtureMixin, TestCase):
    def test_session_for_someone_elses_experience_is_cleared(self):
        """A session activated by the old name match must stop working."""
        self.client.force_login(self.namesake)
        session = self.client.session
        session["special_experience_active"] = True
        session["special_experience_id"] = self.experience.pk
        session["special_experience_data"] = {"welcome_title": "Hi"}
        session.save()

        response = self.client.get("/en/special-welcome/", HTTP_HOST=HOST)

        self.assertEqual(response.status_code, 302)
        self.assertNotIn("special-welcome", response.url)
        self.assertNotIn("special_experience_active", self.client.session)


# The admin helpers reverse crush_admin:*, which only the crush urlconf has.
@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class AdminPrivacyTests(NamesakeFixtureMixin, TestCase):
    def test_profile_admin_journey_progress_ignores_namesake(self):
        from crush_lu.admin import crush_admin_site
        from crush_lu.admin.profiles import CrushProfileAdmin

        JourneyConfiguration.objects.create(
            special_experience=self.experience,
            journey_type="wonderland",
            journey_name="Owner Journey",
            is_active=True,
        )
        model_admin = CrushProfileAdmin(CrushProfile, crush_admin_site)
        namesake_profile = CrushProfile.objects.create(user=self.namesake)
        owner_profile = CrushProfile.objects.create(user=self.owner)

        self.assertIn(
            "No special experience configured",
            str(model_admin.get_journey_progress(namesake_profile)),
        )
        self.assertIn(
            "Owner Journey", str(model_admin.get_journey_progress(owner_profile))
        )

    def _generate_advent(self, experience, generate_qr=True):
        from crush_lu.admin import crush_admin_site
        from crush_lu.admin.special import SpecialUserExperienceAdmin

        data = {"advent_year": "2026"}
        if generate_qr:
            data["generate_qr_tokens"] = "on"
        request = RequestFactory().post("/crush-admin/", data)
        request.session = {}
        request._messages = FallbackStorage(request)
        model_admin = SpecialUserExperienceAdmin(
            SpecialUserExperience, crush_admin_site
        )
        model_admin._create_advent_calendar(request, experience)
        return [(m.level, str(m)) for m in request._messages]

    def test_admin_qr_tokens_never_go_to_a_namesake(self):
        self.experience.linked_user = None
        self.experience.save()

        messages = self._generate_advent(self.experience)

        self.assertFalse(QRCodeToken.objects.exists())
        self.assertEqual(len(messages), 1)
        level, text = messages[0]
        self.assertEqual(level, django_messages.WARNING)
        self.assertIn("no user account is linked", text)
        self.assertIn("Set 'Linked user' on this experience", text)
        # The global one-time migration is not the fix for one experience.
        self.assertNotIn("link_special_experiences", text)

    def test_admin_unlinked_calendar_warns_without_qr_generation(self):
        """The warning must not depend on the QR checkbox: without it the
        generator used to report plain success for a calendar nobody can open."""
        self.experience.linked_user = None
        self.experience.save()

        messages = self._generate_advent(self.experience, generate_qr=False)

        self.assertEqual(len(messages), 1)
        level, text = messages[0]
        self.assertEqual(level, django_messages.WARNING)
        self.assertIn("no user account is linked", text)
        self.assertIn("nobody can open this calendar yet", text)
        self.assertIn("Set 'Linked user' on this experience", text)
        self.assertNotIn("QR", text)
        self.assertNotIn("link_special_experiences", text)

    def test_admin_linked_calendar_without_qr_is_a_success(self):
        messages = self._generate_advent(self.experience, generate_qr=False)

        self.assertFalse(QRCodeToken.objects.exists())
        self.assertEqual(messages[0][0], django_messages.SUCCESS)
        self.assertNotIn("Linked user", messages[0][1])

    def test_admin_qr_tokens_go_to_linked_user(self):
        messages = self._generate_advent(self.experience)

        tokens = QRCodeToken.objects.all()
        self.assertTrue(tokens.exists())
        self.assertEqual(set(tokens.values_list("user_id", flat=True)), {self.owner.pk})
        self.assertEqual(messages[0][0], django_messages.SUCCESS)

    def test_admin_form_says_names_never_grant_access(self):
        from crush_lu.admin import crush_admin_site
        from crush_lu.admin.special import SpecialUserExperienceAdmin

        admin_user = User.objects.create_superuser(
            "admin@example.com", "admin@example.com", "testpass123"
        )
        request = RequestFactory().get("/crush-admin/")
        request.user = admin_user
        model_admin = SpecialUserExperienceAdmin(
            SpecialUserExperience, crush_admin_site
        )

        fields = model_admin.get_form(request)().fields
        titles = [title for title, _options in model_admin.get_fieldsets(request)]

        self.assertEqual(
            fields["first_name"].help_text, "Label only - does not grant access."
        )
        self.assertEqual(
            fields["last_name"].help_text, "Label only - does not grant access."
        )
        self.assertIn("required for access", fields["linked_user"].help_text)
        for field in ("first_name", "last_name", "linked_user"):
            self.assertNotIn("match", str(fields[field].help_text).lower())
        self.assertIn("👤 Linked account", titles)
        self.assertNotIn("👤 User Matching", titles)


class CreateAdventCalendarQrTests(NamesakeFixtureMixin, TestCase):
    def _generate(self, experience):
        from crush_lu.management.commands.create_advent_calendar import Command

        journey = JourneyConfiguration.objects.create(
            special_experience=experience,
            journey_type="advent_calendar",
            journey_name="Advent",
        )
        calendar = AdventCalendar.objects.create(
            journey=journey,
            year=2026,
            start_date=date(2026, 12, 1),
            end_date=date(2026, 12, 24),
        )
        AdventDoor.objects.create(calendar=calendar, door_number=6, qr_mode="required")
        out = StringIO()
        command = Command(stdout=out)
        command._generate_qr_tokens(calendar, experience)
        return out.getvalue()

    def test_unlinked_experience_gets_no_tokens(self):
        self.experience.linked_user = None
        self.experience.save()

        output = self._generate(self.experience)

        self.assertFalse(QRCodeToken.objects.exists())
        self.assertIn("No user account is linked", output)
        self.assertNotIn("link_special_experiences", output)

    def test_tokens_go_to_linked_user(self):
        self._generate(self.experience)

        self.assertEqual(
            list(QRCodeToken.objects.values_list("user_id", flat=True)),
            [self.owner.pk],
        )


class CreateAdventCalendarLinkWarningTests(NamesakeFixtureMixin, TestCase):
    """The missing-link warning must not depend on --generate-qr: the
    documented default invocation would otherwise create a calendar nobody
    can open and report success. Tested through the command's helpers, like
    the QR tests above, because the full command still crashes on main
    (AdventCalendar kwargs); #1029 repairs that path."""

    def _command(self):
        from crush_lu.management.commands.create_advent_calendar import Command

        out = StringIO()
        return Command(stdout=out), out

    def test_unlinked_experience_warns_and_leads_the_next_steps(self):
        self.experience.linked_user = None
        self.experience.save()
        command, out = self._command()

        unlinked = command._warn_if_unlinked(self.experience)
        steps = command._next_steps(unlinked, generate_qr=False)

        self.assertTrue(unlinked)
        output = out.getvalue()
        self.assertIn("No user account is linked", output)
        self.assertIn("nobody can open this calendar", output)
        self.assertNotIn("link_special_experiences", output)
        self.assertTrue(steps[0].startswith("Set 'Linked user'"))
        self.assertEqual(steps[-1], "Run with --generate-qr to create QR codes")

    def test_linked_experience_gets_no_warning(self):
        command, out = self._command()

        unlinked = command._warn_if_unlinked(self.experience)
        steps = command._next_steps(unlinked, generate_qr=True)

        self.assertFalse(unlinked)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(steps[0], "Add personalized content via Django Admin")
        self.assertEqual(steps[-1], "Print QR codes for physical gifts")
        self.assertFalse(any("Linked user" in step for step in steps))


class CreateWonderlandJourneyWarningTests(TestCase):
    def setUp(self):
        cache.clear()

    def _run(self, *args):
        out = StringIO()
        call_command(
            "create_wonderland_journey",
            "--first-name",
            "Lena",
            "--last-name",
            "Schmit",
            *args,
            stdout=out,
        )
        return out.getvalue()

    def test_unlinked_journey_warns_that_nobody_can_open_it(self):
        output = self._run()

        self.assertTrue(
            JourneyConfiguration.objects.filter(
                special_experience__first_name="Lena",
                special_experience__linked_user__isnull=True,
            ).exists()
        )
        self.assertIn("No user account is linked", output)
        self.assertIn("nobody can open this journey", output)

    def test_linked_namesake_row_is_never_reused_by_name(self):
        """A gift claim copies the member's own name onto their linked row.
        Building a journey for a different person with that name must not
        reactivate that row or overwrite the member's gift journey."""
        owner = _make_user("owner@example.com")
        gift = SpecialUserExperience.objects.create(
            first_name="Lena", last_name="Schmit", linked_user=owner, is_active=False
        )
        gift_journey = JourneyConfiguration.objects.create(
            special_experience=gift,
            journey_type="wonderland",
            journey_name="Gift",
            date_first_met=date(2025, 1, 1),
            location_first_met="Gift place",
        )

        output = self._run()

        gift.refresh_from_db()
        gift_journey.refresh_from_db()
        self.assertFalse(gift.is_active)
        self.assertEqual(gift_journey.date_first_met, date(2025, 1, 1))
        self.assertEqual(gift_journey.location_first_met, "Gift place")
        self.assertIsNone(SpecialUserExperience.active_for_user(owner))
        fresh = SpecialUserExperience.objects.get(
            first_name="Lena", last_name="Schmit", linked_user__isnull=True
        )
        self.assertTrue(fresh.journeys.filter(journey_type="wonderland").exists())
        self.assertIn(f"Experience #{gift.pk} (Lena Schmit) is linked to user", output)
        self.assertIn(f"--experience-id {gift.pk}", output)
        self.assertIn("No user account is linked", output)

    def test_experience_id_targets_a_linked_row_on_purpose(self):
        owner = _make_user("owner@example.com")
        linked = SpecialUserExperience.objects.create(
            first_name="Lena", last_name="Schmit", linked_user=owner
        )

        output = self._run("--experience-id", str(linked.pk))

        self.assertEqual(
            list(
                JourneyConfiguration.objects.filter(
                    journey_type="wonderland"
                ).values_list("special_experience_id", flat=True)
            ),
            [linked.pk],
        )
        self.assertEqual(SpecialUserExperience.objects.count(), 1)
        self.assertNotIn("No user account is linked", output)
        self.assertNotIn("is linked to user", output)

    def test_unknown_experience_id_is_a_clean_error(self):
        with self.assertRaisesMessage(
            CommandError, "No Special User Experience with id 424242"
        ):
            self._run("--experience-id", "424242")


class ResolveExperienceTests(TestCase):
    """create_advent_calendar shares the lookup; its full command still
    crashes on main (AdventCalendar kwargs, repaired by #1029), so the shared
    helper is exercised directly."""

    def setUp(self):
        cache.clear()

    def _resolve(self, **kwargs):
        from crush_lu.management.commands._special_experience import (
            resolve_experience,
        )
        from crush_lu.management.commands.create_advent_calendar import Command

        out = StringIO()
        command = Command(stdout=out)
        params = dict(
            first_name="Lena",
            last_name="Schmit",
            experience_id=None,
            defaults={"is_active": True, "vip_badge": True},
            update_existing=False,
        )
        params.update(kwargs)
        return resolve_experience(command, **params), out.getvalue()

    def test_creates_an_unlinked_row_beside_a_linked_namesake(self):
        owner = _make_user("owner@example.com")
        linked = SpecialUserExperience.objects.create(
            first_name="Lena", last_name="Schmit", linked_user=owner, vip_badge=False
        )

        (experience, created), output = self._resolve()

        self.assertTrue(created)
        self.assertNotEqual(experience.pk, linked.pk)
        self.assertIsNone(experience.linked_user)
        linked.refresh_from_db()
        self.assertFalse(linked.vip_badge)
        self.assertIn(f"--experience-id {linked.pk}", output)

    def test_reuses_the_unlinked_row_without_touching_it(self):
        unlinked = SpecialUserExperience.objects.create(
            first_name="Lena", last_name="Schmit", vip_badge=False
        )

        (experience, created), _output = self._resolve()

        self.assertFalse(created)
        self.assertEqual(experience.pk, unlinked.pk)
        unlinked.refresh_from_db()
        self.assertFalse(unlinked.vip_badge)  # get_or_create semantics

    def test_update_existing_applies_defaults_to_the_reused_row(self):
        unlinked = SpecialUserExperience.objects.create(
            first_name="Lena", last_name="Schmit", vip_badge=False
        )

        (experience, created), _output = self._resolve(update_existing=True)

        self.assertFalse(created)
        unlinked.refresh_from_db()
        self.assertTrue(unlinked.vip_badge)

    def test_experience_id_wins_over_the_name(self):
        owner = _make_user("owner@example.com")
        linked = SpecialUserExperience.objects.create(
            first_name="Someone", last_name="Else", linked_user=owner
        )

        (experience, created), output = self._resolve(experience_id=linked.pk)

        self.assertFalse(created)
        self.assertEqual(experience.pk, linked.pk)
        self.assertEqual(SpecialUserExperience.objects.count(), 1)
        self.assertEqual(output, "")


class LinkSpecialExperiencesCommandTests(TestCase):
    def setUp(self):
        cache.clear()

    def _run(self, *args):
        out = StringIO()
        call_command("link_special_experiences", *args, stdout=out)
        return out.getvalue()

    def _run_expecting_abort(self, *args):
        out = StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command("link_special_experiences", *args, stdout=out)
        return out.getvalue(), str(ctx.exception)

    def test_unique_match_is_not_linked_without_approval(self):
        """A unique name match is not proof of identity: if the intended
        recipient never signed up, the only namesake would get the journey."""
        user = _make_user("anna@example.com", first="Anna", last="Muller")
        exp = SpecialUserExperience.objects.create(
            first_name="Anna", last_name="Muller"
        )

        output = self._run()

        exp.refresh_from_db()
        self.assertIsNone(exp.linked_user)
        self.assertIsNone(SpecialUserExperience.active_for_user(user))
        self.assertIn(
            f"NEEDS APPROVAL experience #{exp.pk} (Anna Muller) -> user"
            f" #{user.pk} <anna@example.com>: not linked"
            f" (--approve {exp.pk}:{user.pk} after checking)",
            output,
        )
        self.assertIn(
            "Summary: linked=0 ambiguous=0 unmatched=0 skipped=0 needs_approval=1",
            output,
        )
        self.assertIn("a unique name match is not proof of identity", output)
        self.assertNotIn("LINKED    ", output)

    def test_links_approved_match_ignoring_case_and_whitespace(self):
        user = _make_user("anna@example.com", first=" Anna ", last="Muller")
        _make_user("old@example.com", first="Anna", last="Muller", is_active=False)
        exp = SpecialUserExperience.objects.create(
            first_name="anna ", last_name="MULLER"
        )

        output = self._run("--approve", f"{exp.pk}:{user.pk}")

        exp.refresh_from_db()
        self.assertEqual(exp.linked_user, user)
        self.assertIn(f"LINKED    experience #{exp.pk}", output)
        self.assertIn(
            "Summary: linked=1 ambiguous=0 unmatched=0 skipped=0 needs_approval=0",
            output,
        )

    def test_approval_links_only_the_named_experience(self):
        anna = _make_user("anna@example.com", first="Anna", last="Muller")
        marc = _make_user("marc@example.com", first="Marc", last="Weber")
        approved = SpecialUserExperience.objects.create(
            first_name="Anna", last_name="Muller"
        )
        other = SpecialUserExperience.objects.create(
            first_name="Marc", last_name="Weber"
        )

        output = self._run("--approve", f"{approved.pk}:{anna.pk}")

        approved.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(approved.linked_user, anna)
        self.assertIsNone(other.linked_user)
        self.assertIn(
            f"NEEDS APPROVAL experience #{other.pk} (Marc Weber) -> user #{marc.pk}",
            output,
        )
        self.assertIn("Summary: linked=1 ambiguous=0 unmatched=0 skipped=0", output)
        self.assertIn("needs_approval=1", output)

    def test_comma_separated_approvals(self):
        anna = _make_user("anna@example.com", first="Anna", last="Muller")
        marc = _make_user("marc@example.com", first="Marc", last="Weber")
        first = SpecialUserExperience.objects.create(
            first_name="Anna", last_name="Muller"
        )
        second = SpecialUserExperience.objects.create(
            first_name="Marc", last_name="Weber"
        )

        self._run("--approve", f"{first.pk}:{anna.pk}, {second.pk}:{marc.pk},")

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.linked_user, anna)
        self.assertEqual(second.linked_user, marc)

    def test_approval_for_another_user_aborts_and_links_nothing(self):
        """Approvals are all-or-nothing: one bad pair rolls back the good
        one, and the good one's LINKED line is not printed either."""
        anna = _make_user("anna@example.com", first="Anna", last="Muller")
        stranger = _make_user("stranger@example.com", first="Other", last="Person")
        _make_user("marc@example.com", first="Marc", last="Weber")
        good = SpecialUserExperience.objects.create(
            first_name="Anna", last_name="Muller"
        )
        bad = SpecialUserExperience.objects.create(first_name="Marc", last_name="Weber")

        output, message = self._run_expecting_abort(
            "--approve", f"{good.pk}:{anna.pk}", "--approve", f"{bad.pk}:{stranger.pk}"
        )

        good.refresh_from_db()
        bad.refresh_from_db()
        self.assertIsNone(good.linked_user)
        self.assertIsNone(bad.linked_user)
        self.assertIn(f"REJECTED  --approve {bad.pk}:{stranger.pk}", output)
        self.assertNotIn("LINKED    ", output)
        self.assertNotIn("Summary:", output)
        self.assertIn("Nothing was linked: 1 --approve pair(s) rejected", message)

    def test_approval_cannot_link_an_ambiguous_match(self):
        first = _make_user("one@example.com", first="Marc", last="Weber")
        _make_user("two@example.com", first="marc", last="weber")
        exp = SpecialUserExperience.objects.create(first_name="Marc", last_name="Weber")

        output, _message = self._run_expecting_abort(
            "--approve", f"{exp.pk}:{first.pk}"
        )

        exp.refresh_from_db()
        self.assertIsNone(exp.linked_user)
        self.assertIn(f"AMBIGUOUS experience #{exp.pk}", output)
        self.assertIn(f"REJECTED  --approve {exp.pk}:{first.pk}", output)

    def test_approving_a_user_linked_elsewhere_is_rejected(self):
        user = _make_user("anna@example.com", first="Anna", last="Muller")
        gift = SpecialUserExperience.objects.create(
            first_name="Anna", last_name="Muller", linked_user=user
        )
        legacy = SpecialUserExperience.objects.create(
            first_name="anna", last_name="muller"
        )

        output, _message = self._run_expecting_abort(
            "--approve", f"{legacy.pk}:{user.pk}"
        )

        legacy.refresh_from_db()
        self.assertIsNone(legacy.linked_user)
        self.assertIn(f"already linked to experience #{gift.pk}", output)
        self.assertIn(f"REJECTED  --approve {legacy.pk}:{user.pk}", output)

    def test_approval_for_a_linked_or_missing_experience_is_rejected(self):
        owner = _make_user("owner@example.com", first="Lena", last="Schmit")
        linked = SpecialUserExperience.objects.create(
            first_name="Lena", last_name="Schmit", linked_user=owner
        )

        output, message = self._run_expecting_abort(
            "--approve", f"{linked.pk}:{owner.pk}", "--approve", "999999:1"
        )

        self.assertIn(f"REJECTED  --approve {linked.pk}:{owner.pk}", output)
        self.assertIn("REJECTED  --approve 999999:1", output)
        self.assertIn("2 --approve pair(s) rejected", message)

    def test_malformed_approval_is_rejected_before_any_work(self):
        _make_user("anna@example.com", first="Anna", last="Muller")
        exp = SpecialUserExperience.objects.create(
            first_name="Anna", last_name="Muller"
        )
        for value in ("12", "a:b", "1:2:3", "12:\u00b2", "+1:2", "1_0:2"):
            with self.subTest(value=value):
                with self.assertRaisesMessage(
                    CommandError, "--approve expects EXPERIENCE_ID:USER_ID"
                ):
                    self._run("--approve", value)
        # The 4300-digit int() limit is a CommandError too, not a traceback.
        with self.assertRaisesMessage(
            CommandError, "--approve expects EXPERIENCE_ID:USER_ID"
        ):
            self._run("--approve", f"{'9' * 5000}:1")
        exp.refresh_from_db()
        self.assertIsNone(exp.linked_user)

    def test_dry_run_previews_the_real_run_with_the_same_approvals(self):
        anna = _make_user("anna@example.com", first="Anna", last="Muller")
        marc = _make_user("marc@example.com", first="Marc", last="Weber")
        approved = SpecialUserExperience.objects.create(
            first_name="Anna", last_name="Muller"
        )
        other = SpecialUserExperience.objects.create(
            first_name="Marc", last_name="Weber"
        )

        output = self._run("--dry-run", "--approve", f"{approved.pk}:{anna.pk}")

        approved.refresh_from_db()
        other.refresh_from_db()
        self.assertIsNone(approved.linked_user)
        self.assertIsNone(other.linked_user)
        self.assertIn(
            f"WOULD LINK experience #{approved.pk} (Anna Muller) -> user #{anna.pk}",
            output,
        )
        self.assertIn(
            f"NEEDS APPROVAL experience #{other.pk} (Marc Weber) -> user #{marc.pk}",
            output,
        )
        self.assertIn("Summary: would link=1", output)
        self.assertIn("needs_approval=1", output)
        self.assertIn("Dry run: nothing was changed.", output)

    def test_dry_run_without_approvals_links_nothing_and_says_so(self):
        _make_user("anna@example.com", first="Anna", last="Muller")
        exp = SpecialUserExperience.objects.create(
            first_name="Anna", last_name="Muller"
        )

        output = self._run("--dry-run")

        exp.refresh_from_db()
        self.assertIsNone(exp.linked_user)
        self.assertNotIn("WOULD LINK", output)
        self.assertIn("NEEDS APPROVAL", output)
        self.assertIn("Summary: would link=0", output)

    def test_dry_run_rejects_a_bad_approval_like_the_real_run(self):
        """A dry run that swallowed a mistyped pair could not be used to
        check an approval list; it previews the abort instead."""
        anna = _make_user("anna@example.com", first="Anna", last="Muller")
        exp = SpecialUserExperience.objects.create(
            first_name="Anna", last_name="Muller"
        )

        output, message = self._run_expecting_abort(
            "--dry-run", "--approve", f"{exp.pk}:{anna.pk}", "--approve", "12:999"
        )

        exp.refresh_from_db()
        self.assertIsNone(exp.linked_user)
        self.assertIn("REJECTED  --approve 12:999", output)
        self.assertNotIn("WOULD LINK", output)
        self.assertIn("Dry run: the real run would abort.", message)

    def test_ambiguous_match_is_reported_and_not_linked(self):
        first = _make_user("one@example.com", first="Marc", last="Weber")
        second = _make_user("two@example.com", first="marc", last="weber")
        exp = SpecialUserExperience.objects.create(first_name="Marc", last_name="Weber")

        output = self._run()

        exp.refresh_from_db()
        self.assertIsNone(exp.linked_user)
        self.assertIn(f"AMBIGUOUS experience #{exp.pk}", output)
        self.assertIn(f"#{first.pk} <one@example.com>", output)
        self.assertIn(f"#{second.pk} <two@example.com>", output)
        self.assertIn("ambiguous=1", output)
        self.assertIn("Link the AMBIGUOUS experiences by hand", output)

    def test_skips_empty_names_and_reports_unmatched(self):
        _make_user("blank@example.com", first="", last="Muller")
        blank = SpecialUserExperience.objects.create(
            first_name="  ", last_name="Muller"
        )
        nobody = SpecialUserExperience.objects.create(first_name="No", last_name="Body")

        output = self._run()

        blank.refresh_from_db()
        nobody.refresh_from_db()
        self.assertIsNone(blank.linked_user)
        self.assertIsNone(nobody.linked_user)
        self.assertIn(
            f"SKIPPED   experience #{blank.pk}: empty first or last name", output
        )
        self.assertIn(f"UNMATCHED experience #{nobody.pk}", output)
        self.assertIn(
            "linked=0 ambiguous=0 unmatched=1 skipped=1 needs_approval=0", output
        )

    def test_skips_user_already_linked_to_another_experience(self):
        user = _make_user("anna@example.com", first="Anna", last="Muller")
        gift = SpecialUserExperience.objects.create(
            first_name="Anna", last_name="Muller", linked_user=user
        )
        legacy = SpecialUserExperience.objects.create(
            first_name="anna", last_name="muller"
        )

        output = self._run()

        legacy.refresh_from_db()
        self.assertIsNone(legacy.linked_user)
        self.assertIn(f"already linked to experience #{gift.pk}", output)
        self.assertIn("skipped=1", output)

    def test_two_legacy_rows_for_one_user_link_only_the_approved_one(self):
        user = _make_user("anna@example.com", first="Anna", last="Muller")
        first = SpecialUserExperience.objects.create(
            first_name="Anna", last_name="Muller"
        )
        second = SpecialUserExperience.objects.create(
            first_name="ANNA", last_name="MULLER"
        )

        output = self._run("--approve", f"{first.pk}:{user.pk}")

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.linked_user, user)
        self.assertIsNone(second.linked_user)
        self.assertIn(f"already linked to experience #{first.pk}", output)

    def test_two_legacy_rows_cannot_both_be_approved_for_one_user(self):
        user = _make_user("anna@example.com", first="Anna", last="Muller")
        first = SpecialUserExperience.objects.create(
            first_name="Anna", last_name="Muller"
        )
        second = SpecialUserExperience.objects.create(
            first_name="ANNA", last_name="MULLER"
        )

        output, _message = self._run_expecting_abort(
            "--approve", f"{first.pk}:{user.pk}", "--approve", f"{second.pk}:{user.pk}"
        )

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNone(first.linked_user)
        self.assertIsNone(second.linked_user)
        self.assertIn(f"REJECTED  --approve {second.pk}:{user.pk}", output)

    def test_linked_namesake_still_makes_the_match_ambiguous(self):
        """Never narrow candidates by dropping already-linked users: with a
        linked owner and an unlinked namesake, that would hand the legacy
        experience to the namesake."""
        owner = _make_user("owner@example.com", first="Lena", last="Schmit")
        namesake = _make_user("namesake@example.com", first="Lena", last="Schmit")
        SpecialUserExperience.objects.create(
            first_name="Lena", last_name="Schmit", linked_user=owner
        )
        legacy = SpecialUserExperience.objects.create(
            first_name="lena", last_name="schmit"
        )

        output = self._run()

        legacy.refresh_from_db()
        self.assertIsNone(legacy.linked_user)
        self.assertFalse(
            SpecialUserExperience.objects.filter(linked_user=namesake).exists()
        )
        self.assertIn(f"AMBIGUOUS experience #{legacy.pk}", output)

    def test_skips_inactive_experience(self):
        """Linking a disabled experience would let a later gift claim reuse
        and reactivate it (unique_linked_user)."""
        user = _make_user("marco@example.com", first="Marco", last="Webber")
        disabled = SpecialUserExperience.objects.create(
            first_name="Marco", last_name="Webber", is_active=False
        )

        output = self._run()

        disabled.refresh_from_db()
        self.assertIsNone(disabled.linked_user)
        self.assertIsNone(SpecialUserExperience.active_for_user(user))
        self.assertIn(
            f"SKIPPED   experience #{disabled.pk} (Marco Webber): inactive", output
        )
        self.assertIn(
            "linked=0 ambiguous=0 unmatched=0 skipped=1 needs_approval=0", output
        )

    def test_approving_an_inactive_experience_is_rejected(self):
        user = _make_user("marco@example.com", first="Marco", last="Webber")
        disabled = SpecialUserExperience.objects.create(
            first_name="Marco", last_name="Webber", is_active=False
        )

        output, _message = self._run_expecting_abort(
            "--approve", f"{disabled.pk}:{user.pk}"
        )

        disabled.refresh_from_db()
        self.assertIsNone(disabled.linked_user)
        self.assertIn(f"REJECTED  --approve {disabled.pk}:{user.pk}", output)

    def test_already_linked_skip_explains_how_to_keep_its_journeys(self):
        user = _make_user("marie@example.com", first="Marie", last="Dupont")
        gift = SpecialUserExperience.objects.create(
            first_name="Marie", last_name="Dupont", linked_user=user
        )
        JourneyConfiguration.objects.create(
            special_experience=gift, journey_type="wonderland"
        )
        legacy = SpecialUserExperience.objects.create(
            first_name="Marie", last_name="Dupont"
        )
        advent = JourneyConfiguration.objects.create(
            special_experience=legacy, journey_type="advent_calendar"
        )
        clash = JourneyConfiguration.objects.create(
            special_experience=legacy, journey_type="wonderland"
        )

        output = self._run()

        self.assertIn(f"already linked to experience #{gift.pk}", output)
        self.assertIn(
            f"journey #{advent.pk} (advent_calendar) is unreachable: to keep it,"
            f" move it to experience #{gift.pk} in the admin",
            output,
        )
        self.assertIn(
            f"journey #{clash.pk} (wonderland) is unreachable: experience"
            f" #{gift.pk} already has a wonderland journey, so it cannot be moved",
            output,
        )

    def test_report_stale_lists_rows_not_held_by_the_linked_user(self):
        owner = _make_user("owner@example.com", first="Lena", last="Schmit")
        namesake = _make_user("namesake@example.com", first="Lena", last="Schmit")
        experience = SpecialUserExperience.objects.create(
            first_name="Lena", last_name="Schmit", linked_user=owner
        )
        wonderland = JourneyConfiguration.objects.create(
            special_experience=experience, journey_type="wonderland"
        )
        JourneyProgress.objects.create(user=owner, journey=wonderland)
        stale_journey = JourneyProgress.objects.create(
            user=namesake, journey=wonderland
        )
        calendar = AdventCalendar.objects.create(
            journey=JourneyConfiguration.objects.create(
                special_experience=experience, journey_type="advent_calendar"
            ),
            year=2026,
            start_date=date(2026, 12, 1),
            end_date=date(2026, 12, 24),
        )
        door = AdventDoor.objects.create(calendar=calendar, door_number=1)
        stale_advent = AdventProgress.objects.create(user=namesake, calendar=calendar)
        QRCodeToken.objects.create(door=door, user=owner)
        stale_token = QRCodeToken.objects.create(door=door, user=namesake)
        # An unlinked legacy row the report must NOT link.
        legacy = SpecialUserExperience.objects.create(
            first_name="Solo", last_name="Person"
        )
        _make_user("solo@example.com", first="Solo", last="Person")

        output = self._run("--report-stale")

        described = f"user #{namesake.pk} <namesake@example.com> on experience"
        self.assertIn(
            f"STALE     JourneyProgress #{stale_journey.pk}: {described}", output
        )
        self.assertIn(
            f"STALE     AdventProgress #{stale_advent.pk}: {described}", output
        )
        self.assertIn(f"STALE     QRCodeToken #{stale_token.pk}: {described}", output)
        self.assertNotIn("<owner@example.com> on experience", output)
        self.assertIn(
            "Stale rows: JourneyProgress=1 AdventProgress=1 QRCodeToken=1", output
        )
        legacy.refresh_from_db()
        self.assertIsNone(legacy.linked_user)
        self.assertEqual(JourneyProgress.objects.count(), 2)
