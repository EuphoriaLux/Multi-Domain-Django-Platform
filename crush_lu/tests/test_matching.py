"""Tests for the Crush.lu matching system (models, algorithm, views)."""

from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from crush_lu.matching import (
    get_western_zodiac,
    get_western_element,
    compute_western_zodiac_score,
    get_chinese_zodiac,
    compute_chinese_zodiac_score,
    compute_quality_score,
    compute_match_score,
    get_score_label,
    get_score_display,
    update_match_scores_for_user,
    get_matches_for_user,
)
from crush_lu.models import CrushProfile, MatchScore, Trait
from crush_lu.models.profiles import UserDataConsent


# =============================================================================
# Model Tests
# =============================================================================


class TraitModelTests(TestCase):
    def test_traits_seeded(self):
        """Migration should have seeded 40 traits."""
        self.assertEqual(Trait.objects.count(), 40)
        self.assertEqual(Trait.objects.filter(trait_type="quality").count(), 20)
        self.assertEqual(Trait.objects.filter(trait_type="defect").count(), 20)

    def test_opposite_pairs(self):
        """14 opposite pairs should be linked bidirectionally."""
        with_opposite = Trait.objects.exclude(opposite=None)
        self.assertEqual(with_opposite.count(), 28)  # 14 pairs x 2

        # Verify bidirectionality
        patient = Trait.objects.get(slug="patient")
        impatient = Trait.objects.get(slug="impatient")
        self.assertEqual(patient.opposite, impatient)
        self.assertEqual(impatient.opposite, patient)

    def test_trait_translation_fields_exist(self):
        """Modeltranslation should create label_en, label_de, label_fr."""
        patient = Trait.objects.get(slug="patient")
        self.assertEqual(patient.label_en, "Patient")
        self.assertEqual(patient.label_de, "Geduldig")
        self.assertEqual(patient.label_fr, "Patient(e)")

    def test_trait_categories(self):
        """Each trait should have a valid category."""
        valid_categories = {"social", "emotional", "mindset", "relational", "energy"}
        for trait in Trait.objects.all():
            self.assertIn(trait.category, valid_categories, f"Trait {trait.slug} has invalid category")

    def test_trait_str(self):
        patient = Trait.objects.get(slug="patient")
        self.assertIn("Patient", str(patient))


class MatchScoreModelTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user("user_a", password="test")
        self.user_b = User.objects.create_user("user_b", password="test")

    def test_create_match_score(self):
        ms = MatchScore.objects.create(
            user_a=self.user_a,
            user_b=self.user_b,
            score_qualities=0.6,
            score_zodiac_west=0.8,
            score_zodiac_cn=0.5,
            score_final=0.65,
        )
        self.assertEqual(ms.score_final, 0.65)
        self.assertIsNotNone(ms.computed_at)

    def test_unique_together(self):
        MatchScore.objects.create(
            user_a=self.user_a, user_b=self.user_b, score_final=0.5
        )
        with self.assertRaises(Exception):
            MatchScore.objects.create(
                user_a=self.user_a, user_b=self.user_b, score_final=0.6
            )

    def test_str(self):
        ms = MatchScore.objects.create(
            user_a=self.user_a, user_b=self.user_b, score_final=0.75
        )
        self.assertIn("75%", str(ms))


class CrushProfileMatchingFieldsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("test_user", password="test")
        self.profile = CrushProfile.objects.create(
            user=self.user, location="canton-luxembourg"
        )

    def test_m2m_qualities(self):
        qualities = Trait.objects.filter(trait_type="quality")[:5]
        self.profile.qualities.set(qualities)
        self.assertEqual(self.profile.qualities.count(), 5)

    def test_m2m_defects(self):
        defects = Trait.objects.filter(trait_type="defect")[:5]
        self.profile.defects.set(defects)
        self.assertEqual(self.profile.defects.count(), 5)

    def test_m2m_sought_qualities(self):
        qualities = Trait.objects.filter(trait_type="quality")[5:10]
        self.profile.sought_qualities.set(qualities)
        self.assertEqual(self.profile.sought_qualities.count(), 5)

    def test_astro_enabled_default(self):
        self.assertTrue(self.profile.astro_enabled)


