"""AI Provider abstraction layer."""
import os
from abc import ABC, abstractmethod
from typing import Optional


class AIProvider(ABC):
    @abstractmethod
    def parse(self, text: str) -> dict:
        ...


class FakeProvider(AIProvider):
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
        idx = 0
        cats = {"吃饭": "餐饮", "买菜": "餐饮", "吃": "餐饮", "饭": "餐饮", "菜": "餐饮",
                "奶茶": "餐饮", "咖啡": "餐饮", "打车": "交通", "地铁": "交通",
                "公交": "交通", "加油": "交通", "充": "交通", "充电": "交通",
                "房租": "住房", "话费": "生活缴费", "电费": "生活缴费",
                "水费": "生活缴费", "买": "购物", "购物": "购物"}
        seen = set()

        # Match "keyword + number" patterns (with or without unit)
        for m in re.finditer(r"(吃饭|买菜|打车|交|付|充|充电|花了?|用了?)\s*(\d+(?:\.\d{1,2})?)\s*(?:元|块|块钱)?", text):
            amount = m.group(2)
            if amount in seen:
                continue
            seen.add(amount)
            idx += 1
            cat = "其他"
            for kw, c in cats.items():
                if kw in text:
                    cat = c
                    break
            actions.append({"intent": "create_expense", "action_id": f"a{idx}", "amount": amount, "category": cat, "occurred_at": "2026-08-10T12:00:00"})

        # Match "number + unit" not caught above
        for m in re.finditer(r"(\d+(?:\.\d{1,2})?)\s*(?:元|块|块钱)", text):
            amount = m.group(1)
            if amount in seen:
                continue
            seen.add(amount)
            idx += 1
            cat = "其他"
            for kw, c in cats.items():
                if kw in text:
                    cat = c
                    break
            actions.append({"intent": "create_expense", "action_id": f"a{idx}", "amount": amount, "category": cat, "occurred_at": "2026-08-10T12:00:00"})

        if any(w in text for w in ("收到", "工资", "退款", "报销", "收入")):
            for a in actions:
                a["intent"] = "create_income"

        if any(w in text for w in ("提醒", "要做", "待办", "记得")):
            idx += 1
            title = re.sub(r".*?(?:提醒我|提醒|要做|待办|记得)", "", text).strip("，。 ") or "任务"
            title = re.sub(r"\s*\d+(?:\.\d{1,2})?\s*(?:元|块|块钱)?", "", title).strip("，。 ")
            actions.append({"intent": "create_task", "action_id": f"a{idx}", "title": title[:200]})

        if not actions:
            idx += 1
            actions.append({"intent": "create_note", "action_id": f"a{idx}", "title": text[:200]})

        return {"actions": actions}


class DeepSeekProvider(AIProvider):
    def __init__(self, api_key=None, model=None, timeout=30, max_tokens=1024):
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
                raise RuntimeError("openai package required. pip install openai")
            self._client = OpenAI(api_key=self.api_key, base_url="https://api.deepseek.com", timeout=self.timeout)
        return self._client

    def parse(self, text: str) -> dict:
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY not set.")
        client = self._get_client()
        response = client.chat.completions.create(model=self.model, messages=[
            {"role": "system", "content": _DEEPSEEK_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ], max_tokens=self.max_tokens, temperature=0.1, response_format={"type": "json_object"})
        import json
        return json.loads(response.choices[0].message.content)


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

_provider: Optional[AIProvider] = None


def get_provider() -> AIProvider:
    global _provider
    if _provider is not None:
        return _provider
    _provider = DeepSeekProvider() if os.getenv("DEEPSEEK_API_KEY") else FakeProvider()
    return _provider


def set_provider(p: AIProvider):
    global _provider
    _provider = p
