"""全局搜索。

跨 Expense / Task / Note / Reminder / Countdown 检索，全部严格限定
user=request.user，避免跨用户数据泄露。

每类结果都做上限截断（SEARCH_LIMIT_PER_TYPE），避免某个关键词命中
上千条时把页面拖垮——先给出最有价值的若干条，用户可再去对应列表页
用筛选器细看。
"""

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import render

from .models import Countdown, Expense, Note, Reminder, Task

# 单个类型最多返回多少条。超出只显示前 N 条并标注总数。
SEARCH_LIMIT_PER_TYPE = 20

# 搜索入口支持的类型。key 用于模板分组，label 是中文名。
SEARCH_TYPES = ("expense", "task", "note", "reminder", "countdown")

# 类型别名：用户搜这些词时，返回对应类型的全部记录。
# 例如搜"账目"或"支出"都能把所有 Expense 列出来，避免用户想"看看账目"时 0 结果。
TYPE_ALIASES = {
    "expense": ("账目", "支出", "收入", "expense", "income"),
    "task": ("任务", "待办", "todo", "task"),
    "note": ("随心记", "笔记", "note", "notes"),
    "reminder": ("提醒", "reminders", "提醒事项"),
    "countdown": ("倒计时", "纪念日", "countdown", "countdowns"),
}


def _alias_for_key(q):
    """如果 q 是某个类型的别名，返回该类型 key，否则返回 None。"""
    q = (q or "").lower().strip()
    if not q:
        return None
    for key, aliases in TYPE_ALIASES.items():
        if q in aliases:
            return key
    return None


def _build_cond(q, fields):
    cond = Q()
    for f in fields:
        cond |= Q(**{f"{f}__icontains": q})
    return cond


@login_required
def search(request):
    q = (request.GET.get("q") or "").strip()
    only = (request.GET.get("type") or "").strip()
    if only not in SEARCH_TYPES:
        only = ""

    ctx = {
        "q": q,
        "only": only,
        "has_query": bool(q),
        "limit": SEARCH_LIMIT_PER_TYPE,
        "results": [],
        "total": 0,
    }
    if not q:
        return render(request, "life/search.html", ctx)

    user = request.user
    groups = []

    # 当 q 是某个类型的中文别名时，对该类型返回全部记录（其余类型仍做文本匹配）。
    # 例如搜"账目"会列出所有 Expense；搜"水电费"则只匹配文本命中的那几条。
    alias_for = _alias_for_key(q)

    if not only or only == "expense":
        if alias_for == "expense":
            qs = Expense.objects.filter(user=user, is_deleted=False).order_by("-occurred_at")
        else:
            qs = Expense.objects.filter(
                user=user, is_deleted=False
            ).filter(
                _build_cond(q, ["note", "merchant", "raw_text", "category__name", "tags__name"])
            )
        # tags__name 会 join 中间表，一条记录命中多个标签时会产生重复行，故 distinct
        qs = qs.select_related("category").prefetch_related("tags").distinct()[:SEARCH_LIMIT_PER_TYPE + 1]
        groups.append(_group("expense", "账目", "expense_list", qs))

    if not only or only == "task":
        if alias_for == "task":
            qs = Task.objects.filter(user=user, is_deleted=False).order_by("-created_at")
        else:
            qs = Task.objects.filter(
                user=user, is_deleted=False
            ).filter(
                _build_cond(q, ["title", "description", "raw_text", "tags__name"])
            )
        qs = qs.prefetch_related("tags").distinct()[:SEARCH_LIMIT_PER_TYPE + 1]
        groups.append(_group("task", "任务", "task_list", qs))

    if not only or only == "note":
        if alias_for == "note":
            qs = Note.objects.filter(user=user, is_deleted=False).order_by("-created_at")
        else:
            qs = Note.objects.filter(
                user=user, is_deleted=False
            ).filter(
                _build_cond(q, ["title", "raw_text", "tags__name"])
            )
        qs = qs.prefetch_related("tags").distinct()[:SEARCH_LIMIT_PER_TYPE + 1]
        groups.append(_group("note", "随心记", "note_list", qs))

    if not only or only == "reminder":
        if alias_for == "reminder":
            qs = Reminder.objects.filter(user=user).order_by("remind_at")
        else:
            qs = Reminder.objects.filter(
                user=user
            ).filter(
                _build_cond(q, ["title"])
            )
        qs = qs[:SEARCH_LIMIT_PER_TYPE + 1]
        groups.append(_group("reminder", "提醒", "reminder_list", qs))

    if not only or only == "countdown":
        if alias_for == "countdown":
            qs = Countdown.objects.filter(user=user).order_by("target_date")
        else:
            qs = Countdown.objects.filter(
                user=user
            ).filter(
                _build_cond(q, ["title", "note"])
            )
        qs = qs[:SEARCH_LIMIT_PER_TYPE + 1]
        groups.append(_group("countdown", "倒计时", "countdown_list", qs))

    ctx["results"] = groups
    ctx["total"] = sum(g["count"] for g in groups)
    return render(request, "life/search.html", ctx)


def _group(key, label, list_url_name, qs):
    """把 queryset 裁到上限，并标注是否还有更多。"""
    items = list(qs)
    has_more = len(items) > SEARCH_LIMIT_PER_TYPE
    items = items[:SEARCH_LIMIT_PER_TYPE]
    return {
        "key": key,
        "label": label,
        "list_url_name": list_url_name,
        "items": items,
        "count": len(items),
        "has_more": has_more,
    }
