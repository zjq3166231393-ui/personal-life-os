import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from django.contrib.auth.decorators import login_required

from common.audit import record
from .models import Category, Entry, Expense
from .parser import parse_text


@login_required
def home(request):
    today = timezone.localdate()
    entries = Entry.objects.filter(user=request.user)
    upcoming_tasks = entries.filter(kind=Entry.Kind.TASK, completed=False).filter(due_at__date__gte=today).order_by("due_at")[:5]
    month_expenses = entries.filter(kind=Entry.Kind.EXPENSE, occurred_on__year=today.year, occurred_on__month=today.month)
    total = sum((item.amount or Decimal("0") for item in month_expenses), Decimal("0"))
    recent = entries[:8]
    return render(request, "life/home.html", {"today": today, "upcoming_tasks": upcoming_tasks, "month_total": total, "recent": recent})


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

