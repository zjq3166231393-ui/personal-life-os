"""Rate-limiting middleware regression tests.

Locks in the per-IP API ceiling on /api/* (the AI parse + confirm endpoints)
and the login brute-force lockout. These paths were previously untested, so a
regression in middleware wiring would not have been caught by the suite.

The middleware is exercised directly with a stub get_response — no real AI call
or HTTP round-trip — so the tests stay fast and hermetic.
"""
import json

from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

import life.middleware as mw_mod
from life.middleware import (
    ApiRateLimitMiddleware,
    get_login_attempts,
    record_login_failure,
)


class ApiRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self._orig = (
            mw_mod.API_RATE_MAX,
            mw_mod.API_RATE_WINDOW,
            mw_mod.API_DAILY_MAX,
        )

    def tearDown(self):
        mw_mod.API_RATE_MAX, mw_mod.API_RATE_WINDOW, mw_mod.API_DAILY_MAX = self._orig
        cache.clear()

    def _mid(self, per_min=3, daily=500):
        mw_mod.API_RATE_MAX = per_min
        mw_mod.API_RATE_WINDOW = 60
        mw_mod.API_DAILY_MAX = daily
        return ApiRateLimitMiddleware(get_response=lambda r: HttpResponse("ok", status=200))

    def test_non_api_path_not_limited(self):
        mid = self._mid()
        req = RequestFactory().post("/expenses/")
        self.assertEqual(mid(req).status_code, 200)

    def test_api_get_not_limited(self):
        mid = self._mid()
        req = RequestFactory().get("/api/parse/")
        self.assertEqual(mid(req).status_code, 200)

    def test_api_post_over_per_minute_cap_returns_429(self):
        mid = self._mid(per_min=3)
        factory = RequestFactory()
        for i in range(3):
            self.assertEqual(
                mid(factory.post("/api/parse/")).status_code, 200,
                f"request {i + 1} should pass",
            )
        denied = mid(factory.post("/api/parse/"))
        self.assertEqual(denied.status_code, 429)
        self.assertIn("频繁", json.loads(denied.content)["error"])

    def test_api_daily_ceiling_enforced(self):
        mid = self._mid(per_min=1000, daily=2)
        factory = RequestFactory()
        self.assertEqual(mid(factory.post("/api/parse/")).status_code, 200)
        self.assertEqual(mid(factory.post("/api/parse/")).status_code, 200)
        self.assertEqual(mid(factory.post("/api/parse/")).status_code, 429)

    def test_client_ip_from_x_forwarded_for(self):
        mid = self._mid()
        req = RequestFactory().post("/api/parse/")
        req.META["HTTP_X_FORWARDED_FOR"] = "203.0.113.7, 10.0.0.1"
        self.assertEqual(mid._client_ip(req), "203.0.113.7")


class LoginRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_lockout_after_max_failures(self):
        ip = "198.51.100.23"
        for _ in range(mw_mod.MAX_ATTEMPTS):
            record_login_failure(ip)
        self.assertEqual(get_login_attempts(ip), 0)
        data = cache.get(mw_mod.RATE_LIMIT_KEY.format(ip))
        self.assertIsNotNone(data["locked_until"])

    def test_remaining_attempts_decreases(self):
        ip = "198.51.100.24"
        record_login_failure(ip)
        self.assertEqual(get_login_attempts(ip), mw_mod.MAX_ATTEMPTS - 1)