# =============================================================================
# Algorithm Tests
# =============================================================================


class WesternZodiacTests(TestCase):
    def test_aries(self):
        self.assertEqual(get_western_zodiac(date(1990, 3, 25)), "aries")

    def test_taurus(self):
        self.assertEqual(get_western_zodiac(date(1990, 5, 1)), "taurus")

    def test_gemini(self):
        self.assertEqual(get_western_zodiac(date(1990, 6, 10)), "gemini")

    def test_cancer(self):
        self.assertEqual(get_western_zodiac(date(1990, 7, 1)), "cancer")

    def test_leo(self):
        self.assertEqual(get_western_zodiac(date(1990, 8, 1)), "leo")

    def test_virgo(self):
        self.assertEqual(get_western_zodiac(date(1990, 9, 1)), "virgo")

    def test_libra(self):
        self.assertEqual(get_western_zodiac(date(1990, 10, 1)), "libra")

    def test_scorpio(self):
        self.assertEqual(get_western_zodiac(date(1990, 11, 1)), "scorpio")

    def test_sagittarius(self):
        self.assertEqual(get_western_zodiac(date(1990, 12, 1)), "sagittarius")

    def test_capricorn_december(self):
        self.assertEqual(get_western_zodiac(date(1990, 12, 25)), "capricorn")

    def test_capricorn_january(self):
        self.assertEqual(get_western_zodiac(date(1990, 1, 10)), "capricorn")

    def test_aquarius(self):
        self.assertEqual(get_western_zodiac(date(1990, 2, 5)), "aquarius")

    def test_pisces(self):
        self.assertEqual(get_western_zodiac(date(1990, 3, 1)), "pisces")

    def test_cusp_aries_start(self):
        self.assertEqual(get_western_zodiac(date(1990, 3, 21)), "aries")

    def test_cusp_pisces_end(self):
        self.assertEqual(get_western_zodiac(date(1990, 3, 20)), "pisces")

    def test_none_input(self):
        self.assertIsNone(get_western_zodiac(None))

    def test_element_mapping(self):
        self.assertEqual(get_western_element("aries"), "fire")
        self.assertEqual(get_western_element("taurus"), "earth")
        self.assertEqual(get_western_element("gemini"), "air")
        self.assertEqual(get_western_element("cancer"), "water")


class WesternZodiacScoreTests(TestCase):
    def test_same_element(self):
        # Aries (fire) + Leo (fire) = 0.85
        score = compute_western_zodiac_score(date(1990, 4, 5), date(1990, 8, 1))
        self.assertEqual(score, 0.85)

    def test_complementary_fire_air(self):
        # Aries (fire) + Gemini (air) = 1.0
        score = compute_western_zodiac_score(date(1990, 4, 5), date(1990, 6, 5))
        self.assertEqual(score, 1.0)

    def test_complementary_earth_water(self):
        # Taurus (earth) + Cancer (water) = 1.0
        score = compute_western_zodiac_score(date(1990, 5, 1), date(1990, 7, 1))
        self.assertEqual(score, 1.0)

    def test_challenging_fire_water(self):
        # Aries (fire) + Cancer (water) = 0.3
        score = compute_western_zodiac_score(date(1990, 4, 5), date(1990, 7, 1))
        self.assertEqual(score, 0.3)

    def test_none_returns_neutral(self):
        self.assertEqual(compute_western_zodiac_score(None, date(1990, 4, 5)), 0.5)
        self.assertEqual(compute_western_zodiac_score(date(1990, 4, 5), None), 0.5)


