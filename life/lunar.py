"""Lunar (农历) date helpers. Wraps `zhdate` and returns Django-safe primitives.

`zhdate.from_datetime` requires a `datetime`, so this module always converts
`date` → `datetime` before calling, and caches the conversion to avoid
re-running for the same day within a single request.
"""

from __future__ import annotations

from datetime import date, datetime, time

from zhdate import ZhDate

__all__ = ["lunar_today", "lunar_for", "format_lunar", "LUNAR_MONTH_NAMES", "SHENGXIAO_BY_YEAR_OFFSET"]

# 农历月份常用表达
LUNAR_MONTH_NAMES = {
    1: "正", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六",
    7: "七", 8: "八", 9: "九", 10: "十", 11: "冬", 12: "腊",
}
# 农历日期简称（初一~三十）
LUNAR_DAY_NAMES = {
    1: "初一", 2: "初二", 3: "初三", 4: "初四", 5: "初五",
    6: "初六", 7: "初七", 8: "初八", 9: "初九", 10: "初十",
    11: "十一", 12: "十二", 13: "十三", 14: "十四", 15: "十五",
    16: "十六", 17: "十七", 18: "十八", 19: "十九", 20: "二十",
    21: "廿一", 22: "廿二", 23: "廿三", 24: "廿四", 25: "廿五",
    26: "廿六", 27: "廿七", 28: "廿八", 29: "廿九", 30: "三十",
}
SHENGXIAO_ORDER = ["鼠", "牛", "虎", "兔", "龙", "蛇", "马", "羊", "猴", "鸡", "狗", "猪"]


def _to_dt(d: date) -> datetime:
    if isinstance(d, datetime):
        return d
    return datetime.combine(d, time(12, 0))


def _convert(d: date) -> ZhDate:
    return ZhDate.from_datetime(_to_dt(d))


def lunar_today() -> ZhDate:
    return _convert(date.today())


def lunar_for(d: date) -> ZhDate:
    return _convert(d)


def format_lunar(d: date | None = None, *, include_year: bool = True, include_day: bool = True, include_shengxiao: bool = True) -> str:
    """Return a compact human-readable lunar string.

    Examples:
      "农历七月初十一"（默认） / "丙午年" / "农历 七月十一"
    """
    z = _convert(d) if d else lunar_today()
    parts: list[str] = []
    if include_year:
        parts.append(f"农历{LUNAR_MONTH_NAMES.get(z.lunar_month, str(z.lunar_month))}月{LUNAR_DAY_NAMES.get(z.lunar_day, str(z.lunar_day))}")
    elif include_day:
        # 农历X月X 形式（去掉初/廿/十这类前缀，只保留 X 月 X 日）
        parts.append(f"{LUNAR_MONTH_NAMES.get(z.lunar_month, str(z.lunar_month))}月{LUNAR_DAY_NAMES.get(z.lunar_day, str(z.lunar_day))}")
    if include_shengxiao:
        try:
            offset = (z.lunar_year - 2020) % 12  # 2020 = 鼠年 idx 0
            sx = SHENGXIAO_ORDER[offset]
            parts.append(f"{sx}年")
        except Exception:  # pragma: no cover
            pass
    return " ".join(parts) if parts else ""


def lunar_year_gz(d: date | None = None) -> str:
    """Return the 干支 year like '丙午年' (optional informational use)."""
    z = _convert(d) if d else lunar_today()
    try:
        return z.chinese().split(" ")[-1].strip()  # e.g. '丙午年'
    except Exception:
        return ""
