"""Tests for Power-Up Pixel War routing, domain isolation, API hardening, and admin."""

from datetime import timedelta
from io import StringIO
import json
import re

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, RequestFactory, TestCase, override_settings
from django.utils import timezone

from entreprinder.admin import entreprinder_admin_site
from power_up.admin import power_up_admin_site
from power_up.permissions import POWER_UP_STAFF_GROUP
from power_up.pixel_war.models import (
    Pixel,
    PixelCanvas,
    PixelHistory,
    UserPixelCooldown,
    UserPixelStats,
)

User = get_user_model()
CSRF_INPUT_RE = re.compile(
    r'name=["\']csrfmiddlewaretoken["\']\s+value=["\']([^"\']+)["\']'
    r'|value=["\']([^"\']+)["\']\s+name=["\']csrfmiddlewaretoken["\']'
)


def _extract_csrf_token(html):
    match = CSRF_INPUT_RE.search(html)
    assert match is not None, "Expected hidden csrfmiddlewaretoken input in HTML"
    return match.group(1) or match.group(2)


@override_settings(ALLOWED_HOSTS=["*"])
class PixelWarRoutingTests(TestCase):
    """Pixel War serves on both power-up.lu and powerup.lu and stays off other domains."""

    @classmethod
    def setUpTestData(cls):
        Site.objects.update_or_create(
            domain="power-up.lu", defaults={"name": "Power-Up"}
        )
        Site.objects.update_or_create(
            domain="powerup.lu", defaults={"name": "Power-Up"}
        )
        Site.objects.update_or_create(
            domain="entreprinder.lu", defaults={"name": "Entreprinder"}
        )
        Site.objects.update_or_create(
            domain="vinsdelux.com", defaults={"name": "VinsDelux"}
        )

    def setUp(self):
        cache.clear()

    def test_unprefixed_pixel_war_redirects_to_language_on_both_powerup_hosts(self):
        for host in ("power-up.lu", "powerup.lu"):
            client = Client(HTTP_HOST=host)
            with self.subTest(host=host, lang="default"):
                response = client.get("/pixel-war/")
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], "/en/pixel-war/")

            with self.subTest(host=host, lang="fr"):
                response = client.get("/pixel-war/", HTTP_ACCEPT_LANGUAGE="fr")
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], "/fr/pixel-war/")

    def test_pixel_war_page_renders_clean_bundle_in_all_languages(self):
        for host in ("power-up.lu", "powerup.lu"):
            client = Client(HTTP_HOST=host)
            for lang in ("en", "de", "fr"):
                with self.subTest(host=host, lang=lang):
                    response = client.get(f"/{lang}/pixel-war/")
                    self.assertEqual(response.status_code, 200)
                    self.assertTemplateUsed(response, "pixel_war/pixel_war.html")
                    body = response.content.decode()
                    self.assertIn('id="pixel-war-config"', body)
                    self.assertIn("power_up/js/pixel_war.js", body)
                    self.assertIn('name="csrfmiddlewaretoken"', body)
                    self.assertNotIn("Vite asset unavailable", body)
                    self.assertNotIn("unpkg.com", body)
                    self.assertNotIn("/accounts/signup/", body)

    def test_legacy_pixel_war_paths_permanently_redirect_on_powerup(self):
        client = Client(HTTP_HOST="powerup.lu")
        for legacy_path in (
            "/pixelwar/",
            "/vibe-coding/",
            "/vibe-coding/pixel-war/",
            "/vibe-coding/pixel-war/demo/",
        ):
            with self.subTest(legacy_path=legacy_path):
                response = client.get(legacy_path)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response["Location"], "/pixel-war/")

    def test_pixel_war_and_vibe_coding_removed_from_entreprinder_and_vinsdelux(self):
        entreprinder_client = Client(HTTP_HOST="entreprinder.lu")
        for dead_path in (
            "/vibe-coding/",
            "/vibe-coding/pixel-war/",
            "/vibe-coding/api/canvas-state/",
            "/pixel-war/",
            "/pixel-war/api/canvas-state/",
        ):
            with self.subTest(host="entreprinder.lu", path=dead_path):
                response = entreprinder_client.get(dead_path)
                self.assertEqual(response.status_code, 404)

        # Removing the Vibe Coding link from entreprinder/templates/base.html must not break home.
        home_response = entreprinder_client.get("/")
        self.assertEqual(home_response.status_code, 200)
        self.assertNotIn("vibe-coding", home_response.content.decode())

        vinsdelux_client = Client(HTTP_HOST="vinsdelux.com")
        self.assertEqual(vinsdelux_client.get("/vibe/").status_code, 404)

    def test_powerup_footer_and_work_pages_link_to_pixel_war(self):
        client = Client(HTTP_HOST="powerup.lu")
        work_resp = client.get("/en/work/")
        self.assertEqual(work_resp.status_code, 200)
        work_html = work_resp.content.decode()
        self.assertIn("Lux Pixel War", work_html)
        self.assertIn('href="/pixel-war/"', work_html)
        self.assertIn('href="/en/pixel-war/"', work_html)


