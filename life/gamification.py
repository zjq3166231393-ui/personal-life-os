"""游戏化激励（P2）：连续记账 streak、月度达成度、成就徽章。

设计原则
--------
- 所有指标按「本地日期」计算（TIME_ZONE=Asia/Shanghai），对 occurred_at 做
  timezone.localtime 转换后再取 date，避免 SQLite 下 __date 按 UTC 截断导致
  跨零点附近 streak 错算。
- 徽章规则与数据解耦：规则在 BADGE_DEFS，达成记录持久化到 Badge 表。
  规则一旦达成即永久点亮（不可撤销），符合「成就」语义。
- evaluate_badges 默认会持久化新点亮的徽章；首页摘要用 persist=False，避免
  在只读渲染路径上产生写操作。
"""

from calendar import monthrange
from datetime import timedelta

from django.utils import timezone

from .models import Badge, Expense, Tag

# 已确认、未软删的账目查询基线
_CONFIRMED = dict(is_deleted=False, status="confirmed")


def _log_dates(user):
    """返回用户有记账（>=1 笔已确认账目）的本地日期集合。"""
    dates = set()
    qs = Expense.objects.filter(user=user, **_CONFIRMED).only("occurred_at")
    for e in qs:
        dates.add(timezone.localtime(e.occurred_at).date())
    return dates


def current_streak(user, today=None):
    """截至今天（含）的连续记账天数。

    当天还没记，但昨天记了 → 视为「连续中」（不中断），从昨天起算；
    当天记了 → 含当天；今天和昨天都没记 → 0。
    """
    today = today or timezone.localdate()
    dates = _log_dates(user)
    if not dates:
        return 0
    if today in dates:
        cursor = today
    elif (today - timedelta(days=1)) in dates:
        cursor = today - timedelta(days=1)
    else:
        return 0
    streak = 0
    while cursor in dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def longest_streak(user):
    """历史最长连续记账天数。"""
    dates = sorted(_log_dates(user))
    if not dates:
        return 0
    best = cur = 1
    for i in range(1, len(dates)):
        if (dates[i] - dates[i - 1]).days == 1:
            cur += 1
        else:
            best = max(best, cur)
            cur = 1
    return max(best, cur)


def month_progress(user, today=None):
    """本月记账达成度。

    返回：logged 本月已记账天数、days_in_month 当月总天数、
    elapsed 本月已过去天数（1..today）、pct 进度百分比、is_full 是否全勤。
    """
    today = today or timezone.localdate()
    _, days_in_month = monthrange(today.year, today.month)
    elapsed = today.day
    logged = sum(
        1 for d in _log_dates(user)
        if d.year == today.year and d.month == today.month
    )
    pct = round(100 * logged / elapsed) if elapsed else 0
    return {
        "logged": logged,
        "days_in_month": days_in_month,
        "elapsed": elapsed,
        "pct": pct,
        "is_full": logged == days_in_month,
    }


# ── 徽章定义 ────────────────────────────────────────────────────────────
# metric 取值：count(累计笔数) / streak(当前连续) / month_full(当月全勤) /
#              cats(使用过的分类数) / tags(使用过的标签数)
BADGE_DEFS = [
    {"key": "first_log", "name": "第一笔", "icon": "🎉", "desc": "记录你的第一笔", "metric": "count", "target": 1},
    {"key": "log_30", "name": "小有收获", "icon": "📈", "desc": "累计记账 30 笔", "metric": "count", "target": 30},
    {"key": "log_100", "name": "记账达人", "icon": "🏆", "desc": "累计记账 100 笔", "metric": "count", "target": 100},
    {"key": "streak_3", "name": "三天打鱼", "icon": "🔥", "desc": "连续记账 3 天", "metric": "streak", "target": 3},
    {"key": "streak_7", "name": "一周不落", "icon": "🔥", "desc": "连续记账 7 天", "metric": "streak", "target": 7},
    {"key": "streak_30", "name": "月度满勤", "icon": "🔥", "desc": "连续记账 30 天", "metric": "streak", "target": 30},
    {"key": "month_full", "name": "当月全勤", "icon": "💯", "desc": "当月每天都记账", "metric": "month_full", "target": 1},
    {"key": "cat_8", "name": "分类达人", "icon": "🗂️", "desc": "使用过 8 个分类", "metric": "cats", "target": 8},
    {"key": "tags_3", "name": "标签初体验", "icon": "🏷️", "desc": "使用过 3 个标签", "metric": "tags", "target": 3},
]


def _metrics(user, today):
    qs = Expense.objects.filter(user=user, **_CONFIRMED)
    count = qs.count()
    cats = qs.exclude(category__isnull=True).values("category_id").distinct().count()
    tags = Tag.objects.filter(expenses__in=qs).distinct().count()
    return {
        "count": count,
        "streak": current_streak(user, today),
        "month_full": 1 if month_progress(user, today)["is_full"] else 0,
        "cats": cats,
        "tags": tags,
    }


def evaluate_badges(user, today=None, persist=True):
    """计算全部徽章的达成状态；persist=True 时持久化新点亮的徽章。

    返回列表，每项：{key,name,icon,desc,current,target,progress(0-100),
    earned(bool), earned_at(datetime|None)}。
    """
    today = today or timezone.localdate()
    metrics = _metrics(user, today)
    earned_map = {b.key: b.earned_at for b in Badge.objects.filter(user=user)}
    out = []
    for b in BADGE_DEFS:
        cur = metrics[b["metric"]]
        target = b["target"]
        progress = int(min(1.0, cur / target) * 100) if target else 100
        is_earned = cur >= target
        if is_earned and persist and b["key"] not in earned_map:
            badge, _ = Badge.objects.get_or_create(user=user, key=b["key"])
            earned_map[b["key"]] = badge.earned_at
        out.append({
            "key": b["key"],
            "name": b["name"],
            "icon": b["icon"],
            "desc": b["desc"],
            "current": cur,
            "target": target,
            "progress": progress,
            "earned": is_earned or (b["key"] in earned_map),
            "earned_at": earned_map.get(b["key"]),
        })
    return out


def home_gamification(user):
    """首页摘要（只读，不持久化）。"""
    today = timezone.localdate()
    badges = evaluate_badges(user, today, persist=False)
    mp = month_progress(user, today)
    return {
        "streak": current_streak(user, today),
        "streak_longest": longest_streak(user),
        "month_logged": mp["logged"],
        "month_elapsed": mp["elapsed"],
        "month_pct": mp["pct"],
        "badge_earned": sum(1 for b in badges if b["earned"]),
        "badge_total": len(badges),
    }
