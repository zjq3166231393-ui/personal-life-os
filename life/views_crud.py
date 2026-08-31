from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal
from math import ceil

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from common.audit import record
from common.utils import safe_next

from .constants import (
    ANOMALY_SPIKE_FACTOR,
    ANOMALY_TOPN,
    BILL_CHANGE_ALERT_RATIO,
    BUDGET_WARN_RATIO,
    CATEGORY_GROWTH_FACTOR,
    CATEGORY_SPIKE_RATIO,
    COUNTDOWN_HOME_TOPN,
    DAY_TREND_DAYS,
    DEFAULT_PERIOD,
    EXPENSE_CAT_TOPN_HOME,
    LARGE_EXPENSE_MIN,
    LARGE_EXPENSE_PCT,
    LARGE_ITEM_TOPN,
    MONTH_DIFF_ALERT_RATIO,
    MONTH_TREND_COUNT,
    OVERDUE_ALERT_COUNT,
    PAGE_SIZE,
    PERIOD_DAYS,
    RECURRING_SHARE_ALERT,
    SAVINGS_IMPROVE_RATIO,
    SAVINGS_RATE_LOW,
    SOON_DAYS,
    STREAK_MAX_DAYS,
    SUGGESTION_GEN_EVERY_N_DAYS,
    SUGGESTION_TOPN,
    TOP_CAT_CONCENTRATION_PCT,
    TOP_SPENDING_CATS,
    UPCOMING_HORIZON_DAYS,
    UPCOMING_TOPN,
    WEEK_TREND_DAYS,
)
from .currency import CURRENCY_CHOICES
from .models import (
    Account,
    Budget,
    Category,
    Expense,
    InstallmentPlan,
    Note,
    RecurringExpense,
    Reminder,
    Review,
    SavingsGoal,
    Suggestion,
    Task,
)
from .models_daily import DailyCheckin
from .services import aware_day_end, aware_day_start, net_worth_data
from .views_tag import apply_tags, parse_tag_ids, user_tags
from .views_trash import undo_redirect


def _user_queryset(model, request):
    return model.objects.filter(user=request.user, is_deleted=False)


def _parse_aware_dt(raw, fallback):
    """把表单（datetime-local）提交的字符串解析为感知时区的 datetime。

    直接把字符串赋给 DateTimeField 会让 Django 收到 naive datetime 而告警，
    在 UTC 部署时还会被按服务器时区解释而错位——与已修复的时区 bug 属同类问题。
    """
    if not raw:
        return fallback
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return timezone.make_aware(datetime.strptime(raw.strip(), fmt))
        except ValueError:
            continue
    return fallback


# ── Expense CRUD ────────────────────────────────────────────────────

@login_required
def expense_list(request):
    from datetime import datetime, timedelta
    from decimal import InvalidOperation

    from django.core.paginator import Paginator
    from django.db.models import Max, Q, Sum

    # ── 2026-08-24：recurring=fixed 时直接跳到 /recurring/（固定账单独立页面）────
    if request.GET.get("recurring") == "fixed":
        from django.shortcuts import redirect
        return redirect("recurring_list")

    qs = _user_queryset(Expense, request).select_related("category")

    # ── filters ──────────────────────────────────────────────────
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    cat_id = request.GET.get("category", "")
    typ = request.GET.get("type", "")
    amount_min = request.GET.get("amount_min", "")
    amount_max = request.GET.get("amount_max", "")
    query = request.GET.get("q", "").strip()
    # 智能时段：3天 / 1周 / 1月 / 全部。默认 1周（近 7 天）。
    period = request.GET.get("period", DEFAULT_PERIOD)
    if period not in list(PERIOD_DAYS) + ["all"]:
        period = DEFAULT_PERIOD

    today = timezone.localdate()
    if period != "all":
        days_map = PERIOD_DAYS
        period_start = today - timedelta(days=days_map[period] - 1)
        qs = qs.filter(occurred_at__gte=timezone.make_aware(datetime.combine(period_start, datetime.min.time())))

    if date_from:
        try:
            # 显式 make_aware：naive datetime 进 DateTimeField 查询会触发 RuntimeWarning，
            # 且依赖 Django 的隐式时区解释（Asia/Shanghai 无 DST，make_aware 安全）。
            dt = timezone.make_aware(datetime.strptime(date_from, "%Y-%m-%d"))
            qs = qs.filter(occurred_at__gte=dt)
        except ValueError:
            pass
    if date_to:
        try:
            from datetime import timedelta as _td
            dt = timezone.make_aware(datetime.strptime(date_to, "%Y-%m-%d")) + _td(days=1)
            qs = qs.filter(occurred_at__lt=dt)
        except ValueError:
            pass
    if cat_id and cat_id.isdigit():
        qs = qs.filter(category_id=int(cat_id))
    if typ and typ in dict(Expense.TransactionType.choices):
        qs = qs.filter(type=typ)
    if amount_min:
        try:
            qs = qs.filter(amount__gte=Decimal(amount_min))
        except InvalidOperation:
            pass
    if amount_max:
        try:
            qs = qs.filter(amount__lte=Decimal(amount_max))
        except InvalidOperation:
            pass
    if query:
        qs = qs.filter(Q(note__icontains=query) | Q(merchant__icontains=query) | Q(raw_text__icontains=query))

    # ── pagination ───────────────────────────────────────────────
    paginator = Paginator(qs, PAGE_SIZE)
    page_num = request.GET.get("page", "1")
    page_obj = paginator.get_page(page_num)

    # ── category list for filter dropdown ────────────────────────
    categories = Category.objects.filter(Q(user=request.user) | Q(user__isnull=True), type="expense", is_active=True)

    # ── 自动统计 KPI（针对当前筛选结果，不依赖手填筛选条件）────────
    # 用户进入页面默认看到 1 周数据 + 顶部 KPI 卡片，而不是一个空的筛选表单。
    all_in_period = qs
    total_amount = all_in_period.filter(type="expense").aggregate(s=Sum("amount"))["s"] or Decimal(0)
    total_count = all_in_period.count()
    income_total = all_in_period.filter(type="income").aggregate(s=Sum("amount"))["s"] or Decimal(0)
    max_single = all_in_period.filter(type="expense").aggregate(m=Max("amount"))["m"] or Decimal(0)
    days_in_period = PERIOD_DAYS.get(period, 30)
    daily_avg = total_amount / days_in_period if days_in_period else Decimal(0)

    # 分类占比（仅支出）
    cat_breakdown = []
    cat_data = all_in_period.filter(type="expense").values("category__name", "category__icon", "category__color").annotate(s=Sum("amount"))
    cat_data = sorted(cat_data, key=lambda x: x["s"], reverse=True)
    for row in cat_data:
        if row["s"] <= 0:
            continue
        cat_breakdown.append({
            "name": row["category__name"] or "未分类",
            "icon": row["category__icon"] or "📁",
            "color": row["category__color"] or "#94a3b8",
            "amount": row["s"],
            "pct": round(float(row["s"] / total_amount * 100)) if total_amount > 0 else 0,
        })

    # 对比上月同期
    if period != "all":
        last_period_start = period_start - timedelta(days=days_in_period)
        last_period_end = period_start
        last_total = Expense.objects.filter(
            user=request.user, type="expense", status="confirmed", is_deleted=False,
            occurred_at__gte=aware_day_start(last_period_start),
            occurred_at__lt=aware_day_start(last_period_end),
        ).aggregate(s=Sum("amount"))["s"] or Decimal(0)
    else:
        last_total = Decimal(0)

    return render(request, "life/expense_list.html", {
        "page_obj": page_obj,
        "categories": categories,
        "period": period,
        "kpi": {
            "total": total_amount,
            "count": total_count,
            "income": income_total,
            "max_single": max_single,
            "daily_avg": daily_avg,
            "last_total": last_total,
            "diff_pct": round(float((total_amount - last_total) / last_total * 100)) if last_total > 0 else 0,
        },
        "cat_breakdown": cat_breakdown[:EXPENSE_CAT_TOPN_HOME],
        "all_tags": user_tags(request.user),
        "filters": {
            "date_from": date_from, "date_to": date_to,
            "category": cat_id, "type": typ,
            "amount_min": amount_min, "amount_max": amount_max,
            "q": query,
        },
    })

@login_required
def expense_detail(request, pk):
    expense = get_object_or_404(Expense, pk=pk, user=request.user, is_deleted=False)
    attachments = expense.attachments.filter(is_deleted=False)
    return render(request, "life/expense_detail.html", {
        "expense": expense,
        "attachments": attachments,
    })

@login_required
def expense_edit(request, pk):
    from django.db.models import Q
    expense = get_object_or_404(Expense, pk=pk, user=request.user, is_deleted=False)
    categories = Category.objects.filter(Q(user=request.user) | Q(user__isnull=True), type="expense", is_active=True)
    # 账户选择器：启用中的账户；若本笔已关联一个已停用的账户，也一并纳入，避免编辑时丢关联
    accounts = Account.objects.filter(user=request.user, is_deleted=False, is_active=True)
    if expense.account_id and not accounts.filter(pk=expense.account_id).exists():
        accounts = Account.objects.filter(
            Q(user=request.user, is_deleted=False, is_active=True) | Q(pk=expense.account_id)
        )
    accounts = accounts.order_by("type", "name")
    if request.method == "POST":
        expense.note = request.POST.get("note", expense.note)[:500]
        expense.amount = request.POST.get("amount", expense.amount)
        expense.type = request.POST.get("type", expense.type)
        expense.occurred_at = _parse_aware_dt(request.POST.get("occurred_at"), expense.occurred_at)
        expense.merchant = request.POST.get("merchant", expense.merchant)[:200]
        expense.source = request.POST.get("source", expense.source)
        # 多币种（P1-5）：币种 + 汇率
        from decimal import Decimal as _D
        from decimal import InvalidOperation as _IO

        from .currency import CURRENCY_META

        cur = request.POST.get("currency", expense.currency)
        if cur in CURRENCY_META:
            expense.currency = cur
        raw_rate = request.POST.get("rate")
        if raw_rate not in (None, ""):
            try:
                r = _D(str(raw_rate))
                if r > 0:
                    expense.rate = r
            except (_IO, ValueError):
                pass
        cat_id = request.POST.get("category")
        if cat_id:
            expense.category_id = int(cat_id)
        # 账户：只能选自己的、且启用中的；空值或越权的 account_id 置空（不报错，避免阻断编辑）
        acc_id = request.POST.get("account")
        expense.account = Account.objects.filter(
            pk=acc_id, user=request.user, is_deleted=False, is_active=True
        ).first() if acc_id else None
        # 只有转账才有「转入账户」；非转账必须清空，避免脏数据拖累余额推算
        if expense.type == "transfer":
            tacc_id = request.POST.get("transfer_to_account")
            expense.transfer_to_account = Account.objects.filter(
                pk=tacc_id, user=request.user, is_deleted=False, is_active=True
            ).first() if tacc_id else None
        else:
            expense.transfer_to_account = None
        expense.save()
        apply_tags(expense, request.user, parse_tag_ids(request.POST))
        record(request.user, "expense.update", expense.pk, f"修改支出: {expense.display_title}")
        return redirect("expense_list")
    return render(request, "life/expense_edit.html", {
        "expense": expense,
        "categories": categories,
        "accounts": accounts,
        "all_tags": user_tags(request.user),
        "cur_tag_ids": {t.id for t in expense.tags.all()},
        "currency_choices": CURRENCY_CHOICES,
    })

@login_required
@require_POST
def expense_delete(request, pk):
    expense = get_object_or_404(Expense, pk=pk, user=request.user, is_deleted=False)
    if request.method == "POST":
        expense.is_deleted = True
        expense.deleted_at = timezone.now()
        expense.save()
        _disp = expense.display_title
        record(request.user, "expense.delete", expense.pk, f"删除支出: {_disp}")
        messages.success(request, f"已删除支出「{_disp}」— 可撤销，或在回收站恢复")
        return undo_redirect("expense_list", "expense", expense.pk)
    return render(request, "life/expense_delete.html", {"expense": expense})


# ── Task CRUD ───────────────────────────────────────────────────────

