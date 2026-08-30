"""现金流预测：基于账户余额 + 未来固定支出，推算未来 N 天余额走势。

对标 MoneyWiz 的「现金流预测」——点日历任意日期即可看到未来余额。
我们有 ``RecurringExpense``（固定支出）与 ``Account.balance``（实时余额）数据基础，
本模块把它们合成为一条「未来余额」曲线。

设计要点
--------
- 纯函数，可注入 ``as_of`` 与 ``days``，便于测试（不依赖 ``timezone.now()``）。
- 发生日生成覆盖 weekly / monthly / quarterly / yearly，并尊重 ``start_date`` /
  ``end_date`` 边界与 ``due_day``（自动收敛到当月最后一天）。
- 余额按天累减：当天有多笔扣款时全部计入，曲线连续无跳变。
"""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from .models import Account, RecurringExpense


def _clamp_day(year, month, day):
    """Return a valid date: if ``day`` exceeds the month's last day, clamp it."""
    last = monthrange(year, month)[1]
    return date(year, month, min(day, last))


def _month_steps(lo, hi, step):
    """Yield (year, month) tuples from ``lo`` to ``hi`` advancing by ``step`` months."""
    y, m = lo.year, lo.month
    while (y, m) <= (hi.year, hi.month):
        yield (y, m)
        m += step
        while m > 12:
            m -= 12
            y += 1


def _recurring_occurrences(re, window_start, window_end):
    """Yield occurrence dates of a ``RecurringExpense`` within [window_start, window_end]."""
    if not re.is_active:
        return
    lo = max(window_start, re.start_date)
    hi = window_end
    if re.end_date and re.end_date < hi:
        hi = re.end_date
    if lo > hi:
        return

    freq = re.frequency
    if freq == "weekly":
        occ = re.start_date
        if occ < lo:
            diff = (lo - occ).days
            k = diff // 7
            if diff % 7:
                k += 1
            occ = occ + timedelta(days=k * 7)
        while occ <= hi:
            yield occ
            occ = occ + timedelta(days=7)
        return

    step = {"monthly": 1, "quarterly": 3, "yearly": 12}.get(freq, 1)
    # 月份序列必须锚定在账单启用日（start_date）的月份，而不是窗口起点，
    # 否则季度/年度网格会错位（如从窗口 2 月起算得到 2/5/8/11 而非正确的 4/7/10）。
    for (y, m) in _month_steps(re.start_date, hi, step):
        d = _clamp_day(y, m, re.due_day)
        if lo <= d <= hi:
            yield d


def cashflow_forecast(user, days=30, as_of=None):
    """Project account balance forward ``days`` days from ``as_of``.

    Returns a dict with:
    - ``has_accounts``: whether the user has any active (non-deleted) account
    - ``start_balance``: sum of active account balances today (Decimal)
    - ``series``: list of [iso_date, balance_float] for each day incl. today
    - ``upcoming``: first occurrences (max 8) as {date, amount, name}
    - ``min_balance`` / ``min_date``: lowest projected point
    - ``goes_negative``: True if any day's balance drops below zero
    - ``projected_end``: balance on the last projected day
    - ``next_bill``: the soonest upcoming occurrence or None
    """
    if as_of is None:
        from django.utils import timezone

        as_of = timezone.localdate()

    window_end = as_of + timedelta(days=days)

    accounts = Account.objects.filter(user=user, is_deleted=False, is_active=True)
    has_accounts = accounts.exists()
    start_balance = sum((a.balance for a in accounts), Decimal("0"))

    occurrences = []  # (date, amount, name)
    for re in RecurringExpense.objects.filter(user=user, is_active=True):
        for d in _recurring_occurrences(re, as_of, window_end):
            occurrences.append((d, re.amount, re.name))
    occurrences.sort()

    # Build a per-day balance series by walking the calendar and applying
    # every occurrence that falls on each day.
    series = []
    bal = start_balance
    oi = 0
    cur = as_of
    while cur <= window_end:
        while oi < len(occurrences) and occurrences[oi][0] == cur:
            bal -= occurrences[oi][1]
            oi += 1
        series.append((cur, bal))
        cur += timedelta(days=1)

    upcoming = [
        {"date": d, "amount": amt, "name": nm} for (d, amt, nm) in occurrences[:8]
    ]

    if series:
        min_date, min_balance = min(series, key=lambda x: x[1])
    else:
        min_date, min_balance = as_of, start_balance

    goes_negative = any(b < 0 for _, b in series)
    projected_end = series[-1][1] if series else start_balance
    next_bill = upcoming[0] if upcoming else None

    return {
        "has_accounts": has_accounts,
        "start_balance": start_balance,
        "days": days,
        "as_of": as_of,
        "series": [(d.isoformat(), float(b)) for d, b in series],
        "upcoming": upcoming,
        "min_balance": min_balance,
        "min_date": min_date,
        "goes_negative": goes_negative,
        "projected_end": projected_end,
        "next_bill": next_bill,
    }
