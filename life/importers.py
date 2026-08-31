"""第三方账单解析（P0-2）。

中文用户的流水九成在支付宝和微信里。这两家导出的 CSV 有三个共同特点，
跟「标准 CSV」完全不是一回事：

1. **带十几行前置说明**（账号、导出时间、分隔线），真正的表头在文件中间；
2. **GBK 编码**，直接按 UTF-8 读会炸；
3. **语义靠中文列表达**（收/支、交易状态），需要按状态过滤掉关闭/退款的记录，
   还有大量「不计收支」的内部划转行（余额宝转入、零钱提现）不能算消费。

本模块把这些差异吃掉，统一输出成内部行结构，交给导入流程复用既有的
预览 → 确认 → 写库链路。

对外只暴露两个函数：
- ``detect_source(text)``  判断账单来源
- ``parse_statement(text)`` 解析成统一行结构 + 跳过原因统计
"""

import csv
import re
from datetime import datetime

from django.utils import timezone

# ── 账单来源 ────────────────────────────────────────────────────────
SOURCE_ALIPAY = "alipay"
SOURCE_WECHAT = "wechat"
SOURCE_LIFEOS = "lifeos"

SOURCE_LABELS = {
    SOURCE_ALIPAY: "支付宝",
    SOURCE_WECHAT: "微信支付",
    SOURCE_LIFEOS: "LifeOS 导出",
}

# ── 列名 → 内部字段 ─────────────────────────────────────────────────
# 两家列名大同小异：微信的商品列叫「商品」，支付宝叫「商品说明」；
# 支付方式一个叫「支付方式」一个叫「收/付款方式」。用含糊的关键字匹配更抗版本变化。
COLUMN_ALIASES = {
    "交易时间": "occurred_at",
    "交易创建时间": "occurred_at",      # 支付宝旧版
    "付款时间": "occurred_at",          # 支付宝旧版
    "交易分类": "category",             # 支付宝
    "交易类型": "category",             # 微信
    "类型": "category",                 # 支付宝旧版
    "交易对方": "counterparty",
    "对方": "counterparty",
    "商品说明": "product",              # 支付宝
    "商品名称": "product",              # 支付宝旧版
    "商品": "product",                  # 微信
    "收/支": "direction",
    "收/付款方式": "method",            # 支付宝
    "支付方式": "method",               # 微信
    "金额(元)": "amount",
    "金额（元）": "amount",
    "金额": "amount",
    "交易状态": "status",               # 支付宝
    "当前状态": "status",               # 微信
    "备注": "note",
    "交易订单号": "order_no",           # 支付宝
    "交易单号": "order_no",             # 微信
    "交易号": "order_no",               # 支付宝旧版
}

# ── 状态过滤 ────────────────────────────────────────────────────────
# 只有真正完成、钱确实动了的记录才导入。
STATUS_OK_HINTS = (
    "交易成功", "支付成功", "还款成功", "代付成功", "已存入零钱",
    "充值成功", "提现成功", "转账成功", "已支付",
)
# 明确不该记账的（失败、关闭、退款）。
STATUS_SKIP_HINTS = (
    "交易关闭", "已关闭", "关闭", "失败", "已全额退款", "已退款",
    "退款成功", "全额退款", "部分退款", "解冻成功", "冻结成功",
    "充值失败", "提现失败",
)

# 方向
DIRECTION_EXPENSE = ("支出", "付款")
DIRECTION_INCOME = ("收入", "收款")
# 「不计收支」是两家对内部划转的统称：余额宝转入、零钱提现、信用卡还款等。
# 这些不是消费也不是收入，默认跳过（可在预览页看到被跳过了多少）。
DIRECTION_NEUTRAL = ("不计收支", "/", "", "中性")

_MONEY_RE = re.compile(r"[^\d.\-]")


def _clean_amount(s):
    """把 '¥1,234.50' / '￥35.00' / ' 12.00 ' 洗成 Decimal 可用的字符串。"""
    if s is None:
        return ""
    return _MONEY_RE.sub("", str(s).replace(",", ""))


def _clean_row(row):
    """去掉单元格首尾空白与 Excel 常见的引号包裹。"""
    out = []
    for c in row:
        if c is None:
            out.append("")
            continue
        c = str(c).strip()
        if len(c) >= 2 and c[0] == c[-1] and c[0] in "\"'":
            c = c[1:-1].strip()
        out.append(c)
    return out


def detect_source(text):
    """判断账单来源；认不出来返回 None。

    先读文件头的品牌字样（最可靠），再退回按表头列判断——
    有些用户会手动删掉前置说明行。
    """
    head = text[:600]
    if "支付宝" in head:
        return SOURCE_ALIPAY
    if "微信" in head:
        return SOURCE_WECHAT

    for line in text.splitlines()[:30]:
        cells = [c.strip() for c in line.split(",")]
        if "交易时间" not in cells:
            continue
        if "收/付款方式" in cells or "交易分类" in cells:
            return SOURCE_ALIPAY
        if "支付方式" in cells or "当前状态" in cells:
            return SOURCE_WECHAT
        if "收/支" in cells:
            return SOURCE_WECHAT  # 只有「收/支 + 微信式列」时按微信处理
    return None


