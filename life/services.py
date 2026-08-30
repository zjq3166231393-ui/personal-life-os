"""Shared service helpers used across views.

These are pure-ish functions (category resolution, due-date bumping, title
validation, reminder-window math, and home-dashboard assembly) kept out of the
view modules so the logic lives in one place and the view files stay focused on
request handling.
"""
from calendar import monthrange
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from .gamification import home_gamification
from .lunar import format_lunar, lunar_year_gz
from .models import (
    Budget,
    Category,
    Countdown,
    Expense,
    InstallmentPlan,
    RecurringExpense,
    Reminder,
    Task,
)
from .models_daily import DailyCheckin, daily_progress_for

# 占位标题黑名单：仅 2~6 字、无业务关键词的纯类目词，禁止作为任务/提醒/笔记的标题保存。
# 即使前端 is-invalid 漏了，后端也要兜底拦截，避免数据库出现无意义记录。
TITLE_PLACEHOLDERS = frozenset({
    "任务", "提醒", "待办", "事件", "事项", "备忘", "记录", "东西", "内容", "文本",
})


def aware_day_start(d):
    """date → 当天 00:00 的感知时区 datetime（供 DateTimeField 过滤/赋值）。

    用 date 直接过滤 DateTimeField 时，Django 会隐式补成当天 00:00 的 naive 值并抛
    RuntimeWarning，且在 UTC 部署环境下该值会被按默认时区解释。过滤 DateTimeField
    必须显式转成感知时区值。
    """
    return timezone.make_aware(datetime.combine(d, time.min))


def aware_day_end(d):
    """date → 当天 23:59:59.999999 的感知时区 datetime（闭区间右端）。

    用于 __lte 上界：直接传 date 会被补成 00:00，导致「当月/当周最后一天」的记录
    被整日排除（预算、月度统计、复盘都会少算一天）。
    """
    return timezone.make_aware(datetime.combine(d, time.max))


def resolve_category(user, name):
    """Return an existing expense Category by name (system or user-level),
    creating one for the user if it doesn't exist yet."""
    name = (name or "").strip()
    if not name:
        return None
    cat = Category.objects.filter(
        Q(user=user) | Q(user__isnull=True), name=name, is_active=True, type="expense"
    ).first()
    if cat:
        return cat
    return Category.objects.create(user=user, name=name, type="expense", is_active=True)


def bump_overdue_due(due_at):
    """如果 due_at 早于「当前时刻」，自动顺延到次日的同一时刻。

    解决「下午 14:00 创建了没指定时间的任务 → 默认 today 09:00 → 立即过期」的问题。

    同时做一次时区兜底：前端 ``<input type="date">`` 拼出来的 ISO 字符串不带 tz，
    直接 ``datetime.fromisoformat`` 拿到的是 naive；与 ``timezone.now()`` (aware)
    比较会抛 TypeError。这里把 naive 强制 make_aware 到当前时区 (Asia/Shanghai)，
    统一整个调用链的 aware 语义。
    """
    if due_at is None:
        return None
    if timezone.is_naive(due_at):
        due_at = timezone.make_aware(due_at)
    now = timezone.now()
    if due_at < now:
        # 推到明天同时间，确保至少还有 ~24h 缓冲
        return due_at + timedelta(days=1)
    return due_at


def is_placeholder_title(title):
    """True if ``title`` is a meaningless placeholder that must not be saved."""
    return (title or "").strip() in TITLE_PLACEHOLDERS


