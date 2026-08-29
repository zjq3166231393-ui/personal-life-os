import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from common.audit import record

from .ai_router import route_parse_split
from .models import Expense, Note, ParseJob, RecurringExpense, Reminder, Task
from .services import (
    bump_overdue_due,
    home_data,
    is_placeholder_title,
    resolve_category,
)


@login_required
def home(request):
    return render(request, "life/home.html", home_data(request.user))


@login_required
def appearance(request):
    """外观设置已并入个人主页 `/accounts/profile/#appearance`。
    老链接保留为重定向，避免书签和外链失效。
    """
    from django.shortcuts import redirect
    from django.urls import reverse
    return redirect(reverse("profile") + "#appearance")


@login_required
def lunar_api(request):
    """返回指定公历日期的农历（紧凑：月+日），用于提醒日期选择器的实时预览。

    入参：?date=YYYY-MM-DD
    返回：{"lunar": "七月廿八", "ok": true}
    """
    from datetime import date as _date

    from .lunar import format_lunar

    ds = (request.GET.get("date") or "").strip()
    try:
        y, m, d = (int(x) for x in ds.split("-"))
        dt = _date(y, m, d)
    except (ValueError, AttributeError):
        return JsonResponse({"lunar": "", "ok": False}, status=400)
    try:
        lunar = format_lunar(dt, include_year=False, include_shengxiao=False)
    except Exception:
        lunar = ""
    return JsonResponse({"lunar": lunar, "ok": True})


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

    out = route_parse_split(text, user=request.user)
    if out.get("ready") is False:
        # AI 解析转入后台任务，前端轮询 /api/parse-status/<job_id>/
        return JsonResponse({
            "ready": False,
            "job_id": out["job_id"],
            "raw_text": text.strip(),
        })

    result = out["result"]
    return JsonResponse({
        "ready": True,
        "result": result,
        "raw_text": text.strip(),
        "source": result["source"],
    })


@login_required
def parse_status(request, job_uuid):
    """Pollable status endpoint for async AI parse jobs."""
    try:
        job = ParseJob.objects.get(uuid=job_uuid, user=request.user)
    except ParseJob.DoesNotExist:
        return JsonResponse({"status": "error", "error": "解析任务不存在或已过期。"}, status=404)

    if job.status == "done" and job.result:
        r = job.result
        return JsonResponse({
            "status": "done",
            "result": r,
            "raw_text": job.raw_text,
            "source": r.get("source"),
        })
    if job.status == "error":
        return JsonResponse({"status": "error", "error": job.error or "解析失败。"})
    return JsonResponse({"status": "pending"})


def _parse_source(action, default="ai"):
    """Read the `source` tag from an action, falling back to `default`.
    Only values declared on Task.Source are accepted; anything else is treated
    as the default so the UI never shows a bogus source label."""
    source = action.get("source") or default
    return source if source in dict(Task.Source.choices) else default


def _parse_amount(amount_str):
    """Return a Decimal, or None when the amount is empty.
    Raises ValueError on a non-empty but unparseable amount."""
    if amount_str in (None, ""):
        return None
    try:
        return Decimal(str(amount_str))
    except InvalidOperation:
        raise ValueError(f"金额格式无效: {amount_str}")


def _parse_aware(dt_str):
    """Parse an ISO-8601 datetime string into an Asia/Shanghai-aware datetime.
    Returns None on missing/invalid input so each handler can pick its own
    fallback (e.g. expenses default to now, reminders fall back to due_at)."""
    if not dt_str:
        return None
    try:
        raw = datetime.fromisoformat(str(dt_str).replace("Z", "+00:00"))
        return timezone.make_aware(raw) if timezone.is_naive(raw) else raw
    except ValueError:
        return None


def _prepare_action(action, raw_text):
    """Shared parsing & validation for every action intent.

    Mirrors the validation that used to live inline in confirm_actions:
    task/reminder/note require a non-empty, non-placeholder title; the amount
    is parsed up-front; due_at is bumped for task/reminder so it never lands in
    the past. Raises ValueError → the whole batch rolls back.
    """
    intent = action.get("intent", "")
    title = str(action.get("title", ""))[:200]
    cleaned_title = title.strip()
    if intent in ("create_task", "create_reminder", "create_note"):
        if not cleaned_title:
            raise ValueError("任务/提醒/笔记必须有标题，请在草稿卡中填写后再确认保存")
        if is_placeholder_title(cleaned_title):
            raise ValueError(
                f"标题「{cleaned_title}」是占位词，无业务含义。请在草稿卡里改成具体的事情"
                f"（例如：电瓶车充电、交话费、完善项目）"
            )
    due_at = _parse_aware(action.get("due_at"))
    if intent in ("create_task", "create_reminder"):
        due_at = bump_overdue_due(due_at)
    return {
        "title": title,
        "source": _parse_source(action),
        "amount": _parse_amount(action.get("amount")),
        "occurred_at": _parse_aware(action.get("occurred_at")),
        "due_at": due_at,
        "category_name": action.get("category", ""),
        "raw_text": raw_text,
    }


