from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from common.audit import record
from .models import Budget, Category, Expense, InstallmentPlan, Note, RecurringExpense, Reminder, Task


def _user_queryset(model, request):
    return model.objects.filter(user=request.user, is_deleted=False)


def _check_owner(obj, request):
    if obj.user_id != request.user.id:
        from django.http import Http404
        raise Http404("未找到此记录。")
    return None


# ── Expense CRUD ────────────────────────────────────────────────────

@login_required
def expense_list(request):
    from datetime import date, datetime
    from decimal import Decimal, InvalidOperation
    from django.core.paginator import Paginator
    from django.db.models import Q

    qs = _user_queryset(Expense, request).select_related("category")

    # ── filters ──────────────────────────────────────────────────
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    cat_id = request.GET.get("category", "")
    typ = request.GET.get("type", "")
    amount_min = request.GET.get("amount_min", "")
    amount_max = request.GET.get("amount_max", "")
    query = request.GET.get("q", "").strip()

    if date_from:
        try:
            dt = datetime.strptime(date_from, "%Y-%m-%d")
            qs = qs.filter(occurred_at__gte=dt)
        except ValueError:
            pass
    if date_to:
        try:
            from datetime import timedelta
            dt = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
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
    paginator = Paginator(qs, 20)
    page_num = request.GET.get("page", "1")
    page_obj = paginator.get_page(page_num)

    # ── category list for filter dropdown ────────────────────────
    categories = Category.objects.filter(Q(user=request.user) | Q(user__isnull=True), type="expense", is_active=True)

    return render(request, "life/expense_list.html", {
        "page_obj": page_obj,
        "categories": categories,
        "filters": {
            "date_from": date_from, "date_to": date_to,
            "category": cat_id, "type": typ,
            "amount_min": amount_min, "amount_max": amount_max,
            "q": query,
        },
    })

@login_required
def expense_detail(request, pk):
    expense = get_object_or_404(Expense, pk=pk, is_deleted=False)
    _check_owner(expense, request)
    return render(request, "life/expense_detail.html", {"expense": expense})

@login_required
def expense_edit(request, pk):
    from django.db.models import Q
    expense = get_object_or_404(Expense, pk=pk, is_deleted=False)
    _check_owner(expense, request)
    categories = Category.objects.filter(Q(user=request.user) | Q(user__isnull=True), type="expense", is_active=True)
    if request.method == "POST":
        expense.note = request.POST.get("note", expense.note)[:500]
        expense.amount = request.POST.get("amount", expense.amount)
        expense.type = request.POST.get("type", expense.type)
        expense.occurred_at = request.POST.get("occurred_at", expense.occurred_at)
        expense.merchant = request.POST.get("merchant", expense.merchant)[:200]
        expense.source = request.POST.get("source", expense.source)
        cat_id = request.POST.get("category")
        if cat_id:
            expense.category_id = int(cat_id)
        expense.save()
        record(request.user, "expense.update", expense.pk, f"修改支出: {expense.note or expense.merchant}")
        return redirect("expense_list")
    return render(request, "life/expense_edit.html", {"expense": expense, "categories": categories})

@login_required
def expense_delete(request, pk):
    expense = get_object_or_404(Expense, pk=pk, is_deleted=False)
    _check_owner(expense, request)
    if request.method == "POST":
        expense.is_deleted = True
        expense.deleted_at = timezone.now()
        expense.save()
        record(request.user, "expense.delete", expense.pk, f"删除支出: {expense.title}")
        return redirect("expense_list")
    return render(request, "life/expense_delete.html", {"expense": expense})


# ── Task CRUD ───────────────────────────────────────────────────────

@login_required
def task_list(request):
    from datetime import date, timedelta
    today = timezone.localdate()
    qs = _user_queryset(Task, request)

    filt = request.GET.get("filter", "all")
    prio = request.GET.get("priority", "")

    if filt == "today":
        qs = qs.filter(status__in=["todo", "in_progress"], due_at__date=today)
    elif filt == "week":
        qs = qs.filter(status__in=["todo", "in_progress"], due_at__date__gte=today, due_at__date__lte=today + timedelta(days=7))
    elif filt == "overdue":
        qs = qs.filter(status__in=["todo", "in_progress"], due_at__date__lt=today)
    elif filt == "completed":
        qs = qs.filter(status="completed")
    elif filt == "all":
        qs = qs.filter(status__in=["todo", "in_progress"])
    else:
        qs = qs.filter(status__in=["todo", "in_progress"])

    if prio and prio.isdigit():
        qs = qs.filter(priority=int(prio))

    tasks = qs.order_by("-priority", "due_at")
    filters = [
        ("all", "全部"), ("today", "今日"), ("week", "7天内"),
        ("overdue", "已逾期"), ("completed", "已完成"),
    ]
    return render(request, "life/task_list.html", {
        "tasks": tasks, "filter": filt, "priority": prio,
        "filters": filters, "today": today,
    })