def _reminder_window(r, today):
    """Compute the next occurrence of a reminder's event and whether it should
    be visible on the home page right now, honoring recurrence + lead days.

    Returns (next_event_date, countdown_days, visible, lead_days).
    - next_event_date: the upcoming event date (this/next year/month/week/day).
    - visible: True when today falls inside the remind window
      [next_event - lead_days, next_event] (i.e. we are within "提前 N 天").
    """
    ed = r.event_at.date() if hasattr(r.event_at, "date") else r.event_at
    if r.recurrence_rule == "yearly":
        try:
            ne = ed.replace(year=today.year)
        except ValueError:
            ne = ed.replace(month=2, day=28, year=today.year)
        if ne < today:
            try:
                ne = ed.replace(year=today.year + 1)
            except ValueError:
                ne = ed.replace(month=2, day=28, year=today.year + 1)
    elif r.recurrence_rule == "monthly":
        ne = ed.replace(year=today.year, month=today.month)
        while ne < today:
            ne = ne.replace(year=ne.year + 1, month=1) if ne.month == 12 else ne.replace(month=ne.month + 1)
    elif r.recurrence_rule == "weekly":
        diff = (ed.weekday() - today.weekday()) % 7
        ne = today + timedelta(days=(diff or 7))
    elif r.recurrence_rule == "daily":
        ne = today
    else:
        ne = ed
    try:
        lead = int(str(r.remind_days_before).split(",")[0]) or 0
    except (ValueError, TypeError):
        lead = 0
    remind_start = ne - timedelta(days=lead)
    visible = remind_start <= today <= ne
    countdown = (ne - today).days
    return ne, countdown, visible, lead


