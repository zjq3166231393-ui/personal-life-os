"""OCR（图片识别记账）视图。

流程：
1. ``ocr_upload``：GET 显示上传页；POST 上传图片 → 调用 OCR 引擎识别 →
   ``extract_receipt_fields`` 提取金额/日期/商户 → 渲染确认页（预填）。
2. ``ocr_save``：校验用户修正后的字段 → 创建 Expense（source="ocr"）。

所有字段校验与快速记账保持一致（金额>0、最多两位小数、越权分类/账户静默忽略）。
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone

from common.audit import record

from . import ocr
from .models import Account, Category, Expense
from .ocr import OCRException

_ALLOWED_IMAGE_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpeg",
    "image/webp": ".webp",
}
_MAX_IMAGE_BYTES = 10 * 1024 * 1024


def _provider_name():
    from django.conf import settings
    return getattr(settings, "OCR_PROVIDER", "tesseract")


def _load_choices(request):
    """加载确认页需要的分类（支出）/ 账户选项，均按当前用户隔离。"""
    categories = Category.objects.filter(
        Q(user=request.user) | Q(user__isnull=True),
        type="expense", is_active=True,
    ).order_by("name")
    accounts = Account.objects.filter(
        user=request.user, is_deleted=False, is_active=True,
    ).order_by("type", "name")
    return categories, accounts


@login_required
def ocr_upload(request):
    provider = _provider_name()
    if request.method != "POST":
        return render(request, "life/ocr_upload.html", {"provider": provider})

    img = request.FILES.get("image")
    if not img:
        messages.error(request, "请先选择一张小票 / 账单图片。")
        return render(request, "life/ocr_upload.html", {"provider": provider})
    if img.content_type not in _ALLOWED_IMAGE_TYPES:
        messages.error(request, "仅支持 PNG / JPG / WEBP 图片。")
        return render(request, "life/ocr_upload.html", {"provider": provider})
    if img.size > _MAX_IMAGE_BYTES:
        messages.error(request, "图片过大，请压缩到 10MB 以内。")
        return render(request, "life/ocr_upload.html", {"provider": provider})

    try:
        text = ocr.get_ocr_provider().recognize(img.read())
    except OCRException as e:
        messages.error(request, str(e))
        return render(request, "life/ocr_upload.html", {"provider": provider})

    if not text or not text.strip():
        messages.warning(request, "没有从图片中识别出文字，请确认图片清晰或换一张。")
        return render(request, "life/ocr_upload.html", {"provider": provider})

    fields = ocr.extract_receipt_fields(text)
    categories, accounts = _load_choices(request)
    return render(request, "life/ocr_result.html", {
        "provider": provider,
        "raw_text": text,
        "amount": fields["amount"],
        "date": fields["date"].isoformat() if fields["date"] else "",
        "merchant": fields["merchant"],
        "type": "expense",
        "categories": categories,
        "accounts": accounts,
    })


@login_required
def ocr_save(request):
    if request.method != "POST":
        return redirect("ocr_upload")

    raw_text = request.POST.get("raw_text", "")
    type_ = request.POST.get("type", "expense")
    if type_ not in ("expense", "income"):
        type_ = "expense"
    note = (request.POST.get("note") or "").strip()[:500]
    date_str = (request.POST.get("date") or "").strip()

    # 金额（与快速记账一致：>0、最多两位小数）
    try:
        amount = Decimal((request.POST.get("amount") or "").strip())
    except (InvalidOperation, ValueError):
        return _result_error(request, raw_text, type_, note, date_str, "请输入有效的金额。")
    if amount <= 0:
        return _result_error(request, raw_text, type_, note, date_str, "金额必须大于 0。")
    if amount.as_tuple().exponent < -2:
        return _result_error(request, raw_text, type_, note, date_str, "金额最多保留两位小数。")

    # 日期（留空=今天）
    occurred_at = timezone.now()
    if date_str:
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
            local_time = timezone.localtime(timezone.now()).time()
            occurred_at = timezone.make_aware(datetime.combine(d, local_time))
        except ValueError:
            return _result_error(request, raw_text, type_, note, date_str, "日期格式无效，应为 YYYY-MM-DD。")

    # 分类：只接受本人或全局、类型匹配、启用中（越权 id 静默忽略）
    category = None
    cat_id = request.POST.get("category_id")
    if cat_id:
        category = Category.objects.filter(
            Q(user=request.user) | Q(user__isnull=True),
            pk=cat_id, type=type_, is_active=True,
        ).first()

    # 账户：只能选自己的、启用中的（越权/失效静默忽略）
    account = None
    acc_id = request.POST.get("account_id")
    if acc_id:
        account = Account.objects.filter(
            pk=acc_id, user=request.user, is_deleted=False, is_active=True,
        ).first()

    expense = Expense.objects.create(
        user=request.user,
        type=type_,
        amount=amount,
        category=category,
        account=account,
        note=note,
        occurred_at=occurred_at,
        status="confirmed",
        source="ocr",
        raw_text=raw_text,
    )
    record(request.user, "expense.create", expense.pk,
           f"图片识别记账: {expense.display_title} ¥{amount}")
    messages.success(request, f"已通过图片记账保存：{expense.display_title} ¥{amount}")
    return redirect("expense_list")


def _result_error(request, raw_text, type_, note, date_str, error_msg):
    """校验失败时重新渲染确认页并保留用户已填内容。"""
    messages.error(request, error_msg)
    categories, accounts = _load_choices(request)
    return render(request, "life/ocr_result.html", {
        "provider": _provider_name(),
        "raw_text": raw_text,
        "amount": request.POST.get("amount", ""),
        "date": date_str,
        "merchant": note,
        "type": type_,
        "categories": categories,
        "accounts": accounts,
    })
