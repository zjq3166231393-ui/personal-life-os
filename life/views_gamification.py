"""游戏化成就页（P2）：连续记账、月度达成度、徽章墙。"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from .gamification import (
    current_streak,
    evaluate_badges,
    longest_streak,
    month_progress,
)


@login_required
def gamification(request):
    today = timezone.localdate()
    badges = evaluate_badges(request.user, today, persist=True)
    mp = month_progress(request.user, today)
    earned = sum(1 for b in badges if b["earned"])
    ctx = {
        "today": today,
        "streak": current_streak(request.user, today),
        "streak_longest": longest_streak(request.user),
        "month": mp,
        "badges": badges,
        "earned_count": earned,
        "total_count": len(badges),
    }
    return render(request, "life/gamification.html", ctx)
