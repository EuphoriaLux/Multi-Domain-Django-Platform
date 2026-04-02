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
    compute_language_score,
    compute_age_fit_score,
    _age_fit_one_direction,
    has_matching_profile,
    passes_hard_filters,
    compute_match_score,
    get_score_label,
    get_score_display,
    update_match_scores_for_user,
    get_matches_for_user,
    WEIGHT_QUALITIES,
    WEIGHT_ZODIAC_WEST,
    WEIGHT_ZODIAC_CN,
    WEIGHT_LANGUAGE,
    WEIGHT_AGE_FIT,
)
from crush_lu.models import CrushCoach, CrushProfile, MatchScore, Trait
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
        # __str__ returns translated label + trait_type; check slug-independent format
        trait_str = str(patient)
        self.assertIn("(", trait_str)  # format: "Label (Type)"
        self.assertTrue(
            patient.label_en in trait_str or patient.label_de in trait_str or patient.label_fr in trait_str,
            f"Expected a translated label in '{trait_str}'",
        )


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


class LanguageScoreTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user("lang_a", password="test")
        self.user_b = User.objects.create_user("lang_b", password="test")
        self.profile_a = CrushProfile.objects.create(
            user=self.user_a, location="canton-luxembourg",
            date_of_birth=date(1990, 4, 5),
        )
        self.profile_b = CrushProfile.objects.create(
            user=self.user_b, location="canton-luxembourg",
            date_of_birth=date(1992, 7, 15),
        )

    def test_full_overlap(self):
        self.profile_a.event_languages = ["en", "fr"]
        self.profile_b.event_languages = ["en", "fr"]
        self.assertEqual(compute_language_score(self.profile_a, self.profile_b), 1.0)

    def test_partial_overlap(self):
        self.profile_a.event_languages = ["en", "fr"]
        self.profile_b.event_languages = ["en", "de"]
        self.assertEqual(compute_language_score(self.profile_a, self.profile_b), 0.5)

    def test_no_overlap(self):
        self.profile_a.event_languages = ["en"]
        self.profile_b.event_languages = ["de"]
        self.assertEqual(compute_language_score(self.profile_a, self.profile_b), 0.0)

    def test_empty_languages_neutral(self):
        self.profile_a.event_languages = []
        self.profile_b.event_languages = ["en"]
        self.assertEqual(compute_language_score(self.profile_a, self.profile_b), 0.5)

    def test_both_empty_neutral(self):
        self.profile_a.event_languages = []
        self.profile_b.event_languages = []
        self.assertEqual(compute_language_score(self.profile_a, self.profile_b), 0.5)

    def test_subset_fully_covered(self):
        self.profile_a.event_languages = ["en"]
        self.profile_b.event_languages = ["en", "fr", "de", "lu"]
        self.assertEqual(compute_language_score(self.profile_a, self.profile_b), 1.0)


class AgeFitScoreTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user("age_a", password="test")
        self.user_b = User.objects.create_user("age_b", password="test")
        self.profile_a = CrushProfile.objects.create(
            user=self.user_a, location="canton-luxembourg",
            date_of_birth=date(1996, 4, 5),
            preferred_age_min=25, preferred_age_max=35,
        )
        self.profile_b = CrushProfile.objects.create(
            user=self.user_b, location="canton-luxembourg",
            date_of_birth=date(1996, 7, 15),
            preferred_age_min=25, preferred_age_max=35,
        )

    def test_both_in_range_center(self):
        score = compute_age_fit_score(self.profile_a, self.profile_b)
        self.assertGreater(score, 0.9)

    def test_default_range_returns_075(self):
        self.assertEqual(_age_fit_one_direction(30, 18, 99), 0.75)

    def test_outside_range_penalty(self):
        score = _age_fit_one_direction(40, 25, 35)
        self.assertLess(score, 0.5)

    def test_far_outside_range_zero(self):
        score = _age_fit_one_direction(55, 25, 35)
        self.assertEqual(score, 0.0)

    def test_no_dob_neutral(self):
        self.profile_a.date_of_birth = None
        self.assertEqual(compute_age_fit_score(self.profile_a, self.profile_b), 0.5)

    def test_at_range_edge(self):
        score = _age_fit_one_direction(25, 25, 35)
        self.assertGreaterEqual(score, 0.85)

    def test_exact_center(self):
        score = _age_fit_one_direction(30, 25, 35)
        self.assertEqual(score, 1.0)


class HardFilterTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user("filter_a", password="test")
        self.user_b = User.objects.create_user("filter_b", password="test")
        self.profile_a = CrushProfile.objects.create(
            user=self.user_a, location="canton-luxembourg",
            date_of_birth=date(1990, 4, 5),
            event_languages=["en", "fr"],
            preferred_age_min=25, preferred_age_max=40,
        )
        self.profile_b = CrushProfile.objects.create(
            user=self.user_b, location="canton-luxembourg",
            date_of_birth=date(1992, 7, 15),
            event_languages=["en", "de"],
            preferred_age_min=25, preferred_age_max=40,
        )
        # Both profiles must have traits set to pass the completeness check
        all_q = list(Trait.objects.filter(trait_type="quality").order_by("sort_order"))
        all_d = list(Trait.objects.filter(trait_type="defect").order_by("sort_order"))
        self.profile_a.qualities.set(all_q[:5])
        self.profile_a.defects.set(all_d[:5])
        self.profile_a.sought_qualities.set(all_q[5:10])
        self.profile_b.qualities.set(all_q[5:10])
        self.profile_b.defects.set(all_d[5:10])
        self.profile_b.sought_qualities.set(all_q[:5])

    def test_fails_incomplete_profile(self):
        """Profiles without qualities/defects/sought_qualities are excluded."""
        self.profile_a.qualities.clear()
        self.assertFalse(passes_hard_filters(self.profile_a, self.profile_b))

    def test_fails_no_sought_qualities(self):
        self.profile_b.sought_qualities.clear()
        self.assertFalse(passes_hard_filters(self.profile_a, self.profile_b))

    def test_fails_no_defects(self):
        self.profile_a.defects.clear()
        self.assertFalse(passes_hard_filters(self.profile_a, self.profile_b))

    def test_has_matching_profile_complete(self):
        self.assertTrue(has_matching_profile(self.profile_a))
        self.assertTrue(has_matching_profile(self.profile_b))

    def test_has_matching_profile_incomplete(self):
        self.profile_a.qualities.clear()
        self.assertFalse(has_matching_profile(self.profile_a))

    def test_passes_with_shared_language(self):
        self.assertTrue(passes_hard_filters(self.profile_a, self.profile_b))

    def test_fails_no_shared_language(self):
        self.profile_a.event_languages = ["fr"]
        self.profile_b.event_languages = ["de"]
        self.assertFalse(passes_hard_filters(self.profile_a, self.profile_b))

    def test_passes_empty_language_on_one(self):
        self.profile_a.event_languages = []
        self.assertTrue(passes_hard_filters(self.profile_a, self.profile_b))

    def test_fails_age_out_of_mutual_range(self):
        self.profile_b.preferred_age_min = 18
        self.profile_b.preferred_age_max = 25  # A is ~36, out of B's range
        self.assertFalse(passes_hard_filters(self.profile_a, self.profile_b))

    def test_passes_default_age_range(self):
        self.profile_b.preferred_age_min = 18
        self.profile_b.preferred_age_max = 99
        self.assertTrue(passes_hard_filters(self.profile_a, self.profile_b))

    def test_passes_no_dob(self):
        self.profile_a.date_of_birth = None
        self.assertTrue(passes_hard_filters(self.profile_a, self.profile_b))

    def test_fails_mutual_gender_mismatch(self):
        """Both set preferred_genders, neither wants the other's gender."""
        self.profile_a.gender = "M"
        self.profile_a.preferred_genders = ["F"]
        self.profile_b.gender = "M"
        self.profile_b.preferred_genders = ["F"]
        self.assertFalse(passes_hard_filters(self.profile_a, self.profile_b))

    def test_passes_one_side_wants_other(self):
        """A doesn't want B's gender, but B wants A's — still passes."""
        self.profile_a.gender = "M"
        self.profile_a.preferred_genders = ["F"]
        self.profile_b.gender = "M"
        self.profile_b.preferred_genders = ["M"]
        self.assertTrue(passes_hard_filters(self.profile_a, self.profile_b))

    def test_passes_empty_preferred_genders(self):
        """Empty preferred_genders = not filled out, don't filter."""
        self.profile_a.gender = "M"
        self.profile_a.preferred_genders = []
        self.profile_b.gender = "M"
        self.profile_b.preferred_genders = ["F"]
        self.assertTrue(passes_hard_filters(self.profile_a, self.profile_b))

    def test_passes_compatible_genders(self):
        """Both want each other's gender."""
        self.profile_a.gender = "M"
        self.profile_a.preferred_genders = ["F"]
        self.profile_b.gender = "F"
        self.profile_b.preferred_genders = ["M"]
        self.assertTrue(passes_hard_filters(self.profile_a, self.profile_b))

    def test_stale_scores_deleted(self):
        """When profiles become incompatible, existing MatchScore is deleted."""
        self.profile_a.is_approved = True
        self.profile_a.is_active = True
        self.profile_a.save()
        self.profile_b.is_approved = True
        self.profile_b.is_active = True
        self.profile_b.save()

        # Traits are already set in setUp — just need to set them on these
        # profiles for the update_match_scores_for_user call
        all_q = list(Trait.objects.filter(trait_type="quality").order_by("sort_order"))
        all_d = list(Trait.objects.filter(trait_type="defect").order_by("sort_order"))
        self.profile_a.qualities.set(all_q[:5])
        self.profile_a.defects.set(all_d[:5])
        self.profile_a.sought_qualities.set(all_q[5:10])
        self.profile_b.qualities.set(all_q[5:10])
        self.profile_b.defects.set(all_d[5:10])
        self.profile_b.sought_qualities.set(all_q[:5])

        # First compute — should create a score
        update_match_scores_for_user(self.user_a)
        self.assertEqual(MatchScore.objects.count(), 1)

        # Make them incompatible (no shared language)
        self.profile_a.event_languages = ["fr"]
        self.profile_a.save()
        self.profile_b.event_languages = ["de"]
        self.profile_b.save()

        # Recompute — should delete the stale score
        update_match_scores_for_user(self.user_a)
        self.assertEqual(MatchScore.objects.count(), 0)


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
        """Score should combine all five signals."""
        all_q = list(Trait.objects.filter(trait_type="quality").order_by("sort_order"))
        self.profile_a.qualities.set(all_q[:5])
        self.profile_a.sought_qualities.set(all_q[5:10])
        self.profile_b.qualities.set(all_q[5:10])
        self.profile_b.sought_qualities.set(all_q[:5])

        scores = compute_match_score(self.profile_a, self.profile_b)

        self.assertEqual(scores["score_qualities"], 1.0)
        self.assertGreater(scores["score_zodiac_west"], 0)
        self.assertGreater(scores["score_zodiac_cn"], 0)
        self.assertIn("score_language", scores)
        self.assertIn("score_age_fit", scores)
        # Final should be weighted average of all 5 signals
        expected = (
            WEIGHT_QUALITIES * scores["score_qualities"]
            + WEIGHT_ZODIAC_WEST * scores["score_zodiac_west"]
            + WEIGHT_ZODIAC_CN * scores["score_zodiac_cn"]
            + WEIGHT_LANGUAGE * scores["score_language"]
            + WEIGHT_AGE_FIT * scores["score_age_fit"]
        )
        self.assertAlmostEqual(scores["score_final"], round(expected, 4))

    def test_astro_disabled(self):
        """When astro is disabled, zodiac weights are redistributed."""
        self.profile_a.astro_enabled = False
        self.profile_a.save()

        all_q = list(Trait.objects.filter(trait_type="quality").order_by("sort_order"))
        self.profile_a.qualities.set(all_q[:5])
        self.profile_a.sought_qualities.set(all_q[5:10])
        self.profile_b.qualities.set(all_q[5:10])
        self.profile_b.sought_qualities.set(all_q[:5])

        scores = compute_match_score(self.profile_a, self.profile_b)

        self.assertEqual(scores["score_zodiac_west"], 0.0)
        self.assertEqual(scores["score_zodiac_cn"], 0.0)
        # Zodiac weights redistributed among qualities, language, age_fit
        remaining = WEIGHT_QUALITIES + WEIGHT_LANGUAGE + WEIGHT_AGE_FIT
        expected = (
            (WEIGHT_QUALITIES / remaining) * scores["score_qualities"]
            + (WEIGHT_LANGUAGE / remaining) * scores["score_language"]
            + (WEIGHT_AGE_FIT / remaining) * scores["score_age_fit"]
        )
        self.assertAlmostEqual(scores["score_final"], round(expected, 4))


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
        all_d = list(Trait.objects.filter(trait_type="defect").order_by("sort_order"))
        self.profile_a.qualities.set(all_q[:5])
        self.profile_a.defects.set(all_d[:5])
        self.profile_a.sought_qualities.set(all_q[5:10])
        self.profile_b.qualities.set(all_q[5:10])
        self.profile_b.defects.set(all_d[5:10])
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
    """Test that /matches/ redirects users to dashboard (matches are coach-only)."""

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

    def test_matches_redirects_to_dashboard(self):
        response = self.client.get(
            reverse("crush_lu:matches_list"), HTTP_HOST="crush.lu"
        )
        self.assertEqual(response.status_code, 302)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class CoachMemberMatchesViewTests(TestCase):
    """Test coach views for member matches."""

    def setUp(self):
        # Create coach user
        self.coach_user = User.objects.create_user(
            username="coach@example.com",
            email="coach@example.com",
            password="testpass123",
        )
        self.coach = CrushCoach.objects.create(
            user=self.coach_user, is_active=True
        )

        # Create member with profile
        self.member = User.objects.create_user(
            username="member@example.com",
            email="member@example.com",
            password="testpass123",
        )
        self.member_profile = CrushProfile.objects.create(
            user=self.member,
            location="canton-luxembourg",
            is_approved=True,
            is_active=True,
            date_of_birth=date(1990, 4, 5),
        )

        # Create approved submission linking coach to member
        from crush_lu.models import ProfileSubmission

        ProfileSubmission.objects.create(
            profile=self.member_profile,
            coach=self.coach,
            status="approved",
        )

        consent, _ = UserDataConsent.objects.get_or_create(user=self.coach_user)
        consent.crushlu_consent_given = True
        consent.save()
        self.client.login(username="coach@example.com", password="testpass123")

    def test_coach_members_list(self):
        response = self.client.get(
            reverse("crush_lu:coach_members"), HTTP_HOST="crush.lu"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_members"], 1)

    def test_coach_member_matches_approved_profile(self):
        response = self.client.get(
            reverse("crush_lu:coach_member_matches", args=[self.member.id]),
            HTTP_HOST="crush.lu",
        )
        self.assertEqual(response.status_code, 200)

    def test_coach_member_matches_unapproved_redirects(self):
        self.member_profile.is_approved = False
        self.member_profile.save()
        response = self.client.get(
            reverse("crush_lu:coach_member_matches", args=[self.member.id]),
            HTTP_HOST="crush.lu",
        )
        self.assertEqual(response.status_code, 302)

    def test_coach_member_matches_with_traits(self):
        all_q = list(Trait.objects.filter(trait_type="quality").order_by("sort_order"))
        all_d = list(Trait.objects.filter(trait_type="defect").order_by("sort_order"))
        self.member_profile.qualities.set(all_q[:5])
        self.member_profile.defects.set(all_d[:5])
        self.member_profile.sought_qualities.set(all_q[5:10])

        # Create another user with matching traits
        user_b = User.objects.create_user("user_b", password="test")
        profile_b = CrushProfile.objects.create(
            user=user_b, location="canton-luxembourg",
            is_approved=True, is_active=True,
            date_of_birth=date(1992, 6, 10),
        )
        profile_b.qualities.set(all_q[5:10])
        profile_b.defects.set(all_d[5:10])
        profile_b.sought_qualities.set(all_q[:5])

        update_match_scores_for_user(self.member)

        response = self.client.get(
            reverse("crush_lu:coach_member_matches", args=[self.member.id]),
            HTTP_HOST="crush.lu",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["has_traits"])
        self.assertGreater(len(response.context["matches"]), 0)

    def test_non_coach_cannot_access(self):
        """Regular users cannot access coach member views."""
        regular_user = User.objects.create_user(
            username="regular@example.com",
            email="regular@example.com",
            password="testpass123",
        )
        consent, _ = UserDataConsent.objects.get_or_create(user=regular_user)
        consent.crushlu_consent_given = True
        consent.save()
        self.client.login(username="regular@example.com", password="testpass123")
        response = self.client.get(
            reverse("crush_lu:coach_members"), HTTP_HOST="crush.lu"
        )
        self.assertEqual(response.status_code, 302)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class CoachMatchPairsViewTests(TestCase):
    """Tests for the coach match pairs discovery page."""

    def setUp(self):
        # Create coach user
        self.coach_user = User.objects.create_user(
            username="paircoach@example.com",
            email="paircoach@example.com",
            password="testpass123",
        )
        self.coach = CrushCoach.objects.create(
            user=self.coach_user, is_active=True
        )

        # Create two users with approved profiles
        self.user_a = User.objects.create_user(
            username="pair_a@example.com",
            email="pair_a@example.com",
            password="testpass123",
        )
        self.user_b = User.objects.create_user(
            username="pair_b@example.com",
            email="pair_b@example.com",
            password="testpass123",
        )
        self.profile_a = CrushProfile.objects.create(
            user=self.user_a,
            location="canton-luxembourg",
            is_approved=True,
            is_active=True,
            gender="F",
            date_of_birth=date(1995, 6, 15),
        )
        self.profile_b = CrushProfile.objects.create(
            user=self.user_b,
            location="canton-luxembourg",
            is_approved=True,
            is_active=True,
            gender="M",
            date_of_birth=date(1993, 3, 20),
        )

        # Create approved submissions (for coach assignment)
        from crush_lu.models import ProfileSubmission

        ProfileSubmission.objects.create(
            profile=self.profile_a,
            coach=self.coach,
            status="approved",
        )
        ProfileSubmission.objects.create(
            profile=self.profile_b,
            coach=self.coach,
            status="approved",
        )

        # Create a high match score (enforce user_a.pk < user_b.pk)
        ua, ub = (self.user_a, self.user_b) if self.user_a.pk < self.user_b.pk else (self.user_b, self.user_a)
        MatchScore.objects.create(user_a=ua, user_b=ub, score_final=0.85)

        consent, _ = UserDataConsent.objects.get_or_create(user=self.coach_user)
        consent.crushlu_consent_given = True
        consent.save()
        self.client.login(username="paircoach@example.com", password="testpass123")

    def test_requires_coach(self):
        """Non-coach users should be redirected."""
        regular = User.objects.create_user(
            username="regular_pair@example.com",
            email="regular_pair@example.com",
            password="testpass123",
        )
        consent, _ = UserDataConsent.objects.get_or_create(user=regular)
        consent.crushlu_consent_given = True
        consent.save()
        self.client.login(username="regular_pair@example.com", password="testpass123")
        response = self.client.get(
            reverse("crush_lu:coach_match_pairs"), HTTP_HOST="crush.lu"
        )
        self.assertEqual(response.status_code, 302)

    def test_coach_can_access(self):
        """Coach should see the match pairs page with pair data."""
        response = self.client.get(
            reverse("crush_lu:coach_match_pairs"), HTTP_HOST="crush.lu"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "85%")
        self.assertEqual(response.context["total_pairs"], 1)

    def test_low_scores_excluded(self):
        """Scores below THRESHOLD_GOOD should not appear."""
        user_c = User.objects.create_user("pair_c@example.com", password="testpass123")
        CrushProfile.objects.create(
            user=user_c,
            location="canton-luxembourg",
            is_approved=True,
            is_active=True,
            gender="F",
            date_of_birth=date(1990, 1, 1),
        )
        # Low score pair
        ua, uc = (self.user_a, user_c) if self.user_a.pk < user_c.pk else (user_c, self.user_a)
        MatchScore.objects.create(user_a=ua, user_b=uc, score_final=0.30)

        response = self.client.get(
            reverse("crush_lu:coach_match_pairs"), HTTP_HOST="crush.lu"
        )
        self.assertNotContains(response, "30%")

    def test_inactive_profiles_excluded(self):
        """Pairs with inactive profiles should not appear."""
        self.profile_b.is_active = False
        self.profile_b.save()
        response = self.client.get(
            reverse("crush_lu:coach_match_pairs"), HTTP_HOST="crush.lu"
        )
        self.assertEqual(response.context["total_pairs"], 0)
        # Restore
        self.profile_b.is_active = True
        self.profile_b.save()
