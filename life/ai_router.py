"""Parsing router: rule-first, AI fallback.

Strategy:
  1. Local rule parser runs first
  2. If result is complete & single-intent → return rule result
  3. If incomplete or multi-intent → call AI provider (async-friendly)
  4. AI unavailable → return rule draft or raw text as fallback
"""

import logging
import re
import threading
import uuid as _uuid

from django.contrib.auth import get_user_model
from django.db import connection

logger = logging.getLogger(__name__)

from .ai_provider import get_provider
from .ai_schema import validate_ai_response
from .models import ConversationLog, ParseJob
from .parser import parse_text


def _rule_confidence(draft: dict) -> str:
    """Heuristic confidence of a rule-parsed draft: high / medium / low."""
    kind = draft.get("kind", "note")
    has_amount = draft.get("amount") is not None
    has_category = bool(draft.get("category", ""))

    if kind == "expense" and has_amount and has_category:
        return "high"
    if kind == "recurring_expense" and has_amount and has_category:
        return "high"
    if kind == "income" and has_amount:
        return "high"
    if kind == "task" and bool(draft.get("due_at")):
        return "high"
    if kind == "daily_reminder" and bool(draft.get("title")):
        return "high"
    if kind == "expense" and has_amount and not has_category:
        return "medium"
    if kind == "task" and not draft.get("due_at"):
        return "medium"
    if kind == "note":
        return "medium"
    return "low"


def _detect_multi_intent(text: str) -> bool:
    """Heuristic: does the text contain multiple actionable items?

    Catches the common mixed cases so they get routed to the (AI / FakeProvider)
    splitter instead of a single rule draft that silently drops one intent:
      - 2+ amounts                       → "午饭18元，打车20元"
      - expense + task/reminder cue      → "午饭18元，提醒我交话费"
      - 2+ task/reminder cues            → "提醒我A，记得B"
      - amount (any) + recurrence word   → 固定账单 + 其它
    """
    import re
    # Multiple amounts
    amounts = re.findall(r"\d+(?:\.\d{1,2})?\s*(?:元|块|块钱)", text)
    if len(amounts) >= 2:
        return True
    # Expense + task mixed
    has_expense = bool(amounts)
    has_task = any(w in text for w in ("提醒", "要做", "待办", "记得", "安排"))
    if has_expense and has_task:
        return True
    # 2+ independent task/reminder cues in one sentence
    task_cues = ("提醒", "记得", "待办", "要做", "帮我安排", "别忘了")
    if sum(1 for w in task_cues if w in text) >= 2:
        return True
    # Recurrence word + any amount → 固定账单 likely coexists with another intent
    if any(w in text for w in ("固定账单", "每个月", "每月", "每周", "每年")) and amounts:
        return True
    return False


_ABS_DATE_RE = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?")
_ABS_DATE_RE2 = re.compile(r"(?<!\d)(\d{1,2})\s*[/\-]\s*(\d{1,2})(?!\d|\.)")


def _abs_date_in_text(text: str):
    """Return the absolute date (date obj) explicitly mentioned in text, else None.

    Only matches *explicit* calendar dates (X月X号 / X/X) — NOT relative words
    (今天/明天/后天) which the parser handles reliably and don't need confirmation.
    """
    from django.utils import timezone

    today = timezone.localdate()
    m = _ABS_DATE_RE.search(text) or _ABS_DATE_RE2.search(text)
    if not m:
        return None
    try:
        am, ad = int(m.group(1)), int(m.group(2))
        if not (1 <= am <= 12 and 1 <= ad <= 31):
            return None
        d = today.replace(month=am, day=ad)
        if d < today:
            d = d.replace(year=d.year + 1)
        return d
    except ValueError:
        return None


_PLACEHOLDER_TITLES = ("任务", "提醒", "待办", "事件", "事项")


