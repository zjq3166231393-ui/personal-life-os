import re
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from django.utils import timezone


# ── 中文数字解析 ───────────────────────────────────────────────
# 用户输入里"三十" / "二十块五" 之类的中文数字必须先转成阿拉伯数字，
# 否则下游 `_extract_amount` / 分类 / 金额判断都会失败。
_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000, "万": 10000}


def _chinese_number_to_arabic(s: str):
    """Convert a Chinese number string to Decimal, or None if not a number.

    Supports: 零一二三四五六七八九两 十百千万；basic composition
    e.g. '三十' -> 30, '二十五' -> 25, '一百零五' -> 105
    """
    if not s or not all(c in "零一二三四五六七八九十百千万两" for c in s):
        return None
    if not s:
        return None
    total = 0
    section = 0
    last_digit = 0
    for ch in s:
        if ch in _CN_DIGIT:
            last_digit = _CN_DIGIT[ch]
        elif ch in _CN_UNIT:
            unit = _CN_UNIT[ch]
            if last_digit == 0 and unit >= 10:
                # 「十」「百」开头省略「一」：十=10, 百=100
                last_digit = 1
            section += last_digit * unit
            last_digit = 0
        elif ch == "零":
            last_digit = 0
    total = section + last_digit
    return Decimal(total) if total > 0 else None


def _normalize_chinese_numbers(text: str) -> str:
    """Replace Chinese numbers (0-9999) with Arabic equivalents in text.

    Conservative: only converts 1-6 char Chinese numbers that look like standalone
    amounts (followed by currency marker or end-of-string), so we don't mangle
    arbitrary Chinese phrases that happen to contain '三十' as part of a name.
    """
    # Capture: (1) Chinese digits, (2) optional end-token (元/块/块钱/etc) or end-of-string.
    # The end-token is *captured* (not a lookahead) so the substitution can preserve it.
    pat = re.compile(r"([零一二三四五六七八九十百千万两]{1,6})(元|块|块钱|rmb|RMB|\b|$|[，。, .])")
    out = pat.sub(lambda m: (
        (str(_chinese_number_to_arabic(m.group(1))) + m.group(2))
        if _chinese_number_to_arabic(m.group(1)) is not None and _chinese_number_to_arabic(m.group(1)) != 0
        else m.group(0)
    ), text)
    return out


