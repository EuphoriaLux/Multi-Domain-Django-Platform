"""
Tests for the shared ``crush_lu.decorators.ratelimit`` decorator.

Three bypasses are pinned here:

* Azure App Service sends ``X-Forwarded-For`` as ``IP:PORT`` and the port
  changes with every TCP connection, so keying on the raw header gave each new
  connection a fresh counter. The key must be the client address alone.
* The counter was read, compared, then written, so a concurrent burst could all
  read the same value and pass together. The count must come from ``incr()``.
  That holds for ``count_if`` too: a success-only cap reserves its slot
  atomically before the view runs and releases it if the response doesn't
  qualify, instead of peeking before the view and counting after it.
* A late release used the shared counter key after rollover, briefly reducing
  the next window before compensation. Immutable physical reservations keep
  that release from admitting an extra successful view in the next window.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import MagicMock, patch

import pytest

from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.storage.cookie import CookieStorage
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from crush_lu.decorators import (
    _counter_key,
    _counter_selector_key,
    _count_request,
    _generation_key,
    _get_cache_key,
    _parse_period,
    _release_request,
    _remaining_window_seconds,
    ratelimit,
)


@pytest.mark.parametrize(
    ("period", "seconds"),
    [("s", 1), ("m", 60), ("h", 3600), ("d", 86400)],
)
def test_bare_period_unit_means_one_unit(period, seconds):
    assert _parse_period(period) == seconds


@pytest.mark.parametrize(
    ("period", "seconds"),
    [
        ("15m", 900),
        ("1h", 3600),
        ("1d", 86400),
        ("3s", 3),
        ("2", 120),
        ("2x", 120),
        ("", 900),
        ("garbage", 900),
        ("x", 900),
    ],
)
def test_numbered_periods_and_legacy_fallbacks(period, seconds):
    assert _parse_period(period) == seconds


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

    def _assert_window_lifetime(self, rate, seconds):
        @ratelimit(key="ip", rate=rate, method="POST")
        def timed_view(request):
            return HttpResponse("ok")

        clock = [1_000_000.0]
        with patch("time.time", side_effect=lambda: clock[0]):
            self.assertEqual(self._post(view=timed_view).status_code, 200)
            self.assertEqual(self._post(view=timed_view).status_code, 200)
            self.assertEqual(self._post(view=timed_view).status_code, 429)
            clock[0] += seconds - 1
            self.assertEqual(self._post(view=timed_view).status_code, 429)
            clock[0] += 1
            self.assertEqual(self._post(view=timed_view).status_code, 200)

    def test_minute_shorthand_window_expires_after_one_minute(self):
        self._assert_window_lifetime("2/m", 60)

    def test_hour_shorthand_window_expires_after_one_hour(self):
        self._assert_window_lifetime("2/h", 3600)

    def test_day_shorthand_window_expires_after_one_day(self):
        self._assert_window_lifetime("2/d", 86400)

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

    def _physical_key(self, key):
        window = cache.get(_generation_key(key))
        nonce = cache.get(_counter_selector_key(key, window))
        while True:
            physical = _counter_key(key, window, nonce)
            successor = cache.get(f"{physical}:next")
            if successor is None:
                return physical
            nonce = successor

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
        count_at_cap = cache.get(self._physical_key(self._success_only_key(xff)))

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
        self.assertEqual(cache.get(self._physical_key(self._success_only_key(xff))), 2)

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
        key = self._physical_key(self._success_only_key(xff))
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

        physical_keys = []

        @ratelimit(key="ip", rate="2/m", method="POST", count_if=_created)
        def expiring_view(request):
            physical_keys.append(self._physical_key(key))
            cache.delete(physical_keys[-1])
            return HttpResponse(status=400)

        response = self._post(view=expiring_view, HTTP_X_FORWARDED_FOR=xff)

        self.assertEqual(response.status_code, 400)
        self.assertIsNone(cache.get(physical_keys[0]))

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
                clock[0] += 61  # window A expires while the view runs
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
            self.assertEqual(cache.get(self._physical_key(key)), 1)

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
        self.assertIsNone(cache.get(evicted[0]))
        logical = _get_cache_key(
            self.factory.post("/", HTTP_X_FORWARDED_FOR="203.0.113.7:1"),
            "ip",
            "ip_view",
        )
        self.assertEqual(cache.get(self._physical_key(logical)), 3)

    def test_counter_recreated_by_another_request_after_eviction_keeps_counting(
        self,
    ):
        real_incr = cache.incr
        evicted = []

        def evict_then_recreate(key, *args, **kwargs):
            if not evicted:
                evicted.append(key)
                # A concurrent request elects/counts a fresh physical counter.
                cache.delete(key)
                self.assertEqual(
                    self._post(HTTP_X_FORWARDED_FOR="203.0.113.7:1").status_code, 200
                )
                raise ValueError(f"Key '{key}' not found")
            return real_incr(key, *args, **kwargs)

        with patch.object(cache, "incr", side_effect=evict_then_recreate):
            first = self._post(HTTP_X_FORWARDED_FOR="203.0.113.7:1")
            second = self._post(HTTP_X_FORWARDED_FOR="203.0.113.7:2")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertIsNone(cache.get(evicted[0]))
        logical = _get_cache_key(
            self.factory.post("/", HTTP_X_FORWARDED_FOR="203.0.113.7:1"),
            "ip",
            "ip_view",
        )
        self.assertEqual(cache.get(self._physical_key(logical)), 3)

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

    def test_rollover_after_increment_retries_against_the_new_window(self):
        key = "ratelimit:rollover:test"
        clock = [1_000_000.0]
        real_incr = cache.incr
        fired = []
        nested = []

        def incr_then_roll_over(name, *args, **kwargs):
            value = real_incr(name, *args, **kwargs)
            if not fired:
                fired.append(name)
                clock[0] += 901
                nested.append(_count_request(key, 900))
            return value

        with patch("time.time", side_effect=lambda: clock[0]):
            _count_request(key, 900)
            with patch.object(cache, "incr", side_effect=incr_then_roll_over):
                count, reservation = _count_request(key, 900)
            self.assertEqual(count, 2)
            self.assertEqual(reservation, nested[0][1])
            _release_request(key, reservation)
            self.assertEqual(cache.get(reservation[1]), 1)
            self.assertIsNone(cache.get(fired[0]))

    def test_physical_counter_eviction_fences_old_release_from_successor(self):
        key = "ratelimit:rollover:decr"
        _, old = _count_request(key, 900)
        root = cache.get(_generation_key(key))
        cache.delete(old[1])  # Root/selector are still present.
        count, fresh = _count_request(key, 900)
        self.assertEqual(count, 1)
        self.assertNotEqual(old[1], fresh[1])
        self.assertEqual(cache.get(_generation_key(key)), root)
        with patch.object(cache, "decr", wraps=cache.decr) as decr:
            _release_request(key, old)
        decr.assert_called_once_with(old[1])
        self.assertEqual(cache.get(fresh[1]), 1)
        self.assertIsNone(cache.get(old[1]))

    def test_release_after_counter_expiry_never_touches_the_new_window(self):
        key = "ratelimit:rollover:expiry"
        clock = [1_000_000.0]
        with patch("time.time", side_effect=lambda: clock[0]):
            _, old = _count_request(key, 900)
            clock[0] = cache._expire_info[cache.make_key(old[1])]
            count, fresh = _count_request(key, 900)
            self.assertEqual(count, 1)
            _release_request(key, old)
            self.assertEqual(cache.get(fresh[1]), 1)
            self.assertIsNone(cache.get(old[1]))

    def test_stall_before_counter_publication_cannot_release_new_window(self):
        key = "ratelimit:rollover:stall"
        clock = [1_000_000.0]
        real_add = cache.add
        stalled = []

        def stalled_add(name, *args, **kwargs):
            if name.endswith(":counter") and not stalled:
                stalled.append(name)
                clock[0] += 5
            return real_add(name, *args, **kwargs)

        with patch("time.time", side_effect=lambda: clock[0]):
            with patch.object(cache, "add", side_effect=stalled_add):
                count, old = _count_request(key, 900)
            self.assertEqual(count, 1)
            clock[0] = old[0]
            _, fresh = _count_request(key, 900)
            _release_request(key, old)
            self.assertEqual(cache.get(fresh[1]), 1)
            self.assertIsNone(cache.get(old[1]))

    def test_late_release_never_admits_an_extra_success_in_a_full_new_window(self):
        from crush_lu.decorators import _count_request, _release_request

        xff = "203.0.113.7:1"
        key = self._success_only_key(xff)
        clock = [1_000_000.0]
        real_decr = cache.decr
        interleaved = []
        fired = []

        def delayed_decr(name, *args, **kwargs):
            if fired:
                return real_decr(name, *args, **kwargs)
            fired.append(name)
            clock[0] += 61  # A passed its checks, then stalled past expiry.
            for _ in range(2):
                interleaved.append(
                    self._post(
                        view=success_only_view, HTTP_X_FORWARDED_FOR=xff
                    ).status_code
                )
            error = None
            try:
                value = real_decr(name, *args, **kwargs)
            except ValueError as exc:
                value, error = None, exc
            # C runs after the decrement but before A can compensate. Counting
            # the final cache value alone misses an already-created third gift.
            interleaved.append(
                self._post(view=success_only_view, HTTP_X_FORWARDED_FOR=xff).status_code
            )
            if error:
                raise error
            return value

        with patch("time.time", side_effect=lambda: clock[0]):
            _, generation = _count_request(key, 60)  # A's rejected reservation.
            with patch.object(cache, "decr", side_effect=delayed_decr):
                _release_request(key, generation)

        self.assertEqual(interleaved, [302, 302, 429])
        self.assertEqual(executed, ["create", "create"])

    def _assert_evicted_selector_cannot_admit_an_old_branch(self, intermediate):
        xff = "203.0.113.7:1"
        key = self._success_only_key(xff)
        _, reservation = _count_request(key, 60)
        window = cache.get(_generation_key(key))
        selector = _counter_selector_key(key, window)
        if intermediate:
            cache.delete(reservation[1])
            selector = f"{reservation[1]}:next"
            _, reservation = _count_request(key, 60)
        old_key = reservation[1]
        real_incr = cache.incr
        fired = []
        nested = []

        def replace_selector_before_increment(name, *args, **kwargs):
            if name == old_key and not fired:
                fired.append(name)
                cache.delete(selector)  # Physical old counter + root survive.
                for _ in range(2):
                    nested.append(
                        self._post(
                            view=success_only_view, HTTP_X_FORWARDED_FOR=xff
                        ).status_code
                    )
            return real_incr(name, *args, **kwargs)

        with patch.object(cache, "incr", side_effect=replace_selector_before_increment):
            outer = self._post(view=success_only_view, HTTP_X_FORWARDED_FOR=xff)
        self.assertEqual(nested, [302, 302])
        self.assertEqual(outer.status_code, 429)
        self.assertEqual(executed, ["create", "create"])
        self.assertEqual(cache.get(_generation_key(key)), window)
        self.assertEqual(cache.get(self._physical_key(key)), 2)
        self.assertEqual(cache.get(old_key), 1)  # A refunded only its old slot.

    def test_root_counter_selector_eviction_cannot_admit_an_old_branch(self):
        self._assert_evicted_selector_cannot_admit_an_old_branch(False)

    def test_intermediate_selector_eviction_cannot_admit_an_old_branch(self):
        self._assert_evicted_selector_cannot_admit_an_old_branch(True)

    def test_simultaneous_workers_share_one_window_and_counter(self):
        barrier = Barrier(8, timeout=5)

        def worker(index):
            barrier.wait()
            return self._post(
                view=success_only_view, HTTP_X_FORWARDED_FOR=f"203.0.113.7:{index}"
            ).status_code

        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(worker, range(8)))
        self.assertEqual(sorted(statuses), [302, 302, 429, 429, 429, 429, 429, 429])
        self.assertEqual(executed, ["create", "create"])

    def test_competing_counter_publications_join_the_atomic_winner(self):
        real_add = cache.add
        nested = []
        fired = []

        def competing_add(name, *args, **kwargs):
            if name.endswith(":counter") and not fired:
                fired.append(name)
                for _ in range(2):
                    nested.append(self._post(view=success_only_view).status_code)
            return real_add(name, *args, **kwargs)

        with patch.object(cache, "add", side_effect=competing_add):
            outer = self._post(view=success_only_view)
        self.assertEqual(nested, [302, 302])
        self.assertEqual(outer.status_code, 429)
        self.assertEqual(executed, ["create", "create"])

    def test_stale_window_creator_cannot_replace_a_newer_published_window(self):
        clock = [1_000_000.0]
        real_add = cache.add
        fired = []
        nested = []
        key = self._success_only_key("203.0.113.7:1")

        def delayed_add(name, value, *args, **kwargs):
            if name == _generation_key(key) and not fired:
                fired.append(value)
                clock[0] += 61
                for _ in range(2):
                    nested.append(
                        self._post(
                            view=success_only_view, HTTP_X_FORWARDED_FOR="203.0.113.7:1"
                        ).status_code
                    )
            return real_add(name, value, *args, **kwargs)

        with patch("time.time", side_effect=lambda: clock[0]):
            with patch.object(cache, "add", side_effect=delayed_add):
                outer = self._post(
                    view=success_only_view, HTTP_X_FORWARDED_FOR="203.0.113.7:1"
                )
            self.assertNotEqual(cache.get(_generation_key(key)), fired[0])
            self.assertEqual(cache.get(self._physical_key(key)), 2)
        self.assertEqual(nested, [302, 302])
        self.assertEqual(outer.status_code, 429)
        self.assertEqual(executed, ["create", "create"])

    def test_marker_outliving_its_deadline_refuses_instead_of_failing_open(self):
        clock = [1_000_000.0]
        real_add = cache.add
        fired = []

        def delayed_add(name, *args, **kwargs):
            if name.endswith(":window:v2") and not fired:
                fired.append(name)
                clock[0] += 61
            return real_add(name, *args, **kwargs)

        with patch("time.time", side_effect=lambda: clock[0]):
            with patch.object(cache, "add", side_effect=delayed_add):
                response = self._post(view=success_only_view)
            self.assertIsNotNone(cache.get(fired[0]))
            self.assertLess(cache.get(fired[0])[0], clock[0])
            self.assertEqual(self._post(view=success_only_view).status_code, 429)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(executed, [])

    def test_candidate_expiring_before_publication_retries_a_metered_new_window(self):
        clock = [1_000_000.0]
        real_add = cache.add
        counters = []
        fired = []

        def stalled_add(name, *args, **kwargs):
            if ":counter:" in name and not name.endswith(":next"):
                counters.append(name)
            if name.endswith(":counter") and not fired:
                fired.append(name)
                clock[0] += 61
            return real_add(name, *args, **kwargs)

        with patch("time.time", side_effect=lambda: clock[0]):
            with patch.object(cache, "add", side_effect=stalled_add):
                first = self._post()
            self.assertIsNone(cache.get(counters[0]))
            self.assertEqual(
                [first.status_code, self._post().status_code, self._post().status_code],
                [200, 200, 429],
            )

    def test_counter_transition_retry_exhaustion_refuses_the_view(self):
        def losing_incr(name, *args, **kwargs):
            cache.delete(name)
            raise ValueError("counter evicted")

        with patch.object(cache, "incr", side_effect=losing_incr):
            response = self._post(view=success_only_view)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(executed, [])

    def test_failed_increment_keeps_cache_outage_fail_open(self):
        with patch.object(cache, "incr", return_value=None):
            response = self._post(view=success_only_view)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(executed, ["create"])

    def test_successor_expiry_and_retry_after_stay_at_the_original_anchor(self):
        key = "ratelimit:successor:ttl"
        clock = [1_000_000.0]
        with patch("time.time", side_effect=lambda: clock[0]):
            _, old = _count_request(key, 60)
            root_expiry = cache._expire_info[cache.make_key(_generation_key(key))]
            physical_expiry = cache._expire_info[cache.make_key(old[1])]
            clock[0] += 20
            cache.delete(old[1])
            _, fresh = _count_request(key, 60)
            self.assertEqual(
                cache._expire_info[cache.make_key(fresh[1])], physical_expiry
            )
            self.assertEqual(
                cache._expire_info[cache.make_key(f"{old[1]}:next")], root_expiry
            )
            self.assertEqual(_remaining_window_seconds(key, 60, fresh), 40)
            clock[0] += 10
            _count_request(key, 60)
            _release_request(key, fresh)
            self.assertEqual(
                cache._expire_info[cache.make_key(fresh[1])], physical_expiry
            )
            self.assertEqual(_remaining_window_seconds(key, 60), 30)

    def test_native_ttl_reads_the_window_marker_not_a_new_counter(self):
        key = "ratelimit:ttl:backend"
        _, reservation = _count_request(key, 60)
        with patch.object(cache, "ttl", return_value=23.1, create=True) as ttl:
            self.assertEqual(_remaining_window_seconds(key, 60, reservation), 24)
        ttl.assert_called_once_with(_generation_key(key))
