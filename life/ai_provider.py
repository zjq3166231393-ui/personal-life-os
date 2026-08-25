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
        from datetime import date, timedelta
        actions = []
        idx = 0
        # 14-category keyword map (同步 CATEGORIES_STR，按匹配优先级排序：具体词优先)
        cats = {
            "包子": "餐饮", "水果": "餐饮", "奶茶": "餐饮", "咖啡": "餐饮", "外卖": "餐饮",
            "吃饭": "餐饮", "买菜": "餐饮", "吃": "餐饮", "饭": "餐饮", "菜": "餐饮",
            "打车": "交通", "地铁": "交通", "公交": "交通", "加油": "交通", "充电": "交通",
            "数据线": "电子产品", "充电器": "电子产品", "手机": "电子产品", "耳机": "电子产品",
            "房租": "住房", "话费": "生活缴费", "电费": "生活缴费", "水费": "生活缴费",
            "衣服": "服饰", "鞋": "服饰", "包": "服饰",
            "药": "医疗", "医院": "医疗",
            "书": "教育", "课": "教育",
            "电影": "娱乐", "游戏": "娱乐",
            "猫粮": "宠物", "狗粮": "宠物",
            "红包": "社交人情", "礼金": "社交人情",
            "买": "购物", "购物": "购物", "淘宝": "购物", "京东": "购物",
        }
        cn_amount = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
                     "七": 7, "八": 8, "九": 9, "十": 10, "二十": 20, "三十": 30,
                     "四十": 40, "五十": 50, "六十": 60, "七十": 70, "八十": 80, "九十": 90}
        def to_amount(raw):
            raw = raw.strip()
            if raw.isdigit():
                return raw
            if raw in cn_amount:
                return str(cn_amount[raw])
            # compound like 二十五, 五十
            for unit in (90, 80, 70, 60, 50, 40, 30, 20, 10):
                su = str(unit)
                if raw.startswith(su) and len(raw) > len(su):
                    tail = raw[len(su):]
                    if tail in cn_amount and cn_amount[tail] < 10:
                        return str(unit + cn_amount[tail])
            return None

        seen = set()
        today = date.today()
        if "前天" in text:
            day = today - timedelta(days=2)
        elif "昨天" in text:
            day = today - timedelta(days=1)
        elif "大后天" in text:
            day = today + timedelta(days=3)
        elif "后天" in text:
            day = today + timedelta(days=2)
        elif "明天" in text:
            day = today + timedelta(days=1)
        else:
            day = today
        # 绝对日期（X月X号 / X月X日 / X/X）：文本含明确日期时，覆盖上面的相对/默认 day，
        # 否则「提醒我8月30号过生日」会被错算成「明天 9:00」（8/26）。非法日期（如 2/30）保留默认。
        m_abs = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?", text)
        if not m_abs:
            m_abs = re.search(r"(?<!\d)(\d{1,2})\s*[/\-]\s*(\d{1,2})(?!\d|\.)", text)
        if m_abs:
            try:
                am, ad = int(m_abs.group(1)), int(m_abs.group(2))
                if 1 <= am <= 12 and 1 <= ad <= 31:
                    abs_d = today.replace(month=am, day=ad)
                    if abs_d < today:
                        abs_d = abs_d.replace(year=abs_d.year + 1)
                    day = abs_d
            except ValueError:
                pass
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
            cat = "其他"
            for kw, c in cats.items():
                if kw in text:
                    cat = c
                    break
            actions.append({"intent": "create_expense", "action_id": f"a{idx}", "amount": amount, "category": cat, "title": cat, "occurred_at": occurred_default})

        # Match "number + unit" not caught above (digits only)
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
                        from .parser import _clean_title
                        from decimal import Decimal
                        a["title"] = _clean_title(text, Decimal(str(a["amount"])))
                    except Exception:
                        a["title"] = text[:20]
                    break
        elif is_recurring and not actions:
            # 单独识别固定账单但还没 amount 抓到（边界情况）
            m = re.search(r"(\d+(?:\.\d{1,2})?)", text)
            if m:
                idx += 1
                cat = "其他"
                for kw, c in cats.items():
                    if kw in text:
                        cat = c
                        break
                try:
                    from .parser import _clean_title
                    from decimal import Decimal
                    _title = _clean_title(text, Decimal(m.group(1)))
                except Exception:
                    _title = text[:20]
                actions.append({"intent": "create_recurring_expense", "action_id": f"a{idx}", "amount": m.group(1), "category": cat, "occurred_at": occurred_default, "frequency": "monthly", "title": _title})

        # ── 每日打卡 / 习惯提醒（FakeProvider兜底） ──
        if not actions:
            from .parser import _is_daily_reminder, _clean_daily_title, _daily_icon
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
        from django.utils import timezone
        today_iso = timezone.localdate().isoformat()
        client = self._get_client()
        response = client.chat.completions.create(model=self.model, messages=[
            {"role": "system", "content": _build_system_prompt(today_iso)},
            {"role": "user", "content": text},
        ], max_tokens=self.max_tokens, temperature=0.1, response_format={"type": "json_object"})
        import json
        return json.loads(response.choices[0].message.content)


CATEGORIES_STR = "餐饮, 交通, 生活缴费, 住房, 服饰, 电子产品, 医疗, 教育, 运动健身, 娱乐, 宠物, 社交人情, 购物, 其他"