# 分类关键词，顺序敏感：越靠前越优先匹配（具体品类词放前面，泛义词兜底）。
# 覆盖日常「衣食住行」及电子产品、服饰、医疗、教育、运动、宠物、社交人情等场景。
# 注意：单字/过短词（如「包」「充电」）易误命中，已尽量用具体词；顺序上把易冲突的
# 子类（电子产品、社交人情）排在更泛的父类（交通、服饰、购物）之前。
CATEGORY_KEYWORDS = {
    "餐饮": (
        "早饭", "早餐", "午饭", "午餐", "晚饭", "晚餐", "夜宵", "宵夜", "吃饭", "聚餐", "请客",
        "下馆子", "食堂", "餐厅", "饭店", "馆子", "外卖", "美团", "饿了么", "点外卖",
        "包子", "馒头", "花卷", "饺子", "馄饨", "面条", "米线", "米粉", "麻辣烫", "火锅",
        "串串", "烧烤", "烤串", "炸鸡", "汉堡", "肯德基", "麦当劳", "必胜客", "德克士",
        "星巴克", "瑞幸", "奶茶", "咖啡", "饮料", "果汁", "汽水", "酒", "啤酒", "白酒", "红酒",
        "水果", "西瓜", "零食", "薯片", "饼干", "蛋糕", "甜品", "冰淇淋", "雪糕", "煎饼",
        "肉夹馍", "螺蛳粉", "肠粉", "粥", "寿司", "披萨", "小吃", "买菜", "菜", "生鲜",
        "卤味", "鸭脖", "瓜子", "坚果", "糖", "饭",
    ),
    # 电子产品排在交通之前：避免「充电器/充电宝」被交通的「充电」误判
    "电子产品": (
        "数据线", "充电线", "充电器", "充电宝", "电源", "适配器", "转接头", "手机",
        "电脑", "笔记本", "平板", "耳机", "蓝牙耳机", "键盘", "鼠标", "显示器", "相机",
        "硬盘", "移动硬盘", "U盘", "内存卡", "内存", "显卡", "路由器", "智能手表",
        "手环", "游戏机", "手柄", "音箱", "音响", "贴膜", "手机壳", "电子", "数码", "配件", "电池",
    ),
    "交通": (
        "地铁", "公交", "公交车", "打车", "滴滴", "出租", "出租车", "网约车", "顺风车",
        "加油", "油费", "充电桩", "电瓶车", "电动车", "停车", "停车费", "高速",
        "过路费", "高铁", "火车", "动车", "火车票", "动车票", "飞机", "机票", "航班",
        "单车", "自行车", "骑行", "地铁卡", "公交卡", "ETC", "打车费", "租车", "洗车",
        "汽车", "摩托", "轮渡", "船票", "充电",
    ),
    "生活缴费": (
        "话费", "宽带", "网费", "水费", "电费", "燃气", "燃气费", "暖气费", "宽带费",
        "物业费", "供暖费", "有线电视",
    ),
    "住房": (
        "房租", "租金", "租", "房贷", "水电", "押金", "装修", "家具", "家电",
        "搬家", "保洁", "中介费", "取暖费", "维修", "门锁",
    ),
    # 社交人情排在其他消费类之前：避免「红包」被服饰的「包」误判
    "社交人情": (
        "红包", "份子钱", "随礼", "送礼", "人情", "礼金", "随份子",
    ),
    "服饰": (
        "衣服", "上衣", "裤子", "裙子", "连衣裙", "牛仔裤", "鞋", "运动鞋", "靴子",
        "背包", "双肩包", "手提包", "皮包", "包包", "买包", "帽子", "袜子", "内衣",
        "外套", "羽绒服", "T恤", "卫衣", "毛衣", "衬衫", "围巾", "手套", "皮带",
        "领带", "服装", "买衣服", "换季",
    ),
    "医疗": (
        "药", "药品", "买药", "抓药", "医院", "看病", "诊所", "体检", "挂号", "牙科",
        "牙医", "眼镜", "验光", "疫苗", "感冒", "发烧", "咳嗽", "药店", "医保", "护理",
        "康复", "中医",
    ),
    "教育": (
        "书", "图书", "买书", "课本", "教材", "课程", "网课", "培训", "报名", "学费",
        "考试", "考证", "考研", "英语", "辅导", "文具", "书包", "早教",
    ),
    "运动健身": (
        "健身", "健身房", "游泳", "瑜伽", "篮球", "羽毛球", "乒乓球", "跑步", "马拉松",
        "私教", "体育", "球类", "滑雪", "潜水",
    ),
    "娱乐": (
        "电影", "电影票", "游戏", "游戏充值", "点券", "钻石", "KTV", "按摩", "旅游",
        "门票", "演唱会", "剧本杀", "密室", "桌游", "酒吧", "展览", "演出", "上网",
        "视频会员", "音乐会员", "订阅", "网吧", "游乐场", "游乐园",
    ),
    "宠物": (
        "猫粮", "狗粮", "宠物", "猫", "狗", "兽医", "宠物医院", "猫砂", "宠物用品", "遛狗",
    ),
    "购物": (
        "淘宝", "京东", "拼多多", "抖音", "快手", "超市", "便利店", "日用品", "百货",
        "化妆品", "护肤品", "洗护", "洗发水", "牙膏", "家居", "杂物", "网购", "代购",
        "礼物", "礼品", "玩具", "鲜花", "买",
    ),
}

INCOME_KEYWORDS = ("收到", "工资", "收入", "入账", "领", "奖金", "退款", "报销", "转账", "转入")

