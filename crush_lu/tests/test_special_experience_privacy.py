"""
Namesake privacy for special journeys (UX review finding 7-02).

A SpecialUserExperience used to be granted to anyone whose first and last
name matched it, so a namesake could open someone else's private journey,
get their VIP session on login and even an auto-approved profile. Access now
requires ``linked_user``; ``link_special_experiences`` links the legacy rows.
"""

from datetime import date
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib import messages as django_messages
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.core.cache import cache
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings

from crush_lu.models import (
    AdventCalendar,
    AdventDoor,
    CrushProfile,
    JourneyConfiguration,
    JourneyProgress,
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

    def _generate_advent(self, experience):
        from crush_lu.admin import crush_admin_site
        from crush_lu.admin.special import SpecialUserExperienceAdmin

        request = RequestFactory().post(
            "/crush-admin/",
            {"advent_year": "2026", "generate_qr_tokens": "on"},
        )
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

    def test_admin_qr_tokens_go_to_linked_user(self):
        messages = self._generate_advent(self.experience)

        tokens = QRCodeToken.objects.all()
        self.assertTrue(tokens.exists())
        self.assertEqual(set(tokens.values_list("user_id", flat=True)), {self.owner.pk})
        self.assertEqual(messages[0][0], django_messages.SUCCESS)


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

    def test_tokens_go_to_linked_user(self):
        self._generate(self.experience)

        self.assertEqual(
            list(QRCodeToken.objects.values_list("user_id", flat=True)),
            [self.owner.pk],
        )


class LinkSpecialExperiencesCommandTests(TestCase):
    def setUp(self):
        cache.clear()

    def _run(self, *args):
        out = StringIO()
        call_command("link_special_experiences", *args, stdout=out)
        return out.getvalue()

    def test_links_unique_active_match_ignoring_case_and_whitespace(self):
        user = _make_user("anna@example.com", first=" Anna ", last="Muller")
        _make_user("old@example.com", first="Anna", last="Muller", is_active=False)
        exp = SpecialUserExperience.objects.create(
            first_name="anna ", last_name="MULLER"
        )

        output = self._run()

        exp.refresh_from_db()
        self.assertEqual(exp.linked_user, user)
        self.assertIn(f"LINKED    experience #{exp.pk}", output)
        self.assertIn("Summary: linked=1 ambiguous=0 unmatched=0 skipped=0", output)

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

    def test_dry_run_changes_nothing(self):
        user = _make_user("anna@example.com", first="Anna", last="Muller")
        exp = SpecialUserExperience.objects.create(
            first_name="Anna", last_name="Muller"
        )

        output = self._run("--dry-run")

        exp.refresh_from_db()
        self.assertIsNone(exp.linked_user)
        self.assertIn(
            f"WOULD LINK experience #{exp.pk} (Anna Muller) -> user #{user.pk}",
            output,
        )
        self.assertIn("Summary: would link=1", output)
        self.assertIn("Dry run: nothing was changed.", output)

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
        self.assertIn("linked=0 ambiguous=0 unmatched=1 skipped=1", output)

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

    def test_two_legacy_rows_for_one_user_link_only_the_first(self):
        user = _make_user("anna@example.com", first="Anna", last="Muller")
        first = SpecialUserExperience.objects.create(
            first_name="Anna", last_name="Muller"
        )
        second = SpecialUserExperience.objects.create(
            first_name="ANNA", last_name="MULLER"
        )

        output = self._run()

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.linked_user, user)
        self.assertIsNone(second.linked_user)
        self.assertIn(f"already linked to experience #{first.pk}", output)

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