@login_required
def task_list(request):
    """任务列表（2026-08-24 重构）：
    - 默认显示「所有任务」
    - 未完成按日期分组置顶（今日 / 明日 / 本周 / 本月更早 / 无期限）
    - 已完成按月倒序列在底部，全部带 line-through
    - 状态/优先级筛选仍保留为可选项（顶部下拉）
    """
    from collections import OrderedDict
    from datetime import timedelta

    today = timezone.localdate()
    tomorrow = today + timedelta(days=1)
    week_end = today + timedelta(days=7)
    qs_all = _user_queryset(Task, request)

    # ── mini stats（始终基于全量数据）────────────────────────────
    active_qs = qs_all.filter(status__in=["todo", "in_progress"])
    mini_stats = {
        "active": active_qs.count(),
        "today": active_qs.filter(due_at__date__gte=today, due_at__date__lt=tomorrow).count(),
        "overdue": active_qs.filter(due_at__date__lt=today).count(),
        "week_completed": qs_all.filter(status="completed", completed_at__date__gte=today - timedelta(days=7)).count(),
    }

    # ── 筛选（status / priority）───────────────────────────────
    filt = request.GET.get("filter", "all")     # all / today / week / overdue / completed
    prio = request.GET.get("priority", "")      # 1 / 2 / 3 / '' (全部)

    base = qs_all
    if filt == "today":
        base = base.filter(status__in=["todo", "in_progress"], due_at__date__gte=today, due_at__date__lt=tomorrow)
    elif filt == "week":
        base = base.filter(status__in=["todo", "in_progress"], due_at__date__gte=today, due_at__date__lte=week_end)
    elif filt == "overdue":
        base = base.filter(status__in=["todo", "in_progress"], due_at__date__lt=today)
    elif filt == "completed":
        base = base.filter(status="completed")

    if prio and prio.isdigit():
        base = base.filter(priority=int(prio))

    # ── 全部 / today / week / overdue 模式：按日期分组 ───────────
    groups = []   # [(header_label, [tasks], tone), ...]
    is_overview = filt in ("all", "today", "week", "overdue", "")

    if is_overview:
        active_only = base.filter(status__in=["todo", "in_progress"])
        # 分组容器（保持顺序）
        buckets = OrderedDict([
            ("⚠ 已逾期", {"tone": "danger", "tasks": []}),
            ("🔥 今日到期", {"tone": "warning", "tasks": []}),
            ("📅 明日", {"tone": "warning-soft", "tasks": []}),
            ("🗓 本周内", {"tone": "info", "tasks": []}),
            ("📆 本月", {"tone": "info-soft", "tasks": []}),
            ("⏳ 更晚", {"tone": "brand", "tasks": []}),
            ("❓ 无截止日期", {"tone": "neutral", "tasks": []}),
        ])
        # 用 .iterator() 避免一次性 hydrate 全部对象
        for t in active_only.order_by("due_at", "-priority", "created_at"):
            if not t.due_at:
                buckets["❓ 无截止日期"]["tasks"].append(t)
                continue
            d = t.due_at.date()
            if d < today:
                buckets["⚠ 已逾期"]["tasks"].append(t)
            elif d == today:
                buckets["🔥 今日到期"]["tasks"].append(t)
            elif d == tomorrow:
                buckets["📅 明日"]["tasks"].append(t)
            elif d <= week_end:
                buckets["🗓 本周内"]["tasks"].append(t)
            elif d.year == today.year and d.month == today.month:
                buckets["📆 本月"]["tasks"].append(t)
            else:
                buckets["⏳ 更晚"]["tasks"].append(t)
        for name, data in buckets.items():
            if data["tasks"]:
                groups.append((name, data["tasks"], data["tone"]))

        # 已完成按"月份"分组，列在最后，全部 line-through
        completed_qs = base.filter(status="completed").order_by("-completed_at")
        done_buckets = OrderedDict()
        for t in completed_qs:
            mk = (t.completed_at.year if t.completed_at else t.updated_at.year, t.completed_at.month if t.completed_at else t.updated_at.month)
            label = f"✓ {mk[0]}年{mk[1]:02d}月"
            done_buckets.setdefault(label, []).append(t)
        for label, items in done_buckets.items():
            groups.append((label, items, "done"))
    else:
        # 「已完成」专属模式：平铺
        tasks = base.order_by("-priority", "due_at")
        groups.append(("", tasks, ""))

    filters = [
        ("all", "全部"), ("today", "今日"), ("week", "7天内"),
        ("overdue", "已逾期"), ("completed", "已完成"),
    ]
    return render(request, "life/task_list.html", {
        "tasks": base.order_by("-priority", "due_at"),
        "groups": groups, "filter": filt, "priority": prio,
        "filters": filters, "today": today, "mini_stats": mini_stats,
        "is_overview": is_overview,
    })


@login_required
@require_POST
def task_complete(request, pk):
    task = get_object_or_404(Task, pk=pk, user=request.user, is_deleted=False)
    task.status = "completed"
    task.completed_at = timezone.now()
    task.save()
    record(request.user, "task.complete", task.pk, f"完成任务: {task.title}")
    messages.success(request, f"已完成「{task.title}」")
    # 安全跳转：仅允许站内绝对路径，拒绝 // 协议相对与外部地址
    return safe_next(request, default="task_list", allow_referer=False)


@login_required
@require_POST
def task_postpone(request, pk):
    from datetime import timedelta
    task = get_object_or_404(Task, pk=pk, user=request.user, is_deleted=False)
    if task.due_at:
        task.due_at = task.due_at + timedelta(days=1)
        task.save()
        messages.success(request, f"「{task.title}」已延期到明天")
    return redirect("task_list")


@login_required
@require_POST
def task_cancel(request, pk):
    task = get_object_or_404(Task, pk=pk, user=request.user, is_deleted=False)
    task.status = "cancelled"
    task.save()
    messages.info(request, f"已取消「{task.title}」")
    return redirect("task_list")


@login_required
@require_POST
def task_archive(request, pk):
    task = get_object_or_404(Task, pk=pk, user=request.user, is_deleted=False)
    task.status = "archived"
    task.save()
    messages.info(request, f"已归档「{task.title}」")
    return redirect("task_list")


@login_required
@require_POST
def task_renew(request, pk):
    """Generate the next occurrence of a recurring task. Skips if already generated."""
    task = get_object_or_404(Task, pk=pk, user=request.user, is_deleted=False)
    existing = Task.objects.filter(
        user=request.user, title=task.title, is_deleted=False,
        status__in=["todo", "in_progress"],
    ).exists()
    if not existing:
        next_dt = task.next_occurrence()
        if next_dt:
            Task.objects.create(
                user=request.user, title=task.title, description=task.description,
                status="todo", priority=task.priority, due_at=next_dt,
                source="manual", parent_task=task.parent_task,
                recurrence_rule=task.recurrence_rule,
                recurrence_day=task.recurrence_day,
                recurrence_days_before=task.recurrence_days_before,
            )
    return redirect("task_list")


@login_required
def task_detail(request, pk):
    task = get_object_or_404(Task, pk=pk, user=request.user, is_deleted=False)
    return render(request, "life/task_detail.html", {"task": task})

@login_required
def task_edit(request, pk):
    task = get_object_or_404(Task, pk=pk, user=request.user, is_deleted=False)
    if request.method == "POST":
        task.title = request.POST.get("title", task.title)[:200]
        task.description = request.POST.get("description", "")[:5000]
        task.priority = int(request.POST.get("priority", task.priority))
        task.important = bool(request.POST.get("important"))
        task.urgent = bool(request.POST.get("urgent"))
        task.due_at = _parse_aware_dt(request.POST.get("due_at"), None)
        task.source = request.POST.get("source", task.source)
        task.recurrence_rule = request.POST.get("recurrence_rule", task.recurrence_rule)
        task.recurrence_day = int(request.POST.get("recurrence_day") or 0) or None
        task.recurrence_days_before = int(request.POST.get("recurrence_days_before") or 0)
        new_status = request.POST.get("status", task.status)
        if new_status == "completed" and task.status != "completed":
            task.completed_at = timezone.now()
        elif new_status != "completed":
            task.completed_at = None
        task.status = new_status
        task.save()
        apply_tags(task, request.user, parse_tag_ids(request.POST))
        if new_status == "completed":
            record(request.user, "task.complete", task.pk, f"完成任务: {task.title}")
        else:
            record(request.user, "task.update", task.pk, f"修改任务: {task.title}")
        return redirect("task_list")
    return render(request, "life/task_edit.html", {
        "task": task,
        "all_tags": user_tags(request.user),
        "cur_tag_ids": {t.id for t in task.tags.all()},
    })

@login_required
@require_POST
def task_delete(request, pk):
    task = get_object_or_404(Task, pk=pk, user=request.user, is_deleted=False)
    if request.method == "POST":
        title = task.title
        task.is_deleted = True
        task.deleted_at = timezone.now()
        task.save()
        record(request.user, "task.delete", task.pk, f"删除任务: {title}")
        messages.success(request, f"已删除任务「{title}」— 可撤销，或在回收站恢复")
        return undo_redirect("task_list", "task", task.pk)
    return render(request, "life/task_delete.html", {"task": task})


# ── 四象限任务视图（P2） ────────────────────────────────────────────
@login_required
def task_quadrant(request):
    """Eisenhower 四象限：按 重要 × 紧急 把活跃任务分进 4 格。

    只统计未完成的活跃任务（待办 / 进行中），已完成/取消/归档/删除的不进矩阵。
    """
    active = Task.objects.filter(
        user=request.user,
        is_deleted=False,
        status__in=[Task.Status.TODO, Task.Status.IN_PROGRESS],
    )
    # 视觉顺序：左列=重要，右列=不重要；上行=紧急，下行=不紧急
    quadrants = [
        {"key": "q1", "title": "重要且紧急", "hint": "立即做", "cls": "lf-quad--do",
         "tasks": active.filter(important=True, urgent=True)},
        {"key": "q3", "title": "不重要但紧急", "hint": "委托 / 尽快处理", "cls": "lf-quad--delegate",
         "tasks": active.filter(important=False, urgent=True)},
        {"key": "q2", "title": "重要不紧急", "hint": "计划做", "cls": "lf-quad--plan",
         "tasks": active.filter(important=True, urgent=False)},
        {"key": "q4", "title": "不重要不紧急", "hint": "少做 / 删除", "cls": "lf-quad--drop",
         "tasks": active.filter(important=False, urgent=False)},
    ]
    total = sum(q["tasks"].count() for q in quadrants)
    return render(request, "life/task_quadrant.html", {"quadrants": quadrants, "total": total})


@login_required
@require_POST
def task_toggle_flag(request, pk):
    """翻转单条任务的 important / urgent 标记，重定向回四象限页。

    只接受本人、未删除的任务；flag 名白名单校验，越权 pk 直接 404。
    """
    flag = request.POST.get("flag")
    if flag not in ("important", "urgent"):
        return redirect("task_quadrant")
    task = get_object_or_404(Task, pk=pk, user=request.user, is_deleted=False)
    setattr(task, flag, not getattr(task, flag))
    task.save(update_fields=[flag, "updated_at"])
    return redirect("task_quadrant")


# ── Note CRUD ───────────────────────────────────────────────────────

@login_required
def note_list(request):
    """随心记列表（2026-08-24 加下拉筛选器）。"""
    from datetime import date as _date
    from datetime import timedelta
    interval = request.GET.get("interval", "all")  # all/today/week/month
    interval_label_map = {"all": "全部", "today": "今天", "week": "本周", "month": "本月", "year": "今年"}
    if interval not in interval_label_map:
        interval = "all"
    base = _user_queryset(Note, request).select_related("user").order_by("-created_at")

    today = timezone.localdate()
    yesterday = today - timedelta(days=1)
    week_start = today - timedelta(days=today.weekday())
    month_start = _date(today.year, today.month, 1)
    year_start = _date(today.year, 1, 1)

    if interval == "today":
        base = base.filter(created_at__date__gte=today)
    elif interval == "week":
        base = base.filter(created_at__date__gte=week_start)
    elif interval == "month":
        base = base.filter(created_at__date__gte=month_start)
    elif interval == "year":
        base = base.filter(created_at__date__gte=year_start)

    notes = base
    grouped = []
    buckets: dict[str, list] = {}
    for n in notes:
        d = n.occurred_on if n.occurred_on else n.created_at.date()
        item = {
            "obj": n,
            "is_today": d == today,
            "is_yesterday": d == yesterday,
            "author": (n.user.first_name or n.user.username).strip() or n.user.username,
        }
        if d == today:
            gname = "今天"
        elif d == yesterday:
            gname = "昨天"
        elif (today - d).days < 7:
            gname = "本周早些时候"
        elif d.year == today.year and d.month == today.month:
            gname = "本月更早"
        elif d.year == today.year:
            gname = f"{d.year} 年"
        else:
            gname = f"{d.year} 年"
        buckets.setdefault(gname, []).append(item)
    order = ["今天", "昨天", "本周早些时候", "本月更早"]
    for name in order:
        if name in buckets:
            grouped.append((name, buckets.pop(name)))
    for name in sorted(buckets.keys(), reverse=True):
        grouped.append((name, buckets[name]))
    return render(request, "life/note_list.html", {
        "grouped_notes": grouped, "notes": notes,
        "interval": interval, "interval_label": interval_label_map[interval],
    })

@login_required
def note_detail(request, pk):
    note = get_object_or_404(Note, pk=pk, user=request.user, is_deleted=False)
    return render(request, "life/note_detail.html", {"note": note})

@login_required
def note_edit(request, pk):
    note = get_object_or_404(Note, pk=pk, user=request.user, is_deleted=False)
    if request.method == "POST":
        note.title = request.POST.get("title", note.title)
        note.raw_text = request.POST.get("raw_text", note.raw_text) or ""
        note.occurred_on = request.POST.get("occurred_on") or None
        note.save()
        apply_tags(note, request.user, parse_tag_ids(request.POST))
        record(request.user, "note.update", note.pk, f"修改随心记: {note.title}")
        messages.success(request, "已保存")
        return redirect("note_list")
    return render(request, "life/note_edit.html", {
        "note": note,
        "all_tags": user_tags(request.user),
        "cur_tag_ids": {t.id for t in note.tags.all()},
    })

@login_required
@require_POST
def note_delete(request, pk):
    note = get_object_or_404(Note, pk=pk, user=request.user, is_deleted=False)
    if request.method == "POST":
        title = note.title
        note.is_deleted = True
        note.deleted_at = timezone.now()
        note.save()
        record(request.user, "note.delete", note.pk, f"删除随心记: {title}")
        messages.success(request, f"已删除随心记「{title}」— 可撤销，或在回收站恢复")
        return undo_redirect("note_list", "note", note.pk)
    return render(request, "life/note_delete.html", {"note": note})


