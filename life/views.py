import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from django.contrib.auth.decorators import login_required

from common.audit import record
from django.db.models import Q
from .ai_router import route_parse
from .models import Budget, Category, Expense, InstallmentPlan, Note, RecurringExpense, Reminder, Task
from .services import bump_overdue_due, home_data, is_placeholder_title, resolve_category
from .parser import parse_text


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

    result = route_parse(text, user=request.user)
    return JsonResponse({
        "result": result,
        "raw_text": text.strip(),
        "source": result["source"],
    })


@login_required
@require_POST
def confirm_actions(request):
    """Batch-confirm multiple AI-parsed actions in a single transaction.

    Accepts: {"actions": [{"intent": "create_expense", "title": ..., "amount": ..., ...}, ...], "raw_text": "..."}
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
                intent = action.get("intent", "")
                title = str(action.get("title", ""))[:200]
                category_name = action.get("category", "")
                amount_str = action.get("amount")
                # 来源字段：rule（规则解析）/ ai（AI 解析）/ voice（语音）/ text（文本）/
                # manual（手填）/ fallback（AI 失败回退）。允许前端的 source 透传，便于
                # 在任务详情/列表里正确展示来源，而不是统一显示 "AI"。
                source = action.get("source") or "ai"
                if source not in dict(Task.Source.choices):
                    source = "ai"

                # Parse amount
                amount = None
                if amount_str not in (None, ""):
                    try:
                        amount = Decimal(str(amount_str))
                    except InvalidOperation:
                        raise ValueError(f"金额格式无效: {amount_str}")

                # Parse datetime
                # 前端 `<input type="date">` 提交 "YYYY-MM-DDTHH:MM:SS"（无 tz）→ naive。
                # 与 USE_TZ=True 下 Django 内部的 aware 时序比较会抛 TypeError，
                # 所以这里统一 make_aware 到 Asia/Shanghai。
                occurred_at = None
                if action.get("occurred_at"):
                    try:
                        raw = datetime.fromisoformat(str(action["occurred_at"]).replace("Z", "+00:00"))
                        occurred_at = timezone.make_aware(raw) if timezone.is_naive(raw) else raw
                    except ValueError:
                        occurred_at = timezone.now()

                due_at = None
                if action.get("due_at"):
                    try:
                        raw = datetime.fromisoformat(str(action["due_at"]).replace("Z", "+00:00"))
                        due_at = timezone.make_aware(raw) if timezone.is_naive(raw) else raw
                    except ValueError:
                        pass

                # 校验：任务/提醒/笔记必须有非空标题，且不能是占位字面词（防御性约束）
                cleaned_title = title.strip()
                if intent in ("create_task", "create_reminder", "create_note"):
                    if not cleaned_title:
                        raise ValueError("任务/提醒/笔记必须有标题，请在草稿卡中填写后再确认保存")
                    if is_placeholder_title(cleaned_title):
                        raise ValueError(
                            f"标题「{cleaned_title}」是占位词，无业务含义。请在草稿卡里改成具体的事情（例如：电瓶车充电、交话费、完善项目）"
                        )
                # 任务/提醒：如果 due_at 早于当前，自动顺延到次日同一时刻，避免「立即过期」
                if intent in ("create_task", "create_reminder"):
                    due_at = bump_overdue_due(due_at)
                if intent in ("create_expense", "create_income"):
                    if amount is None:
                        raise ValueError(f"支出/收入必须提供金额: {title}")
                    cat = None
                    if category_name:
                        cat = resolve_category(request.user, category_name)
                        Expense.objects.create(
                            user=request.user, type="income" if intent == "create_income" else "expense",
                            category=cat, amount=amount,
                            occurred_at=occurred_at or timezone.now(),
                        note=title, raw_text=raw_text, source=source,
                    )

                elif intent == "create_recurring_expense":
                    # 固定账单：直接创建 RecurringExpense，无需用户去手动点。
                    if amount is None:
                        raise ValueError(f"固定账单必须提供金额: {title}")
                    cat = None
                    if category_name:
                        cat = resolve_category(request.user, category_name)
                    # 周期：默认 monthly；weekly/quarterly/yearly 从 action 里读
                    frequency = action.get("frequency") or "monthly"
                    if frequency not in dict(RecurringExpense.Frequency.choices):
                        frequency = "monthly"
                    # 扣款日：尽量从 occurred_at 取「日」部分，否则用今天
                    from datetime import date as _date_cls
                    if occurred_at:
                        due_day = occurred_at.day
                        start_date = occurred_at.date()
                    else:
                        from django.utils import timezone as _tz
                        today_loc = _tz.localdate()
                        due_day = today_loc.day
                        start_date = today_loc
                    RecurringExpense.objects.create(
                        user=request.user,
                        name=title,
                        category=cat,
                        amount=amount,
                        frequency=frequency,
                        due_day=due_day,
                        start_date=start_date,
                        remind_days_before=3,
                        is_active=True,
                    )

                elif intent == "create_task":
                    # ── 去重（2026-08-24）─
                    # 同一用户、同标题、due_at 在 ±5 分钟内的待办/进行中任务视为重复，
                    # 跳过创建，避免语音解析两次产生两条「线上面试 08/25」。
                    from datetime import timedelta as _td
                    if due_at:
                        lo = due_at - _td(minutes=5)
                        hi = due_at + _td(minutes=5)
                        dup = Task.objects.filter(
                            user=request.user, title=title,
                            status__in=["todo", "in_progress"], is_deleted=False,
                            due_at__gte=lo, due_at__lte=hi,
                        ).first()
                    else:
                        # 没指定时间时，按「同日同标题」判重（防止 1 天内重复添加「X」任务）
                        from django.utils import timezone as _tz
                        today_start = _tz.now().replace(hour=0, minute=0, second=0, microsecond=0)
                        dup = Task.objects.filter(
                            user=request.user, title=title,
                            status__in=["todo", "in_progress"], is_deleted=False,
                            created_at__gte=today_start,
                        ).first()
                    if dup:
                        saved.append({"intent": intent, "title": title, "ok": True, "deduped": True, "pk": dup.pk})
                        continue
                    Task.objects.create(
                        user=request.user, title=title, description="",
                        due_at=due_at, source=source, raw_text=raw_text,
                    )

                elif intent == "create_reminder":
                    event = occurred_at or due_at or timezone.now()
                    Reminder.objects.create(
                        user=request.user, title=title, reminder_type="custom",
                        event_at=event, remind_at=event,
                    )

                elif intent == "create_note":
                    Note.objects.create(user=request.user, title=title, raw_text=raw_text)

                elif intent == "create_daily_reminder":
                    from .models_daily import DailyCheckin
                    icon = action.get("icon", "") or ""
                    icon = icon[:4] if isinstance(icon, str) else ""
                    DailyCheckin.objects.create(
                        user=request.user, title=title[:100], icon=icon or "📌"
                    )

                saved.append({"intent": intent, "title": title, "ok": True})

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


