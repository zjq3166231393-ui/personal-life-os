"""Custom middleware: login rate limiting, session timeout."""
from django.core.cache import cache
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

RATE_LIMIT_KEY = "login_attempts_{}"
MAX_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


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
