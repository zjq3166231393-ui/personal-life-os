"""Daily check-in / habit reminder model.

Each user can have multiple `DailyCheckin` records. A record represents a
habit-or-reminder that should be checked off once per day (e.g. "背单词",
"练口语", "快手签到"). The `done_dates` JSON Field stores ISO date strings
for each day the user has checked it off; the view layer derives "today is
done" and "streak" from this list.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from django.conf import settings
from django.db import models


class DailyCheckin(models.Model):
    """A recurring daily habit / check-in reminder that the user checks off
    once per day on the Home page.

    Examples: 背单词、练口语、快手签到.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="daily_checkins")
    title = models.CharField(max_length=100)
    icon = models.CharField(max_length=4, default="📌", help_text="Emoji shown on the home card.")
    note = models.CharField(max_length=200, blank=True, default="")
    reminder_time = models.TimeField(null=True, blank=True, help_text="Optional time-of-day the user wants to do it (display only).")
    done_dates = models.JSONField(default=list, blank=True, help_text="List of ISO date strings (YYYY-MM-DD) when this was checked.")
    is_active = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["is_active", "-created_at"]

    def __str__(self) -> str:
        return f"{self.icon} {self.title}"

    # ── convenience helpers ─────────────────────────────────────────────
    def is_done_on(self, d: date) -> bool:
        try:
            return d.isoformat() in (self.done_dates or [])
        except (TypeError, ValueError):
            return False

    def streak(self, today: date | None = None) -> int:
        """Consecutive-day streak ending today.

        - If today is checked off → count consecutive days backward from today.
        - If today is not yet done → use yesterday as anchor; the streak is
          preserved so the UI keeps showing it until the day resets.
        """
        days = sorted(self.done_dates or [])
        if not days:
            return 0
        s = set(days)
        anchor = today or date.fromisoformat(days[-1])
        if anchor.isoformat() not in s:
            # Fall back to the most recent done date.
            anchor = date.fromisoformat(days[-1])
        count = 0
        cur = anchor
        while cur.isoformat() in s:
            count += 1
            cur = cur - timedelta(days=1)
        return count


def _normalize_dates(value) -> list[str]:
    """Return a de-duplicated sorted list of ISO date strings."""
    if not value:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    out = sorted({d for d in value if isinstance(d, str) and len(d) == 10})
    return out


def daily_progress_for(user, today: date) -> dict:
    """Return `{"done": int, "total": int}` for the home page progress bar."""
    qs = DailyCheckin.objects.filter(user=user, is_deleted=False, is_active=True)
    total = qs.count()
    done = sum(1 for c in qs if c.is_done_on(today))
    return {"done": done, "total": total}