# ── Category CRUD ────────────────────────────────────────────────────

@login_required
def category_list(request):
    from django.db.models import Count, Q
    cats = list(Category.objects.filter(Q(user=request.user) | Q(user__isnull=True), is_active=True))
    cat_ids = [c.id for c in cats]
    counts = dict(
        Expense.objects.filter(category_id__in=cat_ids, is_deleted=False)
        .values_list("category_id").annotate(c=Count("id"))
    )
    cat_data = [{"obj": c, "refs": counts.get(c.id, 0), "is_system": c.user_id is None} for c in cats]
    return render(request, "life/category_list.html", {"categories": cat_data})


@login_required
def category_create(request):
    next_url = request.POST.get("next") or request.GET.get("next", "")
    if request.method == "POST":
        Category.objects.create(
            user=request.user,
            name=request.POST.get("name", "")[:50],
            type=request.POST.get("type", "expense"),
            icon=request.POST.get("icon", ""),
            color=request.POST.get("color", ""),
            is_system=False,
        )
        # 安全跳转：仅允许站内绝对路径，拒绝 // 协议相对与外部地址
        return safe_next(request, default="category_list", allow_referer=False)
    return render(request, "life/category_edit.html", {
        "category": None,
        "next": next_url,
        "next_type": request.GET.get("type", "expense"),
    })


@login_required
def category_edit(request, pk):
    # owner 校验放进查询条件：只允许本人自建分类（系统分类 user=None 不命中 → 404）
    cat = get_object_or_404(Category, pk=pk, user=request.user, is_active=True)
    if request.method == "POST":
        cat.name = request.POST.get("name", cat.name)[:50]
        cat.icon = request.POST.get("icon", cat.icon)
        cat.color = request.POST.get("color", cat.color)
        cat.save()
        return redirect("category_list")
    return render(request, "life/category_edit.html", {"category": cat})


@login_required
@require_POST
def category_deactivate(request, pk):
    # owner 校验放进查询条件：只允许本人自建分类（系统分类 user=None 不命中 → 404）
    cat = get_object_or_404(Category, pk=pk, user=request.user, is_active=True)
    refs = Expense.objects.filter(category=cat, is_deleted=False).count()
    if refs > 0:
        return render(request, "life/category_delete.html", {"category": cat, "refs": refs, "blocked": True})
    cat.is_active = False
    cat.save()
    return redirect("category_list")


# ── Budget ────────────────────────────────────────────────────────────

@login_required
def budget(request):
    from datetime import date
    from decimal import Decimal

    from django.db.models import Q, Sum

    today = timezone.localdate()
    month_start = date(today.year, today.month, 1)
    _, last_day = monthrange(today.year, today.month)
    month_end = date(today.year, today.month, last_day)

    # ── POST: save budget ──────────────────────────────────────────
    if request.method == "POST":
        total = request.POST.get("total_budget", "")
        if total:
            Budget.objects.update_or_create(
                user=request.user, category__isnull=True, month=month_start,
                defaults={"amount": Decimal(total)},
            )
        for key, val in request.POST.items():
            if key.startswith("cat_") and val:
                cat_id = int(key[4:])
                Budget.objects.update_or_create(
                    user=request.user, category_id=cat_id, month=month_start,
                    defaults={"amount": Decimal(val)},
                )
        messages.success(request, "预算已保存 ✓")
        return redirect("budget")

    # ── totals ─────────────────────────────────────────────────────
    spent_total = Expense.objects.filter(
        user=request.user, type="expense", status="confirmed", is_deleted=False,
        occurred_at__gte=aware_day_start(month_start), occurred_at__lte=aware_day_end(month_end),
    ).aggregate(s=Sum("amount"))["s"] or Decimal(0)

    budget_total = Budget.objects.filter(
        user=request.user, category__isnull=True, month=month_start,
    ).first()
    total_amount = budget_total.amount if budget_total else Decimal(0)

    remaining = total_amount - spent_total
    pct = min(int(spent_total / total_amount * 100) if total_amount > 0 else 0, 100)

    # ── per-category ───────────────────────────────────────────────
    categories = Category.objects.filter(
        Q(user=request.user) | Q(user__isnull=True), type="expense", is_active=True,
    )
    cat_budgets = {
        b.category_id: b.amount
        for b in Budget.objects.filter(user=request.user, category__isnull=False, month=month_start)
    }
    # N+1 → 单次聚合：本月各分类支出一次性算完
    spent_by_cat = {
        row["category"]: row["s"]
        for row in Expense.objects.filter(
            user=request.user, type="expense", status="confirmed", is_deleted=False,
            occurred_at__gte=aware_day_start(month_start), occurred_at__lte=aware_day_end(month_end),
        ).values("category").annotate(s=Sum("amount"))
    }

    cat_rows = []
    for c in categories:
        spent = spent_by_cat.get(c.id, Decimal(0))
        budgeted = cat_budgets.get(c.id, Decimal(0))
        rem = budgeted - spent
        cat_pct = min(int(spent / budgeted * 100) if budgeted > 0 else 0, 100)
        cat_rows.append({
            "obj": c, "spent": spent, "budget": budgeted,
            "remaining": rem, "pct": cat_pct,
            "over": spent > budgeted > 0,
            "over_amount": abs(rem) if rem < 0 else Decimal(0),
        })

    return render(request, "life/budget.html", {
        "today": today, "month_start": month_start,
        "spent_total": spent_total, "total_amount": total_amount,
        "remaining": remaining, "pct": pct,
        "over_amount": abs(remaining) if remaining < 0 else Decimal(0),
        "over_total": total_amount > 0 and spent_total > total_amount,
        "cat_rows": cat_rows,
        # ── 新增：30 天趋势 + Top 分类 + 节省建议 ──
        "trend_30": _budget_30day_trend(request.user, today),
        "top_spending_cats": sorted(cat_rows, key=lambda r: r["spent"], reverse=True)[:TOP_SPENDING_CATS],
        "savings_tip": _budget_savings_tip(request.user, today, total_amount, spent_total, cat_rows, last_month_total=_budget_last_month_total(request.user, today, month_start)),
    })


def _budget_last_month_total(user, today, month_start):
    """上月同期总支出，用于做节省/超支对比。"""
    from calendar import monthrange as _monthrange
    if today.month == 1:
        last_start = date(today.year - 1, 12, 1)
        _, last_ld = _monthrange(today.year - 1, 12)
        last_end = date(today.year - 1, 12, last_ld)
    else:
        last_start = date(today.year, today.month - 1, 1)
        _, last_ld = _monthrange(today.year, today.month - 1)
        last_end = date(today.year, today.month - 1, last_ld)
    s = Expense.objects.filter(
        user=user, type="expense", status="confirmed", is_deleted=False,
        occurred_at__date__gte=last_start, occurred_at__date__lte=last_end,
    ).aggregate(s=Sum("amount"))["s"]
    return s or Decimal(0)


def _budget_30day_trend(user, today):
    """返回近 30 天每天的支出金额（date 列表），用于 sparkline。

    N+1 → 单次按日聚合（30 次查询降为 1 次）。
    """
    start = today - timedelta(days=DAY_TREND_DAYS - 1)
    rows = Expense.objects.filter(
        user=user, type="expense", status="confirmed", is_deleted=False,
        occurred_at__date__gte=start, occurred_at__date__lte=today,
    ).values("occurred_at__date").annotate(s=Sum("amount"))
    amt_by_day = {row["occurred_at__date"]: row["s"] for row in rows}
    return [
        {"date": today - timedelta(days=i), "amount": amt_by_day.get(today - timedelta(days=i), Decimal(0))}
        for i in range(DAY_TREND_DAYS - 1, -1, -1)
    ]


def _budget_savings_tip(user, today, total_amount, spent_total, cat_rows, last_month_total):
    """基于对比生成单条省钱建议（若无可不返回 None）。"""
    # 1) 预算未设置
    if total_amount == 0:
        return {"tone": "info", "text": "尚未设置总预算。建议按「上个月总支出 × 0.9」设定，更容易达成储蓄目标。"}
    # 2) 上月有数据且本月比上月节省
    if last_month_total > 0 and spent_total < last_month_total * SAVINGS_IMPROVE_RATIO:
        saved = last_month_total - spent_total
        return {"tone": "success", "text": f"本月比上月省了 ¥{saved:.0f}，继续保持！按这个节奏月末预计结余 ¥{saved:.0f}+。"}
    # 3) 已超支
    if total_amount > 0 and spent_total > total_amount:
        over = spent_total - total_amount
        # 找到本月花得最多的分类
        top_cat = max(cat_rows, key=lambda r: r["spent"], default=None)
        if top_cat and top_cat["spent"] > 0:
            return {"tone": "danger", "text": f"已超支 ¥{over:.0f}。「{top_cat['obj'].name}」本月 ¥{top_cat['spent']:.0f} 占比较高，下个月可考虑设分类预算。"}
        return {"tone": "warning", "text": f"已超支 ¥{over:.0f}。建议本月剩余时间避免非必要支出。"}
    # 4) 使用率 > 80%
    if total_amount > 0 and spent_total / total_amount > BUDGET_WARN_RATIO:
        from calendar import monthrange as _mr
        _, last_day = _mr(today.year, today.month)
        remaining_days = last_day - today.day
        if remaining_days < 1:
            remaining_days = 1
        daily_left = (total_amount - spent_total) / remaining_days
        return {"tone": "warning", "text": f"预算执行率 {round(float(spent_total / total_amount * 100))}%。剩余 {remaining_days} 天每天可用约 ¥{daily_left:.0f}。"}
    # 5) 健康
    if last_month_total > 0 and spent_total <= last_month_total:
        return {"tone": "success", "text": f"执行率 {round(float(spent_total / total_amount * 100)) if total_amount > 0 else 0}%，与上月持平，继续保持。"}
    return None


# ── Savings Goals（储蓄目标 / 心愿单） ────────────────────────────────

@login_required
def savings_goals(request):
    """储蓄目标列表页。"""
    all_goals = list(SavingsGoal.objects.filter(user=request.user, is_active=True))
    today = _tz_localdate()
    items = []
    for g in all_goals:
        days_left = None
        overdue = False
        monthly_needed = None
        months_left = None
        if g.deadline:
            days_left = (g.deadline - today).days
            overdue = days_left < 0
            if not g.is_reached and days_left > 0 and g.remaining > 0:
                months_left = max(1, ceil(days_left / 30))
                monthly_needed = g.remaining / months_left
        items.append({
            "obj": g, "days_left": days_left, "overdue": overdue,
            "monthly_needed": monthly_needed, "months_left": months_left,
        })
    total_target = sum((g.target_amount for g in all_goals), Decimal(0))
    total_current = sum((g.current_amount for g in all_goals), Decimal(0))
    overall_pct = min(int(total_current / total_target * 100), 100) if total_target > 0 else 0
    return render(request, "life/savings.html", {
        "goals": items, "today": today,
        "total_target": total_target, "total_current": total_current,
        "overall_pct": overall_pct, "goals_count": len(all_goals),
    })


@login_required
def savings_goal_create(request):
    """创建储蓄目标（GET 渲染表单，POST 入库）。"""
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip() or "我的储蓄目标"
        try:
            target = Decimal(request.POST.get("target_amount") or "0")
        except Exception:
            target = Decimal(0)
        if target <= 0:
            target = Decimal(100)
        try:
            current = Decimal(request.POST.get("current_amount") or "0")
        except Exception:
            current = Decimal(0)
        current = max(current, Decimal(0))
        icon = (request.POST.get("icon") or "🎯")[:8] or "🎯"
        deadline = None
        deadline_raw = request.POST.get("deadline") or ""
        if deadline_raw:
            try:
                deadline = datetime.strptime(deadline_raw, "%Y-%m-%d").date()
            except ValueError:
                deadline = None
        SavingsGoal.objects.create(
            user=request.user, name=name, target_amount=target,
            current_amount=current, icon=icon, deadline=deadline,
            note=(request.POST.get("note") or "")[:500],
        )
        messages.success(request, "储蓄目标已创建 ✓")
        return redirect("savings_goals")
    return render(request, "life/savings_edit.html", {"g": None, "today": _tz_localdate().isoformat()})


@login_required
def savings_goal_edit(request, pk):
    """编辑储蓄目标（GET 渲染表单，POST 更新）。"""
    g = get_object_or_404(SavingsGoal, pk=pk, user=request.user, is_active=True)
    if request.method == "POST":
        g.name = (request.POST.get("name") or "").strip() or g.name
        ta_raw = request.POST.get("target_amount") or ""
        try:
            ta = Decimal(ta_raw)
        except Exception:
            ta = None
        if ta is not None and ta >= 0:
            g.target_amount = ta
        cur_raw = request.POST.get("current_amount") or ""
        try:
            cur = Decimal(cur_raw)
        except Exception:
            cur = None
        if cur is not None:
            g.current_amount = max(cur, Decimal(0))
        g.icon = (request.POST.get("icon") or "🎯")[:8] or "🎯"
        deadline_raw = request.POST.get("deadline") or ""
        if deadline_raw:
            try:
                parsed = datetime.strptime(deadline_raw, "%Y-%m-%d").date()
            except ValueError:
                parsed = None
            if parsed is not None:
                g.deadline = parsed
        else:
            g.deadline = None
        g.note = (request.POST.get("note") or "")[:500]
        g.save()
        messages.success(request, "已更新 ✓")
        return redirect("savings_goals")
    return render(request, "life/savings_edit.html", {"g": g, "today": _tz_localdate().isoformat()})


