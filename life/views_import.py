"""数据导入（CSV）。

设计上与导出成对：导入的 CSV 格式就是导出产出的格式，
用户从别的软件导出后，按同样表头整理即可导入。

流程分两阶段，避免「上传即写入」造成误导入：
  1) POST /import/expense/  上传文件 → 解析 → 预览（结果暂存 session）
  2) POST /import/expense/confirm/  确认后才真正写库

安全约束：
- 只导入到 request.user 名下
- 文件行数与大小有上限，避免超大文件拖垮请求
- 分类按名查找，找不到就为当前用户新建，绝不关联他人分类
"""

import csv
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Category, Expense

# 单次导入上限。个人记账场景下 5000 行已远超实际需求，
# 主要是防御误传超大文件把请求拖死。
IMPORT_MAX_ROWS = 5000
IMPORT_MAX_BYTES = 5 * 1024 * 1024  # 5MB

# 与导出表头一致：日期,类型,金额,分类,商家,备注,状态,来源
EXPECTED_HEADER = ["日期", "类型", "金额", "分类", "商家", "备注", "状态", "来源"]

# CSV 里「类型」列的中文写法 → 模型值
TYPE_MAP = {
    "支出": "expense", "expense": "expense",
    "收入": "income", "income": "income",
    "转账": "transfer", "transfer": "transfer",
}

SESSION_KEY = "import_expense_preview"


@login_required
def import_index(request):
    """导入首页：说明支持的格式，提供上传入口。"""
    return render(request, "life/import.html", {
        "expected_header": EXPECTED_HEADER,
        "max_rows": IMPORT_MAX_ROWS,
    })


@login_required
@require_POST
def import_expense(request):
    """阶段一：上传 CSV → 解析 → 预览（不写库）。"""
    f = request.FILES.get("file")
    if not f:
        messages.error(request, "请先选择一个 CSV 文件。")
        return redirect("import_index")

    if f.size > IMPORT_MAX_BYTES:
        messages.error(request, f"文件过大（{f.size // 1024}KB），请控制在 5MB 以内。")
        return redirect("import_index")

    try:
        raw = f.read()
        # 兼容带 BOM 的 UTF-8（Excel 导出常见）与 GBK（部分国内软件导出）
        text = _decode(raw)
    except Exception:
        messages.error(request, "无法读取文件，请确认是 UTF-8 或 GBK 编码的 CSV。")
        return redirect("import_index")

    rows = _parse_rows(text)
    if rows is None:
        messages.error(request, f"表头不匹配，请确认第一行为：{'、'.join(EXPECTED_HEADER)}")
        return redirect("import_index")

    if len(rows) > IMPORT_MAX_ROWS:
        messages.error(request, f"行数过多（{len(rows)} 行），单次请控制在 {IMPORT_MAX_ROWS} 行以内。")
        return redirect("import_index")

    parsed, errors = _build_preview(request.user, rows)

    # 预览数据放 session，确认阶段取用；只存必要字段，避免撑爆 session
    request.session[SESSION_KEY] = parsed
    return render(request, "life/import_confirm.html", {
        "items": parsed,
        "errors": errors,
        "ok_count": len(parsed),
        "err_count": len(errors),
    })


@login_required
@require_POST
def import_expense_confirm(request):
    """阶段二：确认后真正写入数据库。"""
    parsed = request.session.pop(SESSION_KEY, None)
    if not parsed:
        messages.error(request, "导入会话已过期，请重新上传文件。")
        return redirect("import_index")

    created = 0
    skipped = 0
    for item in parsed:
        if _already_exists(request.user, item):
            skipped += 1
            continue
        Expense.objects.create(
            user=request.user,
            type=item["type"],
            amount=Decimal(item["amount"]),
            category_id=item["category_id"],
            merchant=item["merchant"],
            note=item["note"],
            occurred_at=timezone.make_aware(datetime.strptime(item["occurred_at"], "%Y-%m-%d %H:%M")),
            status="confirmed",
            source="manual",
        )
        created += 1

    if created:
        messages.success(request, f"导入完成：新增 {created} 条" + (f"，跳过重复 {skipped} 条" if skipped else ""))
    else:
        messages.warning(request, f"没有新增记录（跳过重复 {skipped} 条）。")
    return redirect("expense_list")


# ── 内部辅助 ────────────────────────────────────────────────────────

def _decode(raw: bytes) -> str:
    """依次尝试 UTF-8(BOM) / UTF-8 / GBK，兼容不同来源的导出文件。"""
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("unknown encoding")


def _parse_rows(text):
    """按导出表头解析 CSV，返回 dict 列表；表头不匹配返回 None。"""
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return None
    header = [h.strip() for h in header]
    if header[:len(EXPECTED_HEADER)] != EXPECTED_HEADER:
        return None

    rows = []
    for r in reader:
        if not r or all(not c.strip() for c in r):
            continue
        r = (r + [""] * len(EXPECTED_HEADER))[:len(EXPECTED_HEADER)]
        rows.append(dict(zip(EXPECTED_HEADER, [c.strip() for c in r])))
    return rows


def _build_preview(user, rows):
    """把原始行转成可导入的结构，并返回无法解析的行。"""
    parsed, errors = [], []
    # 预取用户可见的分类，避免逐行查库
    cat_cache = {}

    for idx, row in enumerate(rows, start=2):  # 从第 2 行开始（第 1 行是表头）
        try:
            amount = Decimal(row["金额"])
            if amount <= 0:
                raise ValueError("金额必须大于 0")
        except (InvalidOperation, ValueError) as e:
            errors.append({"line": idx, "reason": f"金额无效：{row['金额']}（{e}）", "raw": row})
            continue

        occurred = _parse_dt(row["日期"])
        if occurred is None:
            errors.append({"line": idx, "reason": f"日期无法解析：{row['日期']}（应为 2026-08-29 或 2026-08-29 14:30）", "raw": row})
            continue

        type_ = TYPE_MAP.get(row["类型"], "expense")

        cat_id = None
        cat_name = row["分类"]
        if cat_name:
            cat_id = _resolve_category(user, cat_name, type_, cat_cache)

        parsed.append({
            "occurred_at": occurred.strftime("%Y-%m-%d %H:%M"),
            "type": type_,
            "type_display": {"expense": "支出", "income": "收入", "transfer": "转账"}[type_],
            "amount": str(amount),
            "category_id": cat_id,
            "category_name": cat_name,
            "merchant": row["商家"],
            "note": row["备注"],
        })
    return parsed, errors


def _parse_dt(s):
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M", "%Y/%m/%d"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


def _resolve_category(user, name, type_, cache):
    """按名找分类；找不到就为当前用户新建一个（绝不复用他人分类）。"""
    key = (name, type_)
    if key in cache:
        return cache[key]
    cat = Category.objects.filter(
        Q(user=user) | Q(user__isnull=True), name=name, type=type_
    ).first()
    if cat is None:
        cat = Category.objects.create(user=user, name=name, type=type_)
    cache[key] = cat.id
    return cat.id


def _already_exists(user, item):
    """同日期 + 同金额 + 同备注视为重复，避免重复导入产生脏数据。"""
    dt = datetime.strptime(item["occurred_at"], "%Y-%m-%d %H:%M")
    return Expense.objects.filter(
        user=user,
        is_deleted=False,
        amount=Decimal(item["amount"]),
        note=item["note"],
        occurred_at=timezone.make_aware(dt),
    ).exists()