# 任务触发词（与 ai_provider 同步）
TASK_REMINDER_KEYWORDS = ("提醒", "要做", "待办", "记得", "帮我安排", "别忘了")
# 固定账单触发词：用户输入里出现这些就归类为「周期性账单」，进入 RecurringExpense 而非一次性 Expense
RECURRING_KEYWORDS = (
    "固定账单", "固定开支", "每个月", "每月", "每个月都要", "每月都要",
    "每个星期", "每周", "每周都要", "每个礼拜", "每周固定",
    "每个季度", "每季度", "每季", "每年", "每年都",
    "订阅", "自动扣款", "自动续费", "周期", "定期", "固定",
)
RECURRING_MONTHLY_KEYWORDS = ("每个月", "每月", "每月都要", "月供", "月租", "月付")
RECURRING_WEEKLY_KEYWORDS = ("每个星期", "每周", "每周都要", "周付")
# 动作动词：纯动作短语也算任务（兜底识别，避免被归为 note）
TASK_ACTION_VERBS = (
    "完善", "联系", "发邮件", "打电话", "填表", "提交", "整理", "准备", "跑",
    "运动", "健身", "看书", "学习", "练习", "出差", "拜访", "加班", "复习",
    "翻译", "完成", "写", "找", "约", "问", "处理", "确认", "开会", "还",
    "取", "寄", "交", "借", "查", "读", "改", "送", "接", "拿", "做", "面试",
    "下班",
)
# 未来时间词
TASK_FUTURE_KEYWORDS = ("明天", "后天", "下周", "下个月")

# 固定账单标题清洗：剥掉"新增一笔固定账单/每个月/订阅/自动扣款"等引导词与周期词，
# 避免固定账单标题残留"新增一笔固定账单，每个月宽带费"这种整句。
_RECURRING_TITLE_CLEAN = re.compile(
    r"新增一笔固定账单|新增固定账单|新增一笔账单|加一笔固定账单|加个固定账单|"
    r"添加固定账单|每个月都要|每月都要|每周都要|每个月|每个星期|每个礼拜|"
    r"每周固定|每个季度|每季度|每季|每年都|每月|每周|每年|"
    r"新增一笔|加一笔|加个|添加一笔|添加|"
    r"固定账单|固定开支|自动扣款|自动续费|周期|定期|固定|订阅"
)


# 每日打卡 / 习惯提醒的关键词（用于 parser 直接识别）
DAILY_KEYWORDS = (
    "每日打卡", "每天提醒", "每日提醒", "每天都要", "每天打卡", "我每天", "每日",
    "习惯", "打卡", "签到", "背单词", "练口语", "复盘", "日记", "阅读", "运动", "跑步",
    "百词斩", "快手", "背书", "记单词",
)
DAILY_TITLE_CLEAN = re.compile(
    r"帮我(记录|加|添加|新建|建一个|加一个|创建|搞一个|做一个|做一个新的)|"
    r"我想(每天|每日)(要|要做的|做的)?|每天[要要]?提醒我|每日[要要]?提醒我|"
    r"每天[要要]?都要|每日[要要]?都要|"
    r"每天记得|每日记得|要记得|"
    r"每天|每日|我要|我想|帮我|麻烦|请|我要每天|我想每天|"
    r"^[\s,。.,;:;、了着过是的]?"
)
# Strip platform / context wrappers but KEEP the platform name:
#   在百词斩上背单词 → 百词斩背单词
#   在公园跑步       → 公园跑步
#   用快手签到       → 快手签到
DAILY_WRAP_CLEAN = re.compile(r"^在(.+?)上")
DAILY_PREP_CLEAN = re.compile(r"^(?:用|通过|去)([^\s，。、]{1,10})")


def _is_daily_reminder(text):
    """Returns True when the text reads like a habit/daily check-in.

    Examples:
      "我要每天背单词" → True
      "每日练口语" → True
      "快手签到" → True
      "明天提醒我背单词" → False (that's a task with a specific date)
    """
    t = text.strip()
    if any(k in t for k in ("明天", "后天", "下周", "下个月", "晚上", "明天")):
        # Has a future-pointing date — that's a task or reminder, not a habit.
        return False
    if any(k in t for k in DAILY_KEYWORDS):
        return True
    return False


def _daily_icon(text):
    """Pick a sensible emoji from keywords."""
    if "单词" in text or "百词斩" in text or "背" in text or "记单词" in text:
        return "📚"
    if "口语" in text or "英语" in text or "英语" in text:
        return "🎤"
    if "签到" in text or "快手" in text:
        return "🎬"
    if "跑步" in text or "运动" in text or "健身" in text or "锻炼" in text:
        return "🏃"
    if "阅读" in text or "看书" in text or "读书" in text:
        return "📖"
    if "日记" in text or "复盘" in text or "总结" in text:
        return "📝"
    if "喝水" in text:
        return "💧"
    if "冥想" in text or "正念" in text:
        return "🧘"
    return "📌"


