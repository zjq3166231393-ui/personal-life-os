"""多币种工具（P1-5）。

设计取舍：
- 本位币（base currency）固定为 CNY，作为所有折算的锚点；
- 每笔 Expense 记原始币种 + 原始金额，并可在记账时填入「汇率 rate」
  （1 单位本币 = rate 单位本位币），由此算出 amount_base（本位币金额）；
- 汇率不接外部 API，由用户在记账时手动填写（MVP），后续可加汇率表；
- 聚合统计仍基于 amount（向后兼容，不破坏既有报表），amount_base 用于展示折算。
"""

from decimal import Decimal

BASE_CURRENCY = "CNY"

# 币种元数据：label 中文名、symbol 符号、decimals 小数位
CURRENCY_META = {
    "CNY": {"label": "人民币", "symbol": "¥", "decimals": 2},
    "USD": {"label": "美元", "symbol": "$", "decimals": 2},
    "HKD": {"label": "港币", "symbol": "HK$", "decimals": 2},
    "TWD": {"label": "新台币", "symbol": "NT$", "decimals": 2},
    "JPY": {"label": "日元", "symbol": "¥", "decimals": 0},
    "EUR": {"label": "欧元", "symbol": "€", "decimals": 2},
    "GBP": {"label": "英镑", "symbol": "£", "decimals": 2},
    "KRW": {"label": "韩元", "symbol": "₩", "decimals": 0},
    "SGD": {"label": "新加坡元", "symbol": "S$", "decimals": 2},
    "AUD": {"label": "澳元", "symbol": "A$", "decimals": 2},
    "THB": {"label": "泰铢", "symbol": "฿", "decimals": 2},
    "MYR": {"label": "马来西亚林吉特", "symbol": "RM", "decimals": 2},
}

CURRENCY_CHOICES = [(code, f"{meta['label']} ({code})") for code, meta in CURRENCY_META.items()]


def format_money(amount, currency=BASE_CURRENCY):
    """按币种格式化金额，如 ¥18.50 / $12.00 / ¥1,200（日元无小数，带千分位）。"""
    meta = CURRENCY_META.get(currency, CURRENCY_META[BASE_CURRENCY])
    decimals = meta["decimals"]
    if amount is None:
        return f"{meta['symbol']}0" + ("." + "0" * decimals if decimals else "")
    sign = "-" if amount < 0 else ""
    amt = abs(amount)
    s = f"{amt:.{decimals}f}" if decimals else f"{amt:.0f}"
    int_part, dot, frac = s.partition(".")
    int_part = f"{int(int_part):,}"
    return f"{sign}{meta['symbol']}{int_part}{dot}{frac}"


def to_base(amount, currency=BASE_CURRENCY, rate=1):
    """把某币种金额折算成本位币金额。本位币本身 rate 视为 1。"""
    if currency == BASE_CURRENCY:
        return amount
    return (amount or Decimal("0")) * (rate or 1)


def is_foreign(currency):
    return currency not in (BASE_CURRENCY, None, "")
