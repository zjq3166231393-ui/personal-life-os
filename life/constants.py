"""集中管理"魔法数字"常量，便于统一调参与审阅。

这里收录的是有业务含义的阈值 / 展示上限，而不是与模型字段长度绑定的截断值
（后者集中在 models 里）。改这些常量即可调整看板、预算、异常检测的灵敏度，
不用再到各视图函数里翻字面量。

比值类常量（BUDGET_WARN_RATIO 等）直接定义为 Decimal，与视图里的金额运算保持一致，
替换时无需再包一层 Decimal(str(...))。
"""

from decimal import Decimal

# ── 分页 / 展示上限 ─────────────────────────────────────────────
PAGE_SIZE = 20                              # 支出列表分页每页条数

# 首页 / 看板各类 Top-N 展示条数
EXPENSE_CAT_TOPN_HOME = 5                   # 支出列表页：分类占比 Top 5
TOP_SPENDING_CATS = 3                       # 预算页：Top 3 消费分类
ANOMALY_TOPN = 5                           # 看板：异常提醒最多 5 条
SUGGESTION_TOPN = 5                        # 看板：生活建议 / 建议卡最多 5 条
UPCOMING_TOPN = 10                          # 看板：待办账单最多 10 条
COUNTDOWN_HOME_TOPN = 6                     # 首页：倒计时最多 6 条
LARGE_ITEM_TOPN = 3                         # 看板：大额单笔最多展示 3 条

# ── 时间窗口 ───────────────────────────────────────────────────
PERIOD_DAYS = {"3": 3, "7": 7, "30": 30}   # 支出列表智能时段选项（天）
DEFAULT_PERIOD = "7"                        # 默认近 7 天
DAY_TREND_DAYS = 30                         # 预算页：30 天趋势
MONTH_TREND_COUNT = 6                       # 看板：近 6 个月趋势
WEEK_TREND_DAYS = 7                         # 看板：近 7 天任务/打卡趋势
STREAK_MAX_DAYS = 365                        # 连续打卡最长回看 365 天

# ── 预算 / 储蓄阈值 ───────────────────────────────────────────
BUDGET_WARN_RATIO = Decimal("0.8")          # 预算执行率 > 80% 预警
SAVINGS_IMPROVE_RATIO = Decimal("0.85")     # 本月比上月省 > 15%（< 85%）才算"省了"
SAVINGS_RATE_LOW = Decimal("0.2")           # 储蓄率 < 20% 偏低

# ── 异常 / 大额检测阈值 ───────────────────────────────────────
ANOMALY_SPIKE_FACTOR = 3                    # 单笔 > 分类均值 3 倍 / 当日 > 30 日均 3 倍
CATEGORY_GROWTH_FACTOR = 2                  # 本月分类支出 > 上月 2 倍
CATEGORY_SPIKE_RATIO = Decimal("1.3")       # 分类月支出 > 近 3 月均值 1.3 倍
BILL_CHANGE_ALERT_RATIO = Decimal("0.2")    # 固定账单实付与预期偏离 > 20% 预警
LARGE_EXPENSE_MIN = Decimal("200")          # 单笔大额判定下限 ¥200
LARGE_EXPENSE_PCT = Decimal("0.2")          # 或 > 月支出 20%
TOP_CAT_CONCENTRATION_PCT = 40              # 单一分类占比 >= 40% 集中度过高
RECURRING_SHARE_ALERT = Decimal("0.5")      # 固定支出占比 > 50% 提醒
MONTH_DIFF_ALERT_RATIO = Decimal("0.15")    # 与上月环比涨跌 > 15% 提醒

# ── 提醒 / 任务阈值 ───────────────────────────────────────────
OVERDUE_ALERT_COUNT = 2                     # 逾期任务 > 2 条时给建议
SOON_DAYS = 3                               # 倒计时 <= 3 天为"临近"
UPCOMING_HORIZON_DAYS = 30                  # 提醒未来 30 天范围内为"即将到来"
SUGGESTION_GEN_EVERY_N_DAYS = 3             # 每隔 3 天生成一次建议

# ── AI 请求默认值 ─────────────────────────────────────────────
AI_DEFAULT_MODEL = "deepseek-chat"          # DeepSeek 默认模型名
AI_REQUEST_TIMEOUT = 30                     # AI 请求超时（秒）
AI_MAX_TOKENS = 1024                        # AI 单次最大输出 token
AI_API_BASE = "https://api.deepseek.com"    # DeepSeek API 端点