def _clean_daily_title(text):
    """Strip conversational scaffolding so the home card shows a clean title.

    Designed to leave the core *content noun* (e.g. "摩托范打卡点评", "百词斩背单词"),
    never leading verbs like "添加", "做", "创建".

    Strategy
    --------
    The input frequently contains *two layers* of scaffolding:

        Layer A (sentence prefix): 帮我 / 我要 / 麻烦 / 添加一个 / 新建一个 …
        Layer B (sentence body):   每天 / 每日 / 提醒我 / 打卡任务 …

    We strip A aggressively (because it never carries content), then split the
    remainder by common separators, drop noise tokens, and keep the last
    *contentful* span.
    """
    raw = text.strip()

    # ── Layer A: anywhere in the text, strip verb-creating scaffolding ──
    layer_a_patterns = [
        # verb-creation: 添加一个... / 新增一项... / 创建一项...
        r"帮?(?:我|你)?(?:添加?|增加?|新增|新建|创建|加入|建)(?:一个|一项|一个的|一项的|个|项)?",
        # plain 我/你 (consume by themselves — strip a single 我/你 only when nothing follows)
        r"(?<![我你])[我你](?![要帮])",
        # 麻烦你 + 帮我 / 我要每天 / 我想每天 / 麻烦 / 请你
        r"麻烦(?:你)?(?:帮我)?",
        r"请你(?:帮我)?",
        # 我想每天 / 我想要每天 / 请问每天 / 帮忙（\"我要\" 出现的频率极高）
        r"(?:我[想要]要?|请问|帮忙)(?:帮我)?(?:每天|每日)?",
        # 句末的"提醒/提醒我"尾巴（"我要背单词提醒我"）
        r"提醒我?$",
    ]
    for pat in layer_a_patterns:
        raw = re.sub(pat, " ", raw)

    # ── Drop wrapping framework / platform-keep-words ──
    t = DAILY_WRAP_CLEAN.sub(r"\1", raw)
    t = DAILY_PREP_CLEAN.sub(r"\1", t)

    # ── Tokenise: drop punctuation connectors ──
    spans = re.split(r"[，。,;；.::、\s]+", t)
    keep = []
    drop_set = {
        # token-level fillers
        "我", "我们", "你", "麻烦", "请", "请问", "帮忙",
        "添加", "新增", "创建", "加入", "建", "做", "搞", "弄",
        "增加", "增加一个",
        "执行", "开始", "开展", "发起", "启动",
        "一个", "一项", "一个的", "一项的",
        # habit / schedule
        "每天", "每日", "天天", "每天都要", "每日都要", "要记得", "都要",
        "提醒", "提醒我", "记得", "别忘了",
        "打卡", "签到", "任务", "事项",
        # bare body part → 高频但很多时候是 scaffolding
        "一下", "设置", "记录", "安排",
        # single-character scaffolding survivors
        "要", "都",
    }
    for s in spans:
        s = s.strip()
        if not s:
            continue
        if s in drop_set:
            continue
        # pure punctuation tokens like "、", "。" — already split away
        keep.append(s)

    if not keep:
        return None

    # Take the last (usually the most concrete) span. Join with space if
    # multiple survived (e.g. "摩托范 打卡点评").
    merged = " ".join(keep).strip()
    merged = re.sub(r"^[\s,。.,;:;、的了着过是的]+", "", merged).strip()
    if not merged or len(merged) < 2:
        return None
    return merged[:100]


def _category(text):
    for category, words in CATEGORY_KEYWORDS.items():
        if any(word in text for word in words):
            return category
    return "其他"


def _is_income(text):
    return any(word in text for word in INCOME_KEYWORDS)


def _is_recurring_bill(text):
    """检测固定账单意图。用户说「每月宽带费 80」「每周交电费 50」「每个月房租 2000」时返回 True。
    任何带金额 + 周期词组合都视为固定账单。
    """
    if not any(w in text for w in RECURRING_KEYWORDS):
        return False
    # 必须有金额，否则归为任务/笔记
    amount, _ = _extract_amount(text)
    if amount is not None:
        return True
    # 兜底：纯数字结尾（"固定房租1500" 中 1500 没跟单位）也算
    tail = re.search(r"(\d+(?:\.\d{1,2})?)\s*\.?\s*$", text)
    if tail:
        return True
    return False