def _attach_meta(raw_text: str, result: dict) -> dict:
    """Augment each parsed action with UI hints for the date-confirm modal
    and the clarification (反问) flow. Purely additive — never mutates existing
    fields, so it can't break downstream consumers or schema validation."""
    abs_d = _abs_date_in_text(raw_text)
    for a in result.get("actions", []):
        ds = a.get("due_at") or a.get("occurred_at") or ""
        detected = ""
        if isinstance(ds, str) and len(ds) >= 10:
            detected = ds[:10]  # YYYY-MM-DD
        a["detected_date"] = detected
        # 确认弹窗：用户明确打了绝对日期，且解析结果确实落到了那一天 → 需要二次确认
        a["needs_date_confirm"] = bool(abs_d) and detected == abs_d.isoformat()
        # 反问：任务/提醒/笔记没有可用标题（空或占位词）→ 让用户在确认前补具体内容
        a["clarify"] = ""
        if a.get("intent") in ("create_task", "create_reminder", "create_note"):
            t = (a.get("title") or "").strip()
            if not t or t in _PLACEHOLDER_TITLES:
                a["clarify"] = "这件事具体要做什么？举个例子：交话费 / 完善项目"
    return result


SENSITIVE_PATTERNS = [
    ("身份证", "身份证号"),
    ("银行卡", "银行卡号"),
    ("密码", "密码"),
    ("验证码", "验证码"),
]


def _check_sensitive(text: str) -> list:
    found = []
    for kw, label in SENSITIVE_PATTERNS:
        if kw in text:
            found.append(label)
    return found


def _decide(raw_text: str, user=None):
    """Decide the parse path and pre-build the synchronous result.

    Returns ``(mode, result)`` where ``mode`` is one of:
      - ``"rule"``     : high-confidence single intent → rule result is final
      - ``"fallback"``  : AI disabled / rate-limited / sensitive → rule draft is final
      - ``"ai"``        : needs the AI provider → result is ``None`` (caller runs AI)
    """
    draft = parse_text(raw_text)
    conf = _rule_confidence(draft)
    is_multi = _detect_multi_intent(raw_text)

    if conf == "high" and not is_multi:
        return "rule", _attach_meta(raw_text, {
            "actions": [_draft_to_action(draft)],
            "source": "rule",
            "confidence": "high",
            "error": None,
        })

    skip_reason = None
    if user and hasattr(user, 'profile'):
        if not user.profile.ai_parsing_enabled:
            skip_reason = "AI 解析已关闭，请在个人设置中开启。"
        else:
            limit = user.profile.daily_ai_limit
            if limit > 0:
                from django.utils import timezone
                today = timezone.localdate()
                today_count = ConversationLog.objects.filter(user=user, created_at__date=today).count()
                if today_count >= limit:
                    skip_reason = f"今日 AI 调用已达上限（{limit}次）。"

    if skip_reason:
        return "fallback", _attach_meta(raw_text, {
            "actions": [_draft_to_action(draft)],
            "source": "fallback",
            "confidence": "low",
            "error": skip_reason,
        })

    sensitive = _check_sensitive(raw_text)
    if sensitive:
        return "fallback", _attach_meta(raw_text, {
            "actions": [_draft_to_action(draft)],
            "source": "fallback",
            "confidence": "low",
            "error": f"输入包含疑似敏感信息: {', '.join(sensitive)}，已使用本地解析。",
            "sensitive": True,
        })

    return "ai", None


def _call_ai(raw_text: str, user=None) -> dict:
    """Run the AI provider and return a fully-formed parse result.

    Any failure degrades gracefully to a local rule draft (source="fallback")
    so the caller always gets a usable result.
    """
    draft = parse_text(raw_text)
    try:
        provider = get_provider()
        ai_result = provider.parse(raw_text)
        ok, errors = validate_ai_response(ai_result)
        if ok:
            if user is not None:
                ConversationLog.objects.create(
                    user=user, raw_text=raw_text, input_type="text",
                    model="deepseek-chat", status="confirmed",
                )
            return _attach_meta(raw_text, {
                "actions": ai_result["actions"],
                "source": "ai",
                "confidence": "medium",
                "error": None,
            })
            logger.warning("AI response validation failed for text=%r: %s", raw_text[:200], errors)
            return _attach_meta(raw_text, {
                "actions": [_draft_to_action(draft)],
                "source": "fallback",
                "confidence": "low",
                "error": f"AI validation failed: {'; '.join(errors)}",
            })
    except Exception as e:
        logger.exception("AI parse call failed for text=%r; using rule fallback", raw_text[:200])
        return _attach_meta(raw_text, {
            "actions": [_draft_to_action(draft)],
            "source": "fallback",
            "confidence": "low",
            "error": f"AI error: {str(e)[:200]}",
        })


