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
def expense_title(e):
    """返回一条记录最合适的显示名称。

    对 Expense：备注 > 商家 > 分类名 > 未命名。
    对 Task/Note/Reminder/Countdown：优先 title，没有则 fallback。
    用于账目列表、搜索结果、删除确认等所有需要「给这条记录起个名字」的地方。
    """
    if not e:
        return "未命名"
    # Expense
    if getattr(e, "note", None):
        return e.note
    if getattr(e, "merchant", None):
        return e.merchant
    if getattr(e, "category", None) and e.category:
        return e.category.name
    # 其余模型（Task/Note/Reminder/Countdown）
    if getattr(e, "title", None):
        return e.title
    if getattr(e, "name", None):
        return e.name
    return "未命名"


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
