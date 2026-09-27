"""
UX Wave 3 · WP4 — profile-wizard findings 3-04, 3-05, 3-07, 3-08, 3-09, 3-15.

Covers:
  - 3-08: journey/onboarding pages render exactly one <main> landmark (the
    outer one from base.html) — the templates used to open a second one.
  - 3-07: the phone-verification label has a `for` attribute and the status
    pill lives outside the label with a neutral resting state.
  - 3-09: the welcome intent radios are wrapped in a <fieldset>/<legend>.
  - 3-15: date_of_birth renders as a single native <input type="date"> with
    18-99 min/max bounds, not the old 4-level drill-down.
  - 3-05: the Review step shows a per-row Edit control and an amber nudge
    when the profile has no photo.

Every test here fails against origin/main's version of the relevant
template/form (the old markup this Wave 3 change replaced).
"""

import datetime as dt_module
from datetime import date, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from crush_lu.forms import CrushProfileForm
from crush_lu.models import CrushCoach, CrushProfile, ProfileSubmission
from crush_lu.models.profiles import UserDataConsent

User = get_user_model()

CRUSH_LU_URL_SETTINGS = {"ROOT_URLCONF": "azureproject.urls_crush"}


class _SiteMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )


def _grant_consent(user):
    from allauth.account.models import EmailAddress

    consent, _ = UserDataConsent.objects.get_or_create(user=user)
    consent.crushlu_consent_given = True
    consent.save(update_fields=["crushlu_consent_given"])
    if user.email:
        EmailAddress.objects.update_or_create(
            user=user,
            email=user.email,
            defaults={"verified": True, "primary": True},
        )


@override_settings(**CRUSH_LU_URL_SETTINGS)
class SingleMainLandmarkTests(_SiteMixin, TestCase):
    """3-08: exactly one <main> per rendered onboarding page."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_user(
            username="landmark@example.com",
            email="landmark@example.com",
            password="pass-pass-pass",
            first_name="Lea",
        )
        _grant_consent(self.user)
        self.client.login(username="landmark@example.com", password="pass-pass-pass")

    def _assert_single_main(self, path, template_name=None):
        response = self.client.get(path, follow=True)
        self.assertEqual(response.status_code, 200)
        # A redirect-away (e.g. meet_coach_step bouncing to
        # profile_submitted) also renders exactly one <main>, so the count
        # alone can't prove the target template was reached.
        if template_name:
            self.assertTemplateUsed(response, template_name)
        self.assertEqual(
            response.content.count(b"<main"),
            1,
            f"{path} should render exactly one <main> landmark",
        )

    def test_welcome_has_one_main(self):
        self._assert_single_main("/welcome/")

    def test_phone_has_one_main(self):
        CrushProfile.objects.create(user=self.user, welcome_seen_at=timezone.now())
        self._assert_single_main("/onboarding/phone/")

    def test_coach_intro_has_one_main(self):
        CrushProfile.objects.create(
            user=self.user,
            welcome_seen_at=timezone.now(),
            phone_verified=True,
            phone_number="+352621000000",
        )
        self._assert_single_main("/onboarding/coach-intro/")

    def test_meet_coach_has_one_main(self):
        profile = CrushProfile.objects.create(
            user=self.user,
            welcome_seen_at=timezone.now(),
            phone_verified=True,
            phone_number="+352621000000",
            coach_intro_seen_at=timezone.now(),
            verification_status="pending",
        )
        coach_user = User.objects.create_user(
            username="coach@example.com",
            email="coach@example.com",
            password="pass-pass-pass",
            first_name="Nora",
        )
        coach = CrushCoach.objects.create(user=coach_user, bio="Coach bio")
        ProfileSubmission.objects.create(
            profile=profile,
            coach=coach,
            status="pending",
            assigned_at=timezone.now(),
        )
        self._assert_single_main(
            "/onboarding/meet-coach/",
            template_name="crush_lu/onboarding/meet_coach.html",
        )

    def test_screening_call_has_one_main(self):
        CrushProfile.objects.create(
            user=self.user,
            welcome_seen_at=timezone.now(),
            phone_verified=True,
            phone_number="+352621000000",
            coach_intro_seen_at=timezone.now(),
        )
        self._assert_single_main("/onboarding/screening-call/")


@override_settings(**CRUSH_LU_URL_SETTINGS)
class PhoneStepAccessibilityTests(_SiteMixin, TestCase):
    """3-07: programmatic label + neutral resting status pill."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_user(
            username="phone-a11y@example.com",
            email="phone-a11y@example.com",
            password="pass-pass-pass",
            first_name="Pat",
        )
        _grant_consent(self.user)
        self.client.login(username="phone-a11y@example.com", password="pass-pass-pass")
        CrushProfile.objects.create(user=self.user, welcome_seen_at=timezone.now())

    def test_label_has_for_attribute(self):
        response = self.client.get("/onboarding/phone/", follow=True)
        self.assertContains(response, 'for="onboarding_phone_input"')

    def test_input_has_hints(self):
        response = self.client.get("/onboarding/phone/", follow=True)
        self.assertContains(response, 'autocomplete="tel"')
        self.assertContains(response, 'inputmode="tel"')

    def test_status_pill_moved_outside_label(self):
        """The old markup nested the "Verification Required" pill inside
        the <label>; it must now live in a separate element the label
        merely describes via aria-describedby."""
        response = self.client.get("/onboarding/phone/", follow=True)
        content = response.content.decode()
        label_start = content.index('for="onboarding_phone_input"')
        label_end = content.index("</label>", label_start)
        label_html = content[label_start:label_end]
        self.assertNotIn("Verification Required", label_html)
        self.assertNotIn("Not verified yet", label_html)


