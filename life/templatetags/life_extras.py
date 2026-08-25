"""LifeOS 模板扩展：农历等辅助过滤器。"""
from datetime import date, datetime

from django import template

from ..lunar import format_lunar

register = template.Library()


@register.filter
def lunar_date(value):
    """将日期/时间转为紧凑农历（月+日，如「七月廿八」），用于提醒列表/首页展示。

    用法：{{ reminder.event_at|lunar_date }}
    """
    if value is None:
        return ""
    d = value.date() if isinstance(value, datetime) else value
    if not isinstance(d, date):
        return ""
    try:
        return format_lunar(d, include_year=False, include_shengxiao=False)
    except Exception:
        return ""
