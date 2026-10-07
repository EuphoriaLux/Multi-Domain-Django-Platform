"""Throttles key on the bare client IP, not the raw X-Forwarded-For (IP:PORT)."""
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from crush_lu.throttling import (
    LoginRateThrottle,
    PasswordResetRateThrottle,
    PhoneVerificationRateThrottle,
    QuizPinRateThrottle,
    SignupRateThrottle,
    ratelimit_view,
)

THROTTLES = [
    LoginRateThrottle,
    SignupRateThrottle,
    PhoneVerificationRateThrottle,
    QuizPinRateThrottle,
    PasswordResetRateThrottle,
]


class ClientIPThrottleKeyTests(TestCase):
    def setUp(self):
        cache.clear()
        self.rf = RequestFactory()

    def test_port_is_ignored_in_cache_key(self):
        a = self.rf.post("/", HTTP_X_FORWARDED_FOR="203.0.113.7:50123")
        b = self.rf.post("/", HTTP_X_FORWARDED_FOR="203.0.113.7:50124")
        for cls in THROTTLES:
            self.assertEqual(
                cls().get_cache_key(a, None), cls().get_cache_key(b, None), cls
            )

    def test_different_ips_get_different_keys(self):
        a = self.rf.post("/", HTTP_X_FORWARDED_FOR="203.0.113.7:50123")
        b = self.rf.post("/", HTTP_X_FORWARDED_FOR="203.0.113.8:50123")
        self.assertNotEqual(
            LoginRateThrottle().get_cache_key(a, None),
            LoginRateThrottle().get_cache_key(b, None),
        )

    def test_rotating_port_still_trips_quiz_pin_throttle(self):
        view = ratelimit_view([QuizPinRateThrottle])(lambda r: HttpResponse("ok"))
        statuses = [
            view(
                self.rf.post("/", HTTP_X_FORWARDED_FOR=f"203.0.113.7:{40000 + i}")
            ).status_code
            for i in range(12)
        ]
        self.assertIn(429, statuses)