@login_required
@require_POST
def savings_goal_delete(request, pk):
    """软删除储蓄目标（仅 POST）。"""
    g = get_object_or_404(SavingsGoal, pk=pk, user=request.user)
    g.is_active = False
    g.save(update_fields=["is_active", "updated_at"])
    messages.success(request, "已删除目标")
    return safe_next(request, default="savings_goals", allow_referer=False)


@login_required
@require_POST
def savings_goal_adjust(request, pk):
    """存入 / 取出：adjust 当前金额，保证不为负。"""
    g = get_object_or_404(SavingsGoal, pk=pk, user=request.user, is_active=True)
    try:
        amt = Decimal(request.POST.get("amount") or "0")
    except Exception:
        amt = Decimal(0)
    if amt != 0:
        g.current_amount = max(g.current_amount + amt, Decimal(0))
        g.save(update_fields=["current_amount", "updated_at"])
        verb = "存入" if amt > 0 else "取出"
        messages.success(request, f"{verb} ¥{abs(amt):.2f} ✓")
    return safe_next(request, default="savings_goals", allow_referer=False)


# ── Envelope Budget（信封预算） ───────────────────────────────────────

@login_required
def envelopes(request):
    """信封预算：把月度分类预算当成「信封」，展示每个信封的余额与进度。

    纯展示 + 编辑层，复用已有的 Budget（category__isnull=False 即分类预算）。
    """
    from django.db.models import Q

    today = timezone.localdate()
    month_start = date(today.year, today.month, 1)
    _, last_day = monthrange(today.year, today.month)
    month_end = date(today.year, today.month, last_day)
    days_elapsed = (today - month_start).days + 1
    total_days = last_day
    days_left = total_days - days_elapsed
    month_elapsed_pct = int(days_elapsed / total_days * 100) if total_days else 0

    if request.method == "POST":
        for key, val in request.POST.items():
            if key.startswith("env_") and val:
                try:
                    cat_id = int(key[4:])
                except ValueError:
                    continue
                Budget.objects.update_or_create(
                    user=request.user, category_id=cat_id, month=month_start,
                    defaults={"amount": Decimal(val)},
                )
        # 「添加信封」：new_cat + new_amount
        new_cat = request.POST.get("new_cat")
        new_amount = request.POST.get("new_amount")
        if new_cat and new_amount:
            try:
                Budget.objects.update_or_create(
                    user=request.user, category_id=int(new_cat), month=month_start,
                    defaults={"amount": Decimal(new_amount)},
                )
            except (ValueError, TypeError):
                pass
        messages.success(request, "信封预算已保存 ✓")
        return redirect("envelopes")

    total_budget = Budget.objects.filter(user=request.user, category__isnull=True, month=month_start).first()
    total_amount = total_budget.amount if total_budget else Decimal(0)

    cat_budgets = {
        b.category_id: b.amount
        for b in Budget.objects.filter(user=request.user, category__isnull=False, month=month_start)
    }
    spent_by_cat = {
        row["category"]: row["s"]
        for row in Expense.objects.filter(
            user=request.user, type="expense", status="confirmed", is_deleted=False,
            occurred_at__gte=aware_day_start(month_start), occurred_at__lte=aware_day_end(month_end),
        ).values("category").annotate(s=Sum("amount"))
    }

    env_rows = []
    alloc_total = Decimal(0)
    spent_total = Decimal(0)
    for cat_id, amt in cat_budgets.items():
        cat = Category.objects.filter(pk=cat_id).first()
        if not cat:
            continue
        spent = spent_by_cat.get(cat_id, Decimal(0))
        rem = amt - spent
        # ── 花销节奏洞察：按当前速度预测月末是否超支 ──
        daily_avg = None
        projected = None
        projected_over = Decimal(0)
        pace = None
        if amt > 0 and days_elapsed > 0 and spent > 0:
            daily_avg = spent / days_elapsed
            projected = daily_avg * total_days
            projected_over = max(projected - amt, Decimal(0))
            spend_pct = float(spent / amt)
            month_pct = days_elapsed / total_days
            if spend_pct > month_pct + 0.1:
                pace = "fast"
            elif spend_pct < month_pct - 0.1:
                pace = "slow"
            else:
                pace = "normal"
        env_rows.append({
            "obj": cat, "budget": amt, "spent": spent, "remaining": rem,
            "pct": min(int(spent / amt * 100) if amt > 0 else 0, 100),
            "over": spent > amt > 0,
            "over_amount": abs(rem) if rem < 0 else Decimal(0),
            "days_left": days_left, "daily_avg": daily_avg,
            "projected": projected, "projected_over": projected_over, "pace": pace,
        })
        alloc_total += amt
        spent_total += spent
    # 超支的排前面，其余按使用率降序
    env_rows.sort(key=lambda r: (not r["over"], -r["pct"]))

    available = Category.objects.filter(
        Q(user=request.user) | Q(user__isnull=True), type="expense", is_active=True,
    ).exclude(pk__in=list(cat_budgets.keys()))

    unallocated = max(total_amount - alloc_total, Decimal(0))
    overall_pct = min(int(spent_total / alloc_total * 100) if alloc_total > 0 else 0, 100)

    return render(request, "life/envelopes.html", {
        "today": today, "month_start": month_start,
        "total_amount": total_amount, "alloc_total": alloc_total,
        "spent_total": spent_total, "unallocated": unallocated,
        "overall_pct": overall_pct,
        "month_elapsed_pct": month_elapsed_pct, "days_left": days_left,
        "env_rows": env_rows, "available": available,
    })


@login_required
def net_worth(request):
    """净值趋势图：账户余额按日快照，画净值 / 资产曲线。

    数据底座是 BalanceSnapshot（每日余额快照），由 snapshot_balances 命令回填；
    视图侧「懒确保」今日快照，保证未跑命令也能立即出图。纯后端分析，不干扰后续 UI。
    支持 ?range=30|90|180|365 区间切换（缺省为全部历史）。
    """
    import json

    raw = request.GET.get("range", "all")
    days = int(raw) if raw.isdigit() else None
    data = net_worth_data(request.user, days=days)
    nw_json = json.dumps({
        "labels": data["labels"],
        "series": data["series"],
        "has_data": data["has_data"],
        "accounts": [
            {"name": a["obj"].name, "balance": float(a["balance"]), "pct": a["pct"], "color": a["color"]}
            for a in data["accounts"]
        ],
    }, ensure_ascii=False)
    return render(request, "life/net_worth.html", {
        "today": timezone.localdate(),
        "range": raw,
        "nw_json": nw_json,
        "current": data["current"],
        "first": data["first"],
        "change": data["change"],
        "change_pct": data["change_pct"],
        "change_30": data["change_30"],
        "accounts": data["accounts"],
        "has_data": data["has_data"],
    })


# ── Recurring Expense CRUD ───────────────────────────────────────────

@login_required
def recurring_list(request):
    from django.db.models import Q
    items = RecurringExpense.objects.filter(user=request.user).select_related("category")
    categories = Category.objects.filter(Q(user=request.user) | Q(user__isnull=True), type="expense", is_active=True)
    return render(request, "life/recurring_list.html", {"items": items, "categories": categories})


@login_required
def recurring_create(request):
    if request.method == "POST":
        RecurringExpense.objects.create(
            user=request.user,
            name=request.POST.get("name", "")[:200],
            category_id=int(request.POST.get("category")) if request.POST.get("category") else None,
            amount=request.POST.get("amount", "0"),
            frequency=request.POST.get("frequency", "monthly"),
            due_day=int(request.POST.get("due_day", "1")),
            start_date=request.POST.get("start_date", timezone.localdate().isoformat()),
            remind_days_before=int(request.POST.get("remind_days_before", "3")),
            auto_post=request.POST.get("auto_post") == "on",
        )
        return redirect("recurring_list")
    from django.db.models import Q
    categories = Category.objects.filter(Q(user=request.user) | Q(user__isnull=True), type="expense", is_active=True)
    return render(request, "life/recurring_edit.html", {"item": None, "categories": categories})


@login_required
def recurring_edit(request, pk):
    item = get_object_or_404(RecurringExpense, pk=pk, user=request.user)
    if request.method == "POST":
        item.name = request.POST.get("name", item.name)[:200]
        item.amount = request.POST.get("amount", item.amount)
        item.frequency = request.POST.get("frequency", item.frequency)
        item.due_day = int(request.POST.get("due_day", item.due_day))
        item.start_date = request.POST.get("start_date", item.start_date)
        item.end_date = request.POST.get("end_date") or None
        item.remind_days_before = int(request.POST.get("remind_days_before", item.remind_days_before))
        item.auto_post = request.POST.get("auto_post") == "on"
        cat_id = request.POST.get("category")
        item.category_id = int(cat_id) if cat_id else None
        item.save()
        return redirect("recurring_list")
    from django.db.models import Q
    categories = Category.objects.filter(Q(user=request.user) | Q(user__isnull=True), type="expense", is_active=True)
    return render(request, "life/recurring_edit.html", {"item": item, "categories": categories})


@login_required
@require_POST
def recurring_deactivate(request, pk):
    item = get_object_or_404(RecurringExpense, pk=pk, user=request.user)
    if request.method == "POST":
        item.is_active = False
        item.save()
        return redirect("recurring_list")
    return render(request, "life/recurring_delete.html", {"item": item})


# ── Installment Plan CRUD ────────────────────────────────────────────

@login_required
def installment_list(request):
    plans = InstallmentPlan.objects.filter(user=request.user).select_related("category")
    return render(request, "life/installment_list.html", {"plans": plans})


@login_required
def installment_create(request):

    from django.db.models import Q
    if request.method == "POST":
        InstallmentPlan.objects.create(
            user=request.user,
            name=request.POST.get("name", "")[:200],
            category_id=int(request.POST.get("category")) if request.POST.get("category") else None,
            total_amount=request.POST.get("total_amount", "0"),
            installment_amount=request.POST.get("installment_amount", "0"),
            total_periods=int(request.POST.get("total_periods", "1")),
            next_due_date=request.POST.get("next_due_date", timezone.localdate().isoformat()),
        )
        return redirect("installment_list")
    categories = Category.objects.filter(Q(user=request.user) | Q(user__isnull=True), type="expense", is_active=True)
    return render(request, "life/installment_edit.html", {"plan": None, "categories": categories})


@login_required
def installment_edit(request, pk):
    plan = get_object_or_404(InstallmentPlan, pk=pk, user=request.user)
    if request.method == "POST":
        plan.name = request.POST.get("name", plan.name)[:200]
        plan.total_amount = request.POST.get("total_amount", plan.total_amount)
        plan.installment_amount = request.POST.get("installment_amount", plan.installment_amount)
        plan.total_periods = int(request.POST.get("total_periods", plan.total_periods))
        plan.next_due_date = request.POST.get("next_due_date", plan.next_due_date)
        cat_id = request.POST.get("category")
        plan.category_id = int(cat_id) if cat_id else None
        plan.save()
        return redirect("installment_list")
    from django.db.models import Q
    categories = Category.objects.filter(Q(user=request.user) | Q(user__isnull=True), type="expense", is_active=True)
    return render(request, "life/installment_edit.html", {"plan": plan, "categories": categories})


