"""OCR（图片识别记账）测试。

覆盖：小票字段提取、OCR 引擎工厂与降级、上传→识别→确认→保存全链路。
OCR 引擎用 MockOCRProvider 注入预设文本，不依赖本地 Tesseract 或外部服务。
"""
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from . import ocr
from .models import Expense
from .ocr import (
    CloudOCRProvider,
    MockOCRProvider,
    OCRException,
    TesseractOCRProvider,
    extract_receipt_fields,
    get_ocr_provider,
)

SAMPLE_RECEIPT = """星巴克咖啡(南京西路店)
拿铁 1 32.00
美式 1 25.00
合计 57.00
2026-08-30
微信支付
"""

User = None  # 延迟导入，避免在测试收集阶段触发迁移


def _make_png() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (10, 10), (255, 255, 255)).save(buf, "PNG")
    return buf.getvalue()


class ReceiptExtractTests(TestCase):
    def test_extract_amount_from_total(self):
        f = extract_receipt_fields(SAMPLE_RECEIPT)
        self.assertEqual(f["amount"], Decimal("57.00"))

    def test_extract_date(self):
        f = extract_receipt_fields(SAMPLE_RECEIPT)
        self.assertEqual(str(f["date"]), "2026-08-30")

    def test_extract_merchant(self):
        f = extract_receipt_fields(SAMPLE_RECEIPT)
        self.assertIn("星巴克", f["merchant"])

    def test_no_amount_returns_none(self):
        f = extract_receipt_fields("谢谢惠顾 再见")
        self.assertIsNone(f["amount"])

    def test_keyword_amount_without_decimal(self):
        f = extract_receipt_fields("全家便利店\n金额 18\n2026/08/30")
        self.assertEqual(f["amount"], Decimal("18"))

    def test_ignores_year_as_amount(self):
        # 年份 2026 不应被当成金额
        f = extract_receipt_fields("2026-08-30\n合计 12.50")
        self.assertEqual(f["amount"], Decimal("12.50"))

    def test_currency_symbol_amount(self):
        f = extract_receipt_fields("SUBTOTAL ¥128.00")
        self.assertEqual(f["amount"], Decimal("128.00"))

    def test_empty_text(self):
        f = extract_receipt_fields("")
        self.assertEqual(f, {"amount": None, "date": None, "merchant": "", "raw_text": ""})


class ProviderTests(TestCase):
    def test_default_provider_is_tesseract(self):
        with override_settings(OCR_PROVIDER="tesseract"):
            self.assertIsInstance(get_ocr_provider(), TesseractOCRProvider)

    def test_mock_provider_returns_text(self):
        self.assertEqual(MockOCRProvider("hello").recognize(b"x"), "hello")

    def test_tesseract_missing_dep_raises_friendly(self):
        # 当前环境未安装 pytesseract → 应抛出友好的 OCRException（而非 500）
        with self.assertRaises(OCRException):
            TesseractOCRProvider().recognize(b"fake-image-bytes")

    def test_cloud_parse_ocrspace(self):
        body = '{"ParsedResults":[{"ParsedText":"星巴克 57.00"}]}'
        self.assertIn("57.00", CloudOCRProvider()._parse_response(body))

    def test_cloud_parse_plain_text(self):
        self.assertEqual(CloudOCRProvider()._parse_response("raw text"), "raw text")

    def test_cloud_missing_endpoint(self):
        with override_settings(OCR_CLOUD_ENDPOINT=""):
            with self.assertRaises(OCRException):
                CloudOCRProvider(endpoint="").recognize(b"x")


class OCRViewTests(TestCase):
    @classmethod
    def setUpClass(cls):
        from django.contrib.auth import get_user_model
        global User
        User = get_user_model()
        super().setUpClass()

    def setUp(self):
        self.user = User.objects.create_user("ocruser", "o@e.com", "pw")
        self.client.login(username="ocruser", password="pw")
        self.patcher = patch(
            "life.ocr.get_ocr_provider",
            return_value=MockOCRProvider(SAMPLE_RECEIPT),
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_upload_get(self):
        r = self.client.get(reverse("ocr_upload"))
        self.assertEqual(r.status_code, 200)

    def test_upload_post_renders_result(self):
        r = self.client.post(
            reverse("ocr_upload"),
            {"image": SimpleUploadedFile("r.png", _make_png(), "image/png")},
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "57.00")
        self.assertContains(r, "星巴克")

    def test_upload_rejects_non_image(self):
        r = self.client.post(
            reverse("ocr_upload"),
            {"image": SimpleUploadedFile("r.txt", b"hello", "text/plain")},
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "仅支持")

    def test_save_creates_expense(self):
        r = self.client.post(reverse("ocr_save"), {
            "raw_text": SAMPLE_RECEIPT,
            "type": "expense",
            "amount": "57.00",
            "date": "2026-08-30",
            "note": "星巴克咖啡",
        })
        self.assertEqual(r.status_code, 302)
        e = Expense.objects.get(note="星巴克咖啡")
        self.assertEqual(e.amount, Decimal("57.00"))
        self.assertEqual(e.source, "ocr")
        # occurred_at 是本地感知时间，「日期」应取本地日期而非 UTC 日期，
        # 否则在本地已过午夜、UTC 尚未午夜的时间窗内会因时区回退一天而误判
        self.assertEqual(
            timezone.localtime(e.occurred_at).date().isoformat(), "2026-08-30"
        )

    def test_save_bad_amount_shows_error(self):
        r = self.client.post(reverse("ocr_save"), {
            "raw_text": SAMPLE_RECEIPT,
            "type": "expense",
            "amount": "abc",
            "note": "x",
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "有效的金额")

    def test_save_negative_amount_shows_error(self):
        r = self.client.post(reverse("ocr_save"), {
            "raw_text": SAMPLE_RECEIPT,
            "type": "expense",
            "amount": "-5",
            "note": "x",
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "大于 0")

    def test_save_bad_date_shows_error(self):
        r = self.client.post(reverse("ocr_save"), {
            "raw_text": SAMPLE_RECEIPT,
            "type": "expense",
            "amount": "57.00",
            "date": "not-a-date",
            "note": "x",
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "日期格式无效")

    def test_save_income_type(self):
        r = self.client.post(reverse("ocr_save"), {
            "raw_text": SAMPLE_RECEIPT,
            "type": "income",
            "amount": "57.00",
            "note": "退款",
        })
        self.assertEqual(r.status_code, 302)
        e = Expense.objects.get(note="退款")
        self.assertEqual(e.type, "income")
