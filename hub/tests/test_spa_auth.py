"""
Tests for the SPA session→JWT exchange (azureproject/views_spa_auth.py).

Two surfaces:
  - GET /api/auth/spa-callback/?return=<allowed-url>  on crush.lu  (bounce)
  - POST /api/token/exchange-code/                     on api.crush.lu (exchange)

Both live outside i18n_patterns and the bounce path is exempt from the
Crush.lu consent middleware (/api/ prefix), so no consent boilerplate needed.
"""

from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache

from azureproject.views_spa_auth import CODE_CACHE_PREFIX

pytestmark = pytest.mark.django_db

CRUSH_HOST = "crush.lu"
API_HOST = "api.crush.lu"
ALLOWED_RETURN = "https://hub.crush.lu/auth/callback"


@pytest.fixture
def staff_user(db):
    return get_user_model().objects.create_user(
        username="staff@example.com",
        email="staff@example.com",
        password="staffpass123",
        is_staff=True,
    )


@pytest.fixture
def non_staff_user(db):
    return get_user_model().objects.create_user(
        username="user@example.com",
        email="user@example.com",
        password="userpass123",
    )


@pytest.fixture(autouse=True)
def clear_code_cache():
    cache.clear()
    yield
    cache.clear()


# ---------------------------------------------------------------------------
# /api/auth/spa-callback/  (crush.lu)
# ---------------------------------------------------------------------------


class TestSpaSessionCallback:
    URL = "/api/auth/spa-callback/"

    def test_missing_return_url_rejected(self, client):
        resp = client.get(self.URL, HTTP_HOST=CRUSH_HOST)
        assert resp.status_code == 400

    def test_disallowed_return_url_rejected(self, client):
        resp = client.get(
            f"{self.URL}?return=https://attacker.example.com/auth/callback",
            HTTP_HOST=CRUSH_HOST,
        )
        assert resp.status_code == 400

    def test_prefix_attack_rejected(self, client):
        # `https://hub.crush.lu.attacker.com/auth/callback` would slip past a
        # naive startswith() check. Exact (scheme, netloc, path) match must reject.
        resp = client.get(
            f"{self.URL}?return=https://hub.crush.lu.attacker.com/auth/callback",
            HTTP_HOST=CRUSH_HOST,
        )
        assert resp.status_code == 400

    def test_anonymous_redirected_to_login(self, client):
        resp = client.get(
            f"{self.URL}?return={ALLOWED_RETURN}",
            HTTP_HOST=CRUSH_HOST,
        )
        assert resp.status_code == 302
        parsed = urlparse(resp.url)
        assert parsed.path == "/accounts/login/"

        # next= must decode to the original spa-callback URL with the return
        # query intact — proves the double-encoding survives a parse round-trip.
        next_value = parse_qs(parsed.query)["next"][0]
        inner = urlparse(next_value)
        assert inner.path == self.URL
        assert parse_qs(inner.query)["return"][0] == ALLOWED_RETURN

    def test_non_staff_forbidden(self, client, non_staff_user):
        client.force_login(non_staff_user)
        resp = client.get(
            f"{self.URL}?return={ALLOWED_RETURN}",
            HTTP_HOST=CRUSH_HOST,
        )
        assert resp.status_code == 403

    def test_staff_gets_code_and_redirect(self, client, staff_user):
        client.force_login(staff_user)
        resp = client.get(
            f"{self.URL}?return={ALLOWED_RETURN}",
            HTTP_HOST=CRUSH_HOST,
        )
        assert resp.status_code == 302
        assert resp.url.startswith(f"{ALLOWED_RETURN}?code=")

        code = resp.url.split("?code=", 1)[1]
        assert cache.get(f"{CODE_CACHE_PREFIX}{code}") == staff_user.pk


# ---------------------------------------------------------------------------
# /api/token/exchange-code/  (api.crush.lu)
# ---------------------------------------------------------------------------


class TestExchangeCode:
    URL = "/api/token/exchange-code/"

    def _seed_code(self, user, code="test-code-123"):
        cache.set(f"{CODE_CACHE_PREFIX}{code}", user.pk, timeout=60)
        return code

    def test_missing_code_rejected(self, client):
        resp = client.post(
            self.URL, data={}, content_type="application/json", HTTP_HOST=API_HOST
        )
        assert resp.status_code == 400

    def test_unknown_code_rejected(self, client):
        resp = client.post(
            self.URL,
            data={"code": "nonexistent"},
            content_type="application/json",
            HTTP_HOST=API_HOST,
        )
        assert resp.status_code == 401

    def test_valid_code_returns_jwt_pair(self, client, staff_user):
        code = self._seed_code(staff_user)
        resp = client.post(
            self.URL,
            data={"code": code},
            content_type="application/json",
            HTTP_HOST=API_HOST,
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert "access" in payload
        assert "refresh" in payload
        assert isinstance(payload["access"], str) and payload["access"]
        assert isinstance(payload["refresh"], str) and payload["refresh"]

    def test_code_is_single_use(self, client, staff_user):
        code = self._seed_code(staff_user)
        first = client.post(
            self.URL,
            data={"code": code},
            content_type="application/json",
            HTTP_HOST=API_HOST,
        )
        assert first.status_code == 200

        replay = client.post(
            self.URL,
            data={"code": code},
            content_type="application/json",
            HTTP_HOST=API_HOST,
        )
        assert replay.status_code == 401

    def test_inactive_user_rejected(self, client, staff_user):
        staff_user.is_active = False
        staff_user.save(update_fields=["is_active"])
        code = self._seed_code(staff_user)
        resp = client.post(
            self.URL,
            data={"code": code},
            content_type="application/json",
            HTTP_HOST=API_HOST,
        )
        assert resp.status_code == 401

    def test_user_lost_staff_after_code_issued(self, client, staff_user):
        code = self._seed_code(staff_user)
        staff_user.is_staff = False
        staff_user.save(update_fields=["is_staff"])

        resp = client.post(
            self.URL,
            data={"code": code},
            content_type="application/json",
            HTTP_HOST=API_HOST,
        )
        assert resp.status_code == 403