def route_parse(raw_text: str, user=None) -> dict:
    """Parse text with rule-first + AI fallback (synchronous).

    Kept for tests / synchronous callers. Returns the same shape as before:
      {"actions": [...], "source": "rule"|"ai"|"fallback", "confidence": ..., "error": ...}
    """
    mode, result = _decide(raw_text, user)
    if mode == "ai":
        return _call_ai(raw_text, user)
    return result


def route_parse_split(raw_text: str, user=None) -> dict:
    """Async-friendly parse entry.

    Rule parse / local fallback return immediately (``ready=true``). When the AI
    provider is needed, a background thread runs it and writes the result to a
    ``ParseJob`` row; we return ``ready=false`` + ``job_id`` so the frontend can
    poll ``/api/parse-status/<job_id>/`` — this keeps a ~30s AI call from
    blocking the web worker.
    """
    mode, result = _decide(raw_text, user)
    if mode != "ai":
        return {"ready": True, "result": result, "raw_text": raw_text}

    job = ParseJob.objects.create(
        user=user, raw_text=raw_text, uuid=_uuid.uuid4().hex, status="pending",
    )
    threading.Thread(
        target=_run_ai_job,
        args=(job.uuid, raw_text, user.id if user else None),
        daemon=True,
    ).start()
    return {"ready": False, "job_id": job.uuid, "raw_text": raw_text}


def _run_ai_job(job_uuid: str, raw_text: str, user_id=None):
    """Background worker: run AI parse and persist the result to ParseJob.

    Runs in its own thread with an isolated DB connection (Django connections are
    thread-local); the connection is closed in ``finally`` so it never leaks into
    the request thread. Any failure degrades to a local rule draft.
    """
    try:
        job = ParseJob.objects.get(uuid=job_uuid)
        job.status = "running"
        job.save(update_fields=["status"])

        user = None
        if user_id is not None:
            try:
                user = get_user_model().objects.get(pk=user_id)
            except Exception:
                user = None

        result = _call_ai(raw_text, user)
        job.result = result
        job.status = "done"
        job.save()
    except Exception as e:  # noqa: BLE001 - last-resort guard so job never hangs
        logger.exception("ParseJob %s worker crashed; returning rule fallback", job_uuid)
        try:
            job = ParseJob.objects.get(uuid=job_uuid)
            draft = parse_text(raw_text)
            job.result = _attach_meta(raw_text, {
                "actions": [_draft_to_action(draft)],
                "source": "fallback",
                "confidence": "low",
                "error": f"AI worker error: {str(e)[:200]}",
            })
            job.error = str(e)[:500]
            job.status = "done"
            job.save()
        except Exception:
            pass
    finally:
        connection.close()


def _draft_to_action(draft: dict) -> dict:
    """Convert a legacy parser draft to the new action format."""
    kind = draft.get("kind", "note")
    intent_map = {
        "expense": "create_expense",
        "income": "create_income",
        "task": "create_task",
        "note": "create_note",
        "recurring_expense": "create_recurring_expense",
        "daily_reminder": "create_daily_reminder",
    }
    action = {
        "intent": intent_map.get(kind, "create_note"),
        "action_id": "a1",
        "title": draft.get("title", ""),
        "category": draft.get("category", ""),
        "amount": draft.get("amount"),
        "occurred_at": draft.get("occurred_on"),
        "due_at": draft.get("due_at"),
        "frequency": draft.get("frequency"),
        "icon": draft.get("icon"),
        "source": draft.get("source", "rule"),
    }
    # Propagate parser-level sanity warnings (e.g. invalid date → fell back to today)
    # so the frontend can surface a friendly hint instead of a silently-wrong action.
    if draft.get("validation_warning"):
        action["validation_warning"] = draft["validation_warning"]
    # Clean None values
    return {k: v for k, v in action.items() if v is not None}