@override_settings(**CRUSH_LU_URL_SETTINGS)
class WelcomeIntentFieldsetTests(_SiteMixin, TestCase):
    """3-09: the intent radios are a real fieldset/legend group."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_user(
            username="welcome-a11y@example.com",
            email="welcome-a11y@example.com",
            password="pass-pass-pass",
            first_name="Wen",
        )
        _grant_consent(self.user)
        self.client.login(
            username="welcome-a11y@example.com", password="pass-pass-pass"
        )

    def test_intent_radios_in_fieldset_legend(self):
        response = self.client.get("/welcome/", follow=True)
        content = response.content.decode()
        self.assertIn("<fieldset", content)
        self.assertIn("<legend", content)
        fieldset_start = content.index("<fieldset")
        legend_start = content.index("<legend")
        radio_start = content.index('name="intent"')
        # legend and the radios both sit inside the fieldset we just found.
        self.assertLess(fieldset_start, legend_start)
        self.assertLess(legend_start, radio_start)


@override_settings(**CRUSH_LU_URL_SETTINGS)
class NativeDateOfBirthTests(_SiteMixin, TestCase):
    """3-15: a single native date input replaces the 4-level drill-down."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_user(
            username="dob@example.com",
            email="dob@example.com",
            password="pass-pass-pass",
            first_name="Robin",
        )
        _grant_consent(self.user)
        self.client.login(username="dob@example.com", password="pass-pass-pass")
        CrushProfile.objects.create(
            user=self.user,
            welcome_seen_at=timezone.now(),
            phone_verified=True,
            phone_number="+352621000000",
            coach_intro_seen_at=timezone.now(),
        )

    def test_renders_native_date_input(self):
        response = self.client.get("/en/create-profile/", follow=True)
        self.assertContains(response, 'type="date"')
        self.assertContains(response, 'name="date_of_birth"')

    def test_old_drill_down_is_gone(self):
        response = self.client.get("/en/create-profile/", follow=True)
        content = response.content.decode()
        self.assertNotIn('x-data="dobPicker"', content)
        self.assertNotIn("data-dob-age-ranges", content)
        self.assertNotIn("goToStep1", content)

    def test_form_widget_has_18_to_99_bounds(self):
        """Fails on main: the widget carried no min/max attrs at all."""
        form = CrushProfileForm()
        attrs = form.fields["date_of_birth"].widget.attrs
        today = timezone.now().date()
        self.assertIn("max", attrs)
        self.assertIn("min", attrs)
        max_dob = date.fromisoformat(attrs["max"])
        min_dob = date.fromisoformat(attrs["min"])
        age_at_max = today.year - max_dob.year
        self.assertEqual(age_at_max, 18)

        # Codex review finding: `min` must be the EARLIEST date of birth
        # that clean_date_of_birth() still accepts (age <= 99), not simply
        # "today minus 99 years" — that excludes everyone born earlier in
        # the current year who is genuinely still 99. Assert against the
        # server-side age formula itself rather than a fixed year delta.
        def age_on(today_, dob):
            return (
                today_.year
                - dob.year
                - ((today_.month, today_.day) < (dob.month, dob.day))
            )

        self.assertEqual(age_on(today, min_dob), 99)
        one_day_earlier = min_dob - timedelta(days=1)
        self.assertEqual(age_on(today, one_day_earlier), 100)

    def test_bounds_reflect_the_request_year_not_a_frozen_class_default(self):
        """min/max are recomputed from the server clock on every call, not
        frozen at import time. Moves timezone.now() five years and asserts
        both bounds move by the same delta."""
        form = CrushProfileForm()
        utc = dt_module.timezone.utc

        with mock.patch(
            "django.utils.timezone.now",
            return_value=dt_module.datetime(2020, 6, 15, 12, tzinfo=utc),
        ):
            form._set_date_of_birth_bounds()
            attrs_2020 = dict(form.fields["date_of_birth"].widget.attrs)
        with mock.patch(
            "django.utils.timezone.now",
            return_value=dt_module.datetime(2025, 6, 15, 12, tzinfo=utc),
        ):
            form._set_date_of_birth_bounds()
            attrs_2025 = dict(form.fields["date_of_birth"].widget.attrs)

        max_2020 = date.fromisoformat(attrs_2020["max"])
        max_2025 = date.fromisoformat(attrs_2025["max"])
        min_2020 = date.fromisoformat(attrs_2020["min"])
        min_2025 = date.fromisoformat(attrs_2025["min"])

        self.assertEqual(max_2025.year - max_2020.year, 5)
        self.assertEqual(min_2025.year - min_2020.year, 5)