def _recurring_frequency(text):
    """从原文识别周期频率：monthly / weekly / quarterly / yearly / monthly(默认)。"""
    if any(w in text for w in RECURRING_WEEKLY_KEYWORDS):
        return "weekly"
    if any(w in text for w in ("每季度", "每季")):
        return "quarterly"
    if any(w in text for w in ("每年", "每年都")):
        return "yearly"
    return "monthly"


def _date_with_warning(text):
    """Like ``_date`` but returns ``(date_or_None, warning_msg_or_None)``.

    - If ``_date`` returns a valid ``date`` (or default today) → warning is None.
    - If user input contains an absolute date token but the day is invalid
      (2/31, 8/31, etc.) → returns ``(None, "<friendly warning in Chinese>")``
      so the UI can show a toast and we degrade to "today".
    """
    today = timezone.localdate()
    if any(w in text for w in ("前天", "昨天", "后天", "明天")):
        # relative — always valid
        return _date(text), None

    # Does the text mention an absolute date token at all?
    # IMPORTANT: only match "-/<separator>" forms (not "." which collides with decimals like "16.5").
    m_abs = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?", text) or re.search(
        r"(?<!\d)(\d{1,2})\s*[/\-]\s*(\d{1,2})(?!\d|\.)", text)
    if not m_abs:
        # no absolute date token — safe default today, no warning
        return today, None

    month, day = int(m_abs.group(1)), int(m_abs.group(2))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None, f"{month} 月没有这一天，已改为今天"
    try:
        d = today.replace(month=month, day=day)
    except ValueError:
        try:
            # compute actual days-in-month for an accurate message
            import calendar as _cal
            last = _cal.monthrange(today.year, month)[1]
            return None, f"{month} 月只有 {last} 天，已改为今天"
        except Exception:
            return None, f"{month} 月 {day} 日不是有效日期，已改为今天"
    # roll forward if the date is already past
    if d < today:
        try:
            d = d.replace(year=d.year + 1)
        except ValueError:
            d = d.replace(year=d.year + 1, day=28)
    return d, None


def _invalid_date_draft(raw_text, fallback_today, warning):
    """Build a *note*-kind draft (no destructive actions) when user typed an
    invalid date, so they get a friendly warning instead of a silently-wrong
    parsed action."""
    return {
        "kind": "note",
        "title": raw_text[:200],
        "category": "",
        "amount": None,
        "occurred_on": fallback_today.isoformat(),
        "type": "",
        "merchant": "",
        "source": "rule",
        "validation_warning": warning,
    }


def _date(text):
    """Extract a date from the text, or None if the text mentions a date but it's
    invalid (e.g. "2月31号"). Supports:
      - Relative: 前天/昨天/后天/明天
      - Absolute:  X月Y号 / X月Y日 / X月Y / MM-DD / MM/DD
                   Year defaults to the current year; if the resulting date is
                   already past in the current year, the parser rolls it forward
                   to the same month/day next year (so the user's intent of
                   "next occurrence" is honoured).
    """
    today = timezone.localdate()
    if "前天" in text:
        return today - timedelta(days=2)
    if "昨天" in text:
        return today - timedelta(days=1)
    if "后天" in text:
        return today + timedelta(days=2)
    if "明天" in text:
        return today + timedelta(days=1)

    # 绝对日期：先匹配 "X 月 Y 号/日/省略号"
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?", text)
    if not m:
        # 数字日期：MM-DD 或 MM/DD（如 "08-30"、"8/30"）
        m = re.search(r"(?<!\d)(\d{1,2})\s*[/\-]\s*(\d{1,2})(?!\d|\.)", text)
    if m:
        try:
            month = int(m.group(1))
            day = int(m.group(2))
            if not (1 <= month <= 12 and 1 <= day <= 31):
                return None
            # 尝试构造该日期；若该年该月不存在该日（2月31、8月31等）→ 返回 None 上层会提示
            try:
                d = today.replace(month=month, day=day)
            except ValueError:
                return None
            # 如果日期已过且没有指明年份，则推到明年（典型场景："生日 X月X日"）
            if d < today:
                try:
                    d = d.replace(year=d.year + 1)
                except ValueError:
                    # 闰年 2-29 推到明年非闰年失败 → 退到当年 2-28
                    d = d.replace(year=d.year + 1, day=28)
            return d
        except (ValueError, IndexError):
            return None
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


