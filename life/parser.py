import re
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

CATEGORY_KEYWORDS = {
    "餐饮": ("吃", "饭", "午餐", "晚餐", "早餐", "买菜", "奶茶", "咖啡", "外卖"),
    "交通": ("地铁", "公交", "打车", "加油", "充电", "电瓶车"),
    "住房": ("房租", "租金", "物业"),
    "生活缴费": ("话费", "水费", "电费", "网费", "燃气"),
    "购物": ("买", "购物", "淘宝", "衣服", "礼物"),
}


def _category(text):
    for category, words in CATEGORY_KEYWORDS.items():
        if any(word in text for word in words):
            return category
    return "其他"


def _date_and_time(text):
    today = timezone.localdate()
    if "后天" in text:
        date = today + timedelta(days=2)
    elif "明天" in text:
        date = today + timedelta(days=1)
    else:
        date = today
    match = re.search(r"(?:上午|下午|晚上|中午)?\s*(\d{1,2})[点时](?:([0-5]?\d)分?)?", text)
    if not match:
        return date, None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if any(marker in text for marker in ("下午", "晚上")) and hour < 12:
        hour += 12
    return date, time(hour, minute)


def parse_text(raw_text):
    """Return an untrusted draft. The client must ask the user to confirm it."""
    text = raw_text.strip()
    date, parsed_time = _date_and_time(text)
    amount_match = re.search(r"(?:花了?|消费了?|支出)?\s*(\d+(?:\.\d{1,2})?)\s*(?:元|块|块钱|rmb)", text, re.I)
    amount = Decimal(amount_match.group(1)) if amount_match else None

    if amount is not None:
        title = re.sub(r"(?:今天|昨天|明天)?\s*(?:早上|中午|晚上)?", "", text)
        title = re.sub(r"(?:花了?|消费了?|支出)?\s*\d+(?:\.\d{1,2})?\s*(?:元|块|块钱|rmb)", "", title, flags=re.I).strip("，。 ") or "日常消费"
        return {"kind": "expense", "title": title, "category": _category(text), "amount": str(amount), "occurred_on": date.isoformat(), "due_at": None, "priority": 2}

    if any(word in text for word in ("提醒", "要做", "待办", "完成", "记得", "安排")):
        title = re.sub(r".*?(?:提醒我|提醒|要做|待办|记得|安排)", "", text).strip("，。 ") or text
        due_at = None
        if parsed_time:
            due_at = timezone.make_aware(datetime.combine(date, parsed_time)).isoformat()
        return {"kind": "task", "title": title, "category": "", "amount": None, "occurred_on": date.isoformat(), "due_at": due_at, "priority": 1 if any(x in text for x in ("重要", "紧急", "尽快")) else 2}

    return {"kind": "note", "title": text[:200], "category": "", "amount": None, "occurred_on": date.isoformat(), "due_at": None, "priority": 2}