@override_settings(**CRUSH_LU_URL_SETTINGS)
class DateOfBirthBoundsClockTests(TestCase):
    """Codex round 2 on #1068: the picker bounds use the same clock as
    clean_date_of_birth() (timezone.now().date()), not the host's local
    date.today(), which can be a different day near midnight."""

    def test_bounds_follow_the_server_validation_clock(self):
        utc = dt_module.timezone.utc
        with mock.patch(
            "django.utils.timezone.now",
            return_value=dt_module.datetime(2026, 3, 10, 23, 30, tzinfo=utc),
        ):
            form = CrushProfileForm()
        attrs = form.fields["date_of_birth"].widget.attrs
        self.assertEqual(attrs["max"], "2008-03-10")
        self.assertEqual(attrs["min"], "1926-03-11")


class ReviewStepEditLinksTests(_SiteMixin, TestCase):
    """3-05: per-row Edit controls + an amber no-photo nudge."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_user(
            username="review@example.com",
            email="review@example.com",
            password="pass-pass-pass",
            first_name="Remy",
        )
        _grant_consent(self.user)
        self.client.login(username="review@example.com", password="pass-pass-pass")

    def _complete_profile_without_photo(self):
        now = timezone.now()
        return CrushProfile.objects.create(
            user=self.user,
            welcome_seen_at=now,
            phone_verified=True,
            phone_number="+352621000000",
            coach_intro_seen_at=now,
            date_of_birth=now.date().replace(year=now.year - 30),
            gender="F",
            location="canton-luxembourg",
            event_languages=["en"],
            # verification_status defaults to "incomplete"; no photo_1 set.
        )

    def test_review_rows_have_edit_controls(self):
        self._complete_profile_without_photo()
        response = self.client.get("/en/create-profile/", follow=True)
        content = response.content.decode()
        # wizard_step is None (profile complete) -> resumes on Review (4).
        self.assertContains(response, 'data-initial-step="4"')
        self.assertGreaterEqual(content.count('@click="editSection"'), 6)
        self.assertGreaterEqual(content.count("data-goto-step="), 6)

    def test_no_photo_shows_amber_nudge_not_neutral_copy(self):
        self._complete_profile_without_photo()
        response = self.client.get("/en/create-profile/", follow=True)
        self.assertContains(response, "recognise you at events")
        # "No photos yet" survives only as the JS fallback's data-empty
        # attribute value; it must not be rendered as visible row text.
        self.assertNotContains(response, ">No photos yet<")

    def test_photos_step_copy_is_not_self_contradicting(self):
        response = self.client.get("/en/create-profile/", follow=True)
        self.assertNotContains(
            response, "Upload at least one photo (optional but recommended)"
        )


# ── 3-04: Playwright — Android/WebView back gesture walks the wizard ────────

import pytest  # noqa: E402


@pytest.mark.playwright
class TestWizardHistoryBackGesture:
    """Run with: pytest crush_lu/tests/test_ux_wave3_profile_wizard.py -v -m playwright -p no:xdist

    Not a TestCase subclass — uses the pytest-django `live_server_url` /
    pytest-playwright `page` fixtures directly, same pattern as
    test_profile_registration_e2e.py.
    """

    @pytest.fixture(autouse=True)
    def _site(self, transactional_db):
        from django.contrib.sites.models import SITE_CACHE

        SITE_CACHE.clear()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "localhost", "name": "localhost"}
        )
        Site.objects.get_or_create(domain="127.0.0.1", defaults={"name": "Live Server"})
        yield
        SITE_CACHE.clear()

    @pytest.fixture
    def photo_step_user(self, transactional_db):
        user = User.objects.create_user(
            username="wp4-history@example.com",
            email="wp4-history@example.com",
            password="testpass123",
            first_name="Hist",
        )
        _grant_consent(user)
        from allauth.account.models import EmailAddress

        EmailAddress.objects.update_or_create(
            user=user,
            email=user.email,
            defaults={"verified": True, "primary": True},
        )
        now = timezone.now()
        CrushProfile.objects.create(
            user=user,
            welcome_seen_at=now,
            phone_verified=True,
            phone_number="+352621999888",
            coach_intro_seen_at=now,
            date_of_birth=now.date().replace(year=now.year - 30),
            gender="F",
            location="canton-luxembourg",
            verification_status="incomplete",
            # event_languages left empty -> wizard_step == 3 (Photos).
        )
        return user

    def test_browser_back_moves_a_step_instead_of_leaving_the_wizard(
        self, page, live_server_url, photo_step_user
    ):
        page.goto(f"{live_server_url}/accounts/login/")
        page.wait_for_selector('input[name="login"]', timeout=10000)
        decline = page.locator('button:has-text("Decline All")')
        if decline.count() > 0:
            decline.click()
        page.fill('input[name="login"]', photo_step_user.email)
        page.fill('input[name="password"]', "testpass123")
        page.click('button:has-text("Login")')
        page.wait_for_load_state("networkidle")

        page.goto(f"{live_server_url}/en/create-profile/")
        page.wait_for_selector('[data-wizard-step="3"]', timeout=10000)
        assert page.url.endswith("#step-3")

        # In-page Back (no reload) advances the history stack to step 2.
        page.locator('[data-wizard-step="3"] button:has-text("Back")').click()
        page.wait_for_timeout(300)
        assert page.locator('[data-wizard-step="2"]').is_visible()
        assert page.url.endswith("#step-2")

        # This is the actual regression: on main, the browser Back button
        # here would leave /create-profile/ entirely (no history entry was
        # ever pushed). It must instead walk the wizard forward to step 3.
        page.go_back()
        page.wait_for_timeout(300)
        assert "/create-profile" in page.url
        assert page.locator('[data-wizard-step="3"]').is_visible()
        assert page.url.endswith("#step-3")

    def test_resumed_wizard_leaves_after_step_one_in_a_single_back(
        self, page, live_server_url, photo_step_user
    ):
        """Codex round 2 on #1068: the landing entry must become step 1
        (replaced, not stacked under a synthetic step 1), so Back walks
        3 -> 2 -> 1 and the next Back leaves the wizard."""
        page.goto(f"{live_server_url}/accounts/login/")
        page.wait_for_selector('input[name="login"]', timeout=10000)
        decline = page.locator('button:has-text("Decline All")')
        if decline.count() > 0:
            decline.click()
        page.fill('input[name="login"]', photo_step_user.email)
        page.fill('input[name="password"]', "testpass123")
        page.click('button:has-text("Login")')
        page.wait_for_load_state("networkidle")

        page.goto(f"{live_server_url}/en/create-profile/")
        page.wait_for_selector('[data-wizard-step="3"]', timeout=10000)

        for expected in ("2", "1"):
            page.go_back()
            page.wait_for_timeout(300)
            assert page.url.endswith(f"#step-{expected}")
            assert page.locator(f'[data-wizard-step="{expected}"]').is_visible()

        page.go_back()
        page.wait_for_load_state("load")
        assert "/create-profile" not in page.url


@pytest.mark.playwright
class TestReviewStepRefreshesAfterBackGesture:
    """3-04 follow-up: landing on Review via the back gesture must show the
    edited value, not the stale summary from before the edit.

    Run with: pytest crush_lu/tests/test_ux_wave3_profile_wizard.py -v -m playwright -p no:xdist
    """

    @pytest.fixture(autouse=True)
    def _site(self, transactional_db):
        from django.contrib.sites.models import SITE_CACHE

        SITE_CACHE.clear()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "localhost", "name": "localhost"}
        )
        Site.objects.get_or_create(domain="127.0.0.1", defaults={"name": "Live Server"})
        yield
        SITE_CACHE.clear()

    @pytest.fixture
    def review_step_user(self, transactional_db):
        user = User.objects.create_user(
            username="wp4-review-refresh@example.com",
            email="wp4-review-refresh@example.com",
            password="testpass123",
            first_name="Rev",
        )
        _grant_consent(user)
        from allauth.account.models import EmailAddress

        EmailAddress.objects.update_or_create(
            user=user,
            email=user.email,
            defaults={"verified": True, "primary": True},
        )
        now = timezone.now()
        CrushProfile.objects.create(
            user=user,
            welcome_seen_at=now,
            phone_verified=True,
            phone_number="+352621999888",
            coach_intro_seen_at=now,
            date_of_birth=now.date().replace(year=now.year - 30),
            gender="F",
            location="canton-luxembourg",
            event_languages=["en"],
            verification_status="incomplete",
            # date_of_birth/gender/phone_verified/event_languages all set ->
            # wizard_step is None -> resumes on Review (step 4).
        )
        return user

    def test_review_updates_after_edit_link_and_back_gesture(
        self, page, live_server_url, review_step_user
    ):
        page.goto(f"{live_server_url}/accounts/login/")
        page.wait_for_selector('input[name="login"]', timeout=10000)
        decline = page.locator('button:has-text("Decline All")')
        if decline.count() > 0:
            decline.click()
        page.fill('input[name="login"]', review_step_user.email)
        page.fill('input[name="password"]', "testpass123")
        page.click('button:has-text("Login")')
        page.wait_for_load_state("networkidle")

        page.goto(f"{live_server_url}/en/create-profile/")
        page.wait_for_selector('[data-wizard-step="4"]', timeout=10000)
        assert page.url.endswith("#step-4")

        original_dob = page.locator('[x-ref="reviewDob"]').inner_text()

        # Edit link on the Review row (goes to step 1, pushes history).
        page.locator('[data-wizard-step="4"] [data-goto-step="1"]').first.click()
        page.wait_for_selector('[data-wizard-step="1"]', timeout=10000)

        new_dob = "1990-06-15"
        page.fill('input[name="date_of_birth"]', new_dob)

        # Browser Back returns to Review via popstate -> _setStep(4, false).
        page.go_back()
        page.wait_for_selector('[data-wizard-step="4"]', timeout=10000)
        page.wait_for_timeout(300)
        assert page.url.endswith("#step-4")

        updated_dob = page.locator('[x-ref="reviewDob"]').inner_text()
        assert updated_dob != original_dob
        assert "1990" in updated_dob