def _handle_expense_or_income(user, action, p, raw_text):
    if p["amount"] is None:
        raise ValueError(f"支出/收入必须提供金额: {p['title']}")
    cat = resolve_category(user, p["category_name"]) if p["category_name"] else None
    Expense.objects.create(
        user=user,
        type="income" if action.get("intent") == "create_income" else "expense",
        category=cat,
        amount=p["amount"],
        occurred_at=p["occurred_at"] or timezone.now(),
        note=p["title"],
        raw_text=raw_text,
        source=p["source"],
    )
    return {"intent": action.get("intent"), "title": p["title"], "ok": True}


def _handle_recurring(user, action, p, raw_text):
    if p["amount"] is None:
        raise ValueError(f"固定账单必须提供金额: {p['title']}")
    cat = resolve_category(user, p["category_name"]) if p["category_name"] else None
    frequency = action.get("frequency") or "monthly"
    if frequency not in dict(RecurringExpense.Frequency.choices):
        frequency = "monthly"
    if p["occurred_at"]:
        due_day = p["occurred_at"].day
        start_date = p["occurred_at"].date()
    else:
        today_loc = timezone.localdate()
        due_day = today_loc.day
        start_date = today_loc
    RecurringExpense.objects.create(
        user=user, name=p["title"], category=cat, amount=p["amount"],
        frequency=frequency, due_day=due_day, start_date=start_date,
        remind_days_before=3, is_active=True,
    )
    return {"intent": "create_recurring_expense", "title": p["title"], "ok": True}


def _handle_task(user, action, p, raw_text):
    from datetime import timedelta as _td
    title = p["title"]
    due_at = p["due_at"]
    if due_at:
        lo = due_at - _td(minutes=5)
        hi = due_at + _td(minutes=5)
        dup = Task.objects.filter(
            user=user, title=title, status__in=["todo", "in_progress"],
            is_deleted=False, due_at__gte=lo, due_at__lte=hi,
        ).first()
    else:
        today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        dup = Task.objects.filter(
            user=user, title=title, status__in=["todo", "in_progress"],
            is_deleted=False, created_at__gte=today_start,
        ).first()
    if dup:
        return {"intent": "create_task", "title": title, "ok": True, "deduped": True, "pk": dup.pk}
    Task.objects.create(
        user=user, title=title, description="", due_at=due_at,
        source=p["source"], raw_text=raw_text,
    )
    return {"intent": "create_task", "title": title, "ok": True}


def _handle_reminder(user, action, p, raw_text):
    event = p["occurred_at"] or p["due_at"] or timezone.now()
    Reminder.objects.create(
        user=user, title=p["title"], reminder_type="custom",
        event_at=event, remind_at=event,
    )
    return {"intent": "create_reminder", "title": p["title"], "ok": True}


def _handle_note(user, action, p, raw_text):
    Note.objects.create(user=user, title=p["title"], raw_text=raw_text)
    return {"intent": "create_note", "title": p["title"], "ok": True}


def _handle_daily(user, action, p, raw_text):
    from .models_daily import DailyCheckin
    icon = action.get("icon", "") or ""
    icon = icon[:4] if isinstance(icon, str) else ""
    DailyCheckin.objects.create(user=user, title=p["title"][:100], icon=icon or "📌")
    return {"intent": "create_daily_reminder", "title": p["title"], "ok": True}


# intent → handler. Adding a new action type is now a one-line registration.
ACTION_HANDLERS = {
    "create_expense": _handle_expense_or_income,
    "create_income": _handle_expense_or_income,
    "create_recurring_expense": _handle_recurring,
    "create_task": _handle_task,
    "create_reminder": _handle_reminder,
    "create_note": _handle_note,
    "create_daily_reminder": _handle_daily,
}