@login_required
def task_complete(request, pk):
    task = get_object_or_404(Task, pk=pk, is_deleted=False)
    _check_owner(task, request)
    task.status = "completed"
    task.completed_at = timezone.now()
    task.save()
    record(request.user, "task.complete", task.pk, f"完成任务: {task.title}")
    return redirect("task_list")


@login_required
def task_postpone(request, pk):
    from datetime import timedelta
    task = get_object_or_404(Task, pk=pk, is_deleted=False)
    _check_owner(task, request)
    if task.due_at:
        task.due_at = task.due_at + timedelta(days=1)
        task.save()
    return redirect("task_list")


@login_required
def task_cancel(request, pk):
    task = get_object_or_404(Task, pk=pk, is_deleted=False)
    _check_owner(task, request)
    task.status = "cancelled"
    task.save()
    return redirect("task_list")


@login_required
def task_archive(request, pk):
    task = get_object_or_404(Task, pk=pk, is_deleted=False)
    _check_owner(task, request)
    task.status = "archived"
    task.save()
    return redirect("task_list")


@login_required
def task_renew(request, pk):
    """Generate the next occurrence of a recurring task. Skips if already generated."""
    task = get_object_or_404(Task, pk=pk, is_deleted=False)
    _check_owner(task, request)
    # Prevent duplicate: if a todo/in_progress task with same title already exists, skip
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
    task = get_object_or_404(Task, pk=pk, is_deleted=False)
    _check_owner(task, request)
    return render(request, "life/task_detail.html", {"task": task})

@login_required
def task_edit(request, pk):
    task = get_object_or_404(Task, pk=pk, is_deleted=False)
    _check_owner(task, request)
    if request.method == "POST":
        task.title = request.POST.get("title", task.title)[:200]
        task.description = request.POST.get("description", "")[:5000]
        task.priority = int(request.POST.get("priority", task.priority))
        task.due_at = request.POST.get("due_at") or None
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
        if new_status == "completed":
            record(request.user, "task.complete", task.pk, f"完成任务: {task.title}")
        else:
            record(request.user, "task.update", task.pk, f"修改任务: {task.title}")
        return redirect("task_list")
    return render(request, "life/task_edit.html", {"task": task})

@login_required
def task_delete(request, pk):
    task = get_object_or_404(Task, pk=pk, is_deleted=False)
    _check_owner(task, request)
    if request.method == "POST":
        task.is_deleted = True
        task.deleted_at = timezone.now()
        task.save()
        record(request.user, "task.delete", task.pk, f"删除任务: {task.title}")
        return redirect("task_list")
    return render(request, "life/task_delete.html", {"task": task})


# ── Note CRUD ───────────────────────────────────────────────────────

@login_required
def note_list(request):
    notes = _user_queryset(Note, request)
    return render(request, "life/note_list.html", {"notes": notes})

@login_required
def note_detail(request, pk):
    note = get_object_or_404(Note, pk=pk, is_deleted=False)
    _check_owner(note, request)
    return render(request, "life/note_detail.html", {"note": note})

@login_required
def note_edit(request, pk):
    note = get_object_or_404(Note, pk=pk, is_deleted=False)
    _check_owner(note, request)
    if request.method == "POST":
        note.title = request.POST.get("title", note.title)
        note.occurred_on = request.POST.get("occurred_on") or None
        note.save()
        record(request.user, "note.update", note.pk, f"修改随心记: {note.title}")
        return redirect("note_list")
    return render(request, "life/note_edit.html", {"note": note})

@login_required
def note_delete(request, pk):
    note = get_object_or_404(Note, pk=pk, is_deleted=False)
    _check_owner(note, request)
    if request.method == "POST":
        note.is_deleted = True
        note.deleted_at = timezone.now()
        note.save()
        record(request.user, "note.delete", note.pk, f"删除随心记: {note.title}")
        return redirect("note_list")
    return render(request, "life/note_delete.html", {"note": note})


