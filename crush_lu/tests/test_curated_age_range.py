"""What a curated speed-dating event's age range means, on the member pages.

A curated evening's min/max age only says who may apply. Each date is with
someone inside the member's own chosen range who also has the member's age in
theirs (``passes_event_hard_filters``). Shown bare, "Ages 20-70" read as the
spread of your dates and kept people from applying, so curated events now say
"you choose the ages you meet" wherever the range appears. Direct events keep
the bare range.

Paths are literal because the Crush host middleware replaces the active
urlconf; ``reverse()`` would build a path for the default host instead.
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.utils import timezone

from crush_lu.models import (
    CrushProfile,
    EventRegistration,
    MeetupEvent,
    UserDataConsent,
)
from crush_lu.models.events import EventRegistrationPreference

User = get_user_model()

CARD_PROMISE = "· open to 20-70"


class CuratedAgeRangeTests(TestCase):
    def setUp(self):
        # The ratelimit cache outlives SQLite rollbacks (see AGENTS.md).
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")
        self.curated = self._event("Curated Night", registration_mode="curated")
        self.direct = self._event("Direct Night", registration_mode="direct")

    def _event(self, title, **overrides):
        values = {
            "title": title,
            "description": "Age range test",
            "event_type": "speed_dating",
            "date_time": timezone.now() + timedelta(days=7),
            "location": "Luxembourg",
            "address": "123 Test Street",
            "max_participants": 24,
            "group_size": 12,
            "registration_deadline": timezone.now() + timedelta(days=5),
            "is_published": True,
            "profile_requirement": "none",
            "min_age": 20,
            "max_age": 70,
        }
        if overrides.get("registration_mode") == "direct":
            values["group_size"] = None
        values.update(overrides)
        return MeetupEvent.objects.create(**values)

    def _member(self, email="viewer@example.com", **profile):
        user = User.objects.create_user(
            username=email, email=email, password="testpass123"
        )
        UserDataConsent.objects.update_or_create(
            user=user, defaults={"crushlu_consent_given": True}
        )
        CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 1, 1),
            gender="F",
            location="Luxembourg",
            event_languages=["en"],
            **profile,
        )
        self.client.login(username=email, password="testpass123")
        return user

    def _detail(self, event, language="en"):
        return self.client.get(f"/{language}/events/{event.pk}/")

    def test_anonymous_curated_page_explains_the_range_with_an_example(self):
        response = self._detail(self.curated)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-curated-age-range")
        self.assertContains(response, "You choose who you meet")
        self.assertContains(response, "Open to ages 20-70", count=2)
        self.assertContains(response, 'data-curated-age-own="example"')
        self.assertContains(
            response, "Highlighted: an example of a range you could pick."
        )
        # The bare range is gone from a curated page.
        self.assertNotContains(response, "Ages 20-70")

    def test_direct_page_keeps_the_bare_range_and_no_explainer(self):
        response = self._detail(self.direct)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ages 20-70")
        self.assertNotContains(response, "data-curated-age-range")
        self.assertNotContains(response, "Open to ages")
        self.assertIsNone(response.context["curated_age_range"])

    def test_past_curated_event_has_no_explainer(self):
        past = self._event(
            "Past Curated Night",
            registration_mode="curated",
            date_time=timezone.now() - timedelta(days=3),
            registration_deadline=timezone.now() - timedelta(days=5),
        )

        response = self._detail(past)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "data-curated-age-range")

    def test_applicant_sees_their_own_range_on_the_bar(self):
        user = self._member()
        registration = EventRegistration.objects.create(
            event=self.curated, user=user, status="applied"
        )
        EventRegistrationPreference.objects.create(
            registration=registration,
            preferred_genders=["M"],
            preferred_age_min=28,
            preferred_age_max=38,
        )

        response = self._detail(self.curated)

        self.assertContains(response, 'data-curated-age-own="application"')
        self.assertContains(response, "You asked to meet people aged 28-38.")
        # (28-20)/50 and (38-28)/50 of the 20-70 bar.
        self.assertContains(response, 'x="16%"')
        self.assertContains(response, 'width="20%"')

    def test_application_range_is_clamped_to_the_event_range(self):
        user = self._member()
        registration = EventRegistration.objects.create(
            event=self.curated, user=user, status="applied"
        )
        EventRegistrationPreference.objects.create(
            registration=registration, preferred_age_min=18, preferred_age_max=30
        )

        response = self._detail(self.curated)

        self.assertContains(response, "You asked to meet people aged 20-30.")

    def test_member_with_saved_preferences_sees_them_before_applying(self):
        self._member(preferred_age_min=25, preferred_age_max=35)

        response = self._detail(self.curated)

        self.assertContains(response, 'data-curated-age-own="saved"')
        self.assertContains(
            response,
            "Your saved preferences: ages 25-35. You can change them when you apply.",
        )

    def test_a_range_that_narrows_nothing_falls_back_to_the_example(self):
        # The 18-99 model default is "open to all", not a choice to show.
        self._member()

        response = self._detail(self.curated)

        self.assertContains(response, 'data-curated-age-own="example"')
        self.assertIsNone(response.context["curated_age_range"]["own_source"])

    def test_event_card_promises_the_choice_only_for_curated_events(self):
        response = self.client.get("/en/events/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You choose the ages you meet", count=1)
        # Kept on one line so a narrow card never splits "20-" from "70".
        self.assertContains(
            response, f'<span class="whitespace-nowrap">{CARD_PROMISE}</span>', count=1
        )
        self.assertContains(response, "data-curated-age-promise", count=1)
        # The direct card keeps its bare range; the curated one drops it.
        self.assertContains(response, "Ages 20-70", count=1)

    def test_unrestricted_curated_card_still_names_the_choice(self):
        MeetupEvent.objects.filter(pk=self.curated.pk).update(min_age=18, max_age=99)

        response = self.client.get("/en/events/")

        self.assertContains(response, "You choose the ages you meet")
        self.assertNotContains(response, "open to 18-99")

    def test_application_form_explains_the_age_filter_only_when_curated(self):
        self._member()

        curated = self.client.get(f"/en/events/{self.curated.pk}/register/")
        direct = self.client.get(f"/en/events/{self.direct.pk}/register/")

        self.assertEqual(curated.status_code, 200)
        self.assertContains(curated, "data-curated-age-hint")
        self.assertContains(curated, "every age at this evening suits you (20-70)")
        self.assertEqual(direct.status_code, 200)
        self.assertNotContains(direct, "data-curated-age-hint")

    def test_explainer_is_localized_in_informal_de_and_formal_fr(self):
        expectations = {
            "de": (
                "Du entscheidest, wen du triffst",
                "Offen für 20-70 Jahre",
                "Hervorgehoben: ein Beispiel für einen Bereich, den du wählen könntest.",
            ),
            "fr": (
                "C'est vous qui choisissez qui vous rencontrez",
                "Ouvert aux 20-70 ans",
                "En surbrillance : un exemple de tranche que vous pourriez choisir.",
            ),
        }
        for language, phrases in expectations.items():
            with self.subTest(language=language):
                response = self._detail(self.curated, language=language)
                self.assertEqual(response.status_code, 200)
                for phrase in phrases:
                    self.assertContains(response, phrase)

    def test_speed_dating_guide_names_the_age_choice(self):
        response = self.client.get("/en/speed-dating/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You choose the age range you'd like to meet")