class ChineseZodiacTests(TestCase):
    def test_rat(self):
        self.assertEqual(get_chinese_zodiac(date(1984, 6, 1)), "rat")

    def test_ox(self):
        self.assertEqual(get_chinese_zodiac(date(1985, 6, 1)), "ox")

    def test_dragon(self):
        self.assertEqual(get_chinese_zodiac(date(1988, 6, 1)), "dragon")

    def test_horse(self):
        self.assertEqual(get_chinese_zodiac(date(1990, 6, 1)), "horse")

    def test_chinese_new_year_boundary(self):
        """People born before Feb 4 belong to previous year's animal."""
        # 1990 = horse, but Jan 1990 = snake (previous year)
        self.assertEqual(get_chinese_zodiac(date(1990, 1, 15)), "snake")
        self.assertEqual(get_chinese_zodiac(date(1990, 2, 3)), "snake")
        self.assertEqual(get_chinese_zodiac(date(1990, 2, 4)), "horse")

    def test_none_input(self):
        self.assertIsNone(get_chinese_zodiac(None))


class ChineseZodiacScoreTests(TestCase):
    def test_same_trine(self):
        # Rat + Dragon = same trine = 1.0
        score = compute_chinese_zodiac_score(date(1984, 6, 1), date(1988, 6, 1))
        self.assertEqual(score, 1.0)

    def test_harmony_pair(self):
        # Rat + Ox = harmony = 0.9
        score = compute_chinese_zodiac_score(date(1984, 6, 1), date(1985, 6, 1))
        self.assertEqual(score, 0.9)

    def test_same_animal(self):
        # Rat + Rat = 0.8
        score = compute_chinese_zodiac_score(date(1984, 6, 1), date(1996, 6, 1))
        self.assertEqual(score, 0.8)

    def test_clash_pair(self):
        # Rat + Horse = clash = 0.2
        score = compute_chinese_zodiac_score(date(1984, 6, 1), date(1990, 6, 1))
        self.assertEqual(score, 0.2)

    def test_neutral(self):
        # Rat + Tiger = neutral = 0.6
        score = compute_chinese_zodiac_score(date(1984, 6, 1), date(1986, 6, 1))
        self.assertEqual(score, 0.6)


class QualityScoreTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user("user_a", password="test")
        self.user_b = User.objects.create_user("user_b", password="test")
        self.profile_a = CrushProfile.objects.create(
            user=self.user_a, location="canton-luxembourg",
            date_of_birth=date(1990, 4, 5),
        )
        self.profile_b = CrushProfile.objects.create(
            user=self.user_b, location="canton-luxembourg",
            date_of_birth=date(1992, 7, 15),
        )
        self.all_qualities = list(Trait.objects.filter(trait_type="quality").order_by("sort_order"))

    def test_perfect_match(self):
        """Both users seek exactly what the other has."""
        q1_5 = self.all_qualities[:5]
        q6_10 = self.all_qualities[5:10]

        self.profile_a.qualities.set(q1_5)
        self.profile_a.sought_qualities.set(q6_10)
        self.profile_b.qualities.set(q6_10)
        self.profile_b.sought_qualities.set(q1_5)

        score = compute_quality_score(self.profile_a, self.profile_b)
        self.assertEqual(score, 1.0)

    def test_zero_overlap(self):
        """No overlap between sought and has."""
        q1_5 = self.all_qualities[:5]
        q6_10 = self.all_qualities[5:10]
        q11_15 = self.all_qualities[10:15]
        q16_20 = self.all_qualities[15:20]

        self.profile_a.qualities.set(q1_5)
        self.profile_a.sought_qualities.set(q6_10)
        self.profile_b.qualities.set(q11_15)
        self.profile_b.sought_qualities.set(q16_20)

        score = compute_quality_score(self.profile_a, self.profile_b)
        self.assertEqual(score, 0.0)

    def test_partial_match(self):
        """Some overlap in one direction."""
        q1_5 = self.all_qualities[:5]
        q3_7 = self.all_qualities[2:7]  # Overlaps with q1_5 by 3

        self.profile_a.qualities.set(q1_5)
        self.profile_a.sought_qualities.set(q1_5)
        self.profile_b.qualities.set(q3_7)
        self.profile_b.sought_qualities.set(q3_7)

        score = compute_quality_score(self.profile_a, self.profile_b)
        # A seeks q1_5, B has q3_7: overlap = 3 → 3/5
        # B seeks q3_7, A has q1_5: overlap = 3 → 3/5
        # Average = (3/5 + 3/5) / 2 = 0.6
        self.assertAlmostEqual(score, 0.6)

    def test_no_sought_returns_neutral(self):
        """If either user has no sought_qualities, return 0.5."""
        q1_5 = self.all_qualities[:5]
        self.profile_a.qualities.set(q1_5)
        # profile_b has no sought_qualities

        score = compute_quality_score(self.profile_a, self.profile_b)
        self.assertEqual(score, 0.5)

    def test_bidirectional_symmetry(self):
        """Score should be the same regardless of direction."""
        q1_5 = self.all_qualities[:5]
        q6_10 = self.all_qualities[5:10]

        self.profile_a.qualities.set(q1_5)
        self.profile_a.sought_qualities.set(q6_10)
        self.profile_b.qualities.set(q6_10)
        self.profile_b.sought_qualities.set(q1_5)

        score_ab = compute_quality_score(self.profile_a, self.profile_b)
        score_ba = compute_quality_score(self.profile_b, self.profile_a)
        self.assertEqual(score_ab, score_ba)


class CombinedScoreTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user("user_a", password="test")
        self.user_b = User.objects.create_user("user_b", password="test")
        self.profile_a = CrushProfile.objects.create(
            user=self.user_a, location="canton-luxembourg",
            date_of_birth=date(1990, 4, 5),  # Aries, Horse
            astro_enabled=True,
        )
        self.profile_b = CrushProfile.objects.create(
            user=self.user_b, location="canton-luxembourg",
            date_of_birth=date(1992, 6, 10),  # Gemini, Monkey
            astro_enabled=True,
        )

    def test_combined_score_with_astro(self):
        """Score should combine all three signals."""
        all_q = list(Trait.objects.filter(trait_type="quality").order_by("sort_order"))
        self.profile_a.qualities.set(all_q[:5])
        self.profile_a.sought_qualities.set(all_q[5:10])
        self.profile_b.qualities.set(all_q[5:10])
        self.profile_b.sought_qualities.set(all_q[:5])

        scores = compute_match_score(self.profile_a, self.profile_b)

        self.assertEqual(scores["score_qualities"], 1.0)
        self.assertGreater(scores["score_zodiac_west"], 0)
        self.assertGreater(scores["score_zodiac_cn"], 0)
        # Final should be weighted average
        expected = 0.70 * 1.0 + 0.20 * scores["score_zodiac_west"] + 0.10 * scores["score_zodiac_cn"]
        self.assertAlmostEqual(scores["score_final"], round(expected, 4))

    def test_astro_disabled(self):
        """When astro is disabled, score = quality score."""
        self.profile_a.astro_enabled = False
        self.profile_a.save()

        all_q = list(Trait.objects.filter(trait_type="quality").order_by("sort_order"))
        self.profile_a.qualities.set(all_q[:5])
        self.profile_a.sought_qualities.set(all_q[5:10])
        self.profile_b.qualities.set(all_q[5:10])
        self.profile_b.sought_qualities.set(all_q[:5])

        scores = compute_match_score(self.profile_a, self.profile_b)

        self.assertEqual(scores["score_final"], scores["score_qualities"])
        self.assertEqual(scores["score_zodiac_west"], 0.0)
        self.assertEqual(scores["score_zodiac_cn"], 0.0)


