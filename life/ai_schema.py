"""Unified JSON schema and validator for AI parse responses.

Schema rules per intent:
  create_expense  → amount (str), category (str), occurred_at (ISO 8601)
  create_income   → amount (str), occurred_at (ISO 8601)
  create_task     → title (str)
  create_reminder → title (str), event_at or remind_at (ISO 8601)
  create_note     → title (str)
  update_draft    → action_id (str) + fields to update
  unknown         → no requirements

All intents require:
  - intent (str, one of the 7 values)
  - action_id (str, unique within a conversation)
"""

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

VALID_INTENTS = frozenset({
    "create_expense",
    "create_income",
    "create_task",
    "create_reminder",
    "create_note",
    "create_recurring_expense",
    "create_daily_reminder",
    "update_draft",
    "unknown",
})

# ISO 8601-ish pattern: 2026-08-10 or 2026-08-10T14:30:00 or 2026-08-10T14:30:00+08:00
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:\d{2})?)?$")


def _validate_iso8601(val):
    """Check that val looks like an ISO 8601 string and is parseable."""
    if not isinstance(val, str) or not ISO_RE.match(val):
        return False
    try:
        # Try to parse with or without timezone
        datetime.fromisoformat(val.replace("Z", "+00:00"))
        return True
    except (ValueError, TypeError):
        return False


def _validate_decimal_str(val):
    """Must be a non-empty string that parses to a positive Decimal."""
    if not isinstance(val, str) or not val.strip():
        return False
    try:
        d = Decimal(val)
        return d > 0
    except InvalidOperation:
        return False


def validate_ai_response(response):
    """Validate an AI response dict. Returns (is_valid, list_of_errors).

    On success: (True, [])
    On failure: (False, ["error msg 1", "error msg 2", ...])
    """
    errors = []

    if not isinstance(response, dict):
        return False, ["Response must be a JSON object."]

    actions = response.get("actions")
    if not isinstance(actions, list) or len(actions) == 0:
        return False, ["Response must contain a non-empty 'actions' list."]

    seen_ids = set()
    for i, action in enumerate(actions):
        prefix = f"actions[{i}]"
        if not isinstance(action, dict):
            errors.append(f"{prefix}: must be an object.")
            continue

        intent = action.get("intent", "")
        action_id = action.get("action_id", "")

        if intent not in VALID_INTENTS:
            errors.append(f"{prefix}.intent: '{intent}' is not a valid intent.")
        if not action_id or not isinstance(action_id, str):
            errors.append(f"{prefix}.action_id: required, must be a non-empty string.")
        if action_id in seen_ids:
            errors.append(f"{prefix}.action_id: '{action_id}' is duplicated.")
        seen_ids.add(action_id)

        # Per-intent field validation
        if intent in ("create_expense", "create_income"):
            amount = action.get("amount", "")
            if not _validate_decimal_str(amount):
                errors.append(f"{prefix}.amount: required, must be a positive decimal string, got '{amount}'.")
            occurred = action.get("occurred_at", "")
            if not _validate_iso8601(occurred):
                errors.append(f"{prefix}.occurred_at: required, must be ISO 8601, got '{occurred}'.")
            if intent == "create_expense":
                cat = action.get("category", "")
                if not isinstance(cat, str) or not cat.strip():
                    errors.append(f"{prefix}.category: required for expense, must be a non-empty string.")

        if intent in ("create_task", "create_note"):
            title = action.get("title", "")
            if not isinstance(title, str) or not title.strip():
                errors.append(f"{prefix}.title: required, must be a non-empty string.")

        if intent == "create_recurring_expense":
            title = action.get("title", "")
            if not isinstance(title, str) or not title.strip():
                errors.append(f"{prefix}.title: required, must be a non-empty string.")
            amount = action.get("amount", "")
            if not _validate_decimal_str(amount):
                errors.append(f"{prefix}.amount: required, must be a positive decimal string, got '{amount}'.")

        if intent == "create_reminder":
            title = action.get("title", "")
            if not isinstance(title, str) or not title.strip():
                errors.append(f"{prefix}.title: required, must be a non-empty string.")
            has_event = _validate_iso8601(action.get("event_at", ""))
            has_remind = _validate_iso8601(action.get("remind_at", ""))
            if not has_event and not has_remind:
                errors.append(f"{prefix}: must have event_at or remind_at in ISO 8601.")

        if intent == "create_daily_reminder":
            title = action.get("title", "")
            if not isinstance(title, str) or not title.strip():
                errors.append(f"{prefix}.title: required, must be a non-empty string.")
            icon = action.get("icon", "")
            if icon and (not isinstance(icon, str) or len(icon) > 4):
                errors.append(f"{prefix}.icon: optional, must be a string ≤4 chars if provided.")

        if intent == "update_draft":
            aid = action.get("action_id", "")
            if not aid:
                errors.append(f"{prefix}.action_id: required for update_draft.")

    return len(errors) == 0, errors
