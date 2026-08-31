"""账目凭证附件（P0-3）。

报销、退货、账单争议都要留凭据。此前全库没有文件字段，OCR 识别完就丢。

三个视图刻意保持很小：
- ``attachment_upload``  上传（扩展名 + content_type 双重白名单，5MB 上限）
- ``attachment_delete``  软删除（跟全站 is_deleted 一致，可恢复）
- ``attachment_serve``   受控读取

读取为什么走视图而不是 MEDIA_URL 静态服务：
静态服务在 DEBUG=False 时不挂载，且一旦挂载就是「谁拿到 URL 谁都能看」。
走视图能校验 user，生产环境开箱可用，也不会泄露他人凭证。
"""

import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (
    RECEIPT_ALLOWED_EXT,
    RECEIPT_IMAGE_EXT,
    RECEIPT_MAX_BYTES,
    Attachment,
    Expense,
)

# content_type 白名单：只看浏览器给的 MIME 不够（可伪造），
# 但扩展名 + MIME 双重校验已经能挡住绝大多数误传与恶意上传。
ALLOWED_CONTENT_TYPES = {
    "image/jpeg", "image/pjpeg", "image/png", "image/webp",
    "image/gif", "image/heic", "image/heif", "application/pdf",
}


def _clean_filename(name):
    """只保留用于展示的文件名，去掉可能的路径片段。"""
    name = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    return name[:255]


@login_required
@require_POST
def attachment_upload(request, pk):
    """给指定账目上传凭证。"""
    expense = get_object_or_404(Expense, pk=pk, user=request.user, is_deleted=False)

    f = request.FILES.get("file")
    if not f:
        messages.error(request, "请先选择要上传的文件。")
        return redirect("expense_detail", pk=pk)

    if f.size > RECEIPT_MAX_BYTES:
        messages.error(request, f"文件过大（{f.size // 1024}KB），单张请控制在 5MB 以内。")
        return redirect("expense_detail", pk=pk)

    fname = _clean_filename(f.name)
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
    if ext not in RECEIPT_ALLOWED_EXT:
        messages.error(request, f"不支持的文件类型「{ext or '未知'}」，请上传图片或 PDF。")
        return redirect("expense_detail", pk=pk)

    ctype = (f.content_type or "").split(";")[0].strip().lower()
    if ctype and ctype not in ALLOWED_CONTENT_TYPES:
        messages.error(request, f"文件内容类型「{ctype}」不在允许范围内。")
        return redirect("expense_detail", pk=pk)

    Attachment.objects.create(
        user=request.user,
        expense=expense,
        file=f,
        name=fname,
        size=f.size,
        content_type=ctype or mimetypes.guess_type(fname)[0] or "",
        is_image=ext in RECEIPT_IMAGE_EXT,
    )
    messages.success(request, "凭证已上传。")
    return redirect("expense_detail", pk=pk)


@login_required
@require_POST
def attachment_delete(request, pk):
    """删除凭证（软删除，文件保留在磁盘上以便追溯）。"""
    att = get_object_or_404(Attachment, pk=pk, user=request.user, is_deleted=False)
    expense_pk = att.expense_id
    att.is_deleted = True
    att.deleted_at = timezone.now()
    att.save(update_fields=["is_deleted", "deleted_at"])
    messages.success(request, "凭证已删除。")
    return redirect("expense_detail", pk=expense_pk)


@login_required
def attachment_serve(request, pk):
    """读取凭证：校验归属后以附件形式返回。

    inline 展示图片（方便直接看小票），PDF 也用 inline 让浏览器内嵌预览，
    但都带 Content-Disposition 兜底文件名，另存时名字是对的。
    """
    att = get_object_or_404(Attachment, pk=pk, user=request.user, is_deleted=False)
    if not att.file:
        raise Http404("凭证文件不存在")
    try:
        fh = att.file.open("rb")
    except FileNotFoundError:
        raise Http404("凭证文件已丢失")
    ctype = att.content_type or mimetypes.guess_type(att.name)[0] or "application/octet-stream"
    return FileResponse(fh, content_type=ctype, as_attachment=False, filename=att.name)


@login_required
def attachment_download(request, pk):
    """强制下载（区别于 serve 的内联预览）。"""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    att = get_object_or_404(Attachment, pk=pk, user=request.user, is_deleted=False)
    if not att.file:
        raise Http404("凭证文件不存在")
    try:
        fh = att.file.open("rb")
    except FileNotFoundError:
        raise Http404("凭证文件已丢失")
    ctype = att.content_type or mimetypes.guess_type(att.name)[0] or "application/octet-stream"
    return FileResponse(fh, content_type=ctype, as_attachment=True, filename=att.name)
