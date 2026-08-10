import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from django.contrib.auth.decorators import login_required

from common.audit import record
from .models import Budget, Category, Entry, Expense, InstallmentPlan, RecurringExpense, Reminder, Task
from .parser import parse_text


@login_required
def home(request):
    from calendar import monthrange
    from collections import defaultdict
    from datetime import date, timedelta
    from decimal import Decimal
    from django.db.models import Q, Sum

    today = timezone.localdate()
    tomorrow = today + timedelta(days=1)
    week_end = today + timedelta(days=7)
    month_start = date(today.year, today.month, 1)
    _, last_day = monthrange(today.year, today.month)
    month_end = date(today.year, today.month, last_day)

    # ── top 3 tasks ──────────────────────────────────────────────
    top_tasks = Task.objects.filter(
        user=request.user, is_deleted=False, status__in=["todo", "in_progress"],
    ).order_by("-priority", "due_at")[:3]

    # ── due today ────────────────────────────────────────────────
    due_today = Task.objects.filter(
        user=request.user, is_deleted=False, status__in=["todo", "in_progress"],
        due_at__date__gte=today, due_at__date__lt=tomorrow,
    ).order_by("-priority")

    # ── next 7 days reminders ────────────────────────────────────
    reminders = Reminder.objects.filter(
        user=request.user, is_enabled=True,
        remind_at__gte=today, remind_at__lte=week_end,
    ).order_by("remind_at")

    # ── upcoming bills (recurring + installment) ────────────────
    bills = []
    for r in RecurringExpense.objects.filter(user=request.user, is_active=True):
        bill_date = date(today.year, today.month, r.due_day) if r.due_day >= today.day else date(today.year, today.month + 1 if today.month < 12 else today.year + 1, r.due_day) if today.month < 12 else date(today.year + 1, 1, r.due_day)
        bills.append({"name": r.name, "amount": r.amount, "date": bill_date, "type": "固定"})
    for p in InstallmentPlan.objects.filter(user=request.user, status="active"):
        bills.append({"name": p.name, "amount": p.installment_amount, "date": p.next_due_date, "type": "分期"})
    bills.sort(key=lambda x: x["date"])
    bills = bills[:5]

    # ── budget summary ──────────────────────────────────────────
    spent = Expense.objects.filter(user=request.user, type="expense", status="confirmed", is_deleted=False,
                                   occurred_at__gte=month_start, occurred_at__lte=month_end).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    budget = Budget.objects.filter(user=request.user, category__isnull=True, month=month_start).first()
    budget_amount = budget.amount if budget else Decimal("0")
    budget_pct = min(int(spent / budget_amount * 100) if budget_amount > 0 else 0, 100)

    return render(request, "life/home.html", {
        "today": today, "top_tasks": top_tasks, "due_today": due_today,
        "reminders": reminders, "bills": bills, "spent": spent,
        "budget_amount": budget_amount, "budget_pct": budget_pct,
    })


@login_required
@require_POST
def parse_entry(request):
    try:
        payload = json.loads(request.body)
        text = payload["text"]
    except (json.JSONDecodeError, KeyError):
        return HttpResponseBadRequest("请输入需要记录的内容。")
    if not isinstance(text, str) or not text.strip():
        return HttpResponseBadRequest("请输入需要记录的内容。")
    return JsonResponse({"draft": parse_text(text), "raw_text": text.strip()})


@login_required
@require_POST
def save_entry(request):
    try:
        payload = json.loads(request.body)
        draft = payload["draft"]
        raw_text = payload.get("raw_text", "")
    except (json.JSONDecodeError, KeyError):
        return HttpResponseBadRequest("保存内容不完整。")
    if draft.get("kind") not in Entry.Kind.values or not str(draft.get("title", "")).strip():
        return HttpResponseBadRequest("识别结果无效。")
    amount = None
    if draft.get("amount") not in (None, ""):
        try:
            amount = Decimal(str(draft["amount"]))
        except InvalidOperation:
            return HttpResponseBadRequest("金额格式无效。")
    occurred_on = datetime.fromisoformat(draft["occurred_on"]).date() if draft.get("occurred_on") else None
    due_at = datetime.fromisoformat(draft["due_at"]) if draft.get("due_at") else None
    entry = Entry.objects.create(user=request.user, kind=draft["kind"], title=str(draft["title"])[:200], raw_text=raw_text, category=draft.get("category", ""), amount=amount, occurred_on=occurred_on, due_at=due_at, priority=int(draft.get("priority", 2)))
    record(request.user, "ai.save", entry.pk, f"保存记录: {entry.title}")

    # Also create Expense record for income/expense entries with amount
    if amount is not None and draft["kind"] in ("expense", "income"):
        cat = None
        cat_name = draft.get("category", "")
        if cat_name:
            from django.db.models import Q
            cat = Category.objects.filter(Q(user=request.user) | Q(user__isnull=True), name=cat_name, is_active=True).first()
        exp_type = draft.get("type", "expense")
        occurred_at = timezone.make_aware(datetime(occurred_on.year, occurred_on.month, occurred_on.day, 12, 0)) if occurred_on else timezone.now()
        Expense.objects.create(
            user=request.user, type=exp_type, category=cat,
            amount=amount, occurred_at=occurred_at, note=str(draft["title"])[:500],
            raw_text=raw_text, source="text",
        )

    return JsonResponse({"ok": True})

