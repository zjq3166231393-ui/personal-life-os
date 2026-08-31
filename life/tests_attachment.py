"""凭证附件测试（P0-3）。

报销、退货、账单争议都要留凭据。此前全库没有文件字段。

重点验证的是安全边界，而不只是「能传上去」：
- 落盘文件名必须是 uuid，原始文件名只用于展示（防路径穿越与同名覆盖）
- 扩展名 + content_type 双重白名单
- 读取必须校验归属，别人猜到 URL 也拿不到
- 删除走软删除，与全站 is_deleted 一致
"""

import os
import shutil
import tempfile
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Attachment, Category, Expense

MEDIA_ROOT = tempfile.mkdtemp(prefix="lifeos-test-media-")

# 1x1 PNG
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


def tearDownModule():  # noqa: N802
    shutil.rmtree(MEDIA_ROOT, ignore_errors=True)


def _mkuser(name):
    return User.objects.create_user(name, password="TestPass123!")


def _upload(name="receipt.png", content=PNG_BYTES, ctype="image/png"):
    return SimpleUploadedFile(name, content, content_type=ctype)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class AttachmentModelTests(TestCase):
    """落盘命名与元数据。"""

    def setUp(self):
        self.u = _mkuser("att_u1")
        self.exp = Expense.objects.create(
            user=self.u, type="expense", amount=Decimal("88.00"),
            occurred_at=timezone.now(), note="测试账目",
        )

    def test_file_saved_with_uuid_name(self):
        att = Attachment.objects.create(
            user=self.u, expense=self.exp, file=_upload(), name="receipt.png",
            size=len(PNG_BYTES), content_type="image/png", is_image=True,
        )
        stored = os.path.basename(att.file.name)
        self.assertNotIn("receipt", stored)          # 原始文件名不进存储路径
        self.assertTrue(stored.endswith(".png"))
        self.assertEqual(len(stored.split(".")[0]), 32)  # uuid4().hex

    def test_path_traversal_filename_is_neutralized(self):
        """文件名里的 ../ 不能影响落盘路径。"""
        att = Attachment.objects.create(
            user=self.u, expense=self.exp,
            file=_upload(name="evil.png"),             # upload_to 只用扩展名
            name="../../evil.png", size=len(PNG_BYTES),
            content_type="image/png", is_image=True,
        )
        self.assertNotIn("..", att.file.name)
        self.assertTrue(att.file.name.startswith("receipts/user_"))

    def test_size_display(self):
        att = Attachment.objects.create(
            user=self.u, expense=self.exp, file=_upload(), name="a.png",
            size=2048, content_type="image/png", is_image=True,
        )
        self.assertEqual(att.size_display, "2 KB")
        att.size = 3 * 1024 * 1024
        self.assertEqual(att.size_display, "3.0 MB")

    def test_str_falls_back_to_stored_name(self):
        att = Attachment.objects.create(
            user=self.u, expense=self.exp, file=_upload(), name="",
            content_type="image/png",
        )
        self.assertTrue(str(att).endswith(".png"))


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class AttachmentUploadTests(TestCase):
    """上传的白名单与限额。"""

    def setUp(self):
        self.u = _mkuser("att_u2")
        self.cat = Category.objects.create(user=self.u, name="餐饮", type="expense")
        self.exp = Expense.objects.create(
            user=self.u, category=self.cat, type="expense", amount=Decimal("88.00"),
            occurred_at=timezone.now(), note="测试账目",
        )
        self.client.force_login(self.u)
        self.url = reverse("attachment_upload", args=[self.exp.pk])

    def test_upload_image_ok(self):
        resp = self.client.post(self.url, {"file": _upload()})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Attachment.objects.filter(expense=self.exp, is_deleted=False).count(), 1)

    def test_upload_pdf_ok(self):
        resp = self.client.post(self.url, {"file": _upload("bill.pdf", PDF_BYTES, "application/pdf")})
        self.assertEqual(resp.status_code, 302)
        att = Attachment.objects.get(expense=self.exp)
        self.assertFalse(att.is_image)

    def test_reject_disallowed_extension(self):
        resp = self.client.post(self.url, {"file": _upload("x.exe", b"MZ\x00", "application/octet-stream")})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Attachment.objects.filter(expense=self.exp).count(), 0)

    def test_reject_mismatched_content_type(self):
        """.png 后缀但声明成 text/html —— 双重校验应拦下。"""
        resp = self.client.post(self.url, {"file": _upload("x.png", b"<html>", "text/html")})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Attachment.objects.filter(expense=self.exp).count(), 0)

    def test_reject_oversized(self):
        big = SimpleUploadedFile("big.png", b"0" * (5 * 1024 * 1024 + 1), content_type="image/png")
        resp = self.client.post(self.url, {"file": big})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Attachment.objects.filter(expense=self.exp).count(), 0)

    def test_reject_no_file(self):
        resp = self.client.post(self.url, {})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Attachment.objects.filter(expense=self.exp).count(), 0)

    def test_cannot_upload_to_others_expense(self):
        other = _mkuser("att_u3")
        other_exp = Expense.objects.create(
            user=other, type="expense", amount=Decimal("1.00"),
            occurred_at=timezone.now(), note="别人的账",
        )
        resp = self.client.post(reverse("attachment_upload", args=[other_exp.pk]), {"file": _upload()})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(Attachment.objects.filter(expense=other_exp).count(), 0)

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.post(self.url, {"file": _upload()})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.url or "")


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class AttachmentServeTests(TestCase):
    """读取权限——这是最容易出事的一环。"""

    def setUp(self):
        self.u = _mkuser("att_u4")
        self.other = _mkuser("att_u5")
        self.exp = Expense.objects.create(
            user=self.u, type="expense", amount=Decimal("10.00"),
            occurred_at=timezone.now(), note="我的账",
        )
        self.att = Attachment.objects.create(
            user=self.u, expense=self.exp, file=_upload(),
            name="receipt.png", size=len(PNG_BYTES),
            content_type="image/png", is_image=True,
        )

    def test_owner_can_serve(self):
        self.client.force_login(self.u)
        resp = self.client.get(reverse("attachment_serve", args=[self.att.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/png")

    def test_other_user_gets_404(self):
        """别人猜到 URL 也拿不到。"""
        self.client.force_login(self.other)
        resp = self.client.get(reverse("attachment_serve", args=[self.att.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(reverse("attachment_serve", args=[self.att.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.url or "")

    def test_download_sets_attachment_header(self):
        self.client.force_login(self.u)
        resp = self.client.get(reverse("attachment_download", args=[self.att.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("attachment", resp["Content-Disposition"])

    def test_other_user_cannot_download(self):
        self.client.force_login(self.other)
        resp = self.client.get(reverse("attachment_download", args=[self.att.pk]))
        self.assertEqual(resp.status_code, 404)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class AttachmentDeleteTests(TestCase):
    """删除走软删除，与全站一致。"""

    def setUp(self):
        self.u = _mkuser("att_u6")
        self.other = _mkuser("att_u7")
        self.exp = Expense.objects.create(
            user=self.u, type="expense", amount=Decimal("10.00"),
            occurred_at=timezone.now(), note="我的账",
        )
        self.att = Attachment.objects.create(
            user=self.u, expense=self.exp, file=_upload(),
            name="receipt.png", size=len(PNG_BYTES),
            content_type="image/png", is_image=True,
        )

    def test_owner_delete_is_soft(self):
        self.client.force_login(self.u)
        resp = self.client.post(reverse("attachment_delete", args=[self.att.pk]))
        self.assertEqual(resp.status_code, 302)
        self.att.refresh_from_db()
        self.assertTrue(self.att.is_deleted)
        self.assertIsNotNone(self.att.deleted_at)
        # 文件仍在磁盘上，便于追溯
        self.assertTrue(os.path.exists(self.att.file.path))

    def test_deleted_attachment_hidden_from_detail(self):
        self.client.force_login(self.u)
        self.client.post(reverse("attachment_delete", args=[self.att.pk]))
        html = self.client.get(reverse("expense_detail", args=[self.exp.pk])).content.decode()
        self.assertNotIn("receipt.png", html)

    def test_deleted_attachment_cannot_be_served(self):
        self.client.force_login(self.u)
        self.client.post(reverse("attachment_delete", args=[self.att.pk]))
        resp = self.client.get(reverse("attachment_serve", args=[self.att.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_other_user_cannot_delete(self):
        self.client.force_login(self.other)
        resp = self.client.post(reverse("attachment_delete", args=[self.att.pk]))
        self.assertEqual(resp.status_code, 404)
        self.att.refresh_from_db()
        self.assertFalse(self.att.is_deleted)

    def test_requires_post(self):
        self.client.force_login(self.u)
        resp = self.client.get(reverse("attachment_delete", args=[self.att.pk]))
        self.assertEqual(resp.status_code, 405)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class AttachmentDetailRenderTests(TestCase):
    """详情页渲染。"""

    def setUp(self):
        self.u = _mkuser("att_u8")
        self.exp = Expense.objects.create(
            user=self.u, type="expense", amount=Decimal("10.00"),
            occurred_at=timezone.now(), note="午饭",
        )
        self.client.force_login(self.u)

    def test_empty_state_shows_upload_form(self):
        html = self.client.get(reverse("expense_detail", args=[self.exp.pk])).content.decode()
        self.assertIn("凭证", html)
        self.assertIn("还没有凭证", html)
        self.assertIn(reverse("attachment_upload", args=[self.exp.pk]), html)

    def test_attachment_shown_with_thumbnail(self):
        Attachment.objects.create(
            user=self.u, expense=self.exp, file=_upload(),
            name="receipt.png", size=len(PNG_BYTES),
            content_type="image/png", is_image=True,
        )
        html = self.client.get(reverse("expense_detail", args=[self.exp.pk])).content.decode()
        self.assertIn("receipt.png", html)
        self.assertIn(reverse("attachment_serve", args=[
            Attachment.objects.get(expense=self.exp).pk]), html)
