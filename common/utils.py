"""Small shared helpers (security / navigation)."""
from urllib.parse import urlparse

from django.http import HttpResponseRedirect
from django.shortcuts import redirect


def safe_next(request, default="home", allow_referer=True):
    """Validate a post-action redirect target.

    Only an in-app, path-absolute target is allowed. This closes the
    open-redirect vector where ``next=//evil.com`` passes a naive
    ``startswith("/")`` check (browsers treat ``//evil.com`` as a
    protocol-relative external URL).

    Rejects:
      * empty / missing,
      * any scheme (``http``, ``https``, ``javascript`` …),
      * an explicit netloc (``//evil.com`` parses to netloc ``evil.com``),
      * anything that is not path-absolute.
    Falls back to ``default`` (a view name or URL name).
    """
    candidate = (request.POST.get("next") or "").strip()
    if not candidate and allow_referer:
        candidate = (request.META.get("HTTP_REFERER") or "").strip()
    if candidate:
        parsed = urlparse(candidate)
        # scheme must be empty, netloc must be empty, and it must start with "/".
        if not parsed.scheme and not parsed.netloc and candidate.startswith("/"):
            return HttpResponseRedirect(candidate)
    return redirect(default)
