"""
Tests for the shared ``crush_lu.decorators.ratelimit`` decorator.

Two bypasses are pinned here:

* Azure App Service sends ``X-Forwarded-For`` as ``IP:PORT`` and the port
  changes with every TCP connection, so keying on the raw header gave each new
  connection a fresh counter. The key must be the client address alone.
* The counter was read, compared, then written, so a concurrent burst could all
  read the same value and pass together. The count must come from ``incr()``.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.storage.cookie import CookieStorage
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from crush_lu.decorators import _get_cache_key, ratelimit


@ratelimit(key="ip", rate="2/m", method="POST")
def ip_view(request):
    return HttpResponse("ok")


@ratelimit(key="user", rate="2/m", method="POST")
def user_view(request):
    return HttpResponse("ok")


@ratelimit(key="ip", rate="2/m", method="POST", block=False)
def soft_view(request):
    return HttpResponse("limited" if getattr(request, "limited", False) else "ok")


@ratelimit(key="ip", rate="3/m", method="POST")
def burst_view(request):
    return HttpResponse("ok")


class _InterleavingCache:
    """Wraps the real cache. After the outer request's first cache call
    returns, runs ``interleave()`` - other requests, to completion - before
    handing that result back. This is the interleaving that let a get-then-set
    burst all read the same count and pass together."""

    def __init__(self, real, interleave):
        self._real = real
        self._interleave = interleave
        self._fired = False

    def __getattr__(self, name):
        attr = getattr(self._real, name)
        if self._fired or not callable(attr):
            return attr

        def first_call(*args, **kwargs):
            result = attr(*args, **kwargs)
            if not self._fired:
                self._fired = True
                self._interleave()
            return result

        return first_call


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "test-ratelimit-decorator",
        }
    }
)
class RateLimitDecoratorTests(SimpleTestCase):
    def setUp(self):
        # Tests share one ratelimit counter per key; start every test from zero.
        cache.clear()
        self.factory = RequestFactory()

    def _post(self, view=ip_view, xhr=True, **meta):
        request = self.factory.post("/", **meta)
        request.user = AnonymousUser()
        if xhr:
            request.META["HTTP_X_REQUESTED_WITH"] = "XMLHttpRequest"
        else:
            request._messages = CookieStorage(request)
        return view(request)

    # --- key: the client address, never the connection's port ---------------

    def test_port_changes_share_one_counter(self):
        statuses = [
            self._post(HTTP_X_FORWARDED_FOR=f"203.0.113.7:{port}").status_code
            for port in (50123, 50124, 50125)
        ]
        self.assertEqual(statuses, [200, 200, 429])

    def test_limit_holds_exactly(self):
        statuses = [
            self._post(HTTP_X_FORWARDED_FOR="203.0.113.7:50123").status_code
            for _ in range(4)
        ]
        self.assertEqual(statuses, [200, 200, 429, 429])

    def test_ipv6_spellings_and_ports_share_one_counter(self):
        statuses = [
            self._post(HTTP_X_FORWARDED_FOR=xff).status_code
            for xff in ("[2001:db8::1]:443", "[2001:db8::1]:444", "2001:0db8::1")
        ]
        self.assertEqual(statuses, [200, 200, 429])

    def test_distinct_ipv6_addresses_keep_separate_counters(self):
        for _ in range(2):
            self._post(HTTP_X_FORWARDED_FOR="[2001:db8::1]:443")
        response = self._post(HTTP_X_FORWARDED_FOR="[2001:db8::2]:443")
        self.assertEqual(response.status_code, 200)

    def test_distinct_ipv4_addresses_keep_separate_counters(self):
        for _ in range(2):
            self._post(HTTP_X_FORWARDED_FOR="203.0.113.7:50123")
        response = self._post(HTTP_X_FORWARDED_FOR="203.0.113.8:50123")
        self.assertEqual(response.status_code, 200)

    def test_anonymous_user_key_falls_back_to_address_without_port(self):
        statuses = [
            self._post(
                view=user_view, HTTP_X_FORWARDED_FOR=f"203.0.113.7:{port}"
            ).status_code
            for port in (50123, 50124, 50125)
        ]
        self.assertEqual(statuses, [200, 200, 429])

    def test_cache_key_uses_first_forwarded_address_without_port(self):
        cases = {
            "203.0.113.7:50123": "ratelimit:v:203_0_113_7",
            "203.0.113.7:50123, 10.0.0.1:443": "ratelimit:v:203_0_113_7",
            "[2001:db8::1]:443": "ratelimit:v:2001_db8__1",
        }
        for xff, expected in cases.items():
            with self.subTest(xff=xff):
                request = self.factory.post("/", HTTP_X_FORWARDED_FOR=xff)
                self.assertEqual(_get_cache_key(request, "ip", "v"), expected)

    def test_cache_key_without_any_address_is_unknown(self):
        request = self.factory.post("/", REMOTE_ADDR="")
        request.user = AnonymousUser()
        self.assertEqual(_get_cache_key(request, "ip", "v"), "ratelimit:v:unknown")
        self.assertEqual(_get_cache_key(request, "user", "v"), "ratelimit:v:unknown")

    # --- counting: atomic, decided from the incremented value ---------------

    def test_concurrent_burst_cannot_share_one_count(self):
        nested = []

        def interleave():
            for _ in range(3):
                nested.append(
                    self._post(
                        view=burst_view, HTTP_X_FORWARDED_FOR="203.0.113.7:1"
                    ).status_code
                )

        with patch("crush_lu.decorators.cache", _InterleavingCache(cache, interleave)):
            outer = self._post(
                view=burst_view, HTTP_X_FORWARDED_FOR="203.0.113.7:2"
            ).status_code

        # Four requests against a limit of three: exactly one is refused.
        self.assertEqual(nested, [200, 200, 200])
        self.assertEqual(outer, 429)

    def test_counter_evicted_between_add_and_incr_restarts_at_one(self):
        real_incr = cache.incr
        evicted = []

        def evicting_incr(key, *args, **kwargs):
            if not evicted:
                evicted.append(key)
                cache.delete(key)
                raise ValueError(f"Key '{key}' not found")
            return real_incr(key, *args, **kwargs)

        with patch.object(cache, "incr", side_effect=evicting_incr):
            statuses = [
                self._post(HTTP_X_FORWARDED_FOR="203.0.113.7:1").status_code
                for _ in range(3)
            ]

        self.assertEqual(statuses, [200, 200, 429])
        self.assertEqual(cache.get(evicted[0]), 3)

    def test_counter_recreated_by_another_request_after_eviction_keeps_counting(
        self,
    ):
        real_incr = cache.incr
        evicted = []

        def evict_then_recreate(key, *args, **kwargs):
            if not evicted:
                evicted.append(key)
                # Evicted, then a concurrent request started a fresh window.
                cache.set(key, 1, 60)
                raise ValueError(f"Key '{key}' not found")
            return real_incr(key, *args, **kwargs)

        with patch.object(cache, "incr", side_effect=evict_then_recreate):
            first = self._post(HTTP_X_FORWARDED_FOR="203.0.113.7:1")
            second = self._post(HTTP_X_FORWARDED_FOR="203.0.113.7:2")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(cache.get(evicted[0]), 3)

    # --- fail open when the cache is unavailable ----------------------------

    def test_cache_errors_fail_open(self):
        broken = MagicMock()
        broken.add.side_effect = ConnectionError("redis down")
        broken.incr.side_effect = ConnectionError("redis down")
        broken.get.side_effect = ConnectionError("redis down")
        broken.set.side_effect = ConnectionError("redis down")
        with patch("crush_lu.decorators.cache", broken):
            statuses = [
                self._post(HTTP_X_FORWARDED_FOR="203.0.113.7:1").status_code
                for _ in range(3)
            ]
        self.assertEqual(statuses, [200, 200, 200])

    def test_swallowed_cache_errors_fail_open(self):
        # django_redis with IGNORE_EXCEPTIONS (production) returns None from
        # add()/incr() during a Redis outage instead of raising.
        swallowed = MagicMock()
        swallowed.add.return_value = None
        swallowed.incr.return_value = None
        swallowed.get.return_value = None
        swallowed.set.return_value = None
        with patch("crush_lu.decorators.cache", swallowed):
            statuses = [
                self._post(HTTP_X_FORWARDED_FOR="203.0.113.7:1").status_code
                for _ in range(3)
            ]
        self.assertEqual(statuses, [200, 200, 200])

    # --- responses ----------------------------------------------------------

    def test_ajax_request_over_limit_gets_json_429(self):
        for _ in range(2):
            self._post(HTTP_X_FORWARDED_FOR="203.0.113.7:1")
        response = self._post(HTTP_X_FORWARDED_FOR="203.0.113.7:1")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(json.loads(response.content)["error_code"], "rate_limited")

    def test_browser_request_over_limit_gets_html_429(self):
        for _ in range(2):
            self._post(xhr=False, HTTP_X_FORWARDED_FOR="203.0.113.7:1")
        response = self._post(xhr=False, HTTP_X_FORWARDED_FOR="203.0.113.7:1")
        self.assertEqual(response.status_code, 429)
        self.assertNotIn("application/json", response["Content-Type"])

    def test_non_blocking_limit_flags_the_request_and_runs_the_view(self):
        bodies = [
            self._post(view=soft_view, HTTP_X_FORWARDED_FOR="203.0.113.7:1").content
            for _ in range(3)
        ]
        self.assertEqual(bodies, [b"ok", b"ok", b"limited"])

    def test_other_methods_are_not_counted(self):
        request = self.factory.get("/", HTTP_X_FORWARDED_FOR="203.0.113.7:1")
        request.user = AnonymousUser()
        for _ in range(3):
            self.assertEqual(ip_view(request).status_code, 200)
        self.assertEqual(
            self._post(HTTP_X_FORWARDED_FOR="203.0.113.7:1").status_code, 200
        )
