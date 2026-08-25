"""Shared service helpers used across views.

These are pure-ish functions (category resolution, due-date bumping, title
validation) kept out of the view modules so the logic lives in one place and
the view files stay focused on request handling.
"""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from .models import Category

# 占位标题黑名单：仅 2~6 字、无业务关键词的纯类目词，禁止作为任务/提醒/笔记的标题保存。
# 即使前端 is-invalid 漏了，后端也要兜底拦截，避免数据库出现无意义记录。
TITLE_PLACEHOLDERS = frozenset({
    "任务", "提醒", "待办", "事件", "事项", "备忘", "记录", "东西", "内容", "文本",
})


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