@login_required
@require_POST
def installment_pay(request, pk):
    plan = get_object_or_404(InstallmentPlan, pk=pk, user=request.user)
    from datetime import date
    error = None
    if request.method == "POST":
        if plan.status != "active":
            error = "该计划已结束。"
        elif plan.paid_periods >= plan.total_periods:
            error = "所有期数已还清。"
        else:
            plan.paid_periods += 1
            if plan.paid_periods >= plan.total_periods:
                plan.status = "completed"
            # Advance next_due_date by roughly 1 month
            nd = plan.next_due_date
            plan.next_due_date = date(nd.year + (nd.month // 12), ((nd.month % 12) + 1), min(nd.day, 28))
            plan.save()
            # Also create an Expense record for this payment
            Expense.objects.create(
                user=request.user, category=plan.category, type="expense",
                amount=plan.installment_amount, occurred_at=timezone.now(),
                note=f"分期还款: {plan.name} (第{plan.paid_periods}/{plan.total_periods}期)",
                source="manual", status="confirmed",
            )
            return redirect("installment_list")
    return render(request, "life/installment_pay.html", {"plan": plan, "error": error})


# ── Dashboard ────────────────────────────────────────────────────────

@login_required
def dashboard(request):
    """财务看板（2026-08-24 增强）：
    - ?year=YYYY&month=1-12 时间筛选（下拉式）
    - 支出/收入/结余 3 张卡片点击后跳到 expense_list 并预填过滤
    """
    from collections import defaultdict
    from datetime import date, timedelta
    from decimal import Decimal

    from django.db.models import F, Q, Sum

    today = timezone.localdate()

    # ── 年/月筛选参数 ───────────────────────────────────────
    try:
        sel_year = int(request.GET.get("year") or today.year)
    except ValueError:
        sel_year = today.year
    try:
        sel_month = int(request.GET.get("month") or today.month)
    except ValueError:
        sel_month = today.month
    if sel_month < 1: sel_year -= 1; sel_month = 12
    if sel_month > 12: sel_year += 1; sel_month = 1
    _, last_day = monthrange(sel_year, sel_month)
    month_start = date(sel_year, sel_month, 1)
    month_end = date(sel_year, sel_month, last_day)
    is_current = (sel_year == today.year and sel_month == today.month)

    # ── 下拉筛选可选范围（近 3 年 + 1~12 月）──────────────────────
    year_range = range(today.year - 2, today.year + 1)
    month_range = range(1, 13)

    # ── monthly totals ─────────────────────────────────────────────
    base = Expense.objects.filter(user=request.user, is_deleted=False, status="confirmed").select_related("category")
    month_qs = base.filter(occurred_at__gte=aware_day_start(month_start),
                           occurred_at__lte=aware_day_end(month_end))

    total_expense = month_qs.filter(type="expense").aggregate(s=Sum("amount"))["s"] or Decimal(0)
    total_income = month_qs.filter(type="income").aggregate(s=Sum("amount"))["s"] or Decimal(0)
    balance = total_income - total_expense

    # 上月同期数据（用于对比）
    if sel_month == 1:
        last_month_start = date(sel_year - 1, 12, 1)
        last_month_end = date(sel_year - 1, 12, 31)
    else:
        last_month_start = date(sel_year, sel_month - 1, 1)
        _, last_ld = monthrange(last_month_start.year, last_month_start.month)
        last_month_end = date(last_month_start.year, last_month_start.month, last_ld)
    last_month_total = base.filter(type="expense",
                                   occurred_at__gte=aware_day_start(last_month_start),
                                   occurred_at__lte=aware_day_end(last_month_end)).aggregate(s=Sum("amount"))["s"] or Decimal(0)

    # ── category breakdown ─────────────────────────────────────────
    cat_spent = defaultdict(Decimal)
    for row in month_qs.filter(type="expense").values("category__name", "category__icon", "category__color").annotate(s=Sum("amount")):
        cat_spent[row["category__name"] or "未分类"] = row["s"]
    cat_pct = []
    for name, amt in sorted(cat_spent.items(), key=lambda x: x[1], reverse=True):
        cat_pct.append({"name": name, "amount": amt, "pct": round(amt / total_expense * 100) if total_expense > 0 else 0})

    # ── daily trend（N+1 → 单次按日聚合）──────────────────────────────
    daily_agg = {
        row["occurred_at__date"]: row["s"]
        for row in base.filter(type="expense",
                               occurred_at__gte=aware_day_start(month_start),
                               occurred_at__lte=aware_day_end(month_end))
        .values("occurred_at__date").annotate(s=Sum("amount"))
    }
    daily = []
    for d in range(1, last_day + 1):
        day = date(sel_year, sel_month, d)
        daily.append({"day": d, "amount": daily_agg.get(day, Decimal(0)) or Decimal(0)})

    # ── recurring total ────────────────────────────────────────────
    rec_total = RecurringExpense.objects.filter(user=request.user, is_active=True).aggregate(s=Sum("amount"))["s"] or Decimal(0)

    # ── upcoming bills (recurring + installment) ───────────────────
    upcoming = []
    for r in RecurringExpense.objects.filter(user=request.user, is_active=True):
        upcoming.append({"name": r.name, "amount": r.amount, "date": date(sel_year, sel_month, r.due_day) if r.due_day >= today.day else date(sel_year, sel_month + 1 if sel_month < 12 else 1, r.due_day), "type": "固定"})
    for p in InstallmentPlan.objects.filter(user=request.user, status="active"):
        upcoming.append({"name": p.name, "amount": p.installment_amount, "date": p.next_due_date, "type": "分期"})
    upcoming.sort(key=lambda x: x["date"])

    # ── budget rate ────────────────────────────────────────────────
    budget_total = Budget.objects.filter(user=request.user, category__isnull=True, month=month_start).first()
    budget_amount = budget_total.amount if budget_total else Decimal(0)
    budget_pct = min(int(total_expense / budget_amount * 100) if budget_amount > 0 else 0, 100)

    # ── monthly trend (last 6 months from selected month) ────────────
    import json
    months = []
    for i in range(MONTH_TREND_COUNT - 1, -1, -1):
        m = sel_month - i
        y = sel_year
        if m <= 0:
            m += 12
            y -= 1
        months.append((y, m))
    monthly_labels = [f"{m}月" for (_y, m) in months]
    span_start = date(months[0][0], months[0][1], 1)
    _, sp_ld = monthrange(months[-1][0], months[-1][1])
    span_end = date(months[-1][0], months[-1][1], sp_ld)

    def _sum_by_month(typ):
        out = {}
        for row in base.filter(type=typ, occurred_at__gte=aware_day_start(span_start),
                               occurred_at__lte=aware_day_end(span_end)) \
                .values("occurred_at__year", "occurred_at__month").annotate(s=Sum("amount")):
            out[(row["occurred_at__year"], row["occurred_at__month"])] = row["s"] or Decimal(0)
        return out
    exp_by_month = _sum_by_month("expense")
    inc_by_month = _sum_by_month("income")
    # 12 次查询 → 2 次 GROUP BY
    monthly_expense = [float(exp_by_month.get((y, m), Decimal(0))) for (y, m) in months]
    monthly_income = [float(inc_by_month.get((y, m), Decimal(0))) for (y, m) in months]

    chart_data = json.dumps({
        "monthlyLabels": monthly_labels,
        "monthlyExpense": monthly_expense,
        "monthlyIncome": monthly_income,
        "dailyLabels": [d["day"] for d in daily],
        "dailyAmounts": [float(d["amount"]) for d in daily],
        "catLabels": [c["name"] for c in cat_pct],
        "catAmounts": [float(c["amount"]) for c in cat_pct],
        "catColors": ["#f97316","#3b82f6","#8b5cf6","#06b6d4","#ec4899","#6b7280","#22c55e","#eab308"][:len(cat_pct)],
        "budgetPct": budget_pct,
        "recurringPct": round(float(rec_total / total_expense * 100) if total_expense > 0 else 0),
    })
    # 防止用户可控的分类名含 </script> 跳出 <script> 块（存储型 XSS）。
    # 与 Django json_script 同源：对 < > & 做 \u 转义，JSON.parse 可无损还原。
    chart_data = chart_data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")

    # ── month-end prediction ─────────────────────────────────────
    # 当查询月份 ≠ 当前月时：跳过预测（未来/历史月份无法预测）
    if is_current:
        days_passed = today.day
        days_remaining = last_day - today.day
        daily_avg = total_expense / days_passed if days_passed > 0 else Decimal(0)
        predicted_remaining = daily_avg * days_remaining
        predicted_total = total_expense + predicted_remaining
    else:
        days_passed = last_day
        days_remaining = 0
        daily_avg = total_expense / last_day if last_day > 0 else Decimal(0)
        predicted_remaining = Decimal(0)
        predicted_total = total_expense

    # Identify potential one-time large expenses (> 3x daily avg)
    large_items = []
    threshold = daily_avg * 3 if daily_avg > 0 else Decimal(999999)
    for e in month_qs.filter(type="expense", amount__gte=threshold).order_by("-amount")[:LARGE_ITEM_TOPN]:
        large_items.append({"note": e.display_title, "amount": e.amount})
    # Exclude large items for a conservative estimate
    excluded = sum(item["amount"] for item in large_items)
    conservative_total = predicted_total - excluded if excluded else predicted_total
    predicted_extra = predicted_total - total_expense

    # ── anomaly detection（已优化：原逐分类/逐日 N+1 改为单次聚合）─────────
    anomalies = []
    categories = Category.objects.filter(Q(user=request.user) | Q(user__isnull=True), type="expense", is_active=True)

    # 1. 单笔异常：本月该分类某笔 > 3x 分类均值。一次性取出本月支出后在内存分组，
    #    避免「分类数 × (均值查询 + 明细查询)」的 N+1。
    month_exps = list(
        month_qs.filter(type="expense").exclude(amount=0)
        .values("category", "amount", "note", "merchant", "occurred_at")
    )
    from collections import defaultdict as _dd
    cat_items = _dd(list)
    for e in month_exps:
        cat_items[e["category"]].append(e)

    for c in categories:
        items = cat_items.get(c.id)
        if not items:
            continue
        avg = sum((x["amount"] or Decimal(0)) for x in items) / len(items)
        if avg > 0:
            for e in items:
                if e["amount"] >= avg * ANOMALY_SPIKE_FACTOR:
                    note = e["note"] or e["merchant"] or "未命名"
                    anomalies.append({
                        "type": "单笔异常",
                        "detail": f"{c.name}: {note} ¥{e['amount']}（分类均值 ¥{avg:.0f}）",
                        "date": e["occurred_at"].date(),
                    })

    # 2. 当日暴增：今天 > 3x 30 日均（保留原逻辑，已是单次聚合）
    daily_30 = base.filter(type="expense", occurred_at__gte=aware_day_start(today - timedelta(days=DAY_TREND_DAYS))).aggregate(s=Sum("amount"))["s"] or Decimal(0)
    avg_30 = daily_30 / 30 if daily_30 > 0 else Decimal(0)
    today_spent = base.filter(type="expense", occurred_at__date=today).aggregate(s=Sum("amount"))["s"] or Decimal(0)
    if avg_30 > 0 and today_spent > avg_30 * ANOMALY_SPIKE_FACTOR:
        anomalies.append({"type": "当日暴增", "detail": f"今天 ¥{today_spent:.0f}，30日均值 ¥{avg_30:.0f}", "date": today})

    # 3. 分类增长：本月 > 2x 上月。两次 GROUP BY 替代「2 × 分类数」次查询
    last_month_start = date(today.year, today.month - 1, 1) if today.month > 1 else date(today.year - 1, 12, 1)
    _, last_ld = monthrange(last_month_start.year, last_month_start.month)
    last_month_end = date(last_month_start.year, last_month_start.month, last_ld)
    this_m_by_cat = {
        row["category"]: row["s"]
        for row in month_qs.filter(type="expense").values("category").annotate(s=Sum("amount"))
    }
    last_m_by_cat = {
        row["category"]: row["s"]
        for row in base.filter(type="expense",
                               occurred_at__gte=aware_day_start(last_month_start),
                               occurred_at__lte=aware_day_end(last_month_end))
        .values("category").annotate(s=Sum("amount"))
    }
    for c in categories:
        # 同样统一走 Decimal 域，避免与 constants.py 的金额类常量混算
        this_m = this_m_by_cat.get(c.id) or Decimal(0)
        last_m = last_m_by_cat.get(c.id) or Decimal(0)
        if last_m > 0 and this_m > last_m * CATEGORY_GROWTH_FACTOR:
            anomalies.append({"type": "分类增长", "detail": f"{c.name}: 本月 ¥{this_m:.0f} vs 上月 ¥{last_m:.0f}", "date": today})

    # 4. Recurring bill amount change > 20%
    for r in RecurringExpense.objects.filter(user=request.user, is_active=True):
        recent = Expense.objects.filter(user=request.user, note__icontains=r.name, type="expense", status="confirmed").order_by("-occurred_at").first()
        if recent and recent.amount > 0 and r.amount > 0 and abs(recent.amount - r.amount) / r.amount > BILL_CHANGE_ALERT_RATIO:
            anomalies.append({"type": "账单异常", "detail": f"{r.name}: 实际 ¥{recent.amount} vs 预期 ¥{r.amount}", "date": recent.occurred_at.date()})

    anomalies.sort(key=lambda x: x["date"], reverse=True)

    # ── task analytics ───────────────────────────────────────────
    tasks_all = Task.objects.filter(user=request.user, is_deleted=False)
    # This week (Mon-Sun)
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    week_completed = tasks_all.filter(status="completed", completed_at__date__gte=week_start, completed_at__date__lte=week_end).count()
    week_total = max(week_completed + tasks_all.filter(status__in=["todo", "in_progress"], created_at__date__lte=week_end).count(), 1)
    week_rate = round(week_completed / week_total * 100) if week_total > 0 else 0

    # 本周新建任务数（衡量「规划/输入」活跃度，与完成率互补）
    week_created = tasks_all.filter(created_at__date__gte=week_start, created_at__date__lte=week_end).count()
    # 被推迟的任务数：仍在进行中、但创建后超过 1 小时才被改动（视为「推迟/拖延」信号）
    postpone_count = tasks_all.filter(
        status__in=["todo", "in_progress"], updated_at__gt=F("created_at") + timedelta(hours=1)
    ).count()

    high_priority_done = tasks_all.filter(status="completed", priority=1).count()
    high_priority_total = max(tasks_all.filter(priority=1).count(), 1)
    high_rate = round(high_priority_done / high_priority_total * 100)

    overdue_count = tasks_all.filter(status__in=["todo", "in_progress"], due_at__date__lt=today).count()

    # Consecutive days (walk backwards from yesterday)
    streak = 0
    d = today - timedelta(days=1)
    while d >= today - timedelta(days=STREAK_MAX_DAYS):
        if tasks_all.filter(completed_at__date=d).exists():
            streak += 1
            d -= timedelta(days=1)
        else:
            break

    # Today's most important task completed?
    top_task = tasks_all.filter(status__in=["todo", "in_progress"]).order_by("-priority", "due_at").first()
    top_done = top_task is None  # no tasks = all done

    # ── 7-day task completion & check-in trend (for new dashboard summary) ──
    from .models_daily import DailyCheckin
    last7 = [today - timedelta(days=i) for i in range(WEEK_TREND_DAYS - 1, -1, -1)]
    task_trend = [
        {
            "date": d,
            "completed": tasks_all.filter(completed_at__date=d).count(),
            "total": tasks_all.filter(created_at__date__lte=d).exclude(status="archived").filter(
                # capture tasks active on this date: created on/before, not yet archived, not deleted
                created_at__date__lte=d,
            ).count(),
        }
        for d in last7
    ]
    # Check-in trend for last 7 days (done_dates is a JSONField of dates)
    checkin_trend = []
    daily_active = DailyCheckin.objects.filter(user=request.user, is_active=True)
    active_total = daily_active.count()
    for d in last7:
        # Count of habits where today's date appears in done_dates
        cnt = sum(1 for c in daily_active if d.isoformat() in (c.done_dates or []))
        checkin_trend.append({"date": d, "done": cnt, "total": active_total})

    # ── Countdowns on home (max 3, sorted: pinned first, then soonest) ──
    from life.models import Countdown as _CD
    cd_qs = _CD.objects.filter(user=request.user, is_active=True, show_on_home=True)
    cd_list = []
    for cd in cd_qs.order_by("-pinned", "target_date")[:COUNTDOWN_HOME_TOPN]:
        delta = (cd.next_occurrence(today) - today).days
        cd_list.append({
            "obj": cd,
            "emoji": cd.emoji or "🎯",
            "title": cd.title,
            "days": delta,
            "pinned": cd.pinned,
            "pk": cd.pk,
            "direction": cd.direction,
            "color": cd.color or "#5b8def",
        })
    cd_total = _CD.objects.filter(user=request.user, is_active=True).count()

    # ── suggestions generation ───────────────────────────────────
    suggest_display = Suggestion.objects.filter(user=request.user, generated_at=today).exclude(feedback__in=["not_useful", "dismissed"])[:SUGGESTION_TOPN]
    if not suggest_display.exists() and today.day % SUGGESTION_GEN_EVERY_N_DAYS == 0:  # Generate every N days
        # Budget warning
        if budget_amount > 0 and total_expense > budget_amount * BUDGET_WARN_RATIO:
            Suggestion.objects.create(user=request.user, title="预算即将超支", evidence=f"本月已花 ¥{total_expense:.0f}，预算 ¥{budget_amount}，执行率 {budget_pct}%", category="budget")
        # Category spike vs 3-month average
        # 全段统一走 Decimal 域：与 constants.py 中的金额类常量（CATEGORY_SPIKE_RATIO 等）
        # 保持一致。此前此处用 float，float * Decimal 会抛 TypeError 导致看板 500。
        for c in categories:
            cat_month = month_qs.filter(type="expense", category=c).aggregate(s=Sum("amount"))["s"] or Decimal(0)
            if cat_month > 0:
                three_mo = base.filter(type="expense", category=c, occurred_at__gte=aware_day_start(today - timedelta(days=90))).aggregate(s=Sum("amount"))["s"] or Decimal(0)
                three_avg = three_mo / 3
                if three_avg > 0 and cat_month > three_avg * CATEGORY_SPIKE_RATIO:
                    pct = round((cat_month - three_avg) / three_avg * 100)
                    Suggestion.objects.create(user=request.user, title=f"{c.name}支出偏高", evidence=f"本月 ¥{cat_month:.0f}，比近3月月均 ¥{three_avg:.0f} 高 {pct}%", category="spending")
        # Overdue tasks
        if overdue_count > OVERDUE_ALERT_COUNT:
            Suggestion.objects.create(user=request.user, title=f"有 {overdue_count} 个逾期任务", evidence=f"逾期任务数: {overdue_count}，建议今日优先处理最高优先级任务", category="task")
        suggest_display = Suggestion.objects.filter(user=request.user, generated_at=today)[:SUGGESTION_TOPN]

    # ── 生活建议（每视图重新计算，命中率高）──────────────────
    life_suggestions = []
    # 1) 大额单笔：本月任一单笔 > 200 或 > 月支出的 20%
    threshold_large = max(LARGE_EXPENSE_MIN, total_expense * LARGE_EXPENSE_PCT) if total_expense > 0 else LARGE_EXPENSE_MIN
    for e in month_qs.filter(type="expense", amount__gte=threshold_large).order_by("-amount")[:LARGE_ITEM_TOPN]:
        life_suggestions.append({
            "icon": "wallet",
            "title": f"本月大额支出：{e.display_title}",
            "detail": f"¥{e.amount:.0f}，{e.occurred_at.strftime('%m/%d')} {e.category.name if e.category else '未分类'}。占总支出 {round(e.amount / total_expense * 100) if total_expense else 0}%",
            "tone": "warning",
        })
    # 2) 固定账单占比高
    if total_expense > 0 and rec_total / total_expense > RECURRING_SHARE_ALERT:
        life_suggestions.append({
            "icon": "calendar",
            "title": f"固定支出占比 {round(float(rec_total / total_expense * 100))}%",
            "detail": f"月固定支出 ¥{rec_total:.0f} 占总支出 ¥{total_expense:.0f} 超过一半，可考虑精简订阅/换套餐。",
            "tone": "info",
        })
    # 3) 月结余为负或偏低
    if balance < 0:
        life_suggestions.append({
            "icon": "alert",
            "title": f"本月结余为负：¥{balance:.0f}",
            "detail": f"支出 ¥{total_expense:.0f}，收入 ¥{total_income:.0f}。建议先砍掉非必要订阅。",
            "tone": "danger",
        })
    elif total_income > 0 and (total_income - total_expense) / total_income < SAVINGS_RATE_LOW:
        life_suggestions.append({
            "icon": "piggy",
            "title": "储蓄率偏低",
            "detail": f"本月储蓄率 {round(float((total_income - total_expense) / total_income * 100)) if total_income else 0}%，建议把月收入的 20% 留作储蓄。",
            "tone": "warning",
        })
    # 4) 与上月对比
    if last_month_total > 0:
        diff = total_expense - last_month_total
        if diff > last_month_total * MONTH_DIFF_ALERT_RATIO:
            life_suggestions.append({
                "icon": "trending",
                "title": f"支出比上月涨 {round(float(diff / last_month_total * 100))}%",
                "detail": f"本月 ¥{total_expense:.0f} vs 上月 ¥{last_month_total:.0f}，查看分类找原因。",
                "tone": "warning",
            })
        elif diff < -last_month_total * MONTH_DIFF_ALERT_RATIO:
            life_suggestions.append({
                "icon": "trending",
                "title": f"支出比上月省 {round(float(-diff / last_month_total * 100))}%",
                "detail": f"本月 ¥{total_expense:.0f} vs 上月 ¥{last_month_total:.0f}，保持节奏！",
                "tone": "success",
            })
    # 5) 分类单笔最大
    if cat_pct:
        top_cat = cat_pct[0]
        if top_cat["pct"] >= TOP_CAT_CONCENTRATION_PCT and total_expense > 0:
            life_suggestions.append({
                "icon": "tag",
                "title": f"{top_cat['name']}占比 {top_cat['pct']}%",
                "detail": f"¥{top_cat['amount']:.0f} / ¥{total_expense:.0f}，集中度过高，看下是否需要分配。",
                "tone": "info",
            })

    # 限前 5 条
    life_suggestions = life_suggestions[:SUGGESTION_TOPN]

    return render(request, "life/dashboard.html", {
        "today": today, "month_start": month_start,
        "sel_year": sel_year, "sel_month": sel_month, "is_current": is_current,
        "year_range": year_range, "month_range": month_range,
        "total_expense": total_expense, "total_income": total_income,
        "balance": balance, "cat_pct": cat_pct, "daily": daily,
        "rec_total": rec_total, "upcoming": upcoming[:UPCOMING_TOPN],
        "budget_amount": budget_amount, "budget_pct": budget_pct,
        "chart_data": chart_data,
        "predicted_total": predicted_total,
        "predicted_extra": predicted_extra,
        "daily_avg": daily_avg,
        "days_passed": days_passed,
        "days_remaining": days_remaining,
        "large_items": large_items,
        "conservative_total": conservative_total,
        "anomalies": anomalies[:ANOMALY_TOPN],
        "week_rate": week_rate, "high_rate": high_rate,
        "overdue_count": overdue_count, "streak": streak,
        "top_task": top_task, "top_done": top_done,
        "week_completed": week_completed,
        "week_created": week_created, "postpone_count": postpone_count,
        "suggestions": suggest_display,
        "life_suggestions": life_suggestions,
        "last_month_total": last_month_total,
        "task_trend": task_trend,
        "checkin_trend": checkin_trend,
        "cd_list": cd_list,
        "cd_total": cd_total,
        # ── 跳转链接（供模板里 KPI/结余/分类名点击） ──
        "expense_list_url": "/expenses/",
    })


@login_required
def reports(request):
    """报表增强（2026-08-30）：
    - 时间预设：本月 / 上月 / 今年 / 去年 / 全部
    - 自定义区间（start/end，YYYY-MM-DD）
    - 本期收入 / 支出 / 结余
    - 环比（上一周期）/ 同比（去年同期）增幅
    - 分类支出 / 收入构成
    - 每日趋势（Chart.js）
    - 打印 / 导出 PDF（window.print + 打印样式）
    """
    import json
    from datetime import date, timedelta
    from decimal import Decimal

    from django.db.models import Sum

    today = timezone.localdate()
    base = Expense.objects.filter(user=request.user, is_deleted=False, status="confirmed")

    # ── 解析区间 ───────────────────────────────────────────────
    preset = request.GET.get("preset", "month")

    def _parse(d):
        try:
            return date.fromisoformat(d)
        except (ValueError, TypeError):
            return None

    custom_start = _parse(request.GET.get("start"))
    custom_end = _parse(request.GET.get("end"))

    if custom_start and custom_end and custom_start <= custom_end:
        period_start, period_end = custom_start, custom_end
        preset = "custom"
    elif preset == "last_month":
        y, m = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
        period_start = date(y, m, 1)
        _, ld = monthrange(y, m)
        period_end = date(y, m, ld)
    elif preset == "year":
        period_start = date(today.year, 1, 1)
        period_end = date(today.year, 12, 31)
    elif preset == "last_year":
        period_start = date(today.year - 1, 1, 1)
        period_end = date(today.year - 1, 12, 31)
    elif preset == "all":
        first = base.order_by("occurred_at").first()
        period_start = first.occurred_at.date() if first else today
        period_end = today
    else:  # month（默认）
        period_start = date(today.year, today.month, 1)
        _, ld = monthrange(today.year, today.month)
        period_end = date(today.year, today.month, ld)

    is_all = (preset == "all")

    # ── 环比 / 同比 区间 ───────────────────────────────────────
    span_days = (period_end - period_start).days + 1
    if is_all:
        prev_start = prev_end = yoy_start = yoy_end = None
    else:
        prev_end = period_start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=span_days - 1)
        yoy_end = period_end - timedelta(days=365)
        yoy_start = period_start - timedelta(days=365)

    # ── 总额（本期 / 环比 / 同比）─────────────────────────────
    def _totals(s, e):
        qs = base.filter(occurred_at__gte=aware_day_start(s), occurred_at__lte=aware_day_end(e))
        expense = qs.filter(type="expense").aggregate(s=Sum("amount"))["s"] or Decimal(0)
        income = qs.filter(type="income").aggregate(s=Sum("amount"))["s"] or Decimal(0)
        return expense, income, income - expense

    cur_e, cur_i, cur_b = _totals(period_start, period_end)
    if prev_start:
        prev_e, prev_i, prev_b = _totals(prev_start, prev_end)
    else:
        prev_e = prev_i = prev_b = Decimal(0)
    if yoy_start:
        yoy_e, yoy_i, yoy_b = _totals(yoy_start, yoy_end)
    else:
        yoy_e = yoy_i = yoy_b = Decimal(0)

    def _pct(cur, ref):
        if ref is None or ref == 0:
            return None
        return round((cur - ref) / ref * 100, 1)

    delta_expense_mom = _pct(cur_e, prev_e) if not is_all else None
    delta_income_mom = _pct(cur_i, prev_i) if not is_all else None
    delta_balance_mom = _pct(cur_b, prev_b) if not is_all else None
    delta_expense_yoy = _pct(cur_e, yoy_e) if not is_all else None
    delta_income_yoy = _pct(cur_i, yoy_i) if not is_all else None
    delta_balance_yoy = _pct(cur_b, yoy_b) if not is_all else None

    savings_rate = round(cur_b / cur_i * 100, 1) if cur_i > 0 else None

    # ── 分类构成（支出 / 收入）───────────────────────────────
    def _cat_breakdown(typ):
        rows = base.filter(
            type=typ,
            occurred_at__gte=aware_day_start(period_start),
            occurred_at__lte=aware_day_end(period_end),
        ).values("category__name", "category__icon", "category__color").annotate(s=Sum("amount"))
        total = sum((r["s"] or Decimal(0)) for r in rows)
        out = []
        for r in sorted(rows, key=lambda x: x["s"] or Decimal(0), reverse=True):
            amt = r["s"] or Decimal(0)
            out.append({
                "name": r["category__name"] or "未分类",
                "icon": r["category__icon"] or "💸",
                "color": r["category__color"] or "#6b7280",
                "amount": amt,
                "pct": round(amt / total * 100) if total > 0 else 0,
            })
        return out, total

    expense_cats, expense_cat_total = _cat_breakdown("expense")
    income_cats, income_cat_total = _cat_breakdown("income")

    # ── 每日趋势 ──────────────────────────────────────────────
    daily_agg = {
        row["occurred_at__date"]: row["s"]
        for row in base.filter(
            occurred_at__gte=aware_day_start(period_start),
            occurred_at__lte=aware_day_end(period_end),
        ).values("occurred_at__date").annotate(s=Sum("amount"))
    }
    daily = []
    d = period_start
    while d <= period_end:
        daily.append({"date": d.isoformat(), "amount": daily_agg.get(d, Decimal(0)) or Decimal(0)})
        d += timedelta(days=1)
    if len(daily) > 92:  # 区间过长时降采样，避免图太密
        step = (len(daily) // 92) or 1
        daily = daily[::step]

    # ── 区间标签 ──────────────────────────────────────────────
    if preset == "month":
        period_label = f"{period_start.year}年{period_start.month}月"
    elif preset == "last_month":
        period_label = f"{period_start.year}年{period_start.month}月（上月）"
    elif preset == "year":
        period_label = f"{period_start.year}年"
    elif preset == "last_year":
        period_label = f"{period_start.year}年（去年）"
    elif preset == "custom":
        period_label = f"{period_start} ~ {period_end}"
    else:
        period_label = "全部"

    chart_data = json.dumps({
        "dailyLabels": [x["date"][5:] for x in daily],
        "dailyAmounts": [float(x["amount"]) for x in daily],
        "catLabels": [c["name"] for c in expense_cats],
        "catAmounts": [float(c["amount"]) for c in expense_cats],
        "catColors": ["#f97316", "#3b82f6", "#8b5cf6", "#06b6d4", "#ec4899", "#6b7280", "#22c55e", "#eab308"][:len(expense_cats)],
    })
    # 防存储型 XSS：与 dashboard 同源的转义策略
    chart_data = chart_data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")

    context = {
        "today": today,
        "preset": preset,
        "period_start": period_start, "period_end": period_end,
        "period_label": period_label,
        "is_all": is_all,
        "total_expense": cur_e, "total_income": cur_i, "balance": cur_b,
        "prev_expense": prev_e, "prev_income": prev_i, "prev_balance": prev_b,
        "yoy_expense": yoy_e, "yoy_income": yoy_i, "yoy_balance": yoy_b,
        "delta_expense_mom": delta_expense_mom, "delta_income_mom": delta_income_mom, "delta_balance_mom": delta_balance_mom,
        "delta_expense_yoy": delta_expense_yoy, "delta_income_yoy": delta_income_yoy, "delta_balance_yoy": delta_balance_yoy,
        "savings_rate": savings_rate,
        "expense_cats": expense_cats, "expense_cat_total": expense_cat_total,
        "income_cats": income_cats, "income_cat_total": income_cat_total,
        "daily": daily,
        "chart_data": chart_data,
    }
    return render(request, "life/reports.html", context)


# ── 年度总结（P1-7，支付宝式年度账单，留存钩子）─────────────────────────

@login_required
def annual_summary(request):
    """年度账单：全年收支概览、月度趋势、分类排行、最大单笔、同比。

    纯只读聚合，与 reports 共用 aware_day_* 边界处理；一次性取数后在 Python 里
    按月份 / 分类分组，避免 12 次月度查询。
    """
    from datetime import date

    from django.db.models import Sum

    from .services import aware_day_end, aware_day_start

    today = timezone.localdate()
    try:
        year = int(request.GET.get("year", today.year))
    except (TypeError, ValueError):
        year = today.year
    if year < 2000 or year > today.year + 1:
        year = today.year

    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)
    prev_start = date(year - 1, 1, 1)
    prev_end = date(year - 1, 12, 31)

    base = Expense.objects.filter(user=request.user, is_deleted=False, status="confirmed")

    def _totals(s, e):
        qs = base.filter(occurred_at__gte=aware_day_start(s), occurred_at__lte=aware_day_end(e))
        expense = qs.filter(type="expense").aggregate(s=Sum("amount"))["s"] or Decimal(0)
        income = qs.filter(type="income").aggregate(s=Sum("amount"))["s"] or Decimal(0)
        return expense, income, income - expense

    cur_e, cur_i, cur_b = _totals(year_start, year_end)
    prev_e, prev_i, prev_b = _totals(prev_start, prev_end)

    saving_rate = round(cur_b / cur_i * 100, 1) if cur_i > 0 else None
    avg_monthly_expense = (cur_e / 12) if cur_e > 0 else Decimal(0)

    # 一次性取全年数据，Python 内分组（12 个月 + 分类）
    rows = list(
        base.filter(
            occurred_at__gte=aware_day_start(year_start),
            occurred_at__lte=aware_day_end(year_end),
        ).values("occurred_at__date", "type", "amount", "category__name", "category__icon")
    )

    monthly_expense = [Decimal(0)] * 12
    monthly_income = [Decimal(0)] * 12
    cat_map = {}
    txn_count = 0
    biggest = None
    for r in rows:
        d = r["occurred_at__date"]
        m = d.month - 1
        amt = r["amount"] or Decimal(0)
        txn_count += 1
        if r["type"] == "expense":
            monthly_expense[m] += amt
            if biggest is None or amt > biggest["amount"]:
                biggest = {"amount": amt, "date": d, "category": r["category__name"] or "未分类",
                           "icon": r["category__icon"] or "💸"}
        else:
            monthly_income[m] += amt
        key = r["category__name"] or "未分类"
        if key not in cat_map:
            cat_map[key] = {"name": key, "icon": r["category__icon"] or "💸",
                            "amount": Decimal(0), "count": 0}
        cat_map[key]["amount"] += amt
        cat_map[key]["count"] += 1

    top_categories = sorted(cat_map.values(), key=lambda x: x["amount"], reverse=True)[:8]
    cat_total = sum((c["amount"] for c in top_categories), Decimal(0))
    for c in top_categories:
        c["pct"] = round(c["amount"] / cat_total * 100) if cat_total > 0 else 0

    # 花得最多的月份
    peak_month_idx = max(range(12), key=lambda i: monthly_expense[i]) if cur_e > 0 else 0
    peak_month = {
        "month": peak_month_idx + 1,
        "amount": monthly_expense[peak_month_idx],
    }

    def _pct(cur, ref):
        if ref is None or ref == 0:
            return None
        return round((cur - ref) / ref * 100, 1)

    delta_e = _pct(cur_e, prev_e)
    delta_i = _pct(cur_i, prev_i)
    delta_b = _pct(cur_b, prev_b)

    import json

    chart_labels = json.dumps([f"{i+1}月" for i in range(12)], ensure_ascii=False)
    chart_expense = json.dumps([float(x) for x in monthly_expense])
    chart_income = json.dumps([float(x) for x in monthly_income])

    context = {
        "year": year,
        "today_year": today.year,
        "total_expense": cur_e,
        "total_income": cur_i,
        "balance": cur_b,
        "prev_expense": prev_e,
        "prev_income": prev_i,
        "prev_balance": prev_b,
        "delta_expense_yoy": delta_e,
        "delta_expense_yoy_abs": abs(delta_e) if delta_e is not None else None,
        "delta_income_yoy": delta_i,
        "delta_income_yoy_abs": abs(delta_i) if delta_i is not None else None,
        "delta_balance_yoy": delta_b,
        "saving_rate": saving_rate,
        "avg_monthly_expense": avg_monthly_expense,
        "txn_count": txn_count,
        "biggest": biggest,
        "peak_month": peak_month,
        "top_categories": top_categories,
        "chart_labels": chart_labels,
        "chart_expense": chart_expense,
        "chart_income": chart_income,
    }
    return render(request, "life/annual_summary.html", context)


# ── Review ─────────────────────────────────────────────────────────

@login_required
def review(request):
    from datetime import date, timedelta
    from decimal import Decimal

    from django.db.models import F, Sum

    today = timezone.localdate()
    period = request.GET.get("period", "weekly")

    if period == "weekly":
        start = today - timedelta(days=today.weekday())  # Monday
        end = start + timedelta(days=6)
    else:
        start = date(today.year, today.month, 1)
        _, ld = monthrange(today.year, today.month)
        end = date(today.year, today.month, ld)

    # Save confirmed review
    if request.method == "POST" and request.POST.get("content"):
        Review.objects.update_or_create(
            user=request.user, period=period, period_start=start,
            defaults={"content": request.POST["content"], "is_confirmed": True, "period_end": end},
        )
        return redirect(f"/review/?period={period}")

    # Existing saved review
    existing = Review.objects.filter(user=request.user, period=period, period_start=start, is_confirmed=True).first()

    # Generate draft
    base = Expense.objects.filter(user=request.user, is_deleted=False, status="confirmed")
    period_qs = base.filter(occurred_at__gte=aware_day_start(start), occurred_at__lte=aware_day_end(end))
    total_exp = period_qs.filter(type="expense").aggregate(s=Sum("amount"))["s"] or Decimal(0)
    total_inc = period_qs.filter(type="income").aggregate(s=Sum("amount"))["s"] or Decimal(0)

    tasks_done = Task.objects.filter(user=request.user, is_deleted=False, status="completed", completed_at__date__gte=start, completed_at__date__lte=end)
    tasks_undone = Task.objects.filter(user=request.user, is_deleted=False, status__in=["todo", "in_progress"], created_at__date__lte=end)
    overdue = tasks_undone.filter(due_at__date__lt=today)

    upcoming = Task.objects.filter(user=request.user, is_deleted=False, status__in=["todo", "in_progress"], due_at__date__gte=today).order_by("due_at")[:3]

    # 任务动态：本周期新建数 / 被推迟数（推迟 = 仍在进行中、创建后 >1h 才被改动）
    period_created = Task.objects.filter(
        user=request.user, is_deleted=False, created_at__date__gte=start, created_at__date__lte=end
    ).count()
    period_postponed = Task.objects.filter(
        user=request.user, is_deleted=False, status__in=["todo", "in_progress"],
        created_at__date__gte=start, created_at__date__lte=end,
        updated_at__gt=F("created_at") + timedelta(hours=1),
    ).count()

    budget = Budget.objects.filter(user=request.user, category__isnull=True, month=date(today.year, today.month, 1)).first()

    draft = f"""## {start} ~ {end} {period_label(period)}复盘

### 完成了什么
{chr(10).join(f'- ✅ {t.title}' for t in tasks_done[:10]) if tasks_done else '- 本周暂无已完成任务'}

### 未完成
{chr(10).join(f'- ⏳ {t.title}' for t in tasks_undone[:5]) if tasks_undone else '- 没有未完成任务'}

### {period_label(period)}消费
- 支出: ¥{total_exp:.2f}
- 收入: ¥{total_inc:.2f}
- 结余: ¥{total_inc - total_exp:.2f}
- 预算: {'¥' + str(budget.amount) if budget else '未设置'}

### 异常提醒
{chr(10).join(f'- ⚠ {t.title} (逾期)' for t in overdue[:5]) if overdue else '- 无异常'}

### 任务动态
- 新建: {period_created} 个
- 被推迟: {period_postponed} 个

### 下周期待
{chr(10).join(f'- 📌 {t.title}' for t in upcoming) if upcoming else '- 暂无'}
"""

    return render(request, "life/review.html", {
        "period": period, "start": start, "end": end,
        "draft": draft if not existing else existing.content,
        "is_confirmed": existing is not None,
    })


def period_label(p):
    return "本周" if p == "weekly" else "本月"


@login_required
@require_POST
def suggestion_feedback(request, pk, fb):
    s = get_object_or_404(Suggestion, pk=pk, user=request.user)
    s.feedback = fb
    s.save()
    return redirect("dashboard")


# ── Reminder CRUD ─────────────────────────────────────────────────────

@login_required
def reminder_list(request):
    from datetime import timedelta
    items = Reminder.objects.filter(user=request.user).order_by("event_at")
    # 即将到来 = 未来 30 天的有效提醒（按事件日期算，不管 remind_at）
    today = timezone.localdate()
    horizon = today + timedelta(days=UPCOMING_HORIZON_DAYS)
    upcoming = []
    for r in items:
        if not r.is_enabled:
            continue
        ed = r.event_at.date() if hasattr(r.event_at, "date") else r.event_at
        # 对于循环提醒（生日等）：计算下一个事件日期
        target = ed
        if r.recurrence_rule == "yearly" and ed < today:
            try:
                target = ed.replace(year=today.year)
                if target < today:
                    target = target.replace(year=today.year + 1)
            except ValueError:
                target = ed.replace(month=2, day=28)
        if target < today or target > horizon:
            continue
        days = (target - today).days
        if days < 0:
            countdown_text = "已过期"; tone = "overdue"
        elif days == 0:
            countdown_text = "今天"; tone = "today"
        elif days <= SOON_DAYS:
            countdown_text = f"{days}天后"; tone = "soon"
        else:
            countdown_text = f"{days}天后"; tone = "later"
        upcoming.append({"obj": r, "countdown": countdown_text, "tone": tone, "days": days, "target": target})
    upcoming.sort(key=lambda x: x["days"])
    return render(request, "life/reminder_list.html", {
        "reminders": items,
        "upcoming_birthdays": upcoming,
    })


@login_required
def reminder_create(request):
    from datetime import timedelta
    if request.method == "POST":
        event_at = request.POST.get("event_at", "")
        days = request.POST.get("remind_days_before", "1")
        # Calculate remind_at from event_at - days
        try:
            et = timezone.datetime.fromisoformat(event_at)
            if timezone.is_naive(et):
                et = timezone.make_aware(et)
            rt = et - timedelta(days=int(days.split(",")[0]))
        except (ValueError, TypeError):
            et = timezone.now()
            rt = et
        Reminder.objects.create(
            user=request.user,
            title=request.POST.get("title", "")[:200],
            reminder_type=request.POST.get("reminder_type", "custom"),
            event_at=et,
            remind_at=rt,
            remind_days_before=days,
            recurrence_rule=request.POST.get("recurrence_rule", "none"),
        )
        return redirect("reminder_list")
    return render(request, "life/reminder_edit.html", {"reminder": None})


@login_required
def reminder_edit(request, pk):
    item = get_object_or_404(Reminder, pk=pk, user=request.user)
    if request.method == "POST":
        item.title = request.POST.get("title", item.title)[:200]
        item.reminder_type = request.POST.get("reminder_type", item.reminder_type)
        item.recurrence_rule = request.POST.get("recurrence_rule", item.recurrence_rule)
        item.is_enabled = request.POST.get("is_enabled") == "on"
        event_at = request.POST.get("event_at", "")
        if event_at:
            et = timezone.datetime.fromisoformat(event_at)
            if timezone.is_naive(et):
                et = timezone.make_aware(et)
            item.event_at = et
        item.save()
        return redirect("reminder_list")
    return render(request, "life/reminder_edit.html", {"reminder": item})


@login_required
@require_POST
def reminder_toggle(request, pk):
    item = get_object_or_404(Reminder, pk=pk, user=request.user)
    item.is_enabled = not item.is_enabled
    item.save()
    if item.is_enabled:
        messages.success(request, f"已启用提醒「{item.title}」")
    else:
        messages.info(request, f"已停用提醒「{item.title}」")
    return redirect("reminder_list")


# ── Note extra: manual create ────────────────────────────────────────


@login_required
def note_create(request):
    if request.method == "POST":
        title = (request.POST.get("title") or "").strip()
        raw_text = request.POST.get("raw_text") or ""
        occurred_on = request.POST.get("occurred_on") or None
        if not title:
            messages.error(request, "请填写标题")
            return render(request, "life/note_edit.html", {"note": None})
        note = Note.objects.create(
            user=request.user,
            title=title[:200],
            raw_text=raw_text,
            occurred_on=occurred_on if occurred_on else None,
        )
        record(request.user, "note.create", note.pk, f"新建随心记: {note.title}")
        messages.success(request, f"已记录随心记「{note.title}」")
        return redirect("note_list")
    return render(request, "life/note_edit.html", {"note": None})


# ── Daily check-in CRUD ───────────────────────────────────────────


@login_required
def daily_list(request):
    from datetime import timedelta as _td
    items_qs = DailyCheckin.objects.filter(user=request.user, is_deleted=False).order_by("is_active", "-created_at")
    today = timezone.localdate()
    items = []
    total_done_today = 0
    week_done_total = 0
    best_streak = 0
    for c in items_qs:
        done_today = c.is_done_on(today)
        if done_today:
            total_done_today += 1
        st = c.streak(today)
        if st > best_streak:
            best_streak = st
        # 近 7 天（含今天）完成次数
        week_dates = {(today - _td(days=i)).isoformat() for i in range(WEEK_TREND_DAYS)}
        week_done = sum(1 for d in (c.done_dates or []) if d in week_dates)
        week_done_total += week_done
        items.append({"obj": c, "is_done_today": done_today, "streak": st, "week_done": week_done})
    total = len(items)
    # 近 7 天每天的全局完成率（迷你热力）
    day_grid = []
    for i in range(WEEK_TREND_DAYS - 1, -1, -1):
        d = today - _td(days=i)
        ds = d.isoformat()
        cnt = sum(1 for c in items_qs if ds in (c.done_dates or []))
        day_grid.append({"date": d, "done": cnt, "pct": round(cnt / total * 100) if total else 0})
    return render(request, "life/daily_list.html", {
        "items": items, "today": today,
        "total": total, "today_done": total_done_today,
        "week_done_total": week_done_total, "best_streak": best_streak,
        "day_grid": day_grid,
    })


@login_required
def daily_create(request):
    if request.method == "POST":
        title = (request.POST.get("title") or "").strip()
        icon = (request.POST.get("icon") or "📌").strip() or "📌"
        note_text = request.POST.get("note") or ""
        if not title:
            messages.error(request, "请填写每日提醒名称")
            return render(request, "life/daily_edit.html", {"item": None})
        item = DailyCheckin.objects.create(user=request.user, title=title[:100], icon=icon[:4], note=note_text[:200])
        record(request.user, "daily.create", item.pk, f"新建每日提醒: {item.title}")
        messages.success(request, f"已添加每日提醒「{item.title}」")
        return redirect("daily_list")
    return render(request, "life/daily_edit.html", {"item": None})


@login_required
def daily_edit(request, pk):
    item = get_object_or_404(DailyCheckin, pk=pk, user=request.user, is_deleted=False)
    if request.method == "POST":
        item.title = (request.POST.get("title") or item.title).strip()[:100]
        item.icon = (request.POST.get("icon") or item.icon)[:4] or "📌"
        item.note = (request.POST.get("note") or "")[:200]
        item.save()
        record(request.user, "daily.update", item.pk, f"修改每日提醒: {item.title}")
        messages.success(request, "已保存")
        return redirect("daily_list")
    return render(request, "life/daily_edit.html", {"item": item})


@login_required
@require_POST
def daily_delete(request, pk):
    item = get_object_or_404(DailyCheckin, pk=pk, user=request.user, is_deleted=False)
    if request.method == "POST":
        title = item.title
        item.is_deleted = True
        item.deleted_at = timezone.now()
        item.save()
        record(request.user, "daily.delete", item.pk, f"删除每日提醒: {title}")
        messages.success(request, f"已删除「{title}」— 可撤销，或在回收站恢复")
        return undo_redirect("daily_list", "daily", item.pk)
    return render(request, "life/_confirm_delete.html", {"item": item, "back": "daily_list", "title": title})


@login_required
@require_POST
def daily_toggle(request, pk):
    """Toggle today's check-off for a daily check-in item.

    Adds today's date if missing, removes it if already present. Returns
    JSON for AJAX callers; falls back to redirect to Home.
    """
    from django.http import JsonResponse

    item = get_object_or_404(DailyCheckin, pk=pk, user=request.user, is_deleted=False)
    today = timezone.localdate()
    dates = list(item.done_dates or [])
    iso = today.isoformat()
    if iso in dates:
        dates = [d for d in dates if d != iso]
        done = False
    else:
        dates.append(iso)
        done = True
    item.done_dates = sorted(set(dates))
    item.save()
    if request.headers.get("x-requested-with") == "fetch" or "application/json" in request.headers.get("accept", ""):
        return JsonResponse({
            "ok": True, "done": done, "date": iso,
            "streak": item.streak(today),
        })
    return safe_next(request, default="home", allow_referer=False)


# ── 倒计时 / 纪念日模块（2026-08-25）───────────────────────────

@login_required
def countdown_list(request):
    """倒计时列表页（iOS Day Matters 风格）。"""
    from django.utils import timezone as _tz

    from .models import Countdown

    today = _tz.localdate()
    cds = Countdown.objects.filter(user=request.user, is_active=True)
    items = []
    for cd in cds:
        next_occ = cd.next_occurrence(today)
        delta = (next_occ - today).days
        items.append({
            "cd": cd,
            "next": next_occ,
            "delta": delta,
            "is_today": delta == 0,
            "is_past": delta < 0,
            "is_upcoming": 0 < delta <= 7,
        })
    # sort: pinned first then by delta (down) then by abs(delta desc) (up)
    items.sort(key=lambda x: (
        not x["cd"].pinned,
        x["delta"] if x["cd"].direction == "down" else -x["delta"],
    ))
    featured = next((x for x in items if x["cd"].pinned), items[0] if items else None)
    hidden = Countdown.objects.filter(user=request.user, is_active=True, show_on_home=False)
    return render(request, "life/countdown_list.html", {
        "items": items,
        "featured": featured,
        "hidden_count": hidden.count(),
        "today": today,
        "today_iso": today.isoformat(),
    })


@login_required
def countdown_create(request):
    """创建倒计时（GET 渲染表单，POST 入库 + 可选同步到 Reminder）。"""
    from datetime import datetime as _dt_cls

    from .models import Countdown, Reminder

    next_url = request.POST.get("next") or request.GET.get("next", "")
    if request.method == "POST":
        title = (request.POST.get("title") or "")[:80].strip()
        date_raw = request.POST.get("target_date") or ""
        try:
            target = _dt_cls.strptime(date_raw, "%Y-%m-%d").date()
        except ValueError:
            target = _tz_localdate()
        direction = request.POST.get("direction", "down")
        recurrence = request.POST.get("recurrence", "none")
        emoji = (request.POST.get("emoji") or "")[:8]
        color = (request.POST.get("color") or "")[:16]
        note = (request.POST.get("note") or "")[:500]
        pinned = request.POST.get("pinned") == "on"
        show_on_home = request.POST.get("show_on_home") != "off"
        sync_to_reminder = request.POST.get("sync_to_reminder") == "on"
        remind_days_before = (request.POST.get("remind_days_before") or "1").strip()

        if not title:
            title = "我的倒计时"

        cd = Countdown.objects.create(
            user=request.user,
            title=title,
            target_date=target,
            direction=direction,
            recurrence=recurrence,
            emoji=emoji,
            color=color,
            note=note,
            pinned=pinned,
            show_on_home=show_on_home,
            sync_to_reminder=False,  # set below if requested
        )
        if sync_to_reminder:
            remind_at = _tz_make_aware(_dt_cls.combine(target, _dt_cls.min.time())) - _td(days=int(remind_days_before or 1))
            rem = Reminder.objects.create(
                user=request.user,
                title=title,
                reminder_type="custom",
                event_at=remind_at + _td(days=int(remind_days_before or 1)),
                remind_at=remind_at,
                remind_days_before=remind_days_before,
                recurrence_rule=recurrence if recurrence in ("daily", "weekly", "monthly", "yearly", "none") else "yearly",
                is_enabled=True,
            )
            cd.reminder = rem
            cd.sync_to_reminder = True
            cd.save(update_fields=["reminder", "sync_to_reminder", "updated_at"])
        # 安全跳转：仅允许站内绝对路径，拒绝 // 协议相对与外部地址
        return safe_next(request, default="countdown_list", allow_referer=False)
    return render(request, "life/countdown_edit.html", {
        "cd": None,
        "next": next_url,
        "today": _tz_localdate().isoformat(),
    })


@login_required
def countdown_edit(request, pk):
    from datetime import datetime as _dt_cls

    from .models import Countdown

    cd = get_object_or_404(Countdown, pk=pk, user=request.user, is_active=True)
    if request.method == "POST":
        title = (request.POST.get("title") or "")[:80].strip()
        date_raw = request.POST.get("target_date") or ""
        try:
            target = _dt_cls.strptime(date_raw, "%Y-%m-%d").date()
        except ValueError:
            target = cd.target_date
        direction = request.POST.get("direction", cd.direction)
        recurrence = request.POST.get("recurrence", cd.recurrence)
        cd.title = title or cd.title
        cd.target_date = target
        cd.direction = direction
        cd.recurrence = recurrence
        cd.emoji = (request.POST.get("emoji") or "")[:8]
        cd.color = (request.POST.get("color") or "")[:16]
        cd.note = (request.POST.get("note") or "")[:500]
        cd.pinned = request.POST.get("pinned") == "on"
        cd.show_on_home = request.POST.get("show_on_home") != "off"
        cd.save()
        # 把变更同步到关联的 Reminder
        if cd.reminder_id:
            rem = cd.reminder
            rem.title = cd.title
            rem.event_at = _tz_make_aware(_dt_cls.combine(cd.target_date, _dt_cls.min.time()))
            rem.remind_at = rem.event_at - _td(days=int(request.POST.get("remind_days_before") or 1))
            rem.recurrence_rule = cd.recurrence if cd.recurrence in ("daily", "weekly", "monthly", "yearly", "none") else "yearly"
            rem.save()
        return redirect("countdown_list")

    return render(request, "life/countdown_edit.html", {
        "cd": cd,
        "today": _tz_localdate().isoformat(),
    })


@login_required
@require_POST
def countdown_delete(request, pk):
    from .models import Countdown
    cd = get_object_or_404(Countdown, pk=pk, user=request.user)
    if request.method != "POST":
        return redirect("countdown_list")
    cd.is_active = False
    cd.save(update_fields=["is_active", "updated_at"])
    return redirect("countdown_list")


@login_required
@require_POST
def countdown_pin(request, pk):
    """切换置顶状态（仅 POST）"""
    from .models import Countdown
    cd = get_object_or_404(Countdown, pk=pk, user=request.user)
    cd.pinned = not cd.pinned
    cd.save(update_fields=["pinned", "updated_at"])
    return safe_next(request, default="countdown_list", allow_referer=False)


@login_required
@require_POST
def countdown_toggle_home(request, pk):
    """切换「首页显示」状态 — 隐藏/恢复都在首页小板块操作（仅 POST）"""
    from .models import Countdown
    cd = get_object_or_404(Countdown, pk=pk, user=request.user)
    cd.show_on_home = not cd.show_on_home
    cd.save(update_fields=["show_on_home", "updated_at"])
    return safe_next(request, default="countdown_list", allow_referer=False)


# ── helper imports (deferred to avoid breaking module-level ordering) ──

from datetime import timedelta as _td  # noqa: E402  (used in countdown views above)


def _tz_localdate():
    from django.utils import timezone
    return timezone.localdate()


def _tz_make_aware(dt):
    from django.utils import timezone
    if timezone.is_naive(dt):
        return timezone.make_aware(dt)
    return dt

