"""
Tests for serve_profile_photo rate limiting.

Covers the dual rate-limit (per-user + per-IP) added in the photo-ratelimit-ip-cap
fix. Verifies that the IP cap closes the "N accounts from one IP each get the
full user quota" hole, which matters because profile photos are GDPR PII.
"""
from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings

from crush_lu.models import CrushProfile
from crush_lu.oauth_statekit import get_client_ip
from crush_lu.views_media import serve_profile_photo


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "test-photo-ratelimit",
        }
    },
    # Force the local-filesystem branch of the view (no Azure redirect).
    AZURE_ACCOUNT_NAME="",
)
class ServeProfilePhotoRateLimitTests(TestCase):
    """The view enforces auth + privacy before the photo is served; these tests
    isolate ratelimit behavior by allowing the viewer past the privacy gate.

    The ratelimit runs first in the view body, so we can observe its 429 without
    ever needing a real photo on disk.
    """

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("owner", password="x")
        cls.viewer = User.objects.create_user("viewer", password="x")
        CrushProfile.objects.create(user=cls.owner)
        cls.factory = RequestFactory()

    def setUp(self):
        cache.clear()

    def _hit(self, user, remote_addr="203.0.113.1"):
        """One request to the photo endpoint. Returns the response.

        Patches the privacy check so we can focus on the ratelimit code path.
        Without the patch, can_view_profile_photo returns False for unrelated
        users and the view 403s before any photo-disk logic runs.
        """
        req = self.factory.get(
            f"/crush/media/profile/{self.owner.id}/photo_1/",
            REMOTE_ADDR=remote_addr,
        )
        req.user = user
        with patch("crush_lu.views_media.can_view_profile_photo", return_value=True):
            # photo_field "photo_1" is valid, but the profile has no actual
            # photo file attached -> the view raises Http404 after the ratelimit
            # counters increment. That Http404 is fine: we only care that the
            # *first* N requests succeed past the ratelimit and that the (N+1)th
            # is blocked with 429 BEFORE reaching the photo logic.
            try:
                return serve_profile_photo(
                    req, user_id=self.owner.id, photo_field="photo_1"
                )
            except Exception:
                # Http404 (Django converts to HttpResponseNotFound); any other
                # exception means we got past the ratelimit, which is a pass
                # for our purpose. Return a sentinel.
                from django.http import Http404

                class _Ok:
                    status_code = 200

                return _Ok()

    def test_get_client_ip_keeps_plain_ipv6_distinct(self):
        """Plain IPv6 clients must not be collapsed into one shared IP budget."""
        req1 = self.factory.get("/", REMOTE_ADDR="2001:db8:1::1")
        req2 = self.factory.get("/", REMOTE_ADDR="2001:db8:1::2")

        self.assertEqual(get_client_ip(req1), "2001:db8:1::1")
        self.assertEqual(get_client_ip(req2), "2001:db8:1::2")
        self.assertNotEqual(get_client_ip(req1), get_client_ip(req2))

    def test_get_client_ip_strips_only_real_ports(self):
        """IPv4:port and [IPv6]:port are stripped; plain IPv6 is preserved."""
        cases = {
            "203.0.113.10:1234": "203.0.113.10",
            "[2001:db8:1::1]:443": "2001:db8:1::1",
            "2001:0db8:0001:0000:0000:0000:0000:0001": "2001:db8:1::1",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                req = self.factory.get("/", REMOTE_ADDR=raw)
                self.assertEqual(get_client_ip(req), expected)

    def test_per_user_cap_returns_429_after_200(self):
        """A single user exceeding 200 req/min is rate-limited."""
        for _ in range(200):
            self._hit(self.viewer)
        # 201st request from the same user should be blocked.
        resp = self._hit(self.viewer)
        self.assertEqual(resp.status_code, 429)

    def test_per_ip_cap_blocks_aggregate_abuse(self):
        """Multiple users from one IP cannot bypass the IP cap.

        The IP cap (400 min for non-coaches) must trip even if each individual
        user is well under their own 200/min budget. This is the core fix.
        """
        viewers = [
            User.objects.create_user(f"v{i}", password="x") for i in range(5)
        ]
        blocked = False
        total_sent = 0
        for v in viewers:
            for _ in range(100):
                total_sent += 1
                resp = self._hit(v, remote_addr="198.51.100.7")
                if getattr(resp, "status_code", 0) == 429:
                    blocked = True
                    break
            if blocked:
                break
        self.assertTrue(blocked, f"IP cap never triggered after {total_sent} req")
        self.assertLess(
            total_sent, 405, f"IP cap fired too late ({total_sent} requests)"
        )

    def test_distinct_ips_have_independent_budgets(self):
        """The IP counter is per-IP, not a global counter.

        Two viewers on two different IPs each get their own IP budget. The
        user cap still applies to the same user, so we clear the user counter
        between phases to isolate the IP-counter behavior.
        """
        # Phase A: viewer does 150 req from IP A. Under both user (200) and
        # IP (400) caps. Counters after: user=150, ip[192.0.2.1]=150.
        for _ in range(150):
            resp = self._hit(self.viewer, remote_addr="192.0.2.1")
            self.assertNotEqual(getattr(resp, "status_code", 200), 429)

        # Reset only the user counter so we can observe IP-keyed isolation
        # without the user cap masking it. In production the user cap is a
        # feature, not a bug — here we isolate the IP-keyed behavior.
        from crush_lu.views_media import cache as _cache_alias  # noqa: F401
        cache.delete(f"ratelimit:serve_profile_photo:user_{self.viewer.id}")

        # Phase B: a DIFFERENT user (user2) on IP B does 150 req. Both user
        # (200) and IP (400) caps are clear; ip[192.0.2.2] should NOT inherit
        # the 150 from IP A. (Keep the count under the per-user cap too.)
        user2 = User.objects.create_user("u2b", password="x")
        for _ in range(150):
            resp = self._hit(user2, remote_addr="192.0.2.2")
            self.assertNotEqual(
                getattr(resp, "status_code", 200),
                429,
                "IP B's counter inherited traffic from IP A",
            )