@override_settings(ALLOWED_HOSTS=["*"])
class PixelWarAPITests(TestCase):
    """API functionality, validation, cooldown enforcement, CSRF safety, and query bounds."""

    @classmethod
    def setUpTestData(cls):
        Site.objects.update_or_create(
            domain="power-up.lu", defaults={"name": "Power-Up"}
        )
        Site.objects.update_or_create(
            domain="powerup.lu", defaults={"name": "Power-Up"}
        )

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="powerup.lu")
        self.canvas = PixelCanvas.objects.create(
            name="Lux Pixel War",
            width=20,
            height=20,
            anonymous_cooldown_seconds=10,
            registered_cooldown_seconds=5,
            anonymous_pixels_per_minute=2,
            registered_pixels_per_minute=4,
            is_active=True,
        )

    def _post_pixel(self, payload, client=None):
        target = client or self.client
        return target.post(
            "/pixel-war/api/place-pixel/",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_canvas_state_masks_email_usernames_and_avoids_n_plus_one(self):
        email_user = User.objects.create_user(
            username="alice@example.com",
            email="alice@example.com",
            first_name="Alice",
            password="pw",
        )
        email_no_first = User.objects.create_user(
            username="bob@example.com",
            email="bob@example.com",
            first_name="",
            password="pw",
        )
        handle_user = User.objects.create_user(
            username="charlie",
            email="charlie@example.com",
            password="pw",
        )
        Pixel.objects.create(
            canvas=self.canvas, x=1, y=1, color="#FE4901", placed_by=email_user
        )

        # Warm site/session lookup once, then compare query counts between 1 and 4 pixels.
        warmup = self.client.get(f"/pixel-war/api/canvas-state/{self.canvas.id}/")
        self.assertEqual(warmup.status_code, 200)
        with self.assertNumQueries(2):
            self.client.get(f"/pixel-war/api/canvas-state/{self.canvas.id}/")

        Pixel.objects.create(
            canvas=self.canvas, x=2, y=2, color="#22C55E", placed_by=email_no_first
        )
        Pixel.objects.create(
            canvas=self.canvas, x=3, y=3, color="#2563EB", placed_by=handle_user
        )
        Pixel.objects.create(
            canvas=self.canvas, x=4, y=4, color="#151625", placed_by=None
        )

        with self.assertNumQueries(2):
            resp = self.client.get("/pixel-war/api/canvas-state/")

        alt_resp = Client(HTTP_HOST="power-up.lu").get("/pixel-war/api/canvas-state/")
        self.assertEqual(alt_resp.status_code, 200)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["pixel_count"], 4)
        self.assertEqual(data["pixels"]["1,1"]["placed_by"], "Alice")
        self.assertEqual(data["pixels"]["2,2"]["placed_by"], "Member")
        self.assertEqual(data["pixels"]["3,3"]["placed_by"], "charlie")
        self.assertEqual(data["pixels"]["4,4"]["placed_by"], "Anonymous")
        for pixel in data["pixels"].values():
            self.assertNotIn("@", pixel["placed_by"])

    def test_place_pixel_validation_rejects_bad_inputs(self):
        bad_payloads = [
            {"x": True, "y": 1, "color": "#FF0000"},
            {"x": 1, "y": False, "color": "#FF0000"},
            {"x": 1.5, "y": 1, "color": "#FF0000"},
            {"x": "bad", "y": 1, "color": "#FF0000"},
            {"x": -1, "y": 1, "color": "#FF0000"},
            {"x": 20, "y": 1, "color": "#FF0000"},
            {"x": 1, "y": 20, "color": "#FF0000"},
            {"x": 1, "y": 1, "color": "#abc"},
            {"x": 1, "y": 1, "color": "red"},
            {"x": 1, "y": 1, "color": "#GGGGGG"},
            {"x": 1, "y": 1, "color": "<script"},
        ]
        for payload in bad_payloads:
            with self.subTest(payload=payload):
                resp = self._post_pixel({**payload, "canvas_id": self.canvas.id})
                self.assertEqual(resp.status_code, 400)

        malformed = self.client.post(
            "/pixel-war/api/place-pixel/",
            data="{not-json",
            content_type="application/json",
        )
        self.assertEqual(malformed.status_code, 400)

        self.canvas.is_active = False
        self.canvas.save(update_fields=["is_active"])
        inactive_resp = self._post_pixel(
            {"x": 2, "y": 3, "color": "#fe4901", "canvas_id": self.canvas.id}
        )
        self.assertEqual(inactive_resp.status_code, 400)

    def test_place_pixel_normalizes_hex_and_enforces_cooldown_and_minute_limit(self):
        first = self._post_pixel(
            {"x": 5, "y": 6, "color": "#fe4901", "canvas_id": self.canvas.id}
        )
        self.assertEqual(first.status_code, 200)
        first_data = first.json()
        self.assertEqual(first_data["pixel"]["color"], "#FE4901")
        self.assertEqual(first_data["cooldown_info"]["cooldown_seconds"], 10)
        self.assertEqual(first_data["cooldown_info"]["pixels_remaining"], 1)
        self.assertTrue(
            PixelHistory.objects.filter(
                canvas=self.canvas, x=5, y=6, color="#FE4901"
            ).exists()
        )

        # Second immediate attempt hits per-pixel cooldown.
        too_soon = self._post_pixel(
            {"x": 5, "y": 7, "color": "#2563EB", "canvas_id": self.canvas.id}
        )
        self.assertEqual(too_soon.status_code, 429)
        self.assertGreaterEqual(too_soon.json()["cooldown_remaining"], 1)

        # Backdate last_placed past the 10s per-pixel cooldown (while staying inside the 60s window).
        cooldown = UserPixelCooldown.objects.get(canvas=self.canvas)
        UserPixelCooldown.objects.filter(pk=cooldown.pk).update(
            last_placed=timezone.now() - timedelta(seconds=15),
            last_minute_reset=timezone.now() - timedelta(seconds=15),
        )
        second = self._post_pixel(
            {"x": 5, "y": 7, "color": "#2563EB", "canvas_id": self.canvas.id}
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["cooldown_info"]["pixels_remaining"], 0)

        # Even if per-pixel cooldown passes, per-minute cap (2) blocks a 3rd pixel in the same minute.
        UserPixelCooldown.objects.filter(pk=cooldown.pk).update(
            last_placed=timezone.now() - timedelta(seconds=15),
            last_minute_reset=timezone.now() - timedelta(seconds=20),
        )
        capped = self._post_pixel(
            {"x": 5, "y": 8, "color": "#22C55E", "canvas_id": self.canvas.id}
        )
        self.assertEqual(capped.status_code, 429)
        self.assertIn("minute", capped.json()["error"].lower())

    def test_place_pixel_handles_null_last_minute_reset_and_updates_user_stats(self):
        user = User.objects.create_user(
            username="painter", email="painter@example.com", password="pw"
        )
        UserPixelCooldown.objects.create(
            user=user,
            canvas=self.canvas,
            pixels_placed_last_minute=3,
            last_minute_reset=None,
        )
        UserPixelCooldown.objects.filter(user=user, canvas=self.canvas).update(
            last_minute_reset=None,
            last_placed=timezone.now() - timedelta(seconds=10),
        )
        self.client.force_login(user)

        resp = self._post_pixel(
            {"x": 1, "y": 2, "color": "#9333EA", "canvas_id": self.canvas.id}
        )
        self.assertEqual(resp.status_code, 200)
        stats = UserPixelStats.objects.get(user=user, canvas=self.canvas)
        self.assertEqual(stats.total_pixels_placed, 1)

    @override_settings(CSRF_COOKIE_HTTPONLY=True)
    def test_place_pixel_succeeds_with_httponly_csrf_cookie(self):
        csrf_client = Client(HTTP_HOST="powerup.lu", enforce_csrf_checks=True)
        page_resp = csrf_client.get("/en/pixel-war/")
        self.assertEqual(page_resp.status_code, 200)
        token = _extract_csrf_token(page_resp.content.decode())

        post_resp = csrf_client.post(
            "/pixel-war/api/place-pixel/",
            data=json.dumps(
                {"x": 4, "y": 4, "color": "#FE4901", "canvas_id": self.canvas.id}
            ),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(post_resp.status_code, 200)
        self.assertTrue(post_resp.json()["success"])

    def test_pixel_history_validation_cap_masking_and_query_count(self):
        u1 = User.objects.create_user(
            username="u1@example.com",
            email="u1@example.com",
            first_name="U1",
            password="pw",
        )
        u2 = User.objects.create_user(
            username="u2@example.com",
            email="u2@example.com",
            first_name="",
            password="pw",
        )
        PixelHistory.objects.create(
            canvas=self.canvas, x=0, y=0, color="#111111", placed_by=u1
        )
        PixelHistory.objects.create(
            canvas=self.canvas, x=1, y=1, color="#222222", placed_by=u2
        )

        self.assertEqual(
            self.client.get("/pixel-war/api/pixel-history/?limit=abc").status_code, 400
        )

        with self.assertNumQueries(2):
            resp = self.client.get(
                f"/pixel-war/api/pixel-history/?canvas_id={self.canvas.id}&limit=500"
            )
        self.assertEqual(resp.status_code, 200)
        history = resp.json()["history"]
        self.assertEqual(len(history), 2)
        for item in history:
            self.assertNotIn("@", item["placed_by"])

        # Add 105 entries to verify MAX_HISTORY_LIMIT (100) cap.
        PixelHistory.objects.bulk_create(
            [
                PixelHistory(
                    canvas=self.canvas, x=2, y=i % 20, color="#FFFFFF", placed_by=None
                )
                for i in range(105)
            ]
        )
        capped = self.client.get(
            f"/pixel-war/api/pixel-history/?canvas_id={self.canvas.id}&limit=500"
        )
        self.assertEqual(capped.status_code, 200)
        self.assertEqual(len(capped.json()["history"]), 100)


@override_settings(ALLOWED_HOSTS=["*"])
class PixelWarAdminAndCommandsTests(TestCase):
    """Admin registration lives exclusively on power_up_admin_site and commands run."""

    def test_models_registered_on_power_up_admin_only(self):
        pixel_models = (
            PixelCanvas,
            Pixel,
            PixelHistory,
            UserPixelCooldown,
            UserPixelStats,
        )
        for model in pixel_models:
            with self.subTest(model=model.__name__):
                self.assertTrue(power_up_admin_site.is_registered(model))
                self.assertFalse(entreprinder_admin_site.is_registered(model))

    def test_power_up_admin_groups_pixel_war_models(self):
        group, _ = Group.objects.get_or_create(name=POWER_UP_STAFF_GROUP)
        staff = User.objects.create_superuser(
            username="pu-staff", email="pu@example.com", password="pw"
        )
        staff.groups.add(group)
        request = RequestFactory().get("/power-admin/")
        request.user = staff

        with override_settings(ROOT_URLCONF="azureproject.urls_power_up"):
            app_list = power_up_admin_site.get_app_list(request)
        group_names = [app["name"] for app in app_list]
        self.assertIn("🎨 Pixel War", group_names)

        client = Client(HTTP_HOST="powerup.lu")
        client.force_login(staff)
        admin_resp = client.get("/power-admin/")
        self.assertEqual(admin_resp.status_code, 200)
        self.assertIn("Pixel War", admin_resp.content.decode())

    def test_management_commands_run_cleanly(self):
        canvas = PixelCanvas.objects.create(name="Cmd Canvas", width=10, height=10)
        UserPixelCooldown.objects.create(
            canvas=canvas, session_key="anon-session", pixels_placed_last_minute=2
        )
        UserPixelCooldown.objects.filter(canvas=canvas).update(last_minute_reset=None)

        out_check = StringIO()
        call_command("check_canvas_config", stdout=out_check)
        self.assertIn("Cmd Canvas", out_check.getvalue())

        out_reset = StringIO()
        call_command("reset_rate_limits", "--all-users", "--dry-run", stdout=out_reset)
        self.assertIn("DRY RUN completed", out_reset.getvalue())
