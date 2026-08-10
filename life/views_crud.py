from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from common.audit import record
from .models import Category, Expense, Note, Task


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
    tasks = _user_queryset(Task, request).order_by("completed", "-priority", "due_at")
    return render(request, "life/task_list.html", {"tasks": tasks})

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
        task.title = request.POST.get("title", task.title)
        task.priority = int(request.POST.get("priority", task.priority))
        task.due_at = request.POST.get("due_at") or None
        completed = request.POST.get("completed") == "on"
        if completed and not task.completed:
            task.completed_at = timezone.now()
        elif not completed:
            task.completed_at = None
        task.completed = completed
        task.save()
        if completed:
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