# ── Category CRUD ────────────────────────────────────────────────────

@login_required
def category_list(request):
    from django.db.models import Q
    cats = Category.objects.filter(Q(user=request.user) | Q(user__isnull=True), is_active=True)
    cat_data = []
    for c in cats:
        refs = Expense.objects.filter(category=c, is_deleted=False).count()
        cat_data.append({"obj": c, "refs": refs, "is_system": c.user_id is None})
    return render(request, "life/category_list.html", {"categories": cat_data})


@login_required
def category_create(request):
    if request.method == "POST":
        Category.objects.create(
            user=request.user,
            name=request.POST.get("name", "")[:50],
            type=request.POST.get("type", "expense"),
            icon=request.POST.get("icon", ""),
            color=request.POST.get("color", ""),
            is_system=False,
        )
        return redirect("category_list")
    return render(request, "life/category_edit.html", {"category": None})


@login_required
def category_edit(request, pk):
    cat = get_object_or_404(Category, pk=pk, is_active=True)
    if cat.user_id and cat.user_id != request.user.id:
        raise Http404()
    if request.method == "POST":
        if not cat.user_id:
            raise Http404()
        cat.name = request.POST.get("name", cat.name)[:50]
        cat.icon = request.POST.get("icon", cat.icon)
        cat.color = request.POST.get("color", cat.color)
        cat.save()
        return redirect("category_list")
    return render(request, "life/category_edit.html", {"category": cat})


@login_required
def category_deactivate(request, pk):
    cat = get_object_or_404(Category, pk=pk, is_active=True)
    if cat.user_id and cat.user_id != request.user.id:
        raise Http404()
    if request.method == "POST":
        if not cat.user_id:
            raise Http404()
        refs = Expense.objects.filter(category=cat, is_deleted=False).count()
        if refs > 0:
            return render(request, "life/category_delete.html", {"category": cat, "refs": refs, "blocked": True})
        cat.is_active = False
        cat.save()
        return redirect("category_list")
    refs = Expense.objects.filter(category=cat, is_deleted=False).count()
    return render(request, "life/category_delete.html", {"category": cat, "refs": refs, "blocked": refs > 0})


# ── Budget ────────────────────────────────────────────────────────────

