"""数据导出（CSV）。

设计要点：
1. 全部严格限定 user=request.user，绝不导出他人数据。
2. 使用 utf-8-sig 编码（带 BOM），Excel 打开中文不会乱码——这是国内用户
   最常见的导出场景，纯 utf-8 会被 Excel 当成 ANSI 而显示乱码。
3. 文件名带日期，避免多次导出互相覆盖。
"""

import csv

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from .models import Countdown, Expense, Note, Reminder, Task

# 每种导出的表头与取行函数。新增类型只需在这里加一项。
EXPORT_KINDS = {
    "expense": ("账目", ["日期", "类型", "金额", "分类", "商家", "备注", "状态", "来源"]),
    "task": ("任务", ["标题", "描述", "状态", "优先级", "截止时间", "完成时间", "创建时间"]),
    "note": ("随心记", ["标题", "内容", "记录日期", "创建时间"]),
    "reminder": ("提醒", ["标题", "类型", "事件时间", "提醒时间", "重复", "是否启用"]),
    "countdown": ("倒计时", ["标题", "目标日期", "备注", "创建时间"]),
}

# 最多导出的行数，避免超大账户把请求拖死（单次导出 5 万条已远超个人使用量）
EXPORT_MAX_ROWS = 50000


@login_required
def export_index(request):
    """导出首页：列出各类数据当前有多少条，并提供下载入口。"""
    user = request.user
    items = [
        {"key": "expense", "label": "账目", "count": Expense.objects.filter(user=user, is_deleted=False).count(),
         "desc": "收支流水，含分类、商家、备注"},
        {"key": "task", "label": "任务", "count": Task.objects.filter(user=user, is_deleted=False).count(),
         "desc": "待办与已完成任务"},
        {"key": "note", "label": "随心记", "count": Note.objects.filter(user=user, is_deleted=False).count(),
         "desc": "想法、灵感、随笔"},
        {"key": "reminder", "label": "提醒", "count": Reminder.objects.filter(user=user).count(),
         "desc": "生日、账单、纪念日"},
        {"key": "countdown", "label": "倒计时", "count": Countdown.objects.filter(user=user).count(),
         "desc": "重要日期倒计时"},
    ]
    return render(request, "life/export.html", {"items": items})


@login_required
def export_csv(request, kind):
    if kind not in EXPORT_KINDS:
        raise Http404(f"不支持的导出类型：{kind}")

    label, headers = EXPORT_KINDS[kind]
    rows = _collect(request.user, kind)

    today = timezone.localdate()
    filename = f"lifeos-{label}-{today:%Y%m%d}.csv"

    resp = HttpResponse(content_type="text/csv; charset=utf-8-sig")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(resp)
    writer.writerow(headers)
    writer.writerows(rows)
    return resp


def _collect(user, kind):
    if kind == "expense":
        qs = (
            Expense.objects.filter(user=user, is_deleted=False)
            .select_related("category")
            .order_by("-occurred_at")[:EXPORT_MAX_ROWS]
        )
        return [
            [
                e.occurred_at.strftime("%Y-%m-%d %H:%M"),
                e.get_type_display(),
                e.amount,
                e.category.name if e.category else "",
                e.merchant,
                e.note,
                e.get_status_display(),
                e.get_source_display(),
            ]
            for e in qs
        ]

    if kind == "task":
        qs = Task.objects.filter(user=user, is_deleted=False).order_by("-created_at")[:EXPORT_MAX_ROWS]
        return [
            [
                t.title,
                t.description,
                t.get_status_display(),
                {1: "高", 2: "中", 3: "低"}.get(t.priority, t.priority),
                t.due_at.strftime("%Y-%m-%d %H:%M") if t.due_at else "",
                t.completed_at.strftime("%Y-%m-%d %H:%M") if t.completed_at else "",
                t.created_at.strftime("%Y-%m-%d %H:%M"),
            ]
            for t in qs
        ]

    if kind == "note":
        qs = Note.objects.filter(user=user, is_deleted=False).order_by("-created_at")[:EXPORT_MAX_ROWS]
        return [
            [
                n.title,
                n.raw_text,
                n.occurred_on.strftime("%Y-%m-%d") if n.occurred_on else "",
                n.created_at.strftime("%Y-%m-%d %H:%M"),
            ]
            for n in qs
        ]

    if kind == "reminder":
        qs = Reminder.objects.filter(user=user).order_by("remind_at")[:EXPORT_MAX_ROWS]
        return [
            [
                r.title,
                r.get_reminder_type_display(),
                r.event_at.strftime("%Y-%m-%d %H:%M"),
                r.remind_at.strftime("%Y-%m-%d %H:%M"),
                r.get_recurrence_rule_display(),
                "是" if r.is_enabled else "否",
            ]
            for r in qs
        ]

    # countdown
    qs = Countdown.objects.filter(user=user).order_by("target_date")[:EXPORT_MAX_ROWS]
    return [
        [
            c.title,
            c.target_date.strftime("%Y-%m-%d"),
            getattr(c, "note", "") or "",
            c.created_at.strftime("%Y-%m-%d %H:%M"),
        ]
        for c in qs
    ]
