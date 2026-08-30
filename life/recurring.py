"""周期性账单自动入账（P0-1）。

背景：``RecurringExpense``（固定支出）此前只用于「提醒」和「现金流预测」，
到期并不会真的生成账目——用户每月还得手动再记一遍房租、话费、会员订阅。
这是「记账工具」与「自动化账本」的分水岭。

本模块把到期日推进成真实账目，设计上坚持三条：

1. **幂等**：用 ``last_generated_date`` 作游标，同一到期日只会生成一次；
   管理命令可放心重复执行（cron 每天跑也不会重复入账）。
2. **不抢用户的账**：生成前做近似查重——同分类同金额、日期在到期日 ±5 天内
   已存在账目则跳过。用户自己先手动记过的，不会被记第二遍。
3. **可控**：``auto_post=False`` 的计划只提醒不记账；生成出的账目
   ``source='recurring'``，可追溯、可批量清理。

触发方式：
- 管理命令 ``python manage.py generate_recurring [--dry-run]``
- 页面惰性触发 ``maybe_generate_for_user()``（首页调用，每日缓存节流一次），
  适配没有 cron 的单用户部署（Railway / SQLite）。
"""

from calendar import monthrange
from datetime import date, datetime, time, timedelta

from django.core.cache import cache
from django.utils import timezone

from .models import Expense, RecurringExpense

# 查重窗口：到期日前后各几天内已有同分类同金额的账目，就认为用户已记过
DEDUP_WINDOW_DAYS = 5

# 惰性生成的缓存键前缀与有效期（同一用户同一天只跑一次）
_GEN_CACHE_TTL = 24 * 3600


def _monthly_occurrence(year, month, due_day):
    """返回某年某月的第 due_day 天；该月没有这一天时取月末（如 31 日 → 2 月 28 日）。"""
    last = monthrange(year, month)[1]
    return date(year, month, min(due_day, last))


def due_dates_for(plan, until):
    """列出 plan 从 start_date 起、不晚于 until 的全部到期日（升序）。

    - weekly：从 start_date 起每 7 天
    - monthly / quarterly / yearly：按 due_day 逐月推进，步长 1 / 3 / 12 个月
    - end_date 之后的到期日不计入
    """
    if plan.start_date > until:
        return []

    if plan.frequency == RecurringExpense.Frequency.WEEKLY:
        out = []
        d = plan.start_date
        while d <= until:
            out.append(d)
            d += timedelta(days=7)
        return [d for d in out if plan.end_date is None or d <= plan.end_date]

    step = {
        RecurringExpense.Frequency.MONTHLY: 1,
        RecurringExpense.Frequency.QUARTERLY: 3,
        RecurringExpense.Frequency.YEARLY: 12,
    }.get(plan.frequency, 1)

    out = []
    y, m = plan.start_date.year, plan.start_date.month
    # 上限保护：极端脏数据（如 start_date 远早于今天）不至于无限循环
    guard = 0
    while guard < 2000:
        guard += 1
        d = _monthly_occurrence(y, m, plan.due_day)
        if d > until:
            break
        if d >= plan.start_date and (plan.end_date is None or d <= plan.end_date):
            out.append(d)
        m += step
        while m > 12:
            m -= 12
            y += 1
    return out


def _already_recorded(plan, due):
    """近似查重：用户可能已经手动记过这笔账。

    判定条件：同用户、未删除、同金额、同分类，且发生日期落在
    [due - 5d, due + 5d] 区间内。
    """
    return Expense.objects.filter(
        user=plan.user,
        is_deleted=False,
        amount=plan.amount,
        category=plan.category,
        occurred_at__date__gte=due - timedelta(days=DEDUP_WINDOW_DAYS),
        occurred_at__date__lte=due + timedelta(days=DEDUP_WINDOW_DAYS),
    ).exists()


def _create_expense_for(plan, due):
    """为某个到期日生成一笔真实账目。"""
    occurred = timezone.make_aware(datetime.combine(due, time(12, 0)))
    return Expense.objects.create(
        user=plan.user,
        category=plan.category,
        type=Expense.TransactionType.EXPENSE,
        amount=plan.amount,
        occurred_at=occurred,
        merchant=plan.name,
        note=plan.name,
        source=Expense.Source.RECURRING,
        status=Expense.Status.CONFIRMED,
        raw_text=f"由固定支出「{plan.name}」自动生成",
    )


def generate_due_recurring(user=None, today=None, dry_run=False):
    """把所有到期但未入账的固定支出推进为真实账目。

    Args:
        user: 只处理该用户；None 表示全部用户（管理命令用）
        today: 基准日期（默认今天）
        dry_run: 只统计不写库

    Returns:
        dict: {"plans": 扫描的计划数, "created": 生成笔数,
               "skipped": 因查重跳过的笔数, "dates": [(计划名, 到期日), ...]}
    """
    today = today or timezone.localdate()
    plans = RecurringExpense.objects.filter(is_active=True, auto_post=True)
    if user is not None:
        plans = plans.filter(user=user)

    created = skipped = 0
    touched = []
    for plan in plans:
        last = plan.last_generated_date
        new_dates = []
        for d in due_dates_for(plan, today):
            if last and d <= last:
                continue                      # 幂等：已经生成过的跳过
            if _already_recorded(plan, d):
                skipped += 1                  # 用户已手动记过，不重复入账
                continue
            new_dates.append(d)

        if dry_run:
            created += len(new_dates)
            touched.extend((plan.name, d) for d in new_dates)
            continue

        for d in new_dates:
            _create_expense_for(plan, d)
            created += 1
            touched.append((plan.name, d))

        # 游标推进到今天：无论本次是否真的生成，都不再回头补更早的日期，
        # 避免用户删掉某条自动账目后第二天又被重新生成。
        if new_dates or last is None:
            plan.last_generated_date = today
            plan.save(update_fields=["last_generated_date"])

    return {
        "plans": plans.count(),
        "created": created,
        "skipped": skipped,
        "dates": touched,
    }


def maybe_generate_for_user(user, today=None):
    """页面惰性触发：同一用户每天最多跑一次，结果写缓存。

    适配没有 cron 的部署环境——用户每天开首页即可自动入账。
    返回生成统计 dict；当天已执行过则返回 None。
    """
    today = today or timezone.localdate()
    key = f"recurring:gen:{user.pk}:{today}"
    if cache.get(key):
        return None
    stats = generate_due_recurring(user=user, today=today)
    cache.set(key, 1, _GEN_CACHE_TTL)
    return stats