@login_required
def budget(request):
    from calendar import monthrange
    from datetime import date
    from decimal import Decimal
    from django.db.models import Q, Sum

    today = date.today()
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
        return redirect("budget")

    # ── totals ─────────────────────────────────────────────────────
    spent_total = Expense.objects.filter(
        user=request.user, type="expense", status="confirmed", is_deleted=False,
        occurred_at__gte=month_start, occurred_at__lte=month_end,
    ).aggregate(s=Sum("amount"))["s"] or Decimal("0")

    budget_total = Budget.objects.filter(
        user=request.user, category__isnull=True, month=month_start,
    ).first()
    total_amount = budget_total.amount if budget_total else Decimal("0")

    remaining = total_amount - spent_total
    pct = min(int(spent_total / total_amount * 100) if total_amount > 0 else 0, 100)

    # ── per-category ───────────────────────────────────────────────
    categories = Category.objects.filter(
        Q(user=request.user) | Q(user__isnull=True), type="expense", is_active=True,
    )
    cat_budgets = {}
    for b in Budget.objects.filter(user=request.user, category__isnull=False, month=month_start):
        cat_budgets[b.category_id] = b.amount

    cat_rows = []
    for c in categories:
        spent = Expense.objects.filter(
            user=request.user, category=c, type="expense", status="confirmed",
            is_deleted=False, occurred_at__gte=month_start, occurred_at__lte=month_end,
        ).aggregate(s=Sum("amount"))["s"] or Decimal("0")
        budgeted = cat_budgets.get(c.id, Decimal("0"))
        rem = budgeted - spent
        cat_pct = min(int(spent / budgeted * 100) if budgeted > 0 else 0, 100)
        cat_rows.append({
            "obj": c, "spent": spent, "budget": budgeted,
            "remaining": rem, "pct": cat_pct,
            "over": spent > budgeted > 0,
            "over_amount": abs(rem) if rem < 0 else Decimal("0"),
        })

    return render(request, "life/budget.html", {
        "today": today, "month_start": month_start,
        "spent_total": spent_total, "total_amount": total_amount,
        "remaining": remaining, "pct": pct,
        "over_amount": abs(remaining) if remaining < 0 else Decimal("0"),
        "over_total": total_amount > 0 and spent_total > total_amount,
        "cat_rows": cat_rows,
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
    from datetime import date
    if request.method == "POST":
        RecurringExpense.objects.create(
            user=request.user,
            name=request.POST.get("name", "")[:200],
            category_id=int(request.POST.get("category")) if request.POST.get("category") else None,
            amount=request.POST.get("amount", "0"),
            frequency=request.POST.get("frequency", "monthly"),
            due_day=int(request.POST.get("due_day", "1")),
            start_date=request.POST.get("start_date", date.today().isoformat()),
            remind_days_before=int(request.POST.get("remind_days_before", "3")),
        )
        return redirect("recurring_list")
    from django.db.models import Q
    categories = Category.objects.filter(Q(user=request.user) | Q(user__isnull=True), type="expense", is_active=True)
    return render(request, "life/recurring_edit.html", {"item": None, "categories": categories})


@login_required
def recurring_edit(request, pk):
    item = get_object_or_404(RecurringExpense, pk=pk)
    _check_owner(item, request)
    if request.method == "POST":
        item.name = request.POST.get("name", item.name)[:200]
        item.amount = request.POST.get("amount", item.amount)
        item.frequency = request.POST.get("frequency", item.frequency)
        item.due_day = int(request.POST.get("due_day", item.due_day))
        item.start_date = request.POST.get("start_date", item.start_date)
        item.end_date = request.POST.get("end_date") or None
        item.remind_days_before = int(request.POST.get("remind_days_before", item.remind_days_before))
        cat_id = request.POST.get("category")
        item.category_id = int(cat_id) if cat_id else None
        item.save()
        return redirect("recurring_list")
    from django.db.models import Q
    categories = Category.objects.filter(Q(user=request.user) | Q(user__isnull=True), type="expense", is_active=True)
    return render(request, "life/recurring_edit.html", {"item": item, "categories": categories})


@login_required
def recurring_deactivate(request, pk):
    item = get_object_or_404(RecurringExpense, pk=pk)
    _check_owner(item, request)
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
    from datetime import date
    from django.db.models import Q
    if request.method == "POST":
        InstallmentPlan.objects.create(
            user=request.user,
            name=request.POST.get("name", "")[:200],
            category_id=int(request.POST.get("category")) if request.POST.get("category") else None,
            total_amount=request.POST.get("total_amount", "0"),
            installment_amount=request.POST.get("installment_amount", "0"),
            total_periods=int(request.POST.get("total_periods", "1")),
            next_due_date=request.POST.get("next_due_date", date.today().isoformat()),
        )
        return redirect("installment_list")
    categories = Category.objects.filter(Q(user=request.user) | Q(user__isnull=True), type="expense", is_active=True)
    return render(request, "life/installment_edit.html", {"plan": None, "categories": categories})


@login_required
def installment_edit(request, pk):
    plan = get_object_or_404(InstallmentPlan, pk=pk)
    _check_owner(plan, request)
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
def installment_pay(request, pk):
    plan = get_object_or_404(InstallmentPlan, pk=pk)
    _check_owner(plan, request)
    from datetime import date, timedelta
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
    from calendar import monthrange
    from collections import defaultdict
    from datetime import date, timedelta
    from decimal import Decimal
    from django.db.models import Q, Sum

    today = date.today()
    month_start = date(today.year, today.month, 1)
    _, last_day = monthrange(today.year, today.month)
    month_end = date(today.year, today.month, last_day)

    # ── monthly totals ─────────────────────────────────────────────
    base = Expense.objects.filter(user=request.user, is_deleted=False, status="confirmed")
    month_qs = base.filter(occurred_at__gte=month_start, occurred_at__lte=month_end)

    total_expense = month_qs.filter(type="expense").aggregate(s=Sum("amount"))["s"] or Decimal("0")
    total_income = month_qs.filter(type="income").aggregate(s=Sum("amount"))["s"] or Decimal("0")
    balance = total_income - total_expense

    # ── category breakdown ─────────────────────────────────────────
    cat_spent = defaultdict(Decimal)
    for row in month_qs.filter(type="expense").values("category__name", "category__icon", "category__color").annotate(s=Sum("amount")):
        cat_spent[row["category__name"] or "未分类"] = row["s"]
    cat_pct = []
    for name, amt in sorted(cat_spent.items(), key=lambda x: x[1], reverse=True):
        cat_pct.append({"name": name, "amount": amt, "pct": round(amt / total_expense * 100) if total_expense > 0 else 0})

    # ── daily trend ────────────────────────────────────────────────
    daily = []
    for d in range(1, last_day + 1):
        day = date(today.year, today.month, d)
        amt = base.filter(occurred_at__date=day, type="expense").aggregate(s=Sum("amount"))["s"]
        daily.append({"day": d, "amount": amt or Decimal("0")})

    # ── recurring total ────────────────────────────────────────────
    rec_total = RecurringExpense.objects.filter(user=request.user, is_active=True).aggregate(s=Sum("amount"))["s"] or Decimal("0")

    # ── upcoming bills (recurring + installment) ───────────────────
    upcoming = []
    for r in RecurringExpense.objects.filter(user=request.user, is_active=True):
        upcoming.append({"name": r.name, "amount": r.amount, "date": date(today.year, today.month, r.due_day) if r.due_day >= today.day else date(today.year, today.month + 1 if today.month < 12 else 1, r.due_day), "type": "固定"})
    for p in InstallmentPlan.objects.filter(user=request.user, status="active"):
        upcoming.append({"name": p.name, "amount": p.installment_amount, "date": p.next_due_date, "type": "分期"})
    upcoming.sort(key=lambda x: x["date"])

    # ── budget rate ────────────────────────────────────────────────
    budget_total = Budget.objects.filter(user=request.user, category__isnull=True, month=month_start).first()
    budget_amount = budget_total.amount if budget_total else Decimal("0")
    budget_pct = min(int(total_expense / budget_amount * 100) if budget_amount > 0 else 0, 100)

    # ── monthly trend (last 6 months) ────────────────────────────
    import json
    monthly_labels = []
    monthly_expense = []
    monthly_income = []
    for i in range(5, -1, -1):
        m = today.month - i
        y = today.year
        if m <= 0:
            m += 12
            y -= 1
        ms = date(y, m, 1)
        _, ld = monthrange(y, m)
        me = date(y, m, ld)
        monthly_labels.append(f"{m}月")
        monthly_expense.append(float(base.filter(type="expense", occurred_at__gte=ms, occurred_at__lte=me).aggregate(s=Sum("amount"))["s"] or Decimal("0")))
        monthly_income.append(float(base.filter(type="income", occurred_at__gte=ms, occurred_at__lte=me).aggregate(s=Sum("amount"))["s"] or Decimal("0")))

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

    # ── month-end prediction ─────────────────────────────────────
    days_passed = today.day
    days_remaining = last_day - today.day
    daily_avg = total_expense / days_passed if days_passed > 0 else Decimal("0")
    predicted_remaining = daily_avg * days_remaining
    predicted_total = total_expense + predicted_remaining

    # Identify potential one-time large expenses (> 3x daily avg)
    large_items = []
    threshold = daily_avg * 3 if daily_avg > 0 else Decimal("999999")
    for e in month_qs.filter(type="expense", amount__gte=threshold).order_by("-amount")[:3]:
        large_items.append({"note": e.note or e.merchant or "未命名", "amount": e.amount})
    # Exclude large items for a conservative estimate
    excluded = sum(item["amount"] for item in large_items)
    conservative_total = predicted_total - excluded if excluded else predicted_total
    predicted_extra = predicted_total - total_expense

    return render(request, "life/dashboard.html", {
        "today": today, "month_start": month_start,
        "total_expense": total_expense, "total_income": total_income,
        "balance": balance, "cat_pct": cat_pct, "daily": daily,
        "rec_total": rec_total, "upcoming": upcoming[:10],
        "budget_amount": budget_amount, "budget_pct": budget_pct,
        "chart_data": chart_data,
        "predicted_total": predicted_total,
        "predicted_extra": predicted_extra,
        "daily_avg": daily_avg,
        "days_passed": days_passed,
        "days_remaining": days_remaining,
        "large_items": large_items,
        "conservative_total": conservative_total,
    })


# ── Reminder CRUD ─────────────────────────────────────────────────────

@login_required
def reminder_list(request):
    items = Reminder.objects.filter(user=request.user)
    return render(request, "life/reminder_list.html", {"reminders": items})


@login_required
def reminder_create(request):
    from datetime import date, timedelta
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
    item = get_object_or_404(Reminder, pk=pk)
    _check_owner(item, request)
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
def reminder_toggle(request, pk):
    item = get_object_or_404(Reminder, pk=pk)
    _check_owner(item, request)
    item.is_enabled = not item.is_enabled
    item.save()
    return redirect("reminder_list")
