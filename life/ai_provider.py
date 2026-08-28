"""AI Provider abstraction layer."""
import os
from abc import ABC, abstractmethod
from typing import Optional

from .constants import AI_API_BASE, AI_DEFAULT_MODEL, AI_MAX_TOKENS, AI_REQUEST_TIMEOUT
from .prompts import build_system_prompt


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
        from datetime import date, timedelta

        from .parser import _category, _chinese_number_to_arabic, _date
        actions = []
        idx = 0
        def to_amount(raw):
            """中文/阿拉伯数字 token → 字符串金额。委托 parser 的中文数字解析，避免重复实现与行为漂移。"""
            raw = raw.strip()
            if raw.isdigit():
                return raw
            val = _chinese_number_to_arabic(raw)
            return str(val) if val is not None else None

        seen = set()
        # 日期推导全部交给 parser._date（已含前/昨/明/后/大前/大后 天与绝对日期），
        # 不在此处复制第二套逻辑，避免漂移。
        today = date.today()
        day = _date(text)
        occurred_default = day.isoformat() + "T12:00:00"

        # Match expense verbs + number (with or without unit, supports 中文 numerals)
        pattern = r"(吃饭|买菜|打车|交|付|充|充电|花了?|用了?|买了?|付了?)\s*(\d+(?:\.\d{1,2})?|二十|三十|四十|五十|六十|七十|八十|九十|二|三|四|五|六|七|八|九|十|两)\s*(?:元|块|块钱)?"
        for m in re.finditer(pattern, text):
            amt_raw = m.group(2)
            amount = to_amount(amt_raw)
            if not amount or amount in seen:
                continue
            seen.add(amount)
            idx += 1
            cat = _category(text)
            actions.append({"intent": "create_expense", "action_id": f"a{idx}", "amount": amount, "category": cat, "title": cat, "occurred_at": occurred_default})

        # Match "number + unit" not caught above (digits only)
        for m in re.finditer(r"(\d+(?:\.\d{1,2})?)\s*(?:元|块|块钱)", text):
            amount = m.group(1)
            if amount in seen:
                continue
            seen.add(amount)
            idx += 1
            cat = _category(text)
            actions.append({"intent": "create_expense", "action_id": f"a{idx}", "amount": amount, "category": cat, "title": cat, "occurred_at": occurred_default})

        if any(w in text for w in ("收到", "工资", "退款", "报销", "收入")):
            for a in actions:
                if a["intent"] == "create_expense":
                    a["intent"] = "create_income"

        # 固定账单检测（必须在任务识别之前；只要带金额 + 周期词就算固定账单）
        recurring_kws = (
            "固定账单", "固定开支", "每个月", "每月", "每月都要", "每个星期", "每周",
            "每周都要", "每个礼拜", "每周固定", "每季度", "每季", "每年", "每年都",
            "订阅", "自动扣款", "自动续费", "周期", "定期", "固定",
        )
        is_recurring = any(w in text for w in recurring_kws)
        if is_recurring and actions and actions[0]["intent"] in ("create_expense", "create_income"):
            # 找到第一个 expense action 转成 create_recurring_expense
            for a in actions:
                if a["intent"] == "create_expense":
                    a["intent"] = "create_recurring_expense"
                    freq = "monthly"
                    if any(w in text for w in ("每周", "每个星期", "每个礼拜")):
                        freq = "weekly"
                    elif any(w in text for w in ("每季度", "每季")):
                        freq = "quarterly"
                    elif any(w in text for w in ("每年", "每年都")):
                        freq = "yearly"
                    a["frequency"] = freq
                    # 派生干净标题（避免校验失败回退）
                    try:
                        from decimal import Decimal

                        from .parser import _clean_title
                        a["title"] = _clean_title(text, Decimal(str(a["amount"])))
                    except Exception:
                        a["title"] = text[:20]
                    break
        elif is_recurring and not actions:
            # 单独识别固定账单但还没 amount 抓到（边界情况）
            m = re.search(r"(\d+(?:\.\d{1,2})?)", text)
            if m:
                idx += 1
                cat = _category(text)
                try:
                    from decimal import Decimal

                    from .parser import _clean_title
                    _title = _clean_title(text, Decimal(m.group(1)))
                except Exception:
                    _title = text[:20]
                actions.append({"intent": "create_recurring_expense", "action_id": f"a{idx}", "amount": m.group(1), "category": cat, "occurred_at": occurred_default, "frequency": "monthly", "title": _title})

        # ── 每日打卡 / 习惯提醒（FakeProvider兜底） ──
        if not actions:
            from .parser import _clean_daily_title, _daily_icon, _is_daily_reminder
            if _is_daily_reminder(text):
                clean = _clean_daily_title(text)
                if clean:
                    idx += 1
                    actions.append({
                        "intent": "create_daily_reminder",
                        "action_id": f"a{idx}",
                        "title": clean,
                        "icon": _daily_icon(text),
                        "occurred_at": occurred_default,
                    })

        # 任务/提醒触发词（含"提醒/要做/待办/记得"；含动作动词如"完善/联系/打电话/提交"等；
        # 未来时间词 + 动作者也算任务；纯动作短语也视为任务）
        action_verbs = (
            "完善", "联系", "发邮件", "打电话", "填表", "提交", "整理", "准备", "跑",
            "运动", "健身", "看书", "学习", "练习", "买东西", "出差", "拜访", "加班",
            "复习", "翻译", "完成", "写", "找", "约", "问", "处理", "确认", "开会",
            "还", "取", "寄", "交", "借", "查", "读", "改", "送", "接", "拿", "买",
            "做", "下班", "面试",
        )
        future_words = ("明天", "后天", "下周", "下个月", "今天下午", "明天下午", "明天上午")
        has_action = any(w in text for w in action_verbs)
        has_future = any(w in text for w in future_words)
        is_task_input = any(w in text for w in ("提醒", "要做", "待办", "记得", "帮我安排", "别忘了")) or has_action or has_future

        if is_task_input:
            idx += 1
            # 1. 取「第一个任务触发词」之后的片段作为任务内容，避免把前面的
            #    消费信息（如「午饭18元」）混进任务标题； cue 在句首时也等价。
            _cue_order = ("提醒我", "提醒", "记得", "要做", "待办", "帮我安排", "别忘了", "帮我")
            _seg = text
            for _cue in _cue_order:
                _pos = _seg.find(_cue)
                if _pos != -1:
                    _seg = _seg[_pos + len(_cue):]
                    break
            full = re.sub(r"^[，。,;；.::\s]+", "", _seg).strip()
            # 2. 按句末标点切分，取最后一个非空段作为标题主体
            #    修复 bug：之前用 split(sep)[-1] 当句号在末尾时返回 ''，导致所有任务都 fallback 成"任务"
            for sep in ("。", "，", ",", ";", "；"):
                if sep in full:
                    parts = [p.strip() for p in full.split(sep) if p.strip()]
                    if parts:
                        full = parts[-1]
                    else:
                        full = ""
            # 3. 剥时间词（含：X点 / X时 / 上下午 / 凌晨 / 周X / X月X日 / 明今后 等）
            title = re.sub(
                r"(?:明[天日]|今[天日]|后[天日]|昨[天日]|前[天日]|大后天|大前天|本周[一二三四五六日天]|周[一二三四五六日天]|[上下]?午[下上]?的?|[上中下]午|晚上|凌晨|早上?|夜里|中午|[0-9一-九十百千万半]+个?(?:小时|分钟|秒钟|刻钟|点|分|时)的?)",
                "", full,
            )
            title = re.sub(r"\d+\s*月\s*\d+\s*[日号]?", "", title)
            title = re.sub(r"\d+\s*(?:元|块|块钱)?", "", title)
            title = re.sub(r"[，。,;；、\s]+", "", title).strip()
            # 清理时间词与名词之间的结构性"的/是"（如"4点的面试"→"面试"、"是姐姐的生日"→"姐姐的生日"）
            title = re.sub(r"^[的是]+", "", title)
            # 4. 兜底：截断到 12 字；如果净化后为空，用 full 原值兜底；再空才回 "任务"（后续后端会拒）
            if not title:
                title = re.sub(r"[，。,;；、\s]+", "", full).strip()[:12] or "任务"
            # 剥掉「再记一笔 / 记一笔 / 添加 / 加个」等记账引导词，避免任务标题残留（"再记一笔打车"→"打车"）
            title = re.sub(r"^(?:再记一笔|记一笔|添加一笔|加一笔|再记|添加|加个|添加个|又记一笔)", "", title)
            if len(title) > 12:
                title = title[:12]
            actions.append({"intent": "create_task", "action_id": f"a{idx}", "title": title})

            # 计算 due_at（如果用户没指定时间，默认推到「明天 9:00」，避免立即过期）
            m_t = re.search(r"(\d{1,2})\s*(?:点|:|：)\s*(\d{0,2})", text)
            if m_t:
                hour = int(m_t.group(1))
                minute = int(m_t.group(2) or 0)
            else:
                # 用户未指定时间：默认明天 9 点（不再用今天，避免下午创建就过期）
                if day == today:
                    day = today + timedelta(days=1)
                hour, minute = 9, 0
            if "下午" in text and hour < 12:
                hour += 12
            if "晚上" in text and hour < 12:
                hour += 12
            actions[-1]["due_at"] = day.isoformat() + f"T{hour:02d}:{minute:02d}:00"

        if not actions:
            idx += 1
            actions.append({"intent": "create_note", "action_id": f"a{idx}", "title": text[:200]})

        return {"actions": actions}


class DeepSeekProvider(AIProvider):
    def __init__(self, api_key=None, model=None, timeout=AI_REQUEST_TIMEOUT, max_tokens=AI_MAX_TOKENS):
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        self.model = model or os.getenv("DEEPSEEK_MODEL", AI_DEFAULT_MODEL)
        self.timeout = timeout
        self.max_tokens = max_tokens
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise RuntimeError("openai package required. pip install openai")
            self._client = OpenAI(api_key=self.api_key, base_url=AI_API_BASE, timeout=self.timeout)
        return self._client

    def parse(self, text: str) -> dict:
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY not set.")
        from django.utils import timezone
        today_iso = timezone.localdate().isoformat()
        client = self._get_client()
        response = client.chat.completions.create(model=self.model, messages=[
            {"role": "system", "content": build_system_prompt(today_iso)},
            {"role": "user", "content": text},
        ], max_tokens=self.max_tokens, temperature=0.1, response_format={"type": "json_object"})
        import json
        return json.loads(response.choices[0].message.content)


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
