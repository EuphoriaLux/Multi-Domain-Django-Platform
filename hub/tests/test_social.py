"""
Unit tests for social media planning endpoints in hub app.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from hub.models import SocialPost

User = get_user_model()


class SocialMediaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="coach_test", email="coach@crush.lu", password="password123"
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_create_and_list_social_posts(self):
        post = SocialPost.objects.create(
            user=self.user,
            hook="Soirée Oenologique",
            pillar="event_recap",
            language="fr",
            content="Dégustation d'exception ce jeudi !",
            status="draft",
        )

        res = self.client.get("/hub/social/posts")
        self.assertEqual(res.status_code, 200)
        self.assertIn("items", res.data)
        self.assertEqual(len(res.data["items"]), 1)
        self.assertEqual(res.data["items"][0]["hook"], "Soirée Oenologique")

    def test_batch_generate_social_posts(self):
        payload = {
            "hook": "Lancement Crush Connect",
            "pillar": "milestone",
            "platforms": ["instagram", "facebook"],
            "languages": ["fr", "en"],
        }
        res = self.client.post("/hub/social/generate", payload, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertIn("posts", res.data)
        self.assertEqual(len(res.data["posts"]), 2)

    def test_buffer_profiles_list(self):
        res = self.client.get("/hub/social/buffer-profiles")
        self.assertEqual(res.status_code, 200)
        self.assertIn("items", res.data)
        self.assertGreaterEqual(len(res.data["items"]), 1)

    def test_batch_generate_kpi_card_post(self):
        payload = {
            "category": "kpis",
            "hook": "Croissance Hebdomadaire",
            "pillar": "growth",
            "stats": [
                {"value": "+200", "label": "Membres"},
                {"value": "80", "label": "Matchs"}
            ],
            "languages": ["fr"],
        }
        res = self.client.post("/hub/social/generate", payload, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertIn("posts", res.data)
        self.assertEqual(len(res.data["posts"]), 1)

    def test_expand_post_to_article(self):
        post = SocialPost.objects.create(
            user=self.user,
            hook="Spotlight Guide",
            pillar="guide",
            language="fr",
            content="Les secrets d'un profil attrayant sur Crush.lu",
            status="draft",
        )
        res = self.client.post(f"/hub/social/posts/{post.id}/expand-article")
        self.assertEqual(res.status_code, 200)
        self.assertIn("article_id", res.data)
        post.refresh_from_db()
        self.assertIsNotNone(post.article_id)

    def test_upcoming_events_endpoint(self):
        res = self.client.get("/hub/social/upcoming-events")
        self.assertEqual(res.status_code, 200)
        self.assertIn("items", res.data)

    def test_kpis_summary_endpoint(self):
        res = self.client.get("/hub/social/kpis-summary")
        self.assertEqual(res.status_code, 200)
        self.assertIn("kpis", res.data)

    def test_featured_profiles_endpoint(self):
        res = self.client.get("/hub/social/featured-profiles")
        self.assertEqual(res.status_code, 200)
        self.assertIn("items", res.data)

