"""日历视图 —— 对标滴答清单的「月视图」。

把散落在各模块的时间数据（支出、任务、提醒、倒计时、打卡）按日历聚合，
让用户一眼看到「这个月哪天有钱流出、哪天有截止」。

性能：所有数据用**单条聚合查询**取出后按日期分桶，避免逐日查询的 N+1。
"""

from collections import defaultdict
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import render
from django.utils import timezone

from .models import Countdown, Expense, Reminder, Task
from .models_daily import DailyCheckin
from .services import aware_day_end, aware_day_start

# 日历网格固定 6 行 × 7 列，避免月份切换时高度跳动
CAL_WEEKS = 6
WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]


@login_required
def calendar_view(request):
    today = timezone.localdate()
    year, month = _resolve_year_month(request, today)
    sel_day = _resolve_day(request, year, month)

    # ── 当月区间（含前后补位天，保证聚合覆盖整个网格）──────────────
    first = date(year, month, 1)
    grid_start = first - timedelta(days=first.weekday())          # 周一为起点
    grid_end = grid_start + timedelta(days=CAL_WEEKS * 7 - 1)
    last = _month_end(year, month)

    user = request.user

    # 支出/收入按日聚合
    exp_qs = (
        Expense.objects.filter(
            user=user, is_deleted=False,
            occurred_at__gte=aware_day_start(grid_start),
            occurred_at__lte=aware_day_end(grid_end),
        )
        .values_list("occurred_at", "type", "amount")
    )
    expense_by_day = defaultdict(lambda: {"expense": 0, "income": 0})
    for dt, typ, amount in exp_qs:
        d = timezone.localtime(dt).date()
        if typ in ("expense", "income"):
            expense_by_day[d][typ] += amount or 0

    # 任务（按截止日）
    task_days = set()
    for dt in Task.objects.filter(
        user=user, is_deleted=False, due_at__isnull=False,
        due_at__gte=aware_day_start(grid_start),
        due_at__lte=aware_day_end(grid_end),
    ).values_list("due_at", flat=True):
        task_days.add(timezone.localtime(dt).date())

    # 提醒（按事件日）
    reminder_days = set()
    for dt in Reminder.objects.filter(
        user=user,
        event_at__gte=aware_day_start(grid_start),
        event_at__lte=aware_day_end(grid_end),
    ).values_list("event_at", flat=True):
        reminder_days.add(timezone.localtime(dt).date())

    # 倒计时（DateField，直接按日期比较）
    countdown_days = {
        c.target_date for c in Countdown.objects.filter(
            user=user, target_date__gte=grid_start, target_date__lte=grid_end
        )
    }

    # 打卡：done_dates 是 JSON 数组，只能在 Python 侧判断
    checkins = DailyCheckin.objects.filter(user=user, is_deleted=False)
    checkin_days = set()
    for d in _iter_grid_dates(grid_start, CAL_WEEKS * 7):
        if any(c.is_done_on(d) for c in checkins):
            checkin_days.add(d)

    # ── 组装网格 ────────────────────────────────────────────────
    weeks = []
    for w in range(CAL_WEEKS):
        week = []
        for i in range(7):
            d = grid_start + timedelta(days=w * 7 + i)
            amounts = expense_by_day.get(d, {"expense": 0, "income": 0})
            week.append({
                "date": d,
                "day": d.day,
                "outside": d.month != month,
                "is_today": d == today,
                "selected": sel_day == d,
                "expense": amounts["expense"],
                "income": amounts["income"],
                "has_task": d in task_days,
                "has_reminder": d in reminder_days,
                "has_countdown": d in countdown_days,
                "has_checkin": d in checkin_days,
            })
        weeks.append(week)

    # ── 选中日的明细 ────────────────────────────────────────────
    detail = _day_detail(user, sel_day) if sel_day else None

    prev_month, prev_year = _shift(year, month, -1)
    next_month, next_year = _shift(year, month, 1)

    # 当月合计
    month_expense = sum(
        v["expense"] for d, v in expense_by_day.items() if d.month == month and d.year == year
    )
    month_income = sum(
        v["income"] for d, v in expense_by_day.items() if d.month == month and d.year == year
    )

    return render(request, "life/calendar.html", {
        "weeks": weeks,
        "weekdays": WEEKDAYS,
        "year": year,
        "month": month,
        "today": today,
        "sel_day": sel_day,
        "detail": detail,
        "prev_year": prev_year, "prev_month": prev_month,
        "next_year": next_year, "next_month": next_month,
        "month_expense": month_expense,
        "month_income": month_income,
        "last_day": last.day,
    })


# ── 辅助 ────────────────────────────────────────────────────────────

def _iter_grid_dates(start, count):
    for i in range(count):
        yield start + timedelta(days=i)


def _month_end(year, month):
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _shift(year, month, delta):
    m = month + delta
    if m < 1:
        return 12, year - 1
    if m > 12:
        return 1, year + 1
    return m, year


def _resolve_year_month(request, today):
    """解析 year/month 参数，非法值回退到今天。"""
    try:
        year = int(request.GET.get("year") or today.year)
        month = int(request.GET.get("month") or today.month)
    except (TypeError, ValueError):
        return today.year, today.month
    if not (1 <= month <= 12):
        month = today.month
    if not (1970 <= year <= 9999):
        year = today.year
    return year, month


def _resolve_day(request, year, month):
    """解析 day 参数；非法或越界返回 None（表示只显示整月，不展开明细）。"""
    raw = request.GET.get("day")
    if not raw:
        return None
    try:
        return date(year, month, int(raw))
    except (TypeError, ValueError):
        return None


def _day_detail(user, d):
    """某一天的全部记录，供点击后展开。"""
    start, end = aware_day_start(d), aware_day_end(d)
    day_checkins = [c for c in DailyCheckin.objects.filter(user=user, is_deleted=False) if c.is_done_on(d)]
    return {
        "date": d,
        "expenses": Expense.objects.filter(
            user=user, is_deleted=False, occurred_at__gte=start, occurred_at__lte=end
        ).select_related("category").order_by("-occurred_at"),
        "tasks": Task.objects.filter(
            user=user, is_deleted=False, due_at__gte=start, due_at__lte=end
        ).order_by("due_at"),
        "reminders": Reminder.objects.filter(
            user=user, event_at__gte=start, event_at__lte=end
        ).order_by("event_at"),
        "countdowns": Countdown.objects.filter(user=user, target_date=d),
        "checkins": day_checkins,
        "total_expense": Expense.objects.filter(
            user=user, is_deleted=False, type="expense",
            occurred_at__gte=start, occurred_at__lte=end
        ).aggregate(s=Sum("amount"))["s"] or 0,
        "total_income": Expense.objects.filter(
            user=user, is_deleted=False, type="income",
            occurred_at__gte=start, occurred_at__lte=end
        ).aggregate(s=Sum("amount"))["s"] or 0,
    }