class ScoreLabelTests(TestCase):
    def test_excellent(self):
        self.assertEqual(get_score_label(0.85), "excellent")
        self.assertEqual(get_score_label(1.0), "excellent")
        self.assertEqual(get_score_label(0.80), "excellent")

    def test_good(self):
        self.assertEqual(get_score_label(0.65), "good")
        self.assertEqual(get_score_label(0.79), "good")
        self.assertEqual(get_score_label(0.60), "good")

    def test_possible(self):
        self.assertEqual(get_score_label(0.45), "possible")
        self.assertEqual(get_score_label(0.59), "possible")
        self.assertEqual(get_score_label(0.40), "possible")

    def test_below_threshold(self):
        self.assertIsNone(get_score_label(0.39))
        self.assertIsNone(get_score_label(0.0))

    def test_display_returns_dict(self):
        display = get_score_display(0.85)
        self.assertIsNotNone(display)
        self.assertIn("label", display)
        self.assertIn("color", display)
        self.assertIn("hex", display)

    def test_display_returns_none_below_threshold(self):
        self.assertIsNone(get_score_display(0.35))


# =============================================================================
# Score Persistence Tests
# =============================================================================


class MatchScorePersistenceTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user("user_a", password="test")
        self.user_b = User.objects.create_user("user_b", password="test")
        self.profile_a = CrushProfile.objects.create(
            user=self.user_a, location="canton-luxembourg",
            date_of_birth=date(1990, 4, 5),
            is_approved=True, is_active=True,
        )
        self.profile_b = CrushProfile.objects.create(
            user=self.user_b, location="canton-luxembourg",
            date_of_birth=date(1992, 6, 10),
            is_approved=True, is_active=True,
        )

        all_q = list(Trait.objects.filter(trait_type="quality").order_by("sort_order"))
        self.profile_a.qualities.set(all_q[:5])
        self.profile_a.sought_qualities.set(all_q[5:10])
        self.profile_b.qualities.set(all_q[5:10])
        self.profile_b.sought_qualities.set(all_q[:5])

    def test_update_creates_score(self):
        count = update_match_scores_for_user(self.user_a)
        self.assertEqual(count, 1)
        self.assertEqual(MatchScore.objects.count(), 1)

    def test_update_enforces_pk_order(self):
        update_match_scores_for_user(self.user_a)
        ms = MatchScore.objects.first()
        self.assertLess(ms.user_a.pk, ms.user_b.pk)

    def test_get_matches_filters_by_score(self):
        update_match_scores_for_user(self.user_a)
        matches = get_matches_for_user(self.user_a, min_score=0.0)
        self.assertEqual(matches.count(), 1)

    def test_get_matches_excludes_low_scores(self):
        update_match_scores_for_user(self.user_a)
        matches = get_matches_for_user(self.user_a, min_score=999)
        self.assertEqual(matches.count(), 0)

    def test_unapproved_user_returns_zero(self):
        self.profile_a.is_approved = False
        self.profile_a.save()
        count = update_match_scores_for_user(self.user_a)
        self.assertEqual(count, 0)


