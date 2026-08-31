"""Shared service helpers used across views.

These are pure-ish functions (category resolution, due-date bumping, title
validation, reminder-window math, and home-dashboard assembly) kept out of the
view modules so the logic lives in one place and the view files stay focused on
request handling.
"""
from calendar import monthrange
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from math import ceil

from django.db.models import Q, Sum
from django.utils import timezone

from .forecast import cashflow_forecast
from .gamification import home_gamification
from .lunar import format_lunar, lunar_year_gz
from .models import (
    Account,
    BalanceSnapshot,
    Budget,
    Category,
    Countdown,
    Expense,
    InstallmentPlan,
    RecurringExpense,
    Reminder,
    SavingsGoal,
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


def account_balance_as_of(account, day):
    """计算账户在 day 当天结束时的余额（按日快照 / 历史净值用）。

    逻辑与 Account.balance 完全一致，只是把「全部流水」换成「occurred_at <= day 结束」，
    从而能得到历史任意一天的余额。净流入 = 初始 + 收入 - 支出 - 转出 + 转入。
    """
    from django.db.models import Sum

    zero = Decimal("0")
    end = timezone.make_aware(datetime.combine(day, time.max))
    base = account.initial_balance or zero

    def _sum(qs, **kw):
        return qs.filter(is_deleted=False, status="confirmed", occurred_at__lte=end, **kw).aggregate(s=Sum("amount"))["s"] or zero

    income = _sum(account.transactions, type="income")
    expense = _sum(account.transactions, type="expense")
    out_transfer = _sum(account.transactions, type="transfer")
    in_transfer = _sum(account.incoming_transfers, type="transfer")
    return base + income - expense - out_transfer + in_transfer


def daily_balance_series(account, start, end):
    """返回账户在 [start, end] 区间逐日余额 list[(date, Decimal)]。

    高效做法：一次性预载该账户全部已确认流水，按发生日聚合当日净变动，
    再按日做累积求和得到每日余额。复杂度 O(流水数 + 天数)，远优于对每一天
    各跑 4 次聚合查询（snapshot_balances 回填命令与净值趋势图都依赖它）。

    净变动规则与 Account.balance 完全一致：收入 +、支出 −、转出 −、转入 +。
    """
    zero = Decimal("0")
    base = account.initial_balance or zero
    daily = {}

    def add(d, delta):
        daily[d] = daily.get(d, zero) + delta

    for t in account.transactions.filter(is_deleted=False, status="confirmed"):
        d = t.occurred_at.date()
        if t.type == "income":
            add(d, t.amount)
        elif t.type == "expense":
            add(d, -t.amount)
        elif t.type == "transfer":
            add(d, -t.amount)
    for t in account.incoming_transfers.filter(is_deleted=False, status="confirmed"):
        if t.type == "transfer":
            add(t.occurred_at.date(), t.amount)

    sorted_dates = sorted(daily.keys())
    series = []
    cum = zero
    idx = 0
    cur = start
    step = timedelta(days=1)
    while cur <= end:
        while idx < len(sorted_dates) and sorted_dates[idx] <= cur:
            cum += daily[sorted_dates[idx]]
            idx += 1
        series.append((cur, base + cum))
        cur += step
    return series


def _ensure_today_snapshots(user, today):
    """懒确保：为用户所有活跃账户补齐「今天」的余额快照。

    这样净值趋势图在用户尚未运行 snapshot_balances 回填命令时也能立即出图
    （至少有一条今日曲线）。已存在的日期会被 unique 约束忽略。
    """
    accounts = Account.objects.filter(user=user, is_deleted=False, is_active=True)
    existing = set(
        BalanceSnapshot.objects.filter(user=user, date=today).values_list("account_id", flat=True)
    )
    to_create = []
    for a in accounts:
        if a.id in existing:
            continue
        to_create.append(BalanceSnapshot(user=user, account=a, date=today, balance=a.balance))
    if to_create:
        BalanceSnapshot.objects.bulk_create(to_create, ignore_conflicts=True)


def net_worth_data(user, max_points=90, days=None):
    """组装净值趋势图数据：日期标签、净值序列、当前净值、区间/30 天变化、账户构成。

    供 net_worth 视图与首页净值卡片复用。净值 = 当日所有活跃账户余额之和。
    ``days`` 指定时只取最近 N 天的窗口（用于页面上的区间切换）。
    """
    today = timezone.localdate()
    _ensure_today_snapshots(user, today)

    snaps = BalanceSnapshot.objects.filter(user=user).select_related("account").order_by("date", "account")
    by_date = {}
    for s in snaps:
        by_date[s.date] = by_date.get(s.date, Decimal("0")) + s.balance

    all_dates = sorted(by_date.keys())

    # 区间窗口：days 指定时只取最近 N 天
    if days and days > 0 and len(all_dates) > days:
        window = all_dates[-days:]
    else:
        window = all_dates

    # 下采样：点数过多时按步长抽稀，但始终保留最后一天
    if len(window) > max_points:
        stride = ceil(len(window) / max_points)
        picked = window[::stride]
        if picked[-1] != window[-1]:
            picked.append(window[-1])
        window = picked

    series = [float(by_date[d]) for d in window]
    current = by_date[window[-1]] if window else Decimal("0")
    first = by_date[window[0]] if window else Decimal("0")
    change = current - first
    change_pct = int(change / first * 100) if first > 0 else 0

    nw_30 = by_date.get(today - timedelta(days=30))
    change_30 = (current - nw_30) if nw_30 is not None else None

    acct_qs = Account.objects.filter(user=user, is_deleted=False, is_active=True)
    total = sum((a.balance for a in acct_qs), Decimal("0")) or Decimal("1")
    palette = ["#2b56d8", "#4b80f0", "#22c55e", "#f59e0b", "#ef4444", "#a855f7", "#06b6d4", "#ec4899"]
    accounts = []
    for i, a in enumerate(acct_qs):
        pct = int(a.balance / total * 100)
        accounts.append({
            "obj": a,
            "balance": a.balance,
            "pct": pct,
            "color": palette[i % len(palette)],
        })

    return {
        "labels": [d.isoformat() for d in window],
        "series": series,
        "current": current,
        "first": first,
        "change": change,
        "change_pct": change_pct,
        "change_30": change_30,
        "days": days,
        "accounts": accounts,
        "has_data": bool(window),
    }


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


def _net_worth_sparkline(user, max_points=30, w=100.0, h=30.0, pad=2.0):
    """为首页净值卡生成轻量 SVG 折线点（只读快照，不引 Chart.js）。

    返回 {"points": "x,y x,y ...", "up": bool}；点数不足 2 时返回 None。
    用今日快照求和后的逐日净值序列，按 viewBox 归一化，供模板直接画 <polyline>。
    """
    snaps = BalanceSnapshot.objects.filter(user=user).values_list("date", "balance")
    by_date = {}
    for d, b in snaps:
        by_date[d] = by_date.get(d, Decimal("0")) + b
    dates = sorted(by_date.keys())
    if len(dates) < 2:
        return None
    if len(dates) > max_points:
        stride = ceil(len(dates) / max_points)
        picked = dates[::stride]
        if picked[-1] != dates[-1]:
            picked.append(dates[-1])
        dates = picked
    vals = [float(by_date[d]) for d in dates]
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    n = len(vals)
    pts = []
    for i, v in enumerate(vals):
        x = pad + (w - 2 * pad) * (i / (n - 1))
        y = pad + (h - 2 * pad) * (1 - (v - lo) / rng)
        pts.append(f"{x:.1f},{y:.1f}")
    return {"points": " ".join(pts), "up": vals[-1] >= vals[0]}


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

    # ── 每日记账提醒（2026-08-30 增强）──
    # logged_today 驱动首页 nudge；daily_log_reminder_enabled 来自 UserProfile。
    # 启用时把标记提醒的 remind_at 刷新到今天，便于 scan_reminders 定时扫描触发通知
    # （仅在提醒日期已过期时写一次，避免每次首页加载都写库）。
    _profile = getattr(user, "profile", None)
    daily_log_reminder_enabled = bool(getattr(_profile, "daily_log_reminder_enabled", False))
    logged_today = Expense.objects.filter(
        user=user, status="confirmed", is_deleted=False,
        occurred_at__gte=aware_day_start(today), occurred_at__lte=aware_day_end(today),
    ).exists()
    if daily_log_reminder_enabled and _profile is not None:
        from datetime import datetime as _dt

        dr = Reminder.objects.filter(
            user=user, title="💰 每日记账提醒",
            recurrence_rule=Reminder.Recurrence.DAILY, is_enabled=True,
        ).first()
        if dr is not None and dr.remind_at.date() != today:
            _dt_obj = _dt.combine(today, _profile.daily_log_reminder_time)
            _event_at = timezone.make_aware(_dt_obj)
            dr.event_at = _event_at
            dr.remind_at = _event_at
            dr.save(update_fields=["event_at", "remind_at"])

    # ── 储蓄目标（2026-08-30 增强）──
    # 首页展示前 3 个进行中的目标进度，提供「攒钱」的正向激励入口
    _all_goals = list(SavingsGoal.objects.filter(user=user, is_active=True))
    savings_summary = [{
        "obj": g, "progress_pct": g.progress_pct,
        "remaining": g.remaining, "is_reached": g.is_reached,
    } for g in _all_goals[:3]]
    savings_total_target = sum((g.target_amount for g in _all_goals), Decimal(0))
    savings_total_current = sum((g.current_amount for g in _all_goals), Decimal(0))

    # ── 净值概览（2026-08-30 增强）──
    # 优先用「今日余额快照」求和（趋势图同一数据底座）；无快照时退化到各活跃账户实时余额，
    # 保证首页卡片在用户尚未运行 snapshot_balances 回填时也能显示当前净值。
    # 懒确保今日快照：让首页迷你走势也包含今天这个点（与净值页同一哲学）。
    _ensure_today_snapshots(user, today)
    nw_today = BalanceSnapshot.objects.filter(user=user, date=today).aggregate(s=Sum("balance"))["s"]
    if nw_today is None:
        nw_today = sum(
            (a.balance for a in Account.objects.filter(user=user, is_deleted=False, is_active=True)),
            Decimal(0),
        )
    nw_30 = BalanceSnapshot.objects.filter(user=user, date=today - timedelta(days=30)).aggregate(s=Sum("balance"))["s"]
    net_worth_now = nw_today if nw_today is not None else Decimal(0)
    net_worth_change_30 = (net_worth_now - nw_30) if nw_30 is not None else None
    net_worth_sparkline = _net_worth_sparkline(user)

    return {
        "today": today, "top_tasks": top_tasks, "due_today": due_today,
        "near_three_days": near_three_days, "reminders": reminders, "bills": bills, "spent": spent,
        "budget_amount": budget_amount, "budget_pct": budget_pct,
        # ── 储蓄目标（2026-08-30 增强）──
        "savings_summary": savings_summary,
        "savings_total_target": savings_total_target,
        "savings_total_current": savings_total_current,
        # ── 净值概览（2026-08-30 增强）──
        "net_worth_now": net_worth_now,
        "net_worth_change_30": net_worth_change_30,
        "net_worth_sparkline": net_worth_sparkline,
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
        # ── 现金流预测（洞察增强：基于余额 + 固定支出推算未来余额） ──
        "cashflow": cashflow_forecast(user, days=30),
        # ── 每日记账提醒（2026-08-30 增强）──
        "logged_today": logged_today,
        "daily_log_reminder_enabled": daily_log_reminder_enabled,
    }
