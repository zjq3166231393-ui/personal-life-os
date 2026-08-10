"""Parsing router: rule-first, AI fallback.

Strategy:
  1. Local rule parser runs first
  2. If result is complete & single-intent → return rule result
  3. If incomplete or multi-intent → call AI provider
  4. AI unavailable → return rule draft or raw text as fallback
"""

from .parser import parse_text
from .ai_provider import get_provider
from .ai_schema import validate_ai_response


def _rule_confidence(draft: dict) -> str:
    """Heuristic confidence of a rule-parsed draft: high / medium / low."""
    kind = draft.get("kind", "note")
    has_amount = draft.get("amount") is not None
    has_category = bool(draft.get("category", ""))

    if kind == "expense" and has_amount and has_category:
        return "high"
    if kind == "income" and has_amount:
        return "high"
    if kind == "task" and bool(draft.get("due_at")):
        return "high"
    if kind == "expense" and has_amount and not has_category:
        return "medium"
    if kind == "task" and not draft.get("due_at"):
        return "medium"
    if kind == "note":
        return "medium"
    return "low"


def _detect_multi_intent(text: str) -> bool:
    """Simple heuristic: does the text contain multiple actionable items?"""
    import re
    # Multiple amounts
    amounts = re.findall(r"\d+(?:\.\d{1,2})?\s*(?:元|块|块钱)", text)
    if len(amounts) >= 2:
        return True
    # Expense + task mixed
    has_expense = (any(w in text for w in ("花了", "买", "吃", "喝", "饭", "菜", "午餐", "晚餐")) and bool(amounts)) or bool(amounts)
    has_task = any(w in text for w in ("提醒", "要做", "待办", "记得", "安排"))
    if has_expense and has_task:
        return True
    return False


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


def route_parse(raw_text: str, user=None) -> dict:
    """Parse text with rule-first + AI fallback.

    Returns:
      {
        "actions": [...],
        "source": "rule" | "ai" | "fallback",
        "confidence": "high" | "medium" | "low",
        "error": None | "error message",
      }
    """
    # Step 1: rule parse
    draft = parse_text(raw_text)
    conf = _rule_confidence(draft)
    is_multi = _detect_multi_intent(raw_text)

    # If high confidence single intent → skip AI
    if conf == "high" and not is_multi:
        return {
            "actions": [_draft_to_action(draft)],
            "source": "rule",
            "confidence": "high",
            "error": None,
        }

    # Step 2: try AI for medium/low or multi-intent
    # Check AI switch and limits
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
        return {
            "actions": [_draft_to_action(draft)],
            "source": "fallback",
            "confidence": "low",
            "error": skip_reason,
        }

    # Check sensitive content
    sensitive = _check_sensitive(raw_text)
    if sensitive:
        return {
            "actions": [_draft_to_action(draft)],
            "source": "fallback",
            "confidence": "low",
            "error": f"输入包含疑似敏感信息: {', '.join(sensitive)}，已使用本地解析。",
            "sensitive": True,
        }

    try:
        provider = get_provider()
        ai_result = provider.parse(raw_text)
        ok, errors = validate_ai_response(ai_result)
        if ok:
            # Log conversation
            from .models import ConversationLog
            ConversationLog.objects.create(
                user=user, raw_text=raw_text, input_type="text",
                model="deepseek-chat", status="confirmed",
            )
            return {
                "actions": ai_result["actions"],
                "source": "ai",
                "confidence": "medium",
                "error": None,
            }
        else:
            return {
                "actions": [_draft_to_action(draft)],
                "source": "fallback",
                "confidence": "low",
                "error": f"AI validation failed: {'; '.join(errors)}",
            }
    except Exception as e:
        return {
            "actions": [_draft_to_action(draft)],
            "source": "fallback",
            "confidence": "low",
            "error": f"AI error: {str(e)[:200]}",
        }


def _draft_to_action(draft: dict) -> dict:
    """Convert a legacy parser draft to the new action format."""
    kind = draft.get("kind", "note")
    intent_map = {
        "expense": "create_expense",
        "income": "create_income",
        "task": "create_task",
        "note": "create_note",
    }
    action = {
        "intent": intent_map.get(kind, "create_note"),
        "action_id": "a1",
        "title": draft.get("title", ""),
        "category": draft.get("category", ""),
        "amount": draft.get("amount"),
        "occurred_at": draft.get("occurred_on"),
        "due_at": draft.get("due_at"),
    }
    # Clean None values
    return {k: v for k, v in action.items() if v is not None}
