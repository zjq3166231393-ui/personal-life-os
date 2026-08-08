import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Entry
from .parser import parse_text


def home(request):
    today = timezone.localdate()
    upcoming_tasks = Entry.objects.filter(kind=Entry.Kind.TASK, completed=False).filter(due_at__date__gte=today).order_by("due_at")[:5]
    month_expenses = Entry.objects.filter(kind=Entry.Kind.EXPENSE, occurred_on__year=today.year, occurred_on__month=today.month)
    total = sum((item.amount or Decimal("0") for item in month_expenses), Decimal("0"))
    recent = Entry.objects.all()[:8]
    return render(request, "life/home.html", {"today": today, "upcoming_tasks": upcoming_tasks, "month_total": total, "recent": recent})


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
    Entry.objects.create(kind=draft["kind"], title=str(draft["title"])[:200], raw_text=raw_text, category=draft.get("category", ""), amount=amount, occurred_on=occurred_on, due_at=due_at, priority=int(draft.get("priority", 2)))
    return JsonResponse({"ok": True})

