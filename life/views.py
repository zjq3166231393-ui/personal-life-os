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
from .parser import parse_text
from .services import (
    bump_overdue_due,
    home_data,
    is_placeholder_title,
    resolve_category,
)


@login_required
def home(request):
    _auto_post_recurring(request)
    return render(request, "life/home.html", home_data(request.user))


def _auto_post_recurring(request):
    """首页惰性触发固定支出自动入账（P0-1）。

    没有 cron 的部署环境（Railway / SQLite）靠这里兜底：用户每天开一次首页，
    到期的房租、话费、订阅就会自动记账。同一天只跑一次（缓存节流），
    且生成逻辑本身幂等，重复调用不会重复入账。
    """
    from django.contrib import messages

    from .recurring import maybe_generate_for_user

    try:
        stats = maybe_generate_for_user(request.user)
    except Exception:  # pragma: no cover — 自动记账失败绝不能拖垮首页
        return
    if stats and stats.get("created"):
        names = "、".join(sorted({n for n, _d in stats["dates"]})[:3])
        more = "等" if len({n for n, _d in stats["dates"]}) > 3 else ""
        messages.info(request, f"已自动记账 {stats['created']} 笔固定支出：{names}{more}")


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


@login_required
@require_POST
def voice_expense(request):
    """语音记账：接收语音转写文本，用规则解析器同步提取金额/分类/类型并直接入账。

    相比 /api/parse/ 的 AI 异步轮询，语音记账走同步规则解析（parser.parse_text），
    识别即记、无需等待，契合「按住说话 → 松手入账」的极速体验（对标随手记/叨叨）。
    仅处理记账类意图（支出/收入/固定账单）；任务/提醒/笔记等非记账意图返回友好提示，
    并把识别文本交回前端放入备注，由用户手动处理。
    """
    try:
        payload = json.loads(request.body)
        text = payload.get("text", "")
    except (json.JSONDecodeError, AttributeError):
        return HttpResponseBadRequest("请提供语音转写文本。")
    if not isinstance(text, str) or not text.strip():
        return HttpResponseBadRequest("请提供语音转写文本。")

    draft = parse_text(text)
    kind = draft.get("kind")

    if kind in ("expense", "income", "recurring_expense"):
        amount = draft.get("amount")
        if amount in (None, ""):
            return JsonResponse({
                "ok": False,
                "error": "没听清金额，可手动补充或改用快速记账。",
                "raw_text": text.strip(),
                "title": draft.get("title", ""),
            }, status=200)
        try:
            amount_d = Decimal(str(amount))
        except (InvalidOperation, ValueError):
            return JsonResponse({
                "ok": False, "error": f"金额无法识别：{amount}",
                "raw_text": text.strip(), "title": draft.get("title", ""),
            }, status=200)

        cat = resolve_category(request.user, draft.get("category") or "") if draft.get("category") else None
        # 名称解析失败时，退回到「商户/标题关键字 → 分类」规则，提升语音自动归类率
        if cat is None:
            from .category_rules import match_category

            cat = match_category(
                request.user, draft.get("title") or text, type_="income" if kind == "income" else "expense"
            )

        if kind == "recurring_expense":
            today_loc = timezone.localdate()
            RecurringExpense.objects.create(
                user=request.user, name=draft.get("title") or "固定支出",
                category=cat, amount=amount_d, frequency=draft.get("frequency") or "monthly",
                due_day=today_loc.day, start_date=today_loc, remind_days_before=3, is_active=True,
            )
            return JsonResponse({
                "ok": True, "kind": "recurring_expense",
                "amount": str(amount_d), "category": cat.name if cat else "",
                "title": draft.get("title") or "",
            })

        Expense.objects.create(
            user=request.user,
            type="income" if kind == "income" else "expense",
            category=cat,
            amount=amount_d,
            occurred_at=timezone.now(),
            note=draft.get("title") or "",
            raw_text=text.strip(),
            source="voice",
        )
        return JsonResponse({
            "ok": True, "kind": kind,
            "type_display": "收入" if kind == "income" else "支出",
            "amount": str(amount_d),
            "category": cat.name if cat else "",
            "note": draft.get("title") or "",
        })

    # 非记账意图（任务/提醒/笔记等）：语音记账只管钱，给友好提示并交回文本
    return JsonResponse({
        "ok": False,
        "error": "听起来不像一笔账（可能是任务/提醒）。已放到备注，可手动记账。",
        "raw_text": text.strip(),
        "title": draft.get("title", ""),
    }, status=200)


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

    from .currency import BASE_CURRENCY, CURRENCY_META
    from .models import Account, Category

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "请求格式无效。"}, status=400)

    raw_amount = str(payload.get("amount", "")).strip()
    type_ = payload.get("type", "expense")
    note = str(payload.get("note", "")).strip()[:500]

    # 多币种（P1-5）：币种 + 可选汇率
    currency = str(payload.get("currency", BASE_CURRENCY)).strip().upper()
    if currency not in CURRENCY_META:
        currency = BASE_CURRENCY
    rate = Decimal("1")
    raw_rate = payload.get("rate")
    if raw_rate not in (None, "", 0, "0"):
        try:
            rate = Decimal(str(raw_rate))
            if rate <= 0:
                rate = Decimal("1")
        except (InvalidOperation, ValueError):
            rate = Decimal("1")

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
    auto_matched = False
    cat_id = payload.get("category_id")
    if cat_id:
        category = Category.objects.filter(
            Q(user=request.user) | Q(user__isnull=True),
            pk=cat_id, type=type_, is_active=True,
        ).first()
    # 未显式选分类时，按备注/商户规则自动归类，减少重复手动选择
    if category is None and note:
        from .category_rules import match_category

        category = match_category(request.user, note, type_)
        if category is not None:
            auto_matched = True

    # 账户同理：只能选自己的、且启用中的账户，越权/失效的 account_id 直接忽略（不报错，避免阻断记账）
    account = None
    acc_id = payload.get("account_id")
    if acc_id:
        account = Account.objects.filter(
            pk=acc_id, user=request.user, is_deleted=False, is_active=True
        ).first()

    expense = Expense.objects.create(
        user=request.user,
        type=type_,
        amount=amount,
        currency=currency,
        rate=rate,
        category=category,
        account=account,
        note=note,
        occurred_at=timezone.now(),
        status="confirmed",
        source="manual",
    )
    record(request.user, "expense.create", expense.pk, f"快速记账: {expense.display_title} {expense.display_amount}")

    return JsonResponse({
        "ok": True,
        "id": expense.pk,
        "amount": str(expense.amount),
        "type": expense.type,
        "type_display": expense.get_type_display(),
        "note": expense.note,
        "category": expense.category.name if expense.category else "",
        "account": expense.account.name if expense.account else "",
        "currency": expense.currency,
        "rate": str(expense.rate),
        "amount_base": str(expense.amount_base) if expense.amount_base is not None else "",
        "occurred_at": expense.occurred_at.strftime("%Y-%m-%d %H:%M"),
        "auto_matched": auto_matched,
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


