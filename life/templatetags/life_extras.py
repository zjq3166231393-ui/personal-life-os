"""LifeOS 模板扩展：农历等辅助过滤器。"""
import re
from datetime import date, datetime

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

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


@register.filter
def highlight(value, query):
    """把文本中命中搜索词的片段用 <mark> 包起来。

    用法：{{ item.title|highlight:q }}

    安全：先对原文和搜索词做 HTML 转义，再插入 <mark>，因此即使
    用户输入含 HTML/脚本也只会被原样显示，不会被执行。
    """
    if value is None or not query:
        return value or ""
    text = escape(str(value))
    token = escape(str(query))
    if not token:
        return text
    try:
        pattern = re.compile(re.escape(token), re.IGNORECASE)
    except re.error:
        return text
    return mark_safe(pattern.sub(lambda m: f"<mark class='lf-hl'>{m.group(0)}</mark>", text))