@login_required
@require_POST
def confirm_actions(request):
    """Batch-confirm multiple AI-parsed actions in a single transaction.

    Accepts: {"actions": [{"intent": ..., ...}, ...], "raw_text": "..."}
    On partial failure, rolls back ALL changes.
    """
    from django.db import transaction
    try:
        payload = json.loads(request.body)
        actions = payload["actions"]
        raw_text = payload.get("raw_text", "")
    except (json.JSONDecodeError, KeyError):
        return HttpResponseBadRequest("请求格式无效。")

    if not isinstance(actions, list) or len(actions) == 0:
        return HttpResponseBadRequest("至少需要一条操作。")

    saved = []
    try:
        with transaction.atomic():
            for action in actions:
                handler = ACTION_HANDLERS.get(action.get("intent", ""))
                if handler is None:
                    raise ValueError(f"未知的操作类型: {action.get('intent')}")
                prepared = _prepare_action(action, raw_text)
                saved.append(handler(request.user, action, prepared, raw_text))
    except Exception as e:
        # Transaction rolled back automatically
        return JsonResponse({"ok": False, "error": str(e)[:300], "saved": saved}, status=400)

    record(request.user, "ai.save", None, f"批量确认 {len(saved)} 条记录")
    # count 仅统计「实际新建」（去重跳过的不算）—— 前端 toast 文案「已记录 N 条」要准确
    new_count = sum(1 for s in saved if not s.get("deduped"))
    return JsonResponse({
        "ok": True,
        "count": new_count,
        "deduped": sum(1 for s in saved if s.get("deduped")),
        "saved": saved,   # 保留明细，前端可知道哪条被去重
    })


@login_required
@require_POST
def quick_add_expense(request):
    """悬浮按钮的「快速记账」接口。

    目标：3 秒内完成一笔记录——只填金额，分类可选，备注可选。
    与首页 AI 输入互补：AI 适合自然语言，这里适合「我已经知道金额」的场景。

    接受 JSON: {"amount": "18.5", "type": "expense"|"income",
                "category_id": 可选, "note": 可选}
    """
    from django.db.models import Q

    from .models import Category

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "请求格式无效。"}, status=400)

    raw_amount = str(payload.get("amount", "")).strip()
    type_ = payload.get("type", "expense")
    note = str(payload.get("note", "")).strip()[:500]

    if type_ not in ("expense", "income"):
        return JsonResponse({"ok": False, "error": "类型只能是支出或收入。"}, status=400)

    try:
        amount = Decimal(raw_amount)
    except (InvalidOperation, ValueError):
        return JsonResponse({"ok": False, "error": "请输入有效的金额。"}, status=400)
    if amount <= 0:
        return JsonResponse({"ok": False, "error": "金额必须大于 0。"}, status=400)
    if amount.as_tuple().exponent < -2:
        return JsonResponse({"ok": False, "error": "金额最多保留两位小数。"}, status=400)

    # 分类必须属于当前用户或是全局分类，且类型匹配——防止越权引用他人分类
    category = None
    cat_id = payload.get("category_id")
    if cat_id:
        category = Category.objects.filter(
            Q(user=request.user) | Q(user__isnull=True),
            pk=cat_id, type=type_, is_active=True,
        ).first()

    expense = Expense.objects.create(
        user=request.user,
        type=type_,
        amount=amount,
        category=category,
        note=note,
        occurred_at=timezone.now(),
        status="confirmed",
        source="manual",
    )
    record(request.user, "expense.create", expense.pk, f"快速记账: {expense.display_title} ¥{amount}")

    return JsonResponse({
        "ok": True,
        "id": expense.pk,
        "amount": str(expense.amount),
        "type": expense.type,
        "type_display": expense.get_type_display(),
        "note": expense.note,
        "category": expense.category.name if expense.category else "",
        "occurred_at": expense.occurred_at.strftime("%Y-%m-%d %H:%M"),
    })


@login_required
def quick_categories(request):
    """快速记账面板用的分类列表。

    按需懒加载（面板首次打开时才请求），避免给每个页面都增加一次查询。
    返回当前用户自建分类 + 全局分类（user 为空的），按类型过滤。
    """
    from django.db.models import Q

    from .models import Category

    type_ = request.GET.get("type", "expense")
    if type_ not in ("expense", "income"):
        type_ = "expense"

    cats = Category.objects.filter(
        Q(user=request.user) | Q(user__isnull=True),
        type=type_, is_active=True,
    ).order_by("name").values("id", "name")

    return JsonResponse({"ok": True, "type": type_, "categories": list(cats)})


