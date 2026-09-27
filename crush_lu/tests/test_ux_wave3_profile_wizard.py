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

from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
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
        self.client = Client()
        self.user = User.objects.create_user(
            username="landmark@example.com",
            email="landmark@example.com",
            password="pass-pass-pass",
            first_name="Lea",
        )
        _grant_consent(self.user)
        self.client.login(username="landmark@example.com", password="pass-pass-pass")

    def _assert_single_main(self, path):
        response = self.client.get(path, follow=True)
        self.assertEqual(response.status_code, 200)
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
        self._assert_single_main("/onboarding/meet-coach/")

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
        today = date.today()
        self.assertIn("max", attrs)
        self.assertIn("min", attrs)
        max_dob = date.fromisoformat(attrs["max"])
        min_dob = date.fromisoformat(attrs["min"])
        age_at_max = today.year - max_dob.year
        self.assertEqual(age_at_max, 18)
        self.assertEqual(today.year - min_dob.year, 99)

    def test_bounds_reflect_the_request_year_not_a_frozen_class_default(self):
        """min/max must be computed per-instantiation, not once at import
        time — otherwise they silently go stale after a year passes."""
        form_a = CrushProfileForm()
        form_b = CrushProfileForm()
        self.assertEqual(
            form_a.fields["date_of_birth"].widget.attrs["max"],
            form_b.fields["date_of_birth"].widget.attrs["max"],
        )


@override_settings(**CRUSH_LU_URL_SETTINGS)
class ReviewStepEditLinksTests(_SiteMixin, TestCase):
    """3-05: per-row Edit controls + an amber no-photo nudge."""

    def setUp(self):
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
