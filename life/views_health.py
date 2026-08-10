"""Health check endpoint — no auth required."""
from django.db import connections
from django.http import JsonResponse


def health(request):
    ok = True
    db_ok = False
    try:
        connections["default"].cursor().execute("SELECT 1")
        db_ok = True
    except Exception:
        ok = False

    return JsonResponse({
        "status": "ok" if ok else "degraded",
        "database": "ok" if db_ok else "error",
        "version": "0.8.0",
    }, status=200 if ok else 503)
