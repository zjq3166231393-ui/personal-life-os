"""AI Provider abstraction layer.

Business code calls `get_provider()` and depends only on the `AIProvider`
interface — never on DeepSeek/Fake directly.
"""
import os
from abc import ABC, abstractmethod
from typing import Optional


# ── Provider interface ───────────────────────────────────────────────


class AIProvider(ABC):
    """Unified interface for AI parsing. Swap implementations without touching business code."""

    @abstractmethod
    def parse(self, text: str) -> dict:
        """Parse natural language text into structured actions.

        Returns a dict conforming to the AI JSON schema (ai_schema.py).
        """
        ...


# ── Fake provider (for tests & local development) ────────────────────


class FakeProvider(AIProvider):
    """Returns canned responses. Used in tests and when no API key is configured."""

    def __init__(self):
        self.call_count = 0
        self.last_text = ""

    def parse(self, text: str) -> dict:
        self.call_count += 1
        self.last_text = text
        return self._build_response(text)

    def _build_response(self, text: str) -> dict:
        import re

        actions = []
        action_idx = 0

        # Detect expense patterns
        amount_match = re.search(r"(\d+(?:\.\d{1,2})?)\s*(?:元|块|块钱)", text)
        if amount_match:
            amount = amount_match.group(1)
            # Determine category
            cats = {"吃": "餐饮", "饭": "餐饮", "菜": "餐饮", "奶茶": "餐饮", "咖啡": "餐饮",
                    "打车": "交通", "地铁": "交通", "公交": "交通", "加油": "交通",
                    "房租": "住房", "话费": "生活缴费", "电费": "生活缴费", "水费": "生活缴费",
                    "买": "购物", "购物": "购物"}
            category = "其他"
            for kw, cat in cats.items():
                if kw in text:
                    category = cat
                    break
            action_idx += 1
            actions.append({
                "intent": "create_expense", "action_id": f"a{action_idx}",
                "amount": amount, "category": category,
                "occurred_at": "2026-08-10T12:00:00",
            })
            # Check for income keywords
            if any(w in text for w in ("收到", "工资", "退款", "报销", "收入")):
                actions[-1]["intent"] = "create_income"

        # Detect task patterns
        if any(w in text for w in ("提醒", "要做", "待办", "记得")):
            action_idx += 1
            title = re.sub(r".*?(提醒我|提醒|要做|待办|记得)", "", text).strip("，。 ") or "任务"
            actions.append({
                "intent": "create_task", "action_id": f"a{action_idx}",
                "title": title[:200],
            })

        # If nothing detected, return unknown
        if not actions:
            action_idx += 1
            actions.append({
                "intent": "create_note", "action_id": f"a{action_idx}",
                "title": text[:200],
            })

        return {"actions": actions}


# ── DeepSeek provider (real API) ─────────────────────────────────────


class DeepSeekProvider(AIProvider):
    """Calls DeepSeek API (OpenAI-compatible endpoint)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: int = 30,
        max_tokens: int = 1024,
    ):
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        self.timeout = timeout
        self.max_tokens = max_tokens
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise RuntimeError(
                    "openai package is required for DeepSeekProvider. "
                    "Install with: pip install openai"
                )
            self._client = OpenAI(
                api_key=self.api_key,
                base_url="https://api.deepseek.com",
                timeout=self.timeout,
            )
        return self._client

    def parse(self, text: str) -> dict:
        if not self.api_key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY is not set. Set it in .env or pass api_key=."
            )

        client = self._get_client()
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": _DEEPSEEK_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            max_tokens=self.max_tokens,
            temperature=0.1,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content
        import json
        return json.loads(raw)


_DEEPSEEK_SYSTEM_PROMPT = """You are a personal assistant that extracts structured actions from natural language Chinese text. Return ONLY a JSON object with an "actions" array. Each action must have:
- "intent": one of "create_expense", "create_income", "create_task", "create_reminder", "create_note", "unknown"
- "action_id": unique string within this response (e.g. "a1", "a2")

For create_expense: include "amount" (string, positive decimal), "category" (string), "occurred_at" (ISO 8601 string)
For create_income: include "amount" (string, positive decimal), "occurred_at" (ISO 8601 string)
For create_task: include "title" (string)
For create_reminder: include "title" (string), "event_at" (ISO 8601 string)
For create_note: include "title" (string)

Categories: 餐饮, 交通, 住房, 生活缴费, 购物, 其他

Example:
User: "午饭18元，提醒我明天9点交话费"
Response: {"actions": [{"intent": "create_expense", "action_id": "a1", "amount": "18", "category": "餐饮", "occurred_at": "2026-08-10T12:00:00"}, {"intent": "create_task", "action_id": "a2", "title": "交话费", "due_at": "2026-08-11T09:00:00"}]}
"""


# ── Service locator ──────────────────────────────────────────────────


_provider: Optional[AIProvider] = None


def get_provider() -> AIProvider:
    """Return the configured AI provider. Lazy-init on first call."""
    global _provider
    if _provider is not None:
        return _provider
    # Use FakeProvider as default when no API key is configured
    if os.getenv("DEEPSEEK_API_KEY"):
        _provider = DeepSeekProvider()
    else:
        _provider = FakeProvider()
    return _provider


def set_provider(p: AIProvider):
    """Override the global provider (for tests)."""
    global _provider
    _provider = p