# 明显的「消费」场景词：出现即视为支出意图（即便金额解析失败或单位缺失）
_EXPENSE_CUES = (
    "买", "购买", "入手", "下单", "淘", "剁手",
    "花", "花费", "花了", "消费", "支出", "用了", "付了", "付款", "支付", "刷卡", "刷卡买了",
    "元", "块", "块钱",
)
# 明显的「随心记录 / 灵感」场景词
NOTE_CUES = (
    "随心记录", "随机记录", "记录一下", "记一笔", "灵感", "随想", "备忘", "随笔", "日记",
    "记个事", "提醒自己", "想法",
)
NOTE_TITLE_CLEAN = re.compile(
    r"^(?:"
    r"随心记录[:：]\s*|"
    r"随机记录[:：]\s*|"
    r"记录一下[:：]\s*|"
    r"记一笔[:：]\s*|"
    r"灵感[:：]\s*|"
    r"随想[:：]\s*|"
    r"随笔[:：]\s*|"
    r"备忘[:：]\s*|"
    r"日记[:：]\s*|"
    r"记个事[:：]\s*"
    r")"
)


def _clean_title(text, amount):
    """Remove time markers and amount from text, keep business keywords."""
    t = re.sub(r"\s*(?:今天|昨天|明天|前天|后天|早上|中午|晚上|上午|下午)\s*", "", text)
    # 剥掉绝对日期 token（"8月30号"/"8/30"），避免任务/账单标题残留日期
    t = re.sub(r"\d{1,2}\s*月\s*\d{1,2}\s*[日号]?", "", t)
    t = re.sub(r"(?<!\d)\d{1,2}\s*[/\-]\s*\d{1,2}(?!\d)", "", t)
    t = re.sub(r"\s*(?:花了?|消费了?|支出|用了|付了?|支付)\s*", "", t)
    t = re.sub(r"\s*(?:收到|入账)\s*", "", t)
    # 剥掉任务引导词（避免任务标题出现\"提醒我/要做/待办\"残留）
    t = re.sub(r"^\s*(?:麻烦你|请你|请|麻烦)?(?:提醒我|提醒|记得|要做|帮我|帮我安排|待办|别忘了)", "", t)
    # 剥掉固定账单引导词 / 周期词（避免固定账单标题残留"新增一笔固定账单/每个月"）
    t = _RECURRING_TITLE_CLEAN.sub("", t)
    if amount is not None:
        t = re.sub(rf"{re.escape(str(amount))}\s*(?:元|块|块钱|rmb)?", "", str(t))
        t = re.sub(r"\s*\d+(?:\.\d{1,2})?\s*(?:元|块|块钱|rmb)", "", t)
    t = t.strip("，。, .-") or "日常消费"
    return t[:200]