# =============================================================================
# View Tests
# =============================================================================


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class CrushPreferencesWithTraitsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="traits@example.com",
            email="traits@example.com",
            password="testpass123",
        )
        self.profile = CrushProfile.objects.create(
            user=self.user,
            location="canton-luxembourg",
            is_approved=True,
            date_of_birth=date(1990, 4, 5),
        )
        consent, _ = UserDataConsent.objects.get_or_create(user=self.user)
        consent.crushlu_consent_given = True
        consent.save()
        self.client.login(username="traits@example.com", password="testpass123")

    def test_preferences_page_includes_traits(self):
        """Preferences page should include sought qualities and zodiac in context."""
        response = self.client.get(
            reverse("crush_lu:crush_preferences"), HTTP_HOST="crush.lu"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("qualities_grouped", response.context)
        self.assertIn("zodiac_sign", response.context)

    def test_save_sought_qualities(self):
        """Saving sought qualities through the preferences form should work."""
        sought = list(Trait.objects.filter(trait_type="quality")[5:10].values_list("pk", flat=True))

        response = self.client.post(
            reverse("crush_lu:crush_preferences"),
            {
                "preferred_age_min": 25,
                "preferred_age_max": 35,
                "preferred_genders": ["M", "F"],
                "first_step_preference": "no_preference",
                "sought_qualities_ids": ",".join(str(pk) for pk in sought),
                "astro_enabled": "true",
            },
            HTTP_HOST="crush.lu",
        )
        self.assertEqual(response.status_code, 302)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.sought_qualities.count(), 5)

    def test_too_many_sought_traits_rejected(self):
        """Selecting more than 5 sought qualities should fail."""
        sought = list(Trait.objects.filter(trait_type="quality")[:7].values_list("pk", flat=True))

        response = self.client.post(
            reverse("crush_lu:crush_preferences"),
            {
                "preferred_age_min": 25,
                "preferred_age_max": 35,
                "sought_qualities_ids": ",".join(str(pk) for pk in sought),
            },
            HTTP_HOST="crush.lu",
        )
        # Should re-render form with errors (200, not 302)
        self.assertEqual(response.status_code, 200)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class MatchesListViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="match@example.com",
            email="match@example.com",
            password="testpass123",
        )
        self.profile = CrushProfile.objects.create(
            user=self.user,
            location="canton-luxembourg",
            is_approved=True,
            date_of_birth=date(1990, 4, 5),
        )
        consent, _ = UserDataConsent.objects.get_or_create(user=self.user)
        consent.crushlu_consent_given = True
        consent.save()
        self.client.login(username="match@example.com", password="testpass123")

    def test_unapproved_redirected(self):
        self.profile.is_approved = False
        self.profile.save()
        response = self.client.get(
            reverse("crush_lu:matches_list"), HTTP_HOST="crush.lu"
        )
        self.assertEqual(response.status_code, 302)

    def test_no_traits_shows_empty_state(self):
        response = self.client.get(
            reverse("crush_lu:matches_list"), HTTP_HOST="crush.lu"
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["has_traits"])

    def test_with_traits_shows_matches(self):
        # Set traits
        all_q = list(Trait.objects.filter(trait_type="quality").order_by("sort_order"))
        self.profile.qualities.set(all_q[:5])
        self.profile.sought_qualities.set(all_q[5:10])

        # Create another user with matching traits
        user_b = User.objects.create_user("user_b", password="test")
        profile_b = CrushProfile.objects.create(
            user=user_b, location="canton-luxembourg",
            is_approved=True, is_active=True,
            date_of_birth=date(1992, 6, 10),
        )
        profile_b.qualities.set(all_q[5:10])
        profile_b.sought_qualities.set(all_q[:5])

        # Generate scores
        update_match_scores_for_user(self.user)

        response = self.client.get(
            reverse("crush_lu:matches_list"), HTTP_HOST="crush.lu"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["has_traits"])
        self.assertGreater(len(response.context["matches"]), 0)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class MatchApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="api@example.com",
            email="api@example.com",
            password="testpass123",
        )
        self.profile = CrushProfile.objects.create(
            user=self.user,
            location="canton-luxembourg",
            is_approved=True,
            date_of_birth=date(1990, 4, 5),
        )
        consent, _ = UserDataConsent.objects.get_or_create(user=self.user)
        consent.crushlu_consent_given = True
        consent.save()
        self.client.login(username="api@example.com", password="testpass123")

    def test_match_list_api(self):
        response = self.client.get("/api/matches/", HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("matches", data)
        self.assertIn("total", data)

    def test_match_score_api_not_found(self):
        response = self.client.get("/api/matches/score/99999/", HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 404)

    def test_match_score_api_success(self):
        user_b = User.objects.create_user("user_b", password="test")
        CrushProfile.objects.create(
            user=user_b, location="canton-luxembourg",
            is_approved=True, is_active=True,
            date_of_birth=date(1992, 6, 10),
        )
        response = self.client.get(
            f"/api/matches/score/{user_b.pk}/", HTTP_HOST="crush.lu"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("score_final", data)
        self.assertIn("score_percent", data)
