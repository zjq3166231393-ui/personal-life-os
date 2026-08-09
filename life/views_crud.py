from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

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
    expenses = _user_queryset(Expense, request).select_related("category")
    return render(request, "life/expense_list.html", {"expenses": expenses})

@login_required
def expense_detail(request, pk):
    expense = get_object_or_404(Expense, pk=pk, is_deleted=False)
    _check_owner(expense, request)
    return render(request, "life/expense_detail.html", {"expense": expense})

@login_required
def expense_edit(request, pk):
    expense = get_object_or_404(Expense, pk=pk, is_deleted=False)
    _check_owner(expense, request)
    categories = Category.objects.filter(user__in=[request.user, None], kind="expense", is_default=True)
    if request.method == "POST":
        expense.title = request.POST.get("title", expense.title)
        expense.amount = request.POST.get("amount", expense.amount)
        expense.occurred_on = request.POST.get("occurred_on", expense.occurred_on)
        cat_id = request.POST.get("category")
        if cat_id:
            expense.category_id = int(cat_id)
        expense.save()
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
        return redirect("note_list")
    return render(request, "life/note_delete.html", {"note": note})