def parse_text(raw_text):
    """Return an untrusted draft with type/amount/category/note/date."""
    raw_text = raw_text.strip()
    # 1. 先把中文数字归一化成阿拉伯数字，"三十"→"30"
    text = _normalize_chinese_numbers(raw_text)
    parsed_date, date_warning = _date_with_warning(text)
    if parsed_date is not None:
        date = parsed_date
    else:
        # 用户输入了一个看起来像日期但不合法的"2月31号/8月31号"等 → 回到今天但带上提示
        date = timezone.localdate()
        # 上层会把 date_warning 附加到返回 draft，由前端展示
        if date_warning:
            return _invalid_date_draft(raw_text, date, date_warning)

    amount, _ = _extract_amount(text)
    is_income = _is_income(text)

    # ── 固定账单提前识别（必须在 task/expense 之前）───────────
    # 例：「新增一笔固定账单，每个月宽带费 80 元」「固定房租 1500」
    if _is_recurring_bill(text):
        # 兜底金额：固定账单触发但 amount 解析失败时，抓纯数字尾巴
        if amount is None:
            m = re.search(r"(\d+(?:\.\d{1,2})?)\s*\.?\s*$", text)
            if m:
                try:
                    amount = Decimal(m.group(1))
                except InvalidOperation:
                    amount = None
        title = _clean_title(text, amount)
        return {
            "kind": "recurring_expense",
            "title": title,
            "category": _category(text),
            "amount": str(amount) if amount is not None else None,
            "frequency": _recurring_frequency(text),
            "occurred_on": date.isoformat(),
            "type": "expense",
            "merchant": "",
            "source": "rule",
        }

    # ── 每日打卡 / 习惯提醒（必须在 task 之前）────────────────
    # 例：「我想每天背单词」「我每天都要练习口语」「快手签到」
    if _is_daily_reminder(text):
        clean = _clean_daily_title(text)
        if clean:
            return {
                "kind": "daily_reminder",
                "title": clean,
                "icon": _daily_icon(text),
                "occurred_on": date.isoformat(),
                "type": "",
                "merchant": "",
                "source": "rule",
            }

    # ── 随心记录 / 灵感 / 备忘（必须在 expense 之前）─────────────
    # 例：「随心记录：今天天气真好」「灵感：想到了一个好点子」
    if any(cue in text for cue in NOTE_CUES):
        clean_note = NOTE_TITLE_CLEAN.sub("", text).strip()
        if not clean_note:
            clean_note = text
        return {
            "kind": "note",
            "title": clean_note[:200],
            "category": "",
            "amount": None,
            "occurred_on": date.isoformat(),
            "type": "",
            "merchant": "",
            "source": "rule",
        }

    # ── 支付场景：含「买/花/消费/付」等动词即视作支出意图 ─────────
    # 即便 amount 因单位缺失/中文数字漏掉没解析出来，也强制归 expense，
    # 避免"今天买菜花三十"被误判成 task/note（用户实际场景）。
    is_expense_intent = any(cue in text for cue in _EXPENSE_CUES)

    title = _clean_title(text, amount)

    draft = {
        "kind": "expense" if (amount is not None and not is_income) else
                "expense" if is_expense_intent else
                ("note" if not any(w in text for w in ("提醒", "要做", "待办", "完成", "记得", "安排")) else "task"),
        "title": title,
        "category": _category(text) if (amount is not None or is_expense_intent) else "",
        "amount": str(amount) if amount else None,
        "occurred_on": date.isoformat(),
        "type": "income" if is_income else "expense",
        "merchant": "",
    }

    # Override kind for income
    if is_income and amount is not None:
        draft["kind"] = "income"

    # Task detection: 提示词 / 动作动词 / 未来时间+动作（任一命中即视为任务）
    # 注意：有金额或明确消费意图时不算任务（因为"交/付"这类动作词在金额场景下属于消费）
    has_amount = amount is not None
    has_task_cue = any(word in text for word in TASK_REMINDER_KEYWORDS)
    has_action_verb = any(verb in text for verb in TASK_ACTION_VERBS)
    has_future = any(w in text for w in TASK_FUTURE_KEYWORDS) and any(verb in text for verb in TASK_ACTION_VERBS)
    is_task = (has_task_cue or has_action_verb or has_future) and not has_amount and not is_expense_intent
    if is_task:
        draft["kind"] = "task"
        draft["title"] = title
        due_at = None
        today = timezone.localdate()
        match = re.search(r"(?:上午|下午|晚上|中午)?\s*(\d{1,2})[点时](?:([0-5]?\d)分?)?", text)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2) or 0)
            if any(m in text for m in ("下午", "晚上")) and hour < 12:
                hour += 12
            due_at = timezone.make_aware(datetime.combine(date, time(hour, minute))).isoformat()
        else:
            # 没有明确时刻，但文本含绝对日期 token（如「8月30号」）→ 用该日期 + 默认时间，
            # 避免日期被丢掉、错算成今天/明天。date==today 说明没有日期意图，保持 None 让前端按默认处理。
            if date != today:
                due_at = timezone.make_aware(datetime.combine(date, time(10, 0))).isoformat()
        draft["due_at"] = due_at
        draft["priority"] = 1 if any(x in text for x in ("重要", "紧急", "尽快")) else 2

    # Default to note if no amount, no expense intent, and no task keywords
    if amount is None and not is_expense_intent and draft["kind"] == "expense":
        draft["kind"] = "note"
        draft["title"] = text[:200]

    draft.setdefault("due_at", None)
    draft.setdefault("priority", 2)

    return draft