def _build_system_prompt(today_iso: str) -> str:
    """Build the DeepSeek system prompt with the current date injected so the
    model converts relative dates (今天/明天/后天/昨天/前天) to correct absolute dates."""
    return f"""You are a personal assistant that extracts structured actions from natural language Chinese text. Today's date is {today_iso} (format YYYY-MM-DD). Convert all relative dates (今天/明天/后天/昨天/前天/下周一 etc.) to the correct absolute date based on today.

## Intent priority (decide in this order)
1. **Recurring bill takes priority** when the text mentions a periodic/cadence word (固定账单/固定开支/每个月/每月/每周/每季度/每年/订阅/自动扣款/周期) AND has a positive amount → intent MUST be create_recurring_expense (NOT create_expense). Set frequency based on the cue word: 每周→weekly, 每季度→quarterly, 每年→yearly, otherwise→monthly.
2. **Daily check-in / habit** when the text reads like a recurring daily habit with no specific date ("每天背单词", "我每天都要练习口语", "每日打卡 快手签到", "我每天读书") and there is NO future date mentioned → intent is create_daily_reminder with title stripped to the bare activity (e.g. "背单词") and an emoji icon ("📚").
3. If the text contains an expense verb (花/买了/付了/订了/花费/付/给/交了) AND a positive amount (number + 元/块/块钱 OR Chinese numerals 五十/二十 etc.), the dominant action is create_expense.
4. Otherwise if the text contains a task/reminder cue (提醒我/记得/要做/待办/帮我安排/别忘了/记得), the dominant action is create_task (with due_at when time/date is mentioned) or create_reminder.
5. Otherwise if the text contains a future-time word (明天/后天/下周/下个月) AND an action verb (买/做/完成/写/发/联系/约/打电话/开会/复习/翻译/带/取/交/查/读/写/送/接/拿/找/问/处理/确认), the action is create_task.
6. Otherwise if the text is a bare action verb or verb+object (完善/联系/发邮件/打电话/填表/提交/整理/准备/跑/运动/健身/看书/学习/练习/买东西/出差/拜访/加班/复习), the action is create_task.
7. Otherwise if there is a positive amount and no expense verb, prefer create_expense over create_note.
8. ONLY fall back to create_note when nothing above applies. create_note is for observations/ideas/scribbles with NO action orientation (e.g. "今天的天气真好", "突然想到一个点子", "我感冒好了之后想去体验一下生活的新鲜感").

## Title field rules (create_task / create_reminder)
- Title contains ONLY the event name (e.g. "面试"), NEVER repeat dates or times in the title.
- Strip ALL of: 提醒我/提醒/记得/要做/帮我/帮我安排/别忘了, 时间词 "明天/今天/后天/几点/上午/下午/X点", 日期"X月X日".
- Put the date/time info into due_at (task) or event_at (reminder) instead.
- Keep it short (ideally <= 12 characters). If a short fallback is needed use 任务.
- IMPORTANT: never return an empty string for title. If extracted content is empty, use the action object (e.g. "完善项目") or fallback to the verb phrase; DO NOT return "" or null.

## Multi-intent splitting
- A single user input MAY contain multiple actions (e.g. "午饭18元，提醒我明天交话费" -> 2 actions: expense + task).
- Return each action as a separate item in the array, with distinct action_id (a1, a2, ...).

## Output
Return ONLY a JSON object with an "actions" array. Each action must have:
- "intent": one of "create_expense", "create_income", "create_task", "create_reminder", "create_note", "create_recurring_expense", "create_daily_reminder"
- "action_id": unique string within this response (e.g. "a1", "a2")
For create_expense: include "amount" (string, positive decimal), "category" (string), "occurred_at" (ISO 8601 string, e.g. {today_iso}T12:00:00)
For create_income: include "amount" (string, positive decimal), "occurred_at" (ISO 8601 string)
For create_task: include "title" (string, event name only), "due_at" (ISO 8601 string) when a time/date is mentioned
For create_reminder: include "title" (string, event name only), "event_at" (ISO 8601 string)
For create_note: include "title" (string)
For create_recurring_expense: include "amount" (string), "category" (string), "occurred_at" (ISO 8601 string), "frequency" ("monthly" / "weekly" / "quarterly" / "yearly")
Allowed expense categories: {CATEGORIES_STR}

Examples:
User: "查，昨天买菜花了五十元"
Response: {{"actions": [{{"intent": "create_expense", "action_id": "a1", "amount": "50", "category": "餐饮", "occurred_at": "2026-08-22T12:00:00", "note": "买菜"}}]}}

User: "提醒我明天下午4点的面试"
Response: {{"actions": [{{"intent": "create_task", "action_id": "a1", "title": "面试", "due_at": "2026-08-24T16:00:00"}}]}}

User: "新增一笔固定账单，每个月宽带费80元"
Response: {{"actions": [{{"intent": "create_recurring_expense", "action_id": "a1", "amount": "80", "category": "生活缴费", "occurred_at": "{today_iso}T12:00:00", "frequency": "monthly", "title": "宽带费"}}]}}

User: "我想每天背单词"
Response: {{"actions": [{{"intent": "create_daily_reminder", "action_id": "a1", "title": "背单词", "icon": "📚", "occurred_at": "{today_iso}T12:00:00"}}]}}

User: "快手签到，每天都要"
Response: {{"actions": [{{"intent": "create_daily_reminder", "action_id": "a1", "title": "快手签到", "icon": "🎬", "occurred_at": "{today_iso}T12:00:00"}}]}}

User: "午饭18元，提醒我明天9点交话费"
Response: {{"actions": [{{"intent": "create_expense", "action_id": "a1", "amount": "18", "category": "餐饮", "occurred_at": "{today_iso}T12:00:00"}}, {{"intent": "create_task", "action_id": "a2", "title": "交话费", "due_at": "2026-08-24T09:00:00"}}]}}
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
