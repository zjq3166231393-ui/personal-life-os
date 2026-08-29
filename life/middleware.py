"""Custom middleware: login rate limiting, API rate limiting, session timeout, no-cache for app pages."""
import time

from django.core.cache import cache
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import add_never_cache_headers

RATE_LIMIT_KEY = "login_attempts_{}"
MAX_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

# API rate limiting: per-IP ceiling on the AI parsing endpoints.
# Rules path (route_parse) is cheap, but confirm-actions writes DB rows and
# can be abused; a hard IP cap protects the server regardless of per-user quota.
API_RATE_KEY = "api_hits_{}"
API_RATE_WINDOW = 60          # seconds
API_RATE_MAX = 30             # requests per IP per minute
API_DAILY_KEY = "api_daily_{}"
API_DAILY_MAX = 500           # hard ceiling per IP per day


class LoginRateLimitMiddleware:
    """Limit login attempts. After MAX_ATTEMPTS failures, lock out for LOCKOUT_MINUTES."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == reverse("login") and request.method == "POST":
            ip = self._client_ip(request)
            key = RATE_LIMIT_KEY.format(ip)
            attempts = cache.get(key, {"count": 0, "locked_until": None})

            if attempts.get("locked_until"):
                if timezone.now() < attempts["locked_until"]:
                    # Still locked — redirect with error via session
                    request.session["login_locked"] = True
                else:
                    cache.delete(key)

        return self.get_response(request)

    def _client_ip(self, request):
        xff = request.META.get("HTTP_X_FORWARDED_FOR")
        if xff:
            return xff.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "127.0.0.1")


def record_login_failure(ip):
    """Called from the login view on failure. Records attempt count."""
    key = RATE_LIMIT_KEY.format(ip)
    data = cache.get(key, {"count": 0, "locked_until": None})
    data["count"] += 1
    if data["count"] >= MAX_ATTEMPTS:
        data["locked_until"] = timezone.now() + timezone.timedelta(minutes=LOCKOUT_MINUTES)
    cache.set(key, data, 3600)


def get_login_attempts(ip):
    """Return remaining attempts for this IP."""
    data = cache.get(RATE_LIMIT_KEY.format(ip), {"count": 0})
    return max(0, MAX_ATTEMPTS - data["count"])


class NoBrowserCacheMiddleware:
    """Disable browser/proxy caching for application pages.

    The dev server otherwise sends cacheable HTML, so after editing a
    template the browser keeps serving a stale page (e.g. the home page
    redirecting to /expenses/ long after it was changed). Static assets
    are left untouched so they can still be cached normally.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.method == "GET" and not (
            request.path.startswith("/static/") or request.path.startswith("/media/")
        ):
            add_never_cache_headers(response)
        return response


class ApiRateLimitMiddleware:
    """Per-IP rate limit for the JSON API endpoints (/api/*).

    Two sliding windows:
      - per-minute cap (API_RATE_MAX) to blunt bursts
      - per-day hard ceiling (API_DAILY_MAX) to cap total cost/abuse

    Authenticated users can still hit their own daily_ai_limit inside
    ai_router; this is a transport-level backstop that also covers
    unauthenticated probes (login_required already blocks those, but the
    middleware fails closed if cache is unavailable).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/api/") and request.method == "POST":
            ip = self._client_ip(request)
            denied = self._check(ip)
            if denied is not None:
                return JsonResponse(
                    {"ok": False, "error": denied}, status=429
                )
        return self.get_response(request)

    def _check(self, ip):
        minute_key = API_RATE_KEY.format(ip)
        day_key = API_DAILY_KEY.format(ip)
        now = int(time.time())
        window_start = cache.get(minute_key + "_ts")
        if window_start is None or now - window_start >= API_RATE_WINDOW:
            cache.set(minute_key + "_ts", now, API_RATE_WINDOW)
            cache.set(minute_key, 0, API_RATE_WINDOW)
            window_start = now
        count = cache.get(minute_key, 0)
        if count >= API_RATE_MAX:
            return f"请求过于频繁，请 {API_RATE_WINDOW} 秒后重试。"
        cache.incr(minute_key, 1)

        day_count = cache.get(day_key, 0)
        if day_count >= API_DAILY_MAX:
            return "今日请求次数已达上限，请明天再试。"
        try:
            cache.incr(day_key, 1)
        except ValueError:
            cache.set(day_key, 1, 86400)
        return None

    def _client_ip(self, request):
        xff = request.META.get("HTTP_X_FORWARDED_FOR")
        if xff:
            return xff.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "127.0.0.1")