def home_data(user):
    """Assemble the full context dict for the home dashboard.

    Extracted from the ``home()`` view (which used to be 130+ lines of inline
    query assembly) so the view stays a thin request→render wrapper. Pure-ish:
    takes a user, returns the dict consumed by ``life/home.html``.

    Behavior is intentionally identical to the original inline implementation —
    same queries, same context keys, same computed fields.
    """
    today = timezone.localdate()
    tomorrow = today + timedelta(days=1)
    month_start = date(today.year, today.month, 1)
    _, last_day = monthrange(today.year, today.month)
    month_end = date(today.year, today.month, last_day)

    # ── top 3 tasks ──────────────────────────────────────────────
    top_tasks = Task.objects.filter(
        user=user, is_deleted=False, status__in=["todo", "in_progress"],
    ).order_by("-priority", "due_at")[:3]

    # ── due today ────────────────────────────────────────────────
    due_today = Task.objects.filter(
        user=user, is_deleted=False, status__in=["todo", "in_progress"],
        due_at__date__gte=today, due_at__date__lt=tomorrow,
    ).order_by("-priority")

    # ── 近三天待办（含今天，未来 3 天内到期，按截止日排序） ──────
    near_end = today + timedelta(days=3)
    near_three_days = Task.objects.filter(
        user=user, is_deleted=False, status__in=["todo", "in_progress"],
        due_at__date__gte=today, due_at__date__lte=near_end,
    ).order_by("due_at", "-priority")

    # ── 日历提醒（带"提前 N 天"窗口，支持年/月/周循环） ──────────
    reminders = []
    for r in Reminder.objects.filter(user=user, is_enabled=True):
        ne, days, visible, lead = _reminder_window(r, today)
        if not visible:
            continue
        if days < 0:
            countdown_text = "已过期"
            tone = "overdue"
        elif days == 0:
            countdown_text = "今天"
            tone = "today"
        elif days <= 3:
            countdown_text = f"{days}天后"
            tone = "soon"
        else:
            countdown_text = f"{days}天后"
            tone = "later"
        reminders.append({
            "obj": r, "days": days, "countdown": countdown_text, "tone": tone,
            "event_date": ne, "lead": lead,
        })
    reminders.sort(key=lambda x: x["days"])
    reminders = reminders[:6]

    # ── upcoming bills (recurring + installment) ────────────────
    bills = []
    for r in RecurringExpense.objects.filter(user=user, is_active=True).select_related("category"):
        bill_date = date(today.year, today.month, r.due_day) if r.due_day >= today.day else date(today.year, today.month + 1 if today.month < 12 else today.year + 1, r.due_day) if today.month < 12 else date(today.year + 1, 1, r.due_day)
        bills.append({"name": r.name, "amount": r.amount, "date": bill_date, "type": "固定", "kind": "recurring", "pk": r.pk})
    for p in InstallmentPlan.objects.filter(user=user, status="active").select_related("category"):
        bills.append({"name": p.name, "amount": p.installment_amount, "date": p.next_due_date, "type": "分期", "kind": "installment", "pk": p.pk})
    bills.sort(key=lambda x: x["date"])
    bills = bills[:5]

    # ── budget summary ──────────────────────────────────────────
    # occurred_at 是 DateTimeField：边界必须感知时区且取到当日末尾，否则当月最后一天被漏计
    spent = Expense.objects.filter(user=user, type="expense", status="confirmed", is_deleted=False,
                                   occurred_at__gte=aware_day_start(month_start),
                                   occurred_at__lte=aware_day_end(month_end)).aggregate(s=Sum("amount"))["s"] or Decimal(0)
    budget = Budget.objects.filter(user=user, category__isnull=True, month=month_start).first()
    budget_amount = budget.amount if budget else Decimal(0)
    budget_pct = min(int(spent / budget_amount * 100) if budget_amount > 0 else 0, 100)

    # ── daily check-ins (今日待打卡) ─────────────────────────────
    daily_qs = DailyCheckin.objects.filter(user=user, is_deleted=False).order_by("-created_at")
    daily_items = []
    for c in daily_qs:
        daily_items.append({
            "obj": c, "is_done_today": c.is_done_on(today),
            "streak": c.streak(today),
        })
    daily_progress = daily_progress_for(user, today)

    # ── countdowns / anniversaries（iOS Day Matters 风格） ──────────
    cd_cards = []
    cd_hidden = 0
    cd_total = 0
    for c in Countdown.objects.filter(user=user, is_active=True):
        cd_total += 1
        if not c.show_on_home:
            cd_hidden += 1
            continue
        delta = c.days_diff(today)
        cd_cards.append({
            "obj": c, "delta": delta,
            "is_today": delta == 0,
            "is_past": delta < 0,
            "is_soon": 0 < delta <= 14,
        })
    # sort: pinned first, then by delta asc (down) / desc (up)
    cd_cards.sort(key=lambda x: (not x["obj"].pinned, x["delta"] if x["obj"].direction == "down" else -x["delta"]))
    cd_cards = cd_cards[:6]
    cd_pinned = next((x for x in cd_cards if x["obj"].pinned), None)

    # ── 用户配置的「提醒默认时间」（AI 解析未指定时刻时使用）──
    # 字段默认值已修正为 time(10, 0)（见 accounts/models.py），此处仍同时兼容 str：
    # 一旦上游传入字符串（表单、脚本、旧数据），直接 .strftime() 会抛 AttributeError。
    _remind = getattr(getattr(user, "profile", None), "default_reminder_time", None)
    if hasattr(_remind, "strftime"):
        default_reminder_time = _remind.strftime("%H:%M")
    elif isinstance(_remind, str) and _remind:
        default_reminder_time = _remind[:5]  # 兼容 "10:00" 与 "10:00:00"
    else:
        default_reminder_time = "10:00"

    return {
        "today": today, "top_tasks": top_tasks, "due_today": due_today,
        "near_three_days": near_three_days, "reminders": reminders, "bills": bills, "spent": spent,
        "budget_amount": budget_amount, "budget_pct": budget_pct,
        # ── 农历日期 ──
        "lunar_text": format_lunar(today),
        "lunar_year_gz": lunar_year_gz(today),
        # ── 每日打卡 ──
        "daily_items": daily_items,
        "daily_done_count": daily_progress["done"],
        "daily_total_count": daily_progress["total"],
        "daily_pct": int(100 * daily_progress["done"] / daily_progress["total"]) if daily_progress["total"] else 0,
        # ── 倒计时 / 纪念日（2026-08-25） ──
        "cd_cards": cd_cards,
        "cd_pinned": cd_pinned,
        "cd_total": cd_total,
        "cd_hidden": cd_hidden,
        "default_reminder_time": default_reminder_time,
        # ── 游戏化：连续记账 / 月度达成度 / 徽章（P2） ──
        **home_gamification(user),
    }
