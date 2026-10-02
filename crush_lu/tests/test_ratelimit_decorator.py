"""
Tests for the shared ``crush_lu.decorators.ratelimit`` decorator.

Two bypasses are pinned here:

* Azure App Service sends ``X-Forwarded-For`` as ``IP:PORT`` and the port
  changes with every TCP connection, so keying on the raw header gave each new
  connection a fresh counter. The key must be the client address alone.
* The counter was read, compared, then written, so a concurrent burst could all
  read the same value and pass together. The count must come from ``incr()``.
  That holds for ``count_if`` too: a success-only cap reserves its slot
  atomically before the view runs and releases it if the response doesn't
  qualify, instead of peeking before the view and counting after it.
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


# Every run of a success-only view is recorded, the way gift_create creates
# (and emails) a gift, so tests can count what got past the limiter.
executed = []


def _created(response):
    return response.status_code == 302


@ratelimit(key="ip", rate="2/m", method="POST", count_if=_created)
def success_only_view(request):
    outcome = request.POST.get("outcome", "create")
    executed.append(outcome)
    if outcome == "raise":
        raise RuntimeError("view failed")
    return HttpResponse(status=302 if outcome == "create" else 400)


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
        executed.clear()
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

    def test_concurrent_success_only_burst_cannot_exceed_the_limit(self):
        # count_if used to peek (get) before the view and count after it, so
        # every request in flight read the same pre-limit value and ran.
        nested = []

        def interleave():
            for _ in range(3):
                nested.append(
                    self._post(
                        view=success_only_view,
                        HTTP_X_FORWARDED_FOR="203.0.113.7:1",
                    ).status_code
                )

        with patch("crush_lu.decorators.cache", _InterleavingCache(cache, interleave)):
            outer = self._post(
                view=success_only_view, HTTP_X_FORWARDED_FOR="203.0.113.7:2"
            ).status_code

        # Four concurrent successful creates against a limit of two: exactly
        # two views run, and the rest are refused.
        self.assertEqual(nested, [302, 302, 429])
        self.assertEqual(outer, 429)
        self.assertEqual(executed, ["create", "create"])

    def _success_only_key(self, xff):
        request = self.factory.post("/", HTTP_X_FORWARDED_FOR=xff)
        return _get_cache_key(request, "ip", "success_only_view")

    def test_success_only_rejected_response_releases_its_slot(self):
        xff = "203.0.113.7:1"
        statuses = [
            self._post(
                view=success_only_view,
                data={"outcome": outcome},
                HTTP_X_FORWARDED_FOR=xff,
            ).status_code
            for outcome in ("invalid", "create", "invalid", "invalid", "create")
        ]
        count_at_cap = cache.get(self._success_only_key(xff))

        # Capped: every POST is refused without running the view, and a
        # refusal doesn't give back a slot a success holds.
        capped = [
            self._post(
                view=success_only_view,
                data={"outcome": outcome},
                HTTP_X_FORWARDED_FOR=xff,
            ).status_code
            for outcome in ("invalid", "create", "create")
        ]

        self.assertEqual(statuses, [400, 302, 400, 400, 302])
        self.assertEqual(count_at_cap, 2)
        self.assertEqual(capped, [429, 429, 429])
        self.assertEqual(
            executed, ["invalid", "create", "invalid", "invalid", "create"]
        )
        self.assertEqual(cache.get(self._success_only_key(xff)), 2)

    def test_success_only_view_that_raises_releases_its_slot(self):
        xff = "203.0.113.7:1"
        with self.assertRaises(RuntimeError):
            self._post(
                view=success_only_view,
                data={"outcome": "raise"},
                HTTP_X_FORWARDED_FOR=xff,
            )
        statuses = [
            self._post(view=success_only_view, HTTP_X_FORWARDED_FOR=xff).status_code
            for _ in range(3)
        ]
        self.assertEqual(statuses, [302, 302, 429])

    def test_success_only_release_keeps_the_window_expiry(self):
        xff = "203.0.113.7:1"
        self._post(view=success_only_view, HTTP_X_FORWARDED_FOR=xff)
        key = self._success_only_key(xff)
        internal_key = cache.make_key(key)
        expiry = cache._expire_info[internal_key]

        self._post(
            view=success_only_view,
            data={"outcome": "invalid"},
            HTTP_X_FORWARDED_FOR=xff,
        )

        self.assertEqual(cache.get(key), 1)
        self.assertEqual(cache._expire_info[internal_key], expiry)

    def test_success_only_release_after_expiry_creates_no_counter(self):
        xff = "203.0.113.7:1"
        key = _get_cache_key(
            self.factory.post("/", HTTP_X_FORWARDED_FOR=xff), "ip", "expiring_view"
        )

        @ratelimit(key="ip", rate="2/m", method="POST", count_if=_created)
        def expiring_view(request):
            cache.delete(key)  # the window expires while the view runs
            return HttpResponse(status=400)

        response = self._post(view=expiring_view, HTTP_X_FORWARDED_FOR=xff)

        self.assertEqual(response.status_code, 400)
        self.assertIsNone(cache.get(key))

    def test_release_after_window_rollover_leaves_the_new_window_counted(self):
        # #1111: a reservation straddling the window expiry used to release
        # into the NEXT window, giving that window one free request.
        xff = "203.0.113.7:1"
        clock = [1_000_000.0]
        key = _get_cache_key(
            self.factory.post("/", HTTP_X_FORWARDED_FOR=xff), "ip", "straddle_view"
        )

        @ratelimit(key="ip", rate="2/m", method="POST", count_if=_created)
        def straddle_view(request):
            if request.POST.get("outcome") == "straddle":
                clock[
                    0
                ] += 901  # window A (rate "2/m" parses as 15 minutes) expires while the view runs
                # A second request starts window B and is counted there.
                self._post(
                    view=straddle_view,
                    data={"outcome": "create"},
                    HTTP_X_FORWARDED_FOR=xff,
                )
                return HttpResponse(status=400)  # A's slot is released now
            return HttpResponse(status=302)

        with patch("time.time", side_effect=lambda: clock[0]):
            self._post(
                view=straddle_view,
                data={"outcome": "straddle"},
                HTTP_X_FORWARDED_FOR=xff,
            )
            self.assertEqual(cache.get(key), 1)

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

    def test_rollover_between_increment_and_token_read_never_releases(self):
        # The window can roll over after our increment but before we learn its
        # token. The owner is then unknown, so the slot must not be released
        # into the new window (it would grant that window a free request).
        from crush_lu.decorators import (
            _count_request,
            _record_window_deadline,
            _release_request,
        )

        key = "ratelimit:rollover:test"
        cache.delete(key)
        cache.add(key, 0, 900)
        _record_window_deadline(key, 900)
        real_incr = cache.incr

        def incr_then_roll_over(name, *args, **kwargs):
            value = real_incr(name, *args, **kwargs)
            cache.delete(key)  # window A expires...
            cache.add(key, 1, 900)  # ...and a concurrent request opens window B
            _record_window_deadline(key, 900)
            return value

        with patch.object(cache, "incr", side_effect=incr_then_roll_over):
            count, generation = _count_request(key, 900)

        self.assertEqual(count, 1)
        self.assertIsNone(generation)
        _release_request(key, generation)
        self.assertEqual(cache.get(key), 1)

    def test_rollover_between_generation_check_and_decrement_is_undone(self):
        # The generation check passes, then the window rolls over before the
        # decrement lands: the decrement hit the new window, so it is given back.
        from crush_lu.decorators import _record_window_deadline, _release_request

        key = "ratelimit:rollover:decr"
        cache.delete(key)
        cache.add(key, 0, 900)
        generation = _record_window_deadline(key, 900)
        cache.incr(key)  # our reservation in window A
        real_decr = cache.decr

        def roll_over_then_decr(name, *args, **kwargs):
            cache.delete(key)  # window A expires...
            cache.add(key, 1, 900)  # ...window B opens with one request
            _record_window_deadline(key, 900)
            return real_decr(name, *args, **kwargs)

        with patch.object(cache, "decr", side_effect=roll_over_then_decr):
            _release_request(key, generation)

        self.assertEqual(cache.get(key), 1)

    def test_release_after_counter_expiry_never_takes_from_the_new_window(self):
        # #1150: the token was written after the counter with the same timeout,
        # so it outlived the counter. A release that passed the token check as
        # the counter expired could then decrement a counter a new window had
        # re-created (before that window recorded its own token), and the
        # still-old token hid it: the new window got a free request.
        from crush_lu.decorators import _record_window_deadline, _release_request

        key = "ratelimit:rollover:expiry"
        internal_key = cache.make_key(key)
        clock = [1_000_000.0]
        ticking = [True]

        def now():
            if ticking[0]:
                clock[0] += 0.001  # real time moves on between cache calls
            return clock[0]

        real_decr = cache.decr

        def expire_then_decr(name, *args, **kwargs):
            # Freeze time at the instant the counter expires.
            ticking[0] = False
            clock[0] = cache._expire_info[internal_key]
            cache.add(key, 0, 900)  # request B opens window B...
            cache.incr(key)  # ...request C is counted in it
            return real_decr(name, *args, **kwargs)

        with patch("time.time", side_effect=now):
            cache.add(key, 0, 900)
            generation = _record_window_deadline(key, 900)
            cache.incr(key)  # our reservation in window A
            with patch.object(cache, "decr", side_effect=expire_then_decr):
                _release_request(key, generation)
            count = cache.get(key)

        self.assertEqual(count, 1)
