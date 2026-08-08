"""Tests for the Hub social-media planner and its external-service adapters."""

from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from crush_lu.models import (
    CrushProfile,
    EventConnection,
    Interest,
    MeetupEvent,
    UserDataConsent,
)
from hub.buffer_service import (
    BufferPartialFailure,
    BufferServiceError,
    create_buffer_update,
)
from hub.claude_service import (
    ClaudeServiceError,
    GeneratedArticle,
    generate_social_copy,
)
from hub.image_generator import _background_image, generate_kpi_card
from hub.models import HubResource, SocialPost

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

    def _eligible_profile(self, username="eligible@example.com"):
        member = User.objects.create_user(
            username=username,
            email=username,
            first_name="Sophie",
        )
        profile, _ = CrushProfile.objects.update_or_create(
            user=member,
            defaults={
                "verification_status": "verified",
                "is_active": True,
                "approved_at": timezone.now(),
                "date_of_birth": date(1992, 4, 12),
                "location": "Luxembourg",
                "event_vibe": "quiet_corner",
            },
        )
        interest, _ = Interest.objects.update_or_create(
            slug="wine", defaults={"label": "Œnologie", "category": "food"}
        )
        profile.interests_new.add(interest)
        consent, _ = UserDataConsent.objects.update_or_create(
            user=member,
            defaults={
                "crushlu_consent_given": True,
                "marketing_consent": True,
            },
        )
        return member, profile, consent

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
        self.assertIn("source_event_title", response.data["items"][0])
        self.assertIsNone(response.data["items"][0]["source_event_title"])

    def test_manual_creation_always_starts_as_draft(self):
        response = self.client.post(
            "/hub/social/posts",
            {
                "content": "Publication manuelle",
                "status": SocialPost.Status.SCHEDULED,
                "scheduled_for": (timezone.now() + timedelta(days=1)).isoformat(),
                "buffer_profile_ids": ["channel_1"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        post = SocialPost.objects.get(pk=response.data["post"]["id"])
        self.assertEqual(post.status, SocialPost.Status.DRAFT)
        self.assertEqual(post.status_history[-1]["status"], SocialPost.Status.DRAFT)

    def test_manual_creation_validates_platform_and_buffer_id_shapes(self):
        bad_platform = self.client.post(
            "/hub/social/posts",
            {"content": "Post", "platforms": ["unsupported"]},
            format="json",
        )
        bad_channels = self.client.post(
            "/hub/social/posts",
            {"content": "Post", "buffer_profile_ids": {"channel": "one"}},
            format="json",
        )

        self.assertEqual(bad_platform.status_code, 400)
        self.assertEqual(bad_channels.status_code, 400)

    def test_manual_creation_rejects_nested_buffer_platform_values(self):
        # A list is unhashable, so testing it for set membership without a type
        # guard first raises TypeError and turns a bad payload into a 500.
        response = self.client.post(
            "/hub/social/posts",
            {
                "content": "Post",
                "buffer_profile_platforms": {"channel_1": ["facebook"]},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("buffer_profile_platforms", response.data)

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
        "hub.views_social.generate_social_copy",
        side_effect=ClaudeServiceError("sensitive Claude diagnostic"),
    )
    def test_generation_does_not_expose_service_exception(self, _generate_copy):
        response = self.client.post(
            "/hub/social/generate",
            {
                "category": "tips",
                "hook": "Conversation",
                "pillar": "dating_tip",
                "platforms": ["instagram"],
                "languages": ["fr"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.data["error"],
            "Social copy generation is temporarily unavailable.",
        )
        self.assertNotIn("sensitive Claude diagnostic", str(response.data))

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

    @patch("hub.views_social.generate_kpi_card")
    @patch("hub.views_social.generate_social_copy")
    def test_graphics_are_rendered_and_labeled_per_language(
        self, generate_copy, generate_card
    ):
        generate_copy.return_value = {
            "fr": "Publication française.",
            "en": "English publication.",
            "de": "Deutsche Veröffentlichung.",
        }
        generate_card.side_effect = lambda **kwargs: (
            f"https://media.test/{kwargs['language']}.png"
        )

        response = self.client.post(
            "/hub/social/generate",
            {
                "category": "kpis",
                "hook": "Croissance hebdomadaire",
                "pillar": "milestone",
                "platforms": ["linkedin"],
                "languages": ["fr", "en", "de"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            {post["language"]: post["media_url"] for post in response.data["posts"]},
            {
                "fr": "https://media.test/fr.png",
                "en": "https://media.test/en.png",
                "de": "https://media.test/de.png",
            },
        )
        calls = {
            call.kwargs["language"]: call.kwargs
            for call in generate_card.call_args_list
        }
        self.assertEqual(calls["fr"]["stats"][0]["label"], "Nouveaux membres")
        self.assertEqual(calls["en"]["stats"][0]["label"], "New members")
        self.assertEqual(calls["de"]["stats"][0]["label"], "Neue Mitglieder")

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
        "hub.views_social.list_buffer_profiles",
        side_effect=BufferServiceError("sensitive Buffer diagnostic"),
    )
    def test_buffer_profiles_does_not_expose_service_exception(self, _list_profiles):
        response = self.client.get("/hub/social/buffer-profiles")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.data["error"], "Buffer channels are temporarily unavailable."
        )
        self.assertNotIn("sensitive Buffer diagnostic", str(response.data))

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
        with patch.object(
            SocialPost.objects,
            "select_for_update",
            wraps=SocialPost.objects.select_for_update,
        ) as select_for_update:
            first = self.client.post(f"/hub/social/posts/{post.pk}/expand-article")
            second = self.client.post(f"/hub/social/posts/{post.pk}/expand-article")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data["article_id"], second.data["article_id"])
        expand.assert_called_once()
        self.assertEqual(select_for_update.call_count, 2)
        self.assertEqual(HubResource.objects.count(), 1)

    @patch(
        "hub.views_social.expand_social_post",
        side_effect=ClaudeServiceError("sensitive article diagnostic"),
    )
    def test_article_expansion_does_not_expose_service_exception(self, _expand):
        post = SocialPost.objects.create(
            user=self.user,
            hook="Spotlight Guide",
            pillar="dating_tip",
            language="fr",
            content="Contenu à développer",
        )

        response = self.client.post(f"/hub/social/posts/{post.pk}/expand-article")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.data["error"],
            "Article generation is temporarily unavailable.",
        )
        self.assertNotIn("sensitive article diagnostic", str(response.data))

    def test_upcoming_events_endpoint_reads_published_events(self):
        event = self._event()
        self._event(title="Hidden", is_published=False)
        self._event(title="Private", is_private_invitation=True)
        response = self.client.get("/hub/social/upcoming-events")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["id"] for item in response.data["items"]], [str(event.pk)]
        )
        self.assertEqual(response.data["items"][0]["promotion_status"], "not_started")
        self.assertFalse(response.data["items"][0]["is_promoted"])

    @patch("hub.views_social.generate_social_copy")
    def test_event_draft_reuses_stored_copy_without_ai(self, generate_copy):
        event = self._event(
            title_fr="Rencontre au Casino 2000",
            description_fr="Le texte existant de la fiche événement.",
        )

        response = self.client.post(
            f"/hub/social/upcoming-events/{event.pk}/drafts",
            {"languages": ["fr"]},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["copy_source"], "event")
        self.assertEqual(response.data["created_count"], 1)
        generate_copy.assert_not_called()
        post = SocialPost.objects.get(pk=response.data["posts"][0]["id"])
        self.assertEqual(post.source_event, event)
        self.assertEqual(post.pillar, SocialPost.Pillar.PROMO)
        self.assertEqual(post.platforms, ["facebook"])
        self.assertIsNone(post.scheduled_for)
        self.assertIn("Rencontre au Casino 2000", post.content)
        self.assertIn("Le texte existant de la fiche événement.", post.content)
        self.assertIn(event.location, post.content)
        self.assertIn(f"https://crush.lu/fr/events/{event.pk}/", post.content)
        self.assertIn("without AI generation", post.status_history[0]["note"])

    def test_event_draft_creation_is_idempotent(self):
        event = self._event(
            title_fr="Événement idempotent",
            description_fr="Description idempotente",
        )
        endpoint = f"/hub/social/upcoming-events/{event.pk}/drafts"

        first = self.client.post(endpoint, {"languages": ["fr"]}, format="json")
        second = self.client.post(endpoint, {"languages": ["fr"]}, format="json")

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data["posts"][0]["id"], second.data["posts"][0]["id"])
        self.assertEqual(second.data["created_count"], 0)
        self.assertEqual(second.data["reused_count"], 1)
        self.assertEqual(SocialPost.objects.filter(source_event=event).count(), 1)

    def test_event_draft_expands_platforms_without_duplicate_publication(self):
        event = self._event(
            title_fr="Événement multicanal",
            description_fr="Description multicanale",
        )
        first = self.client.post(
            f"/hub/social/upcoming-events/{event.pk}/drafts",
            {"languages": ["fr"]},
            format="json",
        )
        first_post = SocialPost.objects.get(pk=first.data["posts"][0]["id"])
        first_post.buffer_profile_ids = ["facebook_channel"]
        first_post.save(update_fields=["buffer_profile_ids"])

        expanded = self.client.post(
            "/hub/social/generate",
            {
                "category": "events",
                "event_id": str(event.pk),
                "hook": "Ignored for deterministic event copy",
                "pillar": "promo",
                "platforms": ["facebook", "linkedin"],
                "languages": ["fr"],
            },
            format="json",
        )

        self.assertEqual(expanded.status_code, 200)
        self.assertEqual(expanded.data["created_count"], 0)
        self.assertEqual(expanded.data["posts"][0]["id"], first.data["posts"][0]["id"])
        post = SocialPost.objects.get(pk=first.data["posts"][0]["id"])
        self.assertEqual(post.platforms, ["facebook", "linkedin"])
        self.assertEqual(post.buffer_profile_ids, [])
        self.assertEqual(SocialPost.objects.filter(source_event=event).count(), 1)

    def test_event_draft_locks_linked_posts_before_refresh(self):
        event = self._event(
            title_fr="Événement verrouillé",
            description_fr="Description verrouillée",
        )

        with patch.object(
            SocialPost.objects,
            "select_for_update",
            wraps=SocialPost.objects.select_for_update,
        ) as select_for_update:
            response = self.client.post(
                f"/hub/social/upcoming-events/{event.pk}/drafts",
                {"languages": ["fr"]},
                format="json",
            )

        self.assertEqual(response.status_code, 201)
        select_for_update.assert_called_once()

    def test_event_draft_refreshes_when_event_details_change(self):
        event = self._event(
            title_fr="Ancien titre",
            description_fr="Ancienne description",
        )
        endpoint = f"/hub/social/upcoming-events/{event.pk}/drafts"
        first = self.client.post(endpoint, {"languages": ["fr"]}, format="json")
        post = SocialPost.objects.get(pk=first.data["posts"][0]["id"])
        post.status = SocialPost.Status.APPROVED
        post.save(update_fields=["status"])
        event.title_fr = "Nouveau titre"
        event.description_fr = "Nouvelle description"
        event.location = "Nouveau lieu"
        event.save(update_fields=["title_fr", "description_fr", "location"])

        refreshed = self.client.post(endpoint, {"languages": ["fr"]}, format="json")

        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(refreshed.data["posts"][0]["id"], str(post.pk))
        post.refresh_from_db()
        self.assertEqual(post.status, SocialPost.Status.DRAFT)
        self.assertIn("Nouveau titre", post.content)
        self.assertIn("Nouvelle description", post.content)
        self.assertIn("Nouveau lieu", post.content)
        self.assertNotIn("Ancien titre", post.content)
        self.assertIn("Refreshed from the event", post.status_history[-1]["note"])

    def test_deleting_event_removes_drafts_but_preserves_dispatched_history(self):
        event = self._event()
        draft = SocialPost.objects.create(
            user=self.user,
            source_event=event,
            language="fr",
            platforms=["facebook"],
            content="Texte de l’événement",
        )
        dispatched = SocialPost.objects.create(
            user=self.user,
            source_event=event,
            language="fr",
            platforms=["facebook"],
            content="Publication envoyée",
            status=SocialPost.Status.SCHEDULED,
            buffer_id="buffer_post_1",
        )

        event.delete()

        self.assertFalse(SocialPost.objects.filter(pk=draft.pk).exists())
        dispatched.refresh_from_db()
        self.assertIsNone(dispatched.source_event_id)
        self.assertEqual(dispatched.buffer_id, "buffer_post_1")

    def test_event_without_explicit_translations_cannot_create_draft(self):
        event = self._event()
        MeetupEvent.objects.filter(pk=event.pk).update(
            title_fr=None,
            title_en=None,
            title_de=None,
            description_fr=None,
            description_en=None,
            description_de=None,
        )

        upcoming = self.client.get("/hub/social/upcoming-events")
        response = self.client.post(
            f"/hub/social/upcoming-events/{event.pk}/drafts",
            {"languages": ["fr"]},
            format="json",
        )

        item = next(
            item for item in upcoming.data["items"] if item["id"] == str(event.pk)
        )
        self.assertEqual(item["available_languages"], [])
        self.assertEqual(response.status_code, 400)
        self.assertIn("no stored title", response.data["languages"])

    def test_event_draft_rejects_malformed_languages_shape(self):
        event = self._event()

        for languages in (123, [["fr"]]):
            with self.subTest(languages=languages):
                response = self.client.post(
                    f"/hub/social/upcoming-events/{event.pk}/drafts",
                    {"languages": languages},
                    format="json",
                )

                self.assertEqual(response.status_code, 400)
                self.assertIn("supported language", response.data["languages"])

    def test_event_draft_rejects_copy_over_social_length_limit(self):
        event = self._event(
            title_fr="Événement détaillé",
            description_fr="x" * 1500,
        )

        response = self.client.post(
            f"/hub/social/upcoming-events/{event.pk}/drafts",
            {"languages": ["fr"]},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("exceeds 1500", response.data["languages"])
        self.assertFalse(SocialPost.objects.filter(source_event=event).exists())

    def test_upcoming_event_reports_linked_buffer_promotion(self):
        event = self._event()
        post = SocialPost.objects.create(
            user=self.user,
            source_event=event,
            language="fr",
            platforms=["facebook"],
            content="Texte existant",
            status=SocialPost.Status.SCHEDULED,
        )

        response = self.client.get("/hub/social/upcoming-events")

        item = response.data["items"][0]
        self.assertTrue(item["is_promoted"])
        self.assertEqual(item["promotion_status"], SocialPost.Status.SCHEDULED)
        self.assertEqual(item["promotion_post_id"], str(post.pk))

    @patch("hub.views_social.generate_social_copy")
    def test_legacy_event_generate_route_is_also_deterministic(self, generate_copy):
        event = self._event(
            title_fr="Titre stocké",
            description_fr="Description stockée",
        )

        response = self.client.post(
            "/hub/social/generate",
            {
                "category": "events",
                "event_id": str(event.pk),
                "hook": "Cette accroche ne doit pas remplacer le titre",
                "pillar": "promo",
                "platforms": ["facebook"],
                "languages": ["fr"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["copy_source"], "event")
        self.assertIn("Titre stocké", response.data["posts"][0]["content"])
        self.assertNotIn(
            "Cette accroche ne doit pas remplacer le titre",
            response.data["posts"][0]["content"],
        )
        generate_copy.assert_not_called()

    def test_private_event_cannot_create_social_draft(self):
        event = self._event(is_private_invitation=True)

        response = self.client.post(
            f"/hub/social/upcoming-events/{event.pk}/drafts",
            {"languages": ["fr"]},
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(SocialPost.objects.filter(source_event=event).exists())

    def test_event_draft_requires_stored_copy_for_requested_language(self):
        event = self._event(
            title_fr="Titre français",
            description_fr="Description française",
            title_de=None,
            description_de=None,
        )

        response = self.client.post(
            f"/hub/social/upcoming-events/{event.pk}/drafts",
            {"languages": ["de"]},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("de", response.data["languages"])
        self.assertFalse(SocialPost.objects.filter(source_event=event).exists())

    @patch("hub.views_social.create_buffer_update")
    def test_linked_event_is_rechecked_before_buffer_scheduling(self, dispatch):
        event = self._event()
        post = SocialPost.objects.create(
            user=self.user,
            source_event=event,
            language="fr",
            platforms=["facebook"],
            content="Texte de l’événement",
            status=SocialPost.Status.PENDING_REVIEW,
        )
        event.is_cancelled = True
        event.save(update_fields=["is_cancelled"])

        response = self.client.patch(
            f"/hub/social/posts/{post.pk}",
            {
                "status": SocialPost.Status.SCHEDULED,
                "scheduled_for": (timezone.now() + timedelta(days=1)).isoformat(),
                "buffer_profile_ids": ["facebook_channel"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("no longer public", response.data["error"])
        dispatch.assert_not_called()
        post.refresh_from_db()
        self.assertEqual(post.status, SocialPost.Status.PENDING_REVIEW)

    @patch("hub.views_social.create_buffer_update")
    def test_event_post_must_be_scheduled_before_event_starts(self, dispatch):
        event = self._event()
        post = SocialPost.objects.create(
            user=self.user,
            source_event=event,
            language="fr",
            platforms=["facebook"],
            content="Texte de l’événement",
            status=SocialPost.Status.PENDING_REVIEW,
        )

        response = self.client.patch(
            f"/hub/social/posts/{post.pk}",
            {
                "status": SocialPost.Status.SCHEDULED,
                "scheduled_for": (event.date_time + timedelta(hours=1)).isoformat(),
                "buffer_profile_ids": ["facebook_channel"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("before the linked event", response.data["scheduled_for"])
        dispatch.assert_not_called()
        post.refresh_from_db()
        self.assertEqual(post.status, SocialPost.Status.PENDING_REVIEW)

    def test_kpis_summary_endpoint_reads_database(self):
        recent_member, _recent_profile, _consent = self._eligible_profile(
            "recent@example.com"
        )
        old_member, old_profile, _old_consent = self._eligible_profile(
            "old@example.com"
        )
        old_profile.approved_at = timezone.now() - timedelta(days=8)
        old_profile.save(update_fields=["approved_at"])
        User.objects.create_user(
            username="profile-shell@example.com",
            email="profile-shell@example.com",
        )
        event = self._event()
        EventConnection.objects.create(
            requester=recent_member,
            recipient=self.user,
            event=event,
            status="shared",
            shared_at=timezone.now(),
        )
        EventConnection.objects.create(
            requester=old_member,
            recipient=self.user,
            event=event,
            status="pending",
        )

        response = self.client.get("/hub/social/kpis-summary")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["kpis"]["new_members_week"], "+1")
        self.assertEqual(response.data["kpis"]["matches_created_week"], "1")
        self.assertIn("parity_ratio", response.data["kpis"])

    def test_featured_profiles_requires_crush_and_marketing_consent(self):
        _member, _profile, _consent = self._eligible_profile()

        response = self.client.get("/hub/social/featured-profiles")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["items"][0]["first_name"], "Sophie")
        self.assertEqual(response.data["items"][0]["passions"], ["Œnologie"])

    @patch("hub.views_social.create_buffer_update")
    @patch(
        "hub.views_social.generate_profile_card",
        return_value="https://media.test/profile.png",
    )
    @patch(
        "hub.views_social.generate_social_copy",
        return_value={"fr": "Portrait de membre consenti."},
    )
    def test_profile_consent_is_rechecked_before_scheduling(
        self, _generate_copy, _graphic, dispatch
    ):
        _member, profile, consent = self._eligible_profile()
        generated = self.client.post(
            "/hub/social/generate",
            {
                "category": "profiles",
                "profile_id": str(profile.pk),
                "hook": "Membre de la semaine",
                "pillar": "community",
                "platforms": ["instagram"],
                "languages": ["fr"],
            },
            format="json",
        )
        self.assertEqual(generated.status_code, 201)
        post = SocialPost.objects.get(pk=generated.data["posts"][0]["id"])
        self.assertEqual(post.featured_profile_id, profile.pk)

        consent.marketing_consent = False
        consent.save(update_fields=["marketing_consent"])
        response = self.client.patch(
            f"/hub/social/posts/{post.pk}",
            {
                "status": SocialPost.Status.SCHEDULED,
                "scheduled_for": (timezone.now() + timedelta(days=1)).isoformat(),
                "buffer_profile_ids": ["channel_1"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        dispatch.assert_not_called()
        post.refresh_from_db()
        self.assertEqual(post.status, SocialPost.Status.DRAFT)

    @patch("hub.views_social.create_buffer_update")
    def test_scheduling_dispatches_to_buffer(self, dispatch):
        dispatch.return_value = {"success": True, "buffer_id": "post_1,post_2"}
        post = SocialPost.objects.create(
            user=self.user,
            content="Publication prête",
            status=SocialPost.Status.PENDING_REVIEW,
        )
        with patch.object(
            SocialPost.objects,
            "select_for_update",
            wraps=SocialPost.objects.select_for_update,
        ) as select_for_update:
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
        select_for_update.assert_called_once()
        post.refresh_from_db()
        self.assertEqual(post.buffer_id, "post_1,post_2")
        self.assertEqual(post.status, SocialPost.Status.SCHEDULED)

    def test_scheduled_delivery_fields_are_immutable(self):
        post = SocialPost.objects.create(
            user=self.user,
            content="Version envoyée à Buffer",
            status=SocialPost.Status.SCHEDULED,
            scheduled_for=timezone.now() + timedelta(days=1),
            buffer_profile_ids=["channel_1"],
            buffer_id="buffer_post_1",
        )

        response = self.client.patch(
            f"/hub/social/posts/{post.pk}",
            {"content": "Version locale divergente"},
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["fields"], ["content"])
        post.refresh_from_db()
        self.assertEqual(post.content, "Version envoyée à Buffer")

    @patch(
        "hub.views_social.create_buffer_update",
        side_effect=BufferPartialFailure(["buffer_post_1"], ["facebook_channel"]),
    )
    def test_partial_buffer_failure_preserves_ids_and_blocks_duplicate_retry(
        self, dispatch
    ):
        event = self._event(
            title_fr="Événement multicanal",
            description_fr="Description multicanale",
        )
        post = SocialPost.objects.create(
            user=self.user,
            source_event=event,
            content="Publication prête",
            language="fr",
            platforms=["facebook", "linkedin"],
            status=SocialPost.Status.PENDING_REVIEW,
        )
        payload = {
            "status": SocialPost.Status.SCHEDULED,
            "scheduled_for": (timezone.now() + timedelta(days=1)).isoformat(),
            "buffer_profile_ids": ["facebook_channel", "linkedin_channel"],
            "buffer_profile_platforms": {
                "facebook_channel": "facebook",
                "linkedin_channel": "linkedin",
            },
        }

        first = self.client.patch(
            f"/hub/social/posts/{post.pk}", payload, format="json"
        )
        retry = self.client.patch(
            f"/hub/social/posts/{post.pk}", payload, format="json"
        )

        self.assertEqual(first.status_code, 502)
        self.assertEqual(retry.status_code, 409)
        self.assertEqual(dispatch.call_count, 1)
        post.refresh_from_db()
        self.assertEqual(post.status, SocialPost.Status.FAILED)
        self.assertEqual(post.buffer_id, "buffer_post_1")
        self.assertEqual(post.dispatched_platforms, ["facebook"])

        missing_platform = self.client.post(
            "/hub/social/generate",
            {
                "category": "events",
                "event_id": str(event.pk),
                "hook": "Ignored for deterministic event copy",
                "pillar": "promo",
                "platforms": ["facebook", "linkedin"],
                "languages": ["fr"],
            },
            format="json",
        )

        self.assertEqual(missing_platform.status_code, 201)
        self.assertEqual(missing_platform.data["posts"][0]["platforms"], ["linkedin"])

    @patch(
        "hub.views_social.create_buffer_update",
        side_effect=BufferPartialFailure(["buffer_post_1"], ["facebook_channel"]),
    )
    def test_partial_facebook_delivery_still_reports_the_event_as_promoted(
        self, _dispatch
    ):
        event = self._event(
            title_fr="Événement multicanal",
            description_fr="Description multicanale",
        )
        post = SocialPost.objects.create(
            user=self.user,
            source_event=event,
            content="Publication prête",
            language="fr",
            platforms=["facebook", "linkedin"],
            status=SocialPost.Status.PENDING_REVIEW,
        )
        self.client.patch(
            f"/hub/social/posts/{post.pk}",
            {
                "status": SocialPost.Status.SCHEDULED,
                "scheduled_for": (timezone.now() + timedelta(days=1)).isoformat(),
                "buffer_profile_ids": ["facebook_channel", "linkedin_channel"],
                "buffer_profile_platforms": {
                    "facebook_channel": "facebook",
                    "linkedin_channel": "linkedin",
                },
            },
            format="json",
        )
        # FAILED ranks below DRAFT, so the later draft wins the status ranking
        # even though the Facebook publication lives on the failed post.
        SocialPost.objects.create(
            user=self.user,
            source_event=event,
            content="Brouillon plus récent",
            language="fr",
            platforms=["facebook"],
            status=SocialPost.Status.DRAFT,
        )

        item = self.client.get("/hub/social/upcoming-events").data["items"][0]

        self.assertTrue(item["is_promoted"])
        self.assertEqual(item["promotion_post_id"], str(post.pk))
        self.assertEqual(item["promotion_status"], SocialPost.Status.FAILED)

    @patch(
        "hub.views_social.create_buffer_update",
        return_value={
            "buffer_id": "buffer_post_1",
            "created_profile_ids": ["facebook_channel"],
        },
    )
    def test_scheduling_rejects_channels_mapped_outside_the_post_platforms(
        self, dispatch
    ):
        post = SocialPost.objects.create(
            user=self.user,
            content="Publication prête",
            language="fr",
            platforms=["facebook"],
            status=SocialPost.Status.PENDING_REVIEW,
        )

        response = self.client.patch(
            f"/hub/social/posts/{post.pk}",
            {
                "status": SocialPost.Status.SCHEDULED,
                "scheduled_for": (timezone.now() + timedelta(days=1)).isoformat(),
                "buffer_profile_ids": ["facebook_channel"],
                "buffer_profile_platforms": {"facebook_channel": "linkedin"},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("buffer_profile_platforms", response.data)
        dispatch.assert_not_called()
        post.refresh_from_db()
        self.assertEqual(post.status, SocialPost.Status.PENDING_REVIEW)
        self.assertEqual(post.dispatched_platforms, [])

    @patch(
        "hub.views_social.create_buffer_update",
        side_effect=BufferServiceError("sensitive scheduling diagnostic"),
    )
    def test_scheduling_does_not_expose_or_persist_service_exception(self, _dispatch):
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
                "buffer_profile_ids": ["channel_1"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.data["error"],
            "Buffer could not schedule this post. Try again later.",
        )
        self.assertNotIn("sensitive scheduling diagnostic", str(response.data))
        post.refresh_from_db()
        self.assertEqual(post.status, SocialPost.Status.FAILED)
        self.assertNotIn("sensitive scheduling diagnostic", str(post.status_history))

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

    @patch("hub.buffer_service._create_channel_post")
    def test_partial_failure_reports_already_created_post_ids(self, create_post):
        create_post.side_effect = [
            "buffer_post_1",
            BufferServiceError("second channel failed"),
        ]

        with self.assertRaises(BufferPartialFailure) as raised:
            create_buffer_update(
                text="Hello Luxembourg",
                profile_ids=["channel_1", "channel_2"],
            )

        self.assertEqual(raised.exception.created_post_ids, ["buffer_post_1"])
        self.assertEqual(raised.exception.created_profile_ids, ["channel_1"])

    @override_settings(BUFFER_API_KEY="")
    def test_missing_buffer_key_fails_closed(self):
        with self.assertRaises(BufferServiceError):
            create_buffer_update(text="Hello", profile_ids=["channel_1"])

    def test_localhost_media_is_rejected_before_buffer_dispatch(self):
        with self.assertRaisesMessage(
            BufferServiceError, "publicly reachable HTTP(S) URL"
        ):
            create_buffer_update(
                text="Hello",
                profile_ids=["channel_1"],
                media_url="http://localhost:8000/media/social/card.png",
            )


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
    @patch("hub.image_generator.requests.get")
    def test_remote_background_stops_streaming_at_size_limit(self, get):
        response = Mock()
        response.headers = {}
        response.iter_content.return_value = [b"1234", b"5"]
        get.return_value = response

        with patch("hub.image_generator.MAX_BACKGROUND_BYTES", 4):
            image = _background_image("https://media.test/oversized.png")

        self.assertIsNone(image)
        get.assert_called_once_with(
            "https://media.test/oversized.png", timeout=6, stream=True
        )
        response.close.assert_called_once()

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
