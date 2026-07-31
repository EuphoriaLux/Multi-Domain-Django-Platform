"""Tests for the Hub social-media planner and its external-service adapters."""

from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from crush_lu.models import CrushProfile, Interest, MeetupEvent, UserDataConsent
from hub.buffer_service import BufferServiceError, create_buffer_update
from hub.claude_service import GeneratedArticle, generate_social_copy
from hub.image_generator import generate_kpi_card
from hub.models import SocialPost

User = get_user_model()


class SocialMediaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="coach_test",
            email="coach@crush.lu",
            password="password123",
            is_staff=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _event(self, **overrides):
        now = timezone.now()
        values = {
            "title": "Rencontre au Casino 2000",
            "description": "Une soirée de rencontres et de dégustation.",
            "event_type": "speed_dating",
            "location": "Casino 2000, Mondorf-les-Bains",
            "address": "Rue Flammang, Mondorf-les-Bains",
            "date_time": now + timedelta(days=7),
            "registration_deadline": now + timedelta(days=5),
            "is_published": True,
        }
        values.update(overrides)
        return MeetupEvent.objects.create(**values)

    def test_non_staff_cannot_access_marketing_endpoints(self):
        non_staff = User.objects.create_user(username="member", password="password123")
        self.client.force_authenticate(user=non_staff)
        response = self.client.get("/hub/social/posts")
        self.assertEqual(response.status_code, 403)

    def test_create_and_list_social_posts(self):
        SocialPost.objects.create(
            user=self.user,
            hook="Soirée œnologique",
            pillar="event_recap",
            language="fr",
            content="Dégustation d'exception ce jeudi !",
            status="draft",
        )
        response = self.client.get("/hub/social/posts")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["items"][0]["hook"], "Soirée œnologique")

    @patch("hub.views_social.generate_social_copy")
    def test_batch_generation_uses_claude_copy(self, generate_copy):
        generate_copy.return_value = {
            "fr": "Une publication française vérifiée pour Crush.lu.",
            "en": "A verified English Crush.lu social post.",
        }
        response = self.client.post(
            "/hub/social/generate",
            {
                "category": "tips",
                "hook": "Créer une première conversation naturelle",
                "pillar": "dating_tip",
                "platforms": ["instagram", "facebook"],
                "languages": ["fr", "en"],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(response.data["posts"]), 2)
        self.assertTrue(timezone.is_aware(SocialPost.objects.first().scheduled_for))
        generate_copy.assert_called_once()

    @patch(
        "hub.views_social.generate_kpi_card", return_value="https://media.test/kpi.png"
    )
    @patch("hub.views_social.generate_social_copy")
    def test_kpi_generation_uses_database_snapshot(self, generate_copy, _graphic):
        generate_copy.return_value = {
            "fr": "Des chiffres réels issus de la plateforme."
        }
        response = self.client.post(
            "/hub/social/generate",
            {
                "category": "kpis",
                "hook": "Croissance hebdomadaire",
                "pillar": "milestone",
                "platforms": ["linkedin"],
                "languages": ["fr"],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        context = generate_copy.call_args.kwargs["context"]
        self.assertIn("new_members_week", context)
        self.assertEqual(
            response.data["posts"][0]["media_url"], "https://media.test/kpi.png"
        )

    @patch("hub.views_social.list_buffer_profiles")
    def test_buffer_profiles_list(self, list_profiles):
        list_profiles.return_value = [
            {
                "id": "channel_1",
                "service": "instagram",
                "service_username": "crush.lu",
                "formatted_username": "Crush.lu",
            }
        ]
        response = self.client.get("/hub/social/buffer-profiles")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["items"][0]["id"], "channel_1")

    @patch(
        "hub.views_social.expand_social_post",
        return_value=GeneratedArticle(
            title="Guide Crush.lu",
            content="# Guide\n\n" + "Contenu vérifié. " * 30,
        ),
    )
    def test_expand_post_to_article_is_ai_backed_and_idempotent(self, expand):
        post = SocialPost.objects.create(
            user=self.user,
            hook="Spotlight Guide",
            pillar="dating_tip",
            language="fr",
            content="Les secrets d'un profil attrayant sur Crush.lu",
        )
        first = self.client.post(f"/hub/social/posts/{post.pk}/expand-article")
        second = self.client.post(f"/hub/social/posts/{post.pk}/expand-article")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data["article_id"], second.data["article_id"])
        expand.assert_called_once()

    def test_upcoming_events_endpoint_reads_published_events(self):
        event = self._event()
        self._event(title="Hidden", is_published=False)
        response = self.client.get("/hub/social/upcoming-events")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["id"] for item in response.data["items"]], [str(event.pk)]
        )

    def test_kpis_summary_endpoint_reads_database(self):
        response = self.client.get("/hub/social/kpis-summary")
        self.assertEqual(response.status_code, 200)
        self.assertIn("new_members_week", response.data["kpis"])
        self.assertIn("parity_ratio", response.data["kpis"])

    def test_featured_profiles_requires_crush_and_marketing_consent(self):
        member = User.objects.create_user(
            username="eligible@example.com",
            email="eligible@example.com",
            first_name="Sophie",
        )
        profile, _ = CrushProfile.objects.update_or_create(
            user=member,
            defaults={
                "verification_status": "verified",
                "is_active": True,
                "date_of_birth": date(1992, 4, 12),
                "location": "Luxembourg",
                "event_vibe": "quiet_corner",
            },
        )
        interest, _ = Interest.objects.update_or_create(
            slug="wine", defaults={"label": "Œnologie", "category": "food"}
        )
        profile.interests_new.add(interest)
        UserDataConsent.objects.update_or_create(
            user=member,
            defaults={
                "crushlu_consent_given": True,
                "marketing_consent": True,
            },
        )

        response = self.client.get("/hub/social/featured-profiles")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["items"][0]["first_name"], "Sophie")
        self.assertEqual(response.data["items"][0]["passions"], ["Œnologie"])

    @patch("hub.views_social.create_buffer_update")
    def test_scheduling_dispatches_to_buffer(self, dispatch):
        dispatch.return_value = {"success": True, "buffer_id": "post_1,post_2"}
        post = SocialPost.objects.create(
            user=self.user,
            content="Publication prête",
            status=SocialPost.Status.PENDING_REVIEW,
        )
        response = self.client.patch(
            f"/hub/social/posts/{post.pk}",
            {
                "status": "scheduled",
                "scheduled_for": (timezone.now() + timedelta(days=1)).isoformat(),
                "buffer_profile_ids": ["channel_1", "channel_2"],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        post.refresh_from_db()
        self.assertEqual(post.buffer_id, "post_1,post_2")
        self.assertEqual(post.status, SocialPost.Status.SCHEDULED)

    def test_scheduling_requires_time_and_channel_without_mutating_status(self):
        post = SocialPost.objects.create(
            user=self.user,
            content="Publication prête",
            status=SocialPost.Status.PENDING_REVIEW,
        )
        response = self.client.patch(
            f"/hub/social/posts/{post.pk}", {"status": "scheduled"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        post.refresh_from_db()
        self.assertEqual(post.status, SocialPost.Status.PENDING_REVIEW)


@override_settings(
    BUFFER_API_KEY="buffer-key",
    BUFFER_ORGANIZATION_ID="org_1",
    BUFFER_TIMEOUT_SECONDS=10,
)
class BufferServiceTests(SimpleTestCase):
    @patch("hub.buffer_service.requests.post")
    def test_creates_one_graphql_post_per_channel(self, post_request):
        responses = []
        for post_id in ("post_1", "post_2"):
            response = Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = {
                "data": {
                    "createPost": {
                        "__typename": "PostActionSuccess",
                        "post": {"id": post_id, "status": "buffer", "dueAt": None},
                    }
                }
            }
            responses.append(response)
        post_request.side_effect = responses

        result = create_buffer_update(
            text="Hello Luxembourg",
            profile_ids=["channel_1", "channel_2"],
            scheduled_at="2026-08-07T14:00:00Z",
            media_url="https://media.crush.lu/social/card.png",
        )

        self.assertEqual(result["buffer_id"], "post_1,post_2")
        self.assertEqual(post_request.call_count, 2)
        first_input = post_request.call_args_list[0].kwargs["json"]["variables"][
            "input"
        ]
        self.assertEqual(first_input["mode"], "customScheduled")
        self.assertEqual(
            first_input["assets"],
            [{"image": {"url": "https://media.crush.lu/social/card.png"}}],
        )

    @override_settings(BUFFER_API_KEY="")
    def test_missing_buffer_key_fails_closed(self):
        with self.assertRaises(BufferServiceError):
            create_buffer_update(text="Hello", profile_ids=["channel_1"])


@override_settings(
    ANTHROPIC_API_KEY="anthropic-key",
    ANTHROPIC_MODEL="claude-sonnet-5",
    ANTHROPIC_TIMEOUT_SECONDS=10,
)
class ClaudeServiceTests(SimpleTestCase):
    @patch("hub.claude_service.requests.post")
    def test_social_generation_uses_structured_output(self, post_request):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "content": [
                {
                    "type": "text",
                    "text": '{"posts":[{"language":"fr","content":"Un contenu suffisamment long et vérifié pour Crush.lu."}]}',
                }
            ]
        }
        post_request.return_value = response

        result = generate_social_copy(
            category="tips",
            pillar="dating_tip",
            hook="Conversation",
            platforms=["instagram"],
            languages=["fr"],
            context={"topic": "Conversation"},
        )

        self.assertIn("fr", result)
        payload = post_request.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "claude-sonnet-5")
        self.assertEqual(payload["output_config"]["format"]["type"], "json_schema")


class ImageGeneratorTests(SimpleTestCase):
    def test_pillow_renderer_creates_public_png_without_browser_runtime(self):
        with TemporaryDirectory() as media_root:
            with self.settings(
                MEDIA_ROOT=media_root,
                MEDIA_URL="/media/",
                BACKEND_BASE_URL="https://api.crush.lu",
                STORAGES={
                    "default": {
                        "BACKEND": "django.core.files.storage.FileSystemStorage"
                    },
                    "crush_media": {
                        "BACKEND": "django.core.files.storage.FileSystemStorage"
                    },
                },
            ):
                url = generate_kpi_card(
                    stats=[{"value": "+12", "label": "Nouveaux membres"}]
                )
                generated = list(Path(media_root).glob("social/kpi_card_*.png"))
                self.assertEqual(len(generated), 1)
                png_header = generated[0].read_bytes()[:4]

        self.assertEqual(png_header, b"\x89PNG")
        self.assertTrue(url.startswith("https://api.crush.lu/media/"))