def _find_header_row(lines):
    """在前置说明之后找到真正的表头行，返回 (行号, 单元格列表)。"""
    for i, line in enumerate(lines[:60]):   # 表头不会藏得比 60 行更深
        if "交易时间" not in line and "交易创建时间" not in line:
            continue
        cells = _clean_row(next(csv.reader([line])))
        if "收/支" in cells or "金额" in cells or "金额(元)" in cells:
            return i, cells
    return None, None


def _map_columns(cells):
    """把表头单元格映射成 {内部字段: 列下标}。"""
    mapping = {}
    for idx, name in enumerate(cells):
        field = COLUMN_ALIASES.get(name)
        # 模糊兜底：单元格里含关键字也算命中（抗「金额(元) 」这类空格变体）
        if field is None:
            for alias, f in COLUMN_ALIASES.items():
                if alias and alias in name:
                    field = f
                    break
        if field and field not in mapping:
            mapping[field] = idx
    return mapping


def _parse_dt(s):
    """支付宝/微信的时间戳写法很杂，逐个试。"""
    s = (s or "").strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M", "%Y-%m-%d", "%Y/%m/%d",
        "%Y年%m月%d日 %H:%M:%S", "%Y年%m月%d日",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _direction_of(raw):
    raw = (raw or "").strip()
    for hint in DIRECTION_EXPENSE:
        if hint in raw:
            return "expense"
    for hint in DIRECTION_INCOME:
        if hint in raw:
            return "income"
    return "neutral"


def _status_skipped(raw):
    """返回跳过原因；该记账则返回 None。"""
    raw = (raw or "").strip()
    if not raw:
        return None
    for hint in STATUS_SKIP_HINTS:
        if hint in raw:
            return f"交易未完成（{raw}）"
    for hint in STATUS_OK_HINTS:
        if hint in raw:
            return None
    # 未知状态：宁可导入让用户自己看，也不静默丢数据
    return None


def parse_statement(text, source=None):
    """把支付宝/微信账单文本解析成统一行结构。

    Returns:
        (rows, skipped): rows 是可导入行的列表，skipped 是 {原因: 行数} 统计。
        每行结构：
        {
          "occurred_at": datetime,
          "type": "expense" | "income",
          "amount": str,            # 正数字符串
          "category_name": str,     # 账单自带分类（交易分类/交易类型）
          "merchant": str,          # 交易对方
          "note": str,              # 商品说明 + 备注
          "method": str,            # 支付方式
          "order_no": str,          # 订单号，用于去重
        }
    """
    source = source or detect_source(text)
    if source is None:
        return [], {}

    lines = text.splitlines()
    header_idx, header_cells = _find_header_row(lines)
    if header_idx is None:
        return [], {}

    mapping = _map_columns(header_cells)
    if "occurred_at" not in mapping or "amount" not in mapping:
        return [], {}

    rows, skipped = [], {}

    def _skip(reason):
        skipped[reason] = skipped.get(reason, 0) + 1

    for raw_line in lines[header_idx + 1:]:
        if not raw_line.strip():
            continue
        # 忽略分隔线与统计尾注（-------- / 共 20 笔记录 / 导出时间：...）
        stripped = raw_line.strip()
        if set(stripped) <= set("-—= "):
            continue
        if stripped.startswith(("共", "导出时间", "说明", "注：", "备注：")):
            continue

        try:
            cells = _clean_row(next(csv.reader([raw_line])))
        except csv.Error:
            _skip("该行格式无法解析")
            continue

        # 文件尾注（「本文件由支付宝提供」之类）列数远少于表头，静默忽略，
        # 不计入跳过统计，免得用户看到一堆无关的「时间无法解析」。
        if len(cells) < max(2, len(header_cells) // 2):
            continue

        def get(field):
            idx = mapping.get(field)
            if idx is None or idx >= len(cells):
                return ""
            return cells[idx]

        # 表头之后可能还有第二行表头（微信偶发），跳过
        if get("occurred_at") in ("交易时间", "交易创建时间"):
            continue

        status_reason = _status_skipped(get("status"))
        if status_reason:
            _skip(status_reason)
            continue

        direction = _direction_of(get("direction"))
        if direction == "neutral":
            _skip("不计收支（内部划转，非消费）")
            continue

        dt = _parse_dt(get("occurred_at"))
        if dt is None:
            _skip("时间无法解析")
            continue

        amount_s = _clean_amount(get("amount"))
        if not amount_s or amount_s in ("-", "."):
            _skip("金额为空")
            continue
        try:
            amount = abs(float(amount_s))
        except ValueError:
            _skip("金额无法解析")
            continue
        if amount <= 0:
            _skip("金额为 0")
            continue

        product = get("product")
        note = get("note")
        # 商品说明为空时用分类兜底，避免导入一堆「未命名」
        note_text = " / ".join(x for x in (product, note) if x)

        rows.append({
            "occurred_at": dt,
            "type": direction,
            "amount": f"{amount:.2f}",
            "category_name": get("category"),
            "merchant": get("counterparty") or get("method"),
            "note": note_text,
            "method": get("method"),
            "order_no": get("order_no"),
        })

    return rows, skipped


def to_naive_str(dt):
    """统一转成 'YYYY-MM-DD HH:MM' 字符串，供 session 暂存与预览渲染。"""
    if timezone.is_aware(dt):
        dt = timezone.localtime(dt)
    return dt.strftime("%Y-%m-%d %H:%M")
