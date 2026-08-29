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

    if not only or only == "expense":
        qs = Expense.objects.filter(
            user=user, is_deleted=False
        ).filter(
            _build_cond(q, ["note", "merchant", "raw_text", "category__name"])
        ).select_related("category")[:SEARCH_LIMIT_PER_TYPE + 1]
        groups.append(_group("expense", "账目", "expense_list", qs))

    if not only or only == "task":
        qs = Task.objects.filter(
            user=user, is_deleted=False
        ).filter(
            _build_cond(q, ["title", "description", "raw_text"])
        )[:SEARCH_LIMIT_PER_TYPE + 1]
        groups.append(_group("task", "任务", "task_list", qs))

    if not only or only == "note":
        qs = Note.objects.filter(
            user=user, is_deleted=False
        ).filter(
            _build_cond(q, ["title", "raw_text"])
        )[:SEARCH_LIMIT_PER_TYPE + 1]
        groups.append(_group("note", "随心记", "note_list", qs))

    if not only or only == "reminder":
        qs = Reminder.objects.filter(
            user=user
        ).filter(
            _build_cond(q, ["title"])
        )[:SEARCH_LIMIT_PER_TYPE + 1]
        groups.append(_group("reminder", "提醒", "reminder_list", qs))

    if not only or only == "countdown":
        qs = Countdown.objects.filter(
            user=user
        ).filter(
            _build_cond(q, ["title", "note"])
        )[:SEARCH_LIMIT_PER_TYPE + 1]
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
