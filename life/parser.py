import re
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from django.utils import timezone

CATEGORY_KEYWORDS = {
    "餐饮": ("吃", "饭", "午餐", "晚餐", "早餐", "买菜", "奶茶", "咖啡", "外卖", "水果", "零食"),
    "交通": ("地铁", "公交", "打车", "加油", "充电", "电瓶车", "停车", "高速"),
    "住房": ("房租", "租金", "物业", "房贷"),
    "生活缴费": ("话费", "水费", "电费", "网费", "燃气", "宽带"),
    "购物": ("买", "购物", "淘宝", "衣服", "礼物", "超市"),
}

INCOME_KEYWORDS = ("收到", "工资", "收入", "入账", "领", "奖金", "退款", "报销", "转账", "转入")


def _category(text):
    for category, words in CATEGORY_KEYWORDS.items():
        if any(word in text for word in words):
            return category
    return "其他"


def _is_income(text):
    return any(word in text for word in INCOME_KEYWORDS)


def _date(text):
    today = timezone.localdate()
    if "前天" in text:
        return today - timedelta(days=2)
    if "昨天" in text:
        return today - timedelta(days=1)
    if "后天" in text:
        return today + timedelta(days=2)
    if "明天" in text:
        return today + timedelta(days=1)
    return today


def _extract_amount(text):
    """Extract amount from text. Returns (Decimal, cleaned_text) or (None, text)."""
    patterns = [
        r"(?:花了?|消费了?|支出|用了|付了?|支付)\s*(\d+(?:\.\d{1,2})?)\s*(?:元|块|块钱|rmb)?",
        r"(?:收到|工资|收入|入账|领了?|奖金|退款|报销)\s*(\d+(?:\.\d{1,2})?)\s*(?:元|块|块钱|rmb)?",
        r"(\d+(?:\.\d{1,2})?)\s*(?:元|块|块钱|rmb)",
    ]
    for pat in patterns:
        match = re.search(pat, text)
        if match:
            try:
                return Decimal(match.group(1)), text
            except InvalidOperation:
                return None, text
    return None, text


def _clean_title(text, amount):
    """Remove time markers and amount from text, keep business keywords."""
    t = re.sub(r"\s*(?:今天|昨天|明天|前天|后天|早上|中午|晚上|上午|下午)\s*", "", text)
    t = re.sub(r"\s*(?:花了?|消费了?|支出|用了|付了?|支付)\s*", "", t)
    t = re.sub(r"\s*(?:收到|入账)\s*", "", t)
    if amount is not None:
        t = re.sub(rf"{re.escape(str(amount))}\s*(?:元|块|块钱|rmb)?", "", str(t))
        t = re.sub(r"\s*\d+(?:\.\d{1,2})?\s*(?:元|块|块钱|rmb)", "", t)
    t = t.strip("，。, .-") or "日常消费"
    return t[:200]


def parse_text(raw_text):
    """Return an untrusted draft with type/amount/category/note/date."""
    text = raw_text.strip()
    date = _date(text)
    amount, _ = _extract_amount(text)
    is_income = _is_income(text)
    title = _clean_title(text, amount)

    draft = {
        "kind": "expense" if amount is not None and not is_income else
                "expense" if amount is not None else
                ("note" if not any(w in text for w in ("提醒", "要做", "待办", "完成", "记得", "安排")) else "task"),
        "title": title,
        "category": _category(text) if amount is not None else "",
        "amount": str(amount) if amount else None,
        "occurred_on": date.isoformat(),
        "type": "income" if is_income else "expense",
        "merchant": "",
    }

    # Override kind for income
    if is_income and amount is not None:
        draft["kind"] = "income"

    # Task detection
    if any(word in text for word in ("提醒", "要做", "待办", "完成", "记得", "安排")):
        draft["kind"] = "task"
        draft["title"] = title
        due_at = None
        match = re.search(r"(?:上午|下午|晚上|中午)?\s*(\d{1,2})[点时](?:([0-5]?\d)分?)?", text)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2) or 0)
            if any(m in text for m in ("下午", "晚上")) and hour < 12:
                hour += 12
            due_at = timezone.make_aware(datetime.combine(date, time(hour, minute))).isoformat()
        draft["due_at"] = due_at
        draft["priority"] = 1 if any(x in text for x in ("重要", "紧急", "尽快")) else 2

    # Default to note if no amount and no task keywords
    if amount is None and draft["kind"] == "expense":
        draft["kind"] = "note"
        draft["title"] = text[:200]

    draft.setdefault("due_at", None)
    draft.setdefault("priority", 2)

    return draft
